export function createPlannerMap({ container, savedView }) {
  const hashView = parseHashView(window.location.hash);
  const view = hashView || savedView || { lng: 10.45, lat: 51.16, zoom: 5.25 };
  const map = new maplibregl.Map({
    container,
    style: '/viewer-assets/deutschland-v2/bkg-style.json?v=20260710-planer-v2-1',
    center: [view.lng, view.lat],
    zoom: view.zoom,
    minZoom: 4.2,
    maxZoom: 20,
    maxBounds: [[2.8, 45.0], [17.8, 57.2]],
    hash: true,
    attributionControl: false,
    fadeDuration: 0
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-left');
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  map.scrollZoom.enable();
  let resizeFrame = 0;
  const observer = new ResizeObserver(() => {
    const app = container.closest('.planner-app');
    if (app?.dataset.resizing === 'true' || app?.dataset.layoutTransitioning === 'true') return;
    window.cancelAnimationFrame(resizeFrame);
    resizeFrame = window.requestAnimationFrame(() => map.resize());
  });
  observer.observe(container);
  return map;
}

function parseHashView(hash) {
  const parts = String(hash || '').replace(/^#/, '').split('/').map(Number);
  if (parts.length < 3 || parts.some((value) => !Number.isFinite(value))) return null;
  return { zoom: parts[0], lat: parts[1], lng: parts[2] };
}
