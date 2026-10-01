"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), animationMath = require("../static/editor_animations.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
function load(context, name) {
  const start = source.search(new RegExp(`  (?:async )?function ${name}\\(`)), rest = source.slice(start), following = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
  assert.ok(start >= 0 && following >= 0); vm.runInContext(rest.slice(0, following + 1), context);
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const project = (id = "A") => ({ id, fps: 24, clips: [
  { id: "first", media_id: "source", in: 0, out: 4, speed: 1, volume: 1, animation_in: { type: "fade", duration: 0.6 }, animation_out: { type: "slide_left", duration: 0.5 }, transition_out: { type: "crossfade", duration: 1, next_id: "second" } },
  { id: "second", media_id: "source", in: 4, out: 8, speed: 1, volume: 1 },
], overlays: [] });
function fixture() {
  const state = { session: { project: project() }, selected: "first", selectedKind: "video", playRequest: 0, playhead: 1, playing: false, animationPreviewEnd: null, audio: {}, media: new Map() };
  const pending = deferred(), seeks = [], pauses = [], button = { setAttribute() {} };
  const context = vm.createContext({ ...core, state, animationMath, project: () => state.session.project,
    selectedClip: () => state.session.project.clips.find(clip => clip.id === state.selected), locked: () => false,
    clipTimelineStart: (value, kind, id) => value.clips.slice(0, value.clips.findIndex(clip => clip.id === id)).reduce((sum, clip) => sum + core.duration(clip), 0),
    prepareAudio: () => pending.promise, $: () => button,
    deck: { pause: () => pauses.push("main") }, videoLayers: { pause: () => pauses.push("layers") }, transitionPreview: { pause: () => pauses.push("transitions") }, renderPlaybackStatus() {},
    seek(time, play) { state.playhead = time; seeks.push({ project: state.session.project.id, time, play }); },
  });
  for (const name of ["pause", "togglePlay", "animationNext", "previewSelectedAnimation"]) load(context, name);
  return { context, state, pending, seeks, pauses, button };
}
test("a project switch during audio initialization cannot start a stale animation preview in the newly opened project", async () => {
  const f = fixture(), preview = f.context.previewSelectedAnimation("in");
  f.state.session = { project: project("B") }; f.state.playhead = 0; f.context.pause(); f.pending.resolve(); await preview;
  assert.equal(f.state.playing, false); assert.equal(f.state.animationPreviewEnd, null); assert.deepEqual(f.seeks, [{ project: "A", time: 0, play: false }]);
});
test("even replacing a session with the same project ID invalidates an asynchronous playback request", async () => {
  const f = fixture(), playback = f.context.togglePlay();
  f.state.session = { project: project("A") }; f.pending.resolve(); assert.equal(await playback, false); assert.equal(f.state.playing, false); assert.equal(f.seeks.length, 0);
});
test("pause invalidates a pending playback request before it can seek or resume any transport", async () => {
  const f = fixture(), playback = f.context.togglePlay(); f.context.pause(); f.pending.resolve();
  assert.equal(await playback, false); assert.equal(f.state.playing, false); assert.equal(f.seeks.length, 0); assert.deepEqual(f.pauses, ["main", "layers", "transitions"]);
});
test("scrubbing to another time during audio initialization cancels the earlier playback request", async () => {
  const f = fixture(), playback = f.context.togglePlay(); f.state.playhead = 3; f.pending.resolve();
  assert.equal(await playback, false); assert.equal(f.state.playhead, 3); assert.equal(f.state.playing, false); assert.equal(f.seeks.length, 0);
});
test("concurrent playback requests allow only the latest request to start the normal transport", async () => {
  const f = fixture(), first = f.context.togglePlay(), second = f.context.togglePlay(); f.pending.resolve();
  assert.equal(await first, false); assert.equal(await second, true); assert.equal(f.seeks.length, 1); assert.deepEqual(f.seeks[0], { project: "A", time: 1, play: true });
});
for (const [tab, start, end] of [["in", 0, 0.6], ["out", 3.5, 4], ["transition", 3.5, 4.5]]) test(`a current ${tab} preview starts at its exact effect window and retains its scoped stop time`, async () => {
  const f = fixture(), preview = f.context.previewSelectedAnimation(tab); f.pending.resolve(); await preview;
  assert.equal(f.state.playing, true); assert.equal(f.state.animationPreviewEnd, end);
  assert.deepEqual(f.seeks, [{ project: "A", time: start, play: false }, { project: "A", time: start, play: true }]);
});
test("cancelling animation preview initialization never reinstalls its discarded auto-stop time", async () => {
  const f = fixture(), preview = f.context.previewSelectedAnimation("out"); f.context.pause(); f.pending.resolve(); await preview;
  assert.equal(f.state.animationPreviewEnd, null); assert.equal(f.state.playing, false); assert.equal(f.seeks.filter(seek => seek.play).length, 0);
});
test("failed audio initialization keeps the preview paused and preserves the reported error", async () => {
  const f = fixture(), preview = f.context.previewSelectedAnimation("in"); f.pending.reject(new Error("audio initialization failed"));
  await assert.rejects(preview, /audio initialization failed/); assert.equal(f.state.playing, false); assert.equal(f.state.animationPreviewEnd, null);
});
