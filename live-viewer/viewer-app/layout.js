export function selectionTableAutoFitHeight({
  minimumHeight,
  maximumHeight,
  chromeHeight,
  contentHeight,
  scrollbarHeight = 0
}) {
  const minimum = Math.max(0, Number(minimumHeight) || 0);
  const maximum = Math.max(minimum, Number(maximumHeight) || minimum);
  const naturalHeight = Math.max(0, Number(chromeHeight) || 0)
    + Math.max(0, Number(contentHeight) || 0)
    + Math.max(0, Number(scrollbarHeight) || 0);
  return Math.min(maximum, Math.max(minimum, Math.ceil(naturalHeight)));
}

export function createLayout({ app, map, store, elements }) {
  const {
    exportSidebar,
    selectionDock,
    selectionResize,
    selectionContent,
    exportTool,
    selectTool,
    measureTool,
    exportClose,
    mobileExportSettings
  } = elements;
  let resizing = false;
  let layoutTransitionTimer = 0;

  function isMobile() {
    return window.matchMedia('(max-width: 760px)').matches;
  }

  function minimumTableHeight(layout = store.getState().layout) {
    if (!isMobile()) return 150;
    const header = selectionDock.querySelector('.selection-head');
    const handleHeight = selectionResize.getBoundingClientRect().height || 28;
    const headerHeight = header ? header.getBoundingClientRect().height : 43;
    const exportOverlayHeight = layout.sidebarOpen && !layout.mobileExportSettings
      ? exportSidebar.getBoundingClientRect().height
      : 0;
    return Math.ceil(Math.max(150, handleHeight + headerHeight + exportOverlayHeight + 12));
  }

  function maximumTableHeight(layout = store.getState().layout) {
    const minimumHeight = minimumTableHeight(layout);
    return Math.max(minimumHeight, app.clientHeight * 0.66);
  }

  function numericStyle(element, property) {
    if (!element || typeof window.getComputedStyle !== 'function') return 0;
    return Number.parseFloat(window.getComputedStyle(element)[property]) || 0;
  }

  function intrinsicSelectionContentHeight() {
    const contentRect = selectionContent.getBoundingClientRect();
    const scrollTop = Number(selectionContent.scrollTop) || 0;
    let contentBottom = 0;

    for (const child of selectionContent.children || []) {
      const childRect = child.getBoundingClientRect();
      contentBottom = Math.max(contentBottom, childRect.bottom - contentRect.top + scrollTop);
    }

    const paddingBottom = numericStyle(selectionContent, 'paddingBottom');
    if (contentBottom > 0) return contentBottom + paddingBottom;

    const scrollHeight = Number(selectionContent.scrollHeight) || 0;
    const clientHeight = Number(selectionContent.clientHeight) || 0;
    if (scrollHeight > clientHeight + 1) return scrollHeight;
    return numericStyle(selectionContent, 'paddingTop') + paddingBottom;
  }

  function autoFitTableHeight() {
    const state = store.getState();
    const dockRect = selectionDock.getBoundingClientRect();
    const contentRect = selectionContent.getBoundingClientRect();
    const chromeHeight = Math.max(0, dockRect.height - contentRect.height);
    const scrollbarHeight = Math.max(
      0,
      (Number(selectionContent.offsetHeight) || 0) - (Number(selectionContent.clientHeight) || 0)
    );
    const tableHeight = selectionTableAutoFitHeight({
      minimumHeight: minimumTableHeight(state.layout),
      maximumHeight: maximumTableHeight(state.layout),
      chromeHeight,
      contentHeight: intrinsicSelectionContentHeight(),
      scrollbarHeight
    });
    store.setState({ layout: { ...state.layout, tableHeight } }, 'table');
    return tableHeight;
  }

  function scheduleMapResize(reason) {
    const mobile = isMobile();
    const tableTransition = reason === 'table' || (mobile && reason === 'tool');
    const geometryChanges = tableTransition || (!mobile && ['tool', 'sidebar'].includes(reason));
    if (!geometryChanges) return;
    app.dataset.layoutTransitioning = 'true';
    window.clearTimeout(layoutTransitionTimer);
    layoutTransitionTimer = window.setTimeout(() => {
      app.dataset.layoutTransitioning = 'false';
      map.resize();
    }, tableTransition ? 320 : 400);
  }

  function render(state, reason) {
    const { layout, activeTool } = state;
    app.dataset.sidebarOpen = layout.sidebarOpen ? 'true' : 'false';
    app.dataset.tableOpen = layout.tableOpen ? 'true' : 'false';
    app.dataset.mobileExportSettings = layout.mobileExportSettings ? 'true' : 'false';
    app.dataset.activeTool = activeTool;
    app.style.setProperty('--table-height-open', String(Math.max(layout.tableHeight, minimumTableHeight(layout))) + 'px');
    exportSidebar.setAttribute('aria-hidden', layout.sidebarOpen ? 'false' : 'true');
    selectionDock.setAttribute('aria-hidden', layout.tableOpen ? 'false' : 'true');
    exportTool.classList.toggle('is-active', activeTool === 'export');
    selectTool.classList.toggle('is-active', activeTool === 'select');
    measureTool.classList.toggle('is-active', activeTool === 'measure');
    exportClose.setAttribute('aria-label', isMobile() ? 'Exporteinstellungen schließen' : 'Export schließen');
    mobileExportSettings.setAttribute('aria-pressed', layout.mobileExportSettings ? 'true' : 'false');
    if (reason !== 'boot') scheduleMapResize(reason);
  }

  function setTool(activeTool) {
    const current = store.getState();
    const nextTool = current.activeTool === activeTool ? 'none' : activeTool;
    if (isMobile()) {
      const selectionCount = current.selection.parcels.length + current.selection.buildings.length;
      store.setState({
        activeTool: nextTool,
        layout: {
          ...current.layout,
          sidebarOpen: nextTool === 'export',
          tableOpen: nextTool === 'select' && selectionCount > 0,
          mobileExportSettings: false
        }
      }, 'tool');
      return;
    }

    const sidebarOpen = activeTool === 'export' ? true : current.layout.sidebarOpen;

    store.setState({
      activeTool: nextTool,
      layout: {
        ...current.layout,
        sidebarOpen,
        tableOpen: current.layout.tableOpen,
        mobileExportSettings: sidebarOpen && current.layout.mobileExportSettings
      }
    }, 'tool');
  }

  function setSidebar(open) {
    const state = store.getState();
    if (isMobile()) {
      store.setState({
        activeTool: open ? 'export' : state.activeTool === 'export' ? 'none' : state.activeTool,
        layout: {
          ...state.layout,
          sidebarOpen: open,
          tableOpen: open ? false : state.layout.tableOpen,
          mobileExportSettings: open ? state.layout.mobileExportSettings : false
        }
      }, 'tool');
      return;
    }

    store.setState({
      activeTool: !open && state.activeTool === 'export' ? 'none' : state.activeTool,
      layout: { ...state.layout, sidebarOpen: open, mobileExportSettings: open ? state.layout.mobileExportSettings : false }
    }, 'sidebar');
  }

  function toggleMobileExportSettings() {
    const state = store.getState();
    if (!state.layout.sidebarOpen) return;
    store.setState({
      layout: {
        ...state.layout,
        tableOpen: isMobile() ? false : state.layout.tableOpen,
        mobileExportSettings: !state.layout.mobileExportSettings
      }
    }, 'sidebar');
  }

  function closeMobileExportSettings() {
    const state = store.getState();
    if (!state.layout.mobileExportSettings) return;
    store.setState({
      layout: { ...state.layout, mobileExportSettings: false }
    }, 'sidebar');
  }

  function closeExportPanel() {
    setSidebar(false);
  }

  function closeExportSettingsOrPanel() {
    const state = store.getState();
    if (isMobile()) {
      if (state.layout.mobileExportSettings) closeMobileExportSettings();
      return;
    }
    closeExportPanel();
  }

  function setTable(open) {
    const state = store.getState();
    const mobile = isMobile();
    if (mobile) {
      store.setState({
        activeTool: open ? 'select' : state.activeTool === 'select' ? 'none' : state.activeTool,
        layout: {
          ...state.layout,
          tableOpen: open,
          sidebarOpen: open ? false : state.layout.sidebarOpen,
          mobileExportSettings: open ? false : state.layout.mobileExportSettings
        }
      }, 'tool');
      return;
    }

    store.setState({
      activeTool: state.activeTool,
      layout: {
        ...state.layout,
        tableOpen: open,
        sidebarOpen: state.layout.sidebarOpen,
        mobileExportSettings: mobile && open ? false : state.layout.mobileExportSettings
      }
    }, 'table');
  }

  function beginTableResize(event) {
    if (resizing || event.isPrimary === false) return;
    if (event.pointerType === 'mouse' && Number.isFinite(event.button) && event.button !== 0) return;
    event.preventDefault();
    resizing = true;
    app.dataset.resizing = 'true';
    const startX = Number(event.clientX) || 0;
    const startY = event.clientY;
    const initialLayout = store.getState().layout;
    const startHeight = Math.max(initialLayout.tableHeight, minimumTableHeight(initialLayout));
    const pointerId = event.pointerId;
    const handle = event.currentTarget;
    let didDrag = false;
    handle.setPointerCapture(pointerId);

    const move = (moveEvent) => {
      if (!resizing || moveEvent.pointerId !== pointerId) return;
      if (!didDrag) {
        const movement = Math.hypot((Number(moveEvent.clientX) || 0) - startX, moveEvent.clientY - startY);
        if (movement <= 6) return;
        didDrag = true;
      }
      const state = store.getState();
      const minHeight = minimumTableHeight(state.layout);
      const maxHeight = maximumTableHeight(state.layout);
      const tableHeight = Math.min(maxHeight, Math.max(minHeight, startHeight + startY - moveEvent.clientY));
      store.setState({ layout: { ...state.layout, tableHeight } }, 'resize');
    };
    const finish = (finishEvent) => {
      if (finishEvent.pointerId !== pointerId) return;
      const shouldAutoFit = finishEvent.type === 'pointerup' && !didDrag;
      resizing = false;
      app.dataset.resizing = 'false';
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', finish);
      handle.removeEventListener('pointercancel', finish);
      handle.removeEventListener('lostpointercapture', finish);
      if (typeof handle.hasPointerCapture !== 'function' || handle.hasPointerCapture(pointerId)) {
        try {
          handle.releasePointerCapture(pointerId);
        } catch (_) {
          // Capture may already have been released by the browser.
        }
      }
      if (shouldAutoFit) {
        autoFitTableHeight();
      } else {
        window.requestAnimationFrame(() => map.resize());
      }
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
  }

  store.subscribe(render);
  render(store.getState(), 'boot');
  return {
    isMobile,
    setTool,
    setSidebar,
    setTable,
    toggleMobileExportSettings,
    closeMobileExportSettings,
    closeExportPanel,
    closeExportSettingsOrPanel,
    autoFitTableHeight,
    beginTableResize
  };
}
