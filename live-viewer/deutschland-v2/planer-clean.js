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


function setSidebar(open) {
  app.dataset.sidebarOpen = open ? 'true' : 'false';
  exportTool.classList.toggle('is-active', open);
  document.getElementById('exportSidebar').hidden = false;
  requestAnimationFrame(() => map.resize());
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
}
function renderDemoSelection(point) {
  selectionRows.innerHTML = `<tr><td>Flurstück</td><td>Beispielauswahl</td><td>Demo</td><td>1</td><td>${point.lng.toFixed(4)}, ${point.lat.toFixed(4)}</td></tr>`;
  selectionCount.textContent = '1 Objekt ausgewählt';
  setTable(true);
}
exportTool.addEventListener('click', () => {
  const open = app.dataset.sidebarOpen !== 'true';
  setSidebar(open);
  setActiveTool(open ? 'export' : 'none');
});
closeExport.addEventListener('click', () => setSidebar(false));
selectTool.addEventListener('click', () => setActiveTool(selectTool.classList.contains('is-active') ? 'none' : 'select'));
measureTool.addEventListener('click', () => setActiveTool(measureTool.classList.contains('is-active') ? 'none' : 'measure'));
closeSelection.addEventListener('click', () => {
  app.dataset.tableOpen = 'false';
  selectionRows.innerHTML = '';
  selectionCount.textContent = 'Keine Objekte ausgewählt';
  window.setTimeout(() => { if (app.dataset.tableOpen !== 'true') selectionDock.hidden = true; }, 380);
});
map.on('click', (event) => {
  if (selectTool.classList.contains('is-active')) renderDemoSelection(event.lngLat);
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
map.on('zoom', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
map.on('load', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; addAlkisLayers(); updateLayerInputs(); });
