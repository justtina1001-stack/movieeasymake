const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const helperSource = fs.readFileSync(path.join(__dirname,'../static/lora-help.js'),'utf8');
const appSource = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const wushu = 'h3studio_custom/Wushu-Ref2VA-v7.safetensors';
const combat = 'h3studio_custom/CombatV2.safetensors';
const unknown = 'h3studio_custom/unknown.safetensors';
const builtin = 'h3_turbo.safetensors';
const inventory = {names:[wushu,combat,unknown],folder:'h3studio_custom',mode:'local',items:[
  {name:wushu,title:'武術動作',purpose:'武術／連續動作',family:'ref2va',trigger:'wushu_action',trigger_required:true,usage:'強度由使用者選擇',limits:'需角色參考；不保證動作',source_url:'https://example.org/wushu',known:true},
  {name:combat,title:'戰鬥動作',purpose:'較激烈的戰鬥',family:'both',trigger:'',usage:'選用詞 prfight2 由作者建議',limits:'未經本機畫質驗證',source_url:'https://example.org/combat',known:true},
],builtin_items:[{name:builtin,title:'內建 Turbo',purpose:'減少採樣步數',family:'ref2va',known:true,manual_selectable:false,trigger:'',usage:'由生成品質管理',limits:'適用 Ref2VA',source_url:'https://example.org/turbo'}]};

function helpers() {
  const context=vm.createContext({URL});
  vm.runInContext(helperSource,context);
  return context.H3LoraHelp;
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }

test('every listed adapter has honest metadata and builtin adapters stay read-only',()=>{
  const h=helpers(), data=h.catalog({...inventory,names:[...inventory.names,builtin]});
  assert.deepEqual(plain(data.names),[wushu,combat,unknown]);
  assert.equal(data.items.length,3);
  assert.equal(data.builtin_items.length,1);
  const record=h.item(data,unknown);
  assert.equal(record.known,false);
  assert.match(record.purpose,/尚未登錄/);
  assert.equal(h.family(record),null);
  assert.equal(record.source_url,'');
  for(const record of [...data.items,...data.builtin_items]) {
    const html=h.html(record);
    for(const label of ['用途','適用','觸發詞','用法','限制','來源']) assert.ok(html.includes(`<dt>${label}</dt>`));
  }
});

test('required Wushu trigger is distinguished from optional Combat direction',()=>{
  const h=helpers(),data=h.catalog(inventory);
  assert.equal(h.family(h.item(data,wushu)),'ref2va');
  assert.match(h.html(h.item(data,wushu)),/wushu_action（啟用時必需，生成時自動加入）/);
  assert.match(h.html(h.item(data,combat)),/未指定必需觸發詞/);
  assert.match(h.html(h.item(data,combat)),/選用詞 prfight2/);
  const optional=h.catalog({names:['optional'],items:[{name:'optional',known:true,trigger:'optional_word',trigger_required:false,family:'both'}]});
  assert.match(h.html(h.item(optional,'optional')),/不自動加入/);
});

test('legacy inventory preserves unknown names without inferring from filenames',()=>{
  const h=helpers(),data=h.catalog({names:['Super-Ref2VA-Combat.safetensors']});
  const record=h.item(data,data.names[0]);
  assert.equal(h.family(record),null);
  assert.match(record.purpose,/不依檔名推測/);
  assert.match(h.html(record),/適用模型尚未登錄/);
  assert.equal(record.trigger,'');
});

test('Qwen metadata uses image applicability without claiming compatibility from its folder',()=>{
  const h=helpers(),name='qwen_image_2_1\\style.safetensors';
  const data=h.catalog({loras:[name],lora_items:[{name:name.replaceAll('\\','/'),known:false}]},{model:'qwen'});
  assert.equal(data.items.length,1);
  assert.match(h.html(data.items[0]),/作者須明確支援 Qwen-Image-2.1/);
  assert.equal(h.family(data.items[0]),null);
  assert.doesNotMatch(h.html(data.items[0]),/FL2VA/);
});

