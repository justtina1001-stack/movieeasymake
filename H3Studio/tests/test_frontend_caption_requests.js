"use strict";
/* Exercise shipped request/controller functions with deferred API responses.
   The small DOM and timer adapters avoid a browser, models or real servers. */
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const auto = require("../static/editor_auto_captions.js");
const editor = require("../static/editor.js");
const source = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
const requestFunctions = source.slice(source.indexOf("  function renderAutoCaptionControls()"), source.indexOf("  function applyAutoCaptions()"));
const recheckBinding = source.split(/\r?\n/).find(line => line.includes('$("checkAutoCaptions").onclick ='));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function project(letter = "a") {
  return { id: letter.repeat(32), name: "字幕專案", fps: 24, clips: [{ id: "clip", media_id: "video", in: 0, out: 4, volume: 1, speed: 1 }], audio_clips: [], overlays: [] };
}
function job(value, letter = "b", fields = {}) {
  return { id: letter.repeat(32), project_id: value.id, status: "running", stage: "transcribing", progress: 20, source_audio_snapshot: structuredClone(value), ...fields };
}
function harness(apiHandler) {
  const elements = new Map(), storage = new Map(), timers = new Map(), requests = [], errors = [];
  const p = project();
  const state = { session: { project: p }, backendReady: true, busy: false, captionReady: true, captionChecking: false, captionJob: null,
    captionOtherActive: false, captionError: "", captionTimer: null, captionSerial: 0, textStyleReady: true, overlayTracksReady: true, subtitleCapacity: 5000 };
  let timerId = 0;
  const $ = id => {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, { value: id === "captionLanguage" ? "zh" : "", checked: true, disabled: false, textContent: "", open: false,
        classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }, contains(name) { return classes.has(name); } },
        removeAttribute(name) { delete this[name]; } });
    }
    return elements.get(id);
  };
  const api = async (url, options) => { requests.push({ url, options }); return apiHandler(url, options); };
  const factory = new Function("state", "api", "project", "locked", "$", "autoCaptions", "totalDuration", "overlays", "subtitleCount", "storageGet", "storagePut",
    "setTimeout", "clearTimeout", "saveProject", "json", "action", requestFunctions + '\n' + recheckBinding +
    "\nfunction renderDisabled() { renderAutoCaptionControls(); }\nreturn { renderAutoCaptionControls, rememberCaptionJob, scheduleCaptionPoll, refreshAutoCaptions, pollAutoCaptions, generateAutoCaptions, cancelAutoCaptions }; ");
  const controller = factory(state, api, () => state.session.project, () => state.busy || !state.backendReady, $, auto,
    editor.totalDuration, value => value?.overlays || [], editor.subtitleCount, key => storage.get(key), (key, value) => storage.set(key, value),
    fn => { const id = ++timerId; timers.set(id, fn); return id; }, id => timers.delete(id), async () => {},
    (method, body) => ({ method, body }), fn => async event => { try { return await fn(event); } catch (error) { errors.push(error); } });
  return { state, $, storage, timers, requests, errors, ...controller,
    switchProject(value) { state.session = { project: value }; },
    async tick() { const next = timers.entries().next().value; assert.ok(next, "a caption progress timer is pending"); timers.delete(next[0]); await next[1](); } };
}

test("rechecking during a deferred submit cannot freeze the caption controls", async () => {
  const post = deferred();
  const h = harness(url => { assert.ok(url.endsWith("/captions")); return post.promise; });
  const generating = h.generateAutoCaptions();
  await Promise.resolve();
  assert.equal(h.state.busy, true);
  assert.equal(h.$("checkAutoCaptions").disabled, true);
  assert.equal(h.requests.length, 1);
  // Even a queued click dispatched after disabling the button is guarded.
  await h.$("checkAutoCaptions").onclick();
  assert.equal(h.requests.length, 1);
  post.resolve({ job: job(h.state.session.project) });
  await generating;
  assert.equal(h.state.busy, false);
  assert.equal(h.state.captionChecking, false);
  assert.equal(h.state.captionJob.status, "running");
  assert.equal(h.$("checkAutoCaptions").disabled, false);
  assert.equal(h.$("cancelAutoCaptions").disabled, false);
  assert.equal(h.timers.size, 1);
});

test("submit taking request ownership clears a concurrent background check's busy state", async () => {
  const post = deferred(), status = deferred();
  const h = harness(url => url.endsWith("/status") ? status.promise : post.promise);
  const generating = h.generateAutoCaptions();
  await Promise.resolve();
  const checking = h.refreshAutoCaptions();
  assert.equal(h.state.captionChecking, true);
  const active = job(h.state.session.project);
  post.resolve({ job: active });
  await generating;
  assert.equal(h.state.captionChecking, false);
  status.resolve({ available: false });
  await checking;
  assert.equal(h.state.captionReady, true);
  assert.equal(h.state.captionChecking, false);
  assert.equal(h.state.captionJob.id, active.id);
  assert.equal(h.$("cancelAutoCaptions").disabled, false);
  assert.equal(h.timers.size, 1);
});

