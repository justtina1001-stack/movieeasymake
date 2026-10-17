"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), animationMath = require("../static/editor_animations.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8"), html = fs.readFileSync(require.resolve("../static/editor.html"), "utf8");
const layer = (extra = {}) => ({ id: "title", kind: "text", start: 0, end: 4, x: 0.5, y: 0.5, width: 0.7, rotation: 0, opacity: 1, text: "繁體標題 Text", font_size: 0.06, color: "#ffffff", background: "transparent", bold: true, align: "center", ...extra });
const project = (extra = {}) => ({ id: "font-project", name: "字型試稿", updated_at: "r1", width: 1280, height: 720, fps: 24, clips: [{ id: "main", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [], overlays: [layer()], ...extra });
const catalog = () => ({ available: true, family: "default face", items: [
  { id: "default", label: "預設字型", family: "Default", bold_family: "Default Bold", cjk: true, available: true, bold_available: true },
  { id: "kai", label: "標楷體", family: "Kai", bold_family: "Kai", cjk: true, available: true, bold_available: false },
  { id: "georgia", label: "Georgia", family: "Georgia", bold_family: "Georgia Bold", cjk: false, available: true, bold_available: true },
] });
const idle = { preset: "float", speed: 1.5, amount: 0.75 };
function shipped(name) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`)); assert.ok(start >= 0, name);
  for (let end = source.indexOf("\n", start); end >= 0; end = source.indexOf("\n", end + 1)) { const code = source.slice(start, end); try { new vm.Script(code); return code; } catch (error) { if (!(error instanceof SyntaxError)) throw error; } }
  assert.fail(`Cannot extract ${name}`);
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

test("default font remains legacy-equivalent while a chosen font is canonical editing content and invalidates only its raster", () => {
  const legacy = layer(), defaults = layer({ font_family: "default", animation_idle: { preset: "none" } });
  assert.deepEqual(core.canonicalTextFont(defaults), {}); assert.equal(core.signature(project()), core.signature(project({ overlays: [defaults] })));
  assert.equal(core.overlayRasterKey(legacy, 1280, 720), core.overlayRasterKey(defaults, 1280, 720));
  const chosen = layer({ font_family: "kai", animation_idle: idle });
  assert.equal(core.editableContent(project({ overlays: [chosen] })).overlays[0].font_family, "kai");
  assert.deepEqual(core.canonicalOverlay(chosen).animation_idle, idle); assert.notEqual(core.signature(project()), core.signature(project({ overlays: [chosen] })));
  assert.notEqual(core.overlayRasterKey(legacy, 1280, 720), core.overlayRasterKey(chosen, 1280, 720));
  assert.equal(core.overlayRasterKey(chosen, 1280, 720), core.overlayRasterKey({ ...chosen, animation_idle: { preset: "blink", speed: 4, amount: 2 }, animation_in: { type: "fade", duration: 0.5 } }, 1280, 720));
});

test("font catalog accepts only loaded safe IDs and keeps literal display labels without guessing a file path", () => {
  const data = catalog(); data.items[1].label = '<img src=x> 標楷體'; data.items.push(null, {}, { id: "../../font", available: true }, { id: "unavailable", available: false }, { id: "kai", available: true }, { id: "pretend", available: "true" });
  const fonts = core.normalizeEditorFonts(data); assert.deepEqual(fonts.map(item => item.id), ["default", "kai", "georgia"]); assert.equal(fonts[1].label, data.items[1].label);
  for (const invalid of ["", "../kai", "Kai", "C:/font.ttf", null, false, 42]) assert.throws(() => core.validateTextFont(layer({ font_family: invalid })), /字型/);
  for (const kind of ["image", "video"]) assert.throws(() => core.validateTextFont({ kind, font_family: "default" }), /只適用/);
  assert.doesNotThrow(() => core.validateTextFont(layer({ font_family: "previously_registered_font" })));
  assert.deepEqual(core.normalizeEditorFonts({ available: true, family: "old default" }), []);
});

test("independent font and idle capability guards preserve custom settings while legacy defaults still save", () => {
  const original = project({ overlays: [layer({ font_family: "kai", animation_idle: idle })] }), before = core.clone(original);
  assert.throws(() => core.requireTextFontSupport(original, false), /草稿仍保留/); assert.throws(() => core.requireTextIdleSupport(original, false), /草稿仍保留/); assert.deepEqual(original, before);
  assert.doesNotThrow(() => core.requireTextFontSupport(original, true)); assert.doesNotThrow(() => core.requireTextIdleSupport(original, true));
  const defaults = project({ overlays: [layer({ font_family: "default", animation_idle: { preset: "pulse", amount: 0 } })] }); assert.doesNotThrow(() => core.requireTextFontSupport(defaults, false)); assert.doesNotThrow(() => core.requireTextIdleSupport(defaults, false));
});

test("saving cannot silently drop fonts or idle effects and undo retains the rejected editing content", async () => {
  for (const field of ["font_family", "animation_idle"]) {
    const session = new core.ProjectSession(project()); session.change(value => Object.assign(value.overlays[0], { font_family: "kai", animation_idle: idle })); const before = core.clone(session.project), signature = session.savedSignature;
    await assert.rejects(session.save(async sent => { const saved = core.clone(sent); delete saved.overlays[0][field]; saved.updated_at = "r2"; return saved; }), /字型|動畫/);
    assert.deepEqual(session.project, before); assert.equal(session.savedSignature, signature); assert.equal(session.dirty, true);
    session.travel(true); assert.equal(session.dirty, false); session.travel(false); assert.deepEqual(session.project, before);
  }
});

test("font and idle edits made during saving survive until their own complete snapshot is acknowledged", async () => {
  const session = new core.ProjectSession(project()), waiting = deferred(), snapshots = []; session.change(value => { value.overlays[0].font_family = "kai"; });
  const saving = session.save(async sent => { snapshots.push(core.clone(sent)); if (snapshots.length === 1) await waiting.promise; return { ...sent, overlays: sent.overlays.map(core.canonicalOverlay), updated_at: `r${snapshots.length + 1}` }; });
  session.change(value => Object.assign(value.overlays[0], { font_family: "georgia", animation_idle: idle })); waiting.resolve(); await saving;
  assert.equal(snapshots.length, 2); assert.equal(snapshots[0].overlays[0].font_family, "kai"); assert.equal(snapshots[1].overlays[0].font_family, "georgia"); assert.deepEqual(snapshots[1].overlays[0].animation_idle, idle); assert.equal(session.dirty, false);
  session.travel(true); assert.equal(session.project.overlays[0].font_family, "kai"); session.travel(false); assert.equal(session.project.overlays[0].font_family, "georgia");
});

test("split trimming layer transfer and recovery preserve font IDs and idle configuration without aliasing halves", () => {
  const original = project({ overlays: [layer({ font_family: "kai", animation_idle: idle })] }), session = new core.ProjectSession(original);
  session.change(value => core.splitOverlayAt(value, "title", 2, () => "second"));
  assert.ok(session.project.overlays.every(item => item.font_family === "kai")); assert.deepEqual(session.project.overlays[1].animation_idle, idle);
  const adjusted = core.shiftOverlayTime(session.project, "second", "right", -0.25).project;
  assert.equal(adjusted.overlays[1].font_family, "kai"); assert.deepEqual(adjusted.overlays[1].animation_idle, idle);
  const moved = core.transformTrackDrag(adjusted, { kind: "overlay", id: "second" }, { kind: "overlay", mode: "insert", index: 0, start: 5 }).project;
  const restored = core.resolveDraft(original, { project: moved, savedSignature: core.signature(original) }); assert.equal(restored.status, "recoverable"); assert.equal(restored.project.overlays.find(item => item.id === "second").font_family, "kai");
  session.project.overlays[1].animation_idle.speed = 2; assert.equal(session.project.overlays[0].animation_idle.speed, 1.5); assert.equal(original.overlays[0].animation_idle.speed, 1.5);
});

function harness(options = {}) {
  const session = new core.ProjectSession(project({ overlays: [layer(options.layer)] })), nodes = new Map();
  const state = { session, selected: "title", selectedKind: "overlay", playhead: 1, busy: false, backendReady: true, textFontsReady: true, textIdleReady: true, fonts: core.normalizeEditorFonts(catalog()), fontCatalogReady: true, fontCatalogBusy: false, fontCatalogError: "", fontCatalogSerial: 0, fontRasterGeneration: 0, rasterCache: new Map(), rasterNodes: new Map(), media: new Map([["video", { kind: "video", duration: 8 }]]), ...options.state };
  class Option { constructor(text, value) { this.textContent = text; this.value = value; this.disabled = false; } }
  const node = id => { if (!nodes.has(id)) { const classes = new Set(); nodes.set(id, { value: "", textContent: "", options: [], disabled: false, classList: { toggle(name, flag) { flag ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) }, add(item) { this.options.push(item); }, replaceChildren() { this.options = []; } }); } return nodes.get(id); };
  const context = vm.createContext({ ...core, core, state, animationMath, Option, $: node, api: options.api || (async () => catalog()), selectedClip: () => state.session.project.overlays.find(item => item.id === state.selected), project: () => state.session.project, locked: () => Boolean(options.locked), document: { activeElement: null }, edit: (update, group) => { if (!options.locked) state.session.change(update, group); }, action: fn => fn, render() {}, renderInspector() {}, renderDisabled() {}, renderAnimationControls() {}, renderOverlayPreview() {}, clearTimeout() {}, URL: { revokeObjectURL() {} }, pause() {}, uid: () => "duplicate" });
  for (const name of ["overlayEdit", "renderTextFontControls", "loadTextFonts", "editTextFont", "invalidateTextFontRasters", "setSelectedAnimation", "setSelectedIdleValue"]) vm.runInContext(shipped(name), context);
  context.renderTextFontControls(context.selectedClip()); return { session, state, context, node };
}

test("real font selector changes are undoable and literal labels availability and bold/CJK notes reflect the catalog", () => {
  const h = harness(); h.state.fonts[1].label = '<img> 標楷體'; h.context.renderTextFontControls(h.context.selectedClip());
  assert.equal(h.node("overlayFontFamily").options[1].textContent, '<img> 標楷體'); h.context.editTextFont("kai"); assert.equal(h.session.project.overlays[0].font_family, "kai"); assert.match(h.node("textFontNote").textContent, /没有|沒有/);
  h.context.editTextFont("georgia"); assert.match(h.node("textFontNote").textContent, /西文/); h.context.editTextFont("default"); assert.deepEqual(core.canonicalTextFont(h.session.project.overlays[0]), {}); assert.equal(h.node("overlayFontFamily").value, "default");
  h.session.travel(true); assert.equal(h.session.project.overlays[0].font_family, "georgia");
  assert.throws(() => h.context.editTextFont("missing"), /原設定仍保留/); assert.equal(h.session.project.overlays[0].font_family, "georgia");
});

test("newly discovered installed font IDs are selectable and survive saving and reopening", async () => {
  const data = catalog(), id = "installed_2f48cf651e37a5bd";
  data.items.push({ id, label: "我的已安裝字型", family: "My Installed Font", cjk: true, available: true, bold_available: false });
  const calls = [], h = harness({ api: async url => { calls.push(url); return data; } });
  assert.equal(await h.context.loadTextFonts(), true); assert.deepEqual(calls, ["/api/editor/fonts"]);
  assert.match(h.node("textFontStatus").textContent, /執行 Studio 的電腦已安裝字型/);
  assert.equal(h.node("overlayFontFamily").options.find(item => item.value === id).textContent, "我的已安裝字型");
  h.context.editTextFont(id); assert.equal(h.session.project.overlays[0].font_family, id); assert.equal(h.session.dirty, true);
  let saved;
  await h.session.save(async sent => { saved = core.clone(sent); return { ...saved, updated_at: "r2" }; });
  assert.equal(saved.overlays[0].font_family, id); assert.equal(h.session.dirty, false);
  assert.equal(new core.ProjectSession(saved).project.overlays[0].font_family, id);
});

test("the real refresh button rescans installed fonts and reports failure while retaining the previous catalog", async () => {
  const waiting = deferred(), started = deferred(), calls = [], notifications = [], h = harness({ layer: { font_family: "kai" }, api: async url => { calls.push(url); started.resolve(); return waiting.promise; } });
  h.context.requireCapabilities = async () => ({ text_fonts: true, text_idle_animations: true });
  h.context.notify = message => notifications.push(message);
  const start = source.indexOf('  $("checkTextFontSupport").onclick'), end = source.indexOf("  function editTextStyle", start);
  vm.runInContext(source.slice(start, end), h.context);
  const before = core.signature(h.session.project), reading = h.node("checkTextFontSupport").onclick();
  await started.promise; assert.equal(h.state.fontCatalogBusy, true); assert.equal(h.node("checkTextFontSupport").disabled, true);
  const data = catalog(); data.items.push({ id: "installed_3e7835360980b30f", label: "新安裝字型", cjk: true, available: true });
  waiting.resolve(data); await reading;
  assert.deepEqual(calls, ["/api/editor/fonts?refresh=1"]); assert.equal(h.state.fonts.length, 4); assert.match(notifications.at(-1), /已重新讀取/);
  assert.equal(core.signature(h.session.project), before); assert.equal(h.node("overlayFontFamily").value, "kai");
  h.context.api = async url => { calls.push(url); throw new Error("network offline"); };
  await h.node("checkTextFontSupport").onclick();
  assert.equal(calls.at(-1), "/api/editor/fonts?refresh=1"); assert.equal(h.state.fonts.length, 4);
  assert.match(notifications.at(-1), /更新失敗/); assert.match(h.node("textFontStatus").textContent, /已保留/);
  assert.equal(core.signature(h.session.project), before); assert.equal(h.state.fontCatalogBusy, false);
});

test("unavailable saved fonts remain visible while old capabilities lock selection without changing the project", () => {
  const h = harness({ layer: { font_family: "previously_registered_font" }, state: { textFontsReady: false } }), before = core.signature(h.session.project);
  const selected = h.node("overlayFontFamily").options.find(item => item.value === "previously_registered_font"); assert.equal(selected.disabled, true); assert.equal(h.node("overlayFontFamily").disabled, true); assert.match(h.node("textFontStatus").textContent, /草稿仍保留/);
  assert.throws(() => h.context.editTextFont("default"), /草稿仍保留/); assert.equal(core.signature(h.session.project), before);
});

test("late failed and malformed font catalog responses preserve the currently edited project and last known catalog", async () => {
  const waiting = deferred(), h = harness({ api: async () => waiting.promise }); const loading = h.context.loadTextFonts();
  h.state.session = new core.ProjectSession(project({ id: "other-project", overlays: [layer({ font_family: "georgia", text: "讀取途中寫的稿" })] })); const before = core.signature(h.state.session.project); waiting.resolve(catalog()); assert.equal(await loading, true);
  assert.equal(core.signature(h.state.session.project), before); assert.equal(h.node("overlayFontFamily").value, "georgia"); assert.equal(h.state.session.dirty, false);
  for (const response of [() => { throw new Error("network offline"); }, () => ({ items: [] }), () => ({ available: true, family: "old API" })]) {
    h.context.api = async () => response(); assert.equal(await h.context.loadTextFonts(), false); assert.equal(core.signature(h.state.session.project), before); assert.equal(h.state.fonts.length, 3); assert.match(h.node("textFontStatus").textContent, /已保留/);
  }
});

test("shipped duplicate and text idle actions preserve fonts and effects while none and zero amount explicitly remove only idle", () => {
  const h = harness({ layer: { font_family: "kai", animation_idle: idle } });
  const start = source.indexOf('  $("duplicateClip").onclick'), end = source.indexOf("\n", start); vm.runInContext(source.slice(start, end), h.context); h.node("duplicateClip").onclick();
  const copied = h.state.session.project.overlays.find(item => item.id === "duplicate"); assert.equal(copied.font_family, "kai"); assert.deepEqual(copied.animation_idle, idle);
  h.context.setSelectedAnimation("pulse", "idle"); assert.equal(h.state.session.project.overlays[1].animation_idle.preset, "pulse"); h.context.setSelectedIdleValue("speed", 2); h.context.setSelectedIdleValue("amount", 0.4); assert.deepEqual(JSON.parse(JSON.stringify(h.state.session.project.overlays[1].animation_idle)), { preset: "pulse", speed: 2, amount: 0.4 });
  const before = core.signature(h.state.session.project); assert.throws(() => h.context.setSelectedIdleValue("speed", 5), /待機速度/); assert.equal(core.signature(h.state.session.project), before);
  h.context.setSelectedIdleValue("amount", 0); assert.equal(h.state.session.project.overlays[1].animation_idle, undefined); assert.equal(h.state.session.project.overlays[1].font_family, "kai"); h.state.session.travel(true); assert.equal(h.state.session.project.overlays[1].animation_idle.amount, 0.4);
  h.context.setSelectedAnimation("none", "idle"); assert.equal(h.state.session.project.overlays[1].animation_idle, undefined); assert.equal(h.state.session.project.overlays[0].animation_idle.preset, "float");
});

test("real raster requests send the chosen font and exclude idle clocks while cache hits avoid extra requests", async () => {
  const selected = layer({ font_family: "kai", animation_idle: idle }), calls = [], key = core.overlayRasterKey(selected, 1280, 720);
  const context = vm.createContext({ ...core, state: { rasterCache: new Map(), rasterNodes: new Map([["title", { key }]]), rasterQueue: new core.PreviewRequestQueue(2) }, URL: { createObjectURL: () => "blob:kai" }, pruneRasterCache() {}, json: (method, body) => ({ method, body: JSON.stringify(body) }), fetch: async (url, request) => { calls.push({ url, body: JSON.parse(request.body) }); return { ok: true, status: 200, blob: async () => ({}) }; } }); vm.runInContext(shipped("textRaster"), context);
  assert.equal(await context.textRaster(selected, { width: 1280, height: 720 }), "blob:kai"); assert.equal(calls[0].body.layer.font_family, "kai"); assert.equal(Object.hasOwn(calls[0].body.layer, "animation_idle"), false); assert.equal(selected.animation_idle, idle);
  assert.equal(await context.textRaster({ ...selected, animation_idle: { preset: "pulse" } }, { width: 1280, height: 720 }), "blob:kai"); assert.equal(calls.length, 1);
});

test("successful font refresh replaces cached same-family PNGs and rejects old in-flight blobs while failed refresh preserves previews", async () => {
  const h = harness({ layer: { font_family: "kai" }, state: { textOverlaysReady: true, textStyleReady: true, playing: false, rasterQueue: new core.PreviewRequestQueue(2) } });
  const pending = layer({ id: "pending", font_family: "kai", text: "第二段文字" }); h.session.project.overlays.push(pending);
  const picture = { ...layer(), id: "picture", kind: "image", media_id: "picture" }; h.session.project.overlays.push(picture); h.state.media.set("picture", { url: "/picture.png" });
  const previewNode = src => ({ node: { style: {}, classList: { toggle() {} } }, img: { src, getAttribute(name) { return this[name] || null; }, removeAttribute(name) { delete this[name]; } }, placeholder: { hidden: Boolean(src) }, key: null, error: null });
  const titleNode = previewNode("blob:cached"), pendingNode = previewNode(""), pictureNode = previewNode("/picture.png");
  const key = core.overlayRasterKey(h.session.project.overlays[0], 1280, 720); titleNode.key = titleNode.displayKey = key; pictureNode.key = "/picture.png";
  h.state.rasterNodes = new Map([["title", titleNode], ["pending", pendingNode], ["picture", pictureNode]]);
  h.state.rasterCache.set(key, { url: "blob:cached", promise: Promise.resolve("blob:cached") });
  const oldBlob = deferred(), oldBlobStarted = deferred(), timers = [], calls = [], created = [], revoked = [];
  Object.assign(h.context, { syncTransitionPreview() {}, setTimeout(callback) { timers.push(callback); return timers.length; }, json: (method, body) => ({ method, body: JSON.stringify(body) }),
    URL: { createObjectURL(blob) { created.push(blob.name); return `blob:${blob.name}`; }, revokeObjectURL(url) { revoked.push(url); } },
    fetch: async (url, request) => { const body = JSON.parse(request.body); calls.push(body); return { ok: true, status: 200, blob: async () => { if (calls.length === 1) { oldBlobStarted.resolve(); return oldBlob.promise; } return { name: `refreshed-${body.layer.id}` }; } }; } });
  for (const name of ["textRaster", "pruneRasterCache", "renderOverlayPreview"]) vm.runInContext(shipped(name), h.context);
  h.context.renderOverlayPreview(); timers[0](); await oldBlobStarted.promise;
  const before = core.signature(h.session.project); assert.equal(await h.context.loadTextFonts(true), true);
  assert.equal(h.state.fontRasterGeneration, 1); assert.deepEqual(revoked, ["blob:cached"]); assert.equal(titleNode.img.src, undefined); assert.equal(pictureNode.img.src, "/picture.png");
  for (const callback of timers.slice(1)) callback(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(titleNode.img.src, "blob:refreshed-title"); assert.equal(pendingNode.img.src, "blob:refreshed-pending"); assert.equal(calls.length, 3);
  const currentEntry = h.state.rasterCache.get(pendingNode.key); oldBlob.resolve({ name: "old-in-flight" }); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(created, ["refreshed-title", "refreshed-pending"]); assert.equal(h.state.rasterCache.get(pendingNode.key), currentEntry);
  assert.equal(pendingNode.img.src, "blob:refreshed-pending"); assert.equal(pendingNode.error, null); assert.equal(core.signature(h.session.project), before);
  h.context.api = async () => { throw new Error("network offline"); }; assert.equal(await h.context.loadTextFonts(true), false);
  assert.equal(h.state.fontRasterGeneration, 1); assert.equal(h.state.rasterCache.get(pendingNode.key), currentEntry); assert.equal(pendingNode.img.src, "blob:refreshed-pending"); assert.deepEqual(revoked, ["blob:cached"]);
});

test("shipped overlay preview uses the idle evaluator and a late old-font raster cannot replace the newly chosen font", async () => {
  const title = layer({ font_family: "kai", animation_idle: { preset: "pulse", speed: 1, amount: 1 } }), value = project({ overlays: [title] });
  const first = deferred(), second = deferred(), requests = [], timers = [];
  const classes = new Set(), item = { node: { style: {}, classList: { toggle(name, flag) { flag ? classes.add(name) : classes.delete(name); } } }, img: { src: "", getAttribute(name) { return this[name] || null; } }, placeholder: {}, key: null, error: null };
  const state = { rasterNodes: new Map([["title", item]]), rasterCache: new Map(), playhead: 1, selected: "title", selectedKind: "overlay", textOverlaysReady: true, textStyleReady: true, textFontsReady: true, playing: false };
  const context = vm.createContext({ ...core, state, animationMath, project: () => value, $: () => ({ textContent: "" }), pruneRasterCache() {}, syncTransitionPreview() {}, clearTimeout() {}, setTimeout(callback) { timers.push(callback); return timers.length; }, textRaster(sent) { requests.push(sent.font_family); return sent.font_family === "kai" ? first.promise : second.promise; } }); vm.runInContext(shipped("renderOverlayPreview"), context);
  context.renderOverlayPreview(); const expected = animationMath.animationTransformAt(title, 1, 4); assert.equal(item.node.style.width, `${title.width * expected.scale * 100}%`); const initialWidth = item.node.style.width;
  timers[0](); title.font_family = "georgia"; context.renderOverlayPreview(); timers[1](); second.resolve("blob:georgia"); await Promise.resolve(); await Promise.resolve(); first.resolve("blob:kai"); await Promise.resolve(); await Promise.resolve();
  assert.equal(item.img.src, "blob:georgia"); assert.deepEqual(requests, ["kai", "georgia"]); const timerCount = timers.length;
  state.playhead = 1.37; context.renderOverlayPreview(); assert.notEqual(item.node.style.width, initialWidth); assert.equal(timers.length, timerCount); assert.equal(item.img.src, "blob:georgia");
});

test("font and idle controls are discoverable before geometry settings and cache-busted with their evaluators", () => {
  assert.ok(html.indexOf('id="textFontControls"') < html.indexOf('id="overlayFields"')); assert.ok(html.indexOf('id="animationEditor"') < html.indexOf('id="overlayFields"'));
  for (const name of ["editor.js", "editor_animation_ui.js", "editor_animations.js", "editor_transition_effects.js", "editor.css"]) {
    const escapedName = name.replace(/\./g, "\\.");
    assert.match(html, new RegExp(`${escapedName}\\?v=[a-z0-9-]+[\"]`), `${name} has a versioned asset URL`);
  }
  assert.match(html, /讀取執行 Studio 的電腦已安裝的字型/); assert.match(html, /重新讀取字型/);
  assert.match(source, /textFontsReady = capabilities\.text_fonts === true/); assert.match(source, /textIdleReady = capabilities\.text_idle_animations === true/);
});
