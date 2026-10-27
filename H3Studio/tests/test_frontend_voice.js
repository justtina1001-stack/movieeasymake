const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");

function fixture(connectionMode = "local", selectedModels = {}) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        textContent: "", className: "", disabled: false, value: "", dataset: {},
        firstChild: { nodeType: 3, textContent: "" }, attributes: new Map(),
        classList: {
          toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
          contains(name) { return classes.has(name); },
        },
        setAttribute(name, value) { this.attributes.set(name, value); },
        removeAttribute(name) { this.attributes.delete(name); },
        hasAttribute(name) { return this.attributes.has(name); },
      });
    }
    return elements.get(selector);
  }
  const modeButtons = ["custom", "design", "clone"].map(mode => {
    const button = element(`mode-${mode}`);
    button.dataset.voiceMode = mode;
    return button;
  });
  const context = vm.createContext({
    voiceMode: "custom", voiceStatusData: null,
    voiceModelSelection: { custom: "custom_1_7b", design: "design", clone: "clone_1_7b", ...selectedModels },
    voiceInstructDrafts: { custom: "", design: "溫暖的中文女聲", clone: "" },
    voiceReferenceAsset: null, voicePage: 1, voiceFocusedJobId: "", voiceTotalPages: 1, lastVoiceJobsSignature: "",
    connectionSettings: { mode: connectionMode },
    H3AudioWorkspace: { rememberJobs() {}, patchHistory(container, markup) { container.innerHTML = markup; } },
    $: element, $$: () => modeButtons, Node: { TEXT_NODE: 3 },
    toast() {}, randomVoiceSeed() { return "42"; },
    queueJobBadgeHtml() { return ""; }, queueJobProgressHtml() { return ""; },
    async api() { throw new Error("Unexpected API request"); },
  });
  for (const name of ["escapeHtml", "setButtonBusy", "voiceModeLabel", "voiceModelDefinition", "selectedVoiceModelId", "voiceModelSupportError", "voiceInstructSupported", "renderVoiceModelControls", "setVoiceModel", "setVoiceMode", "renderVoiceStatus", "loadVoiceStatus", "collectVoicePayload", "collectVoiceInstallPayload", "generateVoice", "voiceJobElapsed", "voiceJobModelLabel", "voiceJobModelRepo", "loadVoiceJobs"]) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, `${name} is present`);
    vm.runInContext(source.slice(start, source.indexOf("\n}", start) + 2), context);
  }
  return {
    context, element, modeButtons,
    render(data) { context.renderVoiceStatus(data); },
    hidden(selector) { return element(selector).classList.contains("hidden"); },
  };
}

function status(mode = "local", changes = {}) {
  return {
    connection_mode: mode, can_install: mode === "local", available: true,
    runtime_installed: true, active: false, error: "",
    capabilities: { model_selection: true, custom_voice_instruct: true },
    models: {
      custom: { installed: true, label: "內建聲線 0.6B", description: "九種官方聲線" },
      design: { installed: false, label: "聲線設計 1.7B" },
      clone: { installed: true, label: "聲線複製 0.6B" },
      custom_1_7b: { installed: true, label: "內建聲線 1.7B", mode: "custom", size: "1.7B", supports_instruct: true },
      clone_1_7b: { installed: true, label: "聲線複製 1.7B", mode: "clone", size: "1.7B", supports_instruct: false },
    },
    ...changes,
  };
}

test("local voice stays ready and describes local execution", () => {
  const ui = fixture();
  ui.render(status());
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.match(ui.element("#voiceModelTitle").textContent, /內建聲線 1\.7B已可使用/);
  assert.match(ui.element("#voiceDescription").textContent, /完全本機執行/);
  assert.match(ui.element("#voiceGenerationNote").textContent, /本機 GPU/);
  assert.equal(ui.element("#voiceEngineLabel").textContent, "LOCAL OPEN-WEIGHT VOICE");
  assert.equal(ui.hidden("#installVoiceModel"), true);
  assert.equal(ui.hidden("#cancelVoiceInstall"), true);
});

