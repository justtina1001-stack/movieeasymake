/* SRT interchange and editable subtitle layers; independent of editor UI state. */
(function (root) {
  "use strict";
  const MAX_FILE_BYTES = 16 * 1024 * 1024;
  const MAX_SUBTITLE_CUES = 5000;
  const SUBTITLE_TRACK_PREFIX = "subtitle_";
  const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/;
  const STYLE_FIELDS = Object.freeze(["x", "y", "width", "rotation", "opacity", "font_size", "color", "background", "bold", "align", "font_family", "stroke_width", "stroke_color", "fill_mode", "gradient_start", "gradient_end", "gradient_angle"]);
  const DEFAULT_STYLE = Object.freeze({ x: 0.5, y: 0.86, width: 0.86, rotation: 0, opacity: 1, font_size: 0.045, color: "#ffffff", background: "transparent", bold: true, align: "center", stroke_width: 0.08, stroke_color: "#000000", fill_mode: "solid", gradient_start: "#ffffff", gradient_end: "#ff8a3d", gradient_angle: 90 });
  const TEXT_PRESETS = Object.freeze({
    clean: DEFAULT_STYLE,
    yellow: Object.freeze({ ...DEFAULT_STYLE, color: "#ffd966" }),
    banner: Object.freeze({ ...DEFAULT_STYLE, background: "#101010", stroke_width: 0 }),
  });
  function cueError(index, message) { return new Error(`第 ${index + 1} 段字幕：${message}`); }
  function normalizedText(value, index) {
    if (typeof value !== "string") throw cueError(index, "文字格式無效。");
    const text = value.replace(/\r\n?/g, "\n");
    if (!text.trim()) throw cueError(index, "文字不可空白。");
    if (/[\u0000-\u0008\u000b-\u001f]/.test(text)) throw cueError(index, "文字不能包含控制字元。");
    if (Array.from(text).length > 500 || text.split("\n").length > 10) throw cueError(index, "文字最多 500 字、10 行。");
    if (/\n[\t ]*\n/.test(text) || /^[\t ]*\n|\n[\t ]*$/.test(text)) throw cueError(index, "字幕文字不能包含空白分隔行，請將每段字幕分開。");
    return text;
  }
  function validatedCue(cue, index, tolerance = 0) {
    if (!cue || typeof cue !== "object" || !Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.start < -tolerance || cue.end > 600 + tolerance || cue.start >= cue.end) throw cueError(index, "起訖時間須在 0～600 秒，且結束時間須晚於開始時間。");
    return { start: cue.start, end: cue.end, text: normalizedText(cue.text, index) };
  }
  function timestamp(value, index) {
    const match = /^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$/.exec(value);
    if (!match || Number(match[2]) > 59 || Number(match[3]) > 59) throw cueError(index, "時間格式須為 00:00:00,000。");
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
  }
  function parseSRT(value) {
    if (typeof value !== "string") throw new Error("請讀取 SRT 字幕文字。");
    if (new TextEncoder().encode(value).byteLength > MAX_FILE_BYTES) throw new Error("字幕檔最多 16 MiB，請先拆分檔案。");
    const input = value.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").trim();
    if (!input) throw new Error("字幕檔是空的。");
    return input.split(/\n[\t ]*\n(?:[\t ]*\n)*/).map((block, index) => {
      const lines = block.split("\n");
      if (/^\d+$/.test(lines[0].trim())) lines.shift();
      const timing = /^\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*$/.exec(lines.shift() || "");
      if (!timing) throw cueError(index, "找不到有效的 SRT 起訖時間；字幕編號須為數字，字幕之間須空一行。");
      const text = lines.join("\n");
      // Missing separators must not turn the next cue's timing into visible text.
      if (lines.some(line => /\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->/.test(line))) throw cueError(index, "字幕之間須空一行，請確認下一段的時間標記。");
      return validatedCue({ start: timestamp(timing[1], index), end: timestamp(timing[2], index), text }, index);
    });
  }
  function formatMilliseconds(value) {
    const hours = Math.floor(value / 3600000), minutes = Math.floor(value / 60000) % 60, seconds = Math.floor(value / 1000) % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")},${String(value % 1000).padStart(3, "0")}`;
  }
  function orderedCues(cues, tolerance = 0) {
    if (!Array.isArray(cues) || !cues.length) throw new Error("沒有可用的字幕。");
    return cues.map((cue, index) => ({ ...validatedCue(cue, index, tolerance), index })).sort((left, right) => left.start - right.start || left.index - right.index);
  }
  function stringifySRT(cues) {
    return orderedCues(cues).map((cue, index) => {
      const start = Math.round(cue.start * 1000), end = Math.round(cue.end * 1000);
      if (end <= start) throw cueError(cue.index, "四捨五入為毫秒後沒有顯示時長，請延長字幕。");
      return `${index + 1}\n${formatMilliseconds(start)} --> ${formatMilliseconds(end)}\n${cue.text}`;
    }).join("\n\n") + "\n";
  }
  function isSubtitleTrack(trackId) {
    return typeof trackId === "string" && SAFE_ID.test(trackId) && trackId.startsWith(SUBTITLE_TRACK_PREFIX) && trackId.length > SUBTITLE_TRACK_PREFIX.length;
  }
  function subtitleTracks(project) {
    const tracks = new Map();
    for (const layer of project?.overlays || []) {
      // Mixed overlay tracks are valid in the editor. Only text becomes SRT.
      if (!layer || layer.kind !== "text" || !isSubtitleTrack(layer.track_id)) continue;
      if (!tracks.has(layer.track_id)) tracks.set(layer.track_id, { id: layer.track_id, count: 0, layers: [] });
      const track = tracks.get(layer.track_id); track.layers.push(layer); track.count++;
    }
    return [...tracks.values()].map(track => ({ ...track, layers: track.layers.sort((left, right) => left.start - right.start) }));
  }
  function subtitleCount(project) {
    return (project?.overlays || []).filter(layer => layer?.kind === "text" && isSubtitleTrack(layer.track_id)).length;
  }
  function selectedStyle(style) {
    if (!style || typeof style !== "object" || Array.isArray(style)) throw new Error("字幕樣式格式無效。");
    const picked = {};
    for (const field of STYLE_FIELDS) if (Object.hasOwn(style, field)) picked[field] = style[field];
    const numeric = { x: [0, 1], y: [0, 1], width: [0.02, 2], rotation: [-180, 180], opacity: [0, 1], font_size: [0.01, 0.3], stroke_width: [0, 0.25], gradient_angle: [0, 360] };
    for (const [field, [minimum, maximum]] of Object.entries(numeric)) if (Object.hasOwn(picked, field) && (!Number.isFinite(picked[field]) || picked[field] < minimum || picked[field] > maximum)) throw new Error(`字幕樣式 ${field} 超出可用範圍。`);
    for (const field of ["color", "stroke_color", "gradient_start", "gradient_end"]) if (Object.hasOwn(picked, field) && (typeof picked[field] !== "string" || !/^#[0-9a-f]{6}$/i.test(picked[field]))) throw new Error("字幕顏色須為六位數色碼。");
    if (Object.hasOwn(picked, "background") && picked.background !== "transparent" && (typeof picked.background !== "string" || !/^#[0-9a-f]{6}$/i.test(picked.background))) throw new Error("字幕底色須為透明或六位數色碼。");
    if (Object.hasOwn(picked, "bold") && typeof picked.bold !== "boolean") throw new Error("字幕粗體設定無效。");
    if (Object.hasOwn(picked, "align") && !["left", "center", "right"].includes(picked.align)) throw new Error("字幕對齊設定無效。");
    if (Object.hasOwn(picked, "fill_mode") && !["solid", "linear_gradient"].includes(picked.fill_mode)) throw new Error("字幕填色設定無效。");
    if (Object.hasOwn(picked, "font_family") && (typeof picked.font_family !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(picked.font_family))) throw new Error("請從清單選擇字幕字型。");
    return picked;
  }
  function createSubtitleLayers(cues, options = {}) {
    const { fps, totalDuration, existingCount, makeId, style = DEFAULT_STYLE } = options;
    if (!Number.isFinite(totalDuration) || totalDuration <= 0 || totalDuration > 600) throw new Error("請先加入影片，再匯入字幕；影片長度須在 10 分鐘以內。");
    if (!Number.isFinite(fps) || fps <= 0) throw new Error("專案影格率無效。");
    if (!Number.isInteger(existingCount) || existingCount < 0 || existingCount > MAX_SUBTITLE_CUES) throw new Error("現有字幕句數無效。");
    const ordered = orderedCues(cues, 0.001), appearance = { ...DEFAULT_STYLE, ...selectedStyle(style) };
    if (ordered.length + existingCount > MAX_SUBTITLE_CUES) throw new Error(`字幕獨立容量為 5,000 句，目前可再匯入 ${MAX_SUBTITLE_CUES - existingCount} 句；此檔共有 ${ordered.length} 句，未匯入任何字幕。`);
    for (let index = 0; index < ordered.length; index++) {
      const cue = ordered[index];
      if (cue.end > totalDuration + 0.001 + 1e-9 || cue.start >= totalDuration) throw cueError(cue.index, "超過影片時間範圍，未匯入任何字幕。");
      cue.start = Math.max(0, cue.start); cue.end = Math.min(cue.end, totalDuration);
      if (cue.end - cue.start < 1 / fps - 1e-9) throw cueError(cue.index, "顯示時長至少要有一個專案影格，未匯入任何字幕。");
      if (index > 0 && cue.start < ordered[index - 1].end - 1e-9) throw cueError(cue.index, "與前一段字幕重疊；同一字幕軌不可重疊，未匯入任何字幕。");
    }
    if (typeof makeId !== "function") throw new Error("無法建立字幕編號。");
    const generatedTrackId = makeId();
    if (typeof generatedTrackId !== "string" || !SAFE_ID.test(generatedTrackId)) throw new Error("字幕軌編號無效。");
    const trackId = SUBTITLE_TRACK_PREFIX + generatedTrackId;
    if (!isSubtitleTrack(trackId)) throw new Error("字幕軌編號無效。");
    const ids = new Set([trackId]);
    const layers = ordered.map(cue => {
      const id = makeId();
      if (typeof id !== "string" || !SAFE_ID.test(id) || ids.has(id)) throw new Error("字幕編號無效或重複。");
      ids.add(id);
      return { id, kind: "text", track_id: trackId, start: cue.start, end: cue.end, text: cue.text, ...appearance };
    });
    return { trackId, layers };
  }
  function applySubtitleStyle(project, trackId, style) {
    if (!isSubtitleTrack(trackId)) throw new Error("請選擇字幕軌。");
    const track = subtitleTracks(project).find(item => item.id === trackId);
    if (!track || !track.count) throw new Error("找不到這個字幕軌，或此軌沒有文字字幕。");
    if (track.layers.some(layer => layer.kind !== "text")) throw new Error("字幕樣式只能套用至文字字幕軌。");
    const appearance = selectedStyle(style), copy = JSON.parse(JSON.stringify(project));
    for (const layer of copy.overlays) if (layer.kind === "text" && layer.track_id === trackId) Object.assign(layer, appearance);
    return copy;
  }
  function decodeSubtitleBuffer(buffer, encoding = "utf-8") {
    let bytes;
    if (buffer instanceof ArrayBuffer) bytes = new Uint8Array(buffer);
    else if (ArrayBuffer.isView(buffer)) bytes = new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    else throw new Error("字幕檔案格式無效。");
    if (bytes.byteLength > MAX_FILE_BYTES) throw new Error("字幕檔最多 16 MiB，請先拆分檔案。");
    const requested = typeof encoding === "string" ? encoding.toLowerCase() : "";
    if (!["utf-8", "utf8", "big5"].includes(requested)) throw new Error("字幕編碼請選擇 UTF-8 或 Big5。");
    let selected = requested === "utf8" ? "utf-8" : requested, offset = 0;
    if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) { selected = "utf-8"; offset = 3; }
    else if (bytes[0] === 0xff && bytes[1] === 0xfe) { selected = "utf-16le"; offset = 2; }
    else if (bytes[0] === 0xfe && bytes[1] === 0xff) { selected = "utf-16be"; offset = 2; }
    try { return new TextDecoder(selected, { fatal: true }).decode(bytes.subarray(offset)); }
    catch (_) { throw new Error(selected === "utf-8" ? "無法以 UTF-8 讀取字幕。若這是舊式繁體中文檔，請選 Big5；也可先另存為 UTF-8 再匯入。" : `無法以 ${selected === "big5" ? "Big5" : "UTF-16"} 讀取字幕，請確認編碼或另存為 UTF-8。`); }
  }
  const core = { MAX_FILE_BYTES, MAX_SUBTITLE_CUES, SUBTITLE_TRACK_PREFIX, DEFAULT_STYLE, TEXT_PRESETS, STYLE_FIELDS, parseSRT, stringifySRT, isSubtitleTrack, subtitleTracks, subtitleCount, createSubtitleLayers, applySubtitleStyle, decodeSubtitleBuffer };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorSubtitles = core;
})(typeof window !== "undefined" ? window : globalThis);
