import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

import {
  BASEMAP_RUNTIME_CONSTANTS,
  installEuropeBasemapFailover,
  normalizeBasemapMode,
  resolvePlannerBasemap,
  selectBasemapProfile
} from '../live-viewer/viewer-app/basemap.js';
import { resolveLayerFontStack } from '../live-viewer/viewer-app/layers.js';

globalThis.window = {
  location: new URL('https://tiles.openkataster.de/planer'),
  setTimeout,
  clearTimeout,
  fetch: globalThis.fetch
};

const europe = {
  available: true,
  version: 'europe-20260723-z15',
  style_url: '/viewer-assets/europe-basemap-style-20260724/style.json',
  tile_template: '/api/v1/basemap/europe/{z}/{x}/{y}.mvt',
  minzoom: 0,
  maxzoom: 15,
  bounds: [-25, 34, 45, 72],
  attribution: [
    '© OpenStreetMap contributors',
    '© ESA WorldCover project 2020 / Contains modified Copernicus Sentinel',
    'data (2020) processed by ESA WorldCover consortium'
  ].join(' · '),
  licenses: [
    {
      id: 'openstreetmap',
      license: 'ODbL-1.0',
      url: 'https://www.openstreetmap.org/copyright'
    },
    {
      id: 'esa-worldcover-2020',
      license: 'CC-BY-4.0',
      url: 'https://esa-worldcover.org/'
    }
  ]
};

assert.equal(normalizeBasemapMode('on'), 'on');
assert.equal(normalizeBasemapMode('unexpected'), 'off');
assert.equal(selectBasemapProfile({ mode: 'off', europe }, '?basemap=europe'), 'national');
assert.equal(selectBasemapProfile({ mode: 'preview', europe }, ''), 'national');
assert.equal(selectBasemapProfile({ mode: 'preview', europe }, '?basemap=europe'), 'europe');
assert.equal(selectBasemapProfile({ mode: 'on', europe }, ''), 'europe');
assert.equal(selectBasemapProfile({ mode: 'on', europe }, '?basemap=national'), 'national');
assert.equal(resolveLayerFontStack(false, 'europe'), 'Noto Sans Regular');
assert.equal(resolveLayerFontStack(true, 'europe'), 'Noto Sans Medium');
assert.equal(resolveLayerFontStack(true, 'national'), 'Noto Sans Bold');
assert.equal(
  selectBasemapProfile({ mode: 'on', europe: { ...europe, version: '../unsafe' } }, ''),
  'national'
);

const style = JSON.parse(readFileSync(
  new URL('../live-viewer/europe-basemap-style-20260724/style.json', import.meta.url),
  'utf8'
));
const fetchCalls = [];
const runtime = await resolvePlannerBasemap({
  locationObject: new URL('https://tiles.openkataster.de/planer'),
  fetchImpl: async (url) => {
    fetchCalls.push(String(url));
    if (String(url) === BASEMAP_RUNTIME_CONSTANTS.configUrl) {
      return new Response(JSON.stringify({ mode: 'on', europe }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      });
    }
    return new Response(JSON.stringify(structuredClone(style)), {
      status: 200,
      headers: { 'content-type': 'application/json' }
    });
  }
});
assert.equal(runtime.profile, 'europe');
assert.equal(runtime.version, 'europe-20260723-z15');
assert.deepEqual(fetchCalls, [
  '/api/v1/basemap/config',
  '/viewer-assets/europe-basemap-style-20260724/style.json'
]);
assert.deepEqual(runtime.style.sources.openkataster_europe.tiles, [
  'https://tiles.openkataster.de/api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=europe-20260723-z15'
]);
assert.equal(
  runtime.style.glyphs,
  'https://tiles.openkataster.de/viewer-assets/europe-basemap-assets-protomaps-028c18f7/fonts/{fontstack}/{range}.pbf'
);
assert.equal(
  runtime.style.sprite,
  'https://tiles.openkataster.de/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light'
);

