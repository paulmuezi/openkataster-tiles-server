export function createLayout({ app, map, store, elements }) {
  const { exportSidebar, selectionDock, exportTool, selectTool, measureTool, mobileExportSettings } = elements;
  let resizing = false;
  let layoutTransitionTimer = 0;

  function isMobile() {
    return window.matchMedia('(max-width: 760px)').matches;
  }

  function scheduleMapResize(reason) {
    const geometryChanges = reason === 'table' || (!isMobile() && ['tool', 'sidebar'].includes(reason));
    if (!geometryChanges) return;
    app.dataset.layoutTransitioning = 'true';
    window.clearTimeout(layoutTransitionTimer);
    layoutTransitionTimer = window.setTimeout(() => {
      app.dataset.layoutTransitioning = 'false';
      map.resize();
    }, reason === 'table' ? 320 : 400);
  }

  function render(state, reason) {
    const { layout, activeTool } = state;
    app.dataset.sidebarOpen = layout.sidebarOpen ? 'true' : 'false';
    app.dataset.tableOpen = layout.tableOpen ? 'true' : 'false';
    app.dataset.mobileExportSettings = layout.mobileExportSettings ? 'true' : 'false';
    app.dataset.activeTool = activeTool;
    app.style.setProperty('--table-height-open', `${layout.tableHeight}px`);
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
    const state = store.getState();
    if (isMobile() && state.layout.mobileExportSettings) {
      store.setState({
        layout: { ...state.layout, mobileExportSettings: false }
      }, 'sidebar');
      return;
    }
    setSidebar(false);
  }

  function setTable(open) {
    const state = store.getState();
    const mobile = isMobile();
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
    const startHeight = store.getState().layout.tableHeight;
    const pointerId = event.pointerId;
    const handle = event.currentTarget;
    handle.setPointerCapture(pointerId);

    const move = (moveEvent) => {
      if (!resizing) return;
      const maxHeight = Math.max(180, app.clientHeight * 0.66);
      const tableHeight = Math.min(maxHeight, Math.max(150, startHeight + startY - moveEvent.clientY));
      const state = store.getState();
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
  return { setTool, setSidebar, setTable, toggleMobileExportSettings, closeMobileExportSettings, closeExportPanel, beginTableResize };
}
