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
