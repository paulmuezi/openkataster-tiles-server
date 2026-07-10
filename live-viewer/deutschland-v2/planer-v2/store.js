export function createStore(initialState) {
  let state = structuredClone(initialState);
  const listeners = new Set();

  function getState() {
    return state;
  }

  function setState(patch, reason = 'update') {
    const next = typeof patch === 'function' ? patch(state) : patch;
    if (!next || next === state) return state;
    state = { ...state, ...next };
    for (const listener of listeners) listener(state, reason);
    return state;
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  return { getState, setState, subscribe };
}
