import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  createAnalyticsId,
  createAnalyticsMarker,
  createApi,
  withAnalytics
} from '../live-viewer/viewer-app/api.js';
import {
  addressSuggestionResolutionContext,
  committedAddressSuggestion,
  searchResultTypeLabel,
  selectionPreferenceForSearchResult
} from '../live-viewer/viewer-app/search.js';

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
assert.equal(createAnalyticsMarker('address').analytics_scope, 'address');
assert.equal(createAnalyticsMarker('poi').analytics_scope, 'poi');
assert.equal(withAnalytics('/api/test?client=viewer', null), '/api/test?client=viewer');
assert.equal(
  withAnalytics('/api/test?client=viewer', { analytics_id: 'event 1', analytics_scope: 'address' }),
  '/api/test?client=viewer&analytics_id=event%201&analytics_scope=address'
);

const api = createApi({ token: 'viewer-secret', fresh: 'fresh-value' });
const signal = new AbortController().signal;

await api.suggestSearch(
  { query: 'Hauptstraße 12', nearLon: 6.95, nearLat: 50.94, limit: 8 },
  signal
);
let call = calls.at(-1);
let url = new URL(call.path, window.location.origin);
assert.equal(url.pathname, '/api/v1/suggest/search');
assert.equal(url.searchParams.get('q'), 'Hauptstraße 12');
assert.equal(url.searchParams.get('near_lon'), '6.95');
assert.equal(url.searchParams.get('near_lat'), '50.94');
assert.equal(url.searchParams.get('limit'), '8');
assert.equal(url.searchParams.get('client'), 'viewer');
assert.equal(url.searchParams.get('fresh'), 'fresh-value');
assert.equal(url.searchParams.get('token'), null);
assert.equal(url.searchParams.get('analytics_id'), null, 'autocomplete must remain untracked');
assert.equal(call.options.headers.get('Authorization'), 'Bearer viewer-secret');
assert.equal(call.options.signal, signal);

assert.equal(api.setToken(''), false, 'an empty refresh must not erase a working viewer token');
await api.session();
assert.equal(calls.at(-1).options.headers.get('Authorization'), 'Bearer viewer-secret');
assert.equal(api.setToken('viewer-rotated'), true);
await api.session();
assert.equal(calls.at(-1).options.headers.get('Authorization'), 'Bearer viewer-rotated');
assert.equal(api.setToken('viewer-secret'), true);

await api.suggestGemarkungen('Hausen', signal);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('limit'), '50', 'all current exact Gemarkung homonyms must remain selectable');
assert.equal(url.searchParams.get('analytics_id'), null, 'Gemarkung autocomplete must remain untracked');

await api.searchAddress(
  { query: '50667 Köln Hauptstraße 12', analyticsQuery: 'Hauptstr 12 Köln', state: 'nordrhein-westfalen', nearLon: 6.96, nearLat: 50.94 },
  signal,
  { analytics_id: 'address-id', analytics_scope: 'address' }
);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.pathname, '/api/v1/search/address');
assert.equal(url.searchParams.get('q'), '50667 Köln Hauptstraße 12');
assert.equal(url.searchParams.get('state'), 'nordrhein-westfalen', 'free-text search must retain the selected suggestion state');
assert.equal(url.searchParams.get('analytics_query'), 'Hauptstr 12 Köln');
assert.equal(url.searchParams.get('place'), null);
assert.equal(url.searchParams.get('near_lon'), '6.96');
assert.equal(url.searchParams.get('near_lat'), '50.94');
assert.equal(url.searchParams.get('analytics_id'), 'address-id');
assert.equal(url.searchParams.get('analytics_scope'), 'address');
assert.equal(url.searchParams.get('token'), null);
assert.equal(call.options.headers.get('Authorization'), 'Bearer viewer-secret');

await api.searchAddress(
  { place: 'Köln', street: 'Hauptstraße', houseNumber: '12', state: 'nordrhein-westfalen' },
  signal
);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('q'), null, 'the structured embed fallback remains compatible');
assert.equal(url.searchParams.get('place'), 'Köln');
assert.equal(url.searchParams.get('street'), 'Hauptstraße');
assert.equal(url.searchParams.get('house_number'), '12');
assert.equal(url.searchParams.get('state'), 'nordrhein-westfalen');

