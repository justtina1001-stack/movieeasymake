(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.H3AudioWorkspace = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const fields = {
    music: {
      job_name: "musicJobName", use_case: "musicUseCase", genre: "musicGenre", mood: "musicMood",
      bpm: "musicBpm", key: "musicKey", duration: "musicDuration", instruments: "musicInstruments",
      structure: "musicStructure", vocals: "musicVocals", lyrics: "musicLyrics", production: "musicProduction",
      avoid: "musicAvoid", details: "musicDetails", seed: "musicSeed", format: "musicFormat", tiled_decode: "musicTiledDecode",
    },
    voice: {
      job_name: "voiceJobName", text: "voiceText", language: "voiceLanguage", speaker: "voiceSpeaker",
      seed: "voiceSeed", reference_text: "voiceReferenceText", x_vector_only: "voiceXVectorOnly", instruct: "voiceInstruct",
    },
  };
  const jobs = { music: new Map(), voice: new Map() };
  const snapshots = new WeakMap();
  const validAssetId = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const scalar = value => typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value));

  function cleanDraft(kind, value) {
    if (!fields[kind] || !value || typeof value !== "object") return null;
    const result = { version: 1, mode: kind === "music" ? "instrumental" : "custom", fields: {} };
    const modes = kind === "music" ? ["instrumental", "song"] : ["custom", "design", "clone"];
    if (modes.includes(value.mode)) result.mode = value.mode;
    for (const key of Object.keys(fields[kind])) {
      if (scalar(value.fields?.[key])) result.fields[key] = value.fields[key];
    }
    if (kind === "voice") {
      result.modelSelection = { custom: "custom_1_7b", design: "design", clone: "clone_1_7b" };
      result.instructDrafts = {};
      for (const mode of ["custom", "design", "clone"]) {
        if ((mode === "design" ? ["design"] : [mode, `${mode}_1_7b`]).includes(value.modelSelection?.[mode])) result.modelSelection[mode] = value.modelSelection[mode];
        if (typeof value.instructDrafts?.[mode] === "string") result.instructDrafts[mode] = value.instructDrafts[mode];
      }
      const reference = value.referenceAsset;
      result.referenceAsset = validAssetId(reference?.id)
        ? { id: reference.id, name: typeof reference.name === "string" ? reference.name : "參考音訊", url: `/api/assets/${reference.id}` }
        : null;
    }
    return result;
  }

  function createDraftStore(storage) {
    const key = kind => `h3studio-${kind}-draft-v1`;
    return {
      load(kind) {
        try { return cleanDraft(kind, JSON.parse(storage.getItem(key(kind)) || "null")); }
        catch { return null; }
      },
      save(kind, value) {
        try {
          const draft = cleanDraft(kind, value);
          if (!draft) return false;
          storage.setItem(key(kind), JSON.stringify(draft));
          return true;
        } catch { return false; }
      },
      clear(kind) { try { storage.removeItem(key(kind)); return true; } catch { return false; } },
    };
  }

  function rememberJobs(kind, records) {
    if (!jobs[kind]) return;
    jobs[kind] = new Map((records || []).map(job => [String(job.id), job]));
  }

  function draftFromJob(kind, job) {
    if (!job || !fields[kind]) return null;
    if (kind === "music" && (!job.settings || typeof job.settings !== "object")) return null;
    const source = kind === "music" ? job.settings : job;
    const draft = { mode: job.mode, fields: {} };
    for (const key of Object.keys(fields[kind])) {
      const value = key === "job_name" ? job.name : source[key];
      if (scalar(value)) draft.fields[key] = value;
    }
    if (kind === "voice") {
      draft.modelSelection = { [job.mode]: job.model_id || job.mode };
      draft.instructDrafts = { custom: "", design: "", clone: "", [job.mode]: job.instruct || "" };
      draft.referenceAsset = validAssetId(job.reference_asset_id) ? { id: job.reference_asset_id, name: "歷史參考音訊" } : null;
    }
    return cleanDraft(kind, draft);
  }

  function childKey(node, index) {
    if (node.tagName === "AUDIO") return `audio:${node.getAttribute("src") || ""}`;
    if (node.id) return `id:${node.id}`;
    for (const attr of ["data-voice-recipe-panel", "data-music-caption-panel", "data-queue-progress-panel"]) {
      if (node.hasAttribute(attr)) return `${attr}:${node.getAttribute(attr)}`;
    }
    return `${node.tagName}:${node.classList?.[0] || index}`;
  }

  function updateCard(existing, incoming) {
    if (snapshots.get(existing) === incoming.outerHTML) return existing;
    snapshots.set(existing, incoming.outerHTML);
    existing.className = incoming.className;
    const before = new Map(Array.from(existing.children).map((node, index) => [childKey(node, index), node]));
    const desired = Array.from(incoming.children);
    const keys = new Set(desired.map(childKey));
    for (const [key, node] of before) if (!keys.has(key)) node.remove();
    desired.forEach((fresh, index) => {
      const key = childKey(fresh, index);
      const old = before.get(key);
      if (old?.tagName === "AUDIO") return; // Never detach, reload or reset a live player.
      let node = fresh;
      if (old) {
        if (old.tagName === "PRE" && old.classList.contains("job-recipe")) {
          if (old.textContent !== fresh.textContent) old.textContent = fresh.textContent;
          node = old; // The user's expanded/collapsed state belongs to this card.
        } else if (old.tagName === "DETAILS") {
          const open = old.open;
          if (old.innerHTML !== fresh.innerHTML) old.innerHTML = fresh.innerHTML;
          old.open = open;
          node = old;
        } else {
          if (old.outerHTML !== fresh.outerHTML) old.replaceWith(fresh);
          else node = old;
        }
      }
      const anchor = existing.children[index] || null;
      if (node !== anchor) existing.insertBefore(node, anchor);
    });
    return existing;
  }

  function reconcileHistory(container, incoming, kind) {
    const attr = `data-${kind}-job`;
    const current = new Map(Array.from(container.children).filter(node => node.hasAttribute(attr)).map(node => [node.getAttribute(attr), node]));
    const wanted = new Set(incoming.filter(node => node.hasAttribute(attr)).map(node => node.getAttribute(attr)));
    for (const child of Array.from(container.children)) {
      if (!child.hasAttribute(attr) || !wanted.has(child.getAttribute(attr))) child.remove();
    }
    incoming.forEach((fresh, index) => {
      const existing = current.get(fresh.getAttribute(attr));
      const card = existing ? updateCard(existing, fresh) : fresh;
      if (!existing) { snapshots.set(card, fresh.outerHTML); container.appendChild(card); }
      // moveBefore preserves browser media/focus state. Grid order is the fallback
      // for older browsers, where insertBefore would detach a playing card.
      if (typeof container.moveBefore === "function" && container.children[index] !== card) {
        try { container.moveBefore(card, container.children[index] || null); } catch { /* retain the connected card */ }
      }
      card.style.order = String(index);
    });
  }

  function patchHistory(container, markup, kind) {
    const template = container.ownerDocument.createElement("template");
    template.innerHTML = markup;
    const incoming = Array.from(template.content.children);
    for (const card of incoming) {
      const id = card.getAttribute(`data-${kind}-job`);
      const actions = card.querySelector(".music-job-actions");
      if (!id || !actions) continue;
      const button = container.ownerDocument.createElement("button");
      button.type = "button";
      button.className = "button ghost";
      button.setAttribute("data-audio-use", `${kind}:${id}`);
      button.textContent = "套用設定";
      button.disabled = !draftFromJob(kind, jobs[kind]?.get(id));
      if (button.disabled) button.title = "這筆舊工作未保留完整生成設定。";
      actions.appendChild(button);
    }
    reconcileHistory(container, incoming, kind);
  }

  function beginSubmission(button) {
    if (button.hasAttribute("aria-busy")) return false;
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    return true;
  }

  function endSubmission(button, ready) {
    button.removeAttribute("aria-busy");
    button.disabled = !ready;
  }

  function createConnectionMemory(initial = {}) {
    let active = "local";
    let urls = { local: "http://127.0.0.1:8188", remote: "" };
    const api = {
      load(settings = {}) {
        active = settings.mode === "remote" ? "remote" : "local";
        urls = {
          local: settings.local_base_url || (active === "local" ? settings.base_url : "") || "http://127.0.0.1:8188",
          remote: settings.remote_base_url || (active === "remote" ? settings.base_url : "") || "",
        };
        return urls[active];
      },
      select(mode, currentUrl) {
        const next = mode === "remote" ? "remote" : "local";
        if (typeof currentUrl === "string") urls[active] = currentUrl;
        active = next;
        return urls[next];
      },
      current(mode) { return urls[mode === "remote" ? "remote" : "local"]; },
    };
    api.load(initial);
    return api;
  }

  function musicStatusPresentation(data = {}, fallbackMode = "local", humanBytes = value => `${Number(value || 0)} bytes`) {
    const remote = (data.connection_mode || fallbackMode) === "remote";
    const installed = data.available !== false && Boolean(data.installed);
    const active = !remote && Boolean(data.active);
    const location = remote ? "遠端 GPU 主機" : "這台電腦";
    const unavailable = data.available === false || ["authentication", "connection", "timeout", "invalid_response"].includes(data.error_code);
    const title = installed ? `Music 3 INT8 模型已就緒 · ${location}`
      : unavailable ? `Music 3 ${data.error_code === "authentication" ? "認證失敗" : "暫時無法連線"}`
      : active ? data.current || "正在下載 Music 3 模型"
      : remote ? data.error_code === "nodes_missing" ? "遠端主機需更新 Music 3 節點" : "遠端主機缺少 Music 3 模型"
      : data.error ? "Music 3 模型安裝失敗" : "尚未安裝 Music 3 模型";
    const speed = active && data.speed_bps ? ` · ${humanBytes(data.speed_bps)}/s` : "";
    const connection = data.connection_url ? ` · 連線：${data.connection_url}` : "";
    const detail = data.error || (remote
      ? `運算與模型：遠端 GPU 主機${connection}；完成音樂回存這台電腦。`
      : `${humanBytes(data.downloaded)} / ${humanBytes(data.total)} · ${Number(data.progress || 0).toFixed(1)}%${speed}`);
    return {
      remote, installed, active, title, detail, canInstall: !remote && data.can_install !== false,
      engineLabel: remote ? "REMOTE OPEN-WEIGHT MUSIC" : "LOCAL OPEN-WEIGHT MUSIC",
      description: remote ? "由目前連線的遠端 GPU 主機產生歌曲或純音樂；模型位於遠端，完成音樂回存這台電腦，不使用付費 API。" : "沿用目前本機 ComfyUI 產生歌曲或純音樂；模型與生成運算位於這台電腦，不使用付費 API。",
      note: remote ? "音樂由遠端 GPU 主機排隊執行，完成後自動回存這台電腦；模型及節點由 GPU 主機管理者維護。" : "長曲需要較久時間；生成會和影片共用本機 GPU 佇列，不會同時搶顯存。",
    };
  }

  function renderMusicStatus(doc, data, fallbackMode, humanBytes) {
    const result = musicStatusPresentation(data, fallbackMode, humanBytes);
    const byId = id => doc.getElementById(id);
    byId("musicStatusDot").className = `music-status-dot${result.installed ? " ready" : result.active ? " active" : data.error ? " error" : ""}`;
    byId("musicModelTitle").textContent = result.title;
    byId("musicModelDetail").textContent = result.detail;
    byId("musicInstallProgress").classList.toggle("hidden", !result.active && !(data.downloaded && !result.remote));
    byId("musicInstallBar").style.width = `${Math.max(0, Math.min(100, Number(data.progress) || 0))}%`;
    byId("installMusicModels").classList.toggle("hidden", !result.canInstall || result.installed || result.active);
    byId("installMusicModels").disabled = !result.canInstall;
    byId("cancelMusicInstall").classList.toggle("hidden", !result.canInstall || !result.active);
    byId("cancelMusicInstall").disabled = !result.canInstall || !result.active;
    byId("generateMusic").disabled = !result.installed || byId("generateMusic").hasAttribute("aria-busy");
    const modal = byId("musicModal");
    const label = byId("musicEngineLabel") || modal?.querySelector(".music-heading .eyebrow");
    const description = byId("musicDescription") || modal?.querySelector(".music-heading h2 + p");
    const note = byId("musicGenerationNote") || modal?.querySelector(".music-generation-note");
    if (label) label.textContent = result.engineLabel;
    if (description) description.textContent = result.description;
    if (note) note.textContent = result.note;
    return result;
  }

  function install(options) {
    const doc = options.document;
    const byId = id => doc.getElementById(id);
    const store = createDraftStore(options.storage);
    const timers = {};
    const notices = {};
    let applying = false;
    const notify = (message, error = false) => options.toast?.(message, error);
    function capture(kind) {
      const values = {};
      for (const [key, id] of Object.entries(fields[kind])) {
        const field = byId(id);
        if (field) values[key] = field.type === "checkbox" ? field.checked : field.value;
      }
      const state = options.getState(kind) || {};
      if (kind === "voice") state.instructDrafts = { ...state.instructDrafts, [state.mode]: values.instruct || "" };
      return cleanDraft(kind, { ...state, fields: values });
    }
    function save(kind) {
      if (applying) return true;
      clearTimeout(timers[kind]);
      const saved = store.save(kind, capture(kind));
      if (notices[kind]) notices[kind].textContent = saved ? "草稿已儲存在此瀏覽器" : "無法保存本機草稿，請先複製重要文字";
      return saved;
    }
    function schedule(kind) {
      if (applying) return;
      if (notices[kind]) notices[kind].textContent = "正在儲存草稿…";
      clearTimeout(timers[kind]);
      timers[kind] = setTimeout(() => save(kind), 250);
    }
    function apply(kind, value) {
      const draft = cleanDraft(kind, value);
      if (!draft) return false;
      applying = true;
      try {
        options.setState(kind, draft);
        for (const [key, id] of Object.entries(fields[kind])) {
          const field = byId(id);
          if (!field || draft.fields[key] === undefined) continue;
          if (field.type === "checkbox") field.checked = draft.fields[key] === true;
          else field.value = String(draft.fields[key]);
        }
        if (kind === "voice") {
          byId("voiceReferenceText").disabled = byId("voiceXVectorOnly").checked;
          if (byId("voiceAuthorized")) byId("voiceAuthorized").checked = false;
        }
      } finally { applying = false; }
      save(kind);
      return true;
    }
    const defaults = { music: capture("music"), voice: capture("voice") };
    for (const kind of ["music", "voice"]) {
      const modal = byId(`${kind}Modal`);
      const editor = modal?.querySelector(".music-editor");
      if (!editor) continue;
      const toolbar = doc.createElement("div");
      toolbar.className = "audio-draft-toolbar";
      const status = doc.createElement("small");
      status.id = `${kind}DraftStatus`;
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      status.textContent = "草稿會儲存在此瀏覽器";
      notices[kind] = status;
      const button = doc.createElement("button");
      button.type = "button";
      button.className = "button ghost small";
      button.textContent = "新建草稿";
      button.addEventListener("click", () => {
        const draft = JSON.parse(JSON.stringify(defaults[kind]));
        draft.fields.seed = String(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER));
        if (kind === "voice") { draft.referenceAsset = null; draft.fields.reference_text = ""; draft.fields.x_vector_only = false; }
        apply(kind, draft);
        byId(`${kind}JobName`)?.focus();
        notify("已建立新草稿；已送出的工作仍保留在歷史中。");
      });
      toolbar.append(status, button);
      editor.insertBefore(toolbar, editor.firstChild);
      for (const name of ["input", "change", "click"]) editor.addEventListener(name, () => schedule(kind));
      const restored = store.load(kind);
      if (restored) { apply(kind, restored); status.textContent = "已恢復此瀏覽器的草稿"; }
      byId(`${kind}JobList`)?.addEventListener("click", event => {
        const use = event.target.closest("[data-audio-use]");
        if (!use || use.disabled) return;
        const [requestedKind, id] = use.getAttribute("data-audio-use").split(":");
        if (requestedKind !== kind) return;
        const draft = draftFromJob(kind, jobs[kind].get(id));
        if (!draft) return notify("這筆舊工作沒有完整設定可套用。", true);
        // Start from the initial form so missing legacy fields never borrow a previous draft.
        const merged = { ...defaults[kind], ...draft, fields: { ...defaults[kind].fields, ...draft.fields } };
        apply(kind, merged);
        byId(`${kind}JobName`)?.focus();
        notify(kind === "voice" && draft.mode === "clone" && !draft.referenceAsset
          ? "已套用台詞與聲線設定；這筆舊工作請重新選擇參考音訊。" : "已套用歷史設定，可修改後再生成。");
      });
    }
    const Observer = doc.defaultView?.MutationObserver;
    if (Observer && byId("voiceReferencePreview")) new Observer(() => schedule("voice")).observe(byId("voiceReferencePreview"), { childList: true, subtree: true, attributes: true });
    doc.defaultView?.addEventListener("pagehide", () => { save("music"); save("voice"); });
    doc.defaultView?.addEventListener("beforeunload", () => { save("music"); save("voice"); });
    doc.addEventListener("visibilitychange", () => { if (doc.hidden) { save("music"); save("voice"); } });
    return { save, capture, apply, rememberJobs, patchHistory };
  }

  return { cleanDraft, createDraftStore, draftFromJob, rememberJobs, patchHistory, reconcileHistory, beginSubmission, endSubmission, createConnectionMemory, musicStatusPresentation, renderMusicStatus, install };
});
