import { debounce, deepCopy } from './utils.js';

const KEY_PREFIX = 'openkataster:planer-v2:v1';

function storageKey(dataset = 'deutschland') {
  const safeDataset = /^[a-z0-9_-]+$/.test(String(dataset || '')) ? String(dataset) : 'deutschland';
  return `${KEY_PREFIX}:${safeDataset}`;
}

export function readPersistedState(dataset = 'deutschland') {
  try {
    const current = localStorage.getItem(storageKey(dataset));
    // Retain the previous Germany key as a one-way compatibility fallback.
    const legacy = dataset === 'deutschland' ? localStorage.getItem(KEY_PREFIX) : null;
    const value = JSON.parse(current || legacy);
    if (!value || value.version !== 1 || Date.now() - Number(value.savedAt || 0) > 21 * 86400000) return null;
    return value;
  } catch (_) { return null; }
}

export function createPersistence({ map, store, dataset = 'deutschland' }) {
  const save = debounce(() => {
    const state = store.getState();
    const center = map.getCenter();
    const payload = {
      version: 1,
      savedAt: Date.now(),
      view: { lng: center.lng, lat: center.lat, zoom: map.getZoom() },
      layout: state.layout,
      layers: state.layers,
      export: state.export,
      selection: {
        parcels: state.selection.parcels.slice(0, 25).map(deepCopy).filter(Boolean),
        buildings: state.selection.buildings.slice(0, 25).map(deepCopy).filter(Boolean)
      }
    };
    localStorage.setItem(storageKey(dataset), JSON.stringify(payload));
  }, 180);
  map.on('moveend', save);
  store.subscribe((_state, reason) => { if (!['selection-loading', 'resize'].includes(reason)) save(); });
  return { save };
}
