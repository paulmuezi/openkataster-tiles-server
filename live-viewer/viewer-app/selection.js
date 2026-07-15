import { addressLabel, escapeHtml, featureKey, formatArea, polygonAreaMeters } from './utils.js';

const HIDDEN_DYNAMIC_FIELDS = new Set([
  'source_db', 'gml_id', 'id', 'geometry', 'bbox', 'center', 'addresses', 'address',
  'flurstueckskennzeichen',
  'gebaeudekennzeichen',
  'zaehler', 'nenner', 'flurstuecksfolge', 'nutzungen', 'nutzung_haupt',
  'gemeinde', 'gemeindenummer', 'kreis', 'kreisnummer', 'land', 'landnummer', 'regierungsbezirk'
]);

const FIELD_LABELS = {
  gemeindeteil: 'Gemeindeteil'
};

const BUILDING_OFFICIAL_AREA_KEYS = ['amtliche_flaeche_m2', 'grundflaeche_m2'];
const BUILDING_GEOMETRIC_AREA_KEYS = ['geometrische_flaeche_m2'];

function hasValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim() !== '';
  if (Array.isArray(value)) return value.length > 0;
  return typeof value !== 'object' || Object.keys(value).length > 0;
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

function firstPresentValue(item, keys) {
  for (const key of keys) if (hasValue(item?.[key])) return item[key];
  return null;
}

function buildingOfficialArea(item) {
  return firstPresentValue(item, BUILDING_OFFICIAL_AREA_KEYS);
}

function buildingGeometricArea(item) {
  if (hasValue(buildingOfficialArea(item))) return null;
  const stored = firstPresentValue(item, BUILDING_GEOMETRIC_AREA_KEYS);
  if (hasValue(stored)) return stored;
  const calculated = geometryArea(item?.geometry);
  return calculated > 0 ? calculated : null;
}

export function buildingAreaVisibility(buildings = [], { preview = false } = {}) {
  const states = buildings.map((item) => {
    if (preview) {
      const available = new Set(Array.isArray(item?.available_fields) ? item.available_fields : []);
      return {
        official: BUILDING_OFFICIAL_AREA_KEYS.some((key) => available.has(key)),
        geometric: BUILDING_GEOMETRIC_AREA_KEYS.some((key) => available.has(key)) || Boolean(item?.geometry)
      };
    }
    return {
      official: hasValue(buildingOfficialArea(item)),
      geometric: hasValue(buildingGeometricArea(item))
    };
  });
  return {
    showOfficial: states.some((state) => state.official),
    showGeometric: states.some((state) => !state.official && state.geometric)
  };
}

export function selectionAddressLabels(item = {}) {
  const entries = Array.isArray(item.addresses) && item.addresses.length
    ? item.addresses
    : [item.address].filter(hasValue);
  const labels = entries.map((address) => {
    if (typeof address === 'string') return address.trim();
    if (!address || typeof address !== 'object') return '';
    const streetLine = address.street_house || [address.street, address.house_number].filter(Boolean).join(' ');
    const placeLine = [
      address.post_code || address.postal_code || address.postcode,
      address.city || address.municipality || address.place
    ].filter(Boolean).join(' ');
    return String(address.label || [streetLine, placeLine].filter(Boolean).join(', ')).trim();
  }).filter(Boolean);
  const fallback = addressLabel(item);
  if (!labels.length && fallback && fallback !== '–') labels.push(fallback);
  return [...new Set(labels)];
}

export function previewNoticeScrollOffset({ scrollLeft, scrollWidth, clientWidth }) {
  return Math.min(Math.max(scrollLeft, 0), Math.max(scrollWidth - clientWidth, 0));
}

