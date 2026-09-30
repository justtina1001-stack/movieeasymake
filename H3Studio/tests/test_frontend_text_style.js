"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const { clone, TEXT_STYLE_DEFAULTS, canonicalTextStyle, canonicalOverlay, validateTextStyle,
  validateProject, editableContent, signature, canonicalSavedSignature, resolveDraft,
  overlayRasterKey, requireTextStyleSupport, transformOverlay, shiftOverlayTime,
  splitOverlayAt, transformTrackDrag, ProjectSession } = core;
const style = { stroke_width: 0.075, stroke_color: "#263A5F", fill_mode: "linear_gradient",
  gradient_start: "#FFEEDD", gradient_end: "#CC4433", gradient_angle: 135 };
const layer = (changes = {}) => ({ id: "title", kind: "text", start: 1, end: 5,
  x: 0.5, y: 0.7, width: 0.7, rotation: 0, opacity: 1, text: "文字樣式\nText style",
  font_size: 0.06, color: "#ffffff", background: "transparent", bold: true, align: "center", ...changes });
const project = (layers = [layer()]) => ({ id: "text-style-project", name: "文字樣式",
  width: 1280, height: 720, fps: 24, updated_at: "r1", clips: [
    { id: "main", media_id: "video", in: 0, out: 8, volume: 1, speed: 1 }], audio_clips: [], overlays: layers });
const media = new Map([["video", { kind: "video", duration: 20 }]]);
const key = value => overlayRasterKey(value, 1280, 720);

test("legacy text signatures, saved baselines and raster keys survive explicit style defaults", () => {
  const original = layer(), explicit = layer({ ...TEXT_STYLE_DEFAULTS, stroke_color: "#000000",
    gradient_start: "#FFFFFF", gradient_end: "#FF8A3D" });
  assert.deepEqual(canonicalTextStyle(explicit), {});
  assert.deepEqual(canonicalOverlay(original), canonicalOverlay(explicit));
  assert.equal(signature(project([original])), signature(project([explicit])));
  assert.equal(canonicalSavedSignature(signature(project([original]))), signature(project([explicit])));
  const legacyKey = JSON.stringify({ text: original.text, font_size: original.font_size,
    color: original.color, background: original.background, bold: original.bold,
    align: original.align, boxWidth: original.width, width: 1280, height: 720 });
  assert.equal(key(explicit), legacyKey);
  assert.equal(new ProjectSession(project([explicit])).dirty, false);
  assert.doesNotThrow(() => requireTextStyleSupport(project([explicit]), false));
});

test("canonical styles lowercase colors and preserve inactive nondefaults in editable content", () => {
  const inactive = layer({ stroke_width: 0, stroke_color: "#AABBCC", fill_mode: "solid",
    gradient_start: "#DDEEFF", gradient_end: "#CC4433", gradient_angle: 0 });
  const expected = { stroke_color: "#aabbcc", gradient_start: "#ddeeff", gradient_end: "#cc4433", gradient_angle: 0 };
  assert.deepEqual(canonicalTextStyle(inactive), expected);
  for (const [field, value] of Object.entries(expected)) assert.equal(editableContent(project([inactive])).overlays[0][field], value);
  assert.notEqual(signature(project()), signature(project([inactive])));
  assert.equal(key(inactive), key(layer()));
});

test("raster keys invalidate active fill and stroke changes while ignoring hidden style choices", () => {
  const solid = layer({ ...style, fill_mode: "solid", stroke_width: 0 });
  for (const changes of [{ stroke_color: "#eeeeee" }, { gradient_start: "#111111" },
    { gradient_end: "#222222" }, { gradient_angle: 45 }, { x: 0.2, start: 2, end: 6, opacity: 0.3 }])
    assert.equal(key(solid), key({ ...solid, ...changes }));
  assert.notEqual(key(solid), key({ ...solid, color: "#333333" }));
  const active = layer(style);
  assert.equal(key(active), key({ ...active, color: "#333333" }));
  assert.equal(key(active), key({ ...active, stroke_color: "#263a5f", gradient_start: "#ffeedd", gradient_end: "#cc4433" }));
  for (const changes of [{ stroke_width: 0.1 }, { stroke_color: "#eeeeee" }, { gradient_start: "#111111" },
    { gradient_end: "#222222" }, { gradient_angle: 45 }, { fill_mode: "solid" }, { font_size: 0.08 }, { width: 0.8 }])
    assert.notEqual(key(active), key({ ...active, ...changes }), JSON.stringify(changes));
});