const resolveUnsafeRuntime = async ({ europeOverride = {}, styleOverride = {} }) => (
  resolvePlannerBasemap({
    locationObject: new URL('https://tiles.openkataster.de/planer?basemap=europe'),
    fetchImpl: async (url) => {
      if (String(url) === BASEMAP_RUNTIME_CONSTANTS.configUrl) {
        return new Response(JSON.stringify({
          mode: 'on',
          europe: { ...europe, ...europeOverride }
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        ...structuredClone(style),
        ...styleOverride
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
  })
);
assert.equal(
  (await resolveUnsafeRuntime({
    europeOverride: { tile_template: 'https://example.invalid/{z}/{x}/{y}.mvt' }
  })).profile,
  'national'
);
assert.equal(
  (await resolveUnsafeRuntime({
    styleOverride: { glyphs: 'https://example.invalid/fonts/{fontstack}/{range}.pbf' }
  })).profile,
  'national'
);
assert.equal(
  (await resolveUnsafeRuntime({
    styleOverride: { sprite: 'https://example.invalid/sprites/light' }
  })).profile,
  'national'
);

const failedRuntime = await resolvePlannerBasemap({
  locationObject: new URL('https://tiles.openkataster.de/planer'),
  fetchImpl: async () => new Response('', { status: 503 })
});
assert.equal(failedRuntime.profile, 'national');

class FakeMap {
  constructor() {
    this.listeners = new Map();
  }

  on(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  off(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
  }

  emit(type, event) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}

const map = new FakeMap();
let replacement = '';
const dispose = installEuropeBasemapFailover(map, runtime, {
  locationObject: new URL('https://tiles.openkataster.de/planer?foo=1#7/48/14'),
  replace: (url) => { replacement = url; },
  threshold: 3
});
map.emit('error', { sourceId: 'unrelated' });
map.emit('error', { sourceId: 'openkataster_europe' });
map.emit('error', { sourceId: 'openkataster_europe' });
assert.equal(replacement, '');
map.emit('error', { sourceId: 'openkataster_europe' });
assert.equal(
  replacement,
  'https://tiles.openkataster.de/planer?foo=1&basemap=national#7/48/14'
);
dispose();

assert.equal(style.version, 8);
assert.equal(style.sources.openkataster_europe.type, 'vector');
assert.equal(
  style.sources.openkataster_europe.tiles[0],
  '/api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=__OPENKATASTER_BASEMAP_VERSION__'
);
assert.ok(style.glyphs.startsWith('/viewer-assets/'));
assert.ok(style.sprite.startsWith('/viewer-assets/'));
assert.match(style.sources.openkataster_europe.attribution, /OpenStreetMap contributors/);
assert.match(style.sources.openkataster_europe.attribution, /ESA WorldCover project 2020/);
assert.match(style.sources.openkataster_europe.attribution, /CC BY 4\.0/);
assert.equal(
  style.layers.some((layer) => layer['source-layer'] === 'landcover'),
  false,
  'Der Europe-Style soll den mitverteilten Landcover-Layer bewusst nicht rendern.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'places_region')?.minzoom,
  5.8,
  'Regionen/Bundesländer dürfen in der Länderübersicht nicht beschriften.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'boundaries')?.minzoom,
  5.8,
  'Regionale Grenzen dürfen in der Länderübersicht nicht erscheinen.'
);
assert.equal(
  style.layers.find((layer) => layer.id === 'boundaries_country')?.minzoom,
  undefined,
  'Staatsgrenzen müssen in der Länderübersicht sichtbar bleiben.'
);
for (const layer of style.layers) {
  if ('source' in layer) assert.equal(layer.source, 'openkataster_europe');
}

const assetRoot = new URL(
  '../live-viewer/europe-basemap-assets-protomaps-028c18f7/',
  import.meta.url
);
const provenance = JSON.parse(readFileSync(new URL('PROVENANCE.json', assetRoot), 'utf8'));
const assetRootPath = assetRoot.pathname;
for (const requiredFont of ['Noto Sans Regular', 'Noto Sans Medium', 'Noto Sans Italic']) {
  assert.equal(
    statSync(join(assetRootPath, 'fonts', requiredFont)).isDirectory(),
    true,
    `Required Europe font is missing: ${requiredFont}`
  );
}
const vendoredFiles = [];
const collectFiles = (directory) => {
  for (const name of readdirSync(directory).sort()) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) collectFiles(path);
    else if (name !== 'PROVENANCE.json') vendoredFiles.push(path);
  }
};
collectFiles(assetRootPath);
vendoredFiles.sort((left, right) => (
  Buffer.compare(
    Buffer.from(relative(assetRootPath, left).split(sep).join('/'), 'utf8'),
    Buffer.from(relative(assetRootPath, right).split(sep).join('/'), 'utf8')
  )
));
const treeHash = createHash('sha256');
let vendoredSize = 0;
for (const path of vendoredFiles) {
  const content = readFileSync(path);
  const relativePath = relative(assetRootPath, path).split(sep).join('/');
  treeHash.update(relativePath);
  treeHash.update('\0');
  treeHash.update(createHash('sha256').update(content).digest('hex'));
  treeHash.update('\n');
  vendoredSize += content.byteLength;
}
assert.equal(
  vendoredFiles.length,
  provenance.vendored_file_count_excluding_provenance
);
assert.equal(
  vendoredSize,
  provenance.vendored_size_bytes_excluding_provenance
);
assert.equal(
  treeHash.digest('hex'),
  provenance.canonical_tree_sha256_excluding_provenance
);

console.log('europe-basemap-tests=ok');
