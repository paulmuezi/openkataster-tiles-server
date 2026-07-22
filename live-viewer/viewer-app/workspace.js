export const WORKSPACE_VERSION = 1;
export const WORKSPACE_DATASET = 'deutschland';

export const WORKSPACE_LAYER_KEYS = Object.freeze([
  'alkis',
  'buildings',
  'parcelLines',
  'surfaceOutlines',
  'houseNumbers',
  'streetNames',
  'extended',
  'parcelLabels',
  'surfaces',
  'buildingUsage',
  'buildingLabels',
  'boundaryPoints',
  'symbols',
  'aerial'
]);

const WORKSPACE_TOOLS = new Set(['none', 'select', 'measure', 'export']);
const WORKSPACE_PANELS = new Set(['selection', 'export']);
const WORKSPACE_OUTPUTS = new Set(['pdf', 'png']);
const WORKSPACE_FORMATS = new Set(['a4', 'a3', 'square', 'ratio43']);
const WORKSPACE_ORIENTATIONS = new Set(['portrait', 'landscape']);
const WORKSPACE_SCALES = new Set([500, 1000, 2000]);

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function clampedNumber(value, fallback, minimum, maximum) {
  const number = finiteNumber(value);
  return number === null ? fallback : Math.min(maximum, Math.max(minimum, number));
}

function normalizedCenter(value, fallback = null) {
  const coordinates = Array.isArray(value)
    ? { lng: finiteNumber(value[0]), lat: finiteNumber(value[1]) }
    : { lng: finiteNumber(value?.lng ?? value?.lon), lat: finiteNumber(value?.lat) };
  if (
    coordinates.lng === null
    || coordinates.lat === null
    || coordinates.lng < -180
    || coordinates.lng > 180
    || coordinates.lat < -90
    || coordinates.lat > 90
  ) {
    return fallback;
  }
  return coordinates;
}

function normalizedBounds(value, fallback = null) {
  const source = value && typeof value === 'object' ? value : fallback;
  const west = finiteNumber(source?.west);
  const south = finiteNumber(source?.south);
  const east = finiteNumber(source?.east);
  const north = finiteNumber(source?.north);
  if (
    west === null
    || south === null
    || east === null
    || north === null
    || west < -180
    || east > 180
    || south < -90
    || north > 90
    || west >= east
    || south >= north
  ) {
    return null;
  }
  return { west, south, east, north };
}

function referenceKey(reference) {
  return [reference.state, reference.kind, reference.source_db, reference.gml_id].join(':');
}

function compactReference(raw, fallbackKind = '') {
  const kind = fallbackKind || String(raw?.kind || '').trim().toLocaleLowerCase('en');
  return {
    state: String(raw?.state || raw?.bundesland || '').trim(),
    kind,
    source_db: String(raw?.source_db || '').trim(),
    gml_id: String(raw?.gml_id || raw?.id || '').trim()
  };
}

export function normalizeSelectionReferences(selection = {}, { maximum = 50 } = {}) {
  const buckets = [
    ['parcel', Array.isArray(selection?.parcels) ? selection.parcels : []],
    ['building', Array.isArray(selection?.buildings) ? selection.buildings : []]
  ];
  const references = [];
  const missing = [];
  const seen = new Set();

  for (const [kind, items] of buckets) {
    for (const raw of items) {
      const reference = compactReference(raw, kind);
      if (!reference.state || !reference.source_db || !reference.gml_id) {
        missing.push({ ...reference, reason: 'incomplete reference' });
        continue;
      }
      const key = referenceKey(reference);
      if (seen.has(key)) continue;
      seen.add(key);
      if (references.length >= maximum) {
        missing.push({ ...reference, reason: 'selection is too large' });
        continue;
      }
      references.push(reference);
    }
  }

  return { references, missing };
}

