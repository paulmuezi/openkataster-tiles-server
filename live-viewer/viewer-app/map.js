export function createPlannerMap({ container, savedView }) {
  const hashView = parseHashView(window.location.hash);
  const view = hashView || savedView || { lng: 10.45, lat: 51.16, zoom: 4.05 };
  const map = new maplibregl.Map({
    container,
    style: '/viewer-assets/viewer-app/bkg-style.json?v=20260712-bkg-direct1',
    center: [view.lng, view.lat],
    zoom: view.zoom,
    bearing: 0,
    pitch: 0,
    minZoom: 3.2,
    maxZoom: 20,
    hash: true,
    attributionControl: false,
    dragRotate: false,
    touchPitch: false,
    pitchWithRotate: false,
    fadeDuration: 0
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-left');
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  map.touchPitch?.disable();
  map.keyboard?.disableRotation();
  map.scrollZoom.enable();
  map.on('rotate', () => {
    if (Math.abs(map.getBearing()) > 0.001) map.setBearing(0);
  });
  map.on('pitch', () => {
    if (Math.abs(map.getPitch()) > 0.001) map.setPitch(0);
  });
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
