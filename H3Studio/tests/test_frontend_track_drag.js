"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../static/editor.js");
const { createTrackDragCore } = require("../static/editor_track_drag.js");
const { overlayTrackId, overlayTrackGroups, normalizeOverlayTracks, moveOverlayTrack,
  mainClipStart, mainBoundaryAt, transformTrackDrag, TrackDragTransaction } = createTrackDragCore(core);
const { clone, duration, signature, validateProject, ProjectSession } = core;
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const clip = (id, values = {}) => ({ id, media_id: "source", in: 2, out: 8, speed: 2, volume: 0.7, ...values });
const layer = (id, values = {}) => ({ ...clip(id), kind: "video", start: 2, end: 5, x: 0.5, y: 0.5, width: 0.75, rotation: 0, opacity: 1, ...values });
const project = (values = {}) => ({ id: "project", name: "test", updated_at: "revision-1", clips: [clip("one"), clip("two"), clip("three")], audio_clips: [], overlays: [], width: 1280, height: 720, fps: 24, ...values });
const media = new Map([["source", { id: "source", kind: "video", duration: 120, width: 640, height: 480, has_audio: true }], ["image", { id: "image", kind: "image" }]]);
const curve = [{ time: 2, speed: 0.5 }, { time: 4, speed: 3 }, { time: 8, speed: 1 }];

test("main boundary snapping uses remaining sequence and prefers the earlier boundary in ties", () => {
  const value = project();
  assert.deepEqual(mainBoundaryAt(value, 4.5), { index: 1, time: 3 });
  assert.deepEqual(mainBoundaryAt(value, 5.9), { index: 2, time: 6 });
  assert.deepEqual(mainBoundaryAt(value, 100), { index: 3, time: 9 });
  assert.deepEqual(mainBoundaryAt(value, -2), { index: 0, time: 0 });
  assert.deepEqual(mainBoundaryAt(value, 6, "two"), { index: 2, time: 6 });
  assert.deepEqual(mainBoundaryAt(project({ clips: [] }), 1), { index: 0, time: 0 });
  assert.equal(mainClipStart(value, "two"), 3);
  assert.throws(() => mainBoundaryAt(value, NaN), /時間無效/);
});

test("main video promotion preserves source editing, absolute start, identity, audio and independent curves", () => {
  const value = project();
  Object.assign(value.clips[1], { speed_curve: clone(curve), fade_in: 0.2, fade_out: 0.3, unknown: "ignored" });
  const original = clone(value), result = transformTrackDrag(value, { kind: "video", id: "two" }, { kind: "overlay", index: 0 }, media);
  assert.deepEqual(value, original);
  assert.deepEqual(result.project.clips.map(item => item.id), ["one", "three"]);
  const moved = result.project.overlays[0];
  assert.equal(moved.id, "two"); assert.equal(moved.start, 3); close(moved.end, 3 + duration(value.clips[1]));
  for (const key of ["media_id", "in", "out", "volume", "speed", "fade_in", "fade_out"]) assert.equal(moved[key], value.clips[1][key]);
  assert.deepEqual(moved.speed_curve, curve); assert.notEqual(moved.speed_curve, value.clips[1].speed_curve);
  assert.equal(moved.width, 0.75); assert.equal(moved.x, 0.5); assert.equal(moved.y, 0.5); assert.equal(moved.opacity, 1);
  assert.equal(moved.unknown, undefined); assert.equal(result.selectedKind, "overlay"); assert.equal(result.selectedId, "two");
  validateProject(result.project, media);
});

test("promotion uses explicit horizontal drop time and aspect fits wide, square, portrait or unknown media", () => {
  for (const [dimensions, width] of [[{ width: 1920, height: 1080 }, 1], [{ width: 640, height: 640 }, 0.5625], [{ width: 360, height: 640 }, 0.31640625], [{}, 1]]) {
    const sources = new Map([["source", { ...media.get("source"), width: undefined, height: undefined, ...dimensions }]]);
    const result = transformTrackDrag(project(), { kind: "video", id: "two" }, { kind: "overlay", index: 0, start: 7 }, sources);
    assert.equal(result.start, 7); close(result.project.overlays[0].width, width);
  }
});

