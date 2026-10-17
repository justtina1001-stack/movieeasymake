"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const batch = require("../static/editor_batch_effects.js"), animations = require("../static/editor_animations.js"), speed = require("../static/editor_speed.js");
const video = (id, length = 4, extras = {}) => ({ id, media_id: `media_${id}`, in: 0, out: length, speed: 1, volume: 0.8, ...extras });
const layer = (id, kind, start, end, extras = {}) => ({ id, kind, start, end, track_id: "layer1", ...extras });
const choose = (project, kind, itemId, tab) => batch.applyTrackEffect(project, { kind, itemId, tab });

test("main-track transitions bind each new next id and clamp to both timeline lengths without changing timing or audio", () => {
  const project = { name: "剪輯", fps: 24, clips: [video("a", 4, { transition_out: { type: "radial_zoom", duration: 2, next_id: "b" } }), video("b"), video("c", 0.75), video("d", 3)], overlays: [layer("other", "video", 0, 4, { transition_out: { type: "wipe_left", duration: 1, next_id: "unrelated" } })], audio_clips: [{ id: "audio", volume: 1 }] };
  const before = structuredClone(project), result = choose(project, "video", "a", "transition");
  assert.deepEqual(result.project.clips.map(item => item.transition_out), [{ type: "radial_zoom", duration: 2, next_id: "b" }, { type: "radial_zoom", duration: 0.75, next_id: "c" }, { type: "radial_zoom", duration: 0.75, next_id: "d" }, undefined]);
  assert.deepEqual({ count: result.count, clamped: result.clamped, skipped: result.skipped }, { count: 2, clamped: 2, skipped: 0 });
  assert.deepEqual(project, before); assert.deepEqual(result.project.overlays, before.overlays); assert.deepEqual(result.project.audio_clips, before.audio_clips);
  for (let i = 0; i < project.clips.length; i++) { const { transition_out, ...rest } = result.project.clips[i], { transition_out: original, ...beforeRest } = before.clips[i]; assert.deepEqual(rest, beforeRest); }
  animations.validateTransitions({ clips: result.project.clips });
});

test("duration calculations use the shared speed curve rather than source length", () => {
  const project = { clips: [video("a", 3, { transition_out: { type: "crossfade", duration: 1, next_id: "b" } }), video("b", 3), video("c", 2, { speed_curve: [{ time: 0, speed: 2 }, { time: 2, speed: 4 }] })] };
  const result = choose(project, "video", "a", "transition");
  assert.equal(result.project.clips[1].transition_out.duration, speed.clipDuration(project.clips[2]));
  assert.equal(result.clamped, 1); animations.validateTransitions(result.project);
});

test("overlay transitions sort the selected track while skipping gaps and nonvideo neighbours", () => {
  const project = { overlays: [layer("d", "video", 6, 8), layer("other", "video", 0, 4, { track_id: "other" }), layer("a", "video", 0, 2, { transition_out: { type: "crossfade", duration: 1, next_id: "b" } }), layer("b", "video", 2, 4), layer("label", "text", 4, 5), layer("e", "video", 8, 8.3)] };
  const before = structuredClone(project), result = choose(project, "overlay", "a", "transition");
  assert.equal(result.project.overlays.find(item => item.id === "d").transition_out.duration, 8.3 - 8);
  assert.equal(result.project.overlays.find(item => item.id === "d").transition_out.next_id, "e");
  assert.equal(result.project.overlays.find(item => item.id === "b").transition_out, undefined);
  assert.equal(result.project.overlays.find(item => item.id === "label").transition_out, undefined);
  assert.equal(result.count, 1); assert.equal(result.clamped, 1); assert.equal(result.skipped, 2);
  assert.deepEqual(result.project.overlays.map(item => item.id), before.overlays.map(item => item.id)); assert.deepEqual(project, before);
});

test("a none source clears all transitions on its track including stale final or gap-bound values", () => {
  const project = { overlays: [layer("a", "video", 0, 2), layer("b", "video", 4, 6, { transition_out: { type: "crossfade", duration: 1, next_id: "missing" } }), layer("last", "text", 7, 8, { transition_out: { type: "wipe_left", duration: 1, next_id: "old" } }), layer("other", "video", 0, 1, { track_id: "other", transition_out: { type: "wipe_right", duration: 1, next_id: "old" } })] };
  const result = choose(project, "overlay", "a", "transition");
  assert.equal(result.count, 2); assert.equal(result.skipped, 0);
  for (const item of result.project.overlays.filter(item => item.track_id === "layer1")) assert.equal(Object.hasOwn(item, "transition_out"), false);
  assert.deepEqual(result.project.overlays[3], project.overlays[3]);
});

