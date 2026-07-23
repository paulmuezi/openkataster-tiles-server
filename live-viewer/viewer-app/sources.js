export function createSourceController({
  map,
  api,
  store,
  elements,
  layerController,
  datasetProfile = { detailZoom: 17 },
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
    const detail = map.getZoom() >= Number(layerController.currentDetailZoom?.() || datasetProfile.detailZoom || 17);
    const aerialDetail = map.getZoom() >= Number(layerController.currentAerialZoom?.() || datasetProfile.aerialZoom || datasetProfile.detailZoom || 17);
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
    const appendGermanyBasemap = () => appendSource([
      { text: '© GeoBasis-DE / ' },
      { text: 'BKG', href: 'https://www.bkg.bund.de/' },
      { text: ' 2026 ' },
      { text: 'CC BY 4.0', href: 'https://creativecommons.org/licenses/by/4.0/' }
    ]);
    if (datasetProfile.unified) {
      // Both bounded national basemaps can be visible in the same viewport.
      // Listing both is intentionally conservative and avoids missing a
      // mandatory credit along the German-Austrian border.
      appendGermanyBasemap();
      appendAttribution('Grundkarte: basemap.at');
    } else if (bkgVisible) {
      const basemapAttribution = state?.rendering?.basemap_raster?.attribution;
      if (basemapAttribution) appendAttribution(basemapAttribution);
      else appendGermanyBasemap();
    }
    const cadastreAttribution = state?.rendering?.cadastre_raster?.attribution;
    const cadastreVectorAttribution = state?.rendering?.cadastre_vector?.attribution;
    if (detail && visibleLayers.alkis) {
      appendAttribution(cadastreAttribution || cadastreVectorAttribution || state?.quellenvermerk);
    }
    if (datasetProfile.unified && visibleLayers.alkis) {
      const zoom = map.getZoom();
      const austriaDetailZoom = Number(datasetProfile.detailZoomByRegion?.oesterreich || 16);
      const austriaState = metadata?.states?.find((item) => item.slug === 'oesterreich');
      if (zoom >= austriaDetailZoom && layerController.viewportIntersectsAustria?.()) {
        appendAttribution(
          austriaState?.rendering?.cadastre_vector?.attribution
          || austriaState?.quellenvermerk
          || '© BEV'
        );
      }
      if (zoom >= 17 && !layerController.viewportInsideAustria?.()) {
        appendAttribution('© GeoBasis-DE / Landesvermessungsverwaltungen');
      }
    }
    const aerialCapability = state?.rendering?.aerial_raster;
    if (aerialDetail && visibleLayers.aerial && aerialCapability?.tile_template) {
      appendAttribution(aerialCapability.attribution || state?.quellenvermerk);
    }
    if (layerController.currentDataset?.() !== 'oesterreich') {
      appendSource([
        { text: '© OpenPLZ', href: 'https://www.openplzapi.org/' },
        { text: ', ' },
        { text: 'ODbL 1.0', href: 'https://opendatacommons.org/licenses/odbl/1-0/' }
      ]);
    }
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
