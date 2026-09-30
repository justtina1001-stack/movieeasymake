"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const { clone, ProjectSession, signature, duration, totalDuration, overlays } = core;
const clip = (id, changes = {}) => ({ id, media_id: "source", in: 2, out: 8, speed: 2, volume: .7, ...changes });
const layer = (id, changes = {}) => ({ ...clip(id), kind: "video", start: 3, end: 6,
  x: .5, y: .5, width: .75, rotation: 0, opacity: 1, ...changes });
const project = (changes = {}) => ({ id: "track-actions", name: "拖曳操作", updated_at: "r1",
  width: 1280, height: 720, fps: 24, clips: [clip("one"), clip("two"), clip("three")],
  audio_clips: [], overlays: [], ...changes });
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

// Extract complete shipped handlers. Tests supply DOM surfaces, not a second
// implementation of pointer routing, drop conversion or transaction behavior.
function shipped(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing shipped ${name}`);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`cannot extract ${name}`);
}
function classList() {
  const classes = new Set();
  return { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) };
}
function harness(options = {}) {
  let changes = 0, frameSerial = 0, capture = null, captureFailure = Boolean(options.captureFailure);
  const calls = [], errors = [], messages = [], frames = new Map(), listeners = new Map(), storage = new Map();
  let draftWrites = 0;
  const session = new ProjectSession(options.project || project(), () => { changes++; });
  const state = { session, media: new Map([["source", { id: "source", kind: "video", duration: 120,
    width: 640, height: 480, has_audio: true }]]), zoom: 60, playhead: 3.5, backendReady: true,
    busy: false, selectedKind: "video", selected: "one", queuedSeek: { time: 5 },
    trackDrag: null, trimDrag: null, overlayDrag: null, speedDrag: null, videoOverlaysReady: true, overlayTracksReady: true,
    ...options.state };
  const node = { dataset: options.from?.kind === "overlay" ? { overlayId: options.from.id } : { clipId: options.from?.id || "two" }, classList: classList() };
  const scroll = { scrollLeft: 0, scrollTop: 0 };
  const canvas = { classList: classList(), style: {},
    getBoundingClientRect: () => ({ left: 100 - scroll.scrollLeft, right: 1000, top: 40, bottom: 400, width: 900 }),
    setPointerCapture(id) { if (captureFailure) throw new Error("capture unavailable"); capture = id; },
    hasPointerCapture: id => capture === id,
    releasePointerCapture(id) { capture = null; dispatch("lostpointercapture", { pointerId: id }); },
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(callback); } };
  const body = { classList: classList() };
  const nodes = { timelineCanvas: canvas, timelineScroll: scroll };
  let feedback = { valid: true, target: { kind: "overlay", index: 0 }, label: "上層" };
  let scrollStep = 0;
  const sandbox = { core, state, clone, duration, totalDuration, overlays,
    project: () => session.project, overlaySupported: () => true,
    clamp: (value, low, high) => Math.max(low, Math.min(high, value)),
    $: id => nodes[id], document: { body }, localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => { storage.set(key, value); draftWrites++; },
      removeItem: key => { storage.delete(key); } },
    trackDragUI: { resolve: () => clone(feedback), autoScroll(point, options = {}) {
      const delta = options.horizontal === false ? 0 : scrollStep; scroll.scrollLeft += delta; return delta !== 0;
    }, show: (value, options) => calls.push(["ghost", clone(value), clone(options)]), hide: () => calls.push(["hide"]) },
    pause: () => calls.push(["pause"]), seek: (time, playing) => { calls.push(["seek", time, playing]); state.playhead = time; },
    render: () => calls.push(["render"]), renderInspector: () => calls.push(["inspector"]), renderDisabled: () => calls.push(["disabled"]),
    renderOverlayPreview: () => calls.push(["preview"]), updatePlayhead: () => calls.push(["playhead"]),
    notify: message => messages.push(message), errorNotice: error => errors.push(error), action: fn => fn,
    requestAnimationFrame: callback => { frames.set(++frameSerial, callback); return frameSerial; },
    cancelAnimationFrame: id => frames.delete(id) };
  vm.createContext(sandbox);
  const exporting = source.match(/^  const exporting = .+;$/m);
  assert.ok(exporting); vm.runInContext(exporting[0], sandbox);
  const locked = source.match(/^  const locked = .+;$/m);
  assert.ok(locked); vm.runInContext(locked[0], sandbox);
  for (const name of ["storagePut", "readDraft", "rememberDraft"]) vm.runInContext(shipped(name), sandbox);
  session.draftOwner = "controller-test-owner";
  session.onChange = () => { changes++; sandbox.rememberDraft(session); };
  for (const name of ["beginTrackGesture", "snapTrackStart", "renderTrackGesture", "flushTrackGesture", "finishTrackGesture"])
    vm.runInContext(shipped(name), sandbox);
  const bindingStart = source.indexOf('  $("timelineCanvas").addEventListener("pointerdown", action(beginTrackGesture));');
  const bindingEnd = source.indexOf('\n  for (const canvas of', bindingStart);
  assert.ok(bindingStart >= 0 && bindingEnd > bindingStart);
  vm.runInContext(source.slice(bindingStart, bindingEnd), sandbox);
  function target(excluded = false) { return { closest: selector => selector.includes("data-trim-edge") ? (excluded ? {} : null) : node }; }
  function dispatch(name, values = {}) {
    const event = { pointerId: 7, clientX: 320, clientY: 180, button: 0, isPrimary: true,
      target: target(), preventDefault() {}, stopPropagation() {}, ...values };
    for (const callback of listeners.get(name) || []) callback(event);
  }
  function runFrame() { const next = frames.entries().next().value; assert.ok(next); frames.delete(next[0]); next[1](); }
  return { session, state, node, body, canvas, scroll, sandbox, calls, errors, messages, frames, dispatch, runFrame,
    target, get changes() { return changes; }, get capture() { return capture; },
    get draftWrites() { return draftWrites; },
    draft: () => JSON.parse(storage.get(`h3-editor-draft:${session.project.id}`) || "null"),
    feedback: value => { feedback = value; }, autoScroll: value => { scrollStep = value; },
    clearCaptureFailure: () => { captureFailure = false; } };
}
function clean(h) {
  assert.equal(h.state.trackDrag, null); assert.equal(h.capture, null); assert.equal(h.frames.size, 0);
  assert.equal(h.node.classList.contains("track-drag-source"), false);
  assert.equal(h.body.classList.contains("is-track-dragging"), false);
}
function unchanged(h, before) { assert.equal(signature(h.session.project), before); assert.equal(h.session.undoStack.length, 0); assert.equal(h.changes, 0); }

test("a normal click selects and seeks without a drag transaction edit", () => {
  const h = harness(), before = signature(h.session.project);
  h.dispatch("pointerdown"); h.dispatch("pointerup");
  clean(h); unchanged(h, before); assert.equal(h.state.selected, "two");
  near(h.calls.find(call => call[0] === "seek")[1], (320 - 100) / 60);
});

test("coalesced pointer moves do not write drafts, final release commits one undo and seeks once", () => {
  const h = harness(), before = signature(h.session.project);
  h.dispatch("pointerdown"); assert.equal(h.state.queuedSeek, null);
  for (const clientX of [330, 340, 350]) h.dispatch("pointermove", { clientX, clientY: 80 });
  assert.equal(h.frames.size, 1); h.runFrame(); unchanged(h, before);
  assert.equal(h.calls.some(call => call[0] === "seek"), false);
  h.dispatch("pointerup", { clientX: 440, clientY: 80 }); clean(h);
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.changes, 1);
  assert.deepEqual(h.session.project.clips.map(item => item.id), ["one", "three"]);
  assert.equal(h.session.project.overlays[0].id, "two"); near(h.session.project.overlays[0].start, 5);
  assert.equal(h.state.selectedKind, "overlay"); assert.equal(h.calls.filter(call => call[0] === "seek").length, 1);
  h.session.travel(true); assert.equal(signature(h.session.project), before);
});

test("release before RAF includes horizontal scroll and exact last pointer position", () => {
  const h = harness(); h.dispatch("pointerdown"); h.scroll.scrollLeft = 60;
  h.dispatch("pointermove", { clientX: 350, clientY: 80 });
  h.dispatch("pointerup", { clientX: 380, clientY: 80 }); clean(h);
  near(h.session.project.overlays[0].start, 5);
  assert.equal(h.session.undoStack.length, 1);
});

for (const name of ["pointercancel", "lostpointercapture"]) test(`${name} cancels preview and scheduled autoscroll without writing`, () => {
  const h = harness(), before = signature(h.session.project);
  h.dispatch("pointerdown"); h.autoScroll(12); h.dispatch("pointermove", { clientX: 350, clientY: 80 }); h.runFrame();
  assert.equal(h.frames.size, 1); h.dispatch(name); clean(h); unchanged(h, before);
  assert.equal(h.calls.some(call => call[0] === "seek"), false);
  assert.equal(h.calls.filter(call => call[0] === "hide").length, 1);
});

test("a later invalid drop cannot commit an earlier legal preview", () => {
  const h = harness(), before = signature(h.session.project);
  h.dispatch("pointerdown"); h.dispatch("pointermove", { clientX: 340, clientY: 80 }); h.runFrame();
  h.feedback({ valid: false, reason: "影片不能放入音訊軌道", label: "音訊" });
  h.dispatch("pointerup", { clientX: 340, clientY: 300 }); clean(h); unchanged(h, before);
  assert.match(h.messages.at(-1), /音訊/);
});

test("invalid geometry retains the conflict guard when reentering a legal row", () => {
  const h = harness(); h.dispatch("pointerdown"); h.dispatch("pointermove", { clientX: 340, clientY: 80 }); h.runFrame();
  h.session.change(value => { value.name = "newer edit"; });
  h.feedback({ valid: false, label: "outside" }); h.dispatch("pointermove", { clientX: 340, clientY: 600 }); h.runFrame();
  h.feedback({ valid: true, target: { kind: "overlay", index: 0 }, label: "上層" });
  h.dispatch("pointerup", { clientX: 340, clientY: 80 }); clean(h);
  assert.equal(h.session.project.name, "newer edit"); assert.equal(h.session.project.overlays.length, 0);
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.changes, 1); assert.match(h.errors[0]?.message || "", /內容已變更/);
});

test("pointer identity and handle routing keep unrelated gestures out", () => {
  const h = harness(); h.dispatch("pointerdown", { target: h.target(true) }); assert.equal(h.state.trackDrag, null);
  h.dispatch("pointerdown"); h.dispatch("pointermove", { pointerId: 99, clientX: 400 });
  assert.equal(h.frames.size, 0); h.dispatch("pointerup", { pointerId: 99 }); assert.ok(h.state.trackDrag);
  h.dispatch("pointercancel"); clean(h);
});

for (const state of [{ backendReady: false }, { busy: true }, { exportJob: { status: "running" } }, { speedDrag: {} }])
  test(`locked state ${JSON.stringify(state)} blocks starting`, () => {
    const h = harness({ state }), before = signature(h.session.project);
    h.dispatch("pointerdown"); clean(h); unchanged(h, before);
    assert.equal(h.calls.some(call => call[0] === "pause"), false);
  });

test("in-flight saving blocks a new gesture and autosave is gated during an active one", () => {
  const h = harness(); h.session.inFlight = Promise.resolve(); h.dispatch("pointerdown"); assert.equal(h.state.trackDrag, null);
  h.session.inFlight = null; h.dispatch("pointerdown");
  const gate = source.match(/canSave: (\(\) => state\.session === session[^\n]+),/);
  assert.ok(gate); h.sandbox.session = h.session; vm.runInContext(`this.canSave = ${gate[1]};`, h.sandbox);
  assert.equal(h.sandbox.canSave(), false); h.dispatch("pointercancel"); assert.equal(h.sandbox.canSave(), true);
});

test("source crop, source-anchored speed curve, fades and original audio survive cross-layer release", () => {
  const value = project(); Object.assign(value.clips[1], { speed_curve: [{ time: 2, speed: .5 }, { time: 4, speed: 3 }, { time: 8, speed: 1 }], fade_in: .2, fade_out: .3 });
  const h = harness({ project: value }); h.dispatch("pointerdown"); h.dispatch("pointerup", { clientY: 70 }); clean(h);
  const moved = h.session.project.overlays[0];
  for (const key of ["in", "out", "speed", "volume", "fade_in", "fade_out"]) assert.equal(moved[key], value.clips[1][key]);
  assert.deepEqual(moved.speed_curve, value.clips[1].speed_curve); near(moved.end - moved.start, duration(value.clips[1]));
  assert.equal(h.session.undoStack.length, 1);
});

test("capture failure cleans transient editing state and preserves the project", () => {
  const h = harness({ captureFailure: true }), before = signature(h.session.project);
  assert.throws(() => h.dispatch("pointerdown"), /capture/);
  clean(h); unchanged(h, before);
  h.clearCaptureFailure(); h.dispatch("pointerdown"); assert.ok(h.state.trackDrag); h.dispatch("pointercancel"); clean(h);
});

test("a vertical layer move preserves its source start and ignores horizontal edge autoscroll", () => {
  const h = harness(); h.autoScroll(12); h.dispatch("pointerdown");
  h.dispatch("pointermove", { clientX: 324, clientY: 80 }); h.runFrame();
  assert.equal(h.scroll.scrollLeft, 0); assert.equal(h.frames.size, 0);
  h.dispatch("pointerup", { clientX: 324, clientY: 80 }); clean(h);
  near(h.session.project.overlays[0].start, 3);
});

test("an unavailable overlay backend rejects promotion without arming a legal old result", () => {
  const h = harness({ state: { videoOverlaysReady: false } }), before = signature(h.session.project);
  h.dispatch("pointerdown"); h.dispatch("pointerup", { clientY: 80 }); clean(h); unchanged(h, before);
  assert.match(h.messages.at(-1), /重新啟動/);
});

test("moving upper video to main resets layout visibly and preserves source editing in one history entry", () => {
  const item = layer("upper", { x: .2, width: .4, rotation: 20, opacity: .8, fade_in: .2, fade_out: .3 });
  const h = harness({ project: project({ overlays: [item] }), from: { kind: "overlay", id: "upper" } });
  h.feedback({ valid: true, target: { kind: "video", index: 1 }, label: "主軌" });
  h.dispatch("pointerdown"); h.dispatch("pointerup", { clientY: 260 }); clean(h);
  assert.deepEqual(h.session.project.clips.map(value => value.id), ["one", "upper", "two", "three"]);
  assert.equal(h.session.project.overlays.length, 0); assert.equal(h.state.selectedKind, "video");
  assert.equal(h.state.selected, "upper"); assert.equal(h.session.undoStack.length, 1); assert.equal(h.changes, 1);
  const moved = h.session.project.clips[1];
  for (const key of ["in", "out", "speed", "volume", "fade_in", "fade_out"]) assert.equal(moved[key], item[key]);
  assert.match(h.messages.at(-1), /位置|尺寸|比例/);
});

test("changing only overlay stacking refreshes composition without seeking media", () => {
  const value = project({ overlays: [layer("lower"), layer("upper")] });
  const h = harness({ project: value, from: { kind: "overlay", id: "upper" } });
  h.feedback({ valid: true, target: { kind: "overlay", index: 0 }, label: "下移圖層" });
  h.dispatch("pointerdown"); h.dispatch("pointerup", { clientY: 120 }); clean(h);
  assert.deepEqual(h.session.project.overlays.map(item => item.id), ["upper", "lower"]);
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.calls.some(call => call[0] === "seek"), false);
  assert.ok(h.calls.some(call => call[0] === "preview"));
});

test("nonprimary and nonleft pointers never pause or capture", () => {
  for (const values of [{ isPrimary: false }, { button: 2 }]) {
    const h = harness(); h.dispatch("pointerdown", values); clean(h);
    assert.equal(h.calls.some(call => call[0] === "pause"), false);
  }
});

test("joining an existing track at a horizontal destination creates one undo and persists its track ID", () => {
  const initial = project({ overlays: [layer("target", { start: 0, end: 3 })] });
  const h = harness({ project: initial }), before = signature(initial);
  h.feedback({ valid: true, target: { kind: "overlay", mode: "join", trackId: "target" }, label: "放入此軌" });
  h.dispatch("pointerdown"); h.dispatch("pointermove", { clientX: 470, clientY: 80 }); h.runFrame();
  unchanged(h, before); assert.equal(h.draftWrites, 0);
  h.dispatch("pointerup", { clientX: 500, clientY: 80 }); clean(h);
  assert.deepEqual(h.session.project.clips.map(item => item.id), ["one", "three"]);
  assert.deepEqual(h.session.project.overlays.map(item => [item.id, item.track_id, item.start]),
    [["target", "target", 0], ["two", "target", 6]]);
  assert.equal(core.overlayTrackGroups(h.session.project).length, 1);
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.changes, 1); assert.equal(h.draftWrites, 1);
  const record = h.draft(); assert.equal(record.owner, "controller-test-owner");
  assert.deepEqual(record.project.overlays.map(item => item.track_id), ["target", "target"]);
  assert.equal(signature(record.project), signature(h.session.project));
  assert.equal(core.resolveDraft(initial, record, h.state.media).status, "recoverable");
  h.session.travel(true); assert.equal(signature(h.session.project), before); assert.equal(h.draft(), null);
});

test("an overlapping join cancels a prior legal join result without writing a draft", () => {
  const h = harness({ project: project({ overlays: [layer("target")] }) }), before = signature(h.session.project);
  h.feedback({ valid: true, target: { kind: "overlay", mode: "join", trackId: "target" }, label: "放入此軌" });
  h.dispatch("pointerdown"); h.dispatch("pointermove", { clientX: 500, clientY: 80 }); h.runFrame();
  const transaction = h.state.trackDrag.transaction; assert.ok(transaction.result?.changed);
  near(transaction.result.start, 6); unchanged(h, before);
  h.dispatch("pointerup", { clientX: 320, clientY: 80 }); clean(h); unchanged(h, before);
  assert.equal(transaction.result, null); assert.equal(h.draftWrites, 0); assert.equal(h.draft(), null);
  assert.match(h.messages.at(-1), /同一圖層軌.*不能重疊/);
});

test("an unavailable shared-track capability rejects join and invalidates a prior insert result", () => {
  const h = harness({ project: project({ overlays: [layer("target", { start: 0, end: 3 })] }),
    state: { overlayTracksReady: false } }), before = signature(h.session.project);
  h.feedback({ valid: true, target: { kind: "overlay", mode: "insert", index: 1 }, label: "新增上層" });
  h.dispatch("pointerdown"); h.dispatch("pointermove", { clientX: 500, clientY: 80 }); h.runFrame();
  const transaction = h.state.trackDrag.transaction; assert.ok(transaction.result?.changed);
  h.feedback({ valid: true, target: { kind: "overlay", mode: "join", trackId: "target" }, label: "放入此軌" });
  h.dispatch("pointerup", { clientX: 500, clientY: 80 }); clean(h); unchanged(h, before);
  assert.equal(transaction.result, null); assert.equal(h.draftWrites, 0); assert.equal(h.draft(), null);
  assert.match(h.messages.at(-1), /重新啟動.*同軌剪輯/);
});

test("a pure vertical join keeps the original source time when the target track is free", () => {
  const h = harness({ project: project({ overlays: [layer("target", { start: 0, end: 3 })] }) });
  h.feedback({ valid: true, target: { kind: "overlay", mode: "join", trackId: "target" }, label: "放入此軌" });
  h.autoScroll(12); h.dispatch("pointerdown");
  h.dispatch("pointermove", { clientX: 324, clientY: 80 }); h.runFrame();
  assert.equal(h.scroll.scrollLeft, 0); assert.equal(h.draftWrites, 0);
  h.dispatch("pointerup", { clientX: 324, clientY: 80 }); clean(h);
  const moved = h.session.project.overlays.find(item => item.id === "two");
  near(moved.start, 3); near(moved.end, 6); assert.equal(moved.track_id, "target");
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.draftWrites, 1);
});

test("joining two overlay rows keeps the source editing and places both clips on the target track", () => {
  const moving = layer("moving", { start: 0, end: 3, fade_in: .2, fade_out: .3 });
  const h = harness({ project: project({ overlays: [layer("target", { start: 3, end: 6 }), moving] }),
    from: { kind: "overlay", id: "moving" } });
  h.feedback({ valid: true, target: { kind: "overlay", mode: "join", trackId: "target" }, label: "放入此軌" });
  h.dispatch("pointerdown"); h.dispatch("pointerup", { clientY: 80 }); clean(h);
  assert.deepEqual(h.session.project.overlays.map(item => item.id), ["moving", "target"]);
  assert.equal(core.overlayTrackGroups(h.session.project).length, 1);
  const moved = h.session.project.overlays[0]; near(moved.start, 0);
  for (const key of ["in", "out", "speed", "volume", "fade_in", "fade_out", "x", "y", "width", "rotation", "opacity"])
    assert.equal(moved[key], moving[key]);
  assert.equal(moved.track_id, "target"); assert.equal(h.session.undoStack.length, 1); assert.equal(h.draftWrites, 1);
  assert.equal(h.calls.some(call => call[0] === "seek"), false);
});
