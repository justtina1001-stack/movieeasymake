(function (root) {
  "use strict";
  function currentSection(path, search) {
    if (path.startsWith("/editor")) return "editor";
    if (path.startsWith("/scripts")) return "scripts";
    if (path.startsWith("/images")) return "images";
    const params = new URLSearchParams(search);
    if (["voice", "music", "sfx"].includes(params.get("tool"))) return params.get("tool");
    return params.get("workspace") === "shortfilm" ? "shortfilm" : "quick";
  }
  const destinations = [
    ["scripts", "腳本", "/scripts"], ["images", "圖片", "/images"],
    ["quick", "影片", "/?workspace=quick"], ["shortfilm", "短片", "/?workspace=shortfilm"],
    ["editor", "剪輯", "/editor"], ["voice", "語音", "/?tool=voice"], ["music", "音樂", "/?tool=music"], ["sfx", "音效", "/?tool=sfx"],
  ];
  function setupNavigation(doc) {
    const header = doc.querySelector("body > header");
    if (!header || header.querySelector(".studio-global-nav")) return;
    header.classList.add("workspace-header");
    const nav = doc.createElement("nav");
    nav.className = "studio-global-nav"; nav.setAttribute("aria-label", "創作工作室");
    let active = currentSection(root.location.pathname, root.location.search);
    if (["quick", "shortfilm"].includes(active) && doc.getElementById("quickWorkspace")?.classList.contains("hidden")) active = "shortfilm";
    for (const [key, label, href] of destinations) {
      const link = doc.createElement("a"); link.href = href; link.textContent = label; link.dataset.studioSection = key;
      if (key === active) link.setAttribute("aria-current", "page");
      const triggerId = { voice: "openVoiceStudio", music: "openMusicStudio", sfx: "openSfxStudio" }[key];
      if (triggerId && doc.getElementById(triggerId)) {
        link.setAttribute("aria-haspopup", "dialog");
        link.addEventListener("click", event => {
          event.preventDefault(); doc.getElementById(triggerId).click();
        });
      }
      nav.append(link);
    }
    header.append(nav);
    doc.addEventListener("click", event => {
      const button = event.target.closest("[data-workspace]");
      if (!button) return;
      for (const link of nav.querySelectorAll("[data-studio-section]")) {
        if (link.dataset.studioSection === button.dataset.workspace) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
    });
    const params = new URLSearchParams(root.location.search), tool = params.get("tool");
    if (["voice", "music", "sfx"].includes(tool)) {
      const trigger = doc.getElementById({ voice: "openVoiceStudio", music: "openMusicStudio", sfx: "openSfxStudio" }[tool]);
      if (trigger) root.setTimeout(() => trigger.click(), 0);
    }
  }
  function installModalFocus(doc) {
    const dialogs = [...doc.querySelectorAll(".modal[role=dialog]")];
    if (!dialogs.length) return;
    let open = null, opener = null;
    const background = new Map();
    const visible = element => element && !element.hidden && !element.closest(".hidden") && element.getClientRects().length > 0;
    const focusables = modal => [...modal.querySelectorAll("a[href],button,input,textarea,select,summary,[tabindex]")]
      .filter(element => !element.disabled && element.tabIndex >= 0 && visible(element) && !element.closest("[inert]"));
    function synchronize() {
      const next = dialogs.filter(visible).at(-1) || null;
      if (next === open) return;
      for (const [element, inert] of background) element.inert = inert;
      background.clear();
      if (!open && next) opener = doc.activeElement;
      const previous = open; open = next;
      if (open) {
        for (const sibling of doc.body.children) if (sibling !== open && !sibling.contains(open) && !["SCRIPT", "LINK", "STYLE"].includes(sibling.tagName)) {
          background.set(sibling, sibling.inert); sibling.inert = true;
        }
        const target = focusables(open)[0] || open;
        if (target === open) open.setAttribute("tabindex", "-1");
        if (!open.contains(doc.activeElement)) target.focus({ preventScroll: true });
      } else if (previous && opener?.isConnected && visible(opener)) {
        opener.focus({ preventScroll: true }); opener = null;
      }
    }
    doc.addEventListener("keydown", event => {
      if (!open) return;
      // A nested native dialog owns its focus loop and Escape action.
      if (open.querySelector("dialog[open]")) return;
      if (event.key === "Tab") {
        const items = focusables(open), first = items[0] || open, last = items.at(-1) || open;
        if (!items.length || !open.contains(doc.activeElement) || (!event.shiftKey && doc.activeElement === last) || (event.shiftKey && doc.activeElement === first)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      } else if (event.key === "Escape") {
        const close = open.querySelector(".modal-close,button[aria-label^='關閉']");
        if (close) { event.preventDefault(); event.stopImmediatePropagation(); close.click(); }
      }
    }, true);
    doc.addEventListener("focusin", () => {
      if (open && !open.contains(doc.activeElement)) (focusables(open)[0] || open).focus({ preventScroll: true });
    });
    for (const modal of dialogs) new MutationObserver(synchronize).observe(modal, { attributes: true, attributeFilter: ["class", "hidden"] });
    synchronize();
  }
  function setupHome(doc) {
    if (!doc.getElementById("quickWorkspace")) return;
    doc.body.classList.add("studio-home");
    const prompt = doc.getElementById("promptPanelTitle")?.closest("section"), column = doc.querySelector("#quickWorkspace .editor-column");
    if (prompt && column && prompt.parentElement === column) {
      column.prepend(prompt);
      const heading = prompt.querySelector(".step-number"); if (heading) heading.textContent = "02";
      const settingsHeading = column.querySelector(".settings-panel .step-number"); if (settingsHeading) settingsHeading.textContent = "04";
      const settings = column.querySelector(".settings-panel"), storyboard = column.querySelector(".storyboard-panel");
      if (settings && storyboard) column.insertBefore(settings, storyboard);
    }
    const status = doc.querySelector(".engine-status");
    if (status) {
      const menu = doc.createElement("details"); menu.className = "studio-settings-menu";
      const summary = doc.createElement("summary"); summary.textContent = "工具與設定"; menu.append(summary);
      const actions = doc.createElement("div"); actions.className = "studio-settings-actions";
      for (const id of ["openPromptGuide", "startEngine", "openModelUpdates", "openConnectionSettings", "openGatewaySettings"]) {
        const button = doc.getElementById(id); if (button) actions.append(button);
      }
      menu.append(actions); status.append(menu);
      actions.addEventListener("click", event => { if (event.target.closest("button")) { menu.open = false; summary.focus(); } });
      doc.addEventListener("click", event => { if (!menu.contains(event.target)) menu.open = false; });
    }
  }
  function installFieldCounts(doc) {
    const selector = "[data-sf-asset-field=description],[data-sf-shot-field=action],[data-sf-shot-field=dialogue]";
    let serial = 0;
    function count(field) {
      const limit = field.dataset.sfShotField === "action" ? 5000 : field.dataset.sfShotField === "dialogue" ? 1800 : 1200;
      let hint = doc.getElementById(field.dataset.fieldCountId || "");
      if (!hint) {
        hint = doc.createElement("small"); hint.className = "field-help studio-field-count";
        hint.id = `sf-field-count-${++serial}`; field.dataset.fieldCountId = hint.id;
        const labels = (field.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
        field.setAttribute("aria-describedby", [...new Set([...labels, hint.id])].join(" "));
        field.insertAdjacentElement("afterend", hint);
      }
      const length = Array.from(field.value || "").length, message = `${length} / ${limit} 字${length > limit ? " · 超出上限，原文保留，請縮短此欄位" : ""}`;
      if (hint.textContent !== message) hint.textContent = message;
      hint.classList.toggle("field-limit-warning", length > limit);
    }
    for (const id of ["shortfilmAssetList", "shortfilmSceneList"]) {
      const container = doc.getElementById(id); if (!container) continue;
      const update = () => container.querySelectorAll(selector).forEach(count);
      container.addEventListener("input", event => { if (event.target.matches(selector)) count(event.target); });
      new MutationObserver(update).observe(container, { childList: true, subtree: true }); update();
    }
  }
  function boot() { setupNavigation(root.document); setupHome(root.document); installModalFocus(root.document); installFieldCounts(root.document); }
  root.H3WorkspaceUI = { currentSection, setupNavigation, installModalFocus };
  if (typeof module === "object" && module.exports) module.exports = root.H3WorkspaceUI;
  if (root.document) {
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", boot, { once: true });
    else boot();
  }
})(typeof window !== "undefined" ? window : globalThis);
