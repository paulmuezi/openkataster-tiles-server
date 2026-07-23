import { createAnalyticsMarker } from './api.js?v=20260717-osm-poi-search2';
import { centerFromResult, debounce, escapeHtml, resultLabel } from './utils.js?v=20260711-search-context1';

export function structuredPoiAddress(result) {
  if (!(result?.search_scope === 'poi' || result?.kind === 'poi' || result?.result_type === 'poi')) return null;
  const feature = result?.feature && typeof result.feature === 'object'
    ? result.feature
    : result;
  const street = String(feature?.street || '').trim();
  const houseNumber = String(
    feature?.house_number
    || feature?.housenumber
    || feature?.houseNumber
    || ''
  ).trim();
  if (!street || !houseNumber) return null;
  return {
    street,
    houseNumber,
    postCode: String(feature?.post_code || feature?.postcode || feature?.postal_code || '').trim(),
    city: String(feature?.city || feature?.place || feature?.locality || '').trim()
  };
}

export function selectionPreferenceForSearchResult(result) {
  const scope = result?.search_scope;
  const kind = result?.kind;
  const resultType = result?.result_type;
  if (scope === 'poi' || kind === 'poi' || resultType === 'poi') return structuredPoiAddress(result) ? 'all' : null;
  if (scope === 'parcel' || kind === 'parcel') return 'parcel';
  if (resultType === 'address' || kind === 'address' || kind === 'building') return 'all';
  return null;
}

function compactAddressPart(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .replace(/ß/g, 'ss')
    .toLocaleLowerCase('de-DE')
    .replace(/[^a-z0-9]/g, '');
}

function addressPartsFromResult(result) {
  const address = result?.address && typeof result.address === 'object'
    ? result.address
    : (Array.isArray(result?.feature?.addresses) ? result.feature.addresses[0] : result);
  return {
    street: String(address?.street || '').trim(),
    houseNumber: String(
      address?.house_number
      || address?.housenumber
      || address?.houseNumber
      || ''
    ).trim()
  };
}

export function selectionCenterForPoiAddress(result, addressResults, fallbackCenter) {
  const expected = structuredPoiAddress(result);
  if (!expected || !Array.isArray(fallbackCenter) || fallbackCenter.length !== 2) return fallbackCenter;
  const fallback = fallbackCenter.map(Number);
  const match = (Array.isArray(addressResults) ? addressResults : []).find((candidate) => {
    const actual = addressPartsFromResult(candidate);
    return compactAddressPart(actual.street) === compactAddressPart(expected.street)
      && compactAddressPart(actual.houseNumber) === compactAddressPart(expected.houseNumber);
  });
  const center = centerFromResult(match);
  if (!center?.every(Number.isFinite)) return fallback;
  const latitudeScale = Math.max(0.2, Math.cos((fallback[1] * Math.PI) / 180));
  const approximateDistanceDegrees = Math.hypot(
    (center[0] - fallback[0]) * latitudeScale,
    center[1] - fallback[1]
  );
  return approximateDistanceDegrees <= 0.015 ? center : fallback;
}

export function searchResultScope(result) {
  if (result?.search_scope === 'poi' || result?.kind === 'poi' || result?.result_type === 'poi') return 'poi';
  if (result?.search_scope === 'parcel' || result?.kind === 'parcel' || result?.parcel_search) return 'parcel';
  return 'address';
}

export function searchResultTypeLabel(result) {
  const scope = searchResultScope(result);
  if (scope === 'parcel') return 'Flurstück';
  if (scope === 'poi') return 'POI';
  const type = String(result?.result_type || result?.kind || '').trim().toLocaleLowerCase('de-DE');
  if (type === 'place') return 'Ort';
  if (type === 'street') return 'Straße';
  return 'Adresse';
}

export function addressSuggestionResolutionContext(result) {
  const context = {};
  const state = String(result?.state || '').trim();
  if (state) context.state = state;
  const center = centerFromResult(result);
  if (center?.every(Number.isFinite)) {
    context.nearLon = center[0];
    context.nearLat = center[1];
  }
  return context;
}

