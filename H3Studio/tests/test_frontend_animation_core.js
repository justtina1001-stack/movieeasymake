"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../static/editor.js");
const { clone, signature, canonicalSavedSignature, editableContent, canonicalOverlay, overlayRasterKey,
  validateProject, normalizeProjectAnimations, requireAnimationSupport, ProjectSession, resolveDraft,
  splitAt, splitOverlayAt, trimTimelineClip, transformOverlay, transformClipSpeed, promoteClip,
  transformTrackDrag, reorder, duration, transitionPairs, detachAudio } = core;

const effect = (type = "fade", length = 0.5) => ({ type, duration: length });
const clip = (id, changes = {}) => ({ id, media_id: "source", in: 0, out: 4, speed: 1, volume: 1, ...changes });
const video = (id, changes = {}) => ({ ...clip(id), kind: "video", start: 0, end: 4,
  x: 0.5, y: 0.5, width: 0.6, opacity: 1, rotation: 0, ...changes });
const title = (id = "title", changes = {}) => ({ id, kind: "text", start: 0, end: 4,
  x: 0.5, y: 0.5, width: 0.6, opacity: 1, rotation: 0, text: "動畫", font_size: 0.08,
  color: "#ffffff", background: "transparent", bold: false, align: "center", ...changes });
const project = (changes = {}) => ({ id: "animation-project", name: "動畫測試", updated_at: "r1",
  clips: [clip("first"), clip("second"), clip("third")], audio_clips: [], overlays: [],
  width: 1280, height: 720, fps: 24, ...changes });
const media = new Map([["source", { id: "source", kind: "video", duration: 120, width: 1280, height: 720, has_audio: true }]]);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const link = (nextId, length = 1, type = "crossfade") => ({ type, duration: length, next_id: nextId });

test("legacy signatures and drafts remain equivalent when disabled animation defaults are added", () => {
  const legacy = project({ overlays: [title()] }), value = clone(legacy);
  for (const item of [...value.clips, ...value.overlays]) {
    item.animation_in = effect("none", 0.5); item.animation_out = effect("fade", 0);
    item.transition_out = { type: "none", duration: 0 };
  }
  validateProject(value, media);
  assert.equal(signature(legacy), signature(value));
  assert.equal(canonicalSavedSignature(signature(legacy)), signature(value));
  assert.equal(resolveDraft(legacy, { project: value, savedSignature: signature(legacy) }, media).status, "equivalent");
});

test("animation and bound transition edits participate in canonical content, dirty state and undo", () => {
  const original = project({ overlays: [title()] }), session = new ProjectSession(original);
  session.change(value => {
    value.clips[0].animation_in = effect("slide_up");
    value.clips[0].transition_out = link("second");
    value.overlays[0].animation_out = effect("zoom_out");
  });
  assert.equal(session.dirty, true);
  assert.deepEqual(editableContent(session.project).clips[0].transition_out, link("second"));
  assert.deepEqual(canonicalOverlay(session.project.overlays[0]).animation_out, effect("zoom_out"));
  const changed = signature(session.project);
  session.travel(true); assert.equal(signature(session.project), signature(original));
  session.travel(false); assert.equal(signature(session.project), changed);
});

test("recoverable drafts retain animation types, durations and the exact transition neighbor", () => {
  const original = project(), draft = clone(original);
  draft.clips[0].animation_in = effect("zoom_in", 1);
  draft.clips[0].transition_out = link("second", 1.5, "wipe_left");
  const current = { ...original, updated_at: "r2" };
  const result = resolveDraft(current, { project: draft, savedSignature: signature(original) }, media);
  assert.equal(result.status, "recoverable"); assert.equal(result.project.updated_at, "r2");
  assert.equal(signature(result.project), signature(draft));
});

test("malformed or newly oversized animation edits reject atomically instead of being normalized away", () => {
  const session = new ProjectSession(project()), before = signature(session.project);
  for (const spec of [null, [], { type: "invented", duration: 1 }, { type: "fade", duration: NaN },
    { type: "fade", duration: "1" }, { type: "fade", duration: -1 }, { type: "fade", duration: 6 },
    { type: "fade", duration: 1, arbitrary: true }]) {
    assert.throws(() => session.change(value => { value.clips[0].animation_in = spec; }));
    assert.equal(signature(session.project), before); assert.equal(session.undoStack.length, 0);
  }
  assert.throws(() => session.change(value => { value.clips[0].animation_in = effect("fade", 3); value.clips[0].animation_out = effect("fade", 2); }));
  assert.equal(signature(session.project), before);
});

