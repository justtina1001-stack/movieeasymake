"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../static/editor.js");
const clip = (id, start, end, values = {}) => ({ id, kind: "video", media_id: "source", in: 0, out: end - start, speed: 1, volume: .6, start, end, x: .5, y: .5, width: .75, rotation: 0, opacity: 1, track_id: "shared", ...values });
const project = layers => ({ id: "project", name: "Shared track", updated_at: "r1", clips: [], audio_clips: [], overlays: layers, width: 1280, height: 720, fps: 24 });
const media = new Map([["source", { kind: "video", width: 640, height: 480, duration: 30, has_audio: true }]]);
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("splitting legacy or shared video keeps both pieces on the same track with one undo", () => {
  for (const legacy of [false, true]) {
    const source = clip("one", 0, 4, { fade_in: .4, fade_out: .3 });
    if (legacy) delete source.track_id;
    const session = new core.ProjectSession(project([source]));
    session.change(value => core.splitOverlayAt(value, "one", 2, () => "two"));
    const [track] = core.overlayTrackGroups(session.project);
    assert.equal(track.clips.length, 2); assert.equal(track.clips[0].end, track.clips[1].start);
    assert.equal(track.clips[0].out, track.clips[1].in); assert.equal(track.clips[0].fade_in, .4);
    assert.equal(track.clips[1].fade_out, .3); assert.equal(session.undoStack.length, 1);
    session.travel(true); assert.deepEqual(session.project.overlays, [source]);
    session.travel(false); assert.equal(core.overlayTrackGroups(session.project).length, 1);
  }
});

test("dragging shared video trim boundaries stops at neighbors and retains original source", () => {
  const value = project([clip("before", 0, 2), clip("middle", 3, 5, { in: 2, out: 4 }), clip("after", 6, 8)]);
  const left = core.shiftOverlayTime(value, "middle", "left", -20, media);
  close(left.layer.start, 2); close(left.layer.end, 5); close(left.layer.in, 1); assert.equal(left.clamped, true);
  const right = core.shiftOverlayTime(value, "middle", "right", 20, media);
  close(right.layer.start, 3); close(right.layer.end, 6); close(right.layer.out, 5); assert.equal(right.clamped, true);
  assert.deepEqual(value.overlays.map(item => [item.start, item.end]), [[0, 2], [3, 5], [6, 8]]);
});

test("moving within a free shared-track gap preserves stacking and canonical chronological order", () => {
  const value = project([clip("one", 0, 2), clip("two", 6, 8), clip("front", 0, 3, { track_id: "front" })]);
  const result = core.transformOverlay(value, "one", { start: 9, end: 11 }, media);
  assert.deepEqual(result.project.overlays.map(item => item.id), ["two", "one", "front"]);
  assert.deepEqual(core.overlayTrackGroups(result.project).map(track => track.id), ["shared", "front"]);
  assert.equal(result.layer.volume, .6); assert.equal(value.overlays[0].start, 0);
  assert.throws(() => core.transformOverlay(value, "one", { start: 5, end: 7 }, media), /不能重疊/);
});

test("a slower curve moves following shared-track clips without overwriting them", () => {
  const value = project([clip("one", 0, 2), clip("two", 2, 4)]);
  const result = core.transformClipSpeed(value, "overlay", "one", { speed_curve: [{ time: 0, speed: .5 }, { time: 2, speed: .5 }] }, media);
  assert.deepEqual(result.project.overlays.map(item => [item.start, item.end]), [[0, 4], [4, 6]]);
  assert.equal(result.rippleCount, 1); assert.equal(result.durationDelta, 2);
  assert.equal(value.overlays[0].speed_curve, undefined); assert.equal(value.overlays[0].end, 2);
});

test("saving rejects an old server that silently discards shared track identity and keeps the draft dirty", async () => {
  const session = new core.ProjectSession(project([clip("one", 0, 2), clip("two", 2, 4)]));
  session.change(value => { value.name = "Edited"; });
  await assert.rejects(session.save(async snapshot => ({ ...snapshot, updated_at: "r2", overlays: snapshot.overlays.map(({ track_id, ...layer }) => layer) })), /未完整保存/);
  assert.equal(session.dirty, true); assert.equal(session.project.overlays[0].track_id, "shared");
  await session.save(async snapshot => ({ ...snapshot, updated_at: "r3" }));
  assert.equal(session.dirty, false); assert.equal(core.overlayTrackGroups(session.project).length, 1);
});

test("matching-server draft recovery retains shared track membership", () => {
  const value = project([clip("one", 0, 2), clip("two", 2, 4)]), draft = core.clone(value);
  draft.name = "Draft";
  const result = core.resolveDraft(value, { project: draft, savedSignature: core.signature(value) }, media);
  assert.equal(result.status, "recoverable"); assert.equal(core.overlayTrackGroups(result.project).length, 1);
  assert.deepEqual(result.project.overlays.map(item => item.track_id), ["shared", "shared"]);
});
