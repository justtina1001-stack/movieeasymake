const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/images.js'), 'utf8');
const loraSource = fs.readFileSync(path.join(__dirname, '../static/lora-help.js'), 'utf8');
const a='a'.repeat(32), b='b'.repeat(32), c='c'.repeat(32);
const variants = (standard=true, turbo=true) => ({model_variant_supported:true, variants:[
  {id:'standard',label:'原版',ready:standard,steps:25,model_filename:'standard.safetensors',error:standard ? '' : '原版模型尚未安裝'},
  {id:'turbo',label:'官方 Turbo',ready:turbo,steps:8,model_filename:'turbo.safetensors',error:turbo ? '' : 'Turbo 模型尚未安裝'},
]});
const fluxVariants = (flux=true, standard=true, turbo=true) => {
  const status=variants(standard,turbo);
  status.variants.push({id:'flux3',label:'FLUX 3 Image Klein',ready:flux,steps:16,modes:['edit'],reference_limit:1,transparent_supported:false,lora_supported:false,error:flux?'':'FLUX 3 模型尚未安裝'});
  return status;
};
const imageJob = extra => ({id:a,name:'來源圖片',mode:'generate',status:'completed',width:1024,height:1024,steps:25,seed:42,prompt:'來源描述',transparent:false,reference_resolution:1024,image_asset_ids:[],...extra});
function deferred() { let resolve; const promise=new Promise(done=>{resolve=done;}); return {promise,resolve}; }

async function fixture({draft={}, status={}, jobs=[], deleteError=false, upscaleStatus={}, referenceWait=null}={}) {
  const elements = new Map(), calls=[];
  let stored = JSON.stringify(draft);
  const defaults={imageMode:'generate',imageModel:'standard',imageWidth:'1024',imageHeight:'1024',imageSteps:'25',imageSeed:'42',referenceResolution:'1024',imageLoraStrength:'1'};
  function el(id) {
    if(!elements.has(id)) elements.set(id, {
      value:defaults[id] || '', type:['imageSeedAuto','imageTransparent'].includes(id)?'checkbox':'text', checked:id==='imageSeedAuto',
      innerHTML:'',textContent:'',disabled:false,listeners:{},dataset:{},
      classList:{toggle(){},add(){},remove(){}},focus(){},querySelectorAll(){return [];},
      addEventListener(type,handler){this.listeners[type]=handler;},showModal(){this.open=true;},close(){this.open=false;}
    });
    return elements.get(id);
  }
  let statusError = false, statusData = status, jobsData = jobs;
  const context=vm.createContext({document:{getElementById:el,hidden:false},localStorage:{getItem:()=>stored,setItem:(_,s)=>stored=s},setInterval(){},FormData,URL,
    async fetch(url,options) {
      calls.push({url,options});
      if (url === '/api/images/status' && statusError) throw new Error('連線失敗');
      if(options?.method === 'DELETE') return {ok:!deleteError,json:async()=>deleteError?{error:'刪除失敗'}:{deleted:true}};
      if(url.endsWith('/reference') && referenceWait) return {ok:true,json:async()=>referenceWait};
      const data = url === '/api/images/status' ? {ready:true,lora_supported:true,loras:[],...statusData} :
        url === '/api/images/upscale/status' ? {ready:true,models:[{id:'general',installed:true},{id:'anime',installed:true}],...upscaleStatus} :
        url.startsWith('/api/images/jobs?page=') ? {items:jobsData,page:1,total_pages:1} :
        url === '/api/queue' ? {available:true,jobs:{},running_count:0,pending_count:0,local_waiting_count:0} :
        url.endsWith('/reference') ? {id:c} : {seed:17,id:'job'};
      return {ok:true,json:async()=>data};
    }
  });
  vm.runInContext(loraSource,context);
  vm.runInContext(source,context);
  await new Promise(setImmediate);
  const fire=async(id,type,event={})=>{await el(id).listeners[type]({preventDefault(){},...event});};
  const click=(id,dataset)=>fire(id,'click',{target:{closest:()=>({dataset,disabled:false})}});
  return {el,fire,click,calls,draft:()=>JSON.parse(stored),setStatusError:()=>{statusError=true;},setStatus:value=>{statusData=value;statusError=false;},setJobs:value=>{jobsData=value;}};
}

