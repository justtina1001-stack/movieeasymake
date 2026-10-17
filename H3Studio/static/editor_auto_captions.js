/* A caption result belongs to the audible timeline that was transcribed. */
(function (root) {
  "use strict";
  const node = typeof module !== "undefined" && module.exports;
  const speed = node ? require("./editor_speed.js") : root.H3EditorSpeed;
  const subtitles = node ? require("./editor_subtitles.js") : root.H3EditorSubtitles;
  const ACTIVE = Object.freeze(["queued", "running"]);
  function audioSignature(project) {
    const audio = clip => [clip.media_id, clip.in, clip.out, clip.speed ?? 1, speed.canonicalSpeedCurve(clip), clip.volume ?? 1, clip.fade_in ?? 0, clip.fade_out ?? 0];
    return JSON.stringify([project.fps, project.canvas_duration || 0, (project.clips || []).map(audio),
      (project.audio_clips || []).map(clip => [clip.start, clip.track, ...audio(clip)]),
      (project.overlays || []).filter(layer => layer.kind === "video").map(layer => [layer.start, layer.end, ...audio(layer)])]);
  }
  function resultMatches(job, project) {
    return Boolean(job?.status === "completed" && project && job.project_id === project.id && job.source_audio_snapshot && audioSignature(job.source_audio_snapshot) === audioSignature(project));
  }
  function captionLayers(job, project, options) {
    if (!resultMatches(job, project)) throw new Error("辨識後影片或音訊時間軸已更動，請重新產生字幕以避免時間錯位。原辨識結果仍可下載為 SRT。");
    if (jobAlreadyApplied(job, project)) throw new Error("此辨識結果已加入字幕軌，請編輯現有字幕。");
    let first = true;
    const makeId = () => { if (first) { first = false; return job.id; } return options.makeId(); };
    return subtitles.createSubtitleLayers(job.cues, { ...options, makeId, fps: project.fps, existingCount: subtitles.subtitleCount(project) });
  }
  function jobAlreadyApplied(job, project) {
    return Boolean(job && (project?.overlays || []).some(layer => layer.kind === "text" && layer.track_id === subtitles.SUBTITLE_TRACK_PREFIX + job.id));
  }
  function validateJob(payload) {
    const job = payload?.job || payload;
    if (!job || typeof job.id !== "string" || !/^[a-f0-9]{32}$/.test(job.id) || typeof job.project_id !== "string" || !/^[a-f0-9]{32}$/.test(job.project_id) || ![...ACTIVE, "completed", "failed", "cancelled"].includes(job.status)) throw new Error("主機回傳的字幕工作格式無效，請重新檢查字幕功能。");
    return job;
  }
  function jobLabel(job) {
    if (!job) return "辨識剪輯後的影片原聲與音軌，完成後可加入為新的字幕軌。首次使用需下載約 486 MB 模型；音訊在本機處理。";
    if (job.status === "completed") return `辨識完成 · ${job.cues?.length || 0} 句。加入前會檢查時間軸是否仍一致。`;
    if (job.status === "failed") return job.error || "字幕辨識未完成，請重試。";
    if (job.status === "cancelled") return "已取消字幕辨識。";
    if (job.cancel_requested) return "正在取消字幕辨識，等待目前的音訊工作結束後清理暫存檔…";
    const stages = { runtime: "準備辨識引擎", model: "準備／下載辨識模型", audio: "準備剪輯後的聲音", transcribing: "辨識語音", queued: "等待開始" };
    const percent = Number.isFinite(job.progress) ? Math.max(0, Math.min(99, Math.round(job.progress))) : 0;
    return `${stages[job.stage] || "正在準備字幕"}${job.stage === "transcribing" ? ` · ${percent}%` : "…"}。可繼續編輯文字樣式；更動影片或聲音後需重新辨識。`;
  }
  const core = { ACTIVE, audioSignature, resultMatches, captionLayers, jobAlreadyApplied, validateJob, jobLabel };
  if (node) module.exports = core;
  root.H3EditorAutoCaptions = core;
})(typeof window !== "undefined" ? window : globalThis);
