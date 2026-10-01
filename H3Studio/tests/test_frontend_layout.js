"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const layout = require("../static/editor_layout.js"), core = require("../static/editor.js");
const preview = require("../static/editor_preview.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const html = fs.readFileSync(require.resolve("../static/editor.html"), "utf8");
const css = fs.readFileSync(require.resolve("../static/editor.css"), "utf8");
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const defaults = () => ({ ...layout.DEFAULT_PREFERENCES });
const preferences = () => ({ libraryWidth: 280, inspectorWidth: 270, viewerHeight: 480, maximized: false });
const metrics = (viewportWidth = 1400, height = 800) => ({ width: viewportWidth - 24, height, viewportWidth, viewerMinHeight: 280, timelineMinHeight: 200 });

class Surface {
  constructor(doc, tag = "div") {
    this.ownerDocument = doc; this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.attributes = new Map(); this.listeners = new Map(); this.dataset = {}; this.disabled = false; this.hidden = false;
    this.clientWidth = 1376; this.clientHeight = 800; this.offsetWidth = 1376; this.offsetHeight = 800;
    this.rect = { left: 12, top: 92, width: 1376, height: 800 }; this.capture = null; this.captureFailure = false;
    const values = new Set(); this.classList = { add: (...names) => names.forEach(name => values.add(name)),
      remove: (...names) => names.forEach(name => values.delete(name)), contains: name => values.has(name), toggle(name, force) {
        const on = force === undefined ? !values.has(name) : force; if (on) values.add(name); else values.delete(name); return on;
      } };
    this.style = { setProperty(name, value) { this[name] = String(value); }, getPropertyValue(name) { return this[name] ?? ""; },
      removeProperty(name) { const value = this[name] ?? ""; delete this[name]; return value; } };
    this._src = ""; this.sourceWrites = 0; this.htmlWrites = 0; this.textContent = "";
  }
  set src(value) { this.sourceWrites++; this._src = value; }
  get src() { return this._src; }
  set innerHTML(value) { this.htmlWrites++; this.children = []; }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, callback, config) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push({ callback, config }); }
  removeEventListener(name, callback) { this.listeners.set(name, (this.listeners.get(name) || []).filter(value => value.callback !== callback)); }
  dispatch(name, values = {}) {
    const event = { type: name, target: this, currentTarget: this, pointerId: 7, pointerType: "mouse", button: 0, buttons: 1,
      isPrimary: true, clientX: 300, clientY: 500, key: "", code: "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      repeat: false, defaultPrevented: false, propagationStopped: false, immediateStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; },
      stopImmediatePropagation() { this.propagationStopped = true; this.immediateStopped = true; }, ...values };
    const listeners = [...(this.listeners.get(name) || [])].sort((left, right) => Number(right.config === true || right.config?.capture) - Number(left.config === true || left.config?.capture));
    for (const value of listeners) { if (event.immediateStopped) break; value.callback(event); }
    if (!event.immediateStopped) this[`on${name}`]?.(event);
    return event;
  }
  closest(selector) {
    return selector.split(",").map(value => value.trim()).some(value => value === this.tagName.toLowerCase() ||
      value.startsWith("#") && this.getAttribute("id") === value.slice(1) ||
      value.startsWith(".") && this.classList.contains(value.slice(1)) ||
      value === "[contenteditable=true]" && this.getAttribute("contenteditable") === "true") ? this : this.parentNode?.closest(selector) || null;
  }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  focus() { this.ownerDocument.activeElement = this; }
  setPointerCapture(id) { if (this.captureFailure) throw new Error("capture unavailable"); this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture(id) { if (this.capture === id) this.capture = null; this.dispatch("lostpointercapture", { pointerId: id }); }
  getBoundingClientRect() { return { ...this.rect, right: this.rect.left + this.rect.width, bottom: this.rect.top + this.rect.height }; }
}
function project() {
  return { id: "layout-project", name: "預覽窗格測試", width: 1280, height: 720, fps: 24, updated_at: "r1",
    clips: [{ id: "main", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [],
    overlays: [{ id: "caption", kind: "text", start: 0, end: 8, text: "保留畫面節點", x: .5, y: .5,
      width: .7, font_size: .06, rotation: 0, opacity: 1, color: "#ffffff", background: "transparent", bold: true, align: "center" }] };
}
function harness(options = {}) {
  const doc = new Surface(null, "document"); doc.ownerDocument = doc; doc.body = new Surface(doc, "body"); doc.activeElement = doc.body;
  const win = new Surface(doc, "window"); doc.defaultView = win; win.innerWidth = options.width || 1400; win.innerHeight = 1000;
  win.getComputedStyle = node => ({ paddingLeft: node === elements.shell ? "12px" : "0px", paddingRight: node === elements.shell ? "12px" : "0px",
    paddingTop: node === elements.shell ? "12px" : "0px", paddingBottom: node === elements.shell ? "12px" : "0px", gap: "10px",
    borderLeftWidth: "1px", borderRightWidth: "1px", borderTopWidth: "1px", borderBottomWidth: "1px", minHeight: node === elements.stage ? "100px" : "0px",
    ...node.computedStyle });
  const elements = Object.fromEntries(["shell", "viewer", "stage", "timeline", "library", "inspector", "librarySeparator", "inspectorSeparator", "viewerSeparator", "maximizeButton", "resetButton"]
    .map(name => [name, new Surface(doc, name.endsWith("Button") ? "button" : "div")]));
  const ids = { shell: "editorShell", viewer: "previewPanel", stage: "videoStage", timeline: "timelinePanel", library: "libraryPanel",
    inspector: "inspectorPanel", librarySeparator: "layoutLibrarySeparator", inspectorSeparator: "layoutInspectorSeparator", viewerSeparator: "layoutViewerSeparator",
    maximizeButton: "previewMaximize", resetButton: "layoutReset" };
  for (const [name, id] of Object.entries(ids)) elements[name].setAttribute("id", id);
  for (const axis of ["library", "inspector", "viewer"]) elements[`${axis}Separator`].setAttribute("aria-orientation", axis === "viewer" ? "horizontal" : "vertical");
  elements.shell.clientWidth = win.innerWidth; elements.shell.clientHeight = 824; elements.shell.rect.width = win.innerWidth;
  elements.viewer.clientHeight = 480; elements.viewer.offsetHeight = 480; elements.stage.clientHeight = 280; elements.stage.offsetHeight = 280;
  elements.timeline.clientHeight = 310; elements.timeline.offsetHeight = 310;
  doc.body.append(elements.shell); elements.shell.append(elements.library, elements.viewer, elements.inspector, elements.timeline,
    elements.librarySeparator, elements.inspectorSeparator, elements.viewerSeparator);
  const toolbar = new Surface(doc), chrome = [];
  toolbar.rect.height = 32; toolbar.append(elements.maximizeButton, elements.resetButton);
  for (const height of [45, 35, 58, 17, 22]) { const node = new Surface(doc); node.rect.height = height; chrome.push(node); }
  elements.viewer.append(chrome[0], toolbar, elements.stage, ...chrome.slice(1));
  const exportPanel = new Surface(doc), floating = new Surface(doc);
  exportPanel.rect.height = 260; exportPanel.hidden = true; floating.rect.height = 10000; floating.computedStyle = { position: "absolute" };
  elements.viewer.append(exportPanel, floating);
  const canvas = new Surface(doc), video = new Surface(doc, "video"), text = new Surface(doc, "img"); elements.stage.append(canvas); canvas.append(video, text);
  video.src = "/source.mp4"; text.src = "blob:cached-caption";
  const session = new core.ProjectSession(project()); let contentWrites = 0; session.onChange = () => { contentWrites++; };
  const cacheEntry = { url: text.src, bytes: 50000 }, cache = new Map([[core.overlayRasterKey(session.project.overlays[0], 1280, 720), cacheEntry]]);
  const signature = core.signature(session.project), projectObject = session.project, shellChildren = [...elements.shell.children], viewerChildren = [...elements.viewer.children];
  const writes = [], storage = options.storage || { getItem: key => options.stored ?? null, setItem: (key, value) => writes.push([key, value]), removeItem: key => writes.push([key, null]) };
  const callbacks = [], control = { blocked: Boolean(options.blocked) };
  const observers = [], frames = new Map(); let frameId = 0;
  if (options.observe) {
    win.ResizeObserver = class { constructor(callback) { this.callback = callback; this.nodes = []; this.disconnected = false; observers.push(this); }
      observe(node) { this.nodes.push(node); } disconnect() { this.disconnected = true; } };
    win.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
    win.cancelAnimationFrame = id => frames.delete(id);
  }
  const controller = layout.mountEditorLayout(elements, { window: win, document: doc, storage, isBlocked: () => control.blocked,
    onResize: value => callbacks.push(value) });
  function unchanged() {
    assert.equal(session.project, projectObject); assert.equal(core.signature(session.project), signature); assert.equal(session.undoStack.length, 0);
    assert.equal(session.redoStack.length, 0); assert.equal(session.dirty, false); assert.equal(contentWrites, 0);
    assert.deepEqual(elements.shell.children, shellChildren); assert.deepEqual(elements.viewer.children, viewerChildren);
    assert.deepEqual(canvas.children, [video, text]); assert.equal(video.sourceWrites, 1); assert.equal(text.sourceWrites, 1);
    assert.equal(video.src, "/source.mp4"); assert.equal(text.src, "blob:cached-caption"); assert.equal(elements.viewer.htmlWrites, 0);
    assert.equal(cache.size, 1); assert.equal([...cache.values()][0], cacheEntry);
  }
  const dispatch = (axis, name, values) => elements[`${axis}Separator`].dispatch(name, values);
  const prefs = () => controller.getPreferences();
  const resize = width => { win.innerWidth = width; elements.shell.clientWidth = width; elements.shell.rect.width = width; win.dispatch("resize"); controller.refresh(); };
  const click = name => elements[name].disabled ? null : elements[name].dispatch("click");
  return { doc, win, elements, controller, session, cache, canvas, video, text, control, writes, callbacks, unchanged, dispatch, prefs, resize, click,
    storage, exportPanel, floating, observers, frames, runFrame() { const next = frames.entries().next().value; assert.ok(next); frames.delete(next[0]); next[1](); } };
}
function shipped(name) {
  const start = source.indexOf(`function ${name}(`); assert.ok(start >= 0);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`cannot extract shipped ${name}`);
}
function editorHarness() {
  const h = harness({ stored: JSON.stringify(preferences()) }), nodes = Object.fromEntries(Object.values(h.elements).map(node => [node.getAttribute("id"), node]));
  nodes.videoCanvas = h.canvas;
  for (const id of ["previewZoom", "previewZoomIn", "previewZoomOut", "previewFit", "previewHand"]) nodes[id] = new Surface(h.doc, id === "previewZoom" ? "select" : "button");
  const state = { session: h.session, backendReady: true, busy: false, trimDrag: null, overlayDrag: null, speedDrag: null, trackDrag: null,
    layoutDragging: false, rasterCache: h.cache }, calls = { play: 0, disabled: 0, finishOverlay: 0, previewResize: 0 };
  h.win.H3EditorPreview = preview; h.win.H3EditorLayout = layout; h.win.localStorage = h.storage;
  const sandbox = { ...core, root: h.win, document: h.doc, state, $: id => nodes[id], action: fn => fn,
    locked: () => Boolean(state.layoutDragging || state.overlayDrag || state.trimDrag || state.speedDrag || state.trackDrag),
    renderDisabled: () => { calls.disabled++; sandbox.previewViewport?.render(); sandbox.layoutController?.render(); },
    finishOverlayGesture: () => { calls.finishOverlay++; state.overlayDrag = null; },
    finishTrim() {}, finishTrackGesture() {}, jumpPositionKeyframe: () => false,
    togglePlay: () => { calls.play++; }, renderTimeline() {}, renderInspector() {}, renderOverlayPreview() {},
    keyboardTrim() {}, saveProject() {}, overlayEdit() {}, travel() {}, doSplit() {}, deleteSelected() {} };
  vm.createContext(sandbox);
  const projectBinding = source.match(/^  const project = .+;$/m); assert.ok(projectBinding); vm.runInContext(projectBinding[0], sandbox);
  for (const name of ["fitCanvas", "resizePreview"]) vm.runInContext(shipped(name), sandbox);
  const previewStart = source.indexOf("  const previewViewport = root.H3EditorPreview.mountPreviewViewport("),
    previewEnd = source.indexOf("\n  const trackDragUI", previewStart); assert.ok(previewStart >= 0 && previewEnd > previewStart);
  vm.runInContext(source.slice(previewStart, previewEnd) + "\nthis.previewViewport = previewViewport;", sandbox); sandbox.fitCanvas();
  const layoutStart = source.indexOf("  const layoutController = root.H3EditorLayout.mountEditorLayout("),
    layoutEnd = source.indexOf("\n  const speedEditor", layoutStart); assert.ok(layoutStart >= 0 && layoutEnd > layoutStart);
  h.controller.destroy(); vm.runInContext(source.slice(layoutStart, layoutEnd) + "\nthis.layoutController = layoutController;", sandbox);
  h.controller = sandbox.layoutController; h.prefs = () => h.controller.getPreferences();
  const keysStart = source.indexOf('  document.addEventListener("keydown", action(async event => {'),
    keysEnd = source.indexOf('\n  root.addEventListener("blur"', keysStart); assert.ok(keysStart >= 0 && keysEnd > keysStart);
  vm.runInContext(source.slice(keysStart, keysEnd), sandbox);
  return { ...h, sandbox, state, calls, nodes, previewViewport: sandbox.previewViewport,
    cleanup() { h.controller.destroy(); sandbox.previewViewport.destroy(); } };
}

test("desktop pane constraints preserve a usable preview and timeline at narrow and large sizes", () => {
  for (const width of [851, 1000, 1400, 2400]) for (const request of [defaults(), { libraryWidth: 10000, inspectorWidth: 10000, viewerHeight: 10000, maximized: false }]) {
    const result = layout.deriveLayout(request, metrics(width)); assert.equal(result.mode, "desktop");
    assert.ok(result.libraryWidth >= 180); assert.ok(result.inspectorWidth >= 190); assert.ok(result.viewerWidth >= 300);
    assert.ok(result.viewerHeight >= 280); assert.ok(result.timelineHeight >= 200);
    assert.ok(result.libraryWidth + result.inspectorWidth + result.viewerWidth + 20 <= width - 24 + 1e-8);
    near(result.viewerHeight + result.timelineHeight + 10, result.height);
  }
  const tiny = layout.deriveLayout(preferences(), metrics(851, 100));
  assert.ok(tiny.height >= 490); assert.ok(tiny.viewerHeight >= 280); assert.ok(tiny.timelineHeight >= 200);
});

test("geometry distinguishes desktop 851, tablet 571 to 850 and mobile 570", () => {
  for (const [width, mode] of [[851, "desktop"], [850, "tablet"], [571, "tablet"], [570, "mobile"], [320, "mobile"]])
    assert.equal(layout.deriveLayout(preferences(), metrics(width)).mode, mode);
});

test("pure resizing changes one pane with the actual right-separator sign and reset retains other dimensions", () => {
  const original = preferences(), fitted = layout.deriveLayout(original, metrics());
  const left = layout.resizePreference(original, fitted, "library", 30); near(left.libraryWidth, fitted.libraryWidth + 30);
  assert.equal(left.inspectorWidth, original.inspectorWidth); assert.equal(left.viewerHeight, original.viewerHeight);
  const right = layout.resizePreference(original, fitted, "inspector", 30); near(right.inspectorWidth, fitted.inspectorWidth - 30);
  const down = layout.resizePreference(original, fitted, "viewer", 30); near(down.viewerHeight, fitted.viewerHeight + 30);
  assert.deepEqual(original, preferences());
  const reset = layout.resetPreference(original, "library"); assert.equal(reset.libraryWidth, null);
  assert.equal(reset.inspectorWidth, original.inspectorWidth); assert.equal(reset.viewerHeight, original.viewerHeight);
});

test("only finite positive dimensions and a real boolean survive preference normalization", () => {
  assert.deepEqual(layout.normalizePreferences({ ...preferences(), other: "discard" }), preferences());
  for (const bad of [null, [], "broken", true, 12]) assert.deepEqual(layout.normalizePreferences(bad), defaults());
  for (const key of ["libraryWidth", "inspectorWidth", "viewerHeight"]) for (const value of [true, false, "280", null, NaN, Infinity, -1, 0]) {
    const clean = layout.normalizePreferences({ ...preferences(), [key]: value }); assert.equal(clean[key], null);
    for (const other of ["libraryWidth", "inspectorWidth", "viewerHeight"].filter(name => name !== key)) assert.equal(clean[other], preferences()[other]);
  }
  for (const value of [1, "true", null, {}]) assert.equal(layout.normalizePreferences({ maximized: value }).maximized, false);
});

test("shipped HTML loads layout before editor and supplies accessible separators and view-only buttons", () => {
  assert.ok(html.indexOf("editor_layout.js?") >= 0); assert.ok(html.indexOf("editor_layout.js?") < html.indexOf("/static/editor.js?"));
  for (const id of ["editorShell", "libraryPanel", "previewPanel", "inspectorPanel", "timelinePanel", "previewMaximize", "layoutReset"])
    assert.match(html, new RegExp(`id="${id}"`));
  for (const id of ["layoutLibrarySeparator", "layoutInspectorSeparator", "layoutViewerSeparator"]) {
    const tag = html.match(new RegExp(`<[^>]+id="${id}"[^>]*>`))?.[0]; assert.ok(tag);
    assert.match(tag, /role="separator"/); assert.match(tag, /tabindex="0"/); assert.match(tag, /aria-label="[^\"]+"/);
    assert.match(tag, new RegExp(`aria-orientation="${id === "layoutViewerSeparator" ? "horizontal" : "vertical"}"`));
  }
});

test("pointer moves are live layout previews and only release persists one dedicated preference record", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs(), baselineWrites = h.writes.length;
  const event = h.dispatch("library", "pointerdown", { clientX: 300 }); assert.equal(event.defaultPrevented, true); assert.equal(h.controller.isDragging(), true);
  h.dispatch("library", "pointermove", { clientX: 320 }); h.dispatch("library", "pointermove", { clientX: 340 });
  assert.equal(h.writes.length, baselineWrites); h.unchanged();
  h.dispatch("library", "pointerup", { clientX: 350 }); assert.equal(h.controller.isDragging(), false); assert.equal(h.elements.librarySeparator.capture, null);
  near(h.prefs().libraryWidth, original.libraryWidth + 50); assert.equal(h.writes.length, baselineWrites + 1);
  assert.equal(h.writes.at(-1)[0], "h3-editor-layout-v1"); assert.deepEqual(JSON.parse(h.writes.at(-1)[1]), h.prefs());
  h.unchanged(); h.controller.destroy();
});