test('legacy drafts and jobs select standard while an old backend blocks Turbo without changing the draft',async()=>{
  const f=await fixture({draft:{imagePrompt:'舊稿',imageSteps:'33'},jobs:[imageJob({})]});
  assert.equal(f.el('imageModel').value,'standard'); assert.equal(f.el('imageSteps').value,'33'); assert.equal(f.el('generateImage').disabled,false);
  await f.fire('imageForm','submit');
  const legacy=JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body);
  assert.equal(legacy.model_variant,undefined); assert.equal(legacy.steps,33);
  f.el('imageModel').value='turbo'; await f.fire('imageModel','input');
  assert.equal(f.el('generateImage').disabled,true); assert.match(f.el('imageModelStatus').textContent,/重新啟動 Studio/);
  const before=f.draft(); await f.fire('imageForm','submit'); assert.deepEqual(f.draft(),before);
  assert.equal(f.calls.filter(call=>call.url==='/api/images/jobs').length,1);
  await f.click('imageJobs',{reuse:a}); assert.equal(f.el('imageModel').value,'standard'); assert.equal(f.el('imageSteps').value,'25');
});

test('Turbo submits fixed eight steps without LoRA and restores standard steps and LoRA across switching and draft reload',async()=>{
  const name='qwen_image_2_1/style.safetensors',status={...variants(),loras:[name]};
  const f=await fixture({status,draft:{imagePrompt:'原稿',imageSteps:'37',imageLora:name,imageLoraStrength:'0.65'}});
  f.el('imageModel').value='turbo'; await f.fire('imageModel','input');
  assert.equal(f.el('imageSteps').value,'8'); assert.equal(f.el('imageSteps').disabled,true); assert.equal(f.el('imageLora').disabled,true); assert.equal(f.el('imageLoraStrength').disabled,true);
  assert.match(f.el('loraStatus').textContent,/Turbo 已含加速，回原版可使用 LoRA/);
  assert.equal(f.draft().imageModel,'turbo'); assert.equal(f.draft().standardImageSteps,'37'); assert.equal(f.draft().imageLora,name); assert.equal(f.draft().imageLoraStrength,'0.65');
  f.el('imageSteps').value='50'; await f.fire('imageForm','submit');
  const payload=JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body);
  assert.equal(payload.model_variant,'turbo'); assert.equal(payload.steps,8); assert.equal(payload.lora_name,''); assert.equal(payload.lora_strength,0);
  const saved=f.draft(); await f.fire('refreshStatus','click'); assert.deepEqual(f.draft(),saved); assert.equal(f.el('imageModel').value,'turbo');
  const restored=await fixture({draft:saved,status}); assert.equal(restored.el('imageModel').value,'turbo'); assert.equal(restored.el('imageSteps').value,'8');
  restored.el('imageModel').value='standard'; await restored.fire('imageModel','input');
  assert.equal(restored.el('imageSteps').value,'37'); assert.equal(restored.el('imageSteps').disabled,false); assert.equal(restored.el('imageLora').value,name); assert.equal(restored.el('imageLoraStrength').value,'0.65');
  await restored.fire('imageForm','submit'); const original=JSON.parse(restored.calls.find(call=>call.url==='/api/images/jobs').options.body);
  assert.equal(original.model_variant,'standard'); assert.equal(original.steps,37); assert.equal(original.lora_name,name); assert.equal(original.lora_strength,0.65);
});

test('overall ready cannot submit an unavailable selected profile or a missing profile inventory',async()=>{
  for(const config of [
    {status:variants(false,true),draft:{imageModel:'standard'},error:/原版模型尚未安裝/},
    {status:variants(true,false),draft:{imageModel:'turbo'},error:/Turbo 模型尚未安裝/},
    {status:{model_variant_supported:true,variants:[]},draft:{imageModel:'standard'},error:/尚未就緒/},
  ]) {
    const f=await fixture(config); assert.equal(f.el('generateImage').disabled,true); assert.match(f.el('imageModelStatus').textContent,config.error);
    await f.fire('imageForm','submit'); assert.equal(f.calls.some(call=>call.url==='/api/images/jobs'),false);
  }
  const f=await fixture({status:variants(false,true)}); f.el('imageModel').value='turbo'; await f.fire('imageModel','input');
  assert.equal(f.el('generateImage').disabled,false); await f.fire('imageForm','submit');
  assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body).model_variant,'turbo');
});