export function resolveHitStack({
  currentBuildings = [],
  currentParcels = [],
  hitBuildings = [],
  hitParcels = [],
  additive = false,
  preferredKind = null
} = {}) {
  const geometryKeys = new WeakMap();
  const geometryKey = (item) => {
    const geometry = item?.geometry;
    if (!geometry || typeof geometry !== 'object') return '';
    if (!geometryKeys.has(geometry)) geometryKeys.set(geometry, JSON.stringify(geometry));
    return geometryKeys.get(geometry);
  };
  const isPreview = (item) => Boolean(item?.preview_id && !item?.gml_id);
  const selectionBucket = (items) => {
    const values = new Map();
    const equivalentKey = (item) => {
      const directKey = featureKey(item);
      if (values.has(directKey)) return directKey;
      const candidateGeometry = geometryKey(item);
      if (!candidateGeometry) return null;
      for (const [key, candidate] of values) {
        // Preview responses deliberately hide the cadastral ID. During an access
        // change, the same geometry can therefore arrive once as preview_id and
        // once as source_db:gml_id. Reconcile only that cross-mode pair; two
        // distinct full records with identical geometry remain distinct.
        if (isPreview(candidate) === isPreview(item)) continue;
        if (geometryKey(candidate) === candidateGeometry) return key;
      }
      return null;
    };
    const bucket = {
      has: (item) => equivalentKey(item) !== null,
      delete: (item) => {
        const key = equivalentKey(item);
        if (key !== null) values.delete(key);
      },
      set: (item) => {
        const equivalent = equivalentKey(item);
        if (equivalent !== null && equivalent !== featureKey(item)) values.delete(equivalent);
        values.set(featureKey(item), item);
      },
      values: () => [...values.values()]
    };
    items.forEach(bucket.set);
    return bucket;
  };
  const buildings = selectionBucket(additive ? currentBuildings : []);
  const parcels = selectionBucket(additive ? currentParcels : []);
  const includeAll = !preferredKind || preferredKind === 'all';
  const keyedHits = (items) => [...new Map(items.map((item) => [featureKey(item), item])).values()];
  const buildingHits = includeAll || preferredKind === 'building' ? keyedHits(hitBuildings) : [];
  const parcelHits = includeAll || preferredKind === 'parcel' ? keyedHits(hitParcels) : [];

  if (!additive) {
    buildingHits.forEach(buildings.set);
    parcelHits.forEach(parcels.set);
  } else if (buildingHits.length) {
    const removeBuildings = buildingHits.every(buildings.has);
    for (const item of buildingHits) {
      if (removeBuildings) buildings.delete(item);
      else buildings.set(item);
    }
    // A building click auto-adds its parcel only while adding the building. When
    // removing a building, the parcel keeps its independently chosen state.
    if (!removeBuildings) parcelHits.forEach(parcels.set);
  } else if (parcelHits.length) {
    const removeParcels = parcelHits.every(parcels.has);
    for (const item of parcelHits) {
      if (removeParcels) parcels.delete(item);
      else parcels.set(item);
    }
  }
  return { buildings: buildings.values(), parcels: parcels.values() };
}

export function withoutSelectionItem({ buildings = [], parcels = [] } = {}, kind, key) {
  const safeBuildings = Array.isArray(buildings) ? buildings : [];
  const safeParcels = Array.isArray(parcels) ? parcels : [];
  return {
    buildings: kind === 'building' ? safeBuildings.filter((item) => featureKey(item) !== key) : [...safeBuildings],
    parcels: kind === 'parcel' ? safeParcels.filter((item) => featureKey(item) !== key) : [...safeParcels]
  };
}

