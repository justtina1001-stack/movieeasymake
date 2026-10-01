"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), animationMath = require("../static/editor_animations.js");
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
  const context = vm.createContext({ state, ...core, animationMath, project: () => value, root: { devicePixelRatio: 1 }, videoElements: main,
    $: () => container, transitionPreview: { sync: () => true, get: () => elements }, document: { createElement: () => new SampleCanvas() } });
  for (const name of ["drawTransitionSource", "syncTransitionPreview"]) load(context, name);
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