test('refreshing profile readiness never resets the selected model steps or saved standard LoRA',async()=>{
  const name='qwen_image_2_1/not-installed.safetensors';
  const f=await fixture({status:variants(),draft:{imageModel:'turbo',imageSteps:'8',standardImageSteps:'41',imageLora:name,imageLoraStrength:'0.8'}});
  assert.equal(f.el('generateImage').disabled,false); const saved=f.draft();
  for(const status of [variants(true,false),{model_variant_supported:false},variants(false,true)]) {
    f.setStatus(status); await f.fire('refreshStatus','click'); assert.equal(f.el('imageModel').value,'turbo'); assert.equal(f.el('imageSteps').value,'8'); assert.deepEqual(f.draft(),saved);
  }
  assert.equal(f.el('generateImage').disabled,false);
  f.el('imageModel').value='standard'; await f.fire('imageModel','input'); assert.equal(f.el('imageSteps').value,'41'); assert.equal(f.el('imageLora').value,name); assert.equal(f.el('generateImage').disabled,true);
});

test('continue and reuse explicitly restore the source generation model instead of the current selector',async()=>{
  const name='qwen_image_2_1/style.safetensors';
  for(const action of ['continue','reuse']) {
    const turbo=await fixture({status:{...variants(),loras:[name]},draft:{imageSteps:'31',imageLora:name,imageLoraStrength:'0.6'},jobs:[imageJob({model_variant:'turbo',steps:8})]});
    await turbo.click('imageJobs',{[action]:a}); assert.equal(turbo.el('imageModel').value,'turbo'); assert.equal(turbo.el('imageSteps').value,'8'); assert.equal(turbo.draft().standardImageSteps,'31'); assert.equal(turbo.draft().imageLora,name);
    await turbo.fire('imageForm','submit'); const fast=JSON.parse(turbo.calls.find(call=>call.url==='/api/images/jobs').options.body); assert.equal(fast.model_variant,'turbo'); assert.equal(fast.lora_strength,0);
    const standard=await fixture({status:{...variants(),loras:[name]},draft:{imageModel:'turbo',imageSteps:'8',standardImageSteps:'38'},jobs:[imageJob({steps:40,lora_name:name,lora_strength:0.55})]});
    await standard.click('imageJobs',{[action]:a}); assert.equal(standard.el('imageModel').value,'standard'); assert.equal(standard.el('imageSteps').value,'40'); assert.equal(standard.el('imageLora').value,name);
    await standard.fire('imageForm','submit'); const original=JSON.parse(standard.calls.find(call=>call.url==='/api/images/jobs').options.body); assert.equal(original.model_variant,'standard'); assert.equal(original.steps,40); assert.equal(original.lora_strength,0.55);
  }
});

test('continue keeps the clicked source model and metadata when job polling replaces the page during reference import',async()=>{
  const name='qwen_image_2_1/style.safetensors';
  for(const config of [
    {job:imageJob({model_variant:'turbo',steps:8}),selected:'standard',expected:'turbo',replacement:[]},
    {job:imageJob({steps:40,lora_name:name,lora_strength:0.55}),selected:'turbo',expected:'standard',replacement:[]},
    {job:imageJob({mode:'upscale',source_model_variant:'turbo',steps:null,seed:null}),selected:'standard',expected:'turbo',replacement:[imageJob({model_variant:'standard',name:'已替換的來源'})]},
  ]) {
    const pending=deferred(), job={...config.job,name:'點選時的圖片',transparent:true,reference_resolution:512};
    const f=await fixture({status:{...variants(),loras:[name]},draft:{imageModel:config.selected,standardImageSteps:'31',imageSteps:config.selected === 'turbo' ? '8' : '31'},jobs:[job],referenceWait:pending.promise});
    const importing=f.click('imageJobs',{continue:a}); assert.equal(f.calls.some(call=>call.url===`/api/images/jobs/${a}/reference`),true);
    f.setJobs(config.replacement); await f.fire('refreshJobs','click');
    pending.resolve({id:c}); await importing;
    assert.equal(f.el('imageModel').value,config.expected); assert.equal(f.el('imageName').value,'點選時的圖片 · 編輯');
    assert.equal(f.el('imageTransparent').checked,true); assert.equal(f.el('referenceResolution').value,512); assert.deepEqual(f.draft().refs,[{id:c}]);
    if(config.expected === 'standard') { assert.equal(f.el('imageSteps').value,'40'); assert.equal(f.el('imageLora').value,name); }
    else assert.equal(f.el('imageSteps').value,'8');
    await f.fire('imageForm','submit'); const payload=JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body);
    assert.equal(payload.model_variant,config.expected); assert.equal(payload.reference_resolution,512); assert.equal(payload.transparent,true);
    if(config.expected === 'standard') { assert.equal(payload.lora_name,name); assert.equal(payload.lora_strength,0.55); }
  }
});