test("selecting a last main clip with no effect can remove every earlier transition", () => {
  const project = { clips: [video("a", 2, { transition_out: { type: "crossfade", duration: 1, next_id: "b" } }), video("b", 2, { transition_out: { type: "none", duration: 0 } })] };
  const result = choose(project, "video", "b", "transition"); assert.equal(result.count, 2);
  assert.ok(result.project.clips.every(item => !Object.hasOwn(item, "transition_out")));
});

test("legacy overlays without track ids remain isolated rather than sharing an anonymous track", () => {
  const project = { overlays: [layer("a", "text", 0, 2, { track_id: undefined, animation_in: { type: "fade", duration: 1 } }), layer("b", "text", 2, 4, { track_id: undefined })] };
  const result = choose(project, "overlay", "a", "in"); assert.equal(result.count, 0); assert.equal(result.project.overlays[1].animation_in, undefined);
});

test("entrance copies only to matching kinds and reserves each opposite edge without touching another track", () => {
  const project = { overlays: [layer("a", "text", 0, 4, { animation_in: { type: "slide_up", duration: 1.5 }, animation_idle: { preset: "pulse" } }), layer("b", "text", 3, 4, { animation_out: { type: "fade", duration: 0.75 }, position_keyframes: [{ time: 0, x: 0.4, y: 0.5 }] }), layer("image", "image", 0, 1), layer("other", "text", 0, 1, { track_id: "other" })] };
  const result = choose(project, "overlay", "a", "in");
  assert.deepEqual(result.project.overlays[1].animation_in, { type: "slide_up", duration: 0.25 });
  assert.deepEqual(result.project.overlays[1].animation_out, project.overlays[1].animation_out); assert.deepEqual(result.project.overlays[1].position_keyframes, project.overlays[1].position_keyframes);
  assert.deepEqual(result.project.overlays[0].animation_idle, project.overlays[0].animation_idle);
  assert.equal(result.project.overlays[2].animation_in, undefined); assert.equal(result.project.overlays[3].animation_in, undefined);
  assert.equal(result.count, 1); assert.equal(result.clamped, 1); assert.equal(result.skipped, 1);
});

test("a fully reserved edge removes only the copied animation and reports the shortening", () => {
  const project = { clips: [video("a", 4, { animation_out: { type: "fade", duration: 1 } }), video("b", 1, { animation_in: { type: "fade", duration: 1 }, animation_out: { type: "none", duration: 0 } })] };
  const result = choose(project, "video", "a", "out");
  assert.equal(result.project.clips[1].animation_out, undefined); assert.deepEqual(result.project.clips[1].animation_in, project.clips[1].animation_in);
  assert.equal(result.count, 1); assert.equal(result.clamped, 1);
});

test("none entrance source removes only entrances on matching track and kind", () => {
  const project = { overlays: [layer("a", "text", 0, 2), layer("b", "text", 0, 2, { animation_in: { type: "fade", duration: 1 }, animation_out: { type: "fade", duration: 1 } }), layer("image", "image", 0, 2, { animation_in: { type: "fade", duration: 1 } })] };
  const result = choose(project, "overlay", "a", "in");
  assert.equal(result.count, 1); assert.equal(result.project.overlays[1].animation_in, undefined); assert.deepEqual(result.project.overlays[1].animation_out, project.overlays[1].animation_out); assert.deepEqual(result.project.overlays[2], project.overlays[2]);
});

test("text idle effects retain speed and amount without copying unrelated edge settings", () => {
  const project = { overlays: [layer("a", "text", 0, 4, { animation_idle: { preset: "float", speed: 1.75, amount: 0.4 }, animation_in: { type: "fade", duration: 1 } }), layer("b", "text", 4, 6), layer("image", "image", 0, 4)] };
  const before = structuredClone(project), result = choose(project, "overlay", "a", "idle");
  assert.deepEqual(result.project.overlays[1].animation_idle, { preset: "float", speed: 1.75, amount: 0.4 }); assert.equal(result.project.overlays[1].animation_in, undefined);
  assert.equal(result.count, 1); assert.equal(result.skipped, 1); assert.deepEqual(project, before);
  result.project.overlays[1].animation_idle.amount = 1.8; assert.equal(project.overlays[0].animation_idle.amount, 0.4); assert.equal(result.project.overlays[0].animation_idle.amount, 0.4);
});

