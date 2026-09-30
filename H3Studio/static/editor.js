/* H3 Studio's timeline editor. Core exports also run in Node without a DOM. */
(function (root) {
  "use strict";
  const { fadeEnvelope, clampFades, VideoLayersController } = typeof module !== "undefined" && module.exports ? require("./editor_layers.js") : root.H3EditorLayers;
  const motion = typeof module !== "undefined" && module.exports ? require("./editor_motion.js") : root.H3EditorMotion;
  const speedMath = typeof module !== "undefined" && module.exports ? require("./editor_speed.js") : root.H3EditorSpeed;
  const { speedCurve, canonicalSpeedCurve, validateSpeedCurve, clipDuration, sourceAt, sourceAtExtended, timelineAt, speedAtSource } = speedMath;
  const { positionKeyframes, canonicalPositionKeyframes, validatePositionKeyframes, positionAt, keyframeIndexAt, upsertPositionKeyframe, positionChanges, retimePositionKeyframes } = motion;
  const clone = value => JSON.parse(JSON.stringify(value));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const uid = () => root.crypto?.randomUUID?.() || "clip_" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  const speedOf = clip => clip.speed === undefined ? 1 : Number(clip.speed);
  const duration = clipDuration;
  const curveFields = clip => speedCurve(clip).length ? { speed_curve: canonicalSpeedCurve(clip) } : {};
  const speedLabel = clip => speedCurve(clip).length ? "曲線變速" : `${speedOf(clip)}×`;
  const audioClips = project => project.audio_clips || [];
  const overlays = project => project.overlays || [];
  const mediaKind = media => media?.kind || "video";
  const mainDuration = project => project.clips.reduce((sum, clip) => sum + duration(clip), 0);
  const totalDuration = project => Math.max(mainDuration(project), ...overlays(project).filter(layer => layer.kind === "video").map(layer => layer.end), 0);
  const nonzeroFades = item => ({ ...((item.fade_in ?? 0) !== 0 ? { fade_in: item.fade_in } : {}), ...((item.fade_out ?? 0) !== 0 ? { fade_out: item.fade_out } : {}) });
  const TEXT_STYLE_DEFAULTS = Object.freeze({ stroke_width: 0, stroke_color: "#000000", fill_mode: "solid", gradient_start: "#ffffff", gradient_end: "#ff8a3d", gradient_angle: 90 });
  function canonicalTextStyle(layer) {
    if (layer.kind !== "text") return {};
    const fields = {};
    for (const [field, fallback] of Object.entries(TEXT_STYLE_DEFAULTS)) {
      const value = layer[field] === undefined ? fallback : typeof layer[field] === "string" && field !== "fill_mode" ? layer[field].toLowerCase() : layer[field];
      if (value !== fallback) fields[field] = value;
    }
    return fields;
  }
  function validateTextStyle(layer) {
    if (layer.kind !== "text") {
      if (Object.keys(TEXT_STYLE_DEFAULTS).some(field => Object.hasOwn(layer, field))) throw new Error("描邊與漸層填色只適用於文字圖層。");
      return;
    }
    const style = { ...TEXT_STYLE_DEFAULTS, ...canonicalTextStyle(layer) };
    if (!Number.isFinite(style.stroke_width) || style.stroke_width < 0 || style.stroke_width > 0.25 || !Number.isFinite(style.gradient_angle) || style.gradient_angle < 0 || style.gradient_angle > 360 || !["solid", "linear_gradient"].includes(style.fill_mode) || ![style.stroke_color, style.gradient_start, style.gradient_end].every(color => typeof color === "string" && /^#[0-9a-f]{6}$/i.test(color))) throw new Error("文字描邊寬度為字級 0–25%，漸層角度為 0–360 度，請使用有效的六位數顏色。");
  }
  function requireTextStyleSupport(value, ready) {
    if (!ready && overlays(value).some(layer => Object.keys(canonicalTextStyle(layer)).length)) throw new Error("目前 Studio 尚未載入文字描邊與漸層更新，文字樣式尚未送出儲存、草稿仍保留。請保留此頁，重新啟動 Studio 後按「重新檢查」再儲存。");
  }
  function editableContent(project) {
    const video = clip => ({ id: clip.id, media_id: clip.media_id, in: clip.in, out: clip.out, volume: clip.volume, speed: speedOf(clip), ...curveFields(clip), ...nonzeroFades(clip) });
    return { name: project.name, clips: project.clips.map(video), audio_clips: audioClips(project).map(clip => ({ ...video(clip), start: clip.start, track: clip.track, fade_in: clip.fade_in, fade_out: clip.fade_out })), overlays: overlays(project).map(canonicalOverlay), width: project.width, height: project.height, fps: project.fps };
  }
  function canonicalOverlay(layer) {
    const common = { id: layer.id, kind: layer.kind, start: layer.start, end: layer.kind === "video" ? layer.start + duration(layer) : layer.end, x: layer.x, y: layer.y, width: layer.width, rotation: layer.rotation, opacity: layer.opacity, ...nonzeroFades(layer) };
    if (Object.hasOwn(layer, "track_id")) common.track_id = layer.track_id;
    if (positionKeyframes(layer).length) common.position_keyframes = canonicalPositionKeyframes(layer);
    return layer.kind === "text" ? { ...common, text: typeof layer.text === "string" ? layer.text.replace(/\r\n?/g, "\n") : layer.text, font_size: layer.font_size, color: layer.color?.toLowerCase(), background: layer.background?.toLowerCase(), bold: layer.bold, align: layer.align, ...canonicalTextStyle(layer) } : layer.kind === "video" ? { ...common, media_id: layer.media_id, in: layer.in, out: layer.out, speed: speedOf(layer), ...curveFields(layer), volume: layer.volume } : { ...common, media_id: layer.media_id };
  }
  function validateOverlays(project, media = null) {
    if (project.overlays !== undefined && !Array.isArray(project.overlays)) throw new Error("圖層資料格式無效。");
    const layers = overlays(project), ids = new Set([...project.clips, ...audioClips(project)].map(clip => clip.id));
    if (layers.length > 50) throw new Error("每個專案最多 50 個圖層。");
    const events = [], videoEvents = [], tracks = new Map(); let previousTrack = null;
    for (const layer of layers) {
      if (!layer || typeof layer.id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(layer.id) || ids.has(layer.id)) throw new Error("圖層編號無效或重複。");
      ids.add(layer.id);
      if (Object.hasOwn(layer, "track_id") && (typeof layer.track_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(layer.track_id))) throw new Error("圖層軌道編號無效。");
      const track = layer.track_id || layer.id;
      if (track !== previousTrack && tracks.has(track)) throw new Error("同軌片段必須放在同一層級群組。");
      if (!tracks.has(track)) tracks.set(track, []);
      tracks.get(track).push(layer); previousTrack = track;
      validatePositionKeyframes(layer);
      validateTextStyle(layer);
      validateSpeedCurve(layer, layer.kind === "video" ? media?.get(layer.media_id)?.duration : undefined);
      if (layer.kind !== "video" && speedCurve(layer).length) throw new Error("曲線變速只適用於影片或音訊片段。");
      if (!["text", "image", "video"].includes(layer.kind) || ![layer.start, layer.end, layer.x, layer.y, layer.width, layer.rotation, layer.opacity].every(Number.isFinite) || layer.start < 0 || layer.end > 600 || layer.end - layer.start < 1 / project.fps - 1e-9 || layer.x < 0 || layer.x > 1 || layer.y < 0 || layer.y > 1 || layer.width < 0.02 || layer.width > 2 || layer.rotation < -180 || layer.rotation > 180 || layer.opacity < 0 || layer.opacity > 1) throw new Error("圖層至少顯示一個影格，時間為 0–600 秒，位置為 0–100%，寬度為 2–200%，透明度為 0–100%。");
      if (![layer.fade_in ?? 0, layer.fade_out ?? 0].every(Number.isFinite) || (layer.fade_in ?? 0) < 0 || (layer.fade_out ?? 0) < 0 || (layer.fade_in ?? 0) + (layer.fade_out ?? 0) > layer.end - layer.start + 1e-9) throw new Error("圖層淡入淡出不可為負值，總長不能超過圖層。");
      if (layer.kind === "text") {
        if (typeof layer.text !== "string" || Array.from(layer.text).length > 500 || layer.text.split(/\r\n|\r|\n/).length > 10 || !Number.isFinite(layer.font_size) || layer.font_size < 0.01 || layer.font_size > 0.3 || !/^#[0-9a-f]{6}$/i.test(layer.color) || !(layer.background === "transparent" || /^#[0-9a-f]{6}$/i.test(layer.background)) || typeof layer.bold !== "boolean" || !["left", "center", "right"].includes(layer.align)) throw new Error("文字最多 500 字、10 行；字級為畫面短邊 1–30%，請使用有效的顏色與對齊設定。");
      } else if (layer.kind === "video") {
        const source = media?.get(layer.media_id);
        if (typeof layer.media_id !== "string" || ![layer.in, layer.out, speedOf(layer), layer.volume].every(Number.isFinite) || layer.in < 0 || layer.out <= layer.in || speedOf(layer) < 0.25 || speedOf(layer) > 4 || layer.volume < 0 || layer.volume > 2 || Math.abs(layer.end - layer.start - duration(layer)) > 1e-6 || (media && (!source || mediaKind(source) !== "video" || layer.out > source.duration + 1e-6))) throw new Error("影片圖層來源、入出點或速度無效；結束時間需符合來源長度與速度。");
        videoEvents.push([layer.start, 1], [layer.end, -1]);
      } else if (typeof layer.media_id !== "string" || (media && (!media.get(layer.media_id) || mediaKind(media.get(layer.media_id)) !== "image"))) throw new Error("圖片圖層找不到可用的圖片素材。");
      events.push([layer.start, 1], [layer.end, -1]);
    }
    for (const clips of tracks.values()) {
      const ordered = [...clips].sort((a, b) => a.start - b.start);
      for (let index = 1; index < ordered.length; index++) if (ordered[index].start < ordered[index - 1].end - 1e-6) throw new Error("同一圖層軌的片段不能重疊，請拖到空白處或其他軌道。");
    }
    let active = 0;
    for (const [, change] of events.sort((a, b) => a[0] - b[0] || a[1] - b[1])) { active += change; if (active > 12) throw new Error("同一時間最多顯示 12 個圖層，請錯開起迄時間。"); }
    active = 0; for (const [, change] of videoEvents.sort((a, b) => a[0] - b[0] || a[1] - b[1])) { active += change; if (active > 3) throw new Error("同一時間最多疊加 3 個影片圖層。"); }
  }
  function overlayRasterKey(layer, width, height) {
    const style = { ...TEXT_STYLE_DEFAULTS, ...canonicalTextStyle(layer) }, gradient = style.fill_mode === "linear_gradient";
    return JSON.stringify({ text: layer.text.replace(/\r\n?/g, "\n"), font_size: layer.font_size, color: gradient ? undefined : layer.color?.toLowerCase(), background: layer.background?.toLowerCase(), bold: layer.bold, align: layer.align, boxWidth: layer.width, width, height,
      ...(style.stroke_width > 0 ? { stroke_width: style.stroke_width, stroke_color: style.stroke_color } : {}),
      ...(gradient ? { fill_mode: style.fill_mode, gradient_start: style.gradient_start, gradient_end: style.gradient_end, gradient_angle: style.gradient_angle } : {}) });
  }
  class PreviewRequestQueue {
    constructor(limit = 2) { this.limit = limit; this.active = 0; this.waiting = []; }
    run(work) { return new Promise((resolve, reject) => { this.waiting.push({ work, resolve, reject }); this.pump(); }); }
    pump() {
      while (this.active < this.limit && this.waiting.length) {
        const next = this.waiting.shift(); this.active++;
        Promise.resolve().then(next.work).then(next.resolve, next.reject).finally(() => { this.active--; this.pump(); });
      }
    }
  }
  function transformOverlay(project, id, changes, media = null) {
    const next = clone(project), layer = overlays(next).find(item => item.id === id);
    if (!layer) throw new Error("找不到圖層。");
    const oldDuration = layer.end - layer.start, before = clone(layer);
    Object.assign(layer, changes);
    // Moving both ends keeps clip-local times. Trimming the left edge offsets
    // every point, including hidden points, without changing the easing curve.
    if (positionKeyframes(before).length && !Object.hasOwn(changes, "position_keyframes") && Object.hasOwn(changes, "start") && !(Object.hasOwn(changes, "end") && Math.abs(layer.end - layer.start - oldDuration) < 1e-6)) layer.position_keyframes = retimePositionKeyframes(before, layer.start);
    if (layer.kind === "video") {
      if (Object.hasOwn(changes, "end") && !["in", "out", "speed", "speed_curve"].some(key => Object.hasOwn(changes, key)) && !(Object.hasOwn(changes, "start") && Math.abs(layer.end - layer.start - oldDuration) < 1e-6)) layer.out = sourceAtExtended(layer, layer.end - layer.start, media?.get(layer.media_id)?.duration ?? Infinity);
      layer.end = layer.start + duration(layer);
    }
    if (layer.end - layer.start < oldDuration - 1e-9) clampFades(layer, layer.end - layer.start);
    next.overlays = core.overlayTrackGroups(next).flatMap(track => [...track.clips].sort((a, b) => a.start - b.start));
    validateProject(next, media);
    return { project: next, layer, changed: signature(next) !== signature(project) };
  }
  function shiftOverlayTime(project, id, mode, delta, media = null) {
    const layer = overlays(project).find(item => item.id === id), minimum = 1 / project.fps;
    if (!layer || !["move", "left", "right"].includes(mode) || !Number.isFinite(delta)) throw new Error("圖層時間調整無效。");
    const video = layer.kind === "video", source = video && media?.get(layer.media_id);
    if (video && mode !== "move" && (!source || mediaKind(source) !== "video")) throw new Error("找不到影片來源，無法修剪圖層。");
    const length = layer.end - layer.start;
    const peers = overlays(project).filter(item => item.id !== id && (item.track_id || item.id) === (layer.track_id || layer.id));
    const previousEnd = Math.max(0, ...peers.filter(item => item.end <= layer.start + 1e-6).map(item => item.end));
    const nextStart = Math.min(600, ...peers.filter(item => item.start >= layer.end - 1e-6).map(item => item.start));
    const appliedDelta = mode === "move" ? clamp(delta, -layer.start, 600 - layer.end) : mode === "left" ? clamp(delta, Math.max(previousEnd - layer.start, video ? timelineAt(layer, 0) : -Infinity), length - minimum) : clamp(delta, minimum - length, Math.min(nextStart - layer.end, video ? timelineAt(layer, source.duration) - length : Infinity));
    const changes = mode === "move" ? { start: layer.start + appliedDelta, end: layer.end + appliedDelta } : mode === "left" ? { start: layer.start + appliedDelta, ...(video ? { in: sourceAtExtended(layer, appliedDelta, source.duration) } : {}) } : { end: layer.end + appliedDelta, ...(video ? { out: sourceAtExtended(layer, length + appliedDelta, source.duration) } : {}) };
    return { ...transformOverlay(project, id, changes, media), appliedDelta, clamped: Math.abs(appliedDelta - delta) > 1e-9 };
  }
  function splitOverlayAt(project, id, time, makeId = uid) {
    const index = overlays(project).findIndex(item => item.id === id), layer = overlays(project)[index], minimum = 1 / project.fps;
    if (!layer || time - layer.start < minimum - 1e-9 || layer.end - time < minimum - 1e-9) throw new Error("圖層分割點前後至少各留一個影格。");
    const { fade_in: fadeIn, fade_out: fadeOut, ...base } = layer;
    base.track_id = layer.track_id || layer.id;
    const first = { ...base, end: time, ...nonzeroFades({ fade_in: Math.min(fadeIn ?? 0, time - layer.start) }) }, second = { ...base, id: makeId(), start: time, ...nonzeroFades({ fade_out: Math.min(fadeOut ?? 0, layer.end - time) }) };
    if (layer.kind === "video") { const sourceTime = sourceAt(layer, time - layer.start); first.out = sourceTime; second.in = sourceTime; Object.assign(first, curveFields(layer)); Object.assign(second, curveFields(layer)); }
    if (positionKeyframes(layer).length) { first.position_keyframes = canonicalPositionKeyframes(layer); second.position_keyframes = retimePositionKeyframes(layer, time); }
    project.overlays.splice(index, 1, first, second); validateProject(project); return second.id;
  }
  class OverlayTransaction {
    constructor(session, id, media = null) { this.session = session; this.id = id; this.media = media; this.original = clone(session.project); this.originalSignature = signature(session.project); this.preview = clone(this.original); this.result = null; this.closed = false; }
    update(changes) { if (this.closed) throw new Error("圖層操作已結束。"); this.result = transformOverlay(this.original, this.id, changes, this.media); this.preview = this.result.project; return this.result; }
    updatePosition(time, position) { const layer = overlays(this.original).find(item => item.id === this.id); return this.update(positionChanges(layer, time, position)); }
    updateTime(mode, delta) { if (this.closed) throw new Error("圖層操作已結束。"); this.result = shiftOverlayTime(this.original, this.id, mode, delta, this.media); this.preview = this.result.project; return this.result; }
    commit() {
      if (this.closed) return false; this.closed = true;
      if (signature(this.session.project) !== this.originalSignature) throw new Error("專案內容已變更，本次圖層調整已取消。");
      return this.result?.changed ? this.session.change(value => { value.overlays = clone(overlays(this.preview)); }) : false;
    }
    cancel() { this.closed = true; this.preview = clone(this.original); return false; }
  }
  function transformClipSpeed(project, kind, id, changes, media = null) {
    if (!["video", "audio", "overlay"].includes(kind)) throw new Error("請先選擇影片片段。");
    validateProject(project, media);
    const next = clone(project), items = kind === "overlay" ? overlays(next) : kind === "audio" ? audioClips(next) : next.clips;
    const clip = items.find(item => item.id === id);
    if (!clip || (kind === "overlay" && clip.kind !== "video")) throw new Error("曲線變速只適用於影片片段。");
    const oldLength = duration(clip), oldEnd = clip.end, track = clip.track_id || clip.id;
    if (Object.hasOwn(changes, "speed")) clip.speed = changes.speed;
    if (Object.hasOwn(changes, "speed_curve")) {
      if (!Array.isArray(changes.speed_curve)) throw new Error("速度曲線格式無效。");
      if (changes.speed_curve.length) clip.speed_curve = clone(changes.speed_curve);
      else delete clip.speed_curve;
    }
    validateSpeedCurve(clip, media?.get(clip.media_id)?.duration);
    const length = duration(clip), durationDelta = length - oldLength;
    let rippleCount = 0;
    if (kind === "overlay") {
      clip.end = clip.start + length;
      // Keep the existing gaps while the edited clip grows or shrinks. Moving
      // both ends leaves followers' source data and clip-local animation intact.
      if (durationDelta !== 0) for (const follower of items) {
        if (follower.id === id || (follower.track_id || follower.id) !== track || follower.start < oldEnd - 1e-6) continue;
        follower.start += durationDelta; follower.end += durationDelta; rippleCount++;
      }
    }
    clampFades(clip, length); validateProject(next, media);
    return { project: next, clip, changed: signature(next) !== signature(project), rippleCount, durationDelta };
  }
  class SpeedCurveTransaction {
    constructor(session, kind, id, media = null) { this.session = session; this.kind = kind; this.id = id; this.media = media; this.original = clone(session.project); this.originalSignature = signature(session.project); this.preview = clone(this.original); this.result = null; this.closed = false; }
    update(points) { if (this.closed) throw new Error("曲線編輯已結束。"); this.result = null; this.preview = clone(this.original); this.result = transformClipSpeed(this.original, this.kind, this.id, { speed_curve: points }, this.media); this.preview = this.result.project; return this.result; }
    commit() {
      if (this.closed) return false; this.closed = true;
      if (signature(this.session.project) !== this.originalSignature) throw new Error("專案內容已變更，本次曲線調整已取消。");
      return this.result?.changed ? this.session.change(value => Object.assign(value, editableContent(this.preview))) : false;
    }
    cancel() { this.closed = true; this.preview = clone(this.original); return false; }
  }
  const signature = project => JSON.stringify(editableContent(project));
  function canonicalSavedSignature(raw) {
    try {
      const value = typeof raw === "string" ? JSON.parse(raw) : null;
      if (!value || typeof value.name !== "string" || !Array.isArray(value.clips) || (value.audio_clips !== undefined && !Array.isArray(value.audio_clips)) || ![value.width, value.height, value.fps].every(Number.isFinite)) return null;
      return signature(value);
    } catch { return null; }
  }
  function resolveDraft(serverProject, record, media = null) {
    const current = clone(serverProject);
    if (record === null || record === undefined) return { status: "none", project: current };
    try {
      const draft = record.project;
      const dimensions = [360, 480, 512, 576, 640, 720, 768, 854, 864, 960, 1024, 1080, 1280, 1440, 1920];
      if (!draft || draft.id !== serverProject.id || typeof draft.name !== "string" || draft.name.length > 200 || ![draft.width, draft.height, draft.fps].every(Number.isFinite) || ![24, 25, 30, 60].includes(draft.fps) || !dimensions.includes(draft.width) || !dimensions.includes(draft.height)) throw new Error("草稿的專案設定不完整或無效。");
      validateProject(draft, media);
      for (const clip of [...draft.clips, ...audioClips(draft)]) if (typeof clip.id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(clip.id) || typeof clip.media_id !== "string") throw new Error("草稿片段識別碼無效。");
      const restored = { ...current, ...editableContent(draft) };
      if (signature(draft) === signature(current)) return { status: "equivalent", project: current, draftProject: clone(draft) };
      if (canonicalSavedSignature(record.savedSignature) === signature(current)) return { status: "recoverable", project: restored, draftProject: clone(draft) };
      return { status: "conflict", project: current, draftProject: clone(draft), reason: "Studio 中的專案已更新，舊草稿已保留。可另存草稿，或下載備份後查看。" };
    } catch (error) { return { status: "invalid", project: current, reason: `草稿暫時無法恢復：${error.message || error} 原始資料仍保留，可下載備份。` }; }
  }
  function formatTime(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const centiseconds = Math.floor(value * 100 + 0.0001);
    return `${String(Math.floor(centiseconds / 6000)).padStart(2, "0")}:${String(Math.floor(centiseconds / 100) % 60).padStart(2, "0")}.${String(centiseconds % 100).padStart(2, "0")}`;
  }
  function locateTime(project, seconds) {
    if (!project.clips.length) return null;
    const total = totalDuration(project), time = clamp(Number(seconds) || 0, 0, total);
    if (time >= mainDuration(project) && total > mainDuration(project) + 1e-9) return null;
    let start = 0;
    for (let index = 0; index < project.clips.length; index++) {
      const clip = project.clips[index], end = start + duration(clip);
      if (time < end - 1e-8 || index === project.clips.length - 1) {
        return { index, clip, start, end, time, sourceTime: sourceAt(clip, time - start) };
      }
      start = end;
    }
    return null;
  }
  function validateProject(project, media = null) {
    if (!Array.isArray(project.clips) || project.clips.length > 50) throw new Error("每個專案最多可加入 50 個片段。");
    if (!Array.isArray(audioClips(project)) || audioClips(project).length > 50) throw new Error("最多可加入 50 個音訊片段。");
    const ids = new Set();
    for (const clip of [...project.clips, ...audioClips(project)]) {
      const isAudio = audioClips(project).includes(clip);
      if (!clip.id || ids.has(clip.id)) throw new Error("片段編號重複，請重新加入片段。");
      ids.add(clip.id);
      const source = media?.get(clip.media_id);
      validateSpeedCurve(clip, source?.duration);
      if (![clip.in, clip.out, clip.volume, speedOf(clip)].every(Number.isFinite) || speedOf(clip) < 0.25 || speedOf(clip) > 4 || clip.in < 0 || duration(clip) < 1 / Number(project.fps || 24) - 1e-9 || clip.volume < 0 || clip.volume > 2) throw new Error("每個片段至少保留一個影格，速度為 0.25–4 倍，音量須在 0–200% 之間。");
      if (media && !source) throw new Error("有片段找不到來源素材，請重新加入素材。");
      if (source && clip.out > Number(source.duration) + 0.001) throw new Error("出點不能超過來源影片長度。");
      if (source && !isAudio && mediaKind(source) !== "video") throw new Error("主影片軌只接受影片；音訊請加入音軌，圖片請加入圖層。");
      if (!isAudio && (![clip.fade_in ?? 0, clip.fade_out ?? 0].every(Number.isFinite) || (clip.fade_in ?? 0) < 0 || (clip.fade_out ?? 0) < 0 || (clip.fade_in ?? 0) + (clip.fade_out ?? 0) > duration(clip) + 1e-9)) throw new Error("影片淡入淡出不可為負值，總長不能超過片段。");
      if (source && isAudio && mediaKind(source) !== "audio" && !source.has_audio) throw new Error("這段影片沒有可分離的原聲。");
      if (isAudio && (![clip.start, clip.fade_in, clip.fade_out].every(Number.isFinite) || clip.start < 0 || clip.start > 600 || !Number.isInteger(clip.track) || clip.track < 0 || clip.track > 3 || clip.fade_in < 0 || clip.fade_out < 0 || clip.fade_in + clip.fade_out > duration(clip) + 1e-9 || duration(clip) > 600)) throw new Error("音訊位置需為 0–600 秒、軌道 A1–A4，淡入與淡出總長不能超過片段。");
    }
    for (const track of [0, 1, 2, 3]) {
      const items = audioClips(project).filter(clip => clip.track === track).sort((a, b) => a.start - b.start);
      for (let index = 1; index < items.length; index++) if (items[index].start < items[index - 1].start + duration(items[index - 1]) - 1e-6) throw new Error("同一音軌不能重疊，請移動片段或改用另一條音軌。");
    }
    if (totalDuration(project) > 600.001) throw new Error("專案總長度最多 10 分鐘，請先縮短片段。");
    validateOverlays(project, media);
  }
  function freeAudioTrack(project, start, length) {
    const track = [0, 1, 2, 3].find(value => !audioClips(project).some(item => item.track === value && item.start < start + length - 1e-6 && item.start + duration(item) > start + 1e-6));
    if (track === undefined) throw new Error("這個位置的四條音軌都有片段，請先移動游標或縮短音訊。");
    return track;
  }
  function audioGain(clip, time) {
    const local = time - clip.start, length = duration(clip);
    if (local < 0 || local >= length) return 0;
    const fade = Math.min(1, clip.fade_in > 0 ? local / clip.fade_in : 1, clip.fade_out > 0 ? (length - local) / clip.fade_out : 1);
    return clip.volume * Math.max(0, fade);
  }
  function splitAudioAt(project, id, time, makeId = uid) {
    const index = audioClips(project).findIndex(clip => clip.id === id), clip = audioClips(project)[index];
    const minimum = 1 / project.fps, local = clip && time - clip.start;
    if (!clip || local < minimum - 1e-9 || duration(clip) - local < minimum - 1e-9) throw new Error("音訊分割點前後至少各留一個影格。");
    const sourceTime = sourceAt(clip, local);
    const first = { ...clip, ...curveFields(clip), out: sourceTime, fade_in: Math.min(clip.fade_in, local), fade_out: 0 };
    const second = { ...clip, ...curveFields(clip), id: makeId(), in: sourceTime, start: time, fade_in: 0, fade_out: Math.min(clip.fade_out, duration(clip) - local) };
    project.audio_clips.splice(index, 1, first, second); validateProject(project); return second.id;
  }
  function detachAudio(project, id, makeId = uid) {
    const index = project.clips.findIndex(clip => clip.id === id), clip = project.clips[index];
    if (!clip) throw new Error("請先選擇影片片段。");
    const start = project.clips.slice(0, index).reduce((sum, item) => sum + duration(item), 0);
    const track = freeAudioTrack(project, start, duration(clip));
    const detached = { id: makeId(), media_id: clip.media_id, in: clip.in, out: clip.out, speed: speedOf(clip), ...curveFields(clip), start, volume: clip.volume ?? 1, fade_in: clip.fade_in ?? 0, fade_out: clip.fade_out ?? 0, track };
    (project.audio_clips ||= []).push(detached); clip.volume = 0; validateProject(project); return detached.id;
  }
  function audioLanes(clips, track) {
    const ends = [], result = [];
    for (const clip of clips.filter(item => item.track === track).sort((a, b) => a.start - b.start)) {
      let lane = ends.findIndex(end => end <= clip.start + 1e-9); if (lane < 0) lane = ends.length;
      ends[lane] = clip.start + duration(clip); result.push({ clip, lane });
    }
    return { items: result, height: Math.max(54, ends.length * 29 + 14) };
  }
  function audioPreviewCandidates(project, time) {
    const end = totalDuration(project), result = [];
    for (const track of [0, 1, 2, 3]) {
      const clips = audioClips(project).filter(clip => clip.track === track && clip.start < end && clip.start + duration(clip) > time).sort((a, b) => a.start - b.start);
      const active = clips.find(clip => clip.start <= time), next = clips.find(clip => clip.start > time && clip.start <= time + 2);
      if (active) result.push(active); if (next) result.push(next);
    }
    return result;
  }
  function splitAt(project, time, makeId = uid) {
    const point = locateTime(project, time);
    const minimum = 1 / Number(project.fps || 24);
    if (!point || time - point.start < minimum - 1e-7 || point.end - time < minimum - 1e-7) throw new Error("請將播放游標放在片段內，前後至少各留一個影格。");
    const { fade_in: fadeIn, fade_out: fadeOut, ...base } = point.clip;
    const first = { ...base, ...curveFields(base), out: point.sourceTime, ...nonzeroFades({ fade_in: Math.min(fadeIn ?? 0, time - point.start) }) };
    const second = { ...base, ...curveFields(base), id: makeId(), in: point.sourceTime, ...nonzeroFades({ fade_out: Math.min(fadeOut ?? 0, point.end - time) }) };
    project.clips.splice(point.index, 1, first, second);
    validateProject(project);
    return second.id;
  }
  function reorder(project, id, targetId, after = false) {
    const from = project.clips.findIndex(c => c.id === id);
    const to = project.clips.findIndex(c => c.id === targetId);
    if (from < 0 || to < 0 || id === targetId) return;
    const [clip] = project.clips.splice(from, 1);
    const target = project.clips.findIndex(c => c.id === targetId);
    project.clips.splice(target + (after ? 1 : 0), 0, clip);
  }
  function promoteClip(project, id, move = false, makeId = uid) {
    const index = project.clips.findIndex(clip => clip.id === id), clip = project.clips[index];
    if (!clip) throw new Error("請先選擇 V1 的影片片段。");
    const start = project.clips.slice(0, index).reduce((sum, item) => sum + duration(item), 0);
    const layer = { id: makeId(), kind: "video", media_id: clip.media_id, in: clip.in, out: clip.out, speed: speedOf(clip), ...curveFields(clip), volume: move ? clip.volume : 0, start, end: start + duration(clip), x: 0.5, y: 0.5, width: 1, rotation: 0, opacity: 1, fade_in: clip.fade_in ?? 0, fade_out: clip.fade_out ?? 0 };
    (project.overlays ||= []).push(layer); if (move) project.clips.splice(index, 1);
    validateProject(project); return layer.id;
  }
  function trimTimelineClip(project, { kind, id, edge, delta }, media) {
    if (!["video", "audio"].includes(kind) || !["left", "right"].includes(edge) || !Number.isFinite(delta)) throw new Error("裁切參數無效。");
    const next = clone(project), clips = kind === "audio" ? audioClips(next) : next.clips;
    const clip = clips.find(item => item.id === id), source = clip && media?.get(clip.media_id);
    if (!clip || !source || !Number.isFinite(Number(source.duration))) throw new Error("找不到裁切片段或來源素材。");
    validateProject(project, media);
    const length = duration(clip), minimum = 1 / Number(project.fps || 24);
    const originalIn = clip.in, originalOut = clip.out, originalStart = clip.start;
    const low = [], high = [];
    if (edge === "left") {
      low.push([timelineAt(clip, 0), "已到來源素材開頭"]);
      high.push([length - minimum, "至少保留一個影格"]);
    } else {
      low.push([minimum - length, "至少保留一個影格"]);
      high.push([timelineAt(clip, Number(source.duration)) - length, "已到來源素材結尾"]);
    }
    if (kind === "video") {
      if (edge === "left") low.push([mainDuration(project) - 600, "影片總長最多 10 分鐘"]);
      else high.push([600 - mainDuration(project), "影片總長最多 10 分鐘"]);
    } else {
      const end = clip.start + length, peers = audioClips(project).filter(item => item.id !== id && item.track === clip.track);
      if (edge === "left") {
        low.push([-clip.start, "不能超過時間軸開頭"], [length - 600, "音訊片段最長 10 分鐘"]);
        high.push([600 - clip.start, "音訊起點最多 10 分鐘"]);
        for (const item of peers) if (item.start < clip.start) low.push([item.start + duration(item) - clip.start, "已碰到同軌前一片段"]);
      } else {
        high.push([600 - length, "音訊片段最長 10 分鐘"]);
        for (const item of peers) if (item.start >= end - 1e-6) high.push([item.start - end, "已碰到同軌下一片段"]);
      }
    }
    const lower = low.reduce((a, b) => a[0] > b[0] ? a : b), upper = high.reduce((a, b) => a[0] < b[0] ? a : b);
    let appliedDelta = clamp(delta, lower[0], upper[0]);
    // Preserve exact source endpoints, including non-decimal frame durations.
    if (Math.abs(appliedDelta) > 1e-10) {
      if (edge === "left") {
        clip.in = sourceAtExtended(project.clips.find(item => item.id === id) || audioClips(project).find(item => item.id === id), appliedDelta, Number(source.duration));
        appliedDelta = timelineAt({ ...clip, in: originalIn, out: originalOut }, clip.in);
        if (kind === "audio") clip.start = Math.max(0, originalStart + appliedDelta);
      } else {
        clip.out = sourceAtExtended({ ...clip, in: originalIn, out: originalOut }, length + appliedDelta, Number(source.duration));
        appliedDelta = timelineAt({ ...clip, in: originalIn, out: originalOut }, clip.out) - length;
      }
      clampFades(clip, duration(clip));
    } else appliedDelta = 0;
    validateProject(next, media);
    const clamped = Math.abs(delta - appliedDelta) > 1e-9;
    return { project: next, clip, changed: signature(next) !== signature(project), clamped, requestedDelta: delta, appliedDelta,
      reason: clamped ? (delta < lower[0] ? lower[1] : upper[1]) : "" };
  }
  class TrimTransaction {
    constructor(session, options, media) {
      this.session = session; this.options = { ...options }; this.media = media;
      this.original = clone(session.project); this.originalSignature = signature(session.project);
      this.preview = clone(this.original); this.result = null; this.closed = false;
    }
    update(delta) {
      if (this.closed) throw new Error("這次裁切操作已結束。");
      this.result = trimTimelineClip(this.original, { ...this.options, delta }, this.media);
      this.preview = this.result.project; return this.result;
    }
    commit() {
      if (this.closed) return false;
      this.closed = true;
      if (signature(this.session.project) !== this.originalSignature) throw new Error("專案內容已變更，本次裁切已取消，請重新拖曳。");
      if (!this.result?.changed) return false;
      // A save may have advanced updated_at while the preview was being edited.
      // Commit editing content only, keeping the current server revision intact.
      return this.session.change(value => { value.clips = clone(this.preview.clips); value.audio_clips = clone(audioClips(this.preview)); });
    }
    cancel() { this.closed = true; this.preview = clone(this.original); return false; }
  }
  class ProjectSession {
    constructor(project, onChange = () => {}) {
      this.project = clone(project);
      this.project.audio_clips ||= [];
      this.project.overlays ||= [];
      this.savedSignature = signature(project);
      this.undoStack = [];
      this.redoStack = [];
      this.inFlight = null;
      this.conflict = null;
      this.group = null;
      this.onChange = onChange;
    }
    get dirty() { return signature(this.project) !== (canonicalSavedSignature(this.savedSignature) || this.savedSignature); }
    change(mutate, group = null) {
      const before = clone(this.project), next = clone(this.project);
      mutate(next);
      validateProject(next);
      if (signature(before) === signature(next)) return false;
      if (!group || group !== this.group) this.undoStack.push(before);
      this.undoStack = this.undoStack.slice(-60);
      this.group = group;
      this.redoStack = [];
      this.project = next;
      this.onChange();
      return true;
    }
    travel(undo) {
      const from = undo ? this.undoStack : this.redoStack, to = undo ? this.redoStack : this.undoStack;
      if (!from.length) return false;
      to.push(clone(this.project));
      const revision = this.project.updated_at;
      this.project = from.pop();
      // History restores editing content, never a stale server revision.
      this.project.updated_at = revision;
      this.group = null;
      this.onChange();
      return true;
    }
    save(write) {
      if (this.inFlight) return this.inFlight;
      if (this.conflict) return Promise.reject(this.conflict);
      this.inFlight = (async () => {
        while (this.dirty) {
          const snapshot = clone(this.project);
          let response;
          try { response = await write(snapshot); }
          catch (error) { if (error.status === 409) this.conflict = error; throw error; }
          const saved = response.project || response;
          validateProject(saved);
          if (saved.id !== snapshot.id || !saved.updated_at) throw new Error("伺服器回傳的專案版本不完整，請重新確認儲存結果。");
          if ((audioClips(snapshot).length && !saved.audio_clips) || snapshot.clips.some(clip => speedOf(clip) !== 1 && speedOf(saved.clips.find(item => item.id === clip.id) || {}) !== speedOf(clip))) throw new Error("Studio 後端尚未支援音軌或速度設定，請更新並重啟 Studio；你的草稿仍保留。");
          for (const [expected, actual] of [[snapshot.clips, saved.clips], [audioClips(snapshot), audioClips(saved)]]) {
            if (expected.some(clip => JSON.stringify(canonicalSpeedCurve(clip)) !== JSON.stringify(canonicalSpeedCurve(actual.find(item => item.id === clip.id) || {})))) throw new Error("Studio 後端未完整保存曲線變速，請重新啟動 Studio；目前草稿仍保留。");
          }
          if (snapshot.clips.some(clip => JSON.stringify(nonzeroFades(clip)) !== JSON.stringify(nonzeroFades(saved.clips.find(item => item.id === clip.id) || {})))) throw new Error("Studio 後端未保存影片淡入淡出，請重新啟動 Studio；目前草稿仍保留。");
          if (overlays(snapshot).some(layer => JSON.stringify(canonicalTextStyle(layer)) !== JSON.stringify(canonicalTextStyle(overlays(saved).find(item => item.id === layer.id) || {})))) throw new Error("Studio 後端未完整保存文字描邊／漸層設定，請重新啟動 Studio 後再儲存；文字樣式草稿仍保留在此頁。");
          if (overlays(snapshot).length && JSON.stringify(overlays(snapshot).map(canonicalOverlay)) !== JSON.stringify(overlays(saved).map(canonicalOverlay))) throw new Error("Studio 後端未完整保存文字／圖片圖層，請重新啟動 Studio；圖層草稿仍保留在此頁。");
          const unchanged = signature(this.project) === signature(snapshot);
          if (unchanged) { this.project = clone(saved); this.project.audio_clips ||= []; this.project.overlays ||= []; }
          else this.project.updated_at = saved.updated_at;
          this.savedSignature = signature(unchanged ? saved : snapshot);
          this.onChange();
        }
        return clone(this.project);
      })().finally(() => { this.inFlight = null; this.onChange(); });
      this.onChange();
      return this.inFlight;
    }
  }
  class ProjectAutoSaver {
    constructor(session, write, options = {}) {
      this.session = session; this.write = write; this.options = options; this.delay = options.delay ?? 1000;
      this.setTimer = options.setTimer || ((fn, delay) => setTimeout(fn, delay));
      this.clearTimer = options.clearTimer || (timer => clearTimeout(timer));
      this.timer = null; this.pending = null; this.failed = null;
    }
    cancel() { if (this.timer !== null) this.clearTimer(this.timer); this.timer = null; }
    schedule() {
      this.cancel();
      if (!this.session.dirty || this.session.conflict || this.failed || this.pending || this.session.inFlight) return;
      this.timer = this.setTimer(() => {
        this.timer = null;
        if (this.options.canSave && !this.options.canSave()) { this.schedule(); return; }
        this.flush().catch(() => {});
      }, this.delay);
      this.options.onState?.();
    }
    flush() {
      this.cancel();
      if (this.pending) return this.pending;
      this.failed = null;
      this.pending = Promise.resolve().then(() => this.session.save(this.write)).then(saved => {
        this.options.onSaved?.(saved); return saved;
      }).catch(error => {
        this.failed = error; this.cancel(); this.options.onError?.(error); throw error;
      }).finally(() => { this.pending = null; this.options.onState?.(); });
      this.options.onState?.(); return this.pending;
    }
  }
  class VideoDeck {
    constructor(elements, callbacks = {}) {
      this.callbacks = callbacks; this.activeIndex = 0; this.pending = null; this.serial = 0; this.playAttempt = 0; this.wantsPlay = false; this.held = false;
      this.buffers = elements.map((element, index) => {
        const buffer = { element, index, key: null, url: null, target: null, waitingSeek: false, assigned: false, ready: false, failed: false };
        element.preload = "auto"; element.preservesPitch = true;
        for (const event of ["loadedmetadata", "loadeddata", "canplay", "seeked"]) element.addEventListener(event, () => this.settle(buffer, event));
        element.addEventListener("waiting", () => { if (index === this.activeIndex && this.wantsPlay) this.callbacks.buffering?.(true); });
        element.addEventListener("playing", () => { if (index === this.activeIndex && !this.pending) this.callbacks.buffering?.(false); });
        element.addEventListener("ended", () => { if (index === this.activeIndex && this.wantsPlay && !this.pending) this.callbacks.ended?.(); });
        element.addEventListener("error", () => { if (!buffer.url) return; buffer.ready = false; buffer.failed = true; if (index === this.activeIndex || this.pending?.index === index) this.callbacks.error?.(new Error("這份預覽無法播放，請重新匯入素材。"), buffer.url); });
        return buffer;
      });
    }
    get active() { return this.buffers[this.activeIndex].element; }
    key(point) { return [point.clip.id, point.clip.media_id, point.clip.in, point.clip.out, speedOf(point.clip), JSON.stringify(canonicalSpeedCurve(point.clip))].join("|"); }
    prepare(buffer, point, url) {
      const element = buffer.element, changed = buffer.url !== url || buffer.failed;
      buffer.key = this.key(point); buffer.point = point; buffer.target = point.sourceTime;
      buffer.assigned = false; buffer.waitingSeek = false; buffer.ready = false; buffer.failed = false;
      element.playbackRate = speedAtSource(point.clip, point.sourceTime); element.preservesPitch = true;
      if (changed) { element.pause(); buffer.url = url; element.src = url; element.load(); }
      this.settle(buffer, "prepare");
    }
    settle(buffer, event) {
      const element = buffer.element;
      if (buffer.target === null || element.readyState < 1) return;
      // A playing source may recover from network buffering far beyond its initial trim target.
      if (event === "canplay" && buffer.ready && buffer.assigned && !buffer.waitingSeek && !element.seeking && element.readyState >= 2 && buffer.index === this.activeIndex && !this.pending) { this.callbacks.buffering?.(false); return; }
      if (!buffer.assigned) {
        buffer.assigned = true;
        const target = clamp(buffer.target, 0, Number.isFinite(element.duration) ? Math.max(0, element.duration - 0.00001) : buffer.target);
        if (Math.abs(element.currentTime - target) > 0.002) {
          buffer.waitingSeek = true; element.currentTime = target;
        }
      }
      if (event === "seeked" && Math.abs(element.currentTime - buffer.target) < 0.004) buffer.waitingSeek = false;
      if (buffer.waitingSeek || element.seeking || element.readyState < 2 || Math.abs(element.currentTime - buffer.target) > 0.004) return;
      buffer.ready = true;
      if (this.pending?.index === buffer.index && this.pending.serial === this.serial) this.activate(buffer);
      else if (buffer.index === this.activeIndex && !this.pending) this.callbacks.buffering?.(false);
    }
    request(point, url, play) {
      this.wantsPlay = play; const serial = ++this.serial, key = this.key(point);
      let index = this.buffers.findIndex(buffer => buffer.key === key && buffer.url === url && buffer.ready && !buffer.element.seeking && Math.abs(buffer.element.currentTime - point.sourceTime) < 0.003);
      if (index < 0) index = this.buffers[this.activeIndex].key === key && this.buffers[this.activeIndex].url === url ? this.activeIndex : 1 - this.activeIndex;
      const buffer = this.buffers[index];
      this.pending = { index, serial }; this.buffers.forEach(item => item.element.pause());
      if (buffer.key === key && buffer.url === url && buffer.ready && !buffer.element.seeking && Math.abs(buffer.element.currentTime - point.sourceTime) < 0.003) {
        buffer.point = point; buffer.target = point.sourceTime; this.activate(buffer);
      } else { this.callbacks.buffering?.(true); this.prepare(buffer, point, url); }
    }
    activate(buffer) {
      this.activeIndex = buffer.index; this.pending = null;
      this.buffers.forEach(item => { item.element.hidden = item !== buffer; item.element.muted = item !== buffer; if (item !== buffer) item.element.pause(); });
      this.callbacks.active?.(buffer.element, buffer.point);
      this.callbacks.buffering?.(false); this.resume();
    }
    resume() {
      if (!this.wantsPlay || this.pending || this.held || !this.active.paused) return;
      const serial = this.serial, attempt = ++this.playAttempt, element = this.active;
      Promise.resolve(element.play()).catch(error => { if (serial === this.serial && attempt === this.playAttempt && this.wantsPlay && !this.held && element === this.active) { this.pause(); this.callbacks.error?.(error); } });
    }
    hold(held) { this.held = held; if (held) { this.playAttempt++; this.active.pause(); } else this.resume(); }
    pause() { this.wantsPlay = false; this.serial++; this.playAttempt++; if (this.pending) this.pending.serial = this.serial; this.buffers.forEach(buffer => buffer.element.pause()); }
    preload(point, url) {
      if (this.pending || !point) return;
      const buffer = this.buffers[1 - this.activeIndex], key = this.key(point);
      if (buffer.key === key && buffer.url === url && Math.abs((buffer.target ?? -1) - point.sourceTime) < 0.006) return;
      buffer.element.pause(); buffer.element.muted = true; this.prepare(buffer, point, url);
    }
    clear() { this.pause(); this.pending = null; for (const buffer of this.buffers) { buffer.key = null; buffer.target = null; buffer.ready = false; if (buffer.url) { buffer.url = null; buffer.element.removeAttribute("src"); buffer.element.load(); } } }
  }
  class AudioPreview {
    constructor(makeElement, context, onError = () => {}) { this.makeElement = makeElement; this.context = context; this.onError = onError; this.slots = new Map(); this.serial = 0; }
    sync(project, media, time, playing) {
      let ready = true; const keep = new Set();
      for (const clip of audioPreviewCandidates(project, time)) {
        const end = Math.min(clip.start + duration(clip), totalDuration(project));
        if (clip.start >= totalDuration(project) || end <= time || clip.start > time + 2) continue;
        keep.add(clip.id); const active = time >= clip.start && time < end;
        let slot = this.slots.get(clip.id);
        if (!slot || slot.mediaId !== clip.media_id) {
          if (slot) this.release(clip.id);
          const element = this.makeElement(); element.preload = "auto"; element.preservesPitch = true;
          slot = { element, mediaId: clip.media_id, target: clip.in, waitingSeek: false, assigned: false, failed: false, serial: 0, key: null };
          const source = this.context.createMediaElementSource(element), gain = this.context.createGain(); source.connect(gain); gain.connect(this.context.destination);
          slot.source = source; slot.gain = gain;
          element.addEventListener("seeked", () => { if (Math.abs(element.currentTime - slot.target) < 0.005) slot.waitingSeek = false; });
          element.addEventListener("error", () => { if (!slot.failed) { slot.failed = true; this.onError(new Error("一段音訊無法預覽，請檢查或重新匯入。")); } });
          element.src = media.get(clip.media_id)?.url || `/api/editor/media/${encodeURIComponent(clip.media_id)}/file`; element.load(); this.slots.set(clip.id, slot);
        }
        const element = slot.element, target = sourceAt(clip, Math.max(0, time - clip.start));
        const key = [clip.in, clip.out, clip.start, speedOf(clip), JSON.stringify(canonicalSpeedCurve(clip))].join("|");
        if (key !== slot.key) { slot.key = key; slot.assigned = false; slot.waitingSeek = false; }
        element.playbackRate = speedAtSource(clip, target); slot.gain.gain.value = audioGain(clip, time);
        if (slot.failed) { ready = false; continue; }
        if (element.readyState >= 1 && !slot.waitingSeek) {
          const threshold = slot.assigned ? 0.10 : 0.003; slot.assigned = true;
          if (Math.abs(element.currentTime - target) > threshold) { element.pause(); slot.serial++; slot.target = target; slot.waitingSeek = true; element.currentTime = target; }
        }
        const prepared = element.readyState >= 2 && !slot.waitingSeek && !element.seeking;
        if (active && !prepared) ready = false;
        if (active && playing && prepared && element.paused) {
          const serial = ++slot.serial;
          Promise.resolve(element.play()).catch(error => { if (serial === slot.serial && this.slots.get(clip.id) === slot) this.onError(error); });
        } else if (!active || !playing || !prepared) { if (!element.paused) { slot.serial++; element.pause(); } }
      }
      for (const id of this.slots.keys()) if (!keep.has(id)) this.release(id);
      if (!ready) this.pause();
      return ready;
    }
    pause() { for (const slot of this.slots.values()) { slot.serial++; slot.element.pause(); } }
    seek() { this.pause(); for (const slot of this.slots.values()) { slot.assigned = false; slot.waitingSeek = false; } }
    release(id) { const slot = this.slots.get(id); if (!slot) return; slot.serial++; slot.element.pause(); slot.element.removeAttribute("src"); slot.element.load(); slot.source.disconnect(); slot.gain.disconnect(); this.slots.delete(id); }
    clear() { for (const id of [...this.slots.keys()]) this.release(id); }
  }
  async function requireCapabilities(read) {
    let capabilities;
    const outdated = () => Object.assign(new Error("Studio 後端尚未載入音訊／變速更新，請重新啟動 Studio 後重新整理。"), { outdatedBackend: true });
    try { capabilities = await read("/api/editor/capabilities"); } catch (error) { if (error.status === 404) throw outdated(); throw error; }
    if (!(Number(capabilities.schema_version) >= 2)) throw outdated();
    return capabilities;
  }
  const core = { ...motion, ...speedMath, transformClipSpeed, SpeedCurveTransaction, clone, clamp, speedOf, duration, speedLabel, mainDuration, totalDuration, fadeEnvelope, clampFades, VideoLayersController, nonzeroFades, promoteClip, audioClips, overlays, TEXT_STYLE_DEFAULTS, canonicalTextStyle, validateTextStyle, requireTextStyleSupport, canonicalOverlay, validateOverlays, overlayRasterKey, PreviewRequestQueue, transformOverlay, shiftOverlayTime, splitOverlayAt, OverlayTransaction, mediaKind, audioGain, audioLanes, audioPreviewCandidates, freeAudioTrack, editableContent, signature, canonicalSavedSignature, resolveDraft, formatTime, locateTime, validateProject, splitAt, splitAudioAt, detachAudio, reorder, trimTimelineClip, TrimTransaction, ProjectSession, ProjectAutoSaver, VideoDeck, AudioPreview, requireCapabilities };
  const trackDragModule = typeof module !== "undefined" && module.exports ? require("./editor_track_drag.js") : root.H3EditorTrackDrag;
  Object.assign(core, trackDragModule.createTrackDragCore(core));
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorCore = core;
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const state = { session: null, media: new Map(), projects: [], selected: null, playhead: 0, zoom: 60, busy: true,
    playing: false, previewIndex: -1, loadedMedia: null, pendingSeek: null, seekSerial: 0, selectedKind: "video", queuedSeek: null,
    exportJob: null, exportTimer: null, jobPage: 1, jobsLoading: false, draft: null, audio: null, mixer: null, toastTimer: null,
    previews: new Map(), previewRequests: new Set(), previewTimers: new Map(), buffering: false, audioBuffering: false, backendReady: false,
    trimDrag: null, trimSuppressUntil: 0, recoveries: [], archivesReady: false, overlayDrag: null, trackDrag: null,
    textOverlaysReady: false, textStyleReady: false, textStyleChecking: false, imageOverlaysReady: false, videoOverlaysReady: false, visualFadesReady: false, positionKeyframesReady: false, positionChecking: false, speedCurvesReady: false, overlayTracksReady: false, speedDrag: null,
    layerBuffering: false, lastPlaybackTick: null, rasterCache: new Map(), rasterNodes: new Map(), rasterQueue: new PreviewRequestQueue(2) };
  let video = $("previewVideo");
  const videoElements = [video, $("previewVideoNext")];
  const project = () => state.speedDrag?.preview || state.overlayDrag?.transaction.preview || state.trimDrag?.transaction.preview || state.session?.project;
  const exporting = () => ["queued", "running"].includes(state.exportJob?.status);
  const locked = () => !state.backendReady || state.busy || exporting() || !state.session || Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag);
  const canSwitch = () => !locked();
  const selectedClip = () => (state.selectedKind === "overlay" ? overlays(project() || {}) : state.selectedKind === "audio" ? audioClips(project() || {}) : project()?.clips || []).find(clip => clip.id === state.selected);
  const previewViewport = root.H3EditorPreview.mountPreviewViewport({ stage: $("videoStage"), canvas: $("videoCanvas"),
    zoomSelect: $("previewZoom"), zoomIn: $("previewZoomIn"), zoomOut: $("previewZoomOut"), fitButton: $("previewFit"), handButton: $("previewHand") },
    { window: root, document, isBlocked: () => !state.session || state.busy || Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag) });
  const trackDragUI = root.H3EditorTrackDragUI.mountTrackDrag($("timelineCanvas"));
  const speedEditor = root.H3EditorSpeedUI.mountSpeedEditor($("speedEditor"), {
    onChangeCurve: points => applySelectedSpeed({ speed_curve: points }),
    onChangeMode: mode => {
      const clip = selectedSpeedVideo(); if (!clip) return;
      if (mode === "normal") applySelectedSpeed({ speed_curve: [] });
      else if (!speedCurve(clip).length) applySelectedSpeed({ speed_curve: root.H3EditorSpeedUI.presetPoints("custom", clip.in, clip.out, speedOf(clip)) });
    },
    onSeekSource: seekSelectedSource,
    onGestureStart: beginSpeedGesture,
    onGesturePreview: previewSpeedGesture,
    onGestureCommit: finishSpeedGesture,
    onGestureCancel: () => finishSpeedGesture(null),
  });
  function selectedSpeedVideo() { const clip = selectedClip(); return clip && (state.selectedKind === "video" || state.selectedKind === "overlay" && clip.kind === "video") ? clip : null; }
  function clipTimelineStart(value, kind, id) {
    if (kind === "audio") return audioClips(value).find(clip => clip.id === id)?.start || 0;
    if (kind === "overlay") return overlays(value).find(clip => clip.id === id)?.start || 0;
    const index = value.clips.findIndex(clip => clip.id === id);
    return value.clips.slice(0, Math.max(0, index)).reduce((sum, clip) => sum + duration(clip), 0);
  }
  function renderSpeedControls() {
    const clip = selectedSpeedVideo(), curve = clip && speedCurve(clip).length > 0;
    $("speedEditor").classList.toggle("hidden", !clip);
    $("normalClipSpeed").classList.toggle("hidden", Boolean(curve)); $("normalSpeedPresets").classList.toggle("hidden", Boolean(curve)); $("normalLayerSpeed").classList.toggle("hidden", Boolean(curve));
    speedEditor.render({ clipId: clip ? `${state.selectedKind}:${clip.id}` : null, sourceIn: clip?.in || 0, sourceOut: clip?.out || 1,
      normalSpeed: clip ? speedOf(clip) : 1, points: clip ? canonicalSpeedCurve(clip) : [], mode: curve ? "curve" : "normal",
      sourcePlayhead: clip ? sourceAt(clip, state.playhead - clipTimelineStart(project(), state.selectedKind, clip.id)) : 0,
      curveDuration: clip ? duration(clip) : 0, disabled: locked() && !state.speedDrag, supported: state.speedCurvesReady });
  }
  function seekSelectedSource(sourceTime) {
    const clip = selectedSpeedVideo(); if (!clip || state.speedDrag || state.trimDrag || state.overlayDrag) return;
    pause(); seek(clipTimelineStart(project(), state.selectedKind, clip.id) + timelineAt(clip, clamp(sourceTime, clip.in, clip.out)), false);
  }
  function applySelectedSpeed(changes) {
    if (locked()) { renderSpeedControls(); return; }
    if (changes.speed_curve?.length && !state.speedCurvesReady) { renderSpeedControls(); throw new Error("請先儲存並重新啟動 Studio，以載入曲線變速。"); }
    const clip = selectedClip(); if (!clip) return;
    const kind = state.selectedKind, source = sourceAt(clip, state.playhead - clipTimelineStart(project(), kind, clip.id));
    pause();
    try {
      const result = transformClipSpeed(project(), kind, clip.id, changes, state.media);
      if (result.changed) state.session.change(value => Object.assign(value, editableContent(result.project)));
      const current = selectedClip(); state.playhead = clamp(clipTimelineStart(project(), kind, clip.id) + timelineAt(current, source), 0, totalDuration(project()));
      render(); seek(state.playhead, false);
    } finally { renderSpeedControls(); }
  }
  function beginSpeedGesture() {
    if (locked() || !state.speedCurvesReady || !selectedSpeedVideo()) throw new Error("目前無法調整速度曲線。");
    const clip = selectedClip(); pause();
    const transaction = new SpeedCurveTransaction(state.session, state.selectedKind, clip.id, state.media);
    state.speedDrag = { transaction, preview: transaction.preview, kind: state.selectedKind, id: clip.id,
      source: sourceAt(clip, state.playhead - clipTimelineStart(project(), state.selectedKind, clip.id)), invalid: false };
    renderDisabled();
  }
  function previewSpeedGesture(points) {
    const drag = state.speedDrag; if (!drag) return;
    try {
      drag.transaction.update(points); drag.preview = drag.transaction.preview; drag.invalid = false;
      state.playhead = clamp(clipTimelineStart(project(), drag.kind, drag.id) + timelineAt(selectedClip(), drag.source), 0, totalDuration(project()));
      renderTimeline(); renderInspector(); updatePlayhead();
    } catch (error) {
      drag.invalid = true; drag.error = error; drag.preview = drag.transaction.preview;
      state.playhead = clamp(clipTimelineStart(project(), drag.kind, drag.id) + timelineAt(selectedClip(), drag.source), 0, totalDuration(project()));
      renderTimeline(); renderInspector(); updatePlayhead(); throw error;
    }
  }
  function finishSpeedGesture(points) {
    const drag = state.speedDrag; if (!drag) return;
    if (points) { try { previewSpeedGesture(points); } catch (error) { drag.invalid = true; drag.error = error; } }
    state.speedDrag = null; state.trimSuppressUntil = Date.now() + 450;
    try {
      if (points && !drag.invalid) drag.transaction.commit(); else drag.transaction.cancel();
      if (drag.invalid) throw drag.error;
    } finally {
      state.playhead = clamp(clipTimelineStart(project(), drag.kind, drag.id) + timelineAt(selectedClip(), drag.source), 0, totalDuration(project()));
      render(); seek(state.playhead, false);
    }
  }
  const deck = new VideoDeck(videoElements, {
    active(element, point) { video = element; state.previewIndex = point.index; state.loadedMedia = point.clip.media_id; applyVolume(point.clip); preloadNext(); },
    buffering(value) { state.buffering = value; if (value) state.mixer?.pause(); renderPlaybackStatus(); },
    ended() { nextPreviewClip(); },
    error(error, url) {
      const point = project() && locateTime(project(), state.playhead), cached = point && state.previews.get(point.clip.media_id);
      if (cached?.url === url) { state.previews.set(point.clip.media_id, { ...cached, status: "failed", error: "預覽快取無法播放，已改用原始素材。" }); if (!state.trimDrag && !state.overlayDrag) seek(state.playhead, state.playing); renderCacheStatus(); return; }
      pause(); errorNotice(error);
    },
  });
  const videoLayers = new VideoLayersController(layer => state.rasterNodes.get(layer.id)?.video, {
    resolveUrl: layer => previewUrl(layer.media_id),
    onError(error, layer, url) {
      const cached = layer && state.previews.get(layer.media_id);
      if (cached?.url === url) { state.previews.set(layer.media_id, { ...cached, status: "failed", error: "預覽快取無法播放，已改用原始素材。" }); return; }
      pause(); errorNotice(error);
    },
  });
  function notify(message) {
    $("toast").textContent = message; $("toast").classList.remove("hidden");
    clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 4200);
  }
  function errorNotice(error) {
    $("noticeText").textContent = error.message || String(error); $("notice").classList.remove("hidden");
    renderStatus();
  }
  const action = fn => async event => { try { await fn(event); } catch (error) { errorNotice(error); } };
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || `請求未完成（${response.status}），請稍後再試。`); error.status = response.status; error.data = data; throw error; }
    return data;
  }
  const json = (method, data = {}) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  const list = (data, key) => Array.isArray(data) ? data : (data[key] || data.items || []);
  function storageGet(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
  function storagePut(key, value) { try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
  function readDraft(id) {
    try {
      const raw = localStorage.getItem(`h3-editor-draft:${id}`);
      if (raw === null) return null;
      try { const value = JSON.parse(raw); return value && typeof value === "object" && !Array.isArray(value) ? value : { rawText: raw }; } catch { return { rawText: raw }; }
    } catch { return null; }
  }
  function storedRecoveries(id) {
    const prefix = `h3-editor-recovery:${id}:`, records = [];
    try { for (let index = 0; index < localStorage.length; index++) { const key = localStorage.key(index); if (key?.startsWith(prefix)) { const value = storageGet(key); if (value?.record) records.push({ ...value, key }); } } } catch {}
    return records.sort((a, b) => b.createdAt - a.createdAt);
  }
  function archiveDraft(id, record, reason) {
    if (!record) return null;
    const existing = storedRecoveries(id).find(item => JSON.stringify(item.record) === JSON.stringify(record));
    if (existing) return existing;
    const item = { key: `h3-editor-recovery:${id}:${uid()}`, record: clone(record), reason, createdAt: Date.now(), dismissed: false };
    return storagePut(item.key, item) ? item : null;
  }
  function rememberDraft(session = state.session) {
    if (!session) return;
    if (state.session && state.session !== session && state.session.project.id === session.project.id) return;
    // An unarchived recovery record must not be overwritten by a later edit.
    if (session.protectedDraft) {
      const recovery = archiveDraft(session.project.id, session.protectedDraft, "原草稿保護備份");
      if (recovery) {
        session.protectedDraft = null;
        if (state.session === session) {
          state.recoveries = storedRecoveries(session.project.id);
          if (state.draft && JSON.stringify(state.draft.record) === JSON.stringify(recovery.record)) state.draft = { ...recovery, resolution: state.draft.resolution };
          renderRecovery();
        }
      }
      else { session.localDraftFailed = true; return; }
    }
    const key = `h3-editor-draft:${session.project.id}`;
    const existing = readDraft(session.project.id);
    if (existing && existing.owner !== session.draftOwner) {
      if (!session.dirty) return;
      if (!archiveDraft(session.project.id, existing, "conflict")) { session.protectedDraft = existing; session.localDraftFailed = true; return; }
    }
    const success = storagePut(key, session.dirty ? { project: session.project, savedSignature: session.savedSignature, savedAt: Date.now(), owner: session.draftOwner } : null);
    session.localDraftFailed = !success;
  }
  function sessionChanged(session) {
    if (state.session && state.session !== session && state.session.project.id === session.project.id) return;
    rememberDraft(session);
    session.autosaver?.schedule();
    if (state.session === session) renderStatus();
  }
  function installProject(value) {
    state.session?.autosaver?.cancel();
    pause(); deck.clear(); state.mixer?.clear(); videoLayers.clear();
    const session = new ProjectSession(value);
    session.draftOwner = uid();
    session.autosaver = new ProjectAutoSaver(session, snapshot => {
      validateProject(snapshot, state.media);
      requireTextStyleSupport(snapshot, state.textStyleReady);
      if (overlays(snapshot).some(layer => layer.kind === "text" ? !state.textOverlaysReady : layer.kind === "video" ? !state.videoOverlaysReady : !state.imageOverlaysReady)) throw new Error("Studio 後端尚未載入圖層更新，請重新啟動 Studio 後再儲存；目前圖層草稿仍保留。");
      if (!state.overlayTracksReady && overlays(snapshot).some(layer => Object.hasOwn(layer, "track_id"))) throw new Error("Studio 尚未載入同軌剪輯更新，請重新啟動 Studio；目前草稿仍保留。");
      if (!state.visualFadesReady && [...snapshot.clips, ...overlays(snapshot)].some(item => (item.fade_in ?? 0) > 0 || (item.fade_out ?? 0) > 0)) throw new Error("Studio 後端尚未載入淡入淡出更新，請重新啟動 Studio 後再儲存。");
      return api(`/api/editor/projects/${snapshot.id}`, json("PUT", { ...snapshot, name: snapshot.name.trim() || "未命名專案" }));
    }, {
      canSave: () => state.session === session && !state.trimDrag && !state.overlayDrag && !state.speedDrag && !state.trackDrag && !state.busy,
      onState: () => { if (state.session === session) renderStatus(); },
      onSaved: saved => { if (state.session === session) { state.projects = state.projects.map(item => item.id === saved.id ? saved : item); renderProjectList(); renderStatus(); } },
      onError: error => { if (state.session === session) { errorNotice(new Error(error.status === 409 ? "專案已在其他視窗更新。自動儲存已停止；你的剪輯仍在此頁，請另存新專案。" : `自動儲存未完成：${error.message} ${session.localDraftFailed ? "目前剪輯仍在此頁，但瀏覽器備份失敗；請保持頁面開啟並按「儲存」重試。" : "草稿仍保留，請按「儲存」重試。"}`)); } },
    });
    session.onChange = () => sessionChanged(session);
    state.session = session;
    state.selected = value.clips[0]?.id || null; state.selectedKind = "video"; state.playhead = 0; state.previewIndex = -1;
    state.loadedMedia = null; state.pendingSeek = null; state.exportJob = null;
    clearTimeout(state.exportTimer);
    storagePut("h3-editor-last-project", value.id);
    const url = new URL(location.href); url.searchParams.set("project", value.id); history.replaceState(null, "", url);
    const rawDraft = readDraft(value.id), resolution = resolveDraft(value, rawDraft, state.media);
    state.draft = null;
    if (rawDraft) {
      const recovery = archiveDraft(value.id, rawDraft, resolution.status);
      if (!recovery) { session.protectedDraft = rawDraft; session.localDraftFailed = true; }
      if (resolution.status === "recoverable") {
        session.undoStack.push(clone(session.project)); session.project = resolution.project;
        notify("已恢復上次尚未儲存的剪輯，可直接續編；正在自動儲存。");
      } else if (["conflict", "invalid"].includes(resolution.status)) {
        state.draft = { ...(recovery || { record: rawDraft }), resolution };
      }
      if (recovery) storagePut(`h3-editor-draft:${value.id}`, null);
    }
    state.recoveries = storedRecoveries(value.id);
    if (!state.draft) {
      const pending = state.recoveries.find(item => !item.dismissed && ["conflict", "invalid"].includes(item.reason));
      if (pending) state.draft = { ...pending, resolution: resolveDraft(value, pending.record, state.media) };
    }
    state.selectedKind = session.project.clips.length ? "video" : overlays(session.project).length ? "overlay" : "audio";
    state.selected = (state.selectedKind === "video" ? session.project.clips : state.selectedKind === "overlay" ? overlays(session.project) : audioClips(session.project))[0]?.id || null;
    renderRecovery();
    if (session.dirty) sessionChanged(session);
    render(); seek(0, false);
    const savedExport = storageGet(`h3-editor-export:${value.id}`);
    if (savedExport?.id) { state.exportJob = savedExport; renderExport(); if (exporting()) pollExport(); }
  }
  function renderStatus() {
    if (!state.session) return;
    const session = state.session;
    $("saveStatus").textContent = session.conflict ? "版本衝突 · 請另存新專案" : session.inFlight ? "正在儲存至 Studio…" : session.autosaver?.failed ? "自動儲存未完成 · 按「儲存」重試" : session.dirty ? (session.localDraftFailed ? "待儲存" : "等待自動儲存 · 已保留瀏覽器草稿") : "已儲存至 Studio";
    if (session.localDraftFailed) $("saveStatus").textContent += " · 瀏覽器草稿備份失敗";
    $("saveStatus").classList.toggle("dirty", session.dirty || Boolean(session.conflict));
    $("conflictBanner").classList.toggle("hidden", !session.conflict);
    document.title = `${session.dirty ? "● " : ""}${session.project.name || "未命名專案"} · H3 剪輯室`;
    renderDisabled();
  }
  function renderRecovery() {
    $("draftBanner").classList.toggle("hidden", !state.draft);
    if (state.draft) $("draftMessage").textContent = state.draft.resolution.reason || "已保留一份舊草稿；目前專案可正常編輯。";
    $("restoreDraft").disabled = locked() || !state.draft?.resolution.draftProject;
    $("recoveryList").innerHTML = state.recoveries.map(item => `<div class="recovery-item"><span>${escape(item.record.project?.name || "無法讀取的草稿")}<small>${escape(new Date(item.createdAt).toLocaleString("zh-TW"))}</small></span><button class="button ghost small" data-recovery-download="${escape(item.key)}">下載</button><button class="button ghost small" data-recovery-copy="${escape(item.key)}">另存專案</button></div>`).join("") || '<p class="subtle">目前沒有草稿備份。</p>';
    $("recoveryCount").textContent = state.recoveries.length;
  }
  function renderDisabled() {
    previewViewport.render();
    const noClips = !project() || totalDuration(project()) <= 0, selected = selectedClip(), index = project()?.clips.indexOf(selected) ?? -1;
    const isOverlay = state.selectedKind === "overlay", tracks = project() ? core.overlayTrackGroups(project()) : [], layerIndex = selected && isOverlay ? tracks.findIndex(track => track.id === core.overlayTrackId(selected)) : -1;
    ["uploadZone", "addAudio", "emptyAddMedia", "projectName", "outputSize", "outputFps", "saveCopy", "conflictSaveCopy", "reloadProject"].forEach(id => $(id).disabled = locked());
    $("restoreDraft").disabled = locked() || !state.draft?.resolution.draftProject;
    ["discardDraft", "downloadDraft"].forEach(id => $(id).disabled = locked() || !state.draft);
    ["newProject", "projectSelect", "openProject"].forEach(id => $(id).disabled = !canSwitch());
    ["downloadProjectArchive", "openProjectArchive"].forEach(id => $(id).disabled = locked() || !state.archivesReady);
    document.querySelectorAll("[data-recovery-copy]").forEach(button => button.disabled = locked());
    $("saveProject").disabled = locked() || Boolean(state.session?.inFlight);
    $("exportProject").disabled = locked() || noClips || Boolean(state.session?.conflict);
    $("undo").disabled = locked() || !state.session?.undoStack.length;
    $("redo").disabled = locked() || !state.session?.redoStack.length;
    ["duplicateClip", "deleteClip"].forEach(id => $(id).disabled = locked() || !selected);
    $("resetTrim").disabled = locked() || !selected || isOverlay;
    $("moveEarlier").disabled = locked() || !selected || (isOverlay ? layerIndex <= 0 : state.selectedKind === "audio" ? selected.start <= 0 : index <= 0);
    $("moveLater").disabled = locked() || !selected || (isOverlay ? layerIndex >= tracks.length - 1 : state.selectedKind === "audio" ? selected.start >= 600 : index === project().clips.length - 1);
    $("moveEarlier").title = isOverlay ? "圖層向下移" : "往前移"; $("moveLater").title = isOverlay ? "圖層向上移" : "往後移";
    $("splitClip").disabled = locked() || (state.selectedKind !== "video" ? !selected : !project()?.clips.length);
    $("detachAudio").disabled = locked() || !selected || state.selectedKind !== "video" || !state.media.get(selected.media_id)?.has_audio;
    $("muteOriginal").disabled = locked() || !selected || state.selectedKind !== "video";
    $("clipFields").disabled = locked() || !selected || isOverlay;
    $("overlayFields").disabled = locked() || !selected || !isOverlay || !overlaySupported(selected.kind);
    $("showPositionAnimation").disabled = locked();
    $("showSpeedCurve").disabled = locked() || !selectedSpeedVideo();
    $("checkPositionSupport").disabled = !state.backendReady || state.busy || state.positionChecking;
    $("checkTextStyleSupport").disabled = !state.backendReady || state.busy || state.textStyleChecking;
    renderTextStyleControls(isOverlay && selected?.kind === "text" ? selected : null);
    for (const id of ["clipFadeIn", "clipFadeOut", "overlayFadeIn", "overlayFadeOut"]) $(id).disabled = locked() || !state.visualFadesReady;
    for (const id of ["moveToLayer", "copyToLayer"]) $(id).disabled = locked() || !selected || state.selectedKind !== "video" || !state.videoOverlaysReady;
    $("addTextOverlay").disabled = locked() || !state.textOverlaysReady; $("addImageOverlay").disabled = locked() || !state.imageOverlaysReady;
    $("layerDown").disabled = locked() || !isOverlay || layerIndex <= 0; $("layerUp").disabled = locked() || !isOverlay || layerIndex >= tracks.length - 1;
    if (isOverlay && selected) renderPositionControls(selected);
    $("playPause").disabled = noClips || Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag); $("jumpStart").disabled = noClips || Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag); $("previewSeek").disabled = noClips || Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag);
    $("timelineZoom").disabled = Boolean(state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag);
    document.querySelectorAll("[data-add-media],[data-add-audio],[data-import-job]").forEach(button => button.disabled = locked());
    document.querySelectorAll("[data-add-video-layer]").forEach(button => button.disabled = locked() || !state.videoOverlaysReady);
    document.querySelectorAll(".audio-clip").forEach(button => button.draggable = !locked());
    document.querySelectorAll(".timeline-clip").forEach(button => button.draggable = false);
    document.querySelectorAll("[data-trim-edge]").forEach(handle => handle.setAttribute("aria-disabled", String(locked() || Boolean(state.session?.inFlight))));
    renderSpeedControls();
  }
  function overlaySupported(kind) { return kind === "text" ? state.textOverlaysReady : kind === "video" ? state.videoOverlaysReady : state.imageOverlaysReady; }
  function renderMedia() {
    $("mediaCount").textContent = state.media.size;
    $("mediaList").innerHTML = [...state.media.values()].reverse().map(media => {
      const kind = mediaKind(media), icon = kind === "image" ? `<img class="media-thumb" src="/api/editor/media/${encodeURIComponent(media.id)}/thumbnail" alt="" loading="lazy" decoding="async">` : `<span class="media-icon" aria-hidden="true">${kind === "audio" ? "♫" : "▸"}</span>`;
      return `<article class="media-card ${kind}">${icon}<div><strong title="${escape(media.name)}">${escape(media.name)}</strong><small>${kind === "image" ? `圖片 · ${media.width}×${media.height}` : `${formatTime(media.duration)} · ${kind === "audio" ? "音訊" : `${media.width}×${media.height}${media.has_audio ? " · 有原聲" : ""}`}`}</small></div><div class="media-actions"><button class="button ghost small" data-add-media="${escape(media.id)}">＋ ${kind === "audio" ? "加入音軌" : kind === "image" ? "加入圖片圖層" : "加入影片"}</button>${kind === "video" ? `<button class="button ghost small" data-add-video-layer="${escape(media.id)}">＋ 疊加影片</button>` : ""}${kind === "video" && media.has_audio ? `<button class="button ghost small" data-add-audio="${escape(media.id)}">僅加入原聲</button>` : ""}</div></article>`;
    }).join("") || '<p class="empty-note">加入影片、音訊或圖片；素材可重複使用。</p>';
    renderDisabled();
  }
  function renderProjectList() {
    const current = project();
    if (current && !state.projects.some(p => p.id === current.id)) state.projects.unshift(current);
    $("projectSelect").innerHTML = state.projects.map(item => `<option value="${escape(item.id)}">${escape(item.id === current?.id ? current.name : item.name)}</option>`).join("");
    if (current) $("projectSelect").value = current.id;
  }
  function trimHandles(clip, kind) {
    return ["left", "right"].map(edge => `<span class="trim-handle trim-${edge}" data-trim-edge="${edge}" data-trim-kind="${kind}" data-trim-id="${escape(clip.id)}" role="slider" tabindex="0" aria-label="${kind === "audio" ? "音訊" : "影片"}${edge === "left" ? "入點" : "出點"}，左右方向鍵調整一個影格" aria-valuenow="${edge === "left" ? clip.in : clip.out}" aria-valuemin="0" aria-valuemax="${state.media.get(clip.media_id)?.duration || clip.out}" title="拖曳${edge === "left" ? "入點" : "出點"}；← → 調整一個影格"><i></i></span>`).join("");
  }
  function positionDiamonds(layer) {
    return positionKeyframes(layer).filter(point => point.time >= 0 && point.time <= layer.end - layer.start).map(point => `<span class="position-diamond" role="button" tabindex="0" data-position-time="${point.time}" data-position-layer="${escape(layer.id)}" style="left:${point.time * state.zoom}px" aria-label="跳至位置關鍵幀 ${(layer.start + point.time).toFixed(2)} 秒" title="位置關鍵幀 ${(layer.start + point.time).toFixed(2)} 秒">◆</span>`).join("");
  }
  function overlayClipMarkup(layer) {
    return `<button class="overlay-clip ${layer.kind === "video" ? "video-layer" : ""} ${state.selectedKind === "overlay" && layer.id === state.selected ? "selected" : ""} ${layer.start * state.zoom < 8 ? "at-timeline-start" : ""}" data-overlay-id="${escape(layer.id)}" style="left:${layer.start * state.zoom}px;width:${(layer.end - layer.start) * state.zoom}px" title="按住片段上下換層、左右定位；兩端調整顯示時間"><span class="overlay-clip-name">${layer.kind === "text" ? "T " + escape(layer.text || "空白文字") : (layer.kind === "video" ? "▸ " : "▧ ") + escape(state.media.get(layer.media_id)?.name || "素材")}</span><i class="fade-in" style="width:${(layer.fade_in || 0) * state.zoom}px"></i><i class="fade-out" style="width:${(layer.fade_out || 0) * state.zoom}px"></i>${["left", "right"].map(edge => `<span class="overlay-time-handle ${edge}" data-overlay-edge="${edge}" tabindex="0" role="slider" aria-label="圖層${edge === "left" ? "開始" : "結束"}時間" aria-orientation="horizontal" aria-valuemin="0" aria-valuemax="600" aria-valuenow="${edge === "left" ? layer.start : layer.end}" aria-valuetext="${(edge === "left" ? layer.start : layer.end).toFixed(3)} 秒"><i></i></span>`).join("")}${positionDiamonds(layer)}</button>`;
  }
  function renderTimeline() {
    const value = project(); if (!value) return;
    if (state.trackDrag) { renderTrackGesture(); return; }
    if (state.trimDrag) { renderTrimPreview(); return; }
    if (state.overlayDrag) { renderOverlayGesture(); return; }
    const total = totalDuration(value);
    $("timelineSummary").textContent = `${value.clips.length} 影片 · ${audioClips(value).length} 音訊 · ${core.overlayTrackGroups(value).length} 圖層軌 · ${overlays(value).length} 疊層片段 · ${total.toFixed(2)} 秒`;
    const visualEnd = Math.max(total, ...audioClips(value).map(clip => clip.start + duration(clip)), ...overlays(value).map(layer => layer.end), 0);
    $("timelineCanvas").style.width = Math.max(visualEnd * state.zoom + 25, $("timelineScroll").clientWidth) + "px";
    const step = state.zoom < 25 ? 10 : state.zoom < 65 ? 5 : 1;
    let marks = "";
    for (let second = 0; second <= Math.min(1200, Math.max(visualEnd, 10)); second += step) marks += `<span style="left:${second * state.zoom}px">${formatTime(second).slice(0, 5)}</span>`;
    $("timeRuler").innerHTML = marks;
    $("clipTrack").innerHTML = value.clips.map((clip, index) => `<button type="button" role="listitem" class="timeline-clip ${clip.id === state.selected && state.selectedKind === "video" ? "selected" : ""}" style="width:${duration(clip) * state.zoom}px" data-clip-id="${escape(clip.id)}" draggable="false" aria-label="片段 ${index + 1}：${escape(state.media.get(clip.media_id)?.name || "找不到素材")}，${duration(clip).toFixed(2)} 秒" aria-pressed="${clip.id === state.selected}"><strong>${index + 1} · ${escape(state.media.get(clip.media_id)?.name || "找不到素材")}</strong><small>${duration(clip).toFixed(2)}s · ${speedLabel(clip)} · ${Math.round(clip.volume * 100)}%</small><i class="fade-in" style="width:${(clip.fade_in || 0) * state.zoom}px"></i><i class="fade-out" style="width:${(clip.fade_out || 0) * state.zoom}px"></i>${trimHandles(clip, "video")}</button>`).join("");
    let labels = '<div style="height:31px"></div><div class="track-label track-drop-new-label" style="height:32px"><span>＋</span><small>新增上層</small></div>';
    $("overlayTracks").innerHTML = '<div id="trackDropNew" class="track-drop-new" data-track-drop="new"><span>＋ 拖曳片段至此新增上層</span></div>' + [...core.overlayTrackGroups(value)].reverse().map((track, index) => {
      const kind = track.clips.every(layer => layer.kind === track.clips[0].kind) ? track.clips[0].kind : "mixed";
      labels += `<div class="track-label overlay-label ${kind === "video" ? "video-layer" : ""}" style="height:38px" title="同軌 ${track.clips.length} 個片段"><span>L${core.overlayTrackGroups(value).length - index}</span><small>${kind === "text" ? "文字" : kind === "video" ? "影片" : kind === "image" ? "圖片" : "混合"}</small></div>`;
      return `<div class="overlay-track" data-track-id="${escape(track.id)}" data-layer-id="${escape(track.clips[0].id)}" role="list" aria-label="圖層軌 L${core.overlayTrackGroups(value).length - index}，${track.clips.length} 個片段">${track.clips.map(overlayClipMarkup).join("")}</div>`;
    }).join("");
    labels += '<div class="track-label" style="height:74px"><span>V1</span><strong>影片／原聲</strong></div>';
    $("audioTracks").innerHTML = [0, 1, 2, 3].map(track => {
      const lanes = audioLanes(audioClips(value), track); labels += `<div class="track-label audio-label" style="height:${lanes.height}px"><span>A${track + 1}</span><small>音訊</small></div>`;
      return `<div class="audio-track ${lanes.items.length ? "" : "empty"}" data-audio-track="${track}" style="height:${lanes.height}px" role="list" aria-label="音軌 A${track + 1}">${lanes.items.map(({ clip, lane }) => `<button class="audio-clip ${state.selectedKind === "audio" && clip.id === state.selected ? "selected" : ""}" data-audio-id="${escape(clip.id)}" draggable="${!locked()}" role="listitem" style="left:${clip.start * state.zoom}px;top:${7 + lane * 29}px;width:${duration(clip) * state.zoom}px" title="${escape(state.media.get(clip.media_id)?.name)} · ${clip.start.toFixed(2)}s · ${speedLabel(clip)}"><i class="fade-in" style="width:${clip.fade_in * state.zoom}px"></i><i class="fade-out" style="width:${clip.fade_out * state.zoom}px"></i><span class="audio-clip-text">♫ ${escape(state.media.get(clip.media_id)?.name || "音訊")} · ${speedLabel(clip)}</span>${trimHandles(clip, "audio")}</button>`).join("")}</div>`;
    }).join(""); $("trackLabels").innerHTML = labels;
    document.querySelectorAll(".audio-clip").forEach(node => node.classList.toggle("at-timeline-start", parseFloat(node.style.left) < 9));
    $("timelineEmpty").classList.toggle("hidden", Boolean(totalDuration(value)));
    $("playhead").classList.toggle("hidden", !totalDuration(value));
    $("timelineEmpty").style.top = `${85 + core.overlayTrackGroups(value).length * 38}px`;
    updatePlayhead();
  }
  function beginTrackGesture(event) {
    if (event.target.closest("[data-trim-edge],[data-overlay-edge],[data-position-time]")) return;
    const node = event.target.closest("[data-clip-id],[data-overlay-id]");
    if (!node || locked() || state.session.inFlight || event.button !== 0 || event.isPrimary === false) return;
    const from = { kind: node.dataset.clipId ? "video" : "overlay", id: node.dataset.clipId || node.dataset.overlayId };
    const original = (from.kind === "video" ? project().clips : overlays(project())).find(item => item.id === from.id); if (!original) return;
    if (from.kind === "overlay" && !overlaySupported(original.kind)) return;
    event.preventDefault(); event.stopPropagation(); pause(); state.queuedSeek = null;
    const transaction = new core.TrackDragTransaction(state.session, from, state.media);
    const sourceStart = from.kind === "video" ? core.mainClipStart(project(), from.id) : original.start;
    state.selectedKind = from.kind; state.selected = from.id;
    state.trackDrag = { transaction, from, node, pointerId: event.pointerId, original: clone(original), sourceStart,
      length: from.kind === "video" ? duration(original) : original.end - original.start,
      startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY,
      scrollStart: $("timelineScroll").scrollLeft, zoom: state.zoom, frame: null, activated: false, horizontalIntent: false, invalid: false, feedback: null };
    try { $("timelineCanvas").setPointerCapture(event.pointerId); }
    catch (error) { transaction.cancel(); state.trackDrag = null; renderDisabled(); throw error; }
    node.classList.add("track-drag-source"); document.body.classList.add("is-track-dragging");
    renderInspector(); renderDisabled();
  }
  function snapTrackStart(start, drag) {
    const value = drag.transaction.original, anchors = [0, state.playhead]; let cursor = 0;
    for (const clip of value.clips) { if (clip.id !== drag.from.id) anchors.push(cursor, cursor + duration(clip)); cursor += duration(clip); }
    for (const layer of overlays(value)) if (layer.id !== drag.from.id) anchors.push(layer.start, layer.end);
    const threshold = 6 / drag.zoom;
    let snapped = start, distance = threshold;
    for (const anchor of anchors) for (const candidate of [anchor, anchor - drag.length]) {
      const gap = Math.abs(candidate - start); if (candidate >= 0 && candidate + drag.length <= 600 && gap < distance) { snapped = candidate; distance = gap; }
    }
    return clamp(distance < threshold ? snapped : Math.round(start * value.fps) / value.fps, 0, Math.max(0, 600 - drag.length));
  }
  function renderTrackGesture() {
    const drag = state.trackDrag; if (!drag || !drag.activated) return;
    const result = drag.transaction.result;
    const name = drag.original.kind === "text" ? drag.original.text : state.media.get(drag.original.media_id)?.name || "片段";
    trackDragUI.show(drag.feedback || { valid: false, reason: "請拖至影片軌道內", label: "請拖至影片軌道內" }, {
      name, start: result?.start ?? drag.sourceStart, duration: drag.length, zoom: drag.zoom,
      pointer: { clientX: drag.lastX, clientY: drag.lastY }, from: drag.from });
  }
  function flushTrackGesture() {
    const drag = state.trackDrag; if (!drag) return; drag.frame = null;
    if (!drag.activated && Math.hypot(drag.lastX - drag.startX, drag.lastY - drag.startY) < 5) return;
    drag.activated = true;
    if (Math.abs(drag.lastX - drag.startX) >= 8) drag.horizontalIntent = true;
    const point = { clientX: drag.lastX, clientY: drag.lastY }, scrolled = trackDragUI.autoScroll(point, { horizontal: drag.horizontalIntent });
    const feedback = trackDragUI.resolve(point, drag.from, drag.transaction.original);
    try {
      if (!feedback.valid) { drag.transaction.invalidate(); throw new Error(feedback.reason || feedback.label); }
      const target = { ...feedback.target };
      if (target.kind === "overlay") {
        if (target.mode === "join" && !state.overlayTracksReady) { drag.transaction.invalidate(); throw new Error("請重新啟動 Studio，以載入同軌剪輯。"); }
        if (!state.videoOverlaysReady && drag.from.kind === "video") throw new Error("請重新啟動 Studio，以使用影片疊層。");
        const delta = drag.lastX - drag.startX + $("timelineScroll").scrollLeft - drag.scrollStart;
        if (drag.horizontalIntent) target.start = snapTrackStart(drag.sourceStart + delta / drag.zoom, drag);
      }
      const result = drag.transaction.update(target); drag.invalid = false;
      if (result.layoutReset) feedback.label += " · 重設位置／尺寸／旋轉／透明度";
      if (drag.from.kind === "video" && target.kind === "overlay") feedback.label += " · 主軌後續片段自動接合";
      drag.feedback = feedback;
    } catch (error) { drag.invalid = true; drag.feedback = { ...feedback, valid: false, label: error.message, reason: error.message }; }
    renderTrackGesture();
    if (scrolled && state.trackDrag === drag) drag.frame = requestAnimationFrame(flushTrackGesture);
  }
  function finishTrackGesture(commit) {
    const drag = state.trackDrag; if (!drag) return;
    if (drag.frame !== null) cancelAnimationFrame(drag.frame);
    if (commit) flushTrackGesture();
    if (drag.frame !== null) cancelAnimationFrame(drag.frame);
    state.trackDrag = null; state.trimSuppressUntil = Date.now() + 450;
    trackDragUI.hide(); drag.node.classList.remove("track-drag-source"); document.body.classList.remove("is-track-dragging");
    if ($("timelineCanvas").hasPointerCapture(drag.pointerId)) $("timelineCanvas").releasePointerCapture(drag.pointerId);
    const result = drag.transaction.result; let changed = false;
    try {
      if (commit && drag.activated && !drag.invalid) changed = drag.transaction.commit(); else drag.transaction.cancel();
      state.selectedKind = changed ? result.selectedKind : drag.from.kind; state.selected = drag.from.id;
    } catch (error) { state.selectedKind = drag.from.kind; state.selected = drag.from.id; errorNotice(error); }
    render();
    if (changed) {
      state.playhead = clamp(state.playhead, 0, totalDuration(project()));
      if (drag.from.kind !== result.selectedKind || drag.from.kind === "video" || Math.abs(result.start - drag.sourceStart) > 1e-9) seek(state.playhead, false);
      else { renderOverlayPreview(); updatePlayhead(); }
      if (result.layoutReset) notify("已移入主軌，位置、尺寸、旋轉與透明度套用主軌設定；來源裁切、速度、淡入淡出與原聲已保留。");
      else if (drag.from.kind === "video" && result.selectedKind === "overlay") notify("已放入圖層軌；主軌後續片段已自動接合。");
    } else if (commit && !drag.activated) {
      const rect = $("timelineCanvas").getBoundingClientRect(); seek((drag.lastX - rect.left) / drag.zoom, false);
    } else if (commit && drag.invalid) notify(drag.feedback?.reason || "無法放到此位置，原片段已保留。");
  }
  function renderTrimPreview() {
    const drag = state.trimDrag; if (!drag) return;
    const value = drag.transaction.preview, result = drag.transaction.result, total = totalDuration(value);
    const videoClips = new Map(value.clips.map(clip => [clip.id, clip])), sounds = new Map(audioClips(value).map(clip => [clip.id, clip]));
    // Keep the capture surface and all clip nodes stable throughout the gesture.
    document.querySelectorAll(".timeline-clip,.audio-clip").forEach(node => {
      const isAudio = Boolean(node.dataset.audioId), clip = (isAudio ? sounds : videoClips).get(node.dataset.audioId || node.dataset.clipId);
      if (!clip) return;
      node.style.width = `${duration(clip) * state.zoom}px`;
      node.classList.toggle("selected", clip.id === state.selected && state.selectedKind === (isAudio ? "audio" : "video"));
      if (isAudio) {
        node.style.left = `${clip.start * state.zoom}px`;
        node.classList.toggle("at-timeline-start", clip.start * state.zoom < 9);
        node.querySelector(".fade-in").style.width = `${clip.fade_in * state.zoom}px`;
        node.querySelector(".fade-out").style.width = `${clip.fade_out * state.zoom}px`;
      } else node.querySelector("small").textContent = `${duration(clip).toFixed(2)}s · ${speedLabel(clip)} · ${Math.round(clip.volume * 100)}%`;
      node.querySelectorAll("[data-trim-edge]").forEach(handle => handle.setAttribute("aria-valuenow", handle.dataset.trimEdge === "left" ? clip.in : clip.out));
    });
    const visualEnd = Math.max(total, ...audioClips(value).map(clip => clip.start + duration(clip)), ...overlays(value).map(layer => layer.end), 0);
    drag.canvasWidth = Math.max(drag.canvasWidth, visualEnd * state.zoom + 80);
    $("timelineCanvas").style.width = `${drag.canvasWidth}px`;
    const step = state.zoom < 25 ? 10 : state.zoom < 65 ? 5 : 1;
    if (!drag.rulerEnd || visualEnd > drag.rulerEnd) {
      drag.rulerEnd = Math.max(10, visualEnd); let marks = "";
      for (let second = 0; second <= Math.min(1200, drag.rulerEnd); second += step) marks += `<span style="left:${second * state.zoom}px">${formatTime(second).slice(0, 5)}</span>`;
      $("timeRuler").innerHTML = marks;
    }
    $("timelineSummary").textContent = `${value.clips.length} 影片 · ${audioClips(value).length} 音訊 · ${core.overlayTrackGroups(value).length} 圖層軌 · ${overlays(value).length} 疊層片段 · ${total.toFixed(2)} 秒`;
    renderInspector(); updatePlayhead();
    if (result) {
      const clip = result.clip, location = drag.options.kind === "audio" ? ` · 位置 ${clip.start.toFixed(3)}–${(clip.start + duration(clip)).toFixed(3)}s` : "";
      $("trimFeedback").textContent = `來源 ${clip.in.toFixed(3)}–${clip.out.toFixed(3)}s · 長度 ${duration(clip).toFixed(3)}s${location}${result.clamped ? ` · ${result.reason}` : ""}`;
      $("trimFeedback").classList.toggle("at-limit", result.clamped);
      $("trimFeedback").classList.remove("hidden");
      $("trimFeedback").style.left = `${clamp(drag.lastX, 12, Math.max(12, root.innerWidth - $("trimFeedback").offsetWidth - 12))}px`;
      $("trimFeedback").style.top = `${Math.max(8, $("timelineScroll").getBoundingClientRect().top - $("trimFeedback").offsetHeight - 10)}px`;
    }
  }
  function flushTrimPreview(autoScroll = false) {
    const drag = state.trimDrag; if (!drag) return;
    drag.frame = null;
    const scroller = $("timelineScroll"), rect = scroller.getBoundingClientRect();
    let moved = false;
    if (autoScroll) {
      const before = scroller.scrollLeft, distance = drag.lastX < rect.left + 28 ? -Math.min(12, (rect.left + 28 - drag.lastX) / 2) : drag.lastX > rect.right - 28 ? Math.min(12, (drag.lastX - rect.right + 28) / 2) : 0;
      scroller.scrollLeft += distance; moved = Math.abs(scroller.scrollLeft - before) > 0.01;
    }
    try {
      drag.transaction.update((drag.lastX - drag.startX + scroller.scrollLeft - drag.startScroll) / drag.zoom);
      renderTrimPreview();
    } catch (error) { finishTrim(false); errorNotice(error); return; }
    if (moved && state.trimDrag === drag) drag.frame = requestAnimationFrame(() => flushTrimPreview(true));
  }
  function queueTrimPreview() {
    const drag = state.trimDrag;
    if (drag && drag.frame === null) drag.frame = requestAnimationFrame(() => flushTrimPreview(true));
  }
  function beginTrim(event) {
    const handle = event.target.closest("[data-trim-edge]"); if (!handle) return;
    event.preventDefault(); event.stopPropagation();
    if (locked() || state.session.inFlight || event.button !== 0 || event.isPrimary === false) return;
    const options = { kind: handle.dataset.trimKind, id: handle.dataset.trimId, edge: handle.dataset.trimEdge };
    const transaction = new TrimTransaction(state.session, options, state.media); transaction.update(0);
    pause(); state.queuedSeek = null;
    state.selected = options.id; state.selectedKind = options.kind;
    state.trimDrag = { transaction, options, pointerId: event.pointerId, startX: event.clientX, lastX: event.clientX,
      startScroll: $("timelineScroll").scrollLeft, zoom: state.zoom, playhead: state.playhead, frame: null,
      canvasWidth: $("timelineCanvas").getBoundingClientRect().width };
    document.body.classList.add("is-trimming");
    $("timelineCanvas").setPointerCapture(event.pointerId);
    renderTrimPreview(); renderDisabled();
  }
  function finishTrim(commit) {
    const drag = state.trimDrag; if (!drag) return;
    if (drag.frame !== null) cancelAnimationFrame(drag.frame);
    if (commit) {
      // Include the final pointer position even when release precedes the next RAF.
      try { drag.transaction.update((drag.lastX - drag.startX + $("timelineScroll").scrollLeft - drag.startScroll) / drag.zoom); }
      catch (error) { commit = false; errorNotice(error); }
    }
    state.trimDrag = null; state.trimSuppressUntil = Date.now() + 450;
    document.body.classList.remove("is-trimming"); $("trimFeedback").classList.add("hidden");
    if ($("timelineCanvas").hasPointerCapture(drag.pointerId)) $("timelineCanvas").releasePointerCapture(drag.pointerId);
    let changed = false;
    try { if (commit) changed = drag.transaction.commit(); else drag.transaction.cancel(); }
    catch (error) { errorNotice(error); }
    state.playhead = clamp(drag.playhead, 0, totalDuration(project())); render();
    if (changed) {
      seek(state.playhead, false);
      if (drag.transaction.result.clamped) notify(drag.transaction.result.reason);
    }
  }
  function keyboardTrim(handle, direction) {
    if (locked() || state.session.inFlight) return;
    const options = { kind: handle.dataset.trimKind, id: handle.dataset.trimId, edge: handle.dataset.trimEdge };
    const transaction = new TrimTransaction(state.session, options, state.media), result = transaction.update(direction / project().fps);
    state.selected = options.id; state.selectedKind = options.kind; pause(); state.queuedSeek = null;
    const changed = transaction.commit(); render();
    if (changed) seek(clamp(state.playhead, 0, totalDuration(project())), false);
    if (result.clamped) notify(result.reason);
    const replacement = [...document.querySelectorAll("[data-trim-edge]")].find(node => node.dataset.trimId === options.id && node.dataset.trimEdge === options.edge);
    replacement?.focus({ preventScroll: true });
  }
  function beginOverlayGesture(event, canvas) {
    if (event.target.closest("[data-position-time]")) return;
    if (canvas === $("timelineCanvas") && !event.target.closest("[data-overlay-edge]")) return;
    const node = event.target.closest("[data-overlay-id]"); if (!node) return;
    event.preventDefault(); event.stopPropagation();
    if (locked() || state.session.inFlight || event.button !== 0 || event.isPrimary === false) return;
    const id = node.dataset.overlayId, layer = overlays(project()).find(item => item.id === id);
    if (!layer || !(overlaySupported(layer.kind))) return;
    if (positionKeyframes(layer).length && !state.positionKeyframesReady) throw new Error("請重新啟動 Studio 後編輯位置動畫。");
    pause(); state.queuedSeek = null;
    const transaction = new OverlayTransaction(state.session, id, state.media); transaction.update({});
    state.selectedKind = "overlay"; state.selected = id;
    state.overlayDrag = { transaction, canvas, pointerId: event.pointerId, id, mode: canvas === $("overlayCanvas") ? "position" : event.target.closest("[data-overlay-edge]")?.dataset.overlayEdge || "move",
      rect: $("videoCanvas").getBoundingClientRect(), startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY,
      scrollStart: $("timelineScroll").scrollLeft, zoom: state.zoom, frame: null, original: clone(layer), position: positionAt(layer, state.playhead - layer.start), invalid: false };
    canvas.setPointerCapture(event.pointerId); document.body.classList.add("is-overlay-dragging"); renderOverlayGesture(); renderDisabled();
  }
  function renderOverlayGesture() {
    const drag = state.overlayDrag; if (!drag) return;
    const value = project(), layer = overlays(value).find(item => item.id === drag.id);
    document.querySelectorAll(".overlay-clip").forEach(node => {
      const item = overlays(value).find(entry => entry.id === node.dataset.overlayId); if (!item) return;
      node.style.left = `${item.start * state.zoom}px`; node.style.width = `${(item.end - item.start) * state.zoom}px`; node.classList.toggle("selected", item.id === drag.id);
      node.classList.toggle("at-timeline-start", item.start * state.zoom < 8);
      node.querySelectorAll(".position-diamond").forEach(point => point.remove());
      node.insertAdjacentHTML("beforeend", positionDiamonds(item));
      node.querySelectorAll("[data-overlay-edge]").forEach(handle => { const seconds = handle.dataset.overlayEdge === "left" ? item.start : item.end; handle.setAttribute("aria-valuenow", seconds); handle.setAttribute("aria-valuetext", `${seconds.toFixed(3)} 秒`); });
    });
    const visualEnd = Math.max(totalDuration(value), ...overlays(value).map(item => item.end), ...audioClips(value).map(item => item.start + duration(item)), 0);
    $("timelineCanvas").style.width = `${Math.max($("timelineCanvas").clientWidth, visualEnd * state.zoom + 40)}px`;
    renderOverlayPreview(); renderInspector();
    const position = positionAt(layer, state.playhead - layer.start);
    $("trimFeedback").textContent = drag.error || (drag.mode === "position" ? `圖層位置 ${(position.x * 100).toFixed(1)}% · ${(position.y * 100).toFixed(1)}%${positionKeyframes(layer).length ? " · 位置關鍵幀" : ""}` : `圖層 ${layer.start.toFixed(3)}–${layer.end.toFixed(3)}s · ${(layer.end - layer.start).toFixed(3)} 秒${drag.transaction.result.clamped ? " · 已到時間範圍限制" : ""}`);
    $("trimFeedback").classList.remove("hidden"); $("trimFeedback").classList.toggle("at-limit", Boolean(drag.error || drag.transaction.result.clamped));
    $("trimFeedback").style.left = `${clamp(drag.lastX, 12, Math.max(12, root.innerWidth - $("trimFeedback").offsetWidth - 12))}px`;
    $("trimFeedback").style.top = `${Math.max(8, Math.min(root.innerHeight - $("trimFeedback").offsetHeight - 12, drag.lastY + 18))}px`;
  }
  function flushOverlayGesture() {
    const drag = state.overlayDrag; if (!drag) return; drag.frame = null;
    try {
      if (drag.mode === "position") drag.transaction.updatePosition(state.playhead, { x: clamp(drag.position.x + (drag.lastX - drag.startX) / Math.max(1, drag.rect.width), 0, 1), y: clamp(drag.position.y + (drag.lastY - drag.startY) / Math.max(1, drag.rect.height), 0, 1) });
      else drag.transaction.updateTime(drag.mode, (drag.lastX - drag.startX + $("timelineScroll").scrollLeft - drag.scrollStart) / drag.zoom);
      drag.invalid = false; drag.error = null;
    } catch (error) { drag.invalid = true; drag.error = error.message; }
    renderOverlayGesture();
  }
  function finishOverlayGesture(commit) {
    const drag = state.overlayDrag; if (!drag) return;
    if (drag.frame !== null) cancelAnimationFrame(drag.frame);
    if (commit) flushOverlayGesture();
    state.overlayDrag = null; state.trimSuppressUntil = Date.now() + 450;
    document.body.classList.remove("is-overlay-dragging"); $("trimFeedback").classList.add("hidden");
    if (drag.canvas.hasPointerCapture(drag.pointerId)) drag.canvas.releasePointerCapture(drag.pointerId);
    try { if (commit && !drag.invalid) drag.transaction.commit(); else drag.transaction.cancel(); } catch (error) { errorNotice(error); }
    state.playhead = Math.min(state.playhead, totalDuration(project()));
    render(); if (commit && !drag.invalid && drag.original.kind === "video" && drag.mode !== "position") seek(state.playhead, false);
    if (commit && drag.invalid) notify(drag.error);
  }
  function renderInspector() {
    const clip = selectedClip(), media = clip && state.media.get(clip.media_id), isAudio = state.selectedKind === "audio";
    const isOverlay = state.selectedKind === "overlay";
    const inspector = document.querySelector(".inspector"), selection = `${state.selectedKind}:${clip?.id || ""}`;
    if (inspector.dataset.selection !== selection) {
      inspector.dataset.selection = selection; inspector.scrollTop = 0;
      $("positionActionStatus").classList.add("hidden"); $("positionActionStatus").textContent = "";
    }
    $("showPositionAnimation").textContent = "◇ 加入關鍵幀";
    $("showPositionAnimation").title = "在目前時間記錄文字或圖片的位置";
    $("inspectorEmpty").classList.toggle("hidden", Boolean(clip)); $("clipFields").classList.toggle("hidden", !clip || isOverlay);
    $("overlayFields").classList.toggle("hidden", !clip || !isOverlay);
    renderSpeedControls();
    if (!clip) return;
    if (isOverlay) { renderOverlayInspector(clip); return; }
    $("selectedClipName").textContent = media?.name || "找不到來源素材";
    $("sourceInfo").textContent = media ? `來源 ${Number(media.duration).toFixed(2)} 秒${mediaKind(media) === "audio" ? " · 音訊" : ` · ${media.width} × ${media.height}`}` : "請重新加入素材。";
    $("clipIn").value = clip.in; $("clipOut").value = clip.out;
    $("clipIn").max = clip.out; $("clipOut").max = media?.duration || clip.out;
    $("clipDuration").textContent = `${duration(clip).toFixed(2)} 秒`;
    $("clipSpeed").value = speedOf(clip);
    $("videoFadeFields").classList.toggle("hidden", isAudio); $("promoteVideoTools").classList.toggle("hidden", isAudio);
    for (const [id, field] of [["clipFadeIn", "fade_in"], ["clipFadeOut", "fade_out"]]) { $(id).value = clip[field] ?? 0; $(id).max = duration(clip); }
    $("audioPositionFields").classList.toggle("hidden", !isAudio); $("videoAudioTools").classList.toggle("hidden", isAudio);
    if (isAudio) { $("audioStart").value = clip.start; $("audioTrack").value = clip.track; $("audioFadeIn").value = clip.fade_in; $("audioFadeOut").value = clip.fade_out; $("audioFadeIn").max = duration(clip); $("audioFadeOut").max = duration(clip); $("audioOverflow").textContent = clip.start + duration(clip) > totalDuration(project()) ? "超過影片尾端的音訊不會匯出。" : "淡入淡出使用時間軸秒數；跨軌片段會混音。"; }
    $("muteOriginal").textContent = clip.volume ? "原聲靜音" : "恢復原聲";
    $("clipVolume").value = Math.round(clip.volume * 100); $("volumeRange").value = Math.round(clip.volume * 100);
    $("audioNote").textContent = media?.has_audio || mediaKind(media) === "audio" ? "100% 為原始音量；變速預覽會盡量保持音高。" : "這份素材沒有音軌，匯出會保留無聲片段。";
  }
  function renderOverlayInspector(layer) {
    const set = (id, value) => { if (document.activeElement !== $(id)) $(id).value = value; };
    $("overlayName").textContent = layer.kind === "text" ? "文字圖層" : state.media.get(layer.media_id)?.name || "圖片圖層";
    for (const [id, field, factor] of [["overlayStart", "start", 1], ["overlayEnd", "end", 1], ["overlayWidth", "width", 100], ["overlayRotation", "rotation", 1], ["overlayOpacity", "opacity", 100]]) set(id, Number((layer[field] * factor).toFixed(6)));
    renderPositionControls(layer);
    $("overlayTextFields").classList.toggle("hidden", layer.kind !== "text");
    $("overlayVideoFields").classList.toggle("hidden", layer.kind !== "video");
    for (const [id, field] of [["overlayFadeIn", "fade_in"], ["overlayFadeOut", "fade_out"]]) { set(id, layer[field] ?? 0); $(id).max = layer.end - layer.start; }
    if (layer.kind === "video") {
      set("layerSourceIn", layer.in); set("layerSourceOut", layer.out); set("layerSpeed", speedOf(layer)); set("layerVolume", Math.round(layer.volume * 100));
      $("layerSourceOut").max = state.media.get(layer.media_id)?.duration || layer.out;
    }
    if (layer.kind === "text") {
      set("overlayText", layer.text); set("overlayFontSize", layer.font_size * 100); set("overlayColor", layer.color); set("overlayBackground", layer.background === "transparent" ? "#000000" : layer.background); set("overlayAlign", layer.align);
      $("overlayBold").checked = layer.bold; $("overlayTransparent").checked = layer.background === "transparent"; $("overlayBackground").disabled = layer.background === "transparent";
      $("overlayTextCount").textContent = `${Array.from(layer.text).length} / 500 字`;
      renderTextStyleControls(layer);
    }
    $("overlayTimingNote").textContent = `${(layer.end - layer.start).toFixed(2)} 秒 · ${layer.end > totalDuration(project()) ? "超過影片尾端的部分不會匯出。" : "以完整輸出畫布定位，包含補黑邊的區域。"}`;
    $("overlayRasterStatus").textContent = state.rasterNodes.get(layer.id)?.error || "";
  }
  function renderPositionControls(layer) {
    const supported = ["text", "image"].includes(layer.kind), local = state.playhead - layer.start, length = layer.end - layer.start;
    const points = positionKeyframes(layer), index = keyframeIndexAt(layer, local), position = positionAt(layer, local);
    const inside = local >= -1e-9 && local <= length + 1e-9, blocked = locked() || !state.positionKeyframesReady;
    $("positionAnimation").classList.toggle("hidden", !supported);
    for (const [id, axis] of [["overlayX", "x"], ["overlayY", "y"]]) {
      if (document.activeElement !== $(id)) $(id).value = Number((position[axis] * 100).toFixed(4));
      $(id).disabled = locked() || (points.length > 0 && (blocked || !inside));
    }
    $("addPositionKeyframe").textContent = index >= 0 ? "◆ 已記錄位置關鍵幀" : "◇ 加入位置關鍵幀";
    if (supported) {
      $("showPositionAnimation").textContent = index >= 0 ? "◆ 已記錄關鍵幀" : "◇ 加入關鍵幀";
      $("showPositionAnimation").title = !state.positionKeyframesReady ? "查看位置動畫尚未啟用的原因" : index >= 0 ? "此時間的位置已記錄；移動播放游標後可加入下一點" : "在目前時間加入位置關鍵幀；游標在圖層外時會先移到圖層起點";
    }
    $("addPositionKeyframe").disabled = blocked || !inside || (index < 0 && points.length >= motion.MAX_POSITION_KEYFRAMES);
    $("deletePositionKeyframe").disabled = blocked || !inside || index < 0;
    $("clearPositionKeyframes").disabled = blocked || !points.length;
    $("previousPositionKeyframe").disabled = locked() || !points.some(point => point.time < local - 1e-6 && point.time >= 0 && point.time <= length);
    $("nextPositionKeyframe").disabled = locked() || !points.some(point => point.time > local + 1e-6 && point.time >= 0 && point.time <= length);
    $("positionKeyframeTime").disabled = blocked || !inside || index < 0; $("positionEasing").disabled = blocked || !inside || index < 0;
    $("positionKeyframeTime").min = layer.start; $("positionKeyframeTime").max = layer.end;
    if (document.activeElement !== $("positionKeyframeTime")) $("positionKeyframeTime").value = index >= 0 ? Number((layer.start + points[index].time).toFixed(6)) : "";
    if (document.activeElement !== $("positionEasing")) $("positionEasing").value = index >= 0 ? points[index].easing ?? "linear" : "linear";
    const visible = points.filter(point => point.time >= -1e-9 && point.time <= length + 1e-9), hidden = points.length - visible.length;
    $("positionAnimationNote").textContent = !state.positionKeyframesReady ? "目前 Studio 尚未載入位置動畫，點右上「加入關鍵幀」可查看啟用方式。" : !points.length ? "按 ◇ 記錄起點；移動播放游標，再拖曳文字／圖片或調整 X／Y，即會自動記錄下一點。" : `${points.length} 個關鍵幀${hidden ? `（${hidden} 個在裁切範圍外，延長邊界可恢復）` : ""}。${points.length === 1 ? "還需要在另一時間設定不同位置，才會產生移動。" : ""}拖曳或調整 X／Y 會在目前時間自動加入／更新關鍵幀。${!inside ? "游標在圖層外；按右上「加入關鍵幀」可移到起點。" : ""}`;
    const listKey = JSON.stringify([layer.id, layer.start, visible, index, blocked]);
    if ($("positionKeyframeList").dataset.key !== listKey) {
      $("positionKeyframeList").dataset.key = listKey;
      $("positionKeyframeList").innerHTML = visible.map(point => `<button type="button" class="position-point ${Math.abs(point.time - local) < 1e-6 ? "current" : ""}" data-position-time="${point.time}" data-position-layer="${escape(layer.id)}" ${locked() ? "disabled" : ""} aria-label="跳至位置關鍵幀 ${(layer.start + point.time).toFixed(2)} 秒"><span>◆ ${(layer.start + point.time).toFixed(2)}s</span><small>X ${(point.x * 100).toFixed(1)}% · Y ${(point.y * 100).toFixed(1)}%</small></button>`).join("");
    }
    document.querySelectorAll(`[data-position-layer="${layer.id}"].position-diamond`).forEach(node => node.classList.toggle("current", Math.abs(Number(node.dataset.positionTime) - local) < 1e-6));
  }
  function applyMotionCapability(capabilities) {
    state.positionKeyframesReady = capabilities.position_keyframes === true;
    $("positionSupportBanner").classList.toggle("hidden", state.positionKeyframesReady);
    $("positionSupportMessage").textContent = state.positionKeyframesReady ? "位置關鍵幀已啟用。" : "目前執行中的 Studio 尚未載入位置動畫。請先儲存專案，關閉 Studio 啟動視窗，再執行 start_h3_studio.bat 並按「重新檢查」。啟動視窗未關閉時，再點啟動檔只會開啟原本的 Studio。";
  }
  function applyTextStyleCapability(capabilities) {
    state.textStyleReady = capabilities.text_style === true;
    $("textStyleSupport").classList.toggle("hidden", state.textStyleReady);
    $("textStyleSupportMessage").textContent = state.textStyleReady ? "" : "文字描邊與漸層需載入新版 Studio。請先儲存專案，關閉 Studio 啟動視窗，再執行 start_h3_studio.bat 後按「重新檢查」。原有文字與剪輯仍可編輯。";
  }
  function renderTextStyleControls(layer, force = false) {
    const blocked = locked() || !state.textStyleReady || !layer;
    for (const id of ["overlayFillMode", "overlayGradientStart", "overlayGradientEnd", "overlayGradientAngle", "swapGradientColors", "gradientHorizontal", "gradientVertical", "overlayStrokeWidth", "overlayStrokeColor"]) $(id).disabled = blocked;
    if (!layer) return;
    const style = { ...TEXT_STYLE_DEFAULTS, ...canonicalTextStyle(layer) }, gradient = style.fill_mode === "linear_gradient";
    for (const [id, field, factor] of [["overlayFillMode", "fill_mode", 1], ["overlayGradientStart", "gradient_start", 1], ["overlayGradientEnd", "gradient_end", 1], ["overlayGradientAngle", "gradient_angle", 1], ["overlayStrokeColor", "stroke_color", 1], ["overlayStrokeWidth", "stroke_width", 100]]) {
      if (force || document.activeElement !== $(id)) $(id).value = typeof style[field] === "number" ? Number((style[field] * factor).toFixed(6)) : style[field];
    }
    $("overlayGradientFields").classList.toggle("hidden", !gradient);
    $("overlaySolidColorField").classList.toggle("hidden", gradient);
    $("gradientColorPreview").style.background = `linear-gradient(${style.gradient_angle + 90}deg, ${style.gradient_start}, ${style.gradient_end})`;
    for (const [id, angle] of [["gradientHorizontal", 0], ["gradientVertical", 90]]) $(id).setAttribute("aria-pressed", String(style.gradient_angle === angle));
  }
  function textRaster(layer, value) {
    const key = overlayRasterKey(layer, value.width, value.height);
    const found = state.rasterCache.get(key); if (found) { state.rasterCache.delete(key); state.rasterCache.set(key, found); return found.promise; }
    const entry = { url: null, promise: null };
    entry.promise = state.rasterQueue.run(async () => {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (![...state.rasterNodes.values()].some(item => item.key === key)) throw new Error("此文字預覽已不再需要。");
        const response = await fetch("/api/editor/text-preview", json("POST", { layer, width: value.width, height: value.height }));
        if (response.status !== 409 || attempt === 2) return response;
        await new Promise(resolve => setTimeout(resolve, 350 * (attempt + 1)));
      }
    }).then(async response => {
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || "文字預覽暫時無法產生，請檢查文字框寬度與行數。"); }
      entry.url = URL.createObjectURL(await response.blob());
      pruneRasterCache(key);
      return entry.url;
    }).catch(error => { if (state.rasterCache.get(key) === entry) state.rasterCache.delete(key); throw error; });
    state.rasterCache.set(key, entry); return entry.promise;
  }
  function pruneRasterCache(keep = null) {
    const used = new Set([keep, ...[...state.rasterNodes.values()].flatMap(item => [item.key, item.displayKey])]);
    while (state.rasterCache.size > 40) {
      const oldest = [...state.rasterCache].find(([key, item]) => !used.has(key) && item.url);
      if (!oldest) break; state.rasterCache.delete(oldest[0]); URL.revokeObjectURL(oldest[1].url);
    }
  }
  function renderOverlayPreview() {
    const value = project(); if (!value) return;
    const activeLayers = overlays(value).filter(layer => state.playhead >= layer.start && state.playhead < layer.end);
    const ids = new Set(activeLayers.map(layer => layer.id));
    for (const [id, item] of state.rasterNodes) if (!ids.has(id)) { clearTimeout(item.timer); if (item.video) videoLayers.release(id); item.node.remove(); state.rasterNodes.delete(id); }
    pruneRasterCache();
    activeLayers.forEach(layer => {
      const index = overlays(value).indexOf(layer);
      let item = state.rasterNodes.get(layer.id);
      if (!item) {
        const node = document.createElement("div"), img = layer.kind === "video" ? null : document.createElement("img"), layerVideo = layer.kind === "video" ? document.createElement("video") : null, placeholder = document.createElement("span");
        node.className = "preview-overlay"; node.dataset.overlayId = layer.id; node.tabIndex = 0; node.setAttribute("role", "button"); node.setAttribute("aria-label", "拖曳圖層位置");
        if (img) { img.draggable = false; img.decoding = "async"; img.alt = ""; }
        if (layerVideo) { layerVideo.playsInline = true; layerVideo.draggable = false; layerVideo.setAttribute("aria-label", "疊加影片"); const source = state.media.get(layer.media_id); if (source) layerVideo.style.aspectRatio = `${source.width} / ${source.height}`; }
        placeholder.className = "overlay-placeholder"; placeholder.textContent = "準備預覽…";
        node.append(img || layerVideo, placeholder); $("overlayCanvas").append(node); item = { node, img, video: layerVideo, placeholder, key: null, timer: null, error: null }; state.rasterNodes.set(layer.id, item);
      }
      const position = positionAt(layer, state.playhead - layer.start);
      item.node.style.left = `${position.x * 100}%`; item.node.style.top = `${position.y * 100}%`; item.node.style.width = `${layer.width * 100}%`;
      item.node.style.transform = `translate(-50%, -50%) rotate(${layer.rotation}deg)`; item.node.style.opacity = layer.opacity * fadeEnvelope(layer, state.playhead - layer.start, layer.end - layer.start); item.node.style.zIndex = index + 1;
      item.node.classList.toggle("selected", state.selectedKind === "overlay" && state.selected === layer.id);
      if (layer.kind === "video") { item.placeholder.hidden = true; if (!state.trimDrag && !state.overlayDrag) checkPreview(layer.media_id); return; }
      if (layer.kind === "image") {
        const url = state.media.get(layer.media_id)?.url;
        if (url && item.key !== url) { clearTimeout(item.timer); item.key = url; item.img.src = url; item.placeholder.hidden = true; item.error = null; }
        return;
      }
      if (!state.textOverlaysReady) { item.placeholder.textContent = "請重啟 Studio 載入文字圖層"; return; }
      if (!state.textStyleReady && Object.keys(canonicalTextStyle(layer)).length) {
        clearTimeout(item.timer); item.timer = null; item.key = null; item.img.removeAttribute("src");
        item.error = "這個文字樣式需重新啟動 Studio 後預覽；草稿仍保留。";
        item.placeholder.textContent = "請重啟 Studio 載入文字樣式"; item.placeholder.hidden = false;
        if (state.selected === layer.id) $("overlayRasterStatus").textContent = item.error;
        return;
      }
      const key = overlayRasterKey(layer, value.width, value.height);
      if (item.key === key) return;
      clearTimeout(item.timer); item.key = key; item.error = null; item.placeholder.hidden = Boolean(item.img.getAttribute("src"));
      // Several keystrokes share one request; a late raster can never replace newer text.
      item.timer = setTimeout(() => {
        item.timer = null;
        textRaster(clone(layer), { width: value.width, height: value.height }).then(url => {
          if (state.rasterNodes.get(layer.id) !== item || item.key !== key) return;
          item.img.src = url; item.displayKey = key; item.placeholder.hidden = true; item.error = null;
          if (state.selected === layer.id) $("overlayRasterStatus").textContent = "";
        }).catch(error => {
          if (state.rasterNodes.get(layer.id) !== item || item.key !== key) return;
          item.error = error.message; item.placeholder.textContent = "文字預覽需調整"; item.placeholder.hidden = Boolean(item.img.getAttribute("src"));
          if (state.selected === layer.id) $("overlayRasterStatus").textContent = error.message;
        });
      }, 220);
    });
  }
  function fitCanvas() {
    const value = project(); if (!value) return;
    previewViewport.update({ width: value.width, height: value.height, key: value.id });
  }
  function resizePreview() {
    // Layer drags measure the canvas once at pointerdown. Cancel rather than
    // committing coordinates measured against a previous viewport size.
    if (state.overlayDrag?.mode === "position") finishOverlayGesture(false);
    fitCanvas();
  }
  function render() {
    if (!project()) return;
    $("previewEmpty").classList.toggle("hidden", Boolean(totalDuration(project())));
    if (document.activeElement !== $("projectName")) $("projectName").value = project().name;
    $("outputSize").value = `${project().width}x${project().height}`; $("outputFps").value = String(project().fps);
    $("canvasBadge").textContent = `${project().width} × ${project().height}`;
    renderProjectList(); renderTimeline(); renderInspector(); renderStatus(); renderExport(); fitCanvas(); renderCacheStatus(); renderOverlayPreview();
  }
  function updatePlayhead() {
    const total = project() ? totalDuration(project()) : 0;
    $("playhead").style.transform = `translateX(${state.playhead * state.zoom}px)`;
    $("timeDisplay").innerHTML = `${formatTime(state.playhead)} <span>/ ${formatTime(total)}</span>`;
    $("previewSeek").max = total; $("previewSeek").value = state.playhead;
    renderOverlayPreview();
    if (state.selectedKind === "overlay" && selectedClip()) renderPositionControls(selectedClip());
    renderSpeedControls();
  }
  function pause() { state.playing = false; state.lastPlaybackTick = null; deck.pause(); videoLayers.pause(); state.mixer?.pause(); state.audioBuffering = false; state.layerBuffering = false; $("playPause").textContent = "▶"; $("playPause").setAttribute("aria-label", "播放"); renderPlaybackStatus(); }
  function renderPlaybackStatus() { $("playbackStatus").textContent = state.buffering ? "正在準備這個時間點…" : state.layerBuffering ? "影片圖層緩衝中…" : state.audioBuffering ? "音訊緩衝中…" : ""; }
  async function prepareAudio() {
    const AudioContext = root.AudioContext || root.webkitAudioContext;
    if (!AudioContext) return;
    if (!state.audio) {
      const context = new AudioContext(), gains = new Map();
      for (const element of videoElements) { const source = context.createMediaElementSource(element), gain = context.createGain(); source.connect(gain); gain.connect(context.destination); gains.set(element, gain); }
      state.audio = { context, gains }; videoLayers.setContext(context);
      state.mixer = new AudioPreview(() => new Audio(), context, error => { pause(); errorNotice(error); });
    }
    if (state.audio.context.state === "suspended") await state.audio.context.resume();
  }
  function applyVolume(clip) {
    video.playbackRate = speedAtSource(clip, clamp(video.currentTime, clip.in, clip.out)); video.preservesPitch = true;
    const index = project()?.clips.findIndex(item => item.id === clip.id) ?? -1;
    const start = index >= 0 ? project().clips.slice(0, index).reduce((sum, item) => sum + duration(item), 0) : 0;
    const local = clamp(state.playhead - start, 0, Math.max(0, duration(clip) - 1e-8));
    const envelope = fadeEnvelope(clip, local, duration(clip)); video.style.opacity = envelope;
    const gain = clip.volume * envelope;
    if (state.audio) { video.volume = 1; state.audio.gains.get(video).gain.value = gain; }
    else { video.volume = Math.min(1, gain); if (clip.volume > 1) $("playbackStatus").textContent = "按播放以啟用增益預覽"; }
  }
  function applyPendingSeek() {
    if (state.queuedSeek === null) return;
    const pending = state.queuedSeek; state.queuedSeek = null; seek(pending.time, pending.play);
  }
  function queueSeek(time, play = state.playing) {
    if (state.queuedSeek === null) requestAnimationFrame(applyPendingSeek);
    state.queuedSeek = { time, play };
  }
  function previewUrl(mediaId) {
    const preview = state.previews.get(mediaId);
    return preview?.status === "completed" && preview.url ? preview.url : state.media.get(mediaId)?.url || `/api/editor/media/${encodeURIComponent(mediaId)}/file`;
  }
  function preloadNext() {
    if (state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag) return;
    const value = project(); if (!value || state.previewIndex < 0 || state.previewIndex + 1 >= value.clips.length) return;
    const start = value.clips.slice(0, state.previewIndex + 1).reduce((sum, clip) => sum + duration(clip), 0), point = locateTime(value, start);
    checkPreview(point.clip.media_id); deck.preload(point, previewUrl(point.clip.media_id));
  }
  function seek(time, play = state.playing) {
    if (state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag) return;
    const value = project(); state.playhead = value ? clamp(Number(time) || 0, 0, totalDuration(value)) : 0;
    const point = value && locateTime(value, state.playhead);
    state.lastPlaybackTick = null; state.mixer?.seek(); videoLayers.seek(); state.audioBuffering = false; state.layerBuffering = false;
    updatePlayhead();
    $("previewEmpty").classList.toggle("hidden", Boolean(value && totalDuration(value)));
    if (!point) {
      deck.clear(); videoElements.forEach(element => { element.hidden = true; element.style.opacity = 1; });
      state.previewIndex = -1; state.loadedMedia = null; state.buffering = false;
      $("previewClipName").textContent = value && totalDuration(value) ? "圖層／黑底預覽" : "專案預覽";
      if (!value || !totalDuration(value)) pause(); renderPlaybackStatus(); renderCacheStatus(); return;
    }
    const media = state.media.get(point.clip.media_id);
    if (!media) { pause(); $("playbackStatus").textContent = "找不到此片段的素材"; return; }
    if (play && point.clip.volume > 1 && !state.audio) { pause(); errorNotice(new Error("此片段音量超過 100%，目前瀏覽器無法預覽增益。請使用 Chrome／Edge 或降低片段音量。")); play = false; }
    $("previewClipName").textContent = `${point.index + 1} / ${value.clips.length} · ${media.name}`;
    state.previewIndex = point.index;
    checkPreview(media.id); deck.hold(false); deck.request(point, previewUrl(media.id), play); renderCacheStatus();
  }
  async function togglePlay() {
    if (!project() || !totalDuration(project()) || state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag) return;
    if (state.playing) { pause(); return; }
    await prepareAudio();
    const at = locateTime(project(), state.playhead);
    if (!state.audio && ((at?.clip.volume || 0) > 1 || audioClips(project()).length || overlays(project()).some(layer => layer.kind === "video" && layer.volume > 1))) throw new Error("這個瀏覽器無法預覽音訊混音。請使用 Chrome／Edge。");
    state.playing = true; $("playPause").textContent = "Ⅱ"; $("playPause").setAttribute("aria-label", "暫停");
    seek(state.playhead >= totalDuration(project()) - 0.015 ? 0 : state.playhead, true);
  }
  function nextPreviewClip() {
    if (!state.playing || deck.pending || !project() || state.previewIndex < 0) return;
    const end = project().clips.slice(0, state.previewIndex + 1).reduce((sum, clip) => sum + duration(clip), 0);
    if (end >= totalDuration(project()) - 1e-8) { state.playhead = totalDuration(project()); pause(); updatePlayhead(); }
    else seek(end, true);
  }
  function playbackTick(now) {
    const value = project(), gesture = state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag;
    if (value && !gesture) {
      const elapsed = state.lastPlaybackTick === null ? 0 : Math.min(0.1, (now - state.lastPlaybackTick) / 1000);
      state.lastPlaybackTick = now;
      const blocked = state.buffering || state.audioBuffering || state.layerBuffering || Boolean(deck.pending);
      if (state.playing && !blocked) {
        const clip = value.clips[state.previewIndex];
        if (clip && !video.seeking) {
          if (video.currentTime >= clip.out - Math.min(0.012, speedAtSource(clip, video.currentTime) / 120)) nextPreviewClip();
          else { const start = value.clips.slice(0, state.previewIndex).reduce((sum, item) => sum + duration(item), 0); state.playhead = start + clamp(timelineAt(clip, video.currentTime), 0, duration(clip)); }
        } else if (state.previewIndex < 0) {
          state.playhead = Math.min(totalDuration(value), state.playhead + elapsed);
          if (state.playhead >= totalDuration(value)) pause();
        }
        updatePlayhead();
      }
      const mainReady = !state.buffering && !deck.pending && (state.previewIndex < 0 || (!video.seeking && video.readyState >= 2));
      const layersReady = videoLayers.sync(overlays(value), state.playhead, state.playing && mainReady && !state.audioBuffering);
      const audioReady = !state.mixer || state.mixer.sync(value, state.media, state.playhead, state.playing && mainReady && layersReady);
      state.layerBuffering = state.playing && !layersReady; state.audioBuffering = state.playing && !audioReady;
      const held = !mainReady || !layersReady || !audioReady;
      if (deck.held !== held) deck.hold(held);
      if (held) { videoLayers.pause(); state.mixer?.pause(); state.lastPlaybackTick = null; }
      const clip = value.clips[state.previewIndex]; if (clip) applyVolume(clip);
      renderPlaybackStatus();
    } else state.lastPlaybackTick = null;
    requestAnimationFrame(playbackTick);
  }
  function edit(mutate, group = null, reposition = true) {
    if (locked()) return;
    const changed = state.session.change(mutate, group); if (!changed) return;
    if (!selectedClip()) { state.selectedKind = "video"; state.selected = project().clips[0]?.id || null; }
    state.playhead = clamp(state.playhead, 0, totalDuration(project())); render();
    if (reposition) { pause(); seek(state.playhead, false); }
    else {
      const point = locateTime(project(), state.playhead);
      if (point && state.previewIndex < 0) seek(state.playhead, state.playing);
      else if (point) applyVolume(point.clip);
      else if (state.loadedMedia) { deck.clear(); videoElements.forEach(element => { element.hidden = true; }); state.previewIndex = -1; state.loadedMedia = null; state.buffering = false; state.lastPlaybackTick = null; }
    }
  }
  function addMedia(id) {
    const media = state.media.get(id); if (!media) throw new Error("素材尚未就緒。");
    if (mediaKind(media) === "audio") return addAudioMedia(id);
    if (mediaKind(media) === "image") return addOverlay("image", id);
    const available = 600 - mainDuration(project());
    if (available < 1 / project().fps) throw new Error("專案已達 10 分鐘上限，請先縮短片段。");
    const out = Math.min(Number(media.duration), available), clipId = uid(), start = mainDuration(project());
    edit(value => { value.clips.push({ id: clipId, media_id: id, in: 0, out, volume: 1, speed: 1 }); validateProject(value, state.media); });
    state.selected = clipId; state.selectedKind = "video"; state.playhead = start; render(); seek(start, false);
    if (out < media.duration) notify(`專案最多 10 分鐘，已加入素材前 ${out.toFixed(2)} 秒。`);
  }
  function addAudioMedia(id) {
    const media = state.media.get(id); if (!media || (mediaKind(media) !== "audio" && !media.has_audio)) throw new Error("這份素材沒有可用音訊。");
    const clipId = uid(), start = state.playhead, out = Math.min(600, Number(media.duration));
    edit(value => { const track = freeAudioTrack(value, start, out); (value.audio_clips ||= []).push({ id: clipId, media_id: id, in: 0, out, start, track, volume: 1, speed: 1, fade_in: 0, fade_out: 0 }); validateProject(value, state.media); }, null, false);
    state.selected = clipId; state.selectedKind = "audio"; render();
    if (start + out > totalDuration(project())) notify("音訊已加入；超過影片尾端的部分不會匯出。");
  }
  function addOverlay(kind, mediaId = null) {
    if (locked()) return;
    if (!overlaySupported(kind)) throw new Error("請重新啟動 Studio，再重新整理以使用此圖層功能。");
    const start = clamp(state.playhead, 0, 600 - 1 / project().fps), end = Math.min(600, Math.max(start + 1 / project().fps, Math.min(start + 3, totalDuration(project()) || start + 3)));
    const media = state.media.get(mediaId);
    const layer = { id: uid(), kind, start, end, x: 0.5, y: kind === "text" ? 0.8 : 0.5, width: kind === "text" ? 0.7 : 0.3, rotation: 0, opacity: 1,
      ...(kind === "text" ? { text: "輸入文字", font_size: 0.065, color: "#ffffff", background: "transparent", bold: true, align: "center" } : { media_id: mediaId }) };
    if (kind === "video") {
      if (!media || mediaKind(media) !== "video") throw new Error("請選擇影片素材。");
      Object.assign(layer, { in: 0, out: Math.min(media.duration, 600 - start), speed: 1, volume: 1, width: Math.min(1, project().height / project().width * media.width / media.height) });
      layer.end = start + duration(layer);
    }
    edit(value => { (value.overlays ||= []).push(layer); validateProject(value, state.media); }, null, false);
    state.selectedKind = "overlay"; state.selected = layer.id; render();
    if (kind === "video") { if (!project().clips.length) seek(state.playhead, false); notify("影片已加入上層；重疊原聲會混音，可在右側個別靜音。"); }
  }
  async function saveProject() {
    const session = state.session;
    if (!session) return;
    if (!session.project.name.trim()) session.change(p => { p.name = "未命名專案"; });
    validateProject(session.project, state.media);
    requireTextStyleSupport(session.project, state.textStyleReady);
    try {
      const saved = await (session.autosaver ? session.autosaver.flush() : session.save(snapshot => api(`/api/editor/projects/${snapshot.id}`, json("PUT", snapshot))));
      if (state.session === session) { state.projects = state.projects.map(item => item.id === saved.id ? saved : item); renderProjectList(); renderStatus(); }
      return saved;
    } catch (error) { if (state.session === session) renderStatus(); throw error; }
  }
  async function loadProject(id, ask = true) {
    if (ask && !canSwitch()) return;
    state.busy = true; renderDisabled();
    try { if (ask && state.session) await saveProject(); const value = await api(`/api/editor/projects/${encodeURIComponent(id)}`); installProject(value.project || value); }
    finally { state.busy = false; renderProjectList(); renderDisabled(); }
  }
  async function newProject(copy = false) {
    if (!canSwitch()) return;
    const previous = copy ? clone(project()) : null;
    state.busy = true; renderDisabled();
    try {
      if (!copy) await saveProject();
      else { rememberDraft(); state.session.autosaver?.cancel(); }
      const payload = copy ? { ...editableContent(previous), name: `${previous.name} 副本`.slice(0, 200) } : { name: "未命名專案" };
      requireTextStyleSupport(payload, state.textStyleReady);
      const response = await api("/api/editor/projects", json("POST", payload));
      const value = response.project || response;
      state.projects.unshift(value); installProject(value);
      render();
    } finally { state.busy = false; renderDisabled(); }
  }
  function downloadRecovery(item) {
    const content = item.record.rawText ?? JSON.stringify(item.record, null, 2), blob = new Blob([content], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url;
    link.download = `${String(item.record.project?.name || "草稿救援").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")}.draft.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function saveRecoveryCopy(item) {
    if (locked() || !item) return;
    const resolution = resolveDraft(state.session.project, item.record, state.media), draft = resolution.draftProject;
    if (!draft) throw new Error(resolution.reason || "這份草稿目前無法另存；請先下載保留原始資料。");
    state.busy = true; renderDisabled();
    try {
      if (!state.session.conflict) await saveProject(); else rememberDraft();
      requireTextStyleSupport(draft, state.textStyleReady);
      const response = await api("/api/editor/projects", json("POST", { ...editableContent(draft), name: `${draft.name || "草稿"} 恢復副本`.slice(0, 200) }));
      const saved = response.project || response;
      if (item.key) storagePut(item.key, { ...item, dismissed: true });
      state.projects.unshift(saved); installProject(saved); notify("草稿已另存為新專案，原專案保持原樣。");
    } finally { state.busy = false; renderDisabled(); }
  }
  async function downloadProjectArchive() {
    if (locked() || !state.archivesReady) return;
    state.busy = true; renderDisabled();
    try {
      await saveProject(); const link = document.createElement("a");
      link.href = `/api/editor/projects/${encodeURIComponent(project().id)}/archive`; link.download = ""; link.click();
      notify("正在下載完整專案備份，包含剪輯、文字與使用到的影片、音訊和圖片。");
    } finally { state.busy = false; renderDisabled(); }
  }
  async function openProjectArchive(file) {
    if (!file || locked() || !state.archivesReady) return;
    if (!/\.h3edit\.zip$/i.test(file.name)) throw new Error("請選擇 .h3edit.zip 完整專案備份。");
    state.busy = true; renderDisabled();
    try {
      await saveProject();
      const imported = await uploadFile(file, percent => { $("archiveStatus").textContent = percent < 100 ? `上傳專案備份 ${percent}%` : "正在還原專案與素材…"; }, "/api/editor/projects/import");
      for (const media of imported.media || []) state.media.set(media.id, media);
      state.projects.unshift(imported.project); installProject(imported.project); renderMedia(); notify("已開啟備份為新專案，剪輯、文字與使用到的素材已還原。");
    } finally { state.busy = false; $("archiveStatus").textContent = ""; $("projectArchiveInput").value = ""; renderDisabled(); }
  }
  async function importFiles(files) {
    if (locked()) return;
    const supported = [...files].filter(file => /\.(mp4|webm|mp3|wav|m4a|aac|flac|ogg|opus|png|jpe?g|webp)$/i.test(file.name));
    if (!supported.length || supported.length !== files.length) throw new Error("請選擇影片、音訊，或 PNG／JPEG／WebP 圖片。");
    if (!state.imageOverlaysReady && files.some(file => /\.(png|jpe?g|webp)$/i.test(file.name))) throw new Error("Studio 尚未載入圖片圖層更新，請重新啟動 Studio 後重新整理。");
    state.busy = true; renderDisabled();
    const importedMedia = [];
    try {
      for (let index = 0; index < supported.length; index++) {
        const file = supported[index]; $("uploadStatus").textContent = `匯入 ${index + 1}/${supported.length}：${file.name}`;
        const response = await uploadFile(file, percent => { $("uploadStatus").textContent = `${index + 1}/${supported.length} · ${file.name} · ${percent < 100 ? `上傳 ${percent}%` : "正在確認素材…"}`; }); const media = response.media || response;
        state.media.set(media.id, media); renderMedia();
        importedMedia.push(media);
      }
      notify("素材已加入，可加入影片軌或音訊軌。");
    } finally { state.busy = false; $("uploadStatus").textContent = ""; $("uploadInput").value = ""; $("audioUploadInput").value = ""; $("imageUploadInput").value = ""; renderDisabled(); }
    return importedMedia;
  }
  function uploadFile(file, progress, path = "/api/editor/upload") {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest(), form = new FormData(); form.append("file", file);
      xhr.open("POST", path); xhr.responseType = "json";
      xhr.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded * 100 / event.total)); };
      xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.response) : reject(new Error(xhr.response?.error || `素材上傳未完成（${xhr.status}）。`));
      xhr.onerror = () => reject(new Error("素材上傳連線中斷，請重試。")); xhr.onabort = () => reject(new Error("上傳已取消。")); xhr.send(form);
    });
  }
  async function checkPreview(mediaId, force = false) {
    if (mediaKind(state.media.get(mediaId)) !== "video" || state.previewRequests.has(mediaId) || !force && state.previews.has(mediaId)) return;
    state.previewRequests.add(mediaId);
    try {
      const value = await api(`/api/editor/media/${encodeURIComponent(mediaId)}/preview`); state.previews.set(mediaId, value);
      if (["queued", "running"].includes(value.status)) { clearTimeout(state.previewTimers.get(mediaId)); state.previewTimers.set(mediaId, setTimeout(() => checkPreview(mediaId, true), 1200)); }
      if (value.status === "completed" && !state.trimDrag && !state.overlayDrag) { if (!state.playing && project()?.clips[state.previewIndex]?.media_id === mediaId) seek(state.playhead, false); else preloadNext(); }
    } catch { state.previews.set(mediaId, { status: "missing" }); }
    finally { state.previewRequests.delete(mediaId); renderCacheStatus(); }
  }
  function previewMediaId() { const selected = selectedClip(); return state.selectedKind === "overlay" && selected?.kind === "video" ? selected.media_id : project()?.clips[state.previewIndex]?.media_id || overlays(project() || {}).find(layer => layer.kind === "video" && layer.start <= state.playhead && state.playhead < layer.end)?.media_id; }
  function renderCacheStatus() {
    const mediaId = previewMediaId(), value = state.previews.get(mediaId);
    const busy = ["queued", "running"].includes(value?.status);
    $("buildPreview").disabled = !mediaId || busy || exporting();
    $("buildPreview").textContent = value?.status === "completed" ? "預覽已就緒" : "建立流暢預覽";
    if (value?.status === "completed") $("buildPreview").disabled = true;
    $("previewCacheStatus").textContent = busy ? `正在建立流暢預覽 ${Math.round(value.progress || 0)}% · 原片仍可播放` : value?.status === "completed" ? "流暢預覽已就緒；較低畫質僅用於預覽，匯出使用原始素材。" : value?.status === "failed" ? "預覽建立失敗，仍使用原始素材；可重試。" : "預載下一個裁切入點；仍常等待時，可建立流暢預覽。";
  }
  async function buildPreview() {
    const mediaId = previewMediaId(); if (!mediaId) return;
    const value = await api(`/api/editor/media/${encodeURIComponent(mediaId)}/preview`, json("POST")); state.previews.set(mediaId, value); renderCacheStatus();
    checkPreview(mediaId, true);
  }
  async function importJob(jobId, add = true) {
    if (locked()) return;
    state.busy = true; renderDisabled();
    let media;
    try { const response = await api("/api/editor/media", json("POST", { job_id: jobId })); media = response.media || response; state.media.set(media.id, media); renderMedia(); }
    finally { state.busy = false; renderDisabled(); }
    if (add) addMedia(media.id);
    return media;
  }
  async function loadJobs() {
    if (state.jobsLoading) return;
    state.jobsLoading = true; $("refreshJobs").disabled = true;
    try {
      const data = await api(`/api/jobs?page=${state.jobPage}&page_size=12`), items = list(data, "jobs");
      const completed = items.filter(job => job.status === "completed" && job.output);
      $("jobList").innerHTML = completed.map(job => `<article class="media-card"><span class="media-icon" aria-hidden="true">▸</span><div><strong title="${escape(job.name || job.id)}">${escape(job.name || "生成作品 " + job.id.slice(0, 8))}</strong><small>${escape(new Date(job.created_at).toLocaleDateString("zh-TW"))} · ${job.width || "—"}×${job.height || "—"}</small></div><button class="button ghost small" data-import-job="${escape(job.id)}">＋ 匯入並加入</button></article>`).join("") || '<p class="empty-note">這一頁還沒有可匯入的完成影片。可翻頁找其他作品，或直接上傳影片。</p>';
      $("jobsPrevious").disabled = state.jobPage <= 1;
      const pages = Number(data.total_pages || data.pages || (data.total ? Math.ceil(data.total / 12) : 0));
      $("jobsNext").disabled = pages ? state.jobPage >= pages : items.length < 12;
      $("jobsPage").textContent = `第 ${state.jobPage} 頁${pages ? " / " + pages : ""}`;
    } finally { state.jobsLoading = false; $("refreshJobs").disabled = false; renderDisabled(); }
  }
  function renderExport() {
    const job = state.exportJob;
    $("exportPanel").classList.toggle("hidden", !job); if (!job) return;
    const labels = { queued: "等待匯出", running: "正在匯出影片", completed: "影片匯出完成", failed: "匯出未完成", cancelled: "已取消匯出" };
    $("exportStatus").textContent = labels[job.status] || "確認匯出狀態";
    const progress = clamp(Number(job.progress) || 0, 0, 100);
    $("exportPercent").textContent = `${Math.round(progress)}%`; $("exportProgress").value = progress;
    $("cancelExport").classList.toggle("hidden", !exporting()); $("cancelExport").disabled = false;
    $("downloadExport").classList.toggle("hidden", job.status !== "completed");
    $("downloadExport").href = `/api/editor/exports/${encodeURIComponent(job.id)}/file?download=1`;
    $("exportDetail").textContent = job.error || (job.status === "completed" ? "MP4 已就緒，可下載分享。" : "使用已儲存的剪輯與輸出設定。");
    renderDisabled();
  }
  async function pollExport() {
    const id = state.exportJob?.id; if (!id) return;
    try {
      const response = await api(`/api/editor/exports/${encodeURIComponent(id)}`);
      if (state.exportJob?.id !== id) return;
      state.exportJob = response.export || response; storagePut(`h3-editor-export:${project().id}`, state.exportJob); renderExport();
      if (!exporting()) return;
    } catch (error) { if (state.exportJob?.id !== id) return; $("exportDetail").textContent = "暫時無法取得進度，正在重新連線…"; }
    clearTimeout(state.exportTimer); state.exportTimer = setTimeout(pollExport, 1200);
  }
  async function startExport() {
    if (locked() || !totalDuration(project())) return;
    pause(); state.busy = true; renderDisabled();
    try {
      await saveProject();
      const response = await api(`/api/editor/projects/${project().id}/exports`, json("POST"));
      state.exportJob = response.export || response; storagePut(`h3-editor-export:${project().id}`, state.exportJob); renderExport(); pollExport();
    } finally { state.busy = false; renderDisabled(); }
  }
  async function boot() {
    renderDisabled();
    const capabilities = await requireCapabilities(api); state.backendReady = true; state.archivesReady = capabilities.project_archives === true;
    state.overlayTracksReady = capabilities.overlay_tracks === true;
    state.textOverlaysReady = capabilities.text_overlays === true; state.imageOverlaysReady = capabilities.image_overlays === true; state.videoOverlaysReady = capabilities.video_overlays === true; state.visualFadesReady = capabilities.visual_fades === true; state.positionKeyframesReady = capabilities.position_keyframes === true; state.speedCurvesReady = capabilities.speed_curves === true;
    applyMotionCapability(capabilities);
    applyTextStyleCapability(capabilities);
    $("overlaySupport").textContent = state.textOverlaysReady && state.imageOverlaysReady ? state.videoOverlaysReady && state.visualFadesReady ? state.overlayTracksReady ? "按住片段拖到軌道中央可放入同一軌；上下邊緣可新增層級。影片最多同時 3 個上層。" : "同軌拖曳更新需重新啟動 Studio；原有圖層仍可編輯。" : "影片分層與淡入淡出更新需重新啟動 Studio；文字／圖片仍可編輯。" : "文字／圖片圖層需重新啟動 Studio 後重新整理，現有剪輯仍可使用。";
    $("archiveStatus").textContent = state.archivesReady ? "" : "完整備份需重新啟動 Studio 載入更新；一般儲存與開啟仍可使用。";
    const data = await Promise.all([api("/api/editor/media"), api("/api/editor/projects")]);
    state.media = new Map(list(data[0], "media").map(item => [item.id, item])); state.projects = list(data[1], "projects");
    renderMedia();
    const url = new URL(location.href), requested = url.searchParams.get("project"), last = storageGet("h3-editor-last-project");
    let id = requested || (state.projects.some(p => p.id === last) ? last : state.projects[0]?.id);
    if (!id) { const response = await api("/api/editor/projects", json("POST", { name: "我的剪輯專案" })); const value = response.project || response; state.projects.unshift(value); id = value.id; }
    await loadProject(id, false); state.busy = false; render();
    const jobId = url.searchParams.get("job");
    if (jobId) {
      let seen; try { seen = JSON.parse(sessionStorage.getItem(`h3-editor-job:${jobId}`)); } catch { seen = null; }
      if (!seen) {
        const media = await importJob(jobId, true); await saveProject();
        try { sessionStorage.setItem(`h3-editor-job:${jobId}`, JSON.stringify({ project_id: project().id, media_id: media.id })); } catch {}
      } else notify("這支作品已在本次操作中匯入，未重複加入。");
      const clean = new URL(location.href); clean.searchParams.delete("job"); history.replaceState(null, "", clean);
    }
  }

  $("dismissNotice").onclick = () => $("notice").classList.add("hidden");
  $("saveProject").onclick = action(async () => { await saveProject(); notify("專案已儲存。"); });
  $("newProject").onclick = action(() => newProject(false)); $("saveCopy").onclick = action(() => newProject(true)); $("conflictSaveCopy").onclick = action(() => newProject(true));
  $("openProject").onclick = () => { if (canSwitch()) { $("projectSelect").scrollIntoView({ block: "center", behavior: "smooth" }); $("projectSelect").focus(); try { $("projectSelect").showPicker(); } catch {} } };
  $("downloadProjectArchive").onclick = action(downloadProjectArchive);
  $("openProjectArchive").onclick = () => { if (!locked() && state.archivesReady) $("projectArchiveInput").click(); };
  $("projectArchiveInput").onchange = action(event => openProjectArchive(event.target.files[0]));
  $("projectSelect").onchange = action(event => loadProject(event.target.value));
  $("reloadProject").onclick = action(async () => {
    if (!canSwitch()) return;
    state.busy = true; renderDisabled();
    try {
      const session = state.session;
      session.autosaver?.cancel();
      const pending = session.autosaver?.pending || session.inFlight;
      if (pending) await pending.catch(() => {});
      const response = await api(`/api/editor/projects/${encodeURIComponent(session.project.id)}`), latest = response.project || response;
      if (session.dirty) {
        const record = { project: session.project, savedSignature: session.savedSignature, savedAt: Date.now() };
        if (!archiveDraft(session.project.id, record, "conflict")) throw new Error("瀏覽器無法備份目前剪輯，尚未載入其他版本。請先另存新專案。");
      }
      if (session.protectedDraft && !archiveDraft(session.project.id, session.protectedDraft, "原草稿保護備份")) throw new Error("原草稿尚未成功備份，未切換專案。請先下載草稿資料。");
      // Delete the active draft only after both reading and backup have succeeded.
      storagePut(`h3-editor-draft:${session.project.id}`, null); installProject(latest);
      notify("已開啟 Studio 版本，先前剪輯可從「草稿備份」找回。");
    } finally { state.busy = false; renderDisabled(); }
  });
  $("restoreDraft").onclick = action(() => saveRecoveryCopy(state.draft));
  $("downloadDraft").onclick = () => { if (state.draft) downloadRecovery(state.draft); };
  $("discardDraft").onclick = () => {
    if (!state.draft || locked()) return;
    if (state.draft.key) storagePut(state.draft.key, { ...state.draft, dismissed: true });
    state.draft = null; state.recoveries = storedRecoveries(project().id); renderRecovery(); renderDisabled();
  };
  $("recoveryList").onclick = action(event => {
    const download = event.target.closest("[data-recovery-download]"), copy = event.target.closest("[data-recovery-copy]"), key = download?.dataset.recoveryDownload || copy?.dataset.recoveryCopy;
    const item = state.recoveries.find(entry => entry.key === key); if (!item) return;
    if (download) downloadRecovery(item); else return saveRecoveryCopy(item);
  });
  $("projectName").oninput = () => { if (!locked()) { state.session.change(p => { p.name = $("projectName").value; }, "project-name"); renderProjectList(); } };
  $("projectName").onblur = () => { if (state.session) state.session.group = null; };
  $("outputSize").onchange = action(() => { const [width, height] = $("outputSize").value.split("x").map(Number); edit(p => { p.width = width; p.height = height; }, null, false); });
  $("outputFps").onchange = action(() => edit(p => { p.fps = Number($("outputFps").value); }, null, false));
  ["uploadZone", "emptyAddMedia"].forEach(id => $(id).onclick = () => { if (!locked()) $("uploadInput").click(); });
  $("addAudio").onclick = () => { if (!locked()) $("audioUploadInput").click(); };
  $("uploadInput").onchange = action(event => importFiles([...event.target.files]));
  $("audioUploadInput").onchange = action(event => importFiles([...event.target.files]));
  $("addTextOverlay").onclick = action(() => addOverlay("text"));
  $("addImageOverlay").onclick = () => { if (!locked() && state.imageOverlaysReady) $("imageUploadInput").click(); };
  $("imageUploadInput").onchange = action(async event => { const media = await importFiles([...event.target.files]); for (const item of media || []) addOverlay("image", item.id); });
  let dragDepth = 0;
  document.addEventListener("dragenter", event => { if ([...event.dataTransfer.types].includes("Files")) { event.preventDefault(); dragDepth++; document.body.classList.add("dragging-file"); } });
  document.addEventListener("dragover", event => { if ([...event.dataTransfer.types].includes("Files")) event.preventDefault(); });
  document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging-file"); } });
  document.addEventListener("drop", action(async event => { if (!event.dataTransfer.files.length) return; event.preventDefault(); dragDepth = 0; document.body.classList.remove("dragging-file"); await importFiles([...event.dataTransfer.files]); }));
  $("mediaList").onclick = action(event => { const layer = event.target.closest("[data-add-video-layer]"), audio = event.target.closest("[data-add-audio]"), button = event.target.closest("[data-add-media]"); if (locked()) return; if (layer) addOverlay("video", layer.dataset.addVideoLayer); else if (audio) addAudioMedia(audio.dataset.addAudio); else if (button) addMedia(button.dataset.addMedia); });
  $("jobList").onclick = action(event => { const button = event.target.closest("[data-import-job]"); if (button && !locked()) return importJob(button.dataset.importJob); });
  function showTab(jobs) { $("mediaPanel").classList.toggle("hidden", jobs); $("jobsPanel").classList.toggle("hidden", !jobs); $("mediaTab").classList.toggle("active", !jobs); $("jobsTab").classList.toggle("active", jobs); $("mediaTab").setAttribute("aria-selected", String(!jobs)); $("jobsTab").setAttribute("aria-selected", String(jobs)); }
  $("mediaTab").onclick = () => showTab(false); $("jobsTab").onclick = action(async () => { showTab(true); await loadJobs(); });
  $("refreshJobs").onclick = action(loadJobs);
  $("jobsPrevious").onclick = action(async () => { if (!state.jobsLoading && state.jobPage > 1) { state.jobPage--; await loadJobs(); } });
  $("jobsNext").onclick = action(async () => { if (!state.jobsLoading) { state.jobPage++; await loadJobs(); } });
  $("playPause").onclick = action(togglePlay); $("jumpStart").onclick = () => seek(0);
  $("previewSeek").oninput = () => queueSeek(Number($("previewSeek").value));
  $("buildPreview").onclick = action(buildPreview);
  function selectedEdit(update, reposition = true, group = null) { edit(p => { const clip = (state.selectedKind === "audio" ? audioClips(p) : p.clips).find(c => c.id === state.selected); if (clip) { const before = duration(clip); update(clip); if (duration(clip) < before) clampFades(clip, duration(clip)); } validateProject(p, state.media); }, group, reposition); }
  function overlayEdit(update, group = null) {
    if (state.selectedKind !== "overlay") return;
    edit(value => {
      const before = overlays(value).find(item => item.id === state.selected); if (!before) return;
      const position = positionAt(before, state.playhead - before.start), layer = { ...clone(before), ...position }; update(layer);
      if (positionKeyframes(before).length) {
        if (layer.x !== position.x || layer.y !== position.y) {
          if (!state.positionKeyframesReady) throw new Error("請重新啟動 Studio 後編輯位置動畫。");
          pause();
          layer.position_keyframes = upsertPositionKeyframe(before, state.playhead - before.start, { x: layer.x, y: layer.y });
        }
        if (positionKeyframes(layer).length) { layer.x = before.x; layer.y = before.y; }
      }
      const changes = Object.fromEntries(Object.entries(layer).filter(([key, val]) => JSON.stringify(val) !== JSON.stringify(before[key])));
      value.overlays = transformOverlay(value, state.selected, changes, state.media).project.overlays;
    }, group, false);
  }
  function jumpPositionKeyframe(event) {
    const point = event.target.closest("[data-position-time]"); if (!point) return false;
    event.preventDefault(); event.stopPropagation();
    if (locked()) return true;
    const layer = overlays(project()).find(item => item.id === point.dataset.positionLayer); if (!layer) return true;
    state.selectedKind = "overlay"; state.selected = layer.id; pause();
    renderTimeline(); seek(layer.start + Number(point.dataset.positionTime), false); renderInspector(); renderDisabled();
    return true;
  }
  $("positionKeyframeList").onclick = action(jumpPositionKeyframe);
  function showPositionFeedback(message) {
    $("positionActionStatus").textContent = message; $("positionActionStatus").classList.remove("hidden");
    notify(message);
  }
  function addSelectedPositionKeyframe() {
    if (locked()) return;
    const layer = selectedClip();
    if (state.selectedKind !== "overlay" || !layer || !["text", "image"].includes(layer.kind)) {
      showPositionFeedback("請先點選時間軸上的文字或圖片圖層；尚未加入時，可按左側「加入文字／加入圖片」。"); return;
    }
    document.querySelector(".inspector").scrollTop = 0;
    if (!state.positionKeyframesReady) {
      applyMotionCapability({ position_keyframes: false });
      showPositionFeedback("目前 Studio 尚未載入位置動畫，無法新增。請先儲存，關閉 Studio 啟動視窗，再重開 start_h3_studio.bat 並按「重新檢查」；只重開網頁或再點啟動檔不會更新。");
      return;
    }
    pause();
    let local = state.playhead - layer.start, moved = false;
    if (local < -1e-9 || local > layer.end - layer.start + 1e-9) {
      seek(layer.start, false); renderInspector(); renderDisabled();
      local = state.playhead - layer.start; moved = true;
      if (local < -1e-9 || local > layer.end - layer.start + 1e-9) {
        showPositionFeedback("此圖層的起點超過影片尾端，請先調整圖層時段或延長影片，再加入關鍵幀。"); return;
      }
    }
    local = clamp(local, 0, layer.end - layer.start);
    const points = positionKeyframes(layer), index = keyframeIndexAt(layer, local), position = positionAt(layer, local);
    const time = (layer.start + local).toFixed(2), pose = `X ${(position.x * 100).toFixed(1)}% · Y ${(position.y * 100).toFixed(1)}%`;
    if (index >= 0) {
      showPositionFeedback(`${moved ? "已移到圖層起點；" : ""}${time} 秒已有位置關鍵幀（${pose}）。移動播放游標，再拖曳或調整 X／Y，即可記錄下一點。`); return;
    }
    if (points.length >= motion.MAX_POSITION_KEYFRAMES) {
      showPositionFeedback(`此圖層已有 ${motion.MAX_POSITION_KEYFRAMES} 個位置關鍵幀，請先刪除不需要的點。`); return;
    }
    overlayEdit(item => { item.position_keyframes = upsertPositionKeyframe(item, local); });
    showPositionFeedback(`${moved ? "已移到圖層起點；" : ""}已加入 ${time} 秒位置關鍵幀（${pose}）。${!points.length ? "移動播放游標，再拖曳或調整 X／Y，會自動記錄下一點。" : "可在時間軸 ◆ 或右側清單查看。"}`);
  }
  $("showPositionAnimation").onclick = action(addSelectedPositionKeyframe);
  $("showSpeedCurve").onclick = () => { if (!locked() && selectedSpeedVideo()) { renderSpeedControls(); speedEditor.focusCurve(); } };
  $("addPositionKeyframe").onclick = action(addSelectedPositionKeyframe);
  $("checkPositionSupport").onclick = action(async () => {
    if (state.positionChecking) return;
    state.positionChecking = true; renderDisabled();
    try {
      const capabilities = await requireCapabilities(api); applyMotionCapability(capabilities); state.speedCurvesReady = capabilities.speed_curves === true; state.overlayTracksReady = capabilities.overlay_tracks === true;
      applyTextStyleCapability(capabilities);
      renderInspector();
      showPositionFeedback(state.positionKeyframesReady ? "位置關鍵幀已啟用，選取文字或圖片即可加入。" : "目前仍執行未載入位置動畫的 Studio。請先儲存並關閉 Studio 啟動視窗，再重新執行 start_h3_studio.bat；完成後按「重新檢查」。");
    } finally { state.positionChecking = false; renderDisabled(); }
  });
  $("deletePositionKeyframe").onclick = action(() => overlayEdit(layer => {
    const index = keyframeIndexAt(layer, state.playhead - layer.start); if (index < 0) return;
    layer.position_keyframes.splice(index, 1);
  }));
  $("clearPositionKeyframes").onclick = action(() => overlayEdit(layer => { layer.position_keyframes = []; }));
  for (const [id, direction] of [["previousPositionKeyframe", -1], ["nextPositionKeyframe", 1]]) $(id).onclick = action(() => {
    const layer = selectedClip(); if (!layer) return;
    const local = state.playhead - layer.start, points = positionKeyframes(layer).filter(point => point.time >= 0 && point.time <= layer.end - layer.start);
    const point = direction < 0 ? points.filter(point => point.time < local - 1e-6).at(-1) : points.find(point => point.time > local + 1e-6);
    if (point) { pause(); seek(layer.start + point.time, false); renderInspector(); renderDisabled(); }
  });
  $("positionEasing").onchange = action(() => { try { overlayEdit(layer => {
    const index = keyframeIndexAt(layer, state.playhead - layer.start); if (index >= 0) layer.position_keyframes[index].easing = $("positionEasing").value;
  }); } finally { renderInspector(); } });
  $("positionKeyframeTime").onchange = action(() => {
    const time = Number($("positionKeyframeTime").value), selected = selectedClip();
    try {
      if (!selected || !Number.isFinite(time) || time < selected.start || time > selected.end) throw new Error("關鍵幀時間需在此圖層的顯示範圍內。");
      overlayEdit(layer => {
        const index = keyframeIndexAt(layer, state.playhead - layer.start); if (index < 0) return;
        layer.position_keyframes[index].time = time - layer.start;
        layer.position_keyframes.sort((a, b) => a.time - b.time);
      });
      pause(); seek(time, false);
    } finally { renderInspector(); renderDisabled(); }
  });
  for (const [id, field, factor] of [["overlayStart", "start", 1], ["overlayEnd", "end", 1], ["overlayX", "x", 100], ["overlayY", "y", 100], ["overlayWidth", "width", 100], ["overlayRotation", "rotation", 1], ["overlayOpacity", "opacity", 100], ["overlayFontSize", "font_size", 100], ["overlayFadeIn", "fade_in", 1], ["overlayFadeOut", "fade_out", 1], ["layerSourceIn", "in", 1], ["layerSourceOut", "out", 1], ["layerSpeed", "speed", 1], ["layerVolume", "volume", 100]]) {
    $(id).onchange = action(() => { try { if (field === "speed") { applySelectedSpeed({ speed: Number($(id).value), speed_curve: [] }); return; } overlayEdit(layer => { const number = Number($(id).value) / factor; if (field === "start" && layer.kind === "video") layer.end += number - layer.start; layer[field] = number; }); } finally { renderInspector(); } });
  }
  for (const [id, field] of [["clipFadeIn", "fade_in"], ["clipFadeOut", "fade_out"]]) $(id).onchange = action(() => { try { selectedEdit(clip => { clip[field] = Number($(id).value); }, false); } finally { renderInspector(); } });
  for (const [id, move] of [["moveToLayer", true], ["copyToLayer", false]]) $(id).onclick = action(() => {
    let layerId; edit(value => { layerId = promoteClip(value, state.selected, move); validateProject(value, state.media); }, null, move);
    state.selectedKind = "overlay"; state.selected = layerId; render();
    notify(move ? "已移至上層並保留原時間點；主軌後續影片已向前接合，獨立音訊位置不變。" : "已複製到上層，副本預設靜音。可調整大小、位置與音量。");
  });
  for (const [id, fill] of [["layerFill", true], ["layerPip", false]]) $(id).onclick = action(() => overlayEdit(layer => {
    const media = state.media.get(layer.media_id); if (!media) return;
    const ratio = project().height / project().width * media.width / media.height;
    layer.width = fill ? clamp(Math.max(1, ratio), 0.02, 2) : Math.min(0.35, ratio * 0.35);
    layer.x = fill ? 0.5 : 0.77; layer.y = fill ? 0.5 : 0.76;
  }));
  $("overlayText").oninput = action(() => overlayEdit(layer => { layer.text = $("overlayText").value; }, `overlay-text:${state.selected}`));
  $("overlayText").onchange = () => { if (state.session) state.session.group = null; };
  function editTextStyle(update) {
    if (locked() || state.selectedKind !== "overlay" || selectedClip()?.kind !== "text") return;
    if (!state.textStyleReady) throw new Error("請先儲存專案並重新啟動 Studio，以載入文字描邊與漸層功能。");
    try { overlayEdit(update); } finally { renderInspector(); renderTextStyleControls(selectedClip(), true); renderDisabled(); }
  }
  for (const [id, field] of [["overlayFillMode", "fill_mode"], ["overlayGradientStart", "gradient_start"], ["overlayGradientEnd", "gradient_end"], ["overlayStrokeColor", "stroke_color"]]) $(id).onchange = action(() => editTextStyle(layer => { layer[field] = $(id).value; }));
  for (const [id, field, factor] of [["overlayStrokeWidth", "stroke_width", 100], ["overlayGradientAngle", "gradient_angle", 1]]) $(id).onchange = action(() => editTextStyle(layer => { layer[field] = Number($(id).value) / factor; }));
  $("swapGradientColors").onclick = action(() => editTextStyle(layer => { const style = { ...TEXT_STYLE_DEFAULTS, ...canonicalTextStyle(layer) }; layer.gradient_start = style.gradient_end; layer.gradient_end = style.gradient_start; }));
  for (const [id, angle] of [["gradientHorizontal", 0], ["gradientVertical", 90]]) $(id).onclick = action(() => editTextStyle(layer => { layer.gradient_angle = angle; }));
  $("checkTextStyleSupport").onclick = action(async () => {
    if (state.textStyleChecking) return;
    state.textStyleChecking = true; renderDisabled();
    try {
      applyTextStyleCapability(await requireCapabilities(api));
      if (state.textStyleReady) { renderOverlayPreview(); notify("文字描邊與漸層已啟用，選取文字後可調整。"); }
      else notify("目前仍執行舊版 Studio。請先儲存專案並關閉啟動視窗，再重新啟動後檢查。");
      renderInspector();
    } finally { state.textStyleChecking = false; renderDisabled(); }
  });
  for (const [id, field] of [["overlayColor", "color"], ["overlayBackground", "background"], ["overlayAlign", "align"]]) $(id).onchange = action(() => overlayEdit(layer => { layer[field] = $(id).value; }));
  $("overlayBold").onchange = action(() => overlayEdit(layer => { layer.bold = $("overlayBold").checked; }));
  $("overlayTransparent").onchange = action(() => overlayEdit(layer => { layer.background = $("overlayTransparent").checked ? "transparent" : $("overlayBackground").value; }));
  $("retryTextPreview").onclick = () => {
    const item = state.rasterNodes.get(state.selected);
    if (!item) { notify("將播放游標移到圖層的顯示範圍內，即可預覽文字。"); return; }
    item.key = null; item.error = null; renderOverlayPreview(); $("overlayRasterStatus").textContent = "正在重試文字預覽…";
  };
  ["clipIn", "clipOut"].forEach(id => $(id).onchange = action(() => { try { selectedEdit(clip => { clip[id === "clipIn" ? "in" : "out"] = Number($(id).value); }); } finally { renderInspector(); } }));
  $("clipVolume").onchange = action(() => { try { selectedEdit(clip => { clip.volume = Number($("clipVolume").value) / 100; }, false); } finally { renderInspector(); } });
  $("volumeRange").oninput = action(() => { const value = Number($("volumeRange").value); selectedEdit(clip => { clip.volume = value / 100; }, false, `volume:${state.selected}`); });
  $("volumeRange").onchange = () => { if (state.session) state.session.group = null; };
  function changeSpeed(value) { applySelectedSpeed({ speed: value, speed_curve: [] }); }
  $("clipSpeed").onchange = action(() => { try { changeSpeed(Number($("clipSpeed").value)); } finally { renderInspector(); } });
  document.querySelectorAll("[data-speed]").forEach(button => button.onclick = action(() => changeSpeed(Number(button.dataset.speed))));
  for (const [id, field] of [["audioStart", "start"], ["audioTrack", "track"], ["audioFadeIn", "fade_in"], ["audioFadeOut", "fade_out"]]) $(id).onchange = action(() => { try { selectedEdit(clip => { clip[field] = Number($(id).value); }, field === "start" || field === "track"); } finally { renderInspector(); } });
  $("muteOriginal").onclick = action(() => selectedEdit(clip => { clip.volume = clip.volume ? 0 : 1; }, false));
  $("detachAudio").onclick = action(() => { let id; edit(p => { id = detachAudio(p, state.selected); validateProject(p, state.media); }, null, false); state.selected = id; state.selectedKind = "audio"; render(); notify("原聲已分離到音訊軌，原影片已靜音。"); });
  $("resetTrim").onclick = action(() => selectedEdit(clip => { clip.in = 0; clip.out = Number(state.media.get(clip.media_id).duration); }));
  function doSplit() { if (state.selectedKind === "overlay" && !state.overlayTracksReady) throw new Error("請重新啟動 Studio，以在同一軌道分割片段。"); let selected; edit(p => { selected = state.selectedKind === "overlay" ? splitOverlayAt(p, state.selected, state.playhead) : state.selectedKind === "audio" ? splitAudioAt(p, state.selected, state.playhead) : splitAt(p, state.playhead); }, null, state.selectedKind !== "overlay"); if (selected) { state.selected = selected; render(); } }
  $("splitClip").onclick = action(doSplit);
  $("duplicateClip").onclick = action(() => { let next; edit(p => { const clips = state.selectedKind === "overlay" ? overlays(p) : state.selectedKind === "audio" ? audioClips(p) : p.clips, index = clips.findIndex(c => c.id === state.selected); if (index >= 0) { next = uid(); const copied = { ...clips[index], id: next }; if (state.selectedKind === "audio") { copied.start += duration(copied); copied.track = freeAudioTrack(p, copied.start, duration(copied)); } if (state.selectedKind === "overlay") { delete copied.track_id; copied.x = clamp(copied.x + 0.025, 0, 1); copied.y = clamp(copied.y + 0.025, 0, 1); let after = index; while (after + 1 < clips.length && core.overlayTrackId(clips[after + 1]) === core.overlayTrackId(clips[index])) after++; clips.splice(after + 1, 0, copied); } else clips.splice(index + 1, 0, copied); } }, null, state.selectedKind !== "overlay"); if (next) { state.selected = next; render(); } });
  function deleteSelected() { edit(p => { const clips = state.selectedKind === "overlay" ? overlays(p) : state.selectedKind === "audio" ? audioClips(p) : p.clips, index = clips.findIndex(c => c.id === state.selected); if (index >= 0) clips.splice(index, 1); }, null, state.selectedKind !== "overlay"); }
  $("deleteClip").onclick = action(deleteSelected);
  function moveSelected(delta) { edit(p => { if (state.selectedKind === "audio") { const clip = audioClips(p).find(item => item.id === state.selected); if (clip) clip.start = clamp(clip.start + delta, 0, 600); } else if (state.selectedKind === "overlay") { core.moveOverlayTrack(p, state.selected, delta); } else { const clips = p.clips, index = clips.findIndex(c => c.id === state.selected), target = index + delta; if (index >= 0 && target >= 0 && target < clips.length) { const [clip] = clips.splice(index, 1); clips.splice(target, 0, clip); } } }, null, state.selectedKind !== "overlay"); }
  $("moveEarlier").onclick = action(() => moveSelected(-1)); $("moveLater").onclick = action(() => moveSelected(1));
  $("layerDown").onclick = action(() => moveSelected(-1)); $("layerUp").onclick = action(() => moveSelected(1));
  function transportSignature(value) { return JSON.stringify({ clips: value.clips.map(clip => [clip.id, clip.media_id, clip.in, clip.out, speedOf(clip), canonicalSpeedCurve(clip)]), audio: audioClips(value), layers: overlays(value).filter(layer => layer.kind === "video").map(layer => [layer.id, layer.media_id, layer.in, layer.out, layer.start, speedOf(layer), canonicalSpeedCurve(layer)]).sort((a, b) => a[0].localeCompare(b[0])) }); }
  function travel(undo) {
    if (locked()) return;
    const before = transportSignature(project()); state.session.travel(undo);
    if (!selectedClip()) {
      const groups = [["video", project().clips], ["overlay", overlays(project())], ["audio", audioClips(project())]];
      const same = groups.find(([, items]) => items.some(item => item.id === state.selected));
      if (same) state.selectedKind = same[0];
      else { const available = groups.find(([, items]) => items.length); state.selectedKind = available?.[0] || "video"; state.selected = available?.[1][0].id || null; }
    }
    render(); if (before !== transportSignature(project())) { pause(); seek(Math.min(state.playhead, totalDuration(project())), false); }
  }
  $("undo").onclick = () => travel(true); $("redo").onclick = () => travel(false);
  $("timelineZoom").oninput = () => { state.zoom = Number($("timelineZoom").value); renderTimeline(); };
  $("timelineCanvas").addEventListener("pointerdown", action(beginTrim));
  $("timelineCanvas").addEventListener("pointerdown", action(beginTrackGesture));
  $("timelineCanvas").addEventListener("pointermove", event => {
    const drag = state.trackDrag; if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault(); drag.lastX = event.clientX; drag.lastY = event.clientY;
    if (drag.frame === null) drag.frame = requestAnimationFrame(flushTrackGesture);
  });
  $("timelineCanvas").addEventListener("pointerup", event => {
    const drag = state.trackDrag; if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault(); event.stopPropagation(); drag.lastX = event.clientX; drag.lastY = event.clientY; finishTrackGesture(true);
  });
  for (const name of ["pointercancel", "lostpointercapture"]) $("timelineCanvas").addEventListener(name, event => { if (state.trackDrag?.pointerId === event.pointerId) finishTrackGesture(false); });
  for (const canvas of [$("timelineCanvas"), $("overlayCanvas")]) {
    canvas.addEventListener("pointerdown", action(event => beginOverlayGesture(event, canvas)));
    canvas.addEventListener("pointermove", event => {
      const drag = state.overlayDrag; if (!drag || drag.pointerId !== event.pointerId || drag.canvas !== canvas) return;
      event.preventDefault(); drag.lastX = event.clientX; drag.lastY = event.clientY;
      if (drag.frame === null) drag.frame = requestAnimationFrame(flushOverlayGesture);
    });
    canvas.addEventListener("pointerup", event => { const drag = state.overlayDrag; if (!drag || drag.pointerId !== event.pointerId || drag.canvas !== canvas) return; event.preventDefault(); event.stopPropagation(); drag.lastX = event.clientX; drag.lastY = event.clientY; finishOverlayGesture(true); });
    for (const name of ["pointercancel", "lostpointercapture"]) canvas.addEventListener(name, event => { if (state.overlayDrag?.pointerId === event.pointerId && state.overlayDrag.canvas === canvas) finishOverlayGesture(false); });
    canvas.addEventListener("click", action(event => { if (jumpPositionKeyframe(event)) { event.stopImmediatePropagation(); return; } if (event.target.closest("[data-overlay-id]") || state.overlayDrag || Date.now() < state.trimSuppressUntil) { event.preventDefault(); event.stopImmediatePropagation(); } }), true);
    canvas.addEventListener("dragstart", event => { if (event.target.closest("[data-overlay-id]") || state.overlayDrag) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  }
  $("timelineCanvas").addEventListener("pointermove", event => { if (state.trimDrag?.pointerId !== event.pointerId) return; event.preventDefault(); state.trimDrag.lastX = event.clientX; queueTrimPreview(); });
  $("timelineCanvas").addEventListener("pointerup", event => { if (state.trimDrag?.pointerId !== event.pointerId) return; event.preventDefault(); event.stopPropagation(); state.trimDrag.lastX = event.clientX; finishTrim(true); });
  $("timelineCanvas").addEventListener("pointercancel", event => { if (state.trimDrag?.pointerId === event.pointerId) finishTrim(false); });
  $("timelineCanvas").addEventListener("lostpointercapture", event => { if (state.trimDrag?.pointerId === event.pointerId) finishTrim(false); });
  $("timelineCanvas").addEventListener("click", event => {
    if (state.trimDrag || Date.now() < state.trimSuppressUntil || event.target.closest("[data-trim-edge]")) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  $("timelineCanvas").addEventListener("dragstart", event => { if (state.trimDrag || event.target.closest("[data-trim-edge]")) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  $("timelineCanvas").onclick = event => { if (!project() || state.trimDrag || state.overlayDrag || state.speedDrag || state.trackDrag) return; const clip = event.target.closest("[data-clip-id]"), audio = event.target.closest("[data-audio-id]"); if (audio) { state.selected = audio.dataset.audioId; state.selectedKind = "audio"; } else if (clip) { state.selected = clip.dataset.clipId; state.selectedKind = "video"; } const rect = $("timelineCanvas").getBoundingClientRect(); queueSeek((event.clientX - rect.left) / state.zoom); renderTimeline(); renderInspector(); renderDisabled(); };
  $("timelineScroll").onscroll = () => { $("trackLabels").style.transform = `translateY(${-$("timelineScroll").scrollTop}px)`; if (state.trimDrag) queueTrimPreview(); if (state.overlayDrag && state.overlayDrag.mode !== "position" && state.overlayDrag.frame === null) state.overlayDrag.frame = requestAnimationFrame(flushOverlayGesture); if (state.trackDrag && state.trackDrag.frame === null) state.trackDrag.frame = requestAnimationFrame(flushTrackGesture); };
  $("audioTracks").ondragstart = event => { const clip = event.target.closest("[data-audio-id]"); if (!clip || locked()) { event.preventDefault(); return; } const value = audioClips(project()).find(item => item.id === clip.dataset.audioId); state.audioDragOffset = (event.clientX - clip.getBoundingClientRect().left) / state.zoom; event.dataTransfer.setData("application/x-h3-editor-audio", value.id); event.dataTransfer.effectAllowed = "move"; };
  $("audioTracks").ondragover = event => { if (!locked() && [...event.dataTransfer.types].includes("application/x-h3-editor-audio")) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; } };
  $("audioTracks").ondrop = action(event => { const id = event.dataTransfer.getData("application/x-h3-editor-audio"), track = event.target.closest("[data-audio-track]"); if (!id || !track || locked()) return; event.preventDefault(); event.stopPropagation(); const start = clamp((event.clientX - $("timelineCanvas").getBoundingClientRect().left) / state.zoom - (state.audioDragOffset || 0), 0, 600); edit(p => { const clip = audioClips(p).find(item => item.id === id); if (clip) { clip.start = start; clip.track = Number(track.dataset.audioTrack); } }); state.selected = id; state.selectedKind = "audio"; render(); });
  $("clipTrack").ondragstart = event => event.preventDefault();
  $("exportProject").onclick = action(startExport);
  $("cancelExport").onclick = action(async () => { if (!exporting()) return; $("cancelExport").disabled = true; try { await api(`/api/editor/exports/${state.exportJob.id}/cancel`, json("POST")); await pollExport(); } finally { $("cancelExport").disabled = false; } });
  document.addEventListener("keydown", action(async event => {
    if (state.trackDrag) { if (event.key === "Escape") finishTrackGesture(false); event.preventDefault(); return; }
    if (state.speedDrag) { event.preventDefault(); return; }
    if (state.overlayDrag) { if (event.key === "Escape") finishOverlayGesture(false); event.preventDefault(); return; }
    if (state.trimDrag) { if (event.key === "Escape") finishTrim(false); event.preventDefault(); return; }
    if (event.target.closest("[data-position-time]") && (event.key === "Enter" || event.code === "Space")) { jumpPositionKeyframe(event); return; }
    const overlayNode = event.target.closest("[data-overlay-id]");
    if (overlayNode && (event.key === "Enter" || event.code === "Space") && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); if (locked() || event.repeat) return;
      const inPreview = overlayNode.classList.contains("preview-overlay"), edge = event.target.closest("[data-overlay-edge]")?.dataset.overlayEdge;
      state.selectedKind = "overlay"; state.selected = overlayNode.dataset.overlayId;
      renderTimeline(); renderInspector(); renderDisabled(); renderOverlayPreview();
      if (!inPreview) {
        const replacement = [...$("overlayTracks").querySelectorAll("[data-overlay-id]")].find(node => node.dataset.overlayId === state.selected);
        (edge ? replacement?.querySelector(`[data-overlay-edge="${edge}"]`) : replacement)?.focus({ preventScroll: true });
      }
      return;
    }
    if (overlayNode && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); if (locked()) return;
      state.selectedKind = "overlay"; state.selected = overlayNode.dataset.overlayId;
      if (overlayNode.classList.contains("preview-overlay")) overlayEdit(layer => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") layer.x = clamp(layer.x + (event.key === "ArrowRight" ? 1 : -1) / project().width, 0, 1); else layer.y = clamp(layer.y + (event.key === "ArrowDown" ? 1 : -1) / project().height, 0, 1); });
      else if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        const edge = event.target.closest("[data-overlay-edge]")?.dataset.overlayEdge;
        const transaction = new OverlayTransaction(state.session, state.selected, state.media); transaction.updateTime(edge || "move", (event.key === "ArrowRight" ? 1 : -1) / project().fps); transaction.commit(); render();
        const replacement = [...$("overlayTracks").querySelectorAll("[data-overlay-id]")].find(node => node.dataset.overlayId === state.selected);
        (edge ? replacement?.querySelector(`[data-overlay-edge="${edge}"]`) : replacement)?.focus({ preventScroll: true });
      }
      return;
    }
    const handle = event.target.closest("[data-trim-edge]");
    if (handle && ["ArrowLeft", "ArrowRight"].includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); keyboardTrim(handle, event.key === "ArrowLeft" ? -1 : 1); return; }
    const writing = event.target.closest("input,textarea,select,[contenteditable=true]");
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (!locked()) await saveProject(); return; }
    if (event.target.closest("#speedEditor") && !((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase()))) return;
    if (writing || event.altKey) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); travel(!event.shiftKey); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); travel(false); }
    else if (event.code === "Space") { event.preventDefault(); if (!event.repeat) await togglePlay(); }
    else if (!event.ctrlKey && !event.metaKey && event.key.toLowerCase() === "s" && !locked()) { event.preventDefault(); doSplit(); }
    else if (["Delete", "Backspace"].includes(event.key) && !locked()) { event.preventDefault(); deleteSelected(); }
  }));
  root.addEventListener("blur", () => { finishTrim(false); finishOverlayGesture(false); speedEditor.cancelGesture(); finishTrackGesture(false); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { pause(); finishTrim(false); finishOverlayGesture(false); speedEditor.cancelGesture(); finishTrackGesture(false); } });
  root.addEventListener("beforeunload", event => { finishTrim(false); finishOverlayGesture(false); speedEditor.cancelGesture(); finishTrackGesture(false); if (state.session?.dirty || state.session?.inFlight) { rememberDraft(); event.preventDefault(); event.returnValue = ""; } });
  root.addEventListener("resize", () => { previewViewport.cancelPan(); resizePreview(); renderTimeline(); });
  if (root.ResizeObserver) new ResizeObserver(() => { previewViewport.cancelPan(); resizePreview(); }).observe($("videoStage"));
  requestAnimationFrame(playbackTick);
  boot().catch(error => { state.busy = false; errorNotice(error); renderDisabled(); });
})(typeof window !== "undefined" ? window : globalThis);
