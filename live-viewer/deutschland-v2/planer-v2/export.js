import { addressLabel } from './utils.js';

export function createExportController({ map, api, store, elements }) {
  const {
    exportFrame, exportPageBox, exportFrameBox, exportCenterMarker, exportOutput, exportPaper,
    exportOrientationField, exportOrientation, exportScale, exportLayout, exportHighlight,
    exportDxf, exportSummary, exportStatus, exportPreview, sourceList
  } = elements;
  let drag = null;

  const PAPER_MM = {
    a4: [210, 297],
    a3: [297, 420],
    square: [210, 210],
    ratio43: [280, 210]
  };

  function isDocumentFormat() {
    return ['a4', 'a3'].includes(exportPaper.value);
  }

  function pageSizeMillimeters() {
    const dimensions = PAPER_MM[exportPaper.value] || PAPER_MM.a4;
    let [width, height] = dimensions;
    if (isDocumentFormat() && exportOrientation.value === 'landscape') [width, height] = [height, width];
    return { width, height };
  }

  function layoutMetrics() {
    const page = pageSizeMillimeters();
    const pageWidth = page.width / 25.4;
    const pageHeight = page.height / 25.4;
    const landscape = exportOrientation.value === 'landscape';
    const marginTop = .3;
    const marginBottom = .3;
    const marginLeft = .7;
    const marginRight = .5;
    const headerHeight = landscape ? .5 : .8;
    const footerHeight = landscape ? .5 : .6;
    const padding = .15;
    return {
      pageWidth, pageHeight, marginTop, marginBottom, marginLeft, marginRight,
      headerHeight, footerHeight, padding,
      mapWidth: pageWidth - marginLeft - marginRight,
      mapHeight: pageHeight - marginTop - marginBottom - headerHeight - footerHeight - 2 * padding
    };
  }

  function mapSizeMillimeters() {
    if (exportLayout.checked && isDocumentFormat()) {
      const metrics = layoutMetrics();
      return { width: metrics.mapWidth * 25.4, height: metrics.mapHeight * 25.4 };
    }
    return pageSizeMillimeters();
  }

  function sizeMeters(dimensions) {
    const scale = Number(exportScale.value || 1000);
    return { width: dimensions.width * scale / 1000, height: dimensions.height * scale / 1000 };
  }

  function mapSizeMeters() { return sizeMeters(mapSizeMillimeters()); }
  function pageSizeMeters() { return sizeMeters(pageSizeMillimeters()); }

  function center() {
    const value = store.getState().export.center;
    return value || map.getCenter();
  }

  function boundsForSize(size) {
    const value = center();
    const metersPerLng = Math.max(1, 111320 * Math.cos(value.lat * Math.PI / 180));
    const halfLng = size.width / 2 / metersPerLng;
    const halfLat = size.height / 2 / 111320;
    return { center: value, size, west: value.lng - halfLng, east: value.lng + halfLng, south: value.lat - halfLat, north: value.lat + halfLat };
  }

  function bounds() { return boundsForSize(mapSizeMeters()); }
  function pageBounds() { return boundsForSize(pageSizeMeters()); }

  function setCenter(lngLat) {
    const state = store.getState();
    store.setState({ export: { ...state.export, center: { lng: Number(lngLat.lng), lat: Number(lngLat.lat) } } }, 'export');
  }

  function updateControlState() {
    const pdf = exportOutput.value === 'pdf';
    for (const option of exportPaper.options) {
      const imageOnly = ['square', 'ratio43'].includes(option.value);
      option.disabled = pdf && imageOnly;
      option.hidden = pdf && imageOnly;
    }
    if (pdf && !isDocumentFormat()) exportPaper.value = 'a4';
    exportOrientationField.hidden = !isDocumentFormat();
    exportLayout.disabled = !isDocumentFormat();
    if (!isDocumentFormat()) exportLayout.checked = false;
    const selection = store.getState().selection;
    const hasSelection = selection.parcels.length + selection.buildings.length > 0;
    exportHighlight.disabled = !hasSelection;
  }

  function render() {
    const state = store.getState();
    updateControlState();
    const open = state.layout.sidebarOpen;
    exportFrame.hidden = !open;
    if (!open) return;
    const mapBounds = bounds();
    const outerBounds = exportLayout.checked && isDocumentFormat() ? pageBounds() : mapBounds;
    const nw = map.project([outerBounds.west, outerBounds.north]);
    const se = map.project([outerBounds.east, outerBounds.south]);
    const centerPoint = map.project([outerBounds.center.lng, outerBounds.center.lat]);
    const width = Math.abs(se.x - nw.x);
    const height = Math.abs(se.y - nw.y);
    Object.assign(exportCenterMarker.style, { left: `${centerPoint.x}px`, top: `${centerPoint.y}px` });
    exportFrameBox.hidden = width < 18 || height < 18;
    const frameLeft = Math.min(nw.x, se.x);
    const frameTop = Math.min(nw.y, se.y);
    if (!exportFrameBox.hidden) Object.assign(exportFrameBox.style, { left: `${frameLeft}px`, top: `${frameTop}px`, width: `${width}px`, height: `${height}px` });
    const showMapFrame = exportLayout.checked && isDocumentFormat() && !exportFrameBox.hidden;
    exportPageBox.hidden = !showMapFrame;
    if (showMapFrame) {
      const mapNw = map.project([mapBounds.west, mapBounds.north]);
      const mapSe = map.project([mapBounds.east, mapBounds.south]);
      Object.assign(exportPageBox.style, {
        left: `${Math.min(mapNw.x, mapSe.x)}px`,
        top: `${Math.min(mapNw.y, mapSe.y)}px`,
        width: `${Math.abs(mapSe.x - mapNw.x)}px`,
        height: `${Math.abs(mapSe.y - mapNw.y)}px`
      });
    }
    exportFrameBox.style.pointerEvents = state.activeTool === 'export' ? 'auto' : 'none';
    const formatLabel = exportPaper.value === 'ratio43' ? '4:3' : exportPaper.value === 'square' ? '1:1' : exportPaper.value.toUpperCase();
    const outputs = [exportOutput.value.toUpperCase(), exportDxf.checked && 'DXF'].filter(Boolean);
    exportSummary.textContent = `${formatLabel} · 1:${exportScale.value} · ${outputs.join(' + ')}${exportLayout.checked ? ' · Layout' : ''}`;
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

  function forwardWheelToMap(event) {
    event.preventDefault();
    event.stopPropagation();
    map.getCanvas().dispatchEvent(new WheelEvent('wheel', {
      bubbles: true,
      cancelable: true,
      clientX: event.clientX,
      clientY: event.clientY,
      screenX: event.screenX,
      screenY: event.screenY,
      deltaX: event.deltaX,
      deltaY: event.deltaY,
      deltaZ: event.deltaZ,
      deltaMode: event.deltaMode,
      ctrlKey: event.ctrlKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      metaKey: event.metaKey
    }));
  }

  function plannerRender() {
    const state = store.getState();
    const highlight = exportHighlight.checked && !exportHighlight.disabled;
    const selectedFeature = (item, kind) => ({
      state: item.state || item.state_slug || '',
      source_db: item.source_db || '',
      gml_id: item.gml_id || '',
      preview_id: item.preview_id || '',
      kind,
      geometry: item.geometry
    });
    const frameBounds = bounds();
    return {
      source: 'planner',
      version: 2,
      layers: state.layers,
      selection: highlight ? {
        parcels: state.selection.parcels.map((item) => selectedFeature(item, 'parcel')),
        buildings: state.selection.buildings.map((item) => selectedFeature(item, 'building'))
      } : { parcels: [], buildings: [] },
      highlight_selection: highlight,
      bbox: frameBounds,
      width_m: frameBounds.size.width,
      height_m: frameBounds.size.height,
      layout: exportLayout.checked,
      output: exportOutput.value,
      format: exportPaper.value,
      center: { lng: frameBounds.center.lng, lat: frameBounds.center.lat }
    };
  }

  async function preview() {
    const wantsPdf = exportOutput.value === 'pdf';
    const wantsPng = exportOutput.value === 'png';
    const wantsDxf = exportDxf.checked;
    exportPreview.disabled = true;
    exportStatus.textContent = 'Export wird vorbereitet …';
    try {
      if (wantsPng) await exportPng();
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

  function outputPixelSize() {
    if (exportPaper.value === 'square') return { width: 1800, height: 1800 };
    if (exportPaper.value === 'ratio43') return { width: 1800, height: 1350 };
    const base = exportPaper.value === 'a3' ? { width: 1754, height: 2480 } : { width: 1240, height: 1754 };
    if (exportOrientation.value === 'landscape') [base.width, base.height] = [base.height, base.width];
    return base;
  }

  function outputMapRect(size) {
    if (!exportLayout.checked || !isDocumentFormat()) return { x: 0, y: 0, width: size.width, height: size.height };
    const metrics = layoutMetrics();
    return {
      x: Math.round(size.width * metrics.marginLeft / metrics.pageWidth),
      y: Math.round(size.height * (metrics.marginTop + metrics.headerHeight + metrics.padding) / metrics.pageHeight),
      width: Math.round(size.width * metrics.mapWidth / metrics.pageWidth),
      height: Math.round(size.height * metrics.mapHeight / metrics.pageHeight)
    };
  }

  function sourceText() {
    return String(sourceList?.textContent || '© Amtliches Liegenschaftskataster (ALKIS) · OpenKataster').replace(/\s+/g, ' ').trim();
  }

  function drawPngDetails(canvas, mapRect) {
    const context = canvas.getContext('2d');
    const scale = Math.max(1, Math.min(canvas.width, canvas.height) / 1200);
    if (exportLayout.checked && isDocumentFormat()) {
      context.fillStyle = '#20242a';
      context.font = `600 ${Math.round(22 * scale)}px Arial, sans-serif`;
      context.fillText('Auszug aus dem Liegenschaftskataster', mapRect.x, Math.max(28 * scale, mapRect.y * .47));
      context.font = `400 ${Math.round(11 * scale)}px Arial, sans-serif`;
      context.textAlign = 'right';
      context.fillText(`Maßstab 1:${exportScale.value}`, mapRect.x + mapRect.width, Math.max(28 * scale, mapRect.y * .47));
      context.textAlign = 'left';
      context.fillStyle = '#59616c';
      context.font = `400 ${Math.round(9 * scale)}px Arial, sans-serif`;
      context.fillText(sourceText(), mapRect.x, Math.min(canvas.height - 10 * scale, mapRect.y + mapRect.height + 28 * scale));
      return;
    }
    const text = sourceText();
    context.font = `400 ${Math.round(10 * scale)}px Arial, sans-serif`;
    const padding = Math.round(7 * scale);
    const width = Math.min(canvas.width - 20, context.measureText(text).width + padding * 2);
    const height = Math.round(24 * scale);
    context.fillStyle = 'rgba(255,255,255,.9)';
    context.fillRect(canvas.width - width, canvas.height - height, width, height);
    context.fillStyle = '#59616c';
    context.textBaseline = 'middle';
    context.fillText(text, canvas.width - width + padding, canvas.height - height / 2, width - padding * 2);
  }

  async function exportPng() {
    const size = outputPixelSize();
    const mapRect = outputMapRect(size);
    const container = document.createElement('div');
    Object.assign(container.style, { position: 'fixed', left: '-10000px', top: '0', width: `${mapRect.width}px`, height: `${mapRect.height}px` });
    document.body.appendChild(container);
    const style = structuredClone(map.getStyle());
    if (!exportHighlight.checked || exportHighlight.disabled) {
      style.layers = style.layers.filter((layer) => !['selected-parcels-v2', 'selected-buildings-v2'].includes(layer.id));
    }
    const frameBounds = bounds();
    const printMap = new maplibregl.Map({ container, style, center: center(), zoom: map.getZoom(), bearing: 0, pitch: 0, interactive: false, preserveDrawingBuffer: true, attributionControl: false, fadeDuration: 0 });
    try {
      await new Promise((resolve) => printMap.once('load', () => {
        printMap.fitBounds([[frameBounds.west, frameBounds.south], [frameBounds.east, frameBounds.north]], { padding: 0, duration: 0 });
        printMap.once('idle', resolve);
      }));
      const output = document.createElement('canvas');
      output.width = size.width;
      output.height = size.height;
      const context = output.getContext('2d');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, size.width, size.height);
      context.drawImage(printMap.getCanvas(), mapRect.x, mapRect.y, mapRect.width, mapRect.height);
      drawPngDetails(output, mapRect);
      const blob = await new Promise((resolve) => output.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('PNG konnte nicht erstellt werden.');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `openkataster-${exportPaper.value}-${Date.now()}.png`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } finally { printMap.remove(); container.remove(); }
  }

  map.on('click', (event) => { if (store.getState().activeTool === 'export' && !drag) setCenter(event.lngLat); });
  map.on('move', render);
  map.on('zoom', render);
  store.subscribe((state, reason) => { if (['sidebar', 'tool', 'export', 'restore', 'layers', 'selection', 'selection-clear'].includes(reason)) render(state); });
  exportFrameBox.addEventListener('pointerdown', beginDrag);
  exportFrameBox.addEventListener('pointermove', moveDrag);
  exportFrameBox.addEventListener('pointerup', endDrag);
  exportFrameBox.addEventListener('pointercancel', endDrag);
  exportFrameBox.addEventListener('wheel', forwardWheelToMap, { passive: false });
  for (const control of [exportOutput, exportPaper, exportOrientation, exportScale, exportLayout, exportHighlight, exportDxf]) control.addEventListener('change', render);
  exportPreview.addEventListener('click', preview);
  return { render, setCenter, preview };
}
