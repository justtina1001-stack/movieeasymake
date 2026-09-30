"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { clone, duration, totalDuration, validateProject, signature, ProjectSession, trimTimelineClip, TrimTransaction } = require("../static/editor.js");

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const media = () => new Map([
  ["video", { kind: "video", duration: 12, has_audio: true }],
  ["sound", { kind: "audio", duration: 20 }],
]);
const project = () => ({ id: "trim-test", name: "邊界拖曳", updated_at: "r1", width: 1280, height: 720, fps: 24,
  clips: [
    { id: "v1", media_id: "video", in: 2, out: 6, speed: 2, volume: 1 },
    { id: "v2", media_id: "video", in: 7, out: 10, speed: 1, volume: 0.5 },
  ],
  audio_clips: [
    { id: "a1", media_id: "sound", in: 2, out: 8, speed: 2, start: 3, track: 0, volume: 0.7, fade_in: 0.6, fade_out: 0.4 },
  ],
});
const trim = (value, kind, id, edge, delta, sources = media()) => trimTimelineClip(value, { kind, id, edge, delta }, sources);

test("video left trim converts timeline distance through speed and leaves independent audio in place", () => {
  const value = project(), original = clone(value), result = trim(value, "video", "v1", "left", 0.5);
  near(result.clip.in, 3); near(result.clip.out, 6); near(duration(result.clip), 1.5);
  near(totalDuration(result.project), 4.5);
  assert.deepEqual(value, original);
  assert.deepEqual(result.project.clips[1], original.clips[1]);
  assert.deepEqual(result.project.audio_clips, original.audio_clips);
  assert.equal(result.changed, true); validateProject(result.project, media());
});

test("slow video trim moves source time by only a quarter of timeline time", () => {
  const value = project(); value.clips[0].speed = 0.25;
  const result = trim(value, "video", "v1", "left", 1);
  near(result.clip.in, 2.25); near(duration(result.clip), 15);
});

test("both video edges extend only to existing source content", () => {
  const value = project();
  const left = trim(value, "video", "v1", "left", -100);
  near(left.clip.in, 0); near(left.clip.out, 6); assert.equal(left.clamped, true);
  const right = trim(value, "video", "v1", "right", 100);
  near(right.clip.in, 2); near(right.clip.out, 12); assert.equal(right.clamped, true);
  validateProject(left.project, media()); validateProject(right.project, media());
});

test("shrinking either edge at four times speed keeps at least one output frame", () => {
  const value = project(); value.fps = 60; value.clips[0].speed = 4;
  for (const [edge, delta] of [["left", 100], ["right", -100]]) {
    const result = trim(value, "video", "v1", edge, delta);
    near(duration(result.clip), 1 / 60); near(result.clip.out - result.clip.in, 4 / 60);
    validateProject(result.project, media());
  }
});

test("extension preserves a source endpoint that is not a rounded decimal", () => {
  const value = project(); value.clips = [{ ...value.clips[0], in: 0, out: 4, speed: 1 }];
  const sources = media(); sources.get("video").duration = 124 / 24;
  const result = trim(value, "video", "v1", "right", 100, sources);
  assert.equal(result.clip.out, 124 / 24);
});

test("video extension clamps at the ten-minute project limit from either edge", () => {
  const value = project();
  value.clips = [{ ...value.clips[0], in: 10, out: 20, speed: 1 }, { ...value.clips[1], in: 0, out: 589 }];
  const sources = media(); sources.get("video").duration = 1000;
  for (const [edge, delta] of [["left", -100], ["right", 100]]) {
    const result = trim(value, "video", "v1", edge, delta, sources);
    near(totalDuration(result.project), 600); near(duration(result.clip), 11);
    validateProject(result.project, sources);
  }
});

test("audio left trim moves start and source in-point while the opposite edge stays fixed", () => {
  const value = project(), result = trim(value, "audio", "a1", "left", 0.5);
  near(result.clip.start, 3.5); near(result.clip.in, 3); near(result.clip.out, 8);
  near(result.clip.start + duration(result.clip), 6);
  near(result.clip.speed, 2); near(result.clip.volume, 0.7);
  assert.deepEqual(result.project.clips, value.clips);
});