test("style validation accepts endpoint widths, angles and case-insensitive six-digit colors", () => {
  for (const changes of [{}, { stroke_width: 0.25, gradient_angle: 360 },
    { stroke_width: 0, gradient_angle: 0, fill_mode: "linear_gradient", gradient_end: "#Ab12Cd" }])
    assert.doesNotThrow(() => validateProject(project([layer(changes)]), media));
});
for (const [field, invalid] of Object.entries({
  stroke_width: [-0.001, 0.251, "0.1", null, false, NaN, Infinity],
  gradient_angle: [-1, 361, "90", null, true, NaN, Infinity],
  stroke_color: ["#fff", "#abcdffaa", "red", null, 123],
  gradient_start: ["#ff0000ff", "#gg0000", null],
  gradient_end: ["#ff00", "transparent", null],
  fill_mode: ["gradient", "LINEAR_GRADIENT", "SOLID", null, true],
})) test(`style validation rejects malformed ${field} without mutating the source`, () => {
  for (const value of invalid) {
    const item = layer({ [field]: value }), before = { ...item };
    assert.throws(() => validateTextStyle(item), /描邊|漸層/);
    assert.deepEqual(item, before);
  }
});
test("style fields cannot silently disappear from a non-text overlay", () => {
  for (const kind of ["image", "video"]) for (const [field, value] of Object.entries(TEXT_STYLE_DEFAULTS))
    assert.throws(() => validateTextStyle({ kind, [field]: value }), /只適用於文字/);
});

test("an older backend refuses nondefault style before writing, including inactive settings", () => {
  for (const changes of [style, { stroke_color: "#ff0000" }, { gradient_angle: 0 }, { gradient_end: "#000000" }]) {
    const value = project([layer(changes)]), before = clone(value);
    assert.throws(() => requireTextStyleSupport(value, false), /草稿仍保留/);
    assert.deepEqual(value, before);
    assert.doesNotThrow(() => requireTextStyleSupport(value, true));
  }
});

for (const changes of [style, { stroke_color: "#ffffff" }, { gradient_start: "#000000" }, { gradient_angle: 0 }])
  test(`save protects style and history when a backend drops ${Object.keys(changes).join(",")}`, async () => {
    const session = new ProjectSession(project());
    session.change(p => Object.assign(p.overlays[0], changes));
    const before = clone(session.project), baseline = session.savedSignature;
    await assert.rejects(session.save(async sent => {
      const saved = clone(sent); for (const field of Object.keys(TEXT_STYLE_DEFAULTS)) delete saved.overlays[0][field];
      saved.updated_at = "r2"; return saved;
    }), /文字描邊／漸層/);
    assert.deepEqual(session.project, before); assert.equal(session.savedSignature, baseline);
    assert.equal(session.dirty, true); assert.equal(session.undoStack.length, 1);
    assert.equal(session.travel(true), true); assert.equal(session.dirty, false);
    assert.equal(session.travel(false), true); assert.deepEqual(session.project, before);
  });

test("save accepts backend lowercase normalization and omitted defaults without style loss", async () => {
  const session = new ProjectSession(project()); session.change(p => Object.assign(p.overlays[0], style));
  await session.save(async sent => ({ ...sent, overlays: sent.overlays.map(canonicalOverlay), updated_at: "r2" }));
  assert.equal(session.dirty, false); assert.equal(session.project.overlays[0].stroke_color, "#263a5f");
  assert.equal(session.project.overlays[0].gradient_start, "#ffeedd");
  assert.equal(session.project.overlays[0].gradient_end, "#cc4433");
});

test("recoverable and conflicting drafts preserve complete style choices and latest revision", () => {
  const server = project(), draftProject = project([layer(style)]), record = { project: draftProject, savedSignature: signature(server) };
  server.updated_at = "r2";
  const restored = resolveDraft(server, record, media);
  assert.equal(restored.status, "recoverable"); assert.equal(restored.project.updated_at, "r2");
  assert.deepEqual(restored.project.overlays.map(canonicalOverlay), draftProject.overlays.map(canonicalOverlay));
  const conflicting = resolveDraft({ ...server, name: "其他編輯" }, record, media);
  assert.equal(conflicting.status, "conflict"); assert.deepEqual(conflicting.draftProject.overlays.map(canonicalOverlay), draftProject.overlays.map(canonicalOverlay));
  assert.equal(record.project.updated_at, "r1");
});

