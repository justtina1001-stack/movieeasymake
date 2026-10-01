/* Track moves use existing project data; the editor supplies its shared core. */
(function (root) {
  "use strict";
  function createTrackDragCore(core) {
    const { clone, duration, speedOf, speedCurve, canonicalSpeedCurve, nonzeroFades,
      canonicalOverlay, canonicalAnimations, normalizeProjectAnimations, validateProject, signature, overlays } = core;
    const epsilon = 1e-9;
    const overlayTrackId = layer => layer.track_id || layer.id;
    // Projects created before shared tracks have one implicit track per layer.
    // First appearance retains their existing bottom-to-top stacking order.
    function overlayTrackGroups(project) {
      const groups = [], byId = new Map();
      for (const layer of overlays(project)) {
        const id = overlayTrackId(layer);
        if (!byId.has(id)) { const group = { id, clips: [] }; byId.set(id, group); groups.push(group); }
        byId.get(id).clips.push(layer);
      }
      return groups;
    }
    function flattenOverlayTracks(groups) {
      return groups.flatMap(group => [...group.clips].sort((a, b) => a.start - b.start));
    }
    function normalizeOverlayTracks(project) {
      project.overlays = flattenOverlayTracks(overlayTrackGroups(project));
      return project;
    }
    function moveOverlayTrack(project, id, delta) {
      if (!Number.isInteger(delta)) throw new Error("圖層移動層級無效。");
      const layer = overlays(project).find(item => item.id === id), trackId = layer ? overlayTrackId(layer) : id;
      const groups = overlayTrackGroups(project), index = groups.findIndex(group => group.id === trackId), target = index + delta;
      if (index < 0 || target < 0 || target >= groups.length || !delta) return false;
      const [group] = groups.splice(index, 1); groups.splice(target, 0, group);
      project.overlays = flattenOverlayTracks(groups);
      return true;
    }
    function uniqueTrackId(item, groups) {
      const used = new Set(groups.map(group => group.id));
      if (!used.has(item.id)) return item.id;
      for (let serial = 1; ; serial++) {
        const suffix = serial === 1 ? "_track" : `_track${serial}`;
        const id = item.id.slice(0, 64 - suffix.length) + suffix;
        if (!used.has(id)) return id;
      }
    }
    function mainClipStart(project, id) {
      let start = 0;
      for (const clip of project.clips) {
        if (clip.id === id) return start;
        start += duration(clip);
      }
      throw new Error("找不到主影片片段。");
    }
    // V1 remains a continuous sequence. A drop between boundaries snaps to the
    // closest boundary; an exact tie uses the earlier boundary consistently.
    function mainBoundaryAt(project, time, excludeId = null) {
      if (!Number.isFinite(time)) throw new Error("影片插入時間無效。");
      const clips = project.clips.filter(clip => clip.id !== excludeId);
      let boundary = 0, best = { index: 0, time: 0 }, distance = Math.abs(time);
      clips.forEach((clip, index) => {
        boundary += duration(clip);
        const candidate = Math.abs(boundary - time);
        if (candidate < distance - epsilon) {
          distance = candidate;
          best = { index: index + 1, time: boundary };
        }
      });
      return best;
    }
    function insertionIndex(value, length) {
      if (!Number.isInteger(value) || value < 0 || value > length) throw new Error("影片插入層級無效，請重新拖曳。");
      return value;
    }
    function fittedWidth(project, item, media) {
      const source = media?.get(item.media_id);
      return source && Number.isFinite(source.width) && source.width > 0 && Number.isFinite(source.height) && source.height > 0
        ? Math.min(1, project.height * source.width / (project.width * source.height)) : 1;
    }
    function videoContent(item) {
      return { id: item.id, media_id: item.media_id, in: item.in, out: item.out,
        volume: item.volume, speed: speedOf(item),
        ...(speedCurve(item).length ? { speed_curve: canonicalSpeedCurve(item) } : {}),
        ...nonzeroFades(item), ...canonicalAnimations(item) };
    }
    function transformTrackDrag(project, from, target, media = null) {
      if (!from || !target || !["video", "overlay"].includes(from.kind) || !["video", "overlay"].includes(target.kind)) throw new Error("請將片段拖到影片軌或圖層軌。");
      validateProject(project, media);
      const next = clone(project), sourceItems = from.kind === "video" ? next.clips : overlays(next);
      const sourceIndex = sourceItems.findIndex(item => item.id === from.id), item = sourceItems[sourceIndex];
      if (!item) throw new Error("找不到拖曳的片段，請重新選取。");
      if (target.kind === "video" && from.kind === "overlay" && item.kind !== "video") throw new Error("主影片軌只接受影片；圖片與文字請拖到圖層軌。");
      const originalStart = from.kind === "video" ? mainClipStart(project, from.id) : item.start;
      const originalGroups = overlayTrackGroups(next), sourceGroup = from.kind === "overlay" ? originalGroups.find(group => group.id === overlayTrackId(item)) : null;
      sourceItems.splice(sourceIndex, 1);
      let moved, start, layoutReset = false;
      if (target.kind === "video") {
        const index = insertionIndex(target.index, next.clips.length);
        if (from.kind === "overlay") {
          const width = fittedWidth(project, item, media);
          layoutReset = Math.abs(item.x - 0.5) > epsilon || Math.abs(item.y - 0.5) > epsilon || Math.abs(item.width - width) > epsilon || Math.abs(item.rotation) > epsilon || Math.abs(item.opacity - 1) > epsilon;
        }
        moved = videoContent(item);
        next.clips.splice(index, 0, moved);
        start = mainClipStart(next, moved.id);
      } else {
        const groups = overlayTrackGroups(next);
        start = Object.hasOwn(target, "start") ? target.start : originalStart;
        if (!Number.isFinite(start)) throw new Error("圖層起始時間無效。");
        moved = from.kind === "video"
          ? { ...videoContent(item), kind: "video", start, end: start + duration(item),
            x: 0.5, y: 0.5, width: fittedWidth(project, item, media), rotation: 0, opacity: 1 }
          : { ...canonicalOverlay(item), start, end: start + item.end - item.start };
        if (target.mode === "join") {
          if (typeof target.trackId !== "string" || !target.trackId) throw new Error("找不到目標影片軌，請重新拖曳。");
          let group = groups.find(candidate => candidate.id === target.trackId);
          if (!group && sourceGroup?.id === target.trackId && sourceGroup.clips.length === 1) {
            // Repositioning the only clip within its own row retains the implicit
            // legacy track ID, so releasing without a move is still a no-op.
            group = { id: sourceGroup.id, clips: [] };
            groups.splice(originalGroups.indexOf(sourceGroup), 0, group);
            if (Object.hasOwn(item, "track_id")) moved.track_id = item.track_id;
            else delete moved.track_id;
          } else {
            if (!group) throw new Error("找不到目標影片軌，請重新拖曳。");
            for (const clip of group.clips) clip.track_id = group.id;
            moved.track_id = group.id;
          }
          group.clips.push(moved);
        } else {
          if (target.mode !== undefined && target.mode !== "insert") throw new Error("影片插入層級無效，請重新拖曳。");
          const index = insertionIndex(target.index, groups.length);
          const id = sourceGroup?.clips.length === 1 ? sourceGroup.id : uniqueTrackId(moved, groups);
          if (id !== moved.id || Object.hasOwn(item, "track_id") && sourceGroup?.clips.length === 1) moved.track_id = id;
          else delete moved.track_id;
          groups.splice(index, 0, { id, clips: [moved] });
        }
        next.overlays = flattenOverlayTracks(groups);
      }
      if (target.kind === "video" && from.kind === "overlay") normalizeOverlayTracks(next);
      normalizeProjectAnimations(next, project); validateProject(next, media);
      return { project: next, changed: signature(next) !== signature(project), selectedKind: target.kind,
        selectedId: moved.id, start, end: start + (target.kind === "video" ? duration(moved) : moved.end - moved.start), layoutReset };
    }
    class TrackDragTransaction {
      constructor(session, from, media = null) {
        this.session = session; this.from = { ...from }; this.media = media;
        this.original = clone(session.project); this.originalSignature = signature(session.project);
        this.preview = clone(this.original); this.result = null; this.closed = false;
      }
      update(target) {
        if (this.closed) throw new Error("這次拖曳操作已結束。");
        try {
          this.result = transformTrackDrag(this.original, this.from, target, this.media);
          this.preview = this.result.project;
          return this.result;
        } catch (error) {
          // Rejecting a later target must not leave an earlier target armed.
          this.invalidate();
          throw error;
        }
      }
      invalidate() {
        if (this.closed) throw new Error("這次拖曳操作已結束。");
        this.result = null; this.preview = clone(this.original);
      }
      commit() {
        if (this.closed) return false;
        this.closed = true;
        if (signature(this.session.project) !== this.originalSignature) throw new Error("專案內容已變更，本次拖曳已取消，請重新拖曳。");
        if (!this.result?.changed) return false;
        return this.session.change(value => {
          value.clips = clone(this.preview.clips);
          value.overlays = clone(overlays(this.preview));
        });
      }
      cancel() { this.closed = true; this.result = null; this.preview = clone(this.original); return false; }
    }
    return { overlayTrackId, overlayTrackGroups, flattenOverlayTracks, normalizeOverlayTracks, moveOverlayTrack,
      mainClipStart, mainBoundaryAt, transformTrackDrag, TrackDragTransaction };
  }
  const api = { createTrackDragCore };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.H3EditorTrackDrag = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
