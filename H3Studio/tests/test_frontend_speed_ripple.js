"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const core = require("../static/editor.js"), ui = require("../static/editor_speed_ui.js");
const close = (a, b, tolerance = 1e-8) => assert.ok(Math.abs(a - b) <= tolerance, `${a} != ${b}`);
const media = new Map([
  ["source", { id: "source", kind: "video", duration: 30, has_audio: true }],
  ["image", { id: "image", kind: "image", duration: 0 }],
]);
const clip = (id, start, end, changes = {}) => ({ id, kind: "video", media_id: "source", in: 3, out: 3 + end - start, speed: 1, volume: .7, start, end, x: .5, y: .5, width: .6, rotation: 15, opacity: .8, fade_in: .2, fade_out: .3, track_id: "shared", ...changes });
const project = (layers, changes = {}) => ({ id: "ripple", name: "Speed ripple", updated_at: "r1", clips: [], audio_clips: [], overlays: layers, width: 1280, height: 720, fps: 24, ...changes });
const curve = (item, speed) => [{ time: item.in, speed }, { time: item.out, speed }];
const byId = (value, id) => value.overlays.find(item => item.id === id);
const withoutTimes = item => { const { start, end, ...rest } = item; return rest; };

const presetRatios = { custom: 1, montage: .8822623435374288, hero: 1.4025354499387033, bullet: 1.412441589690591, jump_cut: 1.1150111363073665, flash_in: 1.2700422745412807, flash_out: 1.2700422745412805 };
for (const preset of ui.PRESETS) test(`${preset.id} preset remains selectable before another same-track clip`, () => {
  const value = project([clip("selected", 2, 8), clip("next", 8, 11), clip("last", 12.5, 14.5)]), original = core.clone(value);
  const selected = value.overlays[0], speed_curve = ui.presetPoints(preset.id, selected.in, selected.out, selected.speed);
  const result = core.transformClipSpeed(value, "overlay", selected.id, { speed_curve }, media);
  const expectedDuration = 6 * presetRatios[preset.id], delta = expectedDuration - 6;
  close(core.duration(result.clip), expectedDuration); close(result.clip.start, 2); close(result.clip.end, 2 + expectedDuration);
  close(byId(result.project, "next").start, 8 + delta); close(byId(result.project, "next").end, 11 + delta);
  close(byId(result.project, "last").start, 12.5 + delta); close(byId(result.project, "last").end, 14.5 + delta);
  close(byId(result.project, "next").start - result.clip.end, 0);
  close(byId(result.project, "last").start - byId(result.project, "next").end, 1.5);
  assert.equal(result.rippleCount, delta === 0 ? 0 : 2); close(result.durationDelta, delta);
  assert.deepEqual(withoutTimes(byId(result.project, "next")), withoutTimes(value.overlays[1]));
  assert.deepEqual(withoutTimes(byId(result.project, "last")), withoutTimes(value.overlays[2]));
  assert.equal(result.clip.in, selected.in); assert.equal(result.clip.out, selected.out); assert.equal(result.clip.volume, selected.volume);
  core.validateProject(result.project, media); assert.deepEqual(value, original);
});

