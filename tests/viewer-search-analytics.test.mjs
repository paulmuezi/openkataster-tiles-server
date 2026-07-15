import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  createAnalyticsId,
  createAnalyticsMarker,
  createApi,
  withAnalytics
} from '../live-viewer/viewer-app/api.js';

const calls = [];
globalThis.window = { location: { origin: 'https://viewer.example.test' } };
globalThis.fetch = async (path, options = {}) => {
  calls.push({ path, options });
  return { ok: true, json: async () => ({ results: [] }) };
};

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
assert.match(createAnalyticsId(), uuidPattern);
assert.equal(createAnalyticsMarker('invalid'), null);
assert.equal(createAnalyticsMarker('map_selection'), null, 'map clicks must never create search events');
assert.equal(createAnalyticsMarker('street').analytics_scope, 'street');
assert.equal(withAnalytics('/api/test?client=viewer', null), '/api/test?client=viewer');
assert.equal(
  withAnalytics('/api/test?client=viewer', { analytics_id: 'event 1', analytics_scope: 'place' }),
  '/api/test?client=viewer&analytics_id=event%201&analytics_scope=place'
);

const api = createApi({ token: 'viewer-secret', fresh: 'fresh-value' });
const signal = new AbortController().signal;

await api.suggestPlaces('Köln', signal);
let call = calls.at(-1);
let url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('client'), 'viewer');
assert.equal(url.searchParams.get('fresh'), 'fresh-value');
assert.equal(url.searchParams.get('token'), null);
assert.equal(url.searchParams.get('analytics_id'), null, 'autocomplete must remain untracked');
assert.equal(call.options.headers.get('Authorization'), 'Bearer viewer-secret');
assert.equal(call.options.signal, signal);

await api.suggestGemarkungen('Hausen', signal);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('limit'), '50', 'all current exact Gemarkung homonyms must remain selectable');
assert.equal(url.searchParams.get('analytics_id'), null, 'autocomplete must remain untracked');

const cases = [
  ['place', () => api.suggestPlaces('Köln', signal, { analytics_id: 'place-id', analytics_scope: 'place' })],
  ['street', () => api.suggestStreets('Köln', 'Hauptstr.', 'Nordrhein-Westfalen', signal, { analytics_id: 'street-id', analytics_scope: 'street' })],
  ['address', () => api.searchAddress({ place: 'Köln', street: 'Hauptstr.', houseNumber: '1' }, signal, { analytics_id: 'address-id', analytics_scope: 'address' })],
  ['parcel', () => api.searchParcel({ gemarkung: 'Hofen (0976)', flurstueck: '1066', state: 'baden-wurttemberg' }, signal, { analytics_id: 'parcel-id', analytics_scope: 'parcel' })]
];
for (const [scope, invoke] of cases) {
  await invoke();
  call = calls.at(-1);
  url = new URL(call.path, window.location.origin);
  assert.equal(url.searchParams.get('analytics_id'), `${scope}-id`);
  assert.equal(url.searchParams.get('analytics_scope'), scope);
  assert.equal(url.searchParams.get('token'), null);
  assert.equal(call.options.headers.get('Authorization'), 'Bearer viewer-secret');
}

await api.featurePreviewAt(6.95, 50.94, signal, createAnalyticsMarker('map_selection'));
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('analytics_id'), null);
assert.equal(url.searchParams.get('analytics_scope'), null);

url = new URL(calls.findLast(({ path }) => path.includes('/api/v1/search/parcel')).path, window.location.origin);
assert.equal(url.searchParams.get('gemarkung'), 'Hofen (0976)');
assert.equal(url.searchParams.get('flur'), '');
assert.equal(url.searchParams.get('flurstueck'), '1066');
assert.equal(url.searchParams.get('state'), 'baden-wurttemberg');

await api.createOrder({ test: true });
call = calls.at(-1);
assert.equal(call.path, '/api/orders');
assert.equal(new Headers(call.options.headers).has('Authorization'), false, 'viewer credentials stay on viewer API calls');

const root = fileURLToPath(new URL('..', import.meta.url));
const [searchSource, selectionSource, viewerHtml] = await Promise.all([
  fs.readFile(`${root}/live-viewer/viewer-app/search.js`, 'utf8'),
  fs.readFile(`${root}/live-viewer/viewer-app/selection.js`, 'utf8'),
  fs.readFile(`${root}/live-viewer/viewer-app/index.html`, 'utf8')
]);
assert.match(searchSource, /suggestPlaces\(query, placeRequest\.signal\)/, 'place autocomplete must not receive a marker');
assert.match(searchSource, /suggestStreets\(place, query, selectedPlaceState, streetRequest\.signal\)/, 'street autocomplete must not receive a marker');
for (const scope of ['place', 'street', 'address', 'parcel']) {
  assert.match(searchSource, new RegExp(`createAnalyticsMarker\\('${scope}'\\)`), `submit must mark ${scope}`);
}
assert.doesNotMatch(selectionSource, /createAnalyticsMarker|map_selection/);
assert.match(selectionSource, /async function selectAt\(lngLat, additive = false, preferredKind = null\)/);
assert.match(selectionSource, /selectAt\(event\.lngLat, true\)/);
assert.match(searchSource, /\{ gemarkung, flur, flurstueck, state: selectedGemarkungState \}/);
assert.match(searchSource, /gemarkungInput\.value = label;/, 'selected Gemarkung code must stay visible');
assert.doesNotMatch(searchSource, /!gemarkung \|\| !flur \|\| !flurstueck/);
assert.match(searchSource, /Viele Treffer – bitte Flur zur Eingrenzung eingeben\./);
assert.match(viewerHtml, /placeholder="Flur optional"/);

console.log('viewer-search-analytics-tests=ok');
