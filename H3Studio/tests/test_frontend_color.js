"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const color = require("../static/editor_color.js");
const grade = (extra = {}) => ({ id: "grade", start: 0, end: 1, ...extra });
const project = (items = [], extra = {}) => ({ fps: 24, clips: [{ id: "main", in: 0, out: 3 }], audio_clips: [], overlays: [], color_adjustments: items, ...extra });
const rgba = [0, 0, 0, 0, 24, 48, 96, 128, 64, 128, 192, 255, 255, 255, 255, 255];

test("Python and JavaScript produce identical uint8 pixels across shared RGB/RGBA, extremes and stacked grades", () => {
  const fixture = require("./fixtures/editor_color_parity.json");
  assert.equal(fixture.format, "h3-editor-color-srgb-v1");
  assert.equal(fixture.cases.length, 24);
  for (const example of fixture.cases) {
    const before = JSON.stringify(example.pixels);
    assert.deepEqual(color.applyPixels(example.pixels, example.items, { channels: example.channels }), example.expected, example.name);
    assert.equal(JSON.stringify(example.pixels), before);
  }
});

test("optional and empty color collection remains empty; canonical defaults are explicit and independent", () => {
  assert.deepEqual(color.adjustments({}), []);
  assert.deepEqual(color.canonicalAdjustments({}), []);
  assert.deepEqual(color.validateAdjustments({}), []);
  const source = project([grade({ name: "  🎬 校色  " })]), before = JSON.stringify(source);
  const canonical = color.validateAdjustments(source);
  assert.deepEqual(canonical, [{ id: "grade", start: 0, end: 1, ...color.DEFAULTS, name: "🎬 校色" }]);
  assert.equal(JSON.stringify(source), before);
  canonical[0].contrast = 25;
  assert.equal(source.color_adjustments[0].contrast, undefined);
});

test("schema rejects invalid collection, unknown fields and duplicate identifiers across every track", () => {
  for (const collection of [null, {}, "", false, Array.from({ length: 21 }, (_, index) => grade({ id: `g${index}`, start: index, end: index + 1 }))]) assert.throws(() => color.validateAdjustments(project(collection)), /格式|20/);
  for (const item of [null, [], true, grade({ id: "../invalid" }), grade({ id: "a".repeat(65) }), grade({ id: "grade\n" }), grade({ id: 10 }), grade({ gamma: 1 }), grade({ preset: "natural" })]) assert.throws(() => color.validateAdjustments(project([item])));
  for (const id of ["main", "audio", "overlay"]) assert.throws(() => color.validateAdjustments(project([grade({ id })], { audio_clips: [{ id: "audio" }], overlays: [{ id: "overlay" }] })), /重複/);
  assert.throws(() => color.validateAdjustments(project([grade(), grade({ start: 2, end: 3 })])), /重複/);
});

test("strict numeric and boolean fields refuse string coercion, nonfinite input and range overflow", () => {
  const numeric = ["start", "end", "strength", ...color.CONTROLS];
  for (const field of numeric) for (const invalid of [null, false, true, "1", NaN, Infinity, -Infinity, undefined]) assert.throws(() => color.validateAdjustments(project([grade({ [field]: invalid })])), `${field}:${String(invalid)}`);
  for (const enabled of [null, 0, 1, "true", undefined]) assert.throws(() => color.validateAdjustments(project([grade({ enabled })])), /啟用/);
  for (const [field, invalid] of [["strength", -0.01], ["strength", 100.01], ["exposure", -2.01], ["exposure", 2.01], ["contrast", -100.01], ["highlights", 100.01]]) assert.throws(() => color.validateAdjustments(project([grade({ [field]: invalid })])));
  assert.doesNotThrow(() => color.validateAdjustments(project([grade({ strength: 0, enabled: false, exposure: -2, contrast: -100, highlights: 100 })])));
});

test("names count Unicode characters, trim whitespace and refuse blank or overlong labels", () => {
  for (const name of [false, null, undefined, "", " \n ", "字".repeat(81), "🎬".repeat(81), "字幕\u0000", "\t字幕", "字幕\n", "字幕\u007f"]) assert.throws(() => color.validateAdjustments(project([grade({ name })])), /名稱/);
  assert.equal(color.validateAdjustments(project([grade({ name: " 🎬".repeat(40) })]))[0].name.length, 119);
  assert.doesNotThrow(() => color.validateAdjustments(project([grade({ name: "🎬".repeat(80) })])));
});

