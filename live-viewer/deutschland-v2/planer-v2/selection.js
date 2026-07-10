import { addressLabel, escapeHtml, featureKey, formatArea, polygonAreaMeters } from './utils.js';

export function createSelectionController({ map, api, store, layout, elements }) {
  const { selectionContent, selectionCount, selectTool, selectionDock } = elements;
  let request = null;
  let clearGeneration = 0;

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
    const html = [buildings.length ? buildingTable(buildings) : '', parcels.length ? parcelTable(parcels) : ''].join('');
    if (selectionContent.innerHTML !== html) selectionContent.innerHTML = html;
    const count = parcels.length + buildings.length;
    selectionCount.textContent = loading && !count ? 'Auswahl wird geladen' : count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
    updateSources();
    if (count && !state.layout.tableOpen) layout.setTable(true);
    selectionDock.classList.toggle('is-loading', loading);
  }

  function display(value) {
    return value === null || value === undefined || value === '' ? '–' : escapeHtml(value);
  }

  function addressChips(item) {
    const labels = Array.isArray(item.addresses) && item.addresses.length
      ? item.addresses.map((address) => address?.label || [address?.street, address?.house_number].filter(Boolean).join(' ')).filter(Boolean)
      : [addressLabel(item)].filter((label) => label && label !== '–');
    return labels.length ? labels.map((label) => `<span class="address-chip">${escapeHtml(label)}</span>`).join('') : '–';
  }

  function parcelUsage(item) {
    const usages = Array.isArray(item.nutzungen) ? item.nutzungen : [];
    if (usages.length) return usages.slice(0, 3).map((usage) => {
      const name = usage.thema || usage.nutzung || usage.layer || 'Nutzung';
      const share = Number(usage.anteil);
      return Number.isFinite(share) && share > 0 ? `${name} ${Math.round(share * 100)}%` : name;
    }).join(', ');
    return item.nutzung_haupt || item.nutzung || item.tatsaechliche_nutzung || item.thema || '';
  }

  function buildingTable(buildings) {
    const rows = buildings.map((item) => {
      const footprint = Number(item.grundflaeche_m2) || geometryArea(item.geometry);
      return `<tr><td class="strong">${display(item.gebaeudefunktion_text || item.name || 'Gebäude')}</td><td>${display(item.geschosse_oberirdisch)}</td><td>${display(item.dachform_text)}</td><td>${footprint > 0 ? formatArea(footprint) : '–'}</td><td>${display(item.baujahr)}</td><td>${addressChips(item)}</td></tr>`;
    }).join('');
    return `<section class="selection-section"><div class="selection-section-title">Gebäude</div><div class="selection-table-wrap"><table><thead><tr><th>Nutzungsart</th><th>Geschosse</th><th>Dachform</th><th>Grundfl.</th><th>Baujahr</th><th>Adressen</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function geometryArea(geometry) {
    if (!geometry) return 0;
    const polygon = (coordinates) => {
      if (!coordinates?.length) return 0;
      const outer = polygonAreaMeters(coordinates[0]);
      const holes = coordinates.slice(1).reduce((sum, ring) => sum + polygonAreaMeters(ring), 0);
      return Math.max(0, outer - holes);
    };
    if (geometry.type === 'Polygon') return polygon(geometry.coordinates);
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.reduce((sum, coordinates) => sum + polygon(coordinates), 0);
    return 0;
  }

  function parcelTable(parcels) {
    const rows = parcels.map((item) => {
      const number = item.flurstueck || [item.zaehler, item.nenner].filter(Boolean).join('/');
      const key = item.gemarkung_nummer || item.gemarkungsnummer || item.gemarkungsschluessel || item.gemarkung_key || '';
      return `<tr><td>${display(item.flur)}</td><td class="strong">${display(number)}</td><td>${display(item.gemarkung)}</td><td>${display(key)}</td><td>${display(parcelUsage(item))}</td><td>${formatArea(item.amtliche_flaeche_m2)}</td><td>${addressChips(item)}</td></tr>`;
    }).join('');
    return `<section class="selection-section"><div class="selection-section-title">Flurstücke</div><div class="selection-table-wrap"><table><thead><tr><th>Flur</th><th>Flurstück</th><th>Gemarkung</th><th>Schlüssel</th><th>Nutzung</th><th>Größe</th><th>Adressen</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
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
    const state = store.getState();
    if (!state.layout.tableOpen) {
      store.setState({ selection: { parcels: [], buildings: [], loading: false } }, 'selection-clear');
      return;
    }

    const generation = ++clearGeneration;
    const workspace = selectionDock.parentElement;
    const finish = () => {
      if (generation !== clearGeneration || store.getState().layout.tableOpen) return;
      store.setState({ selection: { parcels: [], buildings: [], loading: false } }, 'selection-clear');
    };
    const onEnd = (event) => {
      if (event.target !== workspace || event.propertyName !== 'grid-template-rows') return;
      workspace.removeEventListener('transitionend', onEnd);
      workspace.removeEventListener('transitioncancel', onCancel);
      finish();
    };
    const onCancel = () => {
      workspace.removeEventListener('transitionend', onEnd);
      workspace.removeEventListener('transitioncancel', onCancel);
      finish();
    };

    workspace.addEventListener('transitionend', onEnd);
    workspace.addEventListener('transitioncancel', onCancel);
    layout.setTable(false);
  }

  map.on('load', addLayers);
  map.on('click', (event) => {
    if (store.getState().activeTool === 'select') selectAt(event.lngLat, true);
  });
  store.subscribe((state, reason) => { if (reason.startsWith('selection') || reason === 'restore') render(state); });
  return { selectAt, clear, render };
}
