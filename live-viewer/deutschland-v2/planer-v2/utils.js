export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[character]);
}

export function debounce(callback, delay = 100) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

export function featureKey(item) {
  return `${item?.source_db || ''}:${item?.gml_id || item?.flurstueckskennzeichen || item?.id || ''}`;
}

export function deepCopy(value) {
  try { return JSON.parse(JSON.stringify(value)); } catch (_) { return null; }
}

export function formatArea(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number).toLocaleString('de-DE')} m²` : '–';
}

export function addressLabel(item) {
  if (item?.address) return item.address;
  const first = Array.isArray(item?.addresses) ? item.addresses[0] : null;
  return first?.label || [first?.street, first?.house_number].filter(Boolean).join(' ') || '–';
}

export function resultLabel(result) {
  const label = String(result?.label || result?.value || 'Treffer').trim();
  if (result?.result_type === 'address' || result?.kind === 'address' || result?.kind === 'building') return label;
  const resultType = String(result?.result_type || result?.kind || '').trim();
  const subtitle = String(result?.subtitle || '').trim();
  const state = String(result?.state_label || result?.state || '').trim();
  const parts = [label];
  if (['place', 'street'].includes(resultType) && subtitle && !['Ort', 'Straße'].includes(subtitle)) parts.push(subtitle);
  if (state) parts.push(state);
  return parts.filter((part, index, values) => {
    const normalized = part.toLocaleLowerCase('de-DE');
    return !values.slice(0, index).some((previous) => previous.toLocaleLowerCase('de-DE').includes(normalized));
  }).join(', ');
}

export function centerFromResult(result) {
  if (Array.isArray(result?.center) && result.center.length === 2) return result.center.map(Number);
  if (Array.isArray(result?.bbox) && result.bbox.length === 4) {
    return [(Number(result.bbox[0]) + Number(result.bbox[2])) / 2, (Number(result.bbox[1]) + Number(result.bbox[3])) / 2];
  }
  return null;
}

export function pointInGeometry(point, geometry) {
  if (!geometry || !Array.isArray(point)) return false;
  const insideRing = (ring) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = Number(ring[i][0]); const yi = Number(ring[i][1]);
      const xj = Number(ring[j][0]); const yj = Number(ring[j][1]);
      const intersects = ((yi > point[1]) !== (yj > point[1])) &&
        (point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || Number.EPSILON) + xi);
      if (intersects) inside = !inside;
    }
    return inside;
  };
  const insidePolygon = (polygon) => polygon?.[0] && insideRing(polygon[0]) && !polygon.slice(1).some(insideRing);
  if (geometry.type === 'Polygon') return insidePolygon(geometry.coordinates);
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.some(insidePolygon);
  return false;
}

export function haversineMeters(a, b) {
  const radius = 6371008.8;
  const toRadians = (value) => value * Math.PI / 180;
  const dLat = toRadians(b[1] - a[1]);
  const dLon = toRadians(b[0] - a[0]);
  const lat1 = toRadians(a[1]);
  const lat2 = toRadians(b[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * radius * Math.asin(Math.sqrt(h));
}

export function polygonAreaMeters(points) {
  if (points.length < 3) return 0;
  const latitude = points.reduce((sum, point) => sum + point[1], 0) / points.length;
  const metersPerLon = 111320 * Math.cos(latitude * Math.PI / 180);
  const metersPerLat = 111320;
  let area = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    area += current[0] * metersPerLon * next[1] * metersPerLat - next[0] * metersPerLon * current[1] * metersPerLat;
  }
  return Math.abs(area) / 2;
}

export function formatDistance(meters) {
  if (meters >= 1000) return `${(meters / 1000).toLocaleString('de-DE', { maximumFractionDigits: 2 })} km`;
  return `${meters.toLocaleString('de-DE', { maximumFractionDigits: 1 })} m`;
}
