import { addressLabel, escapeHtml, featureKey, formatArea, polygonAreaMeters } from './utils.js';
import { normalizeSelectionReferences, selectionFromPayload } from './workspace.js?v=20260722-land-register-table1';

const HIDDEN_DYNAMIC_FIELDS = new Set([
  'source_db', 'gml_id', 'id', 'geometry', 'bbox', 'center', 'addresses', 'address',
  'address_relation_count', 'address_relation_limit', 'address_relations_truncated', 'lage',
  'flurstueckskennzeichen',
  'gebaeudekennzeichen',
  'zaehler', 'nenner', 'flurstuecksfolge', 'nutzungen', 'nutzung_haupt',
  'formal_land_register_entries', 'land_register_office_authority',
  'gemeinde', 'gemeindenummer', 'kreis', 'kreisnummer', 'land', 'landnummer', 'regierungsbezirk'
]);

const FIELD_LABELS = {
  gemeindeteil: 'Gemeindeteil',
  flaechenbestimmung: 'Flächenbestimmung',
  rechtsstatus_text: 'Rechtsstatus'
};

const BUILDING_OFFICIAL_AREA_KEYS = ['amtliche_flaeche_m2', 'grundflaeche_m2'];
const BUILDING_GEOMETRIC_AREA_KEYS = ['geometrische_flaeche_m2'];
const PARCEL_LOCATION_DISPLAY_MAX_LENGTH = 240;
const LAND_REGISTER_INLINE_SHEET_LIMIT = 5;
const WELCOME_HOVER_HIT_LAYERS = ['welcome-hover-building-hit', 'welcome-hover-parcel-hit'];
const WELCOME_HOVER_LAYERS = [
  'welcome-hover-parcel-hit', 'welcome-hover-parcel-line',
  'welcome-hover-building-hit', 'welcome-hover-building-line'
];
const WELCOME_HIDDEN_SELECTION_LAYERS = [
  'selected-parcels-v2', 'selected-buildings-v2',
  'search-highlight-parcels-v2', 'search-highlight-buildings-v2'
];

function hasValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim() !== '';
  if (Array.isArray(value)) return value.some(hasValue);
  return typeof value !== 'object' || Object.values(value).some(hasValue);
}

function normalizedDisplayText(value) {
  if (Array.isArray(value)) return value.map(normalizedDisplayText).filter(Boolean).join(', ');
  if (typeof value !== 'string' && typeof value !== 'number') return '';
  return String(value).replace(/[\u00ad\u200b-\u200d\u2060\ufeff]/gi, '').trim();
}

export function parcelDisplayNumber(item) {
  return normalizedDisplayText(
    item?.flurstueck
      || item?.grundstueck
      || item?.grundstuecksnummer
      || [item?.zaehler, item?.nenner].filter(hasValue).join('/')
  );
}

function buildingName(item) {
  return normalizedDisplayText(item?.name);
}

function isBayernLod2Building(item) {
  return String(item?.source_db || '').trim().toLocaleLowerCase('de-DE').replaceAll('_', '-') === 'bayern-lod2';
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

function compactDisplayKey(value) {
  return normalizedDisplayText(value)
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('de-DE')
    .replace(/[^a-z0-9]/g, '');
}

export function parcelDisplayLocation(item = {}) {
  const location = normalizedDisplayText(item.lage);
  if (!location || location.length > PARCEL_LOCATION_DISPLAY_MAX_LENGTH) return '';
  const locationKey = compactDisplayKey(location);
  if (!locationKey) return '';
  const addressKeys = new Set();
  for (const label of selectionAddressLabels(item)) {
    addressKeys.add(compactDisplayKey(label));
    addressKeys.add(compactDisplayKey(label.split(',', 1)[0]));
  }
  for (const address of Array.isArray(item.addresses) ? item.addresses : []) {
    if (!address || typeof address !== 'object') continue;
    addressKeys.add(compactDisplayKey(address.street_house));
    addressKeys.add(compactDisplayKey([address.street, address.house_number].filter(Boolean).join(' ')));
  }
  return addressKeys.has(locationKey) ? '' : location;
}

function landRegisterLabel(entry, nameKey, codeKey) {
  const label = normalizedDisplayText(entry?.[nameKey]);
  const code = normalizedDisplayText(entry?.[codeKey]);
  return { label: label || code, code };
}

/**
 * Keep the formal office/district/sheet relationship intact while grouping
 * sheets that belong to the same office and district.  An authority-only
 * relation may identify an Amtsgericht, but deliberately leaves Grundbuch and
 * Grundbuchblatt empty instead of inventing either value.
 */
export function landRegisterGroups(item = {}) {
  const groups = [];
  const byKey = new Map();
  const entries = Array.isArray(item?.formal_land_register_entries)
    ? item.formal_land_register_entries
    : [];

  for (const entry of entries) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
    const office = landRegisterLabel(entry, 'land_register_office_name', 'land_register_office_code');
    const district = landRegisterLabel(entry, 'district_name', 'district_code');
    const sheet = normalizedDisplayText(entry.sheet_number);
    if (!office.label && !district.label && !sheet) continue;
    const key = [office.code, office.label, district.code, district.label].join('\u0000');
    let group = byKey.get(key);
    if (!group) {
      group = {
        office: office.label,
        officeCode: office.code,
        district: district.label,
        districtCode: district.code,
        sheets: [],
        authorityOnly: false
      };
      byKey.set(key, group);
      groups.push(group);
    }
    if (sheet && !group.sheets.includes(sheet)) group.sheets.push(sheet);
  }

  const authority = item?.land_register_office_authority;
  const authorityEntries = Array.isArray(authority?.offices) && authority.offices.length
    ? authority.offices
    : authority && typeof authority === 'object' ? [{
      name: authority.office_name,
      code: authority.office_code
    }] : [];
  for (const entry of authorityEntries) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
    const office = landRegisterLabel(entry, 'name', 'code');
    if (!office.label) continue;
    const alreadyFormal = groups.some((group) => (
      (office.code && group.officeCode === office.code)
      || group.office === office.label
    ));
    if (alreadyFormal) continue;
    groups.push({
      office: office.label,
      officeCode: office.code,
      district: '',
      districtCode: '',
      sheets: [],
      authorityOnly: true
    });
  }

  return groups;
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