for (const cause of ["pointercancel", "lostpointercapture", "blur", "Escape"]) test(`${cause} restores an unfinished separator drag without persisting its preview`, () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs(), baselineWrites = h.writes.length;
  h.dispatch("viewer", "pointerdown", { clientY: 500 }); h.dispatch("viewer", "pointermove", { clientY: 560 });
  assert.equal(h.controller.isDragging(), true);
  if (cause === "blur") h.win.dispatch("blur");
  else if (cause === "Escape") h.doc.dispatch("keydown", { key: "Escape", target: h.elements.viewerSeparator });
  else h.dispatch("viewer", cause);
  assert.equal(h.controller.isDragging(), false); assert.equal(h.elements.viewerSeparator.capture, null);
  assert.deepEqual(h.prefs(), original); assert.equal(h.writes.length, baselineWrites); h.unchanged(); h.controller.destroy();
});

test("wrong pointers, capture failure, non-primary and locked gestures leave no layout writes", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs(), baselineWrites = h.writes.length;
  h.control.blocked = true; h.dispatch("library", "pointerdown"); assert.equal(h.controller.isDragging(), false); h.control.blocked = false;
  h.dispatch("library", "pointerdown", { isPrimary: false }); assert.equal(h.controller.isDragging(), false);
  h.dispatch("library", "pointerdown", { button: 1 }); assert.equal(h.controller.isDragging(), false);
  h.elements.librarySeparator.captureFailure = true; h.dispatch("library", "pointerdown"); assert.equal(h.controller.isDragging(), false);
  h.elements.librarySeparator.captureFailure = false; h.dispatch("library", "pointerdown");
  h.dispatch("library", "pointermove", { pointerId: 99, clientX: 800 }); h.dispatch("library", "pointerup", { pointerId: 99 });
  assert.equal(h.controller.isDragging(), true); h.dispatch("library", "pointercancel");
  assert.deepEqual(h.prefs(), original); assert.equal(h.writes.length, baselineWrites); h.unchanged(); h.controller.destroy();
});

