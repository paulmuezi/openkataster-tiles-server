export function createLayout({ app, map, store, elements }) {
  const { exportSidebar, selectionDock, selectionResize, exportTool, selectTool, measureTool, mobileExportSettings } = elements;
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
    event.preventDefault();
    resizing = true;
    app.dataset.resizing = 'true';
    const startY = event.clientY;
    const initialLayout = store.getState().layout;
    const startHeight = Math.max(initialLayout.tableHeight, minimumTableHeight(initialLayout));
    const pointerId = event.pointerId;
    const handle = event.currentTarget;
    handle.setPointerCapture(pointerId);

    const move = (moveEvent) => {
      if (!resizing) return;
      const state = store.getState();
      const minHeight = minimumTableHeight(state.layout);
      const maxHeight = Math.max(minHeight, app.clientHeight * 0.66);
      const tableHeight = Math.min(maxHeight, Math.max(minHeight, startHeight + startY - moveEvent.clientY));
      store.setState({ layout: { ...state.layout, tableHeight } }, 'resize');
    };
    const finish = () => {
      resizing = false;
      app.dataset.resizing = 'false';
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', finish);
      handle.removeEventListener('pointercancel', finish);
      window.requestAnimationFrame(() => map.resize());
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  }

  store.subscribe(render);
  render(store.getState(), 'boot');
  return { isMobile, setTool, setSidebar, setTable, toggleMobileExportSettings, closeMobileExportSettings, closeExportPanel, beginTableResize };
}
