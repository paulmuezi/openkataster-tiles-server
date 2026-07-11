import { formatArea, formatDistance, haversineMeters, polygonAreaMeters } from './utils.js';

const SNAP_LAYERS = [
  'alkis-building-fills', 'alkis-building-lines', 'alkis-parcel-lines',
  'selected-buildings-v2', 'selected-parcels-v2'
];

export function createMeasureController({ map, store, elements }) {
  const {
    measurePanel, measureDistance, measureAngle, measureCumulative, measureArea,
    measureUndo, measureClear
  } = elements;
  let points = [];
  let draft = null;
  let closed = false;
  let cursorPoint = null;
  let snapped = false;

  function featureCollection(features = []) {
    return { type: 'FeatureCollection', features };
  }

  function addLayers() {
    if (map.getSource('measure-v2')) return;
    map.addSource('measure-v2', { type: 'geojson', data: featureCollection() });
    map.addSource('measure-snap-v2', { type: 'geojson', data: featureCollection() });
    map.addLayer({ id: 'measure-v2-fill', type: 'fill', source: 'measure-v2', filter: ['==', '$type', 'Polygon'], paint: { 'fill-color': '#6b7280', 'fill-opacity': .16 } });
    map.addLayer({ id: 'measure-v2-line', type: 'line', source: 'measure-v2', filter: ['==', '$type', 'LineString'], paint: { 'line-color': '#4b5563', 'line-width': 1.35, 'line-dasharray': [3.2, 2.2], 'line-opacity': .96 } });
    map.addLayer({ id: 'measure-v2-line-halo', type: 'line', source: 'measure-v2', filter: ['==', '$type', 'LineString'], paint: { 'line-color': 'rgba(255,255,255,.88)', 'line-width': 3.6, 'line-dasharray': [3.2, 2.2] } }, 'measure-v2-line');
    map.addLayer({ id: 'measure-v2-points', type: 'circle', source: 'measure-v2', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 3.7, 'circle-color': '#fff', 'circle-stroke-color': '#4b5563', 'circle-stroke-width': 1.4 } });
    map.addLayer({ id: 'measure-snap-v2', type: 'circle', source: 'measure-snap-v2', paint: { 'circle-radius': 6.2, 'circle-color': 'rgba(255,255,255,.82)', 'circle-stroke-color': '#f86d14', 'circle-stroke-width': 2 } });
  }

  function validCoordinate(value) {
    return Array.isArray(value) && value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1]);
  }

  function collectVertices(geometry, target = []) {
    if (!geometry) return target;
    if (geometry.type === 'Point') target.push(geometry.coordinates);
    else if (geometry.type === 'LineString' || geometry.type === 'MultiPoint') geometry.coordinates.forEach((point) => target.push(point));
    else if (geometry.type === 'Polygon' || geometry.type === 'MultiLineString') geometry.coordinates.forEach((line) => line.forEach((point) => target.push(point)));
    else if (geometry.type === 'MultiPolygon') geometry.coordinates.forEach((polygon) => polygon.forEach((line) => line.forEach((point) => target.push(point))));
    else if (geometry.type === 'GeometryCollection') (geometry.geometries || []).forEach((part) => collectVertices(part, target));
    return target;
  }

  function collectLineSegments(coordinates, target) {
    for (let index = 1; index < (coordinates || []).length; index += 1) {
      const start = coordinates[index - 1];
      const end = coordinates[index];
      if (validCoordinate(start) && validCoordinate(end)) target.push([start, end]);
    }
  }

  function collectSegments(geometry, target = []) {
    if (!geometry) return target;
    if (geometry.type === 'LineString') collectLineSegments(geometry.coordinates, target);
    else if (geometry.type === 'Polygon') geometry.coordinates.forEach((ring) => collectLineSegments(ring, target));
    else if (geometry.type === 'MultiLineString') geometry.coordinates.forEach((line) => collectLineSegments(line, target));
    else if (geometry.type === 'MultiPolygon') geometry.coordinates.forEach((polygon) => polygon.forEach((ring) => collectLineSegments(ring, target)));
    else if (geometry.type === 'GeometryCollection') (geometry.geometries || []).forEach((part) => collectSegments(part, target));
    return target;
  }

  function snapRadius(event) {
    const touch = Boolean(event.originalEvent?.touches?.length || event.originalEvent?.pointerType === 'touch');
    const far = touch ? 36 : 28;
    const near = touch ? 18 : 9;
    const factor = Math.max(0, Math.min(1, (map.getZoom() - 17) / 5));
    return far + (near - far) * factor;
  }

  function closestPointOnSegment(point, start, end) {
    const a = map.project(start);
    const b = map.project(end);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) return null;
    const ratio = Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
    const x = a.x + dx * ratio;
    const y = a.y + dy * ratio;
    const lngLat = map.unproject([x, y]);
    return { coordinate: [lngLat.lng, lngLat.lat], distance: Math.hypot(x - point.x, y - point.y) };
  }

  function setSnapIndicator(coordinate) {
    map.getSource('measure-snap-v2')?.setData(featureCollection(coordinate ? [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: coordinate } }] : []));
  }

  function nearestSnap(event) {
    const fallback = [event.lngLat.lng, event.lngLat.lat];
    const radius = snapRadius(event);
    const cornerRadius = radius * 1.18;
    const edgeRadius = radius * .72;
    const queryRadius = Math.max(cornerRadius, edgeRadius);
    const box = [[event.point.x - queryRadius, event.point.y - queryRadius], [event.point.x + queryRadius, event.point.y + queryRadius]];
    const vertices = [...points];
    const segments = [];
    const layers = SNAP_LAYERS.filter((id) => map.getLayer(id));
    try {
      for (const feature of map.queryRenderedFeatures(box, { layers })) {
        collectVertices(feature.geometry, vertices);
        collectSegments(feature.geometry, segments);
      }
    } catch (error) {
      console.warn('Punktfang konnte sichtbare Geometrien nicht lesen', error);
    }
    const selection = store.getState().selection;
    for (const item of [...selection.parcels, ...selection.buildings]) {
      collectVertices(item.geometry, vertices);
      collectSegments(item.geometry, segments);
    }

    let nearestVertex = null;
    let vertexDistance = Infinity;
    for (const coordinate of vertices) {
      if (!validCoordinate(coordinate)) continue;
      const projected = map.project(coordinate);
      const distance = Math.hypot(projected.x - event.point.x, projected.y - event.point.y);
      if (distance <= cornerRadius && distance < vertexDistance) {
        nearestVertex = coordinate;
        vertexDistance = distance;
      }
    }
    if (nearestVertex) return { coordinate: [nearestVertex[0], nearestVertex[1]], snapped: true };

    let nearestEdge = null;
    let edgeDistance = Infinity;
    for (const [start, end] of segments) {
      const candidate = closestPointOnSegment(event.point, start, end);
      if (candidate && candidate.distance <= edgeRadius && candidate.distance < edgeDistance) {
        nearestEdge = candidate.coordinate;
        edgeDistance = candidate.distance;
      }
    }
    return nearestEdge ? { coordinate: nearestEdge, snapped: true } : { coordinate: fallback, snapped: false };
  }

  function workingPoints() {
    if (closed) return points;
    if (draft && points.length && haversineMeters(points[points.length - 1], draft) > .03) return [...points, draft];
    return points;
  }

  function lineCoordinates() {
    if (closed && points.length >= 3) return [...points, points[0]];
    return workingPoints();
  }

  function planarDelta(start, end) {
    if (!start || !end) return { dx: 0, dy: 0, distance: 0, angle: 0 };
    const latitude = ((start[1] + end[1]) / 2) * Math.PI / 180;
    const dx = (end[0] - start[0]) * 111320 * Math.cos(latitude);
    const dy = (end[1] - start[1]) * 110540;
    return { dx, dy, distance: Math.hypot(dx, dy), angle: (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360 };
  }

  function angleValue(working) {
    if (working.length >= 3) {
      const first = planarDelta(working[working.length - 3], working[working.length - 2]);
      const second = planarDelta(working[working.length - 2], working[working.length - 1]);
      const denominator = first.distance * second.distance;
      if (denominator > 0) {
        const ratio = Math.max(-1, Math.min(1, (first.dx * second.dx + first.dy * second.dy) / denominator));
        return { label: 'Winkel zur letzten Linie', value: Math.acos(ratio) * 180 / Math.PI };
      }
    }
    if (working.length >= 2) {
      const angle = planarDelta(working[working.length - 2], working[working.length - 1]).angle;
      return { label: 'Winkel zur Horizontalen', value: angle > 180 ? 360 - angle : angle };
    }
    return { label: 'Winkel zur Horizontalen', value: 0 };
  }

  function positionPanel() {
    if (measurePanel.hidden || !cursorPoint) return;
    if (window.matchMedia('(max-width: 760px)').matches) {
      measurePanel.style.left = '65px';
      measurePanel.style.top = '126px';
      return;
    }
    const container = map.getContainer();
    const margin = 8;
    const gap = 18;
    const width = measurePanel.offsetWidth || 226;
    const height = measurePanel.offsetHeight || 142;
    let left = cursorPoint.x + gap;
    let top = cursorPoint.y + gap;
    if (left + width + margin > container.clientWidth) left = cursorPoint.x - width - gap;
    if (top + height + margin > container.clientHeight) top = cursorPoint.y - height - gap;
    measurePanel.style.left = `${Math.max(margin, Math.min(left, container.clientWidth - width - margin))}px`;
    measurePanel.style.top = `${Math.max(margin, Math.min(top, container.clientHeight - height - margin))}px`;
  }

  function render() {
    const active = store.getState().activeTool === 'measure';
    measurePanel.hidden = !active || !points.length;
    if (!map.getSource('measure-v2')) return;
    const working = workingPoints();
    const line = lineCoordinates();
    const features = points.map((coordinates) => ({ type: 'Feature', properties: { kind: 'point' }, geometry: { type: 'Point', coordinates } }));
    if (line.length >= 2) features.unshift({ type: 'Feature', properties: { kind: 'line' }, geometry: { type: 'LineString', coordinates: line } });
    if (working.length >= 3) features.unshift({ type: 'Feature', properties: { kind: 'area' }, geometry: { type: 'Polygon', coordinates: [[...working, working[0]]] } });
    map.getSource('measure-v2').setData(featureCollection(features));

    const currentDistance = working.length >= 2 ? haversineMeters(working[working.length - 2], working[working.length - 1]) : 0;
    const cumulative = line.slice(1).reduce((sum, point, index) => sum + haversineMeters(line[index], point), 0);
    const angle = angleValue(working);
    measureDistance.textContent = formatDistance(currentDistance);
    measureAngle.previousElementSibling.textContent = angle.label;
    measureAngle.textContent = `${angle.value.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}°`;
    measureCumulative.textContent = formatDistance(cumulative);
    measureArea.textContent = working.length >= 3 ? formatArea(polygonAreaMeters(working)) : '–';
    measureUndo.disabled = !points.length;
    measureClear.disabled = !points.length;
    measurePanel.dataset.snapped = snapped ? 'true' : 'false';
    positionPanel();
  }

  function clear() {
    points = [];
    draft = null;
    closed = false;
    snapped = false;
    setSnapIndicator(null);
    render();
  }

  function undo() {
    if (closed) closed = false;
    else points.pop();
    draft = null;
    setSnapIndicator(null);
    render();
  }

  map.on('load', addLayers);
  map.on('click', (event) => {
    if (store.getState().activeTool !== 'measure') return;
    cursorPoint = event.point;
    const candidate = nearestSnap(event);
    const coordinate = candidate.coordinate;
    if (closed) {
      points = [coordinate];
      closed = false;
    } else if (points.length >= 3 && haversineMeters(points[0], coordinate) < .05) {
      closed = true;
    } else if (!points.length || haversineMeters(points[points.length - 1], coordinate) >= .03) {
      points.push(coordinate);
    }
    draft = null;
    snapped = candidate.snapped;
    setSnapIndicator(candidate.snapped ? coordinate : null);
    render();
  });
  map.on('mousemove', (event) => {
    if (store.getState().activeTool !== 'measure') return;
    cursorPoint = event.point;
    if (!points.length || closed) {
      positionPanel();
      return;
    }
    const candidate = nearestSnap(event);
    draft = candidate.coordinate;
    snapped = candidate.snapped;
    setSnapIndicator(candidate.snapped ? draft : null);
    render();
  });
  map.on('mouseout', () => {
    if (store.getState().activeTool !== 'measure') return;
    draft = null;
    snapped = false;
    setSnapIndicator(null);
    render();
  });
  store.subscribe((state, reason) => {
    if (reason !== 'tool') return;
    const active = state.activeTool === 'measure';
    map.getCanvas().style.cursor = active ? 'crosshair' : '';
    if (active) {
      map.doubleClickZoom.disable();
      render();
    } else {
      map.doubleClickZoom.enable();
      clear();
    }
  });
  window.addEventListener('keydown', (event) => {
    if (store.getState().activeTool !== 'measure') return;
    if (event.key === 'Escape') clear();
    if (event.key === 'Backspace' || event.key === 'Delete') {
      event.preventDefault();
      undo();
    }
  });
  measureUndo.addEventListener('click', undo);
  measureClear.addEventListener('click', clear);
  return { clear, undo };
}