test("keyboard increments 10 pixels or Shift 50 pixels and Home resets only that separator", () => {
  const h = harness({ stored: JSON.stringify(preferences()) });
  h.dispatch("library", "keydown", { key: "ArrowRight" }); near(h.prefs().libraryWidth, 290);
  h.dispatch("library", "keydown", { key: "ArrowLeft", shiftKey: true }); near(h.prefs().libraryWidth, 240);
  h.dispatch("inspector", "keydown", { key: "ArrowRight" }); near(h.prefs().inspectorWidth, 260);
  h.dispatch("viewer", "keydown", { key: "ArrowDown", shiftKey: true }); near(h.prefs().viewerHeight, 530);
  const original = h.prefs(); h.dispatch("library", "keydown", { key: "Home" }); assert.equal(h.prefs().libraryWidth, null);
  assert.equal(h.prefs().inspectorWidth, original.inspectorWidth); assert.equal(h.prefs().viewerHeight, original.viewerHeight);
  const count = h.writes.length; const space = h.dispatch("viewer", "keydown", { key: " ", code: "Space" });
  assert.equal(space.defaultPrevented, false); assert.equal(h.writes.length, count);
  h.unchanged(); h.controller.destroy();
});

test("double-click resets a single dimension and the reset-layout button restores all preferences", () => {
  const h = harness({ stored: JSON.stringify(preferences()) });
  h.dispatch("viewer", "dblclick"); assert.equal(h.prefs().viewerHeight, null); assert.equal(h.prefs().libraryWidth, 280); assert.equal(h.prefs().inspectorWidth, 270);
  h.click("resetButton"); assert.deepEqual(h.prefs(), defaults()); h.unchanged(); h.controller.destroy();
});

