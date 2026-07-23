const ANALYTICS_SCOPES = new Set(['place', 'street', 'address', 'parcel', 'poi']);

export function createAnalyticsId() {
  try {
    if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  } catch (_) {
    // Fall through to an RFC-4122-shaped local ID. Analytics must never block search.
  }

  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createAnalyticsMarker(scope) {
  if (!ANALYTICS_SCOPES.has(scope)) return null;
  return { analytics_id: createAnalyticsId(), analytics_scope: scope };
}

export function withAnalytics(path, marker) {
  const analyticsId = String(marker?.analytics_id || '').trim();
  const analyticsScope = String(marker?.analytics_scope || '').trim();
  if (!analyticsId || !ANALYTICS_SCOPES.has(analyticsScope)) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}analytics_id=${encodeURIComponent(analyticsId)}&analytics_scope=${encodeURIComponent(analyticsScope)}`;
}

export function createApi({ token = '', fresh = '', dataset = 'deutschland', requestTokenRefresh = null } = {}) {
  let viewerToken = String(token || '').trim();
  const viewerDataset = /^[a-z0-9_-]+$/.test(String(dataset || '')) ? String(dataset) : 'deutschland';
  let pendingTokenRefresh = null;
  let resolveTokenRefresh = null;
  let rejectTokenRefresh = null;
  let tokenRefreshTimer = 0;

  function setToken(nextToken) {
    const normalized = typeof nextToken === 'string' ? nextToken.trim() : '';
    if (!normalized || normalized === viewerToken) return false;
    viewerToken = normalized;
    if (resolveTokenRefresh) resolveTokenRefresh(normalized);
    clearTokenRefreshWait();
    return true;
  }

  function clearTokenRefreshWait() {
    if (tokenRefreshTimer) globalThis.clearTimeout(tokenRefreshTimer);
    tokenRefreshTimer = 0;
    pendingTokenRefresh = null;
    resolveTokenRefresh = null;
    rejectTokenRefresh = null;
  }

  function abortable(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(signal.reason || new DOMException('Aborted', 'AbortError'));
    return new Promise((resolve, reject) => {
      const abort = () => reject(signal.reason || new DOMException('Aborted', 'AbortError'));
      signal.addEventListener('abort', abort, { once: true });
      promise.then(
        (value) => { signal.removeEventListener('abort', abort); resolve(value); },
        (error) => { signal.removeEventListener('abort', abort); reject(error); }
      );
    });
  }

  function waitForTokenRefresh(staleToken, signal) {
    if (viewerToken && viewerToken !== staleToken) return Promise.resolve(viewerToken);
    if (!pendingTokenRefresh) {
      pendingTokenRefresh = new Promise((resolve, reject) => {
        resolveTokenRefresh = resolve;
        rejectTokenRefresh = reject;
      });
      tokenRefreshTimer = globalThis.setTimeout(() => {
        const reject = rejectTokenRefresh;
        clearTokenRefreshWait();
        reject?.(new Error('Viewer token refresh timed out'));
      }, 10000);
      try {
        requestTokenRefresh?.();
      } catch (error) {
        const reject = rejectTokenRefresh;
        clearTokenRefreshWait();
        reject?.(error);
      }
    }
    return abortable(pendingTokenRefresh, signal);
  }

  function viewerUrl(path) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set('client', 'viewer');
    url.searchParams.set('dataset', viewerDataset);
    if (fresh) url.searchParams.set('fresh', fresh);
    return `${url.pathname}${url.search}`;
  }

  async function json(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
      const error = new Error(`${response.status} ${response.statusText}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function viewerJson(path, options = {}, retried = false) {
    const headers = new Headers(options.headers || undefined);
    const usesViewerToken = !headers.has('Authorization');
    const requestToken = viewerToken;
    if (viewerToken && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${viewerToken}`);
    try {
      return await json(path, { ...options, headers });
    } catch (error) {
      if (retried || error?.status !== 401 || !usesViewerToken || typeof requestTokenRefresh !== 'function') throw error;
      await waitForTokenRefresh(requestToken, options.signal);
      return viewerJson(path, options, true);
    }
  }

  function nearbyQuery({ nearLon = null, nearLat = null } = {}) {
    if (nearLon === null || nearLon === '' || nearLat === null || nearLat === '') return '';
    const longitude = Number(nearLon);
    const latitude = Number(nearLat);
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return '';
    return `&near_lon=${encodeURIComponent(longitude)}&near_lat=${encodeURIComponent(latitude)}`;
  }

  function addressSearchPath({ query = '', analyticsQuery = '', place = '', street = '', houseNumber = '', state = '', nearLon = null, nearLat = null, limit = 12 } = {}) {
    const base = viewerUrl('/api/v1/search/address');
    const normalizedQuery = String(query || '').trim();
    const stateQuery = `&state=${encodeURIComponent(state)}`;
    const search = normalizedQuery
      ? `&q=${encodeURIComponent(normalizedQuery)}${stateQuery}`
      : `&place=${encodeURIComponent(place)}&street=${encodeURIComponent(street)}&house_number=${encodeURIComponent(houseNumber)}${stateQuery}`;
    const analyticsInput = String(analyticsQuery || '').trim();
    return `${base}${search}${analyticsInput ? `&analytics_query=${encodeURIComponent(analyticsInput)}` : ''}${nearbyQuery({ nearLon, nearLat })}&limit=${encodeURIComponent(limit)}`;
  }

  function addressSelectionQuery(hint) {
    if (!hint || typeof hint !== 'object') return '';
    const values = {
      address_street: hint.street,
      address_house_number: hint.houseNumber,
      address_label: hint.label,
      address_id: hint.addressId
    };
    return Object.entries(values)
      .filter(([, value]) => String(value || '').trim())
      .map(([key, value]) => `&${key}=${encodeURIComponent(String(value).trim())}`)
      .join('');
  }

  return {
    viewerUrl,
    setToken,
    session: () => viewerJson(viewerUrl('/api/v1/session')),
    sources: () => viewerJson(viewerUrl('/api/v1/sources')),
    featureAt: (lng, lat, signal, analytics = null, addressHint = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/features/point')}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}${addressSelectionQuery(addressHint)}`, analytics), { signal }),
    featurePreviewAt: (lng, lat, signal, analytics = null, addressHint = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/features/point-preview')}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}${addressSelectionQuery(addressHint)}`, analytics), { signal }),
    featureGeometry: ({ state = '', sourceDb, gmlId, kind = '' }, signal) => viewerJson(`${viewerUrl('/api/v1/features/geometry')}&state=${encodeURIComponent(state)}&source_db=${encodeURIComponent(sourceDb)}&gml_id=${encodeURIComponent(gmlId)}&kind=${encodeURIComponent(kind)}`, { signal }),
    searchAddress: (query, signal, analytics = null) => viewerJson(withAnalytics(addressSearchPath(query), analytics), { signal }),
    searchParcel: ({ gemarkung, flur = '', flurstueck, state = '', analyticsQuery = '', limit = 12 }, signal, analytics = null) => {
      const analyticsInput = String(analyticsQuery || '').trim();
      const path = `${viewerUrl('/api/v1/search/parcel')}&gemarkung=${encodeURIComponent(gemarkung)}&flur=${encodeURIComponent(flur)}&flurstueck=${encodeURIComponent(flurstueck)}&state=${encodeURIComponent(state)}${analyticsInput ? `&analytics_query=${encodeURIComponent(analyticsInput)}` : ''}&limit=${encodeURIComponent(limit)}`;
      return viewerJson(withAnalytics(path, analytics), { signal });
    },
    searchPoi: ({ poiId, analyticsQuery = '' }, signal, analytics = null) => {
      const analyticsInput = String(analyticsQuery || '').trim();
      const path = `${viewerUrl('/api/v1/search/poi')}&poi_id=${encodeURIComponent(poiId)}${analyticsInput ? `&analytics_query=${encodeURIComponent(analyticsInput)}` : ''}`;
      return viewerJson(withAnalytics(path, analytics), { signal });
    },
    suggestSearch: ({ query = '', nearLon = null, nearLat = null, limit = 8 }, signal) => viewerJson(`${viewerUrl('/api/v1/suggest/search')}&q=${encodeURIComponent(query)}${nearbyQuery({ nearLon, nearLat })}&limit=${encodeURIComponent(limit)}`, { signal }),
    suggestAddresses: ({ query = '', nearLon = null, nearLat = null, limit = 8 }, signal) => viewerJson(`${viewerUrl('/api/v1/suggest/addresses')}&q=${encodeURIComponent(query)}${nearbyQuery({ nearLon, nearLat })}&limit=${encodeURIComponent(limit)}`, { signal }),
    suggestPlaces: (query, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/suggest/places')}&q=${encodeURIComponent(query)}&limit=8`, analytics), { signal }),
    suggestStreets: (place, query, state, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/suggest/streets')}&place=${encodeURIComponent(place)}&q=${encodeURIComponent(query)}&state=${encodeURIComponent(state || '')}&limit=8`, analytics), { signal }),
    suggestGemarkungen: (query, signal) => viewerJson(`${viewerUrl('/api/v1/suggest/gemarkungen')}&q=${encodeURIComponent(query)}&limit=50`, { signal }),
    selectionPayload: (references, signal) => viewerJson(
      viewerUrl('/api/v1/integrations/onoffice/selection-payload'),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ features: Array.isArray(references) ? references : [] }),
        signal
      }
    ),
    createOrder: (payload) => json('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
    orderStatus: (orderId, guestToken) => json(`/api/orders/${encodeURIComponent(orderId)}/status${guestToken ? `?guest_token=${encodeURIComponent(guestToken)}` : ''}`)
  };
}

