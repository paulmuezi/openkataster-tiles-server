export function createSourceController({
  map,
  api,
  store,
  elements,
  layerController,
  onStateCapabilities = () => {},
  showCompactAttribution = () => false,
  compactAttributionDurationMs = 5000
}) {
  const { osmAttribution, sourceButton, sourcePanel, sourceList } = elements;
  let metadata = null;
  let compactAttributionCollapsed = false;
  let compactAttributionTimer = 0;

  function activeOsmAttribution() {
    return (metadata?.attributions || []).find((attribution) => (
      attribution?.text
      && attribution?.href
      && String(attribution.href).startsWith('https://www.openstreetmap.org/')
    )) || null;
  }

  function renderCompactAttribution() {
    const attribution = activeOsmAttribution();
    if (attribution) {
      osmAttribution.textContent = attribution.text;
      osmAttribution.href = attribution.href;
    }
    osmAttribution.hidden = (
      !attribution
      || !sourcePanel.hidden
      || compactAttributionCollapsed
      || !showCompactAttribution()
    );
  }

  function clearCompactAttributionTimer() {
    if (!compactAttributionTimer) return;
    window.clearTimeout(compactAttributionTimer);
    compactAttributionTimer = 0;
  }

  function collapseCompactAttribution() {
    if (!activeOsmAttribution() || !showCompactAttribution()) return;
    clearCompactAttributionTimer();
    compactAttributionCollapsed = true;
    renderCompactAttribution();
  }

  function revealCompactAttribution() {
    if (!activeOsmAttribution() || !showCompactAttribution()) return;
    clearCompactAttributionTimer();
    compactAttributionCollapsed = false;
    renderCompactAttribution();
    compactAttributionTimer = window.setTimeout(
      collapseCompactAttribution,
      Math.max(0, Number(compactAttributionDurationMs) || 0)
    );
  }

  function closePanel() {
    sourcePanel.hidden = true;
    sourceButton.setAttribute('aria-expanded', 'false');
    renderCompactAttribution();
  }

  function appendRow(parts) {
    const row = document.createElement('li');
    for (const part of parts) {
      if (part.href) {
        const link = document.createElement('a');
        link.href = part.href;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = part.text;
        row.append(link);
      } else {
        row.append(document.createTextNode(part.text));
      }
    }
    sourceList.append(row);
  }

  function render() {
    const slug = layerController.currentStateSlug();
    const state = metadata?.states?.find((item) => item.slug === slug);
    onStateCapabilities(state || null);
    const detail = map.getZoom() >= 17;
    const bkgVisible = layerController.isBasemapVisible();
    const visibleLayers = store.getState?.()?.layers || {};
    sourceList.replaceChildren();
    const parts = [{ text: '© MapLibre', href: 'https://maplibre.org/' }];
    const sourceAttributions = new Set();
    const appendSource = (sourceParts) => {
      if (!sourceParts?.length) return;
      parts.push({ text: ' · ' }, ...sourceParts);
    };
    const appendAttribution = (text) => {
      const value = String(text || '').trim();
      if (!value || sourceAttributions.has(value)) return;
      sourceAttributions.add(value);
      appendSource([{ text: value }]);
    };
    if (bkgVisible) {
      appendSource([
        { text: '© GeoBasis-DE / ' },
        { text: 'BKG', href: 'https://www.bkg.bund.de/' },
        { text: ' 2026 ' },
        { text: 'CC BY 4.0', href: 'https://creativecommons.org/licenses/by/4.0/' }
      ]);
    }
    const cadastreAttribution = state?.rendering?.cadastre_raster?.attribution;
    if (detail && visibleLayers.alkis) {
      appendAttribution(cadastreAttribution || state?.quellenvermerk);
    }
    const aerialCapability = state?.rendering?.aerial_raster;
    if (detail && visibleLayers.aerial && aerialCapability?.tile_template) {
      appendAttribution(aerialCapability.attribution || state?.quellenvermerk);
    }
    appendSource([
      { text: '© OpenPLZ', href: 'https://www.openplzapi.org/' },
      { text: ', ' },
      { text: 'ODbL 1.0', href: 'https://opendatacommons.org/licenses/odbl/1-0/' }
    ]);
    for (const attribution of metadata?.attributions || []) {
      if (!attribution?.text || !attribution?.href) continue;
      appendSource([{ text: attribution.text, href: attribution.href }]);
    }
    appendRow(parts);
    renderCompactAttribution();
  }

  sourceButton.addEventListener('click', () => {
    const open = sourcePanel.hidden;
    sourcePanel.hidden = !open;
    sourceButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    renderCompactAttribution();
  });
  map.getCanvas?.()?.addEventListener?.(
    'pointerdown',
    collapseCompactAttribution,
    { passive: true }
  );
  map.getCanvas?.()?.addEventListener?.(
    'wheel',
    collapseCompactAttribution,
    { passive: true }
  );
  map.on('moveend', render);
  map.on('idle', render);
  store.subscribe((_state, reason) => { if (reason === 'layers') render(); });
  api.sources().then((data) => {
    metadata = data;
    layerController.setSourceMetadata?.(data);
    render();
    revealCompactAttribution();
  }).catch((error) => console.warn(error));
  return {
    render,
    closePanel,
    revealCompactAttribution,
    collapseCompactAttribution
  };
}
