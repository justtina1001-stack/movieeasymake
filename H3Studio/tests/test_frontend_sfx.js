const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const sfx = require('../static/sfx.js');
const editor = require('../static/editor.js');
const id = digit => digit.repeat(32);

test('sound effect requests preserve Chinese prompt and full seed with strict duration validation', () => {
  const request = sfx.cleanRequest({job_name:' 法術 ',prompt:' 一聲重擊，接著是金屬回響。 ',duration:'5.5',seed:'9007199254740991',api_key:'not retained'});
  assert.deepEqual(request,{job_name:'法術',prompt:'一聲重擊，接著是金屬回響。',duration:5.5,seed:'9007199254740991'});
  for (const duration of [0,31,Infinity,'wrong','']) assert.throws(()=>sfx.cleanRequest({...request,duration}),/1 到 30/);
  for (const seed of ['-1','1.5','9007199254740992','18446744073709551616','1e5','']) assert.throws(()=>sfx.cleanRequest({...request,seed}),/Seed/);
  assert.throws(()=>sfx.cleanRequest({...request,prompt:' '}),/音效描述/);
  assert.throws(()=>sfx.cleanRequest({...request,prompt:'a'.repeat(2001)}),/2,000/);
});

test('editor transfer requires an explicit existing project or a new project and rejects forged identifiers', () => {
  assert.equal(sfx.editorUrl(id('a')),`/editor?sfx=${id('a')}&new=1`);
  assert.equal(sfx.editorUrl(id('a'),id('b')),`/editor?sfx=${id('a')}&project=${id('b')}`);
  assert.deepEqual(editor.soundEffectTransfer(`?sfx=${id('a')}&new=1`),{jobId:id('a'),projectId:null,newProject:true});
  assert.deepEqual(editor.soundEffectTransfer(`?sfx=${id('a')}&project=${id('b')}`),{jobId:id('a'),projectId:id('b'),newProject:false});
  for (const search of [`?sfx=${id('a')}`,`?sfx=${id('a')}&new=1&project=${id('b')}`,'?sfx=../unsafe&new=1']) assert.throws(()=>editor.soundEffectTransfer(search));
  assert.throws(()=>sfx.editorUrl(id('a'),'javascript:alert(1)'));
  assert.equal(editor.soundEffectTransfer('?project=existing'),null);
});

const project = () => ({name:'原剪輯',width:1280,height:720,fps:24,clips:[],audio_clips:[],overlays:[]});
const audio = () => ({id:id('c'),kind:'audio',duration:5});
test('sound effects added to an empty project get a playable canvas without mutating the input', () => {
  const original = project(), saved = JSON.stringify(original);
  const result = editor.appendSoundEffect(original,audio(),'new_sfx');
  assert.equal(JSON.stringify(original),saved);
  assert.equal(result.canvas_duration,5);
  assert.equal(result.audio_clips.length,1);
  assert.deepEqual(result.audio_clips[0],{id:'new_sfx',media_id:id('c'),in:0,out:5,start:0,track:0,volume:1,speed:1,fade_in:0,fade_out:0});
  assert.equal(editor.totalDuration(result),5);
});

test('sound effects use an unoccupied audio lane and preserve existing video and soundtrack settings', () => {
  const original = project();
  original.clips = [{id:'video',media_id:id('d'),in:0,out:10,volume:0.6,speed:1}];
  original.audio_clips = [{id:'music',media_id:id('e'),in:0,out:8,start:0,track:0,volume:0.4,speed:1,fade_in:1,fade_out:2}];
  const result = editor.appendSoundEffect(original,audio(),'new_sfx');
  assert.deepEqual(result.clips,original.clips);
  assert.deepEqual(result.audio_clips[0],original.audio_clips[0]);
  assert.equal(result.audio_clips[1].track,1);
  assert.equal(result.canvas_duration,undefined);
  const full = project();
  full.audio_clips=Array.from({length:4},(_,track)=>({id:`track_${track}`,media_id:id('e'),in:0,out:8,start:0,track,volume:1,speed:1,fade_in:0,fade_out:0}));
  assert.throws(()=>editor.appendSoundEffect(full,audio(),'new_sfx'),/音軌/);
  assert.equal(full.audio_clips.length,4);
});

test('completed effects can be auditioned and transferred; pending work only offers cancellation', () => {
  const completed = sfx.historyMarkup([{id:id('a'),status:'completed',name:'法術',prompt:'落雷',duration:5,seed:'9'}]);
  assert.match(completed,/<audio controls preload="none"/);
  assert.match(completed,/data-sfx-editor=/);
  assert.match(completed,/\/api\/sfx\/jobs\/[a-f0-9]+\/audio\?download=1/);
  const pending = sfx.historyMarkup([{id:id('a'),status:'queued',progress:0}]);
  assert.match(pending,/data-sfx-cancel=/);
  assert.doesNotMatch(pending,/<audio|data-sfx-editor=/);
});

test('sound effect history escapes untrusted text and skips invalid identifiers', () => {
  const markup = sfx.historyMarkup([{id:id('a'),status:'failed',name:'<img onerror=x>',prompt:'<script>x</script>',error:'<svg onload=x>'},{id:'../invalid',status:'completed'}]);
  assert.doesNotMatch(markup,/<img|<script|<svg|invalid/);
  assert.match(markup,/&lt;img/); assert.match(markup,/&lt;script/); assert.match(markup,/&lt;svg/);
});

