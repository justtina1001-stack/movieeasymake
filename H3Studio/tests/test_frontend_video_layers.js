"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { fadeEnvelope, clampFades, VideoLayersController } = require("../static/editor_layers.js");
const {
  clone, clamp, speedOf, speedAtSource, timelineAt, duration, mainDuration, totalDuration, locateTime, validateProject, overlays,
  signature, canonicalSavedSignature, resolveDraft, editableContent,
  canonicalOverlay, overlayRasterKey, transformOverlay, shiftOverlayTime,
  splitAt, splitOverlayAt, trimTimelineClip, detachAudio, promoteClip,
  ProjectSession, OverlayTransaction, VideoDeck,
} = require("../static/editor.js");

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const layer = (changes = {}) => ({
  id: "picture-in-picture", kind: "video", media_id: "source", start: 1, end: 5,
  in: 2, out: 10, speed: 2, volume: 1, fade_in: 0, fade_out: 0,
  x: 0.5, y: 0.5, width: 0.4, rotation: 0, opacity: 1, ...changes,
});

// Media events and promises are controlled here; all seek, gain, readiness and
// lifecycle decisions come from the shipped controller, not a second implementation.
class FakeMedia {
  constructor() {
    this.listeners = new Map(); this.readyState = 0; this.duration = 30;
    this._time = 0; this._volume = 1; this.seeking = false; this.paused = true;
    this.style = {};
    this.loads = 0; this.seeks = []; this.playCalls = []; this.pending = []; this.controlled = false;
  }
  addEventListener(name, fn) { const list = this.listeners.get(name) || []; list.push(fn); this.listeners.set(name, list); }
  emit(name) { for (const fn of this.listeners.get(name) || []) fn(); }
  get currentTime() { return this._time; }
  set currentTime(value) { this._time = value; this.seeks.push(value); this.seeking = true; this.readyState = 1; }
  get volume() { return this._volume; }
  set volume(value) { assert.ok(Number.isFinite(value) && value >= 0 && value <= 1, "HTMLMediaElement volume must be finite and in [0, 1]"); this._volume = value; }
  load() { this.loads++; this.readyState = 0; this._time = 0; this.seeking = false; }
  metadata() { this.readyState = 1; this.emit("loadedmetadata"); }
  finishSeek() { this.readyState = 4; this.seeking = false; this.emit("seeked"); this.emit("canplay"); }
  play() { this.paused = false; this.playCalls.push(this.currentTime); if (!this.controlled) return Promise.resolve(); const pending = deferred(); this.pending.push(pending); return pending.promise; }
  pause() { this.paused = true; }
  removeAttribute(name) { if (name === "src") this.src = ""; }
}
function audioContext() {
  const sources = [], gains = [];
  const node = () => ({ connects: 0, disconnects: 0, connect() { this.connects++; }, disconnect() { this.disconnects++; } });
  return { destination: {}, sources, gains,
    createMediaElementSource(element) { const value = { ...node(), element }; sources.push(value); return value; },
    createGain() { const value = { ...node(), gain: { value: 1 } }; gains.push(value); return value; },
  };
}
function fixture(options = {}) {
  const elements = new Map(), errors = [], requested = [];
  const context = options.context === false ? null : audioContext();
  const controller = new VideoLayersController(item => {
    requested.push(item.id);
    if (!elements.has(item.id)) elements.set(item.id, new FakeMedia());
    return elements.get(item.id);
  }, { context, resolveUrl: options.resolveUrl || (item => `/media/${item.media_id}`), onError: error => errors.push(error.message) });
  return { controller, elements, errors, requested, context };
}
function prepare(f, layers, time, playing = true) {
  assert.equal(f.controller.sync(layers, time, playing), false);
  for (const element of f.elements.values()) element.metadata();
  assert.equal(f.controller.sync(layers, time, playing), false);
  for (const element of f.elements.values()) element.finishSeek();
  assert.equal(f.controller.sync(layers, time, playing), true);
}

test("visual envelopes use timeline seconds, preserve legacy defaults and exclude the end boundary", () => {
  assert.equal(fadeEnvelope({}, -0.01, 4), 0); assert.equal(fadeEnvelope({}, 0, 4), 1);
  assert.equal(fadeEnvelope({}, 3.99, 4), 1); assert.equal(fadeEnvelope({}, 4, 4), 0);
  const item = { fade_in: 1, fade_out: 2 };
  close(fadeEnvelope(item, 0.25, 4), 0.25); close(fadeEnvelope(item, 2, 4), 1);
  close(fadeEnvelope(item, 3, 4), 0.5); assert.equal(fadeEnvelope(item, 4, 4), 0);
});

