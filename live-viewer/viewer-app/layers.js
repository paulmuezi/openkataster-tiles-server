import { pointInGeometry } from './utils.js';

const SOURCE_ID = 'alkis-v2';
const AT_KATASTER_SOURCE_ID = 'bev-kataster';
const AT_SYMBOL_SOURCE_ID = 'bev-symbole';
const AT_LAYER_PREFIX = 'at-kataster-';
const NO_STATE_MASK_FILTER = ['==', 'gen', '__openkataster_no_state__'];
const GERMANY_BASEMAP_SOURCES = new Set(['smarttiles_de', 'germany_geojson', 'states_geojson', 'state_labels_source']);

const GROUPS = {
  surfaces: ['alkis-surface-fills', 'alkis-traffic-surface-fills', `${AT_LAYER_PREFIX}surface-fills`],
  surfaceOutlines: ['alkis-surface-lines', `${AT_LAYER_PREFIX}surface-lines`],
  buildings: ['alkis-building-fills', 'alkis-building-lines', `${AT_LAYER_PREFIX}building-fills`, `${AT_LAYER_PREFIX}building-lines`],
  parcelLines: ['alkis-parcel-lines', `${AT_LAYER_PREFIX}parcel-lines`],
  parcelLabels: ['alkis-parcel-labels', 'alkis-parcel-fractions', `${AT_LAYER_PREFIX}parcel-labels`],
  houseNumbers: ['alkis-house-numbers', `${AT_LAYER_PREFIX}house-numbers`],
  streetNames: ['alkis-street-names'],
  buildingLabels: ['alkis-building-labels'],
  boundaryPoints: ['alkis-boundary-points', 'alkis-boundary-points-inner', `${AT_LAYER_PREFIX}boundary-points`, `${AT_LAYER_PREFIX}boundary-points-inner`],
  symbols: ['alkis-symbols', `${AT_LAYER_PREFIX}symbols`]
};

export function unavailableStateMaskFilter(metadata) {
  if (!metadata) return [...NO_STATE_MASK_FILTER];
  const visuallyCoveredStates = new Set(
    (metadata.states || [])
      .filter((state) => state?.visual_active !== false && (
        state?.active !== false || state?.rendering?.cadastre_raster?.tile_template
      ))
      .map((state) => state?.slug)
  );
  const maskedNames = [];
  if (!visuallyCoveredStates.has('bayern')) maskedNames.push('Bayern', 'Bayern (Bodensee)');
  if (!visuallyCoveredStates.has('sachsen-anhalt')) maskedNames.push('Sachsen-Anhalt');
  const filters = maskedNames.map((name) => ['==', 'gen', name]);
  return filters.length ? ['any', ...filters] : [...NO_STATE_MASK_FILTER];
}