test("positive and negative speed deltas preserve earlier clips, each gap, other tracks and independent audio", () => {
  const before = clip("before", 0, 1), selected = clip("selected", 2, 6), next = clip("next", 7, 9), last = clip("last", 11, 13);
  const other = clip("other", 0, 20, { track_id: "other", fade_in: 0, fade_out: 0 });
  const voice = { id: "voice", media_id: "source", in: 0, out: 5, speed: 1, volume: 1, start: 1, track: 0, fade_in: .1, fade_out: .2 };
  const value = project([before, selected, next, last, other], { audio_clips: [voice] });
  const slow = core.transformClipSpeed(value, "overlay", "selected", { speed: .5, speed_curve: [] }, media);
  assert.deepEqual(slow.project.overlays.slice(0, 4).map(item => [item.start, item.end]), [[0, 1], [2, 10], [11, 13], [15, 17]]);
  assert.equal(slow.rippleCount, 2); assert.equal(slow.durationDelta, 4);
  const fast = core.transformClipSpeed(slow.project, "overlay", "selected", { speed: 2, speed_curve: [] }, media);
  assert.deepEqual(fast.project.overlays.slice(0, 4).map(item => [item.start, item.end]), [[0, 1], [2, 4], [5, 7], [9, 11]]);
  assert.equal(fast.rippleCount, 2); assert.equal(fast.durationDelta, -6);
  for (const result of [slow, fast]) {
    assert.deepEqual(result.project.overlays[0], before); assert.deepEqual(byId(result.project, "other"), other);
    assert.deepEqual(result.project.audio_clips, [voice]);
  }
  const restored = core.transformClipSpeed(fast.project, "overlay", "selected", { speed: 1, speed_curve: [] }, media);
  assert.deepEqual(restored.project, value);
});

test("returning from a curve to normal fixed speed ripples from the current duration", () => {
  const value = project([clip("selected", 0, 4, { speed: 2 }), clip("next", 5, 7)]);
  value.overlays[0].end = 2;
  const ramp = core.transformClipSpeed(value, "overlay", "selected", { speed_curve: ui.presetPoints("hero", 3, 7) }, media);
  const normal = core.transformClipSpeed(ramp.project, "overlay", "selected", { speed_curve: [] }, media);
  assert.equal(normal.clip.speed_curve, undefined); assert.equal(normal.clip.speed, 2);
  assert.deepEqual(normal.project, value);
});

test("same-track text and image followers move together without retiming local position keyframes", () => {
  const motion = [{ time: -.5, x: .1, y: .2, easing: "ease_in" }, { time: 1, x: .8, y: .7, easing: "linear" }, { time: 3, x: .9, y: .9, easing: "ease_out" }];
  const common = { track_id: "shared", x: .5, y: .5, width: .5, rotation: 20, opacity: .8, fade_in: .2, fade_out: .3, position_keyframes: motion };
  const text = { id: "text", kind: "text", start: 3, end: 5, text: "Later caption", font_size: .1, color: "#ffffff", background: "transparent", bold: false, align: "center", ...common };
  const image = { id: "image-layer", kind: "image", media_id: "image", start: 7, end: 9, ...common };
  const value = project([clip("selected", 0, 2), text, image]), before = core.clone(value);
  const result = core.transformClipSpeed(value, "overlay", "selected", { speed: .5 }, media);
  for (const id of ["text", "image-layer"]) {
    const original = byId(value, id), shifted = byId(result.project, id);
    close(shifted.start, original.start + 2); close(shifted.end, original.end + 2);
    assert.deepEqual(withoutTimes(shifted), withoutTimes(original));
    assert.notEqual(shifted.position_keyframes, original.position_keyframes);
    assert.deepEqual(core.positionAt(shifted, .7), core.positionAt(original, .7));
  }
  assert.deepEqual(value, before);
});

test("a legacy track referenced by followers still ripples only its own group", () => {
  const selected = clip("legacy", 0, 2); delete selected.track_id;
  const value = project([selected, clip("next", 3, 5, { track_id: "legacy" }), clip("other", 0, 2, { track_id: "elsewhere" })]);
  const result = core.transformClipSpeed(value, "overlay", "legacy", { speed: .5 }, media);
  assert.deepEqual(result.project.overlays.map(item => [item.start, item.end]), [[0, 4], [5, 7], [0, 2]]);
  assert.equal(result.clip.track_id, undefined); assert.equal(result.rippleCount, 1);
});