const UNIFIED_DATASETS = Object.freeze(['deutschland', 'oesterreich']);

function resultDataset(result, fallback = 'deutschland') {
  const explicit = String(result?.dataset || result?.country_dataset || '').trim().toLocaleLowerCase('de');
  if (explicit === 'oesterreich') return 'oesterreich';
  const state = String(result?.state || result?.parcel_search?.state || '').trim().toLocaleLowerCase('de');
  const sourceDb = String(
    result?.source_db || result?.sourceDb || result?.feature?.source_db || ''
  ).trim().toLocaleLowerCase('de');
  return state === 'oesterreich' || sourceDb === 'austria-bev' ? 'oesterreich' : fallback;
}

function tagResult(result, dataset) {
  if (!result || typeof result !== 'object') return result;
  const taggedDataset = resultDataset(result, dataset);
  return {
    ...result,
    dataset: taggedDataset,
    country_code: result.country_code || (taggedDataset === 'oesterreich' ? 'AT' : 'DE'),
    ...(result.parcel_search && typeof result.parcel_search === 'object'
      ? { parcel_search: { ...result.parcel_search, dataset: taggedDataset } }
      : {})
  };
}

function resultIdentity(result) {
  const feature = result?.feature && typeof result.feature === 'object' ? result.feature : {};
  const sourceDb = String(result?.source_db || feature.source_db || '');
  const gmlId = String(result?.gml_id || feature.gml_id || result?.poi_id || '');
  if (sourceDb && gmlId) return `${sourceDb}\u0000${gmlId}`;
  if (gmlId) return `${resultDataset(result)}\u0000${gmlId}`;
  const center = Array.isArray(result?.center) ? result.center.map((value) => Number(value).toFixed(6)).join(',') : '';
  return [
    resultDataset(result),
    String(result?.result_type || result?.kind || result?.search_scope || ''),
    String(result?.label || result?.value || ''),
    center
  ].join('\u0000');
}

