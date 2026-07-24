import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { welcomeHoverCandidate } from '../live-viewer/viewer-app/selection.js';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const layerSource = readFileSync(new URL('../live-viewer/viewer-app/layers.js', import.meta.url), 'utf8');
const selectionSource = readFileSync(new URL('../live-viewer/viewer-app/selection.js', import.meta.url), 'utf8');

const parcel = {
  id: 'parcel-promoted-id',
  layer: { id: 'welcome-hover-parcel-hit' },
  properties: { gml_id: 'parcel-1' }
};
const building = {
  id: 'building-promoted-id',
  layer: { id: 'welcome-hover-building-hit' },
  properties: { gml_id: 'building-1' }
};

assert.deepEqual(welcomeHoverCandidate([parcel]), {
  id: 'parcel-1',
  key: 'parcel:parcel-1',
  kind: 'parcel',
  sourceLayer: 'surfaces'
});
assert.deepEqual(welcomeHoverCandidate([parcel, building]), {
  id: 'building-1',
  key: 'building:building-1',
  kind: 'building',
  sourceLayer: 'building_fills'
}, 'Ein Gebäude muss gegenüber dem darunterliegenden Flurstück Vorrang haben.');
assert.deepEqual(welcomeHoverCandidate([{
  id: 'fallback-id',
  layer: { id: 'welcome-hover-building-hit' },
  properties: {}
}]), {
  id: 'fallback-id',
  key: 'building:fallback-id',
  kind: 'building',
  sourceLayer: 'building_fills'
});
assert.equal(welcomeHoverCandidate([{
  layer: { id: 'welcome-hover-parcel-hit' },
  properties: {}
}]), null);

assert.match(layerSource, /promoteId: \{ surfaces: 'gml_id', building_fills: 'gml_id' \}/);
for (const id of [
  'welcome-hover-parcel-hit',
  'welcome-hover-parcel-line',
  'welcome-hover-building-hit',
  'welcome-hover-building-line'
]) {
  assert.match(layerSource, new RegExp(`id: '${id}'`));
}
assert.match(layerSource, /filter: \['==', \['get', 'theme_index'\], 0\]/);
assert.match(layerSource, /filter: \['!=', \['get', 'render_fill_role'\], 'underground'\]/);
assert.match(layerSource, /'fill-opacity': \.001/);
assert.match(layerSource, /'line-color': '#c92f26', 'line-width': 4, 'line-dasharray': \[2\.5, 1\.35\]/);
assert.match(layerSource, /'line-color': '#c92f26', 'line-width': 4\.6, 'line-opacity'/);
assert.ok(
  layerSource.indexOf("id: 'welcome-hover-parcel-hit'") > layerSource.indexOf("id: 'alkis-symbols'"),
  'Der rote Hover muss oberhalb der normalen ALKIS-Darstellung liegen.'
);

assert.match(selectionSource, /\(hover: hover\) and \(pointer: fine\)/);
assert.match(selectionSource, /window\.requestAnimationFrame\(renderWelcomeHover\)/);
assert.match(selectionSource, /map\.queryRenderedFeatures\(\[point\.x, point\.y\], \{ layers \}\)/);
assert.doesNotMatch(selectionSource, /map\.queryRenderedFeatures\(point, \{ layers \}\)/);
assert.match(selectionSource, /map\.getZoom\(\) <= 17/);
assert.match(selectionSource, /map\.on\('dragstart', clearWelcomeHover\)/);
assert.match(selectionSource, /map\.on\('zoomstart', clearWelcomeFeature\)/);
assert.match(selectionSource, /welcomeOverlayRects/);
assert.match(selectionSource, /querySelectorAll\('\.welcome-page \[data-welcome-blocker\]'\)/);
assert.match(selectionSource, /frameRect\.width \/ container\.clientWidth/);
assert.match(selectionSource, /Date\.now\(\) - welcomeOverlayRectsAt > 160/);
assert.match(selectionSource, /onWelcomePointer\(\{ x: point\.x, y: point\.y \}\)/);
assert.match(selectionSource, /addEventListener\?\.\('mouseleave', clearWelcomeHover/);
assert.match(selectionSource, /addEventListener\?\.\('pointerleave', clearWelcomeHover/);
assert.match(selectionSource, /window\.addEventListener\('blur', clearWelcomeHover/);
assert.match(selectionSource, /map\.setFeatureState\([\s\S]*welcomeHover: enabled/);
assert.doesNotMatch(selectionSource, /onWelcomeSelection/);
assert.doesNotMatch(selectionSource, /const welcomeClick/);
assert.doesNotMatch(selectionSource, /candidate \? 'pointer'/);

const hoverSection = selectionSource.slice(
  selectionSource.indexOf('function renderWelcomeHover'),
  selectionSource.indexOf('function updateSources')
);
assert.doesNotMatch(hoverSection, /\bapi\./, 'Hover darf keine Serveranfrage auslösen.');
assert.doesNotMatch(hoverSection, /\bfetch\(/, 'Hover darf keine Netzwerkanfrage auslösen.');

assert.match(appSource, /\.\/layers\.js\?v=20260724-europe2/);
assert.match(appSource, /\.\/selection\.js\?v=20260723-table2/);
assert.match(appSource, /isWelcomeMode: \(\) => shellMode === 'welcome'/);
assert.match(appSource, /openkataster:welcome-pointer/);
assert.match(appSource, /parentOrigin = window\.location\.origin/);
assert.doesNotMatch(appSource, /openkataster:request-planner/);
assert.match(appSource, /message\.type === 'openkataster:clear-welcome-hover'/);
assert.match(appSource, /selection\.clearWelcomeHover\(\)/);

console.log('welcome-hover-tests=ok');