test("overlay z-order reorder preserves text keyframes, times and visual settings", () => {
  const text = { id: "title", kind: "text", start: 1, end: 4, x: 0.2, y: 0.3, width: 0.6, rotation: 5, opacity: 0.8, fade_in: 0.2, text: "title", font_size: 0.05, color: "#FFFFFF", background: "transparent", bold: true, align: "center", position_keyframes: [{ time: 0, x: 0.2, y: 0.3 }, { time: 2, x: 0.8, y: 0.7, easing: "ease_out" }] };
  const value = project({ overlays: [layer("lower"), text, layer("upper", { start: 5, end: 8 })] });
  const result = transformTrackDrag(value, { kind: "overlay", id: "title" }, { kind: "overlay", index: 0 }, media);
  assert.deepEqual(result.project.overlays.map(item => item.id), ["title", "lower", "upper"]);
  assert.deepEqual(result.project.overlays[0], core.canonicalOverlay(text));
  assert.notEqual(result.project.overlays[0].position_keyframes, text.position_keyframes);
  assert.equal(result.layoutReset, false); assert.equal(result.start, 1); assert.equal(result.end, 4);
});

test("horizontal overlay displacement preserves local motion anchors and duration", () => {
  const image = { id: "picture", kind: "image", media_id: "image", start: 1, end: 4, x: 0.4, y: 0.3, width: 0.5, rotation: 0, opacity: 1, position_keyframes: [{ time: 0, x: 0.4, y: 0.3 }, { time: 1, x: 0.5, y: 0.3 }] };
  const result = transformTrackDrag(project({ overlays: [image] }), { kind: "overlay", id: "picture" }, { kind: "overlay", index: 0, start: 10 }, media);
  assert.equal(result.project.overlays[0].start, 10); assert.equal(result.project.overlays[0].end, 13);
  assert.deepEqual(result.project.overlays[0].position_keyframes, core.canonicalPositionKeyframes(image));
});

test("upper video demotion inserts without overwrite or gaps and strips only overlay layout", () => {
  const upper = layer("upper", { speed_curve: clone(curve), fade_in: 0.1, fade_out: 0.2, start: 15, width: 0.4, x: 0.2, opacity: 0.8, rotation: 20 });
  upper.end = upper.start + duration(upper);
  const value = project({ overlays: [upper] });
  const result = transformTrackDrag(value, { kind: "overlay", id: "upper" }, { kind: "video", index: 1 }, media);
  assert.equal(result.start, 3); assert.deepEqual(result.project.clips.map(item => item.id), ["one", "upper", "two", "three"]);
  assert.equal(result.project.overlays.length, 0); assert.equal(result.layoutReset, true);
  assert.equal(result.project.clips[1].volume, 0.7); assert.equal(result.project.clips[1].fade_in, 0.1);
  assert.equal(result.project.clips[1].fade_out, 0.2); assert.deepEqual(result.project.clips[1].speed_curve, curve);
  for (const key of ["start", "end", "x", "y", "width", "rotation", "opacity", "kind"]) assert.equal(Object.hasOwn(result.project.clips[1], key), false);
});

test("demotion of fitted full-frame video does not claim a layout reset", () => {
  const result = transformTrackDrag(project({ overlays: [layer("upper")] }), { kind: "overlay", id: "upper" }, { kind: "video", index: 0 }, media);
  assert.equal(result.layoutReset, false);
});

test("main reorder follows after-removal insertion indices and same position is a no-op", () => {
  const value = project();
  const move = transformTrackDrag(value, { kind: "video", id: "one" }, { kind: "video", index: 2 }, media);
  assert.deepEqual(move.project.clips.map(item => item.id), ["two", "three", "one"]); assert.equal(move.start, 6);
  const same = transformTrackDrag(value, { kind: "video", id: "two" }, { kind: "video", index: 1 }, media);
  assert.equal(same.changed, false); assert.equal(signature(same.project), signature(value));
  const overlaySame = transformTrackDrag(project({ overlays: [layer("upper")] }), { kind: "overlay", id: "upper" }, { kind: "overlay", index: 0 }, media);
  assert.equal(overlaySame.changed, false);
});

