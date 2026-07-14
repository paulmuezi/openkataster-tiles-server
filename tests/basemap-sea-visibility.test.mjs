import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const viewerRoot = new URL('../live-viewer/viewer-app/', import.meta.url);
const style = JSON.parse(readFileSync(new URL('bkg-style.json', viewerRoot), 'utf8'));
const mapSource = readFileSync(new URL('map.js', viewerRoot), 'utf8');
const appSource = readFileSync(new URL('app.js', viewerRoot), 'utf8');
const indexSource = readFileSync(new URL('index.html', viewerRoot), 'utf8');
const layersSource = readFileSync(new URL('layers.js', viewerRoot), 'utf8');
const layers = new Map(style.layers.map((layer) => [layer.id, layer]));

assert.equal(
  layers.get('background')?.paint?.['background-color'],
  '#ffffff',
  'Der globale Kartenhintergrund darf keine blaue Meeresfläche erzeugen.'
);
for (const id of ['Gewaesser_F_Meer', 'Gewaesser_L_Meer', 'Gewaesser_F_Fliessgewaesser', 'Gewaesser_F_See_Hafenbecken', 'Gewaesser_F_Watt']) {
  assert.notEqual(layers.get(id)?.layout?.visibility, 'none', `${id} muss sichtbar bleiben.`);
}

assert.match(
  layersSource,
  /\['==', \['get', 'thema'\], 'Gewässer'\], '#DCEFFF'/,
  'ALKIS-Gewässerflächen müssen unverändert gerendert werden.'
);
assert.match(mapSource, /bkg-style\.json\?v=20260715-no-world-blue1/);
assert.match(appSource, /\.\/map\.js\?v=20260715-no-world-blue1/);
assert.match(indexSource, /app\.js\?v=20260715-no-world-blue1/);

console.log('basemap-sea-visibility-tests=ok');
