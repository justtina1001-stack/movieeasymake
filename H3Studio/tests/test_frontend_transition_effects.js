"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const effects = require("../static/editor_transition_effects.js");
const animations = require("../static/editor_animations.js");
const pixel = (pixels, width, x, y) => [...pixels.slice((y * width + x) * 4, (y * width + x + 1) * 4)];
function raster(width, height, paint) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) pixels.set(paint(x, y), (y * width + x) * 4);
  return pixels;
}
test("the six pixel transitions and two reference names match the validation core and are immutable", () => {
  assert.deepEqual(effects.EFFECT_TYPES, animations.TRANSITION_EFFECT_TYPES);
  assert.deepEqual(effects.REFERENCE_TRANSITION_TYPES, animations.REFERENCE_TRANSITION_TYPES);
  assert.ok(Object.isFrozen(effects.EFFECT_TYPES)); assert.ok(Object.isFrozen(animations.TRANSITION_EFFECT_TYPES));
  assert.ok(Object.isFrozen(effects.REFERENCE_TRANSITION_TYPES)); assert.ok(Object.isFrozen(animations.REFERENCE_TRANSITION_TYPES));
  for (const type of effects.EFFECT_TYPES) assert.equal(effects.isEffect(type), true);
  for (const type of ["none", "crossfade", "wipe_left", "wipe_right", "invented", null]) assert.equal(effects.isEffect(type), false);
});
for (const type of effects.EFFECT_TYPES) test(`${type} preserves exact endpoints, inputs and independently owned results`, () => {
  const width = 128, height = 96;
  const from = raster(width, height, (x, y) => [x * 2, y * 2, (x + y) % 256, x % 3 ? 128 : 0]);
  const to = raster(width, height, (x, y) => [255 - x * 2, 255 - y * 2, x % 4 ? 220 : 10, y % 5 ? 255 : 0]);
  const beforeFrom = from.slice(), beforeTo = to.slice();
  for (const progress of [-0.1, 0]) { const result = effects.applyPixels(from, to, width, height, type, progress); assert.deepEqual(result, from); assert.notEqual(result, from); }
  for (const progress of [1, 1.1]) { const result = effects.applyPixels(from, to, width, height, type, progress); assert.deepEqual(result, to); assert.notEqual(result, to); }
  const halfway = effects.applyPixels(from, to, width, height, type, 0.5), copy = halfway.slice();
  assert.deepEqual(effects.applyPixels(from, to, width, height, type, 0.5), copy);
  effects.applyPixels(from, to, width, height, type, 0.75); assert.deepEqual(halfway, copy);
  assert.deepEqual(from, beforeFrom); assert.deepEqual(to, beforeTo);
  assert.ok(halfway instanceof Uint8ClampedArray);
});
test("flash reaches white at the midpoint while keeping the blended transparency", () => {
  const from = new Uint8ClampedArray([255, 0, 0, 0, 20, 40, 60, 128]), to = new Uint8ClampedArray([0, 0, 255, 128, 80, 100, 120, 128]);
  assert.deepEqual([...effects.applyPixels(from, to, 2, 1, "white_flash", 0.5)], [255, 255, 255, 64, 255, 255, 255, 128]);
  const invisible = new Uint8ClampedArray([255, 0, 0, 0]);
  assert.deepEqual([...effects.applyPixels(invisible, invisible, 1, 1, "white_flash", 0.5)], [0, 0, 0, 0]);
});
test("premultiplied mixing ignores hidden color and does not create opaque backdrops", () => {
  const from = new Uint8ClampedArray([255, 0, 0, 0]), to = new Uint8ClampedArray([0, 0, 255, 128]);
  for (const type of ["blur_dissolve", "wave_distort", "rgb_glitch"]) assert.deepEqual([...effects.applyPixels(from, to, 1, 1, type, 0.5)], [0, 0, 255, 64]);
});
test("blur spreads a blue opaque point into transparent pixels without a red halo", () => {
  const width = 64, height = 64, image = raster(width, height, (x, y) => x === 32 && y === 32 ? [0, 0, 255, 255] : [255, 0, 0, 0]);
  const blurred = effects.applyPixels(image, image, width, height, "blur_dissolve", 0.5);
  assert.deepEqual(pixel(blurred, width, 32, 32), [0, 0, 255, 28]);
  assert.deepEqual(pixel(blurred, width, 31, 31), [0, 0, 255, 28]);
  assert.deepEqual(pixel(blurred, width, 30, 30), [0, 0, 0, 0]);
  const flat = raster(width, height, () => [20, 40, 80, 128]);
  assert.deepEqual(effects.applyPixels(flat, flat, width, height, "blur_dissolve", 0.5), flat);
});
test("wave moves complete RGBA samples in opposite row directions and clamps edges", () => {
  const width = 128, height = 96, image = raster(width, height, (x, y) => [x, y, x * 2, 64 + x]);
  const waved = effects.applyPixels(image, image, width, height, "wave_distort", 0.5);
  assert.deepEqual(pixel(waved, width, 20, 8), pixel(image, width, 24, 8));
  assert.deepEqual(pixel(waved, width, 20, 24), pixel(image, width, 16, 24));
  assert.deepEqual(pixel(waved, width, 127, 8), pixel(image, width, 127, 8));
  assert.deepEqual(pixel(waved, width, 0, 24), pixel(image, width, 0, 24));
});
test("glitch creates banded offsets with distinct red and blue channel samples", () => {
  const width = 128, height = 96, image = raster(width, height, x => [x, 100, x * 2, 255]);
  const glitched = effects.applyPixels(image, image, width, height, "rgb_glitch", 0.5);
  assert.deepEqual(pixel(glitched, width, 32, 0), [30, 100, 52, 255]);
  assert.deepEqual(pixel(glitched, width, 32, 7), pixel(glitched, width, 32, 0));
  assert.notDeepEqual(pixel(glitched, width, 32, 24), pixel(glitched, width, 32, 0));
  const transparent = raster(width, height, () => [255, 0, 255, 0]);
  assert.ok(effects.applyPixels(transparent, transparent, width, height, "rgb_glitch", 0.5).every(value => value === 0));
});
test("invalid RGBA shapes, unknown types and nonfinite progress fail before rendering", () => {
  const pixels = new Uint8ClampedArray(4);
  for (const [from, to, width, height, type, progress] of [[pixels, pixels, 0, 1, "white_flash", 0.5], [pixels, pixels, 1.5, 1, "white_flash", 0.5],
    [pixels, pixels, 2, 1, "white_flash", 0.5], [[0, 0, 0, 0], pixels, 1, 1, "white_flash", 0.5], [pixels, pixels, 1, 1, "unknown", 0.5],
    [pixels, pixels, 1, 1, "white_flash", NaN], [pixels, pixels, 1, 1, "white_flash", Infinity]]) assert.throws(() => effects.applyPixels(from, to, width, height, type, progress));
});
function canvas(width, height, data) {
  const calls = [], context = { getImageData(...args) { calls.push(["read", ...args]); return { data }; },
    createImageData(w, h) { calls.push(["create", w, h]); return { data: new Uint8ClampedArray(w * h * 4) }; },
    putImageData(value, x, y) { calls.push(["write", x, y]); this.output = value.data; } };
  return { width, height, calls, context, getContext() { return context; } };
}
test("Canvas render reads both frames and writes the same pure pixel result", () => {
  const from = canvas(1, 1, new Uint8ClampedArray([0, 0, 255, 128])), to = canvas(1, 1, new Uint8ClampedArray([255, 0, 0, 0])), target = canvas(1, 1);
  assert.equal(effects.render(target, from, to, "white_flash", 0.5), true);
  assert.deepEqual([...target.context.output], [255, 255, 255, 64]);
  assert.deepEqual(from.calls, [["read", 0, 0, 1, 1]]); assert.deepEqual(to.calls, [["read", 0, 0, 1, 1]]);
  assert.deepEqual(target.calls, [["create", 1, 1], ["write", 0, 0]]);
  assert.equal(effects.render(target, from, to, "crossfade", 0.5), false);
  assert.throws(() => effects.render(canvas(2, 1), from, to, "white_flash", 0.5), /尺寸/);
});
test("unreadable Canvas errors propagate without replacing the preview frame", () => {
  const from = canvas(1, 1), to = canvas(1, 1), target = canvas(1, 1);
  from.context.getImageData = () => { throw new Error("tainted Canvas"); };
  assert.throws(() => effects.render(target, from, to, "white_flash", 0.5), /tainted/); assert.deepEqual(target.calls, []);
});

