import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { layers, namedFlavor } from '@protomaps/basemaps';

const scriptDirectory = fileURLToPath(new URL('.', import.meta.url));
const repositoryRoot = resolve(scriptDirectory, '../..');
const packageMetadata = JSON.parse(readFileSync(
  resolve(scriptDirectory, 'node_modules/@protomaps/basemaps/package.json'),
  'utf8'
));
const overviewMinZoom = 5;
const countryLabelMaxZoom = 5.8;
const federalStateLabelMinZoom = 5.55;
const federalStateMaxZoom = 10.5;
const majorLocalityMinZoom = 6.8;
const mediumLocalityMinZoom = 8;
const minorLocalityMinZoom = 9.5;
const subplaceMinZoom = 10.5;
const brandOrange = '#f86d14';
const availabilityCountries = ['DEU', 'AUT'];
const availabilityAssetVersion = '20260724-de-at1';
const federalStateLabelAssetVersion = '20260724-de-at1';

const flavor = {
  ...namedFlavor('light'),
  background: '#ffffff',
  earth: '#ffffff',
  park_a: '#e0ffd8',
  park_b: '#eaffd3',
  hospital: '#ffeaf4',
  industrial: '#ededed',
  school: '#ffeaf4',
  wood_a: '#e0ffd8',
  wood_b: '#eaffd3',
  pedestrian: '#ffffff',
  scrub_a: '#e0ffd8',
  scrub_b: '#eaffd3',
  glacier: '#f8fbfc',
  sand: '#f7f1dc',
  beach: '#f8edcf',
  aerodrome: '#f2f5f0',
  runway: '#ffffff',
  water: '#e2ffff',
  zoo: '#e0ffd8',
  military: '#f0ebeb',
  buildings: '#aaa9a7',
  railway: '#9da3a5',
  boundaries: '#b5b4b0',
  roads_label_minor: '#555555',
  roads_label_minor_halo: '#ffffff',
  roads_label_major: '#555555',
  roads_label_major_halo: '#ffffff',
  ocean_label: '#6d8f95',
  subplace_label: '#4a5b75',
  subplace_label_halo: '#ffffff',
  city_label: '#555555',
  city_label_halo: '#ffffff',
  state_label: '#333333',
  state_label_halo: '#ffffff',
  country_label: '#333333',
  address_label: '#666666',
  address_label_halo: '#ffffff',
  landcover: {
    grassland: '#eaffd3',
    barren: '#f8f1de',
    urban_area: '#ffeaf4',
    farmland: '#fffdee',
    glacier: '#f8fbfc',
    scrub: '#eaffd3',
    forest: '#eaffd3'
  }
};

const generatedLayers = layers('openkataster_europe', flavor, { lang: 'de' })
  .filter((layer) => layer.id !== 'landcover');