test("time intervals need one frame within 0–600 seconds but do not extend or constrain movie duration", () => {
  for (const timing of [{ start: -0.001 }, { end: 600.001 }, { end: 0 }, { start: 1, end: 0 }, { end: 1 / 24 - 1e-7 }]) assert.throws(() => color.validateAdjustments(project([grade(timing)])), /起訖/);
  assert.doesNotThrow(() => color.validateAdjustments(project([grade({ end: 1 / 24 })])));
  assert.doesNotThrow(() => color.validateAdjustments(project([grade({ start: 599, end: 600 })])));
  for (const fps of [0, -1, null, true, "24", NaN]) assert.throws(() => color.validateAdjustments(project([grade()], { fps })), /影格率/);
});

test("overlap counts disabled and neutral intervals; touching endpoints reuse the same lane", () => {
  const four = Array.from({ length: 4 }, (_, index) => grade({ id: `g${index}`, enabled: false }));
  assert.doesNotThrow(() => color.validateAdjustments(project(four)));
  assert.throws(() => color.validateAdjustments(project([...four, grade({ id: "fifth" })])), /重疊 4/);
  const ordered = [grade({ id: "late", start: 1, end: 2 }), grade({ id: "first", start: 0, end: 1 }), grade({ id: "overlap", start: 0.5, end: 1.5 })];
  assert.deepEqual(color.timelineLanes(project(ordered)).map(lane => lane.map(item => item.id)), [["first", "late"], ["overlap"]]);
  assert.deepEqual(ordered.map(item => item.id), ["late", "first", "overlap"]);
});

test("active grades use inclusive start/exclusive end and preserve project processing order", () => {
  const items = [grade({ id: "second", exposure: 1 }), grade({ id: "first", brightness: 10 }), grade({ id: "off", exposure: 1, enabled: false }), grade({ id: "zero", exposure: 1, strength: 0 }), grade({ id: "natural" })];
  assert.deepEqual(color.activeAdjustments(project(items), 0).map(item => item.id), ["second", "first"]);
  assert.deepEqual(color.activeAdjustments(project(items), 1), []);
  assert.deepEqual(color.activeAdjustments(project(items), -0.001), []);
  assert.deepEqual(color.activeAdjustments(project(items), NaN), []);
});

test("neutral, disabled and zero strength are exact identity copies for RGB, RGBA and typed buffers", () => {
  for (const pixels of [rgba, Uint8Array.from(rgba), Uint8ClampedArray.from(rgba)]) {
    const unchanged = color.applyPixels(pixels, [grade(), grade({ enabled: false, exposure: 2 }), grade({ strength: 0, saturation: -100 })]);
    assert.deepEqual(Array.from(unchanged), rgba); assert.notEqual(unchanged, pixels); assert.equal(unchanged.constructor, pixels.constructor);
  }
  assert.deepEqual(color.applyPixels([64, 128, 255], [], { channels: 3 }), [64, 128, 255]);
  for (const [pixels, options] of [[[1, 2], {}], [new Float32Array(4), {}], [[1, 2, 3, 4], { channels: 1 }]]) assert.throws(() => color.applyPixels(pixels, [], options), /RGB/);
});

test("matrix combines Rec.709 saturation, exposure, white balance and contrast with a neutral alpha row", () => {
  const neutral = color.affineMatrix(grade());
  assert.deepEqual(neutral, [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0]);
  assert.deepEqual(color.applyPixels([64, 128, 192, 123], [grade({ saturation: -100 })]), [119, 119, 119, 123]);
  assert.deepEqual(color.applyPixels([64, 128, 192, 123], [grade({ exposure: 1 })]), [128, 255, 255, 123]);
  assert.deepEqual(color.applyPixels([0, 128, 255, 77], [grade({ contrast: -100 })]), [128, 128, 128, 77]);
  assert.deepEqual(color.applyPixels([0, 128, 255, 77], [grade({ brightness: 100 })]), [64, 192, 255, 77]);
  const matrix = color.affineMatrix(grade({ exposure: 2, brightness: 40, contrast: 10, saturation: 20, temperature: -50, tint: 20, strength: 50 }));
  assert.deepEqual(matrix.slice(15), [0, 0, 0, 1, 0]);
  assert.ok(matrix[0] > 1 && matrix[2] < 0);
});

