import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createLayout } from '../live-viewer/viewer-app/layout.js';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');

function fakeElement(height = 0) {
  const attributes = new Map();
  return {
    attributes,
    children: [],
    clientHeight: height,
    offsetHeight: height,
    scrollHeight: height,
    scrollTop: 0,
    classList: { toggle() {} },
    style: { setProperty() {} },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() {
      return { top: 0, bottom: height, height };
    },
    querySelector() {
      return null;
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    }
  };
}

function createHarness({ mobile, settingsOpen = true }) {
  let state = {
    activeTool: 'export',
    access: { pro: true },
    layout: {
      sidebarOpen: true,
      tableOpen: false,
      tableHeight: 260,
      mobileExportSettings: settingsOpen
    },
    selection: { parcels: [], buildings: [] }
  };
  let subscriber = () => {};
  const app = fakeElement(800);
  app.dataset = {};
  const exportClose = fakeElement();
  const elements = {
    exportSidebar: fakeElement(118),
    selectionDock: fakeElement(200),
    selectionResize: fakeElement(28),
    selectionContent: fakeElement(100),
    exportTool: fakeElement(),
    selectTool: fakeElement(),
    measureTool: fakeElement(),
    exportClose,
    mobileExportSettings: fakeElement()
  };
  const store = {
    getState() {
      return state;
    },
    setState(patch, reason) {
      state = { ...state, ...patch };
      subscriber(state, reason);
    },
    subscribe(callback) {
      subscriber = callback;
    }
  };

  globalThis.window = {
    matchMedia() {
      return { matches: mobile };
    },
    getComputedStyle() {
      return { paddingBottom: '0', paddingTop: '0' };
    },
    clearTimeout() {},
    setTimeout() {
      return 1;
    },
    requestAnimationFrame() {}
  };

  const layout = createLayout({
    app,
    map: { resize() {} },
    store,
    elements
  });

  return {
    layout,
    exportClose,
    getState() {
      return state;
    }
  };
}

const mobileHarness = createHarness({ mobile: true });
mobileHarness.layout.closeExportSettingsOrPanel();
assert.deepEqual(
  mobileHarness.getState(),
  {
    activeTool: 'export',
    access: { pro: true },
    layout: {
      sidebarOpen: true,
      tableOpen: false,
      tableHeight: 260,
      mobileExportSettings: false
    },
    selection: { parcels: [], buildings: [] }
  },
  'Das mobile X darf nur die Exporteinstellungen schließen und muss das Export-Tool aktiv lassen.'
);
assert.equal(
  mobileHarness.exportClose.attributes.get('aria-label'),
  'Exporteinstellungen schließen',
  'Das mobile X muss seine tatsächliche Aktion ankündigen.'
);

mobileHarness.layout.closeExportSettingsOrPanel();
assert.equal(mobileHarness.getState().activeTool, 'export', 'Ein weiteres mobiles X-Ereignis darf das Export-Tool nicht schließen.');
assert.equal(mobileHarness.getState().layout.sidebarOpen, true);

mobileHarness.layout.setTool('export');
assert.equal(mobileHarness.getState().activeTool, 'none', 'Erst ein erneuter Klick auf den Export-Button schließt das mobile Tool.');
assert.equal(mobileHarness.getState().layout.sidebarOpen, false);

const toolSwitchHarness = createHarness({ mobile: true });
toolSwitchHarness.layout.closeExportSettingsOrPanel();
toolSwitchHarness.layout.setTool('measure');
assert.equal(toolSwitchHarness.getState().activeTool, 'measure', 'Ein anderes Tool muss den Export ersetzen.');
assert.equal(toolSwitchHarness.getState().layout.sidebarOpen, false);

const desktopHarness = createHarness({ mobile: false });
desktopHarness.layout.closeExportSettingsOrPanel();
assert.equal(desktopHarness.getState().activeTool, 'none', 'Desktop-X muss weiterhin das Exportpanel schließen.');
assert.equal(desktopHarness.getState().layout.sidebarOpen, false);
assert.equal(desktopHarness.exportClose.attributes.get('aria-label'), 'Export schließen');

assert.match(
  appSource,
  /elements\.exportClose\.addEventListener\('click', layout\.closeExportSettingsOrPanel\);/,
  'Der sichtbare X-Button muss an die responsive Schließaktion gebunden sein.'
);

console.log('mobile-export-settings-close-tests=ok');
