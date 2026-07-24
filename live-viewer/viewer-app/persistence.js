import { debounce } from './utils.js';

const KEY_PREFIX = 'openkataster:planer-v2:v1';

function storageKey(dataset = 'deutschland') {
  const safeDataset = /^[a-z0-9_-]+$/.test(String(dataset || '')) ? String(dataset) : 'deutschland';
  return `${KEY_PREFIX}:${safeDataset}`;
}

export function readPersistedState(dataset = 'deutschland') {
  try {
    const candidates = [
      localStorage.getItem(storageKey(dataset)),
      dataset === 'deutschland' ? localStorage.getItem(KEY_PREFIX) : null,
      // One-way migration from the former, separate Austria workspace.
      dataset === 'deutschland' ? localStorage.getItem(storageKey('oesterreich')) : null
    ]
      .filter(Boolean)
      .map((serialized) => {
        try { return JSON.parse(serialized); } catch (_) { return null; }
      })
      .filter((value) => (
        value?.version === 1
        && Date.now() - Number(value.savedAt || 0) <= 21 * 86400000
      ))
      .sort((left, right) => Number(right.savedAt || 0) - Number(left.savedAt || 0));
    return candidates[0] || null;
  } catch (_) { return null; }
}

function compactSelectionReference(item, kind) {
  const reference = {
    kind,
    state: String(item?.state || item?.bundesland || '').trim(),
    source_db: String(item?.source_db || '').trim(),
    gml_id: String(item?.gml_id || item?.id || '').trim()
  };
  return reference.state && reference.source_db && reference.gml_id ? reference : null;
}

export function createPersistence({
  map,
  store,
  dataset = 'deutschland',
  exportWorkspace = () => store.getState().export
}) {
  const save = debounce(() => {
    const state = store.getState();
    const center = map.getCenter();
    const payload = {
      version: 1,
      savedAt: Date.now(),
      view: { lng: center.lng, lat: center.lat, zoom: map.getZoom() },
      layout: state.layout,
      layers: state.layers,
      layerWorkspace: state.layerWorkspace || { visible: state.layers },
      export: exportWorkspace(),
      selection: {
        parcels: state.selection.parcels.slice(0, 25).map((item) => compactSelectionReference(item, 'parcel')).filter(Boolean),
        buildings: state.selection.buildings.slice(0, 25).map((item) => compactSelectionReference(item, 'building')).filter(Boolean)
      }
    };
    try {
      localStorage.setItem(storageKey(dataset), JSON.stringify(payload));
    } catch (error) {
      console.warn('Kartenstand konnte nicht lokal gespeichert werden.', error);
    }
  }, 180);
  map.on('moveend', save);
  store.subscribe((_state, reason) => { if (!['selection-loading', 'resize'].includes(reason)) save(); });
  return { save };
}
