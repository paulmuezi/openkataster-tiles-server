export function formatMeasurementCoordinate(value, axis) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '–';
  const longitude = axis === 'lon';
  const hemisphere = longitude ? (number < 0 ? 'W' : 'E') : (number < 0 ? 'S' : 'N');
  const formatted = Math.abs(number).toLocaleString('de-DE', {
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
    useGrouping: false
  });
  return `${formatted}° ${hemisphere}`;
}
