import assert from 'node:assert/strict';
import fs from 'node:fs';

const layersSource = fs.readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');
const appSource = fs.readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
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
assert.match(layersSource, /map\.moveLayer\(activeAerial, activeCadastre\)/);
assert.match(sourcesSource, /const aerialCapability = state\?\.rendering\?\.aerial_raster/);
assert.match(sourcesSource, /aerialCapability\.attribution \|\| state\?\.quellenvermerk/);
assert.match(sourcesSource, /onStateCapabilities\(state \|\| null\)/);
assert.match(appSource, /state\?\.export\?\.dxf !== false/);
assert.match(appSource, /dxfOption\.hidden = !dxfAvailable/);
assert.match(appSource, /\.\/layers\.js\?v=20260723-austria1/);
assert.match(indexSource, /app\.js\?v=20260723-austria1/);
assert.match(indexSource, /styles\.css\?v=20260723-austria1/);

console.log('aerial-layer-tests=ok');
