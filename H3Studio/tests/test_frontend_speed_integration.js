"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const core = require("../static/editor.js");
const {
  clone, duration, speedLabel, mainDuration, totalDuration, signature, canonicalOverlay, validateProject,
  locateTime, sourceAt, timelineAt, speedAtSource, trimTimelineClip, shiftOverlayTime,
  splitAt, splitAudioAt, splitOverlayAt, detachAudio, promoteClip, ProjectSession, transformClipSpeed, SpeedCurveTransaction,
  VideoDeck, AudioPreview, VideoLayersController,
} = core;
const close = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);
const points = () => [{ time: 0, speed: 0.25 }, { time: 4, speed: 4 }, { time: 10, speed: 0.5 }];
const clip = (changes = {}) => ({ id: "ramp", media_id: "source", in: 1, out: 9, speed: 1, volume: 0.8, speed_curve: points(), ...changes });
const lead = () => ({ id: "lead", media_id: "source", in: 0, out: 1, speed: 1, volume: 1 });
const project = (changes = {}) => ({ id: "speed-project", name: "曲線變速", updated_at: "r1", width: 1280, height: 720, fps: 24, clips: [lead(), clip()], audio_clips: [], overlays: [], ...changes });
const audio = (changes = {}) => ({ ...clip({ id: "voice" }), start: 2, track: 0, fade_in: 0, fade_out: 0, ...changes });
const layer = (changes = {}) => { const item = { ...clip({ id: "upper" }), kind: "video", start: 2, x: 0.5, y: 0.5, width: 0.4, rotation: 0, opacity: 1, ...changes }; item.end ??= item.start + duration(item); return item; };
const media = () => new Map([["source", { id: "source", kind: "video", duration: 20, has_audio: true, url: "/source.mp4" }], ["image", { id: "image", kind: "image", duration: 0 }]]);

test("fixed speed labels retain their multiplier and curve labels identify the active curve", () => {
  assert.equal(speedLabel(lead()), "1×"); assert.equal(speedLabel({ ...lead(), speed: 2 }), "2×");
  assert.equal(speedLabel(clip()), "曲線變速");
});

test("empty curve fields remain signature-compatible while main, audio and upper curves all affect signatures", () => {
  const old = project({ clips: [lead()], audio_clips: [audio({ speed_curve: [] })], overlays: [layer({ speed_curve: [], end: 10 })] });
  const empty = clone(old); empty.clips[0].speed_curve = []; delete old.audio_clips[0].speed_curve; delete old.overlays[0].speed_curve;
  assert.equal(signature(empty), signature(old));
  for (const kind of ["video", "audio", "overlay"]) {
    const value = project({ audio_clips: [audio()], overlays: [layer()] }), changed = clone(value);
    const item = kind === "video" ? changed.clips[1] : kind === "audio" ? changed.audio_clips[0] : changed.overlays[0];
    item.speed_curve[1].speed = 2;
    if (kind === "overlay") item.end = item.start + duration(item);
    assert.notEqual(signature(value), signature(changed));
  }
  const item = layer(), original = clone(item), canonical = canonicalOverlay(item);
  canonical.speed_curve[0].speed = 4;
  assert.deepEqual(item, original);
});

for (const kind of ["video", "audio", "overlay"]) test(`saving refuses an old backend that drops a ${kind} curve and preserves its draft`, async () => {
  const value = project({ audio_clips: [audio()], overlays: [layer()] }), session = new ProjectSession(value);
  session.change(next => { next.name = "尚未保存的曲線"; });
  const before = clone(session.project);
  await assert.rejects(session.save(async snapshot => {
    const saved = clone(snapshot); saved.updated_at = "r2";
    const item = kind === "video" ? saved.clips[1] : kind === "audio" ? saved.audio_clips[0] : saved.overlays[0];
    delete item.speed_curve;
    if (kind === "overlay") item.end = item.start + duration(item);
    return saved;
  }), /曲線|完整保存|更新|重啟|重新啟動/);
  assert.equal(session.dirty, true); assert.deepEqual(session.project, before);
});

