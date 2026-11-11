const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { install, defaults, DRAFT_KEY, fileUrl } = require('../static/effects.js');
const html = fs.readFileSync(path.join(__dirname, '../static/effects.html'), 'utf8');
const a = 'a'.repeat(32), b = 'b'.repeat(32);
function response(data, status = 200) { return {ok:status>=200&&status<300,status,json:async()=>data}; }
function deferred() { let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject}; }
function modelStatus(ready=true) {return {ready,import_ready:true,models:[{id:'h3',ready,error:ready?null:'原版模型尚未就緒'},{id:'fasth3_omni_nvfp4',ready:true},{id:'pika_h3_flex',ready:true}]};}
function packet(spec,extra={}) {return {spec:{...spec,version:'test',frame_count:Math.round(spec.duration*spec.fps)},prompt:'integrated_multimodal_description: [Shot 1] Violet flame on a clean black background.\noverall_soundscape: N/A\nnon_diegetic_music: N/A',generation:{duration:5.166666666666667,width:640,height:640,video_model:spec.video_model,seed:spec.seed},review_receipt:'receipt',review_expires_at:'2099-01-01T00:00:00Z',warnings:[],...extra};}
async function fixture({draft=null,status=modelStatus(),jobs=[],handler=null}={}) {
  const elements = new Map(), stored = new Map(), calls=[], timeouts=new Map(), intervals=[],documentEvents=new Map(),windowEvents=new Map();
  let serial=0,override=handler,statusData=status,jobsData=jobs;
  class Element {
    constructor(id){this.id=id;this.value='';this.textContent='';this.innerHTML='';this.disabled=false;this.checked=false;this.open=false;this.listeners={};this.files=[];this.scrolls=[];this.classes=new Set();this.classList={add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),contains:name=>this.classes.has(name),toggle:(name,flag)=>{if(flag===undefined)flag=!this.classes.has(name);if(flag)this.classes.add(name);else this.classes.delete(name);return flag;}};}
    addEventListener(name,fn){this.listeners[name]=fn;}
    showModal(){this.open=true;} close(){this.open=false;}
    scrollIntoView(options){this.scrolls.push(options);}
  }
  for(const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1],new Element(match[1]));
  const el=id=>{assert.ok(elements.has(id),`HTML contains ${id}`);return elements.get(id);};
  if(draft)stored.set(DRAFT_KEY,JSON.stringify(draft));
  const doc={hidden:false,getElementById:el,addEventListener:(name,fn)=>documentEvents.set(name,fn)};
  const win={addEventListener:(name,fn)=>windowEvents.set(name,fn)};
  const ui=install({document:doc,window:win,storage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},
    setTimeout(fn,ms){const id=++serial;timeouts.set(id,{fn,ms});return id;},clearTimeout:id=>timeouts.delete(id),setInterval:(fn,ms)=>intervals.push({fn,ms}),
    async fetch(url,options={}){
      calls.push({url,options});
      if(override){const value=await override(url,options);if(value!==undefined)return value;}
      if(url==='/api/effects/catalog')return response({items:[]});
      if(url==='/api/effects/status')return response(statusData);
      if(url==='/api/effects/jobs'&&!options.method)return response({items:jobsData});
      if(url==='/api/effects/preview')return response(packet(JSON.parse(options.body)));
      if(url==='/api/effects/jobs')return response({id:a,name:'特效測試'},202);
      if(url==='/api/assets')return response({id:b,name:'新來源.mp4'});
      if(url.includes('/retry')||url.includes('/cancel'))return response({id:a});
      throw new Error(`Unexpected request ${url}`);
    }});
  await ui.ready;
  const fire=async(id,event='input',extra={})=>el(id).listeners[event]?.({preventDefault(){},target:el(id),...extra});
  return {ui,el,calls,stored,doc,documentEvents,windowEvents,intervals,timeouts,fire,setHandler:value=>override=value,setStatus:value=>statusData=value,setJobs:value=>jobsData=value};
}

