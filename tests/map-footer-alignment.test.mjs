import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');

assert.match(stylesSource, /\.source-control \{[^}]*bottom: 2px;/, 'desktop source control sits two pixels above the map edge');
assert.match(stylesSource, /\.source-button \{[^}]*bottom: 0;[^}]*width: 18px;[^}]*height: 18px;/, 'the info button uses the shared source-control baseline');
assert.match(stylesSource, /\.brand-mark \{[^}]*bottom: 2px;[^}]*min-height: 18px;[^}]*align-items: center;/, 'brand and info button share an 18px desktop row');
assert.match(
  stylesSource,
  /@media \(max-width: 760px\)[\s\S]*\.source-control,\s*\.brand-mark \{ bottom: calc\(8px \+ env\(safe-area-inset-bottom, 0px\)\); \}/,
  'mobile source and brand use one shared safe-area baseline'
);
assert.match(
  stylesSource,
  /data-mobile-export-settings="false"\]\[data-table-open="false"\] \.source-control,\s*[\s\S]*data-mobile-export-settings="false"\]\[data-table-open="false"\] \.brand-mark \{ bottom: calc\(var\(--mobile-export-bar-height\) \+ 8px \+ env\(safe-area-inset-bottom, 0px\)\); \}/,
  'source and brand stay aligned above the mobile export bar'
);
assert.match(
  stylesSource,
  /data-measure-panel-open="true"\] \.source-control,\s*[\s\S]*data-measure-panel-open="true"\] \.brand-mark \{ bottom: calc\(var\(--mobile-measure-bar-height\) \+ 8px \+ env\(safe-area-inset-bottom, 0px\)\); \}/,
  'source and brand stay aligned above the mobile measure bar'
);
assert.match(indexSource, /styles\.css\?v=20260724-europe2/);
assert.match(indexSource, /app\.js\?v=20260724-europe4/);

console.log('map-footer-alignment-tests=ok');