test("invalid target, missing source, text-to-main, negative and out-of-range time reject without mutation", () => {
  const value = project({ overlays: [layer("upper"), { id: "title", kind: "text", start: 0, end: 2, x: 0.5, y: 0.5, width: 0.5, rotation: 0, opacity: 1, text: "title", font_size: 0.05, color: "#ffffff", background: "transparent", bold: false, align: "center" }] });
  const before = clone(value);
  for (const [from, target] of [
    [{ kind: "video", id: "missing" }, { kind: "video", index: 0 }],
    [{ kind: "video", id: "one" }, { kind: "video", index: 3 }],
    [{ kind: "video", id: "one" }, { kind: "overlay", index: 0.5 }],
    [{ kind: "overlay", id: "title" }, { kind: "video", index: 0 }],
    [{ kind: "video", id: "one" }, { kind: "overlay", index: 0, start: -1 }],
    [{ kind: "video", id: "one" }, { kind: "overlay", index: 0, start: 599 }],
    [{ kind: "audio", id: "one" }, { kind: "video", index: 0 }],
  ]) assert.throws(() => transformTrackDrag(value, from, target, media));
  assert.deepEqual(value, before);
});

test("promotion enforces concurrent video limit and refuses a fourth active decoder", () => {
  const value = project({ overlays: [layer("a", { start: 0, end: 3 }), layer("b", { start: 0, end: 3 }), layer("c", { start: 0, end: 3 })] });
  assert.throws(() => transformTrackDrag(value, { kind: "video", id: "one" }, { kind: "overlay", index: 3 }, media), /最多疊加 3/);
  const adjacent = transformTrackDrag(value, { kind: "video", id: "two" }, { kind: "overlay", index: 3 }, media);
  assert.equal(adjacent.start, 3); validateProject(adjacent.project, media);
});

test("promotion checks maximum overlay count and demotion checks V1 count", () => {
  const image = index => ({ id: `image-${index}`, kind: "image", media_id: "image", start: index * 4, end: index * 4 + 2, x: 0.5, y: 0.5, width: 0.5, rotation: 0, opacity: 1 });
  const images = project({ overlays: Array.from({ length: 50 }, (_, index) => image(index)) });
  assert.throws(() => transformTrackDrag(images, { kind: "video", id: "one" }, { kind: "overlay", index: 0 }, media), /最多 50 個圖層/);
  const fullMain = project({ clips: Array.from({ length: 50 }, (_, index) => clip(`clip-${index}`)), overlays: [layer("upper")] });
  assert.throws(() => transformTrackDrag(fullMain, { kind: "overlay", id: "upper" }, { kind: "video", index: 0 }, media), /最多可加入 50 個片段/);
});

test("demotion checks project ten-minute maximum before committing", () => {
  const value = project({ clips: Array.from({ length: 5 }, (_, index) => clip(`long-${index}`, { in: 0, out: 120, speed: 1 })), overlays: [layer("upper")] });
  validateProject(value, media);
  assert.throws(() => transformTrackDrag(value, { kind: "overlay", id: "upper" }, { kind: "video", index: 0 }, media), /最多 10 分鐘/);
});

test("drag previews do not mutate the session and repeated movement commits exactly one undo", () => {
  const session = new ProjectSession(project()); const before = clone(session.project);
  const drag = new TrackDragTransaction(session, { kind: "video", id: "two" }, media);
  drag.update({ kind: "overlay", index: 0, start: 5 }); drag.update({ kind: "overlay", index: 0, start: 7 });
  assert.deepEqual(session.project, before); assert.equal(session.undoStack.length, 0);
  session.project.updated_at = "revision-2";
  assert.equal(drag.commit(), true); assert.equal(session.undoStack.length, 1);
  assert.equal(session.project.updated_at, "revision-2"); assert.equal(session.project.overlays[0].start, 7);
  assert.equal(drag.commit(), false); assert.equal(session.travel(true), true);
  assert.equal(signature(session.project), signature(before)); assert.equal(session.project.updated_at, "revision-2");
  assert.equal(session.travel(false), true); assert.equal(session.project.overlays[0].start, 7);
});

test("cancel restores original preview, never writes history and rejects future updates", () => {
  const session = new ProjectSession(project()), drag = new TrackDragTransaction(session, { kind: "video", id: "two" }, media);
  drag.update({ kind: "overlay", index: 0 }); assert.equal(drag.cancel(), false);
  assert.equal(signature(drag.preview), signature(session.project)); assert.equal(session.undoStack.length, 0);
  assert.equal(drag.commit(), false); assert.throws(() => drag.update({ kind: "overlay", index: 0 }), /已結束/);
});

