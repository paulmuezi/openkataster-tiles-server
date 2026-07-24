import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

import {
  BASEMAP_RUNTIME_CONSTANTS,
  installEuropeBasemapFailover,
  normalizeBasemapMode,
  resolvePlannerBasemap,
  selectBasemapProfile
} from '../live-viewer/viewer-app/basemap.js';
import { resolveLayerFontStack } from '../live-viewer/viewer-app/layers.js';
import {
  resolvePlannerMapLimits
} from '../live-viewer/viewer-app/map.js';

globalThis.window = {
  location: new URL('https://tiles.openkataster.de/planer'),
  setTimeout,
  clearTimeout,
  fetch: globalThis.fetch
};

const europe = {
  available: true,
  version: 'europe-de-at-20260723-z15',
  style_url: '/viewer-assets/europe-basemap-style-20260724-bkg4/style.json',
  tile_template: '/api/v1/basemap/europe/{z}/{x}/{y}.mvt',
  minzoom: 0,
  maxzoom: 15,
  bounds: [5, 45.5, 18, 55.75],
  attribution: [
    '© OpenStreetMap contributors',
    '© ESA WorldCover project 2020 / Contains modified Copernicus Sentinel',
    'data (2020) processed by ESA WorldCover consortium'
  ].join(' · '),
  licenses: [
    {
      id: 'openstreetmap',
      license: 'ODbL-1.0',
      url: 'https://www.openstreetmap.org/copyright'
    },
    {
      id: 'esa-worldcover-2020',
      license: 'CC-BY-4.0',
      url: 'https://esa-worldcover.org/'
    }
  ]
};

assert.equal(normalizeBasemapMode('on'), 'on');
assert.equal(normalizeBasemapMode('unexpected'), 'off');
assert.equal(selectBasemapProfile({ mode: 'off', europe }, '?basemap=europe'), 'national');
assert.equal(selectBasemapProfile({ mode: 'preview', europe }, ''), 'national');
assert.equal(selectBasemapProfile({ mode: 'preview', europe }, '?basemap=europe'), 'europe');
assert.equal(selectBasemapProfile({ mode: 'on', europe }, ''), 'europe');
assert.equal(selectBasemapProfile({ mode: 'on', europe }, '?basemap=national'), 'national');
assert.equal(resolveLayerFontStack(false, 'europe'), 'Noto Sans Regular');
assert.equal(resolveLayerFontStack(true, 'europe'), 'Noto Sans Medium');
assert.equal(resolveLayerFontStack(true, 'national'), 'Noto Sans Bold');
assert.equal(
  selectBasemapProfile({ mode: 'on', europe: { ...europe, version: '../unsafe' } }, ''),
  'national'
);