test("shortening a clip scales both outer fades proportionally without adding defaults to legacy data", () => {
  const legacy = {}; assert.equal(clampFades(legacy, 2), legacy); assert.deepEqual(legacy, {});
  const item = { fade_in: 2, fade_out: 1 };
  clampFades(item, 1.5); assert.deepEqual(item, { fade_in: 1, fade_out: 0.5 });
  clampFades(item, 9); assert.deepEqual(item, { fade_in: 1, fade_out: 0.5 });
});

test("only active video layers allocate decoders and adjacent boundaries release before the next source", () => {
  const f = fixture(), active = [0, 1, 2].map(index => layer({ id: `video-${index}` }));
  const future = layer({ id: "future", start: 5, end: 9 });
  const layers = [...active, future, layer({ id: "text", kind: "text" }), layer({ id: "image", kind: "image" })];
  f.controller.sync(layers, 1, false);
  assert.equal(f.controller.slots.size, 3); assert.deepEqual(f.requested, active.map(item => item.id));
  f.controller.sync(layers, 5, false);
  assert.deepEqual([...f.controller.slots.keys()], ["future"]);
  for (const item of active) { const element = f.elements.get(item.id); assert.equal(element.src, ""); assert.equal(element.paused, true); }
  assert.equal(f.context.sources.filter(source => source.disconnects === 1).length, 3);
  f.controller.clear(); assert.equal(f.controller.slots.size, 0);
  assert.ok(f.context.sources.every(source => source.disconnects === 1));
  assert.ok(f.context.gains.every(gain => gain.disconnects === 1));
});

test("an invalid fourth simultaneous video is rejected without allocating a fourth decoder", () => {
  const f = fixture(); f.controller.sync([layer()], 1, false);
  const layers = [0, 1, 2, 3].map(index => layer({ id: `v${index}` }));
  assert.equal(f.controller.sync(layers, 1, true), false);
  assert.equal(f.elements.size, 1); assert.equal(f.controller.slots.size, 1);
  assert.match(f.errors[0], /3/); assert.equal(f.elements.values().next().value.paused, true);
});

test("metadata and canplay alone cannot start a trimmed layer before its precise seek completes", () => {
  const f = fixture(), item = layer({ in: 0.05, out: 8.05 });
  assert.equal(f.controller.sync([item], 1, true), false);
  const element = f.elements.get(item.id); element.metadata();
  assert.equal(f.controller.sync([item], 1, true), false); close(element.currentTime, 0.05);
  element.readyState = 4; element.seeking = false; element.emit("canplay");
  assert.equal(f.controller.sync([item], 1, true), false); assert.deepEqual(element.playCalls, []);
  element.finishSeek(); assert.equal(f.controller.sync([item], 1, true), true);
  assert.deepEqual(element.playCalls, [0.05]); assert.equal(element.playbackRate, 2); assert.equal(element.preservesPitch, true);
});

test("all active layers wait together and map one timeline time to independent source speeds", () => {
  const f = fixture(), first = layer(), second = layer({ id: "slow", in: 5, out: 7, speed: 0.5 });
  f.controller.sync([first, second], 2, true);
  for (const element of f.elements.values()) element.metadata();
  f.controller.sync([first, second], 2, true);
  const fast = f.elements.get(first.id), slow = f.elements.get(second.id);
  close(fast.currentTime, 4); close(slow.currentTime, 5.5);
  fast.finishSeek(); assert.equal(f.controller.sync([first, second], 2, true), false);
  assert.equal(fast.playCalls.length, 0); assert.equal(slow.playCalls.length, 0);
  slow.finishSeek(); assert.equal(f.controller.sync([first, second], 2, true), true);
  assert.deepEqual(fast.playCalls, [4]); assert.deepEqual(slow.playCalls, [5.5]);
  slow.readyState = 1; assert.equal(f.controller.sync([first, second], 2, true), false);
  assert.equal(fast.paused, true); assert.equal(slow.paused, true);
});

