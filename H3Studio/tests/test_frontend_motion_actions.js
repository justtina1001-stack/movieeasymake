"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), motion = require("../static/editor_motion.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const { clone, ProjectSession, signature, totalDuration, transformOverlay, formatTime } = core;
const point = (time, x = 0.4, y = 0.7) => ({ time, x, y, easing: "linear" });
const layer = (changes = {}) => ({ id: "title", kind: "text", start: 1, end: 5,
  x: 0.4, y: 0.7, width: 0.5, rotation: 0, opacity: 1, text: "位置動畫",
  font_size: 0.06, color: "#ffffff", background: "transparent", bold: true,
  align: "center", ...changes });
const project = (item = layer(), videoEnd = 120) => ({ id: "motion-actions", name: "按鈕操作",
  width: 1280, height: 720, fps: 24, updated_at: "r1", audio_clips: [],
  clips: [{ id: "main", media_id: "video", in: 0, out: videoEnd, speed: 1, volume: 1 }],
  overlays: [item] });

// Compile the real shipped function, including its guards and feedback. Stop at
// the first complete function rather than duplicating its implementation here.
function shippedFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} must exist in the shipped editor`);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Could not extract ${name}`);
}

function harness(options = {}) {
  const session = new ProjectSession(options.project || project(options.layer || layer()));
  const state = { session, selectedKind: "overlay", selected: session.project.overlays[0]?.id,
    playhead: 2.5, positionKeyframesReady: true, playing: false,
    media: new Map([["video", { id: "video", kind: "video", duration: 120 }],
      ["picture", { id: "picture", kind: "image" }]]), ...options.state };
  const nodes = new Map(), calls = [], messages = [];
  function node(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, { textContent: "", hidden: false, scrollTop: 0, dataset: {}, style: {},
        classList: { add: name => classes.add(name), remove: name => classes.delete(name),
          contains: name => classes.has(name), toggle(name, enabled) {
            const add = enabled === undefined ? !classes.has(name) : enabled;
            if (add) classes.add(name); else classes.delete(name); return add;
          } },
        focus: () => calls.push(`focus:${id}`),
        scrollIntoView: () => calls.push(`scroll:${id}`), setAttribute() {} });
    }
    return nodes.get(id);
  }
  const locked = () => Boolean(options.locked);
  const sandbox = { state, clone, motion, ...motion, transformOverlay, totalDuration, formatTime,
    clamp: (value, min, max) => Math.max(min, Math.min(max, value)),
    locked, project: () => session.project, overlays: p => p.overlays || [],
    selectedClip: () => (state.selectedKind === "overlay" ? session.project.overlays : session.project.clips)
      .find(item => item.id === state.selected),
    $: node, document: { querySelector: selector => node(selector), getElementById: node },
    notify: message => messages.push(message),
    applyMotionCapability: capabilities => { calls.push("capability"); state.positionKeyframesReady = capabilities.position_keyframes === true; },
    pause: () => { calls.push("pause"); state.playing = false; },
    seek: (time, playing) => { calls.push(`seek:${time}`); assert.equal(playing, false);
      state.playhead = Math.max(0, Math.min(time, totalDuration(session.project))); },
    renderPositionControls: () => calls.push("controls"), renderInspector() {}, renderDisabled() {}, renderTimeline() {},
    edit: (mutate, group) => { if (!locked()) session.change(mutate, group); }, action: fn => fn };
  vm.createContext(sandbox);
  for (const name of ["overlayEdit", "showPositionFeedback", "addSelectedPositionKeyframe"])
    vm.runInContext(shippedFunction(name), sandbox);
  for (const id of ["showPositionAnimation", "addPositionKeyframe"]) {
    const binding = source.match(new RegExp(`\\$\\("${id}"\\)\\.onclick\\s*=\\s*action\\(addSelectedPositionKeyframe\\);`));
    assert.ok(binding, `${id} must invoke the shared add action`);
    vm.runInContext(binding[0], sandbox);
  }
  return { session, state, calls, messages, node, sandbox,
    click: (id = "showPositionAnimation") => node(id).onclick(),
    lastMessage: () => messages.at(-1) || "" };
}

function unchanged(h, before) {
  assert.equal(signature(h.session.project), before);
  assert.equal(h.session.undoStack.length, 0);
  assert.equal(h.session.dirty, false);
}

test("both visible keyframe buttons run the same shipped add action", () => {
  const h = harness();
  assert.equal(h.node("showPositionAnimation").onclick, h.sandbox.addSelectedPositionKeyframe);
  assert.equal(h.node("addPositionKeyframe").onclick, h.sandbox.addSelectedPositionKeyframe);
});

test("an old backend produces visible feedback without modifying the project", () => {
  const h = harness({ state: { positionKeyframesReady: false } }), before = signature(h.session.project);
  h.click();
  unchanged(h, before);
  assert.match(h.lastMessage(), /重新啟動|尚未|未載入|未啟用/);
  assert.equal(h.node("positionActionStatus").textContent, h.lastMessage());
  assert.ok(h.calls.includes("capability"));
  assert.equal(h.calls.includes("pause"), false);
});