test("an invalid final target clears a prior legal preview and cannot commit it", () => {
  const session = new ProjectSession(project()), drag = new TrackDragTransaction(session, { kind: "video", id: "two" }, media);
  drag.update({ kind: "overlay", index: 0 });
  assert.throws(() => drag.update({ kind: "overlay", index: 99 }), /層級無效/);
  assert.equal(signature(drag.preview), signature(session.project)); assert.equal(drag.result, null);
  assert.equal(drag.commit(), false); assert.equal(session.undoStack.length, 0);
});

test("conflicting edits cancel the drag and keep newer project changes", () => {
  const session = new ProjectSession(project()), drag = new TrackDragTransaction(session, { kind: "video", id: "two" }, media);
  drag.update({ kind: "overlay", index: 0 }); session.change(value => { value.name = "new name"; });
  assert.throws(() => drag.commit(), /專案內容已變更/);
  assert.equal(session.project.name, "new name"); assert.equal(session.project.overlays.length, 0); assert.equal(session.undoStack.length, 1);
});

test("invalidating geometry retains the original conflict guard before a later valid drop", () => {
  const session = new ProjectSession(project()), drag = new TrackDragTransaction(session, { kind: "video", id: "two" }, media);
  const originalSignature = drag.originalSignature;
  drag.update({ kind: "overlay", index: 0 }); session.change(value => { value.name = "new name"; });
  drag.invalidate(); assert.equal(drag.result, null); assert.equal(signature(drag.preview), originalSignature);
  assert.equal(drag.originalSignature, originalSignature);
  drag.update({ kind: "overlay", index: 0, start: 8 });
  assert.throws(() => drag.commit(), /專案內容已變更/);
  assert.equal(session.project.name, "new name"); assert.equal(session.project.overlays.length, 0); assert.equal(session.undoStack.length, 1);
  assert.throws(() => drag.invalidate(), /已結束/);
});

test("legacy overlays retain distinct tracks while explicit track IDs group clips by first appearance", () => {
  const value = project({ overlays: [layer("a", { start: 0, end: 3 }), layer("b", { track_id: "shared", start: 8, end: 11 }), layer("c", { track_id: "shared", start: 4, end: 7 }), layer("d", { start: 12, end: 15 })] });
  assert.equal(overlayTrackId(value.overlays[0]), "a"); assert.equal(overlayTrackId(value.overlays[1]), "shared");
  assert.deepEqual(overlayTrackGroups(value).map(group => [group.id, group.clips.map(item => item.id)]), [["a", ["a"]], ["shared", ["b", "c"]], ["d", ["d"]]]);
  const before = clone(value); normalizeOverlayTracks(value);
  assert.deepEqual(value.overlays.map(item => item.id), ["a", "c", "b", "d"]);
  assert.deepEqual(overlayTrackGroups(value).map(group => group.id), ["a", "shared", "d"]);
  assert.deepEqual(value.overlays.find(item => item.id === "b"), before.overlays.find(item => item.id === "b"));
});

test("joining a legacy destination adds a second clip to one track and keeps source edit settings", () => {
  const source = layer("source-layer", { start: 8, end: 11, x: 0.2, rotation: 15, opacity: 0.7, fade_in: 0.2, fade_out: 0.3 });
  const value = project({ overlays: [layer("destination", { start: 0, end: 3 }), source] });
  const result = transformTrackDrag(value, { kind: "overlay", id: source.id }, { kind: "overlay", mode: "join", trackId: "destination", start: 3 }, media);
  assert.equal(overlayTrackGroups(result.project).length, 1);
  assert.deepEqual(result.project.overlays.map(item => [item.id, item.track_id, item.start, item.end]), [["destination", "destination", 0, 3], [source.id, "destination", 3, 6]]);
  for (const key of ["in", "out", "speed", "volume", "x", "y", "width", "rotation", "opacity", "fade_in", "fade_out"]) assert.equal(result.project.overlays[1][key], source[key]);
  assert.equal(result.selectedKind, "overlay"); assert.equal(result.selectedId, source.id);
  assert.equal(value.overlays[0].track_id, undefined); assert.equal(value.overlays[1].track_id, undefined);
});