test("a waiting layer with only the current frame holds all layers until future data arrives", () => {
  const f = fixture(), first = layer(), second = layer({ id: "other" }); prepare(f, [first, second], 2);
  const waiting = f.elements.get(first.id), other = f.elements.get(second.id);
  waiting.readyState = 2; waiting.emit("waiting");
  assert.equal(f.controller.sync([first, second], 2, true), false);
  assert.equal(waiting.paused, true); assert.equal(other.paused, true);
  waiting.readyState = 4; waiting.emit("canplay");
  assert.equal(f.controller.sync([first, second], 2, true), true);
  assert.equal(waiting.paused, false); assert.equal(other.paused, false);
});

test("rapid scrubbing ignores an old seek completion and starts only at the newest source position", () => {
  const f = fixture(), item = layer(); prepare(f, [item], 1, false);
  const element = f.elements.get(item.id);
  f.controller.seek(); f.controller.sync([item], 2, true); close(element.currentTime, 4);
  f.controller.seek(); element.readyState = 4; f.controller.sync([item], 3, true); close(element.currentTime, 6);
  element._time = 4; element.finishSeek();
  assert.equal(f.controller.sync([item], 3, true), false); assert.equal(element.playCalls.length, 0);
  element._time = 6; element.finishSeek(); assert.equal(f.controller.sync([item], 3, true), true);
  assert.deepEqual(element.playCalls, [6]);
});

test("gain and visual edits apply one audio fade without reloading or seeking ready layers", () => {
  const f = fixture(), item = layer({ volume: 2, fade_in: 2, opacity: 0.25 });
  prepare(f, [item], 1.5);
  const element = f.elements.get(item.id), gain = f.controller.slots.get(item.id).gain;
  close(gain.gain.value, 0.5); assert.equal(element.volume, 1);
  const loads = element.loads, seeks = element.seeks.length;
  const edited = { ...item, x: 0.2, rotation: 45, opacity: 0.1, volume: 1, fade_in: 1 };
  assert.equal(f.controller.sync([edited], 1.5, true), true);
  close(gain.gain.value, 0.5); assert.equal(element.loads, loads); assert.equal(element.seeks.length, seeks);
});

test("new preview proxies are adopted while paused and require their own seek readiness", () => {
  let url = "/original"; const f = fixture({ resolveUrl: () => url }), item = layer();
  prepare(f, [item], 2); const element = f.elements.get(item.id), loads = element.loads;
  url = "/proxy"; assert.equal(f.controller.sync([item], 2, true), true);
  assert.equal(element.src, "/original"); assert.equal(element.loads, loads);
  assert.equal(f.controller.sync([item], 2, false), false);
  assert.equal(element.src, "/proxy"); assert.equal(element.loads, loads + 1); assert.equal(element.paused, true);
  element.metadata(); assert.equal(f.controller.sync([item], 2, false), false); close(element.currentTime, 4);
  element.finishSeek(); assert.equal(f.controller.sync([item], 2, false), true); assert.equal(element.paused, true);
});

test("pause and removal invalidate old play failures while a current play failure is reported", async () => {
  const f = fixture(), item = layer(); f.controller.sync([item], 1, false);
  const element = f.elements.get(item.id); element.controlled = true;
  element.metadata(); f.controller.sync([item], 1, true); element.finishSeek(); f.controller.sync([item], 1, true);
  const first = element.pending[0]; f.controller.pause(); f.controller.sync([item], 1, true);
  first.reject(new Error("old pause rejection")); await flush(); assert.deepEqual(f.errors, []);
  element.pending[1].reject(new Error("current rejection")); await flush(); assert.deepEqual(f.errors, ["current rejection"]);
  f.controller.pause(); f.controller.sync([item], 1, true); const removed = element.pending[2];
  f.controller.sync([], 1, true); removed.reject(new Error("removed layer rejection")); element.emit("error");
  await flush(); assert.deepEqual(f.errors, ["current rejection"]); assert.equal(element.src, "");
});

test("a failed source does not retry each animation frame but a user seek retries the same URL once", () => {
  const f = fixture(), item = layer(); prepare(f, [item], 1, false);
  const element = f.elements.get(item.id), loads = element.loads;
  element.emit("error"); assert.equal(f.errors.length, 1);
  for (let index = 0; index < 5; index++) assert.equal(f.controller.sync([item], 1, true), false);
  assert.equal(element.loads, loads); assert.equal(element.playCalls.length, 0);
  f.controller.seek(); assert.equal(f.controller.sync([item], 1, true), false);
  assert.equal(element.loads, loads + 1); assert.equal(element.src, "/media/source");
  element.metadata(); assert.equal(f.controller.sync([item], 1, true), false);
  element.finishSeek(); assert.equal(f.controller.sync([item], 1, true), true);
  assert.equal(element.playCalls.length, 1); assert.equal(f.errors.length, 1);
});