const schwerinMv = {
  label: 'Schwerin',
  result_type: 'place',
  state: 'mecklenburg-vorpommern',
  center: [11.41443371543698, 53.62854536881294]
};
const schwerinBrandenburg = {
  label: 'Schwerin',
  result_type: 'place',
  state: 'brandenburg',
  center: [13.643450314379406, 52.1542973855304]
};
assert.deepEqual(addressSuggestionResolutionContext(schwerinMv), {
  state: 'mecklenburg-vorpommern',
  nearLon: 11.41443371543698,
  nearLat: 53.62854536881294
});
assert.equal(
  committedAddressSuggestion(schwerinMv, [schwerinBrandenburg, schwerinMv]),
  schwerinMv,
  'a clicked homonymous place must retain its exact identity even if a re-search ranks another state first'
);
assert.equal(
  committedAddressSuggestion({ label: 'Legacy result without geometry' }, [schwerinBrandenburg]),
  schwerinBrandenburg,
  'legacy suggestions without geometry keep the backend-resolution fallback'
);

await api.searchParcel(
  { gemarkung: 'Hofen (0976)', flurstueck: '1066', state: 'baden-wurttemberg', analyticsQuery: 'Flurstück 1066 in Hofen' },
  signal,
  { analytics_id: 'parcel-id', analytics_scope: 'parcel' }
);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('analytics_id'), 'parcel-id');
assert.equal(url.searchParams.get('analytics_scope'), 'parcel');
assert.equal(url.searchParams.get('gemarkung'), 'Hofen (0976)');
assert.equal(url.searchParams.get('flur'), '');
assert.equal(url.searchParams.get('flurstueck'), '1066');
assert.equal(url.searchParams.get('state'), 'baden-wurttemberg');
assert.equal(url.searchParams.get('analytics_query'), 'Flurstück 1066 in Hofen');

await api.searchPoi(
  { poiId: 'osm:n:123456', analyticsQuery: 'Hauptbahnhof Hannover' },
  signal,
  { analytics_id: 'poi-id', analytics_scope: 'poi' }
);
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.pathname, '/api/v1/search/poi');
assert.equal(url.searchParams.get('poi_id'), 'osm:n:123456');
assert.equal(url.searchParams.get('analytics_query'), 'Hauptbahnhof Hannover');
assert.equal(url.searchParams.get('analytics_id'), 'poi-id');
assert.equal(url.searchParams.get('analytics_scope'), 'poi');

await api.featurePreviewAt(6.95, 50.94, signal, createAnalyticsMarker('map_selection'));
call = calls.at(-1);
url = new URL(call.path, window.location.origin);
assert.equal(url.searchParams.get('analytics_id'), null);
assert.equal(url.searchParams.get('analytics_scope'), null);

await api.createOrder({ test: true });
call = calls.at(-1);
assert.equal(call.path, '/api/orders');
assert.equal(new Headers(call.options.headers).has('Authorization'), false, 'viewer credentials stay on viewer API calls');

const retryCalls = [];
let refreshRequests = 0;
let retryApi;
globalThis.fetch = async (path, options = {}) => {
  retryCalls.push({ path, options });
  if (retryCalls.length === 1) return { ok: false, status: 401, statusText: 'Unauthorized' };
  return { ok: true, json: async () => ({ buildings: [], parcels: [] }) };
};
retryApi = createApi({
  token: 'viewer-expired',
  requestTokenRefresh() {
    refreshRequests += 1;
    queueMicrotask(() => retryApi.setToken('viewer-renewed'));
  }
});
await retryApi.featureAt(9.84, 52.33, signal);
assert.equal(refreshRequests, 1, 'one 401 must request exactly one parent token refresh');
assert.equal(retryCalls.length, 2, 'the failed viewer request must be retried exactly once');
assert.equal(retryCalls[0].options.headers.get('Authorization'), 'Bearer viewer-expired');
assert.equal(retryCalls[1].options.headers.get('Authorization'), 'Bearer viewer-renewed');

const noLoopCalls = [];
let noLoopRefreshRequests = 0;
let noLoopApi;
globalThis.fetch = async (path, options = {}) => {
  noLoopCalls.push({ path, options });
  return { ok: false, status: 401, statusText: 'Unauthorized' };
};
noLoopApi = createApi({
  token: 'viewer-expired-again',
  requestTokenRefresh() {
    noLoopRefreshRequests += 1;
    queueMicrotask(() => noLoopApi.setToken('viewer-renewed-once'));
  }
});
await assert.rejects(
  noLoopApi.featureAt(9.84, 52.33, signal),
  /401 Unauthorized/,
  'a second 401 must escape instead of entering a refresh loop'
);
assert.equal(noLoopRefreshRequests, 1);
assert.equal(noLoopCalls.length, 2, 'a request may be retried at most once');
assert.equal(noLoopCalls[1].options.headers.get('Authorization'), 'Bearer viewer-renewed-once');