export function committedAddressSuggestion(result, resolvedResults) {
  const center = centerFromResult(result);
  if (center?.every(Number.isFinite)) return result;
  return (Array.isArray(resolvedResults) ? resolvedResults : [])[0];
}

export function createSearchController({
  map,
  api,
  elements,
  selection,
  onOsmUse = () => {}
}) {
  const {
    searchControl, searchPanel, searchModeButton, parcelFields,
    addressInput, gemarkungInput, flurInput, parcelInput,
    searchSuggestions, gemarkungSuggestions, searchSubmit, searchResults, searchStatus
  } = elements;
  let searchRequest = null;
  let suggestionRequest = null;
  let gemarkungRequest = null;
  let suggestedResults = [];
  let activeSuggestion = -1;
  let selectedGemarkungState = '';
  let advancedOpen = false;
  let poiMarker = null;
  searchPanel.dataset.suggestionsOpen = 'false';

  function nearbySearchOptions(limit) {
    try {
      const center = map.getCenter();
      const nearLon = Number(center?.lng);
      const nearLat = Number(center?.lat);
      if (Number.isFinite(nearLon) && Number.isFinite(nearLat)) return { nearLon, nearLat, limit };
    } catch (_) {
      // Search remains usable before the map has finished initializing.
    }
    return { limit };
  }

  function setAdvanced(open) {
    advancedOpen = !!open;
    searchPanel.dataset.advanced = advancedOpen ? 'true' : 'false';
    searchControl.classList.toggle('is-advanced', advancedOpen);
    parcelFields.hidden = !advancedOpen;
    searchModeButton.classList.toggle('is-active', advancedOpen);
    searchModeButton.setAttribute('aria-expanded', advancedOpen ? 'true' : 'false');
    searchModeButton.setAttribute(
      'aria-label',
      advancedOpen ? 'Flurstückssuche mit Feldern schließen' : 'Flurstückssuche mit Feldern öffnen'
    );
    hideSuggestions();
    clearResults();
    if (advancedOpen) window.setTimeout(() => gemarkungInput.focus(), 0);
  }

  function setBusy(busy, message = '') {
    searchSubmit.disabled = busy;
    searchSubmit.classList.toggle('is-loading', busy);
    searchPanel.classList.toggle('is-loading', busy);
    searchStatus.hidden = !message;
    searchStatus.textContent = message;
  }

  function clearResults() {
    searchResults.hidden = true;
    searchResults.replaceChildren();
    searchStatus.hidden = true;
    searchStatus.textContent = '';
  }

  function hideSearchSuggestions() {
    suggestedResults = [];
    activeSuggestion = -1;
    searchSuggestions.hidden = true;
    searchSuggestions.replaceChildren();
    searchPanel.dataset.suggestionsOpen = 'false';
    addressInput.setAttribute('aria-expanded', 'false');
    addressInput.removeAttribute('aria-activedescendant');
  }

  function hideGemarkungSuggestions() {
    gemarkungSuggestions.hidden = true;
    gemarkungSuggestions.replaceChildren();
  }

  function hideSuggestions() {
    hideSearchSuggestions();
    hideGemarkungSuggestions();
  }

  function distinctParts(parts, primary = '') {
    const accepted = [];
    const primaryNormalized = primary.toLocaleLowerCase('de-DE');
    for (const value of parts) {
      const part = String(value || '').trim();
      if (!part || ['Adresse', 'Straße', 'Ort', 'Flurstück', 'POI'].includes(part)) continue;
      const normalized = part.toLocaleLowerCase('de-DE');
      if (primaryNormalized.includes(normalized)) continue;
      if (accepted.some((previous) => previous.toLocaleLowerCase('de-DE').includes(normalized))) continue;
      accepted.push(part);
    }
    return accepted;
  }

  function resultDisplay(result) {
    const rawLabel = String(result?.label || result?.value || 'Treffer').trim();
    const labelParts = rawLabel.split(',').map((part) => part.trim()).filter(Boolean);
    const primary = String(result?.primary_label || labelParts[0] || rawLabel).trim();
    const postCode = String(result?.post_code || result?.postcode || result?.postal_code || '').trim();
    const city = String(result?.city || result?.place || result?.locality || '').trim();
    const locality = [postCode, city].filter(Boolean).join(' ');
    const scope = searchResultScope(result);
    const secondaryParts = distinctParts([
      scope === 'poi' ? result?.category_label : '',
      result?.secondary_label,
      result?.subtitle,
      labelParts.slice(1).join(', '),
      locality,
      result?.state_label || result?.state
    ], primary);
    if (!secondaryParts.length) {
      const fullLabel = resultLabel(result);
      if (fullLabel !== primary) secondaryParts.push(fullLabel.replace(new RegExp(`^${primary.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*,?\\s*`), ''));
    }
    return {
      primary,
      secondary: secondaryParts.filter(Boolean).join(' · '),
      scopeLabel: searchResultTypeLabel(result)
    };
  }

  function resultRow(result, index, { option = false } = {}) {
    const { primary, secondary, scopeLabel } = resultDisplay(result);
    const optionAttributes = option
      ? ` id="search-suggestion-${index}" role="option" aria-selected="false"`
      : '';
    const scope = searchResultScope(result);
    return `<button type="button" data-index="${index}" data-search-scope="${scope}"${optionAttributes}><span class="search-result-primary">${escapeHtml(primary)}</span><span class="search-result-secondary"><span class="search-result-type search-result-type-${scope}">${scopeLabel}</span>${secondary ? `<span>${escapeHtml(secondary)}</span>` : ''}</span></button>`;
  }

  function renderResults(results, onPick = chooseResult) {
    searchResults.innerHTML = results.map((result, index) => resultRow(result, index)).join('');
    searchResults.hidden = !results.length;
    for (const button of searchResults.querySelectorAll('button[data-index]')) {
      button.classList.add('search-result');
      button.addEventListener('click', () => onPick(results[Number(button.dataset.index)]));
    }
  }

  function renderGemarkungSuggestions(results, onPick) {
    gemarkungSuggestions.innerHTML = results.map((result, index) => `<button type="button" data-index="${index}">${escapeHtml(resultLabel(result))}</button>`).join('');
    gemarkungSuggestions.hidden = !results.length;
    for (const button of gemarkungSuggestions.querySelectorAll('button[data-index]')) {
      button.addEventListener('click', () => onPick(results[Number(button.dataset.index)]));
    }
  }

  function setActiveSuggestion(index) {
    if (!suggestedResults.length) return;
    const count = suggestedResults.length;
    activeSuggestion = ((index % count) + count) % count;
    for (const button of searchSuggestions.querySelectorAll('[role="option"]')) {
      const active = Number(button.dataset.index) === activeSuggestion;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      if (active && typeof button.scrollIntoView === 'function') button.scrollIntoView({ block: 'nearest' });
    }
    addressInput.setAttribute('aria-activedescendant', `search-suggestion-${activeSuggestion}`);
  }

  function renderSearchSuggestions(results) {
    suggestedResults = results;
    activeSuggestion = -1;
    searchSuggestions.innerHTML = results.map((result, index) => resultRow(result, index, { option: true })).join('');
    searchSuggestions.hidden = !results.length;
    searchPanel.dataset.suggestionsOpen = results.length ? 'true' : 'false';
    addressInput.setAttribute('aria-expanded', results.length ? 'true' : 'false');
    addressInput.removeAttribute('aria-activedescendant');
    if (results.some((result) => searchResultScope(result) === 'poi')) onOsmUse();
    for (const button of searchSuggestions.querySelectorAll('[role="option"]')) {
      button.addEventListener('click', () => commitSuggestion(results[Number(button.dataset.index)]));
    }
  }

  async function requestSuggestions(query, { pickFirst = false } = {}) {
    suggestionRequest?.abort();
    if (query.length < 2) {
      hideSearchSuggestions();
      return [];
    }
    suggestionRequest = new AbortController();
    try {
      const data = await api.suggestSearch(
        { query, ...nearbySearchOptions(8) },
        suggestionRequest.signal
      );
      if (addressInput.value.trim() !== query) return [];
      const results = data.results || [];
      if (pickFirst && results[0]) {
        await commitSuggestion(results[0]);
      } else if (document.activeElement === addressInput) {
        renderSearchSuggestions(results);
      }
      return results;
    } catch (error) {
      if (error.name !== 'AbortError') {
        console.warn(error);
        if (document.activeElement === addressInput && addressInput.value.trim() === query) hideSearchSuggestions();
      }
      return [];
    }
  }

  const suggestSearch = debounce(() => requestSuggestions(addressInput.value.trim()), 180);

  const suggestGemarkungen = debounce(async () => {
    const query = gemarkungInput.value.trim();
    gemarkungRequest?.abort();
    if (query.length < 2) {
      hideGemarkungSuggestions();
      return;
    }
    gemarkungRequest = new AbortController();
    try {
      const data = await api.suggestGemarkungen(query, gemarkungRequest.signal);
      if (document.activeElement !== gemarkungInput || gemarkungInput.value.trim() !== query) return;
      renderGemarkungSuggestions(data.results || [], (result) => {
        const label = String(result.label || result.gemarkung || '').trim();
        gemarkungInput.value = label;
        selectedGemarkungState = result.state || '';
        gemarkungInput.removeAttribute('aria-invalid');
        hideGemarkungSuggestions();
        clearResults();
        parcelInput.focus();
      });
    } catch (error) {
      if (error.name !== 'AbortError') console.warn(error);
    }
  }, 80);

  function trackedAddressSearch(query, signal, limit = 12, analyticsQuery = '', resolutionContext = {}) {
    return api.searchAddress(
      { query, analyticsQuery, ...nearbySearchOptions(limit), ...resolutionContext, limit },
      signal,
      createAnalyticsMarker('address')
    );
  }

  async function submitStructuredParcel() {
    hideSuggestions();
    clearResults();
    searchRequest?.abort();
    searchRequest = new AbortController();
    const gemarkung = gemarkungInput.value.trim();
    const flur = flurInput.value.trim();
    const flurstueck = parcelInput.value.trim();
    const missingFields = [gemarkungInput, parcelInput].filter((input) => !input.value.trim());
    for (const input of [gemarkungInput, parcelInput]) {
      if (missingFields.includes(input)) input.setAttribute('aria-invalid', 'true');
      else input.removeAttribute('aria-invalid');
    }
    if (missingFields.length) {
      missingFields[0].focus();
      setBusy(false, 'Bitte Gemarkung und Flurstück eingeben.');
      return;
    }
    setBusy(true);
    try {
      const results = (await api.searchParcel(
        { gemarkung, flur, flurstueck, state: selectedGemarkungState },
        searchRequest.signal,
        createAnalyticsMarker('parcel')
      )).results || [];
      renderResults(results);
      const resultMessage = !flur && results.length > 1
        ? (results.length >= 12
          ? 'Viele Treffer – bitte Flur zur Eingrenzung eingeben.'
          : 'Mehrere Treffer – Flur zur Eingrenzung eingeben.')
        : '';
      setBusy(false, results.length ? resultMessage : 'Keine Treffer');
    } catch (error) {
      if (error.name !== 'AbortError') setBusy(false, error.message || 'Suche fehlgeschlagen');
    }
  }

  async function commitSuggestion(result) {
    const typedQuery = addressInput.value.trim();
    if (!typedQuery || !result) return;
    const scope = searchResultScope(result);
    const selectedQuery = String(result?.search_query || result?.query || result?.value || result?.label || typedQuery).trim();
    addressInput.value = selectedQuery;
    addressInput.removeAttribute('aria-invalid');
    hideSuggestions();
    clearResults();
    searchRequest?.abort();
    searchRequest = new AbortController();
    setBusy(true);
    try {
      let resolved;
      if (scope === 'parcel') {
        const parcelSearch = result?.parcel_search || {};
        if (!parcelSearch.gemarkung || !parcelSearch.flurstueck) throw new Error('Flurstück konnte nicht aufgelöst werden.');
        const data = await api.searchParcel(
          { ...parcelSearch, analyticsQuery: typedQuery },
          searchRequest.signal,
          createAnalyticsMarker('parcel')
        );
        resolved = (data.results || [])[0];
      } else if (scope === 'poi') {
        const poiId = String(result?.poi_id || '').trim();
        if (!poiId) throw new Error('Ort konnte nicht aufgelöst werden.');
        const data = await api.searchPoi(
          { poiId, analyticsQuery: typedQuery },
          searchRequest.signal,
          createAnalyticsMarker('poi')
        );
        resolved = (data.results || [])[0];
      } else {
        const data = await trackedAddressSearch(
          selectedQuery,
          searchRequest.signal,
          12,
          typedQuery,
          addressSuggestionResolutionContext(result)
        );
        resolved = committedAddressSuggestion(result, data.results || []);
      }
      if (!resolved) {
        setBusy(false, 'Keine Treffer');
        return;
      }
      setBusy(false);
      await chooseResult({ ...resolved, search_scope: scope });
    } catch (error) {
      if (error.name !== 'AbortError') setBusy(false, error.message || 'Suche fehlgeschlagen');
    }
  }

  async function chooseResult(result) {
    clearResults();
    const center = centerFromResult(result);
    if (!center) return;
    const scope = searchResultScope(result);
    const type = result.result_type || result.kind;
    const zoom = type === 'place' ? Number(result.zoom || 12.5) : type === 'street' ? Math.max(Number(result.zoom || 17.4), 17.4) : type === 'poi' ? Number(result.zoom || 17.5) : Number(result.zoom || 18.5);
    if (scope === 'poi') {
      onOsmUse();
      showPoiMarker(center);
    }
    else clearPoiMarker();
    const selectionPreference = selectionPreferenceForSearchResult(result);
    const selectionCenterPromise = scope === 'poi' && selectionPreference
      ? resolvePoiSelectionCenter(result, center)
      : Promise.resolve(center);
    map.flyTo({ center, zoom, duration: 1150, essential: true, curve: 1.25 });
    if (selectionPreference) {
      await new Promise((resolve) => map.once('moveend', resolve));
      const selectionCenter = await selectionCenterPromise;
      await selection.selectAt(
        { lng: selectionCenter[0], lat: selectionCenter[1] },
        false,
        selectionPreference === 'all' ? null : selectionPreference
      );
    }
  }

  async function resolvePoiSelectionCenter(result, fallbackCenter) {
    const address = structuredPoiAddress(result);
    if (!address) return fallbackCenter;
    const query = [
      `${address.street} ${address.houseNumber}`,
      [address.postCode, address.city].filter(Boolean).join(' ')
    ].filter(Boolean).join(', ');
    try {
      const data = await api.searchAddress({
        query,
        nearLon: fallbackCenter[0],
        nearLat: fallbackCenter[1],
        limit: 8
      }, searchRequest?.signal);
      return selectionCenterForPoiAddress(result, data.results || [], fallbackCenter);
    } catch (error) {
      if (error?.name !== 'AbortError') console.warn('POI-Adresse konnte nicht mit ALKIS verknüpft werden', error);
      return fallbackCenter;
    }
  }

  function clearPoiMarker() {
    poiMarker?.remove?.();
    poiMarker = null;
  }

  function showPoiMarker(center) {
    clearPoiMarker();
    const Marker = globalThis.maplibregl?.Marker;
    if (typeof Marker !== 'function') return;
    const element = document.createElement('span');
    element.className = 'poi-search-marker';
    element.setAttribute('aria-hidden', 'true');
    poiMarker = new Marker({ element, anchor: 'center' }).setLngLat(center).addTo(map);
  }

  async function searchAddress(query) {
    const normalizedQuery = String(query || '').trim();
    if (!normalizedQuery) return;
    addressInput.value = normalizedQuery;
    addressInput.removeAttribute('aria-invalid');
    hideSuggestions();
    clearResults();
    searchRequest?.abort();
    searchRequest = new AbortController();
    setBusy(true);
    try {
      const data = await trackedAddressSearch(normalizedQuery, searchRequest.signal);
      const results = data.results || [];
      renderResults(results);
      setBusy(false, results.length ? '' : 'Keine Treffer');
    } catch (error) {
      if (error.name !== 'AbortError') setBusy(false, error.message || 'Suche fehlgeschlagen');
    }
  }

  function handleSearchInput() {
    addressInput.removeAttribute('aria-invalid');
    hideSearchSuggestions();
    clearResults();
    clearPoiMarker();
    if (addressInput.value.trim().length >= 2) suggestSearch();
  }

  function handleParcelInput() {
    hideGemarkungSuggestions();
    clearResults();
    if (document.activeElement === gemarkungInput) suggestGemarkungen();
  }

  function closeSuggestionsOrAdvanced() {
    if (!searchSuggestions.hidden) {
      hideSearchSuggestions();
      return true;
    }
    if (!gemarkungSuggestions.hidden) {
      hideGemarkungSuggestions();
      return true;
    }
    if (advancedOpen) {
      setAdvanced(false);
      addressInput.focus();
      return true;
    }
    return false;
  }

  function handleSearchKeydown(event) {
    if (event.key === 'ArrowDown' && suggestedResults.length) {
      event.preventDefault();
      setActiveSuggestion(activeSuggestion + 1);
    } else if (event.key === 'ArrowUp' && suggestedResults.length) {
      event.preventDefault();
      setActiveSuggestion(activeSuggestion < 0 ? suggestedResults.length - 1 : activeSuggestion - 1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const chosenSuggestion = suggestedResults[activeSuggestion >= 0 ? activeSuggestion : 0];
      if (chosenSuggestion) commitSuggestion(chosenSuggestion);
      else requestSuggestions(addressInput.value.trim(), { pickFirst: true });
    } else if (event.key === 'Escape' && closeSuggestionsOrAdvanced()) {
      event.preventDefault();
    }
  }

  searchModeButton.addEventListener('click', () => setAdvanced(!advancedOpen));
  addressInput.addEventListener('input', handleSearchInput);
  addressInput.addEventListener('focus', () => {
    if (addressInput.value.trim().length >= 2) suggestSearch();
  });
  addressInput.addEventListener('keydown', handleSearchKeydown);
  gemarkungInput.addEventListener('input', () => {
    selectedGemarkungState = '';
    gemarkungInput.removeAttribute('aria-invalid');
    handleParcelInput();
  });
  flurInput.addEventListener('input', handleParcelInput);
  parcelInput.addEventListener('input', () => {
    parcelInput.removeAttribute('aria-invalid');
    handleParcelInput();
  });
  searchSubmit.addEventListener('click', submitStructuredParcel);
  for (const input of [gemarkungInput, flurInput, parcelInput]) {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        submitStructuredParcel();
      } else if (event.key === 'Escape' && closeSuggestionsOrAdvanced()) {
        event.preventDefault();
      }
    });
  }
  for (const button of document.querySelectorAll('[data-clear-target]')) {
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.clearTarget);
      if (target) {
        target.value = '';
        target.removeAttribute('aria-invalid');
        target.focus();
      }
      if (target === gemarkungInput) selectedGemarkungState = '';
      if (target === gemarkungInput || target === flurInput || target === parcelInput) handleParcelInput();
      else handleSearchInput();
    });
  }
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.search-control')) hideSuggestions();
  });
  setAdvanced(false);
  return { setAdvanced, submitParcel: submitStructuredParcel, searchAddress };
}
