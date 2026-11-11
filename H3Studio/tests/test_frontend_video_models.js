const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const fast = 'fasth3_omni_nvfp4';

function fixture() {
  const elements = new Map(), storage = new Map();
  const element = key => {
    if (!elements.has(key)) { const classes = new Set(); elements.set(key, {value:'',checked:false,disabled:false,textContent:'',classes,
      options:[{value:'native'},{value:'turbo'},{value:'turbo_fast'}],classList:{toggle(name,enabled){if(enabled)classes.add(name);else classes.delete(name);},add(name){classes.add(name);},remove(name){classes.delete(name);}},
      scrollIntoView(){}, addEventListener(){}});
    }
    return elements.get(key);
  };
  const c = vm.createContext({$:element,structuredClone, keyframePrepareVersion:0, videoModelSupported:false,videoModelProfiles:[],
    state:{customLoras:[],mode:'t2v'},localStorage:{setItem:(k,v)=>storage.set(k,v)},
    isReferenceMode:()=>false,qualityModeCompatible:()=>true,turboProfile:()=>({steps:8,title:'Turbo',hint:'Turbo'}),
    accelerationAvailabilityNote:()=>'',memoryOptimizationHint:()=>'',renderLoraPanels(){},
    activeShortFilmProject:()=>c.project||null,saveState(){storage.set('state',JSON.stringify(c.state));},
    updateSummary(){c.syncQualityMode(864,480);c.persistForm();},
    activeReferences:()=>[],renderReplacement(){},renderKeyframePreview(){},renderSymbolLoop(){},renderContinuation(){},
    renderMgAnimation(){},renderReferences(){},renderStoryboards(){},uid:()=>'',
    setMode(mode){c.state.mode=mode;},async loadJobRecipe(){return c.recipe;},toast(){},
  });
  for (const name of ['currentSettings','persistForm','restoreForm','videoModelSelectionError','videoModelHint','syncVideoModelStatus','rememberH3ModelSettings','changeVideoModel','syncQualityMode','syncShortFilmAcceleration','recipeAsset','originalGenerationRequest','applyJobRecipe','generationReviewPromptText']) {
    const start=source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start,-1,name);
    vm.runInContext(source.slice(start,source.indexOf('\n}',start)+2),c);
  }
  element('#videoModel').value='h3'; element('#qualityMode').value='native';
  element('#steps').value=20;element('#scheduler').value='simple';element('#refImageSize').value='match';
  element('#aspectRatio').value='16:9';element('#megapixels').value='0.4';element('#duration').value='5';
  element('#prompt').value='A test';element('#jobName').value='';
  return {c,element,storage};
}

