import { austriaBasemapStyle } from './dataset.js?v=20260723-austria1';

// MapLibre 5.14 only starts dragPan with the primary mouse button. Forward
// middle-button drags to that handler so its inertia and move lifecycle stay intact.
export function enableMiddleMousePan(map, {
  canvas = map.getCanvas(),
  canvasContainer = map.getCanvasContainer(),
  eventTarget = window,
  documentTarget = document,
  MouseEventConstructor = MouseEvent
} = {}) {
  const translatedEvents = new WeakSet();
  let active = false;

  const translate = (type, source, buttons, target) => {
    const event = new MouseEventConstructor(type, {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: eventTarget,
      detail: source.detail,
      screenX: source.screenX,
      screenY: source.screenY,
      clientX: source.clientX,
      clientY: source.clientY,
      ctrlKey: false,
      shiftKey: false,
      altKey: false,
      metaKey: false,
      button: 0,
      buttons
    });
    translatedEvents.add(event);
    target.dispatchEvent(event);
  };

  const finish = (source) => {
    if (!active) return;
    active = false;
    translate('mouseup', source, 0, canvasContainer);
  };

  const onMouseMove = (event) => {
    if (translatedEvents.has(event) || !active) return;
    event.preventDefault();
    event.stopPropagation();
    if ((event.buttons & 4) !== 4) {
      finish(event);
      return;
    }
    translate('mousemove', event, 1, documentTarget);
  };

  const onMouseUp = (event) => {
    if (translatedEvents.has(event) || !active || event.button !== 1) return;
    event.preventDefault();
    event.stopPropagation();
    finish(event);
  };

  const onMouseDown = (event) => {
    if (translatedEvents.has(event) || event.button !== 1 || active) return;
    if (map.dragPan?.isEnabled?.() === false) return;
    event.preventDefault();
    event.stopPropagation();
    active = true;
    translate('mousedown', event, 1, canvasContainer);
  };

  const onAuxClick = (event) => {
    if (event.button !== 1) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const onBlur = () => {
    active = false;
  };

  const cleanup = () => {
    active = false;
    canvas.removeEventListener('mousedown', onMouseDown);
    canvas.removeEventListener('auxclick', onAuxClick);
    eventTarget.removeEventListener('mousemove', onMouseMove, true);
    eventTarget.removeEventListener('mouseup', onMouseUp, true);
    eventTarget.removeEventListener('blur', onBlur);
    map.off?.('remove', cleanup);
  };

  canvas.addEventListener('mousedown', onMouseDown, { passive: false });
  canvas.addEventListener('auxclick', onAuxClick, { passive: false });
  eventTarget.addEventListener('mousemove', onMouseMove, { capture: true, passive: false });
  eventTarget.addEventListener('mouseup', onMouseUp, { capture: true, passive: false });
  eventTarget.addEventListener('blur', onBlur);
  map.once?.('remove', cleanup);
  return cleanup;
}

export function createPlannerMap({ container, savedView, datasetProfile = { id: 'deutschland', defaultView: { lng: 10.45, lat: 51.16, zoom: 4.05 } } }) {
  const hashView = parseHashView(window.location.hash);
  const view = hashView || savedView || datasetProfile.defaultView;
  const map = new maplibregl.Map({
    container,
    style: datasetProfile.id === 'oesterreich'
      ? austriaBasemapStyle()
      : '/viewer-assets/viewer-app/bkg-style.json?v=20260723-austria1',
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
  enableMiddleMousePan(map);
  map.on('rotate', () => {
    if (Math.abs(map.getBearing()) > 0.001) map.setBearing(0);
  });
  map.on('pitch', () => {
    if (Math.abs(map.getPitch()) > 0.001) map.setPitch(0);
  });
  let resizeFrame = 0;
  const observer = new ResizeObserver(() => {
    const app = container.closest('.planner-app');
    if (app?.dataset.resizing === 'true' || app?.dataset.layoutTransitioning === 'true' || app?.dataset.shellTransitioning === 'true') return;
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