test('metadata text is escaped and only safe explicit web sources become links',()=>{
  const h=helpers();
  const name='"/><img src=x onerror=alert(1)>';
  for(const url of ['javascript:alert(1)','data:text/html,<script>','//example.org','https://user:pass@example.org','https://example.org\\bad','https://example.org\n/bad']) {
    const record=h.item(h.catalog({names:[name],items:[{name,title:'<script>alert(1)</script>',purpose:'<iframe>',known:true,source_url:url}]}),name);
    const html=h.html(record);
    assert.doesNotMatch(html,/<script>|<iframe>|<img|href=/);
    assert.match(html,/&lt;script&gt;/);
  }
  const html=h.html(h.item(h.catalog(inventory),wushu));
  assert.match(html,/href="https:\/\/example.org\/wushu"/);
  assert.match(html,/target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(h.html({...h.item(h.catalog(inventory),wushu),source_url:'javascript:alert(1)'}),/href=/);
});

test('builtin availability is displayed without representing absent versions as installed',()=>{
  const h=helpers();
  for (const installed of [true,false]) {
    const data=h.catalog({...inventory,builtin_items:[{...inventory.builtin_items[0],installed}]});
    assert.match(h.html(data.builtin_items[0]),installed ? /目前引擎已找到這個版本/ : /目前引擎尚未找到這個版本/);
  }
});

test('DOM renderer inserts metadata as text nodes and safe links',()=>{
  const h=helpers();
  const document={createElement(tag){return {tag,children:[],textContent:'',append(...children){this.children.push(...children);}};}};
  const parent={ownerDocument:document,replaceChildren(...children){this.children=children;}};
  const record=h.item(h.catalog({names:['test'],items:[{name:'test',known:true,title:'<img src=x>',purpose:'<script>bad</script>',family:'both',source_url:'https://example.org/help'}]}),'test');
  const details=h.render(parent,record);
  assert.equal(details.tag,'details');
  assert.equal(details.children[0].children[0].textContent,'<img src=x>');
  assert.equal(details.children[0].children[1].textContent,'<script>bad</script>');
  const link=details.children[1].children.at(-1).children[0];
  assert.equal(link.href,'https://example.org/help');
  assert.equal(link.rel,'noopener noreferrer');
});

function panels({quick=[],shortfilm=[]}={}) {
  const elements=new Map(),saves={quick:0,sf:0},calls=[];
  let response=inventory, failure=null;
  const element=selector=>{
    if(!elements.has(selector)) elements.set(selector,{innerHTML:'',listeners:{},addEventListener(type,handler){this.listeners[type]=handler;}});
    return elements.get(selector);
  };
  const context=vm.createContext({URL,structuredClone,state:{customLoras:plain(quick)},project:{custom_loras:plain(shortfilm)},
    $(selector,parent){return parent ? {open:true} : element(selector);},
    activeShortFilmProject(){return context.project;},
    escapeHtml(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));},
    scheduleShortFilmSave(){saves.sf++;},persistForm(){saves.quick++;},toast(){},
    async api(url){calls.push(url);if(failure)throw new Error(failure);return typeof response === 'function' ? response() : structuredClone(response);},
  });
  vm.runInContext(helperSource,context);
  vm.runInContext(appSource.slice(appSource.indexOf('let loraCatalog = null;'),appSource.indexOf('function randomVideoSeed(')),context);
  vm.runInContext('bindLoraPanels(); renderLoraPanels();',context);
  const panel=scope=>element(scope==='sf'?'#sfLoraPanel':'#quickLoraPanel');
  const change=(scope,field,value,index=0)=>panel(scope).listeners.change({target:{value,checked:value,dataset:field==='picker'?{}:{loraField:field},hasAttribute:name=>field==='picker'&&name==='data-lora-picker',closest:()=>({dataset:{loraRow:String(index)}})}});
  const click=(scope,attribute,index=0)=>panel(scope).listeners.click({target:{closest:()=>({disabled:false,dataset:{loraRemove:String(index)},hasAttribute:name=>name===`data-lora-${attribute}`})}});
  return {context,element,saves,calls,change,click,setResponse(value){response=value;},fail(value){failure=value;},rows(scope){return plain(scope==='sf'?context.project.custom_loras:context.state.customLoras);}};
}

test('refreshing shows all adapters and builtin help without selecting or saving any adapter',async()=>{
  const f=panels(); await f.click('quick','refresh');
  assert.deepEqual(f.rows('quick'),[]);assert.deepEqual(f.rows('sf'),[]);
  assert.deepEqual(f.saves,{quick:0,sf:0});
  for(const id of ['#quickLoraPanel','#sfLoraPanel']) {
    const html=f.element(id).innerHTML;
    assert.match(html,/武術／連續動作/);assert.match(html,/較激烈的戰鬥/);assert.match(html,/用途尚未登錄/);
    assert.doesNotMatch(html,/value="h3_turbo.safetensors"/);
  }
  for(const id of ['#qualityBuiltinLoraHelp','#sfBuiltinLoraHelp']) assert.match(f.element(id).innerHTML,/內建 Turbo/);
});

