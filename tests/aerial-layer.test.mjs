import assert from 'node:assert/strict';
import fs from 'node:fs';

const layersSource = fs.readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');
const appSource = fs.readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const exportSource = fs.readFileSync(new URL('../live-viewer/viewer-app/export.js', import.meta.url), 'utf8');
const indexSource = fs.readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const sourcesSource = fs.readFileSync(new URL('../live-viewer/viewer-app/sources.js', import.meta.url), 'utf8');

assert.match(
  layersSource,
  /state\?\.rendering\?\.aerial_raster/,
  'Der Viewer muss die Luftbild-Verfügbarkeit aus der serverseitigen Capability lesen.'
);
assert.doesNotMatch(layersSource, /AERIAL_STATES/);
assert.match(
  layersSource,
  /tiles: \[`\$\{capability\.tile_template\}\$\{separator\}v=\$\{revision\}`\]/,
  'Die Luftbild-URL und ihre Revision müssen vollständig aus der Capability kommen.'
);
assert.match(layersSource, /tileSize: Number\(capability\.tile_size\) \|\| 512/);
assert.match(
  layersSource,
  /const nativeMaxZoom = Number\(capability\.maxzoom\) \|\| 22;[\s\S]*?map\.addSource\(sourceId,[\s\S]*?maxzoom: nativeMaxZoom[\s\S]*?map\.addLayer\(\{[\s\S]*?minzoom: Number\(capability\.minzoom\) \|\| detailZoom,[\s\S]*?paint:/,
  'Die höchste native Luftbildstufe gehört an die Rasterquelle; der Layer muss darüber sichtbar bleiben und überzoomen.'
);
const updateAerialStart = layersSource.indexOf('function updateAerial(show)');
const aerialLayerStart = layersSource.indexOf('map.addLayer({', updateAerialStart);
const aerialLayerEnd = layersSource.indexOf('}, currentDataset()', aerialLayerStart);
assert.ok(updateAerialStart >= 0 && aerialLayerStart >= 0 && aerialLayerEnd > aerialLayerStart);
assert.doesNotMatch(
  layersSource.slice(aerialLayerStart, aerialLayerEnd),
  /\bmaxzoom:/,
  'Die native Kachelgrenze darf den Luftbild-Layer nicht bei Zoom 19 ausblenden.'
);
assert.match(layersSource, /map\.moveLayer\(activeAerial, activeCadastre\)/);
assert.match(sourcesSource, /const aerialCapability = state\?\.rendering\?\.aerial_raster/);
assert.match(sourcesSource, /aerialCapability\.attribution \|\| state\?\.quellenvermerk/);
assert.match(sourcesSource, /onStateCapabilities\(state \|\| null\)/);
assert.match(appSource, /exportController\?\.setStateCapabilities\(state\)/);
assert.match(exportSource, /stateDxfAllowed = !onOfficeMode && state\?\.export\?\.dxf !== false/);
assert.match(exportSource, /option\.hidden = !allowed/);
assert.match(exportSource, /countryResolver\?\.intersectsAustria\?\.\(frame\) === true/);
assert.match(appSource, /\.\/layers\.js\?v=20260724-bounds1/);
assert.match(indexSource, /app\.js\?v=20260724-unified7/);
assert.match(indexSource, /styles\.css\?v=20260723-unified1/);

console.log('aerial-layer-tests=ok');
