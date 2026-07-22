import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { enableMiddleMousePan } from '../live-viewer/viewer-app/map.js';

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
    this.dispatchedEvents = [];
  }

  addEventListener(type, listener, options = {}) {
    const listeners = this.listeners.get(type) || [];
    listeners.push({ listener, capture: options === true || options.capture === true });
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener, options = {}) {
    const capture = options === true || options.capture === true;
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter(
      (candidate) => candidate.listener !== listener || candidate.capture !== capture
    ));
  }

  dispatch(type, event) {
    for (const { listener } of [...(this.listeners.get(type) || [])]) listener(event);
  }

  dispatchEvent(event) {
    this.dispatchedEvents.push(event);
    this.dispatch(event.type, event);
    return !event.defaultPrevented;
  }

  listenerCount(type) {
    return (this.listeners.get(type) || []).length;
  }
}

class FakeMouseEvent {
  constructor(type, properties = {}) {
    this.type = type;
    this.defaultPrevented = false;
    this.propagationStopped = false;
    Object.assign(this, properties);
  }

  preventDefault() {
    this.defaultPrevented = true;
  }

  stopPropagation() {
    this.propagationStopped = true;
  }
}

function mouseEvent(properties = {}) {
  return new FakeMouseEvent(properties.type || 'mousedown', {
    button: 0,
    buttons: 0,
    clientX: 0,
    clientY: 0,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    metaKey: false,
    ...properties
  });
}

const canvas = new FakeEventTarget();
const canvasContainer = new FakeEventTarget();
const eventTarget = new FakeEventTarget();
const documentTarget = new FakeEventTarget();
let removeListener = null;
const map = {
  getCanvas: () => canvas,
  getCanvasContainer: () => canvasContainer,
  dragPan: { isEnabled: () => true },
  once(type, listener) {
    if (type === 'remove') removeListener = listener;
  },
  off(type, listener) {
    if (type === 'remove' && removeListener === listener) removeListener = null;
  }
};

const cleanup = enableMiddleMousePan(map, {
  canvas,
  canvasContainer,
  eventTarget,
  documentTarget,
  MouseEventConstructor: FakeMouseEvent
});

const leftDown = mouseEvent({ button: 0, buttons: 1, clientX: 20, clientY: 30 });
canvas.dispatch('mousedown', leftDown);
assert.equal(leftDown.defaultPrevented, false, 'Die linke Maustaste muss unverändert bleiben.');
assert.equal(canvasContainer.dispatchedEvents.length, 0);

const middleDown = mouseEvent({
  button: 1,
  buttons: 4,
  clientX: 100,
  clientY: 100,
  ctrlKey: true,
  shiftKey: true
});
canvas.dispatch('mousedown', middleDown);
assert.equal(middleDown.defaultPrevented, true, 'Mittelklick-Autoscroll muss verhindert werden.');
assert.equal(middleDown.propagationStopped, true);
assert.equal(canvasContainer.dispatchedEvents.length, 1);
const translatedDown = canvasContainer.dispatchedEvents[0];
assert.equal(translatedDown.type, 'mousedown');
assert.equal(translatedDown.button, 0);
assert.equal(translatedDown.buttons, 1);
assert.equal(translatedDown.clientX, 100);
assert.equal(translatedDown.clientY, 100);
assert.equal(translatedDown.ctrlKey, false, 'Ctrl+Mitteltaste darf keine Rotation auslösen.');
assert.equal(translatedDown.shiftKey, false, 'Shift+Mitteltaste darf keinen Box-Zoom auslösen.');

const middleMove = mouseEvent({
  type: 'mousemove',
  button: 0,
  buttons: 4,
  clientX: 125,
  clientY: 90,
  ctrlKey: true,
  shiftKey: true
});
eventTarget.dispatch('mousemove', middleMove);
assert.equal(middleMove.defaultPrevented, true);
assert.equal(documentTarget.dispatchedEvents.length, 1);
const translatedMove = documentTarget.dispatchedEvents[0];
assert.equal(translatedMove.type, 'mousemove');
assert.equal(translatedMove.button, 0);
assert.equal(translatedMove.buttons, 1);
assert.equal(translatedMove.clientX, 125);
assert.equal(translatedMove.clientY, 90);
assert.equal(translatedMove.ctrlKey, false);
assert.equal(translatedMove.shiftKey, false);

