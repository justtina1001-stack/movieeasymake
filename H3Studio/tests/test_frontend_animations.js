"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const math = require("../static/editor_animations.js");
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const spec = (type, duration = 1) => ({ type, duration });
const clip = (id, length = 4, extra = {}) => ({ id, media_id: id, in: 0, out: length, speed: 1, volume: 1, ...extra });
const project = (clips = [clip("a"), clip("b")], overlays = []) => ({ clips, overlays, fps: 24 });
const layer = (id, start, end, extra = {}) => ({ ...clip(id, end - start), kind: "video", track_id: "upper", start, end, ...extra });
test("absent, none and zero-duration animation specs retain the legacy canonical signature", () => {
  assert.deepEqual(math.canonicalAnimations({}), {});
  assert.deepEqual(math.canonicalAnimations({ animation_in: spec("none"), animation_out: spec("zoom_in", 0), transition_out: spec("none") }), {});
  assert.deepEqual(math.canonicalAnimations({ animation_in: spec("fade", 0.5), transition_out: { ...spec("crossfade"), next_id: "b" } }), { animation_in: spec("fade", 0.5), transition_out: { ...spec("crossfade"), next_id: "b" } });
});
test("animation validation rejects malformed specs, nonfinite/coerced durations, unsupported effects and overflowing ranges", () => {
  for (const invalid of [null, [], true, {}, spec("magic"), spec("fade", NaN), spec("fade", Infinity), spec("fade", true), spec("fade", "1"), spec("fade", -1), spec("fade", 6), { ...spec("fade"), easing: "linear" }]) assert.throws(() => math.validateAnimations({ animation_in: invalid }, 10));
  assert.throws(() => math.validateAnimations({ animation_in: spec("fade", 2), animation_out: spec("fade", 2) }, 3));
  assert.doesNotThrow(() => math.validateAnimations({ animation_in: spec("fade", 1), animation_out: spec("fade", 2) }, 3));
  assert.doesNotThrow(() => math.validateAnimations({ animation_in: spec("none", 5), animation_out: spec("none", 5) }, 1));
});
test("duration shortening rescales entry and exit proportionally without creating default fields or mutating nested specs", () => {
  const entry = spec("slide_left", 2), item = { animation_in: entry, animation_out: spec("zoom_in", 1) };
  assert.equal(math.clampAnimations(item, 1.5), item); close(item.animation_in.duration, 1); close(item.animation_out.duration, 0.5); close(entry.duration, 2);
  assert.deepEqual(math.clampAnimations({}, 1), {}); assert.throws(() => math.clampAnimations(item, 0));
});
test("splits preserve only original outer edges, move outgoing transition to the second half and clamp its shortened remainder", () => {
  const item = { animation_in: spec("fade", 2), animation_out: spec("slide_down", 2), transition_out: { ...spec("wipe_right", 2), next_id: "next" } };
  const halves = math.splitAnimations(item, 1, 0.5);
  assert.deepEqual(halves.first, { animation_in: spec("fade", 1) });
  assert.deepEqual(halves.second, { animation_out: spec("slide_down", 0.5), transition_out: { ...spec("wipe_right", 0.5), next_id: "next" } });
  assert.equal(item.transition_out.duration, 2); assert.notEqual(halves.second.transition_out, item.transition_out);
});
test("fade animation uses the same smoothstep ratio as exported transforms and composes entrance/exit", () => {
  const item = { animation_in: spec("fade", 1), animation_out: spec("fade", 1) };
  for (const [time, expected] of [[0, 0], [0.25, 0.15625], [0.5, 0.5], [1, 1], [2, 1], [3.5, 0.5], [3.75, 0.15625]]) close(math.animationTransformAt(item, time, 4).opacity, expected);
  assert.equal(math.animationTransformAt(item, -0.1, 4).opacity, 0); assert.equal(math.animationTransformAt(item, 4, 4).opacity, 0);
});
for (const [type, axis, sign] of [["slide_left", "x", 1], ["slide_right", "x", -1], ["slide_up", "y", 1], ["slide_down", "y", -1]]) test(`${type} enters from its opposite canvas edge and exits in its named direction`, () => {
  const item = { animation_in: spec(type), animation_out: spec(type) };
  close(math.animationTransformAt(item, 0, 4)[axis], sign);
  close(math.animationTransformAt(item, 0.25, 4)[axis], sign * 0.84375);
  close(math.animationTransformAt(item, 2, 4)[axis], 0);
  close(math.animationTransformAt(item, 3.5, 4)[axis], -sign * 0.5);
  close(math.animationTransformAt(item, 3.99999, 4)[axis], -sign);
});
for (const [type, initial] of [["zoom_in", 0.15], ["zoom_out", 1.8]]) test(`${type} scales continuously around the clip's placement, preserving opacity`, () => {
  const item = { animation_in: spec(type), animation_out: spec(type) };
  close(math.animationTransformAt(item, 0, 4).scale, initial); close(math.animationTransformAt(item, 0.5, 4).scale, (1 + initial) / 2); close(math.animationTransformAt(item, 2, 4).scale, 1); close(math.animationTransformAt(item, 3.5, 4).scale, (1 + initial) / 2);
  close(math.animationTransformAt(item, 0, 4).opacity, 1);
});
test("adjacent main transitions remain centered at the unchanged edit boundary with held source edge frames", () => {
  const value = project([clip("a", 4, { transition_out: { ...spec("crossfade", 2), next_id: "b" } }), clip("b", 3)]), original = JSON.stringify(value);
  math.validateTransitions(value);
  const [pair] = math.transitionPairs(value); assert.equal(pair.start, 3); assert.equal(pair.end, 5); assert.equal(pair.cut, 4);
  assert.equal(math.transitionAt(value, 2.99).length, 0); assert.equal(math.transitionAt(value, 5).length, 0);
  const [before] = math.transitionAt(value, 3.5); close(before.progress, 0.25); close(before.fromLocal, 3.5); close(before.toLocal, 0); assert.equal(before.toHeld, true);
  const [after] = math.transitionAt(value, 4.5); close(after.progress, 0.75); close(after.fromLocal, 4 - 1 / 24); close(after.toLocal, 0.5); assert.equal(after.fromHeld, true);
  assert.equal(JSON.stringify(value), original); assert.equal(value.clips.reduce((sum, item) => sum + item.out - item.in, 0), 7);
});
test("strict transitions reject dangling/reordered ids, overly long durations and last-clip transitions", () => {
  const a = clip("a", 4, { transition_out: { ...spec("crossfade", 1), next_id: "b" } });
  for (const value of [project([a]), project([a, clip("c")]), project([a, clip("b", 0.5)]), project([clip("a", 4, { transition_out: { ...spec("crossfade"), next_id: false } }), clip("b")])]) assert.throws(() => math.validateTransitions(value));
  assert.equal(math.transitionPairs(project([a, clip("c")])).length, 0);
});
test("same-track video overlays transition but gaps, different tracks or intervening text do not", () => {
  const a = layer("a", 2, 4, { transition_out: { ...spec("wipe_left", 1), next_id: "b" } }), b = layer("b", 4, 6);
  const value = project([], [b, a]); math.validateTransitions(value); assert.equal(math.transitionAt(value, 4).length, 1);
  for (const other of [{ ...b, start: 4.01 }, { ...b, track_id: "other" }, { ...b, kind: "text" }]) { assert.throws(() => math.validateTransitions(project([], [a, other]))); assert.equal(math.transitionPairs(project([], [a, other])).length, 0); }
});
test("crossfade weight sums to one and wipe masks partition the canvas without transparent overlap", () => {
  for (const progress of [0, 0.25, 0.5, 0.75, 1]) {
    const fade = math.transitionVisualAt("crossfade", progress); close(fade.from.opacity + fade.to.opacity, 1);
    const left = math.transitionVisualAt("wipe_left", progress); close(left.from.right, left.to.left); close(left.to.right - left.to.left, progress);
    const right = math.transitionVisualAt("wipe_right", progress); close(right.to.right, right.from.left); close(right.to.right - right.to.left, progress);
  }
});
test("transition durations follow actual speed-curve timeline duration and may be simultaneous on independent tracks", () => {
  const a = clip("a", 4, { speed: 2, transition_out: { ...spec("crossfade", 1), next_id: "b" } }), b = clip("b", 4);
  const value = project([a, b], [layer("c", 0, 2, { transition_out: { ...spec("wipe_right", 1), next_id: "d" } }), layer("d", 2, 4)]);
  assert.equal(math.transitionAt(value, 2).length, 2); assert.equal(math.transitionPairs(value)[0].cut, 2);
});