test("same-track followers are identified by time rather than their array index", () => {
  const selected = clip("selected", 2, 4), later = clip("later", 5, 7), earlier = clip("earlier", 0, 1);
  const value = project([later, selected, earlier]);
  const result = core.transformClipSpeed(value, "overlay", "selected", { speed: .5 }, media);
  assert.deepEqual(result.project.overlays.map(item => item.id), ["later", "selected", "earlier"]);
  assert.deepEqual(byId(result.project, "earlier"), earlier);
  assert.deepEqual(result.project.overlays.map(item => [item.start, item.end]), [[7, 9], [2, 6], [0, 1]]);
});

test("duration changes cannot move a following clip beyond the 600 second project limit", () => {
  const value = project([clip("selected", 590, 592), clip("next", 598, 600)]), before = core.clone(value);
  assert.throws(() => core.transformClipSpeed(value, "overlay", "selected", { speed: .5 }, media), /10 分鐘|600/);
  assert.deepEqual(value, before);
  const shorter = core.transformClipSpeed(value, "overlay", "selected", { speed: 2 }, media);
  assert.equal(byId(shorter.project, "next").end, 599); core.validateProject(shorter.project, media);
});

test("rippling still validates the simultaneous video limit atomically", () => {
  const layers = [clip("selected", 0, 1, { fade_in: 0, fade_out: 0 }), clip("next", 1, 2, { fade_in: 0, fade_out: 0 })];
  for (let index = 0; index < 3; index++) layers.push(clip(`other-${index}`, 3, 4, { track_id: `other-${index}`, fade_in: 0, fade_out: 0 }));
  const value = project(layers), before = core.clone(value); core.validateProject(value, media);
  assert.throws(() => core.transformClipSpeed(value, "overlay", "selected", { speed: .25 }, media), /最多疊加 3/);
  assert.deepEqual(value, before);
});

test("invalid original overlap is rejected rather than silently repaired by changing speed", () => {
  const value = project([clip("selected", 0, 2), clip("next", 1, 3)]), before = core.clone(value);
  assert.throws(() => core.transformClipSpeed(value, "overlay", "selected", { speed: 4 }, media), /不能重疊/);
  assert.deepEqual(value, before);
});

test("unchanged speed creates no ripple and no history entry", () => {
  const value = project([clip("selected", 0, 2), clip("next", 3, 5)]);
  const result = core.transformClipSpeed(value, "overlay", "selected", { speed: 1 }, media);
  assert.equal(result.changed, false); assert.equal(result.durationDelta, 0); assert.equal(result.rippleCount, 0);
  assert.deepEqual(result.project, value);
  const session = new core.ProjectSession(value), transaction = new core.SpeedCurveTransaction(session, "overlay", "selected", media);
  transaction.update([]); assert.equal(transaction.commit(), false); assert.equal(session.undoStack.length, 0);
});

test("curve gesture previews derive all ripple times from the original baseline and create one undo", () => {
  const value = project([clip("selected", 2, 6), clip("next", 7, 9), clip("last", 11, 13)]), session = new core.ProjectSession(value);
  const transaction = new core.SpeedCurveTransaction(session, "overlay", "selected", media);
  for (const speed of [.5, 2, .75, 1.5, .25, 1]) {
    const result = transaction.update(curve(value.overlays[0], speed)), delta = 4 / speed - 4;
    close(byId(result.project, "next").start, 7 + delta); close(byId(result.project, "last").start, 11 + delta);
    assert.deepEqual(session.project, value); assert.equal(session.undoStack.length, 0);
  }
  transaction.update(curve(value.overlays[0], .5)); session.project.updated_at = "r2";
  assert.equal(transaction.commit(), true); assert.equal(session.undoStack.length, 1); assert.equal(session.project.updated_at, "r2");
  const edited = core.clone(session.project); session.travel(true);
  assert.equal(core.signature(session.project), core.signature(value)); assert.equal(session.project.updated_at, "r2");
  session.travel(false); assert.deepEqual(session.project, edited);
});