eventTarget.dispatch('mousemove', translatedMove);
assert.equal(documentTarget.dispatchedEvents.length, 1, 'Übersetzte Events dürfen nicht rekursiv verarbeitet werden.');

const middleUp = mouseEvent({ type: 'mouseup', button: 1, buttons: 0, clientX: 125, clientY: 90 });
eventTarget.dispatch('mouseup', middleUp);
assert.equal(middleUp.defaultPrevented, true);
assert.equal(canvasContainer.dispatchedEvents.length, 2);
const translatedUp = canvasContainer.dispatchedEvents[1];
assert.equal(translatedUp.type, 'mouseup');
assert.equal(translatedUp.button, 0);
assert.equal(translatedUp.buttons, 0);
eventTarget.dispatch('mousemove', mouseEvent({ type: 'mousemove', buttons: 4, clientX: 150, clientY: 110 }));
assert.equal(documentTarget.dispatchedEvents.length, 1, 'Nach mouseup darf die Karte nicht weiter verschoben werden.');

canvas.dispatch('mousedown', mouseEvent({ button: 1, buttons: 4, clientX: 10, clientY: 10 }));
eventTarget.dispatch('mousemove', mouseEvent({ type: 'mousemove', buttons: 0, clientX: 20, clientY: 20 }));
assert.equal(documentTarget.dispatchedEvents.length, 1, 'Ein verlorener Mitteltastenstatus darf keinen weiteren Move erzeugen.');
assert.equal(canvasContainer.dispatchedEvents.at(-1).type, 'mouseup', 'Der verlorene Buttonstatus muss den MapLibre-Drag beenden.');

canvas.dispatch('mousedown', mouseEvent({ button: 1, buttons: 4, clientX: 10, clientY: 10 }));
eventTarget.dispatch('blur', {});
eventTarget.dispatch('mousemove', mouseEvent({ type: 'mousemove', buttons: 4, clientX: 20, clientY: 20 }));
assert.equal(documentTarget.dispatchedEvents.length, 1, 'Ein Fensterwechsel muss den Adapter beenden.');

const leftAuxClick = mouseEvent({ type: 'auxclick', button: 0 });
canvas.dispatch('auxclick', leftAuxClick);
assert.equal(leftAuxClick.defaultPrevented, false);
const middleAuxClick = mouseEvent({ type: 'auxclick', button: 1 });
canvas.dispatch('auxclick', middleAuxClick);
assert.equal(middleAuxClick.defaultPrevented, true, 'Nur der mittlere Aux-Klick muss unterdrückt werden.');

const wheel = mouseEvent({ type: 'wheel', deltaY: -120 });
canvas.dispatch('wheel', wheel);
assert.equal(wheel.defaultPrevented, false, 'Der Adapter darf das normale Rad-Zoomen nicht abfangen.');

assert.equal(typeof removeListener, 'function', 'Der Adapter muss beim Entfernen der Karte aufräumen.');
cleanup();
assert.equal(canvas.listenerCount('mousedown'), 0);
assert.equal(canvas.listenerCount('auxclick'), 0);
assert.equal(eventTarget.listenerCount('mousemove'), 0);
assert.equal(eventTarget.listenerCount('mouseup'), 0);
assert.equal(eventTarget.listenerCount('blur'), 0);
assert.equal(removeListener, null);

const mapSource = readFileSync(new URL('../live-viewer/viewer-app/map.js', import.meta.url), 'utf8');
assert.match(mapSource, /map\.scrollZoom\.enable\(\)/, 'Das normale Rad-Zoomen muss aktiviert bleiben.');
assert.doesNotMatch(mapSource, /scrollZoom\.disable\(\)/);
assert.match(mapSource, /enableMiddleMousePan\(map\);/);
assert.doesNotMatch(mapSource, /map\.panBy\(/, 'Der Adapter darf keine moveend-Kaskade pro Mausbewegung erzeugen.');

console.log('middle-mouse-pan-tests=ok');
