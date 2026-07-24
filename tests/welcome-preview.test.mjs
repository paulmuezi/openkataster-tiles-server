import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const mapSource = readFileSync(new URL('../live-viewer/viewer-app/map.js', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');

assert.match(indexSource, /dataset\.preview = okParams\.get\('preview'\) === '1' \? 'true' : 'false'/);
assert.match(indexSource, /dataset\.shellMode = okParams\.get\('welcome'\) === '1' \? 'welcome' : 'planner'/);
assert.match(indexSource, /styles\.css\?v=20260724-europe2/);
assert.match(indexSource, /app\.js\?v=20260724-europe6/);

// The old preview contract remains isolated and non-persistent.
assert.match(appSource, /const preview = params\.get\('preview'\) === '1'/);
assert.match(appSource, /const saved = preview \|\| onOfficeMode \? null : readPersistedState\(workspaceDataset\)/);
assert.match(appSource, /if \(!preview && !onOfficeMode\) \{[\s\S]*createPersistence\(\{[\s\S]*dataset: workspaceDataset,[\s\S]*exportWorkspace:/);

// Welcome is presentation-only and can switch without recreating the map.
assert.match(appSource, /let shellMode = params\.get\('welcome'\) === '1' \? 'welcome' : 'planner'/);
assert.match(appSource, /const welcomeDefaultView = \{ lng: 9\.84841, lat: 52\.32984, zoom: 16\.5 \}/);
assert.match(appSource, /savedView:[\s\S]*initialFocusDataset === 'oesterreich'[\s\S]*\|\| saved\?\.view[\s\S]*\|\| \(shellMode === 'welcome' \? welcomeDefaultView : null\)/);
assert.match(appSource, /function setShellMode\(mode\)/);
assert.match(appSource, /if \(mode === 'welcome'\) sources\.closePanel\(\)/);
assert.match(appSource, /message\.type === 'openkataster:set-shell-mode'/);
assert.match(appSource, /message\.type === 'openkataster:clear-welcome-hover'/);
assert.match(appSource, /selection\.clearWelcomeHover\(\)/);
assert.match(appSource, /openkataster:welcome-pointer/);
assert.match(appSource, /postToParent\('openkataster:shell-mode'/);
assert.doesNotMatch(appSource, /openkataster:request-planner/);
assert.match(appSource, /'set-shell-mode'/);
assert.match(appSource, /suspendedPlannerUi = \{ activeTool: state\.activeTool, layout: \{ \.\.\.state\.layout \} \}/);
assert.match(appSource, /activeTool: 'none'/);
assert.match(appSource, /sidebarOpen: false/);
assert.match(appSource, /tableOpen: false/);
assert.match(appSource, /const restoredTool = suspendedPlannerUi\.activeTool === 'export' \? 'export' : 'none'/);
assert.match(appSource, /activeTool: restoredTool/);
assert.match(appSource, /const restoreTable = !layout\.isMobile\(\) && suspendedPlannerUi\.layout\.tableOpen && selectionCount > 0/);
assert.doesNotMatch(appSource, /activeTool: suspendedPlannerUi\.activeTool/);
assert.match(appSource, /message\.type === 'openkataster:set-viewer-token'/);
assert.match(appSource, /api\.setToken\(message\.token\)/);
assert.match(appSource, /'set-viewer-token'/);
assert.match(appSource, /function hasCurrentParentContract\(message\)/);
assert.match(appSource, /message\.version === WORKSPACE_VERSION && message\.dataset === WORKSPACE_DATASET/);
assert.match(appSource, /if \(!hasCurrentParentContract\(message\)\) return;/);
assert.match(appSource, /mapLoaded = true/);
assert.match(appSource, /app\.dataset\.ready = 'true'/);
assert.match(appSource, /function publishShellMode[\s\S]*if \(!mapLoaded\) return;/);
assert.match(appSource, /const mapReady = map\.loaded\(\)[\s\S]*Promise\.resolve\(\)[\s\S]*map\.once\('load', resolve\)/);
assert.match(appSource, /selection\.setWelcomeMode\(mode === 'welcome'\)/);
assert.match(appSource, /app\.dataset\.shellTransitioning = 'true'/);
assert.match(appSource, /publishShellMode\(\{ settleLayout: true \}\)/);
assert.match(appSource, /if \(settleLayout\) window\.requestAnimationFrame\(acknowledge\)/);
assert.match(appSource, /if \(settleLayout\) app\.dataset\.shellTransitioning = 'false'/);
assert.match(appSource, /if \(!modeChanged\) \{[\s\S]*publishShellMode\(\);[\s\S]*return;/);
assert.match(mapSource, /app\?\.dataset\.shellTransitioning === 'true'/);
assert.match(stylesSource, /\.planner-app\[data-shell-transitioning="true"\],[\s\S]*\.map-workspace \{ transition: none !important; \}/);

for (const selector of [
  '.search-control',
  '.tool-stack',
  '.layer-control',
  '.zoom-badge',
  '.brand-mark',
  '.measure-panel',
  '.export-frame',
  '.selection-dock',
  '.mobile-export-backdrop',
  '.export-sidebar',
  '.notice-panel',
  '.maplibregl-ctrl-bottom-left'
]) {
  assert.match(stylesSource, new RegExp(`html\\[data-shell-mode="welcome"\\] \\${selector}`));
}
assert.match(stylesSource, /html\[data-preview="true"\] \.source-button,[\s\S]*html\[data-preview="true"\] \.source-panel \{ display: none !important; \}/);
assert.match(stylesSource, /html\[data-shell-mode="welcome"\] \.source-button,[\s\S]*html\[data-shell-mode="welcome"\] \.source-panel \{ display: none !important; \}/);
assert.match(stylesSource, /html\[data-preview="true"\] \.osm-attribution,[\s\S]*html\[data-preview="true"\] \.poi-search-marker \{ display: none !important; \}/);
assert.match(stylesSource, /html\[data-shell-mode="welcome"\] \.osm-attribution,[\s\S]*html\[data-shell-mode="welcome"\] \.poi-search-marker \{ display: none !important; \}/);
assert.doesNotMatch(stylesSource, /html\[data-shell-mode="welcome"\] \.source-control,/);
assert.match(stylesSource, /--sidebar-width: 0px !important;/);
assert.match(stylesSource, /--table-height: 0px !important;/);

console.log('welcome-preview-tests=ok');
