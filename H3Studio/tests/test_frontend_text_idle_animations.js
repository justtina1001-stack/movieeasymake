"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const math = require("../static/editor_animations.js");
const identity = { opacity: 1, x: 0, y: 0, scale: 1 };
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);
const title = (preset = "pulse", extra = {}) => ({ kind: "text", animation_idle: { preset, speed: 1, amount: 1 }, ...extra });

test("idle settings retain legacy signatures when absent, disabled or zero and supply explicit canonical defaults", () => {
  for (const animation_idle of [undefined, {}, { preset: "none" }, { preset: "none", speed: 4, amount: 2 }, { preset: "pulse", amount: 0 }]) {
    const item = { kind: "text", animation_idle };
    math.validateAnimations(item, 1);
    assert.deepEqual(math.canonicalAnimations(item), {});
  }
  assert.deepEqual(math.canonicalAnimations(title("float", { animation_idle: { preset: "float" } })),
    { animation_idle: { preset: "float", speed: 1, amount: 1 } });
});

test("idle rejects unknown fields and presets, nulls, coercion, nonfinite ranges and nontext items", () => {
  for (const animation_idle of [null, [], "pulse", true, { preset: "spin" }, { preset: null },
    { preset: "pulse", speed: true }, { preset: "pulse", speed: "1" }, { preset: "pulse", speed: null },
    { preset: "pulse", speed: NaN }, { preset: "pulse", speed: Infinity }, { preset: "pulse", speed: 0.249 },
    { preset: "pulse", speed: 4.001 }, { preset: "pulse", amount: true }, { preset: "pulse", amount: "1" },
    { preset: "pulse", amount: null }, { preset: "pulse", amount: NaN }, { preset: "pulse", amount: -0.001 },
    { preset: "pulse", amount: 2.001 }, { preset: "pulse", url: "font.ttf" }]) assert.throws(() => math.validateAnimations({ kind: "text", animation_idle }, 1));
  for (const kind of ["image", "video", "audio", undefined]) for (const preset of ["none", "pulse"]) assert.throws(() => math.validateAnimations({ kind, animation_idle: { preset } }, 1), /文字圖層/);
});

test("idle only animates the gap between entry and exit and returns smoothly to original placement", () => {
  for (const preset of math.IDLE_TYPES.filter(value => value !== "none")) {
    const item = title(preset, { animation_in: { type: "fade", duration: 0.5 }, animation_out: { type: "slide_left", duration: 0.5 } });
    const plain = { ...item }; delete plain.animation_idle;
    for (const seconds of [0, 0.25, 0.5, 1.5, 1.75, 1.999999]) assert.deepEqual(math.animationTransformAt(item, seconds, 2), math.animationTransformAt(plain, seconds, 2));
    assert.notDeepEqual(math.idleTransformAt(item, 0.8, 2), identity);
    for (const seconds of [0.5 + 1e-8, 1.5 - 1e-8]) for (const key of Object.keys(identity)) close(math.idleTransformAt(item, seconds, 2)[key], identity[key]);
    for (const length of [1, 1.0000005]) assert.deepEqual(math.idleTransformAt(item, 0.5, length), identity);
  }
});

test("pulse, float, shake and blink use export-matched normalized canvas transforms", () => {
  close(math.idleTransformAt(title("pulse"), 0.25, 2).scale, 1.04);
  close(math.idleTransformAt(title("float"), 0.25, 2).y, -0.025);
  close(math.idleTransformAt(title("shake"), 0.3125, 2).x, 0.0125);
  close(math.idleTransformAt(title("blink"), 0.25, 2).opacity, 0.6);
  for (const preset of math.IDLE_TYPES.filter(value => value !== "none")) assert.deepEqual(math.idleTransformAt(title(preset, { animation_idle: { preset, speed: 4, amount: 0 } }), 0.3, 2), identity);
});

test("trim retains speed and strength and split copies idle to both halves without sharing mutable objects", () => {
  const original = title("pulse", { animation_in: { type: "fade", duration: 2 }, animation_out: { type: "zoom_out", duration: 1 } });
  const idle = original.animation_idle;
  math.clampAnimations(original, 1.5);
  assert.equal(original.animation_in.duration, 1); assert.equal(original.animation_out.duration, 0.5);
  assert.equal(original.animation_idle, idle);
  const halves = math.splitAnimations(original, 0.75, 0.75);
  assert.deepEqual(halves.first.animation_idle, idle); assert.deepEqual(halves.second.animation_idle, idle);
  assert.notEqual(halves.first.animation_idle, idle); assert.notEqual(halves.second.animation_idle, idle);
  assert.notEqual(halves.first.animation_idle, halves.second.animation_idle);
  halves.first.animation_idle.amount = 2; assert.equal(halves.second.animation_idle.amount, 1); assert.equal(idle.amount, 1);
  assert.equal(halves.first.animation_out, undefined); assert.equal(halves.second.animation_in, undefined);
});