test("reusing an element after release reconnects its existing WebAudio graph exactly once", () => {
  const f = fixture(), item = layer(); prepare(f, [item], 1, false);
  f.controller.release(item.id); prepare(f, [item], 1, false);
  assert.equal(f.context.sources.length, 1); assert.equal(f.context.gains.length, 1);
  assert.equal(f.context.sources[0].connects, 2); assert.equal(f.context.sources[0].disconnects, 1);
  assert.equal(f.context.gains[0].connects, 2); assert.equal(f.context.gains[0].disconnects, 1);
});

test("an unavailable active DOM element keeps the controller unready without starting other layers", () => {
  const element = new FakeMedia();
  const controller = new VideoLayersController(item => item.id === "missing" ? null : element);
  const first = layer({ url: "/source" }), second = layer({ id: "missing", url: "/missing" });
  controller.sync([first, second], 1, true); element.metadata(); controller.sync([first, second], 1, true); element.finishSeek();
  assert.equal(controller.sync([first, second], 1, true), false); assert.equal(element.playCalls.length, 0);
});

const title = (changes = {}) => ({ id: "title", kind: "text", start: 0, end: 4,
  x: 0.5, y: 0.8, width: 0.7, rotation: 0, opacity: 1, text: "淡入標題", font_size: 0.06,
  color: "#ffffff", background: "transparent", bold: false, align: "center", ...changes });
const imageLayer = (changes = {}) => ({ id: "image-layer", kind: "image", media_id: "image",
  start: 0, end: 4, x: 0.5, y: 0.5, width: 0.4, rotation: 0, opacity: 1, ...changes });
const project = (layers = []) => ({ id: "multilayer-project", name: "多圖層測試", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [{ id: "main", media_id: "source", in: 2, out: 10, speed: 2, volume: 0.8 }], audio_clips: [], overlays: layers });
const media = () => new Map([
  ["source", { id: "source", kind: "video", duration: 20, has_audio: true }],
  ["image", { id: "image", kind: "image", duration: 0, has_audio: false }],
  ["audio", { id: "audio", kind: "audio", duration: 30, has_audio: true }],
]);

test("main duration stays sequential while video overlays alone extend the timeline through black gaps", () => {
  const value = project([layer({ start: 8, end: 12 }), title({ end: 30 }), imageLayer({ end: 40 })]);
  value.audio_clips = [{ id: "music", media_id: "audio", in: 0, out: 30, start: 0, speed: 1, volume: 1, track: 0, fade_in: 0, fade_out: 0 }];
  validateProject(value, media()); assert.equal(mainDuration(value), 4); assert.equal(totalDuration(value), 12);
  assert.equal(locateTime(value, 2).sourceTime, 6); assert.equal(locateTime(value, 4), null);
  assert.equal(locateTime(value, 6), null); assert.equal(locateTime(value, 9), null);
  value.clips = []; validateProject(value, media()); assert.equal(mainDuration(value), 0); assert.equal(totalDuration(value), 12);
  assert.equal(locateTime(value, 0), null);
  value.overlays = value.overlays.filter(item => item.kind !== "video");
  assert.equal(totalDuration(value), 0); // Titles, images and audio do not create an export duration.
});

test("video overlays validate source mapping, speed, gain, source kind and all finite fade fields", () => {
  validateProject(project([layer()]), media());
  for (const changes of [
    { in: -1 }, { out: 21 }, { out: 2 }, { speed: 0.2 }, { speed: 4.1 }, { speed: NaN },
    { end: 4.9 }, { volume: 2.01 }, { volume: Infinity }, { media_id: "missing" }, { media_id: "image" },
    { media_id: "audio" }, { fade_in: -0.1 }, { fade_out: "1" }, { fade_in: Infinity }, { fade_in: 3, fade_out: 2 },
  ]) assert.throws(() => validateProject(project([layer(changes)]), media()), JSON.stringify(changes));
  for (const item of [title({ fade_in: 3, fade_out: 2 }), imageLayer({ fade_out: -1 })]) {
    assert.throws(() => validateProject(project([item]), media()));
  }
  const invalidMain = project(); invalidMain.clips[0].fade_in = 5;
  assert.throws(() => validateProject(invalidMain, media()));
});

