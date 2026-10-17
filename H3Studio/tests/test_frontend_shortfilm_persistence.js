"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { create, PREFIX } = require("../static/shortfilm_persistence.js");
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
function harness(projects, send, overrides = {}) {
  const timers = [];
  const events = [];
  const storage = overrides.storage || memoryStorage();
  const manager = create({ storage, send,
    getProject: id => projects.find(project => project.id === id),
    onSaved: (saved, info) => { events.push(info); if (!info.hasPending && info.current) Object.assign(info.current, saved); },
    setTimeout: fn => { const timer = { fn, cancelled: false }; timers.push(timer); return timer; },
    clearTimeout: timer => { if (timer) timer.cancelled = true; },
    ...overrides,
  });
  return { manager, storage, events, fire: () => timers.splice(0).forEach(timer => { if (!timer.cancelled) timer.fn(); }) };
}
function deferredRequests() {
  const calls = [];
  return { calls, send: project => new Promise((resolve, reject) => calls.push({ project: copy(project), resolve, reject })) };
}
function project(id = "a", revision = 1) { return { id, revision, title: "原名", scenes: [] }; }

test("edits made during save are serialized and the first response cannot erase them", async () => {
  const current = project();
  const reference = current;
  const requests = deferredRequests();
  const { manager, storage, events } = harness([current], requests.send);
  const saving = manager.save(current);
  await tick();
  assert.equal(requests.calls.length, 1);
  current.title = "保存中繼續輸入";
  manager.schedule(current);
  requests.calls[0].resolve({ ...requests.calls[0].project, title: "伺服器舊內容", revision: 2 });
  await tick();
  assert.equal(current.title, "保存中繼續輸入");
  assert.equal(events[0].hasPending, true);
  assert.equal(requests.calls.length, 2);
  assert.equal(requests.calls[1].project.title, current.title);
  assert.equal(requests.calls[1].project.revision, 2);
  requests.calls[1].resolve({ ...requests.calls[1].project, revision: 3 });
  await saving;
  assert.strictEqual(current, reference);
  assert.equal(current.revision, 3);
  assert.equal(manager.status(current.id).state, "saved");
  assert.equal(storage.getItem(PREFIX + current.id), null);
});

test("switching projects after typing keeps each delayed save attached to its captured project", async () => {
  const first = project("first"), second = project("second");
  const requests = [];
  const { manager, fire } = harness([first, second], async snapshot => {
    requests.push(copy(snapshot)); return { ...snapshot, revision: snapshot.revision + 1 };
  });
  first.title = "第一份已輸入";
  manager.schedule(first);
  second.title = "第二份已輸入";
  manager.schedule(second);
  fire();
  await manager.flushAll();
  assert.deepEqual(requests.map(value => [value.id, value.title]), [["first", first.title], ["second", second.title]]);
  assert.equal(first.revision, 2);
  assert.equal(second.revision, 2);
});

test("explicit saves while a request is pending share its queue and flush the latest snapshot", async () => {
  const current = project();
  const requests = deferredRequests();
  const { manager } = harness([current], requests.send);
  const first = manager.save(current);
  await tick();
  current.title = "第二次手動保存";
  const second = manager.save(current);
  assert.strictEqual(first, second);
  assert.equal(requests.calls.length, 1);
  requests.calls[0].resolve({ ...requests.calls[0].project, revision: 2 });
  await tick();
  requests.calls[1].resolve({ ...requests.calls[1].project, revision: 3 });
  await second;
  assert.equal(current.title, "第二次手動保存");
});

test("unscheduled edits during a request are captured before merging its response", async () => {
  const current = project();
  const requests = deferredRequests();
  const { manager } = harness([current], requests.send);
  const saved = manager.save(current);
  await tick();
  current.title = "尚未排程的輸入";
  requests.calls[0].resolve({ ...requests.calls[0].project, revision: 2 });
  await tick();
  assert.equal(requests.calls[1].project.title, "尚未排程的輸入");
  requests.calls[1].resolve({ ...requests.calls[1].project, revision: 3 });
  await saved;
});

test("leaving before the debounce stores a draft immediately and reopening restores every word", async () => {
  const original = project();
  const { manager, storage } = harness([original], async value => ({ ...value, revision: 2 }));
  original.title = "離頁仍保留的文字";
  manager.schedule(original);
  const draft = JSON.parse(storage.getItem(PREFIX + original.id));
  assert.equal(draft.project.title, original.title);
  assert.equal(draft.baseRevision, 1);
  const server = project();
  const restoredHarness = harness([server], async value => ({ ...value, revision: 2 }), { storage });
  const result = restoredHarness.manager.restore(server);
  assert.equal(result.restored, true);
  assert.equal(result.conflict, false);
  assert.equal(result.project.title, original.title);
  Object.assign(server, result.project);
  await restoredHarness.manager.save(server);
  assert.equal(storage.getItem(PREFIX + server.id), null);
});