test("separator ARIA values describe current constraints and unsupported breakpoints disable interaction", () => {
  const h = harness({ stored: JSON.stringify(preferences()) });
  for (const axis of ["library", "inspector", "viewer"]) {
    const node = h.elements[`${axis}Separator`]; assert.equal(node.getAttribute("aria-disabled"), "false");
    const value = Number(node.getAttribute("aria-valuenow")), minimum = Number(node.getAttribute("aria-valuemin")), maximum = Number(node.getAttribute("aria-valuemax"));
    assert.ok(value >= minimum && value <= maximum); assert.ok(maximum > minimum);
    assert.equal(node.getAttribute("aria-orientation"), axis === "viewer" ? "horizontal" : "vertical");
  }
  h.resize(571); for (const axis of ["library", "inspector", "viewer"]) assert.equal(h.elements[`${axis}Separator`].getAttribute("aria-disabled"), "true");
  const before = h.prefs(), count = h.writes.length; h.dispatch("library", "pointerdown"); h.dispatch("viewer", "keydown", { key: "ArrowDown" });
  assert.equal(h.controller.isDragging(), false); assert.deepEqual(h.prefs(), before); assert.equal(h.writes.length, count); h.unchanged(); h.controller.destroy();
});

test("mobile does not acquire desktop inline grid templates and returning to desktop restores saved pane choices", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }); near(h.controller.getLayout().libraryWidth, 280);
  h.resize(570); assert.equal(h.elements.shell.style.gridTemplateColumns || "", ""); assert.equal(h.elements.shell.style.gridTemplateRows || "", "");
  assert.equal(h.controller.getLayout().mode, "mobile");
  assert.deepEqual(h.prefs(), preferences()); h.resize(1400); near(h.controller.getLayout().libraryWidth, 280); assert.deepEqual(h.prefs(), preferences());
  assert.match(css, /@media\s*\(min-width:\s*851px\)/); assert.match(css, /grid-template-columns:[^}]*var\(--layout-library-width/);
  h.unchanged(); h.controller.destroy();
});

