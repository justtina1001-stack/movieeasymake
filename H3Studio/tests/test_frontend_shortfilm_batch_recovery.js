"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { create, PREFIX } = require("../static/shortfilm_persistence.js");
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const id = "a".repeat(32);
function project(revision = 1, title = "主機版本") { return { id, revision, title, scenes: [] }; }

function fixture(options = {}) {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const server = options.server || project();
  if (options.draft) storage.setItem(PREFIX + id, JSON.stringify({ version: 1, baseRevision: options.draft.revision, project: options.draft }));
  const sent = [], renders = [], batches = [];
  const context = vm.createContext({
    shortFilmProjects: [], activeShortFilmId: id, shortFilmBatchLoading: false, shortFilmBatchRunning: false,
    shortFilmRecoveries: new Map(),
    activeShortFilmProject() { return context.shortFilmProjects.find(item => item.id === context.activeShortFilmId); },
    api: async url => url.startsWith("/api/shortfilm-batches") ? [{ id: "batch", status: "running" }] : { project: copy(server) },
    setShortFilmBatchEditing() {},
    renderShortFilmScenes() { renders.push("scenes"); }, renderShortFilmSummary() { renders.push("summary"); },
    renderShortFilmBatch(batch) { batches.push(batch); context.shortFilmBatchRunning = ["queued", "running"].includes(batch.status); },
    $: () => ({ textContent: "" }),
  });
  const coordinator = create({ storage,
    getProject: requested => context.shortFilmProjects.find(item => item.id === requested),
    send: async snapshot => {
      sent.push(copy(snapshot));
      return options.send ? options.send(snapshot) : { ...snapshot, revision: snapshot.revision + 1 };
    },
    setTimeout: () => ({}), clearTimeout() {},
  });
  context.getShortFilmSaveCoordinator = () => coordinator;
  const recovery = coordinator.restore(copy(server));
  context.shortFilmProjects = [recovery.project];
  if (recovery.conflict) context.shortFilmRecoveries.set(id, recovery.draft);
  const start = source.indexOf("async function loadShortFilmBatchStatus(");
  assert.notEqual(start, -1, "the actual application poll handler exists");
  vm.runInContext(source.slice(start, source.indexOf("\n}", start) + 2), context);
  return { context, coordinator, storage, sent, renders, batches, server, current: () => context.shortFilmProjects[0] };
}

test("batch polling preserves a conflicting recovered draft and later typing retains its original words", async () => {
  const f = fixture({ server: project(4, "其他視窗正在製作"), draft: project(1, "尚未儲存的重要原文") });
  const original = f.current();
  assert.equal(f.coordinator.status(id).state, "conflict");
  await f.context.loadShortFilmBatchStatus();
  assert.strictEqual(f.current(), original);
  assert.equal(f.current().title, "尚未儲存的重要原文");
  assert.equal(f.batches.length, 1, "batch progress still updates while the edit draft is protected");
  f.context.shortFilmBatchRunning = false;
  f.current().title += "，繼續編輯";
  f.coordinator.schedule(f.current());
  assert.equal(f.coordinator.draft(id).title, "尚未儲存的重要原文，繼續編輯");
  assert.equal(JSON.parse(f.storage.getItem(PREFIX + id)).project.title, f.current().title);
  assert.equal(f.sent.length, 0);
});

test("batch polling leaves a dirty project visible and its pending draft intact", async () => {
  const f = fixture();
  f.current().title = "尚未送出的內容";
  f.coordinator.schedule(f.current());
  const original = f.current();
  await f.context.loadShortFilmBatchStatus();
  assert.strictEqual(f.current(), original);
  assert.equal(f.current().title, "尚未送出的內容");
  assert.equal(f.coordinator.status(id).state, "dirty");
  assert.equal(JSON.parse(f.storage.getItem(PREFIX + id)).project.title, "尚未送出的內容");
});

test("a save in flight and newer input cannot be replaced by an older batch poll response", async () => {
  let resolveSave;
  const f = fixture({ send: snapshot => f.sent.length === 1
    ? new Promise(resolve => { resolveSave = resolve; }) : { ...snapshot, revision: snapshot.revision + 1 } });
  f.current().title = "正在儲存的版本";
  const saving = f.coordinator.save(f.current());
  await tick();
  f.current().title = "儲存期間繼續輸入的完整版本";
  f.coordinator.schedule(f.current());
  const original = f.current();
  await f.context.loadShortFilmBatchStatus();
  assert.strictEqual(f.current(), original);
  assert.equal(f.current().title, "儲存期間繼續輸入的完整版本");
  assert.equal(f.coordinator.status(id).state, "saving");
  resolveSave({ ...f.sent[0], revision: 2 });
  await saving;
  assert.equal(f.sent[1].title, "儲存期間繼續輸入的完整版本");
  assert.equal(f.current().title, f.sent[1].title);
  assert.equal(f.coordinator.status(id).state, "saved");
});

test("a clean project accepts batch runtime and its newer revision on the next save", async () => {
  const f = fixture();
  const previous = f.current();
  f.server.revision = 7;
  f.server.title = "依序製作的主機版本";
  await f.context.loadShortFilmBatchStatus();
  assert.notStrictEqual(f.current(), previous);
  assert.equal(f.current().revision, 7);
  assert.equal(f.current().title, "依序製作的主機版本");
  f.current().title += "，完成後修訂";
  await f.coordinator.save(f.current());
  assert.equal(f.sent[0].revision, 7);
  assert.equal(f.current().revision, 8);
});

test("a failed save keeps its editable words when a batch status refresh arrives", async () => {
  const f = fixture({ send: async () => { throw new Error("offline"); } });
  f.current().title = "離線時還沒存好的原文";
  await assert.rejects(f.coordinator.save(f.current()), /offline/);
  const original = f.current();
  await f.context.loadShortFilmBatchStatus();
  assert.strictEqual(f.current(), original);
  assert.equal(f.current().title, "離線時還沒存好的原文");
  assert.equal(f.coordinator.status(id).state, "error");
  assert.equal(f.coordinator.draft(id).title, f.current().title);
});
