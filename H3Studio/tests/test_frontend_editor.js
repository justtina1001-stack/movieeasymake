"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  clone, duration, totalDuration, signature, locateTime, validateProject,
  splitAt, reorder, ProjectSession, speedOf, audioGain, audioClips, freeAudioTrack,
  splitAudioAt, detachAudio, audioPreviewCandidates, VideoDeck, AudioPreview, requireCapabilities,
} = require("../static/editor.js");

const project = () => ({ id: "project-a", name: "測試", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [
    { id: "one", media_id: "shared", in: 2, out: 4, volume: 1 },
    { id: "two", media_id: "shared", in: 7, out: 10, volume: 1.5 },
  ],
});
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

test("sequential preview maps exact boundaries to the next source in-point, including repeated media", () => {
  const value = project();
  assert.equal(locateTime(value, -10).sourceTime, 2);
  assert.equal(locateTime(value, 1.25).sourceTime, 3.25);
  assert.equal(locateTime(value, 2).index, 1);
  assert.equal(locateTime(value, 2).sourceTime, 7);
  assert.equal(locateTime(value, 99).sourceTime, 10);
  assert.equal(locateTime({ ...value, clips: [] }, 0), null);
});

test("splitting a trimmed clip preserves total duration, source continuity and audio gain", () => {
  const value = project(), total = totalDuration(value);
  const selected = splitAt(value, 2.75, () => "new");
  assert.equal(selected, "new");
  assert.equal(value.clips.length, 3);
  assert.deepEqual(value.clips[1], { id: "two", media_id: "shared", in: 7, out: 7.75, volume: 1.5 });
  assert.deepEqual(value.clips[2], { id: "new", media_id: "shared", in: 7.75, out: 10, volume: 1.5 });
  assert.equal(totalDuration(value), total);
});

test("a split rejects either remainder shorter than one frame and accepts exact one-frame segments", () => {
  const value = project(), before = clone(value);
  for (const at of [0, 1 / 48, 2, 2 - 1 / 48, 5]) assert.throws(() => splitAt(value, at), /影格/);
  assert.deepEqual(value, before);
  splitAt(value, 1 / 24, () => "single-frame");
  assert.ok(Math.abs(duration(value.clips[0]) - 1 / 24) < 1e-12);
});

test("clip limits reject nonfinite, too-short, duplicate, excess-duration and missing-source data atomically", () => {
  const session = new ProjectSession(project()), before = clone(session.project);
  const invalid = [
    p => { p.clips[0].in = NaN; },
    p => { p.clips[0].out = p.clips[0].in + 0.02; },
    p => { p.clips[0].volume = 2.01; },
    p => { p.clips.push({ ...p.clips[0] }); },
    p => { p.clips[0].out = 605; },
    p => { p.clips = Array.from({ length: 51 }, (_, n) => ({ ...p.clips[0], id: String(n) })); },
  ];
  for (const mutate of invalid) {
    assert.throws(() => session.change(mutate));
    assert.deepEqual(session.project, before);
    assert.equal(session.undoStack.length, 0);
  }
  assert.throws(() => validateProject(before, new Map()), /找不到/);
  assert.throws(() => validateProject(before, new Map([["shared", { duration: 9 }]])), /來源影片/);
});

test("source duration is preserved at full precision when no trim was requested", () => {
  const value = project(); value.clips = [{ ...value.clips[0], in: 0, out: 124 / 24 }];
  validateProject(value, new Map([["shared", { duration: 124 / 24 }]]));
  const session = new ProjectSession(value);
  session.change(p => { p.clips[0].volume = 2; });
  assert.equal(session.project.clips[0].out, 124 / 24);
});

test("drag reorder handles forward/backward insertion without losing or duplicating clips", () => {
  const value = project(); value.clips.push({ ...value.clips[0], id: "three" });
  reorder(value, "one", "three", true);
  assert.deepEqual(value.clips.map(c => c.id), ["two", "three", "one"]);
  reorder(value, "one", "two", false);
  assert.deepEqual(value.clips.map(c => c.id), ["one", "two", "three"]);
  reorder(value, "one", "one", true);
  assert.equal(totalDuration(value), 7);
});

