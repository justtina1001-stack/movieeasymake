/* Timeline colour grades share an sRGB affine matrix and tone table with export. */
(function (root) {
  "use strict";
  const MAX_ADJUSTMENTS = 20, MAX_ACTIVE = 4, MAX_TIME = 600, TIME_EPSILON = 1e-9;
  const CLIP_ID = /^[A-Za-z0-9_-]{1,64}(?![\s\S])/;
  const CONTROLS = Object.freeze(["exposure", "brightness", "contrast", "saturation", "temperature", "tint", "highlights", "shadows"]);
  const DEFAULTS = Object.freeze({ name: "調色片段", enabled: true, strength: 100, exposure: 0, brightness: 0, contrast: 0, saturation: 0, temperature: 0, tint: 0, highlights: 0, shadows: 0 });
  const ALLOWED = new Set(["id", "start", "end", ...Object.keys(DEFAULTS)]);
  const LUMA = Object.freeze([0.2126, 0.7152, 0.0722]);
  const clamp = (value, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, value));
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const adjustments = project => project.color_adjustments === undefined ? [] : project.color_adjustments;
  function canonicalAdjustment(item) {
    const result = { id: item.id, start: item.start, end: item.end };
    for (const [field, fallback] of Object.entries(DEFAULTS)) result[field] = item[field] === undefined ? fallback : field === "name" && typeof item[field] === "string" ? item[field].trim() : item[field];
    return result;
  }
  function canonicalAdjustments(project) { return adjustments(project).map(canonicalAdjustment); }
  function validateAdjustments(project) {
    const items = adjustments(project);
    if (!Array.isArray(items) || items.length > MAX_ADJUSTMENTS) throw new Error("調色片段格式無效，每個專案最多 20 段。");
    if (!items.length) return [];
    if (!Number.isFinite(project.fps) || project.fps <= 0) throw new Error("調色片段需要有效的專案影格率。");
    const ids = new Set([...(project.clips || []), ...(project.audio_clips || []), ...(project.overlays || [])].map(item => item.id));
    const events = [], result = [];
    for (const item of items) {
      if (!item || typeof item !== "object" || Array.isArray(item) || Object.keys(item).some(field => !ALLOWED.has(field))) throw new Error("調色片段包含不支援的設定。");
      if (typeof item.id !== "string" || !CLIP_ID.test(item.id) || ids.has(item.id)) throw new Error("調色片段編號無效或與其他片段重複。");
      ids.add(item.id);
      if (![item.start, item.end].every(Number.isFinite) || item.start < 0 || item.end > MAX_TIME || item.end - item.start < 1 / project.fps - TIME_EPSILON) throw new Error("調色片段起訖需位於 0–600 秒內，且至少一個專案影格。");
      if (own(item, "name") && (typeof item.name !== "string" || !item.name.trim() || Array.from(item.name.trim()).length > 80 || /[\u0000-\u001f\u007f]/.test(item.name))) throw new Error("調色片段名稱需為 1–80 字，且不能含控制字元。");
      if (own(item, "enabled") && typeof item.enabled !== "boolean") throw new Error("調色片段的啟用設定無效。");
      for (const field of ["strength", ...CONTROLS]) {
        if (!own(item, field)) continue;
        const minimum = field === "strength" ? 0 : field === "exposure" ? -2 : -100;
        const maximum = field === "exposure" ? 2 : 100;
        if (!Number.isFinite(item[field]) || item[field] < minimum || item[field] > maximum) throw new Error(field === "exposure" ? "曝光需介於 -2 至 2。" : field === "strength" ? "濾鏡強度需介於 0 至 100。" : "調色參數需介於 -100 至 100。");
      }
      events.push([item.start, 1], [item.end, -1]); result.push(canonicalAdjustment(item));
    }
    events.sort((first, second) => first[0] - second[0] || first[1] - second[1]);
    let concurrent = 0;
    for (const [, difference] of events) { concurrent += difference; if (concurrent > MAX_ACTIVE) throw new Error("同一時間最多重疊 4 個調色片段。"); }
    return result;
  }
  function hasEffect(item) {
    return (item.enabled === undefined || item.enabled === true) && (item.strength === undefined || item.strength > 0) && CONTROLS.some(field => (item[field] ?? 0) !== 0);
  }
  function activeAdjustments(project, time) {
    if (!Number.isFinite(time)) return [];
    return canonicalAdjustments(project).filter(item => item.start <= time && time < item.end && hasEffect(item));
  }
  function timelineLanes(project) {
    const lanes = [], ends = [];
    const sorted = adjustments(project).map((item, index) => ({ item, index })).sort((first, second) => first.item.start - second.item.start || first.item.end - second.item.end || first.index - second.index);
    for (const { item } of sorted) {
      let lane = ends.findIndex(end => end <= item.start);
      if (lane < 0) { lane = lanes.length; lanes.push([]); }
      lanes[lane].push(item); ends[lane] = item.end;
    }
    return lanes;
  }
  function scaledControls(item) {
    const strength = (item.strength ?? 100) / 100;
    return Object.fromEntries(CONTROLS.map(field => [field, (item[field] ?? 0) * strength]));
  }
  function affineMatrix(item) {
    const control = scaledControls(item), exposure = 2 ** control.exposure;
    const temperature = control.temperature / 100, tint = control.tint / 100;
    const gains = [exposure * (1 + 0.25 * temperature + 0.10 * tint), exposure * (1 - 0.20 * tint), exposure * (1 - 0.25 * temperature + 0.10 * tint)];
    const saturation = 1 + control.saturation / 100, contrast = 1 + control.contrast / 100;
    const offset = 0.5 * (1 - contrast) + 0.25 * control.brightness / 100;
    const matrix = [];
    for (let row = 0; row < 3; row++) {
      for (let column = 0; column < 3; column++) matrix.push(contrast * (saturation * (row === column ? 1 : 0) + (1 - saturation) * LUMA[column]) * gains[column]);
      matrix.push(0, offset);
    }
    matrix.push(0, 0, 0, 1, 0);
    return matrix;
  }
  function toneTable(item) {
    const control = scaledControls(item), shadows = control.shadows / 100, highlights = control.highlights / 100;
    return Array.from({ length: 257 }, (_, index) => {
      const x = index / 256;
      return clamp(x + 0.8 * shadows * x * (1 - x) ** 2 + 0.8 * highlights * x * x * (1 - x));
    });
  }
  function interpolateTone(value, table) {
    const position = clamp(value) * 256, left = Math.min(255, Math.floor(position)), fraction = position - left;
    return table[left] + (table[left + 1] - table[left]) * fraction;
  }
  function applyPixels(pixels, items, options = {}) {
    const channels = options.channels ?? 4;
    if (![3, 4].includes(channels) || !pixels || !Number.isInteger(pixels.length) || pixels.length % channels || !(Array.isArray(pixels) || pixels instanceof Uint8Array || pixels instanceof Uint8ClampedArray)) throw new Error("色彩測試像素需為 RGB 或 RGBA 的 8 位元資料。");
    if (!Array.isArray(items)) throw new Error("調色片段資料格式無效。");
    const output = Array.isArray(pixels) ? pixels.slice() : new pixels.constructor(pixels);
    for (const item of items.filter(hasEffect)) {
      const matrix = affineMatrix(item), table = toneTable(item);
      for (let index = 0; index < output.length; index += channels) {
        const rgb = [output[index] / 255, output[index + 1] / 255, output[index + 2] / 255];
        for (let row = 0; row < 3; row++) {
          const start = row * 5;
          const value = matrix[start] * rgb[0] + matrix[start + 1] * rgb[1] + matrix[start + 2] * rgb[2] + matrix[start + 4];
          output[index + row] = Math.floor(clamp(interpolateTone(clamp(value), table)) * 255 + 0.5);
        }
      }
    }
    return output;
  }
  const preset = (label, values) => Object.freeze({ label, controls: Object.freeze(Object.fromEntries(CONTROLS.map(field => [field, values[field] ?? 0]))) });
  const PRESETS = Object.freeze({
    natural: preset("自然", {}),
    warm: preset("暖色", { temperature: 30, saturation: 8, shadows: 8 }),
    cool: preset("冷色", { temperature: -30, contrast: 8, saturation: -5 }),
    mono: preset("黑白", { saturation: -100, contrast: 12 }),
    cinema: preset("電影感", { contrast: 20, saturation: -12, temperature: -8, shadows: -12, highlights: -15 }),
    vivid: preset("鮮明", { contrast: 12, saturation: 30, highlights: -8, shadows: 8 }),
  });
  let nextPreviewId = 0;
  function mountPreview(stage, doc = root.document) {
    if (!stage?.style || !doc?.createElementNS || !doc.body) throw new Error("調色預覽需要有效的影片畫布。");
    const namespace = "http://www.w3.org/2000/svg", filterId = `h3-editor-color-${++nextPreviewId}`;
    const svg = doc.createElementNS(namespace, "svg"), defs = doc.createElementNS(namespace, "defs"), filter = doc.createElementNS(namespace, "filter");
    svg.setAttribute("aria-hidden", "true"); svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
    svg.style.position = "absolute"; svg.style.pointerEvents = "none";
    filter.setAttribute("id", filterId); filter.setAttribute("x", "0"); filter.setAttribute("y", "0"); filter.setAttribute("width", "100%"); filter.setAttribute("height", "100%"); filter.setAttribute("color-interpolation-filters", "sRGB");
    defs.appendChild(filter); svg.appendChild(defs); doc.body.appendChild(svg);
    let previousKey = "", destroyed = false;
    const clear = () => { stage.style.filter = ""; filter.replaceChildren(); previousKey = ""; };
    function update(project, time) {
      if (destroyed) return;
      const active = activeAdjustments(project, time);
      if (!active.length) { if (previousKey || stage.style.filter) clear(); return; }
      const key = JSON.stringify(active);
      if (key === previousKey) return;
      filter.replaceChildren();
      let input = "SourceGraphic";
      active.forEach((item, index) => {
        const matrix = doc.createElementNS(namespace, "feColorMatrix"), transfer = doc.createElementNS(namespace, "feComponentTransfer");
        const matrixResult = `matrix_${index}`, curveResult = `tone_${index}`;
        matrix.setAttribute("in", input); matrix.setAttribute("type", "matrix"); matrix.setAttribute("values", affineMatrix(item).join(" ")); matrix.setAttribute("result", matrixResult);
        transfer.setAttribute("in", matrixResult); transfer.setAttribute("result", curveResult);
        const table = toneTable(item).join(" ");
        for (const channel of ["R", "G", "B"]) { const component = doc.createElementNS(namespace, `feFunc${channel}`); component.setAttribute("type", "table"); component.setAttribute("tableValues", table); transfer.appendChild(component); }
        const alpha = doc.createElementNS(namespace, "feFuncA"); alpha.setAttribute("type", "identity"); transfer.appendChild(alpha);
        filter.appendChild(matrix); filter.appendChild(transfer); input = curveResult;
      });
      stage.style.filter = `url(#${filterId})`; previousKey = key;
    }
    const destroy = () => { if (destroyed) return; clear(); svg.remove(); destroyed = true; };
    return { update, clear, destroy };
  }
  const core = { MAX_ADJUSTMENTS, MAX_ACTIVE, MAX_TIME, CONTROLS, DEFAULTS, PRESETS, presets: PRESETS, adjustments, canonicalAdjustment, canonicalAdjustments, validateAdjustments, hasEffect, activeAdjustments, timelineLanes, scaledControls, affineMatrix, toneTable, interpolateTone, applyPixels, mountPreview };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorColor = core;
})(typeof window !== "undefined" ? window : globalThis);
