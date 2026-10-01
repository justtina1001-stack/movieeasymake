"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const preview = require("../static/editor_preview.js"), core = require("../static/editor.js");
const browserSource = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const html = fs.readFileSync(require.resolve("../static/editor.html"), "utf8");
const near = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

function classes() {
  const values = new Set();
  return { add: (...names) => names.forEach(name => values.add(name)), remove: (...names) => names.forEach(name => values.delete(name)),
    contains: name => values.has(name), toggle(name, force) {
      const on = force === undefined ? !values.has(name) : force;
      if (on) values.add(name); else values.delete(name); return on;
    } };
}
class Surface {
  constructor(doc, tag = "div") {
    this.ownerDocument = doc; this.tagName = tag.toUpperCase(); this.style = {}; this.classList = classes();
    this.listeners = new Map(); this.attributes = new Map(); this.children = []; this.parentNode = null;
    this.dataset = {}; this.value = "fit"; this.disabled = false; this.hidden = false; this.textContent = "";
    this.clientWidth = 800; this.clientHeight = 450; this.capture = null; this.captureFailure = false;
    this._src = ""; this.sourceWrites = 0; this.rect = { left: 100, top: 80, width: 800, height: 450 };
  }
  set src(value) { this.sourceWrites++; this._src = value; }
  get src() { return this._src; }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, callback, options) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push({ callback, options });
  }
  removeEventListener(name, callback) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item.callback !== callback));
  }
  dispatch(name, values = {}) {
    const event = { type: name, target: this, currentTarget: this, pointerId: 7, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true,
      clientX: 500, clientY: 305, deltaX: 0, deltaY: 0, deltaMode: 0, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      key: "", code: "", repeat: false, defaultPrevented: false, propagationStopped: false, immediateStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; },
      stopImmediatePropagation() { this.propagationStopped = true; this.immediateStopped = true; }, ...values };
    for (const { callback } of [...(this.listeners.get(name) || [])]) {
      if (event.immediateStopped) break;
      callback(event);
    }
    if (!event.immediateStopped) this[`on${name}`]?.(event);
    return event;
  }
  focus() { this.ownerDocument.activeElement = this; }
  closest(selector) {
    const selectors = selector.split(",").map(value => value.trim());
    const matches = selectors.some(value => value === this.tagName.toLowerCase() ||
      value.startsWith("#") && this.getAttribute("id") === value.slice(1) ||
      value.startsWith(".") && this.classList.contains(value.slice(1)) ||
      value === "[data-overlay-id]" && this.dataset.overlayId ||
      value === "[contenteditable=true]" && this.getAttribute("contenteditable") === "true");
    return matches ? this : this.parentNode?.closest(selector) || null;
  }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  setPointerCapture(id) { if (this.captureFailure) throw new Error("capture unavailable"); this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture(id) { if (this.capture === id) this.capture = null; this.dispatch("lostpointercapture", { pointerId: id }); }
  getBoundingClientRect() {
    if (this.dynamicRect) return this.dynamicRect();
    return { ...this.rect, right: this.rect.left + this.rect.width, bottom: this.rect.top + this.rect.height };
  }
}
function project() {
  return { id: "preview-one", name: "預覽測試", updated_at: "r1", width: 1280, height: 720, fps: 24,
    clips: [{ id: "main", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [],
    overlays: [{ id: "caption", kind: "text", start: 0, end: 8, x: .5, y: .5, width: .7, rotation: 0, opacity: 1,
      text: "保持文字與影片快取", font_size: .06, color: "#ffffff", background: "transparent", bold: true, align: "center" }] };
}
function paintedCanvasRect(elements) {
  const { stage, canvas } = elements, width = parseFloat(canvas.style.width) || 0, height = parseFloat(canvas.style.height) || 0;
  const translation = canvas.style.transform?.match(/translate\((-?[.\d]+)px,\s*(-?[.\d]+)px\)/);
  const left = stage.rect.left + (parseFloat(canvas.style.left) || 0) - width / 2 + Number(translation?.[1] || 0);
  const top = stage.rect.top + (parseFloat(canvas.style.top) || 0) - height / 2 + Number(translation?.[2] || 0);
  return { left, top, width, height, right: left + width, bottom: top + height };
}
function harness(options = {}) {
  const doc = new Surface(null, "document"); doc.ownerDocument = doc;
  const win = new Surface(doc, "window"); doc.defaultView = win; doc.body = new Surface(doc, "body"); doc.activeElement = doc.body;
  const elements = Object.fromEntries(["stage", "canvas", "zoomSelect", "zoomIn", "zoomOut", "fitButton", "handButton"].map(name => [name, new Surface(doc,
    name === "zoomSelect" ? "select" : name.endsWith("Button") || name.startsWith("zoom") && name !== "zoomSelect" ? "button" : "div")]));
  for (const [name, id] of Object.entries({ stage: "videoStage", canvas: "videoCanvas", zoomSelect: "previewZoom", zoomIn: "previewZoomIn",
    zoomOut: "previewZoomOut", fitButton: "previewFit", handButton: "previewHand" })) elements[name].setAttribute("id", id);
  doc.body.append(elements.stage); elements.stage.append(elements.canvas);
  const video = new Surface(doc, "video"), caption = new Surface(doc, "img");
  video.src = "/api/editor/media/video/file"; caption.src = "blob:text-preview-cached";
  elements.canvas.append(video, caption);
  const session = new core.ProjectSession(options.project || project());
  const cache = new Map([[core.overlayRasterKey(session.project.overlays[0], 1280, 720), { url: caption.src, bytes: 50000 }]]);
  const before = { signature: core.signature(session.project), object: session.project, cache: [...cache], videoWrites: video.sourceWrites,
    imageWrites: caption.sourceWrites, undo: session.undoStack.length, dirty: session.dirty };
  let blocked = Boolean(options.blocked);
  const controller = preview.mountPreviewViewport(elements, { window: win, document: doc,
    getStyle: () => ({ paddingLeft: "24px", paddingRight: "24px", paddingTop: "12px", paddingBottom: "12px" }),
    isBlocked: () => blocked });
  controller.update({ width: session.project.width, height: session.project.height, key: session.project.id });
  elements.canvas.dynamicRect = () => paintedCanvasRect(elements);
  const click = name => elements[name].disabled ? null : elements[name].dispatch("click");
  const select = value => { elements.zoomSelect.value = String(value); return elements.zoomSelect.dispatch("change"); };
  const wheel = values => elements.stage.dispatch("wheel", values);
  const pointer = (name, values) => elements.stage.dispatch(name, values);
  const unchanged = () => {
    assert.equal(core.signature(session.project), before.signature); assert.equal(session.project, before.object);
    assert.equal(session.undoStack.length, before.undo); assert.equal(session.redoStack.length, 0); assert.equal(session.dirty, before.dirty);
    assert.deepEqual([...cache], before.cache); assert.equal(video.sourceWrites, before.videoWrites); assert.equal(caption.sourceWrites, before.imageWrites);
    assert.equal(video.src, "/api/editor/media/video/file"); assert.equal(caption.src, "blob:text-preview-cached");
  };
  return { controller, elements, doc, win, session, cache, video, caption, before, click, select, wheel, pointer, unchanged,
    blocked(value) { blocked = value; }, snapshot: () => controller.snapshot() };
}
function sourcePoint(h, clientX, clientY) {
  const rect = h.elements.canvas.getBoundingClientRect();
  return { x: (clientX - rect.left) / rect.width, y: (clientY - rect.top) / rect.height };
}
function shipped(name) {
  const start = browserSource.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing shipped ${name}`);
  for (let end = browserSource.indexOf("\n", start); end >= 0; end = browserSource.indexOf("\n", end + 1)) {
    const candidate = browserSource.slice(start, end);
    try { new vm.Script(candidate); return candidate; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`cannot extract shipped ${name}`);
}

function editorHarness() {
  const h = harness(), nodes = { videoStage: h.elements.stage, videoCanvas: h.elements.canvas,
    previewZoom: h.elements.zoomSelect, previewZoomIn: h.elements.zoomIn, previewZoomOut: h.elements.zoomOut,
    previewFit: h.elements.fitButton, previewHand: h.elements.handButton };
  nodes.exportDialog = new Surface(h.doc, "dialog"); nodes.exportDialog.open = false;
  for (const id of ["overlayCanvas", "timelineCanvas", "timelineScroll", "trimFeedback"]) nodes[id] = new Surface(h.doc);
  nodes.timelineScroll.scrollLeft = 0; nodes.trimFeedback.offsetWidth = 150; nodes.trimFeedback.offsetHeight = 20;
  h.elements.canvas.append(nodes.overlayCanvas);
  const overlay = new Surface(h.doc); overlay.dataset.overlayId = "caption"; overlay.classList.add("preview-overlay"); nodes.overlayCanvas.append(overlay);
  const state = { session: h.session, media: new Map([["video", { kind: "video", duration: 10 }]]), playhead: 2,
    selectedKind: "overlay", selected: "caption", zoom: 60, backendReady: true, busy: false, trimDrag: null,
    overlayDrag: null, speedDrag: null, trackDrag: null, positionKeyframesReady: true, queuedSeek: null,
    rasterCache: h.cache, rasterNodes: new Map([["caption", { img: h.caption, key: [...h.cache.keys()][0] }]]) };
  const calls = { pause: 0, preview: 0, render: 0, seek: 0, play: 0, edits: 0, errors: [] }, frames = new Map(); let serial = 0;
  h.win.H3EditorPreview = preview;
  h.win.getComputedStyle = () => ({ paddingLeft: "24px", paddingRight: "24px", paddingTop: "12px", paddingBottom: "12px" });
  h.doc.querySelectorAll = () => [];
  const sandbox = { ...core, state, root: h.win, document: h.doc, $: id => nodes[id],
    locked: () => !state.backendReady || state.busy || Boolean(state.overlayDrag || state.trimDrag || state.speedDrag || state.trackDrag),
    overlaySupported: () => true, action: fn => fn, pause: () => { calls.pause++; },
    renderOverlayGesture: () => { calls.preview++; }, renderDisabled() {},
    render: () => { calls.render++; sandbox.fitCanvas(); },
    seek: () => { calls.seek++; }, notify() {}, errorNotice: error => calls.errors.push(error),
    renderTimeline() {}, renderInspector() {}, renderOverlayPreview: () => { calls.preview++; },
    togglePlay: () => { calls.play++; }, jumpPositionKeyframe: () => false,
    requestAnimationFrame: callback => { frames.set(++serial, callback); return serial; }, cancelAnimationFrame: id => frames.delete(id),
    finishTrim() {}, finishTrackGesture() {}, keyboardTrim() {}, saveProject() { calls.edits++; }, overlayEdit() {},
    travel() { calls.edits++; }, doSplit() { calls.edits++; }, deleteSelected() { calls.edits++; } };
  vm.createContext(sandbox);
  const projectBinding = browserSource.match(/^  const project = .+;$/m); assert.ok(projectBinding); vm.runInContext(projectBinding[0], sandbox);
  const mountStart = browserSource.indexOf("  const previewViewport = root.H3EditorPreview.mountPreviewViewport(");
  const mountEnd = browserSource.indexOf("\n  const trackDragUI", mountStart); assert.ok(mountStart >= 0 && mountEnd > mountStart);
  h.controller.destroy();
  vm.runInContext(browserSource.slice(mountStart, mountEnd) + "\nthis.previewViewport = previewViewport;", sandbox);
  h.controller = sandbox.previewViewport; h.snapshot = () => h.controller.snapshot();
  for (const name of ["fitCanvas", "resizePreview", "beginOverlayGesture", "flushOverlayGesture", "finishOverlayGesture"])
    vm.runInContext(shipped(name), sandbox);
  sandbox.fitCanvas();
  const overlayStart = browserSource.indexOf('  for (const canvas of [$("timelineCanvas"), $("overlayCanvas")]) {');
  const overlayEnd = browserSource.indexOf('\n  $("timelineCanvas").addEventListener("pointermove", event => { if (state.trimDrag', overlayStart);
  assert.ok(overlayStart >= 0 && overlayEnd > overlayStart); vm.runInContext(browserSource.slice(overlayStart, overlayEnd), sandbox);
  const keysStart = browserSource.indexOf('  document.addEventListener("keydown", action(async event => {');
  const keysEnd = browserSource.indexOf('\n  root.addEventListener("blur"', keysStart);
  assert.ok(keysStart >= 0 && keysEnd > keysStart); vm.runInContext(browserSource.slice(keysStart, keysEnd), sandbox);
  const resizeBinding = browserSource.match(/^  root\.addEventListener\("resize", .+;$/m); assert.ok(resizeBinding); vm.runInContext(resizeBinding[0], sandbox);
  function overlayPointer(name, values = {}) {
    // The shipped stage capture handler has first refusal before the shipped
    // overlay-canvas handler receives a pointer, just as the browser routes it.
    const event = h.elements.stage.dispatch(name, { target: overlay, ...values });
    if (!event.propagationStopped) nodes.overlayCanvas.dispatch(name, { target: overlay, ...values });
    return event;
  }
  return { ...h, nodes, state, calls, sandbox, overlay, overlayPointer, frames };
}

test("the actual HTML exposes fit, percentage zoom, plus/minus and hand controls with a loaded preview module", () => {
  for (const id of ["previewZoom", "previewZoomIn", "previewZoomOut", "previewFit", "previewHand"]) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /editor_preview\.js\?/);
  const levels = [...html.matchAll(/<option value="([.\d]+)">([^<]*%)<\/option>/g)].map(match => Number(match[1]));
  for (const level of [.25, .5, 1, 2, 4]) assert.ok(levels.includes(level), `missing zoom ${level}`);
});

test("fit uses the available padded viewport and 100 percent means one output pixel per CSS pixel", () => {
  const h = harness();
  near(h.snapshot().scale, 752 / 1280); assert.equal(h.snapshot().mode, "fit");
  near(parseFloat(h.elements.canvas.style.width), 752); near(parseFloat(h.elements.canvas.style.height), 423);
  h.select(1); near(h.snapshot().scale, 1); assert.equal(h.snapshot().mode, "fixed");
  near(parseFloat(h.elements.canvas.style.width), 1280); near(parseFloat(h.elements.canvas.style.height), 720);
  near(h.elements.canvas.getBoundingClientRect().width, 1280);
  h.click("fitButton"); near(h.snapshot().scale, 752 / 1280); assert.equal(h.snapshot().mode, "fit");
  h.unchanged(); h.controller.destroy();
});

test("percentage controls and plus/minus reach both endpoints and remain bounded", () => {
  const h = harness();
  for (const scale of [.25, .5, .75, 1, 1.5, 2, 3, 4]) { h.select(scale); near(h.snapshot().scale, scale); }
  for (let index = 0; index < 15; index++) h.click("zoomIn"); near(h.snapshot().scale, 4);
  for (let index = 0; index < 20; index++) h.click("zoomOut"); near(h.snapshot().scale, .05);
  h.select(1); h.click("zoomIn"); assert.ok(h.snapshot().scale > 1);
  h.click("zoomOut"); near(h.snapshot().scale, 1);
  h.unchanged(); h.controller.destroy();
});

test("a normal wheel remains native while Ctrl and Meta wheel preserve the source pixel under the cursor", () => {
  const h = harness(); h.select(1);
  const ordinary = h.wheel({ clientX: 450, clientY: 290, deltaY: -50 });
  assert.equal(ordinary.defaultPrevented, false); near(h.snapshot().scale, 1);
  for (const modifiers of [{ ctrlKey: true }, { metaKey: true }]) {
    const clientX = 455, clientY = 285, before = sourcePoint(h, clientX, clientY);
    const event = h.wheel({ clientX, clientY, deltaY: -80, ...modifiers });
    assert.equal(event.defaultPrevented, true); assert.ok(h.snapshot().scale > 1);
    const after = sourcePoint(h, clientX, clientY); near(after.x, before.x); near(after.y, before.y);
  }
  h.unchanged(); h.controller.destroy();
});

test("hand dragging pans only the view and middle mouse temporarily pans without enabling hand mode", () => {
  const h = harness(); h.select(2); h.click("handButton"); assert.equal(h.controller.isHandMode, true);
  const begin = h.pointer("pointerdown", { clientX: 500, clientY: 305 });
  assert.equal(begin.defaultPrevented, true); assert.equal(h.controller.isPanning, true);
  h.pointer("pointermove", { clientX: 610, clientY: 365 });
  h.pointer("pointerup", { clientX: 620, clientY: 375 });
  near(h.snapshot().x, 120); near(h.snapshot().y, 70); assert.equal(h.controller.isPanning, false);
  h.click("handButton"); assert.equal(h.controller.isHandMode, false);
  h.pointer("pointerdown", { button: 1, buttons: 4, clientX: 500, clientY: 305 });
  h.pointer("pointerup", { button: 1, buttons: 0, clientX: 470, clientY: 295 });
  near(h.snapshot().x, 90); near(h.snapshot().y, 60); assert.equal(h.controller.isHandMode, false);
  h.unchanged(); h.controller.destroy();
});

for (const cause of ["pointercancel", "lostpointercapture", "blur", "Escape"]) test(`${cause} cancels an in-progress pan and restores the previous view`, () => {
  const h = harness(); h.select(2); h.controller.model.panBy(25, -20); h.controller.resize();
  const original = h.snapshot(); h.controller.setHand(true);
  h.pointer("pointerdown", { clientX: 500, clientY: 305 }); h.pointer("pointermove", { clientX: 650, clientY: 385 });
  assert.notEqual(h.snapshot().x, original.x);
  if (cause === "blur") h.win.dispatch("blur");
  else if (cause === "Escape") h.doc.dispatch("keydown", { key: "Escape", target: h.elements.stage });
  else h.pointer(cause);
  assert.equal(h.controller.isPanning, false); assert.equal(h.elements.stage.capture, null);
  near(h.snapshot().x, original.x); near(h.snapshot().y, original.y);
  if (cause === "Escape") assert.equal(h.controller.isHandMode, false);
  h.unchanged(); h.controller.destroy();
});

test("pointer identity, normal overlay routing and busy content gestures cannot accidentally pan", () => {
  const h = harness(); h.select(2);
  const overlay = new Surface(h.doc); overlay.dataset.overlayId = "caption"; h.elements.canvas.append(overlay);
  const ordinary = h.pointer("pointerdown", { target: overlay });
  assert.equal(ordinary.defaultPrevented, false); assert.equal(h.controller.isPanning, false);
  h.controller.setHand(true); h.blocked(true); h.pointer("pointerdown"); assert.equal(h.controller.isPanning, false);
  h.blocked(false); h.pointer("pointerdown"); h.pointer("pointermove", { pointerId: 99, clientX: 600 });
  near(h.snapshot().x, 0); h.pointer("pointerup", { pointerId: 99 }); assert.equal(h.controller.isPanning, true);
  h.pointer("pointercancel"); h.unchanged(); h.controller.destroy();
});

test("panning clamps large content to cover the viewport and centers content on an axis that fits", () => {
  const h = harness(); h.select(1); h.controller.setHand(true);
  h.pointer("pointerdown"); h.pointer("pointerup", { clientX: 10000, clientY: 10000 });
  const maximum = h.snapshot();
  near(maximum.x, (1280 - maximum.viewportWidth) / 2); near(maximum.y, (720 - maximum.viewportHeight) / 2);
  h.pointer("pointerdown"); h.pointer("pointerup", { clientX: -10000, clientY: -10000 });
  near(h.snapshot().x, -maximum.x); near(h.snapshot().y, -maximum.y);
  h.select(.25); near(h.snapshot().x, 0); near(h.snapshot().y, 0); assert.equal(h.snapshot().canPan, false);
  h.unchanged(); h.controller.destroy();
});

test("resize follows fit, preserves fixed zoom, clamps pan and same-project renders preserve the view", () => {
  const h = harness(); h.elements.stage.clientWidth = 1000; h.elements.stage.clientHeight = 650; h.controller.resize();
  near(h.snapshot().scale, 952 / 1280);
  h.select(2); h.controller.model.panBy(80, -50); h.controller.resize();
  const before = h.snapshot(); h.controller.update({ width: 1280, height: 720, key: "preview-one" });
  near(h.snapshot().scale, before.scale); near(h.snapshot().x, before.x); near(h.snapshot().y, before.y);
  h.elements.stage.clientWidth = 650; h.elements.stage.clientHeight = 350; h.controller.resize();
  near(h.snapshot().scale, 2); assert.ok(Math.abs(h.snapshot().x) <= (2560 - h.snapshot().viewportWidth) / 2);
  h.controller.fit(); near(h.snapshot().scale, Math.min(602 / 1280, 326 / 720));
  h.unchanged(); h.controller.destroy();
});

test("opening another project or changing output dimensions resets fit and removes stale pan", () => {
  const h = harness(); h.select(3); h.controller.model.panBy(200, 100); h.controller.resize();
  h.controller.update({ width: 1280, height: 720, key: "preview-two" });
  assert.equal(h.snapshot().mode, "fit"); near(h.snapshot().x, 0); near(h.snapshot().y, 0);
  h.select(2); h.controller.model.panBy(100, 50); h.controller.resize();
  h.controller.update({ width: 720, height: 1280, key: "preview-two" });
  assert.equal(h.snapshot().mode, "fit"); near(h.snapshot().scale, 426 / 1280); near(h.snapshot().x, 0); near(h.snapshot().y, 0);
  h.unchanged(); h.controller.destroy();
});

test("keyboard view controls leave normal Space untouched and editing fields retain their own keys", () => {
  const h = harness(); h.select(2); h.controller.setHand(true);
  const normalSpace = h.elements.stage.dispatch("keydown", { code: "Space", key: " " });
  assert.equal(normalSpace.defaultPrevented, false); assert.equal(h.controller.isPanning, false);
  const field = new Surface(h.doc, "input");
  const writing = h.doc.dispatch("keydown", { key: "ArrowRight", target: field });
  assert.equal(writing.defaultPrevented, false); near(h.snapshot().x, 0);
  const arrow = h.elements.stage.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(arrow.defaultPrevented, true); assert.notEqual(h.snapshot().x, 0);
  h.elements.stage.dispatch("keydown", { key: "Home" }); assert.equal(h.snapshot().mode, "fit");
  h.unchanged(); h.controller.destroy();
});

test("fit geometry handles landscape, portrait and square projects without cropping", () => {
  for (const [width, height, viewportWidth, viewportHeight, expected] of [
    [1280, 720, 640, 480, .5], [720, 1280, 640, 480, .375], [720, 720, 500, 300, 300 / 720],
    [1920, 1080, 240, 150, .125], [360, 360, 720, 720, 2],
  ]) {
    const scale = preview.fitScale(width, height, viewportWidth, viewportHeight);
    near(scale, expected); assert.ok(width * scale <= viewportWidth + 1e-8); assert.ok(height * scale <= viewportHeight + 1e-8);
  }
  assert.equal(preview.fitScale(1280, 720, 0, 0), 0);
  for (const values of [[0, 720, 500, 300], [1280, NaN, 500, 300], [1280, 720, -1, 300]]) assert.equal(preview.fitScale(...values), 0);
});

test("model snapshots are detached, bad input is atomic and zoom geometry preserves a source landmark", () => {
  const model = new preview.PreviewViewportModel({ width: 1280, height: 720, viewportWidth: 500, viewportHeight: 300, key: "one" });
  model.setScale(1); model.panBy(12, -10); const original = model.snapshot();
  const changed = model.snapshot(); changed.x = 999; changed.width = 1; assert.deepEqual(model.snapshot(), original);
  for (const action of [() => model.setContent(NaN, 720), () => model.resize(400, -1), () => model.setScale(0),
    () => model.zoomBy(Infinity), () => model.panBy(0, NaN)]) {
    assert.throws(action); assert.deepEqual(model.snapshot(), original);
  }
  const anchor = { x: 145, y: 127 }, zoom = preview.zoomAt(original, 2, anchor, 1280, 720, 500, 300);
  const landmark = (view, point) => ({ x: .5 + (point.x - 250 - view.x) / (1280 * view.scale),
    y: .5 + (point.y - 150 - view.y) / (720 * view.scale) });
  const before = landmark(original, anchor), after = landmark(zoom, anchor);
  near(after.x, before.x); near(after.y, before.y);
  const pan = preview.clampPan({ x: 900, y: -900 }, 1280, 720, .5, 500, 400);
  near(pan.x, 70); near(pan.y, 0);
});

test("wheel variants remain bounded, outside-wheel stays native and the listener is non-passive", () => {
  const h = harness(); h.select(1);
  const wheel = h.elements.stage.listeners.get("wheel"); assert.equal(wheel[0].options.passive, false);
  const outside = h.win.dispatch("wheel", { ctrlKey: true, deltaY: -80 }); assert.equal(outside.defaultPrevented, false); near(h.snapshot().scale, 1);
  h.wheel({ metaKey: true, deltaY: -2, deltaMode: 1 }); assert.ok(h.snapshot().scale > 1);
  h.wheel({ ctrlKey: true, deltaY: -100000, deltaMode: 2 }); near(h.snapshot().scale, 4);
  h.wheel({ ctrlKey: true, deltaY: 100000, deltaMode: 2 });
  assert.ok(h.snapshot().scale >= .05 && h.snapshot().scale <= 4);
  for (const value of [h.snapshot().x, h.snapshot().y, h.snapshot().scale]) assert.ok(Number.isFinite(value));
  h.unchanged(); h.controller.destroy();
});

test("capture failure and released buttons leave no stuck panning state", () => {
  const h = harness(); h.select(2); h.controller.setHand(true); h.elements.stage.captureFailure = true;
  h.pointer("pointerdown"); assert.equal(h.controller.isPanning, false); assert.equal(h.elements.stage.capture, null);
  h.elements.stage.captureFailure = false; h.pointer("pointerdown"); h.pointer("pointermove", { clientX: 600 });
  assert.equal(h.controller.isPanning, true);
  h.pointer("pointermove", { clientX: 610, buttons: 0 }); assert.equal(h.controller.isPanning, false); near(h.snapshot().x, 0);
  h.pointer("pointerdown"); h.doc.hidden = true; h.doc.dispatch("visibilitychange"); assert.equal(h.controller.isPanning, false);
  h.unchanged(); h.controller.destroy();
});

test("destroy cancels capture and removes all view listeners", () => {
  const h = harness(); h.select(2); h.controller.setHand(true); h.pointer("pointerdown"); h.pointer("pointermove", { clientX: 600 });
  h.controller.destroy(); assert.equal(h.controller.isPanning, false); assert.equal(h.elements.stage.capture, null); near(h.snapshot().x, 0);
  const original = h.snapshot(); h.wheel({ ctrlKey: true, deltaY: -80 }); h.click("zoomIn"); h.pointer("pointerdown");
  assert.deepEqual(h.snapshot(), original); h.unchanged();
});

test("a tiny fit can remain below continuous zoom minimum and plus actually enlarges it", () => {
  const h = harness(); h.elements.stage.clientWidth = 100; h.elements.stage.clientHeight = 70; h.controller.resize();
  const scale = h.snapshot().scale; assert.ok(scale < .05); assert.equal(h.snapshot().mode, "fit");
  assert.equal(h.elements.zoomOut.disabled, true); h.click("zoomOut"); near(h.snapshot().scale, scale);
  h.click("zoomIn"); assert.ok(h.snapshot().scale > scale); near(h.snapshot().scale, .25);
  h.unchanged(); h.controller.destroy();
});

test("the shipped fitCanvas binding changes only viewport geometry and keeps media and text caches", () => {
  const h = editorHarness(); h.select(2); h.controller.model.panBy(80, -20); h.controller.render(); const before = h.snapshot();
  for (let index = 0; index < 5; index++) h.sandbox.fitCanvas();
  near(h.snapshot().scale, before.scale); near(h.snapshot().x, before.x); near(h.snapshot().y, before.y);
  assert.deepEqual(h.calls, { pause: 0, preview: 0, render: 0, seek: 0, play: 0, edits: 0, errors: [] });
  h.unchanged(); h.controller.destroy();
});

test("the actual mount binds content gesture locks while a plain viewport change never writes history", () => {
  const h = editorHarness(); h.select(1); const before = h.snapshot();
  for (const field of ["trimDrag", "overlayDrag", "speedDrag", "trackDrag"]) {
    h.state[field] = {}; h.wheel({ ctrlKey: true, deltaY: -80 }); h.click("zoomIn"); near(h.snapshot().scale, before.scale);
    h.state[field] = null;
  }
  h.wheel({ ctrlKey: true, deltaY: -80 }); assert.ok(h.snapshot().scale > before.scale);
  h.unchanged(); h.controller.destroy();
});

test("stage hand capture wins over the real overlay drag binding while normal clicks still edit the layer", () => {
  const h = editorHarness(); h.select(2); h.controller.setHand(true);
  const event = h.overlayPointer("pointerdown", { clientX: 500, clientY: 305 });
  assert.equal(event.immediateStopped, true); assert.equal(h.state.overlayDrag, null); assert.equal(h.calls.pause, 0);
  h.overlayPointer("pointerup", { clientX: 620, clientY: 360 }); near(h.snapshot().x, 120);
  h.unchanged(); h.controller.setHand(false);
  h.overlayPointer("pointerdown", { clientX: 500, clientY: 305 }); assert.equal(h.state.overlayDrag?.mode, "position");
  h.overlayPointer("pointercancel"); assert.equal(h.state.overlayDrag, null);
  h.unchanged(); h.controller.destroy();
});

for (const scale of [1, 2]) test(`the shipped overlay drag at ${scale * 100}% converts mouse movement through the actual enlarged canvas rectangle`, () => {
  const h = editorHarness(); h.select(scale); h.controller.model.panBy(45, -20); h.controller.render();
  const before = core.signature(h.session.project), original = h.session.project.overlays[0];
  h.overlayPointer("pointerdown", { clientX: 450, clientY: 290 });
  assert.equal(h.state.overlayDrag.rect.width, 1280 * scale); assert.equal(h.state.overlayDrag.rect.height, 720 * scale);
  h.overlayPointer("pointerup", { clientX: 578, clientY: 362 });
  assert.equal(h.state.overlayDrag, null); assert.equal(h.session.undoStack.length, 1); assert.equal(h.session.dirty, true);
  near(h.session.project.overlays[0].x, original.x + .1 / scale); near(h.session.project.overlays[0].y, original.y + .1 / scale);
  assert.equal(h.calls.errors.length, 0); assert.equal(h.calls.seek, 0);
  h.session.travel(true); assert.equal(core.signature(h.session.project), before); h.controller.destroy();
});

test("the shipped resize binding cancels a position drag measured against the previous canvas size", () => {
  const h = editorHarness(); h.select(2); h.overlayPointer("pointerdown", { clientX: 500, clientY: 305 });
  h.state.overlayDrag.lastX = 600; h.state.overlayDrag.lastY = 350; h.sandbox.flushOverlayGesture();
  assert.notEqual(h.state.overlayDrag.transaction.preview.overlays[0].x, .5);
  h.elements.stage.clientWidth = 600; h.win.dispatch("resize");
  assert.equal(h.state.overlayDrag, null); near(h.snapshot().scale, 2); h.unchanged(); h.controller.destroy();
});

test("normal Space reaches the shipped playback shortcut in hand mode and repeated or typing Space does not", async () => {
  const h = editorHarness(); h.select(2); h.controller.setHand(true);
  const viewEvent = h.elements.stage.dispatch("keydown", { key: " ", code: "Space" }); assert.equal(viewEvent.defaultPrevented, false);
  const playback = h.doc.dispatch("keydown", { key: " ", code: "Space", target: h.elements.stage });
  assert.equal(playback.defaultPrevented, true); assert.equal(h.calls.play, 1);
  h.doc.dispatch("keydown", { key: " ", code: "Space", target: h.elements.stage, repeat: true }); assert.equal(h.calls.play, 1);
  h.doc.dispatch("keydown", { key: " ", code: "Space", target: new Surface(h.doc, "input") }); assert.equal(h.calls.play, 1);
  h.unchanged(); h.controller.destroy();
});

test("opening the export dialog blocks global playback and editing shortcuts without cancelling native button keys", () => {
  const h = editorHarness(), target = new Surface(h.doc, "button"); h.nodes.exportDialog.append(target);
  const keys = [{ key: " ", code: "Space" }, { key: "s" }, { key: "Backspace" },
    { key: "z", metaKey: true }, { key: "y", ctrlKey: true }, { key: "s", metaKey: true }];
  h.nodes.exportDialog.open = true;
  for (const key of keys) {
    const event = h.doc.dispatch("keydown", { target, ...key });
    assert.equal(event.defaultPrevented, false);
  }
  assert.equal(h.calls.play, 0); assert.equal(h.calls.edits, 0);
  h.nodes.exportDialog.open = false;
  for (const key of keys) h.doc.dispatch("keydown", { target: h.elements.stage, ...key });
  assert.equal(h.calls.play, 1); assert.equal(h.calls.edits, 5);
  h.unchanged(); h.controller.destroy();
});