test("audio tracks reject active visual animations and transitions without losing ordinary fades", () => {
  for (const field of ["animation_in", "animation_out", "transition_out"]) {
    const value = project({ audio_clips: [{ ...clip("music"), start: 0, track: 0, fade_in: 0.5, fade_out: 0.2 }] });
    value.audio_clips[0][field] = field === "transition_out" ? link("first") : effect();
    assert.throws(() => validateProject(value, media), /只適用/);
  }
});

test("transitions require the next bound main clip or adjacent same-track videos", () => {
  for (const mutate of [value => { value.clips[0].transition_out = link("third"); },
    value => { value.clips[2].transition_out = link("first"); },
    value => { value.clips[0].transition_out = link("second", 4.5); },
    value => { value.overlays = [title("first-title", { transition_out: link("next-title"), track_id: "titles" }), title("next-title", { start: 4, end: 8, track_id: "titles" })]; },
    value => { value.overlays = [video("lower", { track_id: "video-track", transition_out: link("upper") }), video("upper", { start: 5, end: 9, track_id: "video-track" })]; }]) {
    const value = project(); mutate(value); assert.throws(() => validateProject(value, media), /轉場/);
  }
  const value = project({ overlays: [video("lower", { track_id: "video-track", transition_out: link("upper") }), video("upper", { start: 4, end: 8, track_id: "video-track" })] });
  value.clips[0].transition_out = link("second"); validateProject(value, media);
  assert.equal(transitionPairs(value).length, 2);
});

test("text animation edits keep raster keys stable so playback does not re-render text", () => {
  const original = title(), changed = { ...original, animation_in: effect("slide_left"), animation_out: effect("fade") };
  assert.equal(overlayRasterKey(original, 1280, 720), overlayRasterKey(changed, 1280, 720));
  assert.notEqual(signature(project({ overlays: [original] })), signature(project({ overlays: [changed] })));
});

test("main clip splits retain only original outer animation edges and move outgoing transition to the remainder", () => {
  const value = project();
  value.clips[0].animation_in = effect("slide_up", 2); value.clips[0].animation_out = effect("fade", 2);
  value.clips[0].transition_out = link("second", 2);
  splitAt(value, 3.5, () => "remainder");
  const [first, second] = value.clips;
  assert.deepEqual(first.animation_in, effect("slide_up", 2)); assert.equal(first.animation_out, undefined); assert.equal(first.transition_out, undefined);
  assert.equal(second.animation_in, undefined); assert.deepEqual(second.animation_out, effect("fade", 0.5));
  assert.deepEqual(second.transition_out, link("second", 0.5)); assert.equal(duration(first) + duration(second), 4);
  validateProject(value, media);
});

test("splitting a transition target retains the inbound link and clamps it to the shorter first half", () => {
  const session = new ProjectSession(project()); session.change(value => { value.clips[0].transition_out = link("second", 2); });
  session.change(value => { splitAt(value, 4.25, () => "remainder"); });
  assert.deepEqual(session.project.clips[0].transition_out, link("second", 0.25));
  validateProject(session.project, media);
});

test("text and image splits avoid an extra internal entrance or exit animation", () => {
  for (const original of [title("text", { animation_in: effect("zoom_in", 2), animation_out: effect("slide_down", 2) }),
    { id: "picture", kind: "image", media_id: "picture", start: 0, end: 4, x: 0.5, y: 0.5, width: 0.5, rotation: 0, opacity: 1, animation_in: effect("fade", 2), animation_out: effect("fade", 2) }]) {
    const value = project({ overlays: [original] }); splitOverlayAt(value, original.id, 0.25, () => "remainder");
    const [first, second] = value.overlays;
    close(first.animation_in.duration, 0.25); assert.equal(first.animation_out, undefined);
    assert.equal(second.animation_in, undefined); close(second.animation_out.duration, 2);
    assert.equal(first.end, second.start);
  }
});

test("video overlay splits keep bound outgoing transitions on the last piece of a shared track", () => {
  const value = project({ overlays: [video("first-layer", { track_id: "shared", animation_in: effect(), animation_out: effect(), transition_out: link("second-layer", 2) }), video("second-layer", { track_id: "shared", start: 4, end: 8 })] });
  splitOverlayAt(value, "first-layer", 3.75, () => "remainder");
  assert.equal(value.overlays[0].transition_out, undefined);
  assert.deepEqual(value.overlays[1].transition_out, link("second-layer", 0.25));
  validateProject(value, media);
});