test("an old project's late status cannot replace the new project or its progress timer", async () => {
  const oldStatus = deferred(), newStatus = deferred();
  const h = harness(() => h.requests.length === 1 ? oldStatus.promise : newStatus.promise);
  const first = h.refreshAutoCaptions(), original = h.state.session.project;
  const secondProject = project("c"), currentJob = job(secondProject, "d");
  h.switchProject(secondProject);
  const second = h.refreshAutoCaptions();
  newStatus.resolve({ available: true, active_job: currentJob });
  await second;
  const timer = h.state.captionTimer;
  oldStatus.resolve({ available: false, active_job: job(original) });
  await first;
  assert.equal(h.state.captionReady, true);
  assert.equal(h.state.captionChecking, false);
  assert.equal(h.state.captionJob.id, currentJob.id);
  assert.equal(h.state.captionJob.project_id, secondProject.id);
  assert.equal(h.state.captionTimer, timer);
  assert.equal(h.timers.size, 1);
});

test("a late stored-result lookup cannot attach subtitles to a newly opened project", async () => {
  const lookup = deferred();
  const h = harness(url => url.endsWith("/status") ? { available: true } : lookup.promise);
  const original = h.state.session.project, originalJob = job(original, "b", { status: "completed", cues: [{ start: 1, end: 2, text: "旧結果" }] });
  h.storage.set(`h3-editor-caption:${original.id}`, { id: originalJob.id, project_id: original.id });
  const first = h.refreshAutoCaptions();
  await Promise.resolve(); await Promise.resolve();
  assert.ok(h.requests.some(item => item.url.endsWith(originalJob.id)));
  const current = project("c");
  h.switchProject(current);
  await h.refreshAutoCaptions();
  lookup.resolve({ job: originalJob });
  await first;
  assert.equal(h.state.captionJob, null);
  assert.equal(h.state.captionChecking, false);
  assert.equal(h.state.session.project.id, current.id);
  assert.equal(h.$("applyAutoCaptions").classList.contains("hidden"), true);
});

test("an in-flight old progress response is ignored after project refresh", async () => {
  const progress = deferred();
  const current = project("c"), currentJob = job(current, "d");
  const h = harness(url => url.endsWith("/status") ? { available: true, active_job: currentJob } : progress.promise);
  const originalJob = job(h.state.session.project);
  h.rememberCaptionJob(originalJob);
  const polling = h.pollAutoCaptions();
  h.switchProject(current);
  await h.refreshAutoCaptions();
  progress.resolve({ job: { ...originalJob, status: "completed", cues: [{ start: 1, end: 2, text: "過期" }] } });
  await polling;
  assert.equal(h.state.captionJob.id, currentJob.id);
  assert.equal(h.state.captionJob.status, "running");
  assert.equal(h.state.captionError, "");
});

test("temporary progress failure keeps its job and recovers through recheck to completion", async () => {
  let failed = false, completed = false;
  const h = harness(url => {
    if (url.endsWith("/status")) return { available: true, active_job: originalJob };
    if (!failed) { failed = true; return Promise.reject(new Error("網路暫時中斷")); }
    completed = true;
    return { job: { ...originalJob, status: "completed", progress: 100, cues: [{ start: 1, end: 2, text: "恢復完成" }] } };
  });
  const originalJob = job(h.state.session.project);
  h.rememberCaptionJob(originalJob);
  h.scheduleCaptionPoll(h.state.captionSerial);
  await h.tick();
  assert.equal(h.state.captionJob.id, originalJob.id);
  assert.equal(h.state.captionJob.status, "running");
  assert.match(h.state.captionError, /暫時無法讀取/);
  assert.equal(h.timers.size, 1);
  await h.$("checkAutoCaptions").onclick();
  assert.equal(h.state.captionError, "");
  assert.equal(h.state.captionChecking, false);
  assert.equal(h.timers.size, 1);
  await h.tick();
  assert.equal(completed, true);
  assert.equal(h.state.captionJob.status, "completed");
  assert.equal(h.state.captionError, "");
  assert.equal(h.$("applyAutoCaptions").disabled, false);
  assert.equal(h.$("captionProgress").classList.contains("hidden"), true);
  assert.equal(h.timers.size, 0);
});

test("a late cancellation response cannot replace another project's active job", async () => {
  const cancel = deferred();
  const current = project("c"), currentJob = job(current, "d");
  const h = harness(url => url.endsWith("/cancel") ? cancel.promise : { available: true, active_job: currentJob });
  const originalJob = job(h.state.session.project);
  h.rememberCaptionJob(originalJob);
  const cancelling = h.cancelAutoCaptions();
  h.switchProject(current);
  await h.refreshAutoCaptions();
  cancel.resolve({ job: { ...originalJob, cancel_requested: true } });
  await cancelling;
  assert.equal(h.state.captionJob.id, currentJob.id);
  assert.equal(h.state.captionJob.cancel_requested, undefined);
  assert.equal(h.$("cancelAutoCaptions").disabled, false);
  assert.equal(h.timers.size, 1);
});

test("cancel acknowledgement stays visible until polling confirms cancellation", async () => {
  const h = harness(url => ({ job: { ...h.state.captionJob, ...(url.endsWith("/cancel") ? { cancel_requested: true } : { status: "cancelled" }) } }));
  h.rememberCaptionJob(job(h.state.session.project));
  await h.cancelAutoCaptions();
  assert.equal(h.state.captionJob.status, "running");
  assert.equal(h.$("cancelAutoCaptions").disabled, true);
  assert.equal(h.$("cancelAutoCaptions").classList.contains("hidden"), false);
  await h.tick();
  assert.equal(h.state.captionJob.status, "cancelled");
  assert.equal(h.$("cancelAutoCaptions").classList.contains("hidden"), true);
  assert.equal(h.$("generateAutoCaptions").disabled, false);
  assert.equal(h.timers.size, 0);
});
