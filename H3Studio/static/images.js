(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const draftKey = 'h3-qwen-image-21-draft-v1';
  const fields = ['imageMode','imageName','imagePrompt','imageWidth','imageHeight','imageSteps','imageSeed','imageSeedAuto','imageTransparent','referenceResolution'];
  let refs = [], ready = false, uploading = false, submitting = false, page = 1, totalPages = 1, jobs = [], queue = null, polling = false;
  async function api(url, options) {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { throw new Error('Studio 後端尚未更新或回應格式錯誤，請確認工具版本並重新啟動。'); }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function update() {
    const edit = $('imageMode').value === 'edit';
    $('referenceSection').classList.toggle('hidden', !edit);
    $('editingSize').classList.toggle('hidden', !edit);
    $('generationSize').classList.toggle('hidden', edit);
    $('imageSeed').disabled = $('imageSeedAuto').checked;
    $('referenceFiles').disabled = uploading || submitting;
    $('generateImage').disabled = !ready || uploading || submitting || (edit && refs.length === 0);
  }
  function saveDraft() {
    const data = {refs};
    for (const id of fields) data[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
    try { localStorage.setItem(draftKey, JSON.stringify(data)); } catch {}
  }
  function renderRefs() {
    $('referenceList').innerHTML = refs.map((ref, i) => `<div class="image-reference"><img src="/api/assets/${escape(ref.id)}" alt="參考圖 ${i+1}"><button type="button" data-remove="${i}" aria-label="移除參考圖 ${i+1}">×</button><small>圖 ${i+1}</small></div>`).join('');
    update(); saveDraft();
  }
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey) || '{}');
    for (const id of fields) if (draft[id] !== undefined) { if ($(id).type === 'checkbox') $(id).checked = draft[id] === true; else $(id).value = draft[id]; }
    refs = Array.isArray(draft.refs) ? draft.refs.filter(r => /^[a-f0-9]{32}$/.test(r.id)).slice(0,10) : [];
  } catch {}
  for (const id of fields) $(id).addEventListener('input', () => { update(); saveDraft(); });
  $('referenceList').addEventListener('click', event => {
    const button = event.target.closest('[data-remove]');
    if (button && !uploading && !submitting) { refs.splice(Number(button.dataset.remove), 1); renderRefs(); }
  });
  $('referenceFiles').addEventListener('change', async event => {
    const files = [...event.target.files];
    if (files.length + refs.length > 10) { $('formMessage').textContent = '最多只能使用 10 張參考圖片。'; event.target.value = ''; return; }
    uploading = true; update();
    try {
      for (const file of files) {
        $('formMessage').textContent = `正在匯入 ${file.name}…`;
        const data = new FormData(); data.append('kind','qwen-image-reference'); data.append('file',file);
        refs.push(await api('/api/assets', {method:'POST',body:data}));
      }
      $('formMessage').textContent = `已匯入 ${refs.length} 張圖片。`;
    } catch (error) { $('formMessage').textContent = error.message; }
    finally { uploading = false; event.target.value = ''; renderRefs(); }
  });
  async function checkStatus() {
    $('refreshStatus').disabled = true;
    try {
      const result = await api('/api/images/status'); ready = result.ready === true;
      $('engineStatus').textContent = ready ? 'Qwen-Image-2.1 已就緒' : '圖片引擎尚未就緒';
      $('engineDetail').textContent = result.error || `${result.mode === 'remote' ? '共用遠端' : '本機'}引擎 · 官方 INT8 模型 · 圖片與影片依佇列順序處理`;
    } catch (error) { ready = false; $('engineStatus').textContent = '暫時無法檢查引擎'; $('engineDetail').textContent = error.message; }
    finally { $('refreshStatus').disabled = false; update(); }
  }
  function jobStatus(job) {
    if (job.status === 'completed') return '已完成';
    if (job.status === 'failed') return '生成失敗';
    if (job.status === 'cancelled') return '已取消';
    if (job.status === 'interrupted') return '工作中斷';
    const entry = queue?.jobs?.[job.id];
    if (entry?.phase === 'engine_waiting') return `排隊第 ${entry.position} 位 · 前方 ${entry.ahead_count} 筆工作`;
    if (entry?.phase === 'engine_running') return `引擎生成中 · ${Math.round(job.progress || 0)}%`;
    if (job.status === 'queued') return '本機待送出 · 等待前一筆工作';
    if (job.current_node === '下載完成圖片') return '正在下載完成圖片';
    return queue?.available ? '準備素材／同步工作狀態中' : '暫時無法取得引擎狀態';
  }
  function renderJobs() {
    $('imageJobs').innerHTML = jobs.length ? jobs.map(job => {
      const active = ['queued','preparing','running'].includes(job.status), done = job.status === 'completed';
      const title = escape(job.name), id = escape(job.id);
      return `<article class="image-card">${done ? `<a href="/api/images/jobs/${id}/image" target="_blank" rel="noopener"><img class="image-preview" loading="lazy" src="/api/images/jobs/${id}/image" alt="${title}"></a>` : ''}<div class="image-card-body"><h3>${title}</h3><p>${escape(jobStatus(job))}</p>${active && queue?.jobs?.[job.id]?.phase === 'engine_running' ? `<progress max="100" value="${Math.min(100,Math.max(0,Number(job.progress)||0))}" aria-label="採樣進度"></progress>` : ''}<p>${done ? `${job.width} × ${job.height} · ` : ''}${job.steps} 步 · Seed ${job.seed}${done && job.rgba ? ' · RGBA' : ''}</p>${job.error ? `<p class="image-error">${escape(job.error)}</p>` : ''}<details><summary>查看描述</summary><p>${escape(job.original_prompt || job.prompt)}</p></details><div class="image-card-actions">${done ? `<a class="button ghost small" href="/api/images/jobs/${id}/image?download=1">下載 PNG</a><button type="button" class="button ghost small" data-reference="${id}">用作參考圖</button>` : ''}<button type="button" class="button ghost small" data-reuse="${id}">套用設定</button>${active ? `<button type="button" class="button ghost small" data-cancel="${id}">取消</button>` : ''}</div></div></article>`;
    }).join('') : '<p class="image-empty">第一張圖片，從左側描述開始。<br>生成結果會保留在這裡，也能作為下一張的參考圖。</p>';
    $('pageLabel').textContent = `第 ${page} / ${totalPages} 頁`;
    $('previousPage').disabled = page <= 1; $('nextPage').disabled = page >= totalPages;
  }
  async function refresh() {
    if (polling) return;
    polling = true;
    const results = await Promise.allSettled([api(`/api/images/jobs?page=${page}`),api('/api/queue')]);
    if (results[0].status === 'fulfilled') { const data = results[0].value; jobs = data.items; page = data.page; totalPages = data.total_pages; }
    else $('imageQueue').textContent = results[0].reason.message;
    queue = results[1].status === 'fulfilled' ? results[1].value : null;
    if (results[0].status === 'fulfilled') $('imageQueue').textContent = queue?.available ? `共享引擎：執行 ${queue.running_count} 筆 · 排隊 ${queue.pending_count} 筆 · 本機待送出 ${queue.local_waiting_count} 筆` : '暫時無法取得共享引擎佇列，工作仍可能進行中。';
    renderJobs(); polling = false;
  }
  $('imageForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || submitting || uploading) return;
    const payload = {name:$('imageName').value, mode:$('imageMode').value, prompt:$('imagePrompt').value,
      width:Number($('imageWidth').value),height:Number($('imageHeight').value),steps:Number($('imageSteps').value),
      seed:Number($('imageSeed').value),seed_auto:$('imageSeedAuto').checked,transparent:$('imageTransparent').checked,
      reference_resolution:Number($('referenceResolution').value),image_asset_ids:$('imageMode').value === 'edit' ? refs.map(r=>r.id) : []};
    submitting = true; update();
    try {
      const job = await api('/api/images/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      $('imageSeed').value = job.seed; saveDraft(); page = 1;
      $('formMessage').textContent = `圖片工作已加入佇列 · Seed ${job.seed}`;
      await refresh();
    } catch(error) { $('formMessage').textContent = error.message; }
    finally { submitting = false; update(); }
  });
  $('imageJobs').addEventListener('click', async event => {
    const button = event.target.closest('button'); if (!button) return;
    button.disabled = true;
    try {
      if (button.dataset.cancel) { await api(`/api/images/jobs/${button.dataset.cancel}/cancel`,{method:'POST'}); await refresh(); }
      if (button.dataset.reference) {
        if (refs.length >= 10) throw new Error('參考圖已達 10 張，請先移除一張。');
        refs.push(await api(`/api/images/jobs/${button.dataset.reference}/reference`,{method:'POST'}));
        $('imageMode').value = 'edit'; renderRefs(); $('imagePrompt').focus();
      }
      if (button.dataset.reuse) {
        const job = jobs.find(j=>j.id === button.dataset.reuse);
        if (!job) return;
        $('imageMode').value = job.mode; $('imageName').value = job.name; $('imagePrompt').value = job.original_prompt || job.prompt;
        $('imageWidth').value = job.width; $('imageHeight').value = job.height; $('imageSteps').value = job.steps;
        $('imageSeed').value = job.seed; $('imageSeedAuto').checked = false; $('imageTransparent').checked = job.transparent;
        $('referenceResolution').value = job.reference_resolution; refs = job.image_asset_ids.map(id=>({id})); renderRefs();
        $('imagePrompt').focus(); $('formMessage').textContent = '已套用原設定及固定 Seed；按生成才會建立新工作。';
      }
    } catch(error) { $('formMessage').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('refreshStatus').addEventListener('click',checkStatus); $('refreshJobs').addEventListener('click',refresh);
  $('previousPage').addEventListener('click',()=>{ if(page>1) {page--;refresh();} });
  $('nextPage').addEventListener('click',()=>{if(page<totalPages) {page++;refresh();} });
  renderRefs(); checkStatus(); refresh();
  setInterval(()=>{if(!document.hidden) refresh();},4000);
})();