test('upscale results use their recorded source variant for editing and never treat the upscale model as a generation profile',async()=>{
  for(const [source,expected] of [[undefined,'standard'],['turbo','turbo'],['standard','standard']]) {
    const f=await fixture({status:variants(),draft:{imageModel:expected === 'turbo' ? 'standard' : 'turbo'},jobs:[imageJob({mode:'upscale',model:'turbo',source_model_variant:source,steps:null,seed:null,scale:2})]});
    assert.match(f.el('imageJobs').innerHTML,/模型：Real-ESRGAN/);
    await f.click('imageJobs',{continue:a}); assert.equal(f.el('imageModel').value,expected);
    await f.fire('imageForm','submit'); assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body).model_variant,expected);
  }
});

test('result cards and details distinguish Qwen models legacy and Real-ESRGAN models',async()=>{
  const f=await fixture({jobs:[imageJob({model_variant:'turbo'}),imageJob({id:b}),imageJob({id:c,mode:'upscale',scale:2,source_model_variant:'turbo'})]});
  assert.match(f.el('imageJobs').innerHTML,/模型：Qwen 官方 Turbo · 快速 8 步/); assert.match(f.el('imageJobs').innerHTML,/模型：Qwen 原版 · 精細生成/); assert.match(f.el('imageJobs').innerHTML,/模型：Real-ESRGAN · 增強放大/);
  const html=fs.readFileSync(path.join(__dirname,'../static/images.html'),'utf8'); assert.match(html,/id="imageModel"/); assert.match(html,/images.js\?v=20261027-flux3/); assert.match(html,/<option value="flux3">FLUX 3 Image Klein/);
});

test('FLUX selection preserves Qwen options across reload and submits only its fixed edit settings',async()=>{
  const name='qwen_image_2_1/style.safetensors';
  const f=await fixture({status:{...fluxVariants(),loras:[name]},draft:{imagePrompt:'只改杯色',imageName:'比較圖',imageMode:'generate',imageSteps:'37',imageTransparent:true,referenceResolution:'512',imageLora:name,imageLoraStrength:'0.65',editTemplate:'cutout',refs:[{id:a}]}});
  f.el('imageModel').value='flux3'; await f.fire('imageModel','input');
  assert.equal(f.el('imageMode').value,'edit'); assert.equal(f.el('imageMode').disabled,true); assert.equal(f.el('imageSteps').value,'16'); assert.equal(f.el('imageSteps').disabled,true);
  assert.equal(f.el('imageTransparent').checked,false); assert.equal(f.el('imageTransparent').disabled,true); assert.equal(f.el('imageLora').disabled,true); assert.equal(f.el('referenceResolution').disabled,true);
  assert.equal(f.el('editTemplate').value,''); assert.equal(f.el('editStyleOption').disabled,true); assert.equal(f.el('editCutoutOption').disabled,true);
  assert.equal(f.el('imagePrompt').value,'只改杯色'); assert.equal(f.el('imageName').value,'比較圖'); assert.deepEqual(f.draft().refs,[{id:a}]);
  assert.match(f.el('imageLicenseText').textContent,/Kommunity/); assert.match(f.el('imageLicenseLink').href,/flux-3-image-klein/);
  const saved=f.draft(); assert.equal(saved.qwenModelSettings.mode,'generate'); assert.equal(saved.qwenModelSettings.transparent,true); assert.equal(saved.qwenModelSettings.referenceResolution,'512');
  await f.fire('refreshStatus','click'); assert.deepEqual(f.draft(),saved); assert.equal(f.el('imageModel').value,'flux3');
  f.setStatus(fluxVariants(false)); await f.fire('refreshStatus','click'); assert.equal(f.el('generateImage').disabled,true); assert.deepEqual(f.draft(),saved);
  f.setStatus(fluxVariants()); await f.fire('refreshStatus','click'); assert.equal(f.el('generateImage').disabled,false); assert.equal(f.el('imageModel').value,'flux3');
  f.el('imageSteps').value='50'; f.el('imageTransparent').checked=true; f.el('referenceResolution').value='2048';
  await f.fire('imageForm','submit');
  const payload=JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body);
  assert.equal(payload.model_variant,'flux3'); assert.equal(payload.mode,'edit'); assert.equal(payload.steps,16); assert.equal(payload.transparent,false); assert.equal(payload.lora_name,''); assert.equal(payload.lora_strength,0); assert.equal(payload.reference_resolution,1024); assert.equal(payload.width,1024); assert.equal(payload.height,1024); assert.deepEqual(payload.image_asset_ids,[a]);
  const restored=await fixture({status:{...fluxVariants(),loras:[name]},draft:saved});
  assert.equal(restored.el('imageModel').value,'flux3'); assert.equal(restored.el('imageMode').value,'edit');
  restored.el('imageModel').value='standard'; await restored.fire('imageModel','input');
  assert.equal(restored.el('imageMode').value,'generate'); assert.equal(restored.el('imageMode').disabled,false); assert.equal(restored.el('imageSteps').value,'37'); assert.equal(restored.el('imageTransparent').checked,true);
  assert.equal(restored.el('referenceResolution').value,'512'); assert.equal(restored.el('imageLora').value,name); assert.equal(restored.el('imageLoraStrength').value,'0.65'); assert.equal(restored.el('editTemplate').value,'cutout');
  assert.match(restored.el('imageLicenseText').textContent,/Qwen/); assert.match(restored.el('imageLicenseLink').href,/Qwen-Image-2.1/);
});

