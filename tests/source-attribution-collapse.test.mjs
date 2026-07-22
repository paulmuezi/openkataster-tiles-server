import assert from 'node:assert/strict';

import { createSourceController } from '../live-viewer/viewer-app/sources.js';

class FakeNode {
  constructor(tagName = '') {
    this.tagName = tagName;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.hidden = false;
    this.href = '';
    this.rel = '';
    this.target = '';
    this.textContent = '';
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = [...nodes];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
}

class FakeMap {
  constructor() {
    this.canvas = new FakeNode('canvas');
    this.listeners = new Map();
  }

  getCanvas() {
    return this.canvas;
  }

  getZoom() {
    return 18;
  }

  on(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
}

function createFakeClock() {
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    pending() {
      return [...timers.entries()].map(([id, timer]) => ({ id, ...timer }));
    },
    runAll() {
      const scheduled = [...timers.values()];
      timers.clear();
      for (const timer of scheduled) timer.callback();
    },
    reset() {
      timers.clear();
    }
  };
}

function createElements() {
  const elements = {
    osmAttribution: new FakeNode('a'),
    sourceButton: new FakeNode('button'),
    sourcePanel: new FakeNode('section'),
    sourceList: new FakeNode('ul')
  };
  elements.osmAttribution.hidden = true;
  elements.sourcePanel.hidden = true;
  elements.sourceButton.setAttribute('aria-expanded', 'false');
  return elements;
}

function findLinks(node) {
  const links = [];
  if (node?.tagName === 'a') links.push(node);
  for (const child of node?.children || []) links.push(...findLinks(child));
  return links;
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

function createHarness({
  showCompactAttribution = () => true,
  compactAttributionDurationMs = 5000
} = {}) {
  const map = new FakeMap();
  const elements = createElements();
  const controller = createSourceController({
    map,
    api: {
      sources: async () => ({
        states: [],
        attributions: [{
          text: '© OpenStreetMap-Mitwirkende',
          href: 'https://www.openstreetmap.org/copyright'
        }]
      })
    },
    store: { subscribe() {} },
    elements,
    layerController: {
      currentStateSlug: () => '',
      isBasemapVisible: () => true
    },
    showCompactAttribution,
    compactAttributionDurationMs
  });
  return { controller, map, elements };
}

const previousWindow = globalThis.window;
const previousDocument = globalThis.document;
const clock = createFakeClock();

globalThis.window = {
  setTimeout: clock.setTimeout,
  clearTimeout: clock.clearTimeout
};
globalThis.document = {
  createElement: (tagName) => new FakeNode(tagName),
  createTextNode: (text) => {
    const node = new FakeNode('#text');
    node.textContent = String(text);
    return node;
  }
};

try {
  {
    const { elements } = createHarness();
    await flushPromises();

    assert.equal(elements.osmAttribution.hidden, false, 'OSM attribution starts visible');
    assert.equal(elements.osmAttribution.textContent, '© OpenStreetMap-Mitwirkende');
    assert.equal(elements.osmAttribution.href, 'https://www.openstreetmap.org/copyright');
    assert.equal(clock.pending().length, 1, 'exactly one collapse timer is scheduled');
    assert.equal(clock.pending()[0].delay, 5000);

    clock.runAll();
    assert.equal(elements.osmAttribution.hidden, true, 'timer collapses compact attribution');

    elements.sourceButton.dispatch('click');
    assert.equal(elements.sourcePanel.hidden, false, 'source panel remains available after collapse');
    assert.equal(elements.sourceButton.getAttribute('aria-expanded'), 'true');
    const osmLink = findLinks(elements.sourceList).find(
      (link) => link.href === 'https://www.openstreetmap.org/copyright'
    );
    assert.ok(osmLink, 'source panel retains the OpenStreetMap copyright link');
    assert.equal(osmLink.textContent, '© OpenStreetMap-Mitwirkende');

    elements.sourceButton.dispatch('click');
    assert.equal(elements.sourcePanel.hidden, true);
    assert.equal(elements.osmAttribution.hidden, true, 'closing the panel does not re-expand collapsed credit');
  }

  clock.reset();

  {
    const { controller, map, elements } = createHarness();
    await flushPromises();
    assert.equal(elements.osmAttribution.hidden, false);

    map.canvas.dispatch('pointerdown', { pointerType: 'mouse' });
    assert.equal(elements.osmAttribution.hidden, true, 'real canvas pointer interaction collapses credit');
    assert.equal(clock.pending().length, 0, 'interaction clears the pending timer');

    controller.revealCompactAttribution();
    assert.equal(elements.osmAttribution.hidden, false);
    map.canvas.dispatch('wheel', { deltaY: 1 });
    assert.equal(elements.osmAttribution.hidden, true, 'canvas wheel interaction also collapses credit');
  }

  clock.reset();

  {
    const { controller, map, elements } = createHarness({
      showCompactAttribution: () => false
    });
    await flushPromises();

    assert.equal(elements.osmAttribution.hidden, true, 'disabled compact attribution stays hidden');
    assert.equal(clock.pending().length, 0, 'hidden surfaces do not schedule a collapse timer');
    controller.revealCompactAttribution();
    map.canvas.dispatch('pointerdown', { pointerType: 'touch' });
    assert.equal(elements.osmAttribution.hidden, true);
    assert.equal(clock.pending().length, 0);
  }
} finally {
  globalThis.window = previousWindow;
  globalThis.document = previousDocument;
}

console.log('source-attribution-collapse-tests=ok');