test("audio cannot extend before timeline zero even when earlier source content exists", () => {
  const value = project(); value.audio_clips[0].start = 0.5; value.audio_clips[0].in = 4;
  const result = trim(value, "audio", "a1", "left", -100);
  near(result.clip.start, 0); near(result.clip.in, 3); near(result.clip.start + duration(result.clip), 2.5);
  validateProject(result.project, media());
});

test("audio expansion stops at the previous or next neighbor in the same track", () => {
  const value = project(); value.audio_clips[0].in = 6; value.audio_clips[0].out = 12; value.audio_clips[0].start = 4;
  const base = value.audio_clips[0];
  value.audio_clips.push({ ...base, id: "before", start: 1, in: 0, out: 2, speed: 1, fade_in: 0, fade_out: 0 });
  value.audio_clips.push({ ...base, id: "after", start: 8, in: 0, out: 2, speed: 1, fade_in: 0, fade_out: 0 });
  const left = trim(value, "audio", "a1", "left", -100), right = trim(value, "audio", "a1", "right", 100);
  near(left.clip.start, 3); near(left.clip.in, 4); near(left.clip.start + duration(left.clip), 7);
  near(right.clip.start, 4); near(right.clip.out, 14); near(right.clip.start + duration(right.clip), 8);
  validateProject(left.project, media()); validateProject(right.project, media());
});

test("audio in a different track does not block extending the selected clip", () => {
  const value = project(); value.audio_clips.push({ ...value.audio_clips[0], id: "other-track", track: 1, start: 6 });
  const result = trim(value, "audio", "a1", "right", 1);
  near(result.clip.out, 10); near(result.clip.start + duration(result.clip), 7);
  validateProject(result.project, media());
});

test("audio minimum length proportionally reduces fades without changing the other edge", () => {
  const value = project();
  for (const [edge, delta] of [["left", 100], ["right", -100]]) {
    const result = trim(value, "audio", "a1", edge, delta);
    near(duration(result.clip), 1 / 24);
    near(result.clip.fade_in, 0.6 / 24); near(result.clip.fade_out, 0.4 / 24);
    if (edge === "left") near(result.clip.start + duration(result.clip), 6);
    else near(result.clip.start, 3);
    validateProject(result.project, media());
  }
});

test("audio extension honors source tail, the 600-second clip limit and 600-second start limit", () => {
  const value = project(); value.audio_clips[0] = { ...value.audio_clips[0], in: 100, out: 600, speed: 1, start: 200 };
  const sources = media(); sources.get("sound").duration = 2000;
  near(duration(trim(value, "audio", "a1", "right", 1000, sources).clip), 600);
  near(duration(trim(value, "audio", "a1", "left", -1000, sources).clip), 600);
  value.audio_clips[0] = { ...value.audio_clips[0], in: 0, out: 5, start: 599 };
  const late = trim(value, "audio", "a1", "left", 100, sources);
  near(late.clip.start, 600); near(late.clip.in, 1); near(late.clip.start + duration(late.clip), 604);
  validateProject(late.project, sources);
  const ordinary = project(), tail = trim(ordinary, "audio", "a1", "right", 100);
  near(tail.clip.out, 20);
});

test("missing source and invalid drag inputs cannot silently change a project", () => {
  const value = project(), before = clone(value);
  assert.throws(() => trim(value, "video", "missing", "left", 1));
  assert.throws(() => trim(value, "video", "v1", "left", Infinity));
  assert.throws(() => trim(value, "video", "v1", "right", 1, new Map()));
  assert.deepEqual(value, before);
});