test("three-video and twelve-visual limits share the same end-exclusive interval boundaries", () => {
  const videos = [0, 1, 2].map(index => layer({ id: `v${index}`, start: 0, end: 4 }));
  const pictures = Array.from({ length: 9 }, (_, index) => imageLayer({ id: `i${index}` }));
  validateProject(project([...videos, ...pictures]), media());
  assert.throws(() => validateProject(project([...videos, layer({ id: "fourth", start: 0, end: 4 })]), media()), /3/);
  assert.throws(() => validateProject(project([...videos, ...pictures, title()]), media()), /12/);
  const next = layer({ id: "next", start: 4, end: 8 });
  validateProject(project([...videos, ...pictures, next]), media());
  const sequential = Array.from({ length: 50 }, (_, index) => layer({ id: `clip${index}`, start: index * 4, end: (index + 1) * 4 }));
  validateProject(project(sequential), media());
  assert.throws(() => validateProject(project([...sequential, layer({ id: "extra", start: 200, end: 204 })]), media()), /50/);
});

test("new zero fade defaults remain signature-compatible with legacy clips and text/image drafts", () => {
  const legacy = project([title(), imageLayer()]), normalized = clone(legacy);
  for (const item of [...normalized.clips, ...normalized.overlays]) Object.assign(item, { fade_in: 0, fade_out: 0 });
  assert.equal(signature(legacy), signature(normalized));
  const oldSignature = JSON.stringify(editableContent(legacy));
  assert.equal(canonicalSavedSignature(oldSignature), signature(normalized));
  const session = new ProjectSession(normalized); session.savedSignature = oldSignature;
  assert.equal(session.dirty, false);
  assert.equal(resolveDraft(normalized, { project: legacy, savedSignature: oldSignature }, media()).status, "equivalent");
});

test("recoverable drafts and editable copies retain nonzero fades and every video overlay source field", () => {
  const original = project([title(), imageLayer()]), draft = clone(original);
  draft.clips[0].fade_in = 0.5; draft.overlays[0].fade_out = 1;
  draft.overlays.push(layer({ fade_in: 1, fade_out: 0.5, volume: 1.7, rotation: 45 }));
  const server = { ...original, updated_at: "r2" };
  const result = resolveDraft(server, { project: draft, savedSignature: signature(original) }, media());
  assert.equal(result.status, "recoverable"); assert.equal(result.project.updated_at, "r2");
  assert.equal(signature(result.project), signature(draft));
  assert.equal(result.project.clips[0].fade_in, 0.5);
  assert.deepEqual(canonicalOverlay(result.project.overlays[2]), canonicalOverlay(draft.overlays[2]));
  assert.deepEqual(editableContent(result.project), editableContent(draft));
});

test("save refuses a backend that silently drops main fades or video source/fade fields", async () => {
  const mutations = [
    saved => { delete saved.clips[0].fade_in; },
    saved => { delete saved.overlays[0].fade_out; },
    saved => { saved.overlays[0].volume = 0; },
    saved => { saved.overlays[0].in = 0; },
    saved => { saved.overlays[0].end -= 0.5; },
    saved => { saved.overlays = []; },
  ];
  for (const mutate of mutations) {
    const session = new ProjectSession(project());
    session.change(value => { value.clips[0].fade_in = 0.5; value.overlays.push(layer({ fade_out: 1 })); });
    const before = clone(session.project);
    await assert.rejects(session.save(async snapshot => { const saved = { ...clone(snapshot), updated_at: "r2" }; mutate(saved); return saved; }), /後端|影片圖層/);
    assert.equal(session.dirty, true); assert.deepEqual(session.project, before);
  }
});

test("save accepts source-derived floating-point normalization after splitting a fractional-speed layer", async () => {
  const session = new ProjectSession(project([layer({ start: 0, end: 4, in: 0.1, out: 1.3, speed: 0.3 })]));
  session.change(value => splitOverlayAt(value, "picture-in-picture", 0.25, () => "right"));
  await session.save(async snapshot => ({ ...snapshot, updated_at: "r2", overlays: snapshot.overlays.map(item => ({
    ...item, end: item.start + (item.out - item.in) / item.speed, fade_in: item.fade_in ?? 0, fade_out: item.fade_out ?? 0,
  })) }));
  assert.equal(session.dirty, false); assert.equal(session.project.updated_at, "r2");
  close(session.project.overlays[0].end, 0.25); close(session.project.overlays[1].in, 0.175);
});