const style = JSON.parse(readFileSync(
  new URL('../live-viewer/europe-basemap-style-20260724-bkg4/style.json', import.meta.url),
  'utf8'
));
const federalStateLabels = JSON.parse(readFileSync(
  new URL(
    '../live-viewer/viewer-app/overlays/federal-state-labels-de-at.json',
    import.meta.url
  ),
  'utf8'
));
const federalStateBoundariesGermany = JSON.parse(readFileSync(
  new URL(
    '../live-viewer/viewer-app/overlays/federal-state-boundaries-germany.json',
    import.meta.url
  ),
  'utf8'
));
const federalStateBoundariesAustria = JSON.parse(readFileSync(
  new URL(
    '../live-viewer/viewer-app/overlays/federal-state-boundaries-austria.json',
    import.meta.url
  ),
  'utf8'
));
const fetchCalls = [];
const runtime = await resolvePlannerBasemap({
  locationObject: new URL('https://tiles.openkataster.de/planer'),
  fetchImpl: async (url) => {
    fetchCalls.push(String(url));
    if (String(url) === BASEMAP_RUNTIME_CONSTANTS.configUrl) {
      return new Response(JSON.stringify({ mode: 'on', europe }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      });
    }
    return new Response(JSON.stringify(structuredClone(style)), {
      status: 200,
      headers: { 'content-type': 'application/json' }
    });
  }
});
assert.equal(runtime.profile, 'europe');
assert.equal(runtime.version, 'europe-de-at-20260723-z15');
assert.deepEqual(fetchCalls, [
  '/api/v1/basemap/config',
  '/viewer-assets/europe-basemap-style-20260724-bkg4/style.json'
]);
assert.deepEqual(runtime.style.sources.openkataster_europe.tiles, [
  'https://tiles.openkataster.de/api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=europe-de-at-20260723-z15'
]);
assert.equal(
  JSON.stringify(runtime.style.sources.openkataster_europe.bounds),
  JSON.stringify([5, 45.5, 18, 55.75])
);
assert.equal(
  runtime.style.glyphs,
  'https://tiles.openkataster.de/viewer-assets/europe-basemap-assets-protomaps-028c18f7/fonts/{fontstack}/{range}.pbf'
);
assert.equal(
  runtime.style.sprite,
  'https://tiles.openkataster.de/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light'
);
assert.equal(
  runtime.style.sources.availability_europe.data,
  'https://tiles.openkataster.de/viewer-assets/viewer-app/overlays/europe-countries.json?v=20260724-de-at1'
);
assert.equal(
  runtime.style.sources.federal_state_labels_de_at.data,
  'https://tiles.openkataster.de/viewer-assets/viewer-app/overlays/federal-state-labels-de-at.json?v=20260724-de-at1'
);
assert.equal(
  runtime.style.sources.federal_state_boundaries_germany.data,
  'https://tiles.openkataster.de/viewer-assets/viewer-app/overlays/federal-state-boundaries-germany.json?v=20260724-de-at1'
);
assert.equal(
  runtime.style.sources.federal_state_boundaries_austria.data,
  'https://tiles.openkataster.de/viewer-assets/viewer-app/overlays/federal-state-boundaries-austria.json?v=20260724-de-at1'
);
assert.equal(runtime.style.sources.availability_germany, undefined);
assert.equal(runtime.style.sources.availability_austria, undefined);

