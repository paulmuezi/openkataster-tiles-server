import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createApi } from '../live-viewer/viewer-app/api.js';
import {
  applyDatasetTerminology,
  austriaBasemapStyle,
  datasetIdFromLocation,
  datasetViewerUrl,
  viewerDatasetProfile
} from '../live-viewer/viewer-app/dataset.js';
import { locationLabelFromFeatures } from '../live-viewer/viewer-app/export.js';
import { readPersistedState } from '../live-viewer/viewer-app/persistence.js';
import { parcelDisplayNumber } from '../live-viewer/viewer-app/selection.js';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const layerSource = readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');

assert.equal(datasetIdFromLocation({ pathname: '/viewer/oesterreich', search: '' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/embed/deutschland', search: '?dataset=oesterreich' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/planer', search: '?dataset=oesterreich' }), 'oesterreich');
assert.equal(datasetIdFromLocation({ pathname: '/viewer/unbekannt', search: '' }), 'deutschland');

assert.equal(
  datasetViewerUrl(
    { pathname: '/viewer/deutschland', search: '?fresh=17&dataset=deutschland', hash: '#16/48.2/16.3' },
    'oesterreich'
  ),
  '/viewer/oesterreich?fresh=17&dataset=oesterreich#16/48.2/16.3'
);
assert.equal(
  datasetViewerUrl({ pathname: '/embed/deutschland', search: '?surface=planner', hash: '' }, 'oesterreich'),
  '/embed/oesterreich?surface=planner&dataset=oesterreich'
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
assert.equal(austria.detailZoom, 14);

const style = austriaBasemapStyle();
assert.deepEqual(
  style.sources['basemap-at'].tiles,
  ['https://mapsneu.wien.gv.at/basemap/bmapgrau/normal/google3857/{z}/{y}/{x}.png']
);
assert.equal(style.sources['basemap-at'].attribution, 'Grundkarte: basemap.at');

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
const switchGermany = fakeElement({ dataset: { datasetSwitch: 'deutschland' } });
const switchAustria = fakeElement({ dataset: { datasetSwitch: 'oesterreich' } });
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
  querySelectorAll: (selector) => selector === '[data-dataset-switch]'
    ? [switchGermany, switchAustria]
    : [termCadastre, termParcel]
};
applyDatasetTerminology(austria, fakeRoot);
assert.equal(addressInput.placeholder, 'Adresse oder Grundstück suchen');
assert.equal(gemarkungInput.placeholder, 'Katastralgemeinde erforderlich');
assert.equal(parcelInput.placeholder, 'Grundstück erforderlich');
assert.equal(flurField.hidden, true);
assert.equal(flurInput.disabled, true);
assert.equal(termCadastre.textContent, 'Kataster');
assert.equal(termParcel.textContent, 'Grundstück');
assert.equal(switchGermany.getAttribute('aria-pressed'), 'false');
assert.equal(switchAustria.getAttribute('aria-pressed'), 'true');
assert.equal(switchAustria.getAttribute('aria-current'), 'true');

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
const persistedAustria = { version: 1, savedAt: now, view: { lng: 16, lat: 48, zoom: 12 } };
const storageValues = new Map([
  ['openkataster:planer-v2:v1:deutschland', JSON.stringify(persistedGermany)],
  ['openkataster:planer-v2:v1:oesterreich', JSON.stringify(persistedAustria)]
]);
globalThis.localStorage = { getItem: (key) => storageValues.get(key) ?? null };
assert.deepEqual(readPersistedState('deutschland'), persistedGermany);
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

assert.match(indexSource, /class="dataset-switch" role="group" aria-label="Land auswählen"/);
assert.match(indexSource, /data-dataset-switch="deutschland" aria-pressed="true"/);
assert.match(indexSource, /data-dataset-switch="oesterreich" aria-pressed="false"/);
assert.match(appSource, /postToParent\('openkataster:request-dataset', \{ source: workspaceDataset, target \}\)/);
assert.match(appSource, /window\.location\.assign\(datasetViewerUrl\(window\.location, target\)\)/);

assert.match(layerSource, /\/bev\/tiles\/kataster\/\{z\}\/\{x\}\/\{y\}\.pbf/);
assert.match(layerSource, /\/bev\/tiles\/symbole\/\{z\}\/\{x\}\/\{y\}\.pbf/);
for (const sourceLayer of ['nfl', 'sli', 'gst', 'gnr', 'hnr', 'gp', 'ssb']) {
  assert.match(layerSource, new RegExp(`'source-layer': '${sourceLayer}'`));
}

console.log('austria-viewer-tests=ok');