test("main clip can join an existing upper track without adding a row", () => {
  const value = project({ overlays: [layer("upper", { start: 0, end: 3 })] });
  value.clips[1].speed_curve = clone(curve);
  const result = transformTrackDrag(value, { kind: "video", id: "two" }, { kind: "overlay", mode: "join", trackId: "upper", start: 3 }, media);
  assert.deepEqual(result.project.clips.map(item => item.id), ["one", "three"]);
  assert.deepEqual(overlayTrackGroups(result.project).map(group => group.id), ["upper"]);
  assert.equal(result.project.overlays[1].id, "two"); assert.equal(result.project.overlays[1].track_id, "upper");
  assert.deepEqual(result.project.overlays[1].speed_curve, curve);
  close(result.end, 3 + duration(value.clips[1]));
});

test("joining another track sorts clips chronologically and retains the destination stacking order", () => {
  const value = project({ overlays: [layer("lower", { start: 0, end: 3 }), layer("late", { track_id: "middle", start: 8, end: 11 }), layer("early", { track_id: "middle", start: 0, end: 3 }), layer("moving", { start: 14, end: 17 }), layer("upper", { start: 0, end: 3 })] });
  normalizeOverlayTracks(value);
  const result = transformTrackDrag(value, { kind: "overlay", id: "moving" }, { kind: "overlay", mode: "join", trackId: "middle", start: 4 }, media);
  assert.deepEqual(overlayTrackGroups(result.project).map(group => [group.id, group.clips.map(item => item.id)]), [["lower", ["lower"]], ["middle", ["early", "moving", "late"]], ["upper", ["upper"]]]);
});

test("extracting a shared-track clip inserts at a group boundary while its source track stays intact", () => {
  const value = project({ overlays: [layer("first", { track_id: "shared", start: 0, end: 3 }), layer("second", { track_id: "shared", start: 4, end: 7 }), layer("upper", { start: 0, end: 3 })] });
  const result = transformTrackDrag(value, { kind: "overlay", id: "first" }, { kind: "overlay", mode: "insert", index: 1 }, media);
  assert.deepEqual(overlayTrackGroups(result.project).map(group => [group.id, group.clips.map(item => item.id)]), [["shared", ["second"]], ["first", ["first"]], ["upper", ["upper"]]]);
  assert.equal(result.project.overlays[0].track_id, "shared"); assert.equal(result.project.overlays[1].track_id, undefined);
  const demoted = transformTrackDrag(value, { kind: "overlay", id: "first" }, { kind: "video", index: 1 }, media);
  assert.deepEqual(overlayTrackGroups(demoted.project).map(group => [group.id, group.clips.map(item => item.id)]), [["shared", ["second"]], ["upper", ["upper"]]]);
});

test("extracting the legacy track leader uses a collision-free bounded track ID", () => {
  const id = "a".repeat(64), firstCandidate = "a".repeat(58) + "_track";
  const value = project({ overlays: [layer(id, { track_id: id, start: 0, end: 3 }), layer("stays", { track_id: id, start: 4, end: 7 }), layer("other", { track_id: firstCandidate, start: 0, end: 3 })] });
  const result = transformTrackDrag(value, { kind: "overlay", id }, { kind: "overlay", index: 2 }, media);
  const moved = result.project.overlays.find(item => item.id === id);
  assert.equal(moved.track_id, "a".repeat(57) + "_track2");
  assert.match(moved.track_id, /^[A-Za-z0-9_-]{1,64}$/);
  assert.equal(overlayTrackGroups(result.project).length, 3);
});

test("joining its own singleton is a no-op until its start changes and retains an explicit stable track ID", () => {
  for (const values of [{}, { track_id: "stable" }]) {
    const value = project({ overlays: [layer("self", values)] }), key = values.track_id || "self";
    const same = transformTrackDrag(value, { kind: "overlay", id: "self" }, { kind: "overlay", mode: "join", trackId: key }, media);
    assert.equal(same.changed, false);
    const moved = transformTrackDrag(value, { kind: "overlay", id: "self" }, { kind: "overlay", mode: "join", trackId: key, start: 10 }, media);
    assert.equal(moved.start, 10); assert.equal(overlayTrackId(moved.project.overlays[0]), key);
    assert.equal(moved.project.overlays[0].track_id, values.track_id);
  }
});

