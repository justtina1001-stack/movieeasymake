"use strict";

const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), subtitleMath = require("../static/editor_subtitles.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const begin = source.indexOf("  function selectSubtitleCue("), end = source.indexOf("  function renderAutoCaptionControls(", begin);
assert.ok(begin >= 0 && end > begin, "Shipped subtitle navigation controller remains extractable");
const controllerSource = source.slice(begin, end);
for (const name of ["selectSubtitleCue", "openSubtitleWorkspace", "changeSubtitleTrack", "stepSubtitleCue"]) {
  assert.match(controllerSource, new RegExp(`function ${name}\\(`));
}
const cue = (id, track, start, end, text) => ({ id, kind: "text", track_id: track, start, end, text, ...subtitleMath.DEFAULT_STYLE });
const projectValue = () => ({ id: "project", name: "字幕校對", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [{ id: "video", media_id: "source", in: 0, out: 15, speed: 1, volume: 1 }], audio_clips: [],
  overlays: [cue("a3", "subtitle_A", 7, 9, "第三句"), cue("a1", "subtitle_A", 1, 3, "第一句"), cue("a2", "subtitle_A", 3, 5, "第二句"),
    { id: "image", kind: "image", track_id: "subtitle_A", start: 10, end: 11, media_id: "picture", x: .5, y: .5, width: .3, rotation: 0, opacity: 1 },
    { id: "overlay-video", kind: "video", track_id: "subtitle_A", start: 12, end: 13, media_id: "source", in: 0, out: 1, speed: 1, volume: 0, x: .5, y: .5, width: .3, rotation: 0, opacity: 1 },
    cue("b2", "subtitle_B", 4, 6, "第二軌第二句"), cue("b1", "subtitle_B", 0, 2, "第二軌第一句"), cue("title", "regular_title", 0, 2, "一般標題")],
});
function harness(value = projectValue()) {
  const session = new core.ProjectSession(value), calls = [], elements = new Map();
  const state = { session, selectedKind: "overlay", selected: "a1", subtitleTrack: "subtitle_A", subtitleWorkspaceOpen: false,
    subtitleWorkspaceTab: "style", playhead: 8, playing: true, blocked: false };
  const $ = id => {
    if (!elements.has(id)) elements.set(id, { value: "", scrollTop: 91, focus(options) { calls.push({ type: "focus", id, options }); } });
    return elements.get(id);
  };
  const sandbox = { state, $, subtitleMath, project: () => state.session?.project || null,
    overlays: core.overlays, locked: () => state.blocked,
    pause() { state.playing = false; calls.push({ type: "pause" }); },
    seek(time, play) { state.playhead = time; calls.push({ type: "seek", time, play }); },
    render() { calls.push({ type: "render", selected: state.selected, kind: state.selectedKind, track: state.subtitleTrack }); },
    renderSubtitleControls() { calls.push({ type: "controls", open: state.subtitleWorkspaceOpen, tab: state.subtitleWorkspaceTab }); },
    renderDisabled() {},
  };
  vm.runInNewContext(controllerSource + "\nthis.controller = { selectSubtitleCue, openSubtitleWorkspace, changeSubtitleTrack, stepSubtitleCue };", sandbox);
  return { state, session, calls, $, ...sandbox.controller };
}
const immutable = h => ({ project: h.session.project, signature: core.signature(h.session.project), undo: JSON.stringify(h.session.undoStack),
  redo: JSON.stringify(h.session.redoStack), saved: h.session.savedSignature, dirty: h.session.dirty });
function unchanged(h, before) {
  assert.equal(h.session.project, before.project);
  assert.equal(core.signature(h.session.project), before.signature);
  assert.equal(JSON.stringify(h.session.undoStack), before.undo);
  assert.equal(JSON.stringify(h.session.redoStack), before.redo);
  assert.equal(h.session.savedSignature, before.saved);
  assert.equal(h.session.dirty, before.dirty);
}
const selection = h => ({ id: h.state.selected, kind: h.state.selectedKind, track: h.state.subtitleTrack, group: h.session.group });

test("selecting a cue pauses, seeks, renders before focusing without scrolling, and ends the previous undo input group", () => {
  const h = harness(); h.session.group = "overlay-text:a1";
  const before = immutable(h);
  h.selectSubtitleCue("b2");
  assert.deepEqual(selection(h), { id: "b2", kind: "overlay", track: "subtitle_B", group: null });
  assert.equal(h.state.subtitleWorkspaceOpen, true); assert.equal(h.state.subtitleWorkspaceTab, "content");
  assert.equal(h.state.playing, false); assert.equal(h.state.playhead, 4);
  assert.deepEqual(h.calls.map(call => call.type), ["pause", "seek", "render", "focus"]);
  assert.equal(h.calls[1].play, false);
  assert.equal(h.calls.at(-1).id, "overlayText"); assert.equal(h.calls.at(-1).options.preventScroll, true);
  unchanged(h, before);
});

test("changing subtitle tracks selects the new track's chronologically first cue and cannot edit the previous track", () => {
  const h = harness();
  h.session.change(value => { value.overlays.find(item => item.id === "a1").text = "第一軌已校對"; }, "overlay-text:a1");
  const before = immutable(h);
  h.$("subtitleTrack").value = "subtitle_B"; h.changeSubtitleTrack();
  assert.equal(h.state.selected, "b1"); assert.equal(h.state.subtitleTrack, "subtitle_B");
  assert.equal(h.state.playhead, 0); assert.equal(h.session.group, null);
  assert.equal(h.calls.some(call => call.type === "focus"), false);
  unchanged(h, before);
  h.session.change(value => { value.overlays.find(item => item.id === h.state.selected).text = "第二軌校對結果"; }, `overlay-text:${h.state.selected}`);
  assert.equal(h.session.project.overlays.find(item => item.id === "a1").text, "第一軌已校對");
  assert.equal(h.session.project.overlays.find(item => item.id === "b1").text, "第二軌校對結果");
  assert.equal(h.session.undoStack.length, 2);
  h.session.travel(true);
  assert.equal(h.session.project.overlays.find(item => item.id === "a1").text, "第一軌已校對");
  assert.equal(h.session.project.overlays.find(item => item.id === "b1").text, "第二軌第一句");
});

test("reselecting the current track keeps its current cue instead of returning to the first sentence", () => {
  const h = harness(); h.state.selected = "b2"; h.state.subtitleTrack = "subtitle_B";
  h.$("subtitleTrack").value = "subtitle_B"; h.changeSubtitleTrack();
  assert.equal(h.state.selected, "b2"); assert.equal(h.state.playhead, 4);
  assert.equal(h.calls.some(call => call.type === "focus"), false);
});

test("subtitle selection rejects ordinary text, missing IDs, and non-text layers even on a subtitle track", () => {
  const h = harness(); h.session.group = "overlay-text:a1";
  const before = immutable(h), selected = selection(h);
  for (const id of ["title", "missing", "image", "overlay-video"]) h.selectSubtitleCue(id);
  assert.deepEqual(selection(h), selected);
  assert.equal(h.state.subtitleWorkspaceOpen, false);
  assert.equal(h.calls.length, 0);
  unchanged(h, before);
});

test("previous and next cue navigation respects sorted sentence order, first/last boundaries, and the active track", () => {
  const h = harness(), before = immutable(h);
  h.stepSubtitleCue(-1); assert.equal(h.state.selected, "a1"); assert.equal(h.calls.length, 0);
  h.stepSubtitleCue(1); assert.equal(h.state.selected, "a2"); assert.equal(h.state.playhead, 3);
  h.stepSubtitleCue(1); assert.equal(h.state.selected, "a3"); assert.equal(h.state.playhead, 7);
  const lastCalls = h.calls.length;
  h.stepSubtitleCue(1); assert.equal(h.state.selected, "a3"); assert.equal(h.calls.length, lastCalls);
  h.stepSubtitleCue(-1); assert.equal(h.state.selected, "a2");
  assert.equal(h.state.subtitleTrack, "subtitle_A");
  assert.equal(h.calls.filter(call => call.type === "focus").every(call => call.options.preventScroll === true), true);
  unchanged(h, before);
});

test("locked selection, track changes, navigation, and opening do not change selection or undo state", () => {
  const h = harness(); h.state.blocked = true; h.session.group = "overlay-text:a1";
  const before = immutable(h), selected = selection(h);
  h.selectSubtitleCue("b2"); h.$("subtitleTrack").value = "subtitle_B"; h.changeSubtitleTrack(); h.stepSubtitleCue(1);
  assert.deepEqual(selection(h), selected); assert.equal(h.calls.length, 0);
  h.openSubtitleWorkspace();
  assert.deepEqual(selection(h), selected);
  assert.equal(h.calls.some(call => ["seek", "pause", "focus"].includes(call.type)), false);
  unchanged(h, before);
});

test("opening an empty project or a project with only normal title text selects the create pane", () => {
  for (const onlyTitle of [false, true]) {
    const value = projectValue(); value.overlays = onlyTitle ? value.overlays.filter(item => item.id === "title") : [];
    const h = harness(value); h.state.selectedKind = "video"; h.state.selected = "video";
    const before = immutable(h), selected = selection(h);
    h.openSubtitleWorkspace();
    assert.equal(h.state.subtitleWorkspaceOpen, true); assert.equal(h.state.subtitleWorkspaceTab, "create");
    assert.deepEqual(selection(h), selected);
    assert.equal(h.$("inspectorPanel").scrollTop, 0);
    assert.deepEqual(h.calls.map(call => call.type), ["controls"]);
    unchanged(h, before);
  }
});

test("opening the workspace resumes the selected cue on the chosen track and falls back safely for a missing track", () => {
  const h = harness(); h.state.selected = "a2";
  h.openSubtitleWorkspace(); assert.equal(h.state.selected, "a2"); assert.equal(h.state.playhead, 3);
  assert.equal(h.calls.some(call => call.type === "focus"), false);
  assert.equal(h.$("inspectorPanel").scrollTop, 0);
  h.state.subtitleTrack = "subtitle_missing"; h.state.selected = "title";
  h.openSubtitleWorkspace();
  assert.equal(h.state.subtitleTrack, "subtitle_A"); assert.equal(h.state.selected, "a1"); assert.equal(h.state.playhead, 1);
});

test("missing tracks and selected cues never make navigation choose unrelated content", () => {
  const h = harness(), before = immutable(h);
  h.state.subtitleTrack = "subtitle_missing"; const missing = selection(h); h.stepSubtitleCue(1);
  assert.deepEqual(selection(h), missing); assert.equal(h.calls.length, 0);
  h.state.subtitleTrack = "subtitle_A"; h.state.selected = "title"; const title = selection(h); h.stepSubtitleCue(1);
  assert.deepEqual(selection(h), title); assert.equal(h.calls.length, 0);
  h.$("subtitleTrack").value = "unknown"; h.changeSubtitleTrack();
  assert.deepEqual(selection(h), title); assert.equal(h.calls.length, 0);
  unchanged(h, before);
});
