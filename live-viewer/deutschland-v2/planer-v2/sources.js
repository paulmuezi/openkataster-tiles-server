export function createSourceController({ map, api, store, elements, layerController }) {
  const { sourceButton, sourcePanel, sourceList } = elements;
  let metadata = null;

  function sourceLine(text, href, suffix = '') {
    const link = href ? `<a href="${href}" target="_blank" rel="noopener">${text}</a>` : text;
    return `<li>${link}${suffix}</li>`;
  }

  function render() {
    const slug = layerController.currentStateSlug();
    const state = metadata?.states?.find((item) => item.slug === slug);
    const detail = map.getZoom() >= 16.7;
    const layers = store.getState().layers;
    const bkgVisible = !detail || (!layers.alkis && !layers.aerial);
    const rows = [sourceLine('© MapLibre', 'https://maplibre.org/')];
    if (bkgVisible) rows.push(sourceLine('© GeoBasis-DE / BKG', 'https://www.bkg.bund.de/', ' 2026 · CC BY 4.0'));
    if (detail && !bkgVisible && state?.quellenvermerk) rows.push(sourceLine(state.quellenvermerk, null, state.datenstand ? ` · Stand ${state.datenstand}` : ''));
    rows.push(sourceLine('© OpenPLZ', 'https://www.openplzapi.org/', ' · ODbL 1.0'));
    sourceList.innerHTML = rows.join('');
  }

  sourceButton.addEventListener('click', () => {
    const open = sourcePanel.hidden;
    sourcePanel.hidden = !open;
    sourceButton.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  map.on('moveend', render);
  store.subscribe((_state, reason) => { if (reason === 'layers') render(); });
  api.sources().then((data) => { metadata = data; render(); }).catch((error) => console.warn(error));
  return { render };
}
