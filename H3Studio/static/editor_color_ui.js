/* One persistent colour inspector and reversible timeline range gestures. */
(function (root) {
  "use strict";
  const color = typeof module !== "undefined" && module.exports ? require("./editor_color.js") : root.H3EditorColor;
  const clone = value => JSON.parse(JSON.stringify(value));
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const escapeText = text => String(text).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const CONTROL_FIELDS = Object.freeze([
    { key: "strength", label: "濾鏡強度", min: 0, max: 100, step: 1, neutral: 100 },
    { key: "exposure", label: "曝光", min: -2, max: 2, step: 0.1, neutral: 0 },
    ...[["brightness", "亮度"], ["contrast", "對比"], ["saturation", "飽和度"], ["temperature", "色溫"], ["tint", "色調"], ["highlights", "高光"], ["shadows", "陰影"]]
      .map(([key, label]) => ({ key, label, min: -100, max: 100, step: 1, neutral: 0 })),
  ]);
  function mountInspector(host, callbacks = {}) {
    if (!host?.ownerDocument) throw new Error("調色介面需要有效的容器。");
    // Build once: autosave and playback renders must preserve typing and focus.
    host.innerHTML = '<p class="color-scope-note subtle">套用到整個畫面，包含影片、圖片與字幕圖層。拖曳時間軸上的調色片段，決定生效時段。</p>' +
      '<div class="color-empty"><p class="empty-note">加入調色片段，再選濾鏡或拉動參數。</p><div class="color-add-actions"><button type="button" class="button secondary small" data-color-add="all">＋ 全片調色</button><button type="button" class="button ghost small" data-color-add="clip">＋ 選取影片時段</button><button type="button" class="button ghost small" data-color-add="range">＋ 目前位置 3 秒</button></div></div>' +
      '<div class="color-selection"><div class="color-selection-heading"><strong data-color-name>調色片段</strong><label class="color-enabled"><input type="checkbox" data-color-enabled checked>啟用</label></div>' +
      '<div class="trim-fields color-timing"><label class="field">開始（秒）<input type="number" data-color-start min="0" max="600" step="0.01"></label><label class="field">結束（秒）<input type="number" data-color-end min="0" max="600" step="0.01"></label></div>' +
      '<button type="button" class="button ghost small full-width" data-color-all>延展為整支影片</button><p class="color-range-note subtle" role="status"></p>' +
      '<div class="color-presets" role="group" aria-label="濾鏡預設">' + Object.entries(color.PRESETS).map(([key, value]) => `<button type="button" class="color-preset" data-color-preset="${escapeText(key)}" aria-pressed="false"><span class="color-preset-swatch ${escapeText(key)}" aria-hidden="true"></span><span>${escapeText(value.label)}</span></button>`).join("") + '</div>' +
      '<div class="color-controls">' + CONTROL_FIELDS.map(field => `<div class="color-control"><div class="color-control-heading"><label for="color-control-${field.key}">${field.label}</label><div><input id="color-control-${field.key}" type="number" data-color-number="${field.key}" min="${field.min}" max="${field.max}" step="${field.step}" aria-label="${field.label}數值"><button type="button" class="color-control-reset" data-color-reset-field="${field.key}" aria-label="還原${field.label}">↺</button></div></div><input type="range" data-color-range="${field.key}" min="${field.min}" max="${field.max}" step="${field.step}" aria-label="${field.label}拉桿" title="雙擊還原預設值"></div>`).join("") + '</div>' +
      '<p class="subtle color-control-note">拉桿即時預覽；雙擊拉桿或按 ↺ 可還原單項。</p><button type="button" class="button ghost small full-width" data-color-reset>還原全部調色參數</button></div>' +
      '<div class="color-support" role="status"><p class="subtle" data-color-support-message></p><button type="button" class="button ghost small" data-color-check>重新檢查調色功能</button></div>';
    const find = selector => host.querySelector(selector), all = selector => [...host.querySelectorAll(selector)];
    let model = {}, selection = null, gesture = null, gestureSerial = 0;
    const blocked = () => model.blocked || !model.ready || model.checking;
    const editable = () => !blocked() && Boolean(model.selected);
    const validNumber = (input, low, high) => input.value.trim() !== "" && Number.isFinite(Number(input.value)) && Number(input.value) >= low && Number(input.value) <= high;
    function syncPeer(field, value, origin) {
      for (const selector of [`[data-color-number="${field}"]`, `[data-color-range="${field}"]`]) {
        const peer = find(selector);
        if (peer !== origin && host.ownerDocument.activeElement !== peer) peer.value = value;
      }
    }
    function group(field) {
      const key = `${selection}:${field}`;
      if (gesture?.key !== key) gesture = { key, value: `color:${key}:${++gestureSerial}` };
      return gesture.value;
    }
    for (const field of CONTROL_FIELDS) {
      const number = find(`[data-color-number="${field.key}"]`), range = find(`[data-color-range="${field.key}"]`);
      number.addEventListener("change", () => {
        if (!editable() || !validNumber(number, field.min, field.max)) return;
        const value = Number(number.value); syncPeer(field.key, value, number);
        callbacks.onControls?.({ [field.key]: value }, null);
      });
      range.addEventListener("pointerdown", () => { gesture = null; });
      range.addEventListener("input", () => {
        if (!editable() || !validNumber(range, field.min, field.max)) return;
        const value = Number(range.value); syncPeer(field.key, value, range);
        callbacks.onControls?.({ [field.key]: value }, group(field.key));
      });
      for (const event of ["change", "blur", "pointercancel"]) range.addEventListener(event, () => { gesture = null; });
      range.addEventListener("dblclick", () => {
        if (!editable()) return;
        gesture = null; syncPeer(field.key, field.neutral, null);
        range.value = field.neutral;
        callbacks.onControls?.({ [field.key]: field.neutral }, null);
      });
    }
    for (const [selector, field] of [["[data-color-start]", "start"], ["[data-color-end]", "end"]]) {
      const input = find(selector);
      input.addEventListener("change", () => {
        if (editable() && validNumber(input, 0, 600)) callbacks.onRange?.({ [field]: Number(input.value) });
      });
    }
    find("[data-color-enabled]").addEventListener("change", event => { if (editable()) callbacks.onControls?.({ enabled: event.target.checked }, null); });
    host.addEventListener("click", event => {
      const target = event.target;
      const check = target.closest("[data-color-check]");
      if (check) { if (!model.blocked && !model.checking) callbacks.onCheck?.(); return; }
      const add = target.closest("[data-color-add]");
      if (add) { if (!blocked() && model.totalDuration > 0 && (add.dataset.colorAdd !== "clip" || model.canUseClip)) callbacks.onAdd?.(add.dataset.colorAdd); return; }
      if (!editable()) return;
      const preset = target.closest("[data-color-preset]");
      if (preset && Object.hasOwn(color.PRESETS, preset.dataset.colorPreset)) { gesture = null; callbacks.onPreset?.(preset.dataset.colorPreset); return; }
      const resetField = target.closest("[data-color-reset-field]");
      if (resetField) {
        const field = CONTROL_FIELDS.find(item => item.key === resetField.dataset.colorResetField);
        if (field) { gesture = null; syncPeer(field.key, field.neutral, null); callbacks.onControls?.({ [field.key]: field.neutral }, null); }
        return;
      }
      if (target.closest("[data-color-reset]")) { gesture = null; callbacks.onReset?.(); }
      if (target.closest("[data-color-all]") && model.totalDuration > 0) callbacks.onAll?.();
    });
    function render(view) {
      model = view || {};
      const item = model.selected, key = item?.id || null;
      if (key !== selection) { selection = key; gesture = null; }
      host.classList.toggle("hidden", !model.open);
      find(".color-empty").classList.toggle("hidden", Boolean(item));
      find(".color-selection").classList.toggle("hidden", !item);
      for (const button of all("[data-color-add]")) button.disabled = Boolean(blocked() || !(model.totalDuration > 0) || button.dataset.colorAdd === "clip" && !model.canUseClip);
      for (const node of all(".color-selection input,.color-selection button")) node.disabled = Boolean(!editable());
      find("[data-color-all]").disabled = Boolean(!editable() || !(model.totalDuration > 0));
      if (item) {
        find("[data-color-name]").textContent = item.name || color.DEFAULTS.name;
        for (const field of CONTROL_FIELDS) for (const kind of ["number", "range"]) {
          const node = find(`[data-color-${kind}="${field.key}"]`);
          if (host.ownerDocument.activeElement !== node) node.value = item[field.key] ?? field.neutral;
        }
        for (const field of ["start", "end"]) {
          const node = find(`[data-color-${field}]`);
          if (host.ownerDocument.activeElement !== node) node.value = Math.round(item[field] * 1000) / 1000;
        }
        find("[data-color-enabled]").checked = item.enabled !== false;
        for (const button of all("[data-color-preset]")) {
          const preset = color.PRESETS[button.dataset.colorPreset];
          const active = color.CONTROLS.every(field => (item[field] ?? 0) === preset.controls[field]);
          button.setAttribute("aria-pressed", String(active)); button.classList.toggle("active", active);
        }
        find(".color-range-note").textContent = item.end > model.totalDuration + 1e-6 ? "超出片尾的調色時段會保留；匯出影片仍維持原本長度。" : item.enabled === false ? "目前暫停這段調色；勾選「啟用」即可恢復。" : `${(item.end - item.start).toFixed(2)} 秒 · 可在時間軸拖曳位置或兩端調整時段。`;
      }
      const unsupported = !model.ready || Boolean(model.error) || model.checking;
      find(".color-support").classList.toggle("hidden", !unsupported);
      find("[data-color-support-message]").textContent = model.checking ? "正在檢查調色功能…" : model.error || (!model.ready ? "目前 Studio 尚未載入調色更新。重新啟動新版 Studio 後，再按「重新檢查調色功能」；現有設定與草稿仍保留。" : "");
      find("[data-color-check]").disabled = Boolean(model.blocked || model.checking);
    }
    return { render };
  }
  const contentSignature = project => { const copy = clone(project); delete copy.updated_at; return JSON.stringify(copy); };
  function transformRange(project, id, mode, delta, options = {}) {
    if (!["move", "left", "right"].includes(mode) || !Number.isFinite(delta)) throw new Error("調色時間調整無效。");
    color.validateAdjustments(project);
    const next = clone(project), item = color.adjustments(next).find(entry => entry.id === id);
    if (!item) throw new Error("找不到調色片段。");
    const fps = project.fps, frame = 1 / fps, start = item.start, end = item.end, length = end - start;
    if (Math.abs(delta) < 1e-12) return { project: next, item, changed: false, clamped: false, requestedDelta: delta, appliedDelta: 0 };
    const maximum = Math.min(600, Math.max(end, Number.isFinite(options.maxEnd) ? options.maxEnd : 600));
    const anchors = Array.isArray(options.snap) ? options.snap.filter(Number.isFinite) : [];
    const tolerance = Number.isFinite(options.snapTolerance) ? Math.max(0, options.snapTolerance) : 0.12;
    const quantize = value => Math.round(value * fps) / fps;
    function snap(value, edge) {
      let result = quantize(value), matched = false;
      if (typeof options.snap === "function") {
        const offered = options.snap(result, { edge, item: clone(item), project });
        if (Number.isFinite(offered)) { matched = Math.abs(offered - result) > 1e-9; result = offered; }
      } else {
        const candidate = anchors.reduce((best, anchor) => Math.abs(anchor - result) <= tolerance && (best === null || Math.abs(anchor - result) < Math.abs(best - result)) ? anchor : best, null);
        if (candidate !== null) { result = candidate; matched = true; }
      }
      return { value: result, matched };
    }
    if (mode === "move") {
      let position = quantize(start + delta);
      const left = snap(position, "left"), right = snap(position + length, "right");
      const candidates = [...(left.matched ? [left.value] : []), ...(right.matched ? [right.value - length] : [])];
      if (candidates.length) position = candidates.reduce((best, candidate) => Math.abs(candidate - position) < Math.abs(best - position) ? candidate : best);
      item.start = clamp(position, 0, Math.max(0, maximum - length)); item.end = item.start + length;
    } else if (mode === "left") item.start = clamp(snap(start + delta, "left").value, 0, end - frame);
    else item.end = clamp(snap(end + delta, "right").value, start + frame, maximum);
    color.validateAdjustments(next);
    const appliedDelta = mode === "right" ? item.end - end : item.start - start;
    const edgeRequested = mode === "right" ? end + delta : start + delta;
    const edgeActual = mode === "right" ? item.end : item.start;
    const minimum = mode === "right" ? start + frame : 0, limit = mode === "left" ? end - frame : mode === "move" ? maximum - length : maximum;
    return { project: next, item, changed: JSON.stringify(color.canonicalAdjustments(next)) !== JSON.stringify(color.canonicalAdjustments(project)), clamped: edgeRequested < minimum - 1e-9 || edgeRequested > limit + 1e-9 || edgeActual < minimum - 1e-9 || edgeActual > limit + 1e-9, requestedDelta: delta, appliedDelta };
  }
  class AdjustmentTransaction {
    constructor(session, id, mode = "move", options = {}) {
      this.session = session; this.id = id; this.mode = mode; this.options = options;
      this.original = clone(session.project); this.originalSignature = contentSignature(session.project);
      this.preview = clone(this.original); this.result = null; this.closed = false;
    }
    update(delta) {
      if (this.closed) throw new Error("這次調色拖曳已結束。");
      try { this.result = transformRange(this.original, this.id, this.mode, delta, this.options); this.preview = this.result.project; return this.result; }
      catch (error) { this.result = null; this.preview = clone(this.original); throw error; }
    }
    commit() {
      if (this.closed) return false;
      this.closed = true;
      if (contentSignature(this.session.project) !== this.originalSignature) throw new Error("專案內容已變更，調色拖曳已取消；請重新拖曳。");
      if (!this.result?.changed) return false;
      return this.session.change(value => { value.color_adjustments = clone(color.adjustments(this.preview)); });
    }
    cancel() { this.closed = true; this.result = null; this.preview = clone(this.original); return false; }
  }
  function timelineMarkup(project, options = {}) {
    const escape = options.escape || escapeText, zoom = Number.isFinite(options.zoom) && options.zoom > 0 ? options.zoom : 60;
    const lanes = color.timelineLanes(project), height = lanes.length * 34;
    const html = lanes.map((lane, index) => `<div class="color-lane" data-color-lane="${index}" style="height:34px">` + lane.map(item => {
      const selected = item.id === options.selectedId, disabled = options.locked === true;
      return `<button type="button" class="color-clip${selected ? " selected" : ""}${item.enabled === false ? " inactive" : ""}${item.start * zoom < 8 ? " at-timeline-start" : ""}" data-color-id="${escape(item.id)}" style="left:${item.start * zoom}px;width:${Math.max(1, (item.end - item.start) * zoom)}px" aria-pressed="${selected}" aria-label="調色：${escape(item.name || color.DEFAULTS.name)}，${item.start.toFixed(2)} 至 ${item.end.toFixed(2)} 秒" title="拖曳調整生效時段；兩端調整開始與結束" ${disabled ? "disabled" : ""}><span class="color-clip-name">◐ ${escape(item.name || color.DEFAULTS.name)}</span>` + ["left", "right"].map(edge => `<span class="color-time-handle ${edge}" data-color-edge="${edge}" tabindex="${disabled ? -1 : 0}" role="slider" aria-label="調色${edge === "left" ? "開始" : "結束"}時間" aria-orientation="horizontal" aria-valuemin="0" aria-valuemax="600" aria-valuenow="${edge === "left" ? item.start : item.end}" aria-valuetext="${(edge === "left" ? item.start : item.end).toFixed(3)} 秒" aria-disabled="${disabled}"><i></i></span>`).join("") + '</button>';
    }).join("") + '</div>').join("");
    return { html, height };
  }
  const api = { CONTROL_FIELDS, mountInspector, transformRange, AdjustmentTransaction, timelineMarkup };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.H3EditorColorUI = api;
})(typeof window !== "undefined" ? window : globalThis);