for (const [label, selectedKind, selected] of [["no selection", "overlay", null], ["main video", "video", "main"]])
  test(`${label} explains that a text or image layer is needed and changes no content`, () => {
    const h = harness({ state: { selectedKind, selected } }), before = signature(h.session.project);
    h.click(); unchanged(h, before);
    assert.match(h.lastMessage(), /文字|圖片/);
    assert.equal(h.node("positionActionStatus").textContent, h.lastMessage());
  });

test("a video overlay cannot receive a position keyframe", () => {
  const item = layer({ kind: "video", media_id: "video", in: 0, out: 4, speed: 1, volume: 1 });
  const h = harness({ layer: item }), before = signature(h.session.project);
  h.click(); unchanged(h, before); assert.match(h.lastMessage(), /文字|圖片/);
});

for (const kind of ["text", "image"]) test(`${kind} adds the evaluated position as one undoable edit`, () => {
  const item = layer(kind === "image" ? { kind, media_id: "picture" } : {});
  const h = harness({ layer: item }), before = signature(h.session.project);
  h.click();
  assert.deepEqual(clone(h.session.project.overlays[0].position_keyframes), [point(1.5)]);
  assert.equal(h.session.undoStack.length, 1); assert.equal(h.session.dirty, true);
  assert.match(h.lastMessage(), /已加入|已新增/);
  assert.equal(h.node("positionActionStatus").textContent, h.lastMessage());
  h.session.travel(true); assert.equal(signature(h.session.project), before);
});

test("a second button click at the same time reports the recorded keyframe without adding history", () => {
  const h = harness({ layer: layer({ position_keyframes: [point(1.5)] }) });
  const before = signature(h.session.project);
  h.click("addPositionKeyframe"); unchanged(h, before);
  assert.match(h.lastMessage(), /已有|已記錄|已存在/);
  assert.equal(h.session.project.overlays[0].position_keyframes.length, 1);
});

test("adding between animated points records the interpolated pose and preserves the existing curve", () => {
  const item = layer({ position_keyframes: [point(0, 0.1, 0.2), point(4, 0.9, 0.8)] });
  const h = harness({ layer: item, state: { playhead: 3 } });
  h.click();
  assert.deepEqual(clone(h.session.project.overlays[0].position_keyframes),
    [point(0, 0.1, 0.2), point(2, 0.5, 0.5), point(4, 0.9, 0.8)]);
  assert.equal(h.session.undoStack.length, 1);
});

test("adding during playback pauses before recording the current frame", () => {
  const h = harness({ state: { playing: true } });
  h.click();
  assert.equal(h.state.playing, false); assert.ok(h.calls.includes("pause"));
  assert.equal(h.session.project.overlays[0].position_keyframes[0].time, 1.5);
});

for (const playhead of [0, 7]) test(`a cursor outside the layer at ${playhead}s moves to its start before adding`, () => {
  const h = harness({ state: { playhead } });
  h.click();
  assert.equal(h.state.playhead, 1); assert.ok(h.calls.includes("seek:1"));
  assert.deepEqual(clone(h.session.project.overlays[0].position_keyframes), [point(0)]);
  assert.equal(h.session.undoStack.length, 1);
});

test("a layer wholly beyond the video end is explained without creating an invalid keyframe", () => {
  const h = harness({ project: project(layer({ start: 10, end: 14 }), 8), state: { playhead: 0 } });
  const before = signature(h.session.project);
  h.click(); unchanged(h, before);
  assert.match(h.lastMessage(), /片尾|範圍|影片|時間/);
});

for (const [playhead, local] of [[1 - 5e-10, 0], [5 + 5e-10, 4]])
  test(`rounding at the ${local}s layer boundary records the exact boundary without seeking`, () => {
    const h = harness({ state: { playhead } });
    h.click();
    assert.deepEqual(clone(h.session.project.overlays[0].position_keyframes), [point(local)]);
    assert.equal(h.calls.some(call => call.startsWith("seek:")), false);
    assert.equal(h.session.undoStack.length, 1);
  });

test("the 100 point limit explains the block while an existing point remains selectable", () => {
  const h = harness({ layer: layer({ end: 101, position_keyframes: Array.from({ length: 100 }, (_, i) => point(i)) }),
    state: { playhead: 51.5 } });
  const before = signature(h.session.project);
  h.click(); unchanged(h, before); assert.match(h.lastMessage(), /100|上限|最多/);
  h.state.playhead = 51; h.click(); unchanged(h, before);
  assert.match(h.lastMessage(), /已有|已記錄|已存在/);
  assert.equal(h.session.project.overlays[0].position_keyframes.length, 100);
});

test("locked editing cannot add a keyframe or seek", () => {
  const h = harness({ locked: true }), before = signature(h.session.project);
  h.click(); unchanged(h, before);
  assert.equal(h.calls.includes("pause"), false);
  assert.equal(h.calls.some(call => call.startsWith("seek:")), false);
});
