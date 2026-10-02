const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");

function fixture(connectionMode = "local") {
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
    connectionSettings: { mode: connectionMode },
    $: element, $$: () => modeButtons, Node: { TEXT_NODE: 3 },
    async api() { throw new Error("Unexpected API request"); },
  });
  for (const name of ["voiceModeLabel", "setVoiceMode", "renderVoiceStatus", "loadVoiceStatus"]) {
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
    models: {
      custom: { installed: true, label: "內建聲線 0.6B", description: "九種官方聲線" },
      design: { installed: false, label: "聲線設計 1.7B" },
      clone: { installed: true, label: "聲線複製 0.6B" },
    },
    ...changes,
  };
}

test("local voice stays ready and describes local execution", () => {
  const ui = fixture();
  ui.render(status());
  assert.equal(ui.element("#generateVoice").disabled, false);
  assert.match(ui.element("#voiceModelTitle").textContent, /內建聲線 0\.6B已可使用/);
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
  assert.match(ui.element("#voiceModelDetail").textContent, /遠端主機已安裝 2\/3/);
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
  assert.match(ui.element("#voiceModelDetail").textContent, /GPU 主機管理者.*內建聲線 0\.6B/);
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
  ui.render(legacy);
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
  assert.match(ui.element("#voiceModelTitle").textContent, /聲線複製 0\.6B已可使用/);
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
  assert.match(html, /app\.js\?v=20261002-remote-voice/);
});