function normalizedRestoredFeature(raw) {
  const properties = raw?.properties && typeof raw.properties === 'object' && !Array.isArray(raw.properties)
    ? raw.properties
    : {};
  const reference = compactReference({ ...properties, ...raw });
  if (!reference.state || !reference.source_db || !reference.gml_id || !['parcel', 'building'].includes(reference.kind)) {
    return null;
  }
  return {
    ...properties,
    state: reference.state,
    kind: reference.kind,
    source_db: reference.source_db,
    gml_id: reference.gml_id,
    label: raw.label ?? properties.label ?? null,
    subtitle: raw.subtitle ?? properties.subtitle ?? null,
    center: Array.isArray(raw.center) ? raw.center : properties.center,
    bbox: Array.isArray(raw.bbox) ? raw.bbox : properties.bbox
  };
}

export function selectionFromPayload(payload = {}) {
  const selection = { parcels: [], buildings: [] };
  const missing = Array.isArray(payload?.missing)
    ? payload.missing.map((item) => ({ ...compactReference(item), reason: String(item?.reason || 'feature not found') }))
    : [];
  const seen = new Set();

  for (const raw of Array.isArray(payload?.features) ? payload.features : []) {
    const feature = normalizedRestoredFeature(raw);
    if (!feature) {
      missing.push({ ...compactReference(raw), reason: 'invalid feature response' });
      continue;
    }
    const key = referenceKey(feature);
    if (seen.has(key)) continue;
    seen.add(key);
    if (feature.kind === 'parcel') selection.parcels.push(feature);
    else selection.buildings.push(feature);
  }

  return { selection, missing };
}

export function normalizeWorkspaceMap(value = {}, fallback = {}) {
  const fallbackCenter = normalizedCenter(fallback.center, { lng: 9.84841, lat: 52.32984 });
  const center = normalizedCenter(value?.center, fallbackCenter);
  return {
    center: [center.lng, center.lat],
    zoom: clampedNumber(value?.zoom, clampedNumber(fallback?.zoom, 16.5, 0, 24), 0, 24),
    bearing: 0,
    pitch: 0
  };
}

export function normalizeLayerVisibility(value = {}, fallback = {}) {
  const source = value?.visible && typeof value.visible === 'object' ? value.visible : value;
  const fallbackSource = fallback?.visible && typeof fallback.visible === 'object' ? fallback.visible : fallback;
  return Object.fromEntries(WORKSPACE_LAYER_KEYS.map((key) => [
    key,
    typeof source?.[key] === 'boolean' ? source[key] : Boolean(fallbackSource?.[key])
  ]));
}

export function normalizeWorkspaceLayers(value = {}, fallback = {}) {
  const visible = normalizeLayerVisibility(value, fallback);
  const visibilitySource = value?.visible && typeof value.visible === 'object' ? value.visible : value;
  const rawOrder = Array.isArray(value?.order)
    ? value.order
    : Array.isArray(fallback?.order) ? fallback.order : WORKSPACE_LAYER_KEYS;
  const order = [...new Set(rawOrder.filter((key) => WORKSPACE_LAYER_KEYS.includes(key)))];
  for (const key of WORKSPACE_LAYER_KEYS) if (!order.includes(key)) order.push(key);

  const rawOpacity = value?.opacity && typeof value.opacity === 'object' ? value.opacity : {};
  const fallbackOpacity = fallback?.opacity && typeof fallback.opacity === 'object' ? fallback.opacity : {};
  const opacity = Object.fromEntries(WORKSPACE_LAYER_KEYS.map((key) => [
    key,
    clampedNumber(rawOpacity[key], clampedNumber(fallbackOpacity[key], 1, 0, 1), 0, 1)
  ]));
  const requestedBaseLayer = String(value?.baseLayer || value?.base_layer || '').trim();
  const fallbackBaseLayer = String(fallback?.baseLayer || fallback?.base_layer || '').trim();
  const baseLayer = ['basemap', 'aerial'].includes(requestedBaseLayer)
    ? requestedBaseLayer
    : typeof visibilitySource?.aerial === 'boolean'
      ? visible.aerial ? 'aerial' : 'basemap'
    : ['basemap', 'aerial'].includes(fallbackBaseLayer)
      ? fallbackBaseLayer
      : visible.aerial ? 'aerial' : 'basemap';
  visible.aerial = baseLayer === 'aerial';

  return { visible, order, opacity, baseLayer };
}

