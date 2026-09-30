"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  clone, overlays, canonicalOverlay, validateOverlays, validateProject,
  signature, canonicalSavedSignature, resolveDraft, overlayRasterKey,
  transformOverlay, shiftOverlayTime, splitOverlayAt, OverlayTransaction,
  ProjectSession, PreviewRequestQueue,
} = require("../static/editor.js");

const textLayer = (changes = {}) => ({
  id: "title", kind: "text", start: 1, end: 4, x: 0.5, y: 0.8,
  width: 0.7, rotation: 0, opacity: 1, text: "中文標題\nSecond line",
  font_size: 0.06, color: "#ffffff", background: "transparent", bold: true, align: "center",
  ...changes,
});
const imageLayer = (changes = {}) => ({
  id: "logo", kind: "image", media_id: "image", start: 2, end: 5,
  x: 0.8, y: 0.2, width: 0.15, rotation: 30, opacity: 0.5, ...changes,
});
const project = (layers = [textLayer()]) => ({
  id: "overlay-project", name: "文字圖片測試", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [{ id: "video-clip", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }],
  audio_clips: [], overlays: layers,
});
const media = () => new Map([
  ["video", { kind: "video", duration: 10, has_audio: true }],
  ["audio", { kind: "audio", duration: 10, has_audio: true }],
  ["image", { kind: "image", duration: 0, width: 400, height: 200, has_audio: false }],
]);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("legacy projects, signatures and saved drafts treat missing overlays as an empty list", () => {
  const legacy = project([]); delete legacy.overlays;
  assert.deepEqual(overlays(legacy), []);
  assert.equal(signature(legacy), signature(project([])));
  const { name, clips, audio_clips, width, height, fps } = legacy;
  const oldSignature = JSON.stringify({ name, clips, audio_clips, width, height, fps });
  assert.equal(canonicalSavedSignature(oldSignature), signature(project([])));
  assert.equal(new ProjectSession(legacy).dirty, false);
  assert.deepEqual(new ProjectSession(legacy).project.overlays, []);
  assert.equal(resolveDraft(project([]), { project: legacy, savedSignature: oldSignature }, media()).status, "equivalent");
});

test("a draft adding overlays to a legacy project recovers with the latest revision without mutating either input", () => {
  const server = project([]), edited = project([textLayer(), imageLayer()]);
  const baseline = clone(server); delete baseline.overlays;
  const draft = { project: edited, savedSignature: JSON.stringify(baseline), savedAt: 1 };
  const before = clone(draft); server.updated_at = "r2";
  const restored = resolveDraft(server, draft, media());
  assert.equal(restored.status, "recoverable");
  assert.equal(restored.project.updated_at, "r2");
  assert.deepEqual(restored.project.overlays, edited.overlays);
  assert.deepEqual(draft, before); assert.deepEqual(server.overlays, []);
});

test("conflicting or missing-image overlay drafts preserve the server project", () => {
  const base = project(), edited = project([textLayer({ text: "草稿內容" }), imageLayer()]);
  const server = project([textLayer({ x: 0.7 })]);
  const draft = { project: edited, savedSignature: signature(base) };
  const result = resolveDraft(server, draft, media());
  assert.equal(result.status, "conflict");
  assert.deepEqual(result.project, server); assert.deepEqual(result.draftProject, edited);
  const missing = media(); missing.delete("image");
  assert.equal(resolveDraft(base, draft, missing).status, "invalid");
});

test("canonical layers ignore key order, color case and line-ending differences but retain geometry and stacking", () => {
  const first = textLayer({ color: "#AaBBcc", background: "#1122AA", text: "第一行\r\n第二行\r第三行" });
  const reordered = Object.fromEntries(Object.entries(first).reverse());
  reordered.color = "#aabbcc"; reordered.background = "#1122aa"; reordered.text = "第一行\n第二行\n第三行";
  assert.deepEqual(canonicalOverlay(first), canonicalOverlay(reordered));
  assert.equal(signature(project([first])), signature(project([reordered])));
  assert.notEqual(signature(project([first])), signature(project([{ ...first, opacity: 0.5 }])));
  assert.notEqual(signature(project([first, imageLayer()])), signature(project([imageLayer(), first])));
});

