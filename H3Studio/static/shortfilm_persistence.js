(function (root, factory) {
  "use strict";
  const exported = factory();
  if (typeof module === "object" && module.exports) module.exports = exported;
  else root.ShortFilmPersistence = exported;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const PREFIX = "h3studio-shortfilm-draft-v1:";
  const clone = value => JSON.parse(JSON.stringify(value));
  function comparable(project) {
    const copy = clone(project);
    delete copy.revision;
    delete copy.updated_at;
    return JSON.stringify(copy);
  }
  function create(options) {
    if (typeof options?.send !== "function") throw new TypeError("send(project) is required");
    const states = new Map();
    const storage = options.storage;
    const delay = options.delay ?? 650;
    const setTimer = options.setTimeout || setTimeout;
    const clearTimer = options.clearTimeout || clearTimeout;
    function notify(state) {
      options.onState?.(state.id, status(state.id));
    }
    function readDraft(id) {
      try {
        const value = JSON.parse(storage?.getItem(PREFIX + id) || "null");
        return value?.version === 1 && value.project?.id === id && Number.isInteger(value.baseRevision) ? value : null;
      } catch (_) { return null; }
    }
    function removeDraft(id) {
      try { storage?.removeItem(PREFIX + id); } catch (_) { /* A storage failure must not stop server saves. */ }
    }
    function writeDraft(state) {
      try {
        storage?.setItem(PREFIX + state.id, JSON.stringify({
          version: 1, baseRevision: state.revision, savedAt: Date.now(), project: state.latest,
        }));
        state.draftError = null;
      } catch (error) {
        state.draftError = "瀏覽器無法保留離頁草稿，請確認已儲存後再關閉。";
        options.onDraftError?.(error, state.id);
      }
    }
    function ensure(project) {
      if (!project?.id) throw new TypeError("A project id is required");
      let state = states.get(project.id);
      if (!state) {
        state = { id: project.id, latest: clone(project), revision: project.revision || 0,
          version: 0, savedVersion: 0, timer: null, pending: null, error: null, blocked: false, draftError: null };
        states.set(project.id, state);
      }
      return state;
    }
    function capture(project) {
      const state = ensure(project);
      // An accepted server operation (append segments or batch status) may have
      // replaced a clean project outside this queue. Adopt that newer revision.
      if (!state.pending && !state.blocked && state.version === state.savedVersion
          && Number.isInteger(project.revision) && project.revision > state.revision) {
        state.revision = project.revision;
      }
      state.latest = clone(project);
      // Server responses own the revision; an older object reference never resets it.
      state.latest.revision = state.revision;
      state.version += 1;
      state.error = null;
      writeDraft(state);
      notify(state);
      return state;
    }
    function status(id) {
      const state = states.get(id);
      if (!state) return { state: "saved", error: null, draftError: null };
      return { state: state.blocked ? "conflict" : state.pending ? "saving" : state.error ? "error"
        : state.version > state.savedVersion ? "dirty" : "saved", error: state.error, draftError: state.draftError };
    }
    function conflictError(state) {
      const error = new Error("此短片已有較新版本。瀏覽器草稿已保留，請確認差異後再儲存。");
      error.status = 409;
      error.code = "shortfilm_revision_conflict";
      state.error = error;
      return error;
    }
    async function drain(state) {
      let saved = null;
      while (state.savedVersion < state.version) {
        if (state.blocked) throw conflictError(state);
        const version = state.version;
        const snapshot = clone(state.latest);
        snapshot.revision = state.revision;
        try {
          saved = await options.send(snapshot);
          if (!saved || saved.id !== state.id || !Number.isInteger(saved.revision)) {
            throw new Error("伺服器未回傳有效的短片儲存版本，瀏覽器草稿已保留。");
          }
        } catch (error) {
          state.error = error;
          state.blocked = error.status === 409 || error.code === "shortfilm_revision_conflict";
          writeDraft(state);
          options.onError?.(error, state.id);
          throw error;
        }
        const current = options.getProject?.(state.id);
        // Capture edits made while saving, even if the caller has not scheduled them yet.
        if (current && state.version === version && comparable(current) !== comparable(snapshot)) capture(current);
        state.revision = saved.revision;
        state.latest.revision = saved.revision;
        if (current) current.revision = saved.revision;
        state.savedVersion = version;
        state.error = null;
        const hasPending = state.version > version;
        if (hasPending) writeDraft(state);
        else {
          state.latest = clone(saved);
          removeDraft(state.id);
        }
        options.onSaved?.(saved, { projectId: state.id, snapshot, current, hasPending });
        notify(state);
      }
      return saved || clone(state.latest);
    }
    function start(state) {
      clearTimer(state.timer);
      state.timer = null;
      if (state.pending) return state.pending;
      if (state.blocked) return Promise.reject(conflictError(state));
      // Defer drain one microtask so synchronous observers see the saving state.
      state.pending = Promise.resolve().then(() => drain(state)).finally(() => {
        state.pending = null;
        notify(state);
      });
      notify(state);
      return state.pending;
    }
    function schedule(project) {
      const state = capture(project);
      clearTimer(state.timer);
      if (!state.blocked) state.timer = setTimer(() => {
        state.timer = null;
        start(state).catch(() => {}); // onError handles background save failures.
      }, delay);
      return status(state.id);
    }
    function save(project) {
      if (!project) return Promise.resolve(null);
      return start(capture(project));
    }
    function restore(serverProject) {
      const draft = readDraft(serverProject.id);
      if (!draft) {
        const state = ensure(serverProject);
        if (state.version === state.savedVersion && !state.pending && !state.blocked) {
          state.revision = serverProject.revision || 0;
          state.latest = clone(serverProject);
        }
        return { project: serverProject, restored: false, conflict: false, draft: null };
      }
      const state = ensure(serverProject);
      if (draft.baseRevision !== (serverProject.revision || 0)) {
        state.latest = clone(draft.project);
        state.revision = draft.baseRevision;
        state.version += 1;
        state.blocked = true;
        conflictError(state);
        notify(state);
        return { project: clone(draft.project), serverProject, restored: false, conflict: true, draft: clone(draft.project) };
      }
      const recovered = clone(draft.project);
      recovered.revision = serverProject.revision || 0;
      capture(recovered);
      return { project: recovered, restored: true, conflict: false, draft: clone(recovered) };
    }
    function rebase(project, serverRevision) {
      if (!Number.isInteger(serverRevision) || serverRevision < 0) throw new TypeError("A server revision is required");
      const state = ensure(project);
      if (state.pending) throw new Error("請等待目前儲存完成後再套用草稿。");
      state.revision = serverRevision;
      state.blocked = false;
      project.revision = serverRevision;
      return capture(project);
    }
    function draft(id) {
      const state = states.get(id);
      if (state && (state.blocked || state.version > state.savedVersion)) return clone(state.latest);
      return null;
    }
    function forget(id) {
      const state = states.get(id);
      if (state) clearTimer(state.timer);
      states.delete(id);
      removeDraft(id);
    }
    function flushAll() {
      return Promise.allSettled([...states.values()].filter(state => state.version > state.savedVersion).map(start));
    }
    return { schedule, save, restore, rebase, draft, forget, flushAll, status };
  }
  return { create, PREFIX };
});
