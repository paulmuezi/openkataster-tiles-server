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

assert.match(mixedBuildingHtml, /Sondermerkmal<\/th><th class="selection-column-address" data-selection-column="address"[^>]*>Adressen<\/th><th class="selection-column-areas" data-selection-column="areas"[^>]*>Flächen<\/th>/, 'Dynamische Felder müssen vor den gemeinsam ausgerichteten Schluss-Spalten bleiben.');
assert.match(mixedBuildingHtml, /<span class="address-list"><span class="address-chip">Am Markt 1<\/span><span class="address-chip">Am Markt 2<\/span><\/span>/);
assert.match(mixedBuildingHtml, /Amtliche Fläche<\/span><span class="selection-area-value">100 m²<\/span><span class="selection-area-label">Geometrische Fläche<\/span><span class="selection-area-value">–<\/span>/, 'Bei amtlicher Fläche darf derselbe Eintrag nicht zusätzlich geometrisch ausgewiesen werden.');
assert.match(mixedBuildingHtml, /Amtliche Fläche<\/span><span class="selection-area-value">–<\/span><span class="selection-area-label">Geometrische Fläche<\/span><span class="selection-area-value">40 m²<\/span>/, 'Rein geometrische Gebäude müssen in der gemischten Auswahl ihren eigenen Wert behalten.');
assert.match(mixedBuildingHtml, /<tfoot><tr><td class="summary-label" colspan="\d+">Summe<\/td><td class="summary-value selection-column-areas" data-selection-column="areas">[\s\S]*100 m²[\s\S]*40 m²/);
assert.doesNotMatch(mixedBuildingHtml, /class="[^"]*strong/, 'Gerenderte Datenzellen dürfen keine Hervorhebungsklasse enthalten.');

const alignedSelectionHtml = renderSelection({
  buildings: [{ preview_id: 'aligned-building', grundflaeche_m2: 100, addresses: ['Gebäudeweg 1'] }],
  parcels: [{ preview_id: 'aligned-parcel', flurstueck: '1/2', amtliche_flaeche_m2: 500, addresses: ['Flurweg 2'] }]
});

function trailingSlots(html, kind) {
  const section = html.match(new RegExp(`<section class="selection-section" data-selection-kind="${kind}">[\\s\\S]*?<thead><tr>([\\s\\S]*?)<\\/tr>`));
  assert.ok(section, `Tabelle für ${kind} fehlt.`);
  return [...section[1].matchAll(/data-selection-column="([^"]+)"/g)].map((match) => match[1]);
}

assert.deepEqual(trailingSlots(alignedSelectionHtml, 'building'), ['address', 'areas']);
assert.deepEqual(trailingSlots(alignedSelectionHtml, 'parcel'), ['address', 'areas']);
assert.match(alignedSelectionHtml, /data-selection-kind="building"[\s\S]*?selection-area-label">Amtliche Fläche<\/span><span class="selection-area-value">100 m²/);
assert.match(alignedSelectionHtml, /data-selection-kind="parcel"[\s\S]*?selection-area-label">Amtliche Fläche<\/span><span class="selection-area-value">500 m²/);

const freeSelectionHtml = renderSelection({
  buildings: [{
    preview_id: 'preview-building',
    available_fields: ['gebaeudefunktion_text', 'grundflaeche_m2']
  }, {
    preview_id: 'preview-building-2',
    available_fields: ['gebaeudefunktion_text']
  }],
  parcels: [{
    preview_id: 'preview-parcel',
    available_fields: ['flurstueck', 'amtliche_flaeche_m2']
  }, {
    preview_id: 'preview-parcel-2',
    available_fields: ['flurstueck']
  }]
}, false);

assert.equal((freeSelectionHtml.match(/Gebäudeinfos sind im Pro-Plan verfügbar\./g) || []).length, 1);
assert.equal((freeSelectionHtml.match(/Flurstücksinfos sind im Pro-Plan verfügbar\./g) || []).length, 1);
assert.equal((freeSelectionHtml.match(/>Pro freischalten<\/a>/g) || []).length, 2);
assert.doesNotMatch(freeSelectionHtml, /Diese Tabelle ist im Pro-Plan verfügbar\./);
for (const kind of ['building', 'parcel']) {
  const sectionStart = freeSelectionHtml.indexOf(`<section class="selection-section" data-selection-kind="${kind}">`);
  const sectionEnd = freeSelectionHtml.indexOf('</section>', sectionStart);
  assert.notEqual(sectionStart, -1, `Hinweis für ${kind} fehlt.`);
  const section = freeSelectionHtml.slice(sectionStart, sectionEnd);
  assert.match(section, /<div class="selection-pro-notice" role="note">/);
  assert.doesNotMatch(
    section,
    /<table\b|<thead\b|<tbody\b|<tr\b|locked-cell|selection-item-remove|data-selection-remove-key/,
    `Free darf für ${kind} keine Tabelle oder ausgewählten Objektzeilen rendern.`
  );
}
assert.doesNotMatch(freeSelectionHtml, /selection-pro-lock|Pro buchen/, 'Die alte überlagernde Pro-Fläche darf nicht mehr gerendert werden.');

const selectionSource = readFileSync(new URL('../live-viewer/viewer-app/selection.js', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../live-viewer/viewer-app/styles.css', import.meta.url), 'utf8');
const buildingBlock = selectionSource.slice(selectionSource.indexOf('function buildingTable'), selectionSource.indexOf('function parcelTable'));
const parcelBlock = selectionSource.slice(selectionSource.indexOf('function parcelTable'), selectionSource.indexOf('async function selectAt'));

assert.ok(buildingBlock.indexOf("label: 'Adressen'") < buildingBlock.indexOf('areaColumn(['));
assert.ok(buildingBlock.indexOf("label: 'Geschossfläche'") < buildingBlock.indexOf("label: 'Amtliche Fläche'"));
assert.ok(buildingBlock.indexOf("label: 'Amtliche Fläche'") < buildingBlock.indexOf("label: 'Geometrische Fläche'"));
assert.ok(parcelBlock.indexOf("label: 'Entstehung'") < parcelBlock.indexOf("label: 'Adressen'"));
assert.ok(parcelBlock.indexOf("label: 'Adressen'") < parcelBlock.indexOf('areaColumn(['));
assert.doesNotMatch(selectionSource, /strong:\s*true/, 'Datenzellen dürfen nicht zufällig fett markiert werden.');
assert.match(selectionSource, /colspan="\$\{firstSumIndex(?: \+ 1)?\}"/, 'Die Summenbezeichnung muss direkt an den Flächenblock anschließen.');
assert.match(stylesSource, /grid-template-columns: minmax\(100%, max-content\)/, 'Beide Tabellen müssen dieselbe Gesamtbreite teilen.');
assert.match(stylesSource, /selection-column-address[^}]*width: 220px[^}]*min-width: 220px[^}]*max-width: 220px/, 'Adressspalten müssen dieselbe Ankerbreite haben.');
assert.match(stylesSource, /selection-column-areas[^}]*width: 250px[^}]*min-width: 250px[^}]*max-width: 250px/, 'Flächenspalten müssen dieselbe Ankerbreite haben.');
assert.match(stylesSource, /selection-area-value \{ text-align: right;/, 'Flächenwerte müssen rechtsbündig sein.');

console.log('selection-table-layout-tests=ok');
