"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), animationMath = require("../static/editor_animations.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
function load(context, name) {
  const start = source.search(new RegExp(`  (?:async )?function ${name}\\(`)), rest = source.slice(start), following = rest.slice(1).search(/\n  (?:(?:async )?function \w+\(|const |let )/);
  assert.ok(start >= 0 && following >= 0); vm.runInContext(rest.slice(0, following + 1), context);
}
const clip = id => ({ id, media_id: "source", in: 0, out: 4, speed: 1, volume: 1 });
const project = type => ({ id: "reference-project", name: "參考轉場", updated_at: "r1", width: 1280, height: 720, fps: 24,
  clips: [{ ...clip("a"), ...(type ? { transition_out: { type, duration: 1, next_id: "b" } } : {}) }, clip("b")], audio_clips: [], overlays: [] });
function fixture(type = "radial_zoom") {
  const value = project(type), requests = [], drafts = [], notes = [];
  const state = { session: null, projects: [value], media: new Map([["source", { kind: "video", duration: 8, width: 1280, height: 720 }]]),
    clipAnimationsReady: true, clipTransitionsReady: true, transitionEffectsReady: true, referenceTransitionsReady: false,
    textStyleReady: true, textFontsReady: true, textIdleReady: true, busy: false };
  const noop = () => {}, context = vm.createContext({ ...core, state, animationMath, root: {}, URL, location: { href: "http://localhost/editor" }, history: { replaceState() {} }, clearTimeout,
    exporting: () => ["queued", "running"].includes(state.exportJob?.status), renderExport: noop, loadExportHistory: noop, refreshAutoCaptions: async () => {}, uid: () => "draft-owner", pause: noop, deck: { clear: noop }, videoLayers: { clear: noop }, clearTransitionPreview: noop, closeExportDialog: noop,
    readDraft: () => null, storageGet: () => null, storagePut: () => true, storedRecoveries: () => [], renderRecovery: noop, render: noop, seek: noop, videoHistoryUI: { render: noop },
    sessionChanged: session => drafts.push(core.clone(session.project)), renderStatus: noop, renderProjectList: noop, renderDisabled: noop,
    project: () => state.session.project, selectedClip: () => state.session.project.clips[0], locked: () => false, canSwitch: () => true,
    rememberDraft: () => drafts.push(core.clone(state.session.project)), errorNotice: error => notes.push(error.message), notify: message => notes.push(message), renderAnimationControls: noop,
    json: (method, value) => ({ method, body: JSON.stringify(value) }), $: () => ({ checked: false }),
    api: async (path, options) => { const payload = JSON.parse(options.body); requests.push({ path, payload }); return path === "/api/editor/projects" ? { project: { ...payload, id: "copy", updated_at: "r2" } } : { ...payload, updated_at: "r2" }; },
    edit: mutator => state.session.change(mutator),
  });
  for (const name of ["installProject", "saveProject", "newProject", "saveRecoveryCopy", "startExport", "animationNext", "requireSelectedTransitionSupport", "setSelectedAnimation", "setSelectedAnimationDuration", "previewSelectedAnimation", "checkAnimationSupport"]) load(context, name);
  context.installProject(value);
  return { context, state, requests, drafts, notes };
}
test("v10 support cannot authorize reference saves and the optional third capability defaults to false", () => {
  for (const type of core.REFERENCE_TRANSITION_TYPES) {
    const value = project(type), before = core.clone(value);
    assert.throws(() => core.requireTransitionEffectSupport(value, true), /放射／橫向模糊/);
    assert.throws(() => core.requireTransitionEffectSupport(value, true, false), /草稿仍保留/);
    assert.doesNotThrow(() => core.requireTransitionEffectSupport(value, true, true)); assert.deepEqual(value, before);
    value.clips[0].transition_out.duration = 0; assert.doesNotThrow(() => core.requireTransitionEffectSupport(value, false));
  }
  const value = project("radial_zoom"); value.overlays = [{ kind: "video", transition_out: { type: "rgb_glitch", duration: 1, next_id: "other" } }];
  assert.throws(() => core.requireTransitionEffectSupport(value, false, true), /內建轉場特效/);
});
for (const type of core.REFERENCE_TRANSITION_TYPES) test(`${type} manual save and real installed autosave preserve the edited session until v11 support arrives`, async () => {
  const f = fixture(type); f.state.session.change(value => { value.name = "保留參考轉場草稿"; });
  const before = core.clone(f.state.session.project);
  await assert.rejects(f.context.saveProject(), /草稿仍保留/); await assert.rejects(f.state.session.autosaver.flush(), /草稿仍保留/);
  assert.deepEqual(f.requests, []); assert.deepEqual(f.state.session.project, before); assert.equal(f.state.session.dirty, true);
  f.state.referenceTransitionsReady = true; await f.context.saveProject();
  assert.equal(f.requests.length, 1); assert.equal(f.requests[0].payload.clips[0].transition_out.type, type); assert.equal(f.state.session.dirty, false);
});
test("copy, recovery copy and export stop before sending unsupported reference content", async () => {
  for (const action of ["copy", "recovery", "export"]) {
    const f = fixture(), original = core.clone(f.state.session.project);
    const item = { record: { project: core.clone(original), savedSignature: core.signature(original) }, key: "protected-recovery" };
    if (action === "recovery") f.state.session.conflict = true;
    await assert.rejects(action === "copy" ? f.context.newProject(true) : action === "recovery" ? f.context.saveRecoveryCopy(item) : f.context.startExport(), /草稿仍保留/);
    assert.deepEqual(f.requests, []); assert.deepEqual(f.state.session.project, original); assert.equal(f.state.busy, false);
    assert.equal(item.key, "protected-recovery"); assert.deepEqual(item.record.project, original);
  }
});
test("shipped selection uses package durations, retains edited timings and permits removal on old servers", () => {
  for (const [type, duration] of [["radial_zoom", 1], ["horizontal_blur", 0.5]]) {
    const f = fixture(null); assert.throws(() => f.context.setSelectedAnimation(type, "transition"), /草稿仍保留/);
    assert.equal(f.state.session.project.clips[0].transition_out, undefined);
    f.state.referenceTransitionsReady = true; f.context.setSelectedAnimation(type, "transition");
    assert.deepEqual(core.clone(f.state.session.project.clips[0].transition_out), { type, duration, next_id: "b" });
    f.context.setSelectedAnimationDuration(0.75, "transition"); f.context.setSelectedAnimation(type, "transition"); assert.equal(f.state.session.project.clips[0].transition_out.duration, 0.75);
    f.state.referenceTransitionsReady = false; assert.throws(() => f.context.setSelectedAnimationDuration(1.5, "transition"), /草稿仍保留/);
    f.context.setSelectedAnimation("none", "transition"); assert.equal(f.state.session.project.clips[0].transition_out, undefined);
  }
});
test("shipped recheck reads the separate capability strictly and keeps v10's four effects enabled", async () => {
  const f = fixture(null);
  for (const advertised of [undefined, "true", true]) {
    f.context.api = async () => ({ schema_version: advertised === true ? 11 : 10, clip_animations: true, clip_transitions: true, transition_effects: true, reference_transitions: advertised });
    await f.context.checkAnimationSupport(); assert.equal(f.state.transitionEffectsReady, true); assert.equal(f.state.referenceTransitionsReady, advertised === true);
  }
});
test("unsupported reference preview cannot start transport while preserving its configured setting", async () => {
  const f = fixture(), before = core.clone(f.state.session.project);
  await assert.rejects(f.context.previewSelectedAnimation("transition"), /草稿仍保留/); assert.deepEqual(f.state.session.project, before); assert.deepEqual(f.requests, []);
});
