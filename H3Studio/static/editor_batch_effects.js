/* Apply one visual effect to its selected track without changing timing or audio. */
(function (root) {
  "use strict";
  const commonJS = typeof module !== "undefined" && module.exports;
  const animations = commonJS ? require("./editor_animations.js") : root.H3EditorAnimations;
  const speed = commonJS ? require("./editor_speed.js") : root.H3EditorSpeed;
  const TABS = ["in", "out", "idle", "transition"];
  const visualKind = (item, kind) => kind === "video" ? item.kind || "video" : item.kind;
  const trackId = item => item.track_id || item.id;
  const active = spec => spec !== undefined && spec.type !== "none" && spec.duration > 0;
  function copy(value, ancestors = new Set()) {
    if (value === null || typeof value !== "object") return value;
    if (ancestors.has(value) || !Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new Error("剪輯專案格式無效。");
    ancestors.add(value);
    const result = Array.isArray(value) ? value.map(item => copy(item, ancestors)) : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item, ancestors)]));
    ancestors.delete(value);
    return result;
  }
  function same(left, right) {
    if (Object.is(left, right)) return true;
    if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && same(left[key], right[key]));
  }
  function lengthOf(item, kind) {
    let length;
    if (kind === "video") {
      if (!Number.isFinite(item.in) || !Number.isFinite(item.out) || item.in < 0 || item.out <= item.in || item.speed !== undefined && (!Number.isFinite(item.speed) || item.speed < speed.MIN_SPEED || item.speed > speed.MAX_SPEED)) throw new Error("影片片段的來源範圍或速度無效。");
      speed.validateSpeedCurve(item);
      length = speed.clipDuration(item);
    } else {
      if (!Number.isFinite(item.start) || !Number.isFinite(item.end) || item.start < 0) throw new Error("圖層開始或結束時間無效。");
      length = item.end - item.start;
    }
    if (!Number.isFinite(length) || length <= 0) throw new Error("動畫片段長度無效。");
    return length;
  }
  function targetLabel(kind, tab) {
    return tab === "transition" ? kind === "video" ? "主軌全部交界" : "同軌全部交界" : kind === "video" ? "主軌全部影片" : "同軌同類圖層";
  }
  function applyTrackEffect(input, options) {
    if (!input || typeof input !== "object" || Array.isArray(input) || !options || !["video", "overlay"].includes(options.kind) || !TABS.includes(options.tab) || typeof options.itemId !== "string") throw new Error("請先選擇影片或圖層，以及要套用的動畫類別。");
    const { kind, itemId, tab } = options, collection = kind === "video" ? "clips" : "overlays";
    if (!Array.isArray(input[collection])) throw new Error("剪輯軌道格式無效。");
    const project = copy(input), source = project[collection].find(item => item?.id === itemId);
    if (!source || project[collection].filter(item => item?.id === itemId).length !== 1) throw new Error("找不到唯一的選取片段，請重新選擇。");
    if (!project[collection].every(item => item && typeof item === "object" && !Array.isArray(item) && typeof item.id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(item.id)) || new Set(project[collection].map(item => item.id)).size !== project[collection].length) throw new Error("剪輯片段編號無效或重複。");
    const sourceKind = visualKind(source, kind);
    if (!["text", "image", "video"].includes(sourceKind) || tab === "idle" && sourceKind !== "text" || tab === "transition" && sourceKind !== "video") throw new Error("這個片段不支援選取的動畫類別。");
    const field = tab === "transition" ? "transition_out" : `animation_${tab}`, setting = source[field];
    const sourceLength = lengthOf(source, kind);
    if (tab === "idle") animations.validateIdleSpec(setting);
    else animations.validateAnimations({ kind: sourceKind, [field]: setting, ...(tab === "in" || tab === "out" ? { [tab === "in" ? "animation_out" : "animation_in"]: source[tab === "in" ? "animation_out" : "animation_in"] } : {}) }, sourceLength);
    const remove = tab === "idle" ? setting === undefined || (setting.preset || "none") === "none" || setting.amount === 0 : !active(setting);
    const track = kind === "video" ? project.clips : project.overlays.filter(item => trackId(item) === trackId(source)).sort((left, right) => left.start - right.start);
    let count = 0, clamped = 0, skipped = 0;
    function assign(item, value, reduced = false) {
      if (value === undefined) {
        if (Object.hasOwn(item, field)) { delete item[field]; count++; if (reduced) clamped++; }
      } else if (!same(item[field], value)) { item[field] = value; count++; if (reduced) clamped++; }
    }
    if (tab === "transition") {
      if (remove) {
        // Clear stale final-clip and gap-bound transitions as well as valid pairs.
        for (const item of track) assign(item, undefined);
      } else {
        const lengths = track.map(item => lengthOf(item, kind));
        const adjacent = index => index + 1 < track.length && visualKind(track[index], kind) === "video" && visualKind(track[index + 1], kind) === "video" && (kind === "video" || Math.abs(track[index].end - track[index + 1].start) <= 1e-6);
        const index = track.indexOf(source), next = track[index + 1];
        if (!adjacent(index) || setting.next_id !== next.id || setting.duration > Math.min(lengths[index], lengths[index + 1]) + 1e-9) throw new Error("選取轉場需連接同軌相鄰影片，時間不可超過前後片段。");
        for (let index = 0; index + 1 < track.length; index++) {
          if (!adjacent(index)) { skipped++; continue; }
          const duration = Math.min(setting.duration, lengths[index], lengths[index + 1]);
          assign(track[index], { type: setting.type, duration, next_id: track[index + 1].id }, duration < setting.duration - 1e-9);
        }
      }
    } else {
      for (const item of track) {
        if (visualKind(item, kind) !== sourceKind) { skipped++; continue; }
        const length = lengthOf(item, kind);
        if (remove) { assign(item, undefined); continue; }
        if (tab === "idle") { assign(item, { ...setting }); continue; }
        const opposite = tab === "in" ? "animation_out" : "animation_in";
        animations.validateAnimations({ kind: sourceKind, [opposite]: item[opposite] }, length);
        const reserved = active(item[opposite]) ? item[opposite].duration : 0;
        const duration = Math.min(setting.duration, Math.max(0, length - reserved));
        assign(item, duration > 0 ? { type: setting.type, duration } : undefined, duration < setting.duration - 1e-9);
      }
    }
    return { project, count, clamped, skipped };
  }
  const core = { applyTrackEffect, targetLabel };
  if (commonJS) module.exports = core;
  root.H3EditorBatchEffects = core;
})(typeof window !== "undefined" ? window : globalThis);
