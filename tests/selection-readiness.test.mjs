import assert from 'node:assert/strict';

import { createSelectionController, waitForAccessReady } from '../live-viewer/viewer-app/selection.js';

function createTestStore(access = { ready: false, pro: false, session: null }) {
  let state = {
    access,
    activeTool: 'select',
    layout: { tableOpen: false },
    selection: { parcels: [], buildings: [], loading: false }
  };
  const listeners = new Set();
  return {
    getState() { return state; },
    setState(patch, reason = 'update') {
      state = { ...state, ...patch };
      for (const listener of listeners) listener(state, reason);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    }
  };
}

function createController({ store, api }) {
  const selectionContent = {
    innerHTML: '',
    addEventListener() {},
    contains() { return false; }
  };
  return createSelectionController({
    map: {
      on() {},
      getSource() { return null; },
      getCanvas() { return { addEventListener() {} }; }
    },
    api,
    store,
    layout: {
      isMobile() { return false; },
      setTable(open) {
        const state = store.getState();
        store.setState({ layout: { ...state.layout, tableOpen: open } }, 'table');
      }
    },
    elements: {
      selectionContent,
      selectionCount: { textContent: '' },
      selectTool: { classList: { add() {}, remove() {} } },
      selectionDock: { classList: { toggle() {} } }
    }
  });
}

const waitingStore = createTestStore();
let readyResolved = false;
const readyPromise = waitForAccessReady(waitingStore).then((access) => {
  readyResolved = true;
  return access;
});
await Promise.resolve();
assert.equal(readyResolved, false, 'access readiness must remain pending while the session is unknown');
waitingStore.setState({ access: { ready: true, pro: false, session: { access: 'free' } } }, 'access');
assert.equal((await readyPromise).pro, false);

for (const scenario of [
  { pro: false, expected: 'preview' },
  { pro: true, expected: 'full' }
]) {
  const store = createTestStore();
  const calls = [];
  const api = {
    async featureAt(_lng, _lat, signal) {
      calls.push({ kind: 'full', signal });
      return { buildings: [{ gml_id: 'building-full' }], parcels: [{ gml_id: 'parcel-full' }] };
    },
    async featurePreviewAt(_lng, _lat, signal) {
      calls.push({ kind: 'preview', signal });
      return { buildings: [{ preview_id: 'building-preview' }], parcels: [{ preview_id: 'parcel-preview' }] };
    }
  };
  const controller = createController({ store, api });
  const selectionPromise = controller.selectAt({ lng: 9.84, lat: 52.33 }, true);
  await Promise.resolve();
  assert.deepEqual(calls, [], 'a click before session resolution must not use the wrong endpoint');

  store.setState({
    access: {
      ready: true,
      pro: scenario.pro,
      session: { authenticated: scenario.pro, access: scenario.pro ? 'pro' : 'free' }
    }
  }, 'access');
  await selectionPromise;

  assert.equal(calls.length, 1);
  assert.equal(calls[0].kind, scenario.expected, `the ${scenario.expected} endpoint must be selected after access resolves`);
  assert.equal(store.getState().selection.buildings.length, 1);
  assert.equal(store.getState().selection.parcels.length, 1);
  assert.equal(store.getState().selection.loading, false);
}

console.log('selection-readiness-tests=ok');
