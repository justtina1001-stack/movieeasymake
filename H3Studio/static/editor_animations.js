/* Clip-local visual animations and duration-preserving, adjacent-clip transitions. */
(function (root) {
  "use strict";
  const speedMath = typeof module !== "undefined" && module.exports ? require("./editor_speed.js") : root.H3EditorSpeed;
  const ANIMATION_TYPES = Object.freeze(["none", "fade", "slide_left", "slide_right", "slide_up", "slide_down", "zoom_in", "zoom_out"]);
  const IDLE_TYPES = Object.freeze(["none", "pulse", "float", "shake", "blink"]);
  const REFERENCE_TRANSITION_TYPES = Object.freeze(["radial_zoom", "horizontal_blur"]);
  const TRANSITION_EFFECT_TYPES = Object.freeze(["white_flash", "blur_dissolve", "wave_distort", "rgb_glitch", ...REFERENCE_TRANSITION_TYPES]);
  const TRANSITION_TYPES = Object.freeze(["none", "crossfade", "wipe_left", "wipe_right", ...TRANSITION_EFFECT_TYPES]);
  const MAX_ANIMATION_DURATION = 5;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const smoothstep = value => { const ratio = clamp(value, 0, 1); return ratio * ratio * (3 - 2 * ratio); };
  const active = value => value && value.type !== "none" && value.duration > 0;
  const idleValue = (value, key, fallback) => value[key] === undefined ? fallback : value[key];
  const activeIdle = value => value && IDLE_TYPES.includes(value.preset) && value.preset !== "none" && idleValue(value, "amount", 1) > 0;
  function validateIdleSpec(value) {
    if (value === undefined) return;
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !["preset", "speed", "amount"].includes(key)) || !IDLE_TYPES.includes(idleValue(value, "preset", "none")) || !Number.isFinite(idleValue(value, "speed", 1)) || idleValue(value, "speed", 1) < 0.25 || idleValue(value, "speed", 1) > 4 || !Number.isFinite(idleValue(value, "amount", 1)) || idleValue(value, "amount", 1) < 0 || idleValue(value, "amount", 1) > 2) throw new Error("文字待機特效類型無效，速度為 0.25–4，強度為 0–2。");
  }
  function validateSpec(value, transition = false) {
    if (value === undefined) return;
    const keys = transition ? ["type", "duration", "next_id"] : ["type", "duration"];
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !keys.includes(key)) || !(transition ? TRANSITION_TYPES : ANIMATION_TYPES).includes(value.type) || !Number.isFinite(value.duration) || value.duration < 0 || value.duration > MAX_ANIMATION_DURATION || (transition && active(value) && (typeof value.next_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(value.next_id)))) throw new Error(transition ? "轉場類型、時間或相鄰片段編號無效。" : "進場／退場動畫類型無效，動畫時間為 0–5 秒。");
  }
  function canonicalAnimations(item) {
    const result = {};
    for (const field of ["animation_in", "animation_out", "transition_out"]) {
      const spec = item[field];
      if (active(spec)) result[field] = { type: spec.type, duration: spec.duration, ...(field === "transition_out" ? { next_id: spec.next_id } : {}) };
    }
    if (activeIdle(item.animation_idle)) result.animation_idle = { preset: item.animation_idle.preset, speed: idleValue(item.animation_idle, "speed", 1), amount: idleValue(item.animation_idle, "amount", 1) };
    return result;
  }
  function validateAnimations(item, length) {
    validateSpec(item.animation_in); validateSpec(item.animation_out); validateSpec(item.transition_out, true);
    validateIdleSpec(item.animation_idle);
    if (item.animation_idle !== undefined && item.kind !== "text") throw new Error("待機特效只能使用文字圖層。");
    if (!Number.isFinite(length) || length <= 0 || (active(item.animation_in) ? item.animation_in.duration : 0) + (active(item.animation_out) ? item.animation_out.duration : 0) > length + 1e-9) throw new Error("進場與退場動畫的總長不可超過片段。");
  }
  function clampAnimations(item, length) {
    validateSpec(item.animation_in); validateSpec(item.animation_out);
    validateIdleSpec(item.animation_idle);
    if (!Number.isFinite(length) || length <= 0) throw new Error("動畫片段長度無效。");
    const total = (active(item.animation_in) ? item.animation_in.duration : 0) + (active(item.animation_out) ? item.animation_out.duration : 0);
    if (total > length && total > 0) for (const field of ["animation_in", "animation_out"]) if (active(item[field])) item[field] = { ...item[field], duration: item[field].duration * length / total };
    return item;
  }
  function splitAnimations(item, firstLength, secondLength) {
    validateSpec(item.animation_in); validateSpec(item.animation_out); validateSpec(item.transition_out, true);
    validateIdleSpec(item.animation_idle);
    const first = {}, second = {};
    if (activeIdle(item.animation_idle)) first.animation_idle = { ...item.animation_idle }, second.animation_idle = { ...item.animation_idle };
    if (active(item.animation_in)) first.animation_in = { ...item.animation_in, duration: Math.min(item.animation_in.duration, firstLength) };
    if (active(item.animation_out)) second.animation_out = { ...item.animation_out, duration: Math.min(item.animation_out.duration, secondLength) };
    if (active(item.transition_out)) second.transition_out = { ...item.transition_out, duration: Math.min(item.transition_out.duration, secondLength) };
    clampAnimations(first, firstLength); clampAnimations(second, secondLength);
    return { first, second };
  }
  function effectTransform(type, progress, entering) {
    const amount = entering ? 1 - smoothstep(progress) : smoothstep(progress), result = { opacity: 1, x: 0, y: 0, scale: 1 };
    if (type === "fade") result.opacity = 1 - amount;
    else if (type === "slide_left") result.x = (entering ? 1 : -1) * amount;
    else if (type === "slide_right") result.x = (entering ? -1 : 1) * amount;
    else if (type === "slide_up") result.y = (entering ? 1 : -1) * amount;
    else if (type === "slide_down") result.y = (entering ? -1 : 1) * amount;
    else if (type === "zoom_in") result.scale = 1 - 0.85 * amount;
    else if (type === "zoom_out") result.scale = 1 + 0.8 * amount;
    return result;
  }
  function animationTransformAt(item, localTime, length) {
    const result = { opacity: 1, x: 0, y: 0, scale: 1 };
    if (!Number.isFinite(localTime) || !Number.isFinite(length) || localTime < 0 || localTime >= length) return { ...result, opacity: 0 };
    for (const [field, entering] of [["animation_in", true], ["animation_out", false]]) {
      const spec = item[field]; if (!active(spec)) continue;
      const progress = entering ? localTime / spec.duration : (localTime - length + spec.duration) / spec.duration;
      const effect = effectTransform(spec.type, progress, entering);
      result.opacity *= effect.opacity; result.x += effect.x; result.y += effect.y; result.scale *= effect.scale;
    }
    const idle = idleTransformAt(item, localTime, length);
    result.opacity *= idle.opacity; result.x += idle.x; result.y += idle.y; result.scale *= idle.scale;
    return result;
  }
  function idleTransformAt(item, localTime, length) {
    const result = { opacity: 1, x: 0, y: 0, scale: 1 }, spec = item.animation_idle;
    if (item.kind !== "text" || !activeIdle(spec) || !IDLE_TYPES.includes(spec.preset)) return result;
    const start = active(item.animation_in) ? item.animation_in.duration : 0;
    const end = length - (active(item.animation_out) ? item.animation_out.duration : 0), idleLength = end - start;
    if (idleLength <= 1e-6 || localTime <= start || localTime >= end) return result;
    const elapsed = localTime - start, ramp = Math.min(0.2, idleLength / 2);
    const envelope = smoothstep(Math.min(1, elapsed / ramp, (end - localTime) / ramp));
    const amount = idleValue(spec, "amount", 1), phase = 2 * Math.PI * elapsed * idleValue(spec, "speed", 1), wave = Math.sin(phase / 2) ** 2;
    if (spec.preset === "pulse") result.scale = 1 + 0.08 * amount * wave * envelope;
    else if (spec.preset === "float") result.y = -0.025 * amount * Math.sin(phase) * envelope;
    else if (spec.preset === "shake") result.x = 0.0125 * amount * Math.sin(phase * 4) * envelope;
    else if (spec.preset === "blink") result.opacity = 1 - Math.min(1, 0.8 * amount) * wave * envelope;
    return result;
  }
  function transitionTracks(project, durationFn = speedMath.clipDuration) {
    let cursor = 0;
    const main = (project.clips || []).map(clip => { const start = cursor, length = durationFn(clip); cursor += length; return { clip, start, end: cursor, length }; });
    const groups = new Map();
    for (const clip of project.overlays || []) { const id = clip.track_id || clip.id; if (!groups.has(id)) groups.set(id, []); groups.get(id).push({ clip, start: clip.start, end: clip.end, length: clip.end - clip.start }); }
    return [{ kind: "video", track_id: "V1", items: main }, ...[...groups].map(([track_id, items]) => ({ kind: "overlay", track_id, items: items.sort((a, b) => a.start - b.start) }))];
  }
  function validateTransitions(project, durationFn = speedMath.clipDuration) {
    for (const track of transitionTracks(project, durationFn)) for (let index = 0; index < track.items.length; index++) {
      const current = track.items[index], spec = current.clip.transition_out; validateSpec(spec, true); if (!active(spec)) continue;
      const next = track.items[index + 1];
      if (!next || (track.kind === "overlay" && (current.clip.kind !== "video" || next.clip.kind !== "video")) || Math.abs(next.start - current.end) > 1e-6 || spec.next_id !== next.clip.id || spec.duration > Math.min(current.length, next.length) + 1e-9) throw new Error("轉場需連接同軌相鄰影片，時間不可超過前後片段。");
    }
  }
  function transitionPairs(project, durationFn = speedMath.clipDuration) {
    const result = [];
    for (const track of transitionTracks(project, durationFn)) for (let index = 0; index + 1 < track.items.length; index++) {
      const from = track.items[index], to = track.items[index + 1], spec = from.clip.transition_out;
      if (!active(spec) || !TRANSITION_TYPES.includes(spec.type) || !Number.isFinite(spec.duration) || spec.duration > Math.min(MAX_ANIMATION_DURATION, from.length, to.length) + 1e-9 || spec.next_id !== to.clip.id || Math.abs(to.start - from.end) > 1e-6 || (track.kind === "overlay" && (from.clip.kind !== "video" || to.clip.kind !== "video"))) continue;
      result.push({ id: `${track.kind}:${track.track_id}:${from.clip.id}:${to.clip.id}`, kind: track.kind, track_id: track.track_id, from: from.clip, to: to.clip, fromStart: from.start, toStart: to.start, fromLength: from.length, toLength: to.length, cut: to.start, duration: spec.duration, start: to.start - spec.duration / 2, end: to.start + spec.duration / 2, type: spec.type });
    }
    return result;
  }
  function transitionStateAt(pair, time, fps = 24) {
    const progress = clamp((time - pair.start) / pair.duration, 0, 1);
    // Before/after the cut, hold the edge frame rather than reading trimmed content.
    const fromLocal = time < pair.cut ? clamp(time - pair.fromStart, 0, Math.max(0, pair.fromLength - 1 / fps)) : Math.max(0, pair.fromLength - 1 / fps);
    const toLocal = time < pair.cut ? 0 : clamp(time - pair.toStart, 0, Math.max(0, pair.toLength - 1 / fps));
    return { ...pair, progress, fromLocal, toLocal, fromHeld: time >= pair.cut, toHeld: time < pair.cut };
  }
  function transitionAt(project, time, durationFn = speedMath.clipDuration) { return transitionPairs(project, durationFn).filter(pair => pair.start <= time && time < pair.end).map(pair => transitionStateAt(pair, time, project.fps || 24)); }
  function transitionVisualAt(type, progress) {
    const value = clamp(progress, 0, 1);
    if (type === "wipe_left") return { from: { opacity: 1, left: 0, right: 1 - value }, to: { opacity: 1, left: 1 - value, right: 1 } };
    if (type === "wipe_right") return { from: { opacity: 1, left: value, right: 1 }, to: { opacity: 1, left: 0, right: value } };
    return { from: { opacity: 1 - value, left: 0, right: 1 }, to: { opacity: value, left: 0, right: 1 } };
  }
  const core = { ANIMATION_TYPES, IDLE_TYPES, TRANSITION_TYPES, TRANSITION_EFFECT_TYPES, REFERENCE_TRANSITION_TYPES, MAX_ANIMATION_DURATION, smoothstep, validateIdleSpec, canonicalAnimations, validateAnimations, clampAnimations, splitAnimations, animationTransformAt, idleTransformAt, transitionTracks, validateTransitions, transitionPairs, transitionStateAt, transitionAt, transitionVisualAt };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorAnimations = core;
})(typeof window !== "undefined" ? window : globalThis);