test('effects opens without submitting work and restores only its dedicated draft',async()=>{
  const saved={spec:{...defaults,name:'原稿',prompt:'紫色火焰',seed:723,video_model:'pika_h3_flex',duration:2},assets:{image:{id:a,name:'參考圖.png'},video:{id:b,name:'火焰.mp4'}}};
  const f=await fixture({draft:saved});
  assert.equal(f.el('effectName').value,'原稿');assert.equal(f.el('effectPrompt').value,'紫色火焰');assert.equal(f.el('effectSeed').value,'723');
  assert.equal(f.ui.state.assets.video.id,b);assert.equal(f.el('effectVideoModel').value,'pika_h3_flex');
  assert.ok(f.calls.every(call=>!call.options.method));assert.equal(f.stored.size,1);
});

test('effects keeps FreeVideo quality through drafts and shows it only for generated sources',async()=>{
  const status=modelStatus();status.models.push({id:'freevideo_vdn_h3',ready:true,mode_ready:{t2v:true,fl2va:true}});
  const f=await fixture({status,draft:{spec:{...defaults,video_model:'freevideo_vdn_h3',freevideo_quality:'high'},assets:{video:{id:a,name:'來源.mp4'}}}});
  assert.equal(f.el('effectFreevideoQuality').value,'high');
  assert.equal(f.el('effectFreevideoQualityWrap').classes.has('hidden'),false);
  assert.match(f.el('effectFreevideoQualityHint').textContent,/單階段 16 步/);
  f.el('effectFreevideoQuality').value='max';await f.fire('effectFreevideoQuality');
  assert.equal(JSON.parse(f.stored.get(DRAFT_KEY)).spec.freevideo_quality,'max');
  f.el('effectVideoModel').value='h3';await f.fire('effectVideoModel');
  assert.equal(f.el('effectFreevideoQualityWrap').classes.has('hidden'),true);
  f.el('effectVideoModel').value='freevideo_vdn_h3';await f.fire('effectVideoModel');
  assert.equal(f.el('effectFreevideoQuality').value,'max');
  f.el('effectSourceMode').value='video';await f.fire('effectSourceMode');
  assert.equal(f.el('effectFreevideoQualityWrap').classes.has('hidden'),true);
  assert.equal(f.el('effectFreevideoQuality').disabled,true);
});

