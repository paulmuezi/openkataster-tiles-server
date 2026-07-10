export function createLayout({ app, map, store, elements }) {
  const { exportSidebar, selectionDock, exportTool, selectTool, measureTool } = elements;
  let resizing = false;

  function render(state, reason) {
    const { layout, activeTool } = state;
    app.dataset.sidebarOpen = layout.sidebarOpen ? 'true' : 'false';
    app.dataset.tableOpen = layout.tableOpen ? 'true' : 'false';
    app.dataset.activeTool = activeTool;
    app.style.setProperty('--table-height-open', `${layout.tableHeight}px`);
    exportSidebar.setAttribute('aria-hidden', layout.sidebarOpen ? 'false' : 'true');
    selectionDock.setAttribute('aria-hidden', layout.tableOpen ? 'false' : 'true');
    exportTool.classList.toggle('is-active', activeTool === 'export');
    selectTool.classList.toggle('is-active', activeTool === 'select');
    measureTool.classList.toggle('is-active', activeTool === 'measure');
    if (reason !== 'boot') requestAnimationFrame(() => map.resize());
  }

  function setTool(activeTool) {
    const current = store.getState();
    const nextTool = current.activeTool === activeTool ? 'none' : activeTool;
    store.setState({
      activeTool: nextTool,
      layout: {
        ...current.layout,
        sidebarOpen: nextTool === 'export' ? true : current.layout.sidebarOpen && activeTool !== 'export'
      }
    }, 'tool');
  }

  function setSidebar(open) {
    const state = store.getState();
    store.setState({ activeTool: open ? 'export' : state.activeTool === 'export' ? 'none' : state.activeTool, layout: { ...state.layout, sidebarOpen: open } }, 'sidebar');
  }

  function setTable(open) {
    const state = store.getState();
    store.setState({ layout: { ...state.layout, tableOpen: open } }, 'table');
  }

  function beginTableResize(event) {
    if (window.matchMedia('(max-width: 760px)').matches) return;
    event.preventDefault();
    resizing = true;
    app.dataset.resizing = 'true';
    const startY = event.clientY;
    const startHeight = store.getState().layout.tableHeight;
    const pointerId = event.pointerId;
    event.currentTarget.setPointerCapture(pointerId);

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
      event.currentTarget.removeEventListener('pointermove', move);
      event.currentTarget.removeEventListener('pointerup', finish);
      event.currentTarget.removeEventListener('pointercancel', finish);
    };
    event.currentTarget.addEventListener('pointermove', move);
    event.currentTarget.addEventListener('pointerup', finish);
    event.currentTarget.addEventListener('pointercancel', finish);
  }

  store.subscribe(render);
  render(store.getState(), 'boot');
  return { setTool, setSidebar, setTable, beginTableResize };
}
