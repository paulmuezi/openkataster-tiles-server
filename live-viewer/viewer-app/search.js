import { centerFromResult, debounce, escapeHtml, resultLabel } from './utils.js?v=20260711-search-context1';

export function createSearchController({ map, api, store, layout, elements, selection }) {
  const {
    searchButton, searchPanel, searchClose, searchMode, addressFields, parcelFields,
    placeInput, streetInput, houseInput, gemarkungInput, flurInput, parcelInput,
    placeSuggestions, streetSuggestions, gemarkungSuggestions, searchSubmit, searchResults, searchStatus
  } = elements;
  let searchRequest = null;
  let placeRequest = null;
  let streetRequest = null;
  let gemarkungRequest = null;
  let selectedPlaceState = '';

  function setOpen(open) {
    searchPanel.hidden = !open;
    searchButton.classList.toggle('is-active', open);
    searchButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) window.setTimeout(() => (searchMode.value === 'parcel' ? gemarkungInput : placeInput).focus(), 0);
  }

  function setMode(mode) {
    const parcelMode = mode === 'parcel';
    addressFields.hidden = parcelMode;
    parcelFields.hidden = !parcelMode;
    clearSuggestions();
  }

  function setBusy(busy, message = '') {
    searchSubmit.disabled = busy;
    searchSubmit.classList.toggle('is-loading', busy);
    searchStatus.hidden = !message;
    searchStatus.textContent = message;
  }

  function clearResults() {
    searchResults.hidden = true;
    searchResults.replaceChildren();
    searchStatus.hidden = true;
    searchStatus.textContent = '';
  }

  function clearSuggestions() {
    placeSuggestions.hidden = true;
    streetSuggestions.hidden = true;
    gemarkungSuggestions.hidden = true;
    placeSuggestions.replaceChildren();
    streetSuggestions.replaceChildren();
    gemarkungSuggestions.replaceChildren();
    clearResults();
  }

  function renderResults(results, onPick = chooseResult) {
    searchResults.innerHTML = results.map((result, index) => `<button type="button" class="search-result" data-index="${index}">${escapeHtml(resultLabel(result))}</button>`).join('');
    searchResults.hidden = !results.length;
    for (const button of searchResults.querySelectorAll('.search-result')) {
      button.addEventListener('click', () => onPick(results[Number(button.dataset.index)]));
    }
  }

  function renderSuggestions(container, results, onPick = chooseResult) {
    container.hidden = true;
    container.replaceChildren();
    renderResults(results, onPick);
  }

  const suggestPlaces = debounce(async () => {
    const query = placeInput.value.trim();
    placeRequest?.abort();
    if (query.length < 2 || streetInput.value.trim()) return;
    placeRequest = new AbortController();
    try {
      const data = await api.suggestPlaces(query, placeRequest.signal);
      if (document.activeElement !== placeInput || placeInput.value.trim() !== query) return;
      renderSuggestions(placeSuggestions, data.results || [], (result) => {
        placeInput.value = result.value || result.label || '';
        selectedPlaceState = result.state || '';
        clearSuggestions();
        streetInput.focus();
      });
    } catch (error) { if (error.name !== 'AbortError') console.warn(error); }
  }, 80);

  const suggestStreets = debounce(async () => {
    const place = placeInput.value.trim();
    const query = streetInput.value.trim();
    streetRequest?.abort();
    if (place.length < 2 || query.length < 2 || houseInput.value.trim()) return;
    streetRequest = new AbortController();
    try {
      const data = await api.suggestStreets(place, query, selectedPlaceState, streetRequest.signal);
      if (document.activeElement !== streetInput || streetInput.value.trim() !== query) return;
      renderSuggestions(streetSuggestions, data.results || [], (result) => {
        streetInput.value = result.value || result.label || '';
        selectedPlaceState = result.state || selectedPlaceState;
        clearSuggestions();
        houseInput.focus();
      });
    } catch (error) { if (error.name !== 'AbortError') console.warn(error); }
  }, 80);

  const suggestGemarkungen = debounce(async () => {
    const query = gemarkungInput.value.trim();
    gemarkungRequest?.abort();
    if (query.length < 2) return;
    gemarkungRequest = new AbortController();
    try {
      const data = await api.suggestGemarkungen(query, gemarkungRequest.signal);
      if (document.activeElement !== gemarkungInput || gemarkungInput.value.trim() !== query) return;
      renderSuggestions(gemarkungSuggestions, data.results || [], (result) => {
        const label = String(result.label || result.gemarkung || '').trim();
        const number = String(result.gemarkungsnummer || '').trim();
        gemarkungInput.value = number && label.endsWith(` (${number})`)
          ? label.slice(0, -(number.length + 3)).trim()
          : label;
        clearSuggestions();
        flurInput.focus();
      });
    } catch (error) { if (error.name !== 'AbortError') console.warn(error); }
  }, 80);

  async function submit() {
    clearSuggestions();
    searchRequest?.abort();
    searchRequest = new AbortController();
    setBusy(true);
    try {
      let results = [];
      if (searchMode.value === 'parcel') {
        const gemarkung = gemarkungInput.value.trim();
        const flur = flurInput.value.trim();
        const flurstueck = parcelInput.value.trim();
        if (!gemarkung || !flur || !flurstueck) throw new Error('Bitte Gemarkung, Flur und Flurstück eingeben.');
        results = (await api.searchParcel({ gemarkung, flur, flurstueck }, searchRequest.signal)).results || [];
      } else {
        const place = placeInput.value.trim();
        const street = streetInput.value.trim();
        const houseNumber = houseInput.value.trim();
        if (!place) throw new Error('Bitte Ort eingeben.');
        if (!street && !houseNumber) results = (await api.suggestPlaces(place, searchRequest.signal)).results || [];
        else if (street && !houseNumber) results = (await api.suggestStreets(place, street, selectedPlaceState, searchRequest.signal)).results || [];
        else results = (await api.searchAddress({ place, street, houseNumber, state: selectedPlaceState }, searchRequest.signal)).results || [];
      }
      renderResults(results);
      setBusy(false, results.length ? '' : 'Keine Treffer');
    } catch (error) {
      if (error.name !== 'AbortError') setBusy(false, error.message || 'Suche fehlgeschlagen');
    }
  }

  async function chooseResult(result) {
    clearResults();
    const center = centerFromResult(result);
    if (!center) return;
    const type = result.result_type || result.kind;
    if (type === 'place') {
      placeInput.value = result.value || result.label || placeInput.value;
      selectedPlaceState = result.state || '';
    } else if (type === 'street') {
      streetInput.value = result.value || result.label || streetInput.value;
      selectedPlaceState = result.state || selectedPlaceState;
    }
    const zoom = type === 'place' ? Number(result.zoom || 12.5) : type === 'street' ? Math.max(Number(result.zoom || 17.4), 17.4) : Number(result.zoom || 18.5);
    map.flyTo({ center, zoom, duration: 1150, essential: true, curve: 1.25 });
    const featureType = result.kind === 'parcel' ? 'parcel' : result.kind === 'building' || result.kind === 'address' || result.result_type === 'address' ? 'building' : null;
    if (featureType) {
      await new Promise((resolve) => map.once('moveend', resolve));
      if (store.getState().access.pro) selection.selectAt({ lng: center[0], lat: center[1] }, false, featureType);
      else selection.flash(result, featureType);
    }
  }

  function handleAddressInput({ changedPlace = false } = {}) {
    if (changedPlace) selectedPlaceState = '';
    clearSuggestions();
    const place = placeInput.value.trim();
    const street = streetInput.value.trim();
    if (place && street && !houseInput.value.trim()) suggestStreets();
    else if (place && !street) suggestPlaces();
  }

  function handleParcelInput() {
    clearSuggestions();
    if (document.activeElement === gemarkungInput) suggestGemarkungen();
  }

  searchButton.addEventListener('click', () => layout.setTool('search'));
  searchClose.addEventListener('click', () => {
    if (store.getState().activeTool === 'search') layout.setTool('search');
    else setOpen(false);
  });
  searchMode.addEventListener('change', () => setMode(searchMode.value));
  placeInput.addEventListener('input', () => handleAddressInput({ changedPlace: true }));
  streetInput.addEventListener('input', handleAddressInput);
  houseInput.addEventListener('input', handleAddressInput);
  gemarkungInput.addEventListener('input', handleParcelInput);
  flurInput.addEventListener('input', handleParcelInput);
  parcelInput.addEventListener('input', handleParcelInput);
  searchSubmit.addEventListener('click', submit);
  for (const input of [placeInput, streetInput, houseInput, gemarkungInput, flurInput, parcelInput]) {
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } });
  }
  for (const button of document.querySelectorAll('[data-clear-target]')) {
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.clearTarget);
      if (target) { target.value = ''; target.focus(); }
      if (target === placeInput) selectedPlaceState = '';
      if (target === gemarkungInput || target === flurInput || target === parcelInput) handleParcelInput();
      else handleAddressInput();
    });
  }
  document.addEventListener('click', (event) => { if (!event.target.closest('.search-control')) clearSuggestions(); });
  store.subscribe((state, reason) => {
    if (reason === 'tool') setOpen(state.activeTool === 'search');
  });
  setMode(searchMode.value);
  return { setOpen, submit };
}