test("a newer server revision preserves the draft and requires explicit rebase before sending", async () => {
  const original = project();
  const { manager, storage } = harness([original], async value => value);
  original.title = "瀏覽器草稿";
  manager.schedule(original);
  const server = { ...project("a", 4), title: "其他視窗已存" };
  let sends = 0;
  const reopened = harness([server], async value => { sends++; return { ...value, revision: 5 }; }, { storage });
  const result = reopened.manager.restore(server);
  assert.equal(result.conflict, true);
  assert.equal(result.project.title, "瀏覽器草稿");
  assert.equal(result.serverProject.title, "其他視窗已存");
  assert.equal(result.draft.title, "瀏覽器草稿");
  assert.equal(reopened.manager.draft(server.id).title, "瀏覽器草稿");
  await reopened.manager.flushAll();
  assert.equal(sends, 0);
  assert.equal(JSON.parse(storage.getItem(PREFIX + server.id)).project.title, "瀏覽器草稿");
  Object.assign(server, result.draft);
  reopened.manager.rebase(server, 4);
  await reopened.manager.save(server);
  assert.equal(sends, 1);
  assert.equal(server.title, "瀏覽器草稿");
  assert.equal(server.revision, 5);
});

test("HTTP conflict stops automatic retries while preserving later input for manual resolution", async () => {
  const current = project();
  let requests = 0;
  const failure = Object.assign(new Error("version conflict"), { status: 409 });
  const errors = [];
  const { manager, storage, fire } = harness([current], async () => { requests++; throw failure; }, { onError: error => errors.push(error) });
  await assert.rejects(manager.save(current), error => error === failure);
  current.title = "衝突後仍輸入";
  manager.schedule(current);
  fire();
  await manager.flushAll();
  assert.equal(requests, 1);
  assert.equal(manager.status(current.id).state, "conflict");
  assert.equal(JSON.parse(storage.getItem(PREFIX + current.id)).project.title, current.title);
  assert.equal(manager.draft(current.id).title, current.title);
  assert.equal(errors.length, 1);
});

test("network failure retains the draft and a manual retry saves the newest content", async () => {
  const current = project();
  let fails = true;
  const { manager, storage } = harness([current], async value => {
    if (fails) throw new Error("offline");
    return { ...value, revision: value.revision + 1 };
  });
  await assert.rejects(manager.save(current), /offline/);
  assert.equal(manager.status(current.id).state, "error");
  assert.ok(storage.getItem(PREFIX + current.id));
  current.title = "重試前再修改";
  fails = false;
  await manager.save(current);
  assert.equal(current.title, "重試前再修改");
  assert.equal(manager.status(current.id).state, "saved");
  assert.equal(manager.draft(current.id), null);
});

test("storage full is visible without preventing the server save", async () => {
  const current = project();
  const storage = { getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: () => {} };
  const { manager } = harness([current], async value => ({ ...value, revision: 2 }), { storage });
  manager.schedule(current);
  assert.match(manager.status(current.id).draftError, /離頁草稿/);
  await manager.save(current);
  assert.equal(current.revision, 2);
});

test("deleting a project clears its scheduled write and retained draft", async () => {
  const current = project();
  let sends = 0;
  const { manager, storage, fire } = harness([current], async value => { sends++; return value; });
  manager.schedule(current);
  manager.forget(current.id);
  fire();
  await manager.flushAll();
  assert.equal(sends, 0);
  assert.equal(storage.getItem(PREFIX + current.id), null);
});

test("a clean queue adopts revisions returned by appending segments or a completed batch", async () => {
  const projects = [project()];
  const sent = [];
  const { manager } = harness(projects, async value => {
    sent.push(copy(value)); return { ...value, revision: value.revision + 1 };
  });
  await manager.save(projects[0]);
  // Another accepted Studio operation returned a newer project object.
  projects[0] = { ...projects[0], revision: 6, scenes: [{ title: "新增場次" }] };
  projects[0].title = "在新場次後編輯";
  await manager.save(projects[0]);
  assert.equal(sent[1].revision, 6);
  assert.equal(projects[0].revision, 7);
});

test("a restored conflicting draft remains visible and export reads the latest edits without overwriting the server", async () => {
  const original = project();
  const local = harness([original], async value => value);
  original.title = "原始未存草稿";
  local.manager.schedule(original);
  const projects = [{ ...project("a", 4), title: "其他視窗版本" }];
  let sends = 0;
  const reopened = harness(projects, async value => { sends++; return value; }, { storage: local.storage });
  const recovery = reopened.manager.restore(projects[0]);
  projects[0] = recovery.project;
  assert.equal(projects[0].title, "原始未存草稿");
  assert.equal(reopened.manager.draft("a").title, "原始未存草稿");
  projects[0].title = "草稿恢復後繼續編輯";
  reopened.manager.schedule(projects[0]);
  reopened.fire();
  await reopened.manager.flushAll();
  const exportDraft = reopened.manager.draft("a");
  assert.equal(exportDraft.title, "草稿恢復後繼續編輯");
  exportDraft.title = "匯出複本不得修改保留草稿";
  assert.equal(reopened.manager.draft("a").title, projects[0].title);
  assert.equal(sends, 0);
  assert.equal(recovery.serverProject.title, "其他視窗版本");
});