export function createLayerController({
  map,
  store,
  elements,
  datasetProfile = { id: 'deutschland', detailZoom: 17, nationalRegion: '' },
  countryResolver = null
}) {
  const { layerButton, layerMenu, layerInputs, layerZoomNote, layerPresentationNote } = elements;
  const DE_DETAIL_ZOOM = Number(datasetProfile.detailZoomByRegion?.deutschland || datasetProfile.detailZoom || 17);
  const AT_DETAIL_ZOOM = Number(datasetProfile.detailZoomByRegion?.oesterreich || 14);
  const layerControl = layerMenu?.closest('.layer-control');
  const baseVisibility = new Map();
  let stateFeatures = [];
  let sourceMetadata = null;
  let activeAerial = '';
  let activeCadastre = '';
  const basemapVisibility = { deutschland: true, oesterreich: true };

  function updateLayerOverflowHint() {
    if (!layerControl || !layerMenu || layerMenu.hidden) {
      layerControl?.removeAttribute('data-layer-overflow');
      return;
    }
    const hasOverflow = layerMenu.scrollHeight > layerMenu.clientHeight + 4;
    const atEnd = layerMenu.scrollTop + layerMenu.clientHeight >= layerMenu.scrollHeight - 4;
    layerControl.dataset.layerOverflow = hasOverflow && !atEnd ? 'true' : 'false';
  }

  async function loadStateFeatures() {
    try {
      const [response] = await Promise.all([
        fetch('/viewer-assets/viewer-app/overlays/states.json?v=20260710-planer-v2'),
        countryResolver?.ready?.()
      ]);
      const data = await response.json();
      stateFeatures = data.features || [];
    } catch (error) {
      console.warn('Bundeslandgeometrien konnten nicht geladen werden', error);
    }
  }

  function currentStateSlug() {
    const center = map.getCenter();
    const point = [center.lng, center.lat];
    if (countryResolver?.datasetAt?.(center.lng, center.lat) === 'oesterreich') return 'oesterreich';
    const feature = stateFeatures.find((candidate) => pointInGeometry(point, candidate.geometry));
    const properties = feature?.properties || {};
    const raw = String(properties.slug || properties.state || properties.name || properties.gen || properties.NAME_1 || '').toLocaleLowerCase('de-DE');
    const aliases = {
      'baden-württemberg': 'baden-wurttemberg', 'mecklenburg-vorpommern': 'mecklenburg-vorpommern',
      'nordrhein-westfalen': 'nordrhein-westfalen', 'rheinland-pfalz': 'rheinland-pfalz',
      'sachsen-anhalt': 'sachsen-anhalt', 'schleswig-holstein': 'schleswig-holstein', 'thüringen': 'thueringen'
    };
    return aliases[raw] || raw.replaceAll(' ', '-').replaceAll('ü', 'u').replaceAll('ä', 'a').replaceAll('ö', 'o').replaceAll('ß', 'ss');
  }

  function currentDataset() {
    return currentStateSlug() === 'oesterreich' ? 'oesterreich' : 'deutschland';
  }

  function currentDetailZoom() {
    return currentDataset() === 'oesterreich' ? AT_DETAIL_ZOOM : DE_DETAIL_ZOOM;
  }

  function officialCadastreCapability(slug) {
    const state = sourceMetadata?.states?.find((candidate) => candidate?.slug === slug);
    const capability = state?.rendering?.cadastre_raster;
    if (!capability?.tile_template) return null;
    return capability;
  }

  function aerialCapability(slug) {
    const state = sourceMetadata?.states?.find((candidate) => candidate?.slug === slug);
    const capability = state?.rendering?.aerial_raster;
    if (!capability?.tile_template) return null;
    return capability;
  }

  function cadastreTileTemplate(capability) {
    const template = String(capability?.tile_template || '');
    return globalThis.navigator?.connection?.saveData === true
      ? template.replace('{ratio}', '')
      : template;
  }

  function updateUnavailableStateMask() {
    const layerId = 'State_Overlay_Bavaria_SaxonyAnhalt_GeoJSON';
    if (!map.getLayer(layerId)) return;
    map.setFilter(layerId, unavailableStateMaskFilter(sourceMetadata));
  }

  function addAustriaBasemap() {
    if (!map.getSource('basemap-at')) {
      map.addSource('basemap-at', {
        type: 'raster',
        tiles: ['https://mapsneu.wien.gv.at/basemap/bmapgrau/normal/google3857/{z}/{y}/{x}.png'],
        tileSize: 256,
        minzoom: 0,
        maxzoom: 19,
        bounds: [9.35, 46.3, 17.2, 49.1],
        attribution: 'Grundkarte: basemap.at'
      });
    }
    if (!map.getLayer('basemap-at-grau')) {
      const before = map.getLayer('Germany_Fill_GeoJSON') ? 'Germany_Fill_GeoJSON' : undefined;
      map.addLayer({
        id: 'basemap-at-grau',
        type: 'raster',
        source: 'basemap-at',
        paint: { 'raster-fade-duration': 0 }
      }, before);
    }
  }

  function addAlkisLayers() {
    addAustriaBasemap();
    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, {
        type: 'vector',
        tiles: [`${window.location.origin}/api/v1/tiles/deutschland/{z}/{x}/{y}.mvt?client=viewer&v=20260714-runtime-schema3`],
        minzoom: 0,
        maxzoom: 17,
        promoteId: { surfaces: 'gml_id', building_fills: 'gml_id' }
      });
    }
    if (map.getLayer('alkis-surface-fills')) {
      addAustriaCadastreLayers();
      return;
    }
    const before = firstToolLayer();
    const add = (layer) => map.addLayer(layer, before);
    const welcomeHover = ['boolean', ['feature-state', 'welcomeHover'], false];
    const welcomeVisibility = document.documentElement.dataset.shellMode === 'welcome' ? 'visible' : 'none';
    add({ id: 'alkis-surface-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DE_DETAIL_ZOOM,
      filter: ['all', ['!=', ['get', 'theme_index'], 0], ['!=', ['get', 'thema'], 'Verkehr']],
      paint: { 'fill-color': ['case',
        ['has', 'fill_color'], ['get', 'fill_color'],
        ['==', ['get', 'thema'], 'Wohnbauflächen'], '#FFEAF4',
        ['==', ['get', 'thema'], 'Vegetation'], '#EAFFD3',
        ['==', ['get', 'thema'], 'Gewässer'], '#DCEFFF',
        ['==', ['get', 'thema'], 'Sport und Freizeit'], '#E0FFD8',
        ['==', ['get', 'thema'], 'Industrie und Gewerbe'], '#EDEDED',
        'rgba(0,0,0,0)'], 'fill-opacity': 1 } });
    add({ id: 'alkis-traffic-surface-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DE_DETAIL_ZOOM,
      filter: ['all', ['==', ['get', 'thema'], 'Verkehr'], ['any', ['!', ['has', 'z_index']], ['<', ['to-number', ['get', 'z_index']], 400]]],
      paint: { 'fill-color': '#ffffff', 'fill-opacity': 1 } });
    add({ id: 'alkis-surface-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'lines', minzoom: DE_DETAIL_ZOOM,
      paint: { 'line-color': ['coalesce', ['get', 'stroke_color'], '#888888'], 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, 1.15], 'line-opacity': .72,
        'line-dasharray': ['case', ['==', ['get', 'render_pattern_kind'], 'dash'], ['literal', [3, 1.6]], ['literal', [1, 0]]] } });
    add({ id: 'alkis-building-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DE_DETAIL_ZOOM,
      filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'fill-color': ['coalesce', ['get', 'fill_color'], '#CCCCCC'], 'fill-opacity': 1 } });
    add({ id: 'alkis-parcel-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'parcel_outline_lines', minzoom: DE_DETAIL_ZOOM,
      paint: { 'line-color': '#36383c', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .5, 20, 1.15], 'line-opacity': .82 } });
    add({ id: 'alkis-building-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'building_lines', minzoom: DE_DETAIL_ZOOM,
      paint: { 'line-color': ['coalesce', ['get', 'stroke_color'], '#202124'], 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .5, 20, 1.35], 'line-opacity': .96,
        'line-dasharray': ['case', ['==', ['get', 'render_pattern_kind'], 'dash'], ['literal', [3, 1.6]], ['literal', [1, 0]]] } });
    add({ id: 'alkis-parcel-fractions', type: 'line', source: SOURCE_ID, 'source-layer': 'parcel_number_lines', minzoom: DE_DETAIL_ZOOM,
      paint: { 'line-color': '#25282d', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, .8], 'line-opacity': .86 } });
    add(labelLayer('alkis-parcel-labels', ['==', ['get', 'theme_index'], 0], 9, false));
    add(labelLayer('alkis-house-numbers', ['all', ['==', ['get', 'theme_index'], 1], ['==', ['get', 'sub_thema'], 'Gebäude']], 9, false));
    add(labelLayer('alkis-building-labels', ['all', ['==', ['get', 'theme_index'], 1], ['==', ['get', 'sub_thema'], 'Geschosse']], 9, false));
    add(labelLayer('alkis-street-names', ['==', ['get', 'theme_index'], 2], 10, true));
    add({ id: 'alkis-boundary-points', type: 'circle', source: SOURCE_ID, 'source-layer': 'boundary_points', minzoom: DE_DETAIL_ZOOM,
      filter: ['in', ['get', 'signaturnummer'], ['literal', ['3020', '3021', '3022', '3023', '3024', '3025']]],
      paint: {
        'circle-color': '#ffffff',
        'circle-opacity': 1,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 17, 3, 19, 4, 20, 4.5],
        'circle-stroke-color': ['match', ['get', 'signaturnummer'],
          '3021', '#aaaaaa', '3023', '#aaaaaa',
          '3024', '#ffffff', '3025', '#ffffff',
          '#000000'],
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 17, .9, 20, 1.2]
      } });
    add({ id: 'alkis-boundary-points-inner', type: 'circle', source: SOURCE_ID, 'source-layer': 'boundary_points', minzoom: DE_DETAIL_ZOOM,
      filter: ['in', ['get', 'signaturnummer'], ['literal', ['3022', '3023', '3024', '3025']]],
      paint: {
        'circle-color': ['match', ['get', 'signaturnummer'], '3023', '#aaaaaa', '3025', '#aaaaaa', '#000000'],
        'circle-opacity': 1,
        'circle-radius': ['interpolate', ['linear'], ['zoom'],
          17, ['case', ['in', ['get', 'signaturnummer'], ['literal', ['3024', '3025']]], 1.8, 1.1],
          19, ['case', ['in', ['get', 'signaturnummer'], ['literal', ['3024', '3025']]], 2.5, 1.5],
          20, ['case', ['in', ['get', 'signaturnummer'], ['literal', ['3024', '3025']]], 2.9, 1.7]]
      } });
    add({ id: 'alkis-symbols', type: 'fill', source: SOURCE_ID, 'source-layer': 'point_symbol_fills_simplified', minzoom: 17.4,
      // MV's DEMV objects include millions of migrated legacy "graphische Punkte"
      // as ALKIS 3629. Keep the same signature from other states untouched.
      filter: ['!', ['all',
        ['==', ['get', 'signaturnummer'], '3629'],
        ['==', ['slice', ['coalesce', ['get', 'gml_id'], ''], 0, 4], 'DEMV']]],
      paint: { 'fill-color': ['coalesce', ['get', 'fill_color'], '#111111'], 'fill-opacity': 1 } });
    // These hit/paint layers deliberately sit above the normal ALKIS rendering.
    // Their idle opacity is effectively invisible; feature-state reveals exactly
    // one locally queried feature without a hover request to the server. The
    // stronger welcome-only stroke remains legible through the parent veil.
    add({ id: 'welcome-hover-parcel-hit', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DE_DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['==', ['get', 'theme_index'], 0],
      paint: { 'fill-color': '#ed3c32', 'fill-opacity': .001 } });
    add({ id: 'welcome-hover-parcel-line', type: 'line', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DE_DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['==', ['get', 'theme_index'], 0],
      paint: { 'line-color': '#c92f26', 'line-width': 4, 'line-dasharray': [2.5, 1.35], 'line-opacity': ['case', welcomeHover, 1, 0] } });
    add({ id: 'welcome-hover-building-hit', type: 'fill', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DE_DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'fill-color': '#ed3c32', 'fill-opacity': .001 } });
    add({ id: 'welcome-hover-building-line', type: 'line', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DE_DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'line-color': '#c92f26', 'line-width': 4.6, 'line-opacity': ['case', welcomeHover, 1, 0] } });
    addAustriaCadastreLayers();
  }

  function addAustriaCadastreLayers() {
    if (map.getSource(AT_KATASTER_SOURCE_ID)) return;
    map.addSource(AT_KATASTER_SOURCE_ID, {
      type: 'vector',
      tiles: [`${window.location.origin}/api/v1/bev/tiles/kataster/{z}/{x}/{y}.pbf?v=bev-kataster-live-v1`],
      minzoom: 0,
      maxzoom: 16
    });
    map.addSource(AT_SYMBOL_SOURCE_ID, {
      type: 'vector',
      tiles: [`${window.location.origin}/api/v1/bev/tiles/symbole/{z}/{x}/{y}.pbf?v=bev-symbole-live-v1`],
      minzoom: 13,
      maxzoom: 16
    });
    const before = firstToolLayer();
    const add = (layer) => map.addLayer(layer, before);
    const usageColor = [
      'match', ['to-number', ['get', 'ns']],
      40, '#dff0b6',
      42, '#ffffff',
      48, '#dff0b6',
      52, '#d6f3c2',
      53, '#ead8b6',
      54, '#f2e1c5',
      55, '#c7ddbd',
      56, '#b8d8ae',
      57, '#c7ddbd',
      58, '#ffffff',
      61, '#dcefff',
      62, '#dcefff',
      63, '#dcefff',
      71, '#ededed',
      72, '#f7f7f4',
      76, '#e0ffd8',
      '#f2f2ee'
    ];
    add({
      id: `${AT_LAYER_PREFIX}surface-fills`,
      type: 'fill',
      source: AT_KATASTER_SOURCE_ID,
      'source-layer': 'nfl',
      minzoom: AT_DETAIL_ZOOM,
      filter: ['!=', ['to-number', ['get', 'ns']], 41],
      paint: { 'fill-color': usageColor, 'fill-opacity': 1 }
    });
    add({
      id: `${AT_LAYER_PREFIX}surface-lines`,
      type: 'line',
      source: AT_SYMBOL_SOURCE_ID,
      'source-layer': 'sli',
      minzoom: 15,
      paint: { 'line-color': '#466278', 'line-width': ['interpolate', ['linear'], ['zoom'], 15, .45, 20, 1] }
    });
    add({
      id: `${AT_LAYER_PREFIX}building-fills`,
      type: 'fill',
      source: AT_KATASTER_SOURCE_ID,
      'source-layer': 'nfl',
      minzoom: AT_DETAIL_ZOOM,
      filter: ['==', ['to-number', ['get', 'ns']], 41],
      paint: { 'fill-color': '#f3b4ae', 'fill-opacity': 1 }
    });
    add({
      id: `${AT_LAYER_PREFIX}building-lines`,
      type: 'line',
      source: AT_KATASTER_SOURCE_ID,
      'source-layer': 'nfl',
      minzoom: AT_DETAIL_ZOOM,
      filter: ['==', ['to-number', ['get', 'ns']], 41],
      paint: { 'line-color': '#8a4b46', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, .45, 20, 1.35] }
    });
    add({
      id: `${AT_LAYER_PREFIX}parcel-lines`,
      type: 'line',
      source: AT_KATASTER_SOURCE_ID,
      'source-layer': 'gst',
      minzoom: AT_DETAIL_ZOOM,
      paint: {
        'line-color': ['match', ['get', 'rstatus'], 'G', '#191b1d', '#777b80'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 14, ['match', ['get', 'rstatus'], 'G', .8, .4], 20, ['match', ['get', 'rstatus'], 'G', 1.7, .9]]
      }
    });
    add({
      id: `${AT_LAYER_PREFIX}parcel-labels`,
      type: 'symbol',
      source: AT_KATASTER_SOURCE_ID,
      'source-layer': 'gnr',
      minzoom: 16,
      layout: {
        'text-field': ['coalesce', ['get', 'gnr'], ''],
        'text-font': ['Noto Sans Bold'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 16, 9, 20, 13],
        'text-rotate': ['*', -1, ['coalesce', ['to-number', ['get', 'rot']], 0]],
        'text-allow-overlap': true,
        'text-ignore-placement': true
      },
      paint: {
        'text-color': ['match', ['get', 'rstatus'], 'G', '#151719', '#777b80'],
        'text-halo-color': '#ffffff',
        'text-halo-width': 1
      }
    });
    add({
      id: `${AT_LAYER_PREFIX}house-numbers`,
      type: 'symbol',
      source: AT_SYMBOL_SOURCE_ID,
      'source-layer': 'hnr',
      minzoom: 17,
      layout: {
        'text-field': ['step', ['zoom'], ['coalesce', ['get', 'hnr'], ''], 19, ['coalesce', ['get', 'name'], ['get', 'hnr'], '']],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 17, 10, 20, 12],
        'text-anchor': 'left',
        'text-offset': [.7, 0],
        'text-allow-overlap': true
      },
      paint: { 'text-color': '#cf6900', 'text-halo-color': '#ffffff', 'text-halo-width': 2 }
    });
    add({
      id: `${AT_LAYER_PREFIX}boundary-points`,
      type: 'circle',
      source: AT_SYMBOL_SOURCE_ID,
      'source-layer': 'gp',
      minzoom: 17,
      paint: {
        'circle-color': '#ffffff',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 17, 2.4, 20, 4],
        'circle-stroke-color': ['match', ['to-number', ['get', 'typ']], 24, '#111111', '#73777c'],
        'circle-stroke-width': 1
      }
    });
    add({
      id: `${AT_LAYER_PREFIX}boundary-points-inner`,
      type: 'circle',
      source: AT_SYMBOL_SOURCE_ID,
      'source-layer': 'gp',
      minzoom: 17,
      filter: ['==', ['to-number', ['get', 'typ']], 24],
      paint: {
        'circle-color': '#111111',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 17, .8, 20, 1.5]
      }
    });
    add({
      id: `${AT_LAYER_PREFIX}symbols`,
      type: 'circle',
      source: AT_SYMBOL_SOURCE_ID,
      'source-layer': 'ssb',
      minzoom: 17,
      paint: {
        'circle-color': '#202326',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 17, 1.5, 20, 2.5],
        'circle-opacity': .86
      }
    });
  }

  function labelLayer(id, filter, baseSize, bold) {
    return {
      id, type: 'symbol', source: SOURCE_ID, 'source-layer': 'labels', minzoom: DE_DETAIL_ZOOM, filter,
      layout: {
        'text-field': ['coalesce', ['get', 'text_content'], ''],
        'text-font': [bold ? 'Noto Sans Bold' : 'Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 17, baseSize, 19, baseSize + 2, 20, baseSize + 4],
        'text-rotation-alignment': 'map',
        'text-rotate': ['*', -1, ['coalesce', ['to-number', ['get', 'render_rotation']], 0]],
        'text-anchor': ['match', ['get', 'render_anchor'],
          'top', 'top', 'bottom', 'bottom', 'left', 'left', 'right', 'right',
          'top-left', 'top-left', 'top-right', 'top-right',
          'bottom-left', 'bottom-left', 'bottom-right', 'bottom-right', 'center'],
        'text-justify': ['match', ['get', 'render_justify'], 'left', 'left', 'right', 'right', 'center'],
        'text-offset': ['case',
          ['all', ['==', ['get', 'signaturnummer'], '4115'], ['==', ['get', 'render_anchor'], 'bottom']], ['literal', [0, -0.02]],
          ['all', ['==', ['get', 'signaturnummer'], '4115'], ['==', ['get', 'render_anchor'], 'top']], ['literal', [0, 0.02]],
          ['literal', [0, 0]]],
        'text-allow-overlap': true,
        'text-ignore-placement': true
      },
      paint: { 'text-color': ['coalesce', ['get', 'font_color'], '#252a32'], 'text-halo-color': '#fff', 'text-halo-width': 1.05 }
    };
  }

  function firstToolLayer() {
    return (map.getStyle().layers || []).find((layer) => String(layer.id).startsWith('selected-') || String(layer.id).startsWith('measure-'))?.id;
  }

  function firstInteractiveOverlay() {
    return map.getLayer('welcome-hover-parcel-hit') ? 'welcome-hover-parcel-hit' : firstToolLayer();
  }

  function ensureRasterStack() {
    const layerIds = () => (map.getStyle().layers || []).map((layer) => layer.id);
    const overlay = firstInteractiveOverlay();
    if (activeCadastre && overlay && map.getLayer(activeCadastre) && map.getLayer(overlay)) {
      const ids = layerIds();
      if (ids.indexOf(activeCadastre) > ids.indexOf(overlay)) map.moveLayer(activeCadastre, overlay);
    }
    if (activeAerial && activeCadastre && map.getLayer(activeAerial) && map.getLayer(activeCadastre)) {
      const ids = layerIds();
      if (ids.indexOf(activeAerial) > ids.indexOf(activeCadastre)) map.moveLayer(activeAerial, activeCadastre);
    }
  }

  function updateAerial(show) {
    const slug = currentStateSlug();
    const detailZoom = currentDetailZoom();
    const capability = aerialCapability(slug);
    if (!show || !capability) {
      for (const layer of map.getStyle().layers || []) if (String(layer.id).startsWith('aerial-') && map.getLayer(layer.id)) map.setLayoutProperty(layer.id, 'visibility', 'none');
      activeAerial = '';
      return;
    }
    const sourceId = `aerial-${slug}`;
    const revision = encodeURIComponent(String(capability.revision || 'aerial-wms-v1'));
    const separator = String(capability.tile_template).includes('?') ? '&' : '?';
    const nativeMaxZoom = Number(capability.maxzoom) || 22;
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, {
        type: 'raster',
        tiles: [`${capability.tile_template}${separator}v=${revision}`],
        tileSize: Number(capability.tile_size) || 512,
        maxzoom: nativeMaxZoom
      });
    }
    if (!map.getLayer(sourceId)) {
      map.addLayer({
        id: sourceId,
        type: 'raster',
        source: sourceId,
        minzoom: Number(capability.minzoom) || detailZoom,
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 }
      }, currentDataset() === 'oesterreich' ? `${AT_LAYER_PREFIX}surface-fills` : 'alkis-surface-fills');
    }
    for (const layer of map.getStyle().layers || []) if (String(layer.id).startsWith('aerial-') && map.getLayer(layer.id)) map.setLayoutProperty(layer.id, 'visibility', layer.id === sourceId ? 'visible' : 'none');
    activeAerial = sourceId;
  }

  function updateOfficialCadastre(show, aerialVisible = false) {
    const slug = currentStateSlug();
    const detailZoom = currentDetailZoom();
    const capability = officialCadastreCapability(slug);
    if (!show || !capability) {
      for (const layer of map.getStyle().layers || []) {
        if (String(layer.id).startsWith('official-cadastre-') && map.getLayer(layer.id)) {
          map.setLayoutProperty(layer.id, 'visibility', 'none');
        }
      }
      activeCadastre = '';
      return;
    }

    const sourceId = `official-cadastre-${slug}`;
    const revision = encodeURIComponent(String(capability.revision || 'official-wms-v1'));
    const tileTemplate = cadastreTileTemplate(capability);
    const separator = tileTemplate.includes('?') ? '&' : '?';
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, {
        type: 'raster',
        tiles: [`${tileTemplate}${separator}v=${revision}`],
        tileSize: Number(capability.tile_size) || 512
      });
    }
    if (!map.getLayer(sourceId)) {
      map.addLayer({
        id: sourceId,
        type: 'raster',
        source: sourceId,
        minzoom: Number(capability.minzoom) || detailZoom,
        maxzoom: Number(capability.maxzoom) || 22,
        paint: { 'raster-opacity': aerialVisible ? .62 : 1, 'raster-fade-duration': 0 }
      }, firstInteractiveOverlay());
    }
    for (const layer of map.getStyle().layers || []) {
      if (!String(layer.id).startsWith('official-cadastre-') || !map.getLayer(layer.id)) continue;
      const visible = layer.id === sourceId;
      map.setLayoutProperty(layer.id, 'visibility', visible ? 'visible' : 'none');
      if (visible) map.setPaintProperty(layer.id, 'raster-opacity', aerialVisible ? .62 : 1);
    }
    activeCadastre = sourceId;
    ensureRasterStack();
  }

  function setBasemapVisible(dataset, visible) {
    if (basemapVisibility[dataset] === visible) return;
    basemapVisibility[dataset] = visible;
    for (const layer of map.getStyle().layers || []) {
      const source = String(layer.source || '');
      const matches = dataset === 'oesterreich'
        ? source === 'basemap-at'
        : GERMANY_BASEMAP_SOURCES.has(source);
      if (!layer.id || !matches) continue;
      if (!map.getLayer(layer.id)) continue;
      if (!baseVisibility.has(layer.id)) baseVisibility.set(layer.id, map.getLayoutProperty(layer.id, 'visibility') || 'visible');
      map.setLayoutProperty(layer.id, 'visibility', visible ? baseVisibility.get(layer.id) : 'none');
    }
  }

  function sourceReady(sourceId) {
    if (!sourceId || !map.getSource(sourceId)) return false;
    if (typeof map.isSourceLoaded !== 'function') return true;
    try { return map.isSourceLoaded(sourceId); } catch (_) { return false; }
  }

  function apply(state = store.getState()) {
    updateUnavailableStateMask();
    if (!map.isStyleLoaded()) return;
    const activeDataset = currentDataset();
    const austria = activeDataset === 'oesterreich';
    const detailZoom = currentDetailZoom();
    const detail = map.getZoom() >= detailZoom;
    const germanyDetail = map.getZoom() >= DE_DETAIL_ZOOM;
    const austriaDetail = map.getZoom() >= AT_DETAIL_ZOOM;
    const layers = state.layers;
    const slug = currentStateSlug();
    const cadastreCapability = officialCadastreCapability(slug);
    const aerial = aerialCapability(slug);
    const fullPresentation = cadastreCapability?.presentation === 'full';
    document.body.dataset.detailLayers = detail ? 'enabled' : 'disabled';
    if (layerZoomNote) {
      layerZoomNote.hidden = detail;
      layerZoomNote.textContent = fullPresentation
        ? 'Amtliche Gesamtdarstellung und Luftbild sind ab Zoom 17 verfügbar.'
        : `${austria ? 'Kataster' : 'ALKIS'} und Luftbild sind ab Zoom ${detailZoom} verfügbar.`;
    }
    if (layerMenu) layerMenu.dataset.detailUnavailable = detail ? 'false' : 'true';
    if (layerMenu) layerMenu.dataset.cadastrePresentation = fullPresentation ? 'full' : 'individual';
    if (layerPresentationNote) layerPresentationNote.hidden = !fullPresentation;
    for (const [group, ids] of Object.entries(GROUPS)) {
      for (const id of ids) {
        if (!map.getLayer(id)) continue;
        const atLayer = id.startsWith(AT_LAYER_PREFIX);
        const regionDetail = atLayer ? austriaDetail : germanyDetail;
        const hiddenByCurrentFullPresentation = fullPresentation && (atLayer === austria);
        const visible = regionDetail && layers.alkis && layers[group] && !hiddenByCurrentFullPresentation;
        map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
      }
    }
    if (map.getLayer('alkis-building-fills')) {
      map.setPaintProperty(
        'alkis-building-fills',
        'fill-color',
        layers.buildingUsage ? ['coalesce', ['get', 'fill_color'], '#CCCCCC'] : '#CCCCCC'
      );
      map.setPaintProperty('alkis-building-fills', 'fill-opacity', !austria && detail && layers.aerial ? .36 : 1);
    }
    if (map.getLayer(`${AT_LAYER_PREFIX}building-fills`)) {
      map.setPaintProperty(`${AT_LAYER_PREFIX}building-fills`, 'fill-opacity', austria && detail && layers.aerial ? .36 : 1);
    }
    for (const id of ['alkis-surface-fills', 'alkis-traffic-surface-fills']) {
      if (map.getLayer(id)) map.setPaintProperty(id, 'fill-opacity', !austria && detail && layers.aerial ? .18 : 1);
    }
    if (map.getLayer(`${AT_LAYER_PREFIX}surface-fills`)) {
      map.setPaintProperty(`${AT_LAYER_PREFIX}surface-fills`, 'fill-opacity', austria && detail && layers.aerial ? .18 : 1);
    }
    updateAerial(detail && layers.aerial);
    updateOfficialCadastre(detail && layers.alkis, detail && layers.aerial);
    ensureRasterStack();
    const detailBackground = detail && (
      (layers.alkis && sourceReady(activeCadastre || (austria ? AT_KATASTER_SOURCE_ID : SOURCE_ID)))
      || (layers.aerial && sourceReady(activeAerial))
    );
    setBasemapVisible('deutschland', !(!austria && detailBackground));
    setBasemapVisible('oesterreich', !(austria && detail && layers.aerial && sourceReady(activeAerial)));
    for (const input of layerInputs) {
      input.checked = !!layers[input.dataset.layer];
      const isSublayer = !['alkis', 'aerial'].includes(input.dataset.layer);
      const unsupportedInAustria = austria && ['streetNames', 'buildingUsage', 'buildingLabels'].includes(input.dataset.layer);
      input.disabled = unsupportedInAustria || !detail || (input.dataset.layer === 'aerial' && !aerial) || (fullPresentation && isSublayer);
      const label = input.closest('label');
      if (label && unsupportedInAustria) label.hidden = true;
    }
  }

  layerButton.addEventListener('click', () => {
    const open = layerMenu.hidden;
    layerMenu.hidden = !open;
    layerButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    window.requestAnimationFrame(updateLayerOverflowHint);
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.layer-control')) {
      layerMenu.hidden = true;
      layerButton.setAttribute('aria-expanded', 'false');
      updateLayerOverflowHint();
    }
  });
  layerMenu.addEventListener('scroll', updateLayerOverflowHint, { passive: true });
  window.addEventListener('resize', updateLayerOverflowHint, { passive: true });
  for (const input of layerInputs) input.addEventListener('change', () => {
    if (input.disabled) return;
    const state = store.getState();
    const layers = { ...state.layers, [input.dataset.layer]: input.checked };
    if (input.dataset.layer === 'alkis' && input.checked) Object.assign(layers, { buildings: true, parcelLines: true, surfaceOutlines: true, houseNumbers: true, streetNames: true, extended: true, parcelLabels: true, surfaces: true, buildingUsage: true, buildingLabels: true, boundaryPoints: true, symbols: true });
    if (input.dataset.layer === 'extended') Object.assign(layers, { parcelLabels: input.checked, surfaces: input.checked, buildingUsage: input.checked, buildingLabels: input.checked, boundaryPoints: input.checked, symbols: input.checked });
    store.setState({ layers }, 'layers');
  });

  map.on('load', async () => { await loadStateFeatures(); addAlkisLayers(); apply(); });
  map.on('zoom', () => apply());
  map.on('moveend', () => apply());
  map.on('sourcedata', (event) => {
    if ([SOURCE_ID, AT_KATASTER_SOURCE_ID, AT_SYMBOL_SOURCE_ID, activeAerial, activeCadastre].includes(event.sourceId)) apply();
  });
  store.subscribe((state, reason) => { if (reason === 'layers' || reason === 'restore') apply(state); });
  return {
    apply,
    currentStateSlug,
    currentDataset,
    currentDetailZoom,
    viewportIntersectsAustria: () => countryResolver?.intersectsAustria?.(map.getBounds()) === true,
    viewportInsideAustria: () => countryResolver?.containsAustria?.(map.getBounds()) === true,
    isBasemapVisible: () => basemapVisibility[currentDataset()],
    setSourceMetadata(metadata) {
      sourceMetadata = metadata || null;
      apply();
    }
  };
}
