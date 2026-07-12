import turfArea from '@turf/area';
import { featureCollection, polygon } from '@turf/helpers';
import union from '@turf/union';
import unkinkPolygon from '@turf/unkink-polygon';

const EMPTY_RESULT = Object.freeze({ geometry: null, geometries: [], area: 0, parts: 0 });

function validCoordinate(value) {
  return Array.isArray(value) && value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1]);
}

function sameCoordinate(left, right) {
  return left[0] === right[0] && left[1] === right[1];
}

function normalizedPath(coordinates) {
  const points = [];
  for (const value of coordinates || []) {
    if (!validCoordinate(value)) continue;
    const coordinate = [Number(value[0]), Number(value[1])];
    if (!points.length || !sameCoordinate(points[points.length - 1], coordinate)) points.push(coordinate);
  }
  return points;
}

function coordinateKey(coordinate) {
  return `${coordinate[0]},${coordinate[1]}`;
}

function candidateRings(coordinates) {
  const path = normalizedPath(coordinates);
  const rings = [];
  let active = [];
  let positions = new Map();

  for (const coordinate of path) {
    const key = coordinateKey(coordinate);
    const repeatedAt = positions.get(key);
    if (repeatedAt !== undefined && active.length - repeatedAt >= 3) {
      rings.push([...active.slice(repeatedAt), [...coordinate]]);
      active = [[...coordinate]];
      positions = new Map([[key, 0]]);
      continue;
    }
    if (repeatedAt === undefined) positions.set(key, active.length);
    active.push([...coordinate]);
  }

  if (active.length >= 3) {
    if (!sameCoordinate(active[0], active[active.length - 1])) active.push([...active[0]]);
    rings.push(active);
  }
  return rings;
}

function fallbackGeometry(parts) {
  if (parts.length === 1) return parts[0].geometry;
  return { type: 'MultiPolygon', coordinates: parts.map((part) => part.geometry.coordinates) };
}

function signedRingArea(ring) {
  let sum = 0;
  for (let index = 1; index < ring.length; index += 1) {
    const previous = ring[index - 1];
    const current = ring[index];
    sum += previous[0] * current[1] - current[0] * previous[1];
  }
  return sum / 2;
}

function renderRing(ring, clockwise) {
  const copy = ring.map((coordinate) => [...coordinate]);
  const isClockwise = signedRingArea(copy) < 0;
  return isClockwise === clockwise ? copy : copy.reverse();
}

function renderPolygon(coordinates) {
  return coordinates.map((ring, index) => renderRing(ring, index === 0));
}

function polygonGeometries(geometry) {
  if (geometry?.type === 'Polygon') return [{ type: 'Polygon', coordinates: renderPolygon(geometry.coordinates) }];
  if (geometry?.type === 'MultiPolygon') {
    return geometry.coordinates.map((coordinates) => ({ type: 'Polygon', coordinates: renderPolygon(coordinates) }));
  }
  return [];
}

export function polygonizeMeasurement(coordinates) {
  try {
    const pieces = candidateRings(coordinates)
      .flatMap((ring) => {
        try {
          return unkinkPolygon(polygon([ring])).features;
        } catch {
          return [];
        }
      })
      .filter((feature) => feature?.geometry?.type === 'Polygon' && turfArea(feature) > 1e-6);
    if (!pieces.length) return EMPTY_RESULT;

    let merged = pieces[0];
    if (pieces.length > 1) {
      try {
        merged = union(featureCollection(pieces)) || { type: 'Feature', properties: {}, geometry: fallbackGeometry(pieces) };
      } catch {
        merged = { type: 'Feature', properties: {}, geometry: fallbackGeometry(pieces) };
      }
    }
    const area = turfArea(merged);
    if (!merged?.geometry || !Number.isFinite(area) || area <= 1e-6) return EMPTY_RESULT;
    const geometries = polygonGeometries(merged.geometry);
    if (!geometries.length) return EMPTY_RESULT;
    return { geometry: merged.geometry, geometries, area, parts: geometries.length };
  } catch {
    return EMPTY_RESULT;
  }
}
