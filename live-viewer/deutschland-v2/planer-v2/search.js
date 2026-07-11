import { centerFromResult, debounce, escapeHtml, resultLabel } from './utils.js?v=20260711-search-context1';

export function createSearchController({ map, api, store, elements, selection }) {
  const {
    searchButton, searchPanel, searchClose, searchMode, addressFields, parcelFields,
    placeInput, streetInput, houseInput, gemarkungInput, flurInput, parcelInput,
    placeSuggestions, streetSuggestions, searchSubmit, searchResults, searchStatus
  } = elements;
  let searchRequest = null;
  let placeRequest = null;
  let streetRequest = null;
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
    searchResults.hidden = true;
  }

  function setBusy(busy, message = '') {
    searchSubmit.disabled = busy;
    searchSubmit.classList.toggle('is-loading', busy);
    searchStatus.hidden = !message;
    searchStatus.textContent = message;
  }

  function clearSuggestions() {
    placeSuggestions.hidden = true;
    streetSuggestions.hidden = true;
    placeSuggestions.replaceChildren();
    streetSuggestions.replaceChildren();
  }

  function renderSuggestions(container, results, onPick) {
    container.innerHTML = results.map((result, index) => `<button type="button" data-index="${index}">${escapeHtml(resultLabel(result))}</button>`).join('');
    container.hidden = !results.length;
    for (const button of container.querySelectorAll('button')) button.addEventListener('click', () => onPick(results[Number(button.dataset.index)]));
  }

  const suggestPlaces = debounce(async () => {
    const query = placeInput.value.trim();
    placeRequest?.abort();
    if (query.length < 2 || streetInput.value.trim()) { placeSuggestions.hidden = true; return; }
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
    if (place.length < 2 || query.length < 2) { streetSuggestions.hidden = true; return; }
    streetRequest = new AbortController();
    try {
      const data = await api.suggestStreets(place, query, selectedPlaceState, streetRequest.signal);
      if (document.activeElement !== streetInput || streetInput.value.trim() !== query) return;
      renderSuggestions(streetSuggestions, data.results || [], (result) => {
        streetInput.value = result.value || result.label || '';
        clearSuggestions();
        houseInput.focus();
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

  function renderResults(results) {
    searchResults.innerHTML = results.map((result, index) => `<button type="button" class="search-result" data-index="${index}">${escapeHtml(resultLabel(result))}</button>`).join('');
    searchResults.hidden = !results.length;
    for (const button of searchResults.querySelectorAll('.search-result')) button.addEventListener('click', () => chooseResult(results[Number(button.dataset.index)]));
  }

  async function chooseResult(result) {
    searchResults.hidden = true;
    const center = centerFromResult(result);
    if (!center) return;
    const type = result.result_type || result.kind;
    if (type === 'place') {
      placeInput.value = result.value || result.label || placeInput.value;
      selectedPlaceState = result.state || '';
    }
    const zoom = type === 'place' ? Number(result.zoom || 12.5) : type === 'street' ? Math.max(Number(result.zoom || 17.4), 17.4) : Number(result.zoom || 18.5);
    map.flyTo({ center, zoom, duration: 1150, essential: true, curve: 1.25 });
    const featureType = result.kind === 'parcel' ? 'parcel' : result.kind === 'building' || result.kind === 'address' || result.result_type === 'address' ? 'building' : null;
    if (featureType && store.getState().access.pro) {
      await new Promise((resolve) => map.once('moveend', resolve));
      selection.selectAt({ lng: center[0], lat: center[1] }, false, featureType);
    }
  }

  searchButton.addEventListener('click', () => setOpen(searchPanel.hidden));
  searchClose.addEventListener('click', () => setOpen(false));
  searchMode.addEventListener('change', () => setMode(searchMode.value));
  placeInput.addEventListener('input', () => { selectedPlaceState = ''; suggestPlaces(); });
  streetInput.addEventListener('input', suggestStreets);
  houseInput.addEventListener('input', clearSuggestions);
  searchSubmit.addEventListener('click', submit);
  for (const input of [placeInput, streetInput, houseInput, gemarkungInput, flurInput, parcelInput]) input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } });
  for (const button of document.querySelectorAll('[data-clear-target]')) button.addEventListener('click', () => {
    const target = document.getElementById(button.dataset.clearTarget);
    if (target) { target.value = ''; target.focus(); }
    if (target === placeInput) selectedPlaceState = '';
    clearSuggestions();
  });
  document.addEventListener('click', (event) => { if (!event.target.closest('.search-control')) clearSuggestions(); });
  setMode(searchMode.value);
  return { setOpen, submit };
}