test("undo and redo retain the latest server revision after a successful save", async () => {
  const session = new ProjectSession(project());
  session.change(p => { p.name = "改名"; });
  await session.save(async snapshot => ({ ...snapshot, updated_at: "r2" }));
  session.travel(true);
  assert.equal(session.project.name, "測試"); assert.equal(session.project.updated_at, "r2"); assert.equal(session.dirty, true);
  const revisions = [];
  await session.save(async snapshot => { revisions.push(snapshot.updated_at); return { ...snapshot, updated_at: "r3" }; });
  session.travel(false);
  assert.equal(session.project.name, "改名"); assert.equal(session.project.updated_at, "r3");
  assert.deepEqual(revisions, ["r2"]);
});

test("typing history coalesces, but a later edit after undo clears the redo branch", () => {
  const session = new ProjectSession(project());
  session.change(p => { p.name = "A"; }, "name");
  session.change(p => { p.name = "AB"; }, "name");
  assert.equal(session.undoStack.length, 1);
  session.travel(true); assert.equal(session.project.name, "測試");
  session.change(p => { p.name = "另一個名稱"; });
  assert.equal(session.redoStack.length, 0);
});

test("concurrent saves share one promise, and editing during save drains the newest snapshot with its current revision", async () => {
  const session = new ProjectSession(project()), pending = [], snapshots = [];
  const write = snapshot => { snapshots.push(snapshot); const next = deferred(); pending.push(next); return next.promise; };
  session.change(p => { p.name = "第一版"; });
  const first = session.save(write), second = session.save(write);
  assert.equal(first, second); assert.equal(snapshots.length, 1);
  session.change(p => { p.name = "第二版"; });
  pending[0].resolve({ ...snapshots[0], updated_at: "r2" }); await flush();
  assert.equal(snapshots.length, 2);
  assert.equal(snapshots[1].name, "第二版"); assert.equal(snapshots[1].updated_at, "r2");
  pending[1].resolve({ ...snapshots[1], updated_at: "r3" });
  const saved = await first;
  assert.equal(saved.name, "第二版"); assert.equal(session.project.updated_at, "r3");
  assert.equal(session.dirty, false); assert.equal(session.inFlight, null);
});

test("an awaited save does not finish early when the export snapshot was edited during an existing save", async () => {
  const session = new ProjectSession(project()), pending = [], sent = [];
  session.change(p => { p.clips[0].volume = 0.5; });
  const write = snapshot => { sent.push(snapshot); const next = deferred(); pending.push(next); return next.promise; };
  const background = session.save(write);
  session.change(p => { p.clips[0].out = 3; });
  let readyForExport = false;
  const exportSave = session.save(write).then(() => { readyForExport = true; });
  pending[0].resolve({ ...sent[0], updated_at: "r2" }); await flush();
  assert.equal(readyForExport, false);
  assert.equal(sent[1].clips[0].out, 3);
  pending[1].resolve({ ...sent[1], updated_at: "r3" }); await Promise.all([background, exportSave]);
  assert.equal(readyForExport, true); assert.equal(session.dirty, false);
});

test("a stale project response remains scoped to its session after switching projects", async () => {
  const first = new ProjectSession(project()), secondValue = project(); secondValue.id = "project-b";
  const second = new ProjectSession(secondValue), pending = deferred();
  first.change(p => { p.name = "舊專案仍在儲存"; });
  const save = first.save(snapshot => pending.promise.then(() => ({ ...snapshot, updated_at: "r2" })));
  second.change(p => { p.name = "目前專案"; }); const expected = clone(second.project);
  pending.resolve(); await save;
  assert.deepEqual(second.project, expected); assert.equal(second.dirty, true);
  assert.equal(first.dirty, false);
});

test("409 is retained and surfaced to every save caller without overwriting the local draft", async () => {
  const session = new ProjectSession(project()), pending = deferred();
  session.change(p => { p.name = "保留這份草稿"; }); const expected = clone(session.project);
  const first = session.save(() => pending.promise), second = session.save(() => assert.fail("must not make another request"));
  const conflict = Object.assign(new Error("專案已更新"), { status: 409, data: { project: { ...project(), updated_at: "r99" } } });
  pending.reject(conflict);
  const results = await Promise.allSettled([first, second]);
  assert.ok(results.every(result => result.status === "rejected" && result.reason === conflict));
  assert.deepEqual(session.project, expected); assert.equal(session.conflict, conflict); assert.equal(session.dirty, true);
  await assert.rejects(session.save(() => assert.fail("conflict must require resolution")), error => error === conflict);
});

