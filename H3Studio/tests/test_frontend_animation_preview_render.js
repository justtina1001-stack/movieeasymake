"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), animationMath = require("../static/editor_animations.js");
const transitionEffects = require("../static/editor_transition_effects.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
function load(context, name) {
  const start = source.search(new RegExp(`  function ${name}\\(`)), rest = source.slice(start), following = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
  assert.ok(start >= 0 && following >= 0); vm.runInContext(rest.slice(0, following + 1), context);
}
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
// A single sample uses Canvas's premultiplied RGBA operations. The editor's real
// drawing functions decide opacity, clipping and blend mode; this controlled
// context makes compositing errors testable without video decoding or a GPU.
class SampleCanvas {
  constructor() {
    this.width = 100; this.height = 100; this.style = {}; this.pixel = [0, 0, 0, 0]; this.operations = []; this.removed = false;
    const canvas = this, saved = [];
    this.context = { globalAlpha: 1, globalCompositeOperation: "source-over", included: true,
      clearRect() { canvas.pixel = [0, 0, 0, 0]; }, fillRect() { canvas.pixel = [0, 0, 0, 1]; },
      save() { saved.push({ globalAlpha: this.globalAlpha, globalCompositeOperation: this.globalCompositeOperation, included: this.included }); },
      restore() { Object.assign(this, saved.pop()); }, translate() {}, rotate() {}, scale() {}, beginPath() { this.bounds = null; },
      rect(left, top, width) { this.bounds = [left, left + width]; },
      clip() { if (this.bounds) this.included = this.included && this.bounds[0] <= canvas.width / 2 && canvas.width / 2 < this.bounds[1]; },
      getImageData() {
        const data = new Uint8ClampedArray(canvas.width * canvas.height * 4), alpha = canvas.pixel[3];
        const pixel = canvas.pixel.map((value, channel) => Math.round((channel === 3 ? value : alpha ? value / alpha : 0) * 255));
        for (let offset = 0; offset < data.length; offset += 4) data.set(pixel, offset);
        return { data, width: canvas.width, height: canvas.height };
      },
      createImageData(width, height) { return { data: new Uint8ClampedArray(width * height * 4), width, height }; },
      putImageData(image) {
        const offset = (Math.floor(canvas.height / 2) * canvas.width + Math.floor(canvas.width / 2)) * 4;
        const pixel = [...image.data.slice(offset, offset + 4)], alpha = pixel[3] / 255;
        canvas.pixel = pixel.map((value, channel) => channel === 3 ? alpha : value / 255 * alpha);
        canvas.operations.push({ effect: true });
      },
      drawImage(image) {
        if (image.readyState !== undefined && image.readyState < 2) throw new Error("No decoded video frame");
        canvas.operations.push({ alpha: this.globalAlpha, blend: this.globalCompositeOperation, included: this.included }); if (!this.included) return;
        const incoming = image.pixel.map(value => value * this.globalAlpha);
        const outgoing = canvas.pixel;
        canvas.pixel = incoming.map((value, index) => this.globalCompositeOperation === "lighter" ? Math.min(1, value + outgoing[index]) : value + outgoing[index] * (1 - incoming[3]));
      },
    };
  }
  getContext() { return this.context; }
  setAttribute() {}
  remove() { this.removed = true; }
}
const clip = (id, start, type, next_id) => ({ id, kind: "video", media_id: id, track_id: "upper", start, end: start + 2, in: 0, out: 2, speed: 1, volume: 1,
  x: 0.5, y: 0.5, width: 1, rotation: 0, opacity: 0.5, ...(type ? { transition_out: { type, duration: 1, next_id } } : {}) });
function fixture(type = "crossfade", kind = "overlay", time = 2) {
  const from = clip("from", 0, type, "to"), to = clip("to", 2), value = { width: 100, height: 100, fps: 24, clips: kind === "video" ? [from, to] : [], overlays: kind === "overlay" ? [from, to] : [] };
  const state = { playhead: time, transitionCanvases: new Map(), rasterNodes: new Map(value.overlays.map(layer => [layer.id, { node: { style: {} } }])) };
  const main = [{ style: {} }, { style: {} }], container = { clientWidth: 100, append() {} };
  const elements = { from: { readyState: 3, videoWidth: 100, videoHeight: 100, pixel: [1, 0, 0, 1] }, to: { readyState: 3, videoWidth: 100, videoHeight: 100, pixel: [0, 0, 1, 1] } };
  const context = vm.createContext({ state, ...core, animationMath, project: () => value, root: { devicePixelRatio: 1, H3EditorTransitionEffects: transitionEffects }, videoElements: main,
    $: () => container, transitionPreview: { sync: () => true, get: () => elements }, document: { createElement: () => new SampleCanvas() } });
  for (const name of ["clearTransitionPreview", "drawTransitionSource", "syncTransitionPreview"]) load(context, name);
  return { context, state, value, main, elements, output: () => [...state.transitionCanvases.values()][0]?.canvas };
}
test("the shipped transition renderer never draws metadata-only video frames into Canvas", () => {
  const f = fixture(), canvas = new SampleCanvas(), element = { ...f.elements.from, readyState: 1 };
  assert.doesNotThrow(() => f.context.drawTransitionSource(canvas, element, f.value.overlays[0], 1, 2, "overlay"));
  assert.deepEqual(canvas.pixel, [0, 0, 0, 0]); assert.equal(canvas.operations.length, 0);
  element.readyState = 2; f.context.drawTransitionSource(canvas, element, f.value.overlays[0], 1, 2, "overlay"); assert.equal(canvas.operations.length, 1);
});
test("the shipped overlay crossfade combines weighted premultiplied alpha so the lower video stays visible", () => {
  const f = fixture(); assert.equal(f.context.syncTransitionPreview(false), true);
  const result = f.output(); assert.deepEqual(result.operations.map(item => item.blend), ["source-over", "lighter"]);
  close(result.pixel[0], 0.25); close(result.pixel[1], 0); close(result.pixel[2], 0.25); close(result.pixel[3], 0.5);
  // Putting this transition over opaque green should retain exactly half green.
  close(1 - result.pixel[3], 0.5); assert.equal(f.state.rasterNodes.get("from").node.style.visibility, "hidden"); assert.equal(f.state.rasterNodes.get("to").node.style.visibility, "hidden");
});
test("the shipped wipe masks select a single transparent layer on each side without a doubled opacity", () => {
  for (const [type, time, expected] of [["wipe_left", 1.75, [0.5, 0, 0, 0.5]], ["wipe_left", 2.25, [0, 0, 0.5, 0.5]], ["wipe_right", 1.75, [0.5, 0, 0, 0.5]], ["wipe_right", 2.25, [0, 0, 0.5, 0.5]]]) {
    const f = fixture(type, "overlay", time); f.context.syncTransitionPreview(false);
    for (let index = 0; index < 4; index++) close(f.output().pixel[index], expected[index]);
  }
});
test("main-track transitions keep the normal transport hidden only within the transition window", () => {
  const f = fixture("crossfade", "video"); f.context.syncTransitionPreview(false); const canvas = f.output();
  assert.ok(f.main.every(element => element.style.visibility === "hidden")); close(canvas.pixel[3], 1);
  f.state.playhead = 3; f.context.syncTransitionPreview(false); assert.ok(f.main.every(element => element.style.visibility === "")); assert.equal(canvas.removed, true); assert.equal(f.state.transitionCanvases.size, 0);
});
test("leaving an overlay transition restores ordinary layer visibility and releases its temporary canvases", () => {
  const f = fixture(); f.context.syncTransitionPreview(false); const canvas = f.output();
  f.state.playhead = 3; f.context.syncTransitionPreview(false); assert.equal(canvas.removed, true); assert.equal(f.state.transitionCanvases.size, 0);
  assert.ok([...f.state.rasterNodes.values()].every(item => item.node.style.visibility === ""));
});

test("an unchanged paused transition keeps polling readiness and visibility without redrawing full video canvases", () => {
  const f = fixture(); let polls = 0; f.context.transitionPreview.sync = () => { polls++; return true; };
  f.context.syncTransitionPreview(false); const canvas = f.output(), original = canvas.operations.length;
  f.state.rasterNodes.get("from").node.style.visibility = "";
  f.context.syncTransitionPreview(false); f.context.syncTransitionPreview(false);
  assert.equal(polls, 3); assert.equal(canvas.operations.length, original); assert.equal(f.state.rasterNodes.get("from").node.style.visibility, "hidden");
  f.context.syncTransitionPreview(true); assert.ok(canvas.operations.length > original, "Playing frames must continue drawing");
});

test("paused drawing resumes when decoding completes or an element reaches a different source frame", () => {
  const f = fixture(); let ready = false; f.context.transitionPreview.sync = () => ready; f.elements.from.readyState = 1;
  f.context.syncTransitionPreview(false); const canvas = f.output(), initial = canvas.operations.length;
  f.context.syncTransitionPreview(false); assert.ok(canvas.operations.length > initial, "Unready frames must not be cached");
  ready = true; f.elements.from.readyState = 3; f.elements.from.currentTime = 1.95; f.context.syncTransitionPreview(false);
  const decoded = canvas.operations.length; f.context.syncTransitionPreview(false); assert.equal(canvas.operations.length, decoded);
  f.elements.from.currentTime = 1.96; f.context.syncTransitionPreview(false); assert.ok(canvas.operations.length > decoded);
});

test("editing the current project or resizing invalidates a paused transition's cached drawing", () => {
  const f = fixture(); f.context.syncTransitionPreview(false); const canvas = f.output(), initial = canvas.operations.length;
  const changed = JSON.parse(JSON.stringify(f.value)); changed.overlays[0].opacity = 0.2; f.context.project = () => changed;
  f.context.syncTransitionPreview(false); assert.ok(canvas.operations.length > initial); close(canvas.pixel[0], 0.1); close(canvas.pixel[3], 0.35);
  const edited = canvas.operations.length; changed.width = 200; f.context.syncTransitionPreview(false);
  assert.ok(canvas.operations.length > edited); assert.equal(canvas.height, 50);
});

test("built-in effects use the shipped pixel renderer while retaining translucent lower tracks and paused caches", () => {
  for (const type of transitionEffects.EFFECT_TYPES) {
    const f = fixture(type); assert.equal(f.context.syncTransitionPreview(false), true);
    const canvas = f.output(); assert.ok(canvas.operations.some(item => item.effect));
    assert.ok(Math.abs(canvas.pixel[3] - 0.5) <= 1 / 255, type);
    if (type === "white_flash") for (let channel = 0; channel < 3; channel++) assert.ok(Math.abs(canvas.pixel[channel] - canvas.pixel[3]) <= 1 / 255);
    const drawn = canvas.operations.length; f.context.syncTransitionPreview(false); assert.equal(canvas.operations.length, drawn);
    f.context.syncTransitionPreview(true); assert.ok(canvas.operations.length > drawn);
    f.state.playhead = 3; f.context.syncTransitionPreview(false); assert.equal(f.state.transitionCanvases.size, 0); assert.equal(canvas.removed, true);
  }
});

test("advanced-effect saves preserve drafts until the backend advertises effect support", () => {
  const value = { clips: [{ transition_out: { type: "white_flash", duration: 1, next_id: "b" } }], overlays: [] }, before = core.clone(value);
  assert.throws(() => core.requireTransitionEffectSupport(value, false), /草稿仍保留/);
  assert.deepEqual(value, before); assert.doesNotThrow(() => core.requireTransitionEffectSupport(value, true));
  value.clips[0].transition_out.type = "crossfade"; assert.doesNotThrow(() => core.requireTransitionEffectSupport(value, false));
  value.clips = []; value.overlays = [{ transition_out: { type: "rgb_glitch", duration: 1, next_id: "b" } }];
  assert.throws(() => core.requireTransitionEffectSupport(value, false), /草稿仍保留/);
});

test("portrait effect previews keep the same pixel budget as landscape video", () => {
  const f = fixture("white_flash", "video"); f.value.width = 1080; f.value.height = 1920;
  f.context.$("videoCanvas").clientWidth = 2000; f.context.syncTransitionPreview(false);
  assert.equal(f.output().width, 540); assert.equal(f.output().height, 960);
});

test("a Canvas readback failure restores ordinary videos, releases canvases, and reports a paused error", () => {
  const f = fixture("white_flash"); let paused = false, error = null, attempts = 0, notifications = 0;
  f.context.root.H3EditorTransitionEffects = { render() { attempts++; throw new Error("readback unavailable"); } };
  f.context.transitionPreview.clear = () => {}; f.context.pause = () => { paused = true; }; f.context.errorNotice = value => { notifications++; error = value; };
  assert.equal(f.context.syncTransitionPreview(true), false); assert.equal(paused, true);
  assert.match(error.message, /readback unavailable/); assert.equal(f.state.transitionCanvases.size, 0);
  assert.ok([...f.state.rasterNodes.values()].every(item => item.node.style.visibility === ""));
  assert.equal(f.context.syncTransitionPreview(false), false); assert.equal(attempts, 1); assert.equal(notifications, 1); assert.equal(f.state.transitionCanvases.size, 0);
  f.state.playhead = 2.1; assert.equal(f.context.syncTransitionPreview(false), false); assert.equal(attempts, 2);
  f.context.clearTransitionPreview(); assert.equal(f.context.syncTransitionPreview(false), false); assert.equal(attempts, 3);
});

test("reference preview budgets follow their sampling cost and bound landscape and portrait canvases", () => {
  for (const [type, limit] of [["radial_zoom", 360], ["horizontal_blur", 240]]) for (const portrait of [false, true]) {
    const f = fixture(type, "video"); f.value.width = portrait ? 1080 : 1920; f.value.height = portrait ? 1920 : 1080;
    f.context.$("videoCanvas").clientWidth = 2000; f.context.syncTransitionPreview(false);
    assert.ok(Math.max(f.output().width, f.output().height) <= limit); assert.ok(Math.max(f.output().width, f.output().height) >= limit - 2);
    assert.ok(f.output().width * f.output().height <= limit * limit * 0.5625 + limit);
  }
});

test("simultaneous reference transitions share smaller preview budgets across main and overlay tracks", () => {
  for (const [type, limit] of [["radial_zoom", 300], ["horizontal_blur", 200]]) {
    const f = fixture(type, "video"); f.value.width = 1920; f.value.height = 1080;
    f.value.overlays = [clip("upper-from", 0, type, "upper-to"), clip("upper-to", 2)];
    f.context.$("videoCanvas").clientWidth = 2000; f.context.syncTransitionPreview(false);
    assert.equal(f.state.transitionCanvases.size, 2);
    for (const item of f.state.transitionCanvases.values()) assert.ok(Math.max(item.canvas.width, item.canvas.height) <= limit);
  }
});
