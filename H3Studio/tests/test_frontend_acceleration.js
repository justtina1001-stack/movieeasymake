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
    "currentSettings", "restoreForm", "dimensions", "isReferenceMode", "qualityModeCompatible", "referenceTurboResolutionWarning", "turboProfile", "syncQualityMode",
    "capabilityNote", "accelerationAvailabilityNote", "memoryOptimizationHint", "shortFilmAliasMentioned",
    "shortFilmShotAssetSelection", "shortFilmShotUsesReferenceMode", "shortFilmFlatten", "shortFilmShotReferenceUsage", "shortFilmWarnings", "syncShortFilmAcceleration",
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
    assert.match(select, /value="turbo">Turbo 快速預覽/);
    assert.doesNotMatch(select, /Turbo 穩定版/);
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
  assert.match(element("#sfQualityHint").textContent, /不相容的鏡頭會改用 Turbo 快速預覽/);
  assert.match(element("#sfMemoryOptimizationHint").textContent, /不可併用/);
  context.project.quality_mode = "native";
  run("syncShortFilmAcceleration()");
  assert.equal(element("#sfMemoryOptimization").disabled, false);
  assert.match(source, /title: "未命名短片", memory_optimization: true/);
  assert.match(source, /\$\("#sfMemoryOptimization"\)\.checked = project\.memory_optimization === true;/);
  assert.match(source, /updateShortFilmGenerationField\(project, project, "memory_optimization", event\.target\.checked\);/);
});

test("shortfilm mode classification accounts for aliases, storyboard and continuation", () => {
  const { run } = fixture();
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {})"), false);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {storyboard_asset_id: 'story'})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [], scenes: []}, {continue_previous: true})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [{id: 'actor', alias: '小明', image_asset_ids: ['img']}], scenes: []}, {action: '小明走進來'})"), true);
  assert.equal(run("shortFilmShotUsesReferenceMode({assets: [{id: 'actor', alias: '小明', image_asset_ids: []}], scenes: []}, {action: '小明走進來'})"), false);
});

test("Ref4 warns above the author's example area and retains the chosen format and seed", () => {
  const { context, element, run } = fixture();
  context.state.mode = "r2v";
  element("#qualityMode").value = "turbo";
  element("#seed").value = "12345";
  for (const [megapixels, aspect, width, height, highArea] of [
    ["0.4", "16:9", 864, 480, false],
    ["0.7", "16:9", 1152, 640, true],
    ["0.9", "16:9", 1280, 736, true],
    ["0.98", "16:9", 1344, 768, true],
    ["0.98", "9:16", 768, 1344, true],
  ]) {
    element("#megapixels").value = megapixels;
    element("#aspectRatio").value = aspect;
    assert.deepEqual(Array.from(run("dimensions()")), [width, height]);
    run(`syncQualityMode(${width}, ${height})`);
    const hint = element("#qualityModeHint").textContent;
    assert.match(hint, /544p（約 0\.5MP）訓練/);
    assert.match(hint, /Euler、simple、Shift 12\/3/);
    assert.equal(hint.includes("高於作者 960 × 544 範例"), highArea);
    if (highArea) assert.ok(hint.includes(`${width} × ${height}`));
    assert.equal(element("#qualityMode").value, "turbo");
    assert.equal(element("#megapixels").value, megapixels);
    assert.equal(element("#aspectRatio").value, aspect);
    assert.equal(element("#seed").value, "12345");
  }
  assert.equal(run("referenceTurboResolutionWarning(960, 544)"), "");
  assert.equal(run("referenceTurboResolutionWarning(544, 960)"), "");
});

test("Ref4 resolution notice does not appear for Native, Ref8 or FL recipes", () => {
  const { context, element, run } = fixture();
  for (const [mode, quality] of [["r2v", "native"], ["r2v", "turbo_ref_quality"], ["t2v", "turbo"], ["t2v", "turbo_fast"]]) {
    context.state.mode = mode;
    element("#qualityMode").value = quality;
    run("syncQualityMode(1344, 768)");
    assert.doesNotMatch(element("#qualityModeHint").textContent, /960 × 544|Ref2VA v0\.1|544p（約/);
    assert.equal(element("#qualityMode").value, quality);
  }
});

test("Native hints identify res_multistep without overwriting advanced choices", () => {
  const { context, element, run } = fixture();
  element("#steps").value = "28";
  element("#scheduler").value = "beta";
  run("syncQualityMode(1344, 768)");
  assert.match(element("#qualityModeHint").textContent, /res_multistep/);
  assert.equal(element("#steps").value, "28");
  assert.equal(element("#scheduler").value, "beta");
  context.project = { quality_mode: "native" };
  run("syncShortFilmAcceleration()");
  assert.match(element("#sfQualityHint").textContent, /res_multistep、simple、20 步/);
});

test("shortfilm resolution notices follow each shot's effective Ref4 recipe", () => {
  const { context, run } = fixture();
  context.project = {
    quality_mode: "turbo", megapixels: 0.7, aspect_ratio: "9:16", target_duration: 10,
    assets: [{ id: "actor", alias: "小龍", image_asset_ids: ["image"] }],
    scenes: [{ title: "測試", shots: [
      { id: "ref", title: "參考鏡", action: "小龍走進畫面", duration: 5, seed: 123 },
      { id: "text", title: "文生鏡", action: "雨滴落下", duration: 5, seed: 456 },
    ] }],
  };
  const before = JSON.stringify(context.project);
  let warnings = Array.from(run("shortFilmWarnings(project)"));
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /參考鏡.*640 × 1152.*高於作者/);
  assert.equal(JSON.stringify(context.project), before);
  context.project.quality_mode = "turbo_fast";
  warnings = Array.from(run("shortFilmWarnings(project)"));
  assert.ok(warnings.some(text => /參考鏡.*高於作者/.test(text)));
  assert.ok(warnings.every(text => !text.includes("文生鏡")));
  for (const quality of ["native", "turbo_ref_quality"]) {
    context.project.quality_mode = quality;
    assert.ok(Array.from(run("shortFilmWarnings(project)")).every(text => !text.includes("960 × 544")));
  }
  context.project.quality_mode = "turbo";
  context.project.megapixels = 0.4;
  assert.equal(run("shortFilmWarnings(project).length"), 0);
});

test("shortfilm uses actual dimensions for recipe descriptions without changing project settings", () => {
  const { context, element, run } = fixture();
  context.project = { quality_mode: "turbo", megapixels: 0.98, aspect_ratio: "16:9", scenes: [], assets: [] };
  const before = JSON.stringify(context.project);
  run("syncShortFilmAcceleration()");
  assert.match(element("#sfQualityHint").textContent, /1344 × 768/);
  assert.match(element("#sfQualityHint").textContent, /Ref2VA v0\.1 4 步快速預覽/);
  assert.equal(JSON.stringify(context.project), before);
  context.project.aspect_ratio = "9:16";
  run("syncShortFilmAcceleration()");
  assert.doesNotMatch(element("#sfQualityHint").textContent, /針對 1344 × 768 訓練/);
  assert.deepEqual(Array.from(run("dimensions(0.7, '1:1')")), [832, 832]);
});