test("drag trims proportionally shorten existing animation edges and outgoing transition without changing the original", () => {
  const value = project(); value.clips[0].animation_in = effect("fade", 2); value.clips[0].animation_out = effect("fade", 2); value.clips[0].transition_out = link("second", 2);
  const original = clone(value), result = trimTimelineClip(value, { kind: "video", id: "first", edge: "right", delta: -3 }, media);
  assert.deepEqual(value, original); close(result.clip.animation_in.duration, 0.5); close(result.clip.animation_out.duration, 0.5);
  assert.deepEqual(result.clip.transition_out, link("second", 1)); validateProject(result.project, media);
});

test("faster playback clamps edge animation durations and both sides' existing transition limits", () => {
  const value = project(); value.clips[0].transition_out = link("second", 3);
  value.clips[1].animation_in = effect("fade", 2); value.clips[1].animation_out = effect("zoom_out", 2);
  const result = transformClipSpeed(value, "video", "second", { speed: 4 }, media);
  close(result.clip.animation_in.duration, 0.5); close(result.clip.animation_out.duration, 0.5);
  assert.deepEqual(result.project.clips[0].transition_out, link("second", 1)); validateProject(result.project, media);
});

test("an explicit animation edit combined with trimming remains strict rather than silently replacing its duration", () => {
  const value = project({ overlays: [title("title", { animation_in: effect("fade", 2) })] });
  assert.throws(() => transformOverlay(value, "title", { end: 0.5, animation_in: effect("slide_up", 1) }, media), /總長/);
  assert.equal(value.overlays[0].end, 4); assert.deepEqual(value.overlays[0].animation_in, effect("fade", 2));
});

test("structural reorder and deletion clear old links while newly invalid next_id input still rejects", () => {
  const original = project(); original.clips[0].transition_out = link("second");
  const value = clone(original); reorder(value, "third", "first", true);
  assert.equal(value.clips[0].transition_out, undefined); validateProject(value, media);
  const session = new ProjectSession(original); session.change(next => { next.clips.splice(1, 1); });
  assert.equal(session.project.clips[0].transition_out, undefined);
  session.travel(true); assert.deepEqual(session.project.clips[0].transition_out, link("second"));
  assert.throws(() => session.change(next => { next.clips[0].transition_out = link("third"); }), /轉場/);
  assert.deepEqual(session.project.clips[0].transition_out, link("second"));
});

test("main duplication keeps the original's entry and exit and binds the copied outgoing transition to its real next clip", () => {
  const value = project(); value.clips[0].animation_in = effect("slide_up"); value.clips[0].animation_out = effect("zoom_out"); value.clips[0].transition_out = link("second");
  const session = new ProjectSession(value);
  session.change(next => { next.clips.splice(1, 0, { ...next.clips[0], id: "copy" }); });
  assert.equal(session.project.clips[0].transition_out, undefined);
  assert.deepEqual(session.project.clips[1].transition_out, link("second"));
  assert.deepEqual(session.project.clips[0].animation_in, effect("slide_up"));
  assert.deepEqual(session.project.clips[1].animation_out, effect("zoom_out"));
  assert.deepEqual(transitionPairs(session.project).map(pair => [pair.from.id, pair.to.id]), [["copy", "second"]]);
  session.travel(true); assert.equal(signature(session.project), signature(value));
});

test("opening a gap in a layer track removes its bound transition, and undo restores it", () => {
  const original = project({ overlays: [video("a", { track_id: "shared", transition_out: link("b") }), video("b", { track_id: "shared", start: 4, end: 8 })] });
  const result = transformOverlay(original, "b", { start: 5, end: 9 }, media);
  assert.equal(result.project.overlays[0].transition_out, undefined); assert.deepEqual(original.overlays[0].transition_out, link("b"));
  const session = new ProjectSession(original); session.change(value => { value.overlays = result.project.overlays; });
  session.travel(true); assert.deepEqual(session.project.overlays[0].transition_out, link("b"));
});