test("local missing models allow installation and installation exposes only its cancellation", () => {
  const ui = fixture();
  ui.render(status("local", { runtime_installed: false }));
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.equal(ui.hidden("#installVoiceModel"), false);
  assert.equal(ui.element("#installVoiceModel").disabled, false);
  assert.match(ui.element("#installVoiceModel").textContent, /建立語音環境/);
  ui.render(status("local", { runtime_installed: false, active: true, current: "下載語音模型" }));
  assert.equal(ui.element("#installVoiceModel").disabled, true);
  assert.equal(ui.hidden("#cancelVoiceInstall"), false);
  assert.equal(ui.element("#cancelVoiceInstall").disabled, false);
  assert.match(ui.element("#voiceInstallProgress").textContent, /背景繼續/);
});

test("remote ready models enable generation while local install and cancellation stay unavailable", () => {
  const ui = fixture("remote");
  ui.render(status("remote", { can_install: true }));
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.match(ui.element("#voiceModelTitle").textContent, /已可使用 · 遠端主機/);
  assert.match(ui.element("#voiceModelDetail").textContent, /遠端主機已安裝 4\/5/);
  assert.match(ui.element("#voiceDescription").textContent, /本機不需安裝語音模型/);
  assert.match(ui.element("#voiceGenerationNote").textContent, /自動回存.*參考音訊會上傳/);
  assert.equal(ui.element("#voiceEngineLabel").textContent, "REMOTE OPEN-WEIGHT VOICE");
  for (const selector of ["#installVoiceModel", "#cancelVoiceInstall"]) {
    assert.equal(ui.hidden(selector), true);
    assert.equal(ui.element(selector).disabled, true);
  }
});

test("remote missing models point to the GPU host manager instead of requesting local installation", () => {
  const ui = fixture("remote");
  ui.render(status("remote", { runtime_installed: false }));
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceModelTitle").textContent, /尚未安裝 · 遠端主機/);
  assert.match(ui.element("#voiceModelDetail").textContent, /GPU 主機管理者.*內建聲線 1\.7B/);
  assert.match(ui.element("#voiceModelDetail").textContent, /這台電腦不需安裝/);
  assert.equal(ui.hidden("#installVoiceModel"), true);
});

test("a remote host installation is informational and cannot be cancelled by the client", () => {
  const ui = fixture("remote");
  ui.render(status("remote", { runtime_installed: false, active: true, current: "正在下載權重" }));
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceModelTitle").textContent, /正在下載權重 · 遠端主機/);
  assert.match(ui.element("#voiceInstallProgress").textContent, /主機正在安裝/);
  assert.equal(ui.hidden("#cancelVoiceInstall"), true);
  assert.equal(ui.element("#cancelVoiceInstall").disabled, true);
});

test("unavailable status disables generation even if cached host model flags are installed", () => {
  for (const mode of ["local", "remote"]) {
    const ui = fixture(mode);
    ui.render(status(mode, { available: false, error: "主機無法連線，請檢查個人金鑰" }));
    assert.equal(ui.element("#generateVoice").disabled, true);
    assert.equal(ui.element("#installVoiceModel").disabled, true);
    assert.equal(ui.hidden("#installVoiceModel"), true);
    assert.match(ui.element("#voiceModelTitle").textContent, /暫時無法使用/);
    assert.equal(ui.element("#voiceModelDetail").textContent, "主機無法連線，請檢查個人金鑰");
    assert.match(ui.element("#voiceStatusDot").className, /error/);
  }
});

test("an old backend in remote mode cannot misrepresent its local models as remote models", () => {
  const ui = fixture("remote");
  const legacy = status();
  delete legacy.connection_mode;
  delete legacy.available;
  delete legacy.can_install;
  ui.render(legacy);
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.equal(ui.hidden("#installVoiceModel"), true);
  assert.match(ui.element("#voiceModelDetail").textContent, /更新並重新啟動 Studio/);
});

