const app = document.getElementById('plannerApp');
const mapEl = document.getElementById('map');
const exportTool = document.getElementById('exportTool');
const closeExport = document.getElementById('closeExport');
const selectTool = document.getElementById('selectTool');
const measureTool = document.getElementById('measureTool');
const selectionDock = document.getElementById('selectionDock');
const closeSelection = document.getElementById('closeSelection');
const selectionRows = document.getElementById('selectionRows');
const selectionCount = document.getElementById('selectionCount');
const zoomBadge = document.getElementById('zoomBadge');
const exportFrame = document.getElementById('exportFrame');
const exportFrameBox = document.getElementById('exportFrameBox');
const exportCenterMarker = document.getElementById('exportCenterMarker');
const exportPaper = document.getElementById('exportPaper');
const exportOrientation = document.getElementById('exportOrientation');
const exportScale = document.getElementById('exportScale');
const exportFrameSummary = document.getElementById('exportFrameSummary');
const exportBoxMinScreenSize = 18;
const urlParams = new URLSearchParams(window.location.search);
const accessToken = urlParams.get('token') || '';
const featureUrl = `/api/v1/features/point?client=viewer${accessToken ? `&token=${encodeURIComponent(accessToken)}` : ''}`;
const selectedParcels = new Map();
const selectedBuildings = new Map();
let selectionAbort = null;
let exportCenter = null;
let exportFrameDrag = null;

const layerButton = document.getElementById('layerButton');
const layerMenu = document.getElementById('layerMenu');
const layerInputs = Array.from(document.querySelectorAll('[data-layer]'));
const alkisSourceId = 'alkis-clean';
const alkisLayerGroups = {
  surfaces: ['alkis-surfaces', 'alkis-traffic-surfaces', 'alkis-surface-lines'],
  buildings: ['alkis-building-fills', 'alkis-building-lines'],
  parcels: ['alkis-parcel-lines', 'alkis-parcel-number-lines'],
  labels: ['alkis-labels']
};
const layerState = { alkis: true, surfaces: true, buildings: true, parcels: true, labels: true };