export function createSelectionController({ map, api, store, layout, elements }) {
  const { selectionContent, selectionCount, selectTool, selectionDock } = elements;
  let request = null;
  let geometryRequest = null;
  let flashTimers = [];
  let clearGeneration = 0;
  let clearObserver = null;
  let noticeAlignmentFrame = null;

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
    const seenRings = new Set();
    for (const item of items) {
      for (const ring of collectRings(item.geometry)) {
        const ringEdges = [];
        for (let index = 1; index < ring.length; index += 1) {
          const start = ring[index - 1];
          const end = ring[index];
          if (!Array.isArray(start) || !Array.isArray(end)) continue;
          const startKey = coordinateKey(start);
          const endKey = coordinateKey(end);
          if (startKey === endKey) continue;
          const key = startKey < endKey ? `${startKey}|${endKey}` : `${endKey}|${startKey}`;
          ringEdges.push({ key, start, end, startKey, endKey });
        }
        const ringKey = ringEdges.map((edge) => edge.key).sort().join('||');
        if (!ringKey || seenRings.has(ringKey)) continue;
        seenRings.add(ringKey);
        for (const { key, start, end, startKey, endKey } of ringEdges) {
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

  function alignPreviewNotices() {
    noticeAlignmentFrame = null;
    const notices = selectionContent.querySelectorAll?.('.selection-pro-notice-copy');
    if (!notices?.length) return;
    const mobile = window.matchMedia?.('(max-width: 760px)').matches ?? window.innerWidth <= 760;
    if (!mobile) {
      for (const notice of notices) {
        notice.style.removeProperty('--selection-pro-notice-width');
        notice.style.removeProperty('--selection-pro-notice-shift');
      }
      return;
    }
    const viewportWidth = selectionContent.clientWidth;
    if (!viewportWidth) return;
    const noticeWidth = Math.max(viewportWidth - 20, 1);
    const shift = previewNoticeScrollOffset({
      scrollLeft: selectionContent.scrollLeft,
      scrollWidth: selectionContent.scrollWidth,
      clientWidth: viewportWidth
    });
    for (const notice of notices) {
      notice.style.setProperty('--selection-pro-notice-width', `${noticeWidth}px`);
      notice.style.setProperty('--selection-pro-notice-shift', `${shift}px`);
    }
  }

  function schedulePreviewNoticeAlignment() {
    if (typeof window === 'undefined' || typeof selectionContent.querySelectorAll !== 'function') return;
    if (noticeAlignmentFrame !== null) return;
    noticeAlignmentFrame = window.requestAnimationFrame(alignPreviewNotices);
  }

  function render(state = store.getState()) {
    const { parcels, buildings, loading } = state.selection;
    const html = state.access.pro
      ? [buildings.length ? buildingTable(buildings) : '', parcels.length ? parcelTable(parcels) : ''].join('')
      : freePreviewTable(buildings, parcels);
    if (selectionContent.innerHTML !== html) selectionContent.innerHTML = html;
    schedulePreviewNoticeAlignment();
    const count = parcels.length + buildings.length;
    selectionCount.textContent = loading && !count ? 'Auswahl wird geladen' : count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
    updateSources();
    const canRevealTable = !layout.isMobile() || ['select', 'search'].includes(state.activeTool);
    if (count && !state.layout.tableOpen && canRevealTable) layout.setTable(true);
    selectionDock.classList.toggle('is-loading', loading);
  }

  function freePreviewTable(buildings, parcels) {
    const sections = [];
    if (buildings.length) sections.push(lockedPreviewTable('Gebäude', buildings, [
      { label: 'Gebäudefunktion', keys: ['gebaeudefunktion_text', 'gebaeudefunktion'] },
      { label: 'Name', keys: ['name'] },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'], compact: true },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'], compact: true },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'], compact: true },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'], compact: true },
      { label: 'Objekthöhe', keys: ['objekthoehe_m'], compact: true },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'], compact: true },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'], alwaysVisible: true, slot: 'address' },
      { label: 'Flächen', keys: ['geschossflaeche_m2', ...BUILDING_OFFICIAL_AREA_KEYS, ...BUILDING_GEOMETRIC_AREA_KEYS], alwaysVisible: true, slot: 'areas' }
    ], 'building', 'Gebäudeinfos sind im Pro-Plan verfügbar.'));
    if (parcels.length) sections.push(lockedPreviewTable('Flurstücke', parcels, [
      { label: 'Gem.-Schl.', title: 'Gemarkungsschlüssel', keys: ['gemarkungsschluessel', 'gemarkung_key'], compact: true },
      { label: 'Gemarkung', keys: ['gemarkung', 'gemarkungsnummer'], compact: true },
      { label: 'Flur', keys: ['flur'], compact: true },
      { label: 'Flurstück', keys: ['flurstueck', 'zaehler', 'nenner'], compact: true },
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'] },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'] },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'] },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'] },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'], compact: true },
      { label: 'Adressen', keys: ['addresses', 'address'], alwaysVisible: true, slot: 'address' },
      { label: 'Flächen', keys: ['amtliche_flaeche_m2'], alwaysVisible: true, slot: 'areas' }
    ], 'parcel', 'Flurstücksinfos sind im Pro-Plan verfügbar.'));
    return sections.join('');
  }

  function lockedPreviewTable(title, items, definitions, kind, message) {
    const available = new Set(items.flatMap((item) => Array.isArray(item.available_fields) ? item.available_fields : []));
    const columns = definitions.filter((column) => column.visible !== false && (column.alwaysVisible || column.keys.some((key) => available.has(key))));
    const headers = columns.map((column) => {
      const fullLabel = column.title || column.label;
      return `<th${tableColumnAttributes(column)} title="${escapeHtml(fullLabel)}">${escapeHtml(column.label)}</th>`;
    }).join('');
    const notice = `<tr class="selection-pro-notice"><td colspan="${Math.max(columns.length, 1)}"><span class="selection-pro-notice-copy" role="note"><span>${escapeHtml(message)}</span><a href="/pro" target="_top">Pro freischalten</a></span></td></tr>`;
    return `<section class="selection-section" data-selection-kind="${kind}"><div class="selection-section-title">${escapeHtml(title)}</div><div class="selection-table-wrap"><table class="selection-data-table preview-table"><thead><tr>${headers}</tr></thead><tbody>${notice}</tbody></table></div></section>`;
  }

  function selectionActionHeader() {
    return '<th class="selection-action-column compact"><span class="sr-only">Auswahl</span></th>';
  }

  function selectionActionCell(item, kind) {
    const noun = kind === 'parcel' ? 'Flurstück' : 'Gebäude';
    return `<td class="selection-action-column compact"><button class="selection-item-remove" type="button" data-selection-remove-kind="${kind}" data-selection-remove-key="${escapeHtml(featureKey(item))}" aria-label="${noun} aus Auswahl entfernen" title="Aus Auswahl entfernen">×</button></td>`;
  }

  function tableColumnAttributes(column, additionalClasses = []) {
    const className = [
      ...additionalClasses,
      column.compact ? 'compact' : '',
      column.numeric ? 'numeric' : '',
      column.slot ? `selection-column-${column.slot}` : ''
    ].filter(Boolean).join(' ');
    const slot = column.slot ? ` data-selection-column="${escapeHtml(column.slot)}"` : '';
    return `${className ? ` class="${className}"` : ''}${slot}`;
  }


  function display(value) {
    return value === null || value === undefined || value === '' ? '–' : escapeHtml(value);
  }

  function addressChips(item) {
    const labels = selectionAddressLabels(item);
    return labels.length ? `<span class="address-list">${labels.map((label) => `<span class="address-chip">${escapeHtml(label)}</span>`).join('')}</span>` : '–';
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

  function areaList(rows) {
    if (!rows.length) return '–';
    return `<span class="selection-area-list">${rows.map((row) => `<span class="selection-area-label">${escapeHtml(row.label)}</span><span class="selection-area-value">${formatCell(row.value, 'area')}</span>`).join('')}</span>`;
  }

  function areaColumn(rows) {
    const visibleRows = rows.filter((row) => row.visible !== false);
    const rowValue = (row, item) => row.value ? row.value(item) : pick(item, row.keys);
    return {
      label: 'Flächen',
      keys: visibleRows.flatMap((row) => row.keys || []),
      alwaysVisible: true,
      slot: 'areas',
      value: (item) => visibleRows.map((row) => rowValue(row, item)).filter(hasValue),
      html: (item) => areaList(visibleRows.map((row) => ({ label: row.label, value: rowValue(row, item) }))),
      summary: (items) => areaList(visibleRows.map((row) => {
        const values = items.map((item) => rowValue(row, item)).filter(hasValue).map(Number).filter(Number.isFinite);
        return { label: row.label, value: values.length ? values.reduce((sum, value) => sum + value, 0) : null };
      }))
    };
  }

  function dynamicTable(title, items, definitions, kind) {
    const visibleDefinitions = definitions.filter((column) => column.visible !== false);
    const extra = extraColumns(items, definitions);
    const firstTrailingIndex = visibleDefinitions.findIndex((column) => column.slot);
    const orderedDefinitions = firstTrailingIndex < 0
      ? [...visibleDefinitions, ...extra]
      : [...visibleDefinitions.slice(0, firstTrailingIndex), ...extra, ...visibleDefinitions.slice(firstTrailingIndex)];
    const columns = orderedDefinitions.filter((column) => column.alwaysVisible || items.some((item) => hasValue(columnValue(column, item))));
    const headers = columns.map((column) => {
      const fullLabel = column.title || column.label;
      return `<th${tableColumnAttributes(column)} title="${escapeHtml(fullLabel)}">${escapeHtml(column.label)}</th>`;
    }).join('');
    const rows = items.map((item) => `<tr>${selectionActionCell(item, kind)}${columns.map((column) => {
      const value = columnValue(column, item);
      const content = column.html ? column.html(item, value) : formatCell(value, column.format);
      return `<td${tableColumnAttributes(column)}>${content}</td>`;
    }).join('')}</tr>`).join('');
    const firstSumIndex = columns.findIndex((column) => column.sum || column.summary);
    const hasTotals = items.length > 1 && firstSumIndex >= 0;
    const totals = hasTotals ? `<tfoot><tr><td class="summary-label" colspan="${firstSumIndex + 1}">Summe</td>${columns.slice(firstSumIndex).map((column) => {
      if (column.summary) return `<td${tableColumnAttributes(column, ['summary-value'])}>${column.summary(items)}</td>`;
      if (!column.sum) return '<td></td>';
      const values = items.map((item) => columnValue(column, item)).filter(hasValue).map(Number).filter(Number.isFinite);
      return `<td${tableColumnAttributes(column, ['summary-value'])}>${values.length ? formatCell(values.reduce((sum, value) => sum + value, 0), column.format) : '–'}</td>`;
    }).join('')}</tr></tfoot>` : '';
    return `<section class="selection-section" data-selection-kind="${kind}"><div class="selection-section-title">${escapeHtml(title)}</div><div class="selection-table-wrap"><table class="selection-data-table"><thead><tr>${selectionActionHeader()}${headers}</tr></thead><tbody>${rows}</tbody>${totals}</table></div></section>`;
  }

  function buildingTable(buildings) {
    const areaVisibility = buildingAreaVisibility(buildings);
    const showFloorArea = buildings.some((item) => hasValue(item.geschossflaeche_m2));
    const columns = [
      { label: 'Gebäudefunktion', keys: ['gebaeudefunktion_text', 'gebaeudefunktion'] },
      { label: 'Name', keys: ['name'] },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'], compact: true },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'], compact: true },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'], compact: true },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'], format: 'volume', compact: true },
      { label: 'Objekthöhe', keys: ['objekthoehe_m'], format: 'length', compact: true },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'], format: 'boolean', compact: true },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'], value: selectionAddressLabels, html: (item) => addressChips(item), alwaysVisible: true, slot: 'address' },
      areaColumn([
        { label: 'Geschossfläche', keys: ['geschossflaeche_m2'], visible: showFloorArea },
        { label: 'Amtliche Fläche', keys: BUILDING_OFFICIAL_AREA_KEYS, value: buildingOfficialArea, visible: areaVisibility.showOfficial },
        {
          label: 'Geometrische Fläche',
          keys: BUILDING_GEOMETRIC_AREA_KEYS,
          value: buildingGeometricArea,
          visible: areaVisibility.showGeometric
        }
      ])
    ];
    return dynamicTable('Gebäude', buildings, columns, 'building');
  }

  function parcelTable(parcels) {
    const columns = [
      { label: 'Gem.-Schl.', title: 'Gemarkungsschlüssel', keys: ['gemarkungsschluessel', 'gemarkung_key'], compact: true },
      { label: 'Gemarkung', keys: ['gemarkung', 'gemarkungsnummer'], value: (item) => item.gemarkung && item.gemarkungsnummer ? `${item.gemarkung} (${item.gemarkungsnummer})` : item.gemarkung || item.gemarkungsnummer, compact: true },
      { label: 'Flur', keys: ['flur'], compact: true },
      { label: 'Flurstück', keys: ['flurstueck', 'zaehler', 'nenner'], value: (item) => item.flurstueck || [item.zaehler, item.nenner].filter(Boolean).join('/'), compact: true },
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'], value: parcelUsage },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'], format: 'boolean' },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'], format: 'boolean' },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'], format: 'boolean' },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'], format: 'date', compact: true },
      { label: 'Adressen', keys: ['addresses', 'address'], value: selectionAddressLabels, html: (item) => addressChips(item), alwaysVisible: true, slot: 'address' },
      areaColumn([
        { label: 'Amtliche Fläche', keys: ['amtliche_flaeche_m2'] }
      ])
    ];
    return dynamicTable('Flurstücke', parcels, columns, 'parcel');
  }

  async function selectAt(lngLat, additive = false, preferredKind = null) {
    const state = store.getState();
    request?.abort();
    request = new AbortController();
    selectTool.classList.add('is-loading');
    store.setState({ selection: { ...state.selection, loading: true } }, 'selection-loading');
    try {
      const data = await (state.access.pro ? api.featureAt : api.featurePreviewAt)(
        lngLat.lng,
        lngLat.lat,
        request.signal
      );
      const next = store.getState();
      const hits = resolveHitStack({
        currentBuildings: next.selection.buildings,
        currentParcels: next.selection.parcels,
        hitBuildings: data.buildings || [],
        hitParcels: data.parcels || [],
        additive,
        preferredKind
      });
      store.setState({ selection: { ...hits, loading: false } }, 'selection');
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
  selectionContent.addEventListener('scroll', schedulePreviewNoticeAlignment, { passive: true });
  if (typeof window !== 'undefined') window.addEventListener('resize', schedulePreviewNoticeAlignment, { passive: true });
  selectionContent.addEventListener('click', (event) => {
    const button = event.target?.closest?.('[data-selection-remove-kind][data-selection-remove-key]');
    if (!button || !selectionContent.contains(button)) return;
    event.preventDefault();
    const current = store.getState();
    const next = withoutSelectionItem(
      current.selection,
      button.dataset.selectionRemoveKind,
      button.dataset.selectionRemoveKey
    );
    store.setState({ selection: { ...current.selection, ...next } }, 'selection-item-remove');
  });
  map.on('click', (event) => {
    if (store.getState().activeTool === 'select') {
      selectAt(event.lngLat, true);
    }
  });
  store.subscribe((state, reason) => { if (reason.startsWith('selection') || reason === 'restore') render(state); });
  return { selectAt, flash, clear, render };
}
