import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const viewerRoot = new URL('../live-viewer/viewer-app/', import.meta.url);
const style = JSON.parse(readFileSync(new URL('bkg-style.json', viewerRoot), 'utf8'));
const mapSource = readFileSync(new URL('map.js', viewerRoot), 'utf8');
const appSource = readFileSync(new URL('app.js', viewerRoot), 'utf8');
const indexSource = readFileSync(new URL('index.html', viewerRoot), 'utf8');
const layersSource = readFileSync(new URL('layers.js', viewerRoot), 'utf8');
const layers = new Map(style.layers.map((layer) => [layer.id, layer]));

function assetVersion(source, asset) {
  const escaped = asset.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`(?:^|/)${escaped}\\?v=([A-Za-z0-9._-]+)`));
  assert.ok(match, `${asset} muss mit einem Cache-Buster referenziert werden.`);
  return match[1];
}

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
const indexAppVersion = assetVersion(indexSource, 'app.js');
const appMapVersion = assetVersion(appSource, 'map.js');
const mapStyleVersion = assetVersion(mapSource, 'bkg-style.json');
assert.match(indexAppVersion, /^[A-Za-z0-9._-]+$/);
assert.equal(
  appMapVersion,
  mapStyleVersion,
  'Map-Modul und Basemap-Style müssen gemeinsam invalidiert werden.'
);

console.log('basemap-sea-visibility-tests=ok');
