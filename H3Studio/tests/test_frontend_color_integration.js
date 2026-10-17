"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const editor = require("../static/editor.js"), color = require("../static/editor_color.js"), colorUI = require("../static/editor_color_ui.js");
const captions = require("../static/editor_auto_captions.js"), subtitles = require("../static/editor_subtitles.js");
const trackDrag = require("../static/editor_track_drag.js").createTrackDragCore(editor);
const { clone, signature, editableContent, ProjectSession, resolveDraft } = editor;
const project = () => ({ id: "project", name: "色彩整合", updated_at: "revision-1", width: 1280, height: 720, fps: 24,
  clips: [{ id: "main", media_id: "video", in: 0, out: 8, volume: 1, speed: 1 }], audio_clips: [], overlays: [] });
const grade = (id = "grade", start = 0, end = 8) => ({ id, start, end, name: "暖色", temperature: 30, contrast: 12 });
const graded = () => ({ ...project(), color_adjustments: [grade()] });
const record = (base, draft) => ({ project: clone(draft), savedSignature: signature(base), savedAt: 12345 });
const saved = snapshot => ({ ...clone(snapshot), updated_at: "revision-2" });
const immutable = session => ({ object: session.project, value: clone(session.project), dirty: session.dirty,
  savedSignature: session.savedSignature, undo: clone(session.undoStack), redo: clone(session.redoStack) });
function unchanged(session, before) {
  assert.equal(session.project, before.object); assert.deepEqual(session.project, before.value);
  assert.equal(session.dirty, before.dirty); assert.equal(session.savedSignature, before.savedSignature);
  assert.deepEqual(session.undoStack, before.undo); assert.deepEqual(session.redoStack, before.redo);
}

