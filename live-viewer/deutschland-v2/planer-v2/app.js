import { createApi } from './api.js?v=20260711-search-highlight1';
import { createExportController } from './export.js?v=20260711-layout-stability1';
import { createLayerController } from './layers.js?v=20260711-zoom-table-tunnel1';
import { createLayout } from './layout.js?v=20260711-zoom-table-tunnel1';
import { createPlannerMap } from './map.js?v=20260711-layout-stability1';
import { createMeasureController } from './measure.js?v=20260711-layer-parity1';
import { createPersistence, readPersistedState } from './persistence.js';
import { createSearchController } from './search.js?v=20260711-search-highlight1';
import { createSelectionController } from './selection.js?v=20260711-zoom-table-tunnel1';
import { createSourceController } from './sources.js?v=20260711-layer-parity1';
import { createStore } from './store.js';

const params = new URLSearchParams(window.location.search);
const saved = readPersistedState();
const mobileBoot = window.matchMedia('(max-width: 760px)').matches;
const app = document.getElementById('plannerApp');
const headerAccountLink = document.getElementById('headerAccountLink');
const map = createPlannerMap({ container: document.getElementById('map'), savedView: saved?.view });
window.__openKatasterPlannerMap = map;

const defaultLayers = {
  alkis: true, buildings: true, parcelLines: true, surfaceOutlines: true, houseNumbers: true,
  streetNames: true, extended: true, parcelLabels: true, surfaces: true, buildingUsage: true,
  buildingLabels: true, boundaryPoints: true, symbols: true, aerial: false
};
const store = createStore({
  activeTool: 'none',
  access: { ready: false, pro: false, session: null },
  layout: {
    sidebarOpen: !!saved?.layout?.sidebarOpen,
    tableOpen: !!saved?.layout?.tableOpen && !(mobileBoot && saved?.layout?.sidebarOpen),
    tableHeight: Number(saved?.layout?.tableHeight || 260),
    mobileExportSettings: !!saved?.layout?.sidebarOpen && !!saved?.layout?.mobileExportSettings
  },
  layers: { ...defaultLayers, ...(saved?.layers || {}) },
  selection: { parcels: saved?.selection?.parcels || [], buildings: saved?.selection?.buildings || [], loading: false },
  export: { center: saved?.export?.center || null },
  notice: null
});
const api = createApi({ token: params.get('token') || '', fresh: params.get('fresh') || '' });

const elements = Object.fromEntries([
  'exportSidebar','selectionDock','exportTool','selectTool','measureTool','selectionResize','selectionClose','selectionContent','selectionCount',
  'layerButton','layerMenu','layerZoomNote','searchButton','searchPanel','searchClose','searchMode','addressFields','parcelFields','placeInput','streetInput','houseInput','gemarkungInput','flurInput','parcelInput','placeSuggestions','streetSuggestions','searchSubmit','searchResults','searchStatus',
  'measurePanel','measureDistance','measureArea','measureUndo','measureClear','sourceButton','sourcePanel','sourceList',
  'exportFrame','exportFrameBox','exportCenterMarker','exportPaper','exportOrientation','exportScale','exportPdf','exportDxf','exportAerial','exportSummary','exportStatus','exportPreview','exportClose','mobileExportSettings','mobileExportBackdrop',
  'noticePanel','noticeClose','noticeTitle','noticeText','zoomBadge'
].map((id) => [id, document.getElementById(id)]));
elements.layerInputs = [...document.querySelectorAll('[data-layer]')];

const layout = createLayout({ app, map, store, elements });
const layers = createLayerController({ map, store, elements });
const selection = createSelectionController({ map, api, store, layout, elements });
const search = createSearchController({ map, api, store, elements, selection });
const measure = createMeasureController({ map, store, elements });
const exportController = createExportController({ map, api, store, elements });
const sources = createSourceController({ map, api, store, elements, layerController: layers });
createPersistence({ map, store });

elements.selectTool.addEventListener('click', () => requirePro('select'));
elements.measureTool.addEventListener('click', () => requirePro('measure'));
elements.exportTool.addEventListener('click', () => layout.setTool('export'));
elements.exportClose.addEventListener('click', layout.closeExportPanel);
elements.mobileExportSettings.addEventListener('click', layout.toggleMobileExportSettings);
elements.mobileExportBackdrop.addEventListener('click', layout.closeMobileExportSettings);
elements.selectionClose.addEventListener('click', selection.clear);
elements.selectionResize.addEventListener('pointerdown', layout.beginTableResize);
elements.noticeClose.addEventListener('click', () => store.setState({ notice: null }, 'notice'));

function requirePro(tool) {
  if (store.getState().access.pro) layout.setTool(tool);
  else store.setState({ notice: { title: 'OpenKataster Pro', text: tool === 'measure' ? 'Messwerkzeuge sind in Pro verfügbar.' : 'Objektinformationen sind in Pro verfügbar.' } }, 'notice');
}

store.subscribe((state, reason) => {
  elements.selectTool.setAttribute('aria-pressed', state.activeTool === 'select' ? 'true' : 'false');
  elements.measureTool.setAttribute('aria-pressed', state.activeTool === 'measure' ? 'true' : 'false');
  elements.exportTool.setAttribute('aria-pressed', state.activeTool === 'export' ? 'true' : 'false');
  document.body.dataset.access = state.access.pro ? 'pro' : 'free';
  if (reason === 'notice') {
    elements.noticePanel.hidden = !state.notice;
    if (state.notice) { elements.noticeTitle.textContent = state.notice.title; elements.noticeText.textContent = state.notice.text; }
  }
});

map.on('zoom', () => { elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
window.addEventListener('message', (event) => { if (event.data?.type === 'openkataster:layout-resize') map.resize(); });

const mapReady = new Promise((resolve) => map.once('load', resolve));
const accessReady = api.session().then((session) => {
  const state = store.getState();
  const pro = !!(session.authenticated && ['pro', 'partner'].includes(session.access));
  store.setState({
    access: { ready: true, pro, session },
    ...(!pro ? {
      selection: { parcels: [], buildings: [], loading: false },
      layout: { ...state.layout, tableOpen: false }
    } : {})
  }, 'access');
  if (session.authenticated) {
    headerAccountLink.href = '/profile';
    headerAccountLink.setAttribute('aria-label', 'Profil');
  }
}).catch((error) => {
  console.warn('Session konnte nicht geladen werden', error);
  const state = store.getState();
  store.setState({
    access: { ready: true, pro: false, session: null },
    selection: { parcels: [], buildings: [], loading: false },
    layout: { ...state.layout, tableOpen: false }
  }, 'access');
});

Promise.all([mapReady, accessReady]).then(() => {
  elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`;
  selection.render();
  layers.apply();
  exportController.render();
  sources.render();
  app.dataset.ready = 'true';
  window.parent?.postMessage({ type: 'openkataster:planer-ready' }, '*');
});
