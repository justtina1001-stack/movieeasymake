// Run with: node --test H3Studio/tests/test_frontend_acceleration.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
const qualityModes = ["native", "turbo", "turbo_fast", "turbo_quality", "turbo_audio", "turbo_ref_quality", "turbo_sla", "sparse_experimental"];

function fixture() {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, { value: "", checked: false, disabled: false, textContent: "", options: [] });
    return elements.get(selector);
  };
  element("#qualityMode").value = "native";
  element("#qualityMode").options = qualityModes.map(value => ({ value, disabled: false }));
  element("#steps").value = "20";
  element("#scheduler").value = "simple";
  const context = vm.createContext({
    $: element, state: { mode: "fl2va", customLoras: [] }, engineModelInventory: {},
    project: null, structuredClone, renderLoraPanels() {},
  });
  vm.runInContext("function activeShortFilmProject() { return project; }", context);
  for (const name of ["flOnlyQualityModes", "refOnlyQualityModes"]) {
    vm.runInContext(source.match(new RegExp(`^const ${name} = .+;$`, "m"))[0], context);
  }
  for (const name of [
    "currentSettings", "restoreForm", "isReferenceMode", "qualityModeCompatible", "turboProfile", "syncQualityMode",
    "capabilityNote", "accelerationAvailabilityNote", "memoryOptimizationHint", "shortFilmAliasMentioned",
    "shortFilmShotAssetSelection", "shortFilmShotUsesReferenceMode", "syncShortFilmAcceleration",
  ]) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `${name} exists`);
    const end = source.indexOf("\n}", start) + 2;
    vm.runInContext(source.slice(start, end), context);
  }
  return { context, element, run: expression => vm.runInContext(expression, context) };
}

test("both workspaces expose all quality choices and independent accessible memory controls", () => {
  for (const id of ["qualityMode", "sfQuality"]) {
    const select = html.match(new RegExp(`<select id="${id}"[^>]*>([\\s\\S]*?)</select>`))[1];
    for (const value of qualityModes) assert.ok(select.includes(`value="${value}"`), `${id}: ${value}`);
  }
  for (const id of ["memoryOptimization", "sfMemoryOptimization"]) {
    assert.match(html, new RegExp(`id="${id}" type="checkbox" checked aria-describedby="${id}Hint"`));
  }
});

test("fresh forms enable memory but old drafts without it retain previous behavior", () => {
  const { context, element, run } = fixture();
  run("restoreForm()");
  assert.equal(element("#memoryOptimization").checked, true);
  context.state.form = { quality_mode: "native" };
  run("restoreForm()");
  assert.equal(element("#memoryOptimization").checked, false);
  context.state.form.memory_optimization = true;
  run("restoreForm()");
  assert.equal(element("#memoryOptimization").checked, true);
  assert.equal(run("currentSettings().memory_optimization"), true);
  element("#memoryOptimization").checked = false;
  assert.equal(run("currentSettings().memory_optimization"), false);
  assert.match(source, /\$\("#memoryOptimization"\)\.checked = raw\.memory_optimization === true;/);
});

test("SLA disables conflicting memory patch and keeps exact selected mode", () => {
  const { element, run } = fixture();
  element("#qualityMode").value = "turbo_sla";
  element("#memoryOptimization").checked = true;
  run("syncQualityMode(1344, 768)");
  assert.equal(element("#memoryOptimization").checked, false);
  assert.equal(element("#memoryOptimization").disabled, true);
  assert.equal(run("currentSettings().memory_optimization"), false);
  assert.equal(element("#qualityMode").value, "turbo_sla");
  assert.equal(element("#steps").value, 4);
  assert.match(element("#memoryOptimizationHint").textContent, /不可併用/);
  assert.match(element("#qualityModeHint").textContent, /keep 15%/);
  assert.match(element("#qualityModeHint").textContent, /尚未確認/);
  element("#qualityMode").value = "native";
  run("syncQualityMode(1344, 768)");
  assert.equal(element("#memoryOptimization").disabled, false);
});

