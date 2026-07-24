import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createLatestFrameScheduler,
  createMapStyleMutationWriter,
  layerZoomBandSignature
} from '../live-viewer/viewer-app/layers.js';

const layout = new Map();
const paint = new Map();
const filters = new Map();
const writes = { layout: 0, paint: 0, filter: 0 };
const map = {
  getLayer(id) {
    return id === 'present' ? { id } : null;
  },
  getLayoutProperty(id, property) {
    return layout.get(`${id}:${property}`);
  },
  setLayoutProperty(id, property, value) {
    writes.layout += 1;
    layout.set(`${id}:${property}`, value);
  },
  getPaintProperty(id, property) {
    return paint.get(`${id}:${property}`);
  },
  setPaintProperty(id, property, value) {
    writes.paint += 1;
    paint.set(`${id}:${property}`, value);
  },
  getFilter(id) {
    return filters.get(id);
  },
  setFilter(id, value) {
    writes.filter += 1;
    filters.set(id, value);
  }
};

const writer = createMapStyleMutationWriter(map);
assert.equal(writer.setLayoutProperty('missing', 'visibility', 'none'), false);
assert.equal(writer.setLayoutProperty('present', 'visibility', 'none'), true);
assert.equal(writer.setLayoutProperty('present', 'visibility', 'none'), false);
assert.equal(writer.setLayoutProperty('present', 'visibility', 'visible'), true);
assert.equal(writes.layout, 2, 'Identische Layoutwerte dürfen MapLibre nicht erneut invalidieren.');

const expression = ['coalesce', ['get', 'fill_color'], '#CCCCCC'];
assert.equal(writer.setPaintProperty('present', 'fill-color', expression), true);
assert.equal(
  writer.setPaintProperty('present', 'fill-color', structuredClone(expression)),
  false
);
assert.equal(writes.paint, 1, 'Strukturell identische Expressions dürfen nicht erneut gesetzt werden.');

const filter = ['all', ['==', 'kind', 'region']];
assert.equal(writer.setFilter('present', filter), true);
assert.equal(writer.setFilter('present', structuredClone(filter)), false);
assert.equal(writes.filter, 1, 'Ein unveränderter Filter darf nicht erneut kompiliert werden.');

const queuedFrames = [];
const deliveredValues = [];
const schedule = createLatestFrameScheduler(
  (value) => deliveredValues.push(value),
  (callback) => queuedFrames.push(callback)
);
schedule('first');
schedule('second');
schedule('latest');
assert.equal(queuedFrames.length, 1, 'Ereignisbursts müssen in einem Animation-Frame gebündelt werden.');
assert.deepEqual(deliveredValues, []);
queuedFrames.shift()();
assert.deepEqual(deliveredValues, ['latest']);
schedule('next-frame');
assert.equal(queuedFrames.length, 1);
queuedFrames.shift()();
assert.deepEqual(deliveredValues, ['latest', 'next-frame']);

assert.equal(
  layerZoomBandSignature({
    zoom: 16.9,
    dataset: 'deutschland',
    deDetailZoom: 17,
    atDetailZoom: 16,
    deAerialZoom: 16,
    atAerialZoom: 14
  }),
  'deutschland:false:true:true:true'
);

const layersSource = readFileSync(
  new URL('../live-viewer/viewer-app/layers.js', import.meta.url),
  'utf8'
);
assert.doesNotMatch(
  layersSource,
  /map\.on\('zoom',\s*\(\)\s*=>\s*apply\(\)\)/,
  'Zoom darf nicht mehr in jedem MapLibre-Frame den gesamten Layerzustand neu schreiben.'
);
assert.match(layersSource, /map\.on\('zoom', applyForZoomBand\)/);
const zoomBandSource = layersSource.slice(
  layersSource.indexOf('function zoomBandSignature('),
  layersSource.indexOf('function relevantSourceId(')
);
assert.doesNotMatch(
  zoomBandSource,
  /currentStateSlug|currentDataset|pointInGeometry/,
  'Der Zoom-Hot-Path muss den beim moveend ermittelten Viewport-Datensatz wiederverwenden.'
);
assert.match(
  layersSource,
  /if \(sourceReadinessChanged\(event\)\) scheduleApply\(\)/,
  'sourcedata darf nur bei einem echten Ladezustandswechsel neu anwenden.'
);
