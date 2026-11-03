const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function install(context, ...names) {
  for (const name of names) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
}

function transport() {
  const timers = new Map(), calls = [];
  let serial = 0, mode = 'pending';
  const context = vm.createContext({ AbortController,
    setTimeout(fn, delay) { const id = ++serial; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      calls.push({url, options});
      if (mode === 'ready') return Promise.resolve({ok:true, json:async()=>({ready:true})});
      return new Promise((resolve, reject) => options.signal?.addEventListener('abort', () => reject(new Error('aborted')), {once:true}));
    },
  });
  install(context, 'api');
  return {context, timers, calls, ready() {mode = 'ready';}};
}

test('a stalled read is bounded and a later read can recover without retrying the request', async () => {
  const f = transport(), pending = f.context.api('/api/status');
  assert.equal(f.calls[0].options.cache, 'no-store');
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 15000);
  [...f.timers.values()][0].fn();
  await assert.rejects(pending);
  assert.equal(f.timers.size, 0);
  assert.equal(f.calls.length, 1, 'no implicit retry');
  f.ready();
  assert.equal((await f.context.api('/api/status')).ready, true);
});

test('submissions and uploads have no new short timeout or implicit retry', async () => {
  const f = transport();
  f.ready();
  for (const options of [{method:'POST',body:'generation'}, {method:'POST',body:'upload'}, {method:'DELETE'}]) {
    await f.context.api('/api/jobs', options);
    assert.equal(f.calls.at(-1).options.signal, undefined);
    assert.equal(f.timers.size, 0);
  }
  assert.equal(f.calls.length, 3);
});

test('a caller-owned cancellation signal keeps its original deadline', async () => {
  const f = transport(), controller = new AbortController();
  const pending = f.context.api('/api/queue', {signal:controller.signal});
  assert.equal(f.calls[0].options.signal, controller.signal);
  assert.equal(f.timers.size, 0);
  controller.abort(); await assert.rejects(pending);
});

test('a longer status budget remains bounded and is never forwarded as a fetch option', async () => {
  const f = transport(), pending = f.context.api('/api/status', {readTimeoutMs:30000});
  assert.equal([...f.timers.values()][0].delay,30000);
  assert.equal('readTimeoutMs' in f.calls[0].options,false);
  [...f.timers.values()][0].fn(); await assert.rejects(pending);
});

test('successful mutation responses without JSON retain the existing API behavior', async () => {
  const context=vm.createContext({fetch:async()=>({ok:true,status:204,json:async()=>{throw new SyntaxError('empty');}})});
  install(context,'api');
  assert.equal(Object.keys(await context.api('/api/jobs/example',{method:'DELETE'})).length,0);
});

function statusFixture() {
  const elements = new Map();
  const el = key => {
    if (!elements.has(key)) elements.set(key, {textContent:'',disabled:false,className:'',classList:{add(){},remove(){},toggle(){}}});
    return elements.get(key);
  };
  let calls = 0, rejectRequest;
  const inventory = {video_vae_int8:true}, profiles = [{id:'pika_h3_flex',ready:true}];
  const context = vm.createContext({$:el,statusLoading:false,engineStartingAt:0,
    engineModelInventory:inventory,videoModelProfiles:profiles,videoModelSupported:true,
    syncVideoModelStatus(){},syncVideoVaeHints(){},syncQualityMode(){},syncShortFilmAcceleration(){},
    dimensions:()=>[864,480],applyStudioRole(){},
    api() { calls++; return new Promise((resolve,reject)=>{rejectRequest=reject;}); },
  });
  install(context, 'checkStatus');
  return {context, el, inventory, profiles, calls:()=>calls, fail:()=>rejectRequest(new Error('offline'))};
}

test('simultaneous status checks coalesce and transient failure preserves model selections and inventory', async () => {
  const f = statusFixture(), first = f.context.checkStatus();
  const second = f.context.checkStatus();
  assert.equal(f.calls(), 1);
  await second;
  f.fail(); await first;
  assert.equal(f.context.statusLoading, false);
  assert.equal(f.context.engineModelInventory, f.inventory);
  assert.equal(f.context.videoModelProfiles, f.profiles);
  assert.equal(f.context.videoModelSupported, true);
  assert.match(f.el('#statusText').textContent, /暫時|重新連線/);
  const retry = f.context.checkStatus();
  assert.equal(f.calls(), 2);
  f.fail(); await retry;
});

test('video history polling releases its guard after failure and does not overlap reads', async () => {
  let calls = 0, rejectRequest;
  const context = vm.createContext({jobsLoading:false,jobsPoll:null,jobSearch:'',jobPage:1,
    console:{warn(){}},api(){calls++;return new Promise((resolve,reject)=>{rejectRequest=reject;});},
  });
  install(context, 'loadJobs', 'loadJobsPage');
  const first = context.loadJobs(), second = context.loadJobs();
  assert.equal(calls, 1);
  rejectRequest(new Error('offline')); await Promise.all([first,second]);
  assert.equal(context.jobsPoll, null);
  const retry = context.loadJobs(); assert.equal(calls, 2);
  rejectRequest(new Error('offline')); await retry;
});

