import { formatArea, formatDistance, haversineMeters, polygonAreaMeters } from './utils.js';

export function createMeasureController({ map, store, elements }) {
  const { measurePanel, measureDistance, measureArea, measureUndo, measureClear } = elements;
  let points = [];
  let draft = null;

  function addLayers() {
    const empty = { type: 'FeatureCollection', features: [] };
    map.addSource('measure-v2', { type: 'geojson', data: empty });
    map.addLayer({ id: 'measure-v2-fill', type: 'fill', source: 'measure-v2', filter: ['==', '$type', 'Polygon'], paint: { 'fill-color': '#f86d14', 'fill-opacity': .14 } });
    map.addLayer({ id: 'measure-v2-line', type: 'line', source: 'measure-v2', paint: { 'line-color': '#f86d14', 'line-width': 2.2, 'line-dasharray': [2.5, 1.6] } });
    map.addLayer({ id: 'measure-v2-points', type: 'circle', source: 'measure-v2', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 4, 'circle-color': '#fff', 'circle-stroke-color': '#f86d14', 'circle-stroke-width': 2 } });
  }

  function render() {
    const active = store.getState().activeTool === 'measure';
    measurePanel.hidden = !active;
    if (!map.getSource('measure-v2')) return;
    const line = draft ? [...points, draft] : points;
    const features = points.map((coordinates) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates } }));
    if (line.length >= 2) features.unshift({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: line } });
    if (points.length >= 3) features.unshift({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...points, points[0]]] } });
    map.getSource('measure-v2').setData({ type: 'FeatureCollection', features });
    const distance = line.slice(1).reduce((sum, point, index) => sum + haversineMeters(line[index], point), 0);
    measureDistance.textContent = formatDistance(distance);
    measureArea.textContent = points.length >= 3 ? formatArea(polygonAreaMeters(points)) : '–';
  }

  function clear() { points = []; draft = null; render(); }
  function undo() { points.pop(); render(); }

  map.on('load', addLayers);
  map.on('click', (event) => {
    if (store.getState().activeTool !== 'measure') return;
    points.push([event.lngLat.lng, event.lngLat.lat]);
    draft = null;
    render();
  });
  map.on('mousemove', (event) => {
    if (store.getState().activeTool !== 'measure' || !points.length) return;
    draft = [event.lngLat.lng, event.lngLat.lat];
    render();
  });
  store.subscribe((state, reason) => {
    if (reason === 'tool') {
      map.getCanvas().style.cursor = state.activeTool === 'measure' ? 'crosshair' : '';
      if (state.activeTool !== 'measure') clear(); else render();
    }
  });
  measureUndo.addEventListener('click', undo);
  measureClear.addEventListener('click', clear);
  return { clear, undo };
}