test("layer schema rejects invalid identifiers, duplicates across tracks, unknown kinds and malformed lists", () => {
  for (const id of ["", "a b", "../layer", "x".repeat(65), 123, "video-clip"]) {
    assert.throws(() => validateOverlays(project([textLayer({ id })])), String(id));
  }
  assert.throws(() => validateOverlays(project([textLayer(), imageLayer({ id: "title" })])));
  const sameAudio = project([textLayer({ id: "a1" })]);
  sameAudio.audio_clips = [{ id: "a1" }];
  assert.throws(() => validateOverlays(sameAudio));
  for (const layers of [null, {}, "bad", [null], [textLayer({ kind: "video" })]]) {
    assert.throws(() => validateOverlays({ ...project(), overlays: layers }));
  }
});

test("geometry accepts documented endpoints and rejects nonfinite, nonnumeric and out-of-range fields", () => {
  for (const changes of [
    { start: 0, end: 1 / 60 }, { start: 599, end: 600 },
    { x: 0, y: 1, width: 0.02, rotation: -180, opacity: 0 },
    { x: 1, y: 0, width: 2, rotation: 180, opacity: 1 },
  ]) assert.doesNotThrow(() => validateOverlays({ ...project([textLayer(changes)]), fps: 60 }));
  const invalid = { start: [-1, NaN, true], end: [1, 600.1, Infinity], x: [-0.01, 1.01, "0.5"],
    y: [-0.01, 1.01, null], width: [0.019, 2.001, NaN], rotation: [-181, 181, Infinity], opacity: [-0.01, 1.01, false] };
  for (const [field, values] of Object.entries(invalid)) for (const value of values) {
    assert.throws(() => validateOverlays(project([textLayer({ [field]: value })])), `${field}=${String(value)}`);
  }
  assert.throws(() => validateOverlays(project([textLayer({ start: 0, end: 0.01 })])));
});

test("text limits count Unicode characters, accept empty editing states and normalize Windows line endings", () => {
  for (const text of ["", "  ", "𠮷".repeat(500), Array(10).fill("行").join("\r\n")]) {
    assert.doesNotThrow(() => validateOverlays(project([textLayer({ text })])));
  }
  for (const text of ["𠮷".repeat(501), Array(11).fill("行").join("\n"), null, 5]) {
    assert.throws(() => validateOverlays(project([textLayer({ text })])));
  }
  for (const changes of [{ font_size: 0.009 }, { font_size: 0.301 }, { font_size: "0.06" },
    { color: "#fff" }, { color: "red" }, { background: "none" }, { bold: 1 }, { align: "justify" }]) {
    assert.throws(() => validateOverlays(project([textLayer(changes)])));
  }
});

test("image overlays require an existing image source and never treat audio or video as still images", () => {
  assert.doesNotThrow(() => validateProject(project([imageLayer()]), media()));
  for (const media_id of ["absent", "video", "audio", null]) {
    assert.throws(() => validateOverlays(project([imageLayer({ media_id })]), media()));
  }
});

test("at most twelve layers overlap, exact end/start boundaries do not overlap, and fifty sequential layers are allowed", () => {
  const batch = (count, start, end, prefix) => Array.from({ length: count }, (_, i) => textLayer({ id: `${prefix}${i}`, start, end }));
  assert.doesNotThrow(() => validateOverlays(project([...batch(12, 0, 1, "a"), ...batch(12, 1, 2, "b")])));
  assert.throws(() => validateOverlays(project(batch(13, 0, 1, "x"))), /12/);
  const sequential = Array.from({ length: 50 }, (_, i) => textLayer({ id: `s${i}`, start: i, end: i + 1 }));
  assert.doesNotThrow(() => validateOverlays(project(sequential)));
  assert.throws(() => validateOverlays(project([...sequential, textLayer({ id: "extra", start: 50, end: 51 })])), /50/);
});

test("transform changes only the requested layer in a detached project and rejects invalid edits atomically", () => {
  const value = project([textLayer(), imageLayer()]), before = clone(value);
  const result = transformOverlay(value, "title", { x: 0.2, rotation: -45 }, media());
  assert.equal(result.changed, true); assert.equal(result.layer.x, 0.2); assert.equal(result.layer.rotation, -45);
  assert.deepEqual(result.project.overlays[1], before.overlays[1]); assert.deepEqual(value, before);
  assert.equal(transformOverlay(value, "title", { x: 0.5 }, media()).changed, false);
  assert.throws(() => transformOverlay(value, "title", { width: 0 }, media()));
  assert.throws(() => transformOverlay(value, "missing", { x: 0.1 }, media()));
  assert.deepEqual(value, before);
});