test("radial dissolve follows the exponential source curve and creates spatial ray trails", () => {
  const from = new Uint8ClampedArray([255, 0, 0, 255]), to = new Uint8ClampedArray([0, 0, 255, 255]);
  assert.deepEqual([...effects.applyPixels(from, to, 1, 1, "radial_zoom", 0.25)], [251, 0, 4, 255]);
  assert.deepEqual([...effects.applyPixels(from, to, 1, 1, "radial_zoom", 0.75)], [4, 0, 251, 255]);
  const width = 101, height = 101, image = raster(width, height, (x, y) => x === 75 && y === 50 ? [255, 255, 255, 255] : [0, 0, 0, 255]);
  const rays = effects.applyPixels(image, image, width, height, "radial_zoom", 0.5);
  assert.ok(pixel(rays, width, 80, 50)[0] > 20); assert.equal(pixel(image, width, 80, 50)[0], 0);
  assert.equal(pixel(rays, width, 74, 50)[0], 0); assert.ok(pixel(rays, width, 75, 50)[0] < 10);
});

test("horizontal blur includes the source exposure pulse and completes its dissolve at 80 percent", () => {
  const gray = new Uint8ClampedArray([80, 80, 80, 128]);
  const early = effects.applyPixels(gray, gray, 1, 1, "horizontal_blur", 0.1), peak = effects.applyPixels(gray, gray, 1, 1, "horizontal_blur", 0.466666), late = effects.applyPixels(gray, gray, 1, 1, "horizontal_blur", 0.95);
  assert.ok(peak[0] > 220); assert.ok(peak[0] > early[0]); assert.ok(late[0] < early[0]); assert.equal(peak[3], 128);
  const from = new Uint8ClampedArray([0, 0, 0, 64]), to = new Uint8ClampedArray([128, 128, 128, 192]);
  assert.equal(effects.applyPixels(from, to, 1, 1, "horizontal_blur", 0.8)[3], 192);
});

test("horizontal Gaussian sampling spreads a translucent blue column without hidden red contamination", () => {
  const width = 100, height = 60, image = raster(width, height, x => x === 50 || x === 51 ? [0, 0, 128, 128] : [255, 0, 0, 0]);
  const blurred = effects.applyPixels(image, image, width, height, "horizontal_blur", 0.466666);
  const spread = pixel(blurred, width, 48, 30); assert.equal(spread[0], 0); assert.equal(spread[1], 0); assert.ok(spread[2] > 240); assert.ok(spread[3] > 0 && spread[3] < 128);
  assert.deepEqual(pixel(blurred, width, 20, 30), [0, 0, 0, 0]);
  assert.deepEqual(pixel(blurred, width, 48, 10), spread);
});
