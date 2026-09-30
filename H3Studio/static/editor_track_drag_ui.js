/* Track centers share a row; narrow edge strips insert another layer. */
(function (root) {
  "use strict";
  const NEW_TRACK_HEIGHT = 32;
  const INSERT_STRIP_HEIGHT = 8;
  const contains = (rect, point) => Boolean(rect && point.clientX >= rect.left && point.clientX <= rect.right && point.clientY >= rect.top && point.clientY <= rect.bottom);
  const verticalContains = (rect, y) => Boolean(rect && y >= rect.top && y <= rect.bottom);
  const invalid = reason => ({ valid: false, target: null, line: null, label: reason, reason });
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const layers = project => project.overlays || [];
  const trackIdOf = layer => layer.track_id || layer.id;
  function tracksOf(overlays) {
    const groups = [];
    for (const layer of overlays) {
      const id = trackIdOf(layer), existing = groups.find(group => group.id === id);
      if (existing) existing.layers.push(layer); else groups.push({ id, layers: [layer] });
    }
    return groups;
  }
  function normalizedBoundary(index, sourceIndex, sameKind) {
    return index - (sameKind && sourceIndex >= 0 && sourceIndex < index ? 1 : 0);
  }
  function layerLabel(sourceIndex, targetIndex, fromKind) {
    if (fromKind === "video") return "移至上層影片";
    if (targetIndex > sourceIndex) return "上移圖層";
    if (targetIndex < sourceIndex) return "下移圖層";
    return "調整片段位置";
  }
  function resolveGeometry(geometry, point, from, project) {
    if (!point || !Number.isFinite(point.clientX) || !Number.isFinite(point.clientY)) return invalid("無效的拖曳位置");
    if (!geometry || !contains(geometry.viewport, point) || !contains(geometry.canvas, point)) return invalid("請拖至影片軌道內");
    if (!project || !from || !["video", "overlay"].includes(from.kind)) return invalid("此片段無法移至影片層");
    const videos = project.clips || [], overlays = layers(project), tracks = tracksOf(overlays);
    const sourceIndex = (from.kind === "video" ? videos : overlays).findIndex(item => item.id === from.id);
    if (sourceIndex < 0) return invalid("找不到拖曳中的片段");
    const source = (from.kind === "video" ? videos : overlays)[sourceIndex];
    const sourceTrackIndex = from.kind === "overlay" ? tracks.findIndex(track => track.id === trackIdOf(source)) : -1;
    const removeSourceTrack = sourceTrackIndex >= 0 && tracks[sourceTrackIndex].layers.length === 1;
    const fullWidth = geometry.canvas.right - geometry.canvas.left;
    if (verticalContains(geometry.newRow, point.clientY)) {
      const index = tracks.length - (removeSourceTrack ? 1 : 0);
      return { valid: true, target: { kind: "overlay", index, rowId: "new" }, label: "新增上層軌道",
        line: { orientation: "horizontal", x: geometry.canvas.left, y: geometry.newRow.bottom, width: fullWidth, height: 0 } };
    }
    const row = (geometry.overlayRows || []).find(item => verticalContains(item.rect, point.clientY));
    if (row) {
      const representedLayer = overlays.find(item => item.id === row.layerId || item.id === row.id);
      const trackId = row.trackId || (representedLayer && trackIdOf(representedLayer)) || row.id;
      const rowIndex = tracks.findIndex(track => track.id === trackId);
      if (rowIndex < 0) return invalid("此圖層已不存在");
      const strip = Math.min(INSERT_STRIP_HEIGHT, Math.max(0, (row.rect.bottom - row.rect.top) / 3));
      const above = point.clientY < row.rect.top + strip, below = point.clientY > row.rect.bottom - strip;
      if (!above && !below) {
        return { valid: true, target: { kind: "overlay", mode: "join", trackId, rowId: trackId }, label: "放入此軌",
          line: null, region: { x: geometry.canvas.left, y: row.rect.top, width: fullWidth, height: row.rect.bottom - row.rect.top } };
      }
      const originalBoundary = rowIndex + (above ? 1 : 0);
      const index = normalizedBoundary(originalBoundary, sourceTrackIndex, removeSourceTrack);
      return { valid: true, target: { kind: "overlay", index, rowId: trackId }, label: layerLabel(sourceTrackIndex, index, from.kind),
        line: { orientation: "horizontal", x: geometry.canvas.left, y: above ? row.rect.top : row.rect.bottom, width: fullWidth, height: 0 } };
    }
    if (verticalContains(geometry.mainRow, point.clientY)) {
      if (from.kind === "overlay" && source.kind !== "video") return invalid("文字與圖片請放在上層圖軌");
      const clips = geometry.mainClips || [];
      const ownClip = from.kind === "video" && clips.find(item => item.id === from.id && point.clientX >= item.rect.left && point.clientX <= item.rect.right);
      let originalBoundary = videos.length, lineX = clips.length ? clips.at(-1).rect.right : geometry.mainRow.left;
      if (ownClip) {
        originalBoundary = sourceIndex; lineX = ownClip.rect.left;
      } else {
        for (const item of clips) {
          if (point.clientX < (item.rect.left + item.rect.right) / 2) {
            originalBoundary = videos.findIndex(clip => clip.id === item.id); lineX = item.rect.left; break;
          }
        }
      }
      const index = normalizedBoundary(originalBoundary, sourceIndex, from.kind === "video");
      return { valid: true, target: { kind: "video", index }, label: from.kind === "overlay" ? "移至主軌 · 自動拼接" : index === sourceIndex ? "維持主軌位置" : "主軌排序 · 自動拼接",
        line: { orientation: "vertical", x: lineX, y: geometry.mainRow.top + 4, width: 0, height: Math.max(0, geometry.mainRow.bottom - geometry.mainRow.top - 8) } };
    }
    return invalid("影片不能放入音訊軌道");
  }
  function rectOf(node) { return node ? node.getBoundingClientRect() : null; }
  function viewportRect(scroll, canvas) {
    if (!scroll) return rectOf(canvas);
    const rect = rectOf(scroll), left = rect.left + (scroll.clientLeft || 0), top = rect.top + (scroll.clientTop || 0);
    return { left, top, right: left + (scroll.clientWidth || rect.right - left), bottom: top + (scroll.clientHeight || rect.bottom - top) };
  }
  function geometryOf(canvas) {
    const scroll = canvas.closest(".timeline-scroll");
    const mainRow = canvas.querySelector("#clipTrack");
    return { canvas: rectOf(canvas), viewport: viewportRect(scroll, canvas), newRow: rectOf(canvas.querySelector("[data-track-drop='new']")), mainRow: rectOf(mainRow),
      overlayRows: [...canvas.querySelectorAll(".overlay-track")].filter(node => node.dataset.trackDrop !== "new").map(node => ({ id: node.dataset.trackId || node.dataset.layerId || node.querySelector("[data-overlay-id]")?.dataset.overlayId,
        trackId: node.dataset.trackId, layerId: node.dataset.layerId, rect: rectOf(node) })),
      mainClips: [...(mainRow?.querySelectorAll("[data-clip-id]") || [])].map(node => ({ id: node.dataset.clipId, rect: rectOf(node) })) };
  }
  function resolveDrop(canvas, point, from, project) { return resolveGeometry(geometryOf(canvas), point, from, project); }
  function scrollDelta(position, low, high, edge = 30) {
    if (position < low || position > high) return 0;
    if (position < low + edge) return -Math.ceil(16 * (1 - (position - low) / edge));
    if (position > high - edge) return Math.ceil(16 * (1 - (high - position) / edge));
    return 0;
  }
  function mountTrackDrag(canvas) {
    if (!canvas || !canvas.ownerDocument) throw new Error("找不到剪輯時間軸");
    const document = canvas.ownerDocument, scroll = canvas.closest(".timeline-scroll");
    const line = document.createElement("div"), region = document.createElement("div"), label = document.createElement("div"), ghost = document.createElement("div");
    line.className = "track-drop-indicator hidden"; line.setAttribute("aria-hidden", "true");
    region.className = "track-drop-region hidden"; region.setAttribute("aria-hidden", "true");
    label.className = "track-drop-label hidden"; label.setAttribute("role", "status"); label.setAttribute("aria-live", "polite");
    ghost.className = "track-drag-ghost hidden"; ghost.setAttribute("aria-hidden", "true");
    const name = document.createElement("strong"), timing = document.createElement("small"); ghost.append(name, timing);
    canvas.append(region, line, label, ghost);
    let activeSource = null, lastLabel = "", destroyed = false;
    function show(feedback, options = {}) {
      if (destroyed) return;
      const rect = rectOf(canvas), viewport = viewportRect(scroll, canvas), pointer = options.pointer || { clientX: rect.left + 12, clientY: rect.top + 40 };
      const valid = Boolean(feedback?.valid), reason = options.reason || feedback?.reason || feedback?.label || "請拖至影片軌道";
      if (lastLabel !== reason) { label.textContent = reason; lastLabel = reason; }
      label.classList.remove("hidden"); ghost.classList.remove("hidden");
      for (const node of [line, region, label, ghost]) { node.classList.toggle("invalid", !valid); node.classList.toggle("valid", valid); }
      const start = Number.isFinite(options.start) ? options.start : 0, length = Number.isFinite(options.duration) ? options.duration : 0;
      const zoom = Number.isFinite(options.zoom) && options.zoom > 0 ? options.zoom : 60;
      name.textContent = options.name || "影片片段";
      timing.textContent = `${start.toFixed(2)} 秒 · ${length.toFixed(2)} 秒`;
      ghost.style.left = `${Math.max(0, start * zoom)}px`;
      ghost.style.top = `${Math.max(0, feedback?.region ? feedback.region.y - rect.top + 3 : pointer.clientY - rect.top - 14)}px`;
      ghost.style.height = feedback?.region ? `${Math.max(18, feedback.region.height - 6)}px` : "44px";
      ghost.classList.toggle("join-track", Boolean(feedback?.region));
      ghost.style.width = `${clamp(length * zoom, 72, 520)}px`;
      if (feedback?.region) {
        const place = feedback.region;
        region.classList.remove("hidden"); region.style.left = `${place.x - rect.left}px`; region.style.top = `${place.y - rect.top}px`;
        region.style.width = `${place.width}px`; region.style.height = `${place.height}px`;
      } else region.classList.add("hidden");
      if (valid && feedback.line) {
        const place = feedback.line;
        line.classList.remove("hidden"); line.classList.toggle("vertical", place.orientation === "vertical");
        line.style.left = `${place.x - rect.left}px`; line.style.top = `${place.y - rect.top}px`;
        line.style.width = `${place.width}px`; line.style.height = `${place.height}px`;
        label.style.top = `${Math.max(0, place.y - rect.top + (place.orientation === "vertical" ? -25 : 4))}px`;
      } else { line.classList.add("hidden"); label.style.top = `${Math.max(0, feedback?.region ? feedback.region.y - rect.top + feedback.region.height + 4 : pointer.clientY - rect.top + 20)}px`; }
      label.style.left = `${clamp(pointer.clientX - rect.left + 12, viewport.left - rect.left + 8, Math.max(viewport.left - rect.left + 8, viewport.right - rect.left - 240))}px`;
      const from = options.from;
      const source = from ? [...canvas.querySelectorAll(from.kind === "video" ? "[data-clip-id]" : "[data-overlay-id]")].find(node => (from.kind === "video" ? node.dataset.clipId : node.dataset.overlayId) === from.id) : null;
      if (activeSource !== source) { activeSource?.classList.remove("track-drag-source"); source?.classList.add("track-drag-source"); activeSource = source; }
      canvas.classList.add("track-drop-active");
    }
    function hide() { for (const node of [line, region, label, ghost]) node.classList.add("hidden"); activeSource?.classList.remove("track-drag-source"); activeSource = null; canvas.classList.remove("track-drop-active"); lastLabel = ""; }
    function autoScroll(point, options = {}) {
      if (!scroll || !point || destroyed) return false;
      const rect = viewportRect(scroll, canvas);
      if (!contains(rect, point)) return false;
      const beforeX = scroll.scrollLeft, beforeY = scroll.scrollTop;
      if (options.horizontal !== false) scroll.scrollLeft += scrollDelta(point.clientX, rect.left, rect.right);
      if (options.vertical !== false) scroll.scrollTop += scrollDelta(point.clientY, rect.top, rect.bottom);
      return beforeX !== scroll.scrollLeft || beforeY !== scroll.scrollTop;
    }
    return { resolve: (point, from, project) => resolveDrop(canvas, point, from, project), show, hide, autoScroll,
      destroy() { hide(); destroyed = true; line.remove(); region.remove(); label.remove(); ghost.remove(); } };
  }
  const api = { NEW_TRACK_HEIGHT, INSERT_STRIP_HEIGHT, normalizedBoundary, resolveGeometry, geometryOf, resolveDrop, scrollDelta, mountTrackDrag };
  if (typeof module === "object" && module.exports) module.exports = api;
  root.H3EditorTrackDragUI = api;
})(typeof window === "undefined" ? globalThis : window);