function deferred() { let resolve; const promise=new Promise(done=>{resolve=done;}); return {promise,resolve}; }
const historyJob = (id,name) => ({id,name,status:'completed',mode:'t2v',width:864,height:480,duration:5,created_at:'2026-11-03T00:00:00Z'});
function historyFixture() {
  const elements=new Map(), reads=[];
  const el=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:'',disabled:false,classList:{toggle(){}}});return elements.get(key);};
  const context=vm.createContext({jobsLoading:false,jobsPoll:null,jobSearch:'',jobPage:1,jobTotalPages:3,lastJobOptionsLoad:Date.now(),lastJobsSignature:'',
    $:el,$$:()=>[],console:{warn(){}},expandedJobIds:new Set(),modeLabels:{t2v:'文生影片'},
    renderContinuationJobs(){},jobExecutionLabel:()=>'',queueJobBadgeHtml:()=>'',queueJobProgressHtml:()=>'',batchSegmentsHtml:()=>'',videoJobErrorHtml:()=>'',escapeHtml:String,
    api(url){if(url==='/api/jobs/options')return Promise.resolve([]);const wait=deferred();reads.push({url,...wait});return wait.promise;},
  });
  install(context,'loadJobs','loadJobsPage');
  return {context,el,reads};
}

for(const view of ['page','search']) {
  test(`an in-flight video poll cannot overwrite a newer ${view} and await waits for its final DOM`,async()=>{
    const f=historyFixture(), old=f.context.loadJobs();
    if(view==='page')f.context.jobPage=2;else f.context.jobSearch='new-job';
    let completed=false;const latest=f.context.loadJobs(true).then(()=>{completed=true;});
    assert.equal(f.reads.length,1,'requests never overlap');
    f.reads[0].resolve({items:[historyJob('old','舊結果')],page:1,total_pages:3,total:40});
    await new Promise(setImmediate);
    assert.equal(f.reads.length,2);
    assert.match(f.reads[1].url,view==='page'?/page=2/:/q=new-job/);
    assert.equal(completed,false,'forced callers wait for the follow-up read');
    assert.doesNotMatch(f.el('#jobList').innerHTML,/舊結果/);
    f.reads[1].resolve({items:[historyJob('new-job','最新結果')],page:view==='page'?2:1,total_pages:3,total:40});
    await Promise.all([old,latest]);
    assert.equal(completed,true);assert.match(f.el('#jobList').innerHTML,/最新結果/);
    assert.equal(f.context.jobPage,view==='page'?2:1);
  });
}

test('a forced refresh during the same video query performs one follow-up read before locating a new job',async()=>{
  const f=historyFixture(), first=f.context.loadJobs();
  let ready=false;const latest=f.context.loadJobs(true).then(()=>{ready=true;});
  f.context.loadJobs(true); // A second user action coalesces into the same follow-up.
  f.reads[0].resolve({items:[],page:1,total_pages:1,total:0});await new Promise(setImmediate);
  assert.equal(f.reads.length,2);assert.equal(ready,false);
  f.reads[1].resolve({items:[historyJob('new-job','剛送出的工作')],page:1,total_pages:1,total:1});
  await Promise.all([first,latest]);
  assert.equal(ready,true);assert.match(f.el('#jobList').innerHTML,/new-job/);assert.equal(f.reads.length,2);
});

test('a query changed while continuation options load cannot commit the old page or old cards',async()=>{
  const f=historyFixture(), options=deferred(), send=f.context.api;
  let firstOptions=true;
  f.context.api=url=>{if(url==='/api/jobs/options' && firstOptions){firstOptions=false;return options.promise;}return send(url);};
  const first=f.context.loadJobs(true);
  f.reads[0].resolve({items:[historyJob('old','舊結果')],page:1,total_pages:3,total:40});await new Promise(setImmediate);
  f.context.jobPage=2;const latest=f.context.loadJobs(true);
  options.resolve([]);await new Promise(setImmediate);
  assert.equal(f.reads.length,2);assert.match(f.reads[1].url,/page=2/);
  assert.equal(f.context.jobPage,2);assert.doesNotMatch(f.el('#jobList').innerHTML,/舊結果/);
  f.reads[1].resolve({items:[historyJob('new','第二頁')],page:2,total_pages:3,total:40});
  await Promise.all([first,latest]);assert.match(f.el('#jobList').innerHTML,/第二頁/);
});

for(const [name,guard] of [['loadInstallerStatus','installerStatusLoading'],['loadModelUpdateStatus','modelUpdateLoading']]) {
  test(`${name} coalesces background reads and releases its guard after a failed probe`,async()=>{
    let calls=0,rejectRequest;
    const context=vm.createContext({[guard]:false,api(){calls++;return new Promise((resolve,reject)=>{rejectRequest=reject;});}});
    install(context,name);
    const first=context[name](), second=context[name]();
    assert.equal(calls,1); await second;
    rejectRequest(new Error('offline')); await assert.rejects(first);
    assert.equal(context[guard],false);
    const retry=context[name](); assert.equal(calls,2);
    rejectRequest(new Error('offline')); await assert.rejects(retry);
  });
}

test('resuming a visible video page refreshes read-only status and history without submitting work', () => {
  const calls=[];
  const context = vm.createContext({document:{hidden:true},studioWorkspace:'quick',
    checkStatus(){calls.push('status');},loadSharedQueue(){calls.push('queue');},loadJobs(){calls.push('jobs');},
  });
  install(context, 'refreshStudioConnection');
  context.refreshStudioConnection(); assert.deepEqual(calls, []);
  context.document.hidden=false; context.refreshStudioConnection();
  assert.deepEqual(calls, ['status','queue','jobs']);
});
