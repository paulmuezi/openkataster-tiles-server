import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildingAreaVisibility, createSelectionController, parcelDisplayLocation, previewNoticeScrollOffset, selectionAddressLabels } from '../live-viewer/viewer-app/selection.js';

for (const scenario of [
  { scrollLeft: 0, expected: 0 },
  { scrollLeft: 180, expected: 180 },
  { scrollLeft: 470, expected: 470 },
  { scrollLeft: 940, expected: 940 },
  { scrollLeft: -35, expected: 0 },
  { scrollLeft: 1010, expected: 940 }
]) {
  const shift = previewNoticeScrollOffset({
    scrollLeft: scenario.scrollLeft,
    scrollWidth: 1440,
    clientWidth: 500
  });
  assert.equal(shift, scenario.expected, `Der mobile Pro-Hinweis braucht bei scrollLeft=${scenario.scrollLeft} einen sicheren Versatz.`);
  if (scenario.scrollLeft >= 0 && scenario.scrollLeft <= 940) {
    assert.equal(10 - scenario.scrollLeft + shift + 240, 250, 'Der Pro-Hinweis muss im 500 px breiten Ausschnitt zentriert bleiben.');
  }
}

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

assert.equal(
  parcelDisplayLocation({
    lage: 'Breiter Weg 1',
    addresses: [{ label: 'Breiter Weg 1, 39104 Magdeburg', street_house: 'Breiter Weg 1' }]
  }),
  '',
  'Eine bereits als Adresse dargestellte Lage darf nicht als zweite Spalte erscheinen.'
);
assert.equal(
  parcelDisplayLocation({ lage: 'X'.repeat(241), addresses: [] }),
  '',
  'Extreme rohe Lage-Listen dürfen auch aus alten Auswahlzuständen nicht gerendert werden.'
);
assert.equal(
  parcelDisplayLocation({ lage: 'Hinter dem Deich', addresses: [{ street_house: 'Dorfstraße 4' }] }),
  'Hinter dem Deich',
  'Eine kurze, eigenständige katasterliche Lageangabe muss erhalten bleiben.'
);