function mergeResultPayloads(entries, limit = 50) {
  const successful = entries.filter((entry) => entry.status === 'fulfilled');
  if (!successful.length) throw entries.find((entry) => entry.status === 'rejected')?.reason || new Error('Suche fehlgeschlagen');
  const results = [];
  const seen = new Set();
  const queues = successful.map((entry) => ({
    dataset: entry.value.dataset,
    results: [...(entry.value.payload?.results || [])]
  }));
  // Round-robin keeps the near-country preference while preventing a full
  // German page from hiding every Austrian result (and vice versa).
  while (results.length < limit && queues.some((queue) => queue.results.length)) {
    for (const queue of queues) {
      const raw = queue.results.shift();
      if (!raw) continue;
      const result = tagResult(raw, queue.dataset);
      const key = resultIdentity(result);
      if (seen.has(key)) continue;
      seen.add(key);
      results.push(result);
      if (results.length >= limit) break;
    }
  }
  return {
    ...(successful[0].value.payload || {}),
    dataset: 'deutschland',
    country: 'Deutschland und Österreich',
    results
  };
}

function mergeSourcePayloads(germany = {}, austria = {}) {
  const mergeUnique = (left, right, keyOf) => {
    const merged = new Map();
    for (const item of [...(left || []), ...(right || [])]) {
      const key = keyOf(item);
      if (!key) continue;
      merged.set(key, item);
    }
    return [...merged.values()];
  };
  const uniqueAttributions = [];
  const attributionKeys = new Set();
  for (const item of [...(germany.attributions || []), ...(austria.attributions || [])]) {
    const key = `${item?.text || ''}\u0000${item?.href || ''}`;
    if (!item?.text || attributionKeys.has(key)) continue;
    attributionKeys.add(key);
    uniqueAttributions.push(item);
  }
  return {
    ...germany,
    dataset: 'deutschland',
    country: 'Deutschland und Österreich',
    runtime_kind: 'unified-de-at',
    bounds: [5.8, 46.3, 17.2, 55.1],
    center: { lon: 11.55, lat: 50.75 },
    states: mergeUnique(
      germany.states,
      austria.states,
      (item) => String(item?.slug || item?.dataset || item?.name || '')
    ),
    regions: mergeUnique(
      germany.regions,
      austria.regions,
      (item) => String(item?.slug || item?.dataset || item?.name || '')
    ),
    sources: mergeUnique(
      germany.sources,
      austria.sources,
      (item) => `${item?.id || item?.name || item?.text || ''}\u0000${item?.href || item?.url || ''}`
    ),
    attributions: uniqueAttributions
  };
}

