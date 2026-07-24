import { addressLabel } from './utils.js';

const LOCATION_PLACEHOLDERS = new Set([
  '',
  '-',
  '–',
  'kartenausschnitt',
  'unbekannter standort'
]);

function usableLocationLabel(value) {
  const label = String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
  return LOCATION_PLACEHOLDERS.has(label.toLocaleLowerCase('de-DE')) ? '' : label;
}

function parcelLocationLabel(parcel, terminology = {}) {
  if (!parcel || typeof parcel !== 'object') return '';
  const austria = String(parcel.state || '').toLocaleLowerCase('de-DE') === 'oesterreich'
    || String(parcel.source_db || '').toLocaleLowerCase('de-DE') === 'austria-bev'
    || Boolean(parcel.katastralgemeinde || parcel.katastralgemeindenummer || parcel.grundstuecksnummer);
  const cadastralDistrict = usableLocationLabel(
    parcel.gemarkung
      || parcel.gemarkungsname
      || parcel.katastralgemeinde
      || parcel.gemarkungsnummer
      || parcel.katastralgemeindenummer
  );
  const flur = usableLocationLabel(parcel.flur);
  const parcelNumber = usableLocationLabel(
    parcel.flurstueck
      || parcel.grundstueck
      || parcel.grundstuecksnummer
      || [parcel.zaehler, parcel.nenner].filter((value) => value !== undefined && value !== null && value !== '').join('/')
  );
  const cadastralDistrictTerm = austria ? 'Katastralgemeinde' : 'Gemarkung';
  const districtTerm = austria ? '' : 'Flur';
  const parcelTerm = austria ? 'Grundstück' : 'Flurstück';
  return [
    cadastralDistrict && `${cadastralDistrictTerm} ${cadastralDistrict}`,
    districtTerm && flur && `${districtTerm} ${flur}`,
    parcelNumber && `${parcelTerm} ${parcelNumber}`
  ].filter(Boolean).join(', ');
}

export function locationLabelFromFeatures(features, terminology) {
  const buildings = Array.isArray(features?.buildings) ? features.buildings : [];
  const parcels = Array.isArray(features?.parcels) ? features.parcels : [];
  for (const feature of [...buildings, ...parcels]) {
    const label = usableLocationLabel(addressLabel(feature));
    if (label) return label;
  }
  for (const parcel of parcels) {
    const label = parcelLocationLabel(parcel, terminology);
    if (label) return label;
  }
  return '';
}

export function exportFrameFitPadding({
  width,
  height,
  topInset = 0,
  leftInset = 0,
  rightInset = 0,
  bottomInset = 0,
  mobile = false
}) {
  const viewportWidth = Math.max(0, Number(width) || 0);
  const viewportHeight = Math.max(0, Number(height) || 0);
  const shortestSide = Math.min(viewportWidth, viewportHeight);
  const gap = Math.round(Math.min(
    mobile ? 22 : 32,
    Math.max(mobile ? 16 : 24, shortestSide * .025)
  ));
  const maximumHorizontal = Math.max(0, Math.floor((viewportWidth - 64) / 2));
  const maximumVertical = Math.max(0, Math.floor((viewportHeight - 64) / 2));
  const horizontal = Math.min(
    maximumHorizontal,
    Math.max(gap, Number(leftInset) + gap || 0, Number(rightInset) + gap || 0)
  );
  const vertical = Math.min(
    maximumVertical,
    Math.max(gap, Number(topInset) + gap || 0, Number(bottomInset) + gap || 0)
  );
  return { top: vertical, right: horizontal, bottom: vertical, left: horizontal };
}

