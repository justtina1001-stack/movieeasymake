"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const project = (id = "project-a") => ({ id, name: "匯出流程", width: 1280, height: 720, fps: 24,
  updated_at: "revision-1", clips: [{ id: "main", media_id: "video", in: 0, out: 3, speed: 1, volume: 1 }],
  audio_clips: [], overlays: [] });
const job = (status = "running", changes = {}) => ({ id: "export-a", project_id: "project-a",
  status, progress: status === "completed" ? 100 : 25, ...changes });

// Compile the real editor functions and bindings so the test exercises the
// shipped guards, persistence ordering, and async response handling.
function shippedStatement(start, label) {
  assert.ok(start >= 0, `${label} must exist in the shipped editor`);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Could not extract ${label}`);
}
function shippedFunction(name) {
  const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
  return shippedStatement(match?.index ?? -1, name);
}
function shippedBinding(id) {
  return shippedStatement(source.indexOf(`$("${id}").onclick =`), `${id} click binding`);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(options = {}) {
  const state = { session: new core.ProjectSession(options.project || project()), backendReady: true,
    busy: false, exportJob: options.job ? copy(options.job) : null, exportTimer: null,
    trimDrag: null, overlayDrag: null, speedDrag: null, trackDrag: null, layoutDragging: false };
  const nodes = new Map(), calls = [], messages = [], requests = [], downloads = [], persisted = new Map(), timers = new Map();
  let serial = 0;
  function node(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      const item = { id, textContent: "", value: 0, href: "", open: false, disabled: false, isAnchor: id === "downloadExport",
        checked: id === "autoDownloadExport", hidden: false, style: {},
        classList: { add: name => classes.add(name), remove: name => classes.delete(name),
          contains: name => classes.has(name), toggle(name, force) {
            const on = force === undefined ? !classes.has(name) : Boolean(force);
            if (on) classes.add(name); else classes.delete(name); return on;
          } },
        focus() { calls.push(`focus:${id}`); }, setAttribute() {},
        remove() { calls.push(`remove:${id}`); this.parentNode = null; },
        showModal() { this.open = true; calls.push(`show:${id}`); },
        close() { this.open = false; calls.push(`close:${id}`); this.onclose?.(); },
        click() {
          if (this.disabled) return;
          const event = { target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
          calls.push(`click:${id}`); this.onclick?.(event);
          if (this.isAnchor && !event.defaultPrevented) {
            downloads.push({ href: this.href, saved: copy(persisted.get(`h3-editor-export:${state.session.project.id}`) || {}) });
          }
        } };
      nodes.set(id, item);
    }
    return nodes.get(id);
  }
  const document = { createElement(tag) {
    const item = node(`created-${tag}-${++serial}`); item.isAnchor = tag === "a"; return item;
  }, body: { append(item) { calls.push(`append:${item.id}`); item.parentNode = this; }, appendChild(item) { this.append(item); } } };
  const sandbox = { state, ...core, $: node, project: () => state.session.project,
    document,
    exporting: () => ["queued", "running"].includes(state.exportJob?.status),
    locked: () => !state.backendReady || state.busy || ["queued", "running"].includes(state.exportJob?.status),
    canSwitch: () => !state.busy && !["queued", "running"].includes(state.exportJob?.status),
    clamp: (value, min, max) => Math.max(min, Math.min(max, value)),
    renderDisabled() { calls.push("render-disabled"); },
    pause() { calls.push("pause"); }, notify(message) { messages.push(message); },
    storagePut(key, value) { calls.push(`store:${key}`); persisted.set(key, copy(value)); return true; },
    async api(path, settings = {}) {
      requests.push({ path, settings }); calls.push(`${settings.method || "GET"}:${path}`);
      if (options.api) return options.api(path, settings);
      if (settings.method === "POST" && path.endsWith("/cancel")) return job("cancelled");
      if (settings.method === "POST") return job("queued");
      return options.response ? copy(options.response) : job("running");
    },
    async saveProject() { calls.push("save"); if (options.save) return options.save(); return copy(state.session.project); },
    json: (method, data = {}) => ({ method, body: JSON.stringify(data) }),
    action: fn => fn,
    setTimeout(callback, delay) { const id = ++serial; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); } };
  vm.createContext(sandbox);
  for (const name of ["renderExport", "openExportDialog", "closeExportDialog", "downloadExport", "pollExport", "startExport"])
    vm.runInContext(shippedFunction(name), sandbox);
  for (const id of ["exportProject", "startExport", "closeExportDialog", "downloadExport", "cancelExport"])
    vm.runInContext(shippedBinding(id), sandbox);
  return { state, calls, messages, requests, downloads, persisted, timers, node, sandbox,
    async settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); },
    async click(id) { node(id).click(); for (let i = 0; i < 8; i++) await Promise.resolve(); },
    async poll() { await sandbox.pollExport(); },
    async open() { await sandbox.openExportDialog(); for (let i = 0; i < 8; i++) await Promise.resolve(); } };
}

test("closing and reopening an active export keeps its job and background polling", async () => {
  const h = harness({ job: job("running") }), before = core.signature(h.state.session.project);
  await h.open(); assert.equal(h.node("exportDialog").open, true);
  assert.equal(h.timers.size, 1);
  const timer = h.state.exportTimer;
  h.sandbox.closeExportDialog();
  assert.equal(h.node("exportDialog").open, false);
  assert.equal(h.state.exportJob.id, "export-a"); assert.equal(h.state.exportTimer, timer);
  assert.equal(h.timers.size, 1);
  await h.open(); assert.equal(h.node("exportDialog").open, true);
  assert.equal(h.timers.size, 1);
  assert.equal(h.requests.some(request => request.settings.method === "POST"), false);
  assert.equal(core.signature(h.state.session.project), before);
  assert.equal(h.state.session.undoStack.length, 0);
});

test("automatic completion requests exactly one download, persisting that fact before the anchor click", async () => {
  const h = harness({ job: job("running", { auto_download: true, download_requested: false }), response: job("completed") });
  h.node("exportDialog").showModal();
  await h.poll();
  assert.equal(h.downloads.length, 1);
  assert.match(h.downloads[0].href, /\/api\/editor\/exports\/export-a\/file\?download=1$/);
  assert.equal(h.downloads[0].saved.download_requested, true);
  assert.equal(h.state.exportJob.auto_download, true);
  assert.equal(h.state.exportJob.download_requested, true);
  assert.equal(h.node("exportDialog").open, false);
  await h.poll(); await h.open();
  assert.equal(h.downloads.length, 1);
  assert.equal(h.timers.size, 0);
  assert.match(h.messages.join(" "), /下載/);
});

test("reload preserves the once-only automatic download marker", async () => {
  const first = harness({ job: job("running", { auto_download: true, download_requested: false }), response: job("completed") });
  await first.poll();
  const saved = first.persisted.get("h3-editor-export:project-a");
  assert.equal(saved.download_requested, true);
  const reload = harness({ job: saved, response: job("completed") });
  await reload.open(); await reload.poll();
  assert.equal(reload.downloads.length, 0);
  assert.equal(reload.node("exportDialog").open, true);
});

test("a completed export saved by an older version never automatically downloads on reopen", async () => {
  const h = harness({ job: job("completed"), response: job("completed") });
  await h.open(); await h.poll();
  assert.equal(h.downloads.length, 0);
  assert.equal(h.node("downloadExport").classList.contains("hidden"), false);
  assert.equal(h.node("exportDialog").open, true);
});

test("manual download closes the dialog but remains available to download again", async () => {
  const h = harness({ job: job("completed", { auto_download: false }), response: job("completed") });
  await h.open(); await h.click("downloadExport");
  assert.equal(h.downloads.length, 1);
  assert.equal(h.downloads[0].saved.download_requested, true);
  assert.equal(h.state.exportJob.id, "export-a");
  assert.equal(h.node("exportDialog").open, false);
  await h.open(); await h.click("downloadExport");
  assert.equal(h.downloads.length, 2);
  assert.equal(h.requests.some(request => request.settings.method === "POST"), false);
});

test("closing the export dialog never cancels the render", async () => {
  const h = harness({ job: job("running") });
  await h.open(); await h.click("closeExportDialog");
  assert.equal(h.node("exportDialog").open, false);
  assert.equal(h.state.exportJob.status, "running");
  assert.equal(h.requests.some(request => request.path.endsWith("/cancel")), false);
  assert.equal(h.timers.size, 1);
});

test("a late poll response cannot overwrite a newer export or download its file", async () => {
  const response = deferred(), h = harness({ job: job("running", { auto_download: true }), api: () => response.promise });
  const pending = h.sandbox.pollExport();
  h.state.exportJob = job("running", { id: "export-b", auto_download: false });
  response.resolve(job("completed")); await pending;
  assert.equal(h.state.exportJob.id, "export-b"); assert.equal(h.state.exportJob.status, "running");
  assert.equal(h.downloads.length, 0); assert.equal(h.persisted.size, 0); assert.equal(h.timers.size, 0);
});

test("a late poll response after switching projects cannot persist under the new project", async () => {
  const response = deferred(), h = harness({ job: job("running", { auto_download: true }), api: () => response.promise });
  const pending = h.sandbox.pollExport();
  h.state.session = new core.ProjectSession(project("project-b")); h.state.exportJob = null;
  response.resolve(job("completed")); await pending;
  assert.equal(h.state.exportJob, null); assert.equal(h.persisted.size, 0);
  assert.equal(h.downloads.length, 0); assert.equal(h.timers.size, 0);
});

test("project identity also guards a late response when the export ID is unchanged", async () => {
  const response = deferred(), h = harness({ job: job("running", { auto_download: true }), api: () => response.promise });
  const pending = h.sandbox.pollExport();
  h.state.session = new core.ProjectSession(project("project-b"));
  response.resolve(job("completed")); await pending;
  assert.equal(h.state.exportJob.status, "running");
  assert.equal(h.persisted.size, 0); assert.equal(h.downloads.length, 0); assert.equal(h.timers.size, 0);
});

test("an obsolete poll failure does not schedule retries against the new project", async () => {
  const response = deferred(), h = harness({ job: job("running"), api: () => response.promise });
  const pending = h.sandbox.pollExport();
  h.state.session = new core.ProjectSession(project("project-b")); h.state.exportJob = null;
  response.reject(new Error("lost connection")); await pending;
  assert.equal(h.timers.size, 0); assert.equal(h.persisted.size, 0);
});

for (const autoDownload of [true, false]) test(`new export captures the chosen automatic download option (${autoDownload}) after saving`, async () => {
  const h = harness({ api: (path, settings) => settings.method === "POST" ? job("queued") : job("running") });
  h.node("autoDownloadExport").checked = autoDownload;
  await h.sandbox.startExport(); await h.settle();
  assert.equal(h.state.exportJob.auto_download, autoDownload);
  assert.equal(h.state.exportJob.download_requested, false);
  assert.equal(h.persisted.get("h3-editor-export:project-a").auto_download, autoDownload);
  assert.ok(h.calls.indexOf("save") < h.calls.indexOf("POST:/api/editor/projects/project-a/exports"));
  assert.equal(h.requests.filter(request => request.settings.method === "POST").length, 1);
  assert.equal(h.state.busy, false); assert.equal(h.downloads.length, 0);
});

test("opening a completed export offers its download without starting another render", async () => {
  const h = harness({ job: job("completed"), response: job("completed") });
  await h.click("exportProject");
  assert.equal(h.node("exportDialog").open, true);
  assert.equal(h.requests.filter(request => request.settings.method === "POST").length, 0);
  assert.equal(h.calls.includes("save"), false);
});

test("rechecking an unavailable previous export leaves a visible failure and permits another export", async () => {
  const missing = new Error("export no longer exists"); missing.status = 404;
  const h = harness({ job: job("completed"), api: () => Promise.reject(missing) });
  await h.open();
  assert.equal(h.state.exportJob.status, "failed");
  assert.ok(h.state.exportJob.error);
  assert.equal(h.node("downloadExport").classList.contains("hidden"), true);
  assert.equal(h.timers.size, 0);
  assert.equal(h.downloads.length, 0);
});

test("a temporary polling error retains the export and continues reconnecting while the dialog is closed", async () => {
  const h = harness({ job: job("running", { auto_download: true, download_requested: false }),
    api: () => Promise.reject(new Error("temporary disconnection")) });
  await h.poll();
  assert.equal(h.node("exportDialog").open, false);
  assert.equal(h.state.exportJob.status, "running");
  assert.equal(h.state.exportJob.auto_download, true);
  assert.equal(h.timers.size, 1);
  assert.equal(h.downloads.length, 0);
});

test("failed or cancelled exports never trigger automatic downloads", async () => {
  for (const status of ["failed", "cancelled"]) {
    const h = harness({ job: job("running", { auto_download: true, download_requested: false }), response: job(status) });
    await h.poll();
    assert.equal(h.state.exportJob.status, status);
    assert.equal(h.downloads.length, 0);
    assert.equal(h.node("downloadExport").classList.contains("hidden"), true);
    assert.equal(h.timers.size, 0);
  }
});

test("only the explicit cancel action cancels an active export", async () => {
  let current = job("running");
  const h = harness({ job: current, api: (path, settings) => {
    if (settings.method === "POST" && path.endsWith("/cancel")) current = job("cancelled");
    return copy(current);
  } });
  await h.open(); await h.click("cancelExport");
  assert.equal(h.requests.filter(request => request.path.endsWith("/cancel")).length, 1);
  assert.equal(h.state.exportJob.status, "cancelled");
  assert.equal(h.downloads.length, 0);
});

test("saving failure does not begin an export or replace the previous download", async () => {
  const h = harness({ job: job("completed"), save: () => Promise.reject(new Error("save failed")) });
  await assert.rejects(h.sandbox.startExport(), /save failed/);
  assert.equal(h.requests.length, 0);
  assert.equal(h.state.exportJob.id, "export-a");
  assert.equal(h.state.exportJob.status, "completed");
  assert.equal(h.state.busy, false);
  assert.equal(h.downloads.length, 0);
});

test("the download preference is captured before an asynchronous save finishes", async () => {
  const saved = deferred(), h = harness({ save: () => saved.promise });
  h.node("autoDownloadExport").checked = false;
  const pending = h.sandbox.startExport();
  h.node("autoDownloadExport").checked = true;
  saved.resolve(project()); await pending; await h.settle();
  assert.equal(h.state.exportJob.auto_download, false);
  assert.equal(h.persisted.get("h3-editor-export:project-a").auto_download, false);
});

test("an export accepted after a session change is saved for its original project only", async () => {
  const accepted = deferred(), h = harness({ api: () => accepted.promise });
  const pending = h.sandbox.startExport(); await h.settle();
  assert.equal(h.requests.length, 1);
  h.state.session = new core.ProjectSession(project("project-b")); h.state.exportJob = null;
  accepted.resolve(job("queued")); await pending; await h.settle();
  assert.equal(h.state.exportJob, null);
  assert.equal(h.persisted.get("h3-editor-export:project-a").id, "export-a");
  assert.equal(h.persisted.has("h3-editor-export:project-b"), false);
  assert.equal(h.requests.length, 1); assert.equal(h.timers.size, 0); assert.equal(h.downloads.length, 0);
});
