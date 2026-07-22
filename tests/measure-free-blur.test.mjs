import assert from 'node:assert/strict';

import {
  createMeasureController,
  LOCKED_MEASUREMENT_VALUES
} from '../live-viewer/viewer-app/measure.js';

class FakeElement {
  constructor() {
    this.hidden = false;
    this.dataset = {};
    this.attributes = new Map();
    this.style = {
      removeProperty: (name) => {
        delete this.style[name];
      }
    };
    this.textContent = '';
    this.title = '';
    this.offsetWidth = 226;
    this.offsetHeight = 118;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  closest(selector) {
    return selector === '.planner-app' ? plannerApp : null;
  }
}

const plannerApp = new FakeElement();
const handlers = new Map();
const sources = new Map([
  ['measure-v2', { setData() {} }],
  ['measure-snap-v2', { setData() {} }]
]);
const map = {
  on(type, listener) {
    const listeners = handlers.get(type) || [];
    listeners.push(listener);
    handlers.set(type, listeners);
  },
  getSource(id) {
    return sources.get(id) || null;
  },
  getLayer() {
    return null;
  },
  getZoom() {
    return 18;
  },
  getCanvas() {
    return { addEventListener() {} };
  },
  getContainer() {
    return { clientWidth: 1000, clientHeight: 700 };
  },
  project(coordinate) {
    return { x: coordinate[0] * 10, y: coordinate[1] * 10 };
  },
  unproject(point) {
    return { lng: point[0] / 10, lat: point[1] / 10 };
  },
  queryRenderedFeatures() {
    return [];
  },
  doubleClickZoom: {
    disable() {},
    enable() {}
  }
};

const subscribers = [];
let state = {
  activeTool: 'measure',
  access: { ready: true, pro: false },
  selection: { parcels: [], buildings: [] }
};
const store = {
  getState() {
    return state;
  },
  subscribe(listener) {
    subscribers.push(listener);
  }
};

globalThis.window = {
  addEventListener() {},
  cancelAnimationFrame() {},
  matchMedia() {
    return { matches: false };
  },
  requestAnimationFrame() {
    return 1;
  },
  setTimeout() {}
};

const elements = {
  measurePanel: new FakeElement(),
  measureValues: new FakeElement(),
  measureLocked: new FakeElement(),
  measureDistance: new FakeElement(),
  measureAngleLabel: new FakeElement(),
  measureAngle: new FakeElement(),
  measureLongitude: new FakeElement(),
  measureLatitude: new FakeElement(),
  measureCumulative: new FakeElement(),
  measureArea: new FakeElement()
};

createMeasureController({ map, store, elements });
const click = handlers.get('click')?.[0];
assert.equal(typeof click, 'function');
click({
  point: { x: 300, y: 250 },
  lngLat: { lng: 9.84841, lat: 52.32984 },
  originalEvent: {}
});

function renderedValues() {
  return {
    distance: elements.measureDistance.textContent,
    angle: elements.measureAngle.textContent,
    longitude: elements.measureLongitude.textContent,
    latitude: elements.measureLatitude.textContent,
    cumulative: elements.measureCumulative.textContent,
    area: elements.measureArea.textContent
  };
}

assert.deepEqual(renderedValues(), LOCKED_MEASUREMENT_VALUES);
assert.equal(elements.measurePanel.dataset.locked, 'true');
assert.equal(elements.measureValues.hidden, false);
assert.equal(elements.measureValues.getAttribute('aria-hidden'), 'true');
assert.equal(elements.measureLocked.hidden, false);
assert.equal(plannerApp.getAttribute('data-measure-panel-locked'), 'true');

state = { ...state, access: { ready: true, pro: true } };
for (const subscriber of subscribers) subscriber(state, 'access');
const proValues = renderedValues();
assert.notDeepEqual(proValues, LOCKED_MEASUREMENT_VALUES);
assert.equal(proValues.longitude, '9,848410° E');
assert.equal(proValues.latitude, '52,329840° N');
assert.equal(elements.measurePanel.dataset.locked, 'false');
assert.equal(elements.measureValues.getAttribute('aria-hidden'), 'false');
assert.equal(elements.measureLocked.hidden, true);
assert.equal(plannerApp.getAttribute('data-measure-panel-locked'), 'false');

state = { ...state, access: { ready: false, pro: true } };
for (const subscriber of subscribers) subscriber(state, 'access-loading');
assert.deepEqual(renderedValues(), LOCKED_MEASUREMENT_VALUES, 'Access-Loading muss alte echte Messwerte sofort überschreiben.');
assert.equal(elements.measurePanel.dataset.locked, 'true');
assert.equal(elements.measureValues.getAttribute('aria-hidden'), 'true');
assert.equal(elements.measureLocked.hidden, false);
assert.equal(plannerApp.getAttribute('data-measure-panel-locked'), 'true');
assert.doesNotMatch(JSON.stringify(renderedValues()), /9,848410|52,329840/);

console.log('measure-free-blur-tests=ok');