test("locating the sequential timeline maps source time through the inverse curve and respects boundaries", () => {
  const value = project(), ramp = value.clips[1]; validateProject(value, media());
  for (const ratio of [0, 0.1, 0.37, 0.9, 1]) {
    const local = duration(ramp) * ratio, point = locateTime(value, 1 + local);
    assert.equal(point.clip.id, "ramp"); close(point.start, 1); close(point.sourceTime, sourceAt(ramp, local));
    close(timelineAt(ramp, point.sourceTime), local); close(point.end, mainDuration(value));
  }
  assert.equal(locateTime(value, 1 - 1e-4).clip.id, "lead");
  close(totalDuration(value), 1 + duration(ramp));
});

for (const kind of ["video", "audio"]) test(`${kind} curve trim and extension retain all hidden anchors and preserve exact endpoints`, () => {
  const value = kind === "video" ? project() : project({ audio_clips: [audio()] });
  const item = kind === "video" ? value.clips[1] : value.audio_clips[0], id = item.id;
  const before = clone(value), initialLength = duration(item);
  const shortened = trimTimelineClip(value, { kind, id, edge: "left", delta: 0.5 }, media());
  close(shortened.clip.in, sourceAt(item, 0.5)); close(duration(shortened.clip), initialLength - 0.5);
  assert.deepEqual(shortened.clip.speed_curve, item.speed_curve);
  if (kind === "audio") close(shortened.clip.start + duration(shortened.clip), item.start + initialLength);
  const restored = trimTimelineClip(shortened.project, { kind, id, edge: "left", delta: -0.5 }, media());
  close(restored.clip.in, item.in); assert.equal(restored.clip.out, item.out);
  assert.deepEqual(restored.clip.speed_curve, item.speed_curve);
  if (kind === "audio") close(restored.clip.start, item.start);
  const start = trimTimelineClip(value, { kind, id, edge: "left", delta: -1000 }, media());
  assert.equal(start.clip.in, 0); assert.equal(start.clamped, true); assert.match(start.reason, /來源|時間軸/);
  const end = trimTimelineClip(value, { kind, id, edge: "right", delta: 1000 }, media());
  assert.equal(end.clip.out, 20); assert.equal(end.clamped, true); assert.match(end.reason, /來源/);
  assert.deepEqual(end.clip.speed_curve, item.speed_curve);
  const right = trimTimelineClip(value, { kind, id, edge: "right", delta: -0.5 }, media());
  close(right.clip.out, sourceAt(item, initialLength - 0.5));
  const again = trimTimelineClip(right.project, { kind, id, edge: "right", delta: 0.5 }, media());
  close(again.clip.out, item.out); assert.equal(again.clip.in, item.in);
  assert.deepEqual(again.clip.speed_curve, item.speed_curve);
  if (kind === "audio") assert.equal(again.clip.start, item.start);
  assert.deepEqual(value, before);
});

test("upper video curve edges use presentation seconds and restore the same source curve", () => {
  const upper = layer(), value = project({ overlays: [upper] }), before = clone(value);
  const shorter = shiftOverlayTime(value, "upper", "left", 0.5, media());
  close(shorter.layer.start, upper.start + 0.5); close(shorter.layer.in, sourceAt(upper, 0.5)); close(shorter.layer.end, upper.end);
  assert.deepEqual(shorter.layer.speed_curve, upper.speed_curve);
  const restored = shiftOverlayTime(shorter.project, "upper", "left", -0.5, media());
  close(restored.layer.in, upper.in); close(restored.layer.start, upper.start); close(restored.layer.end, upper.end);
  assert.equal(restored.layer.out, upper.out); assert.deepEqual(restored.layer.speed_curve, upper.speed_curve);
  const right = shiftOverlayTime(value, "upper", "right", 100, media());
  assert.equal(right.layer.out, 20); close(right.layer.end - right.layer.start, duration(right.layer));
  const left = shiftOverlayTime(value, "upper", "left", -100, media());
  assert.equal(left.layer.in, 0); assert.deepEqual(left.layer.speed_curve, upper.speed_curve);
  assert.deepEqual(value, before);
});