test("legacy local status remains usable without the new remote capability fields", () => {
  const ui = fixture();
  const legacy = status();
  delete legacy.connection_mode;
  delete legacy.available;
  delete legacy.can_install;
  delete legacy.capabilities;
  delete legacy.models.custom_1_7b;
  delete legacy.models.clone_1_7b;
  ui.render(legacy);
  assert.equal(ui.element("#generateVoice").disabled, true);
  ui.context.setVoiceModel("custom");
  assert.equal(ui.element("#generateVoice").disabled, false);
});

test("voice mode changes immediately reevaluate model readiness on the selected host", () => {
  const ui = fixture("remote");
  ui.render(status("remote"));
  ui.context.setVoiceMode("design");
  assert.equal(ui.context.voiceMode, "design");
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceModelDetail").textContent, /主機管理者.*聲線設計 1\.7B/);
  assert.equal(ui.hidden("#voiceInstructField"), false);
  assert.equal(ui.modeButtons[1].classList.contains("active"), true);
  ui.context.setVoiceMode("clone");
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.equal(ui.hidden("#voiceCloneFields"), false);
  assert.match(ui.element("#voiceModelTitle").textContent, /聲線複製 1\.7B已可使用/);
  assert.equal(ui.hidden("#installVoiceModel"), true);
});

test("status polling cannot enable the generate button during submission", () => {
  const ui = fixture("remote");
  ui.element("#generateVoice").setAttribute("aria-busy", "true");
  ui.render(status("remote"));
  assert.equal(ui.element("#generateVoice").disabled, true);
});

test("failed status loading clears stale readiness and keeps the failure visible across mode changes", async () => {
  const ui = fixture("remote");
  ui.render(status("remote"));
  ui.context.api = async () => { throw new Error("HTTP 503"); };
  await assert.rejects(ui.context.loadVoiceStatus(), /HTTP 503/);
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceModelDetail").textContent, /無法讀取遠端主機語音狀態：HTTP 503/);
  assert.equal(ui.hidden("#voiceInstallProgress"), false);
  assert.equal(ui.context.voiceStatusData.available, false);
  ui.context.setVoiceMode("clone");
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceInstallProgress").textContent, /HTTP 503/);
  ui.context.api = async () => status("remote");
  await ui.context.loadVoiceStatus();
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.equal(ui.hidden("#voiceInstallProgress"), true);
});