test("legacy signatures remain byte compatible for omitted and empty colour collections", () => {
  const p = project();
  const legacy = JSON.stringify({ name: p.name, clips: p.clips, audio_clips: [], overlays: [], width: 1280, height: 720, fps: 24 });
  assert.equal(signature(p), legacy); assert.equal(signature({ ...p, color_adjustments: [] }), legacy);
  assert.equal(editor.canonicalSavedSignature(legacy), signature(p));
  assert.equal(Object.hasOwn(editableContent(p), "color_adjustments"), false);
  assert.equal(Object.hasOwn(editableContent({ ...p, color_adjustments: [] }), "color_adjustments"), false);
  const session = new ProjectSession(p);
  assert.equal(session.change(value => { value.color_adjustments = []; }), false); assert.equal(session.dirty, false);
});
test("neutral colour intervals remain editable and canonical defaults compare equally", () => {
  const p = { ...project(), color_adjustments: [{ id: "neutral", start: 1, end: 3 }] };
  const normalized = clone(p); normalized.color_adjustments = color.canonicalAdjustments(p);
  assert.notEqual(signature(p), signature(project())); assert.equal(signature(p), signature(normalized));
  assert.deepEqual(color.activeAdjustments(p, 2), []);
  assert.throws(() => editor.requireColorSupport(p, false), /草稿仍保留/);
  assert.doesNotThrow(() => editor.requireColorSupport({ ...p, color_adjustments: [] }, false));
});
test("a colour-only edit becomes dirty, saves canonical server fields and survives a fresh session", async () => {
  const session = new ProjectSession(project()), writes = [];
  session.change(value => { value.color_adjustments = [grade()]; });
  assert.equal(session.dirty, true); assert.equal(session.undoStack.length, 1);
  const result = await session.save(async snapshot => {
    writes.push(clone(snapshot)); const response = saved(snapshot);
    response.color_adjustments = color.canonicalAdjustments(snapshot).map(item => Object.fromEntries(Object.entries(item).reverse()));
    return { project: response };
  });
  assert.equal(writes.length, 1); assert.equal(session.dirty, false); assert.equal(session.project.updated_at, "revision-2");
  assert.deepEqual(color.canonicalAdjustments(result), color.canonicalAdjustments(writes[0]));
  const reopened = new ProjectSession(result); assert.equal(reopened.dirty, false); assert.equal(signature(reopened.project), signature(session.project));
});
test("colour changes, removing the last interval and undo/redo preserve other tracks and the live server revision", () => {
  const session = new ProjectSession(graded()), base = clone(session.project);
  session.change(value => { value.color_adjustments[0].saturation = -40; }, "drag-saturation");
  session.change(value => { value.color_adjustments[0].saturation = -60; }, "drag-saturation");
  assert.equal(session.undoStack.length, 1);
  session.change(value => { value.color_adjustments = []; }); session.project.updated_at = "revision-live";
  assert.equal(session.undoStack.length, 2); assert.equal(Object.hasOwn(editableContent(session.project), "color_adjustments"), false);
  session.travel(true); assert.equal(session.project.color_adjustments[0].saturation, -60);
  session.travel(true); assert.deepEqual(session.project.color_adjustments, base.color_adjustments);
  assert.equal(session.project.updated_at, "revision-live"); assert.equal(session.dirty, false);
  session.travel(false); assert.equal(session.project.color_adjustments[0].saturation, -60);
  session.travel(false); assert.deepEqual(session.project.color_adjustments, []);
  assert.deepEqual(session.project.clips, base.clips); assert.deepEqual(session.project.audio_clips, base.audio_clips); assert.deepEqual(session.project.overlays, base.overlays);
});
test("draft recovery retains colour-only edits and treats a colour server change as a conflict", () => {
  const base = graded(), draft = clone(base); draft.color_adjustments[0].tint = 15;
  const raw = record(base, draft), untouched = clone(raw), current = { ...clone(base), updated_at: "new-revision" };
  const recovered = resolveDraft(current, raw);
  assert.equal(recovered.status, "recoverable"); assert.equal(recovered.project.color_adjustments[0].tint, 15);
  assert.equal(recovered.project.updated_at, "new-revision"); assert.deepEqual(raw, untouched);
  const changedServer = clone(current); changedServer.color_adjustments[0].temperature = -30;
  const conflict = resolveDraft(changedServer, raw);
  assert.equal(conflict.status, "conflict"); assert.equal(conflict.project.color_adjustments[0].temperature, -30);
  assert.equal(conflict.draftProject.color_adjustments[0].tint, 15);
});
test("recovering a draft that clears every colour interval deletes the server colour field", () => {
  for (const emptyField of [false, true]) {
    const base = graded(), draft = clone(base);
    if (emptyField) draft.color_adjustments = []; else delete draft.color_adjustments;
    const raw = record(base, draft), untouched = clone(raw), server = clone(base);
    const resolved = resolveDraft(server, raw);
    assert.equal(resolved.status, "recoverable"); assert.equal(Object.hasOwn(resolved.project, "color_adjustments"), false);
    assert.deepEqual(color.adjustments(resolved.project), []); assert.notEqual(signature(resolved.project), signature(server));
    assert.deepEqual(server, base); assert.deepEqual(raw, untouched);
  }
});
test("a server that drops or changes colour cannot mark the draft as saved", async () => {
  const mutations = [response => { delete response.color_adjustments; }, response => { response.color_adjustments[0].temperature = -30; },
    response => { response.color_adjustments[0].enabled = false; }, response => { response.color_adjustments[0].start = 1; },
    response => { response.color_adjustments[0].strength = 0; }];
  for (const mutate of mutations) {
    const session = new ProjectSession(project()); session.change(value => { value.color_adjustments = [grade()]; });
    const before = immutable(session);
    await assert.rejects(session.save(async snapshot => { const response = saved(snapshot); mutate(response); return response; }), /未完整保存調色/);
    unchanged(session, before); assert.equal(session.inFlight, null); assert.equal(session.dirty, true);
  }
});
test("clearing colours is rejected when the server silently retains its previous intervals", async () => {
  const base = graded(), session = new ProjectSession(base); session.change(value => { value.color_adjustments = []; });
  const before = immutable(session);
  await assert.rejects(session.save(async snapshot => ({ ...saved(snapshot), color_adjustments: clone(base.color_adjustments) })), /未完整保存調色/);
  unchanged(session, before); assert.deepEqual(color.adjustments(session.project), []);
});
test("colour stacking order is part of the saved project contract", async () => {
  const p = graded(); p.color_adjustments.push({ ...grade("second", 1, 4), saturation: -50 });
  const session = new ProjectSession(p); session.change(value => { value.color_adjustments[0].brightness = 10; });
  const before = immutable(session);
  await assert.rejects(session.save(async snapshot => { const response = saved(snapshot); response.color_adjustments.reverse(); return response; }), /未完整保存調色/);
  unchanged(session, before);
});
test("visual grades neither extend movie duration nor invalidate an existing speech-caption result", () => {
  const p = project(), before = captions.audioSignature(p), captionJob = { id: "job", project_id: p.id, status: "completed", source_audio_snapshot: clone(p) };
  p.color_adjustments = [{ ...grade("tail", 7, 600), brightness: 40 }];
  assert.equal(editor.totalDuration(p), 8); assert.equal(captions.audioSignature(p), before); assert.equal(captions.resultMatches(captionJob, p), true);
  p.color_adjustments[0].enabled = false; p.color_adjustments[0].strength = 0;
  assert.equal(captions.audioSignature(p), before);
  p.clips[0].volume = .5; assert.equal(captions.resultMatches(captionJob, p), false);
});
test("invalid colours cannot partially mutate the project, its history or an existing draft", () => {
  const invalid = [null, {}, [null], [{ ...grade(), start: -1 }], [{ ...grade(), end: 0 }], [{ ...grade(), exposure: 2.1 }],
    [{ ...grade(), contrast: "20" }], [{ ...grade(), strength: NaN }], [{ ...grade(), enabled: 1 }], [{ ...grade(), extra: 1 }],
    [{ ...grade(), id: "main" }], Array.from({ length: 21 }, (_, i) => grade(`over-limit-${i}`, i, i + 1))];
  for (const value of invalid) {
    const session = new ProjectSession(graded()), before = immutable(session);
    assert.throws(() => session.change(next => { next.color_adjustments = value; })); unchanged(session, before);
    const draft = clone(session.project); draft.color_adjustments = value;
    const resolved = resolveDraft(session.project, record(session.project, draft));
    assert.equal(resolved.status, "invalid"); assert.deepEqual(resolved.project, session.project);
  }
});
test("adjacent grades obey half-open times and a fifth overlapping grade is rejected atomically", () => {
  const session = new ProjectSession(project());
  session.change(value => { value.color_adjustments = [...Array.from({ length: 4 }, (_, i) => grade(`before-${i}`, 0, 2)), ...Array.from({ length: 4 }, (_, i) => grade(`after-${i}`, 2, 4))]; });
  assert.equal(color.activeAdjustments(session.project, 2).length, 4);
  const before = immutable(session);
  assert.throws(() => session.change(value => { value.color_adjustments.push(grade("fifth", 1, 3)); }), /重疊 4/);
  unchanged(session, before);
});
test("real ProjectSession colour gesture keeps previews separate and adds one undo item after commit", () => {
  const session = new ProjectSession(graded()), before = immutable(session), transaction = new colorUI.AdjustmentTransaction(session, "grade", "right", { maxEnd: 10 });
  transaction.update(1); transaction.update(2); unchanged(session, before);
  assert.equal(transaction.commit(), true); assert.equal(session.undoStack.length, 1); assert.equal(session.project.color_adjustments[0].end, 10);
  session.travel(true); assert.deepEqual(session.project.color_adjustments, before.value.color_adjustments);
  session.travel(false); assert.equal(session.project.color_adjustments[0].end, 10);
});
test("existing trimming, speed, layer movement and subtitle style helpers retain colour metadata", () => {
  const p = graded(), media = new Map([["video", { kind: "video", duration: 8, has_audio: true }]]);
  const trimmed = editor.trimTimelineClip(p, { kind: "video", id: "main", edge: "right", delta: -1 }, media).project;
  const speeded = editor.transformClipSpeed(p, "video", "main", { speed: 2 }, media).project;
  const moved = trackDrag.transformTrackDrag(p, { kind: "video", id: "main" }, { kind: "overlay", start: 0, index: 0 }, media).project;
  for (const transformed of [trimmed, speeded, moved]) assert.deepEqual(transformed.color_adjustments, p.color_adjustments);
  const text = { id: "cue", kind: "text", track_id: "subtitle_test", start: 0, end: 3, text: "字幕", ...subtitles.DEFAULT_STYLE };
  const withText = { ...clone(p), overlays: [text] }, styled = subtitles.applySubtitleStyle(withText, text.track_id, subtitles.TEXT_PRESETS.yellow);
  assert.deepEqual(styled.color_adjustments, p.color_adjustments); assert.deepEqual(p, graded());
});