test("video, image and text clips can share one track and reposition within its empty time ranges", () => {
  const text = { id: "title", kind: "text", start: 12, end: 15, x: 0.2, y: 0.3, width: 0.6, rotation: 5, opacity: 0.8, text: "title", font_size: 0.05, color: "#ffffff", background: "transparent", bold: true, align: "center", position_keyframes: [{ time: 0, x: 0.2, y: 0.3 }, { time: 2, x: 0.8, y: 0.7, easing: "ease_out" }] };
  const image = { id: "picture", kind: "image", media_id: "image", start: 7, end: 10, x: 0.4, y: 0.3, width: 0.5, rotation: 0, opacity: 1 };
  const value = project({ overlays: [layer("video", { start: 0, end: 3 }), image, text] });
  const joinedImage = transformTrackDrag(value, { kind: "overlay", id: "picture" }, { kind: "overlay", mode: "join", trackId: "video" }, media);
  const joinedText = transformTrackDrag(joinedImage.project, { kind: "overlay", id: "title" }, { kind: "overlay", mode: "join", trackId: "video", start: 3 }, media);
  assert.deepEqual(overlayTrackGroups(joinedText.project).map(group => group.clips.map(item => item.kind)), [["video", "text", "image"]]);
  const repositioned = transformTrackDrag(joinedText.project, { kind: "overlay", id: "title" }, { kind: "overlay", mode: "join", trackId: "video", start: 12 }, media);
  assert.deepEqual(repositioned.project.overlays.map(item => item.id), ["video", "picture", "title"]);
  assert.deepEqual(repositioned.project.overlays[2].position_keyframes, core.canonicalPositionKeyframes(text));
  assert.equal(overlayTrackGroups(repositioned.project).length, 1);
});

test("moving layer arrows changes the entire shared track z-order rather than separating its clips", () => {
  const value = project({ overlays: [layer("a", { track_id: "shared", start: 0, end: 3 }), layer("b", { track_id: "shared", start: 4, end: 7 }), layer("upper", { start: 0, end: 3 })] });
  assert.equal(moveOverlayTrack(value, "b", 1), true);
  assert.deepEqual(value.overlays.map(item => item.id), ["upper", "a", "b"]);
  assert.equal(moveOverlayTrack(value, "shared", 1), false);
  assert.equal(moveOverlayTrack(value, "shared", -1), true);
  assert.deepEqual(value.overlays.map(item => item.id), ["a", "b", "upper"]);
  assert.throws(() => moveOverlayTrack(value, "shared", 0.5), /層級無效/);
});

test("same-track overlap rejects the drop and invalidates a prior legal shared-track preview", () => {
  const session = new ProjectSession(project({ overlays: [layer("destination", { start: 0, end: 3 }), layer("moving", { start: 5, end: 8 })] }));
  const original = clone(session.project), drag = new TrackDragTransaction(session, { kind: "overlay", id: "moving" }, media);
  drag.update({ kind: "overlay", mode: "join", trackId: "destination", start: 3 });
  assert.throws(() => drag.update({ kind: "overlay", mode: "join", trackId: "destination", start: 2 }), /同一.*軌.*重疊|同一.*層.*重疊|重疊/);
  assert.equal(drag.result, null); assert.equal(drag.commit(), false);
  assert.deepEqual(session.project, original); assert.equal(session.undoStack.length, 0);
});

test("shared-track join is one undo operation and an unknown destination cannot commit", () => {
  const session = new ProjectSession(project({ overlays: [layer("destination", { start: 0, end: 3 }), layer("moving", { start: 5, end: 8 })] }));
  const drag = new TrackDragTransaction(session, { kind: "overlay", id: "moving" }, media);
  drag.update({ kind: "overlay", mode: "join", trackId: "destination", start: 3 }); assert.equal(drag.commit(), true);
  assert.equal(session.undoStack.length, 1); assert.equal(overlayTrackGroups(session.project).length, 1);
  assert.equal(session.travel(true), true); assert.equal(overlayTrackGroups(session.project).length, 2);
  assert.equal(session.travel(false), true); assert.equal(overlayTrackGroups(session.project).length, 1);
  const invalid = new TrackDragTransaction(session, { kind: "overlay", id: "moving" }, media);
  assert.throws(() => invalid.update({ kind: "overlay", mode: "join", trackId: "missing", start: 3 }), /找不到目標/);
  assert.equal(invalid.commit(), false); assert.equal(session.undoStack.length, 1);
});
