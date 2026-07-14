import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildingAreaVisibility, createSelectionController, selectionAddressLabels } from '../live-viewer/viewer-app/selection.js';

assert.deepEqual(
  buildingAreaVisibility([
    { grundflaeche_m2: 100, geometrische_flaeche_m2: 99.5 },
    { amtliche_flaeche_m2: 50, geometrische_flaeche_m2: 49.8 }
  ]),
  { showOfficial: true, showGeometric: false },
  'Bei vollständig amtlichen Gebäudeflächen darf die geometrische Spalte nicht doppelt erscheinen.'
);

assert.deepEqual(
  buildingAreaVisibility([
    { grundflaeche_m2: 100, geometrische_flaeche_m2: 99.5 },
    { geometrische_flaeche_m2: 40 }
  ]),
  { showOfficial: true, showGeometric: true },
  'Eine gemischte Gebäudeauswahl muss beide Flächenspalten zeigen.'
);

assert.deepEqual(
  buildingAreaVisibility([{ geometrische_flaeche_m2: 40 }]),
  { showOfficial: false, showGeometric: true },
  'Ohne amtliche Gebäudegrundfläche darf nur die geometrische Spalte erscheinen.'
);

assert.deepEqual(
  buildingAreaVisibility([
    { available_fields: ['grundflaeche_m2', 'geometrische_flaeche_m2'] },
    { available_fields: ['geometrische_flaeche_m2'] }
  ], { preview: true }),
  { showOfficial: true, showGeometric: true },
  'Die Free-Vorschau muss dieselbe Mischlogik aus den verfügbaren Feldern ableiten.'
);

assert.deepEqual(
  selectionAddressLabels({
    addresses: [
      'Am Markt 1',
      { label: 'Am Markt 1' },
      { street: 'Nebenstraße', house_number: '2', post_code: '12345', city: 'Musterstadt' }
    ]
  }),
  ['Am Markt 1', 'Nebenstraße 2, 12345 Musterstadt'],
  'Adressen müssen normalisiert, dedupliziert und einzeln ausgebbar sein.'
);

function renderSelection(selection, pro = true) {
  const state = {
    access: { pro },
    activeTool: 'select',
    layout: { tableOpen: true },
    selection: { ...selection, loading: false }
  };
  const selectionContent = {
    innerHTML: '',
    addEventListener() {},
    contains() { return false; }
  };
  const controller = createSelectionController({
    map: {
      on() {},
      getSource() { return null; }
    },
    api: {},
    store: {
      getState() { return state; },
      subscribe() {}
    },
    layout: {
      isMobile() { return false; },
      setTable() {}
    },
    elements: {
      selectionContent,
      selectionCount: { textContent: '' },
      selectTool: { classList: { add() {}, remove() {} } },
      selectionDock: { classList: { toggle() {} } }
    }
  });
  controller.render(state);
  return selectionContent.innerHTML;
}

const mixedBuildingHtml = renderSelection({
  parcels: [],
  buildings: [
    { preview_id: 'building-official', gebaeudefunktion_text: 'Wohngebäude', sondermerkmal: 'A', grundflaeche_m2: 100, geometrische_flaeche_m2: 99.5, addresses: ['Am Markt 1', 'Am Markt 2'] },
    { preview_id: 'building-geometric', gebaeudefunktion_text: 'Nebengebäude', geometrische_flaeche_m2: 40 }
  ]
});

assert.match(mixedBuildingHtml, /<th class="compact numeric"[^>]*>Amtliche Fläche<\/th><th class="compact numeric"[^>]*>Geometrische Fläche<\/th>/);
assert.match(mixedBuildingHtml, /Sondermerkmal<\/th><th class="compact numeric"[^>]*>Amtliche Fläche<\/th><th class="compact numeric"[^>]*>Geometrische Fläche<\/th><\/tr><\/thead>/, 'Dynamische Felder dürfen den rechtsbündigen Flächenblock nicht verdrängen.');
assert.match(mixedBuildingHtml, /<span class="address-list"><span class="address-chip">Am Markt 1<\/span><span class="address-chip">Am Markt 2<\/span><\/span>/);
assert.match(mixedBuildingHtml, /<td class="compact numeric">100 m²<\/td><td class="compact numeric">–<\/td>/, 'Bei amtlicher Fläche darf derselbe Eintrag nicht zusätzlich geometrisch ausgewiesen werden.');
assert.match(mixedBuildingHtml, /<td class="compact numeric">–<\/td><td class="compact numeric">40 m²<\/td>/, 'Rein geometrische Gebäude müssen in der gemischten Auswahl ihre eigene Spalte behalten.');
assert.match(mixedBuildingHtml, /<tfoot><tr><td class="summary-label" colspan="\d+">Summe<\/td>/);
assert.doesNotMatch(mixedBuildingHtml, /class="[^"]*strong/, 'Gerenderte Datenzellen dürfen keine Hervorhebungsklasse enthalten.');

const freeSelectionHtml = renderSelection({
  buildings: [{
    preview_id: 'preview-building',
    available_fields: ['gebaeudefunktion_text', 'grundflaeche_m2']
  }],
  parcels: [{
    preview_id: 'preview-parcel',
    available_fields: ['flurstueck', 'amtliche_flaeche_m2']
  }]
}, false);

assert.equal((freeSelectionHtml.match(/Diese Tabelle ist im Pro-Plan verfügbar\./g) || []).length, 1, 'Der ruhige Pro-Hinweis darf bei mehreren Tabellen nur einmal erscheinen.');
assert.match(freeSelectionHtml, /<tr class="selection-pro-notice"><td colspan="\d+"><span class="selection-pro-notice-copy"><span>Diese Tabelle ist im Pro-Plan verfügbar\.<\/span><a href="\/pro" target="_top">Pro freischalten<\/a><\/span><\/td><\/tr>/);
assert.doesNotMatch(freeSelectionHtml, /selection-pro-lock|Pro buchen/, 'Die alte überlagernde Pro-Fläche darf nicht mehr gerendert werden.');

const selectionSource = readFileSync(new URL('../live-viewer/viewer-app/selection.js', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');
const buildingBlock = selectionSource.slice(selectionSource.indexOf('function buildingTable'), selectionSource.indexOf('function parcelTable'));
const parcelBlock = selectionSource.slice(selectionSource.indexOf('function parcelTable'), selectionSource.indexOf('async function selectAt'));

assert.ok(buildingBlock.indexOf("label: 'Adressen'") < buildingBlock.indexOf("label: 'Geschossfläche'"));
assert.ok(buildingBlock.indexOf("label: 'Geschossfläche'") < buildingBlock.indexOf("label: 'Amtliche Fläche'"));
assert.ok(buildingBlock.indexOf("label: 'Amtliche Fläche'") < buildingBlock.indexOf("label: 'Geometrische Fläche'"));
assert.ok(parcelBlock.indexOf("label: 'Entstehung'") < parcelBlock.indexOf("label: 'Amtliche Fläche'"));
assert.doesNotMatch(selectionSource, /strong:\s*true/, 'Datenzellen dürfen nicht zufällig fett markiert werden.');
assert.match(selectionSource, /colspan="\$\{firstSumIndex(?: \+ 1)?\}"/, 'Die Summenbezeichnung muss direkt an den Flächenblock anschließen.');
assert.match(stylesSource, /th\.numeric, \.selection-data-table td\.numeric \{ text-align: right;/, 'Flächenwerte müssen rechtsbündig sein.');

console.log('selection-table-layout-tests=ok');
