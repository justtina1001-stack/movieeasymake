"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const UI = require("../static/editor_speed_ui.js"), math = require("../static/editor_speed.js");
const near = (actual, expected, tolerance = 1e-10) => assert.ok(Math.abs(actual - expected) <= tolerance * Math.max(1, Math.abs(expected)), `${actual} != ${expected}`);
for (const preset of UI.PRESETS) test(`speed preset ${preset.id} is accepted by the real timing engine and the chart duration matches export timing`, () => {
  const points = UI.presetPoints(preset.id, 1.27, 17.432, 1.35), clip = { in: 1.27, out: 17.432, speed: 1.35, speed_curve: points };
  assert.ok(points.length >= 2 && points.length <= 50);
  assert.doesNotThrow(() => math.validateSpeedCurve(clip, 20));
  near(UI.durationFor(points, clip.in, clip.out, clip.speed), math.clipDuration(clip));
  for (let index = 0; index <= 80; index++) {
    const source = clip.in + (clip.out - clip.in) * index / 80;
    near(UI.speedAt(points, source, clip.speed), math.speedAtSource(clip, source));
    near(math.sourceAt(clip, math.timelineAt(clip, source)), source);
  }
});
test("the logarithmic graph returns precise source times and speeds at every supported scale", () => {
  for (const time of [13, 15, 23.25, 27]) for (const speed of [.25, .5, .75, 1, 1.5, 2, 4]) {
    const point = UI.graphPoint(UI.graphX(time, 13, 27), UI.graphY(speed), 13, 27);
    near(point.time, time); near(point.speed, speed);
  }
  assert.deepEqual(UI.graphPoint(-100, -100, 13, 27), { time: 13, speed: 4 });
  assert.deepEqual(UI.graphPoint(1000, 1000, 13, 27), { time: 27, speed: .25 });
});
test("dragging cannot cross adjacent source-time anchors and endpoints remain attached to their original source times", () => {
  const points = [{ time: 3, speed: 1 }, { time: 4, speed: .5 }, { time: 6, speed: 2 }], snapshot = structuredClone(points);
  const draggedLeft = UI.constrainPoint(points, 1, -20, .01, 3, 6), draggedRight = UI.constrainPoint(points, 1, 100, 20, 3, 6);
  assert.ok(draggedLeft.time > 3 && draggedLeft.time < 4); assert.equal(draggedLeft.speed, .25);
  assert.ok(draggedRight.time < 6 && draggedRight.time > 4); assert.equal(draggedRight.speed, 4);
  assert.equal(UI.constrainPoint(points, 0, 4, 2, 3, 6).time, 3);
  assert.equal(UI.constrainPoint(points, 2, 4, 1, 3, 6).time, 6);
  assert.equal(UI.constrainPoint(points, 1, NaN, 1, 3, 6), null);
  assert.deepEqual(points, snapshot);
});
test("a preset applied after trimming retains hidden source anchors and does not mutate the clip", () => {
  const points = [{ time: 0, speed: 2 }, { time: 2, speed: 1 }, { time: 4, speed: .5 }, { time: 6, speed: 1 }, { time: 10, speed: 4 }], original = structuredClone(points);
  const preset = UI.presetPoints("hero", 2, 6), edited = UI.replaceVisiblePoints(points, preset, 2, 6);
  assert.deepEqual(edited[0], points[0]); assert.deepEqual(edited.at(-1), points.at(-1));
  assert.deepEqual(edited.slice(1, -1), preset);
  assert.deepEqual(points, original);
  assert.doesNotThrow(() => math.validateSpeedCurve({ speed_curve: edited }, 10));
  near(UI.durationFor(edited, 2.5, 5.3), math.curveDuration({ speed_curve: edited }, 2.5, 5.3));
});
test("nearly flat ramps show the same duration as the timing engine without numerical cancellation", () => {
  const points = [{ time: 0, speed: 1 }, { time: 10, speed: 1 + 1e-9 }], clip = { in: 0, out: 10, speed_curve: points };
  near(UI.durationFor(points, 0, 10), math.clipDuration(clip), 1e-12);
  near(UI.durationFor([], 2, 8, .25), 24);
  assert.equal(UI.durationFor(points, 2, 2), 0);
});
test("trimmed views sample real boundary speeds instead of flattening the surviving motion", () => {
  const points = [{ time: 0, speed: .25 }, { time: 10, speed: 4 }];
  const path = UI.graphPath(points, 3, 7), head = path.match(/^M([^,]+),([^ ]+)/);
  near(Number(head[1]), UI.GRAPH.left);
  near(Number(head[2]), UI.graphY(math.speedAtSource({ speed_curve: points }, 3)), .0001);
  near(UI.durationFor(points, 3, 7), math.curveDuration({ speed_curve: points }, 3, 7));
});
