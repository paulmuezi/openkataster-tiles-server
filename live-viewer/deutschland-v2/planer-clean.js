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

const map = new maplibregl.Map({
  container: mapEl,
  style: '/viewer-assets/deutschland-v2/bkg-style.json?v=20260710-clean1',
  center: [10.45, 51.16],
  zoom: 5.25,
  minZoom: 4.2,
  maxZoom: 20,
  attributionControl: false
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-left');

const resizeObserver = new ResizeObserver(() => map.resize());
resizeObserver.observe(mapEl);

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
map.on('zoom', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
map.on('load', () => { zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
