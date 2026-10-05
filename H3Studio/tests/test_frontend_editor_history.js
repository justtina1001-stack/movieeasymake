"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { editorHistoryVideoUrl, editorVideoHistoryItems, filterEditorVideoHistory,
  createEditorVideoHistory, ProjectSession } = require("../static/editor.js");

const id = number => number.toString(16).padStart(32, "0");
const pipeline = (number, extra = {}) => ({ id: id(number), status: "completed", title: `Film ${number}`,
  updated_at: "2026-10-03T13:00:00+08:00", snapshot: { duration: 15 },
  video_url: `/api/editor/exports/${id(number + 100)}/file`, editor_project_id: id(number + 200), ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

class Element {
  constructor(ownerDocument, tagName = "div") {
    this.ownerDocument = ownerDocument; this.tagName = tagName; this.children = []; this.listeners = new Map();
    this.dataset = {}; this.value = "all"; this.disabled = false; this.textContent = ""; this.open = false; this.src = "";
    this.pauses = 0; this.loads = 0; this.shows = 0; this.classes = new Set();
    this.classList = { toggle: (name, force) => { const active = force ?? !this.classes.has(name); active ? this.classes.add(name) : this.classes.delete(name); } };
  }
  set innerHTML(_value) { throw new Error("History must create safe nodes and use textContent"); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, fn) { const callbacks = this.listeners.get(name) || []; callbacks.push(fn); this.listeners.set(name, callbacks); }
  emit(name, event = {}) { return Promise.all((this.listeners.get(name) || []).map(fn => fn(event))); }
  pause() { this.pauses++; }
  load() { this.loads++; }
  removeAttribute(name) { delete this[name]; }
  showModal() { this.open = true; this.shows++; }
  close() { this.open = false; this.emit("close"); }
}
function fixture(read, initialProject = id(201)) {
  const doc = { createElement: name => new Element(doc, name) }, elements = {};
  for (const name of ["list", "status", "filter", "refresh", "dialog", "video", "title", "detail", "close", "download", "editor", "previewStatus"]) elements[name] = new Element(doc, name);
  let currentProject = initialProject;
  const history = createEditorVideoHistory({ elements, read, projectId: () => currentProject });
  return { history, elements, setProject: value => { currentProject = value; } };
}
const cards = elements => elements.list.children.filter(node => node.tagName === "article");

test("history contains every completed film across projects without a latest-only or 50-item cutoff", () => {
  const all = Array.from({ length: 65 }, (_, index) => pipeline(index + 1));
  const records = editorVideoHistoryItems({ items: [...all, pipeline(70, { status: "running" }), pipeline(71, { video_url: "https://outside.example/film.mp4" })] });
  assert.equal(records.length, 65);
  assert.equal(filterEditorVideoHistory(records, "all", id(201)).length, 65);
  assert.deepEqual(filterEditorVideoHistory(records, "current", id(201)).map(item => item.id), [id(1)]);
  assert.equal(filterEditorVideoHistory(records, "current", id(999)).length, 0);
  assert.equal(editorVideoHistoryItems({ items: [pipeline(1), pipeline(1)] }).length, 1);
});

test("history accepts local completed-film routes and legacy references without masking malicious URLs", () => {
  const local = [`/api/assets/${id(5)}`, `/api/jobs/${id(5)}/video`, `/api/scripts/video-pipelines/${id(5)}/video`, `/api/editor/exports/${id(5)}/file?download=1`];
  for (const video_url of local) assert.equal(editorHistoryVideoUrl(pipeline(1, { video_url })), video_url);
  assert.equal(editorHistoryVideoUrl(pipeline(1, { video_url: "", video_asset_id: id(6) })), `/api/assets/${id(6)}`);
  assert.equal(editorHistoryVideoUrl(pipeline(1, { video_url: null, export_id: id(7) })), `/api/editor/exports/${id(7)}/file`);
  for (const video_url of ["https://outside.example/video.mp4", `//localhost/api/assets/${id(5)}`, "data:video/mp4,unsafe", "javascript:alert(1)", `/api/assets/${id(5)}#fragment`, `/api/assets/../${id(5)}`, `/api/assets\\${id(5)}`, `/api/assets/${id(5)}?redirect=outside`, 123]) {
    assert.equal(editorHistoryVideoUrl(pipeline(1, { video_url, export_id: id(7), video_asset_id: id(6) })), "");
  }
  assert.equal(editorVideoHistoryItems([pipeline(1, { id: "../invalid" })]).length, 0);
  const unsafeProject = editorVideoHistoryItems([pipeline(1, { editor_project_id: "../invalid" })])[0];
  assert.equal(unsafeProject.editorUrl, "");
  assert.equal(editorHistoryVideoUrl(pipeline(1, { status: "failed" })), "");
});

test("cards use literal titles, include old dates and durations, and open editing in a protected new tab", async () => {
  const name = '<img src=x onerror="throw new Error()">';
  const ui = fixture(async () => ({ items: [pipeline(1, { project_title: name, video_url: `/api/editor/exports/${id(101)}/file?download=0` })] }));
  await ui.history.refresh();
  const card = cards(ui.elements)[0], actions = card.children[2];
  assert.equal(card.children[0].textContent, name);
  assert.match(card.children[1].textContent, /15 秒/);
  assert.match(card.children[1].textContent, /2026/);
  assert.equal(actions.children[1].href, `/api/editor/exports/${id(101)}/file?download=1`);
  assert.equal(actions.children[2].href, `/editor?project=${id(201)}`);
  assert.equal(actions.children[2].target, "_blank");
  assert.equal(actions.children[2].rel, "noopener noreferrer");
});

test("filter changes and project switches only redraw the history collection", async () => {
  const ui = fixture(async () => [pipeline(1), pipeline(2)]);
  await ui.history.refresh(); assert.equal(cards(ui.elements).length, 2);
  ui.elements.filter.value = "current"; await ui.elements.filter.emit("change");
  assert.deepEqual(cards(ui.elements).map(card => card.dataset.pipelineId), [id(1)]);
  ui.setProject(id(202)); ui.history.render();
  assert.deepEqual(cards(ui.elements).map(card => card.dataset.pipelineId), [id(2)]);
  ui.elements.filter.value = "all"; await ui.elements.filter.emit("change");
  assert.equal(cards(ui.elements).length, 2);
});

test("preview, refresh and close leave dirty timeline, main playback, selection, export and imported media untouched", async () => {
  const session = new ProjectSession({ id: id(201), name: "Editing", width: 1280, height: 720, fps: 24, updated_at: "r1",
    clips: [{ id: "clip", media_id: "media", in: 0, out: 15, volume: 1 }] });
  session.change(value => { value.name = "Unsaved editing"; });
  const editor = { project: session.project, dirty: session.dirty, undo: session.undoStack, selected: "clip", playhead: 8,
    exporting: { status: "running", progress: 33 }, media: ["media"], mainVideo: { src: "/main-video", currentTime: 8, paused: false }, saves: 0 };
  const before = JSON.stringify(editor), calls = [];
  const ui = fixture(async (...args) => { calls.push(args); return [pipeline(1), pipeline(2)]; });
  await ui.history.refresh();
  assert.equal(ui.history.open(id(1)), true);
  assert.equal(ui.elements.video.src, `/api/editor/exports/${id(101)}/file`);
  assert.equal(ui.elements.dialog.open, true);
  assert.equal(ui.elements.editor.target, "_blank");
  const pauses = ui.elements.video.pauses; await ui.history.refresh(); ui.history.open(id(1));
  assert.equal(ui.elements.video.pauses, pauses, "Refreshing the same film must not reset its preview");
  ui.history.open(id(2)); ui.history.close();
  assert.equal(ui.elements.dialog.open, false); assert.equal(ui.elements.video.src, undefined);
  assert.equal(JSON.stringify(editor), before);
  assert.ok(calls.every(args => args.length === 1 && args[0] === "/api/scripts/video-pipelines"));
});

test("network failures, malformed replies and old hosts preserve already loaded history and allow retries", async () => {
  let failure = null;
  const ui = fixture(async () => { if (failure === "malformed") return { unexpected: [] }; if (failure) throw failure; return { items: [pipeline(1)] }; });
  await ui.history.refresh(); const firstCard = cards(ui.elements)[0];
  for (const error of [new Error("offline"), Object.assign(new Error("not found"), { status: 404 }), "malformed"]) {
    failure = error; assert.equal(await ui.history.refresh(), false);
    assert.equal(cards(ui.elements)[0], firstCard); assert.equal(ui.elements.refresh.disabled, false);
    assert.match(ui.elements.status.textContent, /保留/);
  }
  assert.equal(ui.history.open(id(1)), true);
  failure = null; assert.equal(await ui.history.refresh(), true);
});

test("late history replies cannot steal the current tab, project or latest refresh result", async () => {
  const first = deferred(), second = deferred(); let count = 0, activeTab = "history";
  const ui = fixture(() => ++count === 1 ? first.promise : second.promise);
  const initial = ui.history.refresh(); activeTab = "media"; ui.setProject(id(202));
  const latest = ui.history.refresh(); second.resolve({ items: [pipeline(2)] }); await latest;
  first.resolve({ items: [pipeline(1)] }); await initial;
  assert.equal(activeTab, "media");
  assert.deepEqual(cards(ui.elements).map(card => card.dataset.pipelineId), [id(2)]);
  ui.elements.filter.value = "current"; ui.history.render(); assert.equal(cards(ui.elements).length, 1);
  assert.equal(ui.elements.dialog.shows, 0);
});

test("transient empty or unfinished-only responses retain established history and active preview", async () => {
  let reply = { items: [pipeline(1)] };
  const ui = fixture(async () => reply); await ui.history.refresh(); ui.history.open(id(1));
  const firstCard = cards(ui.elements)[0], source = ui.elements.video.src, pauses = ui.elements.video.pauses;
  for (const data of [{ items: [] }, { items: [pipeline(2, { status: "running" })] }]) {
    reply = data; assert.equal(await ui.history.refresh(), false);
    assert.equal(cards(ui.elements)[0], firstCard); assert.equal(ui.elements.video.src, source);
    assert.equal(ui.elements.video.pauses, pauses); assert.equal(ui.elements.dialog.open, true);
    assert.equal(ui.elements.refresh.disabled, false); assert.match(ui.elements.status.textContent, /先前清單與預覽仍保留/);
  }
  const firstTime = fixture(async () => ({ items: [] }));
  assert.equal(await firstTime.history.refresh(), true); assert.equal(cards(firstTime.elements).length, 0);
  assert.match(firstTime.elements.status.textContent, /0 支/);
});

test("partial future replies retain known older films and state that the collection is incomplete", async () => {
  let partial = false;
  const ui = fixture(async () => partial ? { items: [pipeline(2)], has_more: true, total: 20 } : { items: [pipeline(1)] });
  await ui.history.refresh(); partial = true; await ui.history.refresh();
  assert.equal(cards(ui.elements).length, 2); assert.match(ui.elements.status.textContent, /部分紀錄/);
});

test("preview playback errors preserve cards, source links and the current editor", async () => {
  const ui = fixture(async () => [pipeline(1)]); await ui.history.refresh(); ui.history.open(id(1));
  const card = cards(ui.elements)[0], url = ui.elements.download.href;
  await ui.elements.video.emit("error");
  assert.match(ui.elements.previewStatus.textContent, /無法播放/);
  assert.equal(cards(ui.elements)[0], card); assert.equal(ui.elements.download.href, url);
  ui.history.close(); assert.equal(ui.elements.previewStatus.textContent, "");
  assert.equal(ui.history.open(id(1)), true);
});

test("the shipped editor shortcuts do not handle Space, Delete or save while the history dialog is open", async () => {
  const source = fs.readFileSync(path.join(__dirname, "../static/editor.js"), "utf8");
  const start = source.indexOf('  document.addEventListener("keydown", action(async event => {');
  const end = source.indexOf('\n  root.addEventListener("blur"', start);
  assert.ok(start > 0 && end > start);
  let keydown;
  const context = vm.createContext({ document: { addEventListener: (_name, fn) => { keydown = fn; } }, action: fn => fn,
    $: name => { assert.equal(name, "videoHistoryDialog"); return { open: true }; } });
  vm.runInContext(source.slice(start, end), context);
  for (const event of [{ key: " ", code: "Space" }, { key: "Delete" }, { key: "s", ctrlKey: true }]) {
    await keydown({ ...event, preventDefault: () => assert.fail("History playback must retain native video controls") });
  }
});
