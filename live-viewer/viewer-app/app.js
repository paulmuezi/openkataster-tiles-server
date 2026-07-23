import { createApi } from './api.js?v=20260723-austria2';
import { createExportController } from './export.js?v=20260723-austria1';
import { createLayerController } from './layers.js?v=20260723-austria2';
import { createLayout } from './layout.js?v=20260719-table-autofit1';
import { createPlannerMap } from './map.js?v=20260723-austria1';
import { createMeasureController } from './measure.js?v=20260719-free-preview-controls1';
import { createPersistence, readPersistedState } from './persistence.js?v=20260723-austria1';
import { createSearchController } from './search.js?v=20260723-austria2';
import { createSelectionController } from './selection.js?v=20260723-austria3';
import { createSourceController } from './sources.js?v=20260723-austria1';
import { createStore } from './store.js';
import {
  applyDatasetTerminology,
  datasetViewerUrl,
  datasetIdFromLocation,
  viewerDatasetProfile
} from './dataset.js?v=20260723-austria1';
import {
  WORKSPACE_VERSION,
  normalizeLayerVisibility,
  normalizeWorkspaceLayers,
  normalizeWorkspaceV1
} from './workspace.js?v=20260722-land-register-table1';

const params = new URLSearchParams(window.location.search);
const workspaceDataset = datasetIdFromLocation(window.location);
const WORKSPACE_DATASET = workspaceDataset;
const datasetProfile = viewerDatasetProfile(workspaceDataset);
const surface = params.get('surface') === 'planner' ? 'planner' : 'embed';
const preview = params.get('preview') === '1';
const onOfficeMode = params.get('onoffice') === '1';
let restoringWorkspace = false;
let shellMode = params.get('welcome') === '1' ? 'welcome' : 'planner';
const requestedParentOrigin = params.get('okParentOrigin');
let parentOrigin = window.location.origin;
if (requestedParentOrigin) {
  try {
    parentOrigin = new URL(requestedParentOrigin).origin;
  } catch (_) {
    // Invalid embed origins fall back to the current, same-origin parent contract.
  }
}
const saved = preview || onOfficeMode ? null : readPersistedState(workspaceDataset);
const mobileBoot = window.matchMedia('(max-width: 760px)').matches;
const restoreOpenLayout = !preview && !onOfficeMode && shellMode !== 'welcome' && surface !== 'planner';
const app = document.getElementById('plannerApp');
applyDatasetTerminology(datasetProfile);
app.dataset.shellTransitioning = 'false';
const headerAccountLink = document.getElementById('headerAccountLink');
const welcomeDefaultView = { lng: 9.84841, lat: 52.32984, zoom: 16.5 };
const map = createPlannerMap({
  container: document.getElementById('map'),
  savedView: saved?.view || (shellMode === 'welcome' && workspaceDataset === 'deutschland' ? welcomeDefaultView : null),
  datasetProfile
});
window.__openKatasterPlannerMap = map;
window.__okMap = map;
document.body.dataset.surface = surface;
document.body.dataset.preview = preview ? 'true' : 'false';
document.documentElement.dataset.onoffice = onOfficeMode ? 'true' : 'false';
document.body.dataset.onoffice = onOfficeMode ? 'true' : 'false';
document.documentElement.dataset.shellMode = shellMode;
document.body.dataset.shellMode = shellMode;

const defaultLayers = {
  alkis: true, buildings: true, parcelLines: true, surfaceOutlines: true, houseNumbers: true,
  streetNames: true, extended: true, parcelLabels: true, surfaces: true, buildingUsage: true,
  buildingLabels: true, boundaryPoints: true, symbols: true, aerial: false
};
const initialLayerWorkspace = normalizeWorkspaceLayers(saved?.layerWorkspace || saved?.layers || {}, {
  visible: defaultLayers
});
const store = createStore({
  activeTool: 'none',
  access: { ready: false, pro: false, session: null },
  layout: {
    sidebarOpen: restoreOpenLayout && !!saved?.layout?.sidebarOpen,
    tableOpen: restoreOpenLayout && !!saved?.layout?.tableOpen && !(mobileBoot && saved?.layout?.sidebarOpen),
    tableHeight: Number(saved?.layout?.tableHeight || 260),
    mobileExportSettings: restoreOpenLayout && !!saved?.layout?.sidebarOpen && !!saved?.layout?.mobileExportSettings
  },
  layers: initialLayerWorkspace.visible,
  layerWorkspace: initialLayerWorkspace,
  selection: { parcels: saved?.selection?.parcels || [], buildings: saved?.selection?.buildings || [], loading: false },
  export: { center: saved?.export?.center || null, bbox: saved?.export?.bbox || null },
  notice: null
});
const api = createApi({
  token: params.get('token') || '',
  fresh: params.get('fresh') || '',
  dataset: workspaceDataset,
  requestTokenRefresh: () => postToParent('openkataster:request-viewer-token')
});

