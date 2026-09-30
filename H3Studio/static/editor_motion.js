/* Position keyframes use clip-local seconds; trimmed points remain recoverable. */
(function (root) {
  "use strict";
  const MAX_POSITION_KEYFRAMES = 100;
  const EASINGS = ["linear", "ease_in", "ease_out", "ease_in_out"];
  const positionKeyframes = layer => layer.position_keyframes || [];
  const canonicalPositionKeyframes = layer => positionKeyframes(layer).map(point => ({ time: point.time, x: point.x, y: point.y, easing: point.easing ?? "linear" }));
  function validatePositionKeyframes(layer) {
    const points = layer.position_keyframes === undefined ? [] : layer.position_keyframes;
    if (!Array.isArray(points) || points.length > MAX_POSITION_KEYFRAMES || (points.length && !["text", "image"].includes(layer.kind))) throw new Error("位置關鍵幀支援文字與圖片，每層最多 100 個。");
    let previous = -Infinity;
    for (const point of points) {
      if (!point || typeof point !== "object" || Array.isArray(point) || Object.keys(point).some(key => !["time", "x", "y", "easing"].includes(key)) || ![point.time, point.x, point.y].every(Number.isFinite) || point.time < -600 || point.time > 600 || point.time <= previous + 1e-9 || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1 || !EASINGS.includes(point.easing === undefined ? "linear" : point.easing)) throw new Error("關鍵幀時間必須遞增且不可重複，位置為 0–100%，請使用有效的移動曲線。");
      previous = point.time;
    }
  }
  function positionAt(layer, localTime) {
    const points = positionKeyframes(layer);
    if (!points.length) return { x: layer.x, y: layer.y };
    let left = points[0];
    if (localTime <= left.time) return { x: left.x, y: left.y };
    for (const right of points.slice(1)) {
      if (localTime < right.time) {
        let ratio = (localTime - left.time) / (right.time - left.time);
        const easing = left.easing ?? "linear";
        if (easing === "ease_in") ratio *= ratio;
        else if (easing === "ease_out") ratio = 1 - (1 - ratio) ** 2;
        else if (easing === "ease_in_out") ratio = ratio < 0.5 ? 2 * ratio ** 2 : 1 - 2 * (1 - ratio) ** 2;
        return { x: left.x + (right.x - left.x) * ratio, y: left.y + (right.y - left.y) * ratio };
      }
      left = right;
    }
    return { x: left.x, y: left.y };
  }
  function keyframeIndexAt(layer, localTime) { return positionKeyframes(layer).findIndex(point => Math.abs(point.time - localTime) < 1e-6); }
  function upsertPositionKeyframe(layer, localTime, position = positionAt(layer, localTime), easing = null) {
    if (!Number.isFinite(localTime) || localTime < -1e-9 || localTime > layer.end - layer.start + 1e-9) throw new Error("請先將播放游標移到此圖層的顯示範圍內。");
    const points = canonicalPositionKeyframes(layer), index = keyframeIndexAt(layer, localTime);
    const point = { time: Math.max(0, Math.min(layer.end - layer.start, localTime)), x: position.x, y: position.y, easing: easing ?? (index >= 0 ? points[index].easing : "linear") };
    if (index >= 0) points[index] = point; else points.push(point);
    points.sort((a, b) => a.time - b.time);
    const result = { ...layer, position_keyframes: points }; validatePositionKeyframes(result);
    return points;
  }
  function positionChanges(layer, time, position) {
    return positionKeyframes(layer).length ? { position_keyframes: upsertPositionKeyframe(layer, time - layer.start, position) } : position;
  }
  function retimePositionKeyframes(layer, start) {
    return canonicalPositionKeyframes(layer).map(point => ({ ...point, time: point.time + layer.start - start }));
  }
  const core = { MAX_POSITION_KEYFRAMES, EASINGS, positionKeyframes, canonicalPositionKeyframes, validatePositionKeyframes, positionAt, keyframeIndexAt, upsertPositionKeyframe, positionChanges, retimePositionKeyframes };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorMotion = core;
})(typeof window !== "undefined" ? window : globalThis);
