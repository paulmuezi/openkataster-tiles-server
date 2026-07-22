import { pointInGeometry } from './utils.js';

const SOURCE_ID = 'alkis-v2';
const DETAIL_ZOOM = 17;
const BKG_SOURCES = new Set(['smarttiles_de', 'germany_geojson', 'states_geojson', 'state_labels_source', 'world_countries_geojson', 'europe_countries_geojson']);

const GROUPS = {
  surfaces: ['alkis-surface-fills', 'alkis-traffic-surface-fills'],
  surfaceOutlines: ['alkis-surface-lines'],
  buildings: ['alkis-building-fills', 'alkis-building-lines'],
  parcelLines: ['alkis-parcel-lines'],
  parcelLabels: ['alkis-parcel-labels', 'alkis-parcel-fractions'],
  houseNumbers: ['alkis-house-numbers'],
  streetNames: ['alkis-street-names'],
  buildingLabels: ['alkis-building-labels'],
  boundaryPoints: ['alkis-boundary-points', 'alkis-boundary-points-inner'],
  symbols: ['alkis-symbols']
};

export function createLayerController({ map, store, elements }) {
  const { layerButton, layerMenu, layerInputs, layerZoomNote, layerPresentationNote } = elements;
  const layerControl = layerMenu?.closest('.layer-control');
  const baseVisibility = new Map();
  let stateFeatures = [];
  let sourceMetadata = null;
  let activeAerial = '';
  let activeCadastre = '';
  let basemapVisible = true;

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
      const response = await fetch('/viewer-assets/viewer-app/overlays/states.json?v=20260710-planer-v2');
      const data = await response.json();
      stateFeatures = data.features || [];
    } catch (error) {
      console.warn('Bundeslandgeometrien konnten nicht geladen werden', error);
    }
  }

  function currentStateSlug() {
    const center = map.getCenter();
    const point = [center.lng, center.lat];
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

  function updateUnavailableStateMask() {
    const layerId = 'State_Overlay_Bavaria_SaxonyAnhalt_GeoJSON';
    if (!map.getLayer(layerId)) return;
    const visuallyCoveredStates = new Set(
      (sourceMetadata?.states || [])
        .filter((state) => state?.visual_active !== false && (
          state?.active !== false || state?.rendering?.cadastre_raster?.tile_template
        ))
        .map((state) => state?.slug)
    );
    const maskedNames = [];
    if (!visuallyCoveredStates.has('bayern')) maskedNames.push('Bayern', 'Bayern (Bodensee)');
    if (!visuallyCoveredStates.has('sachsen-anhalt')) maskedNames.push('Sachsen-Anhalt');
    const filters = maskedNames.map((name) => ['==', 'gen', name]);
    map.setFilter(layerId, filters.length ? ['any', ...filters] : ['==', 'gen', '__openkataster_no_state__']);
  }

  function addAlkisLayers() {
    if (map.getSource(SOURCE_ID)) return;
    map.addSource(SOURCE_ID, {
      type: 'vector',
      tiles: [`${window.location.origin}/api/v1/tiles/deutschland/{z}/{x}/{y}.mvt?client=viewer&v=20260714-runtime-schema3`],
      minzoom: 0,
      maxzoom: 17,
      promoteId: { surfaces: 'gml_id', building_fills: 'gml_id' }
    });
    const before = firstToolLayer();
    const add = (layer) => map.addLayer(layer, before);
    const welcomeHover = ['boolean', ['feature-state', 'welcomeHover'], false];
    const welcomeVisibility = document.documentElement.dataset.shellMode === 'welcome' ? 'visible' : 'none';
    add({ id: 'alkis-surface-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DETAIL_ZOOM,
      filter: ['all', ['!=', ['get', 'theme_index'], 0], ['!=', ['get', 'thema'], 'Verkehr']],
      paint: { 'fill-color': ['case',
        ['has', 'fill_color'], ['get', 'fill_color'],
        ['==', ['get', 'thema'], 'Wohnbauflächen'], '#FFEAF4',
        ['==', ['get', 'thema'], 'Vegetation'], '#EAFFD3',
        ['==', ['get', 'thema'], 'Gewässer'], '#DCEFFF',
        ['==', ['get', 'thema'], 'Sport und Freizeit'], '#E0FFD8',
        ['==', ['get', 'thema'], 'Industrie und Gewerbe'], '#EDEDED',
        'rgba(0,0,0,0)'], 'fill-opacity': 1 } });
    add({ id: 'alkis-traffic-surface-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DETAIL_ZOOM,
      filter: ['all', ['==', ['get', 'thema'], 'Verkehr'], ['any', ['!', ['has', 'z_index']], ['<', ['to-number', ['get', 'z_index']], 400]]],
      paint: { 'fill-color': '#ffffff', 'fill-opacity': 1 } });
    add({ id: 'alkis-surface-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'lines', minzoom: DETAIL_ZOOM,
      paint: { 'line-color': ['coalesce', ['get', 'stroke_color'], '#888888'], 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, 1.15], 'line-opacity': .72,
        'line-dasharray': ['case', ['==', ['get', 'render_pattern_kind'], 'dash'], ['literal', [3, 1.6]], ['literal', [1, 0]]] } });
    add({ id: 'alkis-building-fills', type: 'fill', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DETAIL_ZOOM,
      filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'fill-color': ['coalesce', ['get', 'fill_color'], '#CCCCCC'], 'fill-opacity': 1 } });
    add({ id: 'alkis-parcel-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'parcel_outline_lines', minzoom: DETAIL_ZOOM,
      paint: { 'line-color': '#36383c', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .5, 20, 1.15], 'line-opacity': .82 } });
    add({ id: 'alkis-building-lines', type: 'line', source: SOURCE_ID, 'source-layer': 'building_lines', minzoom: DETAIL_ZOOM,
      paint: { 'line-color': ['coalesce', ['get', 'stroke_color'], '#202124'], 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .5, 20, 1.35], 'line-opacity': .96,
        'line-dasharray': ['case', ['==', ['get', 'render_pattern_kind'], 'dash'], ['literal', [3, 1.6]], ['literal', [1, 0]]] } });
    add({ id: 'alkis-parcel-fractions', type: 'line', source: SOURCE_ID, 'source-layer': 'parcel_number_lines', minzoom: DETAIL_ZOOM,
      paint: { 'line-color': '#25282d', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, .8], 'line-opacity': .86 } });
    add(labelLayer('alkis-parcel-labels', ['==', ['get', 'theme_index'], 0], 9, false));
    add(labelLayer('alkis-house-numbers', ['all', ['==', ['get', 'theme_index'], 1], ['==', ['get', 'sub_thema'], 'Gebäude']], 9, false));
    add(labelLayer('alkis-building-labels', ['all', ['==', ['get', 'theme_index'], 1], ['==', ['get', 'sub_thema'], 'Geschosse']], 9, false));
    add(labelLayer('alkis-street-names', ['==', ['get', 'theme_index'], 2], 10, true));
    add({ id: 'alkis-boundary-points', type: 'circle', source: SOURCE_ID, 'source-layer': 'boundary_points', minzoom: DETAIL_ZOOM,
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
    add({ id: 'alkis-boundary-points-inner', type: 'circle', source: SOURCE_ID, 'source-layer': 'boundary_points', minzoom: DETAIL_ZOOM,
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
    add({ id: 'welcome-hover-parcel-hit', type: 'fill', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['==', ['get', 'theme_index'], 0],
      paint: { 'fill-color': '#ed3c32', 'fill-opacity': .001 } });
    add({ id: 'welcome-hover-parcel-line', type: 'line', source: SOURCE_ID, 'source-layer': 'surfaces', minzoom: DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['==', ['get', 'theme_index'], 0],
      paint: { 'line-color': '#c92f26', 'line-width': 4, 'line-dasharray': [2.5, 1.35], 'line-opacity': ['case', welcomeHover, 1, 0] } });
    add({ id: 'welcome-hover-building-hit', type: 'fill', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'fill-color': '#ed3c32', 'fill-opacity': .001 } });
    add({ id: 'welcome-hover-building-line', type: 'line', source: SOURCE_ID, 'source-layer': 'building_fills', minzoom: DETAIL_ZOOM,
      layout: { visibility: welcomeVisibility }, filter: ['!=', ['get', 'render_fill_role'], 'underground'],
      paint: { 'line-color': '#c92f26', 'line-width': 4.6, 'line-opacity': ['case', welcomeHover, 1, 0] } });
  }

  function labelLayer(id, filter, baseSize, bold) {
    return {
      id, type: 'symbol', source: SOURCE_ID, 'source-layer': 'labels', minzoom: DETAIL_ZOOM, filter,
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
    const capability = aerialCapability(slug);
    if (!show || !capability) {
      for (const layer of map.getStyle().layers || []) if (String(layer.id).startsWith('aerial-') && map.getLayer(layer.id)) map.setLayoutProperty(layer.id, 'visibility', 'none');
      activeAerial = '';
      return;
    }
    const sourceId = `aerial-${slug}`;
    const revision = encodeURIComponent(String(capability.revision || 'aerial-wms-v1'));
    const separator = String(capability.tile_template).includes('?') ? '&' : '?';
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, {
        type: 'raster',
        tiles: [`${capability.tile_template}${separator}v=${revision}`],
        tileSize: Number(capability.tile_size) || 512
      });
    }
    if (!map.getLayer(sourceId)) {
      map.addLayer({
        id: sourceId,
        type: 'raster',
        source: sourceId,
        minzoom: Number(capability.minzoom) || DETAIL_ZOOM,
        maxzoom: Number(capability.maxzoom) || 22,
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 }
      }, 'alkis-surface-fills');
    }
    for (const layer of map.getStyle().layers || []) if (String(layer.id).startsWith('aerial-') && map.getLayer(layer.id)) map.setLayoutProperty(layer.id, 'visibility', layer.id === sourceId ? 'visible' : 'none');
    activeAerial = sourceId;
  }

  function updateOfficialCadastre(show, aerialVisible = false) {
    const slug = currentStateSlug();
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
    const separator = String(capability.tile_template).includes('?') ? '&' : '?';
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, {
        type: 'raster',
        tiles: [`${capability.tile_template}${separator}v=${revision}`],
        tileSize: Number(capability.tile_size) || 512
      });
    }
    if (!map.getLayer(sourceId)) {
      map.addLayer({
        id: sourceId,
        type: 'raster',
        source: sourceId,
        minzoom: Number(capability.minzoom) || DETAIL_ZOOM,
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

  function setBasemapVisible(visible) {
    if (basemapVisible === visible) return;
    basemapVisible = visible;
    for (const layer of map.getStyle().layers || []) {
      if (!layer.id || (!BKG_SOURCES.has(String(layer.source || '')) && layer.id !== 'background')) continue;
      if (!map.getLayer(layer.id)) continue;
      if (layer.id === 'background') {
        map.setPaintProperty(layer.id, 'background-color', '#ffffff');
        continue;
      }
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
    if (!map.isStyleLoaded()) return;
    updateUnavailableStateMask();
    const detail = map.getZoom() >= DETAIL_ZOOM;
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
        : 'ALKIS und Luftbild sind ab Zoom 17 verfügbar.';
    }
    if (layerMenu) layerMenu.dataset.detailUnavailable = detail ? 'false' : 'true';
    if (layerMenu) layerMenu.dataset.cadastrePresentation = fullPresentation ? 'full' : 'individual';
    if (layerPresentationNote) layerPresentationNote.hidden = !fullPresentation;
    for (const [group, ids] of Object.entries(GROUPS)) {
      const visible = detail && layers.alkis && layers[group] && !fullPresentation;
      for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
    if (map.getLayer('alkis-building-fills')) {
      map.setPaintProperty('alkis-building-fills', 'fill-color', layers.buildingUsage ? ['coalesce', ['get', 'fill_color'], '#CCCCCC'] : '#CCCCCC');
      map.setPaintProperty('alkis-building-fills', 'fill-opacity', detail && layers.aerial ? .36 : 1);
    }
    for (const id of ['alkis-surface-fills', 'alkis-traffic-surface-fills']) {
      if (map.getLayer(id)) map.setPaintProperty(id, 'fill-opacity', detail && layers.aerial ? .18 : 1);
    }
    updateAerial(detail && layers.aerial);
    updateOfficialCadastre(detail && layers.alkis, detail && layers.aerial);
    ensureRasterStack();
    const detailBackground = detail && (
      (layers.alkis && sourceReady(activeCadastre || SOURCE_ID))
      || (layers.aerial && sourceReady(activeAerial))
    );
    setBasemapVisible(!detailBackground);
    for (const input of layerInputs) {
      input.checked = !!layers[input.dataset.layer];
      const isSublayer = !['alkis', 'aerial'].includes(input.dataset.layer);
      input.disabled = !detail || (input.dataset.layer === 'aerial' && !aerial) || (fullPresentation && isSublayer);
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
    if (event.sourceId === SOURCE_ID || event.sourceId === activeAerial || event.sourceId === activeCadastre) apply();
  });
  store.subscribe((state, reason) => { if (reason === 'layers' || reason === 'restore') apply(state); });
  return {
    apply,
    currentStateSlug,
    isBasemapVisible: () => basemapVisible,
    setSourceMetadata(metadata) {
      sourceMetadata = metadata || null;
      apply();
    }
  };
}