const elements = Object.fromEntries([
  'exportSidebar','selectionDock','exportTool','selectTool','measureTool','selectionResize','selectionClose','selectionContent','selectionCount',
  'layerButton','layerMenu','layerZoomNote','layerPresentationNote','searchControl','searchPanel','searchModeButton','addressFields','parcelFields','addressInput','gemarkungInput','flurInput','parcelInput','searchSuggestions','gemarkungSuggestions','searchSubmit','searchResults','searchStatus',
  'measurePanel','measureValues','measureLocked','measureDistance','measureAngleLabel','measureAngle','measureLongitude','measureLatitude','measureCumulative','measureArea','osmAttribution','sourceButton','sourcePanel','sourceList',
  'exportFrame','exportPageBox','exportFrameBox','exportCenterMarker','exportOutput','exportPaper','exportOrientationField','exportOrientation','exportScale','exportLayout','exportHighlight','exportSummary','exportStatus','exportPreview','exportClose','mobileExportSettings','mobileExportBackdrop',
  'noticePanel','noticeClose','noticeTitle','noticeText','zoomBadge','exportProBadge','exportLocked'
].map((id) => [id, document.getElementById(id)]));
elements.layerInputs = [...document.querySelectorAll('[data-layer]')];
elements.datasetSwitches = [...document.querySelectorAll('[data-dataset-switch]')];
if (onOfficeMode) {
  for (const option of [...elements.exportOutput.options]) {
    if (option.value === 'dxf') option.remove();
  }
}

const layout = createLayout({ app, map, store, elements });
const layers = createLayerController({ map, store, elements, datasetProfile });
function applyStateExportCapabilities(state) {
  const dxfOption = [...elements.exportOutput.options].find((option) => option.value === 'dxf');
  if (!dxfOption) return;
  const dxfAvailable = !onOfficeMode && state?.export?.dxf !== false;
  dxfOption.hidden = !dxfAvailable;
  dxfOption.disabled = !dxfAvailable;
  if (!dxfAvailable && elements.exportOutput.value === 'dxf') {
    elements.exportOutput.value = 'pdf';
    elements.exportOutput.dispatchEvent(new Event('change', { bubbles: true }));
  }
}
const sources = createSourceController({
  map,
  api,
  store,
  elements,
  layerController: layers,
  datasetProfile,
  onStateCapabilities: applyStateExportCapabilities,
  showCompactAttribution: () => false
});
const selection = createSelectionController({
  map,
  api,
  store,
  layout,
  elements,
  datasetProfile,
  isWelcomeMode: () => shellMode === 'welcome',
  onWelcomePointer: (point) => postToParent('openkataster:welcome-pointer', { point })
});
const search = createSearchController({
  map,
  api,
  store,
  layout,
  elements,
  selection,
  datasetProfile,
  onOsmUse: sources.revealCompactAttribution
});
const measure = createMeasureController({ map, store, elements, finish: () => layout.setTool('measure') });
const exportController = createExportController({
  map,
  api,
  store,
  elements,
  datasetProfile,
  onOfficeMode,
  onWorkspaceChange: scheduleWorkspaceChanged
});
if (!preview && !onOfficeMode) createPersistence({ map, store, dataset: workspaceDataset });

function canExport(state = store.getState()) {
  return state.access.pro;
}