test('performance labels distinguish job elapsed time, model inference and allocated model memory', () => {
  assert.equal(sfx.performanceText({execution_seconds:12,performance:{inference_seconds:8,peak_vram_allocated_bytes:2147483648}}),'生成耗時 12.0 秒 · 推論 8.0 秒 · 模型顯存峰值 2.00 GiB');
  assert.equal(sfx.performanceText({performance:{elapsed_seconds:null,inference_seconds:NaN,peak_vram_allocated_bytes:-1}}),'');
});

test('all workspaces expose the sound effect entry and refresh the shared navigation cache', () => {
  const ui = fs.readFileSync(path.join(__dirname,'../static/workspace_ui.js'),'utf8');
  assert.match(ui,/\["sfx", "音效", "\/\?tool=sfx"\]/);
  const home = fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  assert.match(home,/id="sfxModal"/); assert.match(home,/id="sfxEditorProject"/);
  assert.match(home,/sfx\.js\?v=20261030-sfx/);
  for (const file of ['index','editor','images','scripts']) {
    const html=fs.readFileSync(path.join(__dirname,`../static/${file}.html`),'utf8');
    assert.match(html,/workspace_ui\.js\?v=20261030-sfx/);
  }
});

function workspaceFixture() {
  const elements = new Map(), stored = new Map(), requests = [];
  function element(id) {
    if (!elements.has(id)) {
      const attrs = new Map(), classes = new Set(['hidden']), listeners = new Map();
      elements.set(id, {id,value:'',textContent:'',innerHTML:'',dataset:{},disabled:false,open:false,
        classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)},
        setAttribute:(name,value)=>attrs.set(name,value),removeAttribute:name=>attrs.delete(name),hasAttribute:name=>attrs.has(name),
        addEventListener(name,callback){listeners.set(name,callback);},querySelectorAll(){return [];},focus(){},append(){},showModal(){this.open=true;},close(){this.open=false;},
        emit(name,event={}){return listeners.get(name)?.(event);},
      });
    }
    return elements.get(id);
  }
  let post = async () => ({id:id('a')});
  const win = {document:{getElementById:element,createElement:tag=>tag==='template'?{innerHTML:'',content:{children:[]}}:{value:'',textContent:''}},
    localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},
    crypto:{randomUUID:()=>`${requests.length.toString(16).padStart(8,'0')}-0000-0000-0000-000000000000`},
    addEventListener(){},setInterval(){return 1;},clearInterval(){},toast(){},
    async fetch(url,options){
      requests.push({url,options});
      let value;
      if (url==='/api/sfx/status') value={installed:true,available:true,connection_mode:'local'};
      else if (url.startsWith('/api/sfx/jobs?page=')) value={items:[],page:1,total_pages:1};
      else if (url==='/api/sfx/jobs') value=await post(JSON.parse(options.body));
      else if (url==='/api/editor/projects') value={projects:[{id:id('b'),name:'我的影片'}]};
      else throw Error('Unexpected '+url);
      return {ok:!value?.httpError,status:value?.httpError||200,json:async()=>value};
    },
  };
  const workspace=sfx.install(win);
  element('sfxPrompt').value='金屬撞擊後的回響'; element('sfxDuration').value='5'; element('sfxSeed').value='123';
  return {workspace,element,requests,stored,setPost:value=>{post=value;}};
}

test('the actual sound effect submitter stays guarded during status polling and submits only once', async()=>{
  const ui=workspaceFixture(); await ui.workspace.status();
  let finish; ui.setPost(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=ui.workspace.generate();
  await ui.workspace.status(); await ui.workspace.generate();
  assert.equal(ui.element('generateSfx').disabled,true);
  assert.equal(ui.requests.filter(item=>item.url==='/api/sfx/jobs').length,1);
  finish({id:id('a')}); await pending;
  assert.equal(ui.element('generateSfx').disabled,false);
  assert.equal(ui.element('generateSfx').hasAttribute('aria-busy'),false);
  assert.equal(ui.element('sfxSeed').value,'123');
});

test('ambiguous network failure reuses the submission receipt and edits create a distinct request',async()=>{
  const ui=workspaceFixture(); await ui.workspace.status();
  const payloads=[];
  ui.setPost(async payload=>{payloads.push(payload);if(payloads.length<3)throw Error('connection lost');return {id:id('a')};});
  await ui.workspace.generate(); await ui.workspace.generate();
  assert.equal(payloads[0].request_id,payloads[1].request_id);
  ui.element('sfxPrompt').value='輕輕敲擊木桌'; await ui.workspace.generate();
  assert.notEqual(payloads[2].request_id,payloads[1].request_id);
  assert.match(ui.stored.get('h3studio-sfx-draft-v1'),/輕輕敲擊木桌/);
});

test('sound effect editor selection defaults to a separate new project, then honors an explicit choice',async()=>{
  const ui=workspaceFixture(); await ui.workspace.openEditor(id('a'));
  assert.equal(ui.element('sfxOpenEditor').href,`/editor?sfx=${id('a')}&new=1`);
  ui.element('sfxEditorProject').value=id('b'); ui.element('sfxEditorProject').emit('change');
  assert.equal(ui.element('sfxOpenEditor').href,`/editor?sfx=${id('a')}&project=${id('b')}`);
  assert.equal(ui.requests.filter(item=>item.options?.method==='POST').length,0);
});
