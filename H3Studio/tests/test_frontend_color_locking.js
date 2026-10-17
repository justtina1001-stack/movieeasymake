"use strict";
/* Exercise the shipped lock refresh after project/archive asynchronous work. */
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), color = require("../static/editor_color.js"), colorUI = require("../static/editor_color_ui.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8"), lines = source.split(/\r?\n/);
function shippedFunction(name) {
  const found = new RegExp(`^  (?:async )?function ${name}\\(`, "m").exec(source); assert.ok(found, name);
  for (let end = source.indexOf("\n", found.index); end !== -1; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(found.index, end);
    try { new vm.Script(candidate); return candidate; } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Could not extract ${name}`);
}
function shippedLine(needle) { const line = lines.find(value => value.includes(needle)); assert.ok(line, needle); return line; }
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const dataKey = name => name.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
function matches(node, selector) {
  if (selector.startsWith(".")) return node.classes.has(selector.slice(1));
  const match = /^\[data-([a-z-]+)\]$/.exec(selector);
  return Boolean(match && Object.hasOwn(node.dataset, dataKey(match[1])));
}
class Element {
  constructor(name, doc) {
    this.name = name; this.ownerDocument = doc; this.dataset = {}; this.children = []; this.parent = null;
    this.style = {}; this.disabled = false; this.draggable = false; this.attributes = new Map(); this.classes = new Set(); this.rebuilds = 0;
    this.classList = { toggle: (name, on) => { on ? this.classes.add(name) : this.classes.delete(name); }, contains: name => this.classes.has(name) };
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") this.classes = new Set(String(value).split(/\s+/));
    if (name === "tabindex") this.tabIndex = Number(value);
    if (name.startsWith("data-")) this.dataset[dataKey(name.slice(5))] = String(value);
    if (name === "disabled") this.disabled = true;
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  set innerHTML(text) {
    this.children = []; this.rebuilds++; this._html = text;
    const stack = [this];
    for (const match of String(text).matchAll(/<(\/?)([a-z][\w:-]*)([^>]*)>/gi)) {
      if (match[1]) { const at = stack.findLastIndex(node => node.name === match[2]); if (at > 0) stack.length = at; continue; }
      const child = new Element(match[2], this.ownerDocument);
      for (const attribute of match[3].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) child.setAttribute(attribute[1], attribute[2] ?? attribute[3] ?? attribute[4] ?? "");
      child.parent = stack.at(-1); child.parent.children.push(child); stack.push(child);
    }
  }
  get innerHTML() { return this._html; }
  querySelectorAll(selector) {
    const patterns = selector.split(","), found = [];
    const visit = node => { for (const child of node.children) { if (patterns.some(pattern => matches(child, pattern.trim()))) found.push(child); visit(child); } };
    visit(this); return found;
  }
}
const project = (id = "original") => ({ id, name: "調色片段", width: 1280, height: 720, fps: 24, updated_at: "r1",
  clips: [{ id: "video", media_id: "media", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [], overlays: [],
  color_adjustments: [{ id: "grade", name: "暖色", start: 0, end: 8, temperature: 30 }] });
function harness(handler = async () => ({ project: project("loaded") })) {
  const elements = new Map(), requests = [], effects = [];
  const document = { querySelectorAll(selector) { return [...elements.values()].flatMap(node => node.querySelectorAll(selector)); } };
  const $ = id => { if (!elements.has(id)) elements.set(id, new Element(id, document)); return elements.get(id); };
  const state = { session: new core.ProjectSession(project()), projects: [project()], selectedKind: "video", selected: "video", busy: false,
    backendReady: true, colorReady: true, archivesReady: true, media: new Map([["media", { id: "media", kind: "video", duration: 8, has_audio: true }]]), colorChecking: false, zoom: 60,
    textStyleReady: true, textFontsReady: true, textIdleReady: true, clipAnimationsReady: true, clipTransitionsReady: true, transitionEffectsReady: true, referenceTransitionsReady: true };
  const noop = () => {};
  const sandbox = { state, $, core, document, colorMath: color, root: { H3EditorColorUI: colorUI },
    project: () => state.session?.project || null,
    selectedClip: () => state.selectedKind === "color" ? state.session?.project.color_adjustments?.find(item => item.id === state.selected) : state.session?.project.clips.find(item => item.id === state.selected),
    selectedSpeedVideo: () => state.session?.project.clips[0], canSwitch: () => !sandbox.locked(),
    totalDuration: core.totalDuration, clone: core.clone, editableContent: core.editableContent, resolveDraft: core.resolveDraft,
    requireColorSupport: core.requireColorSupport, requireSavedColor: core.requireSavedColor,
    requireTextStyleSupport: core.requireTextStyleSupport, requireTextFontSupport: core.requireTextFontSupport, requireTextIdleSupport: core.requireTextIdleSupport,
    requireAnimationSupport: core.requireAnimationSupport, requireTransitionEffectSupport: core.requireTransitionEffectSupport,
    previewViewport: { render: noop }, layoutController: { render: noop },
    renderTextStyleControls: noop, renderTextFontControls: noop, renderSubtitleControls: noop, renderPositionControls: noop, renderSpeedControls: noop, renderAnimationControls: noop, renderColorControls: noop,
    renderProjectList: noop, saveProject: async () => effects.push("save"), rememberDraft: () => effects.push("draft"),
    storagePut: noop, notify: message => effects.push(message),
    api: async (url, options) => { requests.push({ url, options }); return handler(url, options); },
    uploadFile: async (file, progress, path) => { requests.push({ path, file }); progress(100); return handler(path, file); },
    installProject: value => { state.session = new core.ProjectSession(value); state.selected = value.clips[0]?.id || null; state.selectedKind = "video"; sandbox.renderColorTrack(value); },
    render: () => sandbox.renderColorTrack(state.session.project), renderMedia: () => sandbox.renderDisabled(),
  };
  const controller = [shippedLine("const escape ="), shippedLine("const exporting ="), shippedLine("const locked ="), shippedLine("const json ="),
    ...["overlaySupported", "renderDisabled", "renderColorTrack", "loadProject", "newProject", "saveRecoveryCopy", "openProjectArchive"].map(shippedFunction),
    "this.locked = locked; this.renderDisabled = renderDisabled; this.renderColorTrack = renderColorTrack; this.loadProject = loadProject; this.newProject = newProject; this.saveRecoveryCopy = saveRecoveryCopy; this.openProjectArchive = openProjectArchive;"].join("\n");
  vm.runInNewContext(controller, sandbox);
  sandbox.renderColorTrack(state.session.project);
  const button = () => $("colorTrack").querySelectorAll("[data-color-id]")[0];
  const handles = () => $("colorTrack").querySelectorAll("[data-color-edge]");
  return { state, $, button, handles, requests, effects, document, ...sandbox };
}
test("a timeline rendered during busy work unlocks its existing colour buttons and handles without rebuilding them", () => {
  const h = harness(); h.state.busy = true; h.renderColorTrack(h.state.session.project);
  const button = h.button(), handles = h.handles(), rebuilds = h.$("colorTrack").rebuilds;
  assert.equal(button.disabled, true); assert.ok(handles.every(node => node.getAttribute("aria-disabled") === "true"));
  h.state.busy = false; h.renderDisabled();
  assert.equal(h.button(), button); assert.deepEqual(h.handles(), handles); assert.equal(h.$("colorTrack").rebuilds, rebuilds);
  assert.equal(button.disabled, false); assert.ok(handles.every(node => node.getAttribute("aria-disabled") === "false" && node.tabIndex === 0));
});
test("central lock refresh handles busy/gesture locks and unlocks after each operation", () => {
  const h = harness(), originalButton = h.button(), handles = h.handles();
  for (const [field, active] of [["busy", true], ["colorDrag", {}], ["trimDrag", {}], ["layoutDragging", true]]) {
    h.state[field] = active; h.renderDisabled(); assert.equal(originalButton.disabled, true, field);
    assert.ok(handles.every(node => node.getAttribute("aria-disabled") === "true" && node.tabIndex === -1), field);
    h.state[field] = null; h.renderDisabled(); assert.equal(originalButton.disabled, false, field);
    assert.ok(handles.every(node => node.getAttribute("aria-disabled") === "false" && node.tabIndex === 0), field);
  }
  assert.equal(h.button(), originalButton);
});
test("async project load unlocks the new colour track in finally without a page reload", async () => {
  const wait = deferred(), h = harness(() => wait.promise), loading = h.loadProject("loaded");
  assert.equal(h.state.busy, true); assert.equal(h.button().disabled, true);
  wait.resolve({ project: project("loaded") }); await loading;
  assert.equal(h.state.session.project.id, "loaded"); assert.equal(h.state.busy, false); assert.equal(h.button().disabled, false);
  assert.ok(h.handles().every(node => node.tabIndex === 0));
});
test("V12 archive import unlocks F1 after busy install and preserves the grade content", async () => {
  const restored = project("archive-restored"), h = harness(async path => { assert.equal(path, "/api/editor/projects/import"); return { project: restored, media: [] }; });
  await h.openProjectArchive({ name: "grading.h3edit.zip" });
  assert.equal(h.state.session.project.id, restored.id); assert.deepEqual(color.canonicalAdjustments(h.state.session.project), color.canonicalAdjustments(restored));
  assert.equal(h.state.busy, false); assert.equal(h.button().disabled, false); assert.ok(h.handles().every(node => node.getAttribute("aria-disabled") === "false"));
});
test("copy and recovery-copy unlock newly mounted grades after their protected POST", async () => {
  for (const rescue of [false, true]) {
    const h = harness(async (path, options) => { assert.equal(path, "/api/editor/projects"); const payload = JSON.parse(options.body); return { project: { ...payload, id: rescue ? "rescued" : "copied", updated_at: "r2" } }; });
    if (rescue) { const draft = project(); draft.color_adjustments[0].temperature = -30; await h.saveRecoveryCopy({ record: { project: draft, savedSignature: core.signature(project()) } }); }
    else await h.newProject(true);
    assert.equal(h.state.busy, false); assert.equal(h.button().disabled, false); assert.ok(h.handles().every(node => node.tabIndex === 0));
  }
});
test("failed asynchronous work releases the previous colour buttons instead of leaving the editor inert", async () => {
  const h = harness(async () => { throw new Error("test connection failure"); }), prior = h.button();
  await assert.rejects(h.loadProject("unavailable"), /connection failure/);
  assert.equal(h.state.busy, false); assert.equal(h.button(), prior); assert.equal(prior.disabled, false);
});
test("an unsupported host still permits selecting a grade to inspect while its timing handles stay unavailable", () => {
  const h = harness(); h.state.colorReady = false; h.renderDisabled();
  assert.equal(h.button().disabled, false); assert.ok(h.handles().every(node => node.getAttribute("aria-disabled") === "true" && node.tabIndex === -1));
  h.state.colorReady = true; h.renderDisabled(); assert.ok(h.handles().every(node => node.getAttribute("aria-disabled") === "false" && node.tabIndex === 0));
});
test("saving keeps grade selection available but timing handles wait until the in-flight snapshot is finished", () => {
  const h = harness(); h.state.session.inFlight = Promise.resolve(); h.renderDisabled();
  assert.equal(h.button().disabled, false); assert.ok(h.handles().every(node => node.getAttribute("aria-disabled") === "true" && node.tabIndex === -1));
  h.state.session.inFlight = null; h.renderDisabled(); assert.ok(h.handles().every(node => node.getAttribute("aria-disabled") === "false" && node.tabIndex === 0));
});