test("text styles survive split, trim, motion edits, layer transfer and undo as editing content", () => {
  const title = layer({ ...style, position_keyframes: [
    { time: 0, x: 0.5, y: 0.7, easing: "linear" }, { time: 4, x: 0.8, y: 0.2, easing: "ease_out" }] });
  const original = project([title]), session = new ProjectSession(original);
  session.change(p => splitOverlayAt(p, "title", 3, () => "second"));
  for (const item of session.project.overlays) assert.deepEqual(canonicalTextStyle(item), canonicalTextStyle(title));
  const trimmed = shiftOverlayTime(session.project, "second", "right", -0.25, media).project;
  const moved = transformTrackDrag(trimmed, { kind: "overlay", id: "second" }, { kind: "overlay", mode: "insert", index: 0, start: 6 }, media).project;
  for (const item of moved.overlays) assert.deepEqual(canonicalTextStyle(item), canonicalTextStyle(title));
  const motionEdit = transformOverlay(moved, "second", { position_keyframes: [
    { time: 0, x: 0.2, y: 0.2, easing: "linear" }, { time: 1, x: 0.8, y: 0.8, easing: "ease_in" }] }, media).project;
  session.change(p => { p.overlays = motionEdit.overlays; });
  assert.deepEqual(canonicalTextStyle(session.project.overlays.find(item => item.id === "second")), canonicalTextStyle(title));
  session.travel(true); session.travel(true); assert.deepEqual(session.project, original);
  session.travel(false); session.travel(false);
  assert.equal(session.project.overlays.find(item => item.id === "second").start, 6);
});

// Load the shipped handlers with only DOM surfaces substituted; actions below
// exercise the actual editor's style editing, capability and duplicate logic.
function shipped(name) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`)); assert.ok(start >= 0, name);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) {
    const code = source.slice(start, end);
    try { new vm.Script(code); return code; } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Cannot extract ${name}`);
}
function harness(options = {}) {
  const session = new ProjectSession(project([layer(options.style || {})])), nodes = new Map(), calls = [];
  const state = { session, selectedKind: "overlay", selected: "title", playhead: 2,
    textStyleReady: options.ready !== false, media, ...options.state };
  function node(id) {
    if (!nodes.has(id)) { const classes = new Set(); nodes.set(id, { value: "", disabled: false, style: {},
      attributes: {}, classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
      setAttribute(name, value) { this.attributes[name] = value; } }); }
    return nodes.get(id);
  }
  const sandbox = { ...core, state, $: node, document: { activeElement: null },
    selectedClip: () => session.project.overlays.find(item => item.id === state.selected),
    project: () => session.project, locked: () => Boolean(options.locked),
    edit: (mutate, group) => { if (!options.locked) session.change(mutate, group); }, action: fn => fn,
    uid: () => "duplicate", pause: () => calls.push("pause"), render: () => calls.push("render"),
    renderInspector: () => sandbox.renderTextStyleControls(sandbox.selectedClip()), renderDisabled: () => calls.push("disabled") };
  vm.createContext(sandbox);
  for (const name of ["overlayEdit", "editTextStyle", "renderTextStyleControls", "applyTextStyleCapability"])
    vm.runInContext(shipped(name), sandbox);
  const start = source.indexOf('  for (const [id, field] of [["overlayFillMode"'), end = source.indexOf('  $("checkTextStyleSupport").onclick', start);
  assert.ok(start >= 0 && end > start); vm.runInContext(source.slice(start, end), sandbox);
  return { session, state, sandbox, node, calls, change(id, value) { node(id).value = value; return node(id).onchange(); }, click(id) { return node(id).onclick(); } };
}