test('effects review confirms the actual FreeVideo recipe and a quality edit invalidates it',async()=>{
  const status=modelStatus();status.models.push({id:'freevideo_vdn_h3',ready:true,mode_ready:{t2v:true}});
  const f=await fixture({status,draft:{spec:{...defaults,video_model:'freevideo_vdn_h3',freevideo_quality:'medium'},assets:{}}});
  await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/FreeVideo Medium.*兩階段 8＋3 步/);
  const reviewed=f.ui.state.preview.spec;
  assert.equal(reviewed.freevideo_quality,'medium');assert.equal(f.el('confirmEffect').disabled,false);
  f.el('effectFreevideoQuality').value='max';await f.fire('effectFreevideoQuality');
  await f.ui.confirmReview();assert.equal(f.calls.some(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST'),false);
  await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/FreeVideo Max.*單階段 20 步/);
  await f.ui.confirmReview();
  const sent=JSON.parse(f.calls.find(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST').options.body);
  assert.equal(sent.freevideo_quality,'max');assert.equal(sent.video_model,'freevideo_vdn_h3');
});

test('small-resolution FreeVideo effects disclose equivalent two-pass plans before confirmation',async()=>{
  const status=modelStatus();status.models.push({id:'freevideo_vdn_h3',ready:true,mode_ready:{t2v:true}});
  for(const quality of ['light','medium']){
    const f=await fixture({status,draft:{spec:{...defaults,width:864,height:480,video_model:'freevideo_vdn_h3',freevideo_quality:quality},assets:{}}});
    assert.match(f.el('effectFreevideoQualityHint').textContent,/小尺寸時 Light／Medium 可能使用相同計畫/);
    await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/不保證品質或速度差異/);
    assert.equal(f.ui.state.preview.spec.freevideo_quality,quality);
  }
});

test('explicit presets preserve the source, manuscript, color and advanced cleanup values',async()=>{
  const f=await fixture({draft:{spec:{...defaults,prompt:'保留的描述',color:'#ab34ef',black_level:.06,loop_blend:.1,source_mode:'video'},assets:{video:{id:a,name:'原片.mp4'}}}});
  f.el('effectKind').value='lightning';await f.fire('effectKind');await f.fire('applyEffectPreset','click');
  assert.equal(f.el('effectPlayback').value,'once');assert.equal(f.el('effectDirection').value,'up');assert.equal(f.el('effectAnchor').value,'center');
  assert.equal(f.el('effectPrompt').value,'保留的描述');assert.equal(f.el('effectColor').value,'#ab34ef');assert.equal(f.el('effectBlackLevel').value,'0.06');assert.equal(f.el('effectLoopBlend').value,'0.1');
  assert.equal(f.el('previewEffect').disabled,true);assert.match(f.el('effectValidation').textContent,/單次播放不使用接縫混合/);
  assert.equal(f.ui.state.assets.video.id,a);assert.equal(JSON.parse(f.stored.get(DRAFT_KEY)).spec.kind,'lightning');
});

test('preview shows the actual compiled manuscript and confirmation submits exactly the server reviewed specification once',async()=>{
  const f=await fixture();f.el('effectPrompt').value='紫色火焰';await f.fire('effectPrompt');
  await f.ui.prepareReview({preventDefault(){}});
  assert.equal(f.el('effectReview').open,true);assert.match(f.el('effectReviewPrompt').textContent,/integrated_multimodal_description/);
  assert.match(f.el('effectReviewSummary').textContent,/生成原片：5.167 秒/);assert.match(f.el('effectReviewSummary').textContent,/成品：從 0 秒起取用 4 秒/);
  assert.doesNotMatch(f.el('effectReviewSummary').textContent,/5\.166666/);
  for(const detail of ['播放方式：持續循環','固定點：底部中央','黑底清理：0.015','透明柔邊：0.08','時序平滑：0.15','循環銜接：0 秒'])assert.ok(f.el('effectReviewSummary').textContent.includes(detail));
  assert.equal(f.calls.filter(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST').length,0);
  const reviewed=f.ui.state.preview.spec;await Promise.all([f.ui.confirmReview(),f.ui.confirmReview()]);
  const submissions=f.calls.filter(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST');assert.equal(submissions.length,1);
  assert.deepEqual(JSON.parse(submissions[0].options.body),{...reviewed,review_receipt:'receipt'});assert.equal(f.el('effectReview').open,false);
});

test('editing parameters or source after preview blocks stale confirmation and preserves the current draft',async()=>{
  const f=await fixture();await f.ui.prepareReview();f.el('effectDuration').value='3';await f.fire('effectDuration');
  assert.equal(f.el('confirmEffect').disabled,true);await f.ui.confirmReview();assert.match(f.el('effectFormMessage').textContent,/重新檢查稿件/);
  assert.equal(f.calls.filter(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST').length,0);
  assert.equal(JSON.parse(f.stored.get(DRAFT_KEY)).spec.duration,3);
});

test('an edit during asynchronous preview cannot open a review for an older manuscript',async()=>{
  const pending=deferred();const f=await fixture({handler:(url)=>url==='/api/effects/preview'?pending.promise:undefined});
  const old=f.ui.readSpec(),preparing=f.ui.prepareReview();f.el('effectPrompt').value='尚未保存的新描述';await f.fire('effectPrompt');pending.resolve(response(packet(old)));await preparing;
  assert.equal(f.el('effectReview').open,false);assert.equal(f.ui.state.preview,null);assert.equal(f.el('effectPrompt').value,'尚未保存的新描述');assert.match(f.el('effectFormMessage').textContent,/設定已變更/);
});

test('translation fallback requires explicit original-text confirmation before submission',async()=>{
  const f=await fixture({handler:(url,options)=>url==='/api/effects/preview'?response(packet(JSON.parse(options.body),{generation:{h3_translation:{status:'unavailable'}}})):undefined});
  await f.ui.prepareReview();assert.equal(f.el('confirmEffect').disabled,true);assert.match(f.el('effectReviewWarnings').textContent,/英文整理未完成/);
  await f.ui.confirmReview();assert.equal(f.calls.some(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST'),false);
  f.el('effectOriginalConfirmed').checked=true;await f.fire('effectOriginalConfirmed','change');await f.ui.confirmReview();
  assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST').options.body).original_prompt_confirmed,true);
});

test('imported video can be reviewed while generation is unavailable and keeps the original asset identity',async()=>{
  const f=await fixture({status:modelStatus(false),draft:{spec:{...defaults,source_mode:'video',duration:1},assets:{video:{id:a,name:'既有火焰.mp4'}}}});
  assert.equal(f.el('previewEffect').disabled,false);await f.ui.prepareReview();
  assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/effects/preview').options.body).source_asset_id,a);
  assert.equal(f.ui.state.preview.spec.source_mode,'video');assert.equal(f.el('effectGenerationFields').classList.contains('hidden'),true);
});

test('failed uploads keep the previous source and successful uploads invalidate an earlier review',async()=>{
  const f=await fixture({draft:{spec:{...defaults,source_mode:'video'},assets:{video:{id:a,name:'舊原片.mp4'}}}});
  await f.ui.prepareReview();f.setHandler(url=>url==='/api/assets'?response({error:'上傳失敗'},400):undefined);
  const file=new File([new Uint8Array([1,2,3])],'new.mp4',{type:'video/mp4'});await f.ui.uploadSource(file);
  assert.equal(f.ui.state.assets.video.id,a);assert.match(f.el('effectFormMessage').textContent,/上傳失敗/);
  f.setHandler(null);await f.ui.uploadSource(file);assert.equal(f.ui.state.assets.video.id,b);assert.equal(f.el('confirmEffect').disabled,true);
  const body=f.calls.find(call=>call.url==='/api/assets').options.body;assert.equal(body.get('kind'),'effects-source');assert.equal(body.get('file').name,'new.mp4');
});

test('recovery coalesces status polls and does not change selections or submit work',async()=>{
  const pending=deferred();let count=0;const f=await fixture();f.el('effectPrompt').value='保留輸入';f.el('effectVideoModel').value='pika_h3_flex';await f.fire('effectPrompt');
  f.setHandler(url=>{if(url==='/api/effects/status'){count++;return pending.promise;}});
  const one=f.ui.checkStatus();f.ui.resume();f.windowEvents.get('online')();f.documentEvents.get('visibilitychange')();assert.equal(count,1);
  pending.resolve(response(modelStatus()));await one;assert.equal(f.el('effectPrompt').value,'保留輸入');assert.equal(f.el('effectVideoModel').value,'pika_h3_flex');
  assert.ok(f.calls.every(call=>!call.options.method));f.doc.hidden=true;const before=f.calls.length;for(const timer of f.intervals)timer.fn();assert.equal(f.calls.length,before);
});

test('failed job refresh keeps the prior gallery and later forced refresh recovers',async()=>{
  const original={id:a,name:'保留作品',status:'needs_adjustment',spec:{kind:'flame',playback:'loop'},qa:{warnings:['接縫需要檢查']},files:[]};
  const f=await fixture({jobs:[original]});assert.match(f.el('effectJobs').innerHTML,/保留作品/);assert.match(f.el('effectJobs').innerHTML,/需要調整/);
  f.setHandler(url=>url==='/api/effects/jobs'?response({error:'暫時離線'},503):undefined);await f.ui.loadJobs(true);
  assert.match(f.el('effectJobs').innerHTML,/保留作品/);assert.match(f.el('effectJobsStatus').textContent,/上次結果已保留/);
  f.setHandler(null);f.setJobs([{...original,name:'恢復更新'}]);await f.ui.loadJobs(true);assert.match(f.el('effectJobs').innerHTML,/恢復更新/);
});

test('retry sends only a postprocess request for its frozen job and cancel targets only the selected job',async()=>{
  const f=await fixture({jobs:[{id:a,status:'failed',name:'失敗工作',spec:{kind:'smoke'}},{id:b,status:'generating',name:'生成中',spec:{kind:'flame'}}]});
  await f.ui.jobAction(a,'retry');await f.ui.jobAction(b,'cancel');await f.ui.jobAction(b,'retry');
  const posts=f.calls.filter(call=>call.options.method==='POST');assert.deepEqual(posts.map(call=>call.url),[`/api/effects/jobs/${a}/retry`,`/api/effects/jobs/${b}/cancel`]);
  assert.ok(posts.every(call=>call.options.body==='{}'));
});

test('preview output links stay local and QA messages are escaped without claiming transparent MP4',async()=>{
  assert.equal(fileUrl(a,{name:'../secrets.json'}),'');assert.equal(fileUrl('bad',{name:'preview.mp4'}),'');
  const f=await fixture({jobs:[{id:a,name:'<script>bad</script>',status:'needs_adjustment',spec:{kind:'glow',playback:'loop'},qa:{warnings:['<img onerror=bad>']},files:[{name:'r0001_preview-checker.mp4',kind:'preview',label:'棋盤格'},{name:'r0001_frames.zip',kind:'archive',label:'PNG 序列'}]}]});
  const markup=f.el('effectJobs').innerHTML;assert.ok(markup.includes('&lt;script&gt;'));assert.ok(!markup.includes('<script>bad'));
  assert.match(markup,/loop src=/);assert.match(markup,/透明素材請下載 PNG 序列/);assert.match(markup,/\/api\/effects\/jobs\//);
});

test('GET deadlines include hung response bodies while mutation requests have no new short timeout',async()=>{
  const f=await fixture();
  // A native fetch rejects body reads on abort; the fixture mirrors that transport behavior.
  f.setHandler((url,options)=>url==='/hung'?{ok:true,status:200,json:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}))}:url==='/mutation'?response({ok:true}):undefined);
  const pending=f.ui.api('/hung');await new Promise(setImmediate);assert.equal(f.timeouts.size,1);const rejected=assert.rejects(pending,/逾時/);[...f.timeouts.values()][0].fn();await rejected;assert.equal(f.timeouts.size,0);
  await f.ui.api('/mutation',{method:'POST',body:'{}'});const call=f.calls.find(item=>item.url==='/mutation');assert.equal(call.options.signal,undefined);assert.equal(f.timeouts.size,0);
});

test('postprocess adjustments use the selected source job without changing the new-work draft',async()=>{
  const job={id:a,name:'紫火焰原片',status:'needs_adjustment',spec:{...defaults,name:'原本工作',duration:4},source_info:{duration:5.17},files:[]};
  const f=await fixture({jobs:[job],draft:{spec:{...defaults,name:'上方未保存的新工作',prompt:'新描述',black_level:.02},assets:{video:{id:b,name:'另一原片.mp4'}}}});
  const before=JSON.stringify(f.ui.readSpec()),stored=f.stored.get(DRAFT_KEY);
  f.ui.openPostprocess(a);assert.equal(f.el('effectRetry').open,true);assert.match(f.el('effectRetrySource').textContent,/紫火焰原片/);
  f.el('retryEffectBlackLevel').value='.03';f.el('retryEffectDuration').value='2';f.el('retryEffectTrimStart').value='1';
  await f.ui.submitPostprocess();const post=f.calls.find(call=>call.url===`/api/effects/jobs/${a}/retry`);
  const body=JSON.parse(post.options.body);assert.deepEqual(Object.keys(body),['postprocess']);assert.equal(body.postprocess.black_level,.03);assert.equal(body.postprocess.duration,2);assert.equal(body.postprocess.trim_start,1);
  assert.equal(Object.hasOwn(body.postprocess,'prompt'),false);assert.equal(Object.hasOwn(body.postprocess,'source_asset_id'),false);
  assert.equal(JSON.stringify(f.ui.readSpec()),before);assert.equal(f.stored.get(DRAFT_KEY),stored);assert.equal(job.spec.black_level,.015);
  assert.equal(f.el('effectRetry').open,false);
});

test('invalid postprocess ranges cannot submit and failed retries preserve editable settings',async()=>{
  const job={id:a,name:'煙塵',status:'needs_adjustment',spec:{...defaults,kind:'smoke'},source_info:{duration:5},files:[]};const f=await fixture({jobs:[job]});
  f.ui.openPostprocess(a);f.el('retryEffectTrimStart').value='4';await f.ui.submitPostprocess();assert.match(f.el('effectRetryMessage').textContent,/超過原片長度/);
  assert.equal(f.calls.some(call=>call.options.method==='POST'),false);f.el('retryEffectTrimStart').value='0';f.el('retryEffectSoftness').value='.12';
  f.setHandler(url=>url.endsWith('/retry')?response({error:'無法讀取原片'},409):undefined);await f.ui.submitPostprocess();
  assert.equal(f.el('effectRetry').open,true);assert.equal(f.el('retryEffectSoftness').value,'.12');assert.match(f.el('effectRetryMessage').textContent,/無法讀取原片/);assert.equal(f.el('confirmEffectRetry').disabled,false);
});

test('current previews and downloads select the newest revision while the original source stays accessible',async()=>{
  const f=await fixture({jobs:[{id:a,name:'新版',status:'completed',spec:{...defaults},files:[
    {name:'source.mp4',kind:'source',label:'原片'},
    {name:'r0001_preview-checker.mp4',kind:'preview',label:'舊預覽'},
    {name:'r0001_frames.zip',kind:'archive',label:'舊序列'},
    {name:'r0002_preview-checker.mp4',kind:'preview',label:'最新預覽'},
    {name:'r0002_frames.zip',kind:'archive',label:'最新序列'},
  ]}]});
  const markup=f.el('effectJobs').innerHTML;assert.match(markup,/r0002_preview-checker/);assert.match(markup,/r0002_frames/);assert.match(markup,/source.mp4/);assert.doesNotMatch(markup,/r0001_/);
});

test('import mode disables generation-only appearance controls while preserving choices for return to generation',async()=>{
  const spec={...defaults,source_mode:'video',prompt:'紫火焰描述',color:'#aa00ff',secondary_color:'#ffffff',style:'cartoon',strength:4,speed:'fast',direction:'left'};
  const f=await fixture({draft:{spec,assets:{video:{id:a,name:'紫焰.mp4'}}}});
  const ids=['effectPrompt','effectColor','effectSecondaryColor','effectStyle','effectStrength','effectSpeed','effectDirection'];
  for(const id of ids)assert.equal(f.el(id).disabled,true,id);
  assert.equal(f.el('effectImportedNotice').classList.contains('hidden'),false);assert.match(f.el('effectImportedNotice').textContent||html,/匯入影片只做後製/);
  const before=Object.fromEntries(ids.map(id=>[id,f.el(id).value]));f.el('effectKind').value='explosion';await f.fire('applyEffectPreset','click');assert.equal(f.el('effectDirection').value,'left');
  f.el('effectSourceMode').value='text';await f.fire('effectSourceMode');
  for(const id of ids){assert.equal(f.el(id).disabled,false,id);assert.equal(f.el(id).value,before[id],id);}
  assert.equal(f.el('effectImportedNotice').classList.contains('hidden'),true);
});

test('import and postprocess source summaries round seconds without changing the reviewed or submitted values',async()=>{
  const duration=5.166666666666667,job={id:a,name:'原片',status:'needs_adjustment',spec:{...defaults},source_info:{duration}};
  const f=await fixture({jobs:[job],draft:{spec:{...defaults,source_mode:'video'},assets:{video:{id:b,name:'來源.mp4'}}},handler:(url,options)=>url==='/api/effects/preview'?response(packet(JSON.parse(options.body),{generation:null,source_info:{duration,width:640,height:640}})):undefined});
  await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/匯入原片：5.167 秒/);assert.equal(f.ui.state.preview.source_info.duration,duration);
  f.ui.openPostprocess(a);assert.match(f.el('effectRetrySource').textContent,/原片 5.167 秒/);assert.equal(f.ui.state.retryJob.source_info.duration,duration);
});

test('quality-checking jobs remain cancellable and cannot be retried while active',async()=>{
  const f=await fixture({jobs:[{id:a,name:'檢查中',status:'checking',spec:{...defaults}}]});
  assert.match(f.el('effectJobs').innerHTML,/品質檢查/);assert.match(f.el('effectJobs').innerHTML,/data-cancel-effect/);assert.doesNotMatch(f.el('effectJobs').innerHTML,/data-adjust-effect/);
  await f.ui.jobAction(a,'retry');assert.equal(f.calls.some(call=>call.options.method==='POST'),false);await f.ui.jobAction(a,'cancel');assert.equal(f.calls.find(call=>call.options.method==='POST').url,`/api/effects/jobs/${a}/cancel`);
});

test('only a successful explicit creation navigates to results after its history refresh completes',async()=>{
  const job={id:a,name:'既有特效',status:'failed',spec:{...defaults}},f=await fixture({jobs:[job]});
  const title=f.el('effectResultsTitle');assert.equal(title.scrolls.length,0);await f.ui.loadJobs();await f.ui.jobAction(a,'retry');assert.equal(title.scrolls.length,0);
  await f.ui.prepareReview();f.setHandler((url,options)=>url==='/api/effects/jobs'&&options.method==='POST'?response({error:'建立失敗'},503):undefined);
  await f.ui.confirmReview();assert.equal(title.scrolls.length,0);assert.equal(f.el('effectReview').open,true);
  const pending=deferred();f.setHandler((url,options)=>url==='/api/effects/jobs'&&!options.method?pending.promise:undefined);
  const creating=f.ui.confirmReview();await new Promise(setImmediate);assert.equal(title.scrolls.length,0,'wait for the resulting job list before moving the viewport');
  pending.resolve(response({items:[{...job,name:'剛建立的特效',status:'queued'}]}));await creating;
  assert.equal(title.scrolls.length,1);assert.deepEqual(title.scrolls[0],{behavior:'smooth',block:'start'});assert.match(f.el('effectJobs').innerHTML,/剛建立的特效/);
  await f.ui.loadJobs();assert.equal(title.scrolls.length,1,'background polling never steals scroll position');
});

test('complete-source retime invalidates a crop review, preserves its start across reloads and submits the actual speed specification',async()=>{
  const {timing_mode,...oldSpec}=defaults;
  const f=await fixture({draft:{spec:{...oldSpec,trim_start:1,duration:2},assets:{}}});
  assert.equal(f.el('effectTimingMode').value,'trim');assert.equal(f.el('effectTrimStart').value,'1');
  await f.ui.prepareReview();f.el('effectTimingMode').value='retime';await f.fire('effectTimingMode');
  assert.equal(f.el('confirmEffect').disabled,true);assert.equal(f.el('effectTrimStart').disabled,true);assert.equal(f.el('effectTrimStart').value,'0');
  assert.match(f.el('effectOutputSummary').textContent,/加速 2.58×/);assert.match(f.el('effectTimingHint').textContent,/不會提高模型生成速度/);
  const saved=JSON.parse(f.stored.get(DRAFT_KEY));assert.equal(saved.spec.timing_mode,'retime');assert.equal(saved.spec.trim_start,0);assert.equal(saved.trim_start_draft,'1');
  await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/完整原片 5.167 秒 → 成品 2 秒，加速 2.58×/);
  assert.doesNotMatch(f.el('effectReviewSummary').textContent,/成品：從/);assert.match(f.el('effectReviewSummary').textContent,/生成原片：5.167 秒/);
  await f.ui.confirmReview();const body=JSON.parse(f.calls.find(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST').options.body);
  assert.equal(body.timing_mode,'retime');assert.equal(body.trim_start,0);assert.equal(body.source_duration,5);assert.equal(body.duration,2);
  const restored=await fixture({draft:saved});assert.equal(restored.el('effectTimingMode').value,'retime');assert.equal(restored.el('effectTrimStart').value,'0');
  restored.el('effectTimingMode').value='trim';await restored.fire('effectTimingMode');assert.equal(restored.el('effectTrimStart').disabled,false);assert.equal(restored.el('effectTrimStart').value,'1');
});

test('retime allows a longer final duration while crop mode still protects the source range',async()=>{
  const f=await fixture();f.el('effectDuration').value='8';await f.fire('effectDuration');
  assert.match(f.el('effectValidation').textContent,/取用區間超過/);assert.equal(f.el('previewEffect').disabled,true);
  f.el('effectTimingMode').value='retime';await f.fire('effectTimingMode');assert.equal(f.el('effectValidation').textContent,'');assert.equal(f.el('previewEffect').disabled,false);
  assert.match(f.el('effectOutputSummary').textContent,/預估慢放 0.63×/);await f.ui.prepareReview();assert.equal(f.el('effectReview').open,true);
});

test('video import reports unknown length until reviewed and then uses the measured source for retime speed',async()=>{
  const f=await fixture({draft:{spec:{...defaults,source_mode:'video',timing_mode:'retime',duration:2},assets:{video:{id:a,name:'完整原片.mp4'}}},handler:(url,options)=>url==='/api/effects/preview'?response(packet(JSON.parse(options.body),{generation:null,source_info:{duration:10.25,width:640,height:640}})):undefined});
  assert.match(f.el('effectOutputSummary').textContent,/原片長度與變速倍率將於檢查時確認/);assert.doesNotMatch(f.el('effectOutputSummary').textContent,/預估加速/);
  await f.ui.prepareReview();assert.match(f.el('effectReviewSummary').textContent,/完整原片 10.25 秒 → 成品 2 秒，加速 5.13×/);assert.match(f.el('effectOutputSummary').textContent,/加速 5.13×/);
  f.el('effectDuration').value='5';await f.fire('effectDuration');assert.equal(f.el('confirmEffect').disabled,true);assert.match(f.el('effectOutputSummary').textContent,/完整原片 10.25 秒 → 成品 5 秒，加速 2.05×/);
});

test('postprocess retime uses the complete saved source, preserves crop controls and leaves the main draft untouched',async()=>{
  const job={id:a,name:'既有五秒動畫',status:'completed',spec:{...defaults,trim_start:1,duration:2},source_info:{duration:5.166666666666667}};
  const f=await fixture({jobs:[job],draft:{spec:{...defaults,name:'上方草稿'},assets:{}}}),before=f.stored.get(DRAFT_KEY);
  f.ui.openPostprocess(a);f.el('retryEffectTimingMode').value='retime';await f.fire('retryEffectTimingMode');
  assert.equal(f.el('retryEffectTrimStart').value,'0');assert.equal(f.el('retryEffectTrimStart').disabled,true);assert.match(f.el('effectRetrySource').textContent,/完整原片 5.167 秒 → 成品 2 秒，加速 2.58×/);
  f.el('retryEffectTimingMode').value='trim';await f.fire('retryEffectTimingMode');assert.equal(f.el('retryEffectTrimStart').value,'1');
  f.el('retryEffectTimingMode').value='retime';await f.fire('retryEffectTimingMode');f.el('retryEffectDuration').value='8';await f.fire('retryEffectDuration');
  f.setHandler(url=>url.endsWith('/retry')?response({error:'稍後重試'},409):undefined);await f.ui.submitPostprocess();
  assert.equal(f.el('effectRetry').open,true);assert.equal(f.el('retryEffectTrimStart').disabled,true);assert.equal(f.el('retryEffectTrimStart').value,'0');
  f.setHandler(null);await f.ui.submitPostprocess();const posts=f.calls.filter(call=>call.url.endsWith('/retry'));assert.equal(posts.length,2);
  for(const post of posts){const body=JSON.parse(post.options.body).postprocess;assert.equal(body.timing_mode,'retime');assert.equal(body.trim_start,0);assert.equal(body.duration,8);}
  assert.equal(f.stored.get(DRAFT_KEY),before);assert.equal(f.ui.readSpec().timing_mode,'trim');assert.ok(f.calls.every(call=>call.url!=='/api/effects/jobs'||!call.options.method));
});

test('retime review rejects older servers that ignore its timing mode instead of confirming a cropped output',async()=>{
  const f=await fixture({draft:{spec:{...defaults,timing_mode:'retime',duration:2},assets:{}},handler:(url,options)=>{
    if(url!=='/api/effects/preview')return;const spec=JSON.parse(options.body);delete spec.timing_mode;return response(packet(spec));
  }});
  assert.match(f.el('effectOutputSummary').textContent,/預估加速 2.5×/);
  await f.ui.prepareReview();assert.match(f.el('effectFormMessage').textContent,/主機尚未載入完整原片變速功能，請完整重啟 Studio 後再試/);
  assert.equal(f.ui.state.preview,null);assert.equal(f.el('effectReview').open,false);assert.equal(f.el('confirmEffect').disabled,true);
  await f.ui.confirmReview();assert.equal(f.calls.some(call=>call.url==='/api/effects/jobs'&&call.options.method==='POST'),false);
});
