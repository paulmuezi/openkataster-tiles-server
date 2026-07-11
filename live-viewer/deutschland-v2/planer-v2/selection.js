import { addressLabel, escapeHtml, featureKey, formatArea, polygonAreaMeters } from './utils.js';

const HIDDEN_DYNAMIC_FIELDS = new Set([
  'source_db', 'gml_id', 'id', 'geometry', 'bbox', 'center', 'addresses', 'address',
  'flurstueckskennzeichen',
  'zaehler', 'nenner', 'nutzungen', 'nutzung_haupt',
  'gemeinde', 'gemeindenummer', 'kreis', 'kreisnummer', 'land', 'landnummer', 'regierungsbezirk'
]);

const FIELD_LABELS = {
  gemeindeteil: 'Gemeindeteil',
  gebaeudekennzeichen: 'Gebäudekennzeichen',
  flurstuecksfolge: 'Flurstücksfolge'
};

export function createSelectionController({ map, api, store, layout, elements }) {
  const { selectionContent, selectionCount, selectTool, selectionDock } = elements;
  let request = null;
  let geometryRequest = null;
  let flashTimers = [];
  let clearGeneration = 0;
  let clearObserver = null;

  function featureCollection(items) {
    return { type: 'FeatureCollection', features: items.filter((item) => item.geometry).map((item) => ({ type: 'Feature', properties: { id: featureKey(item) }, geometry: item.geometry })) };
  }

  function collectRings(geometry, target = []) {
    if (!geometry) return target;
    if (geometry.type === 'Polygon') geometry.coordinates.forEach((ring) => target.push(ring));
    else if (geometry.type === 'MultiPolygon') geometry.coordinates.forEach((polygon) => polygon.forEach((ring) => target.push(ring)));
    else if (geometry.type === 'GeometryCollection') (geometry.geometries || []).forEach((part) => collectRings(part, target));
    return target;
  }

  function coordinateKey(coordinate) {
    return `${Number(coordinate[0]).toFixed(8)},${Number(coordinate[1]).toFixed(8)}`;
  }

  function selectionOutlineCollection(items) {
    const edges = new Map();
    for (const item of items) {
      for (const ring of collectRings(item.geometry)) {
        for (let index = 1; index < ring.length; index += 1) {
          const start = ring[index - 1];
          const end = ring[index];
          if (!Array.isArray(start) || !Array.isArray(end)) continue;
          const startKey = coordinateKey(start);
          const endKey = coordinateKey(end);
          if (startKey === endKey) continue;
          const key = startKey < endKey ? `${startKey}|${endKey}` : `${endKey}|${startKey}`;
          const edge = edges.get(key);
          if (edge) edge.count += 1;
          else edges.set(key, { count: 1, start, end, startKey, endKey });
        }
      }
    }

    const segments = [...edges.values()].filter((edge) => edge.count % 2 === 1);
    const connections = new Map();
    segments.forEach((segment, index) => {
      for (const key of [segment.startKey, segment.endKey]) {
        if (!connections.has(key)) connections.set(key, []);
        connections.get(key).push(index);
      }
    });

    const used = new Set();
    const lines = [];
    for (let index = 0; index < segments.length; index += 1) {
      if (used.has(index)) continue;
      const first = segments[index];
      const line = [first.start, first.end];
      const startKey = first.startKey;
      let currentKey = first.endKey;
      used.add(index);
      while (currentKey !== startKey) {
        const nextIndex = (connections.get(currentKey) || []).find((candidate) => !used.has(candidate));
        if (nextIndex === undefined) break;
        const next = segments[nextIndex];
        const forward = next.startKey === currentKey;
        line.push(forward ? next.end : next.start);
        currentKey = forward ? next.endKey : next.startKey;
        used.add(nextIndex);
      }
      if (line.length >= 2) lines.push(line);
    }

    return {
      type: 'FeatureCollection',
      features: lines.map((coordinates) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } }))
    };
  }

  function addLayers() {
    if (map.getSource('selected-parcels-v2')) return;
    map.addSource('selected-parcels-v2', { type: 'geojson', data: featureCollection([]) });
    map.addSource('selected-buildings-v2', { type: 'geojson', data: featureCollection([]) });
    map.addSource('search-highlight-parcels-v2', { type: 'geojson', data: featureCollection([]) });
    map.addSource('search-highlight-buildings-v2', { type: 'geojson', data: featureCollection([]) });
    map.addLayer({ id: 'selected-parcels-v2', type: 'line', source: 'selected-parcels-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.4, 'line-dasharray': [2.5, 1.35] } });
    map.addLayer({ id: 'selected-buildings-v2', type: 'line', source: 'selected-buildings-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.8 } });
    map.addLayer({ id: 'search-highlight-parcels-v2', type: 'line', source: 'search-highlight-parcels-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.4, 'line-dasharray': [2.5, 1.35], 'line-opacity': 0 } });
    map.addLayer({ id: 'search-highlight-buildings-v2', type: 'line', source: 'search-highlight-buildings-v2', paint: { 'line-color': '#ed3c32', 'line-width': 2.8, 'line-opacity': 0 } });
  }

  function updateSources() {
    const selection = store.getState().selection;
    map.getSource('selected-parcels-v2')?.setData(selectionOutlineCollection(selection.parcels));
    map.getSource('selected-buildings-v2')?.setData(selectionOutlineCollection(selection.buildings));
  }

  function render(state = store.getState()) {
    const { parcels, buildings, loading } = state.selection;
    const html = state.access.pro
      ? [buildings.length ? buildingTable(buildings) : '', parcels.length ? parcelTable(parcels) : ''].join('')
      : freePreviewTable(buildings, parcels);
    if (selectionContent.innerHTML !== html) selectionContent.innerHTML = html;
    const count = parcels.length + buildings.length;
    selectionCount.textContent = loading && !count ? 'Auswahl wird geladen' : count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
    updateSources();
    if (count && !state.layout.tableOpen) layout.setTable(true);
    selectionDock.classList.toggle('is-loading', loading);
  }

  function freePreviewTable(buildings, parcels) {
    const sections = [];
    if (buildings.length) sections.push(lockedPreviewTable('Gebäude', buildings, [
      { label: 'Gebäudefunktion', keys: ['gebaeudefunktion_text', 'gebaeudefunktion'] },
      { label: 'Name', keys: ['name'] },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'] },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'] },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'] },
      { label: 'Grundfläche', keys: ['grundflaeche_m2'] },
      { label: 'Geschossfläche', keys: ['geschossflaeche_m2'] },
      { label: 'Geometrische Fläche', keys: ['geometrische_flaeche_m2'] },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'] },
      { label: 'Objekthöhe', keys: ['objekthoehe_m'] },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'] },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'] }
    ]));
    if (parcels.length) sections.push(lockedPreviewTable('Flurstücke', parcels, [
      { label: 'Gem.-Schl.', keys: ['gemarkungsschluessel', 'gemarkung_key'] },
      { label: 'Gemarkung', keys: ['gemarkung', 'gemarkungsnummer'] },
      { label: 'Flur', keys: ['flur'] },
      { label: 'Flurstück', keys: ['flurstueck', 'zaehler', 'nenner'] },
      { label: 'Amtliche Fläche', keys: ['amtliche_flaeche_m2'] },
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'] },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Flurstücksfolge', keys: ['flurstuecksfolge'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'] },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'] },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'] },
      { label: 'Adressen', keys: ['addresses', 'address'] },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'] }
    ]));
    if (!sections.length) return '';
    return `${sections.join('')}<div class="selection-pro-lock"><span>Objektinformationen sind in Pro verfügbar.</span><a href="/pro" target="_top">Pro buchen</a></div>`;
  }

  function lockedPreviewTable(title, items, definitions) {
    const available = new Set(items.flatMap((item) => item.available_fields || []));
    const columns = definitions.filter((column) => column.keys.some((key) => available.has(key)));
    const headers = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join('');
    const cells = columns.map(() => '<td><span class="locked-cell">–</span></td>').join('');
    return `<section class="selection-section"><div class="selection-section-title">${escapeHtml(title)}</div><div class="selection-table-wrap"><table class="preview-table"><thead><tr>${headers}</tr></thead><tbody><tr>${cells}</tr></tbody></table></div></section>`;
  }

  function display(value) {
    return value === null || value === undefined || value === '' ? '–' : escapeHtml(value);
  }

  function hasValue(value) {
    if (value === null || value === undefined) return false;
    if (typeof value === 'string') return value.trim() !== '';
    if (Array.isArray(value)) return value.length > 0;
    return typeof value !== 'object' || Object.keys(value).length > 0;
  }

  function addressLabels(item) {
    return Array.isArray(item.addresses) && item.addresses.length
      ? item.addresses.map((address) => address?.label || [address?.street, address?.house_number].filter(Boolean).join(' ')).filter(Boolean)
      : [addressLabel(item)].filter((label) => label && label !== '–');
  }

  function addressChips(item) {
    const labels = addressLabels(item);
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

  function pick(item, keys) {
    for (const key of keys || []) if (hasValue(item[key])) return item[key];
    return null;
  }

  function columnValue(column, item) {
    return column.value ? column.value(item) : pick(item, column.keys);
  }

  function formatCell(value, format) {
    if (!hasValue(value)) return '–';
    if (format === 'area') return formatArea(value);
    if (format === 'length') return `${Number(value).toLocaleString('de-DE', { maximumFractionDigits: 2 })} m`;
    if (format === 'volume') return `${Number(value).toLocaleString('de-DE', { maximumFractionDigits: 2 })} m³`;
    if (format === 'boolean') {
      if (value === true || value === 1 || String(value).toLowerCase() === 'true') return 'Ja';
      if (value === false || value === 0 || String(value).toLowerCase() === 'false') return 'Nein';
    }
    if (format === 'date' && /^\d{4}-\d{2}-\d{2}/.test(String(value))) {
      const [year, month, day] = String(value).slice(0, 10).split('-');
      return `${day}.${month}.${year}`;
    }
    if (typeof value === 'number') return value.toLocaleString('de-DE', { maximumFractionDigits: 2 });
    return display(value);
  }

  function humanizeField(key) {
    if (FIELD_LABELS[key]) return FIELD_LABELS[key];
    return key
      .replace(/_m2$/, ' (m²)')
      .replace(/_m3$/, ' (m³)')
      .replace(/_m$/, ' (m)')
      .replaceAll('_', ' ')
      .replace(/^./, (character) => character.toLocaleUpperCase('de-DE'));
  }

  function extraColumns(items, definitions) {
    const handled = new Set(definitions.flatMap((column) => column.keys || []));
    const keys = new Set(items.flatMap((item) => Object.keys(item || {})));
    return [...keys].filter((key) => {
      if (handled.has(key) || HIDDEN_DYNAMIC_FIELDS.has(key)) return false;
      const values = items.map((item) => item[key]).filter(hasValue);
      if (!values.length || values.some((value) => typeof value === 'object')) return false;
      if (!key.endsWith('_text')) {
        const textKey = `${key}_text`;
        if (keys.has(textKey) && items.some((item) => hasValue(item[textKey]))) return false;
      }
      return true;
    }).sort((a, b) => humanizeField(a).localeCompare(humanizeField(b), 'de')).map((key) => ({ label: humanizeField(key), keys: [key] }));
  }

  function dynamicTable(title, items, definitions) {
    const columns = [...definitions, ...extraColumns(items, definitions)].filter((column) => items.some((item) => hasValue(columnValue(column, item))));
    const columnClass = (column) => [column.compact ? 'compact' : '', column.strong ? 'strong' : ''].filter(Boolean).join(' ');
    const headers = columns.map((column) => {
      const className = columnClass(column);
      const fullLabel = column.title || column.label;
      return `<th${className ? ` class="${className}"` : ''} title="${escapeHtml(fullLabel)}">${escapeHtml(column.label)}</th>`;
    }).join('');
    const rows = items.map((item) => `<tr>${columns.map((column) => {
      const value = columnValue(column, item);
      const content = column.html ? column.html(item, value) : formatCell(value, column.format);
      const className = columnClass(column);
      return `<td${className ? ` class="${className}"` : ''}>${content}</td>`;
    }).join('')}</tr>`).join('');
    const hasTotals = items.length > 1 && columns.some((column) => column.sum);
    const totals = hasTotals ? `<tfoot><tr>${columns.map((column, index) => {
      if (index === 0) return '<td class="summary-label">Summe</td>';
      if (!column.sum) return '<td></td>';
      const values = items.map((item) => Number(columnValue(column, item))).filter(Number.isFinite);
      return `<td class="summary-value${column.compact ? ' compact' : ''}">${values.length ? formatCell(values.reduce((sum, value) => sum + value, 0), column.format) : '–'}</td>`;
    }).join('')}</tr></tfoot>` : '';
    return `<section class="selection-section"><div class="selection-section-title">${escapeHtml(title)}</div><div class="selection-table-wrap"><table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody>${totals}</table></div></section>`;
  }

  function buildingTable(buildings) {
    const columns = [
      { label: 'Gebäudefunktion', keys: ['gebaeudefunktion_text', 'gebaeudefunktion'], strong: true },
      { label: 'Name', keys: ['name'] },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'], compact: true },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'], compact: true },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'], compact: true },
      { label: 'Grundfläche', keys: ['grundflaeche_m2'], format: 'area', sum: true, compact: true },
      { label: 'Geschossfläche', keys: ['geschossflaeche_m2'], format: 'area', sum: true, compact: true },
      { label: 'Geometrische Fläche', keys: ['geometrische_flaeche_m2'], value: (item) => item.geometrische_flaeche_m2 || geometryArea(item.geometry), format: 'area', sum: true, compact: true },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'], format: 'volume', compact: true },
      { label: 'Objekthöhe', keys: ['objekthoehe_m'], format: 'length', compact: true },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'], format: 'boolean', compact: true },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'], value: addressLabels, html: (item) => addressChips(item) }
    ];
    return dynamicTable('Gebäude', buildings, columns);
  }

  function parcelTable(parcels) {
    const columns = [
      { label: 'Gem.-Schl.', title: 'Gemarkungsschlüssel', keys: ['gemarkungsschluessel', 'gemarkung_key'], compact: true },
      { label: 'Gemarkung', keys: ['gemarkung', 'gemarkungsnummer'], value: (item) => item.gemarkung && item.gemarkungsnummer ? `${item.gemarkung} (${item.gemarkungsnummer})` : item.gemarkung || item.gemarkungsnummer },
      { label: 'Flur', keys: ['flur'], compact: true },
      { label: 'Flurstück', keys: ['flurstueck', 'zaehler', 'nenner'], value: (item) => item.flurstueck || [item.zaehler, item.nenner].filter(Boolean).join('/'), strong: true, compact: true },
      { label: 'Amtliche Fläche', keys: ['amtliche_flaeche_m2'], format: 'area', sum: true, compact: true },
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'], value: parcelUsage },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Flurstücksfolge', keys: ['flurstuecksfolge'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'], format: 'boolean' },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'], format: 'boolean' },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'], format: 'boolean' },
      { label: 'Adressen', keys: ['addresses', 'address'], value: addressLabels, html: (item) => addressChips(item) },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'], format: 'date', compact: true }
    ];
    return dynamicTable('Flurstücke', parcels, columns);
  }

  async function selectAt(lngLat, additive = false, preferredKind = null) {
    const state = store.getState();
    request?.abort();
    request = new AbortController();
    selectTool.classList.add('is-loading');
    store.setState({ selection: { ...state.selection, loading: true } }, 'selection-loading');
    try {
      const data = await (state.access.pro ? api.featureAt : api.featurePreviewAt)(lngLat.lng, lngLat.lat, request.signal);
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

  async function flash(result, preferredKind) {
    const feature = result?.feature || {};
    const sourceDb = feature.source_db || result?.source_db;
    const gmlId = feature.gml_id || result?.gml_id;
    if (!sourceDb || !gmlId) return;
    geometryRequest?.abort();
    geometryRequest = new AbortController();
    for (const timer of flashTimers) window.clearTimeout(timer);
    flashTimers = [];
    try {
      const geometry = await api.featureGeometry({ state: result.state || '', sourceDb, gmlId, kind: preferredKind || result.kind || '' }, geometryRequest.signal);
      const kind = geometry.kind === 'parcel' ? 'parcel' : 'building';
      const sourceId = kind === 'parcel' ? 'search-highlight-parcels-v2' : 'search-highlight-buildings-v2';
      const layerId = sourceId;
      const otherSourceId = kind === 'parcel' ? 'search-highlight-buildings-v2' : 'search-highlight-parcels-v2';
      const otherLayerId = otherSourceId;
      map.getSource(otherSourceId)?.setData(featureCollection([]));
      if (map.getLayer(otherLayerId)) map.setPaintProperty(otherLayerId, 'line-opacity', 0);
      map.getSource(sourceId)?.setData(featureCollection([geometry]));
      if (map.getLayer(layerId)) map.setPaintProperty(layerId, 'line-opacity', 1);
      flashTimers.push(window.setTimeout(() => map.getLayer(layerId) && map.setPaintProperty(layerId, 'line-opacity', .18), 230));
      flashTimers.push(window.setTimeout(() => map.getLayer(layerId) && map.setPaintProperty(layerId, 'line-opacity', 1), 430));
      flashTimers.push(window.setTimeout(() => {
        map.getSource(sourceId)?.setData(featureCollection([]));
        if (map.getLayer(layerId)) map.setPaintProperty(layerId, 'line-opacity', 0);
      }, 1650));
    } catch (error) {
      if (error.name !== 'AbortError') console.warn('Objektumriss konnte nicht geladen werden', error);
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
    clearObserver?.disconnect();
    const finish = () => {
      if (generation !== clearGeneration || store.getState().layout.tableOpen) return;
      store.setState({ selection: { parcels: [], buildings: [], loading: false } }, 'selection-clear');
    };
    clearObserver = new ResizeObserver(() => {
      if (generation !== clearGeneration || store.getState().layout.tableOpen) {
        clearObserver?.disconnect();
        clearObserver = null;
        return;
      }
      if (selectionDock.getBoundingClientRect().height > 1.5) return;
      clearObserver?.disconnect();
      clearObserver = null;
      finish();
    });
    clearObserver.observe(selectionDock);
    layout.setTable(false);
  }

  map.on('load', addLayers);
  map.on('click', (event) => {
    if (store.getState().activeTool === 'select') selectAt(event.lngLat, true);
  });
  store.subscribe((state, reason) => { if (reason.startsWith('selection') || reason === 'restore') render(state); });
  return { selectAt, flash, clear, render };
}