test('FLUX readiness is separate from Qwen and an old or unavailable backend cannot send it as standard',async()=>{
  for(const status of [fluxVariants(false),variants(),{ready:true,model_variant_supported:false}]) {
    const f=await fixture({status,draft:{imageModel:'flux3',imagePrompt:'改圖',refs:[{id:a}]}});
    assert.equal(f.el('generateImage').disabled,true); assert.match(f.el('imageModelStatus').textContent,/FLUX 3/);
    await f.fire('imageForm','submit'); assert.equal(f.calls.some(call=>call.url==='/api/images/jobs'),false);
  }
  const f=await fixture({status:{...fluxVariants(true,false,false),lora_supported:false},draft:{imageModel:'flux3',refs:[{id:a}]}});
  assert.equal(f.el('generateImage').disabled,false); await f.fire('imageForm','submit');
  assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body).model_variant,'flux3');
});

test('switching a multi-reference Qwen draft to FLUX retains every reference and blocks submission until one is chosen',async()=>{
  const f=await fixture({status:fluxVariants(),draft:{imageMode:'edit',imagePrompt:'保留原稿',refs:[{id:a},{id:b}]}});
  f.el('imageModel').value='flux3'; await f.fire('imageModel','input');
  assert.deepEqual(f.draft().refs,[{id:a},{id:b}]); assert.equal(f.el('generateImage').disabled,true); assert.match(f.el('referenceStatus').textContent,/目前保留 2 張/);
  await f.fire('imageForm','submit'); assert.equal(f.calls.some(call=>call.url==='/api/images/jobs'),false);
  f.el('imageModel').value='standard'; await f.fire('imageModel','input'); assert.equal(f.el('generateImage').disabled,false); assert.deepEqual(f.draft().refs,[{id:a},{id:b}]);
  f.el('imageModel').value='flux3'; await f.fire('imageModel','input'); await f.click('referenceList',{remove:'1'});
  assert.equal(f.el('generateImage').disabled,false); assert.equal(f.el('imagePrompt').value,'保留原稿');
  await f.fire('imageForm','submit'); assert.deepEqual(JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body).image_asset_ids,[a]);
});

test('FLUX upload and template limits reject unsupported actions without erasing the current draft',async()=>{
  const f=await fixture({status:fluxVariants(),draft:{imageModel:'flux3',imagePrompt:'原始修改',refs:[{id:a}]}});
  await f.fire('referenceDrop','drop',{dataTransfer:{files:[{name:'extra.png'}]}});
  assert.equal(f.calls.some(call=>call.url==='/api/assets'),false); assert.match(f.el('formMessage').textContent,/只支援 1 張/); assert.deepEqual(f.draft().refs,[{id:a}]);
  await f.click('imageJobs',{reference:'job'}); assert.equal(f.calls.some(call=>call.url.endsWith('/reference')),false);
  for(const key of ['style','cutout']) { f.el('editTemplate').value=key; await f.fire('insertTemplate','click'); assert.equal(f.el('imagePrompt').value,'原始修改'); assert.equal(f.el('imageTransparent').checked,false); }
});