test("splitting V1 resets only internal fades and preserves speed, source continuity and undo", () => {
  const value = project(); Object.assign(value.clips[0], { fade_in: 1, fade_out: 2 });
  const session = new ProjectSession(value);
  session.change(next => splitAt(next, 1.5, () => "right"));
  const [left, right] = session.project.clips;
  close(left.out, 5); close(right.in, 5); assert.equal(left.speed, 2); assert.equal(right.speed, 2);
  assert.equal(left.fade_in, 1); assert.equal(left.fade_out ?? 0, 0);
  assert.equal(right.fade_in ?? 0, 0); assert.equal(right.fade_out, 2); assert.equal(totalDuration(session.project), 4);
  assert.equal(session.undoStack.length, 1); session.travel(true); assert.equal(signature(session.project), signature(value));
  session.travel(false); assert.equal(session.project.clips.length, 2);
});

test("splitting video/text/image layers retains z order and outer fades without creating an internal dip", () => {
  for (const source of [layer({ start: 0, end: 4, fade_in: 1, fade_out: 2 }), title({ fade_in: 1, fade_out: 2 }), imageLayer({ fade_in: 1, fade_out: 2 })]) {
    const value = project([title({ id: "below" }), source, imageLayer({ id: "above" })]);
    splitOverlayAt(value, source.id, 1.5, () => "right");
    assert.deepEqual(value.overlays.map(item => item.id), ["below", source.id, "right", "above"]);
    const left = value.overlays[1], right = value.overlays[2];
    assert.equal(left.end, right.start); assert.equal(left.fade_in, 1); assert.equal(left.fade_out ?? 0, 0);
    assert.equal(right.fade_in ?? 0, 0); assert.equal(right.fade_out, 2);
    assert.equal(fadeEnvelope(right, 0, right.end - right.start), 1);
    if (source.kind === "video") { close(left.out, 5); close(right.in, 5); }
  }
});

test("video overlay trims preserve opposite edges, respect source bounds and normalize fades", () => {
  const value = project([layer({ start: 2, end: 6, fade_in: 2, fade_out: 1 })]), before = clone(value);
  const left = shiftOverlayTime(value, "picture-in-picture", "left", 2, media()).layer;
  assert.equal(left.start, 4); assert.equal(left.end, 6); assert.equal(left.in, 6); assert.equal(left.out, 10);
  close(left.fade_in, 4 / 3); close(left.fade_out, 2 / 3);
  const extended = shiftOverlayTime(value, "picture-in-picture", "left", -99, media());
  assert.equal(extended.clamped, true); assert.equal(extended.layer.in, 0); assert.equal(extended.layer.start, 1); assert.equal(extended.layer.end, 6);
  const right = shiftOverlayTime(value, "picture-in-picture", "right", 99, media());
  assert.equal(right.clamped, true); assert.equal(right.layer.out, 20); assert.equal(right.layer.start, 2); assert.equal(right.layer.end, 11);
  const tiny = shiftOverlayTime(value, "picture-in-picture", "right", -99, media()).layer;
  close(tiny.end - tiny.start, 1 / value.fps); close((tiny.fade_in ?? 0) + (tiny.fade_out ?? 0), 1 / value.fps);
  assert.deepEqual(value, before);
});

test("speed edits recompute overlay ends and scaled fades while move gestures preserve source trims", () => {
  const value = project([layer({ fade_in: 2, fade_out: 1 })]);
  const faster = transformOverlay(value, "picture-in-picture", { speed: 4 }, media());
  assert.equal(faster.layer.start, 1); assert.equal(faster.layer.end, 3); close(faster.layer.fade_in, 4 / 3); close(faster.layer.fade_out, 2 / 3);
  const session = new ProjectSession(value), drag = new OverlayTransaction(session, "picture-in-picture", media());
  drag.updateTime("move", 3); drag.updateTime("move", 6); assert.equal(drag.commit(), true);
  assert.equal(session.undoStack.length, 1); assert.equal(session.project.overlays[0].start, 7); assert.equal(session.project.overlays[0].end, 11);
  assert.equal(session.project.overlays[0].in, 2); assert.equal(session.project.overlays[0].out, 10);
  session.travel(true); assert.equal(signature(session.project), signature(value));
});