test("corrupt storage and denied reads or writes do not block layout adjustment", () => {
  for (const stored of ["not json", "null", "[]", "true", JSON.stringify({ libraryWidth: "wide", inspectorWidth: Infinity, viewerHeight: -2, maximized: "yes" })]) {
    const h = harness({ stored }); assert.deepEqual(h.prefs(), defaults()); h.dispatch("library", "keydown", { key: "ArrowRight" });
    assert.ok(Number.isFinite(h.prefs().libraryWidth)); h.unchanged(); h.controller.destroy();
  }
  const h = harness({ storage: { getItem() { throw new Error("storage denied"); }, setItem() { throw new Error("quota denied"); }, removeItem() { throw new Error("denied"); } } });
  assert.deepEqual(h.prefs(), defaults()); h.dispatch("library", "keydown", { key: "ArrowRight" }); assert.ok(Number.isFinite(h.prefs().libraryWidth));
  h.click("maximizeButton"); assert.equal(h.controller.isMaximized(), true); h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage });
  assert.equal(h.controller.isMaximized(), false); h.unchanged(); h.controller.destroy();
});

test("maximizing and returning by button or Escape preserve pane dimensions and media node identity", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs(), geometry = h.controller.getLayout();
  for (const escape of [false, true]) {
    h.click("maximizeButton"); assert.equal(h.controller.isMaximized(), true);
    for (const field of ["libraryWidth", "inspectorWidth", "viewerHeight"]) assert.equal(h.prefs()[field], original[field]);
    h.unchanged();
    if (escape) h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage }); else h.click("maximizeButton");
    assert.equal(h.controller.isMaximized(), false); assert.deepEqual(h.prefs(), original);
    assert.deepEqual(h.controller.getLayout(), geometry);
  }
  assert.ok(h.writes.every(([key]) => key === "h3-editor-layout-v1")); h.unchanged(); h.controller.destroy();
});