test("ordinary save failures keep edits and allow a later retry", async () => {
  const session = new ProjectSession(project()); session.change(p => { p.name = "網路中断草稿"; });
  await assert.rejects(session.save(async () => { throw new Error("offline"); }), /offline/);
  assert.equal(session.inFlight, null); assert.equal(session.conflict, null); assert.equal(session.dirty, true);
  await session.save(async snapshot => ({ ...snapshot, updated_at: "r2" }));
  assert.equal(session.dirty, false);
});

test("server revisions alone do not change editable content signatures", () => {
  const value = project(); assert.equal(signature(value), signature({ ...value, updated_at: "other", id: "other-project" }));
});

// Exercise the shipped browser functions with a controllable media element. The
// core logic is not copied into the fixture, so playback race regressions fail here.
const browserSource = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
function loadBrowserFunction(context, name) {
  const start = browserSource.search(new RegExp(`  (?:async )?function ${name}\\(`));
  assert.notEqual(start, -1, `Production function ${name} must exist`);
  const rest = browserSource.slice(start);
  const following = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
  assert.notEqual(following, -1, `Production function ${name} must have a following boundary`);
  vm.runInContext(rest.slice(0, following + 1), context);
}
class FakeMedia {
  constructor() { this.listeners = new Map(); this.readyState = 0; this.duration = 30; this._time = 0; this.seeking = false; this.paused = true; this.loads = 0; this.seeks = []; this.playCalls = []; this.pending = []; this.controlled = false; }
  addEventListener(name, fn) { const list = this.listeners.get(name) || []; list.push(fn); this.listeners.set(name, list); }
  emit(name) { for (const fn of this.listeners.get(name) || []) fn(); }
  get currentTime() { return this._time; }
  set currentTime(value) { this._time = value; this.seeks.push(value); this.seeking = true; this.readyState = 1; }
  load() { this.loads++; this.readyState = 0; this._time = 0; this.seeking = false; }
  metadata() { this.readyState = 1; this.emit("loadedmetadata"); }
  finishSeek() { this.readyState = 4; this.seeking = false; this.emit("seeked"); this.emit("canplay"); }
  play() { this.paused = false; this.playCalls.push(this.currentTime); if (!this.controlled) return Promise.resolve(); const call = deferred(); this.pending.push(call); return call.promise; }
  pause() { this.paused = true; }
  removeAttribute() { this.src = ""; }
}
function deckFixture() {
  const elements = [new FakeMedia(), new FakeMedia()], errors = [], buffering = [];
  const deck = new VideoDeck(elements, { error: error => errors.push(error.message), buffering: value => buffering.push(value) });
  const value = project(), first = locateTime(value, 0), second = locateTime(value, 2);
  return { deck, elements, errors, buffering, first, second, value };
}
function prepared(deck, elements, point, url = "/source", play = true) {
  deck.request(point, url, play); const element = elements[deck.pending.index]; element.metadata(); element.finishSeek(); return element;
}

test("dual buffers pre-seek the next cut, including another in-point in the same file, then swap without loading", () => {
  const { deck, elements, first, second } = deckFixture();
  const active = prepared(deck, elements, first);
  deck.preload(second, "/source"); const next = elements[1 - deck.activeIndex]; next.metadata();
  assert.equal(next.currentTime, 7); assert.equal(next.playCalls.length, 0); assert.equal(deck.active, active);
  next.finishSeek(); const loads = elements.map(element => element.loads);
  deck.request(second, "/source", true);
  assert.equal(deck.active, next); assert.deepEqual(next.playCalls, [7]); assert.deepEqual(elements.map(element => element.loads), loads);
  assert.equal(active.hidden, true); assert.equal(next.hidden, false);
});

test("metadata is insufficient: no playback or buffer swap before the requested seek has completed", () => {
  const { deck, elements, first } = deckFixture(); deck.request(first, "/source", true);
  const pending = elements[deck.pending.index]; pending.metadata(); pending.readyState = 4; pending.emit("canplay");
  assert.equal(pending.playCalls.length, 0); assert.ok(deck.pending);
  pending.finishSeek(); assert.equal(pending.playCalls.length, 1); assert.equal(deck.pending, null);
});

test("rapid requests use the latest trim and speed even if older metadata arrives later", () => {
  const { deck, elements, first } = deckFixture(); deck.request(first, "/original", false);
  const modified = { ...first, clip: { ...first.clip, in: 5, out: 9, speed: 2 }, sourceTime: 5.1 };
  deck.request(modified, "/proxy", true); const pending = elements[deck.pending.index]; pending.metadata();
  assert.equal(pending.currentTime, 5.1); assert.equal(pending.playbackRate, 2);
  pending.finishSeek(); assert.deepEqual(pending.playCalls, [5.1]); assert.equal(deck.buffers[deck.activeIndex].url, "/proxy");
});

