import { createApi } from './api.js?v=20260711-free-preview1';
import { createExportController } from './export.js?v=20260712-exclusive-tools1';
import { createLayerController } from './layers.js?v=20260712-brandenburg-labels1';
import { createLayout } from './layout.js?v=20260712-exclusive-tools1';
import { createPlannerMap } from './map.js?v=20260712-bkg-direct1';
import { createMeasureController } from './measure.js?v=20260712-exclusive-tools1';
import { createPersistence, readPersistedState } from './persistence.js';
import { createSearchController } from './search.js?v=20260712-exclusive-tools1';
import { createSelectionController } from './selection.js?v=20260712-exclusive-tools1';
import { createSourceController } from './sources.js?v=20260711-inline-sources1';
import { createStore } from './store.js';

const params = new URLSearchParams(window.location.search);
const surface = params.get('surface') === 'planner' ? 'planner' : 'embed';
const parentOrigin = params.get('okParentOrigin') || '*';
const saved = readPersistedState();
const mobileBoot = window.matchMedia('(max-width: 760px)').matches;
const app = document.getElementById('plannerApp');
const headerAccountLink = document.getElementById('headerAccountLink');
const map = createPlannerMap({ container: document.getElementById('map'), savedView: saved?.view });
window.__openKatasterPlannerMap = map;
window.__okMap = map;
document.body.dataset.surface = surface;

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
  'layerButton','layerMenu','layerZoomNote','searchButton','searchPanel','searchClose','searchMode','addressFields','parcelFields','placeInput','streetInput','houseInput','gemarkungInput','flurInput','parcelInput','placeSuggestions','streetSuggestions','gemarkungSuggestions','searchSubmit','searchResults','searchStatus',
  'measurePanel','measureValues','measureLocked','measureDistance','measureAngle','measureCumulative','measureArea','sourceButton','sourcePanel','sourceList',
  'exportFrame','exportPageBox','exportFrameBox','exportCenterMarker','exportOutput','exportPaper','exportOrientationField','exportOrientation','exportScale','exportLayout','exportHighlight','exportDxf','exportSummary','exportStatus','exportPreview','exportClose','mobileExportSettings','mobileExportBackdrop',
  'noticePanel','noticeClose','noticeTitle','noticeText','zoomBadge','exportProBadge'
].map((id) => [id, document.getElementById(id)]));
elements.layerInputs = [...document.querySelectorAll('[data-layer]')];

const layout = createLayout({ app, map, store, elements });
const layers = createLayerController({ map, store, elements });
const selection = createSelectionController({ map, api, store, layout, elements });
const search = createSearchController({ map, api, store, layout, elements, selection });
const measure = createMeasureController({ map, store, elements, finish: () => layout.setTool('measure') });
const exportController = createExportController({ map, api, store, elements });
const sources = createSourceController({ map, api, store, elements, layerController: layers });
createPersistence({ map, store });

elements.selectTool.addEventListener('click', () => layout.setTool('select'));
elements.measureTool.addEventListener('click', () => layout.setTool('measure'));
elements.exportTool.addEventListener('click', () => {
  const state = store.getState();
  const scopes = new Set(state.access.session?.scopes || []);
  const canExport = scopes.has('export:map') || scopes.has('export:cadastre');
  if (!canExport) {
    store.setState({
      notice: {
        title: 'OpenKataster Pro',
        text: 'Kartenexporte sind mit einem Pro- oder API-Zugang verfügbar.'
      }
    }, 'notice');
    return;
  }
  layout.setTool('export');
});
elements.exportClose.addEventListener('click', layout.closeExportPanel);
elements.mobileExportSettings.addEventListener('click', layout.toggleMobileExportSettings);
elements.mobileExportBackdrop.addEventListener('click', layout.closeMobileExportSettings);
elements.selectionClose.addEventListener('click', selection.clear);
elements.selectionResize.addEventListener('pointerdown', layout.beginTableResize);
elements.noticeClose.addEventListener('click', () => store.setState({ notice: null }, 'notice'));

