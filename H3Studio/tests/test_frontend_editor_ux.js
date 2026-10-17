"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const editor = require("../static/editor.js"), subtitles = require("../static/editor_subtitles.js"), library = require("../static/editor_media_library.js");
const source = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
const project = () => ({ id: "a".repeat(32), name: "圖片配音", width: 360, height: 360, fps: 24, clips: [], audio_clips: [], overlays: [], canvas_duration: 500, background: "#112233" });
function load(context, name) {
  const start = source.search(new RegExp(`^  (?:async )?function ${name}\\(`, "m")), rest = source.slice(start);
  assert.notEqual(start, -1);
  for (let end = rest.indexOf("\n"); end >= 0; end = rest.indexOf("\n", end + 1)) {
    const candidate = rest.slice(0, end);
    try { new vm.Script(candidate); vm.runInContext(candidate, context); return; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Cannot extract ${name}`);
}
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const element = () => ({ textContent: "", innerHTML: "", value: "", disabled: false, open: false, classList: { add() {}, remove() {}, toggle() {} } });

test("5000 subtitles coexist with 50 ordinary layers and preserve animation through save, copy and undo", () => {
  let index = 0;
  const cues = Array.from({ length: 5000 }, (_, i) => ({ start: i / 10, end: (i + 1) / 10, text: `字幕 ${i}` }));
  const layers = subtitles.createSubtitleLayers(cues, { fps: 24, totalDuration: 500, existingCount: 0, makeId: () => `cue_${++index}` }).layers;
  layers[0].animation_in = { type: "fade", duration: 0.05 };
  layers[0].animation_idle = { preset: "float", amount: 1, speed: 1 };
  const value = project();
  value.overlays = [...Array.from({ length: 50 }, (_, i) => ({ ...layers[0], id: `title_${i}`, track_id: `title_track_${i}`, start: i, end: i + 0.5 })), ...layers];
  assert.doesNotThrow(() => editor.validateProject(value));
  assert.equal(editor.generalLayerCount(value), 50); assert.equal(editor.subtitleCount(value), 5000);
  const saved = editor.editableContent(value), copied = editor.clone(saved);
  assert.deepEqual(copied.overlays.at(-5000).animation_idle, layers[0].animation_idle);
  assert.equal(subtitles.parseSRT(subtitles.stringifySRT(cues)).length, 5000);
  const session = new editor.ProjectSession(value);
  session.change(p => p.overlays.pop()); assert.equal(editor.subtitleCount(session.project), 4999);
  session.travel(true); assert.equal(editor.subtitleCount(session.project), 5000);
  assert.throws(() => editor.validateProject({ ...value, overlays: [...value.overlays, { ...layers[0], id: "extra", track_id: "subtitle_extra" }] }), /5,000/);
  assert.throws(() => editor.validateProject({ ...value, overlays: [...value.overlays, { ...layers[0], id: "extra", track_id: "ordinary_extra" }] }), /50/);
});

test("background duration participates in preview length, drafts and content signatures", () => {
  const value = project(); value.canvas_duration = 10;
  assert.equal(editor.totalDuration(value), 10);
  assert.equal(editor.locateTime(value, 2), null);
  assert.doesNotThrow(() => editor.validateProject(value));
  assert.notEqual(editor.signature(value), editor.signature({ ...value, canvas_duration: 12 }));
  assert.notEqual(editor.signature(value), editor.signature({ ...value, background: "#abcdef" }));
  for (const duration of [-1, 0.001, 601, Infinity, "10"]) assert.throws(() => editor.validateProject({ ...value, canvas_duration: duration }), /片長/);
  for (const background of ["red", null, "#123"]) assert.throws(() => editor.validateProject({ ...value, background }), /背景/);
});

function backgroundControls() {
  const value = { ...project(), canvas_duration: 0, background: "#000000" };
  const session = new editor.ProjectSession(value), elements = new Map();
  const state = { session, media: new Map(), zoom: 50, playhead: 0, selectedKind: "video", selected: null, previewIndex: -1 };
  const node = id => {
    if (!elements.has(id)) elements.set(id, { ...element(), style: {}, clientWidth: 600, setAttribute() {}, insertAdjacentHTML() {}, querySelectorAll: () => [] });
    return elements.get(id);
  };
  const document = { activeElement: null, querySelectorAll: () => [] };
  const context = vm.createContext({ ...editor, core: editor, state, $: node, document, project: () => session.project, action: fn => fn,
    locked: () => false, selectedClip: () => null, syncOutputSize() {}, renderMedia() {}, renderProjectList() {}, renderInspector() {},
    renderStatus() {}, renderExport() {}, fitCanvas() {}, renderCacheStatus() {}, renderOverlayPreview() {}, renderSpeedControls() {},
    renderColorTrack: () => 0, transitionButtons: () => "", colorPreview: { update() {} },
    deck: { pause() {}, clear() {} }, videoLayers: { pause() {}, seek() {} }, transitionPreview: { pause() {}, seek() {} }, videoElements: [] });
  for (const name of ["render", "renderTimeline", "updatePlayhead", "pause", "renderPlaybackStatus", "seek", "edit", "transportSignature", "travel"]) load(context, name);
  const binding = source.match(/^  \$\("canvasDuration"\)\.onchange = .+;$/m);
  assert.ok(binding); vm.runInContext(binding[0], context);
  context.render();
  return { context, state, session, node, document };
}

test("the shipped background duration change handler creates a playable empty project and survives save and undo", async () => {
  const { context, state, session, node } = backgroundControls();
  node("canvasDuration").value = "3";
  await node("canvasDuration").onchange();
  assert.equal(session.project.canvas_duration, 3); assert.equal(editor.totalDuration(session.project), 3);
  assert.equal(session.dirty, true); assert.equal(session.undoStack.length, 1);
  assert.equal(node("previewSeek").max, 3); assert.match(node("timeDisplay").innerHTML, /00:03/);
  assert.match(node("timelineSummary").textContent, /3\.00 秒/); assert.equal(node("previewClipName").textContent, "圖層／背景預覽");
  await session.save(async snapshot => ({ ...editor.clone(snapshot), updated_at: "saved-background" }));
  assert.equal(session.dirty, false); assert.equal(session.project.canvas_duration, 3);
  context.seek(2, false); assert.equal(state.playhead, 2);
  context.travel(true); assert.equal(session.project.canvas_duration, 0); assert.equal(node("previewSeek").max, 0); assert.equal(state.playhead, 0);
  context.travel(false); assert.equal(session.project.canvas_duration, 3); assert.equal(node("previewSeek").max, 3);
  node("canvasDuration").value = "601";
  assert.throws(() => node("canvasDuration").onchange(), /片長/); assert.equal(session.project.canvas_duration, 3);
});

test("a delayed render preserves unfinished background inputs until their native change event", () => {
  const { context, session, node, document } = backgroundControls();
  document.activeElement = node("canvasDuration"); node("canvasDuration").value = "3";
  context.render(); assert.equal(node("canvasDuration").value, "3"); assert.equal(session.project.canvas_duration, 0);
  document.activeElement = null; node("canvasDuration").onchange(); assert.equal(session.project.canvas_duration, 3);
  document.activeElement = node("canvasBackground"); node("canvasBackground").value = "#204060";
  context.render(); assert.equal(node("canvasBackground").value, "#204060"); assert.equal(session.project.background, "#000000");
});

test("a backend that drops background settings cannot mark the edited project saved", async () => {
  const session = new editor.ProjectSession(project());
  session.change(value => { value.background = "#abcdef"; });
  await assert.rejects(session.save(async snapshot => { const response = editor.clone(snapshot); delete response.background; return { ...response, updated_at: "new" }; }), /背景片長或顏色/);
  assert.equal(session.dirty, true); assert.equal(session.project.background, "#abcdef");
});

test("nudging an audio tail past the project end still moves by exactly one frame", () => {
  const value = project();
  value.audio_clips = [{ id: "tail", media_id: "voice", in: 0, out: 100, start: 580, track: 0, volume: 1, fade_in: 0, fade_out: 0 }];
  const session = new editor.ProjectSession(value), state = { selectedKind: "audio", selected: "tail" };
  const context = vm.createContext({ state, audioClips: editor.audioClips, clamp: editor.clamp, edit: mutate => session.change(mutate) });
  load(context, "moveSelected"); context.moveSelected(-1);
  assert.equal(session.project.audio_clips[0].start, 580 - 1 / 24);
  assert.equal(session.undoStack.length, 1); session.travel(true); assert.equal(session.project.audio_clips[0].start, 580);
});

test("media filters combine Unicode name search, type, current usage and removal without changing a project", () => {
  const value = project(); value.overlays = [{ id: "image", kind: "image", media_id: "image" }]; value.audio_clips = [{ media_id: "sound" }];
  const items = [{ id: "image", name: "Ｈ３ 森林.png", kind: "image" }, { id: "sound", name: "森林配音.wav", kind: "audio" }, { id: "video", name: "森林.mp4" }, { id: "removed", name: "森林.png", kind: "image", library_removed_at: "today" }];
  const before = JSON.stringify(value);
  assert.deepEqual(library.filterMedia(items, value, { query: "h3", kind: "image", used: true }).map(item => item.id), ["image"]);
  assert.deepEqual(library.filterMedia(items, value, { query: "森林", used: true }).map(item => item.id), ["image", "sound"]);
  assert.deepEqual(library.filterMedia(items, value, { kind: "video" }).map(item => item.id), ["video"]);
  assert.equal(JSON.stringify(value), before);
});

test("global Space and Delete leave ordinary buttons, links and summaries to their native behavior", async () => {
  let handler, plays = 0, deletes = 0;
  const context = vm.createContext({ state: {}, $: () => ({ open: false }), action: fn => fn,
    document: { addEventListener: (_event, fn) => { handler = fn; } }, locked: () => false, togglePlay: async () => { plays++; }, deleteSelected: () => { deletes++; } });
  const start = source.indexOf('  document.addEventListener("keydown", action(async event => {'), end = source.indexOf('  root.addEventListener("blur"', start);
  vm.runInContext(source.slice(start, end), context);
  for (const tag of ["button", "a", "summary", "role=button", "role=tab", "role=slider", "role=separator"]) {
    for (const [key, code] of [[" ", "Space"], ["Delete", "Delete"]]) {
      let prevented = false;
      const target = { closest: selector => selector.split(",").some(item => item === tag || item === `[${tag}]`) ? target : null };
      await handler({ key, code, target, preventDefault() { prevented = true; } });
      assert.equal(prevented, false, tag);
    }
  }
  assert.equal(plays, 0); assert.equal(deletes, 0);
  await handler({ key: " ", code: "Space", target: { closest: () => null }, preventDefault() {} });
  assert.equal(plays, 1);
});

test("an export from A keeps polling and storing A after the user opens B", async () => {
  const writes = [], current = { id: "B" }, state = { exportJob: { id: "export-A", project_id: "A", status: "running", auto_download: false } };
  let refreshes = 0;
  const context = vm.createContext({ state, project: () => current, api: async () => ({ id: "export-A", project_id: "A", status: "completed" }),
    storagePut: (...args) => writes.push(args), renderExport() {}, loadExportHistory: () => { refreshes++; },
    exporting: () => ["queued", "running"].includes(state.exportJob.status), clearTimeout() {}, setTimeout() {} });
  load(context, "pollExport"); await context.pollExport();
  assert.equal(state.exportJob.project_id, "A"); assert.equal(writes[0][0], "h3-editor-export:A");
  assert.equal(current.id, "B"); assert.equal(refreshes, 1);
});

test("upload successes survive a later failure and the editor stays usable while the next file uploads", async () => {
  const elements = new Map(), pending = deferred(), state = { media: new Map(), imageOverlaysReady: true, busy: false };
  const context = vm.createContext({ state, AbortController, locked: () => false, $: id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    escape: value => value, renderDisabled() {}, renderMedia() {}, notify() {}, uploadFile: async file => file.name === "a.mp4" ? { id: "A" } : pending.promise });
  load(context, "importFiles"); load(context, "renderUploadResults");
  const result = context.importFiles([{ name: "a.mp4" }, { name: "b.wav" }, { name: "c.exe" }]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.media.has("A"), true); assert.equal(state.busy, false);
  pending.resolve(Promise.reject(new Error("offline"))); const imported = await result;
  assert.equal(imported.length, 1); assert.equal(state.media.has("A"), true); assert.equal(state.uploadBatch, null);
  assert.match(elements.get("uploadStatus").innerHTML, /完成/); assert.match(elements.get("uploadStatus").innerHTML, /offline/); assert.match(elements.get("uploadStatus").innerHTML, /不支援/);
});

test("upload cancellation preserves completed assets and skips the remaining files", async () => {
  let calls = 0; const state = { media: new Map(), imageOverlaysReady: true, busy: false }, elements = new Map();
  const context = vm.createContext({ state, AbortController, locked: () => false, $: id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    escape: value => value, renderDisabled() {}, renderMedia() {}, notify() {}, uploadFile: async () => { calls++; state.uploadBatch.controller.abort(); return { id: "A" }; } });
  load(context, "importFiles"); load(context, "renderUploadResults");
  const imported = await context.importFiles([{ name: "a.mp4" }, { name: "b.mp4" }]);
  assert.equal(calls, 1); assert.equal(imported.length, 1); assert.equal(state.media.has("A"), true);
  assert.match(elements.get("uploadStatus").innerHTML, /已取消/);
});

test("the actual upload transport releases abort listeners and terminates timed out requests", async () => {
  const requests = [];
  class XHR {
    constructor() { this.upload = {}; requests.push(this); }
    open() {}
    send() { this.sent = true; }
    abort() { this.onabort(); }
  }
  const context = vm.createContext({ XMLHttpRequest: XHR, FormData: class { append() {} } });
  load(context, "uploadFile");
  const controller = new AbortController(), request = context.uploadFile({ name: "large.mp4" }, () => {}, "/api/editor/upload", controller.signal);
  assert.equal(requests[0].timeout, 600000); assert.equal(requests[0].sent, true);
  controller.abort(); await assert.rejects(request, /取消/);
  const timeout = context.uploadFile({ name: "offline.mp4" }, () => {});
  requests[1].ontimeout(); await assert.rejects(timeout, /10 分鐘/);
});
