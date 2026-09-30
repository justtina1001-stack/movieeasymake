"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const speed = require("../static/editor_speed.js");
const clip = (changes = {}) => ({ in: 0, out: 4, speed: 1, ...changes });
const point = (time, rate) => ({ time, speed: rate });
const close = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);

for (const rate of [0.25, 0.5, 1, 2, 4]) test(`legacy ${rate}× speed and an empty curve have identical mapping`, () => {
  const fixed = clip({ in: 2, out: 10, speed: rate }), empty = { ...fixed, speed_curve: [] };
  close(speed.clipDuration(fixed), 8 / rate);
  assert.equal(speed.clipDuration(empty), speed.clipDuration(fixed));
  close(speed.sourceAt(fixed, 4 / rate), 6);
  close(speed.timelineAt(fixed, 6), 4 / rate);
  close(speed.sourceAtExtended(fixed, -100, 12), 0);
  close(speed.sourceAtExtended(fixed, 100, 12), 12);
});
test("a linear source-speed ramp integrates logarithmically and inverts exponentially", () => {
  const value = clip({ speed_curve: [point(0, 1), point(4, 2)] });
  close(speed.clipDuration(value), 4 * Math.log(2));
  close(speed.sourceAt(value, 2 * Math.log(2)), 4 * (Math.sqrt(2) - 1));
  close(speed.speedAtSource(value, 2), 1.5);
});
test("a decelerating curve remains continuous, monotonic and reversible", () => {
  const value = clip({ speed_curve: [point(0, 4), point(4, 0.25)] });
  close(speed.clipDuration(value), 4 * Math.log(0.25 / 4) / (0.25 - 4));
  let previous = -1;
  for (let i = 0; i <= 100; i++) {
    const source = i / 25, timeline = speed.timelineAt(value, source);
    assert.ok(timeline > previous); close(speed.sourceAt(value, timeline), source); previous = timeline;
  }
});
test("endpoint rates hold before and after anchors while nonempty curves override fixed speed", () => {
  const value = clip({ out: 8, speed_curve: [point(2, 0.5), point(6, 2)] });
  assert.equal(speed.speedAtSource(value, 0), 0.5); assert.equal(speed.speedAtSource(value, 20), 2);
  close(speed.clipDuration(value), 4 + 4 * Math.log(4) / 1.5 + 1);
  assert.equal(speed.clipDuration(value), speed.clipDuration({ ...value, speed: 4 }));
  for (const source of [0, 1, 2, 3, 6, 7, 8]) close(speed.sourceAt(value, speed.timelineAt(value, source)), source);
});
test("almost equal rates are numerically stable over long source intervals", () => {
  for (const delta of [0, 1e-14, 1e-12, 1e-9]) {
    const value = clip({ in: 100, out: 104, speed_curve: [point(0, 2), point(1000, 2 + delta)] });
    close(speed.clipDuration(value), 2); close(speed.sourceAt(value, 1), 102);
  }
});
test("trimming and extending retains hidden source anchors and the original motion", () => {
  const original = clip({ out: 10, speed_curve: [point(0, 0.25), point(3, 4), point(7, 0.5), point(10, 2)] });
  const snapshot = structuredClone(original), trimmed = { ...original, in: 2, out: 8 }, offset = speed.timelineAt(original, 2);
  speed.validateSpeedCurve(trimmed, 10);
  for (const source of [2, 3, 5, 7, 8]) {
    const local = speed.timelineAt(trimmed, source);
    close(speed.timelineAt(original, source), offset + local); close(speed.sourceAt(trimmed, local), source);
  }
  close(speed.sourceAtExtended(trimmed, -offset, 10), 0);
  close(speed.sourceAtExtended(trimmed, speed.timelineAt(trimmed, 10), 10), 10);
  assert.deepEqual(original, snapshot); assert.deepEqual(trimmed.speed_curve, original.speed_curve);
});
test("splitting at a timeline point preserves duration and exact source-time progression", () => {
  const original = clip({ in: 1, out: 9, speed_curve: [point(0, 2), point(4, 0.25), point(10, 4)] });
  const source = speed.sourceAt(original, speed.clipDuration(original) * 0.37), first = { ...original, out: source }, second = { ...original, in: source };
  close(speed.clipDuration(original), speed.clipDuration(first) + speed.clipDuration(second));
  for (const ratio of [0, 0.2, 0.5, 0.8, 1]) {
    const local = speed.clipDuration(second) * ratio;
    close(speed.sourceAt(second, local), speed.sourceAt(original, speed.clipDuration(first) + local));
  }
});
test("sourceAt clamps current trim, while extended mapping permits restoration to media limits", () => {
  for (const points of [[], [point(0, 0.25), point(4, 4)]]) {
    const value = clip({ in: 1, out: 3, speed_curve: points });
    assert.equal(speed.sourceAt(value, -100), 1); assert.equal(speed.sourceAt(value, 100), 3);
    assert.equal(speed.sourceAtExtended(value, -100, 4), 0); assert.equal(speed.sourceAtExtended(value, 100, 4), 4);
    assert.ok(speed.sourceAtExtended(value, 100) > 4); assert.ok(speed.timelineAt(value, 0) < 0);
  }
});
test("schema rejects malformed, nonfinite, boolean, duplicate, unsorted and oversized curves", () => {
  const valid = [point(0, 1), point(4, 2)];
  for (const points of [null, {}, valid.slice(0, 1), [null, valid[1]], [false, valid[1]], [point(true, 1), valid[1]], [point(0, true), valid[1]], [point("0", 1), valid[1]], [point(0, "1"), valid[1]], [point(NaN, 1), valid[1]], [point(0, Infinity), valid[1]], [point(-1, 1), valid[1]], [point(0, 0.249), valid[1]], [point(0, 4.001), valid[1]], [{ ...valid[0], easing: "linear" }, valid[1]], [{ time: 0 }, valid[1]], [valid[0], valid[0]], valid.toReversed(), [point(0, 1), point(1e-10, 2)], Array.from({ length: 51 }, (_, i) => point(i, 1))]) assert.throws(() => speed.validateSpeedCurve(clip({ speed_curve: points })));
  assert.throws(() => speed.validateSpeedCurve(clip({ speed_curve: valid }), 3));
});
test("long media and trimmed-away source anchors are valid, including the maximum point count", () => {
  const value = clip({ in: 1200, out: 1204, speed_curve: [point(0, 0.25), point(1300, 4)] });
  assert.deepEqual(speed.validateSpeedCurve(value, 1300), value.speed_curve);
  assert.equal(speed.validateSpeedCurve(clip({ speed_curve: Array.from({ length: 50 }, (_, i) => point(i, 1)) })).length, 50);
  assert.deepEqual(speed.validateSpeedCurve(clip()), []); assert.deepEqual(speed.validateSpeedCurve(clip({ speed_curve: [] })), []);
});
test("canonical points and validated points are independent copies", () => {
  const value = clip({ speed_curve: [point(0, 1), point(4, 2)] }), before = structuredClone(value);
  const canonical = speed.canonicalSpeedCurve(value), validated = speed.validateSpeedCurve(value, 4);
  canonical[0].speed = 4; validated[1].time = 1;
  assert.deepEqual(value, before); assert.equal(speed.MAX_SPEED_CURVE_POINTS, 50);
});
test("many piecewise ramps round-trip all timeline samples and share anchor continuity", () => {
  const value = clip({ in: 0.3, out: 19.8, speed_curve: [point(0, 0.25), point(2, 4), point(3, 0.5), point(6, 3), point(12, 0.25), point(20, 2)] });
  const length = speed.clipDuration(value);
  for (let i = 0; i <= 200; i++) { const local = length * i / 200; close(speed.timelineAt(value, speed.sourceAt(value, local)), local); }
  for (const p of value.speed_curve.filter(p => p.time > value.in && p.time < value.out)) close(speed.sourceAt(value, speed.timelineAt(value, p.time)), p.time);
});