store.subscribe((state, reason) => {
  elements.selectTool.setAttribute('aria-pressed', state.activeTool === 'select' ? 'true' : 'false');
  elements.measureTool.setAttribute('aria-pressed', state.activeTool === 'measure' ? 'true' : 'false');
  elements.exportTool.setAttribute('aria-pressed', state.activeTool === 'export' ? 'true' : 'false');
  document.body.dataset.access = state.access.pro ? 'pro' : 'free';
  const mapCursor = ['export', 'measure', 'select'].includes(state.activeTool) ? 'crosshair' : '';
  if (map.getCanvas().style.cursor !== mapCursor) map.getCanvas().style.cursor = mapCursor;
  if (reason === 'notice') {
    elements.noticePanel.hidden = !state.notice;
    if (state.notice) { elements.noticeTitle.textContent = state.notice.title; elements.noticeText.textContent = state.notice.text; }
  }
  const scopes = new Set(state.access.session?.scopes || []);
  const canExport = scopes.has('export:map') || scopes.has('export:cadastre');
  if (elements.exportProBadge) elements.exportProBadge.hidden = canExport;
  if (reason.startsWith('selection')) publishSelection();
});

map.on('zoom', () => { elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
function postToParent(type, payload = {}) {
  if (!window.parent || window.parent === window) return;
  window.parent.postMessage({ type, version: 1, dataset: 'deutschland', ...payload }, parentOrigin);
}

function compactSelectionItem(kind, item) {
  return {
    kind,
    state: item.state || item.bundesland || null,
    source_db: item.source_db || null,
    gml_id: item.gml_id || item.id || null,
    label: item.label || item.name || null,
    address: item.address || item.adresse || null,
    center: Array.isArray(item.center) ? item.center : null,
    bbox: Array.isArray(item.bbox) ? item.bbox : null
  };
}

function selectionState() {
  const current = store.getState().selection;
  return {
    parcels: current.parcels.map((item) => compactSelectionItem('parcel', item)),
    buildings: current.buildings.map((item) => compactSelectionItem('building', item))
  };
}

function mapState() {
  const center = map.getCenter();
  return { center: [center.lng, center.lat], zoom: map.getZoom(), bearing: 0, pitch: 0 };
}

let lastPublishedSelection = '';
function publishSelection() {
  const selectionValue = selectionState();
  const signature = JSON.stringify(selectionValue);
  if (signature === lastPublishedSelection) return;
  lastPublishedSelection = signature;
  postToParent('openkataster:selection', { selection: selectionValue });
}

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  if (parentOrigin !== '*' && event.origin !== parentOrigin) return;
  const message = event.data;
  if (!message || typeof message !== 'object') return;
  if (message.type === 'openkataster:layout-resize') {
    map.resize();
  } else if (message.type === 'openkataster:set-view') {
    const center = Array.isArray(message.center) ? message.center.map(Number) : null;
    const zoom = Number(message.zoom);
    if (center?.length === 2 && center.every(Number.isFinite)) {
      map.easeTo({ center, zoom: Number.isFinite(zoom) ? zoom : map.getZoom(), bearing: 0, pitch: 0, duration: 350 });
    }
  } else if (message.type === 'openkataster:search-address') {
    const address = message.address || {};
    elements.placeInput.value = String(address.place || '');
    elements.streetInput.value = String(address.street || '');
    elements.houseInput.value = String(address.house_number || '');
    elements.searchSubmit.click();
  } else if (message.type === 'openkataster:set-layers' && message.layers) {
    const current = store.getState();
    store.setState({ layers: { ...current.layers, ...message.layers } }, 'layers-message');
    layers.apply();
  } else if (message.type === 'openkataster:clear-selection') {
    selection.clear();
  } else if (message.type === 'openkataster:request-state') {
    postToParent('openkataster:state', { map: mapState(), selection: selectionState() });
  }
});

const mapReady = new Promise((resolve) => map.once('load', resolve));
const accessReady = api.session().then((session) => {
  const state = store.getState();
  const pro = !!(session.authenticated && ['pro', 'partner'].includes(session.access));
  const hasPreviewSelection = [...state.selection.parcels, ...state.selection.buildings]
    .some((item) => item?.preview_id && !item?.gml_id);
  store.setState({
    access: { ready: true, pro, session },
    ...((!pro || hasPreviewSelection) ? {
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
  const session = store.getState().access.session;
  postToParent('openkataster:ready', {
    capabilities: ['set-view', 'search-address', 'set-layers', 'clear-selection', 'selection'],
    access: session?.access || 'free',
    map: mapState()
  });
});