test("moving a layer clamps to zero and ten minutes while preserving its duration", () => {
  const value = project([textLayer({ start: 5, end: 8 })]);
  const left = shiftOverlayTime(value, "title", "move", -20);
  assert.deepEqual([left.layer.start, left.layer.end, left.appliedDelta, left.clamped], [0, 3, -5, true]);
  const right = shiftOverlayTime(value, "title", "move", 999);
  assert.deepEqual([right.layer.start, right.layer.end, right.appliedDelta, right.clamped], [597, 600, 592, true]);
  const normal = shiftOverlayTime(value, "title", "move", 1.25);
  assert.deepEqual([normal.layer.start, normal.layer.end, normal.clamped], [6.25, 9.25, false]);
  assert.deepEqual([value.overlays[0].start, value.overlays[0].end], [5, 8]);
});

test("trimming either overlay edge holds the opposite edge and leaves at least one output frame", () => {
  const value = project([textLayer({ start: 5, end: 8 })]); value.fps = 60;
  const left = shiftOverlayTime(value, "title", "left", 999);
  assert.equal(left.layer.end, 8); close(left.layer.start, 8 - 1 / 60); assert.equal(left.clamped, true);
  const right = shiftOverlayTime(value, "title", "right", -999);
  assert.equal(right.layer.start, 5); close(right.layer.end, 5 + 1 / 60);
  assert.equal(shiftOverlayTime(value, "title", "left", -999).layer.start, 0);
  assert.equal(shiftOverlayTime(value, "title", "right", 999).layer.end, 600);
  for (const [id, mode, delta] of [["missing", "move", 1], ["title", "resize", 1], ["title", "move", NaN]]) {
    assert.throws(() => shiftOverlayTime(value, id, mode, delta));
  }
});

test("splitting preserves visual properties, stacking position and adjacent timing", () => {
  const original = textLayer(), value = project([original, imageLayer()]);
  assert.equal(splitOverlayAt(value, "title", 2.5, () => "second-title"), "second-title");
  assert.deepEqual(value.overlays, [{ ...original, track_id: "title", end: 2.5 }, { ...original, track_id: "title", id: "second-title", start: 2.5 }, imageLayer()]);
  assert.doesNotThrow(() => validateProject(value, media()));
});

test("invalid split times and duplicate split IDs cannot modify the editing session", () => {
  const session = new ProjectSession(project()), before = clone(session.project);
  for (const time of [1, 4, 1.01, 3.99, NaN]) {
    assert.throws(() => session.change(value => splitOverlayAt(value, "title", time, () => "new")));
    assert.deepEqual(session.project, before);
  }
  assert.throws(() => session.change(value => splitOverlayAt(value, "title", 2, () => "title")));
  assert.deepEqual(session.project, before); assert.equal(session.undoStack.length, 0);
});

test("one gesture produces one undo step, preview updates remain detached, and redo keeps all layer fields", () => {
  const session = new ProjectSession(project()), before = clone(session.project);
  const drag = new OverlayTransaction(session, "title", media());
  drag.update({ x: 0.6 }); drag.update({ x: 0.7, y: 0.4, rotation: 35 });
  assert.deepEqual(session.project, before); assert.equal(session.undoStack.length, 0);
  assert.equal(drag.commit(), true); assert.equal(session.undoStack.length, 1);
  const final = clone(session.project); assert.equal(final.overlays[0].x, 0.7);
  assert.equal(drag.commit(), false);
  session.travel(true); assert.deepEqual(session.project, before);
  session.travel(false); assert.deepEqual(session.project, final);
});

test("cancelled and unchanged gestures never change the project or undo history", () => {
  const session = new ProjectSession(project()), before = clone(session.project);
  const cancelled = new OverlayTransaction(session, "title"); cancelled.updateTime("move", 3);
  assert.equal(cancelled.cancel(), false); assert.equal(cancelled.commit(), false);
  assert.throws(() => cancelled.update({ x: 0.1 }), /已結束/);
  const unchanged = new OverlayTransaction(session, "title"); unchanged.update({ x: 0.5 });
  assert.equal(unchanged.commit(), false); assert.deepEqual(session.project, before); assert.equal(session.undoStack.length, 0);
});