for (const kind of ["video", "audio", "overlay"]) test(`splitting a ${kind} curve retains exact timing and gives each half an independent anchor array`, () => {
  const value = project({ audio_clips: [audio()], overlays: [layer()] });
  const original = clone(kind === "video" ? value.clips[1] : kind === "audio" ? value.audio_clips[0] : value.overlays[0]);
  const start = kind === "video" ? 1 : original.start, local = duration(original) * 0.4, at = start + local;
  const id = kind === "video" ? splitAt(value, at, () => "second") : kind === "audio" ? splitAudioAt(value, original.id, at, () => "second") : splitOverlayAt(value, original.id, at, () => "second");
  assert.equal(id, "second");
  const list = kind === "video" ? value.clips.slice(1) : kind === "audio" ? value.audio_clips : value.overlays, [first, second] = list;
  close(first.out, sourceAt(original, local)); close(second.in, first.out); close(duration(first) + duration(second), duration(original));
  assert.deepEqual(first.speed_curve, original.speed_curve); assert.deepEqual(second.speed_curve, original.speed_curve);
  assert.notEqual(first.speed_curve, second.speed_curve); assert.notEqual(first.speed_curve[0], second.speed_curve[0]);
  for (const ratio of [0, 0.2, 0.7, 1]) close(sourceAt(second, duration(second) * ratio), sourceAt(original, local + duration(second) * ratio));
  first.speed_curve[0].speed = 4;
  assert.equal(second.speed_curve[0].speed, 0.25); assert.equal(original.speed_curve[0].speed, 0.25);
});

test("audio detachment and upper-video promotion preserve source ramp, duration, fades and sequential origin", () => {
  const value = project(); Object.assign(value.clips[1], { fade_in: 0.2, fade_out: 0.3 });
  const original = clone(value.clips[1]); detachAudio(value, "ramp", () => "detached");
  const detached = value.audio_clips[0]; close(detached.start, 1); close(duration(detached), duration(original));
  assert.deepEqual(detached.speed_curve, original.speed_curve); assert.notEqual(detached.speed_curve, value.clips[1].speed_curve);
  assert.equal(detached.fade_in, 0.2); assert.equal(detached.fade_out, 0.3); assert.equal(value.clips[1].volume, 0);
  promoteClip(value, "ramp", false, () => "upper"); const upper = value.overlays[0];
  close(upper.start, 1); close(upper.end - upper.start, duration(original)); assert.equal(upper.volume, 0);
  assert.deepEqual(upper.speed_curve, original.speed_curve); assert.notEqual(upper.speed_curve, value.clips[1].speed_curve);
  upper.speed_curve[1].speed = 1; assert.equal(value.clips[1].speed_curve[1].speed, 4); assert.equal(detached.speed_curve[1].speed, 4);
});

for (const kind of ["video", "audio", "overlay"]) test(`editing a ${kind} curve recomputes duration and fades, while returning to fixed speed clears only the curve`, () => {
  const value = project({ audio_clips: [audio()], overlays: [layer()] });
  const item = kind === "video" ? value.clips[1] : kind === "audio" ? value.audio_clips[0] : value.overlays[0];
  Object.assign(item, { fade_in: 2, fade_out: 1 }); validateProject(value, media()); const original = clone(value);
  const updated = transformClipSpeed(value, kind, item.id, { speed_curve: [{ time: 0, speed: 4 }, { time: 10, speed: 4 }] }, media());
  close(duration(updated.clip), 2); close(updated.clip.fade_in, 4 / 3); close(updated.clip.fade_out, 2 / 3);
  assert.equal(updated.clip.in, item.in); assert.equal(updated.clip.out, item.out); assert.equal(updated.clip.volume, item.volume);
  if (kind === "overlay") close(updated.clip.end, item.start + 2);
  const fixed = transformClipSpeed(updated.project, kind, item.id, { speed: 2, speed_curve: [] }, media());
  assert.equal(Object.hasOwn(fixed.clip, "speed_curve"), false); close(duration(fixed.clip), 4);
  if (kind === "overlay") close(fixed.clip.end, item.start + 4);
  assert.deepEqual(value, original);
});

