/* Preset animation inspector; timing comes from the shared timeline, not CSS clocks. */
(function (root) {
  "use strict";
  const EFFECTS = [
    ["none", "無", "—"], ["fade", "淡化", "◐"], ["slide_left", "向左滑動", "←"],
    ["slide_right", "向右滑動", "→"], ["slide_up", "向上滑動", "↑"], ["slide_down", "向下滑動", "↓"],
    ["zoom_in", "放大進入", "⊕"], ["zoom_out", "縮小進入", "⊖"],
  ];
  const TRANSITIONS = [["none", "無", "—"], ["crossfade", "交叉淡化", "◒"], ["wipe_left", "向左擦除", "⇠"], ["wipe_right", "向右擦除", "⇢"]];
  const LABELS = { in: "入場", out: "退場", transition: "轉場" };
  const escape = text => String(text).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  function effectLabel(type, tab = "in") {
    if (tab === "out" && type === "zoom_in") return "縮小離開";
    if (tab === "out" && type === "zoom_out") return "放大離開";
    return (tab === "transition" ? TRANSITIONS : EFFECTS).find(item => item[0] === type)?.[1] || "無";
  }
  function mountAnimationEditor(host, callbacks = {}) {
    host.innerHTML = '<div class="animation-heading"><h3>動畫</h3><button type="button" class="button ghost small" data-animation-preview>▶ 預覽效果</button></div><div class="animation-tabs" role="tablist" aria-label="動畫類別">' + Object.entries(LABELS).map(([tab, label]) => `<button type="button" role="tab" data-animation-tab="${tab}" aria-selected="${tab === "in"}">${label}</button>`).join("") + '</div><div class="animation-presets" role="group" aria-label="動畫預設"></div><div class="animation-duration"><label class="field">動畫時長（秒）<input type="number" data-animation-duration min="0.05" max="5" step="0.05" value="0.6"></label><input type="range" data-animation-range min="0.05" max="5" step="0.05" value="0.6" aria-label="動畫時長"></div><p class="animation-note subtle"></p><div class="animation-support hidden" role="status"><span></span><button type="button" class="button ghost small" data-animation-recheck>重新檢查動畫功能</button></div>';
    const find = selector => host.querySelector(selector), all = selector => [...host.querySelectorAll(selector)];
    let tab = "in", model = {}, selection = null;
    function field() { return tab === "transition" ? "transition_out" : `animation_${tab}`; }
    function blocked() { return model.disabled || !(tab === "transition" ? model.transitionSupported && model.next : model.supported); }
    host.addEventListener("click", event => {
      const tabButton = event.target.closest("[data-animation-tab]");
      if (tabButton) { tab = tabButton.dataset.animationTab; render(model); return; }
      const preset = event.target.closest("[data-animation-preset]");
      if (preset && !blocked()) callbacks.onChoose?.(preset.dataset.animationPreset, tab);
      if (event.target.closest("[data-animation-preview]") && !blocked()) callbacks.onPreview?.(tab);
      if (event.target.closest("[data-animation-recheck]")) callbacks.onRecheck?.();
    });
    for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) {
      find(selector).addEventListener("change", event => { if (!blocked() && model.item?.[field()]) callbacks.onDuration?.(Number(event.target.value), tab); });
    }
    function render(next) {
      model = next || {};
      const key = model.item ? `${model.kind}:${model.item.id}` : null;
      if (selection !== key) { selection = key; tab = "in"; }
      host.classList.toggle("hidden", !model.item);
      if (!model.item) return;
      const isVideo = model.kind === "video" || model.item.kind === "video";
      if (!isVideo && tab === "transition") tab = "in";
      for (const button of all("[data-animation-tab]")) {
        const active = button.dataset.animationTab === tab;
        button.setAttribute("aria-selected", String(active)); button.classList.toggle("active", active);
        button.hidden = button.dataset.animationTab === "transition" && !isVideo;
      }
      const current = model.item[field()], disabled = Boolean(blocked());
      const presets = tab === "transition" ? TRANSITIONS : EFFECTS;
      find(".animation-presets").innerHTML = presets.map(([type, label, icon]) => `<button type="button" class="animation-preset ${(current?.type || "none") === type ? "active" : ""}" data-animation-preset="${type}" aria-pressed="${(current?.type || "none") === type}" aria-label="${LABELS[tab]}：${escape(effectLabel(type, tab))}" ${disabled ? "disabled" : ""}><span aria-hidden="true">${icon}</span><small>${escape(effectLabel(type, tab))}</small></button>`).join("");
      const other = model.item[tab === "in" ? "animation_out" : "animation_in"]?.duration || 0;
      const limit = tab === "transition" ? Math.min(5, model.length, model.next?.length || 0) : Math.min(5, Math.max(0, model.length - other));
      for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) {
        const node = find(selector); node.max = Math.max(0.001, limit); node.min = Math.min(0.05, limit);
        // Autosave/playback status renders must not erase an uncommitted edit.
        if (host.ownerDocument?.activeElement !== node) node.value = current?.duration || Math.min(0.6, limit);
        node.disabled = disabled || !current;
      }
      find("[data-animation-preview]").disabled = disabled || !current;
      find(".animation-note").textContent = tab === "transition" ? model.next ? `接至 ${model.next.name || "下一片段"}。效果跨越兩段交界，原本剪輯長度與音訊不變。` : "同一軌道需要前後相接的兩段影片。可拖曳片段接合，再套用轉場。" : `套用在片段${tab === "in" ? "開頭" : "結尾"}，可與位置關鍵幀搭配；原聲維持原本的音量與淡化設定。`;
      const unsupported = !(tab === "transition" ? model.transitionSupported : model.supported);
      find(".animation-support").classList.toggle("hidden", !unsupported);
      find(".animation-support span").textContent = "目前 Studio 尚未載入動畫更新。先儲存剪輯、關閉 Studio 啟動視窗，再啟動新版並重新檢查。";
      find("[data-animation-recheck]").disabled = Boolean(model.checking || model.disabled);
    }
    return { render, selectTab(value) { if (Object.hasOwn(LABELS, value)) { tab = value; render(model); } }, currentTab: () => tab };
  }
  const core = { EFFECTS, TRANSITIONS, effectLabel, mountAnimationEditor };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorAnimationUI = core;
})(typeof window !== "undefined" ? window : globalThis);
