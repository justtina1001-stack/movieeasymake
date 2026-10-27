(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const draftKey = 'h3-qwen-image-21-draft-v1';
  const fields = ['imageMode','imageModel','imageName','imagePrompt','imageWidth','imageHeight','imageSteps','imageSeed','imageSeedAuto','imageTransparent','referenceResolution','imageLora','imageLoraStrength','editTemplate'];
  let refs = [], ready = false, uploading = false, submitting = false, page = 1, totalPages = 1, jobs = [], queue = null, polling = false;
  const deletedIds = new Set();
  let pendingDelete = null;
  let pendingUpscale = null, upscaleModels = [], upscaleReady = false;
  let selectedLora = '', loraSupported = false, availableLoras = [], lastJobsMarkup = '';
  let selectedVariant = 'standard', standardSteps = '25', modelVariantSupported = false, modelVariants = [], engineError = '';
  const modelLabels = {standard:'Qwen 原版 · 精細生成',turbo:'Qwen 官方 Turbo · 快速 8 步',flux3:'FLUX 3 Image Klein · 單張改圖'};
  const modelLicenseUrls = {standard:'https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE',turbo:'https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE',flux3:'https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md'};
  const variantOf = value => ['standard','turbo','flux3'].includes(value) ? value : 'standard';
  const normaliseQwenSettings = value => ({
    mode:value?.mode === 'edit' ? 'edit' : 'generate', transparent:value?.transparent === true,
    referenceResolution:['512','1024','2048'].includes(String(value?.referenceResolution)) ? String(value.referenceResolution) : '1024',
    editTemplate:['color','background','style','detail','cutout'].includes(value?.editTemplate) ? value.editTemplate : '',
  });
  let qwenSettings = normaliseQwenSettings({});
  const sourceVariant = job => variantOf(job?.mode === 'upscale' ? job.source_model_variant : job?.model_variant);
  const jobModelLabel = job => job.mode === 'upscale' ? 'Real-ESRGAN · 增強放大' : modelLabels[sourceVariant(job)];
  let loraCatalog = H3LoraHelp.catalog({}, {model:'qwen'});
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
  function selectedModelReady() {
    return ready && (modelVariantSupported ? modelVariants.some(item => item.id === selectedVariant && item.ready === true) : selectedVariant === 'standard');
  }
  const referenceLimit = () => selectedVariant === 'flux3' ? 1 : 10;
  function rememberQwenSettings() {
    if (selectedVariant !== 'flux3') qwenSettings = normaliseQwenSettings({mode:$('imageMode').value,transparent:$('imageTransparent').checked,referenceResolution:$('referenceResolution').value,editTemplate:$('editTemplate').value});
  }
  function updateModelControls() {
    const turbo = selectedVariant === 'turbo', flux = selectedVariant === 'flux3', profile = modelVariants.find(item => item.id === selectedVariant);
    $('imageModel').value = selectedVariant;
    $('imageModel').disabled = submitting;
    $('imageMode').disabled = flux || submitting;
    $('imageSteps').disabled = turbo || flux || submitting;
    if (turbo) $('imageSteps').value = '8';
    if (flux) { $('imageMode').value='edit'; $('imageSteps').value='16'; $('imageTransparent').checked=false; $('referenceResolution').value='1024'; if (['style','cutout'].includes($('editTemplate').value)) $('editTemplate').value=''; }
    $('imageTransparent').disabled = flux || submitting;
    $('referenceResolution').disabled = flux || submitting;
    $('qwenEditingSize').classList.toggle('hidden', flux);
    $('fluxEditingSize').classList.toggle('hidden', !flux);
    $('imageLoraSection').classList.toggle('hidden', flux);
    $('editStyleOption').disabled=flux; $('editCutoutOption').disabled=flux;
    $('referenceDropLabel').textContent = flux ? '① 上傳或拖入一張主圖' : '① 上傳或拖入圖片 · 最多 10 張';
    $('referenceHint').textContent = flux ? '本版 FLUX 3 使用單張主圖改圖，保留主圖比例並輸出約 1MP。多圖參考可切換至 Qwen；已匯入的參考圖會保留。' : '圖 1 是編輯主圖，決定輸出比例；其餘圖片可供角色、服裝或風格參考。可調整順序，調整後請同步修改描述中的圖號。';
    $('imageTransparentHint').textContent = flux ? 'FLUX 3 目前輸出一般 PNG，未提供透明背景與 LoRA。切回 Qwen 會恢復原有設定。' : '透明背景由模型生成，可能仍需清理邊緣。16 GB 顯卡建議從 1024、單張參考開始。';
    $('imageStepsHint').textContent = flux ? 'FLUX 3 固定使用 16 步，會自動整理修改與保留的內容。' : turbo ? '官方 Turbo 固定使用 8 步；切回原版會恢復你的原採樣步數。' : '原版預設 25 步供比較；官方完整品質範例使用 40 步。增加步數會延長生成時間。';
    $('imageLicenseText').textContent = flux ? 'FLUX 3 使用 Kommunity 授權，商用輸出須符合使用者資格及其他條件。' : 'Qwen-Image-2.1 限研究／評估；正式商業用途需另取得授權。';
    $('imageLicenseLink').href=modelLicenseUrls[selectedVariant];
    $('imageLicenseBadge').textContent=flux ? '商用有授權條件' : '研究／評估版';
    $('imageModelStatus').textContent = (turbo || flux) && (!modelVariantSupported || !profile) ? `${flux ? 'FLUX 3' : 'Turbo'} 需要新版 Studio；請重新啟動 Studio，再按「重新檢查」。回原版可使用既有圖片功能。` :
      selectedModelReady() ? `${modelLabels[selectedVariant]}已就緒${flux ? ' · 固定 16 步，約 1MP，保留主圖比例' : turbo ? ' · 固定 8 步，Turbo 已含加速' : ' · 可調整採樣步數及使用 LoRA'}` :
      `${modelLabels[selectedVariant]}尚未就緒。${profile?.error || engineError || '請確認已安裝此模型，再按「重新檢查」。'}`;
  }
  function selectModelVariant(value, steps) {
    const previous = selectedVariant;
    rememberQwenSettings();
    if (selectedVariant === 'standard') standardSteps = $('imageSteps').value || standardSteps;
    selectedVariant = variantOf(value);
    if (selectedVariant === 'flux3') {
      $('imageMode').value='edit'; $('imageTransparent').checked=false; $('referenceResolution').value='1024';
      if (['style','cutout'].includes($('editTemplate').value)) $('editTemplate').value='';
    } else if (previous === 'flux3') {
      $('imageMode').value=qwenSettings.mode; $('imageTransparent').checked=qwenSettings.transparent;
      $('referenceResolution').value=qwenSettings.referenceResolution; $('editTemplate').value=qwenSettings.editTemplate;
    }
    if (selectedVariant === 'standard' && steps !== undefined) standardSteps = String(steps || 25);
    $('imageSteps').value = selectedVariant === 'flux3' ? '16' : selectedVariant === 'turbo' ? '8' : standardSteps;
    renderLoras();
  }
  function applySourceModel(job) {
    const variant = sourceVariant(job);
    if (variant === 'standard') {
      selectedLora = job.lora_name || ''; $('imageLoraStrength').value = job.lora_strength ?? 1;
    }
    selectModelVariant(variant, variant === 'standard' ? job.steps || 25 : undefined);
  }
  function update() {
    updateModelControls();
    const edit = $('imageMode').value === 'edit';
    $('promptLabel').textContent = edit ? '③ 要修改與保留的內容' : '圖片描述';
    $('referenceSection').classList.toggle('hidden', !edit);
    $('editingSize').classList.toggle('hidden', !edit);
    $('generationSize').classList.toggle('hidden', edit);
    $('imageSeed').disabled = $('imageSeedAuto').checked;
    $('imageWidth').disabled = edit || submitting; $('imageHeight').disabled = edit || submitting;
    $('imageLora').disabled = selectedVariant !== 'standard' || submitting;
    $('imageLoraStrength').disabled = selectedVariant !== 'standard' || !selectedLora || !loraSupported || submitting;
    $('referenceFiles').disabled = uploading || submitting;
    $('referenceFiles').multiple = selectedVariant !== 'flux3';
    $('referenceStatus').textContent = edit && refs.length > referenceLimit() ? `目前保留 ${refs.length} 張參考圖；${selectedVariant === 'flux3' ? 'FLUX 3 本版只支援 1 張主圖，請移除多餘圖片，或切換 Qwen 使用多圖編輯。' : '最多可使用 10 張，請移除多餘圖片。'}` : '';
    $('generateImage').disabled = !selectedModelReady() || uploading || submitting || (edit && refs.length === 0) ||
      (edit && refs.length > referenceLimit()) ||
      (selectedVariant === 'standard' && Boolean(selectedLora) && (!loraSupported || !availableLoras.includes(selectedLora)));
  }
  function saveDraft() {
    rememberQwenSettings();
    const data = {refs};
    for (const id of fields) data[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
    data.imageLora = selectedLora;
    data.standardImageSteps = standardSteps;
    data.qwenModelSettings = {...qwenSettings};
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
    selectedVariant = variantOf(draft.imageModel);
    qwenSettings = normaliseQwenSettings(draft.qwenModelSettings || {mode:draft.imageMode,transparent:draft.imageTransparent,referenceResolution:draft.referenceResolution,editTemplate:draft.editTemplate});
    standardSteps = String(draft.standardImageSteps ?? (selectedVariant === 'standard' ? draft.imageSteps || 25 : 25));
    $('imageSteps').value = selectedVariant === 'flux3' ? '16' : selectedVariant === 'turbo' ? '8' : standardSteps;
    refs = Array.isArray(draft.refs) ? draft.refs.filter(r => /^[a-f0-9]{32}$/.test(r.id)).slice(0,10) : [];
  } catch {}
  for (const id of fields) $(id).addEventListener('input', () => {
    if (id === 'imageModel') selectModelVariant($('imageModel').value);
    if (id === 'imageSteps' && selectedVariant === 'standard') standardSteps = $('imageSteps').value;
    if (id === 'imageLora' && selectedVariant === 'standard') { selectedLora = $('imageLora').value; renderLoras(); }
    update(); saveDraft();
  });
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
    if (files.length + refs.length > referenceLimit()) { $('formMessage').textContent = selectedVariant === 'flux3' ? 'FLUX 3 本版只支援 1 張主圖；請先移除現有圖片，或切換 Qwen 使用多圖編輯。' : '最多只能使用 10 張參考圖片。'; return; }
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
    if (selectedVariant === 'flux3' && ['style','cutout'].includes(key)) { $('formMessage').textContent='FLUX 3 本版提供單張一般圖片編輯；多圖風格參考與透明去背請切換 Qwen。'; return; }
    if (key === 'style' && refs.length < 2) { $('formMessage').textContent='風格參考需要圖 1 主圖和圖 2 風格圖。'; return; }
    const combined = [$('imagePrompt').value.trim(),text].filter(Boolean).join('\n');
    if(combined.length > 8000) { $('formMessage').textContent='描述已超過 8,000 字元，請先精簡。'; return; }
    $('imagePrompt').value=combined;
    if(key === 'cutout') $('imageTransparent').checked=true;
    saveDraft(); $('imagePrompt').focus();
  });
  function renderLoras() {
    $('imageLora').innerHTML = '<option value="">不使用 LoRA</option>' + availableLoras.map(n=>`<option value="${escape(n)}">${escape(H3LoraHelp.label(H3LoraHelp.item(loraCatalog,n,{model:'qwen'})))}</option>`).join('') +
      (selectedLora && !availableLoras.includes(selectedLora) ? `<option value="${escape(selectedLora)}">未提供：${escape(selectedLora)}</option>` : '');
    $('imageLora').value=selectedLora;
    const turbo = selectedVariant === 'turbo', flux = selectedVariant === 'flux3';
    $('loraStatus').textContent = flux ? 'FLUX 3 本版未提供 LoRA；Qwen 的 LoRA 選擇與強度已保留。' : turbo ? 'Turbo 已含加速，回原版可使用 LoRA；原版的 LoRA 選擇與強度已保留。' : !loraSupported ? '此後端尚未提供 LoRA 支援；請更新並重啟 Studio／檢查引擎。' :
      selectedLora && !availableLoras.includes(selectedLora) ? '原本選擇的 LoRA 不在目前引擎，請重新選擇後生成。' :
      availableLoras.length ? `目前引擎提供 ${availableLoras.length} 個專用資料夾中的 LoRA；請確認作者標示的基礎模型與觸發詞。` : '目前引擎尚未安裝 Qwen-Image-2.1 專用 LoRA，可直接使用基礎模型。';
    $('imageLoraHelp').innerHTML = selectedLora ? H3LoraHelp.html(H3LoraHelp.item(loraCatalog,selectedLora,{model:'qwen'})) : '';
    $('imageLoraCatalog').innerHTML = loraCatalog.items.length ? `<details class="lora-catalog"><summary>查看全部 ${loraCatalog.items.length} 個圖片 LoRA 的用途</summary><div class="lora-catalog-list">${loraCatalog.items.map(item=>H3LoraHelp.html(item)).join('')}</div></details>` : '';
    $('imageLoraHelp').classList.toggle('hidden', turbo || flux); $('imageLoraCatalog').classList.toggle('hidden', turbo || flux);
    update();
  }
  async function checkStatus() {
    $('refreshStatus').disabled = true;
    try {
      const result = await api('/api/images/status'); ready = result.ready === true; engineError = result.error || '';
      modelVariantSupported = result.model_variant_supported === true;
      modelVariants = Array.isArray(result.variants) ? result.variants.filter(item => item && ['standard','turbo','flux3'].includes(item.id)) : [];
      loraSupported = result.lora_supported === true; loraCatalog = H3LoraHelp.catalog(result,{model:'qwen'}); availableLoras = loraCatalog.names; renderLoras();
      $('engineStatus').textContent = ready ? '圖片引擎已有可用模型' : '圖片引擎尚未就緒';
      const available = modelVariants.filter(item => item.ready === true).map(item => modelLabels[item.id]);
      $('engineDetail').textContent = result.error || `${result.mode === 'remote' ? '共用遠端' : '本機'}引擎 · ${modelVariantSupported ? available.join('／') || '模型尚未就緒' : '原版官方 INT8 模型'} · 圖片與影片依佇列順序處理`;
    } catch (error) { ready = false; engineError = error.message; $('engineStatus').textContent = '暫時無法檢查引擎'; $('engineDetail').textContent = error.message; }
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
      const title = escape(job.name), id = escape(job.id), model = escape(jobModelLabel(job));
      return `<article class="image-card" data-job-id="${id}">${done ? `<a class="image-preview-link" href="/api/images/jobs/${id}/image" target="_blank" rel="noopener" aria-label="查看 ${title} 的大圖" title="點擊查看大圖"><img class="image-preview" loading="lazy" src="/api/images/jobs/${id}/image" alt="${title}"></a>` : ''}<div class="image-card-body"><h3 title="${title}">${title}</h3><p>${escape(jobStatus(job))}</p><p class="image-hint">${model}</p>${active && queue?.jobs?.[job.id]?.phase === 'engine_running' ? `<progress max="100" value="${Math.min(100,Math.max(0,Number(job.progress)||0))}" aria-label="處理進度"></progress>` : ''}${job.error ? `<p class="image-error">${escape(job.error)}</p>` : ''}${active ? `<button type="button" class="button ghost small" data-cancel="${id}">取消</button>` : ''}${done ? `<p class="image-size">${job.width} × ${job.height}</p>` : ''}<details><summary>詳細資料與操作</summary><p>模型：${model}</p><p>${job.mode === 'upscale' ? `${job.scale} 倍增強放大` : `${job.steps} 步 · Seed ${job.seed}`}${done && job.rgba ? ' · RGBA' : ''}</p><p>${escape(job.original_prompt || job.prompt)}</p><p>${job.lora_name ? `LoRA：${escape(job.lora_name)} · 強度 ${escape(job.lora_strength)}` : '未使用 LoRA'}</p><div class="image-card-actions">${done ? `<a class="button ghost small" href="/api/images/jobs/${id}/image?download=1">下載 PNG</a><button type="button" class="button ghost small" data-continue="${id}">以此圖繼續編輯</button><button type="button" class="button ghost small" data-upscale="${id}">增強放大</button><button type="button" class="button ghost small" data-reference="${id}">新增為輔助參考</button>${['edit','upscale'].includes(job.mode) && job.image_asset_ids?.length ? `<button type="button" class="button ghost small" data-compare="${id}">原圖／結果比較</button>` : ''}` : ''}${job.mode !== 'upscale' ? `<button type="button" class="button ghost small" data-reuse="${id}">套用設定</button>` : ''}${['completed','failed','cancelled','interrupted'].includes(job.status) ? `<button type="button" class="button danger small" data-delete="${id}">刪除圖片</button>` : ''}</div></details></div></article>`;
    }).join('') : '<p class="image-empty">第一張圖片，從左側描述開始。<br>生成結果會保留在這裡，也能作為下一張的參考圖。</p>';
    if (markup !== lastJobsMarkup) {
      const expanded = new Set([...$('imageJobs').querySelectorAll('article[data-job-id]')].filter(e=>e.querySelector('details')?.open).map(e=>e.dataset.jobId));
      $('imageJobs').innerHTML = markup; lastJobsMarkup = markup;
      for(const card of $('imageJobs').querySelectorAll('article[data-job-id]')) if(expanded.has(card.dataset.jobId)) card.querySelector('details').open=true;
    }
    $('pageLabel').textContent = `第 ${page} / ${totalPages} 頁`;
    $('previousPage').disabled = page <= 1; $('nextPage').disabled = page >= totalPages;
  }
  let pendingFocusedJob = typeof location === 'undefined' ? '' : new URLSearchParams(location.search).get('job') || '';
  async function refresh() {
    if (polling) return;
    polling = true;
    const results = await Promise.allSettled([api(`/api/images/jobs?page=${page}${pendingFocusedJob ? `&focus=${encodeURIComponent(pendingFocusedJob)}` : ""}`),api('/api/queue')]);
    if (results[0].status === 'fulfilled') { const data = results[0].value; jobs = data.items.filter(job=>!deletedIds.has(job.id)); page = data.page; totalPages = data.total_pages; }
    else $('imageQueue').textContent = results[0].reason.message;
    queue = results[1].status === 'fulfilled' ? results[1].value : null;
    if (results[0].status === 'fulfilled') $('imageQueue').textContent = queue?.available ? `共享引擎：執行 ${queue.running_count} 筆 · 排隊 ${queue.pending_count} 筆 · 本機待送出 ${queue.local_waiting_count} 筆` : '暫時無法取得共享引擎佇列，工作仍可能進行中。';
    renderJobs(); polling = false;
    if (pendingFocusedJob && results[0].status === 'fulfilled') {
      const id = pendingFocusedJob; pendingFocusedJob = '';
      const card = [...$('imageJobs').querySelectorAll('[data-job-id]')].find(node => node.dataset.jobId === id);
      if (card) { const details = card.querySelector('details'); if (details) details.open = true; card.scrollIntoView({block:'center',behavior:'smooth'}); }
    }
  }
  $('imageForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || uploading) return;
    if (!selectedModelReady()) { $('formMessage').textContent = $('imageModelStatus').textContent; return; }
    if ($('generateImage').disabled) return;
    const turbo = selectedVariant === 'turbo', flux = selectedVariant === 'flux3';
    const payload = {name:$('imageName').value, mode:flux ? 'edit' : $('imageMode').value, ...(modelVariantSupported ? {model_variant:selectedVariant} : {}), prompt:$('imagePrompt').value,
      width:flux ? 1024 : Number($('imageWidth').value),height:flux ? 1024 : Number($('imageHeight').value),steps:flux ? 16 : turbo ? 8 : Number($('imageSteps').value),
      seed:Number($('imageSeed').value),seed_auto:$('imageSeedAuto').checked,transparent:flux ? false : $('imageTransparent').checked,
      lora_name:turbo || flux ? '' : selectedLora,lora_strength:turbo || flux ? 0 : Number($('imageLoraStrength').value),
      reference_resolution:flux ? 1024 : Number($('referenceResolution').value),image_asset_ids:flux || $('imageMode').value === 'edit' ? refs.map(r=>r.id) : []};
    submitting = true; update();
    try {
      const job = await api('/api/images/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      $('imageSeed').value = job.seed; saveDraft(); page = 1;
      $('formMessage').textContent = `${modelLabels[selectedVariant]}圖片工作已加入佇列 · Seed ${job.seed}`;
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
      if (button.dataset.upscale) {
        const job=jobs.find(j=>j.id === button.dataset.upscale); if(!job) return;
        pendingUpscale=job; upscaleReady=false; upscaleModels=[];
        $('upscaleScale').value='2'; $('upscaleModel').value='general';
        $('upscaleSource').textContent=job.name; $('upscaleError').textContent='';
        $('upscaleStatus').textContent='正在檢查目前引擎的放大模型…';
        updateUpscale(); $('upscaleDialog').showModal();
        try {
          const status=await api('/api/images/upscale/status');
          upscaleReady=status.ready === true; upscaleModels=status.models || [];
          const first=upscaleModels.find(m=>m.installed); if(first) $('upscaleModel').value=first.id;
          $('upscaleStatus').textContent=status.error || 'Real-ESRGAN 放大模型已就緒';
        } catch(error) { $('upscaleStatus').textContent=error.message; }
        updateUpscale(); return;
      }
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
        // Polling can replace the visible jobs while the reference is imported.
        const currentSource=jobs.find(j=>j.id === button.dataset.continue), source=currentSource ? {...currentSource} : null;
        const asset=await api(`/api/images/jobs/${button.dataset.continue}/reference`,{method:'POST'});
        refs=[asset];
        if(source) {
          applySourceModel(source); $('editTemplate').value='';
          $('imageName').value=source.name.slice(0,74)+' · 編輯';
          $('imageTransparent').checked=selectedVariant !== 'flux3' && source.transparent === true;
          $('referenceResolution').value=selectedVariant === 'flux3' ? 1024 : source.reference_resolution || 1024;
        }
        $('imageMode').value='edit'; $('imagePrompt').value=''; $('imageSeedAuto').checked=true;
        renderRefs(); $('imagePrompt').focus();
        $('formMessage').textContent=`已將此結果設為唯一主圖，使用${modelLabels[selectedVariant]}；請描述下一步要修改的內容。`;
      }
      if (button.dataset.reference) {
        uploading=true; update();
        if (refs.length >= referenceLimit()) throw new Error(selectedVariant === 'flux3' ? 'FLUX 3 本版只支援 1 張主圖；可用「以此圖繼續編輯」替換主圖，或切換 Qwen 使用多圖編輯。' : '參考圖已達 10 張，請先移除一張。');
        refs.push(await api(`/api/images/jobs/${button.dataset.reference}/reference`,{method:'POST'}));
        $('imageMode').value = 'edit'; renderRefs(); $('imagePrompt').focus();
      }
      if (button.dataset.reuse) {
        const job = jobs.find(j=>j.id === button.dataset.reuse);
        if (!job || job.mode === 'upscale') return;
        applySourceModel(job);
        $('imageMode').value = selectedVariant === 'flux3' ? 'edit' : job.mode; $('imageName').value = job.name; $('imagePrompt').value = job.original_prompt || job.prompt;
        if (selectedVariant !== 'flux3') { $('imageWidth').value = job.width; $('imageHeight').value = job.height; }
        $('imageSeed').value = job.seed; $('imageSeedAuto').checked = false; $('imageTransparent').checked = selectedVariant !== 'flux3' && job.transparent === true;
        $('referenceResolution').value = selectedVariant === 'flux3' ? 1024 : job.reference_resolution || 1024; refs = (job.image_asset_ids || []).map(id=>({id})); renderRefs();
        $('imagePrompt').focus(); $('formMessage').textContent = `已套用${modelLabels[selectedVariant]}原設定及固定 Seed；按生成才會建立新工作。`;
      }
    } catch(error) { $('formMessage').textContent = error.message; }
    finally { uploading=false; update(); button.disabled = false; }
  });
  function updateUpscale() {
    const job=pendingUpscale, scale=Number($('upscaleScale').value);
    const fits=job && job.width*job.height <= 4194304 && Math.max(job.width,job.height)*scale <= 4096;
    const installed=upscaleModels.some(m=>m.id === $('upscaleModel').value && m.installed);
    $('upscaleSize').textContent=job ? `${job.width} × ${job.height} → ${job.width*scale} × ${job.height*scale}${fits ? '' : ' · 超出尺寸上限，請降低倍率或選擇較小來源'}` : '';
    $('submitUpscale').disabled=!upscaleReady || !fits || !installed || submitting;
    if(upscaleReady) $('upscaleStatus').textContent=installed ? 'Real-ESRGAN 放大模型已就緒' : '此模型尚未安裝在目前引擎，請主機執行 setup_image_upscale.bat。';
  }
  $('upscaleScale').addEventListener('input',updateUpscale); $('upscaleModel').addEventListener('input',updateUpscale);
  $('cancelUpscale').addEventListener('click',()=>{if(!submitting) {pendingUpscale=null; $('upscaleDialog').close();}});
  $('upscaleDialog').addEventListener('cancel',event=>{if(submitting) event.preventDefault(); else pendingUpscale=null;});
  $('submitUpscale').addEventListener('click',async()=>{
    if(!pendingUpscale || $('submitUpscale').disabled || submitting || uploading) return;
    const source=pendingUpscale;
    submitting=true; update(); updateUpscale(); $('cancelUpscale').disabled=true;
    try {
      await api(`/api/images/jobs/${source.id}/upscale`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scale:Number($('upscaleScale').value),model:$('upscaleModel').value})});
      pendingUpscale=null; $('upscaleDialog').close(); page=1;
      $('formMessage').textContent='已加入增強放大佇列，完成後會另存新圖片。'; await refresh();
    } catch(error) { $('upscaleError').textContent=error.message; }
    finally {submitting=false; update(); updateUpscale(); $('cancelUpscale').disabled=false;}
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
