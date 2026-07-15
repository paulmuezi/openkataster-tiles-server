import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { resolveHitStack, withoutSelectionItem } from '../live-viewer/viewer-app/selection.js';
import { selectionPreferenceForSearchResult } from '../live-viewer/viewer-app/search.js';

const selectionSource = readFileSync(new URL('../live-viewer/viewer-app/selection.js', import.meta.url), 'utf8');
assert.match(selectionSource, /HIDDEN_DYNAMIC_FIELDS[\s\S]*?'flurstuecksfolge'/, 'Flurstücksfolge muss auch als dynamische Zusatzspalte verborgen bleiben.');
assert.doesNotMatch(selectionSource, /Flurstücksfolge/, 'Flurstücksfolge darf weder in der Free- noch in der Pro-Tabelle beschriftet werden.');

const buildingA = { preview_id: 'building-a' };
const buildingB = { preview_id: 'building-b' };
const parcelA = { preview_id: 'parcel-a' };
const parcelB = { preview_id: 'parcel-b' };
const foreignParcel = { preview_id: 'parcel-foreign' };
const parcelGeometry = {
  type: 'Polygon',
  coordinates: [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
};
const buildingGeometryA = {
  type: 'Polygon',
  coordinates: [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]
};
const buildingGeometryB = {
  type: 'Polygon',
  coordinates: [[[6, 6], [8, 6], [8, 8], [6, 8], [6, 6]]]
};

let result = resolveHitStack({ hitBuildings: [buildingA], hitParcels: [parcelA] });
assert.deepEqual(result, { buildings: [buildingA], parcels: [parcelA] }, 'Ein Klick muss Gebäude und Flurstück gemeinsam auswählen.');

result = resolveHitStack({
  currentBuildings: [buildingA],
  hitBuildings: [buildingA],
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [], parcels: [] }, 'Ein bereits gewähltes Gebäude muss sich abwählen lassen, ohne ein manuell entferntes Flurstück wiederherzustellen.');

result = resolveHitStack({
  currentBuildings: [buildingA],
  currentParcels: [parcelA],
  hitBuildings: [buildingA],
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [], parcels: [parcelA] }, 'Ein Gebäudeklick darf das gemeinsame Flurstück nicht abwählen.');

result = resolveHitStack({ hitBuildings: [buildingA], hitParcels: [parcelA] });
result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: result.parcels,
  hitBuildings: [buildingB],
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [buildingA, buildingB], parcels: [parcelA] }, 'Das zweite Gebäude muss hinzukommen, ohne das gemeinsame Flurstück abzuwählen.');
result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: result.parcels,
  hitBuildings: [buildingA],
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [buildingB], parcels: [parcelA] }, 'Das erste Gebäude muss sich getrennt vom gemeinsamen Flurstück abwählen lassen.');
result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: result.parcels,
  hitBuildings: [buildingB],
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [], parcels: [parcelA] }, 'Auch das letzte Gebäude darf das Flurstück nicht implizit abwählen.');
result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: result.parcels,
  hitParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [], parcels: [] }, 'Ein reiner Flurstücksklick darf das Flurstück explizit abwählen.');

const buildingOnParcelA = { preview_id: 'building-on-parcel-a', geometry: buildingGeometryA };
const buildingOnParcelB = { preview_id: 'building-on-parcel-b', geometry: buildingGeometryB };
const sharedParcel = { preview_id: 'shared-parcel', geometry: parcelGeometry };
result = resolveHitStack({
  currentBuildings: [buildingOnParcelA, buildingOnParcelB],
  currentParcels: [sharedParcel],
  hitParcels: [sharedParcel],
  additive: true
});
assert.deepEqual(result, {
  buildings: [buildingOnParcelA, buildingOnParcelB],
  parcels: []
}, 'Ein reiner Flurstückstreffer muss das Flurstück auch bei ausgewählten Gebäuden explizit abwählen.');

result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: result.parcels,
  hitParcels: [sharedParcel],
  additive: true
});
assert.deepEqual(result, {
  buildings: [buildingOnParcelA, buildingOnParcelB],
  parcels: [sharedParcel]
}, 'Ein manuell entferntes Flurstück muss sich unabhängig wieder auswählen lassen.');

result = resolveHitStack({
  currentBuildings: result.buildings,
  currentParcels: [],
  hitBuildings: [buildingOnParcelA],
  hitParcels: [sharedParcel],
  additive: true
});
assert.deepEqual(result, {
  buildings: [buildingOnParcelB],
  parcels: []
}, 'Ein Gebäudeklick darf ein zuvor manuell entferntes Flurstück nicht wiederherstellen.');

result = withoutSelectionItem({
  buildings: [buildingOnParcelA, buildingOnParcelB],
  parcels: [sharedParcel]
}, 'parcel', 'shared-parcel');
assert.deepEqual(result, {
  buildings: [buildingOnParcelA, buildingOnParcelB],
  parcels: []
}, 'Die Tabellenaktion muss nur das gewählte Flurstück entfernen.');

const previewParcel = { preview_id: 'parcel-preview', geometry: parcelGeometry };
const fullParcel = { source_db: 'alkis_test', gml_id: 'parcel-full', geometry: structuredClone(parcelGeometry) };
result = resolveHitStack({
  currentBuildings: [buildingOnParcelA],
  currentParcels: [previewParcel],
  hitBuildings: [buildingOnParcelB],
  hitParcels: [fullParcel],
  additive: true
});
assert.deepEqual(result, {
  buildings: [buildingOnParcelA, buildingOnParcelB],
  parcels: [fullParcel]
}, 'Preview und Vollzugriff müssen dasselbe Flurstück anhand der identischen Geometrie zusammenführen.');

result = resolveHitStack({
  currentParcels: [foreignParcel],
  hitBuildings: [buildingA, buildingB, buildingA],
  hitParcels: [parcelA, parcelB, parcelA],
  additive: true
});
assert.deepEqual(result.buildings, [buildingA, buildingB], 'Mehrfach gelieferte Gebäude müssen dedupliziert werden.');
assert.deepEqual(result.parcels, [foreignParcel, parcelA, parcelB], 'Alle geschnittenen Flurstücke und fremde additive Auswahl müssen erhalten bleiben.');

result = resolveHitStack({
  hitBuildings: [buildingA],
  hitParcels: [parcelA],
  preferredKind: 'parcel'
});
assert.deepEqual(result, { buildings: [], parcels: [parcelA] }, 'Eine explizite Flurstückssuche darf auf Flurstücke begrenzt bleiben.');

result = resolveHitStack({
  currentBuildings: [buildingA],
  currentParcels: [parcelA],
  additive: true
});
assert.deepEqual(result, { buildings: [buildingA], parcels: [parcelA] }, 'Ein leerer additiver Klick darf die Auswahl nicht verändern.');

assert.equal(selectionPreferenceForSearchResult({ result_type: 'address', kind: 'building' }), 'all');
assert.equal(selectionPreferenceForSearchResult({ kind: 'building' }), 'all');
assert.equal(selectionPreferenceForSearchResult({ kind: 'parcel' }), 'parcel');
assert.equal(selectionPreferenceForSearchResult({ result_type: 'street' }), null);

console.log('selection-hit-stack-tests=ok');
