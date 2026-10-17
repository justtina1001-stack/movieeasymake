"use strict";
/* Exercise the shipped event/controller code with real project transactions. */
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), color = require("../static/editor_color.js"), ui = require("../static/editor_color_ui.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
function statement(fragment) {
  const start = source.indexOf(fragment); assert.notEqual(start, -1, `Missing shipped ${fragment}`);
  for (let end = source.indexOf("\n", start); end !== -1; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Cannot extract shipped ${fragment}`);
}
const declaration = name => statement(`  function ${name}(`);
const colorBindings = source.slice(source.indexOf('  $("colorTrack").onclick ='), source.indexOf('  $("timelineCanvas").addEventListener("pointerdown", action(beginTrim))'));
const controller = [statement("  const exporting ="), statement("  const locked ="), statement("  const selectedClip ="),
  ...["selectColor", "beginColorGesture", "renderColorGesture", "flushColorGesture", "finishColorGesture"].map(declaration),
  colorBindings, statement('  $("timelineCanvas").onclick = event =>'),
  statement('  document.addEventListener("keydown", action(async event =>'),
  statement("  const subtitleControlKeys ="),
  statement('  $("colorPanel").addEventListener("keydown", subtitleControlKeys)'),
  statement('  $("openColorWorkspace").addEventListener("keydown", subtitleControlKeys)'),
].join("\n");
const attrKey = name => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
class Element {
  constructor(tagName = "div", parent = null) {
    this.tagName = tagName; this.parent = parent; this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.listeners = new Map(); this.capture = new Set(); this.open = false;
    const classes = new Set(); this.classList = { toggle: (name, force) => { force ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) };
    if (parent) parent.children.push(this);
  }
  matches(selector) {
    const attribute = /^\[data-([a-z-]+)(?:="([^"]*)")?\]$/.exec(selector);
    return attribute ? Object.hasOwn(this.dataset, attrKey(attribute[1])) && (attribute[2] === undefined || this.dataset[attrKey(attribute[1])] === attribute[2]) : selector === this.tagName;
  }
  closest(selector) { return selector.split(",").some(part => this.matches(part.trim())) ? this : this.parent?.closest(selector) || null; }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(selector.split(",").some(part => child.matches(part.trim())) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(callback); }
  dispatch(event) { for (const callback of this.listeners.get(event.type) || []) callback(event); this[`on${event.type}`]?.(event); }
  setPointerCapture(pointerId) { if (this.captureError) throw this.captureError; this.capture.add(pointerId); }
  hasPointerCapture(pointerId) { return this.capture.has(pointerId); }
  releasePointerCapture(pointerId) { this.capture.delete(pointerId); this.dispatch(event("lostpointercapture", this, { pointerId })); }
  focus() { this.focused = true; }
}
function event(type, target, options = {}) {
  return { type, target, pointerId: 1, button: 0, isPrimary: true, clientX: 100, defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.stopped = true; }, ...options };
}
const grade = extra => ({ id: "grade", name: "暖色", start: 1, end: 4, temperature: 30, ...extra });
function harness(items = [grade()]) {
  const elements = new Map(), raf = new Map(), cancelled = [], errors = [], notices = [], seeks = [], queuedSeeks = [], previews = [];
  const $ = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const canvas = $("timelineCanvas"), track = $("colorTrack"); track.parent = canvas; canvas.children.push(track);
  const nodes = new Map();
  for (const item of items) {
    const node = new Element("button", track); node.dataset.colorId = item.id; nodes.set(item.id, node);
    for (const edge of ["left", "right"]) { const handle = new Element("span", node); handle.dataset.colorEdge = edge; }
  }
  const scroll = $("timelineScroll"); scroll.scrollLeft = 0; scroll.getBoundingClientRect = () => ({ left: 0, right: 1000 });
  canvas.getBoundingClientRect = () => ({ left: 0, right: 1000 });
  const project = { id: "demo", name: "調色測試", updated_at: "r1", width: 1280, height: 720, fps: 24,
    clips: [{ id: "main", media_id: "a".repeat(32), in: 0, out: 10, speed: 1, volume: 1 }], audio_clips: [], overlays: [], color_adjustments: items };
  let changes = 0, renders = 0, nextRaf = 0, plays = 0;
  const session = new core.ProjectSession(project, () => { changes++; });
  const state = { session, backendReady: true, busy: false, colorReady: true, selected: "main", selectedKind: "video", playhead: 0, zoom: 60, colorDrag: null, colorSuppressUntil: 0 };
  const value = () => state.colorDrag?.transaction.preview || session.project;
  const doc = new Element("document"), noop = () => {};
  const sandbox = { state, $, core, colorMath: color, document: doc, project: value, duration: core.duration, totalDuration: core.totalDuration,
    root: { H3EditorColorUI: ui }, performance: { now: () => 1000 },
    action: callback => async (...args) => { try { return await callback(...args); } catch (error) { errors.push(error); } },
    pause: () => { state.playing = false; }, render: () => { renders++; }, renderInspector: noop, renderDisabled: noop, renderColorControls: noop,
    renderTimeline: noop, queueSeek: time => queuedSeeks.push(time), seek: (time, play) => seeks.push([time, play]),
    colorPreview: { update: (snapshot, time) => previews.push([core.clone(snapshot), time]) },
    errorNotice: error => errors.push(error), notify: text => notices.push(text),
    requestAnimationFrame: callback => { const id = ++nextRaf; raf.set(id, callback); return id; },
    cancelAnimationFrame: id => { cancelled.push(id); raf.delete(id); },
    togglePlay: async () => { plays++; }, deleteSelected: () => { session.change(next => { next.color_adjustments = next.color_adjustments.filter(item => item.id !== state.selected); }); },
  };
  vm.createContext(sandbox); vm.runInContext(controller, sandbox);
  const begin = (mode = "move", options = {}) => { const node = nodes.get(options.id || "grade"), target = mode === "move" ? node : node.querySelector(`[data-color-edge="${mode}"]`); sandbox.beginColorGesture(event("pointerdown", target, options)); return target; };
  const pointer = (type, options = {}) => { const emitted = event(type, canvas, options); canvas.dispatch(emitted); return emitted; };
  const runRaf = () => { const pending = [...raf]; raf.clear(); for (const [, callback] of pending) callback(); };
  const key = async (target, options) => { const emitted = event("keydown", target, options); for (let node = target; node && node !== doc; node = node.parent) { node.dispatch(emitted); if (emitted.stopped) return emitted; } for (const callback of doc.listeners.get("keydown") || []) await callback(emitted); return emitted; };
  return { $, canvas, nodes, state, session, sandbox, raf, cancelled, errors, notices, seeks, queuedSeeks, previews, begin, pointer, runRaf, key, get changes() { return changes; }, get renders() { return renders; }, get plays() { return plays; } };
}

test("shipped drag previews leave the session unchanged and release creates one undo entry", () => {
  const h = harness(), before = core.signature(h.session.project); h.begin();
  h.pointer("pointermove", { clientX: 160 }); assert.equal(h.raf.size, 1); h.runRaf();
  assert.equal(core.signature(h.session.project), before); assert.equal(h.changes, 0);
  assert.equal(color.adjustments(h.state.colorDrag.transaction.preview)[0].start, 2);
  assert.equal(h.nodes.get("grade").style.left, "120px"); assert.ok(h.previews.length);
  h.pointer("pointermove", { clientX: 220 }); h.pointer("pointerup", { clientX: 220 });
  assert.equal(h.session.project.color_adjustments[0].start, 3); assert.equal(h.session.project.color_adjustments[0].end, 6);
  assert.equal(h.changes, 1); assert.equal(h.session.undoStack.length, 1); assert.equal(h.state.colorDrag, null);
  assert.equal(h.canvas.hasPointerCapture(1), false); assert.equal(h.raf.size, 0);
  h.pointer("pointerup", { clientX: 220 }); assert.equal(h.changes, 1);
  h.session.travel(true); assert.equal(core.signature(h.session.project), before);
});

test("release cancels the pending pointermove RAF and any new edge autoscroll RAF", () => {
  const h = harness(); h.begin(); h.pointer("pointermove", { clientX: 990 });
  const pending = [...h.raf.keys()][0]; h.pointer("pointerup", { clientX: 990 });
  assert.ok(h.cancelled.includes(pending)); assert.equal(h.cancelled.length, 2);
  assert.equal(h.raf.size, 0); assert.equal(h.changes, 1); assert.equal(h.state.colorDrag, null);
  assert.equal(h.$("timelineScroll").scrollLeft, 12);
});

test("shipped Escape and pointer cancellation discard previews and release capture without history", async () => {
  for (const finish of ["Escape", "pointercancel", "lostpointercapture"]) {
    const h = harness(), before = core.signature(h.session.project); const node = h.begin();
    h.pointer("pointermove", { clientX: 160 }); h.runRaf(); h.pointer("pointermove", { clientX: 180 });
    if (finish === "Escape") { const emitted = await h.key(node, { key: "Escape", code: "Escape" }); assert.equal(emitted.defaultPrevented, true); }
    else h.pointer(finish);
    assert.equal(core.signature(h.session.project), before, finish); assert.equal(h.changes, 0); assert.equal(h.session.undoStack.length, 0);
    assert.equal(h.state.colorDrag, null); assert.equal(h.raf.size, 0); assert.equal(h.canvas.hasPointerCapture(1), false);
  }
});

test("invalid fifth overlap shows feedback and cannot commit a partial range change", () => {
  const items = [grade({ start: 0, end: 1 }), ...Array.from({ length: 4 }, (_, index) => grade({ id: `other${index}`, start: 3, end: 5 }))];
  const h = harness(items), before = core.signature(h.session.project); h.begin(); h.pointer("pointermove", { clientX: 310 }); h.runRaf();
  assert.match(h.state.colorDrag.invalid, /重疊 4/); assert.equal(core.signature(h.session.project), before);
  h.pointer("pointerup", { clientX: 310 });
  assert.equal(core.signature(h.session.project), before); assert.equal(h.changes, 0); assert.equal(h.state.colorDrag, null);
  assert.match(h.notices[0], /重疊 4/); assert.equal(h.errors.length, 0);
});

test("unrelated pointermove, release, cancellation and lost capture do not affect the active pointer", () => {
  const h = harness(); h.begin("move", { pointerId: 12 }); const active = h.state.colorDrag;
  for (const type of ["pointermove", "pointerup", "pointercancel", "lostpointercapture"]) h.pointer(type, { pointerId: 99, clientX: 300 });
  assert.equal(h.state.colorDrag, active); assert.equal(active.lastX, 100); assert.equal(h.changes, 0);
  h.pointer("pointercancel", { pointerId: 12 }); assert.equal(h.state.colorDrag, null);
});

test("color range clicks never reach generic timeline seeking, including the post-drag suppressed click", async () => {
  const h = harness(), node = h.nodes.get("grade");
  h.canvas.onclick(event("click", node, { clientX: 300 })); assert.deepEqual(h.queuedSeeks, []);
  const normal = event("click", h.canvas, { clientX: 300 }); h.canvas.onclick(normal); assert.deepEqual(h.queuedSeeks, [5]);
  h.queuedSeeks.length = 0; h.begin(); h.pointer("pointermove", { clientX: 160 }); h.pointer("pointerup", { clientX: 160 });
  const afterRelease = event("click", node, { clientX: 160 }); await h.$("colorTrack").onclick(afterRelease); h.canvas.onclick(afterRelease);
  assert.deepEqual(h.queuedSeeks, []); assert.deepEqual(h.seeks, []); assert.equal(h.changes, 1);
});

test("timeline arrow keys use real range transactions and preserve handle focus", async () => {
  const h = harness(), node = h.nodes.get("grade"), right = node.querySelector('[data-color-edge="right"]');
  const move = await h.key(node, { key: "ArrowRight", code: "ArrowRight" }); assert.equal(move.defaultPrevented, true);
  assert.ok(Math.abs(h.session.project.color_adjustments[0].start - (1 + 1 / 24)) < 1e-9);
  const oldEnd = h.session.project.color_adjustments[0].end;
  await h.key(right, { key: "ArrowLeft", code: "ArrowLeft" });
  assert.ok(Math.abs(h.session.project.color_adjustments[0].end - (oldEnd - 1 / 24)) < 1e-9);
  assert.equal(right.focused, true); assert.equal(h.changes, 2); assert.equal(h.plays, 0);
  const before = core.signature(h.session.project); await h.key(node, { key: "ArrowRight", code: "ArrowRight", ctrlKey: true });
  assert.equal(core.signature(h.session.project), before);
});

test("Space on color inspector and workspace buttons retains native activation without global playback", async () => {
  const h = harness(), preset = new Element("button", h.$("colorPanel"));
  for (const target of [preset, h.$("openColorWorkspace")]) {
    target.tagName = "button";
    const emitted = await h.key(target, { key: " ", code: "Space" });
    assert.equal(emitted.stopped, true); assert.equal(emitted.defaultPrevented, false); assert.equal(h.plays, 0);
  }
  const normal = await h.key(h.canvas, { key: " ", code: "Space" }); assert.equal(normal.defaultPrevented, true); assert.equal(h.plays, 1);
});

test("unsupported backend, in-flight save and secondary pointers cannot start range dragging", () => {
  for (const options of [{ colorReady: false }, { busy: true }, { backendReady: false }]) {
    const h = harness(); Object.assign(h.state, options); h.begin(); assert.equal(h.state.colorDrag, null); assert.equal(h.canvas.capture.size, 0);
  }
  const saving = harness(); saving.session.inFlight = Promise.resolve(); saving.begin(); assert.equal(saving.state.colorDrag, null);
  const other = harness(); other.begin("move", { isPrimary: false }); assert.equal(other.state.colorDrag, null);
});

test("commit conflicts report the error while clearing capture, RAF and gesture state", () => {
  const h = harness(); h.begin(); h.pointer("pointermove", { clientX: 160 }); h.runRaf();
  h.session.change(next => { next.name = "另一個修改"; }); const before = core.signature(h.session.project);
  h.pointer("pointerup", { clientX: 160 });
  assert.equal(core.signature(h.session.project), before); assert.equal(h.errors.length, 1); assert.match(h.errors[0].message, /內容已變更/);
  assert.equal(h.state.colorDrag, null); assert.equal(h.raf.size, 0); assert.equal(h.canvas.capture.size, 0);
});