const industrialLayerIndex = generatedLayers.findIndex((layer) => (
  layer.id === 'landuse_industrial'
));
if (industrialLayerIndex >= 0) {
  generatedLayers.splice(industrialLayerIndex, 0, {
    id: 'landuse_residential',
    type: 'fill',
    source: 'openkataster_europe',
    'source-layer': 'landuse',
    minzoom: 9,
    filter: ['==', 'kind', 'residential'],
    paint: {
      'fill-color': '#ffeaf4',
      'fill-opacity': [
        'interpolate',
        ['linear'],
        ['zoom'],
        9,
        0,
        11,
        0.65,
        14,
        1
      ]
    }
  });
}
const regionLabelIndex = generatedLayers.findIndex((layer) => layer.id === 'places_region');
if (regionLabelIndex >= 0) {
  generatedLayers.splice(regionLabelIndex, 1);
}
const localityIndex = generatedLayers.findIndex((layer) => layer.id === 'places_locality');
if (localityIndex >= 0) {
  const originalLocality = generatedLayers[localityIndex];
  const cloneLocality = () => JSON.parse(JSON.stringify(originalLocality));
  const majorLocality = cloneLocality();
  majorLocality.id = 'places_locality-major';
  majorLocality.minzoom = majorLocalityMinZoom;
  majorLocality.filter = [
    'all',
    originalLocality.filter,
    ['>=', 'population_rank', 10]
  ];
  const mediumLocality = cloneLocality();
  mediumLocality.id = 'places_locality-medium';
  mediumLocality.minzoom = mediumLocalityMinZoom;
  mediumLocality.filter = [
    'all',
    originalLocality.filter,
    ['>=', 'population_rank', 7],
    ['<', 'population_rank', 10]
  ];
  const minorLocality = cloneLocality();
  minorLocality.id = 'places_locality-minor';
  minorLocality.minzoom = minorLocalityMinZoom;
  minorLocality.filter = [
    'all',
    originalLocality.filter,
    [
      'any',
      ['!has', 'population_rank'],
      ['<', 'population_rank', 7]
    ]
  ];
  generatedLayers.splice(
    localityIndex,
    1,
    majorLocality,
    mediumLocality,
    minorLocality
  );
}
const subplaceLabels = generatedLayers.find((layer) => layer.id === 'places_subplace');
if (subplaceLabels) {
  subplaceLabels.minzoom = subplaceMinZoom;
}
const regionBoundaries = generatedLayers.find((layer) => layer.id === 'boundaries');
if (regionBoundaries) {
  regionBoundaries.minzoom = federalStateMaxZoom;
  regionBoundaries.paint['line-color'] = '#e8e8e8';
}
const buildings = generatedLayers.find((layer) => layer.id === 'buildings');
if (buildings) {
  buildings.paint['fill-opacity'] = [
    'interpolate',
    ['linear'],
    ['zoom'],
    12,
    0.25,
    16.7,
    0.48,
    18,
    0.82
  ];
}
for (const layer of generatedLayers) {
  if (layer.type !== 'line' || !layer.id.includes('_casing')) continue;
  if (layer.id.includes('highway')) layer.paint['line-color'] = '#8f8f8f';
  else if (layer.id.includes('major')) layer.paint['line-color'] = '#999999';
  else layer.paint['line-color'] = '#d8d8d8';
}
const earthLayerIndex = generatedLayers.findIndex((layer) => layer.id === 'earth');
if (earthLayerIndex >= 0) {
  generatedLayers.splice(earthLayerIndex + 1, 0,
    {
      id: 'availability-germany-fill',
      type: 'fill',
      source: 'availability_europe',
      filter: ['==', 'ISO_A3', 'DEU'],
      paint: {
        'fill-color': '#fffdee',
        'fill-opacity': 1
      }
    },
    {
      id: 'availability-austria-fill',
      type: 'fill',
      source: 'availability_europe',
      filter: ['==', 'ISO_A3', 'AUT'],
      paint: {
        'fill-color': '#fffdee',
        'fill-opacity': 1
      }
    }
  );
}
const countryLabels = generatedLayers.find((layer) => layer.id === 'places_country');
if (countryLabels) {
  countryLabels.maxzoom = countryLabelMaxZoom;
  countryLabels.filter = [
    'all',
    countryLabels.filter,
    ['in', 'wikidata', 'Q183', 'Q40']
  ];
  countryLabels.paint['text-halo-color'] = '#ffffff';
}
const countryLabelIndex = generatedLayers.findIndex((layer) => layer.id === 'places_country');
if (countryLabelIndex >= 0) {
  generatedLayers.splice(
    countryLabelIndex,
    0,
    {
      id: 'availability-supported-region-boundaries',
      type: 'line',
      source: 'openkataster_europe',
      'source-layer': 'boundaries',
      minzoom: overviewMinZoom,
      maxzoom: federalStateMaxZoom,
      filter: [
        'all',
        ['==', 'kind', 'region'],
        ['==', 'kind_detail', 4]
      ],
      paint: {
        'line-color': brandOrange,
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          overviewMinZoom,
          0.8,
          7,
          1.1,
          federalStateMaxZoom,
          1.3
        ],
        'line-opacity': 0.9
      }
    },
    {
      id: 'availability-unavailable-countries-mask',
      type: 'fill',
      source: 'availability_europe',
      filter: ['!in', 'ISO_A3', ...availabilityCountries],
      paint: {
        'fill-color': '#ffffff',
        'fill-opacity': 1
      }
    },
    {
      id: 'availability-supported-countries-outline',
      type: 'line',
      source: 'availability_europe',
      minzoom: overviewMinZoom,
      maxzoom: federalStateMaxZoom,
      filter: ['in', 'ISO_A3', ...availabilityCountries],
      paint: {
        'line-color': brandOrange,
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          overviewMinZoom,
          1,
          7,
          1.35,
          federalStateMaxZoom,
          1.6
        ],
        'line-opacity': 0.95
      }
    },
    {
      id: 'availability-supported-region-labels',
      type: 'symbol',
      source: 'federal_state_labels_de_at',
      minzoom: federalStateLabelMinZoom,
      maxzoom: federalStateMaxZoom,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': ['Noto Sans Medium'],
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          federalStateLabelMinZoom,
          10,
          7,
          11.5,
          federalStateMaxZoom,
          13
        ],
        'text-letter-spacing': 0.02,
        'text-max-width': 10,
        'text-allow-overlap': false,
        'text-ignore-placement': false
      },
      paint: {
        'text-color': '#333333',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
        'text-halo-blur': 0.35
      }
    }
  );
}

