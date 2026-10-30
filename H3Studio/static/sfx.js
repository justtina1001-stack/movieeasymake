(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else { root.H3SoundEffects = api; if (root.document) api.workspace = api.install(root); }
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const validId = value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const activeStates = new Set(["queued", "waiting", "preparing", "running"]);
  const labels = { queued: "等待中", waiting: "等待中", preparing: "準備模型", running: "生成中", completed: "已完成", failed: "失敗", cancelled: "已取消", interrupted: "已中斷" };
  const escape = value => String(value ?? "").replace(/[&<>"']/g, letter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[letter]);
  function cleanRequest(value = {}) {
    const prompt = String(value.prompt || "").trim(), name = String(value.job_name || "").trim();
    const duration = Number(value.duration), seed = String(value.seed ?? "").trim();
    if (!prompt || prompt.length > 2000) throw new Error("請填寫音效描述，最多 2,000 字。");
    if (name.length > 80) throw new Error("音效名稱最多 80 個字。");
    if (!Number.isFinite(duration) || duration < 1 || duration > 30) throw new Error("音效長度請設定在 1 到 30 秒之間。");
    if (!/^\d+$/.test(seed) || BigInt(seed) > 9007199254740991n) throw new Error("請填寫有效的 Seed 數字（0–9007199254740991）。");
    return { job_name: name, prompt, duration, seed };
  }
  function editorUrl(jobId, projectId = "") {
    if (!validId(jobId) || projectId && !validId(projectId)) throw new Error("音效或剪輯專案編號無效。");
    return `/editor?sfx=${jobId}${projectId ? `&project=${projectId}` : "&new=1"}`;
  }
  function performanceText(job) {
    const metrics = job.performance || {}, parts = [];
    const positive = value => value !== null && value !== undefined && Number.isFinite(Number(value)) && Number(value) >= 0;
    const elapsed = job.execution_seconds ?? metrics.elapsed_seconds;
    if (positive(elapsed)) parts.push(`生成耗時 ${Number(elapsed).toFixed(1)} 秒`);
    if (positive(metrics.inference_seconds)) parts.push(`推論 ${Number(metrics.inference_seconds).toFixed(1)} 秒`);
    if (positive(metrics.peak_vram_allocated_bytes)) parts.push(`模型顯存峰值 ${(Number(metrics.peak_vram_allocated_bytes) / 1073741824).toFixed(2)} GiB`);
    return parts.join(" · ");
  }
  function historyMarkup(records) {
    return (records || []).filter(job => validId(job.id)).map(job => {
      const active = activeStates.has(job.status), id = job.id;
      const percent = Number.isFinite(Number(job.progress)) ? Math.max(0, Math.min(100, Number(job.progress))) : null;
      const created = job.created_at ? new Date(job.created_at).toLocaleString("zh-TW", { hour12: false }) : "";
      return `<article class="music-job" data-sfx-job="${id}">
        <div class="music-job-head"><div class="music-job-title"><strong>${escape(job.name || "未命名音效")}</strong><small>${escape(created)} · ${escape(job.duration)} 秒 · Seed ${escape(job.seed)}</small></div></div>
        <div class="music-job-status"><b class="${escape(job.status)}">${escape(labels[job.status] || "確認中")}</b><span>${escape(active ? job.current_node || "等待共用 GPU 佇列" : performanceText(job))}</span></div>
        ${active ? `<div class="sfx-progress" role="status">${percent !== null ? `<progress max="100" value="${percent}"></progress><span>${percent.toFixed(0)}%</span>` : "正在確認進度…"}</div>` : ""}
        ${job.status === "completed" ? `<audio controls preload="none" aria-label="${escape(job.name || "音效")}試聽" src="/api/sfx/jobs/${id}/audio"></audio>` : ""}
        ${job.error ? `<div class="music-job-error">${escape(job.error)}</div>` : ""}
        <div class="music-job-actions">
          ${job.status === "completed" ? `<a class="button secondary" href="/api/sfx/jobs/${id}/audio?download=1" download>下載 WAV</a><button class="button secondary" type="button" data-sfx-editor="${id}">加入剪輯音軌</button>` : ""}
          ${active ? `<button class="button ghost" type="button" data-sfx-cancel="${id}">取消</button>` : ""}
          <button class="button ghost" type="button" data-sfx-reuse="${id}">套用設定</button>
        </div><details class="sfx-job-description"><summary>音效描述</summary><p>${escape(job.prompt || job.settings?.prompt || "")}</p></details>
      </article>`;
    }).join("") || '<div class="empty-state">尚無音效工作。輸入一段聲音描述即可開始。</div>';
  }
  function install(win) {
    const doc = win.document, byId = id => doc.getElementById(id);
    if (!byId("sfxModal")) return null;
    let ready = false, page = 1, totalPages = 1, records = new Map(), pending = null, busy = false, loading = false, timer = null, editorJobId = "";
    const notify = (message, error = false) => { if (typeof win.toast === "function") win.toast(message, error); else byId("sfxFeedback").textContent = message; };
    const randomSeed = () => String(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER));
    const requestId = () => win.crypto?.randomUUID?.().replaceAll("-", "") || `${Date.now().toString(16)}${randomSeed()}`;
    async function read(url, options) {
      const response = await win.fetch(url, options), value = await response.json().catch(() => ({}));
      if (!response.ok) { const error = new Error(value.error || `HTTP ${response.status}`); error.status = response.status; throw error; }
      return value;
    }
    const json = value => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
    function capture() { return { job_name: byId("sfxJobName").value, prompt: byId("sfxPrompt").value, duration: byId("sfxDuration").value, seed: byId("sfxSeed").value }; }
    function saveDraft() {
      try { win.localStorage.setItem("h3studio-sfx-draft-v1", JSON.stringify(capture())); byId("sfxDraftStatus").textContent = "草稿已儲存在此瀏覽器"; }
      catch { byId("sfxDraftStatus").textContent = "無法保存草稿，請先複製重要描述"; }
    }
    function applyDraft(draft) {
      for (const [key, id] of Object.entries({ job_name: "sfxJobName", prompt: "sfxPrompt", duration: "sfxDuration", seed: "sfxSeed" })) {
        if (["string", "number"].includes(typeof draft?.[key])) byId(id).value = String(draft[key]);
      }
    }
    async function status() {
      try {
        const data = await read("/api/sfx/status");
        ready = data.installed === true && data.available !== false && data.connection_mode !== "remote";
        byId("sfxModelTitle").textContent = ready ? "MOSS-SoundEffect v2.0 已就緒" : data.connection_mode === "remote" ? "目前遠端引擎尚未提供 MOSS 音效" : "MOSS 音效尚未就緒";
        byId("sfxModelDetail").textContent = data.error || (ready ? "中文／英文描述 · 最長 30 秒 · 48kHz WAV" : "請由主機完成 MOSS 安裝，再按「重新檢查」。");
      } catch (error) { ready = false; byId("sfxModelTitle").textContent = "暫時無法取得音效狀態"; byId("sfxModelDetail").textContent = error.message; }
      byId("generateSfx").disabled = !ready || busy;
      byId("sfxStatusDot").className = `music-status-dot${ready ? " ready" : ""}`;
    }
    async function loadJobs() {
      if (loading) return;
      loading = true;
      try {
        const data = await read(`/api/sfx/jobs?page=${page}&page_size=12`);
        const items = Array.isArray(data.items) ? data.items : [];
        page = Number(data.page) || 1; totalPages = Number(data.total_pages) || 1;
        records = new Map(items.filter(job => validId(job.id)).map(job => [job.id, job]));
        const markup = historyMarkup(items), container = byId("sfxJobList");
        const template = doc.createElement("template"); template.innerHTML = markup;
        if (win.H3AudioWorkspace?.reconcileHistory) win.H3AudioWorkspace.reconcileHistory(container, [...template.content.children], "sfx");
        else if (container.dataset.markup !== markup) container.innerHTML = markup;
        container.dataset.markup = markup;
        byId("sfxPageLabel").textContent = `${page} / ${totalPages}`;
        byId("previousSfxPage").disabled = page <= 1; byId("nextSfxPage").disabled = page >= totalPages;
      } catch (error) { byId("sfxFeedback").textContent = `讀取音效工作失敗：${error.message}`; }
      finally { loading = false; }
    }
    async function generate() {
      if (busy || !ready) return;
      busy = true; byId("generateSfx").disabled = true; byId("generateSfx").setAttribute("aria-busy", "true");
      try {
        const payload = cleanRequest(capture()), signature = JSON.stringify(payload);
        if (!pending || pending.signature !== signature) pending = { signature, request_id: requestId() };
        const job = await read("/api/sfx/jobs", json({ ...payload, request_id: pending.request_id }));
        pending = null; page = 1; saveDraft();
        byId("sfxFeedback").textContent = `音效工作 ${String(job.id).slice(0, 8)} 已加入佇列；關閉面板仍會繼續。`;
        notify("音效工作已加入共用 GPU 佇列。"); await loadJobs();
      } catch (error) { if (error.status >= 400 && error.status < 500) pending = null; byId("sfxFeedback").textContent = error.message; notify(error.message, true); }
      finally { busy = false; byId("generateSfx").removeAttribute("aria-busy"); byId("generateSfx").disabled = !ready; }
    }
    async function openEditor(jobId) {
      if (!validId(jobId)) throw new Error("找不到這筆音效。");
      editorJobId = jobId;
      const select = byId("sfxEditorProject"); select.innerHTML = '<option value="">建立新的剪輯專案</option>';
      byId("sfxOpenEditor").href = editorUrl(jobId); byId("sfxEditorStatus").textContent = "正在讀取剪輯專案…";
      byId("sfxEditorDialog").showModal();
      try {
        const data = await read("/api/editor/projects"), projects = Array.isArray(data) ? data : data.projects || data.items || [];
        if (editorJobId !== jobId) return;
        for (const project of projects) if (validId(project.id)) {
          const option = doc.createElement("option"); option.value = project.id; option.textContent = project.name || "未命名專案"; select.append(option);
        }
        byId("sfxEditorStatus").textContent = "音效會加入所選專案開頭的可用音軌；既有影片與音軌都會保留。";
      } catch (error) { byId("sfxEditorStatus").textContent = `無法讀取既有專案；仍可建立新專案。${error.message}`; }
    }
    async function open() {
      byId("sfxModal").classList.remove("hidden");
      await Promise.all([status(), loadJobs()]);
      if (timer === null) timer = win.setInterval(() => { if (!doc.hidden && !byId("sfxModal").classList.contains("hidden")) { status(); loadJobs(); } }, 2500);
      byId("sfxPrompt").focus();
    }
    function close() {
      saveDraft(); byId("sfxModal").classList.add("hidden");
      byId("sfxModal").querySelectorAll("audio").forEach(audio => audio.pause());
      if (byId("sfxEditorDialog").open) byId("sfxEditorDialog").close();
      if (timer !== null) { win.clearInterval(timer); timer = null; }
    }
    byId("sfxSeed").value = randomSeed();
    try { applyDraft(JSON.parse(win.localStorage.getItem("h3studio-sfx-draft-v1") || "null")); } catch { /* keep form defaults */ }
    byId("openSfxStudio").addEventListener("click", () => open().catch(error => notify(error.message, true)));
    byId("sfxModal").querySelectorAll("[data-close-sfx]").forEach(node => node.addEventListener("click", close));
    byId("sfxForm").addEventListener("submit", event => { event.preventDefault(); generate(); });
    byId("sfxForm").addEventListener("input", saveDraft); byId("sfxForm").addEventListener("change", saveDraft);
    byId("randomSfxSeed").addEventListener("click", () => { byId("sfxSeed").value = randomSeed(); saveDraft(); });
    byId("checkSfxStatus").addEventListener("click", status); byId("refreshSfxJobs").addEventListener("click", loadJobs);
    byId("previousSfxPage").addEventListener("click", () => { if (page > 1) { page--; loadJobs(); } });
    byId("nextSfxPage").addEventListener("click", () => { if (page < totalPages) { page++; loadJobs(); } });
    byId("sfxJobList").addEventListener("click", async event => {
      try {
        const cancel = event.target.closest("[data-sfx-cancel]"), reuse = event.target.closest("[data-sfx-reuse]"), editor = event.target.closest("[data-sfx-editor]");
        if (cancel) { const id = cancel.dataset.sfxCancel; if (!validId(id)) return; cancel.disabled = true; await read(`/api/sfx/jobs/${id}/cancel`, json({})); await loadJobs(); }
        if (reuse) { const job = records.get(reuse.dataset.sfxReuse); if (job) { applyDraft({ ...(job.settings || job), job_name: job.name }); saveDraft(); byId("sfxPrompt").focus(); notify("已套用音效描述、長度與 Seed；尚未送出新工作。"); } }
        if (editor) await openEditor(editor.dataset.sfxEditor);
      } catch (error) { notify(error.message, true); }
    });
    byId("sfxEditorProject").addEventListener("change", () => { byId("sfxOpenEditor").href = editorUrl(editorJobId, byId("sfxEditorProject").value); });
    byId("closeSfxEditor").addEventListener("click", () => byId("sfxEditorDialog").close());
    win.addEventListener("pagehide", saveDraft);
    return { open, close, status, loadJobs, generate, openEditor };
  }
  return { cleanRequest, editorUrl, performanceText, historyMarkup, install };
});
