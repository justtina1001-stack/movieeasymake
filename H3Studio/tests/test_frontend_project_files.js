"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { clone, signature, canonicalSavedSignature, resolveDraft, ProjectSession, ProjectAutoSaver } = require("../static/editor.js");

const project = () => ({ id: "persist-project", name: "續編測試", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [{ id: "v1", media_id: "video", in: 0, out: 4, speed: 1, volume: 1 }],
  audio_clips: [{ id: "a1", media_id: "audio", in: 0.5, out: 2.5, speed: 1, volume: 0.6, start: 1, track: 0, fade_in: 0.1, fade_out: 0.2 }],
});
const media = () => new Map([["video", { kind: "video", duration: 5, has_audio: true }], ["audio", { kind: "audio", duration: 5, has_audio: true }]]);
const record = (base, edited) => ({ project: clone(edited), savedSignature: signature(base), savedAt: 1234 });
const flushTasks = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("canonical signatures ignore server field order and explicitly default missing speed", () => {
  const first = project(), second = clone(first);
  second.clips = [{ volume: 1, out: 4, in: 0, media_id: "video", id: "v1" }];
  second.audio_clips = [{ track: 0, fade_out: 0.2, fade_in: 0.1, start: 1, volume: 0.6, out: 2.5, in: 0.5, media_id: "audio", id: "a1" }];
  second.updated_at = "r99";
  assert.equal(signature(first), signature(second));
  second.audio_clips[0].start = 2; assert.notEqual(signature(first), signature(second));
});

test("legacy stored signatures can be compared after server normalization", () => {
  const value = project(); delete value.clips[0].speed; delete value.audio_clips[0].speed;
  const legacy = JSON.stringify({ name: value.name, clips: value.clips, audio_clips: value.audio_clips, width: value.width, height: value.height, fps: value.fps });
  assert.equal(canonicalSavedSignature(legacy), signature(project()));
  assert.equal(canonicalSavedSignature("broken JSON"), null);
});

test("safe unsaved draft restores editing content with the latest server revision", () => {
  const server = project(), edited = clone(server); edited.clips[0].in = 0.5; edited.audio_clips[0].start = 1.5;
  const draft = record(server, edited), untouched = clone(draft); server.updated_at = "r2";
  const result = resolveDraft(server, draft, media());
  assert.equal(result.status, "recoverable");
  assert.equal(result.project.updated_at, "r2"); assert.equal(result.project.clips[0].in, 0.5); assert.equal(result.project.audio_clips[0].start, 1.5);
  assert.deepEqual(draft, untouched); assert.equal(server.clips[0].in, 0);
});

test("an already-saved equivalent draft does not create a false recovery prompt", () => {
  const server = project(), draft = record(server, server);
  draft.project.clips[0] = { volume: 1, out: 4, in: 0, media_id: "video", id: "v1" };
  assert.equal(resolveDraft(server, draft, media()).status, "equivalent");
  assert.equal(resolveDraft(server, null, media()).status, "none");
});

test("a draft against an older changed server is a conflict and never overwrites either version", () => {
  const base = project(), draftValue = clone(base), server = clone(base);
  draftValue.clips[0].in = 1; server.clips[0].out = 3; server.updated_at = "r2";
  const draft = record(base, draftValue), serverBefore = clone(server), draftBefore = clone(draft);
  assert.equal(resolveDraft(server, draft, media()).status, "conflict");
  assert.deepEqual(server, serverBefore); assert.deepEqual(draft, draftBefore);
});

test("malformed, mismatched or missing-source drafts are classified without throwing", () => {
  const base = project(), bad = [false, "broken", { project: null }, { project: { ...base, clips: null } }, record(base, { ...base, id: "other" })];
  for (const draft of bad) assert.ok(["none", "invalid"].includes(resolveDraft(base, draft, media()).status));
  const changed = clone(base); changed.clips[0].in = 1;
  assert.equal(resolveDraft(base, record(base, changed), new Map()).status, "invalid");
});

