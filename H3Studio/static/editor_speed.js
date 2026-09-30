/* Source-anchored linear speed ramps with exact source/timeline mapping. */
(function (root) {
  "use strict";
  const MAX_SPEED_POINTS = 50, MAX_SPEED_CURVE_POINTS = MAX_SPEED_POINTS, MIN_SPEED = 0.25, MAX_SPEED = 4, TIME_EPSILON = 1e-9;
  const speedCurve = clip => clip.speed_curve || [];
  const canonicalSpeedCurve = clip => speedCurve(clip).map(point => ({ time: point.time, speed: point.speed }));
  function validateSpeedCurve(clip, sourceDuration = null) {
    const points = clip.speed_curve === undefined ? [] : clip.speed_curve;
    if (!Array.isArray(points) || points.length === 1 || points.length > MAX_SPEED_POINTS) throw new Error("變速曲線需有 2–50 個控制點；清除曲線後可使用固定速度。");
    let previous = -Infinity;
    for (const point of points) {
      if (!point || typeof point !== "object" || Array.isArray(point) || Object.keys(point).length !== 2 || Object.keys(point).some(key => !["time", "speed"].includes(key)) || !Number.isFinite(point.time) || !Number.isFinite(point.speed) || point.time < 0 || point.time <= previous + TIME_EPSILON || point.speed < MIN_SPEED || point.speed > MAX_SPEED || (sourceDuration !== null && point.time > sourceDuration + 1e-6)) throw new Error("曲線時間需依序排列且不可重複，速度為 0.25–4 倍，控制點須位於來源素材內。");
      previous = point.time;
    }
    return canonicalSpeedCurve(clip);
  }
  function speedAtSource(clip, sourceTime) {
    const points = speedCurve(clip);
    if (!points.length) return clip.speed === undefined ? 1 : clip.speed;
    let left = points[0];
    if (sourceTime <= left.time) return left.speed;
    for (const right of points.slice(1)) {
      if (sourceTime < right.time) return left.speed + (right.speed - left.speed) * (sourceTime - left.time) / (right.time - left.time);
      left = right;
    }
    return left.speed;
  }
  function segmentTime(length, startSpeed, endSpeed) {
    if (length === 0) return 0;
    const delta = endSpeed - startSpeed;
    return Math.abs(delta) <= Math.abs(startSpeed) * 1e-12 ? length / startSpeed : length * Math.log1p(delta / startSpeed) / delta;
  }
  function segmentSource(elapsed, length, startSpeed, endSpeed) {
    const delta = endSpeed - startSpeed;
    if (Math.abs(delta) <= Math.abs(startSpeed) * 1e-12) return elapsed * startSpeed;
    const slope = delta / length;
    return startSpeed * Math.expm1(slope * elapsed) / slope;
  }
  function curveDuration(clip, sourceStart = clip.in, sourceEnd = clip.out) {
    if (sourceEnd < sourceStart) return -curveDuration(clip, sourceEnd, sourceStart);
    const points = speedCurve(clip);
    if (!points.length) return (sourceEnd - sourceStart) / (clip.speed === undefined ? 1 : clip.speed);
    const boundaries = [sourceStart, ...points.filter(point => point.time > sourceStart && point.time < sourceEnd).map(point => point.time), sourceEnd];
    let time = 0;
    for (let i = 1; i < boundaries.length; i++) time += segmentTime(boundaries[i] - boundaries[i - 1], speedAtSource(clip, boundaries[i - 1]), speedAtSource(clip, boundaries[i]));
    return time;
  }
  const clipDuration = clip => curveDuration(clip);
  const timelineAt = (clip, sourceTime) => curveDuration(clip, clip.in, sourceTime);
  function sourceAtExtended(clip, localTime, sourceDuration = null) {
    const points = speedCurve(clip);
    let source;
    if (!points.length) source = clip.in + localTime * (clip.speed === undefined ? 1 : clip.speed);
    else {
      let elapsed = curveDuration(clip, 0, clip.in) + localTime;
      source = 0;
      if (elapsed > 0) {
        let left = 0, located = false;
        for (const point of points) {
          const right = point.time;
          if (right <= left) continue;
          const startSpeed = speedAtSource(clip, left), endSpeed = point.speed, length = right - left, segment = segmentTime(length, startSpeed, endSpeed);
          if (elapsed <= segment) { source = left + segmentSource(elapsed, length, startSpeed, endSpeed); located = true; break; }
          elapsed -= segment; left = right;
        }
        if (!located) source = left + elapsed * points[points.length - 1].speed;
      }
    }
    source = Math.max(0, source);
    return sourceDuration === null ? source : Math.min(sourceDuration, source);
  }
  function sourceAt(clip, localTime) {
    if (localTime <= 0) return clip.in;
    if (localTime >= clipDuration(clip)) return clip.out;
    return Math.min(clip.out, Math.max(clip.in, sourceAtExtended(clip, localTime)));
  }
  const core = { MAX_SPEED_POINTS, MAX_SPEED_CURVE_POINTS, MIN_SPEED, MAX_SPEED, speedCurve, canonicalSpeedCurve, validateSpeedCurve, speedAtSource, curveDuration, clipDuration, sourceAt, sourceAtExtended, timelineAt };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorSpeed = core;
})(typeof window !== "undefined" ? window : globalThis);