test("a precise 50ms seek in a ready clip is not incorrectly treated as an already cached position", () => {
  const { deck, elements, first } = deckFixture(); const active = prepared(deck, elements, first, "/source", false);
  deck.request({ ...first, sourceTime: 2.05 }, "/source", true);
  assert.equal(active.currentTime, 2.05); assert.ok(deck.pending); assert.equal(active.playCalls.length, 0);
  active.finishSeek(); assert.deepEqual(active.playCalls, [2.05]);
});

test("a failed background buffer reloads on demand rather than buffering forever", () => {
  const { deck, elements, first, second } = deckFixture(); prepared(deck, elements, first);
  deck.preload(second, "/bad-proxy"); const next = elements[1 - deck.activeIndex]; next.emit("error");
  const count = next.loads; deck.request(second, "/bad-proxy", true); assert.equal(next.loads, count + 1);
  next.metadata(); next.finishSeek(); assert.equal(deck.active, next); assert.equal(deck.pending, null);
});

test("holding for audio invalidates a pending play attempt, even if its rejection arrives after resume", async () => {
  const { deck, elements, errors, first } = deckFixture(); elements.forEach(element => element.controlled = true);
  const active = prepared(deck, elements, first); const old = active.pending[0];
  deck.hold(true); deck.hold(false); assert.equal(active.pending.length, 2);
  old.reject(Object.assign(new Error("interrupted by pause"), { name: "AbortError" })); await flush();
  assert.deepEqual(errors, []); assert.equal(deck.wantsPlay, true); active.pending[1].resolve();
});

test("an old play rejection after a new seek cannot stop newer playback; current failures are reported", async () => {
  const { deck, elements, errors, first } = deckFixture(); elements.forEach(element => element.controlled = true);
  const active = prepared(deck, elements, first); const old = active.pending[0];
  deck.request({ ...first, sourceTime: 3 }, "/source", true); active.finishSeek();
  old.reject(new Error("old error")); await flush(); assert.deepEqual(errors, []); assert.equal(deck.wantsPlay, true);
  active.pending[1].reject(new Error("current error")); await flush(); assert.deepEqual(errors, ["current error"]); assert.equal(deck.wantsPlay, false);
});

const audioClip = (extra = {}) => ({ id: "music", media_id: "audio", in: 0, out: 2, start: 0, track: 0, volume: 1, speed: 1, fade_in: 0, fade_out: 0, ...extra });
test("speed changes timeline duration, seek mapping and split source coordinates", () => {
  const value = project(); value.clips[0].speed = 2; value.clips[1].speed = 0.5;
  assert.equal(totalDuration(value), 7); assert.equal(locateTime(value, 0.5).sourceTime, 3); assert.equal(locateTime(value, 2).sourceTime, 7.5);
  splitAt(value, 0.5, () => "speed-split"); assert.equal(value.clips[1].in, 3); assert.equal(value.clips[1].speed, 2); assert.equal(totalDuration(value), 7);
});

test("audio validates source kinds, finite controls, fades, four nonoverlapping tracks and atomic conflicts", () => {
  const value = project(); value.audio_clips = [audioClip()];
  const media = new Map([["shared", { duration: 12, has_audio: true }], ["audio", { kind: "audio", duration: 8 }]]);
  validateProject(value, media); const session = new ProjectSession(value), before = clone(session.project);
  for (const mutate of [p => p.audio_clips[0].start = -1, p => p.audio_clips[0].fade_in = Infinity, p => p.audio_clips[0].fade_out = 3, p => p.audio_clips[0].track = 4, p => p.audio_clips[0].speed = 4.1,
    p => p.audio_clips.push(audioClip({ id: "overlap", start: 1 }))]) { assert.throws(() => session.change(mutate)); assert.deepEqual(session.project, before); }
  value.audio_clips.push(audioClip({ id: "across", track: 1 })); validateProject(value, media);
  value.audio_clips.push(audioClip({ id: "touching", start: 2 })); validateProject(value, media);
  assert.throws(() => validateProject({ ...value, clips: [{ ...value.clips[0], media_id: "audio" }] }, media), /主影片軌只接受影片/);
});