export function welcomeHoverCandidate(features = []) {
  const building = features.find((feature) => feature?.layer?.id === 'welcome-hover-building-hit');
  const parcel = features.find((feature) => feature?.layer?.id === 'welcome-hover-parcel-hit');
  const feature = building || parcel;
  if (!feature) return null;
  const id = feature.properties?.gml_id ?? feature.id;
  if (id === null || id === undefined || String(id).trim() === '') return null;
  const kind = building ? 'building' : 'parcel';
  return {
    id,
    key: `${kind}:${String(id)}`,
    kind,
    sourceLayer: kind === 'building' ? 'building_fills' : 'surfaces'
  };
}

export function waitForAccessReady(store) {
  const current = store.getState().access;
  if (current?.ready) return Promise.resolve(current);

  return new Promise((resolve) => {
    let settled = false;
    let unsubscribe = () => {};
    const finish = (state) => {
      if (settled || !state?.access?.ready) return;
      settled = true;
      unsubscribe();
      resolve(state.access);
    };
    unsubscribe = store.subscribe(finish);
    finish(store.getState());
  });
}

export function createSelectionController({
  map,
  api,
  store,
  layout,
  elements,
  datasetProfile = {
    id: 'deutschland',
    terminology: {
      cadastralDistrict: 'Gemarkung',
      parcel: 'Flurstück',
      parcelPlural: 'Flurstücke',
      parcelNumber: 'Flurstücksnummer',
      district: 'Flur'
    }
  },
  isWelcomeMode = () => false,
  onWelcomePointer = () => {}
}) {
  const { selectionContent, selectionCount, selectTool, selectionDock } = elements;
  const terms = datasetProfile.terminology;
  const parcelDistrictColumns = () => [
    {
      label: datasetProfile.id === 'oesterreich' ? 'KG-Nr.' : 'Gem.-Schl.',
      title: datasetProfile.id === 'oesterreich' ? 'Katastralgemeindenummer' : 'Gemarkungsschlüssel',
      keys: ['gemarkungsschluessel', 'gemarkung_key', 'katastralgemeindenummer', 'kg_nummer'],
      compact: true
    },
    {
      label: terms.cadastralDistrict,
      keys: ['gemarkung', 'gemarkungsname', 'gemarkungsnummer', 'katastralgemeinde', 'katastralgemeindenummer'],
      value: (item) => {
        const name = item.gemarkung || item.gemarkungsname || item.katastralgemeinde;
        const number = item.gemarkungsnummer || item.katastralgemeindenummer;
        return name && number ? `${name} (${number})` : name || number;
      },
      compact: true
    },
    terms.district
      ? { label: terms.district, keys: ['flur'], compact: true }
      : null,
    {
      label: terms.parcel,
      keys: ['flurstueck', 'grundstueck', 'grundstuecksnummer', 'zaehler', 'nenner'],
      value: parcelDisplayNumber,
      compact: true
    }
  ].filter(Boolean);
  let request = null;
  let restoreRequest = null;
  let geometryRequest = null;
  let flashTimers = [];
  let clearGeneration = 0;
  let clearObserver = null;
  let noticeAlignmentFrame = null;
  let columnAlignmentFrame = null;
  let welcomeHoverFrame = null;
  let pendingWelcomeHoverPoint = null;
  let activeWelcomeHover = null;
  let welcomeOverlayRects = [];
  let welcomeOverlayRectsAt = 0;
  let welcomePointerVisible = false;
  const fineHoverPointer = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(hover: hover) and (pointer: fine)')
    : { matches: false };

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
    setWelcomeMode(isWelcomeMode());
  }

  function restoreToolCursor() {
    const activeTool = store.getState().activeTool;
    const canvas = map.getCanvas?.();
    if (canvas?.style) canvas.style.cursor = ['export', 'measure', 'select'].includes(activeTool) ? 'crosshair' : '';
  }

  function setWelcomeHoverState(candidate, enabled) {
    if (!candidate || !map.getSource('alkis-v2')) return;
    try {
      map.setFeatureState({ source: 'alkis-v2', sourceLayer: candidate.sourceLayer, id: candidate.id }, { welcomeHover: enabled });
    } catch (_) {
      // Source tiles may be changing while the pointer leaves the map.
    }
  }

  function publishWelcomePointer(point) {
    if (!point) {
      if (!welcomePointerVisible) return;
      welcomePointerVisible = false;
      onWelcomePointer(null);
      return;
    }
    welcomePointerVisible = true;
    onWelcomePointer({ x: point.x, y: point.y });
  }

  function refreshWelcomeOverlayRects() {
    welcomeOverlayRectsAt = Date.now();
    welcomeOverlayRects = [];
    if (typeof window === 'undefined' || window.parent === window) return;
    try {
      const frameRect = window.frameElement?.getBoundingClientRect?.();
      if (!frameRect) return;
      const container = map.getContainer?.();
      const scaleX = frameRect.width > 0 && container?.clientWidth > 0
        ? frameRect.width / container.clientWidth
        : 1;
      const scaleY = frameRect.height > 0 && container?.clientHeight > 0
        ? frameRect.height / container.clientHeight
        : 1;
      welcomeOverlayRects = [...window.parent.document.querySelectorAll('.welcome-page [data-welcome-blocker]')]
        .map((element) => element.getBoundingClientRect())
        .filter((rect) => rect.width > 0 && rect.height > 0)
        .map((rect) => ({
          left: (rect.left - frameRect.left) / scaleX,
          right: (rect.right - frameRect.left) / scaleX,
          top: (rect.top - frameRect.top) / scaleY,
          bottom: (rect.bottom - frameRect.top) / scaleY
        }));
    } catch (_) {
      // Cross-origin embeddings simply rely on the parent clear-hover message.
    }
  }

  function welcomeOverlayContains(point) {
    if (!point || !isWelcomeMode()) return false;
    if (Date.now() - welcomeOverlayRectsAt > 160) refreshWelcomeOverlayRects();
    return welcomeOverlayRects.some((rect) => (
      point.x >= rect.left && point.x <= rect.right &&
      point.y >= rect.top && point.y <= rect.bottom
    ));
  }

  function clearWelcomeFeature() {
    setWelcomeHoverState(activeWelcomeHover, false);
    activeWelcomeHover = null;
    restoreToolCursor();
  }

  function clearWelcomeHover() {
    if (welcomeHoverFrame !== null) window.cancelAnimationFrame(welcomeHoverFrame);
    welcomeHoverFrame = null;
    pendingWelcomeHoverPoint = null;
    clearWelcomeFeature();
    publishWelcomePointer(null);
  }

  function setWelcomeMode(enabled) {
    welcomeOverlayRectsAt = 0;
    for (const id of WELCOME_HOVER_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', enabled ? 'visible' : 'none');
    }
    for (const id of WELCOME_HIDDEN_SELECTION_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', enabled ? 'none' : 'visible');
    }
    if (!enabled) clearWelcomeHover();
  }

  function renderWelcomeHover() {
    welcomeHoverFrame = null;
    const point = pendingWelcomeHoverPoint;
    pendingWelcomeHoverPoint = null;
    if (!point || !isWelcomeMode() || !fineHoverPointer.matches || welcomeOverlayContains(point)) {
      clearWelcomeHover();
      return;
    }
    publishWelcomePointer(point);
    if (map.getZoom() <= 17) {
      clearWelcomeFeature();
      return;
    }
    const layers = WELCOME_HOVER_HIT_LAYERS.filter((id) => map.getLayer(id));
    let candidate = null;
    if (layers.length) {
      try {
        candidate = welcomeHoverCandidate(map.queryRenderedFeatures([point.x, point.y], { layers }));
      } catch (_) {
        // A tile/style transition can invalidate a rendered query for one frame.
      }
    }
    if (candidate?.key === activeWelcomeHover?.key) return;
    setWelcomeHoverState(activeWelcomeHover, false);
    activeWelcomeHover = candidate;
    setWelcomeHoverState(activeWelcomeHover, true);
  }

  function scheduleWelcomeHover(event) {
    const point = event?.point ? { x: event.point.x, y: event.point.y } : null;
    if (!point || !isWelcomeMode() || !fineHoverPointer.matches || welcomeOverlayContains(point)) {
      clearWelcomeHover();
      return;
    }
    pendingWelcomeHoverPoint = point;
    if (welcomeHoverFrame === null) welcomeHoverFrame = window.requestAnimationFrame(renderWelcomeHover);
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

  function textWidth(element) {
    const range = element?.ownerDocument?.createRange?.();
    if (!range) return Number(element?.scrollWidth) || 0;
    range.selectNodeContents(element);
    return range.getBoundingClientRect().width;
  }

  function cellPadding(cell) {
    const style = window.getComputedStyle(cell);
    return (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0);
  }

  function addressCellContentWidth(cell) {
    const entries = [...cell.querySelectorAll('.address-chip, .address-relation-note')];
    return entries.length ? Math.max(...entries.map(textWidth)) : textWidth(cell);
  }

  function areaCellContentWidth(cell) {
    const grid = cell.querySelector('.selection-area-grid');
    if (!grid) return textWidth(cell);
    const entries = [...grid.children];
    const gap = Number.parseFloat(window.getComputedStyle(grid).columnGap) || 0;
    const entryWidth = entries.length ? Math.max(...entries.map(textWidth)) : 0;
    return entryWidth * entries.length + gap * Math.max(entries.length - 1, 0);
  }

  function alignSelectionColumns() {
    columnAlignmentFrame = null;
    if (!selectionContent.style || typeof selectionContent.querySelectorAll !== 'function') return;
    const slots = [
      { name: 'address', property: '--selection-address-width', measure: addressCellContentWidth },
      { name: 'areas', property: '--selection-areas-width', measure: areaCellContentWidth }
    ];
    for (const slot of slots) selectionContent.style.removeProperty(slot.property);
    for (const slot of slots) {
      const cells = [...selectionContent.querySelectorAll(`[data-selection-column="${slot.name}"]`)];
      if (!cells.length) continue;
      const width = Math.ceil(Math.max(...cells.map((cell) => slot.measure(cell) + cellPadding(cell))));
      if (Number.isFinite(width) && width > 0) selectionContent.style.setProperty(slot.property, `${width}px`);
    }
  }

  function scheduleSelectionColumnAlignment() {
    if (typeof window === 'undefined' || typeof selectionContent.querySelectorAll !== 'function') return;
    if (columnAlignmentFrame !== null) return;
    columnAlignmentFrame = window.requestAnimationFrame(alignSelectionColumns);
  }

  function render(state = store.getState()) {
    const { parcels, buildings, loading } = state.selection;
    const html = state.access.ready && state.access.pro
      ? [buildings.length ? buildingTable(buildings) : '', parcels.length ? parcelTable(parcels) : ''].join('')
      : freePreviewTable(buildings, parcels);
    if (selectionContent.innerHTML !== html) selectionContent.innerHTML = html;
    schedulePreviewNoticeAlignment();
    scheduleSelectionColumnAlignment();
    const count = parcels.length + buildings.length;
    selectionCount.textContent = loading && !count ? 'Auswahl wird geladen' : count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
    updateSources();
    const canRevealTable = !layout.isMobile() || ['select', 'search'].includes(state.activeTool);
    if (count && !state.layout.tableOpen && canRevealTable) layout.setTable(true);
    selectionDock.classList.toggle('is-loading', loading);
  }

  function freePreviewTable(buildings, parcels) {
    const sections = [];
    const buildingAreas = buildingAreaVisibility(buildings, { preview: true });
    const showBuildingFloorArea = buildings.some((item) => Array.isArray(item.available_fields) && item.available_fields.includes('geschossflaeche_m2'));
    const showBuildingObjectHeight = buildings.some((item) => (
      !isBayernLod2Building(item)
      && Array.isArray(item.available_fields)
      && item.available_fields.includes('objekthoehe_m')
    ));
    if (buildings.length) sections.push(lockedPreviewTable('Gebäude', buildings, [
      { label: 'Gebäudefunktion', keys: ['gebaeudefunktion_text', 'gebaeudefunktion'] },
      { label: 'Name', keys: ['name'], value: buildingName },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'], compact: true },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'], compact: true },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'], compact: true },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'], compact: true },
      { label: 'Objekthöhe', keys: ['objekthoehe_m'], visible: showBuildingObjectHeight, compact: true },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'], compact: true },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'], alwaysVisible: true, slot: 'address' },
      areaColumn([
        { kind: 'floor', label: 'Geschossfläche', keys: ['geschossflaeche_m2'], visible: showBuildingFloorArea },
        { kind: 'official', label: 'Amtliche Fläche', keys: BUILDING_OFFICIAL_AREA_KEYS, visible: buildingAreas.showOfficial },
        { kind: 'geometric', label: 'Geometrische Fläche', keys: BUILDING_GEOMETRIC_AREA_KEYS, visible: buildingAreas.showGeometric }
      ])
    ], 'building', 'Gebäudeinfos sind im Pro-Plan verfügbar.'));
    if (parcels.length) sections.push(lockedPreviewTable(terms.parcelPlural, parcels, [
      ...parcelDistrictColumns(),
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'] },
      { label: 'Lage', keys: ['lage'] },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'] },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'] },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'] },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'], compact: true },
      landRegisterColumn({ preview: true }),
      { label: 'Adressen', keys: ['addresses', 'address'], alwaysVisible: true, slot: 'address' },
      areaColumn([
        { kind: 'official', label: 'Amtliche Fläche', keys: ['amtliche_flaeche_m2'] }
      ])
    ], 'parcel', datasetProfile.id === 'deutschland'
      ? 'Flurstücksinfos sind im Pro-Plan verfügbar.'
      : `${terms.parcel}sinformationen sind im Pro-Plan verfügbar.`));
    return sections.join('');
  }

  function lockedPreviewTable(title, items, definitions, kind, message) {
    const available = new Set(items.flatMap((item) => Array.isArray(item.available_fields) ? item.available_fields : []));
    const columns = definitions.filter((column) => column.visible !== false && (column.alwaysVisible || column.keys.some((key) => available.has(key))));
    const firstSlotIndex = columns.findIndex((column) => column.slot);
    const fillIndex = firstSlotIndex > 0 ? firstSlotIndex - 1 : -1;
    const headers = columns.map((column, index) => {
      const fullLabel = column.title || column.label;
      const content = column.headerHtml || escapeHtml(column.label);
      return `<th${tableColumnAttributes(column, index === fillIndex ? ['selection-column-fill'] : [])} title="${escapeHtml(fullLabel)}">${content}</th>`;
    }).join('');
    const rows = items.map((item, rowIndex) => {
      const availableFields = new Set(Array.isArray(item.available_fields) ? item.available_fields : []);
      const cells = columns.map((column, columnIndex) => {
        const attributes = tableColumnAttributes(column, columnIndex === fillIndex ? ['selection-column-fill'] : []);
        const hasPreviewValue = column.keys.some((key) => availableFields.has(key));
        if (!hasPreviewValue) return `<td${attributes}><span class="selection-locked-empty" aria-hidden="true">–</span></td>`;
        const content = column.previewHtml
          ? column.previewHtml(item, rowIndex)
          : escapeHtml(lockedPreviewSample(column, rowIndex, columnIndex));
        return `<td${attributes}><span class="selection-locked-value" aria-hidden="true">${content}</span></td>`;
      }).join('');
      return `<tr class="selection-locked-row">${previewSelectionActionCell(item, kind)}${cells}</tr>`;
    }).join('');
    const firstSummaryIndex = columns.findIndex((column) => typeof column.previewSummaryHtml === 'function');
    const totals = items.length > 1 && firstSummaryIndex >= 0
      ? `<tfoot><tr><td class="summary-label" colspan="${firstSummaryIndex + 1}">Summe</td>${columns.slice(firstSummaryIndex).map((column) => {
        const attributes = tableColumnAttributes(column, ['summary-value']);
        if (typeof column.previewSummaryHtml !== 'function') return `<td${attributes}></td>`;
        return `<td${attributes}><span class="selection-locked-value selection-locked-summary" aria-hidden="true">${column.previewSummaryHtml()}</span></td>`;
      }).join('')}</tr></tfoot>`
      : '';
    const notice = `<tr class="selection-pro-notice"><td colspan="${Math.max(columns.length + 1, 1)}"><span class="selection-pro-notice-copy" role="note"><span>${escapeHtml(message)}</span><a href="/pro" target="_top">Pro freischalten</a></span></td></tr>`;
    return `<section class="selection-section" data-selection-kind="${kind}"><div class="selection-section-title">${escapeHtml(title)}</div><div class="selection-table-wrap"><table class="selection-data-table preview-table"><thead><tr>${selectionActionHeader()}${headers}</tr></thead><tbody>${rows}${notice}</tbody>${totals}</table></div></section>`;
  }

  function lockedPreviewSample(column, rowIndex, columnIndex) {
    const label = String(column.title || column.label || '');
    const sampleIndex = (rowIndex * 3 + columnIndex) % 3;
    if (column.slot === 'address') return ['Musterstraße 12', 'Beispielweg 8', 'Am Markt 4'][sampleIndex];
    if (/Gebäudefunktion/i.test(label)) return ['Wohngebäude', 'Nebengebäude', 'Garage'][sampleIndex];
    if (/Gemarkungsschlüssel|Gem\.-Schl\.|Katastralgemeindenummer|KG-Nr\./i.test(label)) return ['032410', '051230', '160510'][sampleIndex];
    if (/Gemarkung|Katastralgemeinde/i.test(label)) return ['Musterfeld', 'Innenstadt', 'Nord'][sampleIndex];
    if (/Flurstück|Grundstück/i.test(label)) return ['123/4', '77/9', '4752'][sampleIndex];
    if (/Flur\b/i.test(label)) return ['7', '15', '0'][sampleIndex];
    if (/Baujahr|Entstehung/i.test(label)) return ['1998', '2012', '2021'][sampleIndex];
    if (column.compact) return ['2', '4', '7'][sampleIndex];
    return ['Beispielangabe', 'Musterwert', 'Weitere Angabe'][sampleIndex];
  }

  function selectionActionHeader() {
    return '<th class="selection-action-column compact"><span class="sr-only">Auswahl</span></th>';
  }

  function selectionActionCell(item, kind) {
    const noun = kind === 'parcel' ? terms.parcel : 'Gebäude';
    return `<td class="selection-action-column compact"><button class="selection-item-remove" type="button" data-selection-remove-kind="${kind}" data-selection-remove-key="${escapeHtml(featureKey(item))}" aria-label="${noun} aus Auswahl entfernen" title="Aus Auswahl entfernen">×</button></td>`;
  }

  function previewSelectionActionCell(item, kind) {
    const key = typeof item?.preview_id === 'string' ? item.preview_id.trim() : '';
    if (!key) return '<td class="selection-action-column compact"></td>';
    const noun = kind === 'parcel' ? terms.parcel : 'Gebäude';
    return `<td class="selection-action-column compact"><button class="selection-item-remove" type="button" data-selection-remove-kind="${kind}" data-selection-remove-key="${escapeHtml(key)}" aria-label="${noun} aus Auswahl entfernen" title="Aus Auswahl entfernen">×</button></td>`;
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
    const relationCount = Number(item?.address_relation_count);
    const relationLimit = Number(item?.address_relation_limit);
    const truncated = item?.address_relations_truncated === true && Number.isInteger(relationCount) && relationCount > 0;
    const notice = truncated
      ? `<span class="address-relation-note">${Number.isInteger(relationLimit) && relationLimit > 0 ? `${escapeHtml(relationLimit.toLocaleString('de-DE'))} von ${escapeHtml(relationCount.toLocaleString('de-DE'))} amtlichen Adresszuordnungen berücksichtigt` : `Von ${escapeHtml(relationCount.toLocaleString('de-DE'))} amtlichen Adresszuordnungen wurde nur ein Teil berücksichtigt`}</span>`
      : '';
    const chips = labels.map((label) => `<span class="address-chip">${escapeHtml(label)}</span>`).join('');
    return chips || notice ? `<span class="address-list">${chips}${notice}</span>` : '–';
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

  function landRegisterValueHtml(value, code = '') {
    if (!value) return '–';
    const title = code && code !== value
      ? ` title="Kennziffer ${escapeHtml(code)}"`
      : '';
    return `<span${title}>${escapeHtml(value)}</span>`;
  }

  function landRegisterSheetListHtml(sheets = []) {
    if (!sheets.length) return '–';
    const initial = sheets.slice(0, LAND_REGISTER_INLINE_SHEET_LIMIT)
      .map((sheet) => `<span>${escapeHtml(sheet)}</span>`)
      .join('');
    const remaining = sheets.slice(LAND_REGISTER_INLINE_SHEET_LIMIT);
    if (!remaining.length) return `<div class="selection-land-register-sheet-list">${initial}</div>`;
    const more = remaining.map((sheet) => `<span>${escapeHtml(sheet)}</span>`).join('');
    return `<div class="selection-land-register-sheet-list">${initial}<details class="selection-land-register-more"><summary>Weitere ${escapeHtml(remaining.length.toLocaleString('de-DE'))} anzeigen</summary><div class="selection-land-register-more-list">${more}</div></details></div>`;
  }

  function landRegisterGrid(groups, { header = false, preview = false } = {}) {
    if (header) {
      return '<span class="selection-land-register-grid selection-land-register-header"><span>Amtsgericht</span><span>Grundbuch</span><span>Grundbuchblatt</span></span>';
    }
    if (!groups.length) return '–';
    if (preview) {
      const group = groups[0];
      return `<span class="selection-land-register-grid selection-land-register-values"><span>${landRegisterValueHtml(group.office, group.officeCode)}</span><span>${landRegisterValueHtml(group.district, group.districtCode)}</span><span class="selection-land-register-sheet-list">${group.sheets.map((sheet) => `<span>${escapeHtml(sheet)}</span>`).join('')}</span></span>`;
    }
    const rows = groups.map((group) => [
      `<div class="selection-land-register-office">${landRegisterValueHtml(group.office, group.officeCode)}</div>`,
      `<div class="selection-land-register-district">${landRegisterValueHtml(group.district, group.districtCode)}</div>`,
      `<div class="selection-land-register-sheets">${landRegisterSheetListHtml(group.sheets)}</div>`
    ].join('')).join('');
    return `<div class="selection-land-register-grid selection-land-register-values">${rows}</div>`;
  }

  function landRegisterColumn({ preview = false } = {}) {
    const previewGroups = [{
      office: 'Amtsgericht Musterstadt',
      officeCode: '',
      district: 'Musterbezirk',
      districtCode: '',
      sheets: ['123', '124'],
      authorityOnly: false
    }];
    return {
      label: 'Grundbuchdaten',
      title: 'Amtsgericht, Grundbuch und Grundbuchblatt',
      keys: ['formal_land_register_entries', 'land_register_office_authority'],
      slot: 'land-register',
      headerHtml: landRegisterGrid([], { header: true }),
      value: landRegisterGroups,
      html: (item) => landRegisterGrid(landRegisterGroups(item)),
      previewHtml: preview ? () => landRegisterGrid(previewGroups, { preview: true }) : undefined
    };
  }

  function areaGrid(rows, { header = false } = {}) {
    if (!rows.length) return '–';
    const className = header ? 'selection-area-grid selection-area-header' : 'selection-area-grid selection-area-values';
    const entries = rows.map((row) => {
      const content = header ? escapeHtml(row.label) : formatCell(row.value, 'area');
      return `<span data-area-kind="${escapeHtml(row.kind)}">${content}</span>`;
    }).join('');
    return `<span class="${className}" style="--selection-area-count:${rows.length}">${entries}</span>`;
  }

  function areaColumn(rows) {
    const visibleRows = rows.filter((row) => row.visible !== false);
    const rowValue = (row, item) => row.value ? row.value(item) : pick(item, row.keys);
    const label = visibleRows.map((row) => row.label).join(' / ');
    return {
      label,
      title: label,
      // Auch bewusst ausgeblendete Standardfelder gelten als verarbeitet.
      // Andernfalls können sie als generische Rohdatenspalte zurückkehren.
      keys: rows.flatMap((row) => row.keys || []),
      alwaysVisible: true,
      visible: visibleRows.length > 0,
      slot: 'areas',
      headerHtml: areaGrid(visibleRows, { header: true }),
      previewSummaryHtml: () => areaGrid(visibleRows.map((row, areaIndex) => ({
        ...row,
        value: 1234 + areaIndex * 742
      }))),
      previewHtml: (item, rowIndex) => {
        const availableFields = new Set(Array.isArray(item.available_fields) ? item.available_fields : []);
        return areaGrid(visibleRows.map((row, areaIndex) => ({
          ...row,
          value: row.keys.some((key) => availableFields.has(key))
            ? 486 + rowIndex * 137 + areaIndex * 59
            : null
        })));
      },
      value: (item) => visibleRows.map((row) => rowValue(row, item)).filter(hasValue),
      html: (item) => areaGrid(visibleRows.map((row) => ({ ...row, value: rowValue(row, item) }))),
      summary: (items) => areaGrid(visibleRows.map((row) => {
        const values = items.map((item) => rowValue(row, item)).filter(hasValue).map(Number).filter(Number.isFinite);
        return { ...row, value: values.length ? values.reduce((sum, value) => sum + value, 0) : null };
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
    const firstSlotIndex = columns.findIndex((column) => column.slot);
    const fillIndex = firstSlotIndex > 0 ? firstSlotIndex - 1 : -1;
    const headers = columns.map((column, index) => {
      const fullLabel = column.title || column.label;
      const content = column.headerHtml || escapeHtml(column.label);
      return `<th${tableColumnAttributes(column, index === fillIndex ? ['selection-column-fill'] : [])} title="${escapeHtml(fullLabel)}">${content}</th>`;
    }).join('');
    const rows = items.map((item) => `<tr>${selectionActionCell(item, kind)}${columns.map((column, index) => {
      const value = columnValue(column, item);
      const content = column.html ? column.html(item, value) : formatCell(value, column.format);
      return `<td${tableColumnAttributes(column, index === fillIndex ? ['selection-column-fill'] : [])}>${content}</td>`;
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
      { label: 'Name', keys: ['name'], value: buildingName },
      { label: 'Vollgeschosse', keys: ['geschosse_oberirdisch'], compact: true },
      { label: 'Unterirdische Geschosse', keys: ['geschosse_unterirdisch'], compact: true },
      { label: 'Dachform', keys: ['dachform_text', 'dachform'] },
      { label: 'Dachart', keys: ['dachart'] },
      { label: 'Dachgeschossausbau', keys: ['dachgeschossausbau_text', 'dachgeschossausbau'] },
      { label: 'Bauweise', keys: ['bauweise_text', 'bauweise'] },
      { label: 'Baujahr', keys: ['baujahr'], compact: true },
      { label: 'Umbauter Raum', keys: ['umbauter_raum_m3'], format: 'volume', compact: true },
      {
        label: 'Objekthöhe',
        keys: ['objekthoehe_m'],
        value: (item) => isBayernLod2Building(item) ? null : item.objekthoehe_m,
        format: 'length',
        compact: true
      },
      { label: 'Lage', keys: ['lage_zur_erdoberflaeche_text', 'lage_zur_erdoberflaeche'] },
      { label: 'Hochhaus', keys: ['hochhaus'], format: 'boolean', compact: true },
      { label: 'Weitere Gebäudefunktion', keys: ['weitere_gebaeudefunktion_text', 'weitere_gebaeudefunktion'] },
      { label: 'Zustand', keys: ['zustand_text', 'zustand'] },
      { label: 'Adressen', keys: ['addresses', 'address'], value: selectionAddressLabels, html: (item) => addressChips(item), alwaysVisible: true, slot: 'address' },
      areaColumn([
        { kind: 'floor', label: 'Geschossfläche', keys: ['geschossflaeche_m2'], visible: showFloorArea },
        { kind: 'official', label: 'Amtliche Fläche', keys: BUILDING_OFFICIAL_AREA_KEYS, value: buildingOfficialArea, visible: areaVisibility.showOfficial },
        {
          kind: 'geometric',
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
      ...parcelDistrictColumns(),
      { label: 'Nutzung', keys: ['nutzungen', 'nutzung_haupt', 'nutzung', 'tatsaechliche_nutzung', 'thema'], value: parcelUsage },
      { label: 'Lage', keys: ['lage'], value: parcelDisplayLocation },
      { label: 'Gemeindeteil', keys: ['gemeindeteil'] },
      { label: 'Abweichender Rechtszustand', keys: ['abweichender_rechtszustand'], format: 'boolean' },
      { label: 'Rechtsbehelfsverfahren', keys: ['rechtsbehelfsverfahren'], format: 'boolean' },
      { label: 'Zweifelhafter Nachweis', keys: ['zweifelhafter_flurstuecksnachweis'], format: 'boolean' },
      { label: 'Entstehung', keys: ['zeitpunkt_der_entstehung'], format: 'date', compact: true },
      landRegisterColumn(),
      { label: 'Adressen', keys: ['addresses', 'address'], value: selectionAddressLabels, html: (item) => addressChips(item), alwaysVisible: true, slot: 'address' },
      areaColumn([
        { kind: 'official', label: 'Amtliche Fläche', keys: ['amtliche_flaeche_m2'] }
      ])
    ];
    return dynamicTable(terms.parcelPlural, parcels, columns, 'parcel');
  }

  async function selectAt(lngLat, additive = false, preferredKind = null, addressHint = null) {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    const state = store.getState();
    selectTool.classList.add('is-loading');
    store.setState({ selection: { ...state.selection, loading: true } }, 'selection-loading');
    try {
      const access = await waitForAccessReady(store);
      if (controller.signal.aborted || request !== controller) return null;
      const data = await (access.pro ? api.featureAt : api.featurePreviewAt)(
        lngLat.lng,
        lngLat.lat,
        controller.signal,
        null,
        addressHint
      );
      if (controller.signal.aborted || request !== controller) return null;
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
      return hits;
    } catch (error) {
      if (error.name === 'AbortError' || request !== controller) return null;
      console.error(error);
      const current = store.getState();
      store.setState({ selection: { ...current.selection, loading: false } }, 'selection-error');
      return null;
    } finally {
      if (request === controller) {
        request = null;
        selectTool.classList.remove('is-loading');
      }
    }
  }

  async function restoreReferences(referenceSelection = {}) {
    restoreRequest?.abort();
    const controller = new AbortController();
    restoreRequest = controller;
    const normalized = normalizeSelectionReferences(referenceSelection);
    selectTool.classList.add('is-loading');
    store.setState({
      selection: { parcels: [], buildings: [], loading: normalized.references.length > 0 }
    }, 'selection-restore-loading');

    if (!normalized.references.length) {
      selectTool.classList.remove('is-loading');
      restoreRequest = null;
      return {
        applied: true,
        selection: { parcels: [], buildings: [] },
        missing: normalized.missing
      };
    }

    try {
      await waitForAccessReady(store);
      if (controller.signal.aborted || restoreRequest !== controller) {
        return { applied: false, aborted: true, selection: null, missing: [] };
      }
      const payload = await api.selectionPayload(normalized.references, controller.signal);
      if (controller.signal.aborted || restoreRequest !== controller) {
        return { applied: false, aborted: true, selection: null, missing: [] };
      }
      const restored = selectionFromPayload(payload);
      store.setState({
        selection: { ...restored.selection, loading: false }
      }, 'selection-restore');
      return {
        applied: true,
        selection: restored.selection,
        missing: [...normalized.missing, ...restored.missing]
      };
    } catch (error) {
      if (error?.name === 'AbortError' || restoreRequest !== controller) {
        return { applied: false, aborted: true, selection: null, missing: [] };
      }
      store.setState({
        selection: { parcels: [], buildings: [], loading: false }
      }, 'selection-restore-error');
      throw error;
    } finally {
      if (restoreRequest === controller) {
        restoreRequest = null;
        selectTool.classList.remove('is-loading');
      }
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
  if (typeof window !== 'undefined') window.addEventListener('resize', () => {
    welcomeOverlayRectsAt = 0;
    clearWelcomeHover();
    schedulePreviewNoticeAlignment();
    scheduleSelectionColumnAlignment();
  }, { passive: true });
  if (typeof document !== 'undefined' && document.fonts?.ready) document.fonts.ready.then(scheduleSelectionColumnAlignment);
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
  map.on('mousemove', scheduleWelcomeHover);
  map.on('dragstart', clearWelcomeHover);
  map.on('zoomstart', clearWelcomeFeature);
  map.on('zoom', () => { if (map.getZoom() <= 17) clearWelcomeFeature(); });
  map.getCanvas?.()?.addEventListener?.('mouseleave', clearWelcomeHover, { passive: true });
  map.getCanvas?.()?.addEventListener?.('pointerleave', clearWelcomeHover, { passive: true });
  if (typeof window !== 'undefined') window.addEventListener('blur', clearWelcomeHover, { passive: true });
  map.on('click', async (event) => {
    if (store.getState().activeTool === 'select') {
      await selectAt(event.lngLat, true);
    }
  });
  store.subscribe((state, reason) => {
    if (reason.startsWith('selection') || ['restore', 'access', 'access-loading'].includes(reason)) render(state);
  });
  return { selectAt, restoreReferences, flash, clear, render, clearWelcomeHover, setWelcomeMode };
}