function autoFixture(write, canSave = () => true) {
  const timers = new Map(), errors = [], saved = []; let serial = 0;
  const session = new ProjectSession(project());
  const autosaver = new ProjectAutoSaver(session, write, {
    delay: 1000, canSave, onSaved: value => saved.push(value), onError: error => errors.push(error),
    setTimer: fn => { timers.set(++serial, fn); return serial; }, clearTimer: id => timers.delete(id),
  });
  const fire = async () => { const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await flushTasks(); };
  return { session, autosaver, timers, errors, saved, fire };
}

test("autosave debounces several edits into one write of the latest project", async () => {
  const writes = [], f = autoFixture(async p => { writes.push(clone(p)); return { ...p, updated_at: "r2" }; });
  f.session.change(p => { p.name = "第一個字"; }); f.autosaver.schedule();
  f.session.change(p => { p.name = "最後輸入內容"; }); f.autosaver.schedule();
  assert.equal(f.timers.size, 1); assert.equal(writes.length, 0);
  await f.fire(); assert.equal(writes.length, 1); assert.equal(writes[0].name, "最後輸入內容"); assert.equal(f.session.dirty, false);
});

test("autosave waits while editing is temporarily busy instead of dropping unsaved work", async () => {
  let available = false; const writes = [], f = autoFixture(async p => { writes.push(p); return { ...p, updated_at: "r2" }; }, () => available);
  f.session.change(p => { p.clips[0].in = 0.5; }); f.autosaver.schedule();
  await f.fire(); assert.equal(writes.length, 0); assert.equal(f.timers.size, 1);
  available = true; await f.fire(); assert.equal(writes.length, 1); assert.equal(f.session.dirty, false);
});

test("manual flush drains edits made while an autosave request is in flight", async () => {
  const pending = [], writes = [], f = autoFixture(p => { const wait = deferred(); writes.push(clone(p)); pending.push(wait); return wait.promise; });
  f.session.change(p => { p.name = "先存版本"; }); const first = f.autosaver.flush();
  await flushTasks(); f.session.change(p => { p.name = "後續編輯"; }); const final = f.autosaver.flush();
  assert.equal(writes.length, 1); pending[0].resolve({ ...writes[0], updated_at: "r2" }); await flushTasks();
  assert.equal(writes.length, 2); assert.equal(writes[1].name, "後續編輯"); assert.equal(writes[1].updated_at, "r2");
  pending[1].resolve({ ...writes[1], updated_at: "r3" }); await Promise.all([first, final]);
  assert.equal(f.session.project.updated_at, "r3"); assert.equal(f.session.dirty, false);
});

test("network failure keeps the draft, stops background retries, and allows explicit retry", async () => {
  let failing = true, attempts = 0;
  const f = autoFixture(async p => { attempts++; if (failing) throw new Error("offline"); return { ...p, updated_at: "r2" }; });
  f.session.change(p => { p.name = "保留離線修改"; });
  await assert.rejects(f.autosaver.flush(), /offline/); assert.equal(f.session.dirty, true);
  f.autosaver.schedule(); assert.equal(f.timers.size, 0); assert.equal(attempts, 1);
  failing = false; await f.autosaver.flush(); assert.equal(f.session.dirty, false); assert.equal(attempts, 2);
});

test("revision conflicts never automatically retry or overwrite the server", async () => {
  let attempts = 0;
  const conflict = Object.assign(new Error("conflict"), { status: 409 });
  const f = autoFixture(async () => { attempts++; throw conflict; });
  f.session.change(p => { p.name = "保留衝突版本"; });
  await assert.rejects(f.autosaver.flush(), error => error === conflict);
  f.autosaver.schedule(); assert.equal(f.timers.size, 0);
  await assert.rejects(f.autosaver.flush()); assert.equal(attempts, 1); assert.equal(f.session.dirty, true);
});

test("canceling a timer before switching projects cannot save the wrong session", async () => {
  let writes = 0;
  const f = autoFixture(async p => { writes++; return { ...p, updated_at: "r2" }; });
  f.session.change(p => { p.name = "待存專案"; }); f.autosaver.schedule(); f.autosaver.cancel();
  assert.equal(f.timers.size, 0); assert.equal(writes, 0); assert.equal(f.session.dirty, true);
});