test("destroy rolls back unfinished drag and detaches layout event handlers", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs();
  h.dispatch("library", "pointerdown"); h.dispatch("library", "pointermove", { clientX: 360 }); h.controller.destroy();
  assert.equal(h.controller.isDragging(), false); assert.equal(h.elements.librarySeparator.capture, null); assert.deepEqual(h.prefs(), original);
  const count = h.writes.length; h.dispatch("library", "keydown", { key: "ArrowRight" }); h.click("maximizeButton");
  assert.deepEqual(h.prefs(), original); assert.equal(h.writes.length, count); h.unchanged();
});

test("layout model has detached snapshots, original-based drag deltas, atomic cancellation and one commit", () => {
  const initial = preferences(), fitted = layout.deriveLayout(initial, metrics()), model = layout.createLayoutModel(initial);
  const snapshot = model.getState(); snapshot.libraryWidth = 999; assert.deepEqual(model.getState(), initial);
  assert.equal(model.begin("library", fitted, 300), true); model.move(330); near(model.getState().libraryWidth, 310);
  model.move(350); near(model.getState().libraryWidth, 330); model.move(NaN); near(model.getState().libraryWidth, 330);
  assert.equal(model.begin("viewer", fitted, 500), false); assert.equal(model.cancel(), true); assert.deepEqual(model.getState(), initial);
  assert.equal(model.commit(), false); assert.equal(model.cancel(), false);
  assert.equal(model.begin("inspector", fitted, 300), true); model.move(350); assert.equal(model.commit(), true);
  near(model.getState().inspectorWidth, 220); assert.equal(model.commit(), false); assert.equal(model.isDragging(), false);
  for (const [axis, coordinate] of [["wrong", 0], ["library", NaN], ["viewer", Infinity]]) assert.equal(model.begin(axis, fitted, coordinate), false);
});

