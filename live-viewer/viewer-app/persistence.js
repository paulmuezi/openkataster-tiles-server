import { debounce, deepCopy } from './utils.js';

const KEY = 'openkataster:planer-v2:v1';

export function readPersistedState() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY));
    if (!value || value.version !== 1 || Date.now() - Number(value.savedAt || 0) > 21 * 86400000) return null;
    return value;
  } catch (_) { return null; }
}

export function createPersistence({ map, store }) {
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
    localStorage.setItem(KEY, JSON.stringify(payload));
  }, 180);
  map.on('moveend', save);
  store.subscribe((_state, reason) => { if (!['selection-loading', 'resize'].includes(reason)) save(); });
  return { save };
}
