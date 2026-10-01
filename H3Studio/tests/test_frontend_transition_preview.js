"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { TransitionPreviewController } = require("../static/editor_transition_preview.js");
const { transitionPairs } = require("../static/editor_animations.js");
class FakeVideo {
  constructor() { this.readyState = 3; this.duration = 20; this.paused = true; this.seeking = false; this.listeners = new Map(); this.value = 0; this.seeks = 0; this.plays = 0; this.loads = 0; this.removed = false; this.playResult = null; }
  get currentTime() { return this.value; }
  set currentTime(value) { this.value = value; this.seeking = true; this.seeks++; }
  addEventListener(name, callback) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(callback); }
  emit(name) { for (const callback of this.listeners.get(name) || []) callback(); }
  seekDone() { this.seeking = false; this.emit("seeked"); }
  pause() { this.paused = true; }
  play() { this.paused = false; this.plays++; return this.playResult || Promise.resolve(); }
  load() { this.loads++; }
  removeAttribute(name) { if (name === "src") this.src = undefined; }
  remove() { this.removed = true; }
}
function pair(suffix = "", offset = 0, extra = {}) {
  const from = { id: `a${suffix}`, media_id: "sourceA", in: 0, out: 2, speed: 1, transition_out: { type: "crossfade", duration: 1, next_id: `b${suffix}` }, ...extra };
  const to = { id: `b${suffix}`, media_id: "sourceB", in: 0, out: 2, speed: 1 };
  const [value] = transitionPairs({ clips: [from, to] });
  return { ...value, start: value.start + offset, end: value.end + offset, cut: value.cut + offset, fromStart: value.fromStart + offset, toStart: value.toStart + offset };
}
function fixture(options = {}) {
  const made = [], errors = [];
  const controller = new TransitionPreviewController((value, role) => { const element = new FakeVideo(); made.push({ element, pair: value, role }); return element; }, { resolveUrl: clip => `/media/${clip.media_id}`, onError: (...args) => errors.push(args), ...options });
  return { controller, made, errors };
}
const settle = controller => { for (const slot of controller.slots.values()) slot.element.seekDone(); };
test("incoming first frame is held muted before the cut; outgoing last frame is held after the cut", () => {
  const { controller } = fixture(), value = pair();
  assert.equal(controller.sync([value], 1.6, true), false); settle(controller);
  assert.equal(controller.sync([value], 1.6, true), true);
  const elements = controller.get(value.id); assert.equal(elements.from.paused, false); assert.equal(elements.to.paused, true); assert.equal(elements.to.currentTime, 0);
  for (const element of Object.values(elements)) { assert.equal(element.muted, true); assert.equal(element.volume, 0); }
  assert.equal(controller.sync([value], 2.1, true), false); settle(controller);
  assert.equal(controller.sync([value], 2.1, true), true);
  assert.equal(elements.from.paused, true); assert.equal(elements.to.paused, false); assert.ok(Math.abs(elements.from.currentTime - (2 - 1 / 24)) < 1e-9);
  const seeks = elements.from.seeks; controller.sync([value], 2.12, true); assert.equal(elements.from.seeks, seeks);
});
test("nearby transition preloads only within a quarter second and cannot stall current non-transition playback", () => {
  const { controller, made } = fixture(), value = pair();
  assert.equal(controller.sync([value], 1.24, true), true); assert.equal(made.length, 0);
  assert.equal(controller.sync([value], 1.3, true), true); assert.equal(made.length, 2);
  const elements = controller.get(value.id); assert.equal(elements.from.paused, true); assert.equal(elements.to.paused, true); assert.equal(elements.from.currentTime, 1.5);
  elements.from.readyState = 0; assert.equal(controller.sync([value], 1.4, true), true); assert.equal(controller.sync([value], 1.6, true), false);
});
test("resources remain bounded at eight muted video nodes even with many imminent transitions", () => {
  const { controller, made } = fixture(), values = Array.from({ length: 20 }, (_, index) => pair(String(index), index * 0.01));
  assert.equal(controller.sync(values, 1.3, true), true); assert.equal(made.length, 8); assert.equal(controller.slots.size, 8);
  const first = controller.get(values[0].id); controller.sync(values, 3, false);
  assert.equal(controller.slots.size, 0); assert.equal(controller.get(values[0].id), null); assert.equal(first.from.src, undefined); assert.equal(first.from.removed, true);
});
test("paused scrubbing waits for the requested frame and seek() retries failed sources without duplicate allocations", () => {
  const { controller, made, errors } = fixture(), value = pair();
  assert.equal(controller.sync([value], 1.75, false), false); settle(controller); assert.equal(controller.sync([value], 1.75, false), true);
  const { from } = controller.get(value.id); assert.equal(from.paused, true); const seeks = from.seeks;
  assert.equal(controller.sync([value], 1.75, false), true); assert.equal(from.seeks, seeks);
  from.emit("error"); from.emit("error"); assert.equal(errors.length, 1); assert.equal(controller.sync([value], 1.75, false), false);
  controller.seek(); assert.equal(controller.sync([value], 1.75, false), true); assert.equal(made.length, 2);
});
test("stalled active transitions pause both sides until canplay; preloaded held incoming remains paused", () => {
  const { controller } = fixture(), value = pair(); controller.sync([value], 1.6, true); settle(controller); controller.sync([value], 1.6, true);
  const { from, to } = controller.get(value.id); from.emit("waiting"); assert.equal(controller.sync([value], 1.6, true), false); assert.equal(from.paused, true); assert.equal(to.paused, true);
  from.emit("canplay"); assert.equal(controller.sync([value], 1.6, true), true); assert.equal(from.paused, false); assert.equal(to.paused, true);
});
test("released source events and late play rejections cannot affect a replacement project", async () => {
  const { controller, errors } = fixture(), value = pair(); controller.sync([value], 1.6, false); settle(controller);
  const { from } = controller.get(value.id); let reject;
  from.playResult = new Promise((resolve, failure) => { reject = failure; }); controller.sync([value], 1.6, true); controller.clear();
  from.emit("error"); from.emit("waiting"); reject(new Error("old playback aborted")); await Promise.resolve();
  assert.equal(errors.length, 0); assert.equal(controller.slots.size, 0); assert.equal(controller.pairs.size, 0);
});
test("finished preview proxies replace paused sources, while active playback keeps its loaded source", () => {
  let version = "original";
  const { controller } = fixture({ resolveUrl: clip => `/${version}/${clip.media_id}` }), value = pair();
  controller.sync([value], 1.6, false); settle(controller); controller.sync([value], 1.6, true);
  const { from } = controller.get(value.id); version = "proxy"; controller.sync([value], 1.6, true); assert.equal(from.src, "/original/sourceA");
  controller.sync([value], 1.6, false); assert.equal(from.src, "/proxy/sourceA");
});
test("source mapping and playback speed honor trimmed, accelerated clips without reading outside the selected range", () => {
  const { controller } = fixture(), value = pair("", 0, { in: 2, out: 6, speed: 2 });
  controller.sync([value], 1.75, false); settle(controller); controller.sync([value], 1.75, false);
  const { from } = controller.get(value.id); assert.equal(from.currentTime, 5.5); assert.equal(from.playbackRate, 2);
  controller.sync([value], 2.25, false); settle(controller); controller.sync([value], 2.25, false);
  assert.ok(Math.abs(from.currentTime - (6 - 2 / 24)) < 1e-9); assert.ok(from.currentTime < 6);
});
test("active pairs receive resource priority over preload and unsupported excessive active pairs fail without allocating more", () => {
  const { controller, made, errors } = fixture(), first = pair("active"), future = Array.from({ length: 10 }, (_, index) => pair(`future${index}`, 0.2 + index * 0.001));
  controller.sync([first, ...future], 1.6, false); assert.ok(controller.get(first.id)); assert.equal(controller.slots.size, 8);
  assert.equal(controller.sync(Array.from({ length: 5 }, (_, index) => pair(`more${index}`)), 1.6, true), false); assert.equal(made.length, 8); assert.equal(errors.length, 1);
});
