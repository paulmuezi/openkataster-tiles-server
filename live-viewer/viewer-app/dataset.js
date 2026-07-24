import { pointInGeometry } from './utils.js?v=20260711-search-context1';

export const WORKSPACE_DATASET = 'deutschland';

const DATASETS = Object.freeze({
  deutschland: Object.freeze({
    id: WORKSPACE_DATASET,
    countryCode: 'DE+AT',
    name: 'Deutschland und Österreich',
    defaultView: Object.freeze({ lng: 11.55, lat: 50.75, zoom: 4.3 }),
    detailZoom: 17,
    detailZoomByRegion: Object.freeze({ deutschland: 17, oesterreich: 16 }),
    aerialZoomByRegion: Object.freeze({ deutschland: 17, oesterreich: 14 }),
    nationalRegion: '',
    supportsPoi: true,
    unified: true,
    terminology: Object.freeze({
      cadastre: 'Kataster',
      cadastralDistrict: 'Gemarkung / Katastralgemeinde',
      parcel: 'Flurstück / Grundstück',
      parcelPlural: 'Flurstücke / Grundstücke',
      parcelNumber: 'Flurstücks-/Grundstücksnummer',
      district: 'Flur'
    })
  }),
  oesterreich: Object.freeze({
    id: 'oesterreich',
    countryCode: 'AT',
    name: 'Österreich',
    defaultView: Object.freeze({ lng: 14.20, lat: 47.60, zoom: 6.6 }),
    detailZoom: 16,
    aerialZoom: 14,
    nationalRegion: 'oesterreich',
    supportsPoi: false,
    terminology: Object.freeze({
      cadastre: 'Kataster',
      cadastralDistrict: 'Katastralgemeinde',
      parcel: 'Grundstück',
      parcelPlural: 'Grundstücke',
      parcelNumber: 'Grundstücksnummer',
      district: ''
    })
  })
});

const AUSTRIA_FALLBACK_BOUNDS = Object.freeze([9.35, 46.3, 17.2, 49.1]);

