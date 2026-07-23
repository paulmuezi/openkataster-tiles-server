import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { formatMeasurementCoordinate } from '../live-viewer/viewer-app/measure-format.mjs';

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
assert.match(mobileCss, /--mobile-measure-bar-height: 117px;/);
assert.match(mobileCss, /--mobile-measure-lock-row-height: 34px;/);
assert.match(mobileCss, /\.planner-app\[data-measure-panel-locked="true"\] \{ --mobile-measure-bar-height: calc\(117px \+ var\(--mobile-measure-lock-row-height\)\); \}/);
assert.match(mobileCss, /\.measure-panel \{[^}]*bottom: 0;[^}]*padding: 14px 16px calc\(14px \+ env\(safe-area-inset-bottom, 0px\)\);/);
assert.match(mobileCss, /border-top: 1px solid rgba\(89,97,108,\.28\);/);
assert.match(mobileCss, /box-shadow: 0 -7px 22px rgba\(24,30,39,\.14\);/);
assert.match(mobileCss, /\.measure-grid \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: 8px 18px; \}/);
assert.match(mobileCss, /\.measure-cell \{ grid-template-columns: 18px minmax\(0,1fr\) max-content; gap: 8px; min-height: 24px; \}/);
assert.match(mobileCss, /\.measure-cell span:not\(\.measure-symbol\) \{ font-size: 13px; line-height: 1\.25; \}/);
assert.match(mobileCss, /\.measure-cell strong \{ font-size: 14px; font-weight: 600; line-height: 1\.2; \}/);
assert.match(mobileCss, /\.measure-symbol, \.measure-symbol svg \{ width: 16px; height: 16px; \}/);
assert.match(mobileCss, /data-mobile-label\] \{ font-size: 0; \}/);
assert.match(mobileCss, /data-mobile-label\]::after \{ content: attr\(data-mobile-label\); font-size: 13px;/);
assert.match(mobileCss, /\.measure-pro-lock \{ min-height: 40px;[^}]*margin: -14px -16px 2px;[^}]*display: flex;[^}]*flex-wrap: nowrap;[^}]*gap: 4px 10px;/);
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
assert.match(measureSource, /setAttribute\('data-measure-panel-locked', pro \? 'false' : 'true'\)/);
assert.equal((indexSource.match(/class="measure-cell"/g) || []).length, 6, 'Alle sechs Messwertarten müssen in der Leiste bleiben.');
assert.equal((indexSource.match(/class="measure-symbol" aria-hidden="true"><svg/g) || []).length, 6, 'Jeder Messwert benötigt ein eigenes SVG-Symbol.');
const measurePanelStart = indexSource.indexOf('<section id="measurePanel"');
const measurePanelSource = indexSource.slice(measurePanelStart, indexSource.indexOf('</section>', measurePanelStart));
assert.ok(measurePanelSource.indexOf('id="measureLocked"') < measurePanelSource.indexOf('id="measureValues"'), 'Der Pro-Hinweis muss oberhalb der Messwertliste stehen.');
const measureValuesSource = measurePanelSource.slice(measurePanelSource.indexOf('id="measureValues"'));
const orderedIds = ['measureDistance', 'measureAngle', 'measureLongitude', 'measureLatitude', 'measureCumulative', 'measureArea'];
for (let index = 1; index < orderedIds.length; index += 1) {
  assert.ok(
    measureValuesSource.indexOf(`id="${orderedIds[index - 1]}"`) < measureValuesSource.indexOf(`id="${orderedIds[index]}"`),
    `${orderedIds[index - 1]} muss vor ${orderedIds[index]} stehen.`
  );
}
assert.match(indexSource, /id="measureLongitude">–<\/strong>/);
assert.match(indexSource, /id="measureLatitude">–<\/strong>/);
assert.match(indexSource, /title="WGS 84 \(EPSG:4326\)" data-mobile-label="Lon">Längengrad/);
assert.match(indexSource, /title="WGS 84 \(EPSG:4326\)" data-mobile-label="Lat">Breitengrad/);
assert.match(indexSource, /data-mobile-label="Gesamt">Kumulierter Abstand/);
assert.match(indexSource, /class="measure-symbol-total"[^>]*><path d="M2\.5 5h4M4\.5 3v4M7 12\.5 13 6\.5M9\.5 6\.5H13V10"/);
assert.match(indexSource, /class="measure-symbol-area"[^>]*><path d="m2\.5 6\.5 4-4M2\.5 11l8\.5-8\.5M5 13\.5 13\.5 5M9\.5 13\.5l4-4"/);
assert.match(desktopCss, /\.measure-symbol svg\.measure-symbol-total \{ stroke-width: 1\.55; \}/);
assert.match(desktopCss, /\.measure-symbol svg\.measure-symbol-area \{ stroke-width: 1\.65; \}/);
assert.match(indexSource, /<a class="measure-upgrade-link" href="\/pro" target="_top">Pro freischalten<\/a>/);
assert.match(measureSource, /measureValues\.hidden = false;/);
assert.match(measureSource, /measureValues\.setAttribute\('aria-hidden', pro \? 'false' : 'true'\)/);
assert.match(measureSource, /measurePanel\.dataset\.locked = pro \? 'false' : 'true';/);
assert.match(measureSource, /measureLocked\.hidden = pro;/);
assert.match(measureSource, /const pro = state\.access\.ready && state\.access\.pro;/);
assert.match(measureSource, /if \(\['access', 'access-loading'\]\.includes\(reason\)\) \{\s*render\(\);/);
assert.match(measureSource, /distance: '12,4 m'/);
assert.match(measureSource, /longitude: '9,812345° E'/);
assert.match(desktopCss, /\.measure-panel\[data-locked="true"\] \.measure-cell strong \{[^}]*filter: blur\(3\.4px\);/);
assert.match(desktopCss, /\.measure-pro-lock \{[^}]*margin: -7px -9px 0;[^}]*border-bottom: 1px solid var\(--ok-border-soft\);[^}]*background: rgba\(250,249,247,\.82\);/);
assert.doesNotMatch(desktopCss, /\.measure-pro-lock \{[^}]*position: absolute;/, 'Der Hinweis darf die Messwerte nicht mehr überlagern.');
assert.match(measureSource, /\.\/measure-format\.mjs\?v=20260717-measure-icons2/);
assert.match(appSource, /'measureAngleLabel','measureAngle','measureLongitude','measureLatitude','measureCumulative'/);
assert.doesNotMatch(measureSource, /measure-v2-points|properties: \{ kind: 'point' \}/, 'Gesetzte Messpunkte dürfen keine permanenten Kreis-Features erzeugen.');
assert.match(measureSource, /id: 'measure-snap-v2'/, 'Der temporäre orange Fangkreis muss erhalten bleiben.');
assert.equal((measureSource.match(/setSnapIndicator\(candidate\.snapped \? (?:candidate\.coordinate|draft) : null\)/g) || []).length, 2, 'Orange wird ausschließlich bei tatsächlichem Einrasten gezeigt.');
assert.equal(formatMeasurementCoordinate(9.84841, 'lon'), '9,848410° E');
assert.equal(formatMeasurementCoordinate(52.32984, 'lat'), '52,329840° N');
assert.equal(formatMeasurementCoordinate(-0.1276, 'lon'), '0,127600° W');
assert.equal(formatMeasurementCoordinate(-33.8688, 'lat'), '33,868800° S');
const stylesVersion = assetVersion(indexSource, 'styles.css');
const appVersion = assetVersion(indexSource, 'app.js');
assetVersion(appSource, 'measure.js');
assert.equal(
  stylesVersion,
  '20260723-unified1',
  'Die Österreich-Viewer-Stile müssen mit dem deployten CSS-Cache-Key geladen werden.'
);
assert.equal(
  appVersion,
  '20260723-unified4',
  'Der App-Einstieg muss den Österreich-Viewer-Stand invalidieren.'
);
console.log('mobile-measure-bar-tests=ok');