test('picker remains a preview choice until explicitly added then applies known family',async()=>{
  const f=panels();await f.click('quick','refresh');
  await f.click('quick','add');assert.deepEqual(f.rows('quick'),[]);
  f.change('quick','picker',wushu);
  assert.deepEqual(f.rows('quick'),[]);assert.equal(f.saves.quick,0);
  await f.click('quick','add');
  assert.deepEqual(f.rows('quick'),[{name:wushu,strength:0.5,family:'ref2va',enabled:true}]);
  assert.equal(f.saves.quick,1);assert.deepEqual(f.rows('sf'),[]);
});

test('name changes use known family but preserve strength and enabled state; unknown does not guess',async()=>{
  const f=panels({quick:[{name:unknown,strength:0.7,family:'fl2va',enabled:false}]});await f.click('quick','refresh');
  f.change('quick','name',wushu);
  assert.deepEqual(f.rows('quick'),[{name:wushu,strength:0.7,family:'ref2va',enabled:false}]);
  f.change('quick','name',unknown);
  assert.equal(f.rows('quick')[0].family,'ref2va');
  f.change('quick','family','both');f.change('quick','strength',0);f.change('quick','enabled',true);
  assert.deepEqual(f.rows('quick'),[{name:unknown,strength:0,family:'both',enabled:true}]);
});

test('shortfilm editing and remove controls stay independent from quick settings',async()=>{
  const f=panels({quick:[{name:unknown,strength:0.9,family:'both',enabled:true}]});await f.click('sf','refresh');
  f.change('sf','picker',combat);await f.click('sf','add');
  assert.equal(f.rows('sf')[0].family,'both');assert.equal(f.saves.sf,1);assert.equal(f.saves.quick,0);
  await f.click('sf','remove');assert.deepEqual(f.rows('sf'),[]);assert.equal(f.saves.sf,2);
  assert.equal(f.rows('quick')[0].strength,0.9);
});

test('refresh failures and changed catalogs retain draft rows and known cached help',async()=>{
  const row={name:wushu,strength:0.65,family:'both',enabled:false};
  const f=panels({quick:[row],shortfilm:[row]});await f.click('quick','refresh');
  assert.deepEqual(f.rows('quick'),[row]);assert.deepEqual(f.rows('sf'),[row]);
  f.fail('掃描失敗');await f.click('quick','refresh');
  assert.match(f.element('#quickLoraPanel').innerHTML,/掃描失敗/);
  assert.match(f.element('#quickLoraPanel').innerHTML,/武術／連續動作/);
  assert.deepEqual(f.rows('quick'),[row]);assert.equal(f.saves.quick,0);
  f.fail(null);f.setResponse({names:[],folder:'new',mode:'remote'});await f.click('quick','refresh');
  assert.deepEqual(f.rows('quick'),[row]);assert.match(f.element('#quickLoraPanel').innerHTML,/目前引擎未找到/);
});

test('inflight catalog refresh never overwrites edits or a newly selected shortfilm project',async()=>{
  const row={name:unknown,strength:0.5,family:'both',enabled:true};
  const f=panels({quick:[row],shortfilm:[row]});await f.click('quick','refresh');
  let resolve;f.setResponse(()=>new Promise(done=>{resolve=done;}));
  const pending=f.click('quick','refresh');
  f.change('quick','strength',0.8);f.change('quick','enabled',false);
  const originalProject=f.context.project;
  f.context.project={custom_loras:[{name:combat,strength:0.25,family:'fl2va',enabled:false}]};
  resolve(inventory);await pending;
  assert.deepEqual(f.rows('quick'),[{...row,strength:0.8,enabled:false}]);
  assert.deepEqual(f.rows('sf'),[{name:combat,strength:0.25,family:'fl2va',enabled:false}]);
  assert.deepEqual(plain(originalProject.custom_loras),[row]);
  assert.deepEqual(f.saves,{quick:2,sf:0});
});

test('duplicate and fifth adapters cannot be added',async()=>{
  const rows=[wushu,combat,unknown,'fourth'].map(name=>({name,strength:0.5,family:'both',enabled:true}));
  const f=panels({quick:rows});await f.click('quick','refresh');
  f.change('quick','picker',wushu);await f.click('quick','add');
  assert.equal(f.rows('quick').length,4);assert.equal(f.saves.quick,0);
  f.change('quick','name',combat,0);assert.equal(f.rows('quick')[0].name,wushu);
});

test('all relevant pages load the shared helper before their application scripts',()=>{
  for(const [page,script] of [['index','app'],['images','images']]) {
    const html=fs.readFileSync(path.join(__dirname,`../static/${page}.html`),'utf8');
    assert.ok(html.indexOf('/static/lora-help.js?') < html.indexOf(`/static/${script}.js?`));
    assert.match(html,/lora-help.css\?v=20261005-lora-help/);
  }
});
