(function (root) {
  'use strict';
  const DRAFT_KEY = 'h3studio-effects-draft-v1';
  const fields = {
    source_mode:'effectSourceMode',kind:'effectKind',playback:'effectPlayback',name:'effectName',prompt:'effectPrompt',
    color:'effectColor',secondary_color:'effectSecondaryColor',style:'effectStyle',strength:'effectStrength',speed:'effectSpeed',direction:'effectDirection',
    video_model:'effectVideoModel',freevideo_quality:'effectFreevideoQuality',source_duration:'effectSourceDuration',seed:'effectSeed',duration:'effectDuration',timing_mode:'effectTimingMode',trim_start:'effectTrimStart',
    fps:'effectFps',anchor:'effectAnchor',width:'effectWidth',height:'effectHeight',black_level:'effectBlackLevel',softness:'effectSoftness',
    temporal_smoothing:'effectTemporalSmoothing',loop_blend:'effectLoopBlend',
  };
  const numberFields = new Set(['strength','source_duration','seed','duration','trim_start','fps','width','height','black_level','softness','temporal_smoothing','loop_blend']);
  const retryFields={playback:'retryEffectPlayback',anchor:'retryEffectAnchor',timing_mode:'retryEffectTimingMode',trim_start:'retryEffectTrimStart',duration:'retryEffectDuration',fps:'retryEffectFps',loop_blend:'retryEffectLoopBlend',width:'retryEffectWidth',height:'retryEffectHeight',black_level:'retryEffectBlackLevel',softness:'retryEffectSoftness',temporal_smoothing:'retryEffectTemporalSmoothing'};
  const defaults = {source_mode:'text',kind:'flame',playback:'loop',name:'',prompt:'',color:'#ff8a22',secondary_color:'#fff2a8',style:'game_cg',strength:3,speed:'normal',direction:'up',video_model:'h3',freevideo_quality:'medium',source_duration:5,seed:1,duration:4,timing_mode:'trim',trim_start:0,fps:24,anchor:'bottom_center',width:640,height:640,black_level:.015,softness:.08,temporal_smoothing:.15,loop_blend:0};
  const presets = {
    flame:{label:'火焰',description:'底部發射點固定，保持向上的流動與柔光。',defaults:{playback:'loop',direction:'up',anchor:'bottom_center'}},
    lightning:{label:'閃電',description:'單次放電保留分枝與微光，也可使用脈衝循環。',defaults:{playback:'once',direction:'down',anchor:'center'}},
    explosion:{label:'爆炸',description:'完整保留蓄力、爆發與消散，不使用倒放循環。',defaults:{playback:'once',direction:'outward',anchor:'center'}},
    smoke:{label:'煙塵',description:'以灰白或褐色煙保留柔軟透明邊緣；純黑煙需獨立遮罩。',defaults:{playback:'once',direction:'up',anchor:'bottom_center'}},
    glow:{label:'光效',description:'中心固定的光暈、掃光與呼吸變化，保留弱光。',defaults:{playback:'loop',direction:'static',anchor:'center'}},
    particles:{label:'火花／粒子',description:'固定發射點，保留細小粒子與自然拖尾。',defaults:{playback:'once',direction:'outward',anchor:'center'}},
  };
  const labels = {queued:'排隊中',generating:'生成原片',preparing:'準備素材',matting:'透明處理',looping:'循環整理',exporting:'匯出序列',checking:'品質檢查',completed:'製作完成',needs_adjustment:'需要調整',failed:'處理失敗',cancelled:'已取消',interrupted:'工作中斷'};
  const active = status => ['queued','generating','preparing','matting','looping','exporting','checking','running'].includes(status);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeId = value => /^[a-f0-9]{32}$/.test(String(value || ''));
  const displayNumber = value => Number.isFinite(Number(value))&&value!==null&&value!==undefined?String(Number(Number(value).toFixed(3))):'待確認';
  function timingSummary(spec,sourceDuration,estimated=false) {
    if(spec.timing_mode!=='retime') return `時長處理：原速擷取 · 成品：從 ${displayNumber(spec.trim_start||0)} 秒起取用 ${displayNumber(spec.duration)} 秒 · 原速 1×`;
    const source=Number(sourceDuration),target=Number(spec.duration),ratio=source/target;
    if(!(source>0&&target>0)) return `時長處理：完整原片變速 · 使用完整原片 → 成品 ${displayNumber(spec.duration)} 秒；原片長度與變速倍率將於檢查時確認。`;
    const speed=ratio>1.001?'加速':ratio<.999?'慢放':'等速';
    return `時長處理：完整原片變速 · 完整原片 ${displayNumber(source)} 秒 → 成品 ${displayNumber(target)} 秒，${estimated?'預估':''}${speed} ${Number(ratio.toFixed(2))}×`;
  }
  function postprocessSummary(spec) {
    const playback={once:'單次播放',loop:'持續循環',pulse:'脈衝循環'}[spec.playback]||spec.playback;
    const anchor={center:'畫面中央',bottom_center:'底部中央'}[spec.anchor]||spec.anchor;
    return `特效類型：${presets[spec.kind]?.label||spec.kind}\n播放方式：${playback} · 固定點：${anchor}\n黑底清理：${displayNumber(spec.black_level)} · 透明柔邊：${displayNumber(spec.softness)} · 時序平滑：${displayNumber(spec.temporal_smoothing)} · 循環銜接：${displayNumber(spec.loop_blend)} 秒`;
  }
  function freevideoRecipeText(quality) {
    const label=({light:'FreeVideo Light · 快速預覽 · 兩階段 8＋3 步',medium:'FreeVideo Medium · 標準 · 兩階段 8＋3 步',high:'FreeVideo High · 精細 · 單階段 16 步',max:'FreeVideo Max · 極致 · 單階段 20 步'})[quality]||'請選擇 FreeVideo 品質。';
    return label+(['light','medium'].includes(quality)?'；小尺寸時 Light／Medium 可能使用相同計畫，依作者尺寸保護規則調整，不保證品質或速度差異。':'');
  }
  function fileUrl(id, file) {
    if (!safeId(id) || !/^[A-Za-z0-9_.-]+$/.test(String(file?.name || ''))) return '';
    return `/api/effects/jobs/${id}/files/${encodeURIComponent(file.name)}`;
  }
  function install({document:doc,window:win,storage,fetch:fetcher,setTimeout:delay=win.setTimeout.bind(win),clearTimeout:cancelDelay=win.clearTimeout.bind(win),setInterval:interval=win.setInterval.bind(win)} = {}) {
    const $ = id => doc.getElementById(id);
    const state = {assets:{image:null,video:null},catalog:[],status:null,jobs:[],preview:null,previewSignature:null,previewBusy:false,submitting:false,uploading:false,uploadSerial:0,statusPromise:null,jobsPromise:null,jobsPending:false,selectedPreviews:new Map(),lastMarkup:'',actionJobs:new Set(),retryJob:null,retryBusy:false,timingMode:'trim',trimStartDraft:'0',retryTimingMode:'trim',retryTrimStartDraft:'0'};
    function message(value,error=false) { $('effectFormMessage').textContent=value; $('effectFormMessage').classList.toggle('error',error); }
    function readSpec() {
      const result={};
      for(const [key,id] of Object.entries(fields)) result[key]=numberFields.has(key)?Number($(id).value):$(id).value;
      if(result.timing_mode==='retime')result.trim_start=0;
      const asset=state.assets[result.source_mode];
      if(asset) result.source_asset_id=asset.id;
      return result;
    }
    function signature() { return JSON.stringify(readSpec()); }
    function writeSpec(value) { for(const [key,id] of Object.entries(fields)) if(Object.hasOwn(value,key)) $(id).value=String(value[key]); }
    function syncTimingInputs(retry=false) {
      const mode=$(retry?'retryEffectTimingMode':'effectTimingMode').value,trim=$(retry?'retryEffectTrimStart':'effectTrimStart');
      const modeKey=retry?'retryTimingMode':'timingMode',draftKey=retry?'retryTrimStartDraft':'trimStartDraft';
      if(mode==='retime') {if(state[modeKey]!=='retime')state[draftKey]=trim.value;trim.value='0';}
      else {if(state[modeKey]==='retime')trim.value=state[draftKey];state[draftKey]=trim.value;}
      state[modeKey]=mode;trim.disabled=mode==='retime'||(retry&&state.retryBusy);
    }
    function persist() {
      syncTimingInputs();
      try { storage.setItem(DRAFT_KEY,JSON.stringify({spec:readSpec(),trim_start_draft:state.trimStartDraft,assets:state.assets})); }
      catch { message('瀏覽器無法保存草稿；目前欄位仍保留，請勿關閉這個分頁。',true); }
    }
    function invalidateReview() { if(state.preview && signature()!==state.previewSignature) $('effectReviewStatus').textContent='設定或素材已變更，請關閉後重新檢查。'; updateControls(); }
    function sourceAsset() {return state.assets[$('effectSourceMode').value];}
    function needsOriginalChoice(packet=state.preview) {return ['unavailable','failed'].includes((packet?.h3_translation || packet?.generation?.h3_translation)?.status);}
    function currentReview() {
      const expires=state.preview?.review_expires_at;
      return Boolean(state.preview?.review_receipt && state.previewSignature===signature() && (!expires || Date.parse(expires)>Date.now()));
    }
    function selectedReady() {
      if($('effectSourceMode').value==='video') return state.status?.import_ready !== false;
      const profiles=state.status?.models || state.status?.video_models || [];
      const model=profiles.find(item=>item.id===$('effectVideoModel').value);
      const mode=$('effectSourceMode').value==='image'?'fl2va':'t2v';
      return state.status?.ready===true && model?.ready===true && (!model.mode_ready || model.mode_ready[mode]!==false);
    }
    function localError() {
      const spec=readSpec();
      if(spec.source_mode!=='text' && !sourceAsset()) return spec.source_mode==='video'?'請先上傳來源影片。':'請先上傳參考圖片。';
      if(!Number.isFinite(spec.duration)||spec.duration<.25||spec.duration>10) return '成品長度需介於 0.25～10 秒。';
      if(![12,24,30].includes(spec.fps)) return '請選擇支援的輸出 FPS。';
      if(![spec.width,spec.height].every(value=>Number.isInteger(value)&&value>=256&&value<=1536&&value%32===0)) return '畫布寬高需為 256～1536，且為 32 的倍數。';
      if(Math.round(spec.duration*spec.fps)>300||spec.width*spec.height*Math.round(spec.duration*spec.fps)>120000000) return '序列總量過大，請降低畫布尺寸、FPS 或成品長度。';
      if(!Number.isFinite(spec.trim_start)||spec.trim_start<0) return '取用起點不能小於 0。';
      if(spec.source_mode!=='video' && (spec.source_duration<5||spec.source_duration>15)) return '生成原片長度需介於 5～15 秒。';
      if(spec.timing_mode!=='retime' && spec.source_mode!=='video' && spec.trim_start+spec.duration>spec.source_duration+.001) return '取用區間超過生成原片長度，請調整起點、成品長度或原片長度。';
      if(spec.playback==='once' && spec.loop_blend!==0) return '單次播放不使用接縫混合，請將循環銜接設為 0。';
      return '';
    }
    function updateControls() {
      syncTimingInputs();
      const mode=$('effectSourceMode').value, asset=sourceAsset(), busy=state.submitting||state.uploading;
      $('effectSourceSection').classList.toggle('hidden',mode==='text');
      $('effectGenerationFields').classList.toggle('hidden',mode==='video');
      const freevideo=mode!=='video'&&$('effectVideoModel').value==='freevideo_vdn_h3';
      $('effectFreevideoQualityWrap').classList.toggle('hidden',!freevideo);
      $('effectFreevideoQuality').disabled=!freevideo||busy;
      $('effectFreevideoQualityHint').textContent=freevideoRecipeText($('effectFreevideoQuality').value);
      $('effectImportedNotice').classList.toggle('hidden',mode!=='video');
      for(const key of ['prompt','color','secondary_color','style','strength','speed','direction'])$(fields[key]).disabled=mode==='video';
      $('effectPresetHelp').textContent=(mode==='video'?'匯入影片只更新播放方式與固定點；原片色彩與方向保持原樣，整體快慢請使用時長處理。':'只更新播放方式、方向與固定點；保留來源、描述、色彩及進階設定。')+'混有煙塵請以透明版預覽；獨立煙塵／發光分層需分別製作。';
      $('effectFixedRules').textContent=mode==='video'?'沿用原片動作順序與鏡頭，只做黑底透明處理、時長處理、循環整理及固定畫布輸出，成品靜音。原片內容、色彩與動作不會重新生成。':'純黑背景要求、單一連續鏡頭、固定相機、無人物／字幕／地板、輸出靜音。固定規則會編成英文稿件，確認完整生成稿後才送出；生成後仍檢查背景、裁邊與循環接縫。';
      $('effectUploadLabel').textContent=mode==='video'?'上傳或拖入來源影片':'上傳或拖入參考圖片';
      $('effectSourceFile').accept=mode==='video'?'video/mp4,video/quicktime,video/webm,video/x-matroska':'image/png,image/jpeg,image/webp,image/bmp';
      $('effectSourceStatus').textContent=asset?`${asset.name || '來源素材'} · 已匯入，原始素材保留`:'尚未匯入素材。';
      $('removeEffectSource').disabled=!asset||busy;
      $('effectSourceFile').disabled=busy||mode==='text';
      $('previewEffect').disabled=busy||state.previewBusy||Boolean(localError())||!selectedReady();
      $('effectValidation').textContent=localError();
      $('previewEffect').textContent=state.previewBusy?'正在整理實際稿件…':mode==='video'?'檢查去背與輸出設定 →':'檢查稿件與輸出設定 →';
      $('confirmEffect').disabled=busy||!currentReview()||(needsOriginalChoice()&&!$('effectOriginalConfirmed').checked);
      $('confirmEffect').textContent=state.submitting?'正在建立工作…':needsOriginalChoice()?'使用原文稿並加入工作':'確認並加入工作';
      for(const id of ['cancelEffectReview','closeEffectReview']) $(id).disabled=state.submitting;
      const frames=Math.round(Number($('effectDuration').value)*Number($('effectFps').value));
      const spec=readSpec(),packet=state.preview;
      const reviewedDuration=mode==='video'?(packet?.spec?.source_asset_id===asset?.id?packet?.source_info?.duration:null):(packet?.spec?.source_duration===spec.source_duration&&packet?.spec?.video_model===spec.video_model?packet?.generation?.duration:null);
      $('effectOutputSummary').textContent=`${$('effectWidth').value} × ${$('effectHeight').value} · ${$('effectFps').value} FPS · 約 ${Number.isFinite(frames)?frames:'—'} 張 PNG · 所有畫格共用固定點\n${timingSummary(spec,reviewedDuration??(mode==='video'?null:spec.source_duration),!reviewedDuration&&mode!=='video')}`;
      $('effectTimingHint').textContent=spec.timing_mode==='retime'?'使用完整原片等比例變速，不會裁掉後半段；成品秒數可比原片短或長。這是後製變速，不會提高模型生成速度。':'以原速擷取指定起點與長度；若要把完整 5 秒縮成 2 秒，請選「完整原片變速」。';
      const preset=state.catalog.find(item=>item.id===$('effectKind').value)||presets[$('effectKind').value];
      $('effectPresetHint').textContent=preset?.description||'';
      const model=(state.status?.models||state.status?.video_models||[]).find(item=>item.id===$('effectVideoModel').value);
      $('effectModelHint').textContent=mode==='video'?'匯入影片只執行後製，不需要啟動影片生成。':model?.ready?freevideo?'FreeVideo 自動管理顯存；依所選品質生成原片，再執行黑底透明處理與序列匯出。':'模型可用；完整稿件確認後才會加入生成佇列。':model?.error||'影片模型尚未確認可用，請重新檢查。';
    }
    async function api(url,options={}) {
      const {readTimeoutMs=15000,...requestOptions}=options;
      const read=['GET','HEAD'].includes(String(requestOptions.method||'GET').toUpperCase());
      const controller=read&&!requestOptions.signal?new AbortController():null;
      const timer=controller?delay(()=>controller.abort(),readTimeoutMs):null;
      try {
        const response=await fetcher(url,{...requestOptions,...(read?{cache:'no-store'}:{}),...(controller?{signal:controller.signal}:{})});
        let data;
        try {data=await response.json();} catch(error) {throw new Error(controller?.signal.aborted?'狀態讀取逾時，稍後會自動重試。':'主機回應格式不完整，請確認已重新啟動新版 Studio。');}
        if(!response.ok) {const error=new Error(data.error||`Studio 請求失敗（${response.status}）`);error.status=response.status;throw error;}
        return data;
      } catch(error) {if(controller?.signal.aborted) throw new Error('狀態讀取逾時，稍後會自動重試。');throw error;}
      finally {if(timer!==null) cancelDelay(timer);}
    }
    const json = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    async function loadCatalog() {
      try {const data=await api('/api/effects/catalog');if(Array.isArray(data.items)) state.catalog=data.items;updateControls();}
      catch(error) {message(`特效設定暫時無法讀取：${error.message}`,true);}
    }
    function checkStatus() {
      if(state.statusPromise) return state.statusPromise;
      $('refreshEffectStatus').disabled=true;
      state.statusPromise=(async()=>{
        try {
          const data=await api('/api/effects/status',{readTimeoutMs:30000});
          if(typeof data.ready!=='boolean') throw new Error('模型狀態回應不完整。');
          state.status=data;
          $('effectEngineStatus').textContent=data.ready?'特效工作室已連線':'影片模型尚未就緒 · 可匯入影片後製';
          $('effectEngineDetail').textContent=data.error||'黑底生成 → 透明處理 → 循環檢查 → 固定畫布 PNG 序列';
        } catch(error) {
          state.status={...(state.status||{}),ready:false};
          $('effectEngineStatus').textContent='暫時無法檢查模型 · 正在重新連線';
          $('effectEngineDetail').textContent=`${error.message} 已填寫的設定與來源素材保留。`;
        } finally {state.statusPromise=null;$('refreshEffectStatus').disabled=false;updateControls();}
      })();return state.statusPromise;
    }
    function jobFiles(job) {
      const files=(Array.isArray(job.files)?job.files:[]).map(file=>({...file,url:fileUrl(job.id,file)})).filter(file=>file.url);
      const revision=Math.max(0,...files.map(file=>Number(/^r(\d+)_/.exec(file.name)?.[1]||0)));
      return files.filter(file=>!/^r\d+_/.test(file.name)||Number(/^r(\d+)_/.exec(file.name)[1])===revision);
    }
    function previewFiles(job) {return jobFiles(job).filter(file=>file.kind==='preview'||/\.mp4$/i.test(file.name));}
    function renderJobs() {
      const markup=state.jobs.length?state.jobs.map(job=>{
        const id=escape(job.id), result=job.result||{}, spec=job.spec||{}, qa=job.qa||result.qa||{}, files=jobFiles(job), previews=previewFiles(job);
        const selected=state.selectedPreviews.get(job.id), current=previews.find(file=>file.name===selected)||previews.find(file=>/checker|棋盤/i.test(file.name+' '+file.label))||previews[0];
        const warning=Array.isArray(qa.warnings)?qa.warnings.join('；'):'';
        const phase=labels[job.status]||labels[job.stage]||job.current_node||job.status;
        const progress=Math.min(100,Math.max(0,Number(job.progress)||0));
        const loop=['loop','pulse'].includes(spec.playback);
        return `<article class="effects-card" data-effect-job="${id}"><div class="effects-card-heading"><div><h3>${escape(job.name||spec.name||'未命名特效')}</h3><p>${escape(presets[spec.kind]?.label||spec.kind||'特效')} · ${escape({once:'單次播放',loop:'持續循環',pulse:'脈衝循環'}[spec.playback]||'')} ${result.frame_count?`· ${escape(result.frame_count)} 張 · ${escape(result.fps)} FPS`:''}</p></div><span class="effects-phase${job.status==='needs_adjustment'?' qa':''}">${escape(phase)}</span></div>${active(job.status)?`<progress max="100" value="${progress}" aria-label="${escape(phase)}進度"></progress><p>${escape(job.current_node||job.stage||'等待目前階段完成')} · ${Math.round(progress)}%</p>`:''}${job.error?`<p class="effects-warning">${escape(job.error)}</p>`:''}${warning?`<p class="effects-warning">${escape(warning)}</p>`:''}${current?`<video class="effects-preview" controls preload="metadata" playsinline ${loop?'loop':''} src="${escape(current.url)}" data-effect-video="${id}"></video><div class="effects-preview-tabs">${previews.map(file=>`<button class="button ghost small" type="button" data-preview-job="${id}" data-preview-file="${escape(file.name)}" aria-pressed="${file.name===current.name}">${escape(file.label||file.name)}</button>`).join('')}</div><p>預覽 MP4 為合成檢查畫面；透明素材請下載 PNG 序列。${loop?'循環請至少連播三次，檢查亮度、位置與透明邊緣。':''}</p>`:''}<div class="effects-card-actions">${active(job.status)?`<button class="button danger small" type="button" data-cancel-effect="${id}" ${state.actionJobs.has(job.id)?'disabled':''}>取消這筆工作</button>`:''}${!active(job.status)?`<button class="button ${job.status==='needs_adjustment'?'primary':'ghost'} small" type="button" data-adjust-effect="${id}" ${state.actionJobs.has(job.id)?'disabled':''}>調整後製</button>`:''}${!active(job.status)&&['completed','needs_adjustment','failed','interrupted','cancelled'].includes(job.status)?`<button class="button ghost small" type="button" data-retry-effect="${id}" ${state.actionJobs.has(job.id)?'disabled':''}>只重做後製</button>`:''}${files.filter(file=>!['preview','image'].includes(file.kind)).map(file=>`<a class="button ghost small" href="${escape(file.url)}" download>${escape(file.label||file.name)}</a>`).join('')}</div><details><summary>製作規格與檢查資訊</summary><p>透明度由黑底來源推估；請在不同背景下檢查。${result.alpha_mode?' · Alpha：'+escape(result.alpha_mode):''}${result.blend_mode?' · 混合方式：'+escape(result.blend_mode):''}</p><pre>${escape(JSON.stringify({spec,qa},null,2))}</pre>${files.filter(file=>file.kind==='image').map(file=>`<a class="button ghost small" href="${escape(file.url)}" target="_blank" rel="noopener">${escape(file.label||'查看檢查圖')}</a>`).join('')}</details></article>`;
      }).join(''):'<p class="effects-empty">從一個特效開始。原始影片與後製結果會分別保留。</p>';
      if(markup!==state.lastMarkup){
        const playback=new Map([...( $('effectJobs').querySelectorAll?.('video[data-effect-video]')||[])].map(video=>[video.dataset.effectVideo,{src:video.src,time:video.currentTime,paused:video.paused,rate:video.playbackRate}]));
        $('effectJobs').innerHTML=markup;state.lastMarkup=markup;
        for(const video of $('effectJobs').querySelectorAll?.('video[data-effect-video]')||[]){
          const previous=playback.get(video.dataset.effectVideo);if(!previous||previous.src!==video.src)continue;
          const restore=()=>{try{video.currentTime=previous.time;video.playbackRate=previous.rate;if(!previous.paused)video.play()?.catch(()=>{});}catch{}};
          if(video.readyState)restore();else video.addEventListener('loadedmetadata',restore,{once:true});
        }
      }
    }
    function loadJobs(force=false) {
      if(state.jobsPromise){if(force===true)state.jobsPending=true;return state.jobsPromise;}
      state.jobsPromise=(async()=>{
        try {do {
          state.jobsPending=false;
          try {const data=await api('/api/effects/jobs');if(!Array.isArray(data.items)) throw new Error('特效工作清單回應不完整。');state.jobs=data.items;$('effectJobsStatus').textContent=`共 ${state.jobs.length} 筆工作 · 原始影片與製作設定保存在 Studio 主機`;renderJobs();}
          catch(error){$('effectJobsStatus').textContent=`工作清單暫時無法更新：${error.message} 上次結果已保留。`;}
        } while(state.jobsPending);} finally {state.jobsPromise=null;}
      })();return state.jobsPromise;
    }
    async function prepareReview(event) {
      event?.preventDefault();if(state.previewBusy||state.submitting||state.uploading)return;
      const error=localError();if(error){message(error,true);return;}
      if(!selectedReady()){message('影片模型尚未就緒；可重新檢查，或匯入既有影片只做後製。',true);return;}
      state.previewBusy=true;state.preview=null;state.previewSignature=null;updateControls();message('正在準備完整稿件與輸出規格；尚未送出生成。');
      const spec=readSpec(), snapshot=signature();
      try {
        const packet=await api('/api/effects/preview',json(spec));
        if(snapshot!==signature()){message('準備期間設定已變更，已保留目前內容，請重新檢查。',true);return;}
        if(!packet.review_receipt||!packet.spec)throw new Error('主機尚未提供有效的稿件確認，請重新啟動更新後的 Studio。');
        if(spec.timing_mode==='retime'&&packet.spec.timing_mode!=='retime')throw new Error('主機尚未載入完整原片變速功能，請完整重啟 Studio 後再試。');
        state.preview=packet;state.previewSignature=snapshot;
        const output=packet.spec,generation=packet.generation||{}, frames=Math.round(Number(output.duration)*Number(output.fps));
        $('effectReviewSummary').textContent=`${output.source_mode==='video'?`匯入原片：${displayNumber(packet.source_info?.duration)} 秒 · ${packet.source_info?.width||'—'} × ${packet.source_info?.height||'—'}`:`生成原片：${displayNumber(generation.duration??output.source_duration)} 秒 · ${generation.width||'—'} × ${generation.height||'—'} · ${generation.video_model||output.video_model}`}${output.source_mode!=='video'&&(generation.video_model||output.video_model)==='freevideo_vdn_h3'?`\n${freevideoRecipeText(generation.freevideo_quality||output.freevideo_quality||'medium')}`:''}\n${timingSummary(output,output.source_mode==='video'?packet.source_info?.duration:generation.duration??output.source_duration)}\n成品輸出：${output.width} × ${output.height} · ${output.fps} FPS · ${frames} 張 PNG\n${postprocessSummary(output)}\n固定畫布、同一固定點、輸出靜音。${output.timing_mode==='retime'?'完整原片等比例變速；不會提高模型生成速度。':''}`;
        const translation=needsOriginalChoice(packet)?'英文整理未完成，目前顯示原文。只有勾選確認使用原文稿後，才會送出。':'';
        $('effectReviewWarnings').textContent=[...(Array.isArray(packet.warnings)?packet.warnings:[]),translation].filter(Boolean).join('\n');
        $('effectReviewPrompt').textContent=packet.prompt||JSON.stringify(packet.spec,null,2);
        $('effectOriginalConfirmed').checked=false;$('effectOriginalChoice').classList.toggle('hidden',!needsOriginalChoice(packet));
        $('effectReviewStatus').textContent='這是本次實際送出的稿件與處理設定。確認前不會建立工作。';
        $('effectReview').showModal();message('請確認實際稿件與成品時長處理。');
      }catch(error){message(error.message,true);}finally{state.previewBusy=false;updateControls();}
    }
    async function confirmReview() {
      if(state.submitting||state.uploading||!currentReview()){message('設定已變更或確認已逾期，請重新檢查稿件。',true);return;}
      if(needsOriginalChoice()&&!$('effectOriginalConfirmed').checked)return;
      const packet=state.preview;state.submitting=true;updateControls();
      try {
        const payload={...packet.spec,review_receipt:packet.review_receipt,...(needsOriginalChoice(packet)?{original_prompt_confirmed:true}:{})};
        const job=await api('/api/effects/jobs',json(payload));
        state.preview=null;state.previewSignature=null;$('effectReview').close();persist();message(`已建立特效工作：${job.name||'特效動畫'}。`);await loadJobs(true);
        $('effectResultsTitle').scrollIntoView({behavior:win.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
      }catch(error){message(error.message,true);if(error.status===409){state.preview=null;state.previewSignature=null;$('effectReviewStatus').textContent='確認已失效，請關閉後重新檢查。';}}
      finally{state.submitting=false;updateControls();}
    }
    async function uploadSource(file) {
      const mode=$('effectSourceMode').value;if(!file||mode==='text'||state.uploading||state.submitting)return;
      const allowed=mode==='video'?/\.(mp4|mov|webm|mkv)$/i:/\.(png|jpe?g|webp|bmp)$/i;
      if(!allowed.test(file.name||'')||!file.size||file.size>512*1024*1024){message(mode==='video'?'請選擇 512 MiB 以內的 MP4、MOV、WebM 或 MKV。':'請選擇 512 MiB 以內的 PNG、JPG、WebP 或 BMP。',true);return;}
      const serial=++state.uploadSerial;state.uploading=true;updateControls();message('正在匯入來源；原本的來源會保留到上傳成功。');
      try {
        const body=new FormData();body.append('kind','effects-source');body.append('file',file);
        const asset=await api('/api/assets',{method:'POST',body});if(!safeId(asset.id))throw new Error('主機未回傳有效的來源素材。');
        if(serial!==state.uploadSerial)return;state.assets[mode]={id:asset.id,name:asset.name||file.name};persist();invalidateReview();message('來源已匯入；完成稿件確認後才會建立工作。');
      }catch(error){message(error.message,true);}finally{state.uploading=false;$('effectSourceFile').value='';updateControls();}
    }
    async function jobAction(id,action) {
      if(!safeId(id)||state.actionJobs.has(id))return;
      const job=state.jobs.find(item=>item.id===id);if(!job)return;
      if(action==='retry'&&active(job.status))return;
      if(action==='cancel'&&!active(job.status))return;
      state.actionJobs.add(id);renderJobs();
      try {await api(`/api/effects/jobs/${id}/${action}`,json({}));message(action==='retry'?'已要求重做後製，將沿用原片與這筆工作的設定。':'已要求取消這筆工作。');await loadJobs(true);}
      catch(error){message(error.message,true);}finally{state.actionJobs.delete(id);renderJobs();}
    }
    function openPostprocess(id) {
      const job=state.jobs.find(item=>item.id===id);if(!job||active(job.status)||state.retryBusy)return;
      state.retryJob=JSON.parse(JSON.stringify(job));
      state.retryTimingMode='trim';
      for(const [key,field] of Object.entries(retryFields))$(field).value=String(job.spec?.[key]??defaults[key]);
      updatePostprocess();
      $('effectRetryMessage').textContent='';$('effectRetry').showModal();
    }
    function readPostprocess() {
      const result={};for(const [key,field] of Object.entries(retryFields))result[key]=numberFields.has(key)?Number($(field).value):$(field).value;
      if(result.timing_mode==='retime')result.trim_start=0;
      return result;
    }
    function updatePostprocess() {
      if(!state.retryJob)return;
      syncTimingInputs(true);
      const job=state.retryJob,estimated=!job.source_info?.duration&&job.spec?.source_mode!=='video',sourceDuration=job.source_info?.duration??(estimated?job.spec?.source_duration:null);
      $('effectRetrySource').textContent=`${job.name||'特效動畫'} · 原片${estimated?'預估':''} ${displayNumber(sourceDuration)} 秒\n${timingSummary(readPostprocess(),sourceDuration,estimated)}`;
      $('effectRetryTimingHint').textContent=$('retryEffectTimingMode').value==='retime'?'完整原片等比例變速，取用起點固定為 0；原片與動作順序保留。這是後製變速，不會提高模型生成速度。':'以原速擷取指定區間；完整原片變速可把 5 秒完整動作縮成 2 秒。';
    }
    async function submitPostprocess(event) {
      event?.preventDefault();if(!state.retryJob||state.retryBusy)return;
      const id=state.retryJob.id,postprocess=readPostprocess();
      const frames=Math.round(postprocess.duration*postprocess.fps),sourceDuration=Number(state.retryJob.source_info?.duration);
      let error='';
      if(!Number.isFinite(postprocess.duration)||postprocess.duration<.25||postprocess.duration>10||!Number.isFinite(postprocess.trim_start)||postprocess.trim_start<0)error='請檢查成品長度與取用起點。';
      else if(![postprocess.width,postprocess.height].every(value=>Number.isInteger(value)&&value>=256&&value<=1536&&value%32===0)||![12,24,30].includes(postprocess.fps))error='畫布需為 256～1536 的 32 倍數，FPS 請選擇 12、24 或 30。';
      else if(frames>300||postprocess.width*postprocess.height*frames>120000000)error='序列總量過大，請降低畫布、FPS 或成品長度。';
      else if(postprocess.timing_mode!=='retime'&&sourceDuration>0&&postprocess.trim_start+postprocess.duration>sourceDuration+.001)error='取用區間超過原片長度，請縮短成品或調整起點。';
      else if(postprocess.playback==='once'&&postprocess.loop_blend!==0)error='單次播放不使用接縫混合，請將循環銜接設為 0。';
      if(error){$('effectRetryMessage').textContent=error;return;}
      state.retryBusy=true;state.actionJobs.add(id);$('confirmEffectRetry').disabled=true;$('closeEffectRetry').disabled=true;$('cancelEffectRetry').disabled=true;for(const field of Object.values(retryFields))$(field).disabled=true;renderJobs();
      try {await api(`/api/effects/jobs/${id}/retry`,json({postprocess}));$('effectRetry').close();state.retryJob=null;message('已套用後製設定，沿用原片重新處理。');await loadJobs(true);}
      catch(error){$('effectRetryMessage').textContent=error.message;}
      finally {state.retryBusy=false;state.actionJobs.delete(id);$('confirmEffectRetry').disabled=false;$('closeEffectRetry').disabled=false;$('cancelEffectRetry').disabled=false;for(const field of Object.values(retryFields))$(field).disabled=false;updatePostprocess();renderJobs();}
    }
    function applyPreset(){const preset=state.catalog.find(item=>item.id===$('effectKind').value)||presets[$('effectKind').value];for(const key of $('effectSourceMode').value==='video'?['playback','anchor']:['playback','direction','anchor'])if(preset?.defaults?.[key]!==undefined)$(fields[key]).value=preset.defaults[key];persist();invalidateReview();updateControls();message('已套用動態預設；來源、描述與其他設定保留。');}
    function resume(){if(doc.hidden)return;checkStatus();loadJobs();}
    writeSpec(defaults);
    try{const draft=JSON.parse(storage.getItem(DRAFT_KEY)||'null');if(draft&&typeof draft==='object'){writeSpec(draft.spec||{});if(draft.spec?.timing_mode==='retime'&&draft.trim_start_draft!==undefined)$('effectTrimStart').value=String(draft.trim_start_draft);for(const mode of ['image','video']){const asset=draft.assets?.[mode];if(safeId(asset?.id))state.assets[mode]={id:asset.id,name:String(asset.name||'來源素材')};}}}catch{}
    for(const id of Object.values(fields)) $(id).addEventListener('input',()=>{persist();invalidateReview();updateControls();});
    $('effectForm').addEventListener('submit',prepareReview);$('confirmEffect').addEventListener('click',confirmReview);
    $('effectOriginalConfirmed').addEventListener('change',updateControls);
    for(const id of ['closeEffectReview','cancelEffectReview'])$(id).addEventListener('click',()=>{if(!state.submitting)$('effectReview').close();});
    $('effectReview').addEventListener('cancel',event=>{if(state.submitting)event.preventDefault();});
    $('applyEffectPreset').addEventListener('click',applyPreset);
    $('effectSourceFile').addEventListener('change',event=>uploadSource(event.target.files?.[0]));
    $('removeEffectSource').addEventListener('click',()=>{if(state.uploading||state.submitting)return;state.assets[$('effectSourceMode').value]=null;persist();invalidateReview();updateControls();});
    $('effectDrop').addEventListener('dragover',event=>{event.preventDefault();$('effectDrop').classList.add('dragging');});
    $('effectDrop').addEventListener('dragleave',()=>$('effectDrop').classList.remove('dragging'));
    $('effectDrop').addEventListener('drop',event=>{event.preventDefault();$('effectDrop').classList.remove('dragging');uploadSource(event.dataTransfer?.files?.[0]);});
    $('refreshEffectStatus').addEventListener('click',checkStatus);$('refreshEffectJobs').addEventListener('click',()=>loadJobs(true));
    $('effectJobs').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.previewJob){const job=state.jobs.find(item=>item.id===button.dataset.previewJob);if(previewFiles(job||{}).some(file=>file.name===button.dataset.previewFile)){state.selectedPreviews.set(job.id,button.dataset.previewFile);renderJobs();}}else if(button.dataset.adjustEffect)openPostprocess(button.dataset.adjustEffect);else if(button.dataset.retryEffect)jobAction(button.dataset.retryEffect,'retry');else if(button.dataset.cancelEffect)jobAction(button.dataset.cancelEffect,'cancel');});
    $('effectRetryForm').addEventListener('submit',submitPostprocess);
    for(const id of Object.values(retryFields))$(id).addEventListener('input',updatePostprocess);
    for(const id of ['closeEffectRetry','cancelEffectRetry'])$(id).addEventListener('click',()=>{if(!state.retryBusy){$('effectRetry').close();state.retryJob=null;}});
    $('effectRetry').addEventListener('cancel',event=>{if(state.retryBusy)event.preventDefault();});
    doc.addEventListener('visibilitychange',resume);win.addEventListener('online',resume);win.addEventListener('pageshow',resume);
    interval(()=>{if(!doc.hidden)loadJobs();},3000);interval(()=>{if(!doc.hidden)checkStatus();},15000);
    updateControls();const ready=Promise.allSettled([loadCatalog(),checkStatus(),loadJobs()]);
    return {state,ready,api,readSpec,writeSpec,signature,persist,updateControls,prepareReview,confirmReview,uploadSource,jobAction,openPostprocess,submitPostprocess,loadJobs,checkStatus,resume,applyPreset};
  }
  const api={install,defaults,presets,fields,DRAFT_KEY,fileUrl};
  root.H3Effects=api;if(typeof module==='object'&&module.exports)module.exports=api;
  if(root.document){const boot=()=>install({document:root.document,window:root,storage:root.localStorage,fetch:root.fetch.bind(root)});if(root.document.readyState==='loading')root.document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();}
})(typeof window!=='undefined'?window:globalThis);