const root = fileURLToPath(new URL('..', import.meta.url));
const [searchSource, selectionSource, viewerHtml, appSource] = await Promise.all([
  fs.readFile(`${root}/live-viewer/viewer-app/search.js`, 'utf8'),
  fs.readFile(`${root}/live-viewer/viewer-app/selection.js`, 'utf8'),
  fs.readFile(`${root}/live-viewer/viewer-app/index.html`, 'utf8'),
  fs.readFile(`${root}/live-viewer/viewer-app/app.js`, 'utf8')
]);
assert.match(searchSource, /api\.suggestSearch\(\s*\{ query, \.\.\.nearbySearchOptions\(8\) \},\s*suggestionRequest\.signal/s, 'mixed autocomplete must use the untracked unified endpoint');
assert.doesNotMatch(searchSource, /api\.suggestAddresses|api\.suggestPlaces|api\.suggestStreets/, 'the search controller must use only the mixed endpoint');
assert.equal((searchSource.match(/createAnalyticsMarker\('address'\)/g) || []).length, 1, 'submit and suggestion click must share one tracked-search helper');
assert.equal((searchSource.match(/createAnalyticsMarker\('parcel'\)/g) || []).length, 2, 'mixed suggestions and the structured fallback each create one parcel marker in their exclusive paths');
assert.equal((searchSource.match(/createAnalyticsMarker\('poi'\)/g) || []).length, 1, 'a selected POI creates one deliberate POI event');
assert.doesNotMatch(searchSource, /createAnalyticsMarker\('(place|street)'\)/);
assert.match(
  searchSource,
  /api\.searchAddress\(\{[\s\S]*query,[\s\S]*nearLon: fallbackCenter\[0\],[\s\S]*nearLat: fallbackCenter\[1\],[\s\S]*limit: 8[\s\S]*\}, searchRequest\?\.signal\)/,
  'an addressed POI resolves its ALKIS address near the POI without creating another analytics event'
);
assert.match(searchSource, /trackedAddressSearch\([\s\S]*selectedQuery,[\s\S]*searchRequest\.signal,[\s\S]*12,[\s\S]*typedQuery,[\s\S]*addressSuggestionResolutionContext\(result\)[\s\S]*\)/, 'a clicked suggestion creates one address event scoped to the selected identity');
assert.match(searchSource, /const typedQuery = addressInput\.value\.trim\(\);[\s\S]*trackedAddressSearch\([\s\S]*typedQuery,[\s\S]*addressSuggestionResolutionContext\(result\)/, 'suggestion analytics retain the actual typed input and selected context');
assert.match(searchSource, /\{ \.\.\.parcelSearch, analyticsQuery: typedQuery \}[\s\S]*createAnalyticsMarker\('parcel'\)/, 'parcel suggestions resolve through the structured endpoint while recording the original free text once');
const commitSuggestionSource = searchSource.slice(
  searchSource.indexOf('async function commitSuggestion(result)'),
  searchSource.indexOf('async function chooseResult(result)')
);
assert.ok(commitSuggestionSource.length > 0, 'the mixed suggestion commit path must remain testable');
assert.equal((commitSuggestionSource.match(/api\.searchParcel\(/g) || []).length, 1, 'a parcel suggestion performs exactly one exact parcel resolution');
assert.equal((commitSuggestionSource.match(/createAnalyticsMarker\('parcel'\)/g) || []).length, 1, 'a parcel suggestion records exactly one parcel search event');
assert.equal((commitSuggestionSource.match(/analyticsQuery: typedQuery/g) || []).length, 2, 'parcel and POI resolution each forward the original input exactly once');
assert.equal((commitSuggestionSource.match(/api\.searchPoi\(/g) || []).length, 1, 'a POI suggestion resolves through the exact POI endpoint');
assert.match(commitSuggestionSource, /resolved = \(data\.results \|\| \[\]\)\[0\]/, 'only an exact resolved parcel result may be selected');
assert.match(commitSuggestionSource, /trackedAddressSearch\([\s\S]*addressSuggestionResolutionContext\(result\)[\s\S]*\)/, 'address suggestions resolve with the selected state and center');
assert.match(commitSuggestionSource, /resolved = committedAddressSuggestion\(result, data\.results \|\| \[\]\)/, 'the clicked suggestion identity wins over an ambiguously reranked text result');
assert.match(commitSuggestionSource, /chooseResult\(\{ \.\.\.resolved, search_scope: scope \}\)/, 'the resolved suggestion kind is preserved for selection behavior');
assert.match(searchSource, /nearbySearchOptions\(limit\)/, 'map center must be forwarded for ranking');
assert.match(searchSource, /event\.key === 'ArrowDown'/);
assert.match(searchSource, /event\.key === 'ArrowUp'/);
assert.match(searchSource, /event\.key === 'Escape'/);
assert.match(searchSource, /suggestedResults\[activeSuggestion >= 0 \? activeSuggestion : 0\]/, 'Enter opens the highlighted suggestion or the first suggestion without requiring a search button');
assert.match(searchSource, /setActiveSuggestion\(activeSuggestion \+ 1\)/, 'ArrowDown moves through the shared result list');
assert.match(searchSource, /setActiveSuggestion\(activeSuggestion < 0 \? suggestedResults\.length - 1 : activeSuggestion - 1\)/, 'ArrowUp wraps through the shared result list');
assert.match(searchSource, /addressInput\.setAttribute\('aria-activedescendant', `search-suggestion-\$\{activeSuggestion\}`\)/, 'keyboard focus is exposed to assistive technology');
assert.match(searchSource, /button\.setAttribute\('aria-selected', active \? 'true' : 'false'\)/, 'the active mixed suggestion exposes aria-selected');
assert.match(searchSource, /selectionPreference === 'all' \? null : selectionPreference/, 'address selection must still include building and parcel');
assert.equal(selectionPreferenceForSearchResult({ search_scope: 'address', kind: 'place' }), null, 'place suggestions must only move the map');
assert.equal(selectionPreferenceForSearchResult({ search_scope: 'address', kind: 'street' }), null, 'street suggestions must only move the map');
assert.equal(selectionPreferenceForSearchResult({ search_scope: 'address', result_type: 'address' }), 'all');
assert.equal(selectionPreferenceForSearchResult({ search_scope: 'parcel', kind: 'parcel' }), 'parcel');
assert.equal(selectionPreferenceForSearchResult({ search_scope: 'poi', kind: 'poi' }), null, 'an addressless POI only receives its own marker');
assert.equal(
  selectionPreferenceForSearchResult({
    search_scope: 'poi',
    kind: 'poi',
    feature: { street: 'Ernst-August-Platz', house_number: '1' }
  }),
  'all',
  'an addressed POI selects every intersecting ALKIS building and parcel'
);
assert.equal(searchResultTypeLabel({ kind: 'place' }), 'Ort');
assert.equal(searchResultTypeLabel({ result_type: 'street' }), 'Straße');
assert.equal(searchResultTypeLabel({ result_type: 'address' }), 'Adresse');
assert.equal(searchResultTypeLabel({ search_scope: 'parcel' }), 'Flurstück');
assert.equal(searchResultTypeLabel({ search_scope: 'poi' }), 'POI');
assert.match(searchSource, /function handleSearchInput\(\)[\s\S]*clearPoiMarker\(\);[\s\S]*suggestSearch\(\)/, 'typing a different query clears a stale POI marker immediately');
assert.doesNotMatch(selectionSource, /createAnalyticsMarker|map_selection/);
assert.match(selectionSource, /async function selectAt\(lngLat, additive = false, preferredKind = null\)/);
assert.match(selectionSource, /selectAt\(event\.lngLat, true\)/);
assert.match(searchSource, /\{ gemarkung, flur, flurstueck, state: selectedGemarkungState \}/);
assert.match(searchSource, /gemarkungInput\.value = label;/, 'selected Gemarkung code must stay visible');
assert.doesNotMatch(searchSource, /!gemarkung \|\| !flur \|\| !flurstueck/);
assert.match(searchSource, /Viele Treffer – bitte \$\{terms\.district\} zur Eingrenzung eingeben\./);
assert.match(viewerHtml, /id="addressInput"[^>]*placeholder="Adresse, Flurstück oder POI suchen"/);
assert.match(viewerHtml, /id="addressInput"[^>]*role="combobox"[^>]*aria-controls="searchSuggestions"/);
assert.match(viewerHtml, /id="searchSuggestions"[^>]*role="listbox"/);
assert.match(searchSource, /class="search-result-type search-result-type-\$\{scope\}"/, 'address and parcel suggestions share one renderer with kind badges');
assert.match(searchSource, /if \(type === 'place'\) return 'Ort';[\s\S]*if \(type === 'street'\) return 'Straße';[\s\S]*return 'Adresse';/, 'mixed result badges use precise German kind labels');
assert.doesNotMatch(viewerHtml, /id="(placeInput|streetInput|houseInput|placeSuggestions|streetSuggestions)"/);
assert.match(viewerHtml, /placeholder="Flur optional"/);
assert.match(appSource, /search\.searchAddress\(String\(address\.query \|\| ''\)\.trim\(\) \|\|/, 'embed address searches must use the controller contract');
assert.match(appSource, /requestTokenRefresh: \(\) => postToParent\('openkataster:request-viewer-token'\)/);
assert.match(appSource, /message\.type === 'openkataster:set-viewer-token'/);
assert.match(appSource, /if \(!api\.setToken\(message\.token\)\) return;/);
assert.match(appSource, /refreshAccess\(\{ preserveCompatibleSelection: true \}\)/);
assert.match(searchSource, /async function searchAddress\(query\)[\s\S]*renderResults\(results\);[\s\S]*setBusy\(false, results\.length \? '' : 'Keine Treffer'\);/, 'embed searches retain the existing result-choice behavior');

console.log('viewer-search-analytics-tests=ok');
