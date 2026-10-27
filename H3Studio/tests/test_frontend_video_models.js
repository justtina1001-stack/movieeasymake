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
    if (!elements.has(key)) elements.set(key, {value:'',checked:false,disabled:false,textContent:'',
      options:[{value:'native'},{value:'turbo'},{value:'turbo_fast'}],classList:{toggle(){},add(){},remove(){}},
      scrollIntoView(){}, addEventListener(){}});
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
  for (const name of ['currentSettings','persistForm','restoreForm','videoModelSelectionError','videoModelHint','syncVideoModelStatus','changeVideoModel','syncQualityMode','syncShortFilmAcceleration','recipeAsset','originalGenerationRequest','applyJobRecipe']) {
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

test('quick and short-film pages offer H3 and FastH3 with labelled status',()=>{
  for(const id of ['videoModel','sfVideoModel']){
    const select=html.match(new RegExp(`<select id="${id}" aria-describedby="${id}Hint">([\\s\\S]*?)</select>`));
    assert.ok(select,id);
    assert.deepEqual([...select[1].matchAll(/value="([^"]+)"/g)].map(m=>m[1]),['h3',fast]);
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

test('job reuse restores the actual model and legacy history clears a previous Fast choice',async()=>{
  const {c,element}=fixture();c.state.replacement={};
  for(const selection of [fast,undefined]){
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