function referenceDataset(reference) {
  return resultDataset(reference, 'deutschland');
}

/**
 * One browser-facing API facade for both national runtimes. Point queries are
 * routed by the exact country polygon; text searches are federated and retain
 * the originating dataset on every result for subsequent geometry requests.
 */
export function createUnifiedApi({
  token = '',
  fresh = '',
  requestTokenRefresh = null,
  countryResolver = null
} = {}) {
  const clients = Object.fromEntries(UNIFIED_DATASETS.map((dataset) => [
    dataset,
    createApi({ token, fresh, dataset, requestTokenRefresh })
  ]));

  const orderedDatasets = async ({ nearLon = null, nearLat = null, state = '', dataset = '' } = {}) => {
    const requested = String(dataset || '').trim().toLocaleLowerCase('de');
    if (UNIFIED_DATASETS.includes(requested)) return [requested, ...UNIFIED_DATASETS.filter((item) => item !== requested)];
    if (String(state || '').trim().toLocaleLowerCase('de') === 'oesterreich') return ['oesterreich', 'deutschland'];
    if (Number.isFinite(Number(nearLon)) && Number.isFinite(Number(nearLat)) && countryResolver) {
      await countryResolver.ready?.();
      const primary = countryResolver.datasetAt(Number(nearLon), Number(nearLat));
      return [primary, ...UNIFIED_DATASETS.filter((item) => item !== primary)];
    }
    return [...UNIFIED_DATASETS];
  };

  const federate = async (method, args, routing = {}, limit = 50) => {
    const order = await orderedDatasets(routing);
    const entries = await Promise.allSettled(order.map(async (dataset) => ({
      dataset,
      payload: await clients[dataset][method](...args)
    })));
    return mergeResultPayloads(entries, limit);
  };

  const pointQuery = async (method, lng, lat, signal, analytics, addressHint) => {
    const order = await orderedDatasets({ nearLon: lng, nearLat: lat });
    let firstEmpty = null;
    let firstError = null;
    for (const dataset of order) {
      try {
        const payload = await clients[dataset][method](lng, lat, signal, analytics, addressHint);
        const tagged = {
          ...payload,
          dataset,
          parcels: (payload?.parcels || []).map((item) => tagResult(item, dataset)),
          buildings: (payload?.buildings || []).map((item) => tagResult(item, dataset))
        };
        if (tagged.parcels.length || tagged.buildings.length) return tagged;
        firstEmpty ||= tagged;
      } catch (error) {
        if (error?.name === 'AbortError') throw error;
        firstError ||= error;
      }
    }
    if (firstEmpty) return firstEmpty;
    throw firstError || new Error('Objektabfrage fehlgeschlagen');
  };

  return {
    viewerUrl: clients.deutschland.viewerUrl,
    setToken(nextToken) {
      const results = UNIFIED_DATASETS.map((dataset) => clients[dataset].setToken(nextToken));
      return results.some(Boolean);
    },
    session: clients.deutschland.session,
    async sources() {
      const [germany, austria] = await Promise.allSettled([
        clients.deutschland.sources(),
        clients.oesterreich.sources()
      ]);
      if (germany.status === 'rejected' && austria.status === 'rejected') throw germany.reason;
      return mergeSourcePayloads(
        germany.status === 'fulfilled' ? germany.value : {},
        austria.status === 'fulfilled' ? austria.value : {}
      );
    },
    featureAt: (lng, lat, signal, analytics = null, addressHint = null) => (
      pointQuery('featureAt', lng, lat, signal, analytics, addressHint)
    ),
    featurePreviewAt: (lng, lat, signal, analytics = null, addressHint = null) => (
      pointQuery('featurePreviewAt', lng, lat, signal, analytics, addressHint)
    ),
    featureGeometry(reference, signal) {
      return clients[referenceDataset(reference)].featureGeometry(reference, signal);
    },
    searchAddress(query, signal, analytics = null) {
      const limit = Number(query?.limit) || 12;
      return federate(
        'searchAddress',
        [query, signal, analytics],
        { nearLon: query?.nearLon, nearLat: query?.nearLat, state: query?.state, dataset: query?.dataset },
        limit
      );
    },
    searchParcel(query, signal, analytics = null) {
      const limit = Number(query?.limit) || 12;
      const state = String(query?.state || '').trim().toLocaleLowerCase('de');
      if (state && state !== 'oesterreich') {
        return clients.deutschland.searchParcel(query, signal, analytics).then((payload) => ({
          ...payload,
          results: (payload?.results || []).map((item) => tagResult(item, 'deutschland'))
        }));
      }
      if (state === 'oesterreich' || query?.dataset === 'oesterreich') {
        return clients.oesterreich.searchParcel(query, signal, analytics).then((payload) => ({
          ...payload,
          results: (payload?.results || []).map((item) => tagResult(item, 'oesterreich'))
        }));
      }
      return federate('searchParcel', [query, signal, analytics], {}, limit);
    },
    searchPoi: (query, signal, analytics = null) => clients.deutschland.searchPoi(query, signal, analytics),
    suggestSearch(query, signal) {
      const limit = Number(query?.limit) || 8;
      return federate(
        'suggestSearch',
        [query, signal],
        { nearLon: query?.nearLon, nearLat: query?.nearLat },
        limit
      );
    },
    suggestAddresses(query, signal) {
      const limit = Number(query?.limit) || 8;
      return federate(
        'suggestAddresses',
        [query, signal],
        { nearLon: query?.nearLon, nearLat: query?.nearLat },
        limit
      );
    },
    suggestPlaces(query, signal, analytics = null) {
      return federate('suggestPlaces', [query, signal, analytics], {}, 8);
    },
    suggestStreets(place, query, state, signal, analytics = null) {
      const dataset = String(state || '').trim().toLocaleLowerCase('de') === 'oesterreich'
        ? 'oesterreich'
        : 'deutschland';
      return clients[dataset].suggestStreets(place, query, state, signal, analytics);
    },
    suggestGemarkungen(query, signal) {
      return federate('suggestGemarkungen', [query, signal], {}, 50);
    },
    async selectionPayload(references, signal) {
      const partitions = Object.fromEntries(UNIFIED_DATASETS.map((dataset) => [dataset, []]));
      for (const reference of Array.isArray(references) ? references : []) {
        partitions[referenceDataset(reference)].push(reference);
      }
      const responses = await Promise.all(UNIFIED_DATASETS
        .filter((dataset) => partitions[dataset].length)
        .map((dataset) => clients[dataset].selectionPayload(partitions[dataset], signal)));
      return {
        integration: 'onoffice',
        mode: 'selection-payload-preview',
        features: responses.flatMap((payload) => payload?.features || []),
        missing: responses.flatMap((payload) => payload?.missing || []),
        warnings: responses.flatMap((payload) => payload?.warnings || [])
      };
    },
    createOrder: clients.deutschland.createOrder,
    orderStatus: clients.deutschland.orderStatus
  };
}
