import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createApi, createUnifiedApi } from '../live-viewer/viewer-app/api.js';
import {
  applyDatasetTerminology,
  austriaBasemapStyle,
  createCountryResolver,
  datasetIdFromLocation,
  datasetViewerUrl,
  unifiedViewerProfile,
  viewerDatasetProfile,
  WORKSPACE_DATASET
} from '../live-viewer/viewer-app/dataset.js';
import { locationLabelFromFeatures } from '../live-viewer/viewer-app/export.js';
import { readPersistedState } from '../live-viewer/viewer-app/persistence.js';
import { parcelDisplayNumber } from '../live-viewer/viewer-app/selection.js';
import {
  AUSTRIA_USAGE_COLOR,
  COUNTRY_OVERVIEW_LABELS,
  COUNTRY_OVERVIEW_MAX_ZOOM
} from '../live-viewer/viewer-app/layers.js';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const layerSource = readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');
const selectionSource = readFileSync(new URL('../live-viewer/viewer-app/selection.js', import.meta.url), 'utf8');

assert.equal(datasetIdFromLocation({ pathname: '/viewer/oesterreich', search: '' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/embed/deutschland', search: '?dataset=oesterreich' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/planer', search: '?dataset=oesterreich' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/viewer/unbekannt', search: '' }), 'deutschland');

assert.equal(
  datasetViewerUrl(
    { pathname: '/viewer/deutschland', search: '?fresh=17&dataset=deutschland', hash: '#16/48.2/16.3' },
    'oesterreich'
  ),
  '/viewer/deutschland?fresh=17&initialCountry=AT#16/48.2/16.3'
);
assert.equal(
  datasetViewerUrl({ pathname: '/embed/deutschland', search: '?surface=planner', hash: '' }, 'oesterreich'),
  '/embed/deutschland?surface=planner&initialCountry=AT'
);

const austria = viewerDatasetProfile('oesterreich');
assert.deepEqual(austria.terminology, {
  cadastre: 'Kataster',
  cadastralDistrict: 'Katastralgemeinde',
  parcel: 'Grundstück',
  parcelPlural: 'Grundstücke',
  parcelNumber: 'Grundstücksnummer',
  district: ''
});
assert.equal(austria.detailZoom, 16);
assert.equal(austria.aerialZoom, 14);
const unified = unifiedViewerProfile();
assert.equal(unified.id, WORKSPACE_DATASET);
assert.equal(unified.unified, true);
assert.deepEqual(unified.detailZoomByRegion, { deutschland: 17, oesterreich: 16 });
assert.deepEqual(unified.aerialZoomByRegion, { deutschland: 17, oesterreich: 14 });

const style = austriaBasemapStyle();
assert.deepEqual(
  style.sources['basemap-at'].tiles,
  ['https://mapsneu.wien.gv.at/basemap/geolandbasemap/normal/google3857/{z}/{y}/{x}.png']
);
assert.equal(style.sources['basemap-at'].attribution, 'Grundkarte: basemap.at');
assert.deepEqual(
  style.sources['basemap-at-overlay'].tiles,
  ['https://mapsneu.wien.gv.at/basemap/bmapoverlay/normal/google3857/{z}/{y}/{x}.png']
);
assert.equal(style.sources['basemap-at-overlay'].maxzoom, 20);
assert.equal(style.sources['basemap-at-overlay'].attribution, 'Datenquelle: basemap.at');
assert.equal(style.layers.find((layer) => layer.id === 'basemap-at-standard')?.minzoom, COUNTRY_OVERVIEW_MAX_ZOOM);
assert.deepEqual(
  style.layers.find((layer) => layer.id === 'basemap-at-standard')?.paint?.['raster-opacity'],
  ['interpolate', ['linear'], ['zoom'], 5.8, .84, 15.7, .84, 16.2, .62, 17.2, .18]
);
assert.match(layerSource, /id: AT_STREET_OVERLAY_LAYER_ID,[\s\S]*source: AT_STREET_OVERLAY_SOURCE_ID/);
assert.match(layerSource, /bmapoverlay\/normal\/google3857/);
assert.match(layerSource, /const AUSTRIA_SOURCE_BOUNDS = \[9\.35, 46\.3, 17\.2, 49\.1\]/);
assert.equal(
  (layerSource.match(/bounds: AUSTRIA_SOURCE_BOUNDS/g) || []).length,
  2,
  'Both BEV vector sources must be constrained to Austria.'
);
assert.match(layerSource, /id: AT_STREET_LABEL_LAYER_ID,[\s\S]*\['get', 'text'\][\s\S]*\['get', 'rot_nr'\]/);
assert.match(
  layerSource,
  /id: `\$\{AT_LAYER_PREFIX\}symbols`[\s\S]*filter: \['!=', \['to-number', \['get', 'typ'\]\], 200\]/
);
assert.doesNotMatch(layerSource, /austriaDetailFillOpacity/);
assert.match(
  layerSource,
  /id: `\$\{AT_LAYER_PREFIX\}surface-fills`[\s\S]*?'fill-color': AUSTRIA_USAGE_COLOR, 'fill-opacity': 1/
);
assert.match(
  layerSource,
  /id: `\$\{AT_LAYER_PREFIX\}building-fills`[\s\S]*?'fill-color': '#f3b4ae', 'fill-opacity': 1/
);
assert.match(
  layerSource,
  /id: `\$\{AT_LAYER_PREFIX\}building-lines`[\s\S]*?'line-opacity': 1/
);
assert.match(
  layerSource,
  /id: `\$\{AT_LAYER_PREFIX\}parcel-lines`[\s\S]*?'line-opacity': 1/
);
assert.match(
  layerSource,
  /`\$\{AT_LAYER_PREFIX\}building-fills`,[\s\S]*?'fill-opacity',[\s\S]*?austria && detail && layers\.aerial \? \.36 : 1/
);
assert.match(
  layerSource,
  /`\$\{AT_LAYER_PREFIX\}surface-fills`,[\s\S]*?'fill-opacity',[\s\S]*?austria && detail && layers\.aerial \? \.18 : 1/
);
assert.doesNotMatch(
  layerSource,
  /\['streetNames', 'buildingUsage', 'buildingLabels'\]\.includes\(input\.dataset\.layer\)/
);
assert.equal(COUNTRY_OVERVIEW_MAX_ZOOM, 5.8);
assert.deepEqual(
  COUNTRY_OVERVIEW_LABELS.features.map((feature) => [feature.properties.name, feature.geometry.coordinates]),
  [
    ['Deutschland', [10.45, 51.16]],
    ['Österreich', [14.12, 47.58]]
  ]
);

const austriaPalette = new Map();
for (let index = 2; index < AUSTRIA_USAGE_COLOR.length - 1; index += 2) {
  austriaPalette.set(AUSTRIA_USAGE_COLOR[index], AUSTRIA_USAGE_COLOR[index + 1]);
}
for (const code of [59, 60, 64, 88]) assert.equal(austriaPalette.get(code), '#DCEFFF');
assert.equal(austriaPalette.get(61), '#EAFFD3', 'Feuchtgebiete dürfen nicht als Gewässer erscheinen.');
for (const code of [62, 87]) assert.equal(austriaPalette.get(code), '#F2F2EE');
for (const code of [63, 84]) assert.equal(austriaPalette.get(code), '#EDEDED');
assert.equal(AUSTRIA_USAGE_COLOR.at(-1), '#FFFDEE');

function fakeElement({ dataset = {} } = {}) {
  const attributes = new Map();
  return {
    dataset,
    hidden: false,
    disabled: false,
    placeholder: '',
    textContent: '',
    setAttribute(name, value) { attributes.set(name, String(value)); },
    removeAttribute(name) { attributes.delete(name); },
    getAttribute(name) { return attributes.get(name) ?? null; },
    querySelector() { return null; },
    closest() { return null; }
  };
}

const addressInput = fakeElement();
const searchPanel = fakeElement();
const searchModeButton = fakeElement();
const parcelFields = fakeElement();
const gemarkungInput = fakeElement();
const flurInput = fakeElement();
const flurField = fakeElement();
flurInput.closest = () => flurField;
const parcelInput = fakeElement();
const searchSubmitLabel = fakeElement();
const searchSubmit = fakeElement();
searchSubmit.querySelector = () => searchSubmitLabel;
const termCadastre = fakeElement({ dataset: { datasetTerm: 'cadastre' } });
const termParcel = fakeElement({ dataset: { datasetTerm: 'parcel' } });
const byId = {
  addressInput,
  searchPanel,
  searchModeButton,
  parcelFields,
  gemarkungInput,
  flurInput,
  parcelInput,
  searchSubmit
};
const fakeRoot = {
  documentElement: fakeElement(),
  body: fakeElement(),
  getElementById: (id) => byId[id] || null,
  querySelectorAll: () => [termCadastre, termParcel]
};
applyDatasetTerminology(unified, fakeRoot);
assert.equal(addressInput.placeholder, 'Adresse, Flurstück, Grundstück oder POI suchen');
assert.equal(gemarkungInput.placeholder, 'Gemarkung oder Katastralgemeinde');
assert.equal(parcelInput.placeholder, 'Flurstück oder Grundstück');
assert.equal(flurField.hidden, false);
assert.equal(flurInput.disabled, false);
assert.equal(termCadastre.textContent, 'Kataster');
assert.equal(termParcel.textContent, 'Flurstück / Grundstück');

assert.equal(parcelDisplayNumber({ grundstuecksnummer: '.123/2' }), '.123/2');
assert.equal(
  locationLabelFromFeatures(
    { parcels: [{ katastralgemeinde: 'Innere Stadt', grundstuecksnummer: '.123/2' }] },
    austria.terminology
  ),
  'Katastralgemeinde Innere Stadt, Grundstück .123/2'
);

const now = Date.now();
const persistedGermany = { version: 1, savedAt: now, view: { lng: 10, lat: 51, zoom: 8 } };
const persistedAustria = { version: 1, savedAt: now + 1, view: { lng: 16, lat: 48, zoom: 12 } };
const storageValues = new Map([
  ['openkataster:planer-v2:v1:deutschland', JSON.stringify(persistedGermany)],
  ['openkataster:planer-v2:v1:oesterreich', JSON.stringify(persistedAustria)]
]);
globalThis.localStorage = { getItem: (key) => storageValues.get(key) ?? null };
assert.deepEqual(readPersistedState('deutschland'), persistedAustria);
assert.deepEqual(readPersistedState('oesterreich'), persistedAustria);

globalThis.window = { location: { origin: 'https://tiles.openkataster.de' } };
let requestedPath = '';
globalThis.fetch = async (path) => {
  requestedPath = String(path);
  return { ok: true, json: async () => ({ results: [] }) };
};
const api = createApi({ dataset: 'oesterreich' });
await api.searchParcel({ gemarkung: '01001', flurstueck: '.123/2' });
assert.match(requestedPath, /^\/api\/v1\/search\/parcel\?/);
assert.match(requestedPath, /dataset=oesterreich/);
assert.match(requestedPath, /gemarkung=01001/);
assert.match(requestedPath, /flur=&flurstueck=.123%2F2/);

assert.doesNotMatch(indexSource, /class="dataset-switch"/);
assert.match(appSource, /const api = createUnifiedApi\(/);
assert.doesNotMatch(appSource, /openkataster:request-dataset/);

assert.match(layerSource, /\/api\/v1\/bev\/tiles\/kataster\/\{z\}\/\{x\}\/\{y\}\.pbf/);
assert.match(layerSource, /\/api\/v1\/bev\/tiles\/symbole\/\{z\}\/\{x\}\/\{y\}\.pbf/);
assert.match(layerSource, /setLayerZoomRange\('Borders_States_Precise', COUNTRY_OVERVIEW_MAX_ZOOM, 22\)/);
assert.match(layerSource, /setLayerZoomRange\('Labels_States_GeoJSON', COUNTRY_OVERVIEW_MAX_ZOOM, 24\)/);
assert.match(layerSource, /const AT_AERIAL_ZOOM = Number\(datasetProfile\.aerialZoomByRegion\?\.oesterreich \|\| datasetProfile\.aerialZoom \|\| 14\)/);
assert.match(layerSource, /updateAerial\(aerialDetail && layers\.aerial\)/);
assert.match(layerSource, /layerMenu\.dataset\.detailUnavailable = detail \|\| aerialDetail \? 'false' : 'true'/);
for (const sourceLayer of ['nfl', 'sli', 'gst', 'gnr', 'hnr', 'gp', 'ssb']) {
  assert.match(layerSource, new RegExp(`'source-layer': '${sourceLayer}'`));
}
assert.match(selectionSource, /flaechenbestimmung: 'Flächenbestimmung'/);
assert.match(selectionSource, /rechtsstatus_text: 'Rechtsstatus'/);

const squareAustria = {
  type: 'Feature',
  properties: { country_code: 'AT' },
  geometry: {
    type: 'Polygon',
    coordinates: [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
  }
};
const resolver = createCountryResolver({
  fetchImpl: async () => ({ ok: true, json: async () => squareAustria }),
  countriesUrl: '/fixture/austria.json'
});
await resolver.ready();
assert.equal(resolver.datasetAt(5, 5), 'oesterreich');
assert.equal(resolver.datasetAt(11, 5), 'deutschland');
assert.equal(resolver.intersectsAustria({ west: 9, south: 4, east: 11, north: 6 }), true);
assert.equal(resolver.intersectsAustria({ west: 11, south: 4, east: 12, north: 6 }), false);
assert.equal(resolver.containsAustria({ west: 2, south: 2, east: 4, north: 4 }), true);
assert.equal(resolver.containsAustria({ west: 9, south: 4, east: 11, north: 6 }), false);

const originalWarn = console.warn;
console.warn = () => {};
const failedResolver = createCountryResolver({
  fetchImpl: async () => ({ ok: false }),
  countriesUrl: '/fixture/missing.json'
});
await failedResolver.ready();
console.warn = originalWarn;
assert.equal(failedResolver.datasetAt(11.5, 48.1), 'deutschland');
assert.equal(failedResolver.intersectsAustria({ west: 11, south: 47, east: 12, north: 48 }), false);

globalThis.fetch = async (path) => {
  const url = new URL(String(path), globalThis.window.location.origin);
  const dataset = url.searchParams.get('dataset');
  const prefix = dataset === 'oesterreich' ? 'AT' : 'DE';
  return {
    ok: true,
    json: async () => ({
      results: [
        { label: `${prefix} 1`, center: dataset === 'oesterreich' ? [16, 48] : [10, 51] },
        { label: `${prefix} 2`, center: dataset === 'oesterreich' ? [16.1, 48.1] : [10.1, 51.1] }
      ]
    })
  };
};
const unifiedApi = createUnifiedApi();
const federated = await unifiedApi.suggestAddresses({ query: 'Test', limit: 4 });
assert.deepEqual(federated.results.map((result) => result.label), ['DE 1', 'AT 1', 'DE 2', 'AT 2']);
assert.deepEqual(federated.results.map((result) => result.dataset), ['deutschland', 'oesterreich', 'deutschland', 'oesterreich']);

console.log('austria-viewer-tests=ok');