test("real style controls edit percent widths, gradient colors and direction as undoable actions", () => {
  const h = harness();
  h.change("overlayStrokeWidth", "7.5"); assert.equal(h.session.project.overlays[0].stroke_width, 0.075);
  h.change("overlayStrokeColor", "#224488"); h.change("overlayFillMode", "linear_gradient");
  h.change("overlayGradientStart", "#ff0000"); h.change("overlayGradientEnd", "#0000ff");
  h.change("overlayGradientAngle", "45");
  h.click("swapGradientColors"); assert.equal(h.session.project.overlays[0].gradient_start, "#0000ff");
  assert.equal(h.session.project.overlays[0].gradient_end, "#ff0000");
  h.click("gradientHorizontal"); assert.equal(h.session.project.overlays[0].gradient_angle, 0);
  assert.equal(h.node("gradientHorizontal").attributes["aria-pressed"], "true");
  assert.equal(h.node("gradientColorPreview").style.background, "linear-gradient(90deg, #0000ff, #ff0000)");
  h.click("gradientVertical"); assert.equal(h.session.project.overlays[0].gradient_angle, 90);
  h.change("overlayFillMode", "solid");
  assert.equal(h.node("overlayGradientFields").classList.contains("hidden"), true);
  assert.equal(h.node("overlaySolidColorField").classList.contains("hidden"), false);
  assert.equal(h.session.project.overlays[0].gradient_start, "#0000ff");
  h.session.travel(true); assert.equal(h.session.project.overlays[0].fill_mode, "linear_gradient");
});

test("real invalid width or angle controls restore their prior displayed value atomically", () => {
  const h = harness({ style });
  const before = signature(h.session.project);
  h.sandbox.document.activeElement = h.node("overlayStrokeWidth");
  assert.throws(() => h.change("overlayStrokeWidth", "26"), /描邊/);
  assert.equal(h.node("overlayStrokeWidth").value, 7.5);
  h.sandbox.document.activeElement = h.node("overlayGradientAngle");
  assert.throws(() => h.change("overlayGradientAngle", "361"), /漸層/);
  assert.equal(h.node("overlayGradientAngle").value, 135);
  assert.equal(signature(h.session.project), before); assert.equal(h.session.undoStack.length, 0);
});

test("old capability disables only new style controls and prevents synthetic edits until rechecked", () => {
  const h = harness({ ready: false }), before = signature(h.session.project);
  h.sandbox.applyTextStyleCapability({ text_style: false }); h.sandbox.renderTextStyleControls(h.sandbox.selectedClip());
  for (const id of ["overlayFillMode", "overlayStrokeWidth", "overlayStrokeColor", "overlayGradientAngle"])
    assert.equal(h.node(id).disabled, true);
  for (const id of ["overlayText", "overlayColor", "overlayX", "overlayWidth"])
    assert.equal(h.node(id).disabled, false);
  assert.throws(() => h.change("overlayStrokeWidth", "5"), /重新啟動/);
  assert.equal(signature(h.session.project), before); assert.equal(h.session.undoStack.length, 0);
  h.sandbox.applyTextStyleCapability({ text_style: true }); h.sandbox.renderTextStyleControls(h.sandbox.selectedClip());
  assert.equal(h.node("overlayStrokeWidth").disabled, false);
  h.change("overlayStrokeWidth", "5"); assert.equal(h.session.project.overlays[0].stroke_width, 0.05);
});

test("locked style handlers make no project edits and real duplicate retains complete text styling", () => {
  const locked = harness({ locked: true, style }), before = signature(locked.session.project);
  locked.change("overlayStrokeWidth", "5"); assert.equal(signature(locked.session.project), before);
  const h = harness({ style: { ...style, position_keyframes: [{ time: 0, x: 0.5, y: 0.7, easing: "linear" }] } });
  const binding = source.split(/\r?\n/).find(line => line.startsWith('  $("duplicateClip").onclick = action('));
  assert.ok(binding); vm.runInContext(binding, h.sandbox); h.click("duplicateClip");
  assert.equal(h.session.project.overlays.length, 2);
  assert.deepEqual(canonicalTextStyle(h.session.project.overlays[1]), canonicalTextStyle(h.session.project.overlays[0]));
  assert.deepEqual(h.session.project.overlays[1].position_keyframes, h.session.project.overlays[0].position_keyframes);
  assert.equal(h.session.project.overlays[1].id, "duplicate");
  assert.equal(h.session.undoStack.length, 1); h.session.travel(true); assert.equal(h.session.project.overlays.length, 1);
});
