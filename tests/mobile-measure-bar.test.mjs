import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');
const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const measureSource = readFileSync(new URL('../live-viewer/viewer-app/measure.js', import.meta.url), 'utf8');

function assetVersion(source, asset) {
  const escaped = asset.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`${escaped}\\?v=([A-Za-z0-9._-]+)`));
  assert.ok(match, `${asset} muss mit einem Cache-Buster referenziert werden.`);
  return match[1];
}

const mobileStart = stylesSource.indexOf('@media (max-width: 760px)');
const reducedMotionStart = stylesSource.indexOf('@media (prefers-reduced-motion:', mobileStart);
assert.notEqual(mobileStart, -1, 'Mobile CSS fehlt.');
assert.notEqual(reducedMotionStart, -1, 'Das Ende des Mobile-CSS ist nicht auffindbar.');
const desktopCss = stylesSource.slice(0, mobileStart);
const mobileCss = stylesSource.slice(mobileStart, reducedMotionStart);

assert.match(
  desktopCss,
  /\.measure-panel \{[^}]*left: 66px;[^}]*top: 126px;[^}]*width: 226px;[^}]*padding: 7px 9px 8px;/,
  'Die Desktop-Messanzeige darf durch die Mobile-Anpassung nicht verändert werden.'
);
assert.match(desktopCss, /\.measure-panel \{[^}]*pointer-events: none;/, 'Das mitbewegte Desktop-Panel muss nicht-interaktiv bleiben.');
assert.match(desktopCss, /\.measure-upgrade-link \{ display: none; \}/, 'Desktop darf den Upgrade-Link nicht anzeigen.');
assert.match(mobileCss, /--mobile-measure-bar-height: 85px;/);
assert.match(mobileCss, /\.measure-panel \{[^}]*bottom: 0;[^}]*padding: 14px 16px calc\(14px \+ env\(safe-area-inset-bottom, 0px\)\);/);
assert.match(mobileCss, /border-top: 1px solid rgba\(89,97,108,\.28\);/);
assert.match(mobileCss, /box-shadow: 0 -7px 22px rgba\(24,30,39,\.14\);/);
assert.match(mobileCss, /\.measure-grid \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: 8px 18px; \}/);
assert.match(mobileCss, /\.measure-cell \{ grid-template-columns: 18px minmax\(0,1fr\) max-content; gap: 8px; min-height: 24px; \}/);
assert.match(mobileCss, /\.measure-cell span:not\(\.measure-symbol\) \{ font-size: 13px; line-height: 1\.25; \}/);
assert.match(mobileCss, /\.measure-cell strong \{ font-size: 14px; font-weight: 600; line-height: 1\.2; \}/);
assert.match(mobileCss, /\.measure-symbol \{ font-size: 14px; \}/);
assert.match(mobileCss, /\.measure-pro-lock \{ min-height: 56px;[^}]*display: flex;[^}]*flex-wrap: wrap;[^}]*gap: 4px 10px;/);
assert.match(mobileCss, /\.measure-upgrade-link \{ min-height: 32px;[^}]*display: inline-flex;[^}]*pointer-events: auto;[^}]*touch-action: manipulation;/);

for (const selector of ['maplibregl-ctrl-bottom-left', 'zoom-badge', 'source-control', 'brand-mark']) {
  assert.match(
    mobileCss,
    new RegExp(`data-measure-panel-open="true"[^}]*\\.${selector}[^}]*bottom: calc\\(var\\(--mobile-measure-bar-height\\)[^}]*env\\(safe-area-inset-bottom, 0px\\)\\)`),
    `${selector} muss oberhalb der tatsächlichen Messleistenhöhe und Safe Area bleiben.`
  );
}

assert.match(measureSource, /measurePanel\.hidden = !active \|\| !points\.length;/);
assert.match(measureSource, /setAttribute\('data-measure-panel-open', measurePanel\.hidden \? 'false' : 'true'\)/);
assert.equal((indexSource.match(/class="measure-cell"/g) || []).length, 4, 'Alle vier Messwertarten müssen in der Leiste bleiben.');
assert.match(indexSource, /<a class="measure-upgrade-link" href="\/pro" target="_top">Pro freischalten<\/a>/);
assert.match(measureSource, /measureValues\.hidden = !pro;/);
assert.match(measureSource, /measureLocked\.hidden = pro;/);
const stylesVersion = assetVersion(indexSource, 'styles.css');
const appVersion = assetVersion(indexSource, 'app.js');
assetVersion(appSource, 'measure.js');
assert.equal(
  stylesVersion,
  appVersion,
  'CSS und App-Einstieg müssen mit demselben Viewer-Release invalidiert werden.'
);
console.log('mobile-measure-bar-tests=ok');
