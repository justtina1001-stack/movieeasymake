"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { clone, positionAt, positionChanges, upsertPositionKeyframe, keyframeIndexAt, validatePositionKeyframes, canonicalOverlay, signature, overlayRasterKey, shiftOverlayTime, splitOverlayAt, OverlayTransaction, ProjectSession, resolveDraft, transformOverlay } = require("../static/editor.js");
const point = (time, x, y = 0.5, easing = "linear") => ({ time, x, y, easing });
const layer = (changes = {}) => ({ id: "title", kind: "text", start: 1, end: 5, x: 0.5, y: 0.5, width: 0.5, rotation: 0, opacity: 1, text: "文字位移", font_size: 0.06, color: "#ffffff", background: "transparent", bold: true, align: "center", position_keyframes: [point(0, 0.1), point(4, 0.9)], ...changes });
const project = (item = layer()) => ({ id: "motion", name: "動畫", updated_at: "r1", width: 1280, height: 720, fps: 24, clips: [{ id: "v", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [], overlays: [item] });
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("position holds at either end, interpolates both axes, and falls back to static placement", () => {
  const value = layer({ position_keyframes: [point(0, 0.1, 0.2), point(4, 0.9, 0.8)] });
  assert.deepEqual(positionAt(value, -1), { x: 0.1, y: 0.2 });
  close(positionAt(value, 2).x, 0.5); close(positionAt(value, 2).y, 0.5);
  assert.deepEqual(positionAt(value, 4), { x: 0.9, y: 0.8 });
  assert.deepEqual(positionAt(value, 10), positionAt(value, 4));
  assert.deepEqual(positionAt(layer({ position_keyframes: [] }), 2), { x: 0.5, y: 0.5 });
  assert.deepEqual(positionAt(layer({ position_keyframes: [point(1, 0.3)] }), 3), { x: 0.3, y: 0.5 });
});
for (const [easing, expected] of [["linear", 0.25], ["ease_in", 0.0625], ["ease_out", 0.4375], ["ease_in_out", 0.125]]) test(`${easing} follows the same quadratic curve used by the exporter`, () => {
  const value = layer({ position_keyframes: [point(0, 0, 1, easing), point(4, 1, 0)] });
  close(positionAt(value, 1).x, expected); close(positionAt(value, 1).y, 1 - expected);
  if (easing === "ease_in_out") close(positionAt(value, 3).x, 0.875);
});
test("invalid, duplicate, unsorted, over-limit, nonfinite or unsupported keyframes are rejected", () => {
  const good = point(0, 0.5);
  for (const bad of [null, {}, [null], [false], [{ ...good, time: NaN }], [{ ...good, time: true }], [{ ...good, x: Infinity }], [{ ...good, y: -0.1 }], [{ ...good, time: 601 }], [{ ...good, time: -601 }], [{ ...good, easing: null }], [{ ...good, easing: "bezier" }], [{ ...good, other: 1 }], [good, good], [point(2, 0.1), point(1, 0.9)], Array.from({ length: 101 }, (_, i) => point(i, 0.5))]) assert.throws(() => validatePositionKeyframes(layer({ position_keyframes: bad })));
  assert.throws(() => validatePositionKeyframes(layer({ kind: "video" })));
  assert.doesNotThrow(() => validatePositionKeyframes(layer({ kind: "video", position_keyframes: [] })));
});
test("upserting uses the evaluated pose, preserves existing easing, sorts points and never mutates input", () => {
  const value = layer(), original = clone(value), points = upsertPositionKeyframe(value, 2);
  assert.equal(points.length, 3); close(points[1].x, 0.5); assert.deepEqual(value, original);
  value.position_keyframes[0].easing = "ease_in";
  const replaced = upsertPositionKeyframe(value, 0, { x: 0.2, y: 0.7 });
  assert.equal(replaced.length, 2); assert.equal(replaced[0].easing, "ease_in");
  assert.equal(keyframeIndexAt({ position_keyframes: replaced }, 0), 0);
  for (const time of [-1, 5, NaN]) assert.throws(() => upsertPositionKeyframe(value, time));
});
test("a full layer can replace an existing keyframe but cannot add a 101st", () => {
  const value = layer({ end: 101, position_keyframes: Array.from({ length: 100 }, (_, i) => point(i, 0.5)) });
  assert.equal(upsertPositionKeyframe(value, 20).length, 100);
  assert.throws(() => upsertPositionKeyframe(value, 99.5));
});
test("legacy and empty keyframe fields stay signature-compatible; easing defaults are canonical", () => {
  const staticLayer = layer(); delete staticLayer.position_keyframes;
  assert.equal(signature(project(staticLayer)), signature(project({ ...staticLayer, position_keyframes: [] })));
  const animated = layer({ position_keyframes: [{ time: 0, x: 0.1, y: 0.2 }] });
  assert.equal(canonicalOverlay(animated).position_keyframes[0].easing, "linear");
  assert.notEqual(signature(project(animated)), signature(project(staticLayer)));
});
test("moving a layer carries its keyframes without modifying local timing", () => {
  const value = project(), moved = shiftOverlayTime(value, "title", "move", 2).project.overlays[0];
  assert.equal(moved.start, 3); assert.deepEqual(moved.position_keyframes, value.overlays[0].position_keyframes);
  close(positionAt(moved, 2).x, 0.5);
});
test("left trim retains hidden points and preserves exact easing; extension recovers the original animation", () => {
  const value = project(layer({ position_keyframes: [point(0, 0.1, 0.2, "ease_in_out"), point(4, 0.9, 0.8)] }));
  const shortened = shiftOverlayTime(value, "title", "left", 1).project;
  assert.equal(shortened.overlays[0].position_keyframes[0].time, -1);
  for (const t of [0, 0.25, 1, 2.5, 3]) assert.deepEqual(positionAt(shortened.overlays[0], t), positionAt(value.overlays[0], t + 1));
  const restored = shiftOverlayTime(shortened, "title", "left", -1).project;
  assert.deepEqual(restored.overlays[0].position_keyframes, value.overlays[0].position_keyframes);
  const inspectorTrim = transformOverlay(value, "title", { start: 2 }).project;
  assert.deepEqual(inspectorTrim.overlays[0].position_keyframes, shortened.overlays[0].position_keyframes);
});
test("right trim and extension do not discard later keyframes", () => {
  const value = project(), trimmed = shiftOverlayTime(value, "title", "right", -2).project;
  assert.deepEqual(trimmed.overlays[0].position_keyframes, value.overlays[0].position_keyframes);
  const extended = shiftOverlayTime(trimmed, "title", "right", 2).project;
  assert.equal(signature(extended), signature(value));
});
test("splitting retains an uninterrupted easing trajectory in both halves and remains independently editable", () => {
  const value = project(layer({ position_keyframes: [point(0, 0.1, 0.5, "ease_out"), point(4, 0.9)] })), original = clone(value.overlays[0]);
  splitOverlayAt(value, "title", 2.5, () => "second");
  const [first, second] = value.overlays;
  for (const t of [0, 0.5, 1.49]) assert.deepEqual(positionAt(first, t), positionAt(original, t));
  for (const t of [0, 0.1, 1, 2.5]) assert.deepEqual(positionAt(second, t), positionAt(original, t + 1.5));
  second.position_keyframes[0].x = 0.4; assert.equal(first.position_keyframes[0].x, 0.1);
});
test("a complete position drag inserts just one keyframe and commits as one undo operation", () => {
  const session = new ProjectSession(project()), transaction = new OverlayTransaction(session, "title");
  transaction.updatePosition(3, { x: 0.6, y: 0.4 }); transaction.updatePosition(3, { x: 0.7, y: 0.3 });
  assert.equal(session.project.overlays[0].position_keyframes.length, 2);
  transaction.commit(); assert.equal(session.undoStack.length, 1);
  assert.equal(session.project.overlays[0].position_keyframes.length, 3);
  assert.equal(session.project.overlays[0].position_keyframes[1].x, 0.7);
  session.travel(true); assert.equal(session.project.overlays[0].position_keyframes.length, 2);
  session.travel(false); assert.equal(session.project.overlays[0].position_keyframes[1].x, 0.7);
});
test("static dragging preserves static placement and cancelled or stale animation drags do not alter a project", () => {
  const value = layer({ position_keyframes: [] }); assert.deepEqual(positionChanges(value, 3, { x: 0.4, y: 0.2 }), { x: 0.4, y: 0.2 });
  const session = new ProjectSession(project()), transaction = new OverlayTransaction(session, "title");
  transaction.updatePosition(3, { x: 0.7, y: 0.3 }); transaction.cancel(); assert.equal(session.project.overlays[0].position_keyframes.length, 2);
  const stale = new OverlayTransaction(session, "title"); stale.updatePosition(3, { x: 0.7, y: 0.3 }); session.change(p => { p.name = "新名稱"; });
  assert.throws(() => stale.commit()); assert.equal(session.project.overlays[0].position_keyframes.length, 2);
});
test("animation does not invalidate text raster caching", () => {
  const value = layer(), key = overlayRasterKey(value, 1280, 720);
  assert.equal(overlayRasterKey({ ...value, position_keyframes: [point(0, 0.9), point(1, 0.1)] }, 1280, 720), key);
});
test("clicking a timeline diamond selects its layer, refreshes the selection and seeks without editing", () => {
  const fs = require("node:fs"), vm = require("node:vm");
  const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
  const start = source.indexOf("  function jumpPositionKeyframe(event) {");
  const code = source.slice(start, source.indexOf('  $("positionKeyframeList").onclick', start));
  const value = project(), state = { selected: "other", selectedKind: "video", playhead: 0 }, calls = [];
  const sandbox = { state, locked: () => false, project: () => value, overlays: p => p.overlays,
    pause: () => calls.push("pause"), renderTimeline: () => calls.push(`selected:${state.selected}`),
    seek: (time, playing) => { state.playhead = time; assert.equal(playing, false); calls.push("seek"); },
    renderInspector: () => {}, renderDisabled: () => {} };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  const event = { target: { closest: () => ({ dataset: { positionLayer: "title", positionTime: "2" } }) }, preventDefault() {}, stopPropagation() {} };
  assert.equal(sandbox.jumpPositionKeyframe(event), true);
  assert.deepEqual(calls, ["pause", "selected:title", "seek"]); assert.equal(state.playhead, 3);
  assert.equal(state.selectedKind, "overlay"); assert.equal(value.overlays[0].position_keyframes.length, 2);
});
test("draft recovery preserves animated fields and saving refuses silent backend loss", async () => {
  const base = project(layer({ position_keyframes: [] })), draft = project();
  assert.equal(resolveDraft(base, { project: draft, savedSignature: signature(base) }).status, "recoverable");
  const session = new ProjectSession(draft); session.change(p => { p.name = "動態草稿"; });
  await assert.rejects(session.save(async p => { const saved = clone(p); delete saved.overlays[0].position_keyframes; return saved; }), /未完整保存/);
  assert.equal(session.dirty, true); assert.equal(session.project.overlays[0].position_keyframes.length, 2);
  await session.save(async p => ({ ...clone(p), updated_at: "r2" })); assert.equal(session.dirty, false);
});
