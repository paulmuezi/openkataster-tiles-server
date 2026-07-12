import assert from 'node:assert/strict';
import { polygonizeMeasurement } from '../live-viewer/viewer-app/measure-geometry.mjs';

function approximately(actual, expected, tolerance, message) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} statt ${expected}`);
}

function signedRingArea(ring) {
  return ring.slice(1).reduce((sum, point, index) => (
    sum + ring[index][0] * point[1] - point[0] * ring[index][1]
  ), 0) / 2;
}

const square = polygonizeMeasurement([[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001]]);
assert.ok(square.geometry, 'Ein einfaches Rechteck muss eine Fläche ergeben.');
assert.ok(square.area > 12_000 && square.area < 13_000, 'Die Rechteckfläche muss plausibel sein.');

const bowTie = polygonizeMeasurement([[0, 0], [0.001, 0.001], [0, 0.001], [0.001, 0]]);
assert.ok(bowTie.geometry, 'Eine gekreuzte Schleife muss polygonisiert werden.');
assert.equal(bowTie.geometries.length, 2, 'Eine gekreuzte Schleife muss als zwei einfache Polygone gerendert werden.');
assert.ok(bowTie.geometries.every((geometry) => geometry.type === 'Polygon'), 'Der Renderer darf nur einfache Polygone erhalten.');
assert.ok(bowTie.geometries.every((geometry) => signedRingArea(geometry.coordinates[0]) < 0), 'Außenringe müssen für MapLibre im Uhrzeigersinn laufen.');
approximately(bowTie.area, square.area / 2, square.area * 0.03, 'Die Schleifenfläche muss aus beiden Dreiecken bestehen');

const doubleLoop = polygonizeMeasurement([
  [0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0],
  [-0.001, 0], [-0.001, -0.001], [0, -0.001]
]);
assert.ok(doubleLoop.geometry, 'Zwei verbundene Schleifen müssen gültige Flächen ergeben.');
approximately(doubleLoop.area, square.area * 2, square.area * 0.05, 'Beide Schleifen müssen addiert werden');

const repeatedPoint = polygonizeMeasurement([[0, 0], [0.001, 0], [0.001, 0], [0.001, 0.001], [0, 0.001]]);
approximately(repeatedPoint.area, square.area, square.area * 0.01, 'Doppelte aufeinanderfolgende Punkte dürfen die Fläche nicht verändern');

const repeatedLoop = polygonizeMeasurement([
  [0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0],
  [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]
]);
approximately(repeatedLoop.area, square.area, square.area * 0.01, 'Mehrfach durchlaufene Flächen dürfen nicht doppelt zählen');

const crossedStar = polygonizeMeasurement([
  [0, 0.001], [0.000588, -0.000809], [-0.000951, 0.000309],
  [0.000951, 0.000309], [-0.000588, -0.000809]
]);
assert.ok(crossedStar.geometry, 'Mehrfach gekreuzte Pfade müssen polygonisiert werden.');
assert.ok(Number.isFinite(crossedStar.area) && crossedStar.area > 0, 'Mehrfach gekreuzte Pfade brauchen eine endliche Fläche.');

const degenerate = polygonizeMeasurement([[0, 0], [0.001, 0], [0, 0], [0, 0.001]]);
assert.equal(degenerate.geometry, null, 'Eine degenerierte Rücklauflinie darf keine kaputte Fläche erzeugen.');
assert.equal(degenerate.area, 0);

console.log('measure-geometry-tests=ok');
