// Run with: node --test H3Studio/tests/test_frontend_video_vae.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");

function fixture() {
  const elements = new Map();
  const storage = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: "", checked: false, disabled: false, textContent: "", listeners: {},
      classList: { add() {}, remove() {}, toggle() {} }, scrollIntoView() {},
      addEventListener(name, handler) { this.listeners[name] = handler; },
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    $: element, structuredClone, keyframePrepareVersion: 0, engineModelInventory: {},
    shortFilmProjects: [], activeShortFilmId: "", shortFilmSaveTimer: null, clearTimeout,
    localStorage: { setItem(key, value) { storage.set(key, value); } },
    activeReferences: () => [], escapeHtml: value => value, uid: () => "id",
    updateSummary() {}, toast() {}, renderLoraPanels() {}, renderReplacement() {},
    renderKeyframePreview() {}, renderSymbolLoop() {}, renderContinuation() {},
    renderMgAnimation() {}, renderReferences() {}, renderStoryboards() {},
    renderShortFilmAssets() {}, renderShortFilmScenes() {}, renderShortFilmSummary() {},
    scheduleShortFilmSave() {}, async loadShortFilmJobs() {},
    setMode(mode) { context.state.mode = mode; },
    async loadJobRecipe() { return context.recipe; },
    async api(url, options) {
      context.savedProject = JSON.parse(options.body);
      return structuredClone(context.savedProject);
    },
  });
  const start = source.indexOf("const defaultState =");
  vm.runInContext(source.slice(start, source.indexOf("\n};", start) + 3) + "\nglobalThis.defaultState = defaultState;", context);
  context.state = structuredClone(context.defaultState);
  for (const name of [
    "currentSettings", "persistForm", "restoreForm", "saveState", "collectPayload", "recipeAsset", "applyJobRecipe",
    "videoVaeBackendIsStale", "videoVaeHint", "syncVideoVaeHints", "activeShortFilmProject", "renderShortFilmWorkspace", "saveShortFilmProject",
  ]) {
    const index = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(index, -1, `${name} exists`);
    vm.runInContext(source.slice(index, source.indexOf("\n}", index) + 2), context);
  }
  return { context, element, storage };
}

test("both video workspaces offer the same accessible VAE choices", () => {
  for (const id of ["videoVae", "sfVideoVae"]) {
    const select = html.match(new RegExp(`<select id="${id}" aria-describedby="${id}Hint">([\\s\\S]*?)</select>`));
    assert.ok(select, id);
    assert.deepEqual([...select[1].matchAll(/value="([^"]+)"/g)].map(match => match[1]), ["auto", "int8", "fp16"]);
  }
});

test("new and legacy drafts choose auto and explicit VAE choices survive a saved draft and request", () => {
  const { context: c, element, storage } = fixture();
  c.restoreForm();
  assert.equal(element("#videoVae").value, "auto");
  c.state.form = { quality_mode: "native" };
  element("#videoVae").value = "int8";
  c.restoreForm();
  assert.equal(element("#videoVae").value, "auto");
  for (const selected of ["int8", "fp16", "auto"]) {
    element("#videoVae").value = selected;
    c.persistForm();
    c.state = JSON.parse(storage.get("h3studio-state-v1"));
    element("#videoVae").value = "";
    c.restoreForm();
    assert.equal(element("#videoVae").value, selected);
    assert.equal(c.collectPayload().video_vae, selected);
  }
});

test("applying a job preserves its VAE and a legacy recipe clears a previously explicit choice", async () => {
  const { context: c, element } = fixture();
  for (const selected of ["int8", "fp16", "auto", undefined]) {
    c.recipe = { request: { mode: "t2v", video_vae: selected }, assets: {} };
    await c.applyJobRecipe("job");
    assert.equal(element("#videoVae").value, selected || "auto");
    assert.equal(c.collectPayload().video_vae, selected || "auto");
  }
});

test("auto explains supported INT8, FP16 fallback and missing models without changing explicit selections", () => {
  const { context: c, element } = fixture();
  assert.match(c.videoVaeHint("auto"), /優先使用 INT8.*使用 FP16/);
  c.engineModelInventory = { video_vae_int8: true, video_vae_fp16: true, video_vae_int8_supported: true };
  assert.match(c.videoVaeHint("auto"), /目前引擎會優先使用 INT8/);
  c.engineModelInventory.video_vae_int8_supported = false;
  assert.match(c.videoVaeHint("auto"), /目前引擎會使用 FP16/);
  element("#videoVae").value = "int8";
  c.syncVideoVaeHints();
  assert.equal(element("#videoVae").value, "int8");
  assert.match(element("#videoVaeHint").textContent, /缺少 INT8.*不會自動換用 FP16/);
  c.engineModelInventory.video_vae_fp16 = false;
  assert.match(c.videoVaeHint("auto"), /沒有可用/);
  assert.match(c.videoVaeHint("fp16"), /缺少 FP16.*不會自動換用 INT8/);
});

test("shortfilm selector edits persist through project saving and project reload", async () => {
  const { context: c, element } = fixture();
  c.shortFilmProjects = [{ id: "project", title: "test", scenes: [], assets: [] }];
  c.activeShortFilmId = "project";
  c.renderShortFilmWorkspace();
  assert.equal(element("#sfVideoVae").value, "auto");
  const start = source.indexOf("  const shortFilmProjectFields =");
  const end = source.indexOf('  $("#sfExportFrames").addEventListener', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), c);
  for (const selected of ["fp16", "int8", "auto"]) {
    const select = element("#sfVideoVae");
    select.value = selected;
    select.listeners.input({ target: select });
    await c.saveShortFilmProject();
    assert.equal(c.savedProject.video_vae, selected);
    select.value = "";
    c.renderShortFilmWorkspace();
    assert.equal(select.value, selected);
  }
});

test("legacy backend disables both selectors with a restart hint but keeps selections and distinguishes offline state", () => {
  const { context: c, element } = fixture();
  c.shortFilmProjects = [{ id: "project", video_vae: "fp16" }];
  c.activeShortFilmId = "project";
  element("#videoVae").value = "int8";
  element("#sfVideoVae").value = "fp16";
  c.engineModelInventory = { video_vae: true };
  c.syncVideoVaeHints();
  for (const id of ["videoVae", "sfVideoVae"]) {
    assert.equal(element(`#${id}`).disabled, true);
    assert.match(element(`#${id}Hint`).textContent, /等目前工作完成後重新啟動 Studio.*只重新整理網頁不會更新後端/);
  }
  assert.equal(element("#videoVae").value, "int8");
  assert.equal(element("#sfVideoVae").value, "fp16");
  assert.equal(c.shortFilmProjects[0].video_vae, "fp16");
  for (const inventory of [{}, { video_vae: true, video_vae_int8: true, video_vae_fp16: true, video_vae_int8_supported: true }]) {
    c.engineModelInventory = inventory;
    c.syncVideoVaeHints();
    assert.equal(element("#videoVae").disabled, false);
    assert.equal(element("#sfVideoVae").disabled, false);
    assert.doesNotMatch(element("#videoVaeHint").textContent, /待重啟/);
  }
});