function requestedDatasetFromLocation(locationValue = globalThis.location) {
  const pathname = String(locationValue?.pathname || '');
  const match = pathname.match(/\/(?:embed|viewer)\/([^/?#]+)/);
  const query = new URLSearchParams(String(locationValue?.search || ''));
  const initialCountry = String(query.get('initialCountry') || '').trim().toLocaleUpperCase('de');
  if (initialCountry === 'AT') return 'oesterreich';
  const requested = String(query.get('dataset') || match?.[1] || WORKSPACE_DATASET)
    .trim()
    .toLocaleLowerCase('de');
  return requested === 'oesterreich' ? 'oesterreich' : WORKSPACE_DATASET;
}

/**
 * Historical Austria URLs now select the initial focus only. The persisted
 * workspace and the parent postMessage contract always remain "deutschland".
 */
export function datasetIdFromLocation(locationValue = globalThis.location) {
  return requestedDatasetFromLocation(locationValue);
}

export function viewerDatasetProfile(dataset = WORKSPACE_DATASET) {
  return DATASETS[dataset] || DATASETS[WORKSPACE_DATASET];
}

export function unifiedViewerProfile() {
  return DATASETS[WORKSPACE_DATASET];
}

/**
 * Keep old callers functional while canonicalising every viewer/embed URL to
 * the single workspace. Austria is represented as a focus hint, not a second
 * application instance.
 */
export function datasetViewerUrl(locationValue, targetDataset) {
  const target = targetDataset === 'oesterreich' ? 'oesterreich' : WORKSPACE_DATASET;
  const currentPath = String(locationValue?.pathname || '');
  const route = currentPath.startsWith('/embed/') ? 'embed' : 'viewer';
  const query = new URLSearchParams(String(locationValue?.search || ''));
  query.delete('dataset');
  if (target === 'oesterreich') query.set('initialCountry', 'AT');
  else query.delete('initialCountry');
  return `/${route}/${WORKSPACE_DATASET}${query.size ? `?${query.toString()}` : ''}${String(locationValue?.hash || '')}`;
}

export function applyDatasetSwitchState(_profile, root = document) {
  for (const button of root.querySelectorAll?.('[data-dataset-switch]') || []) {
    button.setAttribute('aria-pressed', 'true');
    button.setAttribute('aria-current', 'true');
  }
}

export function applyDatasetTerminology(profile, root = document) {
  const terms = profile.terminology;
  root.documentElement?.setAttribute('data-dataset', WORKSPACE_DATASET);
  root.body?.setAttribute('data-dataset', WORKSPACE_DATASET);

  const addressInput = root.getElementById?.('addressInput');
  const searchPanel = root.getElementById?.('searchPanel');
  const searchModeButton = root.getElementById?.('searchModeButton');
  const parcelFields = root.getElementById?.('parcelFields');
  const gemarkungInput = root.getElementById?.('gemarkungInput');
  const flurInput = root.getElementById?.('flurInput');
  const parcelInput = root.getElementById?.('parcelInput');
  const searchSubmit = root.getElementById?.('searchSubmit');

  const searchLabel = 'Adresse, Flurstück, Grundstück oder POI suchen';
  if (addressInput) {
    addressInput.placeholder = searchLabel;
    addressInput.setAttribute('aria-label', searchLabel);
  }
  searchPanel?.setAttribute('aria-label', searchLabel);
  searchModeButton?.setAttribute('aria-label', 'Katasterreferenz mit Feldern öffnen');
  parcelFields?.setAttribute('aria-label', 'Flurstücks- oder Grundstückssuche');
  if (gemarkungInput) gemarkungInput.placeholder = 'Gemarkung oder Katastralgemeinde';
  if (parcelInput) parcelInput.placeholder = 'Flurstück oder Grundstück';
  if (searchSubmit) {
    const label = searchSubmit.querySelector('span:last-child');
    if (label) label.textContent = 'Katasterreferenz suchen';
  }
  const districtField = flurInput?.closest('.clear-field');
  if (districtField) {
    districtField.hidden = false;
    flurInput.disabled = false;
    flurInput.placeholder = 'Flur optional';
  }

  for (const element of root.querySelectorAll?.('[data-dataset-term]') || []) {
    const term = element.dataset.datasetTerm;
    if (terms[term]) element.textContent = terms[term];
  }
}

function fallbackAustriaCandidate(point) {
  return point[0] >= AUSTRIA_FALLBACK_BOUNDS[0]
    && point[0] <= AUSTRIA_FALLBACK_BOUNDS[2]
    && point[1] >= AUSTRIA_FALLBACK_BOUNDS[1]
    && point[1] <= AUSTRIA_FALLBACK_BOUNDS[3];
}

function geometryRings(geometry) {
  if (geometry?.type === 'Polygon') return geometry.coordinates || [];
  if (geometry?.type === 'MultiPolygon') return (geometry.coordinates || []).flat();
  return [];
}

function normalizedBounds(value) {
  const west = Number(value?.west ?? value?.getWest?.());
  const south = Number(value?.south ?? value?.getSouth?.());
  const east = Number(value?.east ?? value?.getEast?.());
  const north = Number(value?.north ?? value?.getNorth?.());
  if (![west, south, east, north].every(Number.isFinite) || west > east || south > north) return null;
  return { west, south, east, north };
}

function pointInsideBounds(point, bounds) {
  return point[0] >= bounds.west
    && point[0] <= bounds.east
    && point[1] >= bounds.south
    && point[1] <= bounds.north;
}

function segmentIntersectsSegment(a, b, c, d) {
  const cross = (first, second, third) => (
    (second[0] - first[0]) * (third[1] - first[1])
    - (second[1] - first[1]) * (third[0] - first[0])
  );
  const between = (value, first, second) => value >= Math.min(first, second) && value <= Math.max(first, second);
  const touches = (first, second, point) => (
    Math.abs(cross(first, second, point)) < 1e-10
    && between(point[0], first[0], second[0])
    && between(point[1], first[1], second[1])
  );
  const abC = cross(a, b, c);
  const abD = cross(a, b, d);
  const cdA = cross(c, d, a);
  const cdB = cross(c, d, b);
  if (
    ((abC > 0 && abD < 0) || (abC < 0 && abD > 0))
    && ((cdA > 0 && cdB < 0) || (cdA < 0 && cdB > 0))
  ) return true;
  return touches(a, b, c) || touches(a, b, d) || touches(c, d, a) || touches(c, d, b);
}

function geometryIntersectsBounds(geometry, boundsValue) {
  const bounds = normalizedBounds(boundsValue);
  if (!geometry || !bounds) return false;
  const corners = [
    [bounds.west, bounds.south],
    [bounds.east, bounds.south],
    [bounds.east, bounds.north],
    [bounds.west, bounds.north]
  ];
  if (corners.some((point) => pointInGeometry(point, geometry))) return true;
  const edges = corners.map((point, index) => [point, corners[(index + 1) % corners.length]]);
  for (const ring of geometryRings(geometry)) {
    if (ring.some((point) => pointInsideBounds(point, bounds))) return true;
    for (let index = 1; index < ring.length; index += 1) {
      if (edges.some(([first, second]) => segmentIntersectsSegment(ring[index - 1], ring[index], first, second))) {
        return true;
      }
    }
  }
  return false;
}

function geometryBoundaryIntersectsBounds(geometry, bounds) {
  const corners = [
    [bounds.west, bounds.south],
    [bounds.east, bounds.south],
    [bounds.east, bounds.north],
    [bounds.west, bounds.north]
  ];
  const edges = corners.map((point, index) => [point, corners[(index + 1) % corners.length]]);
  for (const ring of geometryRings(geometry)) {
    if (ring.some((point) => pointInsideBounds(point, bounds))) return true;
    for (let index = 1; index < ring.length; index += 1) {
      if (edges.some(([first, second]) => segmentIntersectsSegment(ring[index - 1], ring[index], first, second))) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Loads the exact Austria polygon once and provides one shared routing
 * decision to map layers and API calls. The bounding box is only a temporary
 * fallback while the small overlay is still loading.
 */
export function createCountryResolver({
  fetchImpl = (...args) => fetch(...args),
  countriesUrl = '/viewer-assets/viewer-app/overlays/austria-boundary.json?v=20260723-unified1'
} = {}) {
  let austriaGeometry = null;
  let loaded = false;
  const loading = Promise.resolve()
    .then(() => fetchImpl(countriesUrl))
    .then((response) => {
      if (!response?.ok || typeof response?.json !== 'function') throw new Error('country overlay unavailable');
      return response.json();
    })
    .then((payload) => {
      const geometry = payload?.type === 'Feature' ? payload.geometry : payload?.geometry;
      if (!['Polygon', 'MultiPolygon'].includes(geometry?.type) || !geometryRings(geometry).length) {
        throw new Error('country overlay has no valid Austria geometry');
      }
      austriaGeometry = geometry;
      loaded = true;
      return austriaGeometry;
    })
    .catch((error) => {
      loaded = true;
      console.warn('Österreich-Grenze konnte nicht geladen werden', error);
      return null;
    });

  function datasetAt(lng, lat) {
    const point = [Number(lng), Number(lat)];
    if (!point.every(Number.isFinite)) return WORKSPACE_DATASET;
    if (austriaGeometry) return pointInGeometry(point, austriaGeometry) ? 'oesterreich' : WORKSPACE_DATASET;
    return !loaded && fallbackAustriaCandidate(point) ? 'oesterreich' : WORKSPACE_DATASET;
  }

  return {
    ready: () => loading,
    datasetAt,
    intersectsAustria: (bounds) => {
      if (austriaGeometry) return geometryIntersectsBounds(austriaGeometry, bounds);
      const value = normalizedBounds(bounds);
      return !loaded && Boolean(value)
        && value.east >= AUSTRIA_FALLBACK_BOUNDS[0]
        && value.west <= AUSTRIA_FALLBACK_BOUNDS[2]
        && value.north >= AUSTRIA_FALLBACK_BOUNDS[1]
        && value.south <= AUSTRIA_FALLBACK_BOUNDS[3];
    },
    containsAustria: (bounds) => {
      const value = normalizedBounds(bounds);
      if (!austriaGeometry || !value) return false;
      const corners = [
        [value.west, value.south],
        [value.east, value.south],
        [value.east, value.north],
        [value.west, value.north]
      ];
      return corners.every((point) => pointInGeometry(point, austriaGeometry))
        && !geometryBoundaryIntersectsBounds(austriaGeometry, value);
    },
    isReady: () => loaded
  };
}

export function austriaBasemapStyle() {
  return {
    version: 8,
    name: 'OpenKataster Österreich – basemap.at',
    sources: {
      'basemap-at': {
        type: 'raster',
        tiles: ['https://mapsneu.wien.gv.at/basemap/geolandbasemap/normal/google3857/{z}/{y}/{x}.png'],
        tileSize: 256,
        minzoom: 0,
        maxzoom: 19,
        attribution: 'Grundkarte: basemap.at'
      },
      'basemap-at-overlay': {
        type: 'raster',
        tiles: ['https://mapsneu.wien.gv.at/basemap/bmapoverlay/normal/google3857/{z}/{y}/{x}.png'],
        tileSize: 256,
        minzoom: 0,
        maxzoom: 20,
        attribution: 'Datenquelle: basemap.at'
      }
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#FFFDEE' } },
      {
        id: 'basemap-at-standard',
        type: 'raster',
        source: 'basemap-at',
        minzoom: 5.8,
        paint: {
          'raster-opacity': [
            'interpolate', ['linear'], ['zoom'],
            5.8, .84,
            15.7, .84,
            16.2, .62,
            17.2, .18
          ],
          'raster-saturation': -.22,
          'raster-contrast': -.14,
          'raster-brightness-min': .07,
          'raster-brightness-max': .99,
          'raster-fade-duration': 0
        }
      }
    ]
  };
}