test("one curve drag creates one undo step, preserves a newer save revision and cancels stale concurrent edits", () => {
  const session = new ProjectSession(project()), original = clone(session.project), drag = new SpeedCurveTransaction(session, "video", "ramp", media());
  for (let i = 0; i < 30; i++) { const next = points(); next[1].speed = 1 + i / 30; drag.update(next); }
  assert.deepEqual(session.project, original); assert.equal(session.undoStack.length, 0);
  session.project.updated_at = "r2";
  assert.equal(drag.commit(), true); assert.equal(session.undoStack.length, 1); assert.equal(session.project.updated_at, "r2");
  assert.throws(() => drag.update(points()), /結束/); assert.equal(drag.commit(), false);
  assert.equal(session.travel(true), true); assert.equal(signature(session.project), signature(original)); assert.equal(session.project.updated_at, "r2");
  const cancelled = new SpeedCurveTransaction(session, "video", "ramp", media()); cancelled.update([{ time: 0, speed: 4 }, { time: 10, speed: 4 }]);
  assert.equal(cancelled.cancel(), false); assert.equal(signature(session.project), signature(original));
  const stale = new SpeedCurveTransaction(session, "video", "ramp", media()); stale.update([{ time: 0, speed: 4 }, { time: 10, speed: 4 }]);
  session.change(value => { value.name = "另一筆編輯"; });
  assert.throws(() => stale.commit(), /變更/); assert.equal(session.project.name, "另一筆編輯"); assert.deepEqual(session.project.clips[1].speed_curve, original.clips[1].speed_curve);
});

class FakeMedia {
  constructor() { this.listeners = new Map(); this.readyState = 0; this.duration = 20; this._time = 0; this.seeking = false; this.paused = true; this.loads = 0; this.seeks = []; this.playCalls = []; this.style = {}; }
  addEventListener(name, callback) { const listeners = this.listeners.get(name) || []; listeners.push(callback); this.listeners.set(name, listeners); }
  emit(name) { for (const callback of this.listeners.get(name) || []) callback(); }
  get currentTime() { return this._time; }
  set currentTime(value) { this._time = value; this.seeks.push(value); this.seeking = true; this.readyState = 1; }
  load() { this.loads++; this.readyState = 0; this._time = 0; this.seeking = false; }
  metadata() { this.readyState = 1; this.emit("loadedmetadata"); }
  finishSeek() { this.readyState = 4; this.seeking = false; this.emit("seeked"); this.emit("canplay"); }
  play() { this.paused = false; this.playCalls.push(this.currentTime); return Promise.resolve(); }
  pause() { this.paused = true; }
  removeAttribute(name) { if (name === "src") this.src = ""; }
}
function context() {
  const node = () => ({ connect() {}, disconnect() {} });
  return { destination: {}, createMediaElementSource() { return node(); }, createGain() { return { ...node(), gain: { value: 1 } }; } };
}

test("VideoDeck uses the initial source-point speed and invalidates its cached curve key when anchors change", () => {
  const value = project(), elements = [new FakeMedia(), new FakeMedia()], deck = new VideoDeck(elements), point = locateTime(value, 1.75);
  deck.request(point, "/source.mp4", true); const selected = elements[deck.pending.index];
  close(selected.playbackRate, speedAtSource(point.clip, point.sourceTime)); assert.equal(selected.preservesPitch, true);
  selected.metadata(); close(selected.currentTime, point.sourceTime); selected.finishSeek(); assert.equal(deck.pending, null);
  const changed = clone(point); changed.clip.speed_curve[1].speed = 2;
  assert.notEqual(deck.key(changed), deck.key(point));
  assert.equal(selected.paused, false);
});

test("AudioPreview follows variable source position and updates instantaneous speed without reloading the same media", () => {
  const voice = audio(), value = project({ audio_clips: [voice] }), elements = [];
  const mixer = new AudioPreview(() => { const element = new FakeMedia(); elements.push(element); return element; }, context());
  const at = voice.start + 0.7; assert.equal(mixer.sync(value, media(), at, true), false);
  const element = elements[0]; close(element.playbackRate, speedAtSource(voice, sourceAt(voice, 0.7)));
  element.metadata(); mixer.sync(value, media(), at, true); close(element.currentTime, sourceAt(voice, 0.7)); element.finishSeek();
  assert.equal(mixer.sync(value, media(), at, true), true); const loads = element.loads, seeks = element.seeks.length;
  const later = at + 0.02; element._time = sourceAt(voice, later - voice.start);
  assert.equal(mixer.sync(value, media(), later, true), true); close(element.playbackRate, speedAtSource(voice, element.currentTime));
  assert.equal(element.loads, loads); assert.equal(element.seeks.length, seeks); assert.equal(element.preservesPitch, true);
  const edited = clone(value); edited.audio_clips[0].speed_curve[1].speed = 2;
  assert.equal(mixer.sync(edited, media(), at, false), false); close(element.currentTime, sourceAt(edited.audio_clips[0], at - voice.start));
  assert.equal(element.loads, loads); mixer.clear(); assert.equal(mixer.slots.size, 0);
});