test('quick and short-film pages retain H3 and FastH3 and offer the installed dedicated models',()=>{
  for(const id of ['videoModel','sfVideoModel']){
    const select=html.match(new RegExp(`<select id="${id}" aria-describedby="${id}Hint">([\\s\\S]*?)</select>`));
    assert.ok(select,id);
    assert.deepEqual([...select[1].matchAll(/value="([^"]+)"/g)].map(m=>m[1]),['h3',fast,'pika_h3_flex','h3_ref_patch','freevideo_vdn_h3']);
  }
});

test('Fast selection emits a separate model and fixed clean recipe, then restores H3 settings',()=>{
  const {c,element}=fixture();
  Object.assign(c.state,{customLoras:[{name:'h3-style.safetensors',strength:0.6}]});
  element('#qualityMode').value='turbo_fast';element('#steps').value=4;
  element('#scheduler').value='beta';element('#refImageSize').value='720';element('#memoryOptimization').checked=true;
  c.persistForm();element('#videoModel').value=fast;c.changeVideoModel();
  const request=c.currentSettings();
  assert.equal(request.video_model,fast);assert.equal(request.quality_mode,'native');assert.equal(request.steps,8);
  assert.equal(request.scheduler,'simple');assert.equal(request.ref_image_size,'match');assert.equal(request.memory_optimization,false);
  assert.deepEqual(Array.from(request.custom_loras),[]);
  assert.equal(element('#qualityMode').disabled,true);assert.equal(element('#memoryOptimization').disabled,true);
  element('#videoModel').value='h3';c.changeVideoModel();
  assert.equal(c.currentSettings().video_model,'h3');assert.equal(element('#qualityMode').value,'turbo_fast');
  assert.equal(element('#memoryOptimization').checked,true);assert.equal(c.state.customLoras[0].strength,0.6);
});

test('Fast drafts reload without losing H3 settings and legacy drafts choose H3',()=>{
  const {c,element,storage}=fixture();
  element('#memoryOptimization').checked=true;c.persistForm();element('#videoModel').value=fast;c.changeVideoModel();
  c.state=JSON.parse(storage.get('state'));element('#videoModel').value='';c.restoreForm();
  assert.equal(element('#videoModel').value,fast);
  element('#videoModel').value='h3';c.changeVideoModel();assert.equal(element('#memoryOptimization').checked,true);
  c.state.form={quality_mode:'native'};element('#videoModel').value=fast;c.restoreForm();
  assert.equal(element('#videoModel').value,'h3');assert.equal(c.currentSettings().video_model,'h3');
});

test('Pika and Ref Patch send fixed recipes and restore the original H3 choices after a reload',()=>{
  for (const [model,steps,scheduler] of [['pika_h3_flex',6,'manual'],['h3_ref_patch',20,'simple']]) {
    const {c,element,storage}=fixture();
    c.state.customLoras=[{name:'kept.safetensors',strength:0.5}];
    element('#memoryOptimization').checked=true;element('#qualityMode').value='turbo_fast';
    element('#steps').value=4;element('#scheduler').value='beta';c.persistForm();
    element('#videoModel').value=model;c.changeVideoModel();
    const payload=c.currentSettings();
    assert.equal(payload.video_model,model);assert.equal(payload.steps,steps);assert.equal(payload.scheduler,scheduler);
    assert.equal(payload.quality_mode,'native');assert.equal(payload.memory_optimization,false);assert.equal(payload.custom_loras.length,0);
    assert.equal(element('#qualityMode').disabled,true);assert.equal(element('#scheduler').disabled,true);
    c.state=JSON.parse(storage.get('state'));c.restoreForm();
    assert.equal(element('#videoModel').value,model);
    element('#videoModel').value='h3';c.changeVideoModel();
    assert.equal(element('#qualityMode').value,'turbo_fast');assert.equal(element('#memoryOptimization').checked,true);
    assert.equal(c.state.customLoras[0].name,'kept.safetensors');
  }
  assert.match(html,/<option value="manual" hidden>Pika 專用時間表/);
});

test('dedicated models remain selected when unavailable and Ref Patch explicitly rejects non-reference modes',()=>{
  const {c}=fixture();c.videoModelSupported=true;
  c.videoModelProfiles=[{id:'pika_h3_flex',ready:false,error:'缺少 Pika LoRA'},
    {id:'h3_ref_patch',ready:true,modes:['r2v','replace','popup_panel','mg_animation'],mode_ready:{r2v:true}}];
  assert.equal(c.videoModelSelectionError('pika_h3_flex','r2v'),'缺少 Pika LoRA');
  for (const mode of ['t2v','fl2va','extend','symbol_loop']) assert.match(c.videoModelSelectionError('h3_ref_patch',mode),/僅支援/);
  assert.equal(c.videoModelSelectionError('h3_ref_patch','r2v'),'');
});

test('unknown draft identities remain selected in a real-select contract and history import is explicitly rejected',async()=>{
  const {c,element,storage}=fixture();
  const select=element('#videoModel');select.options=[{value:'h3'},{value:fast}];
  let selected='h3';Object.defineProperty(select,'value',{get:()=>selected,set:value=>{selected=select.options.some(option=>option.value===String(value))?String(value):'';}});
  select.add=option=>select.options.push(option);c.Option=class {constructor(text,value){this.textContent=text;this.value=value;}};
  c.state.form={video_model:'future_video_model',quality_mode:'native'};c.restoreForm();
  assert.equal(select.value,'future_video_model');assert.match(select.options.at(-1).textContent,/不支援/);
  assert.equal(c.currentSettings().video_model,'future_video_model');c.persistForm();
  assert.equal(JSON.parse(storage.get('state')).form.video_model,'future_video_model');
  assert.match(c.videoModelSelectionError('future_video_model'),/尚未支援/);
  c.recipe={request:{video_model:'another_future_model',mode:'t2v'}};
  await assert.rejects(c.applyJobRecipe('unknown'),/未套用設定/);
  assert.equal(select.value,'future_video_model');
});

test('model readiness is independent and old backends cannot silently accept FastH3',()=>{
  const {c}=fixture();
  assert.match(c.videoModelSelectionError(fast),/重新啟動/);assert.equal(c.videoModelSelectionError('h3'),'');
  c.videoModelSupported=true;c.videoModelProfiles=[{id:'h3',ready:true,modes:['t2v']},{id:fast,ready:false,error:'缺少 Fast 模型'}];
  assert.equal(c.videoModelSelectionError('h3','t2v'),'');assert.match(c.videoModelSelectionError(fast),/缺少 Fast/);
  c.videoModelProfiles=[{id:'h3',ready:false,error:'缺少 H3'},{id:fast,ready:true,modes:['t2v','r2v'],mode_ready:{t2v:true,r2v:false}}];
  assert.match(c.videoModelSelectionError('h3'),/缺少 H3/);assert.equal(c.videoModelSelectionError(fast,'t2v'),'');
  assert.match(c.videoModelSelectionError(fast,'r2v'),/尚未就緒/);assert.match(c.videoModelSelectionError(fast,'replace'),/尚未就緒/);
});

test('short-film Fast controls project fixed settings without overwriting saved H3 choices',()=>{
  const {c,element}=fixture();
  c.project={video_model:fast,quality_mode:'turbo_fast',memory_optimization:true,custom_loras:[{name:'kept'}]};
  c.syncShortFilmAcceleration();
  assert.equal(element('#sfQuality').value,'native');assert.equal(element('#sfMemoryOptimization').checked,false);
  assert.equal(element('#sfQuality').disabled,true);
  assert.equal(c.project.quality_mode,'turbo_fast');assert.equal(c.project.memory_optimization,true);assert.equal(c.project.custom_loras[0].name,'kept');
});

test('short-film dedicated model controls preserve stored H3 preferences',()=>{
  for(const model of ['pika_h3_flex','h3_ref_patch']){
    const {c,element}=fixture();
    c.project={video_model:model,quality_mode:'turbo_ref_quality_v11',memory_optimization:true,custom_loras:[{name:'kept'}]};
    const before=JSON.stringify(c.project);c.syncShortFilmAcceleration();
    assert.equal(element('#sfQuality').value,'native');assert.equal(element('#sfQuality').disabled,true);
    assert.equal(element('#sfMemoryOptimization').checked,false);assert.equal(JSON.stringify(c.project),before);
  }
});

test('job reuse restores the actual model and legacy history clears a previous Fast choice',async()=>{
  const {c,element}=fixture();c.state.replacement={};
  for(const selection of [fast,'pika_h3_flex','h3_ref_patch',undefined]){
    c.recipe={request:{mode:'t2v',video_model:selection,quality_mode:'native',steps:selection?8:20},assets:{},job:{}};
    await c.applyJobRecipe('job');
    assert.equal(element('#videoModel').value,selection||'h3');
    assert.equal(c.currentSettings().video_model,selection||'h3');
  }
});

test('changing a short-film model saves the choice and invalidates shots while retaining H3 preferences',()=>{
  const project={video_model:'h3',quality_mode:'turbo_fast',memory_optimization:true,custom_loras:[{name:'saved.safetensors'}],scenes:[{shots:[{id:'one',job_id:'old-job',status:'completed'},{id:'two',job_id:'second-job',status:'queued',continue_previous:true}]}]};
  let handler,rendered=0,saved=0;
  const c=vm.createContext({$:()=>({addEventListener(event,fn){assert.equal(event,'change');handler=fn;}}),
    activeShortFilmProject:()=>project,renderShortFilmWorkspace:()=>rendered++,scheduleShortFilmSave:()=>saved++});
  for(const name of ['shortFilmFlatten','invalidateShortFilmRenders','updateShortFilmGenerationField']){
    const start=source.search(new RegExp(`function ${name}\\(`));
    vm.runInContext(source.slice(start,source.indexOf('\n}',start)+2),c);
  }
  const start=source.indexOf('  $("#sfVideoModel").addEventListener("change"');
  assert.ok(start>=0);vm.runInContext(source.slice(start,source.indexOf('\n  });',start)+6),c);
  handler({target:{value:fast}});
  assert.equal(project.video_model,fast);assert.equal(project.quality_mode,'turbo_fast');assert.equal(project.memory_optimization,true);
  assert.equal(project.custom_loras[0].name,'saved.safetensors');assert.equal(rendered,1);assert.equal(saved,1);
  for(const shot of project.scenes[0].shots){assert.equal(shot.job_id,null);assert.equal(shot.status,'draft');}
});

test('FreeVideo quality survives reload and model switching without overwriting H3 controls',()=>{
  const {c,element,storage}=fixture();
  element('#videoVae').value='fp16';element('#memoryOptimization').checked=true;
  element('#qualityMode').value='turbo_fast';element('#steps').value=4;element('#scheduler').value='beta';
  c.state.customLoras=[{name:'kept.safetensors',strength:0.7}];c.persistForm();
  element('#freevideoQuality').value='max';element('#videoModel').value='freevideo_vdn_h3';c.changeVideoModel();
  const request=c.currentSettings();
  assert.equal(request.freevideo_quality,'max');assert.equal(request.steps,20);
  assert.equal(request.video_vae,'auto');assert.equal(request.memory_optimization,false);assert.equal(request.custom_loras.length,0);
  assert.equal(element('#freevideoQualityWrap').classes.has('hidden'),false);
  for(const id of ['h3QualityWrap','nativeSamplingWrap','videoVaeWrap','memoryOptimizationWrap']) assert.equal(element('#'+id).classes.has('hidden'),true);
  c.state=JSON.parse(storage.get('state'));c.restoreForm();
  assert.equal(element('#videoModel').value,'freevideo_vdn_h3');assert.equal(element('#freevideoQuality').value,'max');
  element('#videoModel').value='h3';c.changeVideoModel();
  assert.equal(element('#qualityMode').value,'turbo_fast');assert.equal(element('#scheduler').value,'simple');
  assert.equal(element('#videoVae').value,'fp16');assert.equal(element('#memoryOptimization').checked,true);
  assert.equal(c.state.customLoras[0].name,'kept.safetensors');assert.equal(element('#freevideoQuality').value,'max');
  assert.equal(element('#freevideoQualityWrap').classes.has('hidden'),true);assert.equal(element('#videoVae').disabled,false);
});

test('FreeVideo history reuse restores its own quality and blocks an unready reference path',async()=>{
  const {c,element}=fixture();c.state.replacement={};
  c.recipe={request:{mode:'t2v',video_model:'freevideo_vdn_h3',freevideo_quality:'high',prompt:'Saved prompt'},assets:{},job:{}};
  await c.applyJobRecipe('saved');assert.equal(c.currentSettings().video_model,'freevideo_vdn_h3');
  assert.equal(c.currentSettings().freevideo_quality,'high');assert.equal(c.currentSettings().steps,16);
  c.videoModelSupported=true;c.videoModelProfiles=[{id:'freevideo_vdn_h3',ready:true,modes:['t2v','r2v'],mode_ready:{t2v:true,r2v:false}}];
  assert.equal(c.videoModelSelectionError('freevideo_vdn_h3','t2v'),'');
  assert.match(c.videoModelSelectionError('freevideo_vdn_h3','r2v'),/尚未就緒/);
  assert.match(c.videoModelHint('freevideo_vdn_h3'),/多模態參考仍屬實驗/);
  assert.equal(element('#videoModel').value,'freevideo_vdn_h3');
});

test('applying FreeVideo history preserves native H3 preferences through reload and return',async()=>{
  const {c,element,storage}=fixture();c.state.replacement={};
  element('#videoVae').value='fp16';element('#memoryOptimization').checked=true;
  element('#qualityMode').value='native';element('#steps').value=28;element('#scheduler').value='beta';element('#refImageSize').value='max';
  c.state.customLoras=[{name:'kept.safetensors',strength:.7}];c.persistForm();
  c.recipe={request:{mode:'t2v',video_model:'freevideo_vdn_h3',freevideo_quality:'high',prompt:'Saved'},assets:{},job:{}};
  await c.applyJobRecipe('saved');
  assert.equal(c.currentSettings().video_model,'freevideo_vdn_h3');assert.equal(c.currentSettings().freevideo_quality,'high');
  c.state=JSON.parse(storage.get('state'));c.restoreForm();
  element('#videoModel').value='h3';c.changeVideoModel();
  assert.equal(element('#videoVae').value,'fp16');assert.equal(element('#memoryOptimization').checked,true);
  assert.equal(element('#qualityMode').value,'native');assert.equal(element('#steps').value,28);assert.equal(element('#scheduler').value,'beta');
  assert.equal(element('#refImageSize').value,'max');assert.equal(c.state.customLoras[0].name,'kept.safetensors');assert.equal(c.state.customLoras[0].strength,.7);
});

test('a newly applied H3 history becomes the preferences preserved by the next dedicated recipe',async()=>{
  const {c,element}=fixture();c.state.replacement={};
  element('#steps').value=28;element('#scheduler').value='beta';element('#videoVae').value='fp16';element('#memoryOptimization').checked=true;
  c.state.customLoras=[{name:'old.safetensors',strength:.7}];c.persistForm();
  element('#videoModel').value='freevideo_vdn_h3';c.changeVideoModel();
  c.recipe={request:{mode:'t2v',video_model:'h3',quality_mode:'native',steps:32,scheduler:'karras',video_vae:'int8',memory_optimization:false,custom_loras:[{name:'new.safetensors',strength:.9}],prompt:'Native recipe'},assets:{},job:{}};
  await c.applyJobRecipe('new-native');
  assert.equal(element('#videoModel').value,'h3');assert.equal(element('#steps').value,32);assert.equal(element('#scheduler').value,'karras');
  assert.equal(element('#videoVae').value,'int8');assert.equal(c.state.customLoras[0].name,'new.safetensors');
  c.recipe={request:{mode:'t2v',video_model:'freevideo_vdn_h3',freevideo_quality:'light',prompt:'Another dedicated recipe'},assets:{},job:{}};
  await c.applyJobRecipe('next-dedicated');element('#videoModel').value='h3';c.changeVideoModel();
  assert.equal(element('#steps').value,32);assert.equal(element('#scheduler').value,'karras');assert.equal(element('#videoVae').value,'int8');
  assert.equal(element('#memoryOptimization').checked,false);assert.equal(c.state.customLoras[0].name,'new.safetensors');assert.equal(c.state.customLoras[0].strength,.9);
});

test('generation review shows the FreeVideo two-pass recipe and preserves the exact sent prompt',()=>{
  const {c}=fixture();const prompt='subject_definitions:\n<Subject 1>\ndetailed_description:\n<d>[Chinese] 原句不改。</d>';
  for(const [quality,steps] of [['light','8＋3 步（兩階段）'],['medium','8＋3 步（兩階段）'],['high','16 步（單階段）'],['max','20 步（單階段）']]) {
    const review=c.generationReviewPromptText({video_model:'freevideo_vdn_h3',freevideo_quality:quality,steps:8,prompt,actual_duration:5.167});
    assert.match(review,/模型：FreeVideo／VDN-H3/);assert.ok(review.includes('採樣步數：'+steps));assert.ok(review.endsWith(prompt));
    assert.ok(review.includes('FreeVideo '+quality[0].toUpperCase()+quality.slice(1)));
  }
});

test('short-film FreeVideo settings expose a separate quality without rewriting stored H3 preferences',()=>{
  const {c,element}=fixture();
  c.project={video_model:'freevideo_vdn_h3',freevideo_quality:'high',quality_mode:'turbo_ref_quality_v11',memory_optimization:true,video_vae:'int8',custom_loras:[{name:'kept'}]};
  const before=JSON.stringify(c.project);c.syncShortFilmAcceleration();
  assert.equal(element('#sfFreevideoQuality').value,'high');assert.equal(element('#sfFreevideoQualityWrap').classes.has('hidden'),false);
  assert.equal(element('#sfVideoVae').disabled,true);assert.equal(element('#sfH3QualityWrap').classes.has('hidden'),true);
  assert.equal(JSON.stringify(c.project),before);
});

test('small-resolution FreeVideo hints disclose equivalent Light and Medium plans in quick and short-film views',()=>{
  const {c,element}=fixture();element('#videoModel').value='freevideo_vdn_h3';element('#megapixels').value='0.4';
  for(const quality of ['light','medium']){
    element('#freevideoQuality').value=quality;c.syncQualityMode(864,480);
    const hint=element('#freevideoQualityHint').textContent;
    assert.match(hint,/小尺寸/);assert.match(hint,/相同/);
    assert.doesNotMatch(hint,/通常比 Light 耗時|第一階段保留較多細節/);
    const review=c.generationReviewPromptText({video_model:'freevideo_vdn_h3',freevideo_quality:quality,width:864,height:480,prompt:'Saved prompt'});
    assert.match(review,/小尺寸的 Light／Medium 可能使用相同計畫/);assert.ok(review.endsWith('Saved prompt'));
    c.project={video_model:'freevideo_vdn_h3',freevideo_quality:quality,megapixels:.4};c.syncShortFilmAcceleration();
    assert.match(element('#sfFreevideoQualityHint').textContent,/小尺寸/);assert.match(element('#sfFreevideoQualityHint').textContent,/相同/);
  }
});

test('FreeVideo storyboards retain exact-guide drafts but explain their unsupported image constraint',()=>{
  const {c,element}=fixture();c.escapeHtml=String;
  const start=source.indexOf('function renderStoryboards(');
  vm.runInContext(source.slice(start,source.indexOf('\n}',start)+2),c);
  c.state.mode='r2v';c.state.storyboards=[{id:'one',duration:2,guideMode:'exact',image:{id:'image',url:'/api/assets/image'}}];
  element('#videoModel').value='freevideo_vdn_h3';c.renderStoryboards();
  assert.match(element('#storyboardHint').textContent,/文字分鏡可用/);
  assert.match(element('#storyboardList').innerHTML,/value="exact" selected disabled/);
  assert.match(element('#storyboardList').innerHTML,/FreeVideo 不支援精確錨定/);
  assert.equal(c.state.storyboards[0].guideMode,'exact');
  assert.match(c.videoModelSelectionError('freevideo_vdn_h3','r2v'),/分鏡圖片精確錨定/);
  c.state.storyboards[0].guideMode='reference';c.renderStoryboards();
  assert.match(element('#storyboardList').innerHTML,/作為柔性構圖參考/);
  element('#videoModel').value='h3';c.state.storyboards[0].guideMode='exact';c.renderStoryboards();
  assert.match(element('#storyboardHint').textContent,/官方 Guide/);
  assert.match(element('#storyboardList').innerHTML,/鎖定 0.000 秒畫面/);
});
