const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture() {
  let decide;
  const pending = new Promise(resolve => { decide = resolve; });
  const waiting = {id:'job-one',name:'角色替換',status:'awaiting_review',review:{id:'review-one',prompts:[{prompt:'[Video]\n<Subject 1> turns.\n[Audio]\nNo speech.',video_model:'fasth3_omni_nvfp4',duration:5.167,width:864,height:480,seed:99,mapping:[{alias:'hero',picture_tags:['<Picture 1>']}]}]}};
  const c = vm.createContext({ calls:[],reviews:[],loads:0,messages:[],job:waiting,supported:true,
    async api(url, options={}) { c.calls.push({url,body:options.body ? JSON.parse(options.body) : null}); if(url==='/api/scripts/status')return {prompt_review_supported:c.supported}; if(options.method==='POST')return {id:'job-one',status:'queued'};return structuredClone(c.job); },
    async requestGenerationReview(compiled,payload,options){c.reviews.push({compiled,payload,options});return pending;},
    toast(message){c.messages.push(message);},async loadJobs(){c.loads++;},
  });
  for(const name of ['reviewReplacementJobPrompt','resumeReplacementJob']){
    const index=source.search(new RegExp(`async function ${name}\\(`));assert.notEqual(index,-1);
    vm.runInContext(source.slice(index,source.indexOf('\n}',index)+2),c);
  }
  return {c,waiting,decide,flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

test('replacement segment approval waits for the displayed exact prompt and then sends only its review id',async()=>{
  const f=fixture(), running=f.c.reviewReplacementJobPrompt('job-one');await f.flush();
  assert.equal(f.c.calls.length,1);assert.equal(f.c.reviews[0].compiled.prompt,f.waiting.review.prompts[0].prompt);
  assert.equal(f.c.reviews[0].options.receiptRequired,false);assert.equal(f.c.reviews[0].payload.seed,99);
  f.decide('approve');await running;
  assert.equal(f.c.calls.length,2);assert.equal(f.c.calls[1].url,'/api/jobs/job-one/approve');
  assert.deepEqual(JSON.parse(JSON.stringify(f.c.calls[1].body)),{review_id:'review-one'});assert.equal(f.c.loads,1);
});

test('returning from a replacement segment review never submits or resumes the job',async()=>{
  const f=fixture(), running=f.c.reviewReplacementJobPrompt('job-one');await f.flush();f.decide('cancel');await running;
  assert.equal(f.c.calls.length,1);assert.equal(f.c.loads,0);
});

test('a replacement already advanced by another tab is refreshed without opening stale review',async()=>{
  const f=fixture();f.c.job.status='running';await f.c.reviewReplacementJobPrompt('job-one');
  assert.equal(f.c.reviews.length,0);assert.equal(f.c.calls.length,1);
});

test('resuming existing replacements explicitly upgrades them to per-segment prompt approval',async()=>{
  const f=fixture();await f.c.resumeReplacementJob('job-one');
  assert.deepEqual(f.c.calls.map(call=>call.url),['/api/scripts/status','/api/jobs/job-one/resume']);
  assert.deepEqual(JSON.parse(JSON.stringify(f.c.calls[1].body)),{require_prompt_review:true});assert.equal(f.c.loads,1);
  assert.match(f.c.messages[0],/每段完整生成稿均需確認/);
});

test('an old host cannot resume replacements without the new approval flow',async()=>{
  const f=fixture();f.c.supported=false;await assert.rejects(f.c.resumeReplacementJob('job-one'),/尚未提供逐段生成稿確認/);
  assert.deepEqual(f.c.calls.map(call=>call.url),['/api/scripts/status']);assert.equal(f.c.loads,0);
});
