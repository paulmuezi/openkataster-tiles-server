const ANALYTICS_SCOPES = new Set(['place', 'street', 'address', 'parcel']);

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

export function createApi({ token = '', fresh = '' } = {}) {
  function viewerUrl(path) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set('client', 'viewer');
    if (fresh) url.searchParams.set('fresh', fresh);
    return `${url.pathname}${url.search}`;
  }

  async function json(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function viewerJson(path, options = {}) {
    const headers = new Headers(options.headers || undefined);
    if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
    return json(path, { ...options, headers });
  }

  return {
    viewerUrl,
    session: () => viewerJson(viewerUrl('/api/v1/session')),
    sources: () => viewerJson(viewerUrl('/api/v1/sources')),
    featureAt: (lng, lat, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/features/point')}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}`, analytics), { signal }),
    featurePreviewAt: (lng, lat, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/features/point-preview')}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}`, analytics), { signal }),
    featureGeometry: ({ state = '', sourceDb, gmlId, kind = '' }, signal) => viewerJson(`${viewerUrl('/api/v1/features/geometry')}&state=${encodeURIComponent(state)}&source_db=${encodeURIComponent(sourceDb)}&gml_id=${encodeURIComponent(gmlId)}&kind=${encodeURIComponent(kind)}`, { signal }),
    searchAddress: ({ place, street = '', houseNumber = '', state = '', limit = 12 }, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/search/address')}&place=${encodeURIComponent(place)}&street=${encodeURIComponent(street)}&house_number=${encodeURIComponent(houseNumber)}&state=${encodeURIComponent(state)}&limit=${limit}`, analytics), { signal }),
    searchParcel: ({ gemarkung, flur = '', flurstueck, state = '', limit = 12 }, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/search/parcel')}&gemarkung=${encodeURIComponent(gemarkung)}&flur=${encodeURIComponent(flur)}&flurstueck=${encodeURIComponent(flurstueck)}&state=${encodeURIComponent(state)}&limit=${limit}`, analytics), { signal }),
    suggestPlaces: (query, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/suggest/places')}&q=${encodeURIComponent(query)}&limit=8`, analytics), { signal }),
    suggestStreets: (place, query, state, signal, analytics = null) => viewerJson(withAnalytics(`${viewerUrl('/api/v1/suggest/streets')}&place=${encodeURIComponent(place)}&q=${encodeURIComponent(query)}&state=${encodeURIComponent(state || '')}&limit=8`, analytics), { signal }),
    suggestGemarkungen: (query, signal) => viewerJson(`${viewerUrl('/api/v1/suggest/gemarkungen')}&q=${encodeURIComponent(query)}&limit=8`, { signal }),
    createOrder: (payload) => json('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
    orderStatus: (orderId, guestToken) => json(`/api/orders/${encodeURIComponent(orderId)}/status${guestToken ? `?guest_token=${encodeURIComponent(guestToken)}` : ''}`)
  };
}
