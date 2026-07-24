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
const regionDetailMinZoom = 5.8;

const flavor = {
  ...namedFlavor('light'),
  background: '#f3f2ee',
  earth: '#fffdee',
  park_a: '#e7f3df',
  park_b: '#d8edce',
  hospital: '#f6eeee',
  industrial: '#efefed',
  school: '#f4f1e8',
  wood_a: '#e0efd8',
  wood_b: '#d2e8c8',
  pedestrian: '#f5f2e9',
  scrub_a: '#e8f0dc',
  scrub_b: '#dbe9cd',
  glacier: '#f8fbfc',
  sand: '#f7f1dc',
  beach: '#f8edcf',
  aerodrome: '#efefef',
  runway: '#f8f8f8',
  water: '#dcefff',
  zoo: '#e4f1e0',
  military: '#f0ebeb',
  buildings: '#e4e2dd',
  railway: '#9da3a5',
  boundaries: '#b5b4b0',
  roads_label_minor: '#656565',
  roads_label_minor_halo: '#ffffff',
  roads_label_major: '#4f4f4f',
  roads_label_major_halo: '#ffffff',
  ocean_label: '#6688a6',
  subplace_label: '#666666',
  subplace_label_halo: '#ffffff',
  city_label: '#333333',
  city_label_halo: '#ffffff',
  state_label: '#696969',
  state_label_halo: '#ffffff',
  country_label: '#333333',
  address_label: '#666666',
  address_label_halo: '#ffffff',
  landcover: {
    grassland: '#eaf4df',
    barren: '#f8f1de',
    urban_area: '#efefed',
    farmland: '#fff5cf',
    glacier: '#f8fbfc',
    scrub: '#e8f0dc',
    forest: '#dcebd4'
  }
};

const generatedLayers = layers('openkataster_europe', flavor, { lang: 'de' })
  .filter((layer) => layer.id !== 'landcover');
const regionLabels = generatedLayers.find((layer) => layer.id === 'places_region');
if (regionLabels) regionLabels.minzoom = regionDetailMinZoom;
const regionBoundaries = generatedLayers.find((layer) => layer.id === 'boundaries');
if (regionBoundaries) regionBoundaries.minzoom = regionDetailMinZoom;

const style = {
  version: 8,
  name: 'OpenKataster Europa',
  metadata: {
    'openkataster:profile': 'europe-v1',
    'openkataster:data-build': '20260723',
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
      bounds: [-25, 34, 45, 72],
      attribution: [
        '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
        '<a href="https://esa-worldcover.org/">© ESA WorldCover project 2020</a>',
        'Contains modified Copernicus Sentinel data (2020) processed by ESA WorldCover consortium',
        '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
      ].join(' · ')
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

const outputPath = resolve(repositoryRoot, 'live-viewer/europe-basemap-style-20260724/style.json');
mkdirSync(resolve(outputPath, '..'), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(style, null, 2)}\n`);
console.log(`Wrote ${style.layers.length} layers to ${outputPath}`);