test("normalization does not silently clean an invalid existing link or malformed explicit setting", () => {
  const before = project(), value = clone(before); value.clips[0].transition_out = link("third");
  normalizeProjectAnimations(value, before); assert.deepEqual(value.clips[0].transition_out, link("third"));
  assert.throws(() => validateProject(value, media), /轉場/);
  const broken = clone(value), next = clone(broken); next.clips.pop(); normalizeProjectAnimations(next, broken);
  assert.deepEqual(next.clips[0].transition_out, link("third")); assert.throws(() => validateProject(next, media), /轉場/);
});

test("moving or copying main video to an isolated layer preserves entry and exit while dropping only ineligible links", () => {
  for (const move of [false, true]) {
    const value = project(); value.clips[0].animation_in = effect("slide_up"); value.clips[0].animation_out = effect("zoom_out"); value.clips[0].transition_out = link("second");
    promoteClip(value, "first", move, () => "promoted"); const promoted = value.overlays[0];
    assert.deepEqual(promoted.animation_in, effect("slide_up")); assert.deepEqual(promoted.animation_out, effect("zoom_out"));
    assert.equal(promoted.transition_out, undefined); assert.equal(promoted.volume, move ? 1 : 0);
    validateProject(value, media);
  }
});

test("track drags retain visual animations but remove links when the connected videos no longer share a track", () => {
  const value = project(); value.clips[0].animation_in = effect("slide_up"); value.clips[0].animation_out = effect("fade"); value.clips[0].transition_out = link("second");
  const raised = transformTrackDrag(value, { kind: "video", id: "first" }, { kind: "overlay", index: 0 }, media);
  assert.deepEqual(raised.project.overlays[0].animation_in, effect("slide_up")); assert.deepEqual(raised.project.overlays[0].animation_out, effect("fade"));
  assert.equal(raised.project.overlays[0].transition_out, undefined);
  const demoted = transformTrackDrag(raised.project, { kind: "overlay", id: "first" }, { kind: "video", index: 0 }, media);
  assert.deepEqual(demoted.project.clips[0].animation_in, effect("slide_up")); assert.deepEqual(demoted.project.clips[0].animation_out, effect("fade"));
});

test("detaching original audio leaves visual animations on video and does not copy them into audio", () => {
  const value = project(); value.clips[0].animation_in = effect("fade"); value.clips[0].transition_out = link("second");
  detachAudio(value, "first", () => "detached");
  assert.deepEqual(value.clips[0].animation_in, effect()); assert.deepEqual(value.clips[0].transition_out, link("second"));
  for (const field of ["animation_in", "animation_out", "transition_out"]) assert.equal(value.audio_clips[0][field], undefined);
  validateProject(value, media);
});

test("saving detects older backends that silently discard main or text animations or transition binding", async () => {
  for (const discard of [saved => { delete saved.clips[0].animation_in; }, saved => { delete saved.clips[0].transition_out; }, saved => { delete saved.overlays[0].animation_out; }]) {
    const session = new ProjectSession(project({ overlays: [title()] }));
    session.change(value => { value.clips[0].animation_in = effect(); value.clips[0].transition_out = link("second"); value.overlays[0].animation_out = effect("zoom_out"); });
    const before = signature(session.project);
    await assert.rejects(session.save(async snapshot => { const saved = clone(snapshot); discard(saved); saved.updated_at = "r2"; return saved; }), /動畫／轉場/);
    assert.equal(signature(session.project), before); assert.equal(session.project.updated_at, "r1"); assert.equal(session.dirty, true);
  }
});

test("animation support guards allow legacy clips and keep configured drafts out of an older backend", () => {
  const value = project(); requireAnimationSupport(value, false);
  value.clips[0].animation_in = effect(); assert.throws(() => requireAnimationSupport(value, false), /草稿仍保留/);
  requireAnimationSupport(value, true);
});

test("animation support accepts empty create requests before the server adds clip collections", () => {
  for (const value of [{}, { name: "未命名專案" }, { name: "未命名專案", clips: [] }]) {
    const before = clone(value);
    assert.doesNotThrow(() => requireAnimationSupport(value, false));
    assert.deepEqual(value, before);
  }
});

test("a create request without main clips still protects animated overlay content", () => {
  const value = { name: "疊層副本", overlays: [title("title", { animation_in: effect() })] };
  const before = clone(value);
  assert.throws(() => requireAnimationSupport(value, false), /草稿仍保留/);
  assert.deepEqual(value, before);
  requireAnimationSupport(value, true);
});