test("audio fades use timeline seconds and split retains speed, placement and outer fades", () => {
  const value = project(); value.audio_clips = [audioClip({ in: 2, out: 10, start: 1, speed: 2, fade_in: 0.5, fade_out: 1, volume: 0.8 })];
  assert.equal(audioGain(value.audio_clips[0], 1.25), 0.4); assert.equal(audioGain(value.audio_clips[0], 4.5), 0.4); assert.equal(audioGain(value.audio_clips[0], 5), 0);
  splitAudioAt(value, "music", 3, () => "tail"); assert.equal(value.audio_clips[0].out, 6); assert.equal(value.audio_clips[1].in, 6); assert.equal(value.audio_clips[1].start, 3);
  assert.equal(value.audio_clips[0].fade_in, 0.5); assert.equal(value.audio_clips[0].fade_out, 0); assert.equal(value.audio_clips[1].fade_out, 1);
});

test("detach preserves a trimmed clip's speed, source coordinates, existing mute and timeline position", () => {
  const value = project(); value.clips[0].speed = 2; value.clips[1].speed = 0.5; value.clips[1].volume = 0;
  detachAudio(value, "two", () => "detached"); const clip = value.audio_clips[0];
  assert.equal(clip.start, 1); assert.equal(clip.speed, 0.5); assert.equal(clip.in, 7); assert.equal(clip.out, 10); assert.equal(clip.volume, 0); assert.equal(value.clips[1].volume, 0);
  value.audio_clips = [0, 1, 2, 3].map(track => audioClip({ id: String(track), track }));
  assert.throws(() => freeAudioTrack(value, 0.5, 1), /四條/); assert.equal(freeAudioTrack(value, 2, 1), 0);
});

test("old projects default to normal speed/no extra audio; audio edits participate in save undo redo and stale-backend protection", async () => {
  const session = new ProjectSession(project()); assert.equal(speedOf(session.project.clips[0]), 1); assert.deepEqual(audioClips(session.project), []); assert.equal(session.dirty, false);
  session.change(p => { p.clips[0].speed = 2; p.audio_clips.push(audioClip()); });
  await assert.rejects(session.save(async snapshot => ({ ...snapshot, clips: project().clips, audio_clips: undefined, updated_at: "r2" })), /後端/);
  assert.equal(session.dirty, true); await session.save(async snapshot => ({ ...snapshot, updated_at: "r3" }));
  session.travel(true); assert.deepEqual(session.project.audio_clips, []); assert.equal(speedOf(session.project.clips[0]), 1);
  session.travel(false); assert.equal(session.project.audio_clips.length, 1); assert.equal(session.project.updated_at, "r3");
});

test("capability checks reject old backends explicitly, preserve network failures and accept schema v2", async () => {
  await assert.rejects(requireCapabilities(async () => { throw Object.assign(new Error("404"), { status: 404 }); }), /重新啟動/);
  await assert.rejects(requireCapabilities(async () => ({ schema_version: 1 })), /重新啟動/);
  const offline = new Error("offline"); await assert.rejects(requireCapabilities(async () => { throw offline; }), error => error === offline);
  assert.equal((await requireCapabilities(async () => ({ schema_version: 2 }))).schema_version, 2);
});

function audioFixture(value) {
  const elements = [], errors = [], context = { destination: {}, createMediaElementSource: () => ({ connect() {}, disconnect() {} }), createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }) };
  const mixer = new AudioPreview(() => { const element = new FakeMedia(); elements.push(element); return element; }, context, error => errors.push(error.message));
  const media = new Map([["audio", { url: "/sound" }]]); return { mixer, elements, media, errors };
}
test("audio preview preloads only current and next per track, even with 50 tiny clips nearby", () => {
  const value = project(); value.audio_clips = Array.from({ length: 50 }, (_, index) => audioClip({ id: String(index), start: index / 24, out: 1 / 24 }));
  const { mixer, elements, media } = audioFixture(value); mixer.sync(value, media, 0, false);
  assert.equal(audioPreviewCandidates(value, 0).length, 2); assert.equal(elements.length, 2);
  mixer.sync(value, media, 0.25, false); assert.equal(mixer.slots.size, 2);
});