test("tone table interpolates 257 normalized endpoints, protects endpoints and scales highlights/shadows", () => {
  const neutral = color.toneTable(grade()), controls = color.toneTable(grade({ shadows: 100, highlights: -100 }));
  assert.equal(controls.length, 257); assert.equal(controls[0], 0); assert.equal(controls[256], 1);
  assert.equal(neutral[128], 0.5); assert.equal(controls[128], 0.5);
  assert.ok(controls[32] > neutral[32]); assert.ok(controls[224] < neutral[224]);
  assert.equal(color.interpolateTone(0.125 + 1 / 512, controls), (controls[32] + controls[33]) / 2);
  assert.equal(color.interpolateTone(-10, controls), 0); assert.equal(color.interpolateTone(10, controls), 1);
  assert.deepEqual(color.toneTable(grade({ shadows: 100, highlights: 60, strength: 50 })), color.toneTable(grade({ shadows: 50, highlights: 30 })));
});

test("each stacked grade clips and half-up quantizes before the next, keeping input alpha unchanged", () => {
  const input = [64, 128, 192, 101], first = grade({ exposure: 2 }), second = grade({ exposure: -2 });
  const forward = color.applyPixels(input, [first, second]), reverse = color.applyPixels(input, [second, first]);
  assert.deepEqual(forward, [64, 64, 64, 101]); assert.deepEqual(reverse, [64, 128, 192, 101]);
  assert.deepEqual(color.applyPixels(input, [first, second]), color.applyPixels(color.applyPixels(input, [first]), [second]));
  assert.deepEqual(input, [64, 128, 192, 101]);
});

test("presets expose only validated neutral-default controls and cannot mutate across projects", () => {
  assert.deepEqual(Object.keys(color.PRESETS), ["natural", "warm", "cool", "mono", "cinema", "vivid"]);
  for (const value of Object.values(color.PRESETS)) {
    assert.ok(Object.isFrozen(value) && Object.isFrozen(value.controls));
    assert.deepEqual(Object.keys(value.controls), color.CONTROLS);
    assert.doesNotThrow(() => color.validateAdjustments(project([grade(value.controls)])));
    assert.equal(typeof value.label, "string");
  }
  assert.equal(color.hasEffect(color.PRESETS.natural.controls), false);
  assert.equal(color.PRESETS.mono.controls.saturation, -100);
});

class Element {
  constructor(name) { this.name = name; this.attributes = {}; this.children = []; this.style = {}; this.parent = null; }
  setAttribute(key, value) { this.attributes[key] = value; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  replaceChildren() { this.children.forEach(child => { child.parent = null; }); this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
}
const document = () => ({ body: new Element("body"), createElementNS: (_, name) => new Element(name) });

test("SVG preview uses ordered sRGB affine/table primitives and skips rebuilding the same active interval", () => {
  const doc = document(), stage = new Element("canvas"), mounted = color.mountPreview(stage, doc);
  const value = project([grade({ id: "first", exposure: 0.5 }), grade({ id: "second", shadows: 20 })]);
  mounted.update(value, 0);
  const filter = doc.body.children[0].children[0].children[0];
  assert.equal(filter.attributes["color-interpolation-filters"], "sRGB");
  assert.deepEqual(filter.children.map(child => child.name), ["feColorMatrix", "feComponentTransfer", "feColorMatrix", "feComponentTransfer"]);
  assert.equal(filter.children[0].attributes.in, "SourceGraphic"); assert.equal(filter.children[2].attributes.in, "tone_0");
  assert.equal(filter.children[1].children[0].attributes.tableValues.split(" ").length, 257);
  assert.equal(filter.children[1].children[3].attributes.type, "identity");
  assert.match(stage.style.filter, /^url\(#h3-editor-color-\d+\)$/);
  const firstPrimitive = filter.children[0]; mounted.update(value, 0.5); assert.equal(filter.children[0], firstPrimitive);
  mounted.update(value, 1); assert.equal(stage.style.filter, ""); assert.equal(filter.children.length, 0);
  mounted.update(value, 0); mounted.clear(); assert.equal(stage.style.filter, "");
  mounted.destroy(); mounted.destroy(); assert.equal(doc.body.children.length, 0);
  mounted.update(value, 0); assert.equal(stage.style.filter, "");
});

test("identity-only SVG preview clears any prior active filter and separate mounts use unique IDs", () => {
  const doc = document(), firstStage = new Element("canvas"), secondStage = new Element("canvas");
  const first = color.mountPreview(firstStage, doc), second = color.mountPreview(secondStage, doc), value = project([grade({ contrast: 10 })]);
  first.update(value, 0); second.update(value, 0); assert.notEqual(firstStage.style.filter, secondStage.style.filter);
  for (const item of [grade(), grade({ exposure: 2, enabled: false }), grade({ exposure: 2, strength: 0 })]) { first.update(project([item]), 0); assert.equal(firstStage.style.filter, ""); }
  assert.throws(() => color.mountPreview({}, doc), /畫布/);
  first.destroy(); second.destroy();
});