const browserSource = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
function browserStorageFixture(session) {
  const values = new Map(), state = { session }; let serial = 0, denied = false;
  const localStorage = { get length() { return values.size; }, key: i => [...values.keys()][i],
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { if (denied) throw new Error("QuotaExceededError"); values.set(key, value); },
    removeItem: key => { if (denied) throw new Error("QuotaExceededError"); values.delete(key); },
  };
  const context = vm.createContext({ state, localStorage, clone, uid: () => `recovery-${++serial}`, renderStatus() {} });
  for (const name of ["storageGet", "storagePut", "readDraft", "storedRecoveries", "archiveDraft", "rememberDraft", "sessionChanged"]) {
    const start = browserSource.search(new RegExp(`  function ${name}\\(`));
    assert.notEqual(start, -1);
    const rest = browserSource.slice(start), end = rest.slice(1).search(/\n  (?:async )?function \w+\(/);
    assert.notEqual(end, -1); vm.runInContext(rest.slice(0, end + 1), context);
  }
  return { values, state, context, deny: () => { denied = true; } };
}

test("late save from an old session cannot delete the new draft after reopening the same project", async () => {
  const old = new ProjectSession(project()), current = new ProjectSession(project()), wait = deferred();
  old.draftOwner = "old"; current.draftOwner = "new";
  const f = browserStorageFixture(old), key = `h3-editor-draft:${old.project.id}`;
  old.onChange = () => f.context.sessionChanged(old);
  old.change(p => { p.name = "舊分頁存檔中"; });
  const saving = old.save(async p => { await wait.promise; return { ...p, updated_at: "r2" }; });
  f.state.session = current; current.change(p => { p.name = "重新開啟後的新修改"; }); f.context.sessionChanged(current);
  const preserved = f.values.get(key); wait.resolve(); await saving;
  assert.equal(f.values.get(key), preserved); assert.equal(current.dirty, true);
  assert.equal(JSON.parse(preserved).project.name, "重新開啟後的新修改");
});

test("another tab's draft survives a clean save and is archived before new edits replace its active key", () => {
  const session = new ProjectSession(project()); session.draftOwner = "tab-b";
  const f = browserStorageFixture(session), key = `h3-editor-draft:${session.project.id}`;
  const other = { ...record(project(), { ...project(), name: "另一分頁草稿" }), owner: "tab-a" };
  f.values.set(key, JSON.stringify(other)); f.context.rememberDraft(session);
  assert.deepEqual(JSON.parse(f.values.get(key)), other);
  session.change(p => { p.name = "目前分頁草稿"; }); f.context.rememberDraft(session);
  assert.equal(JSON.parse(f.values.get(key)).owner, "tab-b");
  const recoveries = [...f.values].filter(([key]) => key.startsWith("h3-editor-recovery:"));
  assert.equal(recoveries.length, 1); assert.deepEqual(JSON.parse(recoveries[0][1]).record, other);
});

test("storage exhaustion preserves the original recovery draft and reports that the new backup failed", () => {
  const session = new ProjectSession(project()); session.draftOwner = "new";
  const f = browserStorageFixture(session), key = `h3-editor-draft:${session.project.id}`;
  const original = JSON.stringify({ ...record(project(), { ...project(), name: "不可覆蓋" }), owner: "old" });
  f.values.set(key, original); f.deny();
  session.change(p => { p.name = "仍可留在記憶體的修改"; }); f.context.rememberDraft(session);
  assert.equal(f.values.get(key), original); assert.equal(session.localDraftFailed, true);
  assert.equal(session.protectedDraft.project.name, "不可覆蓋"); assert.equal(session.dirty, true);
});

test("a pending draft prompt does not lock the library or editor controls", () => {
  const state = { session: new ProjectSession(project()), backendReady: true, busy: false, trimDrag: null,
    draft: { resolution: { status: "conflict" } } };
  const context = vm.createContext({ state, exporting: () => false });
  const start = browserSource.indexOf("  const locked ="), end = browserSource.indexOf("\n", start);
  assert.ok(start >= 0 && end > start); vm.runInContext(`${browserSource.slice(start, end)}\nthis.isLocked = locked();`, context);
  assert.equal(context.isLocked, false);
});