export function normalizeWorkspaceUi(value = {}, fallback = {}) {
  const activeTool = WORKSPACE_TOOLS.has(value?.active_tool)
    ? value.active_tool
    : WORKSPACE_TOOLS.has(fallback?.active_tool) ? fallback.active_tool : 'none';
  const rawPanels = Array.isArray(value?.open_panels)
    ? value.open_panels
    : Array.isArray(fallback?.open_panels) ? fallback.open_panels : [];
  const openPanels = [...new Set(rawPanels.filter((panel) => WORKSPACE_PANELS.has(panel)))];
  return {
    active_tool: activeTool,
    open_panels: openPanels,
    table_height: clampedNumber(
      value?.table_height,
      clampedNumber(fallback?.table_height, 260, 150, 1200),
      150,
      1200
    ),
    mobile_export_settings: typeof value?.mobile_export_settings === 'boolean'
      ? value.mobile_export_settings
      : Boolean(fallback?.mobile_export_settings)
  };
}

export function normalizeWorkspaceExport(value = {}, fallback = {}) {
  const output = WORKSPACE_OUTPUTS.has(value?.output)
    ? value.output
    : WORKSPACE_OUTPUTS.has(fallback?.output) ? fallback.output : 'pdf';
  const requestedFormat = WORKSPACE_FORMATS.has(value?.format)
    ? value.format
    : WORKSPACE_FORMATS.has(fallback?.format) ? fallback.format : 'a4';
  const format = output === 'pdf' && !['a4', 'a3'].includes(requestedFormat) ? 'a4' : requestedFormat;
  const orientation = WORKSPACE_ORIENTATIONS.has(value?.orientation)
    ? value.orientation
    : WORKSPACE_ORIENTATIONS.has(fallback?.orientation) ? fallback.orientation : 'portrait';
  const requestedScale = finiteNumber(value?.scale);
  const fallbackScale = finiteNumber(fallback?.scale);
  const center = normalizedCenter(value?.center, normalizedCenter(fallback?.center, null));
  const bbox = normalizedBounds(value?.bbox, normalizedBounds(fallback?.bbox));
  const requestedLayout = typeof value?.layout === 'boolean' ? value.layout : fallback?.layout !== false;
  return {
    output,
    format,
    orientation,
    scale: WORKSPACE_SCALES.has(requestedScale)
      ? requestedScale
      : WORKSPACE_SCALES.has(fallbackScale) ? fallbackScale : 1000,
    layout: requestedLayout && ['a4', 'a3'].includes(format),
    highlight_selection: typeof value?.highlight_selection === 'boolean'
      ? value.highlight_selection
      : fallback?.highlight_selection !== false,
    center,
    bbox
  };
}

export function normalizeWorkspaceV1(value, fallback = {}) {
  if (!value || value.version !== WORKSPACE_VERSION) return null;
  const normalizedReferences = normalizeSelectionReferences(value.selection);
  return {
    version: WORKSPACE_VERSION,
    map: normalizeWorkspaceMap(value.map, fallback.map),
    layers: normalizeWorkspaceLayers(value.layers, fallback.layers),
    selection: {
      parcels: normalizedReferences.references.filter((reference) => reference.kind === 'parcel'),
      buildings: normalizedReferences.references.filter((reference) => reference.kind === 'building')
    },
    ui: normalizeWorkspaceUi(value.ui, fallback.ui),
    export: normalizeWorkspaceExport(value.export, fallback.export)
  };
}
