import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createExportController,
  exportFrameFitPadding,
  fitMapToExportFrame
} from '../live-viewer/viewer-app/export.js';
import { createStore } from '../live-viewer/viewer-app/store.js';

class FakeControl {
  constructor(properties = {}) {
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.style = {};
    this.options = [];
    this.label = { hidden: false };
    this.classList = { add() {}, remove() {} };
    Object.assign(this, properties);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) listener({ type, ...event });
  }

  closest(selector) {
    return selector === 'label' ? this.label : null;
  }

  setPointerCapture() {}
  releasePointerCapture() {}
}

function rect({ left = 0, top = 0, width = 0, height = 0 }) {
  return { left, top, width, height, right: left + width, bottom: top + height };
}

const searchPill = { hidden: false, getBoundingClientRect: () => rect({ left: 13, top: 13, width: 410, height: 48 }) };
const layerButton = { hidden: false, getBoundingClientRect: () => rect({ left: 899, top: 13, width: 48, height: 48 }) };
const toolStack = { hidden: false, getBoundingClientRect: () => rect({ left: 13, top: 70, width: 48, height: 158 }) };
const mapContainer = {
  clientWidth: 960,
  clientHeight: 744,
  getBoundingClientRect: () => rect({ width: 960, height: 744 }),
  parentElement: {
    querySelector(selector) {
      if (selector === '.search-pill-row') return searchPill;
      if (selector === '#layerButton') return layerButton;
      if (selector === '.tool-stack') return toolStack;
      return null;
    }
  },
  closest() {
    return { dataset: { layoutTransitioning: 'false' } };
  }
};

const mapHandlers = new Map();
const fitCalls = [];
let mapCenter = { lng: 9.84841, lat: 52.32984 };
const map = {
  on(type, listener) {
    const listeners = mapHandlers.get(type) || [];
    listeners.push(listener);
    mapHandlers.set(type, listeners);
  },
  emit(type, event = {}) {
    for (const listener of mapHandlers.get(type) || []) listener(event);
  },
  getCenter: () => ({ ...mapCenter }),
  getContainer: () => mapContainer,
  getCanvas: () => ({ dispatchEvent() {} }),
  getZoom: () => 18,
  project([lng, lat]) {
    return { x: 480 + (lng - mapCenter.lng) * 50000, y: 372 - (lat - mapCenter.lat) * 50000 };
  },
  unproject(point) {
    return { lng: mapCenter.lng + (point.x - 480) / 50000, lat: mapCenter.lat - (point.y - 372) / 50000 };
  },
  fitBounds(bounds, options) {
    fitCalls.push({ bounds: structuredClone(bounds), options: structuredClone(options) });
    this.emit('move');
    this.emit('zoom');
  },
  resize() {},
  zoomTo() {},
  panBy() {}
};

const exportPaper = new FakeControl({
  value: 'a4',
  options: ['a4', 'a3', 'square', 'ratio43'].map((value) => ({ value, hidden: false, disabled: false }))
});
const elements = {
  exportFrame: new FakeControl(),
  exportPageBox: new FakeControl(),
  exportFrameBox: new FakeControl(),
  exportCenterMarker: new FakeControl(),
  exportOutput: new FakeControl({ value: 'pdf' }),
  exportPaper,
  exportOrientationField: new FakeControl(),
  exportOrientation: new FakeControl({ value: 'portrait' }),
  exportScale: new FakeControl({ value: '1000' }),
  exportLayout: new FakeControl({ checked: true }),
  exportHighlight: new FakeControl({ checked: true }),
  exportSummary: new FakeControl({ textContent: '' }),
  exportStatus: new FakeControl({ textContent: '' }),
  exportPreview: new FakeControl(),
  exportSidebar: {
    querySelector: () => ({ hidden: false, getBoundingClientRect: () => rect({ top: 602, width: 390, height: 142 }) })
  }
};
const store = createStore({
  activeTool: 'none',
  layout: { sidebarOpen: false, mobileExportSettings: false },
  export: { center: null },
  selection: { parcels: [], buildings: [] },
  layers: {},
  access: { pro: false }
});

const controller = createExportController({ map, api: {}, store, elements });

function frameMeters(call, latitude = store.getState().export.center.lat) {
  const [[west, south], [east, north]] = call.bounds;
  return {
    width: (east - west) * 111320 * Math.cos(latitude * Math.PI / 180),
    height: (north - south) * 111320
  };
}

function assertDimensions(call, expectedWidth, expectedHeight) {
  const dimensions = frameMeters(call);
  assert.ok(Math.abs(dimensions.width - expectedWidth) < .01, `${dimensions.width} m statt ${expectedWidth} m Breite`);
  assert.ok(Math.abs(dimensions.height - expectedHeight) < .01, `${dimensions.height} m statt ${expectedHeight} m Höhe`);
}