test("none and zero-strength idle sources clear same-track text idle fields", () => {
  for (const setting of [undefined, { preset: "none" }, { preset: "pulse", speed: 2, amount: 0 }]) {
    const project = { overlays: [layer("a", "text", 0, 1, { animation_idle: setting }), layer("b", "text", 1, 2, { animation_idle: { preset: "float", speed: 2 } }), layer("other", "text", 0, 1, { track_id: "other", animation_idle: { preset: "shake" } })] };
    const result = choose(project, "overlay", "a", "idle"); assert.equal(result.project.overlays[0].animation_idle, undefined); assert.equal(result.project.overlays[1].animation_idle, undefined); assert.deepEqual(result.project.overlays[2], project.overlays[2]);
  }
});

test("invalid source effects throw instead of turning into bulk removal or being normalized by cloning", () => {
  for (const setting of [null, { type: "unknown", duration: 1 }, { type: "fade", duration: NaN }, { type: "fade", duration: Infinity }, { type: "fade", duration: 6 }, { type: "fade", duration: -1 }, { type: "fade", duration: 1, unexpected: true }]) {
    const project = { clips: [video("a", 4, { animation_in: setting }), video("b", 4, { animation_in: { type: "slide_up", duration: 1 } })] }, before = structuredClone(project);
    assert.throws(() => choose(project, "video", "a", "in")); assert.deepEqual(project, before);
  }
  for (const setting of [null, { preset: "pulse", speed: 0 }, { preset: "bad" }, { preset: "pulse", amount: NaN }]) assert.throws(() => choose({ overlays: [layer("a", "text", 0, 2, { animation_idle: setting })] }, "overlay", "a", "idle"));
});

test("active transition sources must have a valid adjacent binding and valid duration", () => {
  for (const setting of [{ type: "crossfade", duration: 1, next_id: "wrong" }, { type: "crossfade", duration: 3, next_id: "b" }, { type: "crossfade", duration: 1 }]) assert.throws(() => choose({ clips: [video("a", 2, { transition_out: setting }), video("b", 2)] }, "video", "a", "transition"));
  assert.throws(() => choose({ overlays: [layer("a", "video", 0, 2, { transition_out: { type: "crossfade", duration: 1, next_id: "b" } }), layer("b", "video", 3, 5)] }, "overlay", "a", "transition"), /同軌相鄰/);
});

test("source edge durations must already fit before applying to other clips", () => {
  assert.throws(() => choose({ clips: [video("a", 1, { animation_in: { type: "fade", duration: 1 }, animation_out: { type: "fade", duration: 1 } }), video("b", 4)] }, "video", "a", "in"), /總長/);
});

test("invalid selections track structures and unsupported kinds fail without editing input", () => {
  const project = { clips: [video("a")], overlays: [layer("text", "text", 0, 2), layer("image", "image", 0, 2)] };
  for (const options of [null, { kind: "audio", itemId: "a", tab: "in" }, { kind: "video", itemId: "a", tab: "wrong" }, { kind: "video", itemId: "missing", tab: "in" }, { kind: "overlay", itemId: "text", tab: "transition" }, { kind: "overlay", itemId: "image", tab: "idle" }]) assert.throws(() => batch.applyTrackEffect(project, options));
  assert.throws(() => choose({ clips: {} }, "video", "a", "in")); assert.throws(() => choose({ clips: [video("a"), video("a")] }, "video", "a", "in"));
});

test("invalid target ranges speeds and opposite animations cannot be hidden by bulk clamping", () => {
  for (const bad of [video("b", 0), video("b", 2, { speed: 0 }), video("b", 2, { animation_out: { type: "fade", duration: 3 } }), video("b", 2, { speed_curve: [{ time: 1, speed: 2 }] })]) {
    const project = { clips: [video("a", 4, { animation_in: { type: "fade", duration: 1 } }), bad] }, before = structuredClone(project);
    assert.throws(() => choose(project, "video", "a", "in")); assert.deepEqual(project, before);
  }
});

test("no-op applications still return an independent clone and accurate zero count", () => {
  const project = { clips: [video("a", 4, { animation_in: { type: "fade", duration: 1 } }), video("b", 4, { animation_in: { type: "fade", duration: 1 } })], metadata: { notes: ["保留"] } };
  const result = choose(project, "video", "a", "in"); assert.equal(result.count, 0); assert.equal(result.clamped, 0); assert.deepEqual(result.project, project);
  assert.notEqual(result.project, project); assert.notEqual(result.project.metadata.notes, project.metadata.notes);
  assert.equal(batch.targetLabel("video", "transition"), "主軌全部交界"); assert.equal(batch.targetLabel("overlay", "in"), "同軌同類圖層");
});