test('FLUX history retains model identity for reuse continue editing and upscale sources',async()=>{
  for(const action of ['reuse','continue']) {
    const f=await fixture({status:fluxVariants(),draft:{imageMode:'generate',imageTransparent:true,imageSteps:'34',imageWidth:'640',imageHeight:'960'},jobs:[imageJob({model_variant:'flux3',mode:'edit',steps:16,image_asset_ids:[a],original_prompt:'把杯子改藍色'})]});
    assert.match(f.el('imageJobs').innerHTML,/模型：FLUX 3 Image Klein/);
    await f.click('imageJobs',{[action]:a}); assert.equal(f.el('imageModel').value,'flux3'); assert.equal(f.el('imageMode').value,'edit'); assert.equal(f.el('imageSteps').value,'16');
    assert.equal(f.el('imageTransparent').checked,false); assert.equal(f.draft().qwenModelSettings.mode,'generate'); assert.equal(f.draft().qwenModelSettings.transparent,true);
    assert.equal(f.el('imagePrompt').value,action==='reuse'?'把杯子改藍色':''); await f.fire('imageForm','submit'); assert.equal(JSON.parse(f.calls.find(call=>call.url==='/api/images/jobs').options.body).model_variant,'flux3');
    f.el('imageModel').value='standard'; await f.fire('imageModel','input'); assert.equal(f.el('imageWidth').value,'640'); assert.equal(f.el('imageHeight').value,'960');
  }
  const f=await fixture({status:fluxVariants(),jobs:[imageJob({mode:'upscale',source_model_variant:'flux3',steps:null,seed:null})]});
  await f.click('imageJobs',{continue:a}); assert.equal(f.el('imageModel').value,'flux3'); assert.equal(f.el('imageMode').value,'edit');
});

test('FLUX selected against an older two-model backend requests a Studio restart',async()=>{
  const f=await fixture({status:variants(),draft:{imageModel:'flux3',imageMode:'edit',imagePrompt:'改成藍色',refs:[{id:a}]}});
  assert.equal(f.el('generateImage').disabled,true);
  assert.match(f.el('imageModelStatus').textContent,/重新啟動 Studio/);
  assert.doesNotMatch(f.el('imageModelStatus').textContent,/請確認已安裝此模型/);
  await f.fire('imageForm','submit');
  assert.equal(f.calls.some(call=>call.url==='/api/images/jobs'),false);
});

test('promoting a reference changes submitted order and preserves the prompt',async()=>{
  const f=await fixture({draft:{imageMode:'edit',imagePrompt:'保留圖 1',refs:[{id:a},{id:b}]}});
  await f.click('referenceList',{primary:'1'});
  assert.deepEqual(f.draft().refs.map(r=>r.id),[b,a]);
  await f.fire('imageForm','submit');
  const payload=JSON.parse(f.calls.find(c=>c.url==='/api/images/jobs').options.body);
  assert.deepEqual(payload.image_asset_ids,[b,a]);
  assert.equal(payload.prompt,'保留圖 1');
});

test('continue editing replaces the old references and clears the previous edit instruction',async()=>{
  const f=await fixture({draft:{imageMode:'edit',imagePrompt:'舊指令',refs:[{id:a},{id:b}]},jobs:[{id:'job',name:'完成圖',status:'completed',reference_resolution:512}]});
  await f.click('imageJobs',{continue:'job'});
  assert.deepEqual(f.draft().refs,[{id:c}]);
  assert.equal(f.el('imagePrompt').value,'');
  assert.equal(f.el('imageMode').value,'edit');
  assert.equal(f.el('imageSeedAuto').checked,true);
  assert.equal(f.el('referenceResolution').value,512);
});

test('adding a secondary reference preserves the existing main image and prompt',async()=>{
  const f=await fixture({draft:{imagePrompt:'下一步',refs:[{id:a}]}});
  await f.click('imageJobs',{reference:'job'});
  assert.deepEqual(f.draft().refs.map(r=>r.id),[a,c]);
  assert.equal(f.el('imagePrompt').value,'下一步');
});

test('comparison uses the job source, not the currently selected draft reference',async()=>{
  const f=await fixture({draft:{refs:[{id:b}]},jobs:[{id:'job',name:'Edit',image_asset_ids:[a]}]});
  await f.click('imageJobs',{compare:'job'});
  assert.equal(f.el('compareBefore').src,`/api/assets/${a}`);
  assert.equal(f.el('compareAfter').src,'/api/images/jobs/job/image');
  assert.equal(f.el('imageCompare').open,true);
});

test('missing adapter and old backend block submission instead of silently dropping LoRA',async()=>{
  for(const status of [{lora_supported:false},{loras:[]}]) {
    const f=await fixture({draft:{imageLora:'qwen_image_2_1/style.safetensors'},status});
    assert.equal(f.el('generateImage').disabled,true);
    await f.fire('imageForm','submit');
    assert.equal(f.calls.some(c=>c.url==='/api/images/jobs'),false);
  }
});

test('selected engine adapter and strength are included in the job',async()=>{
  const name='qwen_image_2_1\\角色.safetensors';
  const f=await fixture({draft:{imagePrompt:'角色',imageLora:name,imageLoraStrength:'0.65'},status:{loras:[name]}});
  await f.fire('imageForm','submit');
  const payload=JSON.parse(f.calls.find(c=>c.url==='/api/images/jobs').options.body);
  assert.equal(payload.lora_name,name);assert.equal(payload.lora_strength,0.65);
});