store.setState({
  activeTool: 'export',
  layout: { ...store.getState().layout, sidebarOpen: true }
}, 'tool');
assert.equal(fitCalls.length, 1, 'Das Öffnen des Exporttools muss den aktuellen Rahmen einmalig einpassen.');
assert.deepEqual(store.getState().export.center, mapCenter);
assertDimensions(fitCalls.at(-1), 210, 297);
assert.equal(fitCalls.at(-1).options.linear, true);
assert.equal(fitCalls.at(-1).options.retainPadding, false);
assert.ok(Object.values(fitCalls.at(-1).options.padding).every((value) => value >= 24));
assert.equal(fitCalls.at(-1).options.padding.left, fitCalls.at(-1).options.padding.right);
assert.equal(fitCalls.at(-1).options.padding.top, fitCalls.at(-1).options.padding.bottom);

const countAfterOpen = fitCalls.length;
for (let index = 0; index < 10; index += 1) {
  map.emit('move');
  map.emit('zoom');
}
controller.render();
elements.exportHighlight.checked = false;
elements.exportHighlight.dispatch('change');
controller.setCenter({ lng: 9.85, lat: 52.33 });
map.emit('click', { lngLat: { lng: 9.86, lat: 52.34 } });
assert.equal(fitCalls.length, countAfterOpen, 'Rendern, Kartenbewegungen, Highlight und Centeränderungen dürfen keinen Refit auslösen.');

elements.exportPaper.value = 'a3';
elements.exportPaper.dispatch('change');
assert.equal(fitCalls.length, countAfterOpen + 1);
assertDimensions(fitCalls.at(-1), 297, 420);

elements.exportPaper.value = 'a4';
elements.exportPaper.dispatch('change');
assert.equal(fitCalls.length, countAfterOpen + 2);
assertDimensions(fitCalls.at(-1), 210, 297);

elements.exportOrientation.value = 'landscape';
elements.exportOrientation.dispatch('change');
assertDimensions(fitCalls.at(-1), 297, 210);

elements.exportScale.value = '2000';
elements.exportScale.dispatch('change');
assertDimensions(fitCalls.at(-1), 594, 420);

elements.exportScale.value = '500';
elements.exportScale.dispatch('change');
assertDimensions(fitCalls.at(-1), 148.5, 105);

elements.exportOutput.value = 'png';
elements.exportOutput.dispatch('change');
elements.exportPaper.value = 'square';
elements.exportPaper.dispatch('change');
assert.equal(elements.exportPaper.value, 'a4', 'PNG mit aktivem Layout muss auf einem Dokumentformat bleiben.');
assertDimensions(fitCalls.at(-1), 148.5, 105);
elements.exportLayout.checked = false;
elements.exportLayout.dispatch('change');
elements.exportPaper.value = 'square';
elements.exportPaper.dispatch('change');
assertDimensions(fitCalls.at(-1), 105, 105);
elements.exportOutput.value = 'pdf';
elements.exportOutput.dispatch('change');
assert.equal(elements.exportPaper.value, 'a4', 'PDF muss ein zuvor gewähltes Bildformat auf A4 zurückführen.');
assertDimensions(fitCalls.at(-1), 148.5, 105);

const fitCountBeforeClose = fitCalls.length;
store.setState({
  activeTool: 'none',
  layout: { ...store.getState().layout, sidebarOpen: false }
}, 'tool');
map.emit('move');
assert.equal(fitCalls.length, fitCountBeforeClose);
mapCenter = { lng: 10.1, lat: 52.6 };
store.setState({
  activeTool: 'export',
  layout: { ...store.getState().layout, sidebarOpen: true }
}, 'tool');
assert.equal(fitCalls.length, fitCountBeforeClose + 1);
assert.deepEqual(store.getState().export.center, mapCenter, 'Beim erneuten Öffnen muss der aktuelle Kartenmittelpunkt übernommen werden.');

const mobilePadding = exportFrameFitPadding({
  width: 390,
  height: 788,
  topInset: 61,
  leftInset: 61,
  bottomInset: 174,
  mobile: true
});
assert.ok(mobilePadding.bottom >= 190, 'Die mobile Exportleiste plus Sichtabstand muss vollständig reserviert werden.');
assert.equal(mobilePadding.top, mobilePadding.bottom, 'Vertikales Padding bleibt symmetrisch und verhindert geografisches Wandern.');
assert.equal(mobilePadding.left, mobilePadding.right, 'Horizontales Padding bleibt symmetrisch.');

const directFitCalls = [];
assert.equal(fitMapToExportFrame(
  { fitBounds: (...args) => directFitCalls.push(args) },
  { west: 9, south: 52, east: 10, north: 53 },
  mobilePadding,
  123
), true);
assert.equal(directFitCalls[0][1].duration, 123);
assert.equal(fitMapToExportFrame({ fitBounds() {} }, { west: NaN, south: 52, east: 10, north: 53 }, mobilePadding), false);

const exportSource = readFileSync(new URL('../live-viewer/viewer-app/export.js', import.meta.url), 'utf8');
assert.match(exportSource, /for \(const control of \[exportOutput, exportPaper, exportOrientation, exportScale, exportLayout\]\)/);
assert.match(exportSource, /exportHighlight\.addEventListener\('change', \(\) => \{\s*render\(\);\s*onWorkspaceChange\(\);\s*\}\)/);
assert.doesNotMatch(exportSource, /map\.on\(['"](?:move|zoom)['"][^;]*fitFrame/);

console.log('export-frame-fit-tests=ok');