test("missing capability is explicit and does not silently select another mode", () => {
  const { context, element, run } = fixture();
  context.engineModelInventory = { turbo_fl2v_768_sla: true, h3_sla_attention: false };
  element("#qualityMode").value = "turbo_sla";
  run("syncQualityMode(1344, 768)");
  assert.match(element("#qualityModeHint").textContent, /目前引擎缺少原生 SLA/);
  assert.match(element("#qualityModeHint").textContent, /請更新目前運算引擎的 ComfyUI 核心至 0\.35\.0 以上及配套依賴，再重啟/);
  assert.match(element("#qualityModeHint").textContent, /模型更新只會補模型／自訂節點/);
  assert.match(element("#qualityModeHint").textContent, /不會自動改用/);
  assert.equal(element("#qualityMode").value, "turbo_sla");
  context.state.mode = "replace";
  run("syncQualityMode(864, 480)");
  assert.match(element("#qualityModeHint").textContent, /不相容/);
  assert.equal(element("#qualityMode").value, "turbo_sla");
  assert.equal(element("#qualityMode").options.find(item => item.value === "turbo_sla").disabled, true);
});

test("quality versions show correct step counts and family restrictions", () => {
  const { context, element, run } = fixture();
  for (const [mode, steps, key] of [
    ["turbo_audio", 4, "fl2v_768_audio_v12"],
    ["turbo_sla", 4, "fl2v_768_sla"],
    ["turbo_ref_quality", 8, "ref2v_768_quality_v10"],
  ]) {
    assert.equal(run(`turboProfile(1344, 768, '${mode}').steps`), steps);
    assert.equal(run(`turboProfile(1344, 768, '${mode}').key`), key);
  }
  assert.equal(run("qualityModeCompatible('turbo_audio', true)"), false);
  assert.equal(run("qualityModeCompatible('turbo_ref_quality', false)"), false);
  assert.equal(run("qualityModeCompatible('turbo_ref_quality', true)"), true);
  context.state.mode = "replace";
  element("#qualityMode").value = "turbo_ref_quality";
  run("syncQualityMode(864, 480)");
  assert.equal(element("#steps").value, 8);
  assert.equal(element("#refImageSize").value, "match");
  assert.match(element("#qualityModeHint").textContent, /並非比現有 4 步更快/);
});

test("shortfilm memory persistence and incompatibility notices match project settings", () => {
  const { context, element, run } = fixture();
  context.project = { quality_mode: "turbo_sla", memory_optimization: true, assets: [], scenes: [] };
  element("#sfMemoryOptimization").checked = true;
  run("syncShortFilmAcceleration()");
  assert.equal(context.project.memory_optimization, false);
  assert.equal(element("#sfMemoryOptimization").disabled, true);
  assert.equal(element("#sfMemoryOptimization").checked, false);
  assert.match(element("#sfQualityHint").textContent, /不相容的鏡頭會改用 Turbo 穩定版/);
  assert.match(element("#sfMemoryOptimizationHint").textContent, /不可併用/);
  context.project.quality_mode = "native";
  run("syncShortFilmAcceleration()");
  assert.equal(element("#sfMemoryOptimization").disabled, false);
  assert.match(source, /title: "未命名短片", memory_optimization: true/);
  assert.match(source, /\$\("#sfMemoryOptimization"\)\.checked = project\.memory_optimization === true;/);
  assert.match(source, /project\.memory_optimization = event\.target\.checked;/);
});

test("shortfilm mode classification accounts for aliases, storyboard and continuation", () => {
  const { run } = fixture();
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {})"), false);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {storyboard_asset_id: 'story'})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {continue_previous: true})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [{id: 'actor', alias: '小明', image_asset_ids: ['img']}], scenes: []}, {action: '小明走進來'})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [{id: 'actor', alias: '小明', image_asset_ids: []}], scenes: []}, {action: '小明走進來'})"), false);
});
