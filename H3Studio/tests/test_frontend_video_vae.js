// Run with: node --test H3Studio/tests/test_frontend_video_vae.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ShortFilmPersistence = require("../static/shortfilm_persistence.js");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");

function fixture() {
  const elements = new Map();
  const storage = new Map();
  const serverRevisions = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: "", checked: false, disabled: false, textContent: "", listeners: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} }, scrollIntoView() {},
      addEventListener(name, handler) { this.listeners[name] = handler; },
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    $: element, structuredClone, keyframePrepareVersion: 0, engineModelInventory: {}, videoModelSupported: false,
    shortFilmProjects: [], activeShortFilmId: "", shortFilmSaveTimer: null, clearTimeout,
    shortFilmSaveCoordinator: null, shortFilmRecoveries: new Map(), ShortFilmPersistence,
    localStorage: { getItem(key) { return storage.get(key) ?? null; }, setItem(key, value) { storage.set(key, value); }, removeItem(key) { storage.delete(key); } },
    activeReferences: () => [], escapeHtml: value => value, uid: () => "id",
    updateSummary() {}, toast() {}, renderLoraPanels() {}, renderReplacement() {},
    renderKeyframePreview() {}, renderSymbolLoop() {}, renderContinuation() {},
    renderMgAnimation() {}, renderReferences() {}, renderStoryboards() {},
    renderShortFilmAssets() {}, renderShortFilmScenes() {}, renderShortFilmSummary() {},
    async loadShortFilmJobs() {},
    setMode(mode) { context.state.mode = mode; },
    async loadJobRecipe() { return context.recipe; },
    async api(url, options) {
      context.savedProject = JSON.parse(options.body);
      const project = context.savedProject, revision = serverRevisions.get(project.id) ?? 0;
      assert.equal(project.revision, revision, "The real save coordinator submits the current server revision");
      serverRevisions.set(project.id, revision + 1);
      return { ...structuredClone(project), revision: revision + 1 };
    },
  });
  const start = source.indexOf("const defaultState =");
  vm.runInContext(source.slice(start, source.indexOf("\n};", start) + 3) + "\nglobalThis.defaultState = defaultState;", context);
  context.state = structuredClone(context.defaultState);
  for (const name of [
    "currentSettings", "persistForm", "restoreForm", "saveState", "collectPayload", "recipeAsset", "originalGenerationRequest", "applyJobRecipe",
    "videoVaeBackendIsStale", "videoVaeHint", "syncVideoVaeHints", "activeShortFilmProject", "renderShortFilmWorkspace", "saveShortFilmProject",
    "scheduleShortFilmSave", "shortFilmModelSaveError", "getShortFilmSaveCoordinator", "renderShortFilmSaveStatus",
    "shortFilmFlatten", "shortFilmAliasMentioned", "shortFilmShotAssetSelection", "shortFilmAssetShotIds", "invalidateShortFilmRenders", "updateShortFilmGenerationField",
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

test("short-film reload retains an unknown model identity rather than selecting H3", () => {
  const {context:c,element}=fixture();
  c.shortFilmProjects=[{id:'unknown-project',video_model:'future_video_model'}];c.activeShortFilmId='unknown-project';
  const select=element('#sfVideoModel');select.options=[{value:'h3'},{value:'fasth3_omni_nvfp4'}];
  let value='h3';Object.defineProperty(select,'value',{get:()=>value,set:next=>{value=select.options.some(option=>option.value===String(next))?String(next):'';}});
  select.add=option=>select.options.push(option);c.Option=class {constructor(text,value){this.textContent=text;this.value=value;}};
  c.renderShortFilmWorkspace();
  assert.equal(select.value,'future_video_model');assert.match(select.options.at(-1).textContent,/不支援/);
  assert.equal(c.shortFilmProjects[0].video_model,'future_video_model');
});

test("unsupported hosts cannot erase Fast or unknown short-film selections through autosave", async () => {
  for(const selection of ['fasth3_omni_nvfp4','future_video_model']){
    const {context:c,storage}=fixture();let calls=0;c.api=async()=>{calls++;return {id:'project',revision:2};};
    c.shortFilmProjects=[{id:'project',revision:1,title:'kept',video_model:selection,scenes:[]}];c.activeShortFilmId='project';
    await assert.rejects(c.saveShortFilmProject(),/草稿|原選擇/);assert.equal(calls,0);
    const saved=JSON.parse(storage.get(ShortFilmPersistence.PREFIX+'project'));assert.equal(saved.project.video_model,selection);
    c.shortFilmSaveCoordinator=null;
    const recovered=c.getShortFilmSaveCoordinator().restore({id:'project',revision:1,title:'server H3',scenes:[]});
    assert.equal(recovered.project.video_model,selection);assert.equal(recovered.restored,true);
  }
});

test("short-film save verifies semantic model identity before clearing the crash draft", async () => {
  const {context:c,storage}=fixture();c.videoModelSupported=true;let calls=0;
  c.shortFilmProjects=[{id:'project',revision:1,title:'kept',video_model:'fasth3_omni_nvfp4',scenes:[]}];c.activeShortFilmId='project';
  c.api=async()=>{calls++;return {id:'project',revision:2,title:'dropped Fast',scenes:[]};};
  await assert.rejects(c.saveShortFilmProject(),/未完整保存影片模型/);assert.equal(calls,1);
  assert.equal(c.getShortFilmSaveCoordinator().status('project').state,'error');
  assert.equal(JSON.parse(storage.get(ShortFilmPersistence.PREFIX+'project')).project.video_model,'fasth3_omni_nvfp4');
  assert.equal(c.shortFilmProjects[0].video_model,'fasth3_omni_nvfp4');assert.equal(c.shortFilmProjects[0].revision,1);
  const legacy=fixture();legacy.context.shortFilmProjects=[{id:'legacy',revision:1,title:'H3',video_model:'h3',scenes:[]}];legacy.context.activeShortFilmId='legacy';
  legacy.context.api=async()=>({id:'legacy',revision:2,title:'H3',scenes:[]});
  await legacy.context.saveShortFilmProject();assert.equal(legacy.storage.get(ShortFilmPersistence.PREFIX+'legacy'),undefined);
});

test("draft rescue copy also refuses unsupported model storage without discarding the original",async()=>{
  const {context:c,storage}=fixture();let calls=0;c.api=async()=>{calls++;return {};};
  c.shortFilmProjects=[{id:'project',revision:1,title:'kept',video_model:'fasth3_omni_nvfp4',scenes:[]}];c.activeShortFilmId='project';
  c.shortFilmRecovering=false;c.shortFilmBatchRunning=false;c.setShortFilmBatchEditing=()=>{};
  await assert.rejects(c.saveShortFilmProject());
  const start=source.indexOf('async function recoverShortFilmDraft(');vm.runInContext(source.slice(start,source.indexOf('\n}',start)+2),c);
  await assert.rejects(c.recoverShortFilmDraft(true),/草稿已保留/);
  assert.equal(calls,0);assert.equal(JSON.parse(storage.get(ShortFilmPersistence.PREFIX+'project')).project.video_model,'fasth3_omni_nvfp4');
});