const resolveUnsafeRuntime = async ({ europeOverride = {}, styleOverride = {} }) => (
  resolvePlannerBasemap({
    locationObject: new URL('https://tiles.openkataster.de/planer?basemap=europe'),
    fetchImpl: async (url) => {
      if (String(url) === BASEMAP_RUNTIME_CONSTANTS.configUrl) {
        return new Response(JSON.stringify({
          mode: 'on',
          europe: { ...europe, ...europeOverride }
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        ...structuredClone(style),
        ...styleOverride
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
  })
);
assert.equal(
  (await resolveUnsafeRuntime({
    europeOverride: { tile_template: 'https://example.invalid/{z}/{x}/{y}.mvt' }
  })).profile,
  'national'
);
assert.equal(
  (await resolveUnsafeRuntime({
    styleOverride: { glyphs: 'https://example.invalid/fonts/{fontstack}/{range}.pbf' }
  })).profile,
  'national'
);
assert.equal(
  (await resolveUnsafeRuntime({
    styleOverride: { sprite: 'https://example.invalid/sprites/light' }
  })).profile,
  'national'
);
assert.equal(
  (await resolveUnsafeRuntime({
    styleOverride: {
      sources: {
        ...style.sources,
        availability_europe: {
          ...style.sources.availability_europe,
          data: 'https://example.invalid/europe.json'
        }
      }
    }
  })).profile,
  'national'
);

const failedRuntime = await resolvePlannerBasemap({
  locationObject: new URL('https://tiles.openkataster.de/planer'),
  fetchImpl: async () => new Response('', { status: 503 })
});
assert.equal(failedRuntime.profile, 'national');

class FakeMap {
  constructor() {
    this.listeners = new Map();
  }

  on(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  off(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
  }

  emit(type, event) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}

const map = new FakeMap();
let replacement = '';
const dispose = installEuropeBasemapFailover(map, runtime, {
  locationObject: new URL('https://tiles.openkataster.de/planer?foo=1#7/48/14'),
  replace: (url) => { replacement = url; },
  threshold: 3
});
map.emit('error', { sourceId: 'unrelated' });
map.emit('error', { sourceId: 'openkataster_europe' });
map.emit('error', { sourceId: 'openkataster_europe' });
assert.equal(replacement, '');
map.emit('error', { sourceId: 'openkataster_europe' });
assert.equal(
  replacement,
  'https://tiles.openkataster.de/planer?foo=1&basemap=national#7/48/14'
);
dispose();

assert.equal(style.version, 8);
assert.equal(style.metadata['openkataster:profile'], 'europe-de-at-bkg-v5');
assert.equal(style.sources.openkataster_europe.type, 'vector');
assert.deepEqual(style.sources.openkataster_europe.bounds, [5, 45.5, 18, 55.75]);
assert.equal(
  style.sources.openkataster_europe.tiles[0],
  '/api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=__OPENKATASTER_BASEMAP_VERSION__'
);
assert.ok(style.glyphs.startsWith('/viewer-assets/'));
assert.ok(style.sprite.startsWith('/viewer-assets/'));
assert.match(style.sources.openkataster_europe.attribution, /OpenStreetMap contributors/);
assert.match(style.sources.openkataster_europe.attribution, /ESA WorldCover project 2020/);
assert.match(style.sources.openkataster_europe.attribution, /CC BY 4\.0/);
assert.equal(
  style.layers.some((layer) => layer['source-layer'] === 'landcover'),
  false,
  'Der Europe-Style soll den mitverteilten Landcover-Layer bewusst nicht rendern.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_region'),
  undefined,
  'Die PMTiles enthalten keine verlässlichen Regionsnamen; statische DE/AT-Namen ersetzen sie.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_locality-major')?.minzoom,
  6.8,
  'Großstädte dürfen erst nach der Bundeslandübersicht erscheinen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_locality-medium')?.minzoom,
  8,
  'Mittlere Städte sollen später erscheinen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_locality-minor')?.minzoom,
  9.5,
  'Kleinere Städte sollen erst in der regionalen Ansicht erscheinen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_subplace')?.minzoom,
  10.5,
  'Ortsteile sollen die Bundeslandansicht nicht überladen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'pois')?.minzoom,
  14,
  'Restaurants und andere POIs dürfen die Übersichtskarte nicht überladen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'boundaries')?.minzoom,
  10.5,
  'Generische Detailgrenzen dürfen die orangefarbenen Bundeslandgrenzen nicht doppeln.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'boundaries_country')?.minzoom,
  undefined,
  'Staatsgrenzen müssen in der Länderübersicht sichtbar bleiben.'
);
assert.equal(style.metadata['openkataster:available-countries'], 'DE,AT');
assert.equal(style.layers.find((layer) => layer.id === 'background')?.paint['background-color'], '#ffffff');
assert.equal(style.layers.find((layer) => layer.id === 'earth')?.paint['fill-color'], '#ffffff');
assert.equal(style.layers.find((layer) => layer.id === 'water')?.paint['fill-color'], '#e2ffff');
assert.equal(style.layers.find((layer) => layer.id === 'buildings')?.paint['fill-color'], '#aaa9a7');
assert.equal(style.layers.find((layer) => layer.id === 'landuse_residential')?.paint['fill-color'], '#ffeaf4');
assert.deepEqual(
  style.layers.find((layer) => layer.id === 'places_country')?.filter,
  ['all', ['==', 'kind', 'country'], ['in', 'wikidata', 'Q183', 'Q40']]
);
assert.deepEqual(
  style.layers.find((layer) => layer.id === 'availability-unavailable-countries-mask')?.filter,
  ['!in', 'ISO_A3', 'DEU', 'AUT']
);
assert.equal(
  style.layers.find((layer) => layer.id === 'availability-unavailable-countries-mask')
    ?.paint['fill-color'],
  '#ffffff',
  'Nicht unterstützte Länder müssen vollständig weiß maskiert sein.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'availability-unavailable-countries-mask')
    ?.paint['fill-opacity'],
  1,
  'Die weiße Verfügbarkeitsmaske darf die darunterliegende Europakarte nicht durchscheinen lassen.'
);
const supportedRegionBoundariesGermany = style.layers.find(
  (layer) => layer.id === 'availability-supported-region-boundaries-de'
);
const supportedRegionBoundariesAustria = style.layers.find(
  (layer) => layer.id === 'availability-supported-region-boundaries-at'
);
for (const boundaryLayer of [
  supportedRegionBoundariesGermany,
  supportedRegionBoundariesAustria
]) {
  assert.equal(boundaryLayer?.minzoom, 5);
  assert.equal(boundaryLayer?.maxzoom, 10.5);
  assert.equal(boundaryLayer?.paint['line-color'], '#f86d14');
}
assert.equal(
  supportedRegionBoundariesGermany?.source,
  'federal_state_boundaries_germany'
);
assert.equal(
  supportedRegionBoundariesAustria?.source,
  'federal_state_boundaries_austria'
);
const supportedCountriesOutline = style.layers.find(
  (layer) => layer.id === 'availability-supported-countries-outline'
);
assert.equal(supportedCountriesOutline?.minzoom, 5);
assert.equal(supportedCountriesOutline?.maxzoom, 10.5);
assert.deepEqual(
  supportedCountriesOutline?.filter,
  ['in', 'ISO_A3', 'DEU', 'AUT']
);
assert.equal(supportedCountriesOutline?.paint['line-color'], '#f86d14');
const supportedRegionBoundaryGermanyIndex = style.layers.indexOf(
  supportedRegionBoundariesGermany
);
const supportedRegionBoundaryAustriaIndex = style.layers.indexOf(
  supportedRegionBoundariesAustria
);
const unsupportedCountriesMaskIndex = style.layers.findIndex(
  (layer) => layer.id === 'availability-unavailable-countries-mask'
);
const supportedCountriesOutlineIndex = style.layers.indexOf(supportedCountriesOutline);
const supportedRegionLabels = style.layers.find(
  (layer) => layer.id === 'availability-supported-region-labels'
);
assert.equal(supportedRegionLabels?.source, 'federal_state_labels_de_at');
assert.equal(supportedRegionLabels?.minzoom, 5.55);
assert.equal(supportedRegionLabels?.maxzoom, 10.5);
assert.equal(style.sources.federal_state_labels_de_at.type, 'geojson');
assert.equal(federalStateLabels.type, 'FeatureCollection');
assert.equal(federalStateLabels.features.length, 25);
assert.equal(
  federalStateLabels.features.filter((feature) => feature.properties?.country_code === 'DE').length,
  16
);
assert.equal(
  federalStateLabels.features.filter((feature) => feature.properties?.country_code === 'AT').length,
  9
);
assert.equal(
  federalStateLabels.features.some((feature) => feature.properties?.name === 'Niederösterreich'),
  true
);
assert.equal(federalStateBoundariesGermany.type, 'FeatureCollection');
assert.equal(federalStateBoundariesGermany.features.length, 16);
assert.equal(
  federalStateBoundariesGermany.features.every(
    (feature) => feature.properties?.country_code === 'DE'
  ),
  true
);
assert.equal(federalStateBoundariesGermany.metadata?.license, 'dl-de/by-2-0');
assert.equal(
  federalStateBoundariesGermany.metadata?.attribution,
  '© GeoBasis-DE / BKG'
);
assert.equal(federalStateBoundariesAustria.type, 'FeatureCollection');
assert.equal(federalStateBoundariesAustria.features.length, 9);
assert.equal(
  new Set(
    federalStateBoundariesAustria.features.map((feature) => feature.properties?.name)
  ).size,
  9
);
assert.equal(
  federalStateBoundariesAustria.features.every(
    (feature) => feature.properties?.country_code === 'AT'
  ),
  true
);
assert.equal(federalStateBoundariesAustria.metadata?.license, 'CC-BY-4.0');
assert.match(
  federalStateBoundariesAustria.metadata?.attribution || '',
  /Datenquelle: Statistik Austria/
);
assert.ok(unsupportedCountriesMaskIndex < supportedRegionBoundaryGermanyIndex);
assert.ok(unsupportedCountriesMaskIndex < supportedRegionBoundaryAustriaIndex);
assert.ok(
  supportedRegionBoundaryGermanyIndex < supportedCountriesOutlineIndex,
  'Die deutsche Ländergrenze muss unter der gemeinsamen Außenkontur liegen.'
);
assert.ok(
  supportedRegionBoundaryAustriaIndex < supportedCountriesOutlineIndex,
  'Die österreichische Ländergrenze muss unter der gemeinsamen Außenkontur liegen.'
);
assert.equal(style.sources.availability_europe.type, 'geojson');
assert.deepEqual(
  style.layers.find((layer) => layer.id === 'availability-germany-fill')?.filter,
  ['==', 'ISO_A3', 'DEU']
);
assert.equal(
  style.layers.find((layer) => layer.id === 'availability-germany-fill')?.paint['fill-color'],
  '#fffdee'
);
assert.deepEqual(
  style.layers.find((layer) => layer.id === 'availability-austria-fill')?.filter,
  ['==', 'ISO_A3', 'AUT']
);
assert.equal(
  style.layers.find((layer) => layer.id === 'availability-austria-fill')?.paint['fill-color'],
  '#fffdee'
);
for (const layer of style.layers) {
  if (!('source' in layer)) continue;
  assert.ok(
    [
      'openkataster_europe',
      'availability_europe',
      'federal_state_labels_de_at',
      'federal_state_boundaries_germany',
      'federal_state_boundaries_austria'
    ].includes(layer.source),
    `Unerwartete Style-Quelle: ${layer.source}`
  );
}

assert.deepEqual(resolvePlannerMapLimits({ profile: 'national' }), {
  minZoom: 3.2,
  maxBounds: undefined
});
assert.deepEqual(resolvePlannerMapLimits({ profile: 'europe' }), {
  minZoom: 4.9,
  maxBounds: [[-4.0, 41.5], [27.0, 59.0]]
});
assert.deepEqual(resolvePlannerMapLimits({ profile: 'europe' }, { mobile: true }), {
  minZoom: 4.35,
  maxBounds: [[-10.0, 35.0], [32.0, 66.0]]
});
const viewerIndex = readFileSync(
  new URL('../live-viewer/viewer-app/index.html', import.meta.url),
  'utf8'
);
assert.doesNotMatch(viewerIndex, /Verfügbar in Deutschland und Österreich/);
assert.match(
  viewerIndex,
  /Katasterlayer und Luftbilder sind ab Zoomstufe 17 verfügbar\./
);

const assetRoot = new URL(
  '../live-viewer/europe-basemap-assets-protomaps-028c18f7/',
  import.meta.url
);
const provenance = JSON.parse(readFileSync(new URL('PROVENANCE.json', assetRoot), 'utf8'));
const assetRootPath = assetRoot.pathname;
for (const requiredFont of ['Noto Sans Regular', 'Noto Sans Medium', 'Noto Sans Italic']) {
  assert.equal(
    statSync(join(assetRootPath, 'fonts', requiredFont)).isDirectory(),
    true,
    `Required Europe font is missing: ${requiredFont}`
  );
}
const vendoredFiles = [];
const collectFiles = (directory) => {
  for (const name of readdirSync(directory).sort()) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) collectFiles(path);
    else if (name !== 'PROVENANCE.json') vendoredFiles.push(path);
  }
};
collectFiles(assetRootPath);
vendoredFiles.sort((left, right) => (
  Buffer.compare(
    Buffer.from(relative(assetRootPath, left).split(sep).join('/'), 'utf8'),
    Buffer.from(relative(assetRootPath, right).split(sep).join('/'), 'utf8')
  )
));
const treeHash = createHash('sha256');
let vendoredSize = 0;
for (const path of vendoredFiles) {
  const content = readFileSync(path);
  const relativePath = relative(assetRootPath, path).split(sep).join('/');
  treeHash.update(relativePath);
  treeHash.update('\0');
  treeHash.update(createHash('sha256').update(content).digest('hex'));
  treeHash.update('\n');
  vendoredSize += content.byteLength;
}
assert.equal(
  vendoredFiles.length,
  provenance.vendored_file_count_excluding_provenance
);
assert.equal(
  vendoredSize,
  provenance.vendored_size_bytes_excluding_provenance
);
assert.equal(
  treeHash.digest('hex'),
  provenance.canonical_tree_sha256_excluding_provenance
);

console.log('europe-basemap-tests=ok');