test('image LoRA metadata shows all purposes without changing selected adapter or draft',async()=>{
  const name='qwen_image_2_1/style.safetensors',other='qwen_image_2_1/unknown.safetensors';
  const draft={imagePrompt:'保留原稿',imageLora:name,imageLoraStrength:'0.65'};
  const f=await fixture({draft,status:{loras:[name,other],lora_items:[{name,title:'水彩風格',purpose:'柔和水彩',known:true,family:'qwen-image-2.1',trigger:'',usage:'依作者設定',limits:'須相容Qwen',source_url:'https://example.org/model'}]}});
  assert.equal(f.el('imageLora').value,name);assert.equal(f.el('imageLoraStrength').value,'0.65');
  assert.match(f.el('imageLoraHelp').innerHTML,/柔和水彩/);
  assert.match(f.el('imageLoraCatalog').innerHTML,/尚未登錄/);
  assert.match(f.el('imageLoraHelp').innerHTML,/noopener noreferrer/);
  for (const [field,value] of Object.entries(draft)) assert.equal(f.draft()[field],value);
  const saved=f.draft();await f.fire('refreshStatus','click');assert.deepEqual(f.draft(),saved);
});

test('legacy image adapters get unknown help and unavailable old selection stays intact',async()=>{
  const name='qwen_image_2_1/old.safetensors',other='qwen_image_2_1/new.safetensors';
  const f=await fixture({draft:{imageLora:name,imageLoraStrength:'0.4',imagePrompt:'原稿'},status:{loras:[other]}});
  assert.equal(f.el('imageLora').value,name);
  assert.match(f.el('imageLoraHelp').innerHTML,/用途尚未登錄/);
  assert.match(f.el('loraStatus').textContent,/不在目前引擎/);
  assert.equal(f.el('generateImage').disabled,true);
});

test('failed image inventory refresh preserves metadata selection and draft',async()=>{
  const name='qwen_image_2_1/style.safetensors',draft={imageLora:name,imageLoraStrength:'0.7',imagePrompt:'原稿'};
  const f=await fixture({draft,status:{loras:[name],lora_items:[{name,title:'已讀風格',purpose:'已讀用途',known:true}]}});
  const help=f.el('imageLoraHelp').innerHTML;
  const saved=f.draft();
  f.setStatusError();await f.fire('refreshStatus','click');
  assert.equal(f.el('imageLoraHelp').innerHTML,help);
  assert.equal(f.el('imageLora').value,name);assert.equal(f.el('imageLoraStrength').value,'0.7');
  assert.deepEqual(f.draft(),saved);
  assert.equal(f.el('generateImage').disabled,true);
});

test('unsafe image LoRA metadata cannot inject markup or an executable source link',async()=>{
  const name='qwen_image_2_1/unsafe.safetensors';
  const f=await fixture({draft:{imageLora:name},status:{loras:[name],lora_items:[{name,known:true,title:'<img src=x>',purpose:'<script>alert(1)</script>',source_url:'javascript:alert(1)'}]}});
  assert.doesNotMatch(f.el('imageLoraHelp').innerHTML,/<img|<script|href=/);
  assert.match(f.el('imageLoraHelp').innerHTML,/&lt;img/);
});

test('edit templates append without erasing text and style template requires a second image',async()=>{
  const f=await fixture({draft:{imagePrompt:'原始內容',refs:[{id:a}]}});
  f.el('editTemplate').value='style';await f.fire('insertTemplate','click');
  assert.equal(f.el('imagePrompt').value,'原始內容');
  assert.match(f.el('formMessage').textContent,/需要圖 1/);
  f.el('editTemplate').value='cutout';await f.fire('insertTemplate','click');
  assert.match(f.el('imagePrompt').value,/原始內容\n擷取/);
  assert.equal(f.el('imageTransparent').checked,true);
});

test('oversized dropped batch is rejected before upload',async()=>{
  const f=await fixture({draft:{refs:[{id:a}]}});
  await f.fire('referenceDrop','drop',{dataTransfer:{files:Array.from({length:10},()=>({name:'test.png'}))}});
  assert.match(f.el('formMessage').textContent,/10 張/);
  assert.equal(f.calls.some(c=>c.url==='/api/assets'),false);
});