test("a stale gesture cannot overwrite another edit, while a new server revision alone does not invalidate it", () => {
  const session = new ProjectSession(project()), stale = new OverlayTransaction(session, "title");
  stale.update({ x: 0.9 }); session.change(value => { value.name = "another edit"; });
  assert.throws(() => stale.commit(), /已變更/); assert.equal(session.project.overlays[0].x, 0.5);
  const valid = new OverlayTransaction(session, "title"); valid.updateTime("move", 2);
  session.project.updated_at = "r2";
  assert.equal(valid.commit(), true); assert.equal(session.project.updated_at, "r2");
  assert.equal(session.project.overlays[0].start, 3);
  session.travel(true); assert.equal(session.project.updated_at, "r2"); assert.equal(session.project.overlays[0].start, 1);
});

test("text raster keys exclude placement, opacity, rotation and timing but include every raster-affecting field", () => {
  const layer = textLayer(), key = overlayRasterKey(layer, 1280, 720);
  assert.equal(overlayRasterKey({ ...layer, id: "other", x: 0, y: 1, rotation: 90, opacity: 0.2, start: 8, end: 12 }, 1280, 720), key);
  assert.equal(overlayRasterKey({ ...layer, color: "#FFFFFF", text: layer.text.replace("\n", "\r\n") }, 1280, 720), key);
  for (const changes of [{ text: "new text" }, { font_size: 0.08 }, { color: "#ff0000" },
    { background: "#000000" }, { bold: false }, { align: "left" }, { width: 0.5 }]) {
    assert.notEqual(overlayRasterKey({ ...layer, ...changes }, 1280, 720), key);
  }
  assert.notEqual(overlayRasterKey(layer, 720, 1280), key);
  assert.notEqual(overlayRasterKey(layer, 1280, 1080), key);
});

test("saving tolerates server color/line-ending normalization but rejects silently lost overlays", async () => {
  const session = new ProjectSession(project()); session.change(value => { value.overlays[0].text = "改過\r\n文字"; });
  await session.save(async snapshot => ({ ...snapshot, updated_at: "r2", overlays: snapshot.overlays.map(layer => ({ ...layer, text: layer.text.replace(/\r\n/g, "\n"), color: layer.color.toUpperCase() })) }));
  assert.equal(session.dirty, false); assert.equal(session.project.overlays[0].text, "改過\n文字");
  session.change(value => { value.overlays.push(imageLayer()); });
  await assert.rejects(session.save(async snapshot => ({ ...snapshot, updated_at: "r3", overlays: [] })), /圖層/);
  assert.equal(session.dirty, true); assert.equal(session.project.overlays.length, 2);
});

test("preview queue starts at most two requests and advances waiting work in submission order", async () => {
  const queue = new PreviewRequestQueue(2), gates = Array.from({ length: 5 }, deferred), started = [];
  let running = 0, maximum = 0;
  const jobs = gates.map((gate, index) => queue.run(async () => {
    started.push(index); running++; maximum = Math.max(maximum, running);
    try { return await gate.promise; } finally { running--; }
  }));
  await tick(); assert.deepEqual(started, [0, 1]);
  gates[1].resolve("one"); await tick(); assert.deepEqual(started, [0, 1, 2]);
  gates[0].resolve("zero"); await tick(); assert.deepEqual(started, [0, 1, 2, 3]);
  gates[2].resolve("two"); await tick(); assert.deepEqual(started, [0, 1, 2, 3, 4]);
  gates[3].resolve("three"); gates[4].resolve("four");
  assert.deepEqual(await Promise.all(jobs), ["zero", "one", "two", "three", "four"]);
  await tick(); assert.equal(maximum, 2); assert.equal(queue.active, 0); assert.equal(queue.waiting.length, 0);
});

test("preview queue releases slots after rejected requests and synchronous failures", async () => {
  const queue = new PreviewRequestQueue(2), first = deferred(), started = [];
  const jobs = [
    queue.run(() => { started.push(0); return first.promise; }),
    queue.run(() => { started.push(1); throw new Error("sync failure"); }),
    queue.run(() => { started.push(2); return Promise.reject(new Error("http failure")); }),
    queue.run(() => { started.push(3); return "recovered"; }),
  ];
  const settled = Promise.allSettled(jobs);
  await tick(); assert.deepEqual(started, [0, 1, 2, 3]);
  first.resolve("first"); const results = await settled; await tick();
  assert.deepEqual(results.map(result => result.status), ["fulfilled", "rejected", "rejected", "fulfilled"]);
  assert.equal(results[3].value, "recovered"); assert.equal(queue.active, 0);
  assert.equal(await queue.run(() => "next batch"), "next batch");
});