function renderSelection(selection, pro = true) {
  const state = {
    access: { ready: true, pro },
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

function sectionFor(html, kind) {
  const start = html.indexOf(`<section class="selection-section" data-selection-kind="${kind}">`);
  const end = html.indexOf('</section>', start);
  assert.notEqual(start, -1, `Tabelle für ${kind} fehlt.`);
  return html.slice(start, end);
}

function tablePart(section, tag) {
  const match = section.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`));
  assert.ok(match, `${tag} fehlt.`);
  return match[1];
}

function areaEntries(fragment) {
  return [...fragment.matchAll(/<span data-area-kind="([^"]+)">([^<]*)<\/span>/g)].map((match) => [match[1], match[2]]);
}

function areaRows(section) {
  return [...tablePart(section, 'tbody').matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((match) => areaEntries(match[1]));
}

const mixedBuildingHtml = renderSelection({
  parcels: [],
  buildings: [
    { preview_id: 'building-official', gebaeudefunktion_text: 'Wohngebäude', sondermerkmal: 'A', grundflaeche_m2: 100.4, geometrische_flaeche_m2: 99.5, addresses: ['Am Markt 1', 'Am Markt 2'] },
    { preview_id: 'building-geometric', gebaeudefunktion_text: 'Nebengebäude', geometrische_flaeche_m2: 40.6 }
  ]
});

const mixedBuildingSection = sectionFor(mixedBuildingHtml, 'building');
const mixedBuildingHead = tablePart(mixedBuildingSection, 'thead');
const mixedBuildingBody = tablePart(mixedBuildingSection, 'tbody');
const mixedBuildingFoot = tablePart(mixedBuildingSection, 'tfoot');
assert.match(mixedBuildingHead, /Sondermerkmal<\/th><th class="selection-column-address" data-selection-column="address"[^>]*>Adressen<\/th><th class="selection-column-areas" data-selection-column="areas"/, 'Dynamische Felder müssen vor den gemeinsam ausgerichteten Schluss-Spalten bleiben.');
assert.match(mixedBuildingHtml, /<span class="address-list"><span class="address-chip">Am Markt 1<\/span><span class="address-chip">Am Markt 2<\/span><\/span>/);
assert.deepEqual(areaEntries(mixedBuildingHead), [['official', 'Amtliche Fläche'], ['geometric', 'Geometrische Fläche']], 'Gemischte Flächenarten müssen ausschließlich als Spaltenköpfe erscheinen.');
assert.deepEqual(areaRows(mixedBuildingSection), [
  [['official', '100 m²'], ['geometric', '–']],
  [['official', '–'], ['geometric', '41 m²']]
], 'Jede Gebäudezeile muss nur ihre Werte in den passenden Flächenspalten ausgeben.');
assert.deepEqual(areaEntries(mixedBuildingFoot), [['official', '100 m²'], ['geometric', '41 m²']], 'Die Summenzeile muss je Flächenart nur gerundete Quadratmeterwerte ausgeben.');
assert.doesNotMatch(mixedBuildingBody + mixedBuildingFoot, /Amtliche Fläche|Geometrische Fläche|Geschossfläche/, 'Flächenarten dürfen nicht pro Eintrag wiederholt werden.');
assert.doesNotMatch(mixedBuildingHead, />Flächen<\/th>/, 'Der alte generische Spaltenkopf darf nicht mehr erscheinen.');
assert.match(mixedBuildingHtml, /<tfoot><tr><td class="summary-label" colspan="\d+">Summe<\/td><td class="summary-value selection-column-areas" data-selection-column="areas">[\s\S]*100 m²[\s\S]*41 m²/);
assert.doesNotMatch(mixedBuildingHtml, /class="[^"]*strong/, 'Gerenderte Datenzellen dürfen keine Hervorhebungsklasse enthalten.');

const officialBuildingSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [{ source_db: 'test', gml_id: 'official-only', grundflaeche_m2: 100.6, geometrische_flaeche_m2: 99.5 }]
}), 'building');
assert.deepEqual(areaEntries(tablePart(officialBuildingSection, 'thead')), [['official', 'Amtliche Fläche']]);
assert.deepEqual(areaRows(officialBuildingSection), [[['official', '101 m²']]], 'Amtliche Fläche muss gerundet werden und die geometrische Dublette unterdrücken.');
assert.doesNotMatch(officialBuildingSection, /Geometrische flaeche \(m²\)|99,5/, 'Ein ausgeblendetes geometrisches Rohfeld darf nicht als dynamische Thüringen-Spalte zurückkehren.');

const geometricBuildingSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [{ source_db: 'test', gml_id: 'geometric-only', geometrische_flaeche_m2: 40.6 }]
}), 'building');
assert.deepEqual(areaEntries(tablePart(geometricBuildingSection, 'thead')), [['geometric', 'Geometrische Fläche']]);
assert.deepEqual(areaRows(geometricBuildingSection), [[['geometric', '41 m²']]], 'Geometrische Flächen müssen als ganze Quadratmeter mit Einheit erscheinen.');

const bayernLod2HeightSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [{ source_db: 'bayern-lod2', gml_id: 'DEBY-height', objekthoehe_m: 38.5, geometrische_flaeche_m2: 140 }]
}), 'building');
assert.doesNotMatch(bayernLod2HeightSection, /Objekthöhe|38,5 m/, 'Die missverständliche LoD2-Objekthöhe darf bei reinen Bayern-LoD2-Auswahlen nicht erscheinen.');

const mixedSourceHeightSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [
    { source_db: 'bayern-lod2', gml_id: 'DEBY-height-mixed', objekthoehe_m: 38.5, geometrische_flaeche_m2: 140 },
    { source_db: 'alkis_test', gml_id: 'ALKIS-height-mixed', objekthoehe_m: 12.4, grundflaeche_m2: 80 }
  ]
}), 'building');
assert.match(tablePart(mixedSourceHeightSection, 'thead'), />Objekthöhe<\/th>/, 'Eine fachlich verfügbare Objekthöhe aus anderen Quellen muss die gemeinsame Spalte erhalten.');
assert.match(mixedSourceHeightSection, />12,4 m<\/td>/, 'Höhenangaben anderer Datenquellen müssen unverändert sichtbar bleiben.');
assert.doesNotMatch(mixedSourceHeightSection, /38,5 m/, 'Auch in gemischten Auswahlen darf die Bayern-LoD2-Höhe nicht erscheinen.');

const thuringiaOfficialBuildingSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [
    { source_db: 'alkis_thueringen_1', gml_id: 'DETHL51P000004BA', grundflaeche_m2: 130, geometrische_flaeche_m2: 129.75 },
    { source_db: 'alkis_thueringen_1', gml_id: 'DETHL51P000004BB', grundflaeche_m2: 62, geometrische_flaeche_m2: 101.1 }
  ]
}), 'building');
assert.deepEqual(areaEntries(tablePart(thuringiaOfficialBuildingSection, 'thead')), [['official', 'Amtliche Fläche']]);
assert.deepEqual(areaRows(thuringiaOfficialBuildingSection), [
  [['official', '130 m²']],
  [['official', '62 m²']]
]);
assert.doesNotMatch(
  thuringiaOfficialBuildingSection,
  /Geometrische flaeche \(m²\)|Geometrische Fläche|129,75|101,1/,
  'Sind bei allen Thüringer Gebäuden amtliche Flächen vorhanden, muss die geometrische Spalte vollständig verschwinden.'
);

const floorAreaSection = sectionFor(renderSelection({
  parcels: [],
  buildings: [
    { source_db: 'test', gml_id: 'floor-one', geschossflaeche_m2: 220, grundflaeche_m2: 100 },
    { source_db: 'test', gml_id: 'floor-two', grundflaeche_m2: 50 }
  ]
}), 'building');
assert.deepEqual(areaEntries(tablePart(floorAreaSection, 'thead')), [['floor', 'Geschossfläche'], ['official', 'Amtliche Fläche']]);
assert.deepEqual(areaRows(floorAreaSection), [
  [['floor', '220 m²'], ['official', '100 m²']],
  [['floor', '–'], ['official', '50 m²']]
]);
assert.doesNotMatch(tablePart(floorAreaSection, 'tbody') + tablePart(floorAreaSection, 'tfoot'), /Geschossfläche|Amtliche Fläche/);

const emptyBuildingNameHtml = renderSelection({
  parcels: [],
  buildings: [
    { source_db: 'test', gml_id: 'empty-building-name', gebaeudefunktion_text: 'Wohngebäude', name: [' ', '\u200b', '\ufeff'] }
  ]
});
assert.doesNotMatch(emptyBuildingNameHtml, />Name<\/th>/, 'Leere oder unsichtbare Namenswerte dürfen keine Namensspalte erzeugen.');

const mixedBuildingNameHtml = renderSelection({
  parcels: [],
  buildings: [
    { source_db: 'test', gml_id: 'blank-building-name', gebaeudefunktion_text: 'Wohngebäude', name: '   ' },
    { source_db: 'test', gml_id: 'real-building-name', gebaeudefunktion_text: 'Öffentliches Gebäude', name: 'Rathaus' }
  ]
});
assert.match(mixedBuildingNameHtml, />Name<\/th>/, 'Ein echter Name muss in einer gemischten Auswahl sichtbar bleiben.');
assert.match(mixedBuildingNameHtml, /<td>Wohngebäude<\/td><td class="selection-column-fill">–<\/td><td class="selection-column-address"/, 'Ein namenloses Gebäude muss in der gemeinsamen Namensspalte einen Gedankenstrich zeigen.');
assert.match(mixedBuildingNameHtml, /<td>Öffentliches Gebäude<\/td><td class="selection-column-fill">Rathaus<\/td><td class="selection-column-address"/, 'Ein echter Gebäudename muss unverändert ausgegeben werden.');

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
assert.deepEqual(areaEntries(tablePart(sectionFor(alignedSelectionHtml, 'building'), 'thead')), [['official', 'Amtliche Fläche']]);
assert.deepEqual(areaRows(sectionFor(alignedSelectionHtml, 'building')), [[['official', '100 m²']]]);
assert.deepEqual(areaEntries(tablePart(sectionFor(alignedSelectionHtml, 'parcel'), 'thead')), [['official', 'Amtliche Fläche']]);
assert.deepEqual(areaRows(sectionFor(alignedSelectionHtml, 'parcel')), [[['official', '500 m²']]]);

const truncatedAddressHtml = renderSelection({
  buildings: [],
  parcels: [{
    preview_id: 'parcel-truncated-addresses',
    flurstueck: '12/3',
    lage: 'Testweg 1',
    addresses: [{ label: 'Testweg 1/2/3/4/5, Teststadt', street_house: 'Testweg 1/2/3/4/5' }],
    address_relation_count: 159,
    address_relation_limit: 25,
    address_relations_truncated: true
  }]
});
const truncatedAddressSection = sectionFor(truncatedAddressHtml, 'parcel');
assert.match(truncatedAddressSection, /25 von 159 amtlichen Adresszuordnungen berücksichtigt/);
assert.match(truncatedAddressSection, /class="address-relation-note"/);
assert.doesNotMatch(truncatedAddressSection, /Address relation count|Address relation limit|Address relations truncated/);

const duplicateLocationSection = sectionFor(renderSelection({
  buildings: [],
  parcels: [{
    preview_id: 'parcel-duplicate-location',
    flurstueck: '8/4',
    lage: 'Breiter Weg 1',
    addresses: [{ label: 'Breiter Weg 1, 39104 Magdeburg', street_house: 'Breiter Weg 1' }]
  }]
}), 'parcel');
assert.doesNotMatch(tablePart(duplicateLocationSection, 'thead'), />Lage<\/th>/);

const longLocation = `EXTREME-LAGE-${'x'.repeat(260)}`;
const longLocationSection = sectionFor(renderSelection({
  buildings: [],
  parcels: [{ preview_id: 'parcel-long-location', flurstueck: '9/1', lage: longLocation }]
}), 'parcel');
assert.doesNotMatch(tablePart(longLocationSection, 'thead'), />Lage<\/th>/);
assert.doesNotMatch(longLocationSection, /EXTREME-LAGE/);

const usefulLocationSection = sectionFor(renderSelection({
  buildings: [],
  parcels: [{ preview_id: 'parcel-useful-location', flurstueck: '10/2', lage: 'Hinter dem Deich' }]
}), 'parcel');
assert.match(tablePart(usefulLocationSection, 'thead'), />Lage<\/th>/);
assert.match(usefulLocationSection, />Hinter dem Deich<\/td>/);

function assertCompactParcelIdentityBlock(html, mode) {
  const section = html.match(/<section class="selection-section" data-selection-kind="parcel">[\s\S]*?<thead><tr>([\s\S]*?)<\/tr>/);
  assert.ok(section, `Flurstückstabelle für ${mode} fehlt.`);
  const headers = [...section[1].matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)].map((match) => ({
    attributes: match[1],
    label: match[2].replace(/<[^>]+>/g, '').trim()
  }));
  const labels = headers.map((header) => header.label);
  const identityStart = labels.indexOf('Gem.-Schl.');
  assert.notEqual(identityStart, -1, `Gemarkungsschlüssel für ${mode} fehlt.`);
  assert.deepEqual(
    labels.slice(identityStart, identityStart + 4),
    ['Gem.-Schl.', 'Gemarkung', 'Flur', 'Flurstück'],
    `Die Flurstücks-Grunddaten müssen in ${mode} unmittelbar zusammenstehen.`
  );
  assert.deepEqual(
    labels.slice(-2),
    ['Adressen', 'Amtliche Fläche'],
    `Die gemeinsamen Adress- und Flächenspalten müssen in ${mode} am Tabellenende bleiben.`
  );
  for (const header of headers.slice(identityStart, identityStart + 4)) {
    assert.match(header.attributes, /class="[^"]*\bcompact\b[^"]*"/, `${header.label} muss in ${mode} kompakt bleiben.`);
  }
}

const proParcelIdentityHtml = renderSelection({
  buildings: [],
  parcels: [{
    preview_id: 'parcel-identity-pro',
    gemarkungsschluessel: '051234',
    gemarkung: 'Muster',
    gemarkungsnummer: '1234',
    flur: '7',
    flurstueck: '12/3',
    nutzung: 'Wohnbaufläche'
  }]
});
assertCompactParcelIdentityBlock(proParcelIdentityHtml, 'Pro');

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

const freeBuildingSection = sectionFor(freeSelectionHtml, 'building');
const freeParcelSection = sectionFor(freeSelectionHtml, 'parcel');
assert.match(freeBuildingSection, /<thead><tr>[\s\S]*>Gebäudefunktion<\/th>[\s\S]*data-selection-column="address"[\s\S]*>Adressen<\/th>[\s\S]*data-selection-column="areas"/);
assert.deepEqual(areaEntries(tablePart(freeBuildingSection, 'thead')), [['official', 'Amtliche Fläche']]);
assert.doesNotMatch(freeBuildingSection, />Name<\/th>/, 'Nicht verfügbare Gebäudespalten dürfen nicht erscheinen.');
assert.match(freeParcelSection, /<thead><tr>[\s\S]*>Flurstück<\/th>[\s\S]*data-selection-column="address"[\s\S]*>Adressen<\/th>[\s\S]*data-selection-column="areas"/);
assert.deepEqual(areaEntries(tablePart(freeParcelSection, 'thead')), [['official', 'Amtliche Fläche']]);
assert.doesNotMatch(freeParcelSection, />Flur<\/th>/, 'Nicht verfügbare Flurstücksspalten dürfen nicht erscheinen.');
for (const [kind, section, message] of [
  ['building', freeBuildingSection, 'Gebäudeinfos sind im Pro-Plan verfügbar.'],
  ['parcel', freeParcelSection, 'Flurstücksinfos sind im Pro-Plan verfügbar.']
]) {
  assert.equal((section.match(/<tr class="selection-locked-row">/g) || []).length, 2, `Free muss für ${kind} zwei sichere, geblurrte Vorschauzeilen rendern.`);
  assert.match(section, new RegExp(`<tr class="selection-pro-notice"><td colspan="4"><span class="selection-pro-notice-copy" role="note"><span>${message.replace('.', '\\.')}`));
  assert.match(section, /class="selection-locked-value" aria-hidden="true"/, `Free muss für ${kind} gefüllte Felder sichtbar andeuten, ohne Beispielwerte vorzulesen.`);
  assert.match(section, /class="selection-locked-empty" aria-hidden="true">–<\/span>/, `Tatsächlich fehlende ${kind}-Felder müssen weiterhin als leer erkennbar sein.`);
  assert.equal((section.match(/class="selection-item-remove"/g) || []).length, 2, `Jeder ${kind}-Eintrag braucht links einen Entfernen-Button.`);
  assert.match(section, new RegExp(`data-selection-remove-kind="${kind}"`));
  assert.match(section, /<tfoot><tr><td class="summary-label" colspan="3">Summe<\/td><td class="summary-value selection-column-areas" data-selection-column="areas"><span class="selection-locked-value selection-locked-summary" aria-hidden="true">/);
  assert.doesNotMatch(section, /locked-cell|selection-locked-row" aria-hidden=/, `Die Buttons der ${kind}-Zeilen müssen für Hilfstechnologien erreichbar bleiben.`);
}
assert.match(freeBuildingSection, /data-selection-remove-key="preview-building"/);
assert.match(freeBuildingSection, /data-selection-remove-key="preview-building-2"/);
assert.match(freeParcelSection, /data-selection-remove-key="preview-parcel"/);
assert.match(freeParcelSection, /data-selection-remove-key="preview-parcel-2"/);
assert.equal((freeSelectionHtml.match(/>Summe<\/td>/g) || []).length, 2, 'Beide mehrfach belegten Free-Abschnitte brauchen eine sichtbare Summenbezeichnung.');
assert.equal((freeSelectionHtml.match(/1\.234 m²/g) || []).length, 2, 'Free-Summen dürfen ausschließlich feste, geblurrte Beispielwerte verwenden.');
assert.doesNotMatch(freeSelectionHtml, /selection-pro-lock|Pro buchen/, 'Die alte überlagernde Pro-Fläche darf nicht mehr gerendert werden.');

const freeMixedBuildingSection = sectionFor(renderSelection({
  buildings: [
    { preview_id: 'free-official', available_fields: ['grundflaeche_m2'] },
    { preview_id: 'free-geometric', available_fields: ['geometrische_flaeche_m2'] }
  ],
  parcels: []
}, false), 'building');
assert.deepEqual(areaEntries(tablePart(freeMixedBuildingSection, 'thead')), [['official', 'Amtliche Fläche'], ['geometric', 'Geometrische Fläche']]);
assert.doesNotMatch(tablePart(freeMixedBuildingSection, 'tbody'), /Amtliche Fläche|Geometrische Fläche/, 'Auch die Free-Hinweiszeile darf keine Flächenart wiederholen.');

const freeBayernLod2HeightSection = sectionFor(renderSelection({
  buildings: [{
    preview_id: 'free-bayern-lod2-height',
    source_db: 'bayern-lod2',
    available_fields: ['objekthoehe_m', 'geometrische_flaeche_m2']
  }],
  parcels: []
}, false), 'building');
assert.doesNotMatch(freeBayernLod2HeightSection, /Objekthöhe/, 'Auch ein alter Free-Vorschaustand darf die Bayern-LoD2-Höhenspalte nicht wieder einblenden.');

const freeParcelIdentityHtml = renderSelection({
  buildings: [],
  parcels: [{
    preview_id: 'parcel-identity-free',
    available_fields: ['gemarkungsschluessel', 'gemarkung', 'flur', 'flurstueck']
  }]
}, false);
assertCompactParcelIdentityBlock(freeParcelIdentityHtml, 'Free-Preview');
assert.match(freeParcelIdentityHtml, /<tr class="selection-pro-notice"><td colspan="7">/, 'Free muss trotz kompaktem Viererblock genau eine passende Hinweiszeile behalten.');
assert.equal((freeParcelIdentityHtml.match(/<tr class="selection-locked-row">/g) || []).length, 1, 'Free muss auch mit allen Grunddatenspalten genau eine sichere Vorschauzeile rendern.');
assert.match(freeParcelIdentityHtml, /data-selection-remove-key="parcel-identity-free"/);
assert.doesNotMatch(freeParcelIdentityHtml, /<tfoot>/, 'Ein einzelnes Free-Objekt darf keine Summenzeile erhalten.');

const freeSecretGuardHtml = renderSelection({
  buildings: [{
    preview_id: 'preview-secret',
    available_fields: ['gebaeudefunktion_text', 'addresses', 'grundflaeche_m2'],
    gebaeudefunktion_text: 'ECHTER NICHT FREIGEGEBENER WERT',
    addresses: ['Geheimweg 987'],
    grundflaeche_m2: 987654
  }],
  parcels: []
}, false);
assert.match(freeSecretGuardHtml, /Wohngebäude|Nebengebäude|Garage/, 'Free muss ausschließlich sichere Beispielwerte rendern.');
assert.doesNotMatch(
  freeSecretGuardHtml,
  /ECHTER NICHT FREIGEGEBENER WERT|Geheimweg 987|987654/,
  'Echte Premiumwerte dürfen auch während eines Zugriffswechsels nicht im Free-DOM verbleiben.'
);

const freeAccessTransitionGuardHtml = renderSelection({
  buildings: [{
    source_db: 'NICHT-FREIGEBEN-SOURCE',
    gml_id: 'NICHT-FREIGEBEN-GML',
    available_fields: ['gebaeudefunktion_text', 'grundflaeche_m2']
  }],
  parcels: []
}, false);
assert.doesNotMatch(
  freeAccessTransitionGuardHtml,
  /data-selection-remove-key|selection-item-remove|NICHT-FREIGEBEN/,
  'Während access-loading darf ohne opake preview_id weder eine interne Kennung noch eine scheinbar bedienbare Aktion erscheinen.'
);

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
assert.doesNotMatch(selectionSource, /label:\s*'Flächen'/, 'Der generische Flächenkopf darf nicht zurückkehren.');
assert.match(selectionSource, /colspan="\$\{firstSumIndex(?: \+ 1)?\}"/, 'Die Summenbezeichnung muss direkt an den Flächenblock anschließen.');
assert.match(stylesSource, /\.selection-content \{[^}]*grid-template-columns: max-content;/, 'Das gemeinsame Raster muss von der breitesten Tabelle bestimmt werden.');
assert.match(stylesSource, /\.selection-section \{ min-width: 100%; \}/, 'Beide Tabellenabschnitte müssen mindestens die sichtbare Breite belegen.');
assert.match(stylesSource, /@media \(min-width: 761px\) \{[\s\S]*?\.selection-content \{ grid-template-columns: minmax\(100%, max-content\); \}/, 'Desktop muss den gemeinsamen Tabellenbereich mindestens auf die verfügbare Breite strecken.');
assert.match(stylesSource, /@media \(min-width: 761px\) \{[\s\S]*?selection-column-fill[^}]*width: 100%/, 'Auf Desktop muss eine Datenspalte den Restplatz aufnehmen, damit die gemeinsamen Schluss-Spalten ihre gemessene Breite behalten.');
assert.match(stylesSource, /selection-data-table th\.compact[^}]*width: 1%[^}]*white-space: nowrap/, 'Der kompakte Flurstücksblock muss auf Desktop und Mobile eng zusammenbleiben.');
assert.match(stylesSource, /@media \(max-width: 760px\)[\s\S]*?\.selection-data-table \{[^}]*-webkit-text-size-adjust: 100%;[^}]*text-size-adjust: 100%;[^}]*\}/, 'Mobile Safari darf lange Tabellenwerte wie die Gebäudefunktion nicht selektiv vergrößern.');
assert.match(stylesSource, /selection-column-address[^}]*width: var\(--selection-address-width, 220px\)[^}]*min-width: var\(--selection-address-width, 220px\)[^}]*max-width: var\(--selection-address-width, 220px\)/, 'Beide Adressspalten müssen dieselbe dynamische Breite verwenden.');
assert.match(stylesSource, /selection-column-areas[^}]*width: var\(--selection-areas-width, 250px\)[^}]*min-width: var\(--selection-areas-width, 250px\)[^}]*max-width: var\(--selection-areas-width, 250px\)/, 'Beide Flächenspalten müssen dieselbe dynamische Breite verwenden.');
assert.match(stylesSource, /\.address-chip \{[^}]*width: max-content[^}]*white-space: nowrap/, 'Adresswerte müssen für die längste gemeinsame Breite einzeilig messbar bleiben.');
assert.match(stylesSource, /\.address-relation-note \{[^}]*white-space: normal/, 'Der Kürzungshinweis muss im Adressfeld lesbar umbrechen können.');
assert.match(stylesSource, /\.selection-area-grid \{[^}]*grid-template-columns: repeat\(var\(--selection-area-count, 1\), minmax\(0, 1fr\)\)[^}]*width: 100%/, 'Die fachlichen Flächenspalten müssen im gemeinsamen Block gleichmäßig ausgerichtet bleiben.');
assert.match(selectionSource, /querySelectorAll\(`\[data-selection-column="\$\{slot\.name\}"\]`\)/, 'Die dynamische Breite muss alle Gebäude- und Flurstückszellen eines Slots gemeinsam messen.');
assert.match(selectionSource, /const html = state\.access\.ready && state\.access\.pro/, 'Während eines Zugriffswechsels dürfen keine alten Pro-Werte sichtbar bleiben.');
assert.match(selectionSource, /\['restore', 'access', 'access-loading'\]\.includes\(reason\)/, 'Die Tabelle muss auf jeden Zugriffswechsel reagieren.');
assert.match(stylesSource, /\.selection-locked-value \{[^}]*filter: blur\(3\.2px\);[^}]*user-select: none;/, 'Free-Vorschauwerte müssen sichtbar geblurrt und nicht auswählbar sein.');
assert.match(stylesSource, /\.selection-locked-summary \{ display: block; width: 100%; \}/, 'Der geblurrte Summenplatzhalter muss den gemeinsamen Flächenblock ausfüllen.');
assert.match(selectionSource, /typeof item\?\.preview_id === 'string'/, 'Free-Entfernen-Buttons dürfen ausschließlich opake Preview-IDs verwenden.');
assert.doesNotMatch(selectionSource.slice(selectionSource.indexOf('function lockedPreviewTable'), selectionSource.indexOf('function lockedPreviewSample')), /\.summary\(items\)|columnValue\(/, 'Free-Summen dürfen keine echten Werte lesen oder berechnen.');
assert.match(selectionSource, /index === fillIndex \? \['selection-column-fill'\] : \[\]/, 'Der Renderer muss die letzte Datenspalte vor Adressen und Flächen als Desktop-Füllspalte markieren.');
assert.match(selectionSource, /--selection-address-width/);
assert.match(selectionSource, /--selection-areas-width/);
assert.match(stylesSource, /selection-area-grid > span \{[^}]*text-align: right;[^}]*font-variant-numeric: tabular-nums/, 'Flächenköpfe und -werte müssen spaltenweise rechtsbündig bleiben.');
assert.match(stylesSource, /selection-pro-notice-copy \{[\s\S]*?width: var\(--selection-pro-notice-width,[\s\S]*?transform: translateX\(var\(--selection-pro-notice-shift, 0px\)\)/, 'Der mobile Hinweis braucht eine sichtfensterbreite, scrollabhängige Ausrichtung.');
assert.match(selectionSource, /selectionContent\.addEventListener\('scroll', schedulePreviewNoticeAlignment, \{ passive: true \}\)/, 'Horizontales Scrollen muss die mobile Hinweisausrichtung aktualisieren.');

console.log('selection-table-layout-tests=ok');
