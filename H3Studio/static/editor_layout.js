(function (root) {
  "use strict";
  // Viewing preferences belong to this browser, never to an editing project.
  const STORAGE_KEY = "h3-editor-layout-v1";
  const DEFAULT_PREFERENCES = Object.freeze({ libraryWidth: null, inspectorWidth: null, viewerHeight: null, maximized: false });
  const SEPARATOR_SIZE = 10;
  const MIN_LIBRARY_WIDTH = 180, MIN_INSPECTOR_WIDTH = 190, MIN_VIEWER_WIDTH = 300;
  const MIN_STAGE_HEIGHT = 100, MIN_TIMELINE_HEIGHT = 200;
  const dimensions = { library: "libraryWidth", inspector: "inspectorWidth", viewer: "viewerHeight" };
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const positive = (value, fallback) => typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
  const same = (left, right) => Object.keys(DEFAULT_PREFERENCES).every(key => left[key] === right[key]);

  function normalizePreferences(raw) {
    const value = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    return { libraryWidth: positive(value.libraryWidth, null), inspectorWidth: positive(value.inspectorWidth, null),
      viewerHeight: positive(value.viewerHeight, null), maximized: value.maximized === true };
  }
  function deriveLayout(raw, metrics = {}) {
    const preferences = normalizePreferences(raw), viewportWidth = positive(metrics.viewportWidth, positive(metrics.width, 1280));
    const mode = viewportWidth >= 851 ? "desktop" : viewportWidth >= 571 ? "tablet" : "mobile";
    const width = positive(metrics.width, viewportWidth), availableHeight = positive(metrics.height, 600);
    const viewerMinHeight = Math.max(MIN_STAGE_HEIGHT, positive(metrics.viewerMinHeight, 300));
    const timelineMinHeight = Math.max(MIN_TIMELINE_HEIGHT, positive(metrics.timelineMinHeight, MIN_TIMELINE_HEIGHT));
    const defaultLibrary = viewportWidth >= 1700 ? 280 : viewportWidth <= 1150 ? 215 : 250;
    const defaultInspector = viewportWidth >= 1700 ? 275 : viewportWidth <= 1150 ? 212 : 246;
    const contentWidth = mode === "desktop" ? Math.max(width, MIN_LIBRARY_WIDTH + MIN_INSPECTOR_WIDTH + MIN_VIEWER_WIDTH + 2 * SEPARATOR_SIZE) : width;
    const libraryWidth = mode === "desktop" ? clamp(preferences.libraryWidth ?? defaultLibrary, MIN_LIBRARY_WIDTH,
      Math.min(600, contentWidth - MIN_INSPECTOR_WIDTH - MIN_VIEWER_WIDTH - 2 * SEPARATOR_SIZE)) : mode === "tablet" ? Math.min(200, width * .35) : 0;
    const inspectorWidth = mode === "desktop" ? clamp(preferences.inspectorWidth ?? defaultInspector, MIN_INSPECTOR_WIDTH,
      Math.min(600, contentWidth - libraryWidth - MIN_VIEWER_WIDTH - 2 * SEPARATOR_SIZE)) : 0;
    const height = preferences.maximized ? Math.max(availableHeight, viewerMinHeight)
      : Math.max(availableHeight, viewerMinHeight + timelineMinHeight + SEPARATOR_SIZE);
    const viewerMaximum = height - timelineMinHeight - SEPARATOR_SIZE;
    const defaultViewer = height - clamp(height * .38, timelineMinHeight, 360) - SEPARATOR_SIZE;
    const viewerHeight = preferences.maximized ? height : clamp(preferences.viewerHeight ?? defaultViewer, viewerMinHeight, viewerMaximum);
    return { mode, maximized: preferences.maximized, width: contentWidth, height,
      libraryWidth: preferences.maximized ? 0 : libraryWidth, inspectorWidth: preferences.maximized ? 0 : inspectorWidth,
      viewerWidth: preferences.maximized ? width : mode === "desktop" ? contentWidth - libraryWidth - inspectorWidth - 2 * SEPARATOR_SIZE
        : mode === "tablet" ? Math.max(0, width - libraryWidth - 10) : width,
      viewerHeight, timelineHeight: preferences.maximized ? 0 : height - viewerHeight - SEPARATOR_SIZE,
      bounds: { library: { min: MIN_LIBRARY_WIDTH, max: Math.max(MIN_LIBRARY_WIDTH, Math.min(600, contentWidth - inspectorWidth - MIN_VIEWER_WIDTH - 2 * SEPARATOR_SIZE)) },
        inspector: { min: MIN_INSPECTOR_WIDTH, max: Math.max(MIN_INSPECTOR_WIDTH, Math.min(600, contentWidth - libraryWidth - MIN_VIEWER_WIDTH - 2 * SEPARATOR_SIZE)) },
        viewer: { min: viewerMinHeight, max: Math.max(viewerMinHeight, viewerMaximum) } } };
  }
  function resizePreference(raw, layout, axis, delta) {
    const next = normalizePreferences(raw), field = dimensions[axis], bounds = layout?.bounds?.[axis];
    if (!field || !bounds || !Number.isFinite(delta) || delta === 0 || layout.mode !== "desktop" || layout.maximized) return next;
    next[field] = clamp(layout[field] + (axis === "inspector" ? -delta : delta), bounds.min, bounds.max);
    // A preference previously clamped by a smaller window must not make the
    // opposite sidebar grow as soon as this separator releases some space.
    const opposite = axis === "library" ? "inspectorWidth" : axis === "inspector" ? "libraryWidth" : null;
    if (opposite && next[opposite] !== null && next[opposite] !== layout[opposite]) next[opposite] = layout[opposite];
    return next;
  }
  function resetPreference(raw, axis) {
    const next = normalizePreferences(raw);
    if (axis === undefined) return { ...DEFAULT_PREFERENCES };
    if (dimensions[axis]) next[dimensions[axis]] = null;
    return next;
  }
  function keyboardDelta(key, shift, axis) {
    if (!dimensions[axis]) return null;
    const negative = axis === "viewer" ? "ArrowUp" : "ArrowLeft", positiveKey = axis === "viewer" ? "ArrowDown" : "ArrowRight";
    return key === negative ? -(shift ? 50 : 10) : key === positiveKey ? shift ? 50 : 10 : null;
  }
  function createLayoutModel(initial) {
    let preferences = normalizePreferences(initial), drag = null;
    // All preference return values are detached objects. Only commit accepts a
    // temporary drag; cancel restores the exact preferences before pointerdown.
    return {
      getState: () => ({ ...preferences }), isDragging: () => Boolean(drag),
      begin(axis, layout, coordinate) {
        if (drag || !dimensions[axis] || !Number.isFinite(coordinate) || layout?.mode !== "desktop" || layout.maximized) return false;
        drag = { axis, layout, coordinate, original: { ...preferences } }; return true;
      },
      move(coordinate) {
        if (drag && Number.isFinite(coordinate)) preferences = resizePreference(drag.original, drag.layout, drag.axis, coordinate - drag.coordinate);
        return { ...preferences };
      },
      commit() { if (!drag) return false; const changed = !same(preferences, drag.original); drag = null; return changed; },
      cancel() { if (!drag) return false; preferences = drag.original; drag = null; return true; },
      reset(axis) { this.cancel(); preferences = resetPreference(preferences, axis); return { ...preferences }; },
      setMaximized(value) { this.cancel(); preferences = { ...preferences, maximized: value === true }; return { ...preferences }; },
    };
  }

  function mountEditorLayout(elements, options = {}) {
    const { shell, viewer, stage, timeline, librarySeparator, inspectorSeparator, viewerSeparator, maximizeButton, resetButton } = elements;
    const win = options.window || root, doc = options.document || shell.ownerDocument || win.document;
    const getStyle = options.getStyle || (node => win.getComputedStyle?.(node) || {}), bindings = [];
    let storage = options.storage, initial, gesture = null, layout = null, observer = null, destroyed = false, frame = null;
    try { storage ||= win.localStorage; initial = JSON.parse(storage?.getItem(STORAGE_KEY) || "null"); } catch { initial = null; }
    const model = createLayoutModel(initial), blocked = () => Boolean(options.isBlocked?.());
    const separators = [[librarySeparator, "library"], [inspectorSeparator, "inspector"], [viewerSeparator, "viewer"]];
    const number = (style, name) => parseFloat(style[name]) || 0;
    function bind(node, name, callback, config) { if (!node?.addEventListener) return; node.addEventListener(name, callback, config); bindings.push([node, name, callback, config]); }
    function persist() { try { storage?.setItem(STORAGE_KEY, JSON.stringify(model.getState())); } catch { /* Private browsing and full storage still allow resizing. */ } }
    function viewerMinimum() {
      const style = getStyle(viewer);
      let chrome = number(style, "paddingTop") + number(style, "paddingBottom") + number(style, "borderTopWidth") + number(style, "borderBottomWidth");
      for (const child of viewer.children || []) {
        if (child === stage || child.hidden || child.classList?.contains("hidden")) continue;
        const childStyle = getStyle(child);
        if (childStyle.display === "none" || ["absolute", "fixed"].includes(childStyle.position)) continue;
        chrome += (child.getBoundingClientRect?.().height || 0) + number(childStyle, "marginTop") + number(childStyle, "marginBottom");
      }
      return Math.ceil(chrome + MIN_STAGE_HEIGHT);
    }
    function metrics() {
      const rect = shell.getBoundingClientRect(), style = getStyle(shell);
      const horizontalPadding = number(style, "paddingLeft") + number(style, "paddingRight");
      const verticalPadding = number(style, "paddingTop") + number(style, "paddingBottom");
      // Use the document position, so scrolling a short viewport cannot make
      // its workspace grow every time it is measured.
      const documentTop = rect.top + (win.scrollY || 0);
      return { width: Math.max(1, (shell.clientWidth || rect.width) - horizontalPadding),
        height: Math.max(1, (win.innerHeight || rect.height) - documentTop - verticalPadding),
        viewportWidth: win.innerWidth || rect.width, viewerMinHeight: viewerMinimum() };
    }
    function render() {
      const prefs = model.getState(), unavailable = blocked();
      shell.classList.toggle("preview-maximized", prefs.maximized);
      for (const [node, axis] of separators) {
        if (!node) continue;
        const disabled = !layout || layout.mode !== "desktop" || prefs.maximized || unavailable;
        node.setAttribute("aria-disabled", String(disabled)); node.tabIndex = disabled ? -1 : 0;
        if (layout) {
          node.setAttribute("aria-valuemin", String(Math.round(layout.bounds[axis].min)));
          node.setAttribute("aria-valuemax", String(Math.round(layout.bounds[axis].max)));
          node.setAttribute("aria-valuenow", String(Math.round(layout[dimensions[axis]])));
          node.setAttribute("aria-valuetext", `${Math.round(layout[dimensions[axis]])} 像素`);
        }
      }
      if (maximizeButton) {
        maximizeButton.disabled = unavailable; maximizeButton.setAttribute("aria-pressed", String(prefs.maximized));
        maximizeButton.textContent = prefs.maximized ? "↙ 返回剪輯版面" : "⛶ 放大預覽窗格";
        maximizeButton.title = prefs.maximized ? "返回剪輯版面（Esc）" : "讓預覽佔滿工作區";
      }
      if (resetButton) resetButton.disabled = unavailable;
      return layout;
    }
    function refresh() {
      if (destroyed) return layout;
      const next = deriveLayout(model.getState(), metrics()), changed = !layout || JSON.stringify(next) !== JSON.stringify(layout);
      layout = next;
      for (const [name, value] of Object.entries({ "--layout-library-width": layout.libraryWidth, "--layout-inspector-width": layout.inspectorWidth,
        "--layout-viewer-height": layout.viewerHeight, "--layout-viewer-min-height": layout.bounds.viewer.min,
        "--layout-timeline-height": layout.timelineHeight, "--layout-height": layout.height })) shell.style.setProperty(name, `${value}px`);
      render();
      if (changed) options.onResize?.(layout);
      return layout;
    }
    function scheduleRefresh() {
      if (destroyed || frame !== null) return;
      if (!win.requestAnimationFrame) { refresh(); return; }
      frame = win.requestAnimationFrame(() => { frame = null; refresh(); });
    }
    function releaseGesture() {
      const ending = gesture; gesture = null;
      doc.body?.classList.remove("is-layout-resizing", "layout-resize-horizontal", "layout-resize-vertical");
      ending?.node.classList.remove("is-resizing");
      if (ending?.node.hasPointerCapture?.(ending.pointerId)) ending.node.releasePointerCapture(ending.pointerId);
      if (ending) options.onDragState?.(false);
    }
    function cancelDrag() {
      if (!gesture) return false;
      model.cancel(); releaseGesture(); refresh(); return true;
    }
    function reset(axis) {
      if (blocked()) return;
      cancelDrag(); const before = model.getState(); model.reset(axis); refresh();
      if (!same(before, model.getState())) persist();
    }
    function setMaximized(enabled) {
      if (blocked()) return;
      cancelDrag(); const before = model.getState(); model.setMaximized(enabled); refresh();
      if (!same(before, model.getState())) persist();
      maximizeButton?.focus?.({ preventScroll: true });
    }
    for (const [node, axis] of separators) {
      bind(node, "pointerdown", event => {
        if (gesture || blocked() || event.button !== 0 || event.isPrimary === false) return;
        refresh(); const coordinate = axis === "viewer" ? event.clientY : event.clientX;
        if (!model.begin(axis, layout, coordinate)) return;
        event.preventDefault(); event.stopPropagation();
        try { node.setPointerCapture(event.pointerId); } catch { model.cancel(); return; }
        gesture = { node, axis, pointerId: event.pointerId };
        doc.body?.classList.add("is-layout-resizing", axis === "viewer" ? "layout-resize-vertical" : "layout-resize-horizontal");
        node.classList.add("is-resizing"); node.focus?.({ preventScroll: true }); options.onDragState?.(true); render();
      });
      bind(node, "pointermove", event => {
        if (!gesture || gesture.node !== node || event.pointerId !== gesture.pointerId) return;
        event.preventDefault(); event.stopPropagation();
        if (event.buttons === 0) { cancelDrag(); return; }
        model.move(axis === "viewer" ? event.clientY : event.clientX); refresh();
      });
      bind(node, "pointerup", event => {
        if (!gesture || gesture.node !== node || event.pointerId !== gesture.pointerId) return;
        event.preventDefault(); event.stopPropagation(); model.move(axis === "viewer" ? event.clientY : event.clientX);
        const changed = model.commit(); releaseGesture(); refresh(); if (changed) persist();
      });
      for (const name of ["pointercancel", "lostpointercapture"]) bind(node, name, event => { if (gesture?.node === node && gesture.pointerId === event.pointerId) cancelDrag(); });
      bind(node, "dblclick", event => { if (layout?.mode !== "desktop" || model.getState().maximized || blocked()) return; event.preventDefault(); event.stopPropagation(); reset(axis); });
      bind(node, "keydown", event => {
        if (event.ctrlKey || event.metaKey || event.altKey || blocked() || gesture || layout?.mode !== "desktop" || model.getState().maximized) return;
        const delta = keyboardDelta(event.key, event.shiftKey, axis);
        if (event.key !== "Home" && delta === null) return;
        event.preventDefault(); event.stopPropagation();
        if (event.key === "Home") { reset(axis); return; }
        const coordinate = 0;
        if (model.begin(axis, layout, coordinate)) { model.move(delta); const changed = model.commit(); refresh(); if (changed) persist(); }
      });
    }
    bind(maximizeButton, "click", () => setMaximized(!model.getState().maximized));
    bind(resetButton, "click", () => reset());
    bind(doc, "keydown", event => {
      if (event.key !== "Escape" || !gesture) return;
      event.preventDefault(); event.stopImmediatePropagation(); cancelDrag();
    }, true);
    // Bubble after the preview's hand/position/speed controls have had a chance
    // to consume Escape, and leave native text/select editing alone.
    bind(doc, "keydown", event => {
      if (event.key !== "Escape" || event.defaultPrevented || blocked() || !model.getState().maximized ||
        event.target?.closest?.("input,textarea,select,[contenteditable=true],#speedEditor")) return;
      event.preventDefault(); event.stopImmediatePropagation(); setMaximized(false);
    });
    bind(win, "blur", cancelDrag);
    bind(win, "resize", () => { cancelDrag(); refresh(); });
    bind(doc, "visibilitychange", () => { if (doc.hidden) cancelDrag(); });
    if (win.ResizeObserver) {
      observer = new win.ResizeObserver(scheduleRefresh); observer.observe(shell);
      for (const child of viewer.children || []) if (child !== stage) observer.observe(child);
      // Notices and recovery banners can change the workspace's top edge.
      for (const child of doc.body?.children || []) if (child !== shell && child !== viewer && child !== timeline) observer.observe(child);
    }
    refresh();
    return { model, render, refresh, cancelDrag, reset, setMaximized, isDragging: () => Boolean(gesture), isMaximized: () => model.getState().maximized,
      getPreferences: () => model.getState(), getLayout: () => layout && { ...layout, bounds: Object.fromEntries(Object.entries(layout.bounds).map(([key, value]) => [key, { ...value }])) },
      destroy() { cancelDrag(); destroyed = true; observer?.disconnect(); if (frame !== null) win.cancelAnimationFrame?.(frame);
        for (const [node, name, callback, config] of bindings) node.removeEventListener(name, callback, config); bindings.length = 0; } };
  }
  const api = { STORAGE_KEY, DEFAULT_PREFERENCES, SEPARATOR_SIZE, MIN_LIBRARY_WIDTH, MIN_INSPECTOR_WIDTH, MIN_VIEWER_WIDTH,
    MIN_STAGE_HEIGHT, MIN_TIMELINE_HEIGHT, normalizePreferences, deriveLayout, resizePreference, resetPreference, keyboardDelta, createLayoutModel, mountEditorLayout };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.H3EditorLayout = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