const map = new maplibregl.Map({
  container: mapEl,
  style: '/viewer-assets/deutschland-v2/bkg-style.json?v=20260710-clean1',
  center: [10.45, 51.16],
  zoom: 5.25,
  minZoom: 4.2,
  maxZoom: 20,
  hash: true,
  attributionControl: false
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-left');

const resizeObserver = new ResizeObserver(() => map.resize());
resizeObserver.observe(mapEl);


function itemKey(item) { return `${item.source_db || ''}:${item.gml_id || item.flurstueckskennzeichen || ''}`; }
function featureCollection(items) {
  return { type: 'FeatureCollection', features: items.filter(item => item.geometry).map(item => ({ type: 'Feature', properties: { id: itemKey(item) }, geometry: item.geometry })) };
}
function coordKey(point) { return `${Number(point[0]).toFixed(7)},${Number(point[1]).toFixed(7)}`; }
function segmentKey(a, b) { const ka = coordKey(a); const kb = coordKey(b); return ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`; }
function ringSegments(ring, segments) {
  let points = (ring || []).filter(point => Array.isArray(point) && point.length >= 2).map(point => [Number(point[0]), Number(point[1])]).filter(point => Number.isFinite(point[0]) && Number.isFinite(point[1]));
  if (points.length > 1 && points[0][0] === points[points.length - 1][0] && points[0][1] === points[points.length - 1][1]) points = points.slice(0, -1);
  if (points.length < 2) return;
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    if (a[0] !== b[0] || a[1] !== b[1]) segments.push([a, b]);
  }
}
function geometrySegments(geometry, segments) {
  if (!geometry) return;
  if (geometry.type === 'Polygon') (geometry.coordinates || []).forEach(ring => ringSegments(ring, segments));
  else if (geometry.type === 'MultiPolygon') for (const polygon of geometry.coordinates || []) (polygon || []).forEach(ring => ringSegments(ring, segments));
  else if (geometry.type === 'LineString') { const line = geometry.coordinates || []; for (let i = 1; i < line.length; i++) segments.push([line[i - 1], line[i]]); }
  else if (geometry.type === 'MultiLineString') for (const line of geometry.coordinates || []) for (let i = 1; i < line.length; i++) segments.push([line[i - 1], line[i]]);
}
function outlineCollection(items) {
  const edges = new Map();
  for (const item of items) {
    const segments = [];
    geometrySegments(item.geometry, segments);
    for (const [a, b] of segments) {
      const key = segmentKey(a, b);
      if (edges.has(key)) edges.delete(key); else edges.set(key, [a, b]);
    }
  }
  return { type: 'FeatureCollection', features: [...edges.values()].map((line, index) => ({ type: 'Feature', properties: { id: String(index) }, geometry: { type: 'LineString', coordinates: line } })) };
}
function addSelectionLayers() {
  if (map.getSource('selected-parcels')) return;
  map.addSource('selected-parcels', { type: 'geojson', data: featureCollection([]) });
  map.addSource('selected-buildings', { type: 'geojson', data: featureCollection([]) });
  map.addSource('selected-parcel-outlines', { type: 'geojson', data: outlineCollection([]) });
  map.addSource('selected-building-outlines', { type: 'geojson', data: outlineCollection([]) });
  map.addLayer({ id: 'selected-parcel-fill', type: 'fill', source: 'selected-parcels', paint: { 'fill-color': '#ef4444', 'fill-opacity': 0 } });
  map.addLayer({ id: 'selected-parcel-halo', type: 'line', source: 'selected-parcel-outlines', layout: { 'line-cap': 'butt', 'line-join': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': 6.8, 'line-opacity': .92, 'line-dasharray': [0.92, 0.49] } });
  map.addLayer({ id: 'selected-parcel-outline', type: 'line', source: 'selected-parcel-outlines', layout: { 'line-cap': 'butt', 'line-join': 'round' }, paint: { 'line-color': '#ef4444', 'line-width': 2.4, 'line-opacity': 1, 'line-dasharray': [2.6, 1.4] } });
  map.addLayer({ id: 'selected-building-fill', type: 'fill', source: 'selected-buildings', paint: { 'fill-color': '#ef4444', 'fill-opacity': 0 } });
  map.addLayer({ id: 'selected-building-halo', type: 'line', source: 'selected-building-outlines', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': 6, 'line-opacity': .92 } });
  map.addLayer({ id: 'selected-building-outline', type: 'line', source: 'selected-building-outlines', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#ef4444', 'line-width': 2.8, 'line-opacity': 1 } });
}
function updateSelectionSources() {
  const parcels = [...selectedParcels.values()];
  const buildings = [...selectedBuildings.values()];
  map.getSource('selected-parcels')?.setData(featureCollection(parcels));
  map.getSource('selected-buildings')?.setData(featureCollection(buildings));
  map.getSource('selected-parcel-outlines')?.setData(outlineCollection(parcels));
  map.getSource('selected-building-outlines')?.setData(outlineCollection(buildings));
}
function addressLabel(item) {
  if (item.address) return item.address;
  const first = Array.isArray(item.addresses) ? item.addresses[0] : null;
  return first?.label || [first?.street, first?.house_number].filter(Boolean).join(' ') || '–';
}
function formatArea(value) {
  if (value === null || value === undefined || value === '') return '–';
  const number = Number(value);
  if (!Number.isFinite(number)) return '–';
  return `${Math.round(number).toLocaleString('de-DE')} m²`;
}
function renderSelectionTable() {
  const rows = [];
  for (const building of selectedBuildings.values()) {
    rows.push(`<tr><td>Gebäude</td><td>${escapeHtml(addressLabel(building))}</td><td class="muted">–</td><td class="muted">–</td><td class="muted">–</td></tr>`);
  }
  for (const parcel of selectedParcels.values()) {
    rows.push(`<tr><td>Flurstück</td><td>${escapeHtml(addressLabel(parcel))}</td><td>${escapeHtml(parcel.gemarkung || '–')}</td><td>${escapeHtml(parcel.flur ?? '–')}</td><td><strong>${escapeHtml(parcel.flurstueck || [parcel.zaehler, parcel.nenner].filter(Boolean).join('/') || '–')}</strong><br><span class="muted">${formatArea(parcel.amtliche_flaeche_m2)}</span></td></tr>`);
  }
  selectionRows.innerHTML = rows.join('');
  const count = selectedBuildings.size + selectedParcels.size;
  selectionCount.textContent = count === 1 ? '1 Objekt ausgewählt' : `${count} Objekte ausgewählt`;
  setTable(count > 0);
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));
}
async function selectAt(lngLat) {
  if (selectionAbort) selectionAbort.abort();
  selectionAbort = new AbortController();
  selectTool.classList.add('is-loading');
  const url = `${featureUrl}&lon=${encodeURIComponent(lngLat.lng)}&lat=${encodeURIComponent(lngLat.lat)}`;
  try {
    const response = await fetch(url, { signal: selectionAbort.signal });
    if (!response.ok) throw new Error(`Feature-API ${response.status}`);
    const data = await response.json();
    selectedParcels.clear();
    selectedBuildings.clear();
    for (const parcel of data.parcels || []) selectedParcels.set(itemKey(parcel), parcel);
    for (const building of data.buildings || []) selectedBuildings.set(itemKey(building), building);
    updateSelectionSources();
    renderSelectionTable();
  } catch (error) {
    if (error.name !== 'AbortError') console.error(error);
  } finally {
    selectTool.classList.remove('is-loading');
  }
}
function clearSelection() {
  selectedParcels.clear();
  selectedBuildings.clear();
  updateSelectionSources();
  selectionRows.innerHTML = '';
  selectionCount.textContent = 'Keine Objekte ausgewählt';
  app.dataset.tableOpen = 'false';
  window.setTimeout(() => { if (app.dataset.tableOpen !== 'true') selectionDock.hidden = true; }, 380);
}

function addAlkisLayers() {
  if (map.getSource(alkisSourceId)) return;
  map.addSource(alkisSourceId, { type: 'vector', url: '/api/v1/tilejson/deutschland.json?v=20260710-clean2' });
  const minzoom = 16.7;
  map.addLayer({ id: 'alkis-surfaces', type: 'fill', source: alkisSourceId, 'source-layer': 'surfaces', minzoom,
    filter: ['all', ['!=', ['get', 'thema'], 'Verkehr']],
    paint: { 'fill-color': ['coalesce', ['get', 'fill_color'], '#f7e9ef'], 'fill-opacity': 1 }});
  map.addLayer({ id: 'alkis-traffic-surfaces', type: 'fill', source: alkisSourceId, 'source-layer': 'surfaces', minzoom,
    filter: ['==', ['get', 'thema'], 'Verkehr'],
    paint: { 'fill-color': '#ffffff', 'fill-opacity': 1 }});
  map.addLayer({ id: 'alkis-building-fills', type: 'fill', source: alkisSourceId, 'source-layer': 'building_fills', minzoom,
    paint: { 'fill-color': ['coalesce', ['get', 'fill_color'], '#a8a8a8'], 'fill-opacity': 1 }});
  map.addLayer({ id: 'alkis-surface-lines', type: 'line', source: alkisSourceId, 'source-layer': 'lines', minzoom,
    paint: { 'line-color': ['coalesce', ['get', 'stroke_color'], '#989898'], 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, 1.2], 'line-opacity': .65 }});
  map.addLayer({ id: 'alkis-parcel-lines', type: 'line', source: alkisSourceId, 'source-layer': 'parcel_outline_lines', minzoom,
    paint: { 'line-color': '#36383c', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .55, 20, 1.25], 'line-opacity': .82 }});
  map.addLayer({ id: 'alkis-building-lines', type: 'line', source: alkisSourceId, 'source-layer': 'building_lines', minzoom,
    paint: { 'line-color': '#202124', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .55, 20, 1.45], 'line-opacity': .95 }});
  map.addLayer({ id: 'alkis-parcel-number-lines', type: 'line', source: alkisSourceId, 'source-layer': 'parcel_number_lines', minzoom,
    paint: { 'line-color': '#1f2933', 'line-width': ['interpolate', ['linear'], ['zoom'], 17, .35, 20, .85], 'line-opacity': .78 }});
  map.addLayer({ id: 'alkis-labels', type: 'symbol', source: alkisSourceId, 'source-layer': 'labels', minzoom,
    layout: {
      'text-field': ['coalesce', ['get', 'text_content'], ''],
      'text-font': ['Noto Sans Regular'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 17, 9, 19, 12, 20, 15],
      'text-rotation-alignment': 'map',
      'text-rotate': ['*', -1, ['coalesce', ['to-number', ['get', 'render_rotation']], 0]],
      'text-allow-overlap': true,
      'text-ignore-placement': true
    },
    paint: { 'text-color': ['coalesce', ['get', 'font_color'], '#252a32'], 'text-halo-color': '#fff', 'text-halo-width': 1.1, 'text-opacity': 1 }});
  applyLayerState();
}
function applyLayerState() {
  const alkisOn = !!layerState.alkis;
  for (const [group, ids] of Object.entries(alkisLayerGroups)) {
    const visible = alkisOn && !!layerState[group] ? 'visible' : 'none';
    for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible);
  }
}
function updateLayerInputs() {
  for (const input of layerInputs) {
    input.checked = !!layerState[input.dataset.layer];
    input.disabled = input.dataset.layer !== 'alkis' && !layerState.alkis;
  }
}



function paperSizeMeters() {
  const scale = Number(exportScale.value || 1000);
  if (exportPaper.value === 'square') return { width: 180 * scale / 1000, height: 180 * scale / 1000 };
  const longMm = 297;
  const shortMm = 210;
  const landscape = exportOrientation.value !== 'portrait';
  return {
    width: (landscape ? longMm : shortMm) * scale / 1000,
    height: (landscape ? shortMm : longMm) * scale / 1000
  };
}
function metersToLngLatDelta(center, meters) {
  const latRad = center.lat * Math.PI / 180;
  const metersPerDegLat = 111320;
  const metersPerDegLng = Math.max(1, 111320 * Math.cos(latRad));
  return { lng: meters.x / metersPerDegLng, lat: meters.y / metersPerDegLat };
}
function exportFrameBounds() {
  const center = exportCenter || map.getCenter();
  const size = paperSizeMeters();
  const half = metersToLngLatDelta(center, { x: size.width / 2, y: size.height / 2 });
  return {
    west: center.lng - half.lng,
    east: center.lng + half.lng,
    south: center.lat - half.lat,
    north: center.lat + half.lat,
    center,
    size
  };
}
function setExportCenter(lngLat) {
  exportCenter = { lng: Number(lngLat.lng), lat: Number(lngLat.lat) };
  updateExportFrame();
}
function updateExportFrame() {
  if (!exportCenter || app.dataset.sidebarOpen !== 'true') {
    exportFrame.hidden = true;
    exportFrameSummary.textContent = 'Ausschnitt noch nicht gesetzt';
    return;
  }
  const bounds = exportFrameBounds();
  const nw = map.project([bounds.west, bounds.north]);
  const se = map.project([bounds.east, bounds.south]);
  const centerPx = map.project([bounds.center.lng, bounds.center.lat]);
  const left = Math.min(nw.x, se.x);
  const top = Math.min(nw.y, se.y);
  const width = Math.abs(se.x - nw.x);
  const height = Math.abs(se.y - nw.y);
  exportFrame.hidden = false;
  Object.assign(exportCenterMarker.style, { left: `${centerPx.x}px`, top: `${centerPx.y}px` });
  if (width < exportBoxMinScreenSize || height < exportBoxMinScreenSize) {
    exportFrameBox.hidden = true;
  } else {
    exportFrameBox.hidden = false;
    Object.assign(exportFrameBox.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
  }
  exportFrameSummary.textContent = `Mitte: ${bounds.center.lat.toFixed(6)}, ${bounds.center.lng.toFixed(6)} · ${Math.round(bounds.size.width)} × ${Math.round(bounds.size.height)} m`;
}
function beginExportFrameDrag(event) {
  if (!exportCenter) return;
  event.preventDefault();
  event.stopPropagation();
  exportFrameBox.classList.add('is-dragging');
  const startPoint = map.project([exportCenter.lng, exportCenter.lat]);
  exportFrameDrag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, startPoint, moved: false };
  exportFrameBox.setPointerCapture(event.pointerId);
}
function moveExportFrameDrag(event) {
  if (!exportFrameDrag) return;
  event.preventDefault();
  const dx = event.clientX - exportFrameDrag.startX;
  const dy = event.clientY - exportFrameDrag.startY;
  if (Math.hypot(dx, dy) > 3) exportFrameDrag.moved = true;
  const point = { x: exportFrameDrag.startPoint.x + dx, y: exportFrameDrag.startPoint.y + dy };
  const lngLat = map.unproject(point);
  setExportCenter(lngLat);
}
function endExportFrameDrag(event) {
  if (!exportFrameDrag) return;
  exportFrameBox.classList.remove('is-dragging');
  try { exportFrameBox.releasePointerCapture(exportFrameDrag.pointerId); } catch (_) {}
  if (!exportFrameDrag.moved) {
    const rect = mapEl.getBoundingClientRect();
    setExportCenter(map.unproject({ x: event.clientX - rect.left, y: event.clientY - rect.top }));
  }
  exportFrameDrag = null;
}

function setSidebar(open) {
  app.dataset.sidebarOpen = open ? 'true' : 'false';
  exportTool.classList.toggle('is-active', open);
  document.getElementById('exportSidebar').hidden = false;
  if (open && !exportCenter) setExportCenter(map.getCenter());
  if (!open) exportFrame.hidden = true;
  requestAnimationFrame(() => { map.resize(); updateExportFrame(); });
}
function setTable(open) {
  app.dataset.tableOpen = open ? 'true' : 'false';
  selectionDock.hidden = false;
  requestAnimationFrame(() => map.resize());
}
function setActiveTool(tool) {
  for (const el of [selectTool, measureTool, exportTool]) el.classList.remove('is-active');
  if (tool === 'select') selectTool.classList.add('is-active');
  if (tool === 'measure') measureTool.classList.add('is-active');
  if (tool === 'export') exportTool.classList.add('is-active');
  if (exportFrameBox) exportFrameBox.style.pointerEvents = (tool === 'select' || tool === 'measure') ? 'none' : 'auto';
}
exportTool.addEventListener('click', () => {
  const open = app.dataset.sidebarOpen !== 'true';
  setSidebar(open);
  setActiveTool(open ? 'export' : 'none');
});
closeExport.addEventListener('click', () => setSidebar(false));
selectTool.addEventListener('click', () => setActiveTool(selectTool.classList.contains('is-active') ? 'none' : 'select'));
measureTool.addEventListener('click', () => setActiveTool(measureTool.classList.contains('is-active') ? 'none' : 'measure'));
closeSelection.addEventListener('click', clearSelection);
for (const control of [exportPaper, exportOrientation, exportScale]) control.addEventListener('change', updateExportFrame);
exportFrameBox.addEventListener('pointerdown', beginExportFrameDrag);
exportFrameBox.addEventListener('pointermove', moveExportFrameDrag);
exportFrameBox.addEventListener('pointerup', endExportFrameDrag);
exportFrameBox.addEventListener('pointercancel', endExportFrameDrag);
map.on('click', (event) => {
  if (selectTool.classList.contains('is-active')) {
    selectAt(event.lngLat);
    return;
  }
  if (measureTool.classList.contains('is-active')) return;
  if (app.dataset.sidebarOpen === 'true') setExportCenter(event.lngLat);
});
layerButton.addEventListener('click', () => {
  const open = layerMenu.hidden;
  layerMenu.hidden = !open;
  layerButton.setAttribute('aria-expanded', open ? 'true' : 'false');
});
for (const input of layerInputs) {
  input.addEventListener('change', () => {
    layerState[input.dataset.layer] = input.checked;
    if (input.dataset.layer === 'alkis' && input.checked) {
      layerState.surfaces = true;
      layerState.buildings = true;
      layerState.parcels = true;
      layerState.labels = true;
    }
    updateLayerInputs();
    applyLayerState();
  });
}
document.addEventListener('click', (event) => {
  if (!event.target.closest('.layer-control')) {
    layerMenu.hidden = true;
    layerButton.setAttribute('aria-expanded', 'false');
  }
});
map.on('zoom', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; updateExportFrame(); });
map.on('move', updateExportFrame);
map.on('resize', updateExportFrame);
map.on('load', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; addAlkisLayers(); addSelectionLayers(); updateLayerInputs(); });