test("initial audio trim under 100ms seeks exactly and waits for seeked; gain changes never reload or seek", () => {
  const value = project(); value.audio_clips = [audioClip({ in: 0.05, out: 1.05, fade_in: 0.5 })];
  const { mixer, elements, media } = audioFixture(value); assert.equal(mixer.sync(value, media, 0, true), false);
  const sound = elements[0]; sound.metadata(); assert.equal(mixer.sync(value, media, 0, true), false);
  assert.equal(sound.currentTime, 0.05); assert.equal(sound.playCalls.length, 0); sound.finishSeek();
  assert.equal(mixer.sync(value, media, 0, true), true); assert.deepEqual(sound.playCalls, [0.05]);
  const seeks = sound.seeks.length, loads = sound.loads; value.audio_clips[0].volume = 0.5;
  mixer.sync(value, media, 0, true); assert.equal(sound.seeks.length, seeks); assert.equal(sound.loads, loads);
  assert.equal(mixer.slots.get("music").gain.gain.value, 0);
  sound._time = 0.3; mixer.sync(value, media, 0.25, true); assert.equal(mixer.slots.get("music").gain.gain.value, 0.25);
  mixer.seek(); mixer.sync(value, media, 0.02, false); assert.equal(sound.currentTime, 0.07);
});

test("rapid scrub input is coalesced to one seek per animation frame", () => {
  const calls = [], scheduled = [], context = vm.createContext({ state: { queuedSeek: null, playing: false }, requestAnimationFrame: fn => scheduled.push(fn), seek: (...args) => calls.push(args) });
  loadBrowserFunction(context, "applyPendingSeek"); loadBrowserFunction(context, "queueSeek");
  context.queueSeek(1); context.queueSeek(2); context.queueSeek(3.25);
  assert.equal(scheduled.length, 1); scheduled[0](); assert.deepEqual(calls, [[3.25, false]]);
});

test("volume-only edits update live gain and never pause, seek or load the media", () => {
  const session = new ProjectSession(project()), state = { session, selected: "one", selectedKind: "video", playhead: 0.5, playing: true };
  let pauses = 0, seeks = 0, renders = 0; const gains = [];
  const context = vm.createContext({ state, locked: () => false, project: () => session.project,
    selectedClip: () => session.project.clips[0], clamp: (value, low, high) => Math.max(low, Math.min(high, value)),
    totalDuration, locateTime, render: () => { renders++; }, pause: () => { pauses++; }, seek: () => { seeks++; }, applyVolume: clip => gains.push(clip.volume) });
  loadBrowserFunction(context, "edit"); context.edit(value => { value.clips[0].volume = 0.6; }, null, false);
  assert.equal(pauses, 0); assert.equal(seeks, 0); assert.equal(state.playing, true); assert.deepEqual(gains, [0.6]); assert.equal(renders, 1);
});

test("capability failure stops boot before project access and leaves backend editing locked", async () => {
  const state = { backendReady: false }, paths = [];
  const context = vm.createContext({ state, renderDisabled() {}, requireCapabilities, api: async path => { paths.push(path); throw Object.assign(new Error("missing"), { status: 404 }); } });
  // boot is the final declaration before event binding; isolate its body at the
  // known event-binding boundary rather than duplicating its implementation.
  const start = browserSource.indexOf("  async function boot() {"), end = browserSource.indexOf('\n  $("dismissNotice")', start);
  vm.runInContext(browserSource.slice(start, end), context);
  await assert.rejects(context.boot(), /重新啟動/); assert.equal(state.backendReady, false); assert.deepEqual(paths, ["/api/editor/capabilities"]);
});

test("browser save completion cannot re-render an old project after a session switch", async () => {
  const previous = new ProjectSession(project()), nextProject = project(); nextProject.id = "other";
  const current = new ProjectSession(nextProject), pending = deferred();
  previous.change(p => { p.name = "舊專案儲存中"; });
  const state = { session: previous, media: new Map([["shared", { duration: 12 }]]), projects: [project(), nextProject] };
  let renderCount = 0;
  const context = vm.createContext({ state, validateProject, json: (method, snapshot) => snapshot,
    api: (_path, snapshot) => pending.promise.then(() => ({ ...snapshot, updated_at: "r2" })),
    render: () => { renderCount++; }, renderStatus() {},
  });
  loadBrowserFunction(context, "saveProject");
  const saving = context.saveProject(); state.session = current;
  current.change(p => { p.name = "目前正在編輯"; });
  pending.resolve(); await saving;
  assert.equal(state.session, current); assert.equal(state.session.project.name, "目前正在編輯");
  assert.equal(renderCount, 0); assert.equal(previous.project.updated_at, "r2");
});