test("main-track trim capacity uses main duration even when an overlay already reaches ten minutes", () => {
  const value = project([layer({ start: 596, end: 600 })]); Object.assign(value.clips[0], { fade_in: 2, fade_out: 1 });
  const extension = trimTimelineClip(value, { kind: "video", id: "main", edge: "right", delta: 1 }, media());
  assert.equal(extension.appliedDelta, 1); assert.equal(mainDuration(extension.project), 5); assert.equal(totalDuration(extension.project), 600);
  const shorter = trimTimelineClip(value, { kind: "video", id: "main", edge: "right", delta: -2.5 }, media());
  assert.equal(duration(shorter.clip), 1.5); assert.equal(shorter.clip.fade_in, 1); assert.equal(shorter.clip.fade_out, 0.5);
});

test("detaching original audio retains the same time envelope and leaves the picture fade unchanged", () => {
  const value = project(); Object.assign(value.clips[0], { fade_in: 0.5, fade_out: 1 });
  detachAudio(value, "main", () => "sound");
  const sound = value.audio_clips[0]; assert.equal(sound.fade_in, 0.5); assert.equal(sound.fade_out, 1);
  assert.equal(sound.volume, 0.8); assert.equal(sound.start, 0); assert.equal(sound.speed, 2);
  assert.equal(value.clips[0].volume, 0); assert.equal(value.clips[0].fade_in, 0.5); assert.equal(value.clips[0].fade_out, 1);
});

test("copying V1 to an overlay mutes the duplicate while moving preserves sound, fades and absolute placement", () => {
  const original = project([title()]); original.clips.push({ id: "second", media_id: "source", in: 4, out: 8, speed: 1, volume: 1.5, fade_in: 1, fade_out: 2 });
  const copied = clone(original); promoteClip(copied, "second", false, () => "copy");
  const copy = copied.overlays[1]; assert.equal(copied.clips.length, 2); assert.equal(copy.volume, 0);
  assert.equal(copy.start, 4); assert.equal(copy.end, 8); assert.equal(copy.fade_in, 1); assert.equal(copy.fade_out, 2);
  const session = new ProjectSession(original); session.change(value => promoteClip(value, "second", true, () => "moved"));
  const moved = session.project.overlays[1]; assert.equal(session.project.clips.length, 1); assert.equal(moved.volume, 1.5);
  assert.equal(moved.start, 4); assert.equal(moved.end, 8); assert.equal(totalDuration(session.project), 8);
  session.travel(true); assert.equal(signature(session.project), signature(original));
});

test("text raster cache keys stay stable for fades, timing, opacity and placement edits", () => {
  const original = title(), changed = { ...original, start: 5, end: 9, fade_in: 1, fade_out: 2, opacity: 0.5, x: 0.2, y: 0.1, rotation: 60 };
  assert.equal(overlayRasterKey(original, 1280, 720), overlayRasterKey(changed, 1280, 720));
  assert.notEqual(signature(project([original])), signature(project([changed])));
  assert.notEqual(overlayRasterKey(original, 1280, 720), overlayRasterKey({ ...original, text: "新文字" }, 1280, 720));
});

const browserSource = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
function loadBrowserFunction(context, name) {
  const start = browserSource.search(new RegExp(`  (?:async )?function ${name}\\(`));
  assert.notEqual(start, -1, `Production function ${name} must exist`);
  const rest = browserSource.slice(start), following = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
  assert.notEqual(following, -1, `Production function ${name} must have a following boundary`);
  vm.runInContext(rest.slice(0, following + 1), context);
}

test("the shipped V1 gain function resets opacity on a new clip and applies exactly one matching audio envelope", () => {
  const value = project(); value.clips[0].fade_in = 1;
  value.clips.push({ id: "next", media_id: "source", in: 0, out: 2, speed: 1, volume: 1.5 });
  const first = new FakeMedia(), second = new FakeMedia(), firstGain = { gain: { value: 1 } }, secondGain = { gain: { value: 1 } };
  const state = { playhead: 0.25, audio: { gains: new Map([[first, firstGain], [second, secondGain]]) } };
  const context = vm.createContext({ state, video: first, project: () => value, clamp, speedOf, speedAtSource, timelineAt, duration, fadeEnvelope, $: () => ({}) });
  loadBrowserFunction(context, "applyVolume"); context.applyVolume(value.clips[0]);
  close(first.style.opacity, 0.25); close(firstGain.gain.value, 0.2); assert.equal(first.volume, 1);
  second.style.opacity = 0.01; context.video = second; state.playhead = 4.5;
  context.applyVolume(value.clips[1]); assert.equal(second.style.opacity, 1); assert.equal(secondGain.gain.value, 1.5);
});

