const DATASETS = Object.freeze({
  deutschland: Object.freeze({
    id: 'deutschland',
    countryCode: 'DE',
    name: 'Deutschland',
    defaultView: Object.freeze({ lng: 10.45, lat: 51.16, zoom: 4.05 }),
    detailZoom: 17,
    nationalRegion: '',
    supportsPoi: true,
    terminology: Object.freeze({
      cadastre: 'ALKIS',
      cadastralDistrict: 'Gemarkung',
      parcel: 'Flurstück',
      parcelPlural: 'Flurstücke',
      parcelNumber: 'Flurstücksnummer',
      district: 'Flur'
    })
  }),
  oesterreich: Object.freeze({
    id: 'oesterreich',
    countryCode: 'AT',
    name: 'Österreich',
    defaultView: Object.freeze({ lng: 14.20, lat: 47.60, zoom: 6.6 }),
    detailZoom: 14,
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

export function datasetIdFromLocation(locationValue = globalThis.location) {
  const pathname = String(locationValue?.pathname || '');
  const match = pathname.match(/\/(?:embed|viewer)\/([^/?#]+)/);
  const queryDataset = new URLSearchParams(String(locationValue?.search || '')).get('dataset');
  const requested = String(queryDataset || match?.[1] || 'deutschland').trim().toLocaleLowerCase('de');
  return Object.hasOwn(DATASETS, requested) ? requested : 'deutschland';
}

export function viewerDatasetProfile(dataset = datasetIdFromLocation()) {
  return DATASETS[dataset] || DATASETS.deutschland;
}

export function datasetViewerUrl(locationValue, targetDataset) {
  const target = viewerDatasetProfile(targetDataset).id;
  const currentPath = String(locationValue?.pathname || '');
  const route = currentPath.startsWith('/embed/') ? 'embed' : 'viewer';
  const query = new URLSearchParams(String(locationValue?.search || ''));
  query.set('dataset', target);
  return `/${route}/${target}${query.size ? `?${query.toString()}` : ''}${String(locationValue?.hash || '')}`;
}

export function applyDatasetSwitchState(profile, root = document) {
  for (const button of root.querySelectorAll?.('[data-dataset-switch]') || []) {
    const active = button.dataset.datasetSwitch === profile.id;
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    if (active) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  }
}

export function applyDatasetTerminology(profile, root = document) {
  const terms = profile.terminology;
  root.documentElement?.setAttribute('data-dataset', profile.id);
  root.body?.setAttribute('data-dataset', profile.id);

  const addressInput = root.getElementById?.('addressInput');
  const searchPanel = root.getElementById?.('searchPanel');
  const searchModeButton = root.getElementById?.('searchModeButton');
  const parcelFields = root.getElementById?.('parcelFields');
  const gemarkungInput = root.getElementById?.('gemarkungInput');
  const flurInput = root.getElementById?.('flurInput');
  const parcelInput = root.getElementById?.('parcelInput');
  const searchSubmit = root.getElementById?.('searchSubmit');

  const searchLabel = profile.supportsPoi
    ? `Adresse, ${terms.parcel} oder POI suchen`
    : `Adresse oder ${terms.parcel} suchen`;
  if (addressInput) {
    addressInput.placeholder = searchLabel;
    addressInput.setAttribute('aria-label', searchLabel);
  }
  searchPanel?.setAttribute('aria-label', searchLabel);
  searchModeButton?.setAttribute('aria-label', `${terms.parcel}suche mit Feldern öffnen`);
  parcelFields?.setAttribute('aria-label', `${terms.parcel}suche`);
  if (gemarkungInput) gemarkungInput.placeholder = `${terms.cadastralDistrict} erforderlich`;
  if (parcelInput) parcelInput.placeholder = `${terms.parcel} erforderlich`;
  if (searchSubmit) {
    const label = searchSubmit.querySelector('span:last-child');
    if (label) label.textContent = `${terms.parcel} suchen`;
  }
  const districtField = flurInput?.closest('.clear-field');
  if (districtField) {
    districtField.hidden = !terms.district;
    flurInput.disabled = !terms.district;
    if (terms.district) flurInput.placeholder = `${terms.district} optional`;
  }

  for (const element of root.querySelectorAll?.('[data-dataset-term]') || []) {
    const term = element.dataset.datasetTerm;
    if (terms[term]) element.textContent = terms[term];
  }
  applyDatasetSwitchState(profile, root);
}

export function austriaBasemapStyle() {
  return {
    version: 8,
    name: 'OpenKataster Österreich – basemap.at',
    sources: {
      'basemap-at': {
        type: 'raster',
        tiles: ['https://mapsneu.wien.gv.at/basemap/bmapgrau/normal/google3857/{z}/{y}/{x}.png'],
        tileSize: 256,
        minzoom: 0,
        maxzoom: 19,
        attribution: 'Grundkarte: basemap.at'
      }
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#f5f5f2' } },
      { id: 'basemap-at-grau', type: 'raster', source: 'basemap-at', paint: { 'raster-fade-duration': 0 } }
    ]
  };
}
