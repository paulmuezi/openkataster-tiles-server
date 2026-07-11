export function createApi({ token = '', fresh = '' } = {}) {
  function viewerUrl(path) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set('client', 'viewer');
    if (token) url.searchParams.set('token', token);
    if (fresh) url.searchParams.set('fresh', fresh);
    return `${url.pathname}${url.search}`;
  }

  async function json(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  return {
    viewerUrl,
    session: () => json(viewerUrl('/api/v1/session')),
    sources: () => json(viewerUrl('/api/v1/sources')),
    featureAt: (lng, lat, signal) => json(`${viewerUrl('/api/v1/features/point')}&lon=${encodeURIComponent(lng)}&lat=${encodeURIComponent(lat)}`, { signal }),
    featureGeometry: ({ state = '', sourceDb, gmlId, kind = '' }, signal) => json(`${viewerUrl('/api/v1/features/geometry')}&state=${encodeURIComponent(state)}&source_db=${encodeURIComponent(sourceDb)}&gml_id=${encodeURIComponent(gmlId)}&kind=${encodeURIComponent(kind)}`, { signal }),
    searchAddress: ({ place, street = '', houseNumber = '', state = '', limit = 12 }, signal) => json(`${viewerUrl('/api/v1/search/address')}&place=${encodeURIComponent(place)}&street=${encodeURIComponent(street)}&house_number=${encodeURIComponent(houseNumber)}&state=${encodeURIComponent(state)}&limit=${limit}`, { signal }),
    searchParcel: ({ gemarkung, flur, flurstueck, limit = 12 }, signal) => json(`${viewerUrl('/api/v1/search/parcel')}&gemarkung=${encodeURIComponent(gemarkung)}&flur=${encodeURIComponent(flur)}&flurstueck=${encodeURIComponent(flurstueck)}&limit=${limit}`, { signal }),
    suggestPlaces: (query, signal) => json(`/api/suggest/places/deutschland?key=y2Gi6D47jEClM12fnar_PaLGz9uHCK8Tu7yrbW0FiII&q=${encodeURIComponent(query)}&limit=8`, { signal }),
    suggestStreets: (place, query, state, signal) => json(`/api/suggest/streets/deutschland?key=y2Gi6D47jEClM12fnar_PaLGz9uHCK8Tu7yrbW0FiII&place=${encodeURIComponent(place)}&q=${encodeURIComponent(query)}&state=${encodeURIComponent(state || '')}&limit=8`, { signal }),
    createOrder: (payload) => json('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
    orderStatus: (orderId, guestToken) => json(`/api/orders/${encodeURIComponent(orderId)}/status${guestToken ? `?guest_token=${encodeURIComponent(guestToken)}` : ''}`)
  };
}