test("VideoLayersController follows a curve independently of V1 and updates its source-rate mapping without decoder reload", () => {
  const upper = layer(), element = new FakeMedia(), controller = new VideoLayersController(() => element, { context: context(), resolveUrl: () => "/source.mp4" });
  const at = upper.start + 0.7; assert.equal(controller.sync([upper], at, true), false);
  element.metadata(); controller.sync([upper], at, true); close(element.currentTime, sourceAt(upper, 0.7)); element.finishSeek();
  assert.equal(controller.sync([upper], at, true), true); close(element.playbackRate, speedAtSource(upper, element.currentTime));
  const loads = element.loads, seeks = element.seeks.length, later = at + 0.02; element._time = sourceAt(upper, later - upper.start);
  assert.equal(controller.sync([upper], later, true), true); close(element.playbackRate, speedAtSource(upper, element.currentTime));
  assert.equal(element.loads, loads); assert.equal(element.seeks.length, seeks);
  const edited = clone(upper); edited.speed_curve[1].speed = 2; edited.end = edited.start + duration(edited);
  assert.equal(controller.sync([edited], at, false), false); close(element.currentTime, sourceAt(edited, at - edited.start));
  assert.equal(element.loads, loads); controller.clear(); assert.equal(controller.slots.size, 0);
});

test("the shipped V1 clock inverts curved source time and updates the live playback rate each animation frame", () => {
  const value = project(), ramp = value.clips[1], video = new FakeMedia(); video.readyState = 4; video.paused = false;
  const state = { playing: true, playhead: 1, previewIndex: 1, lastPlaybackTick: null, buffering: false, audioBuffering: false, layerBuffering: false, media: media(), audio: null };
  const deck = { pending: null, held: false, hold(value) { this.held = value; } }, videoLayers = { sync: () => true, pause() {} };
  const environment = vm.createContext({ ...core, state, deck, video, videoLayers, animationMath: require("../static/editor_animations.js"), project: () => value,
    syncTransitionPreview: () => true, transitionPreview: { pause() {} },
    nextPreviewClip() { throw new Error("Unexpected early clip transition"); }, updatePlayhead() {}, renderPlaybackStatus() {},
    requestAnimationFrame() {}, $: () => ({}),
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
  for (const name of ["applyVolume", "playbackTick"]) {
    const start = source.search(new RegExp(`  (?:async )?function ${name}\\(`)), rest = source.slice(start), following = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
    assert.ok(start >= 0 && following >= 0); vm.runInContext(rest.slice(0, following + 1), environment);
  }
  let previous = -1;
  for (const [index, sourceTime] of [2, 4, 6, 8].entries()) {
    video._time = sourceTime; environment.playbackTick(index * 20);
    close(state.playhead, 1 + timelineAt(ramp, sourceTime)); close(video.playbackRate, speedAtSource(ramp, sourceTime));
    assert.ok(state.playhead > previous); previous = state.playhead;
  }
  state.buffering = true; video._time = 8.5; environment.playbackTick(100); close(state.playhead, previous);
  state.buffering = false; environment.playbackTick(120); close(state.playhead, 1 + timelineAt(ramp, 8.5));
});

test("non-video overlays reject nonempty speed curves while empty legacy fields are omitted", () => {
  const image = { id: "image-overlay", kind: "image", media_id: "image", start: 0, end: 3, x: 0.5, y: 0.5, width: 0.4, rotation: 0, opacity: 1, speed_curve: points() };
  assert.throws(() => validateProject(project({ overlays: [image] }), media()), /曲線/);
  image.speed_curve = []; assert.doesNotThrow(() => validateProject(project({ overlays: [image] }), media()));
  assert.ok(!Object.hasOwn(canonicalOverlay(image), "speed_curve"));
});