test("a whole drag previews from its original snapshot then commits one undo step", () => {
  let changes = 0;
  const session = new ProjectSession(project(), () => changes++), original = clone(session.project);
  const gesture = new TrimTransaction(session, { kind: "video", id: "v1", edge: "right" }, media());
  gesture.update(0.5); gesture.update(1); gesture.update(0.5);
  near(gesture.preview.clips[0].out, 7);
  assert.deepEqual(session.project, original); assert.equal(changes, 0); assert.equal(session.undoStack.length, 0);
  gesture.commit(); gesture.commit();
  near(session.project.clips[0].out, 7); assert.equal(changes, 1); assert.equal(session.undoStack.length, 1);
  session.travel(true); assert.deepEqual(session.project, original);
  session.travel(false); near(session.project.clips[0].out, 7);
});

test("cancel and return-to-origin gestures preserve saved content, redo and draft callbacks", () => {
  let changes = 0;
  const session = new ProjectSession(project(), () => changes++);
  session.change(p => { p.name = "另一個名稱"; }); session.travel(true);
  const before = clone(session.project), undo = clone(session.undoStack), redo = clone(session.redoStack), prior = changes;
  const gesture = new TrimTransaction(session, { kind: "audio", id: "a1", edge: "left" }, media());
  gesture.update(1); gesture.cancel(); gesture.commit();
  assert.deepEqual(session.project, before); assert.deepEqual(session.undoStack, undo); assert.deepEqual(session.redoStack, redo); assert.equal(changes, prior);
  const noop = new TrimTransaction(session, { kind: "video", id: "v1", edge: "right" }, media());
  noop.update(1); noop.update(0); noop.commit();
  assert.deepEqual(session.project, before); assert.deepEqual(session.redoStack, redo); assert.equal(changes, prior);
});

test("shorten then re-extend in one audio gesture restores the original fades", () => {
  const session = new ProjectSession(project());
  const gesture = new TrimTransaction(session, { kind: "audio", id: "a1", edge: "right" }, media());
  gesture.update(-100); near(gesture.preview.audio_clips[0].fade_in, 0.6 / 24);
  gesture.update(0); near(gesture.preview.audio_clips[0].fade_in, 0.6); near(gesture.preview.audio_clips[0].fade_out, 0.4);
  gesture.commit(); assert.equal(session.undoStack.length, 0);
});

test("a save finishing during a gesture keeps its current server revision", () => {
  const session = new ProjectSession(project());
  const gesture = new TrimTransaction(session, { kind: "video", id: "v1", edge: "right" }, media());
  gesture.update(0.5); session.project.updated_at = "r2"; gesture.commit();
  assert.equal(session.project.updated_at, "r2"); near(session.project.clips[0].out, 7);
  session.travel(true); assert.equal(session.project.updated_at, "r2");
});

test("a stale gesture cannot overwrite unrelated edits made after it began", () => {
  const session = new ProjectSession(project());
  const gesture = new TrimTransaction(session, { kind: "audio", id: "a1", edge: "right" }, media());
  gesture.update(0.5); session.change(p => { p.name = "較新的編輯"; });
  const expected = signature(session.project);
  try { gesture.commit(); } catch {}
  assert.equal(signature(session.project), expected);
});