test("voice controls initially wait for readiness and load the updated frontend", () => {
  for (const id of ["installVoiceModel", "cancelVoiceInstall", "generateVoice"]) {
    assert.match(html, new RegExp(`<button[^>]+id="${id}"[^>]+disabled`));
  }
  for (const id of ["voiceEngineLabel", "voiceDescription", "voiceGenerationNote"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /<script\b[^>]*\bsrc="\/static\/app\.js\?v=[A-Za-z0-9][A-Za-z0-9._-]*"[^>]*>\s*<\/script>/);
  assert.match(html, /\/static\/app\.js\?v=20261027-fasth3/);
  assert.match(source, /const voiceModelSelection = \{ custom: "custom_1_7b", design: "design", clone: "clone_1_7b" \}/);
  const voiceSection = html.slice(html.indexOf('id="voiceModal"'), html.indexOf('id="toast"'));
  assert.match(voiceSection, /id="voiceModelChoice"/);
  assert.match(voiceSection, /value="custom_1_7b" selected/);
});

test("changing model checks that exact model installation and preserves choices by mode", () => {
  const ui = fixture();
  const data = status();
  data.models.custom_1_7b.installed = false;
  ui.render(data);
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#installVoiceModel").textContent, /1\.7B/);
  ui.context.setVoiceModel("custom");
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.match(ui.element("#voiceModelChoiceHelp").textContent, /0\.6B-CustomVoice/);
  ui.context.setVoiceMode("clone");
  assert.equal(ui.context.selectedVoiceModelId(), "clone_1_7b");
  ui.context.setVoiceModel("clone");
  ui.context.setVoiceMode("custom");
  assert.equal(ui.context.selectedVoiceModelId(), "custom");
  ui.context.setVoiceMode("clone");
  assert.equal(ui.context.selectedVoiceModelId(), "clone");
  ui.context.setVoiceModel("custom_1_7b");
  assert.equal(ui.context.selectedVoiceModelId(), "clone");
});

test("design stays fixed at 1.7B and explicitly requires a description", () => {
  const ui = fixture();
  ui.render(status());
  ui.context.setVoiceMode("design");
  assert.equal(ui.element("#voiceModelChoice").disabled, true);
  assert.equal(ui.element("#voiceInstruct").required, true);
  assert.equal(ui.element("#voiceInstruct").disabled, false);
  assert.equal(ui.context.collectVoicePayload().model_id, "design");
  ui.element("#voiceInstruct").value = " ";
  assert.throws(() => ui.context.collectVoicePayload(), /填寫聲線設計描述/);
});

test("custom 1.7B shows and passes an optional instruct; fast mode and clone omit it", () => {
  const ui = fixture();
  ui.render(status());
  assert.equal(ui.hidden("#voiceInstructField"), false);
  assert.equal(ui.element("#voiceInstruct").required, false);
  ui.element("#voiceInstruct").value = "  溫柔平靜，放慢語速  ";
  assert.equal(ui.context.collectVoicePayload().instruct, "溫柔平靜，放慢語速");
  ui.element("#voiceInstruct").value = "";
  assert.equal(ui.context.collectVoicePayload().instruct, "");
  ui.context.setVoiceModel("custom");
  ui.element("#voiceInstruct").value = "保留但不送出";
  assert.equal(ui.hidden("#voiceInstructField"), true);
  assert.equal(ui.element("#voiceInstruct").disabled, true);
  assert.equal(Object.hasOwn(ui.context.collectVoicePayload(), "instruct"), false);
  ui.context.setVoiceMode("clone");
  assert.equal(ui.hidden("#voiceInstructField"), true);
  assert.equal(Object.hasOwn(ui.context.collectVoicePayload(), "instruct"), false);
});

test("mode changes keep separate custom and design instruction drafts, including cleared drafts", () => {
  const ui = fixture();
  ui.render(status());
  ui.element("#voiceInstruct").value = "充滿喜悅，節奏明快";
  ui.context.setVoiceMode("design");
  assert.equal(ui.element("#voiceInstruct").value, "溫暖的中文女聲");
  ui.element("#voiceInstruct").value = "沉穩的中年男聲";
  ui.context.setVoiceMode("custom");
  assert.equal(ui.element("#voiceInstruct").value, "充滿喜悅，節奏明快");
  ui.context.setVoiceModel("custom");
  ui.context.setVoiceModel("custom_1_7b");
  assert.equal(ui.element("#voiceInstruct").value, "充滿喜悅，節奏明快");
  ui.context.setVoiceMode("design");
  assert.equal(ui.element("#voiceInstruct").value, "沉穩的中年男聲");
  ui.element("#voiceInstruct").value = "";
  ui.context.setVoiceMode("clone");
  ui.context.setVoiceMode("design");
  assert.equal(ui.element("#voiceInstruct").value, "");
});

test("old remote hosts keep the recommended selection blocked until manually selecting fast mode", () => {
  const ui = fixture("remote");
  const legacy = status("remote");
  delete legacy.capabilities;
  delete legacy.models.custom_1_7b;
  delete legacy.models.clone_1_7b;
  ui.render(legacy);
  assert.equal(ui.context.selectedVoiceModelId(), "custom_1_7b");
  assert.equal(ui.element("#generateVoice").disabled, true);
  assert.match(ui.element("#voiceModelDetail").textContent, /更新並重新啟動 Studio.*手動選擇 0\.6B/);
  assert.match(ui.element("#voiceModelChoice").innerHTML, /value="custom_1_7b" selected disabled/);
  assert.throws(() => ui.context.collectVoicePayload(), /主機尚未支援/);
  assert.throws(() => ui.context.collectVoiceInstallPayload(), /主機尚未支援/);
  ui.context.setVoiceModel("custom");
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.equal(ui.context.collectVoicePayload().model_id, "custom");
  assert.equal(Object.hasOwn(ui.context.collectVoicePayload(), "instruct"), false);
  ui.context.setVoiceMode("clone");
  assert.equal(ui.element("#generateVoice").disabled, true);
  ui.context.setVoiceModel("clone");
  assert.equal(ui.element("#generateVoice").disabled, false);
});

test("capabilities must be explicit true and the requested model must exist", () => {
  for (const mutate of [
    data => { data.capabilities.model_selection = "true"; },
    data => { data.capabilities.custom_voice_instruct = false; },
    data => { data.models.custom_1_7b.supports_instruct = false; },
    data => { delete data.models.custom_1_7b; },
  ]) {
    const ui = fixture("remote");
    const data = status("remote");
    mutate(data);
    ui.render(data);
    assert.equal(ui.element("#generateVoice").disabled, true);
    assert.equal(ui.hidden("#voiceInstructField"), true);
    assert.throws(() => ui.context.collectVoicePayload(), /主機尚未支援/);
  }
});

test("generation and installation payloads carry the exact selected model_id", async () => {
  const ui = fixture();
  ui.render(status());
  assert.equal(ui.context.collectVoiceInstallPayload().model_id, "custom_1_7b");
  ui.context.setVoiceMode("clone");
  assert.equal(ui.context.collectVoiceInstallPayload().mode, "clone");
  assert.equal(ui.context.collectVoiceInstallPayload().model_id, "clone_1_7b");
  ui.context.setVoiceMode("custom");
  ui.element("#voiceInstruct").value = "語氣充滿期待";
  const requests = [];
  ui.context.api = async (url, options) => {
    requests.push({ url, payload: JSON.parse(options.body) });
    return { id: "abc123456" };
  };
  ui.context.loadVoiceJobs = async () => {};
  await ui.context.generateVoice();
  assert.equal(requests[0].url, "/api/voice/jobs");
  assert.equal(requests[0].payload.model_id, "custom_1_7b");
  assert.equal(requests[0].payload.instruct, "語氣充滿期待");
  assert.match(source, /JSON\.stringify\(collectVoiceInstallPayload\(\)\)/);
});

test("history labels and recipes report the actual model and preserve legacy job sizes", async () => {
  const ui = fixture();
  assert.match(ui.context.voiceJobModelLabel({ mode: "custom" }), /內建聲線 0\.6B · custom$/);
  assert.match(ui.context.voiceJobModelLabel({ mode: "clone" }), /聲線複製 0\.6B · clone$/);
  assert.match(ui.context.voiceJobModelLabel({ mode: "design" }), /聲線設計 1\.7B · design$/);
  ui.context.api = async () => ({ page: 1, total_pages: 1, items: [
    { id: "new", mode: "custom", model_id: "custom_1_7b", text: "你好", status: "queued", seed: 42, instruct: "溫暖" },
    { id: "old", mode: "clone", text: "早安", status: "queued", seed: 43 },
  ] });
  await ui.context.loadVoiceJobs(true);
  const rendered = ui.element("#voiceJobList").innerHTML;
  assert.match(rendered, /內建聲線 1\.7B · custom_1_7b/);
  assert.match(rendered, /Qwen\/Qwen3-TTS-12Hz-1\.7B-CustomVoice/);
  assert.match(rendered, /聲線複製 0\.6B · clone/);
  assert.match(rendered, /Qwen\/Qwen3-TTS-12Hz-0\.6B-Base/);
});