elements.selectTool.addEventListener('click', () => layout.setTool('select'));
elements.measureTool.addEventListener('click', () => layout.setTool('measure'));
elements.exportTool.addEventListener('click', () => {
  // Free users can inspect the export workflow, but only Pro can start an order.
  layout.setTool('export');
});
elements.exportClose.addEventListener('click', layout.closeExportSettingsOrPanel);
elements.mobileExportSettings.addEventListener('click', layout.toggleMobileExportSettings);
elements.mobileExportBackdrop.addEventListener('click', layout.closeMobileExportSettings);
elements.selectionClose.addEventListener('click', selection.clear);
elements.selectionResize.addEventListener('pointerdown', layout.beginTableResize);
elements.noticeClose.addEventListener('click', () => store.setState({ notice: null }, 'notice'));
for (const button of elements.datasetSwitches) {
  button.addEventListener('click', () => {
    const target = button.dataset.datasetSwitch;
    if (!target || target === workspaceDataset) return;
    if (window.parent && window.parent !== window) {
      postToParent('openkataster:request-dataset', { source: workspaceDataset, target });
      return;
    }
    window.location.assign(datasetViewerUrl(window.location, target));
  });
}

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
  const exportAllowed = canExport(state);
  if (elements.exportProBadge) elements.exportProBadge.hidden = exportAllowed;
  if (elements.exportPreview) elements.exportPreview.hidden = !exportAllowed;
  if (elements.exportLocked) elements.exportLocked.hidden = exportAllowed;
  if (reason.startsWith('selection') && !restoringWorkspace) publishSelection();
  if (!['access', 'access-loading', 'notice', 'selection-loading', 'selection-restore-loading'].includes(reason)) {
    scheduleWorkspaceChanged();
  }
});