function clockFixture(value) {
  const controls = { layersReady: true, audioReady: true, layerPauses: 0, audioPauses: 0, frames: 0 };
  const video = new FakeMedia(); video.readyState = 4;
  const state = { playing: true, playhead: 0, previewIndex: -1, lastPlaybackTick: null,
    buffering: false, audioBuffering: false, layerBuffering: false, media: media(), trimDrag: null, overlayDrag: null };
  const deck = { pending: null, held: false, hold(value) { this.held = value; }, pause() {}, clear() {} };
  const videoLayers = { sync: () => controls.layersReady, pause() { controls.layerPauses++; } };
  state.mixer = { sync: () => controls.audioReady, pause() { controls.audioPauses++; } };
  const context = vm.createContext({ state, deck, video, videoLayers, project: () => value, overlays, totalDuration, speedOf, speedAtSource, timelineAt, duration, clamp,
    requestAnimationFrame() { controls.frames++; }, updatePlayhead() {}, applyVolume() {}, renderPlaybackStatus() {},
    $: () => ({ setAttribute() {} }), seek(time) { state.playhead = time; state.previewIndex = locateTime(value, time)?.index ?? -1; },
  });
  for (const name of ["pause", "nextPreviewClip", "playbackTick"]) loadBrowserFunction(context, name);
  return { context, state, controls, deck, video };
}

test("the shipped clock advances overlay-only black gaps, freezes on buffering and resumes without a catch-up jump", () => {
  const value = project([layer({ start: 1, end: 5 })]); value.clips = [];
  const f = clockFixture(value); f.context.playbackTick(0); f.context.playbackTick(50); close(f.state.playhead, 0.05);
  f.controls.layersReady = false; f.context.playbackTick(100); const stopped = f.state.playhead;
  f.context.playbackTick(1000); assert.equal(f.state.playhead, stopped); assert.equal(f.deck.held, true);
  assert.ok(f.controls.layerPauses > 0); assert.ok(f.controls.audioPauses > 0);
  f.controls.layersReady = true; f.context.playbackTick(2000); assert.equal(f.state.playhead, stopped);
  f.context.playbackTick(2050); close(f.state.playhead, stopped + 0.05); assert.equal(f.deck.held, false);
  f.context.pause(); f.context.playbackTick(3000); assert.equal(f.state.playhead, stopped + 0.05);
});

test("the shipped clock holds for any audio buffer and crosses the V1 end into an overlay tail", () => {
  const value = project([layer({ start: 4, end: 8 })]), f = clockFixture(value);
  f.state.previewIndex = 0; f.state.playhead = 3.5; f.video._time = 9;
  f.context.playbackTick(0); close(f.state.playhead, 3.5);
  f.controls.audioReady = false; f.context.playbackTick(20); const stopped = f.state.playhead;
  f.video._time = 9.5; f.context.playbackTick(1000); assert.equal(f.state.playhead, stopped); assert.equal(f.deck.held, true);
  f.controls.audioReady = true; f.context.playbackTick(2000); f.video._time = 10; f.context.playbackTick(2020);
  assert.equal(f.state.playhead, 4); assert.equal(f.state.previewIndex, -1); assert.equal(f.state.playing, true);
  f.context.playbackTick(2070); close(f.state.playhead, 4.05);
});

test("V1 canplay after a mid-clip waiting event releases the shared buffering hold", () => {
  const value = project([layer()]), f = clockFixture(value), elements = [new FakeMedia(), new FakeMedia()];
  const deck = new VideoDeck(elements, {
    active(element) { f.context.video = element; },
    buffering(value) { f.state.buffering = value; },
  });
  f.context.deck = deck; f.state.previewIndex = 0;
  deck.request(locateTime(value, 0), "/source", true);
  const active = elements[deck.pending.index]; active.metadata(); active.finishSeek();
  active._time = 6; f.context.playbackTick(0); close(f.state.playhead, 2);
  active.readyState = 2; active.emit("waiting"); f.context.playbackTick(20);
  assert.equal(deck.held, true); assert.equal(active.paused, true);
  active.readyState = 4; active.emit("canplay"); f.context.playbackTick(40);
  assert.equal(f.state.buffering, false); assert.equal(deck.held, false); assert.equal(active.paused, false);
  close(active.currentTime, 6); // Recovery must not jump back to the initial source in-point.
});
