import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appSource = readFileSync(new URL('../live-viewer/viewer-app/app.js', import.meta.url), 'utf8');
const indexSource = readFileSync(new URL('../live-viewer/viewer-app/index.html', import.meta.url), 'utf8');
const measureSource = readFileSync(new URL('../live-viewer/viewer-app/measure.js', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');

const measureNotice = indexSource.match(/<div id="measureLocked"[^>]*>.*?<\/div>/)?.[0] || '';
const measurePanel = indexSource.slice(indexSource.indexOf('<section id="measurePanel"'), indexSource.indexOf('</section>', indexSource.indexOf('<section id="measurePanel"')));
const exportNotice = indexSource.match(/<div id="exportLocked"[^>]*>[\s\S]*?<\/div>/)?.[0] || '';
assert.match(measureNotice, /<span>Diese Funktion ist im Pro-Plan verfügbar\.<\/span>/);
assert.match(measureNotice, /<a class="measure-upgrade-link" href="\/pro" target="_top">Pro freischalten<\/a>/);
assert.doesNotMatch(measureNotice, /Pro buchen/);
assert.ok(measurePanel.indexOf('id="measureLocked"') < measurePanel.indexOf('id="measureValues"'), 'Der Messtool-Hinweis muss vor der Werteliste stehen.');
assert.match(measureSource, /measureValues\.hidden = false;/, 'Free muss die wertähnlichen, geblurrten Platzhalter sehen.');
assert.match(measureSource, /measureValues\.setAttribute\('aria-hidden', pro \? 'false' : 'true'\)/, 'Screenreader dürfen Free-Platzhalter nicht als echte Messwerte vorlesen.');
assert.match(measureSource, /if \(!pro\) showLockedMetrics\(\);/, 'Free muss echte oder alte Pro-Werte sofort überschreiben.');
assert.match(measureSource, /measureLocked\.hidden = pro;/);
assert.match(exportNotice, /<span>Karte exportieren ist im Pro-Plan verfügbar\.<\/span>/);
assert.match(exportNotice, /<a class="primary-action" href="\/pro" target="_top">Pro freischalten<\/a>/);
assert.doesNotMatch(exportNotice, />Pro buchen</);

assert.doesNotMatch(stylesSource, /\.selection-pro-lock\b/, 'Das alte Floating-Panel darf nicht mehr gestylt werden.');
assert.match(stylesSource, /\.selection-data-table \.selection-pro-notice td \{ position: static;[^}]*text-align: center;/);
assert.match(stylesSource, /\.selection-pro-notice a \{ color: var\(--ok-orange\); font-weight: 500; text-decoration: none;/);
assert.match(stylesSource, /\.measure-upgrade-link \{ display: none; \}/, 'Der Link muss im Desktop-Panel verborgen bleiben.');
assert.match(stylesSource, /\.measure-panel\[data-locked="true"\] \.measure-cell strong \{[^}]*filter: blur\(3\.4px\);/, 'Nur die sicheren Messwert-Platzhalter werden geblurrt.');
assert.match(stylesSource, /\.measure-pro-lock \{[^}]*border-bottom: 1px solid var\(--ok-border-soft\);/);
assert.doesNotMatch(stylesSource, /\.measure-pro-lock \{[^}]*position: absolute;/);
assert.match(stylesSource, /@media \(max-width: 760px\)[\s\S]*\.measure-upgrade-link \{[^}]*display: inline-flex;[^}]*pointer-events: auto;/);
assert.doesNotMatch(stylesSource, /@media \(max-width: 760px\)[\s\S]*\.measure-panel \{[^}]*pointer-events: auto;/, 'Nur der Link, nicht die ganze Mobile-Leiste, darf Pointer-Events erhalten.');

assert.match(appSource, /\.\/measure\.js\?v=20260719-free-preview-controls1/);
assert.match(appSource, /\.\/selection\.js\?v=20260723-table2/);
assert.match(indexSource, /styles\.css\?v=20260723-unified1/);
assert.match(indexSource, /app\.js\?v=20260723-unified4/);

console.log('pro-plan-notice-tests=ok');
