import assert from 'node:assert/strict';
import fs from 'node:fs';

const layersSource = fs.readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');
const sourcesSource = fs.readFileSync(new URL('../live-viewer/viewer-app/sources.js', import.meta.url), 'utf8');
const mainSource = fs.readFileSync(new URL('../openkataster_tiles/main.py', import.meta.url), 'utf8');

assert.match(
  layersSource,
  /state\?\.rendering\?\.cadastre_raster/,
  'Der Viewer darf Kataster-WMS nur aus einer serverseitigen Capability einschalten.'
);
assert.match(layersSource, /state\?\.visual_active !== false/);
assert.match(layersSource, /state\?\.rendering\?\.cadastre_raster\?\.tile_template/);
assert.match(layersSource, /official-cadastre-\$\{slug\}/);
assert.match(layersSource, /capability\.tile_template/);
assert.match(layersSource, /welcome-hover-parcel-hit/);
assert.match(
  layersSource,
  /updateOfficialCadastre\(detail && layers\.alkis, detail && layers\.aerial\)/,
  'Das amtliche Kartenbild muss dem bestehenden ALKIS-Schalter folgen.'
);
assert.match(
  layersSource,
  /'raster-opacity': aerialVisible \? \.62 : 1/,
  'Mit Luftbild muss das amtliche Kartenbild durchsichtig werden.'
);
assert.match(layersSource, /setSourceMetadata\(metadata\)/);
assert.match(sourcesSource, /layerController\.setSourceMetadata\?\.\(data\)/);
assert.match(sourcesSource, /cadastre_raster\?\.attribution/);

assert.match(mainSource, /KATASTER_WMS_CONFIGS = \{/);
assert.match(mainSource, /"sachsen-anhalt": \{/);
assert.match(mainSource, /"bayern": \{/);
assert.match(mainSource, /\/katasterbild\/\{state_slug\}\/\{z\}\/\{x\}\/\{y\}\.png/);
assert.match(mainSource, /def _cadastre_rendering_capability/);

console.log('cadastre-hybrid-layer-tests=ok');
