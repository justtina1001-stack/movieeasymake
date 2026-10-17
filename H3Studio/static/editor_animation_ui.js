/* Preset animation inspector; timing comes from the shared timeline, not CSS clocks. */
(function (root) {
  "use strict";
  const EFFECTS = [
    ["none", "無", "—"], ["fade", "淡化", "◐"], ["slide_left", "向左滑動", "←"],
    ["slide_right", "向右滑動", "→"], ["slide_up", "向上滑動", "↑"], ["slide_down", "向下滑動", "↓"],
    ["zoom_in", "放大進入", "⊕"], ["zoom_out", "縮小進入", "⊖"],
  ];
  const REFERENCE_TRANSITION_TYPES = Object.freeze(["radial_zoom", "horizontal_blur"]);
  const TRANSITION_EFFECT_TYPES = Object.freeze(["white_flash", "blur_dissolve", "wave_distort", "rgb_glitch", ...REFERENCE_TRANSITION_TYPES]);
  const TRANSITIONS = [["none", "無", "—"], ["crossfade", "交叉淡化", "◒"], ["wipe_left", "向左擦除", "⇠"], ["wipe_right", "向右擦除", "⇢"],
    ["white_flash", "閃白", "✦"], ["blur_dissolve", "模糊溶接", "◌"], ["wave_distort", "波浪扭曲", "≈"], ["rgb_glitch", "RGB 故障", "▥"],
    ["radial_zoom", "放射", "⊛"], ["horizontal_blur", "橫向模糊", "↔"]];
  const IDLE_EFFECTS = [["none", "無", "—"], ["pulse", "呼吸縮放", "◉"], ["float", "輕柔浮動", "↕"], ["shake", "輕微抖動", "≈"], ["blink", "閃爍", "✧"]];
  const TRANSITION_CATEGORIES = [["all", "全部", 9], ["basic", "基礎", 3], ["motion", "動感", 6]];
  const LABELS = { in: "入場", out: "退場", idle: "待機", transition: "轉場" };
  const escape = text => String(text).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  function effectLabel(type, tab = "in") {
    if (tab === "out" && type === "zoom_in") return "縮小離開";
    if (tab === "out" && type === "zoom_out") return "放大離開";
    return (tab === "transition" ? TRANSITIONS : tab === "idle" ? IDLE_EFFECTS : EFFECTS).find(item => item[0] === type)?.[1] || "無";
  }
  function mountAnimationEditor(host, callbacks = {}) {
    host.innerHTML = '<div class="animation-heading"><h3>動畫</h3><button type="button" class="button ghost small" data-animation-preview>▶ 預覽效果</button></div><div class="animation-tabs" role="tablist" aria-label="動畫類別">' + Object.entries(LABELS).map(([tab, label]) => `<button type="button" role="tab" data-animation-tab="${tab}" aria-selected="${tab === "in"}">${label}</button>`).join("") + '</div><div class="animation-categories animation-tabs hidden" role="group" aria-label="轉場效果分類">' + TRANSITION_CATEGORIES.map(([category, label, count]) => `<button type="button" data-animation-category="${category}" aria-pressed="${category === "all"}">${label} ${count}</button>`).join("") + '</div><div class="animation-presets" role="group" aria-label="動畫預設"></div><div class="animation-duration"><label class="field">動畫時長（秒）<input type="number" data-animation-duration min="0.05" max="5" step="0.05" value="0.6"></label><input type="range" data-animation-range min="0.05" max="5" step="0.05" value="0.6" aria-label="動畫時長"></div><div class="animation-idle-controls hidden"><label class="field">速度（倍）<input type="number" data-animation-idle-speed min="0.25" max="4" step="0.05" value="1"></label><label class="field">強度（%）<input type="number" data-animation-idle-amount min="0" max="200" step="5" value="100"></label></div><p class="animation-note subtle"></p><button type="button" class="button secondary small full-width" data-animation-apply-track>套用同軌</button><p class="animation-batch-note subtle"></p><div class="animation-support hidden" role="status"><span></span><button type="button" class="button ghost small" data-animation-recheck>重新檢查動畫功能</button></div>';
    const find = selector => host.querySelector(selector), all = selector => [...host.querySelectorAll(selector)];
    let tab = "in", category = "all", model = {}, selection = null;
    function field() { return tab === "transition" ? "transition_out" : `animation_${tab}`; }
    function blocked() { return model.disabled || !(tab === "transition" ? model.transitionSupported && model.next : tab === "idle" ? model.idleSupported && model.item?.kind === "text" : model.supported); }
    function effectUnsupported(type) { return tab === "transition" && (REFERENCE_TRANSITION_TYPES.includes(type) ? model.referenceSupported !== true : TRANSITION_EFFECT_TYPES.includes(type) && model.effectsSupported !== true); }
    function controlsBlocked() { return blocked() || effectUnsupported(model.item?.[field()]?.type); }
    function batchBlocked() {
      if (!model.item || model.disabled || typeof callbacks.onApplyTrack !== "function") return true;
      if (tab === "transition") {
        const current = model.item.transition_out;
        const removing = !current || current.type === "none" || current.duration === 0;
        return model.transitionSupported !== true || !removing && (!model.next || effectUnsupported(current.type));
      }
      return tab === "idle" ? model.idleSupported !== true || model.item.kind !== "text" : model.supported !== true;
    }
    host.addEventListener("click", event => {
      const tabButton = event.target.closest("[data-animation-tab]");
      if (tabButton) { tab = tabButton.dataset.animationTab; render(model); return; }
      const categoryButton = event.target.closest("[data-animation-category]");
      if (categoryButton && tab === "transition" && TRANSITION_CATEGORIES.some(item => item[0] === categoryButton.dataset.animationCategory)) { category = categoryButton.dataset.animationCategory; render(model); return; }
      const preset = event.target.closest("[data-animation-preset]");
      if (preset && !blocked() && !effectUnsupported(preset.dataset.animationPreset)) callbacks.onChoose?.(preset.dataset.animationPreset, tab);
      if (event.target.closest("[data-animation-preview]") && !controlsBlocked()) callbacks.onPreview?.(tab);
      if (event.target.closest("[data-animation-apply-track]") && !batchBlocked()) callbacks.onApplyTrack(tab);
      if (event.target.closest("[data-animation-recheck]")) callbacks.onRecheck?.();
    });
    for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) {
      find(selector).addEventListener("change", event => { if (!controlsBlocked() && model.item?.[field()]) callbacks.onDuration?.(Number(event.target.value), tab); });
    }
    for (const [selector, field, factor] of [["[data-animation-idle-speed]", "speed", 1], ["[data-animation-idle-amount]", "amount", 100]]) find(selector).addEventListener("change", event => {
      if (tab === "idle" && !blocked() && model.item?.animation_idle) callbacks.onIdleChange?.(field, Number(event.target.value) / factor);
    });
    function render(next) {
      model = next || {};
      const key = model.item ? `${model.kind}:${model.item.id}` : null;
      if (selection !== key) { selection = key; tab = "in"; category = "all"; }
      host.classList.toggle("hidden", !model.item);
      if (!model.item) return;
      const isVideo = model.kind === "video" || model.item.kind === "video";
      const isText = model.item.kind === "text";
      if (!isVideo && tab === "transition") tab = "in";
      if (!isText && tab === "idle") tab = "in";
      find(".animation-heading h3").textContent = isText ? "文字動畫" : "動畫";
      for (const button of all("[data-animation-tab]")) {
        const active = button.dataset.animationTab === tab;
        button.setAttribute("aria-selected", String(active)); button.classList.toggle("active", active);
        button.hidden = button.dataset.animationTab === "transition" && !isVideo || button.dataset.animationTab === "idle" && !isText;
      }
      const current = model.item[field()], disabled = Boolean(blocked()), controlsDisabled = Boolean(controlsBlocked());
      const presets = tab === "transition" ? TRANSITIONS : tab === "idle" ? IDLE_EFFECTS : EFFECTS, currentType = (tab === "idle" ? current?.preset : current?.type) || "none";
      const visiblePresets = tab !== "transition" || category === "all" ? presets : presets.filter(([type]) => type === "none" || (category === "motion" ? TRANSITION_EFFECT_TYPES.includes(type) : !TRANSITION_EFFECT_TYPES.includes(type)));
      find(".animation-categories").classList.toggle("hidden", tab !== "transition");
      for (const button of all("[data-animation-category]")) { const active = button.dataset.animationCategory === category; button.setAttribute("aria-pressed", String(active)); button.classList.toggle("active", active); }
      find(".animation-presets").innerHTML = visiblePresets.map(([type, label, icon]) => `<button type="button" class="animation-preset ${currentType === type ? "active" : ""}" data-animation-preset="${type}" aria-pressed="${currentType === type}" aria-label="${LABELS[tab]}：${escape(effectLabel(type, tab))}" ${disabled || effectUnsupported(type) ? "disabled" : ""}><span aria-hidden="true">${icon}</span><small>${escape(effectLabel(type, tab))}</small></button>`).join("");
      find(".animation-duration").classList.toggle("hidden", tab === "idle");
      find(".animation-idle-controls").classList.toggle("hidden", tab !== "idle");
      const other = model.item[tab === "in" ? "animation_out" : "animation_in"]?.duration || 0;
      const limit = tab === "transition" ? Math.min(5, model.length, model.next?.length || 0) : Math.min(5, Math.max(0, model.length - other));
      for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) {
        const node = find(selector); node.max = Math.max(0.001, limit); node.min = Math.min(0.05, limit);
        // Autosave/playback status renders must not erase an uncommitted edit.
        if (host.ownerDocument?.activeElement !== node) node.value = current?.duration || Math.min(0.6, limit);
        node.disabled = controlsDisabled || !current || tab === "idle";
      }
      for (const [selector, field, factor] of [["[data-animation-idle-speed]", "speed", 1], ["[data-animation-idle-amount]", "amount", 100]]) { const node = find(selector); if (host.ownerDocument?.activeElement !== node) node.value = (current?.[field] ?? 1) * factor; node.disabled = disabled || tab !== "idle" || currentType === "none"; }
      find("[data-animation-preview]").disabled = controlsDisabled || !current || currentType === "none" || tab === "idle" && (current?.amount === 0 || model.length <= (model.item.animation_in?.duration || 0) + (model.item.animation_out?.duration || 0));
      const apply = find("[data-animation-apply-track]");
      apply.classList.toggle("animation-apply-track", true);
      apply.textContent = tab === "transition" ? model.kind === "video" ? "套用主軌全部交界" : "套用同軌全部交界" : model.kind === "video" ? "套用主軌全部影片" : "套用同軌同類圖層";
      apply.disabled = Boolean(batchBlocked());
      find(".animation-batch-note").textContent = tab === "transition" ? "只處理同軌前後相接的影片；短片段會縮短轉場。「無」可移除同軌全部轉場。" : tab === "idle" ? "保留速度與強度，套用到同軌全部文字；「無」可清除待機。" : "只套用目前的入場或退場到同軌同類片段；短片段會縮短動畫，另一端動畫保留。「無」可清除這一類動畫。";
      find(".animation-note").textContent = tab === "transition" ? model.next ? `接至 ${model.next.name || "下一片段"}。效果跨越兩段交界，原本剪輯長度與音訊不變。` : "同一軌道需要前後相接的兩段影片。可拖曳片段接合，再套用轉場。" : tab === "idle" ? "在入場結束到退場開始之間循環，兩端平順恢復原姿態。速度 0.25～4 倍；強度 0% 或「無」可取消待機。" : `套用在片段${tab === "in" ? "開頭" : "結尾"}，可與位置關鍵幀搭配；原聲維持原本的音量與淡化設定。`;
      const effectsUnavailable = tab === "transition" && model.transitionSupported && model.effectsSupported !== true;
      const referenceUnavailable = tab === "transition" && model.transitionSupported && model.referenceSupported !== true;
      const unsupported = effectsUnavailable || referenceUnavailable || !(tab === "transition" ? model.transitionSupported : tab === "idle" ? model.idleSupported : model.supported);
      find(".animation-support").classList.toggle("hidden", !unsupported);
      find(".animation-support span").textContent = effectsUnavailable ? "目前 Studio 尚未載入轉場特效更新，原有設定仍保留。關閉 Studio 啟動視窗，再啟動新版並重新檢查。" : referenceUnavailable ? "目前 Studio 尚未載入「放射／橫向模糊」轉場更新，原有設定與草稿仍保留。重新啟動新版 Studio 後按「重新檢查動畫功能」。" : "目前 Studio 尚未載入動畫更新。先儲存剪輯、關閉 Studio 啟動視窗，再啟動新版並重新檢查。";
      find("[data-animation-recheck]").disabled = Boolean(model.checking || model.disabled);
    }
    return { render, selectTab(value) { if (Object.hasOwn(LABELS, value)) { tab = value; render(model); } }, currentTab: () => tab };
  }
  const core = { EFFECTS, TRANSITIONS, TRANSITION_EFFECT_TYPES, REFERENCE_TRANSITION_TYPES, IDLE_EFFECTS, TRANSITION_CATEGORIES, effectLabel, mountAnimationEditor };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorAnimationUI = core;
})(typeof window !== "undefined" ? window : globalThis);