test('delete cancellation sends no request and preserves the card',async()=>{
  const f=await fixture({jobs:[{id:a,status:'completed',name:'圖片'}]});
  await f.click('imageJobs',{delete:a});
  assert.equal(f.el('deleteImageDialog').open,true);
  await f.fire('cancelImageDelete','click');
  assert.equal(f.calls.some(c=>c.options?.method==='DELETE'),false);
  assert.match(f.el('imageJobs').innerHTML,/刪除圖片/);
});

test('delete targets the chosen job and stale polling cannot resurrect it or alter references',async()=>{
  const f=await fixture({jobs:[{id:a,status:'completed',name:'圖片'},{id:b,status:'completed',name:'其他圖片'}],draft:{refs:[{id:c}]}});
  await f.click('imageJobs',{delete:a});
  await f.fire('confirmImageDelete','click');
  const calls=f.calls.filter(c=>c.options?.method==='DELETE');
  assert.equal(calls.length,1);assert.equal(calls[0].url,`/api/images/jobs/${a}`);
  assert.equal(f.el('imageJobs').innerHTML.includes(`data-job-id="${a}"`),false);
  assert.equal(f.el('imageJobs').innerHTML.includes(`data-job-id="${b}"`),true);
  assert.deepEqual(f.draft().refs,[{id:c}]);
});

test('failed deletion leaves the card visible and reports error',async()=>{
  const f=await fixture({jobs:[{id:a,status:'completed',name:'圖片'}],deleteError:true});
  await f.click('imageJobs',{delete:a});
  await f.fire('confirmImageDelete','click');
  assert.match(f.el('deleteImageError').textContent,/刪除失敗/);
  assert.equal(f.el('deleteImageDialog').open,true);
  assert.equal(f.el('imageJobs').innerHTML.includes(`data-job-id="${a}"`),true);
});

test('active image jobs show cancel but not delete',async()=>{
  const f=await fixture({jobs:[{id:a,status:'running',name:'圖片'}]});
  assert.equal(f.el('imageJobs').innerHTML.includes('data-delete'),false);
  assert.equal(f.el('imageJobs').innerHTML.includes('data-cancel'),true);
});


test('upscale preview shows exact dimensions and submits a separate job without changing the draft',async()=>{
  const f=await fixture({jobs:[{id:a,name:'測試',status:'completed',width:512,height:768}],draft:{imagePrompt:'保留原提示'}});
  await f.click('imageJobs',{upscale:a});
  assert.equal(f.el('upscaleDialog').open,true);
  assert.match(f.el('upscaleSize').textContent,/512 × 768 → 1024 × 1536/);
  assert.equal(f.el('submitUpscale').disabled,false);
  f.el('upscaleScale').value='4';await f.fire('upscaleScale','input');
  await f.fire('submitUpscale','click');
  const call=f.calls.find(c=>c.url===`/api/images/jobs/${a}/upscale`);
  assert.deepEqual(JSON.parse(call.options.body),{scale:4,model:'general'});
  assert.equal(f.el('imagePrompt').value,'保留原提示');
});

test('upscaling a standard source never copies the current Turbo selection into its separate upscale request',async()=>{
  const f=await fixture({status:variants(),jobs:[imageJob({width:512,height:512,model_variant:'standard'})],draft:{imageModel:'turbo',standardImageSteps:'36',imagePrompt:'保留Turbo草稿'}});
  const before=f.draft(); await f.click('imageJobs',{upscale:a}); await f.fire('submitUpscale','click');
  const call=f.calls.find(item=>item.url===`/api/images/jobs/${a}/upscale`); assert.deepEqual(JSON.parse(call.options.body),{scale:2,model:'general'});
  assert.deepEqual(f.draft(),before); assert.equal(f.el('imageModel').value,'turbo');
});

test('oversized upscale and missing model block submission',async()=>{
  for(const config of [{jobs:[{id:a,width:3000,height:2000}]},{jobs:[{id:a,width:512,height:512}],upscaleStatus:{ready:false,error:'後端尚未更新'}}]) {
    const f=await fixture(config);await f.click('imageJobs',{upscale:a});
    assert.equal(f.el('submitUpscale').disabled,true);
    await f.fire('submitUpscale','click');
    assert.equal(f.calls.some(c=>c.url.endsWith('/upscale')),false);
  }
});

test('upscale results expose compare and further editing without invalid seed reuse',async()=>{
  const f=await fixture({jobs:[{id:a,mode:'upscale',scale:2,status:'completed',image_asset_ids:[b]}]});
  const html=f.el('imageJobs').innerHTML;
  assert.match(html,/2 倍增強放大/);assert.match(html,/data-compare/);assert.match(html,/data-continue/);
  assert.equal(html.includes('data-reuse'),false);assert.equal(html.includes('Seed null'),false);
});
