/* Library visibility never removes a source used by a timeline or a draft. */
(function (root) {
  "use strict";
  const MEDIA_ID = /^[0-9a-f]{32}$/;
  const MEDIA_KINDS = new Set(["video", "audio", "image"]);
  const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const nonblank = value => typeof value === "string" && value.trim().length > 0;

  function isRemoved(media) {
    return record(media) && nonblank(media.library_removed_at);
  }
  function mediaItems(items) {
    if (!items || typeof items === "string" || typeof items[Symbol.iterator] !== "function") return [];
    return Array.from(items).filter(record);
  }
  function visibleMedia(items) {
    return mediaItems(items).filter(item => !isRemoved(item));
  }
  function removedMedia(items) {
    return mediaItems(items).filter(isRemoved).map((item, index) => {
      const timestamp = Date.parse(item.library_removed_at);
      return { item, index, timestamp: Number.isFinite(timestamp) ? timestamp : -Infinity };
    }).sort((left, right) => right.timestamp - left.timestamp || left.index - right.index).map(value => value.item);
  }
  function partitionMedia(items) {
    const all = mediaItems(items);
    return { visible: visibleMedia(all), removed: removedMedia(all) };
  }

  function validateVisibilityResponse(payload, expectedId, removed) {
    const invalid = () => new Error("Studio 回傳的素材狀態不完整；素材與剪輯內容已保留，請重新整理後再試。");
    if (typeof expectedId !== "string" || !MEDIA_ID.test(expectedId) || typeof removed !== "boolean" || !record(payload)) throw invalid();
    const item = Object.hasOwn(payload, "media") ? payload.media : payload;
    if (!record(item) || item.id !== expectedId || !nonblank(item.name) || (item.kind !== undefined && !MEDIA_KINDS.has(item.kind))) throw invalid();
    if (item.url !== undefined && item.url !== `/api/editor/media/${expectedId}/file`) throw invalid();
    if (item.library_removed_at !== undefined && item.library_removed_at !== null && typeof item.library_removed_at !== "string") throw invalid();
    if (isRemoved(item) !== removed || (item.library_removed !== undefined && (typeof item.library_removed !== "boolean" || item.library_removed !== removed))) throw invalid();
    return { ...item, library_removed: removed };
  }

  function mediaReferences(project, mediaId) {
    if (!record(project) || typeof mediaId !== "string" || !mediaId) return 0;
    const matches = items => Array.isArray(items) ? items.filter(item => record(item) && item.media_id === mediaId).length : 0;
    const sourceLayers = Array.isArray(project.overlays) ? project.overlays.filter(layer => record(layer) && MEDIA_KINDS.has(layer.kind) && layer.kind !== "audio") : [];
    return matches(project.clips) + matches(project.audio_clips) + matches(sourceLayers);
  }
  function filterMedia(items, project, { query = "", kind = "all", used = false } = {}) {
    const needle = String(query).normalize("NFKC").trim().toLocaleLowerCase();
    return visibleMedia(items).filter(item => (kind === "all" || (item.kind || "video") === kind)
      && (!needle || String(item.name || "").normalize("NFKC").toLocaleLowerCase().includes(needle))
      && (!used || mediaReferences(project, item.id) > 0));
  }

  const api = { isRemoved, visibleMedia, removedMedia, partitionMedia, validateVisibilityResponse, mediaReferences, filterMedia };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.H3EditorMediaLibrary = api;
})(typeof window !== "undefined" ? window : globalThis);