export function fitMapToExportFrame(map, frameBounds, padding, duration = 280) {
  if (!map?.fitBounds || !frameBounds) return false;
  const coordinates = [
    [Number(frameBounds.west), Number(frameBounds.south)],
    [Number(frameBounds.east), Number(frameBounds.north)]
  ];
  if (coordinates.flat().some((value) => !Number.isFinite(value))) return false;
  map.fitBounds(coordinates, {
    padding,
    duration: Math.max(0, Number(duration) || 0),
    linear: true,
    retainPadding: false
  });
  return true;
}

function coordinateLocationLabel(value) {
  const lat = Number(value?.lat);
  const lon = Number(value?.lng ?? value?.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return 'Kartenausschnitt';
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}

export function createExportController({
  map,
  api,
  store,
  elements,
  datasetProfile = { terminology: {} },
  countryResolver = null,
  basemapRuntime = { profile: 'national', version: '' },
  onOfficeMode = false,
  onWorkspaceChange = () => {}
}) {
  const {
    exportFrame, exportPageBox, exportFrameBox, exportCenterMarker, exportOutput, exportPaper,
    exportOrientationField, exportOrientation, exportScale, exportLayout, exportHighlight,
    exportSummary, exportStatus, exportPreview, exportSidebar
  } = elements;
  let drag = null;
  let pinch = null;
  let pendingFitTimer = 0;
  let pendingFitFrame = 0;
  let stateDxfAllowed = !onOfficeMode;
  const activePointers = new Map();

  if (onOfficeMode && exportOutput.value === 'dxf') exportOutput.value = 'pdf';

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

  function explicitBounds() {
    const value = store.getState().export.bbox;
    const west = Number(value?.west);
    const south = Number(value?.south);
    const east = Number(value?.east);
    const north = Number(value?.north);
    if (
      ![west, south, east, north].every(Number.isFinite)
      || west >= east
      || south >= north
    ) return null;
    const boundsCenter = { lng: (west + east) / 2, lat: (south + north) / 2 };
    const metersPerLng = Math.max(1, 111320 * Math.cos(boundsCenter.lat * Math.PI / 180));
    return {
      center: boundsCenter,
      size: {
        width: (east - west) * metersPerLng,
        height: (north - south) * 111320
      },
      west,
      south,
      east,
      north
    };
  }

  function boundsForSize(size, value = center()) {
    const metersPerLng = Math.max(1, 111320 * Math.cos(value.lat * Math.PI / 180));
    const halfLng = size.width / 2 / metersPerLng;
    const halfLat = size.height / 2 / 111320;
    return { center: value, size, west: value.lng - halfLng, east: value.lng + halfLng, south: value.lat - halfLat, north: value.lat + halfLat };
  }

  function pageBounds() {
    const restored = explicitBounds();
    if (restored && (!exportLayout.checked || !isDocumentFormat())) return restored;
    return boundsForSize(pageSizeMeters());
  }

  function bounds() {
    const restored = explicitBounds();
    if (restored) return restored;
    const page = pageBounds();
    if (!exportLayout.checked || !isDocumentFormat()) return page;
    const metrics = layoutMetrics();
    const scale = Number(exportScale.value || 1000);
    const metersPerLng = Math.max(1, 111320 * Math.cos(page.center.lat * Math.PI / 180));
    const mapCenterOffsetEast = (metrics.marginLeft + metrics.mapWidth / 2 - metrics.pageWidth / 2) * scale * .0254;
    const mapCenterOffsetSouth = (metrics.marginTop + metrics.headerHeight + metrics.padding + metrics.mapHeight / 2 - metrics.pageHeight / 2) * scale * .0254;
    const mapCenter = {
      lng: page.center.lng + mapCenterOffsetEast / metersPerLng,
      lat: page.center.lat - mapCenterOffsetSouth / 111320
    };
    return boundsForSize(mapSizeMeters(), mapCenter);
  }
  function setCenter(lngLat) {
    const state = store.getState();
    store.setState({
      export: {
        ...state.export,
        center: { lng: Number(lngLat.lng), lat: Number(lngLat.lat) },
        bbox: null
      }
    }, 'export');
  }

  function workspaceState() {
    const value = store.getState().export.center;
    const lng = Number(value?.lng);
    const lat = Number(value?.lat);
    const frame = bounds();
    return {
      output: onOfficeMode && exportOutput.value === 'dxf' ? 'pdf' : exportOutput.value,
      format: exportPaper.value,
      orientation: exportOrientation.value,
      scale: Number(exportScale.value),
      layout: Boolean(exportLayout.checked && !exportLayout.disabled),
      highlight_selection: Boolean(exportHighlight.checked && !exportHighlight.disabled),
      center: Number.isFinite(lng) && Number.isFinite(lat) ? { lng, lat } : null,
      bbox: {
        west: frame.west,
        south: frame.south,
        east: frame.east,
        north: frame.north
      }
    };
  }

  function restoreWorkspace(value = {}) {
    clearScheduledFit();
    const setSelectValue = (control, nextValue) => {
      const normalized = String(nextValue ?? '');
      if ([...control.options].some((option) => option.value === normalized && !option.disabled)) {
        control.value = normalized;
      }
    };
    setSelectValue(exportOutput, onOfficeMode && value.output === 'dxf' ? 'pdf' : value.output);
    exportLayout.checked = Boolean(value.layout);
    updateControlState();
    setSelectValue(exportPaper, value.format);
    updateControlState();
    setSelectValue(exportOrientation, value.orientation);
    setSelectValue(exportScale, value.scale);
    exportHighlight.checked = Boolean(value.highlight_selection);
    const bboxCenter = {
      lng: (
        Number(value.bbox?.west) + Number(value.bbox?.east)
      ) / 2,
      lat: (
        Number(value.bbox?.south) + Number(value.bbox?.north)
      ) / 2
    };
    const lng = Number(value.center?.lng ?? value.center?.lon ?? bboxCenter.lng);
    const lat = Number(value.center?.lat ?? bboxCenter.lat);
    const state = store.getState();
    store.setState({
      export: {
        ...state.export,
        center: Number.isFinite(lng) && Number.isFinite(lat) ? { lng, lat } : null,
        bbox: value.bbox && [value.bbox.west, value.bbox.south, value.bbox.east, value.bbox.north].every(Number.isFinite)
          ? {
              west: Number(value.bbox.west),
              south: Number(value.bbox.south),
              east: Number(value.bbox.east),
              north: Number(value.bbox.north)
            }
          : null
      }
    }, 'restore-export');
    updateControlState();
    render();
    return workspaceState();
  }

  function mediaMatches(query) {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  }

  function visibleRect(element) {
    if (!element || element.hidden || typeof element.getBoundingClientRect !== 'function') return null;
    const rect = element.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    return rect;
  }

  function fitPadding() {
    const container = map.getContainer();
    const mapRect = visibleRect(container) || {
      left: 0,
      top: 0,
      right: Number(container?.clientWidth) || 0,
      bottom: Number(container?.clientHeight) || 0,
      width: Number(container?.clientWidth) || 0,
      height: Number(container?.clientHeight) || 0
    };
    const surface = container?.parentElement;
    const searchRect = visibleRect(surface?.querySelector?.('.search-pill-row'));
    const layerRect = visibleRect(surface?.querySelector?.('#layerButton'));
    const toolRect = visibleRect(surface?.querySelector?.('.tool-stack'));
    const mobile = mediaMatches('(max-width: 760px)');
    const footerRect = mobile && store.getState().layout.sidebarOpen
      ? visibleRect(exportSidebar?.querySelector?.('footer'))
      : null;
    const topInset = Math.max(
      0,
      searchRect ? searchRect.bottom - mapRect.top : 0,
      layerRect ? layerRect.bottom - mapRect.top : 0
    );
    const leftInset = Math.max(0, toolRect ? toolRect.right - mapRect.left : 0);
    const bottomInset = Math.max(0, footerRect ? footerRect.height : 0);
    return exportFrameFitPadding({
      width: mapRect.width,
      height: mapRect.height,
      topInset,
      leftInset,
      bottomInset,
      mobile
    });
  }

  function fitFrame({ duration } = {}) {
    updateControlState();
    const reducedMotion = mediaMatches('(prefers-reduced-motion: reduce)');
    return fitMapToExportFrame(
      map,
      pageBounds(),
      fitPadding(),
      duration ?? (reducedMotion ? 0 : 280)
    );
  }

  function clearScheduledFit() {
    if (pendingFitTimer && typeof window !== 'undefined') window.clearTimeout(pendingFitTimer);
    if (pendingFitFrame && typeof window !== 'undefined') window.cancelAnimationFrame(pendingFitFrame);
    pendingFitTimer = 0;
    pendingFitFrame = 0;
  }

  function scheduleFitFrame(delay = 0) {
    clearScheduledFit();
    if (typeof window === 'undefined') return fitFrame({ duration: 0 });
    const run = () => {
      pendingFitTimer = 0;
      if (store.getState().activeTool !== 'export' || !store.getState().layout.sidebarOpen) return;
      map.resize();
      pendingFitFrame = window.requestAnimationFrame(() => {
        pendingFitFrame = 0;
        fitFrame();
      });
    };
    if (delay > 0) pendingFitTimer = window.setTimeout(run, delay);
    else pendingFitFrame = window.requestAnimationFrame(() => {
      pendingFitFrame = 0;
      run();
    });
    return true;
  }

  function layoutIsTransitioning() {
    return map.getContainer()?.closest?.('.planner-app')?.dataset?.layoutTransitioning === 'true';
  }

  function exportCountries(frame = pageBounds()) {
    const includesAustria = countryResolver?.intersectsAustria?.(frame) === true;
    if (!includesAustria) return ['DE'];
    return countryResolver?.containsAustria?.(frame) === true ? ['AT'] : ['DE', 'AT'];
  }

  function dxfAllowed() {
    return stateDxfAllowed && !exportCountries().includes('AT');
  }

  function updateDxfControl() {
    const option = [...exportOutput.options].find((candidate) => candidate.value === 'dxf');
    if (!option) return;
    const allowed = dxfAllowed();
    option.hidden = !allowed;
    option.disabled = !allowed;
    if (!allowed && exportOutput.value === 'dxf') exportOutput.value = 'pdf';
  }

  function updateControlState() {
    if (onOfficeMode && exportOutput.value === 'dxf') exportOutput.value = 'pdf';
    updateDxfControl();
    const pdf = exportOutput.value === 'pdf';
    const png = exportOutput.value === 'png';
    const layoutRequested = Boolean(exportLayout.checked);
    for (const option of exportPaper.options) {
      const imageOnly = ['square', 'ratio43'].includes(option.value);
      const blocked = imageOnly && (pdf || ((pdf || png) && layoutRequested));
      option.disabled = blocked;
      option.hidden = blocked;
    }
    if ((pdf || ((pdf || png) && layoutRequested)) && !isDocumentFormat()) exportPaper.value = 'a4';
    exportOrientationField.hidden = !isDocumentFormat();
    const layoutAvailable = (pdf || png) && isDocumentFormat();
    exportLayout.disabled = !layoutAvailable;
    exportLayout.closest('label').hidden = !layoutAvailable;
    if (!layoutAvailable) exportLayout.checked = false;
    const selection = store.getState().selection;
    const hasSelection = selection.parcels.length + selection.buildings.length > 0;
    exportHighlight.disabled = !hasSelection;
    exportHighlight.closest('label').hidden = !hasSelection;
  }

  function render() {
    const state = store.getState();
    updateControlState();
    const open = state.layout.sidebarOpen;
    exportFrame.hidden = !open;
    if (!open) return;
    const mapBounds = bounds();
    const outerBounds = pageBounds();
    const mapNw = map.project([mapBounds.west, mapBounds.north]);
    const mapSe = map.project([mapBounds.east, mapBounds.south]);
    const mapLeft = Math.min(mapNw.x, mapSe.x);
    const mapTop = Math.min(mapNw.y, mapSe.y);
    const mapWidth = Math.abs(mapSe.x - mapNw.x);
    const mapHeight = Math.abs(mapSe.y - mapNw.y);
    const outerNw = map.project([outerBounds.west, outerBounds.north]);
    const outerSe = map.project([outerBounds.east, outerBounds.south]);
    const frameLeft = Math.min(outerNw.x, outerSe.x);
    const frameTop = Math.min(outerNw.y, outerSe.y);
    const width = Math.abs(outerSe.x - outerNw.x);
    const height = Math.abs(outerSe.y - outerNw.y);
    const centerPoint = map.project([outerBounds.center.lng, outerBounds.center.lat]);
    const showMapFrame = exportLayout.checked && isDocumentFormat();
    Object.assign(exportCenterMarker.style, { left: `${centerPoint.x}px`, top: `${centerPoint.y}px` });
    exportFrameBox.hidden = width < 18 || height < 18;
    if (!exportFrameBox.hidden) Object.assign(exportFrameBox.style, { left: `${frameLeft}px`, top: `${frameTop}px`, width: `${width}px`, height: `${height}px` });
    exportPageBox.hidden = !showMapFrame || exportFrameBox.hidden;
    if (!exportPageBox.hidden) {
      Object.assign(exportPageBox.style, {
        left: `${mapLeft}px`,
        top: `${mapTop}px`,
        width: `${mapWidth}px`,
        height: `${mapHeight}px`
      });
    }
    exportFrameBox.style.pointerEvents = state.activeTool === 'export' ? 'auto' : 'none';
    const formatLabel = exportPaper.value === 'ratio43' ? '4:3' : exportPaper.value === 'square' ? '1:1' : exportPaper.value.toUpperCase();
    const outputs = [exportOutput.value.toUpperCase()];
    exportSummary.textContent = `${formatLabel} · 1:${exportScale.value} · ${outputs.join(' + ')}${exportLayout.checked ? ' · Layout' : ''}`;
  }

  function beginDrag(event) {
    event.preventDefault();
    event.stopPropagation();
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    exportFrameBox.setPointerCapture(event.pointerId);
    if (activePointers.size > 1) {
      const [first, second] = [...activePointers.values()];
      const midpoint = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
      pinch = {
        distance: Math.max(1, Math.hypot(second.x - first.x, second.y - first.y)),
        zoom: map.getZoom(),
        around: map.unproject(mapPoint(midpoint.x, midpoint.y))
      };
      drag = null;
      exportFrameBox.classList.remove('is-dragging');
      return;
    }
    const current = center();
    drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, point: map.project([current.lng, current.lat]), moved: false };
    exportFrameBox.classList.add('is-dragging');
  }

  function moveDrag(event) {
    if (!activePointers.has(event.pointerId)) return;
    event.preventDefault();
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch && activePointers.size > 1) {
      const [first, second] = [...activePointers.values()];
      const midpoint = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
      const distance = Math.max(1, Math.hypot(second.x - first.x, second.y - first.y));
      map.zoomTo(pinch.zoom + Math.log2(distance / pinch.distance), { around: pinch.around, duration: 0 });
      const rendered = map.project(pinch.around);
      const target = mapPoint(midpoint.x, midpoint.y);
      map.panBy([rendered.x - target.x, rendered.y - target.y], { duration: 0 });
      return;
    }
    if (!drag || drag.pointerId !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.moved && distance < 4) return;
    drag.moved = true;
    const point = { x: drag.point.x + event.clientX - drag.startX, y: drag.point.y + event.clientY - drag.startY };
    setCenter(map.unproject(point));
  }

  function mapPoint(clientX, clientY) {
    const rect = map.getContainer().getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function endDrag(event) {
    const wasPinching = Boolean(pinch);
    const currentDrag = drag;
    activePointers.delete(event.pointerId);
    try { exportFrameBox.releasePointerCapture(event.pointerId); } catch (_) {}
    if (wasPinching) {
      if (activePointers.size < 2) pinch = null;
      drag = null;
      exportFrameBox.classList.remove('is-dragging');
      return;
    }
    if (currentDrag?.pointerId === event.pointerId && !currentDrag.moved && event.type === 'pointerup') {
      setCenter(map.unproject(mapPoint(event.clientX, event.clientY)));
    }
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
    const countries = exportCountries(pageBounds());
    return {
      source: 'planner',
      version: 2,
      countries,
      dataset: countries.length > 1 ? 'deutschland-oesterreich' : countries[0] === 'AT' ? 'oesterreich' : 'deutschland',
      basemap_profile: basemapRuntime.profile,
      basemap_revision: basemapRuntime.version,
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

  function canExport() {
    return store.getState().access.pro;
  }

  function exportDateStamp() {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/Berlin',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).formatToParts(new Date());
    const value = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
    return value.year + '-' + value.month + '-' + value.day;
  }

  function filenameSafe(value) {
    return String(value || '')
      .replaceAll('ß', 'ss')
      .replaceAll('ẞ', 'SS')
      .replaceAll('&', ' und ')
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^A-Za-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 120);
  }

  async function resolveExportLocation(value) {
    try {
      const features = await api.featureAt(value.lng, value.lat);
      const centerLabel = locationLabelFromFeatures(features, datasetProfile.terminology);
      return centerLabel || coordinateLocationLabel(value);
    } catch (error) {
      console.warn('[export] Standort am Exportzentrum konnte nicht aufgelöst werden.', error);
    }

    const selection = store.getState().selection;
    const selectionLabel = locationLabelFromFeatures({
      buildings: selection.buildings,
      parcels: selection.parcels
    }, datasetProfile.terminology);
    return selectionLabel || coordinateLocationLabel(value);
  }

  function exportFilenamePrefix(locationLabel) {
    const address = filenameSafe(locationLabel) || 'Kartenausschnitt';
    const format = exportPaper.value === 'ratio43' ? '4-3' : exportPaper.value.toUpperCase();
    return 'OpenKataster_' + address + '_1-' + exportScale.value + '_' + format + '_' + exportDateStamp();
  }

  async function preview() {
    if (!canExport()) {
      exportStatus.textContent = 'Kartenexporte sind in Pro verfügbar.';
      return;
    }
    const wantsPdf = exportOutput.value === 'pdf';
    const wantsPng = exportOutput.value === 'png';
    const wantsDxf = !onOfficeMode && exportOutput.value === 'dxf';
    exportPreview.disabled = true;
    exportStatus.textContent = 'Export wird vorbereitet …';
    try {
      const downloads = await exportVectorFiles({ pdf: wantsPdf, png: wantsPng, dxf: wantsDxf });
      await triggerDownloads(downloads);
      exportStatus.textContent = downloads.length > 1 ? 'Downloads wurden gestartet.' : 'Download wurde gestartet.';
    } catch (error) {
      console.error(error);
      exportStatus.textContent = error.message || 'Export fehlgeschlagen.';
    } finally {
      exportPreview.disabled = false;
    }
  }

  async function exportVectorFiles(options) {
    const value = center();
    const addressDisplay = await resolveExportLocation(value);
    const renderContext = plannerRender();
    renderContext.address = addressDisplay;
    const order = await api.createOrder({
      address_display: addressDisplay,
      center: { lat: value.lat, lon: value.lng },
      // Content-only image and DXF formats still carry their exact dimensions in planner_render.
      // The layout API itself accepts only DIN page formats.
      paper_format: isDocumentFormat() ? exportPaper.value.toUpperCase() : 'A4',
      orientation: exportOrientation.value === 'landscape' ? 'Querformat' : 'Hochformat',
      scale: Number(exportScale.value),
      include_pdf: options.pdf || options.png,
      include_dxf: options.dxf,
      include_luftbild: false,
      planner_render: renderContext
    });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt < 8 ? 1000 : 2000));
      const status = await api.orderStatus(order.order_id, order.guest_token || '');
      if (status.api_status === 'failed') throw new Error(status.message || 'Export-Erstellung fehlgeschlagen.');
      const outputs = status.outputs || {};
      if ((!(options.pdf || options.png) || outputs.pdf_url) && (!options.dxf || outputs.dxf_url)) {
        const prefix = exportFilenamePrefix(addressDisplay);
        return [
          options.pdf && { href: downloadUrl(order.order_id, order.guest_token, 'pdf'), filename: `${prefix}.pdf` },
          options.png && { href: downloadUrl(order.order_id, order.guest_token, 'png'), filename: `${prefix}.png` },
          options.dxf && { href: downloadUrl(order.order_id, order.guest_token, 'dxf'), filename: `${prefix}.dxf` }
        ].filter(Boolean);
      }
    }
    throw new Error('Export ist noch nicht fertig.');
  }

  function downloadUrl(orderId, guestToken, format) {
    return `/api/orders/${encodeURIComponent(orderId)}/download/${format}${guestToken ? `?guest_token=${encodeURIComponent(guestToken)}` : ''}`;
  }


  async function triggerDownloads(downloads) {
    for (const [index, download] of downloads.entries()) {
      const response = await fetch(download.href, { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Download konnte nicht geladen werden.');
      const href = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = href;
      link.download = download.filename || 'openkataster-export';
      link.hidden = true;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 30000);
      if (index < downloads.length - 1) await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  }

  map.on('click', (event) => { if (store.getState().activeTool === 'export' && !drag) setCenter(event.lngLat); });
  map.on('move', render);
  map.on('zoom', render);
  let previousActiveTool = store.getState().activeTool;
  store.subscribe((state, reason) => {
    const exportActivated = previousActiveTool !== 'export' && state.activeTool === 'export';
    previousActiveTool = state.activeTool;
    if (exportActivated) {
      setCenter(map.getCenter());
      scheduleFitFrame(430);
      return;
    }
    if (state.activeTool !== 'export') clearScheduledFit();
    if (['sidebar', 'tool', 'export', 'restore', 'layers', 'selection', 'selection-clear'].includes(reason)) render(state);
  });
  exportFrameBox.addEventListener('pointerdown', beginDrag);
  exportFrameBox.addEventListener('pointermove', moveDrag);
  exportFrameBox.addEventListener('pointerup', endDrag);
  exportFrameBox.addEventListener('pointercancel', endDrag);
  exportFrameBox.addEventListener('wheel', forwardWheelToMap, { passive: false });
  for (const control of [exportOutput, exportPaper, exportOrientation, exportScale, exportLayout]) {
    control.addEventListener('change', () => {
      const state = store.getState();
      if (state.export.bbox) {
        store.setState({ export: { ...state.export, bbox: null } }, 'export-options');
      }
      render();
      if (layoutIsTransitioning()) scheduleFitFrame(430);
      else fitFrame();
      onWorkspaceChange();
    });
  }
  exportHighlight.addEventListener('change', () => {
    render();
    onWorkspaceChange();
  });
  exportPreview.addEventListener('click', preview);
  return {
    render,
    setCenter,
    fitFrame,
    preview,
    workspaceState,
    restoreWorkspace,
    setStateCapabilities(state) {
      stateDxfAllowed = !onOfficeMode && state?.export?.dxf !== false;
      render();
    }
  };
}