map.on('zoom', () => { elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`; });
map.on('moveend', scheduleWorkspaceChanged);
function postToParent(type, payload = {}) {
  if (!window.parent || window.parent === window) return;
  window.parent.postMessage({ type, version: 1, dataset: WORKSPACE_DATASET, ...payload }, parentOrigin);
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

function workspaceSelectionState() {
  const reference = (kind, item) => ({
    kind,
    state: String(item.state || item.bundesland || ''),
    source_db: String(item.source_db || ''),
    gml_id: String(item.gml_id || item.id || '')
  });
  const current = store.getState().selection;
  return {
    parcels: current.parcels.map((item) => reference('parcel', item)).filter((item) => item.state && item.source_db && item.gml_id),
    buildings: current.buildings.map((item) => reference('building', item)).filter((item) => item.state && item.source_db && item.gml_id)
  };
}

function workspaceUiState() {
  const state = store.getState();
  return {
    active_tool: state.activeTool,
    open_panels: [
      state.layout.tableOpen ? 'selection' : null,
      state.layout.sidebarOpen ? 'export' : null
    ].filter(Boolean),
    table_height: Number(state.layout.tableHeight),
    mobile_export_settings: Boolean(state.layout.mobileExportSettings)
  };
}

function workspaceState() {
  const state = store.getState();
  const layerWorkspace = normalizeWorkspaceLayers({
    ...state.layerWorkspace,
    visible: state.layers,
    baseLayer: state.layers.aerial ? 'aerial' : 'basemap'
  }, { visible: defaultLayers });
  return {
    version: WORKSPACE_VERSION,
    map: mapState(),
    layers: layerWorkspace,
    selection: workspaceSelectionState(),
    ui: workspaceUiState(),
    export: exportController.workspaceState()
  };
}

let workspaceChangeTimer = 0;
let workspacePublishingReady = false;
let workspaceRestoreGeneration = 0;

function publishWorkspaceChanged() {
  if (!onOfficeMode || !workspacePublishingReady || restoringWorkspace) return;
  window.clearTimeout(workspaceChangeTimer);
  workspaceChangeTimer = 0;
  postToParent('openkataster:workspace-changed', { workspace: workspaceState() });
}

function scheduleWorkspaceChanged() {
  if (!onOfficeMode || !workspacePublishingReady || restoringWorkspace) return;
  window.clearTimeout(workspaceChangeTimer);
  workspaceChangeTimer = window.setTimeout(publishWorkspaceChanged, 750);
}

let mapLoaded = false;
let suspendedPlannerUi = null;
let shellTransitionVersion = 0;
let accessRefreshVersion = 0;

function isPreviewSelectionItem(item) {
  return Boolean(item?.preview_id && !item?.gml_id);
}

function compatibleSelectionForAccess(selection, pro, preserveCompatibleSelection) {
  const keep = pro
    ? (item) => !isPreviewSelectionItem(item)
    : (item) => preserveCompatibleSelection && isPreviewSelectionItem(item);
  return {
    ...selection,
    parcels: selection.parcels.filter(keep),
    buildings: selection.buildings.filter(keep)
  };
}

async function refreshAccess({ preserveCompatibleSelection = false } = {}) {
  const refreshVersion = ++accessRefreshVersion;
  const initialState = store.getState();
  const selectionAtStart = initialState.selection;
  store.setState({ access: { ...initialState.access, ready: false } }, 'access-loading');

  let session = null;
  try {
    session = await api.session();
  } catch (error) {
    if (refreshVersion !== accessRefreshVersion) return { applied: false, session: store.getState().access.session };
    console.warn('Session konnte nicht geladen werden', error);
  }
  if (refreshVersion !== accessRefreshVersion) return { applied: false, session: store.getState().access.session };

  const state = store.getState();
  const pro = !!(session?.authenticated && ['pro', 'partner'].includes(session.access));
  const selectionChangedWhileLoading = state.selection !== selectionAtStart;
  const compatibleSelection = compatibleSelectionForAccess(
    state.selection,
    pro,
    preserveCompatibleSelection || selectionChangedWhileLoading
  );
  const selectionChanged = compatibleSelection.parcels.length !== state.selection.parcels.length
    || compatibleSelection.buildings.length !== state.selection.buildings.length;
  const compatibleCount = compatibleSelection.parcels.length + compatibleSelection.buildings.length;
  store.setState({
    access: { ready: true, pro, session },
    ...(selectionChanged ? {
      selection: { ...compatibleSelection, loading: false },
      layout: { ...state.layout, tableOpen: state.layout.tableOpen && compatibleCount > 0 }
    } : {})
  }, 'access');

  if (session?.authenticated) {
    headerAccountLink.href = '/profile';
    headerAccountLink.setAttribute('aria-label', 'Profil');
  } else {
    headerAccountLink.href = '/login';
    headerAccountLink.setAttribute('aria-label', 'Anmelden');
  }
  return { applied: true, session };
}

function publishShellMode({ settleLayout = false } = {}) {
  if (!mapLoaded) return;
  if (!settleLayout && app.dataset.shellTransitioning === 'true') return;
  const transitionVersion = ++shellTransitionVersion;
  window.requestAnimationFrame(() => {
    if (transitionVersion !== shellTransitionVersion) return;
    map.resize();
    const acknowledge = () => {
      if (transitionVersion !== shellTransitionVersion) return;
      if (settleLayout) app.dataset.shellTransitioning = 'false';
      postToParent('openkataster:shell-mode', { mode: shellMode, map: mapState() });
    };
    if (settleLayout) window.requestAnimationFrame(acknowledge);
    else acknowledge();
  });
}

function setShellMode(mode) {
  if (!['welcome', 'planner'].includes(mode)) return;
  const previousMode = shellMode;
  const modeChanged = mode !== previousMode;
  const state = store.getState();

  if (mode === 'welcome') sources.closePanel();

  if (!modeChanged) {
    selection.setWelcomeMode(mode === 'welcome');
    publishShellMode();
    return;
  }

  app.dataset.shellTransitioning = 'true';

  if (mode === 'welcome' && previousMode !== 'welcome') {
    suspendedPlannerUi = { activeTool: state.activeTool, layout: { ...state.layout } };
    store.setState({
      activeTool: 'none',
      layout: {
        ...state.layout,
        sidebarOpen: false,
        tableOpen: false,
        mobileExportSettings: false
      }
    }, 'shell-mode');
  } else if (mode === 'planner' && previousMode === 'welcome' && suspendedPlannerUi) {
    const selectionCount = state.selection.parcels.length + state.selection.buildings.length;
    const restoredTool = suspendedPlannerUi.activeTool === 'export' ? 'export' : 'none';
    const restoreTable = !layout.isMobile() && suspendedPlannerUi.layout.tableOpen && selectionCount > 0;
    store.setState({
      // Pointer tools are intentionally not reactivated after the welcome page:
      // otherwise the user's first click on the visibly restored button toggles
      // the tool off again. Existing selections remain available in the table.
      activeTool: restoredTool,
      layout: {
        ...suspendedPlannerUi.layout,
        sidebarOpen: restoredTool === 'export' && suspendedPlannerUi.layout.sidebarOpen,
        tableOpen: restoreTable,
        mobileExportSettings: restoredTool === 'export' && suspendedPlannerUi.layout.mobileExportSettings
      }
    }, 'shell-mode');
    suspendedPlannerUi = null;
  }

  shellMode = mode;
  document.documentElement.dataset.shellMode = mode;
  document.body.dataset.shellMode = mode;
  sources.render();
  if (mode === 'planner') sources.revealCompactAttribution();
  selection.setWelcomeMode(mode === 'welcome');
  publishShellMode({ settleLayout: true });
}

function hasCurrentParentContract(message) {
  return message.version === WORKSPACE_VERSION && message.dataset === WORKSPACE_DATASET;
}

let lastPublishedSelection = '';
function publishSelection() {
  const selectionValue = selectionState();
  const signature = JSON.stringify(selectionValue);
  if (signature === lastPublishedSelection) return;
  lastPublishedSelection = signature;
  postToParent('openkataster:selection', { selection: selectionValue });
}

function restoredLayout(workspace) {
  const current = store.getState();
  const panels = new Set(workspace.ui.open_panels);
  let sidebarOpen = panels.has('export');
  let tableOpen = panels.has('selection') && (
    current.selection.parcels.length + current.selection.buildings.length > 0
  );
  let mobileExportSettings = sidebarOpen && workspace.ui.mobile_export_settings;
  if (layout.isMobile() && sidebarOpen && tableOpen) {
    if (workspace.ui.active_tool === 'export') tableOpen = false;
    else {
      sidebarOpen = false;
      mobileExportSettings = false;
    }
  }
  return {
    ...current.layout,
    sidebarOpen,
    tableOpen,
    tableHeight: workspace.ui.table_height,
    mobileExportSettings
  };
}

async function restoreWorkspaceMessage(message) {
  if (!onOfficeMode) return;
  const fallback = workspaceState();
  const workspace = normalizeWorkspaceV1(message.workspace, fallback);
  if (!workspace) return;
  const generation = ++workspaceRestoreGeneration;
  restoringWorkspace = true;
  window.clearTimeout(workspaceChangeTimer);
  workspaceChangeTimer = 0;
  let missing = [];
  let restoreError = null;

  try {
    await Promise.all([mapReady, accessReady]);
    if (generation !== workspaceRestoreGeneration) return;

    map.jumpTo({
      center: workspace.map.center,
      zoom: workspace.map.zoom,
      bearing: 0,
      pitch: 0
    });
    const current = store.getState();
    const layerWorkspace = normalizeWorkspaceLayers(workspace.layers, {
      ...current.layerWorkspace,
      visible: current.layers
    });
    store.setState({
      layers: layerWorkspace.visible,
      layerWorkspace
    }, 'restore');
    layers.apply();

    const referenceSelection = message.workspace?.selection === undefined
      ? workspace.selection
      : message.workspace.selection;
    try {
      const restored = await selection.restoreReferences(referenceSelection);
      if (generation !== workspaceRestoreGeneration || !restored?.applied) return;
      missing = restored.missing || [];
    } catch (error) {
      restoreError = error;
      missing = [...workspace.selection.parcels, ...workspace.selection.buildings]
        .map((reference) => ({ ...reference, reason: 'selection restore failed' }));
    }
    if (generation !== workspaceRestoreGeneration) return;

    const selected = store.getState();
    store.setState({
      activeTool: workspace.ui.active_tool,
      layout: restoredLayout(workspace),
      selection: { ...selected.selection, loading: false }
    }, 'restore');
    exportController.restoreWorkspace(workspace.export);
    selection.render();
    layers.apply();
    exportController.render();
    map.resize();
    publishSelection();

    const restoredWorkspace = workspaceState();
    postToParent('openkataster:workspace-restored', {
      request_id: typeof message.request_id === 'string' ? message.request_id.slice(0, 128) : null,
      status: restoreError ? 'error' : missing.length ? 'partial' : 'ok',
      workspace: restoredWorkspace,
      missing,
      ...(restoreError ? { error: 'selection_restore_failed' } : {})
    });
  } finally {
    if (generation === workspaceRestoreGeneration) restoringWorkspace = false;
  }
}

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  if (parentOrigin !== '*' && event.origin !== parentOrigin) return;
  const message = event.data;
  if (!message || typeof message !== 'object') return;
  if (!hasCurrentParentContract(message)) return;
  if (message.type === 'openkataster:layout-resize') {
    map.resize();
  } else if (message.type === 'openkataster:set-shell-mode') {
    setShellMode(String(message.mode || ''));
  } else if (message.type === 'openkataster:set-viewer-token') {
    if (typeof message.token !== 'string') return;
    if (!api.setToken(message.token)) return;
    void refreshAccess({ preserveCompatibleSelection: true }).then((result) => {
      if (!result.applied) return;
      selection.render();
      exportController.render();
      postToParent('openkataster:viewer-token-set', { access: result.session?.access || 'free' });
    });
  } else if (message.type === 'openkataster:set-view') {
    const center = Array.isArray(message.center) ? message.center.map(Number) : null;
    const zoom = Number(message.zoom);
    if (center?.length === 2 && center.every(Number.isFinite)) {
      map.easeTo({ center, zoom: Number.isFinite(zoom) ? zoom : map.getZoom(), bearing: 0, pitch: 0, duration: 350 });
    }
  } else if (message.type === 'openkataster:clear-welcome-hover') {
    selection.clearWelcomeHover();
  } else if (message.type === 'openkataster:search-address') {
    const address = message.address || {};
    const streetAddress = [address.street, address.house_number].map((value) => String(value || '').trim()).filter(Boolean).join(' ');
    const locality = [address.post_code || address.postcode, address.place].map((value) => String(value || '').trim()).filter(Boolean).join(' ');
    search.searchAddress(String(address.query || '').trim() || [streetAddress, locality].filter(Boolean).join(', '));
  } else if (message.type === 'openkataster:set-layers' && message.layers) {
    const current = store.getState();
    const layerWorkspace = normalizeWorkspaceLayers(message.layers, {
      ...current.layerWorkspace,
      visible: current.layers
    });
    store.setState({
      layers: normalizeLayerVisibility(layerWorkspace, current.layers),
      layerWorkspace
    }, 'layers-message');
    layers.apply();
  } else if (message.type === 'openkataster:clear-selection') {
    selection.clear();
  } else if (message.type === 'openkataster:restore-workspace') {
    void restoreWorkspaceMessage(message);
  } else if (message.type === 'openkataster:request-state') {
    const workspace = workspaceState();
    postToParent('openkataster:state', {
      workspace,
      map: workspace.map,
      selection: workspace.selection
    });
  }
});

if (onOfficeMode) window.addEventListener('pagehide', publishWorkspaceChanged);

const mapReady = new Promise((resolve) => map.once('load', resolve));
mapReady.then(() => {
  mapLoaded = true;
  elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`;
  layers.apply();
  app.dataset.ready = 'true';
  publishShellMode({ settleLayout: app.dataset.shellTransitioning === 'true' });
});
const accessReady = refreshAccess();

Promise.all([mapReady, accessReady]).then(() => {
  elements.zoomBadge.textContent = `Zoom ${map.getZoom().toFixed(2)}`;
  selection.render();
  layers.apply();
  exportController.render();
  sources.render();
  const session = store.getState().access.session;
  workspacePublishingReady = true;
  const capabilities = [
    'set-view',
    'set-shell-mode',
    'set-viewer-token',
    'search-address',
    'set-layers',
    'clear-selection',
    'selection',
    'request-state'
  ];
  if (onOfficeMode) capabilities.push('restore-workspace', 'workspace-state');
  postToParent('openkataster:ready', {
    capabilities,
    access: session?.access || 'free',
    mode: shellMode,
    map: mapState()
  });
});