const style = {
  version: 8,
  name: 'OpenKataster Europa',
  metadata: {
    'openkataster:profile': 'europe-de-at-bkg-v4',
    'openkataster:data-build': '20260723',
    'openkataster:available-countries': 'DE,AT',
    'openkataster:style-generator': `@protomaps/basemaps@${packageMetadata.version}`,
    'openkataster:assets': 'basemaps-assets@028c18f713baecad011301ff7a69acc39bcc2ae7',
    'openkataster:license': (
      'OpenStreetMap data © OpenStreetMap contributors, ODbL 1.0; '
      + 'ESA WorldCover 2020, CC BY 4.0'
    )
  },
  glyphs: '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/fonts/{fontstack}/{range}.pbf',
  sprite: '/viewer-assets/europe-basemap-assets-protomaps-028c18f7/sprites/v4/light',
  sources: {
    openkataster_europe: {
      type: 'vector',
      tiles: [
        '/api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=__OPENKATASTER_BASEMAP_VERSION__'
      ],
      minzoom: 0,
      maxzoom: 15,
      bounds: [5, 45.5, 18, 55.75],
      attribution: [
        '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
        '<a href="https://esa-worldcover.org/">© ESA WorldCover project 2020</a>',
        'Contains modified Copernicus Sentinel data (2020) processed by ESA WorldCover consortium',
        '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
      ].join(' · ')
    },
    availability_europe: {
      type: 'geojson',
      data: `/viewer-assets/viewer-app/overlays/europe-countries.json?v=${availabilityAssetVersion}`
    },
    federal_state_labels_de_at: {
      type: 'geojson',
      data: (
        '/viewer-assets/viewer-app/overlays/federal-state-labels-de-at.json'
        + `?v=${federalStateLabelAssetVersion}`
      )
    }
  },
  layers: generatedLayers
};

for (const layer of style.layers) {
  layer.metadata = {
    ...(layer.metadata || {}),
    'openkataster:basemap': 'europe'
  };
}

const outputPath = resolve(
  repositoryRoot,
  'live-viewer/europe-basemap-style-20260724-bkg3/style.json'
);
mkdirSync(resolve(outputPath, '..'), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(style, null, 2)}\n`);
console.log(`Wrote ${style.layers.length} layers to ${outputPath}`);
