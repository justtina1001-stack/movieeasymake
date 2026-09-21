const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/images.js'), 'utf8');
const a='a'.repeat(32), b='b'.repeat(32), c='c'.repeat(32);

async function fixture({draft={}, status={}, jobs=[], deleteError=false}={}) {
  const elements = new Map(), calls=[];
  let stored = JSON.stringify(draft);
  const defaults={imageMode:'generate',imageWidth:'1024',imageHeight:'1024',imageSteps:'25',imageSeed:'42',referenceResolution:'1024',imageLoraStrength:'1'};
  function el(id) {
    if(!elements.has(id)) elements.set(id, {
      value:defaults[id] || '', type:['imageSeedAuto','imageTransparent'].includes(id)?'checkbox':'text', checked:id==='imageSeedAuto',
      innerHTML:'',textContent:'',disabled:false,listeners:{},dataset:{},
      classList:{toggle(){},add(){},remove(){}},focus(){},querySelectorAll(){return [];},
      addEventListener(type,handler){this.listeners[type]=handler;},showModal(){this.open=true;},close(){this.open=false;}
    });
    return elements.get(id);
  }
  const context=vm.createContext({document:{getElementById:el,hidden:false},localStorage:{getItem:()=>stored,setItem:(_,s)=>stored=s},setInterval(){},FormData,
    async fetch(url,options) {
      calls.push({url,options});
      if(options?.method === 'DELETE') return {ok:!deleteError,json:async()=>deleteError?{error:'刪除失敗'}:{deleted:true}};
      const data = url === '/api/images/status' ? {ready:true,lora_supported:true,loras:[],...status} :
        url.startsWith('/api/images/jobs?page=') ? {items:jobs,page:1,total_pages:1} :
        url === '/api/queue' ? {available:true,jobs:{},running_count:0,pending_count:0,local_waiting_count:0} :
        url.endsWith('/reference') ? {id:c} : {seed:17,id:'job'};
      return {ok:true,json:async()=>data};
    }
  });
  vm.runInContext(source,context);
  await new Promise(setImmediate);
  const fire=async(id,type,event={})=>{await el(id).listeners[type]({preventDefault(){},...event});};
  const click=(id,dataset)=>fire(id,'click',{target:{closest:()=>({dataset,disabled:false})}});
  return {el,fire,click,calls,draft:()=>JSON.parse(stored)};
}

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