function gestureFixture() {
  const source = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
  const session = new ProjectSession(project()), state = { session, media: media(), zoom: 60, playhead: 0.5, queuedSeek: { time: 3 } };
  const scheduled = new Map(), counters = { pause: 0, seek: 0, preview: 0 }, errors = [];
  let serial = 0, captured = null;
  const classes = { add() {}, remove() {} };
  const canvas = { getBoundingClientRect: () => ({ width: 800 }), setPointerCapture: id => { captured = id; }, hasPointerCapture: id => captured === id, releasePointerCapture: () => { captured = null; } };
  const scroll = { scrollLeft: 0, getBoundingClientRect: () => ({ left: 0, right: 800 }) };
  const elements = { timelineCanvas: canvas, timelineScroll: scroll, trimFeedback: { classList: classes } };
  const handle = { dataset: { trimKind: "video", trimId: "v1", trimEdge: "right" } };
  const context = vm.createContext({ state, TrimTransaction, totalDuration,
    project: () => state.trimDrag?.transaction.preview || session.project,
    locked: () => Boolean(state.trimDrag), $: id => elements[id], document: { body: { classList: classes } },
    pause: () => { counters.pause++; }, seek: () => { counters.seek++; },
    renderTrimPreview: () => { counters.preview++; }, renderDisabled() {}, render() {},
    clamp: (value, min, max) => Math.max(min, Math.min(max, value)), notify() {}, errorNotice: error => errors.push(error),
    requestAnimationFrame: fn => { scheduled.set(++serial, fn); return serial; }, cancelAnimationFrame: id => scheduled.delete(id),
  });
  for (const name of ["beginTrim", "flushTrimPreview", "queueTrimPreview", "finishTrim"]) {
    const start = source.indexOf(`  function ${name}(`), end = source.indexOf("\n  function ", start + 1);
    assert.ok(start >= 0 && end > start, `Cannot locate shipped ${name}`);
    vm.runInContext(source.slice(start, end), context);
  }
  const down = () => context.beginTrim({ target: { closest: () => handle }, button: 0, isPrimary: true, pointerId: 5, clientX: 200, preventDefault() {}, stopPropagation() {} });
  const runFrame = () => { const [id, fn] = scheduled.entries().next().value; scheduled.delete(id); fn(); };
  return { context, state, session, scheduled, counters, errors, scroll, down, runFrame };
}

test("pointer gesture coalesces moves, includes scroll distance, and seeks only after commit", () => {
  const f = gestureFixture(); f.down();
  assert.equal(f.counters.pause, 1); assert.equal(f.state.queuedSeek, null);
  f.state.trimDrag.lastX = 210; f.context.queueTrimPreview();
  f.state.trimDrag.lastX = 220; f.context.queueTrimPreview();
  f.state.trimDrag.lastX = 230; f.context.queueTrimPreview();
  assert.equal(f.scheduled.size, 1);
  f.scroll.scrollLeft = 30; f.runFrame();
  near(f.state.trimDrag.transaction.preview.clips[0].out, 8);
  assert.equal(f.counters.seek, 0); assert.equal(f.session.undoStack.length, 0);
  f.context.finishTrim(true);
  near(f.session.project.clips[0].out, 8);
  assert.equal(f.counters.seek, 1); assert.equal(f.counters.pause, 1); assert.equal(f.session.undoStack.length, 1);
  assert.equal(f.state.trimDrag, null); assert.deepEqual(f.errors, []);
});

test("pointer release before its scheduled frame still commits the final position", () => {
  const f = gestureFixture(); f.down(); f.state.trimDrag.lastX = 230; f.context.queueTrimPreview();
  f.context.finishTrim(true);
  near(f.session.project.clips[0].out, 7); assert.equal(f.scheduled.size, 0); assert.equal(f.counters.seek, 1);
});

test("gesture cancellation discards pending frames without draft writes or media seeks", () => {
  const f = gestureFixture(), before = clone(f.session.project); f.down();
  f.state.trimDrag.lastX = 230; f.context.queueTrimPreview(); f.runFrame();
  f.state.trimDrag.lastX = 240; f.context.queueTrimPreview(); f.context.finishTrim(false);
  assert.deepEqual(f.session.project, before); assert.equal(f.session.undoStack.length, 0);
  assert.equal(f.counters.seek, 0); assert.equal(f.scheduled.size, 0); assert.equal(f.state.trimDrag, null);
});

test("holding a trim near the visible edge schedules bounded auto-scroll until cancellation", () => {
  const f = gestureFixture(); f.down();
  f.state.trimDrag.lastX = 790; f.context.queueTrimPreview(); f.runFrame();
  near(f.scroll.scrollLeft, 9); assert.equal(f.scheduled.size, 1);
  f.runFrame(); near(f.scroll.scrollLeft, 18);
  assert.equal(f.counters.seek, 0); assert.equal(f.session.undoStack.length, 0);
  f.context.finishTrim(false); assert.equal(f.scheduled.size, 0);
});
