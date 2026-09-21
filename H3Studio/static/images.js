(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const draftKey = 'h3-qwen-image-21-draft-v1';
  const fields = ['imageMode','imageName','imagePrompt','imageWidth','imageHeight','imageSteps','imageSeed','imageSeedAuto','imageTransparent','referenceResolution','imageLora','imageLoraStrength'];
  let refs = [], ready = false, uploading = false, submitting = false, page = 1, totalPages = 1, jobs = [], queue = null, polling = false;
  const deletedIds = new Set();
  let pendingDelete = null;
  let selectedLora = '', loraSupported = false, availableLoras = [], lastJobsMarkup = '';
  const templates = {
    color:'以圖 1 為主圖，將【指定物件】的顏色改為【目標顏色】，保留原本人物五官、造型、姿勢、構圖與其他物件。',
    background:'以圖 1 為主圖，將背景替換為【新場景】，保留主體外觀、五官、服裝與姿勢，使光線與陰影自然融入新場景。',
    style:'以圖 1 為主圖，參考圖 2 的【色彩／筆觸／材質】風格，保留圖 1 的主體身分、姿勢與構圖，不加入圖 2 的其他物件。',
    detail:'以圖 1 為主圖，改善【臉部／材質／邊緣】細節，保留主體身分、五官比例、構圖與色彩，不新增人物或物件。',
    cutout:'擷取圖 1 中的【指定主體】，移除背景並生成透明背景，保留主體外觀、顏色與完整輪廓。'
  };
  async function api(url, options) {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { throw new Error('Studio 後端尚未更新或回應格式錯誤，請確認工具版本並重新啟動。'); }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function update() {
    const edit = $('imageMode').value === 'edit';
    $('promptLabel').textContent = edit ? '③ 要修改與保留的內容' : '圖片描述';
    $('referenceSection').classList.toggle('hidden', !edit);
    $('editingSize').classList.toggle('hidden', !edit);
    $('generationSize').classList.toggle('hidden', edit);
    $('imageSeed').disabled = $('imageSeedAuto').checked;
    $('imageLoraStrength').disabled = !selectedLora || !loraSupported;
    $('referenceFiles').disabled = uploading || submitting;
    $('generateImage').disabled = !ready || uploading || submitting || (edit && refs.length === 0) ||
      (Boolean(selectedLora) && (!loraSupported || !availableLoras.includes(selectedLora)));
  }
  function saveDraft() {
    const data = {refs};
    for (const id of fields) data[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
    data.imageLora = selectedLora;
    try { localStorage.setItem(draftKey, JSON.stringify(data)); } catch {}
  }
  function renderRefs() {
    $('referenceList').innerHTML = refs.map((ref, i) => `<div class="image-reference ${i === 0 ? 'is-primary' : ''}"><img src="/api/assets/${escape(ref.id)}" alt="參考圖 ${i+1}"><button type="button" data-remove="${i}" aria-label="移除參考圖 ${i+1}">×</button><small>圖 ${i+1}${i === 0 ? ' · 主圖' : ' · 輔助參考'}</small><div class="reference-actions">${i > 0 ? `<button type="button" data-primary="${i}">設為主圖</button><button type="button" data-up="${i}" aria-label="將圖 ${i+1} 往前移">←</button>` : ''}${i < refs.length-1 ? `<button type="button" data-down="${i}" aria-label="將圖 ${i+1} 往後移">→</button>` : ''}</div></div>`).join('');
    update(); saveDraft();
  }
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey) || '{}');
    for (const id of fields) if (draft[id] !== undefined) { if ($(id).type === 'checkbox') $(id).checked = draft[id] === true; else $(id).value = draft[id]; }
    selectedLora = typeof draft.imageLora === 'string' ? draft.imageLora : '';
    refs = Array.isArray(draft.refs) ? draft.refs.filter(r => /^[a-f0-9]{32}$/.test(r.id)).slice(0,10) : [];
  } catch {}
  for (const id of fields) $(id).addEventListener('input', () => { if(id === 'imageLora') { selectedLora = $('imageLora').value; renderLoras(); } update(); saveDraft(); });
  $('referenceList').addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || uploading || submitting) return;
    if (button.dataset.remove !== undefined) refs.splice(Number(button.dataset.remove), 1);
    if (button.dataset.primary !== undefined) refs.unshift(...refs.splice(Number(button.dataset.primary), 1));
    if (button.dataset.up !== undefined) { const i=Number(button.dataset.up); [refs[i-1],refs[i]]=[refs[i],refs[i-1]]; }
    if (button.dataset.down !== undefined) { const i=Number(button.dataset.down); [refs[i+1],refs[i]]=[refs[i],refs[i+1]]; }
    renderRefs(); $('formMessage').textContent = '已更新參考圖片；請確認描述中的圖號仍正確。';
  });
  async function uploadFiles(files) {
    if (uploading || submitting || !files.length) return;
    if (files.length + refs.length > 10) { $('formMessage').textContent = '最多只能使用 10 張參考圖片。'; return; }
    if (files.some(file => !/\.(png|jpe?g|webp|bmp)$/i.test(file.name))) { $('formMessage').textContent = '請使用 PNG、JPEG、WebP 或 BMP 圖片。'; return; }
    uploading = true; update();
    try {
      for (const file of files) {
        $('formMessage').textContent = `正在匯入 ${file.name}…`;
        const data = new FormData(); data.append('kind','qwen-image-reference'); data.append('file',file);
        refs.push(await api('/api/assets', {method:'POST',body:data}));
      }
      $('formMessage').textContent = `已匯入 ${refs.length} 張圖片。`;
    } catch (error) { $('formMessage').textContent = error.message; }
    finally { uploading = false; renderRefs(); }
  }
  $('referenceFiles').addEventListener('change', async event => { await uploadFiles([...event.target.files]); event.target.value=''; });
  $('referenceDrop').addEventListener('dragover', event => { event.preventDefault(); $('referenceDrop').classList.add('dragging'); });
  $('referenceDrop').addEventListener('dragleave', () => $('referenceDrop').classList.remove('dragging'));
  $('referenceDrop').addEventListener('drop', async event => { event.preventDefault(); $('referenceDrop').classList.remove('dragging'); await uploadFiles([...event.dataTransfer.files]); });
  $('insertTemplate').addEventListener('click', () => {
    const key = $('editTemplate').value, text = templates[key]; if (!text) return;
    if (key === 'style' && refs.length < 2) { $('formMessage').textContent='風格參考需要圖 1 主圖和圖 2 風格圖。'; return; }
    const combined = [$('imagePrompt').value.trim(),text].filter(Boolean).join('\n');
    if(combined.length > 8000) { $('formMessage').textContent='描述已超過 8,000 字元，請先精簡。'; return; }
    $('imagePrompt').value=combined;
    if(key === 'cutout') $('imageTransparent').checked=true;
    saveDraft(); $('imagePrompt').focus();
  });
  function renderLoras() {
    $('imageLora').innerHTML = '<option value="">不使用 LoRA</option>' + availableLoras.map(n=>`<option value="${escape(n)}">${escape(n.replace(/^qwen_image_2_1[\\/]/,''))}</option>`).join('') +
      (selectedLora && !availableLoras.includes(selectedLora) ? `<option value="${escape(selectedLora)}">未提供：${escape(selectedLora)}</option>` : '');
    $('imageLora').value=selectedLora;
    $('loraStatus').textContent = !loraSupported ? '此後端尚未提供 LoRA 支援；請更新並重啟 Studio／檢查引擎。' :
      selectedLora && !availableLoras.includes(selectedLora) ? '原本選擇的 LoRA 不在目前引擎，請重新選擇後生成。' :
      availableLoras.length ? `目前引擎提供 ${availableLoras.length} 個專用資料夾中的 LoRA；請確認作者標示的基礎模型與觸發詞。` : '目前引擎尚未安裝 Qwen-Image-2.1 專用 LoRA，可直接使用基礎模型。';
    update();
  }
  async function checkStatus() {
    $('refreshStatus').disabled = true;
    try {
      const result = await api('/api/images/status'); ready = result.ready === true;
      loraSupported = result.lora_supported === true; availableLoras = Array.isArray(result.loras) ? result.loras : []; renderLoras();
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
    const markup = jobs.length ? jobs.map(job => {
      const active = ['queued','preparing','running'].includes(job.status), done = job.status === 'completed';
      const title = escape(job.name), id = escape(job.id);
      return `<article class="image-card" data-job-id="${id}">${done ? `<a href="/api/images/jobs/${id}/image" target="_blank" rel="noopener"><img class="image-preview" loading="lazy" src="/api/images/jobs/${id}/image" alt="${title}"></a>` : ''}<div class="image-card-body"><h3>${title}</h3><p>${escape(jobStatus(job))}</p>${active && queue?.jobs?.[job.id]?.phase === 'engine_running' ? `<progress max="100" value="${Math.min(100,Math.max(0,Number(job.progress)||0))}" aria-label="採樣進度"></progress>` : ''}<p>${done ? `${job.width} × ${job.height} · ` : ''}${job.steps} 步 · Seed ${job.seed}${done && job.rgba ? ' · RGBA' : ''}</p>${job.error ? `<p class="image-error">${escape(job.error)}</p>` : ''}<details><summary>查看描述與 LoRA</summary><p>${escape(job.original_prompt || job.prompt)}</p><p>${job.lora_name ? `LoRA：${escape(job.lora_name)} · 強度 ${escape(job.lora_strength)}` : '未使用 LoRA'}</p></details><div class="image-card-actions">${done ? `<a class="button ghost small" href="/api/images/jobs/${id}/image?download=1">下載 PNG</a><button type="button" class="button ghost small" data-continue="${id}">以此圖繼續編輯</button><button type="button" class="button ghost small" data-reference="${id}">新增為輔助參考</button>${job.mode === 'edit' && job.image_asset_ids?.length ? `<button type="button" class="button ghost small" data-compare="${id}">原圖／結果比較</button>` : ''}` : ''}<button type="button" class="button ghost small" data-reuse="${id}">套用設定</button>${active ? `<button type="button" class="button ghost small" data-cancel="${id}">取消</button>` : ''}${['completed','failed','cancelled','interrupted'].includes(job.status) ? `<button type="button" class="button danger small" data-delete="${id}">刪除圖片</button>` : ''}</div></div></article>`;
    }).join('') : '<p class="image-empty">第一張圖片，從左側描述開始。<br>生成結果會保留在這裡，也能作為下一張的參考圖。</p>';
    if (markup !== lastJobsMarkup) {
      const expanded = new Set([...$('imageJobs').querySelectorAll('article[data-job-id]')].filter(e=>e.querySelector('details')?.open).map(e=>e.dataset.jobId));
      $('imageJobs').innerHTML = markup; lastJobsMarkup = markup;
      for(const card of $('imageJobs').querySelectorAll('article[data-job-id]')) if(expanded.has(card.dataset.jobId)) card.querySelector('details').open=true;
    }
    $('pageLabel').textContent = `第 ${page} / ${totalPages} 頁`;
    $('previousPage').disabled = page <= 1; $('nextPage').disabled = page >= totalPages;
  }
  async function refresh() {
    if (polling) return;
    polling = true;
    const results = await Promise.allSettled([api(`/api/images/jobs?page=${page}`),api('/api/queue')]);
    if (results[0].status === 'fulfilled') { const data = results[0].value; jobs = data.items.filter(job=>!deletedIds.has(job.id)); page = data.page; totalPages = data.total_pages; }
    else $('imageQueue').textContent = results[0].reason.message;
    queue = results[1].status === 'fulfilled' ? results[1].value : null;
    if (results[0].status === 'fulfilled') $('imageQueue').textContent = queue?.available ? `共享引擎：執行 ${queue.running_count} 筆 · 排隊 ${queue.pending_count} 筆 · 本機待送出 ${queue.local_waiting_count} 筆` : '暫時無法取得共享引擎佇列，工作仍可能進行中。';
    renderJobs(); polling = false;
  }
  $('imageForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || submitting || uploading || $('generateImage').disabled) return;
    const payload = {name:$('imageName').value, mode:$('imageMode').value, prompt:$('imagePrompt').value,
      width:Number($('imageWidth').value),height:Number($('imageHeight').value),steps:Number($('imageSteps').value),
      seed:Number($('imageSeed').value),seed_auto:$('imageSeedAuto').checked,transparent:$('imageTransparent').checked,
      lora_name:selectedLora,lora_strength:Number($('imageLoraStrength').value),
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
    if (uploading || submitting) return;
    uploading=true; update();
    button.disabled = true;
    try {
      if (button.dataset.delete) {
        const job=jobs.find(j=>j.id === button.dataset.delete);
        if (!job) return;
        pendingDelete=job.id;
        $('deleteImageName').textContent=job.name;
        $('deleteImageError').textContent='';
        $('deleteImageDialog').showModal();
        return;
      }
      if (button.dataset.cancel) { await api(`/api/images/jobs/${button.dataset.cancel}/cancel`,{method:'POST'}); await refresh(); }
      if (button.dataset.compare) {
        const job=jobs.find(j=>j.id === button.dataset.compare);
        if(job?.image_asset_ids?.length) {
          $('compareBefore').src=`/api/assets/${job.image_asset_ids[0]}`;
          $('compareAfter').src=`/api/images/jobs/${job.id}/image`;
          $('compareTitle').textContent=job.name + ' · 原圖與結果'; $('imageCompare').showModal();
        }
      }
      if (button.dataset.continue) {
        uploading=true; update();
        const asset=await api(`/api/images/jobs/${button.dataset.continue}/reference`,{method:'POST'});
        refs=[asset]; $('imageMode').value='edit';
        $('imagePrompt').value=''; $('imageSeedAuto').checked=true;
        const source=jobs.find(j=>j.id === button.dataset.continue);
        if(source) {
          selectedLora=source.lora_name || ''; $('imageLoraStrength').value=source.lora_strength ?? 1; renderLoras();
          $('imageSteps').value=source.steps || 25; $('editTemplate').value='';
          $('imageName').value=source.name.slice(0,74)+' · 編輯';
          $('imageTransparent').checked=source.transparent === true;
          $('referenceResolution').value=source.reference_resolution || 1024;
        }
        renderRefs(); $('imagePrompt').focus();
        $('formMessage').textContent='已將此結果設為唯一主圖，請描述下一步要修改的內容。';
      }
      if (button.dataset.reference) {
        uploading=true; update();
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
        selectedLora=job.lora_name || ''; $('imageLoraStrength').value=job.lora_strength ?? 1; renderLoras();
        $('referenceResolution').value = job.reference_resolution; refs = job.image_asset_ids.map(id=>({id})); renderRefs();
        $('imagePrompt').focus(); $('formMessage').textContent = '已套用原設定及固定 Seed；按生成才會建立新工作。';
      }
    } catch(error) { $('formMessage').textContent = error.message; }
    finally { uploading=false; update(); button.disabled = false; }
  });
  $('cancelImageDelete').addEventListener('click',()=>{if(!uploading) {pendingDelete=null; $('deleteImageDialog').close();}});
  $('deleteImageDialog').addEventListener('cancel',event=>{if(uploading) event.preventDefault(); else pendingDelete=null;});
  $('confirmImageDelete').addEventListener('click',async()=>{
    if (!pendingDelete || uploading || submitting) return;
    const id=pendingDelete;
    uploading=true; update(); $('confirmImageDelete').disabled=true; $('cancelImageDelete').disabled=true;
    try {
      await api(`/api/images/jobs/${id}`,{method:'DELETE'});
      deletedIds.add(id); jobs=jobs.filter(j=>j.id !== id); renderJobs();
      pendingDelete=null; $('deleteImageDialog').close();
      $('formMessage').textContent='圖片工作已刪除；已轉存的參考圖與 ComfyUI 原始輸出仍保留。';
      await refresh();
    } catch(error) { $('deleteImageError').textContent=error.message; }
    finally {uploading=false; update(); $('confirmImageDelete').disabled=false; $('cancelImageDelete').disabled=false;}
  });
  $('closeCompare').addEventListener('click',()=> $('imageCompare').close());
  $('refreshStatus').addEventListener('click',checkStatus); $('refreshJobs').addEventListener('click',refresh);
  $('previousPage').addEventListener('click',()=>{ if(page>1) {page--;refresh();} });
  $('nextPage').addEventListener('click',()=>{if(page<totalPages) {page++;refresh();} });
  renderRefs(); checkStatus(); refresh();
  setInterval(()=>{if(!document.hidden) refresh();},4000);
})();