test("all separator keyboard directions use expected ten/fifty pixel steps and unrelated keys are ignored", () => {
  for (const axis of ["library", "inspector", "viewer"]) {
    const negative = axis === "viewer" ? "ArrowUp" : "ArrowLeft", positive = axis === "viewer" ? "ArrowDown" : "ArrowRight";
    assert.equal(layout.keyboardDelta(negative, false, axis), -10); assert.equal(layout.keyboardDelta(positive, true, axis), 50);
    for (const key of [" ", "Home", "Escape", axis === "viewer" ? "ArrowRight" : "ArrowDown"]) assert.equal(layout.keyboardDelta(key, false, axis), null);
  }
});

test("visible export controls increase the minimum preview height while hidden and absolute chrome do not", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.controller.getLayout();
  assert.ok(original.bounds.viewer.min >= 100); assert.ok(original.bounds.viewer.min < 480);
  assert.equal(original.viewerHeight, 480); h.exportPanel.hidden = false; h.controller.refresh();
  const shown = h.controller.getLayout(); near(shown.bounds.viewer.min - original.bounds.viewer.min, 260);
  assert.ok(shown.viewerHeight >= shown.bounds.viewer.min); assert.ok(shown.timelineHeight >= 200);
  h.exportPanel.classList.add("hidden"); h.controller.refresh(); near(h.controller.getLayout().bounds.viewer.min, original.bounds.viewer.min);
  h.exportPanel.classList.remove("hidden"); h.exportPanel.computedStyle = { display: "none" }; h.controller.refresh();
  near(h.controller.getLayout().bounds.viewer.min, original.bounds.viewer.min);
  assert.equal(h.writes.length, 0); h.unchanged(); h.controller.destroy();
});

test("scrolling does not change document-based available height or grow the layout", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.controller.getLayout(), callbackCount = h.callbacks.length;
  h.win.scrollY = 200; h.elements.shell.rect.top -= 200; h.controller.refresh();
  assert.deepEqual(h.controller.getLayout(), original); assert.equal(h.callbacks.length, callbackCount);
  assert.equal(h.writes.length, 0); h.unchanged(); h.controller.destroy();
});

test("ResizeObserver coalesces chrome changes and cleanup cancels a pending resize", () => {
  const h = harness({ stored: JSON.stringify(preferences()), observe: true }); assert.equal(h.observers.length, 1);
  const observer = h.observers[0]; assert.ok(observer.nodes.includes(h.elements.shell)); assert.ok(observer.nodes.includes(h.exportPanel));
  assert.equal(observer.nodes.includes(h.elements.stage), false);
  const originalCount = h.callbacks.length; h.exportPanel.hidden = false; observer.callback(); observer.callback();
  assert.equal(h.frames.size, 1); assert.equal(h.callbacks.length, originalCount); h.runFrame(); assert.equal(h.callbacks.length, originalCount + 1);
  observer.callback(); assert.equal(h.frames.size, 1); h.controller.destroy(); assert.equal(h.frames.size, 0); assert.equal(observer.disconnected, true);
  h.unchanged();
});

test("a viewport breakpoint change cancels active resizing without storing desktop preview values", () => {
  const h = harness({ stored: JSON.stringify(preferences()) }), original = h.prefs(), count = h.writes.length;
  h.dispatch("library", "pointerdown", { clientX: 300 }); h.dispatch("library", "pointermove", { clientX: 350 });
  h.resize(570); assert.equal(h.controller.isDragging(), false); assert.deepEqual(h.prefs(), original);
  assert.equal(h.writes.length, count); assert.equal(h.controller.getLayout().mode, "mobile"); h.unchanged(); h.controller.destroy();
});