test("invalid curve previews clear the last valid ripple result and cannot commit it", () => {
  const value = project([clip("selected", 590, 592), clip("next", 598, 600)]), session = new core.ProjectSession(value);
  const transaction = new core.SpeedCurveTransaction(session, "overlay", "selected", media);
  transaction.update(curve(value.overlays[0], 2)); assert.equal(transaction.result.rippleCount, 1);
  assert.throws(() => transaction.update(curve(value.overlays[0], .25)), /10 分鐘|600/);
  assert.equal(transaction.result, null); assert.deepEqual(transaction.preview, value);
  assert.equal(transaction.commit(), false); assert.deepEqual(session.project, value); assert.equal(session.undoStack.length, 0);
});

test("curve ripple cancellation and stale concurrent-edit checks preserve the current project", () => {
  const value = project([clip("selected", 0, 2), clip("next", 2, 4)]), session = new core.ProjectSession(value);
  const cancelled = new core.SpeedCurveTransaction(session, "overlay", "selected", media);
  cancelled.update(curve(value.overlays[0], .5)); assert.equal(cancelled.cancel(), false);
  assert.deepEqual(session.project, value); assert.equal(session.undoStack.length, 0);
  const stale = new core.SpeedCurveTransaction(session, "overlay", "selected", media); stale.update(curve(value.overlays[0], .5));
  session.change(next => { next.name = "Newer edit"; });
  assert.throws(() => stale.commit(), /已變更/); assert.equal(session.project.name, "Newer edit");
  assert.deepEqual(session.project.overlays, value.overlays); assert.equal(session.undoStack.length, 1);
});

test("saving and draft recovery retain rippled positions, curves and track membership", async () => {
  const value = project([clip("selected", 0, 2), clip("next", 3, 5)]), session = new core.ProjectSession(value);
  const transaction = new core.SpeedCurveTransaction(session, "overlay", "selected", media);
  transaction.update(curve(value.overlays[0], .5)); transaction.commit();
  const draft = core.clone(session.project), snapshotSignature = core.signature(draft);
  const recovered = core.resolveDraft(value, { project: draft, savedSignature: core.signature(value) }, media);
  assert.equal(recovered.status, "recoverable"); assert.equal(core.signature(recovered.project), snapshotSignature);
  let savedSnapshot;
  await session.save(async snapshot => { savedSnapshot = core.clone(snapshot); return { ...snapshot, updated_at: "r2" }; });
  assert.deepEqual(savedSnapshot.overlays, draft.overlays); assert.equal(session.dirty, false);
  const reopened = new core.ProjectSession(session.project); assert.equal(core.signature(reopened.project), snapshotSignature);
  assert.deepEqual(reopened.project.overlays.map(item => [item.start, item.end, item.track_id]), [[0, 4, "shared"], [5, 7, "shared"]]);
});

test("main video and independent audio speed edits retain existing placement rules", () => {
  const main = { id: "main", media_id: "source", in: 0, out: 2, speed: 1, volume: 1 }, after = { ...main, id: "main-after" };
  const voice = { ...main, id: "voice", start: 5, track: 0, fade_in: 0, fade_out: 0 };
  const value = project([clip("overlay", 0, 2, { track_id: "overlay" })], { clips: [main, after], audio_clips: [voice] });
  const video = core.transformClipSpeed(value, "video", "main", { speed: .5 }, media);
  assert.equal(core.mainClipStart(video.project, "main-after"), 4); assert.equal(video.rippleCount, 0);
  assert.deepEqual(video.project.overlays, value.overlays); assert.deepEqual(video.project.audio_clips, value.audio_clips);
  const audio = core.transformClipSpeed(value, "audio", "voice", { speed: .5 }, media);
  assert.equal(audio.clip.start, 5); assert.equal(audio.rippleCount, 0);
  assert.deepEqual(audio.project.clips, value.clips); assert.deepEqual(audio.project.overlays, value.overlays);
});