const browserSource = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
function shippedFunction(name) {
  const match = new RegExp(`^  (?:async )?function ${name}\\(`, "m").exec(browserSource); assert.ok(match, name);
  for (let end = browserSource.indexOf("\n", match.index); end !== -1; end = browserSource.indexOf("\n", end + 1)) {
    const candidate = browserSource.slice(match.index, end);
    try { new vm.Script(candidate); return candidate; } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Cannot extract ${name}`);
}
function copyHarness(p, ready = true, respond = response => response) {
  const state = { session: new ProjectSession(p), projects: [clone(p)], busy: false, colorReady: ready, textStyleReady: true, textFontsReady: true,
    textIdleReady: true, clipAnimationsReady: true, clipTransitionsReady: true, transitionEffectsReady: true, referenceTransitionsReady: true };
  const writes = [], events = [], context = vm.createContext({ state, clone, editableContent, resolveDraft, colorMath: color,
    requireColorSupport: editor.requireColorSupport, requireSavedColor: editor.requireSavedColor, requireTextStyleSupport: editor.requireTextStyleSupport,
    requireTextFontSupport: editor.requireTextFontSupport, requireTextIdleSupport: editor.requireTextIdleSupport,
    requireAnimationSupport: editor.requireAnimationSupport, requireTransitionEffectSupport: editor.requireTransitionEffectSupport,
    canSwitch: () => true, locked: () => false, project: () => state.session.project, renderDisabled() {}, render() {},
    saveProject: async () => events.push("save"), rememberDraft: () => events.push("draft"),
    json: (method, value) => ({ method, body: JSON.stringify(value) }),
    api: async (url, options) => { assert.equal(url, "/api/editor/projects"); assert.equal(options.method, "POST"); const payload = JSON.parse(options.body); writes.push(payload); return { project: respond({ ...payload, id: "copy-project", updated_at: "new-revision", clips: payload.clips || [], audio_clips: payload.audio_clips || [], overlays: payload.overlays || [], width: payload.width || 1280, height: payload.height || 720, fps: payload.fps || 24 }) }; },
    installProject: p => { state.session = new ProjectSession(p); events.push("install"); }, storagePut() {}, notify() {},
  });
  vm.runInContext(shippedFunction("newProject") + "\n" + shippedFunction("saveRecoveryCopy"), context);
  return { state, writes, events, context };
}
test("browser copy and recovery-copy retain grades as independent editable content", async () => {
  const p = graded(), before = clone(p), harness = copyHarness(p);
  await harness.context.newProject(true);
  assert.equal(harness.writes.length, 1); assert.deepEqual(harness.writes[0].color_adjustments, color.canonicalAdjustments(p));
  assert.equal(harness.state.session.project.id, "copy-project"); assert.deepEqual(p, before);
  const recovery = copyHarness(p), draft = clone(p); draft.color_adjustments[0].saturation = 35;
  await recovery.context.saveRecoveryCopy({ record: record(p, draft) });
  assert.equal(recovery.writes.length, 1); assert.equal(recovery.state.session.project.color_adjustments[0].saturation, 35);
  assert.deepEqual(recovery.writes[0].clips, p.clips); assert.deepEqual(p, before);
});
test("browser copy and draft recovery do not send colour settings to a backend without colour support", async () => {
  const p = graded(), f = copyHarness(p, false), before = immutable(f.state.session);
  await assert.rejects(f.context.newProject(true), /調色草稿仍保留/);
  assert.equal(f.writes.length, 0); unchanged(f.state.session, before); assert.equal(f.state.busy, false);
  const recovery = copyHarness(p, false), draft = clone(p); draft.color_adjustments[0].tint = 25;
  const recoveryBefore = immutable(recovery.state.session);
  await assert.rejects(recovery.context.saveRecoveryCopy({ record: record(p, draft) }), /調色草稿仍保留/);
  assert.equal(recovery.writes.length, 0); unchanged(recovery.state.session, recoveryBefore); assert.equal(recovery.state.busy, false);
});
test("copy and rescue preserve the original session if a purportedly capable server drops colours", async () => {
  const dropColors = response => { delete response.color_adjustments; return response; }, p = graded();
  const copy = copyHarness(p, true, dropColors), before = immutable(copy.state.session);
  await assert.rejects(copy.context.newProject(true), /未完整保存調色/);
  unchanged(copy.state.session, before); assert.equal(copy.events.includes("install"), false); assert.equal(copy.state.projects.length, 1);
  const rescue = copyHarness(p, true, dropColors), rescueBefore = immutable(rescue.state.session), draft = clone(p); draft.color_adjustments[0].saturation = 40;
  await assert.rejects(rescue.context.saveRecoveryCopy({ record: record(p, draft) }), /未完整保存調色/);
  unchanged(rescue.state.session, rescueBefore); assert.equal(rescue.events.includes("install"), false); assert.equal(rescue.state.projects.length, 1);
});