test("opening space from clamped sidebars gives it to the viewer and click-only gestures are not stored", () => {
  const original = { ...preferences(), libraryWidth: 600, inspectorWidth: 600 }, measured = metrics(1224), fitted = layout.deriveLayout(original, measured);
  near(fitted.libraryWidth, 600); near(fitted.inspectorWidth, 280); near(fitted.viewerWidth, 300);
  const changed = layout.resizePreference(original, fitted, "library", -100), widened = layout.deriveLayout(changed, measured);
  near(changed.libraryWidth, 500); near(changed.inspectorWidth, 280); near(widened.viewerWidth, 400);
  const h = harness({ stored: JSON.stringify(original) }), count = h.writes.length;
  h.dispatch("library", "pointerdown", { clientX: 300 }); h.dispatch("library", "pointerup", { clientX: 300 });
  assert.deepEqual(h.prefs(), original); assert.equal(h.writes.length, count); h.unchanged(); h.controller.destroy();
});

test("the shipped layout callbacks resize the existing camera without changing fixed zoom or editing state", () => {
  const h = editorHarness(); h.previewViewport.setZoom(1); const dimensions = [h.canvas.style.width, h.canvas.style.height];
  h.dispatch("library", "keydown", { key: "ArrowRight", shiftKey: true }); near(h.prefs().libraryWidth, 330);
  h.click("maximizeButton"); assert.equal(h.controller.isMaximized(), true); h.click("maximizeButton"); assert.equal(h.controller.isMaximized(), false);
  near(h.previewViewport.snapshot().scale, 1); assert.deepEqual([h.canvas.style.width, h.canvas.style.height], dimensions);
  h.unchanged(); h.cleanup();
});

test("the shipped active-layout flag blocks camera gestures and Space while normal separator Space still plays", () => {
  const h = editorHarness(); h.previewViewport.setZoom(1); h.previewViewport.setHand(true);
  h.doc.dispatch("keydown", { key: " ", code: "Space", target: h.elements.librarySeparator }); assert.equal(h.calls.play, 1);
  h.dispatch("library", "pointerdown", { clientX: 300 }); assert.equal(h.state.layoutDragging, true); assert.equal(h.nodes.previewZoom.disabled, true);
  const wheel = h.elements.stage.dispatch("wheel", { ctrlKey: true, deltaY: -80 }); assert.equal(wheel.defaultPrevented, false);
  near(h.previewViewport.snapshot().scale, 1); h.elements.stage.dispatch("pointerdown"); assert.equal(h.previewViewport.isPanning, false);
  const space = h.doc.dispatch("keydown", { key: " ", code: "Space", target: h.elements.librarySeparator });
  assert.equal(space.defaultPrevented, true); assert.equal(h.calls.play, 1);
  h.dispatch("library", "pointercancel"); assert.equal(h.state.layoutDragging, false); h.unchanged(); h.cleanup();
});

test("the actual mount refuses layout gestures during camera pan and content motion gestures", () => {
  const h = editorHarness(); h.previewViewport.setZoom(2); h.previewViewport.setHand(true); h.elements.stage.dispatch("pointerdown");
  assert.equal(h.previewViewport.isPanning, true); h.dispatch("library", "pointerdown"); assert.equal(h.controller.isDragging(), false);
  h.elements.stage.dispatch("pointercancel");
  for (const field of ["trimDrag", "overlayDrag", "speedDrag", "trackDrag"]) {
    h.state[field] = {}; h.dispatch("viewer", "pointerdown"); assert.equal(h.controller.isDragging(), false);
    h.click("maximizeButton"); assert.equal(h.controller.isMaximized(), false); h.state[field] = null;
  }
  h.unchanged(); h.cleanup();
});

test("Escape first cancels the shipped hand or position gesture before exiting maximum preview", () => {
  const h = editorHarness(); h.click("maximizeButton"); h.previewViewport.setHand(true);
  h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage }); assert.equal(h.previewViewport.isHandMode, false); assert.equal(h.controller.isMaximized(), true);
  h.state.overlayDrag = {}; h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage });
  assert.equal(h.calls.finishOverlay, 1); assert.equal(h.controller.isMaximized(), true);
  h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage }); assert.equal(h.controller.isMaximized(), false);
  h.unchanged(); h.cleanup();
});

test("maximum-preview Escape does not steal native typing or an already handled key", () => {
  const h = editorHarness(); h.click("maximizeButton");
  h.doc.dispatch("keydown", { key: "Escape", target: new Surface(h.doc, "input") }); assert.equal(h.controller.isMaximized(), true);
  h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage, defaultPrevented: true }); assert.equal(h.controller.isMaximized(), true);
  h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage }); assert.equal(h.controller.isMaximized(), false);
  h.unchanged(); h.cleanup();
});
