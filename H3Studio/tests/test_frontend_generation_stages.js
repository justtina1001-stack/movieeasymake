const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture() {
  const elements = new Map(), prep = deferred(), submission = deferred();
  const element = selector => {
    if (!elements.has(selector)) {
      const classes = new Set(['hidden']), attributes = {}, label = {textContent: '開始生成影片'};
      elements.set(selector, {value: '5', checked: false, disabled: false, textContent: '', innerHTML: '', label,
        classList: {remove: name => classes.delete(name), add: name => classes.add(name), contains: name => classes.has(name), toggle() {}},
        setAttribute: (name, value) => attributes[name] = value, removeAttribute: name => delete attributes[name],
        getAttribute: name => attributes[name] || 'false', querySelector: () => label, scrollIntoView() {} });
    }
    return elements.get(selector);
  };
  const c = vm.createContext({
    $: element, structuredClone, state: {mode:'t2v'}, modeLabels: {t2v:'文字生成'}, generationPreflight: null,
    input: {mode:'t2v', prompt:'A character waves.', seed:1}, calls: [], messages: [], confirmations: [], allowFallback: true,
    collectPayload: () => structuredClone(c.input), currentSettings: () => structuredClone(c.input),
    promptKeywords: () => [], persistForm() {}, saveState() {}, updateSummary() {}, renderLoraPanels() {},
    isReferenceMode: () => c.state.mode === 'r2v',
    confirm(message) { c.confirmations.push(message); return c.allowFallback; },
    toast(message) { c.messages.push(message); }, async loadJobs() { if (c.listFailed) throw new Error('list offline'); },
    async api(url, options) { c.calls.push({url, payload:JSON.parse(options.body)}); return url === '/api/compile' ? prep.promise : submission.promise; },
  });
  const names = ['setButtonBusy','escapeHtml','originalGenerationRequest','validateReplacementSourceCompilation',
    'generationPreflightSignature','showGenerationPreflight','prepareGeneration','showGenerationStage',
    'confirmTranslationFallback','compilePreview','renderVideo','restoreForm','changeQualityMode'];
  for (const name of names) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}',start)+2), c);
  }
  const compiled = status => ({official_mode:'T2VA', actual_duration:5, asset_count:0, mapping:[], warnings:[],
    prompt:'English directions', prepared_payload:{...c.input, h3_translation:{status:status || 'translated'}}});
  return {c, element, prep, submission, compiled, flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

test('preparation and actual submission show distinct stages and prevent duplicate requests', async () => {
  const f = fixture();
  const running = f.c.renderVideo();
  assert.match(f.element('#renderButton').label.textContent, /準備影片內容/);
  assert.match(f.element('#generationPreparationStatus').textContent, /還沒進影片佇列/);
  assert.equal(await f.c.renderVideo(), null);
  assert.equal(f.c.calls.length, 1);
  f.prep.resolve(f.compiled()); await f.flush();
  assert.match(f.element('#renderButton').label.textContent, /送入工作佇列/);
  assert.match(f.element('#generationPreparationStatus').textContent, /正在提交影片工作/);
  assert.equal(f.c.calls[1].url, '/api/render');
  f.submission.resolve({id:'job-accepted'}); await running;
  assert.match(f.element('#generationPreparationStatus').textContent, /已加入佇列/);
  assert.equal(f.element('#renderButton').disabled, false);
  assert.equal(f.element('#compileButton').disabled, false);
});

test('edits during slow preparation never submit the previous settings', async () => {
  const f = fixture(), running = f.c.renderVideo();
  f.c.input.prompt = 'A new scene.';
  f.prep.resolve(f.compiled()); await running;
  assert.deepEqual(f.c.calls.map(call=>call.url), ['/api/compile']);
  assert.match(f.element('#generationPreparationStatus').textContent, /設定已變更.*未送出/);
});

test('failed English preparation needs an explicit choice before original text is submitted', async () => {
  const f = fixture(), running = f.c.renderVideo();
  f.c.allowFallback = false;
  const compiled = f.compiled('unavailable'); compiled.prepared_payload.h3_translation.reason_code = 'timeout';
  f.prep.resolve(compiled); await running;
  assert.equal(f.c.confirmations.length, 1);
  assert.match(f.c.confirmations[0], /英文整理逾時/);
  assert.equal(f.c.calls.some(call=>call.url === '/api/render'), false);
  assert.match(f.element('#generationPreparationStatus').textContent, /影片尚未送出/);
  assert.equal(f.element('#renderButton').disabled, false);
});

test('choosing original text uses the same prepared payload rather than a new compilation', async () => {
  const f = fixture(), running = f.c.renderVideo();
  f.prep.resolve(f.compiled('unavailable')); await f.flush();
  f.submission.resolve({id:'accepted-original'}); await running;
  assert.deepEqual(f.c.calls.map(call=>call.url), ['/api/compile','/api/render']);
  assert.equal(f.c.calls[1].payload.h3_translation.status, 'unavailable');
});

test('a list error after acceptance reports the submitted job instead of a generation failure', async () => {
  const f = fixture(); f.c.listFailed = true;
  const running = f.c.renderVideo();
  f.prep.resolve(f.compiled()); await f.flush(); f.submission.resolve({id:'accepted-job'}); await running;
  assert.match(f.element('#generationPreparationStatus').textContent, /accepted.*已送出.*列表更新失敗/);
  assert.equal(f.c.calls.filter(call=>call.url === '/api/render').length, 1);
});

test('preview retries preparation and preserves a render button disabled for other reasons', async () => {
  const f = fixture(); f.element('#renderButton').disabled = true;
  const first = f.c.compilePreview(); f.prep.resolve(f.compiled('unavailable')); await first;
  assert.equal(f.element('#renderButton').disabled, true);
  await f.c.compilePreview();
  assert.equal(f.c.calls.filter(call=>call.url === '/api/compile').length, 2);
  assert.equal(f.c.calls.some(call=>call.url === '/api/render'), false);
});

test('a cached fallback from a different seed is recompiled so its handoff remains trustworthy', async () => {
  const f = fixture();
  f.c.generationPreflight = {signature:f.c.generationPreflightSignature(f.c.input), compiled:f.compiled('unavailable')};
  f.c.input.seed = 99;
  const ready = f.c.prepareGeneration(f.c.input);
  f.prep.resolve(f.compiled()); await ready;
  assert.equal(f.c.calls.length, 1);
  assert.equal(f.c.calls[0].payload.seed, 99);
});

test('legacy native reference defaults migrate to simple while explicit new drafts retain beta', () => {
  const f = fixture(); f.c.state.mode = 'r2v';
  f.c.state.form = {scheduler:'beta', quality_mode:'native'}; f.c.restoreForm();
  assert.equal(f.element('#scheduler').value, 'simple');
  f.c.state.form.native_scheduler_version = 1; f.c.restoreForm();
  assert.equal(f.element('#scheduler').value, 'beta');
  f.c.state.form = {scheduler:'beta', quality_mode:'turbo'}; f.c.restoreForm();
  assert.equal(f.element('#scheduler').value, 'beta');
});

test('returning from accelerated mode uses the official default while intentional beta remains available', () => {
  const f = fixture(); f.c.state.mode = 'r2v'; f.element('#qualityMode').value = 'native';
  f.c.state.nativeSampling = {steps:20, scheduler:'beta'}; f.c.changeQualityMode();
  assert.equal(f.element('#scheduler').value, 'simple');
  f.c.state.nativeSampling = {steps:20, scheduler:'beta', scheduler_version:1}; f.c.changeQualityMode();
  assert.equal(f.element('#scheduler').value, 'beta');
});
