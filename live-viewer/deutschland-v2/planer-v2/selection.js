import { addressLabel, escapeHtml, featureKey, formatArea } from './utils.js';

export function createSelectionController({ map, api, store, layout, elements }) {
  const { selectionRows, selectionCount, selectTool, selectionDock } = elements;
  let request = null;

  function featureCollection(items) {
    return { type: 'FeatureCollection', features: items.filter((item) => item.geometry).map((item) => ({ type: 'Feature', properties: { id: featureKey(item) }, geometry: item.geometry })) };
  }

  function addLayers() {
    if (map.getSource('selected-parcels-v2')) return;
    map.addSource('selected-parcels-v2', { type: 'geojson', data: featureCollection([]) });
    map.addSource('selected-buildings-v2', { type: 'geojson', data: featureCollection([]) });
    map.addLayer({ id: 'selected-parcels-v2', type: 'line', source: 'selected-parcels-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.4, 'line-dasharray': [2.5, 1.35] } });
    map.addLayer({ id: 'selected-buildings-v2', type: 'line', source: 'selected-buildings-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.8 } });
  }

  function updateSources() {
    const selection = store.getState().selection;
    map.getSource('selected-parcels-v2')?.setData(featureCollection(selection.parcels));
    map.getSource('selected-buildings-v2')?.setData(featureCollection(selection.buildings));
  }

  function render(state = store.getState()) {
    const { parcels, buildings, loading } = state.selection;
    const rows = [];
    for (const building of buildings) rows.push(`<tr><td>Gebäude</td><td>${escapeHtml(addressLabel(building))}</td><td>–</td><td>–</td><td>–</td><td>–</td></tr>`);
    for (const parcel of parcels) rows.push(`<tr><td>Flurstück</td><td>${escapeHtml(addressLabel(parcel))}</td><td>${escapeHtml(parcel.gemarkung || '–')}</td><td>${escapeHtml(parcel.flur ?? '–')}</td><td><strong>${escapeHtml(parcel.flurstueck || [parcel.zaehler, parcel.nenner].filter(Boolean).join('/') || '–')}</strong></td><td>${formatArea(parcel.amtliche_flaeche_m2)}</td></tr>`);
    if (selectionRows.innerHTML !== rows.join('')) selectionRows.innerHTML = rows.join('');
    const count = parcels.length + buildings.length;
    selectionCount.textContent = loading && !count ? 'Auswahl wird geladen' : count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
    updateSources();
    if (count && !state.layout.tableOpen) layout.setTable(true);
    selectionDock.classList.toggle('is-loading', loading);
  }

  async function selectAt(lngLat, additive = false, preferredKind = null) {
    const state = store.getState();
    if (!state.access.pro) {
      store.setState({ notice: { title: 'OpenKataster Pro', text: 'Objektinformationen sind in Pro verfügbar.' } }, 'notice');
      return;
    }
    request?.abort();
    request = new AbortController();
    selectTool.classList.add('is-loading');
    store.setState({ selection: { ...state.selection, loading: true } }, 'selection-loading');
    try {
      const data = await api.featureAt(lngLat.lng, lngLat.lat, request.signal);
      const buildings = data.buildings || [];
      const parcels = data.parcels || [];
      const kind = preferredKind || (buildings.length ? 'building' : parcels.length ? 'parcel' : null);
      const next = store.getState();
      const currentBuildings = additive ? new Map(next.selection.buildings.map((item) => [featureKey(item), item])) : new Map();
      const currentParcels = additive ? new Map(next.selection.parcels.map((item) => [featureKey(item), item])) : new Map();
      if (kind === 'building') toggleItems(currentBuildings, buildings, additive);
      if (kind === 'parcel') toggleItems(currentParcels, parcels, additive);
      store.setState({ selection: { buildings: [...currentBuildings.values()], parcels: [...currentParcels.values()], loading: false } }, 'selection');
    } catch (error) {
      if (error.name !== 'AbortError') console.error(error);
      const current = store.getState();
      store.setState({ selection: { ...current.selection, loading: false } }, 'selection-error');
    } finally {
      selectTool.classList.remove('is-loading');
    }
  }

  function toggleItems(target, items, additive) {
    for (const item of items) {
      const key = featureKey(item);
      if (additive && target.has(key)) target.delete(key); else target.set(key, item);
    }
  }

  function clear() {
    layout.setTable(false);
    window.setTimeout(() => {
      if (!store.getState().layout.tableOpen) {
        store.setState({ selection: { parcels: [], buildings: [], loading: false } }, 'selection-clear');
      }
    }, 360);
  }

  map.on('load', addLayers);
  map.on('click', (event) => {
    if (store.getState().activeTool === 'select') selectAt(event.lngLat, event.originalEvent?.shiftKey || event.originalEvent?.ctrlKey || event.originalEvent?.metaKey);
  });
  store.subscribe((state, reason) => { if (reason.startsWith('selection') || reason === 'restore') render(state); });
  return { selectAt, clear, render };
}
