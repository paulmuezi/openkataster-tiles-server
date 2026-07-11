export function createSourceController({ map, api, store, elements, layerController }) {
  const { sourceButton, sourcePanel, sourceList } = elements;
  let metadata = null;

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
    const detail = map.getZoom() >= 16.7;
    const bkgVisible = layerController.isBasemapVisible();
    sourceList.replaceChildren();
    appendRow([{ text: '© MapLibre', href: 'https://maplibre.org/' }]);
    if (bkgVisible) {
      appendRow([
        { text: '© GeoBasis-DE / ' },
        { text: 'BKG', href: 'https://www.bkg.bund.de/' },
        { text: ' 2026 ' },
        { text: 'CC BY 4.0', href: 'https://creativecommons.org/licenses/by/4.0/' }
      ]);
    }
    if (detail && !bkgVisible && state?.quellenvermerk) {
      appendRow([{ text: `${state.quellenvermerk}${state.datenstand ? ` · Stand ${state.datenstand}` : ''}` }]);
    }
    appendRow([
      { text: '© OpenPLZ', href: 'https://www.openplzapi.org/' },
      { text: ', ' },
      { text: 'ODbL 1.0', href: 'https://opendatacommons.org/licenses/odbl/1-0/' }
    ]);
  }

  sourceButton.addEventListener('click', () => {
    const open = sourcePanel.hidden;
    sourcePanel.hidden = !open;
    sourceButton.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  map.on('moveend', render);
  map.on('idle', render);
  store.subscribe((_state, reason) => { if (reason === 'layers') render(); });
  api.sources().then((data) => { metadata = data; render(); }).catch((error) => console.warn(error));
  return { render };
}
