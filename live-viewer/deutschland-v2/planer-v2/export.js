import { addressLabel } from './utils.js';

export function createExportController({ map, api, store, elements }) {
  const {
    exportFrame, exportFrameBox, exportCenterMarker, exportPaper, exportOrientation, exportScale,
    exportPdf, exportDxf, exportAerial, exportSummary, exportStatus, exportPreview
  } = elements;
  let drag = null;

  function paperSizeMeters() {
    const scale = Number(exportScale.value || 1000);
    const dimensions = exportPaper.value === 'a3' ? [420, 297] : [297, 210];
    const landscape = exportOrientation.value === 'landscape';
    const widthMm = landscape ? dimensions[0] : dimensions[1];
    const heightMm = landscape ? dimensions[1] : dimensions[0];
    return { width: widthMm * scale / 1000, height: heightMm * scale / 1000 };
  }

  function center() {
    const value = store.getState().export.center;
    return value || map.getCenter();
  }

  function bounds() {
    const value = center();
    const size = paperSizeMeters();
    const metersPerLng = Math.max(1, 111320 * Math.cos(value.lat * Math.PI / 180));
    const halfLng = size.width / 2 / metersPerLng;
    const halfLat = size.height / 2 / 111320;
    return { center: value, size, west: value.lng - halfLng, east: value.lng + halfLng, south: value.lat - halfLat, north: value.lat + halfLat };
  }

  function setCenter(lngLat) {
    const state = store.getState();
    store.setState({ export: { ...state.export, center: { lng: Number(lngLat.lng), lat: Number(lngLat.lat) } } }, 'export');
  }

  function render() {
    const state = store.getState();
    const open = state.layout.sidebarOpen;
    exportFrame.hidden = !open;
    if (!open) return;
    const frameBounds = bounds();
    const nw = map.project([frameBounds.west, frameBounds.north]);
    const se = map.project([frameBounds.east, frameBounds.south]);
    const centerPoint = map.project([frameBounds.center.lng, frameBounds.center.lat]);
    const width = Math.abs(se.x - nw.x);
    const height = Math.abs(se.y - nw.y);
    Object.assign(exportCenterMarker.style, { left: `${centerPoint.x}px`, top: `${centerPoint.y}px` });
    exportFrameBox.hidden = width < 18 || height < 18;
    if (!exportFrameBox.hidden) Object.assign(exportFrameBox.style, { left: `${Math.min(nw.x, se.x)}px`, top: `${Math.min(nw.y, se.y)}px`, width: `${width}px`, height: `${height}px` });
    exportFrameBox.style.pointerEvents = state.activeTool === 'export' ? 'auto' : 'none';
    const formats = [exportPdf.checked && 'PDF', exportDxf.checked && 'DXF', exportAerial.checked && 'JPG'].filter(Boolean);
    exportSummary.textContent = `${exportPaper.value.toUpperCase()} · 1:${exportScale.value} · ${formats.join(' + ') || 'Format wählen'}`;
  }

  function beginDrag(event) {
    event.preventDefault();
    event.stopPropagation();
    const current = center();
    drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, point: map.project([current.lng, current.lat]) };
    exportFrameBox.setPointerCapture(event.pointerId);
    exportFrameBox.classList.add('is-dragging');
  }

  function moveDrag(event) {
    if (!drag) return;
    const point = { x: drag.point.x + event.clientX - drag.startX, y: drag.point.y + event.clientY - drag.startY };
    setCenter(map.unproject(point));
  }

  function endDrag() {
    if (!drag) return;
    try { exportFrameBox.releasePointerCapture(drag.pointerId); } catch (_) {}
    drag = null;
    exportFrameBox.classList.remove('is-dragging');
  }

  function plannerRender() {
    const state = store.getState();
    return {
      version: 1,
      layers: state.layers,
      selection: { parcels: state.selection.parcels, buildings: state.selection.buildings },
      bbox: bounds(),
      center: state.export.center
    };
  }

  async function preview() {
    const wantsPdf = exportPdf.checked;
    const wantsDxf = exportDxf.checked;
    const wantsJpg = exportAerial.checked;
    if (!wantsPdf && !wantsDxf && !wantsJpg) { exportStatus.textContent = 'Bitte mindestens ein Dateiformat wählen.'; return; }
    exportPreview.disabled = true;
    exportStatus.textContent = 'Export wird vorbereitet …';
    try {
      if (wantsJpg) await exportJpg();
      if (wantsPdf || wantsDxf) await exportVectorFiles({ pdf: wantsPdf, dxf: wantsDxf });
      exportStatus.textContent = 'Export ist fertig.';
    } catch (error) {
      console.error(error);
      exportStatus.textContent = error.message || 'Export fehlgeschlagen.';
    } finally {
      exportPreview.disabled = false;
    }
  }

  async function exportVectorFiles(options) {
    const value = center();
    const first = [...store.getState().selection.buildings, ...store.getState().selection.parcels][0];
    const order = await api.createOrder({
      address_display: first ? addressLabel(first) : 'Kartenausschnitt',
      center: { lat: value.lat, lon: value.lng },
      paper_format: exportPaper.value.toUpperCase(),
      orientation: exportOrientation.value === 'landscape' ? 'Querformat' : 'Hochformat',
      scale: Number(exportScale.value),
      include_pdf: options.pdf,
      include_dxf: options.dxf,
      include_luftbild: false,
      planner_render: plannerRender()
    });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt < 8 ? 1000 : 2000));
      const status = await api.orderStatus(order.order_id, order.guest_token || '');
      if (status.api_status === 'failed') throw new Error(status.message || 'Export-Erstellung fehlgeschlagen.');
      const outputs = status.outputs || {};
      if ((!options.pdf || outputs.pdf_url) && (!options.dxf || outputs.dxf_url)) {
        if (options.pdf) openDownload(order.order_id, order.guest_token, 'pdf');
        if (options.dxf) window.setTimeout(() => openDownload(order.order_id, order.guest_token, 'dxf'), 160);
        return;
      }
    }
    throw new Error('Export ist noch nicht fertig.');
  }

  function openDownload(orderId, guestToken, format) {
    const url = `/api/orders/${encodeURIComponent(orderId)}/download/${format}${guestToken ? `?guest_token=${encodeURIComponent(guestToken)}` : ''}`;
    window.open(url, '_blank', 'noopener');
  }

  async function exportJpg() {
    const size = exportPaper.value === 'a3' ? { width: 1754, height: 1240 } : { width: 1240, height: 877 };
    if (exportOrientation.value !== 'landscape') [size.width, size.height] = [size.height, size.width];
    const container = document.createElement('div');
    Object.assign(container.style, { position: 'fixed', left: '-10000px', top: '0', width: `${size.width}px`, height: `${size.height}px` });
    document.body.appendChild(container);
    const printMap = new maplibregl.Map({ container, style: structuredClone(map.getStyle()), center: center(), zoom: map.getZoom(), bearing: 0, pitch: 0, interactive: false, preserveDrawingBuffer: true, attributionControl: false, fadeDuration: 0 });
    try {
      await new Promise((resolve) => printMap.once('idle', resolve));
      const blob = await new Promise((resolve) => printMap.getCanvas().toBlob(resolve, 'image/jpeg', .94));
      if (!blob) throw new Error('JPG konnte nicht erstellt werden.');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `openkataster-${Date.now()}.jpg`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } finally { printMap.remove(); container.remove(); }
  }

  map.on('click', (event) => { if (store.getState().activeTool === 'export' && !drag) setCenter(event.lngLat); });
  map.on('move', render);
  map.on('zoom', render);
  store.subscribe((state, reason) => { if (['sidebar', 'tool', 'export', 'restore', 'layers'].includes(reason)) render(state); });
  exportFrameBox.addEventListener('pointerdown', beginDrag);
  exportFrameBox.addEventListener('pointermove', moveDrag);
  exportFrameBox.addEventListener('pointerup', endDrag);
  exportFrameBox.addEventListener('pointercancel', endDrag);
  for (const control of [exportPaper, exportOrientation, exportScale, exportPdf, exportDxf, exportAerial]) control.addEventListener('change', render);
  exportPreview.addEventListener('click', preview);
  return { render, setCenter, preview };
}
