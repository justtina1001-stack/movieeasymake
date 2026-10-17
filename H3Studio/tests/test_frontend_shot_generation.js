const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ShortFilmPersistence = require('../static/shortfilm_persistence.js');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture() {
  const elements = new Map();
  function element(key) {
    if (!elements.has(key)) {
      const classes = new Set();
      elements.set(key, { textContent: '', disabled: false, attributes: {}, dataset: {},
        classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
          remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); } },
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; }, scrollIntoView() {} });
    }
    return elements.get(key);
  }
  const projects = ['a', 'b'].map(id => ({ id, revision: 1, created_at: 'created', updated_at: 'initial', title: id, assets: [], scenes: [{ id: `scene-${id}`, title: `scene ${id}`,
    shots: [{ id: `shot-${id}`, title: `shot ${id}`, action: 'walk', seed: 1, status: 'draft', job_id: null }] }] }));
  const preparation = deferred();
  const storage = new Map(), serverRevisions = new Map(projects.map(project => [project.id, project.revision]));
  const context = vm.createContext({
    shortFilmProjects: structuredClone(projects), activeShortFilmId: 'a', shortFilmShotRequests: new Map(),
    shortFilmBatchRunning: false, shortFilmSaveTimer: null, expandedShortFilmJobIds: new Set(),
    shortFilmSaveCoordinator: null, shortFilmRecoveries: new Map(), ShortFilmPersistence, serverRevisions,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    shortFilmStatusRefreshing: false, studioWorkspace: 'shortfilm',
    messages: [], calls: [], savedIds: [], savedProjects: [], listLoads: 0, renders: 0, console,
    structuredClone, clearTimeout, setTimeout, confirm: () => true,
    $(selector, card) { return element(card ? `${card.dataset.sfShotId}:${selector}` : selector); },
    $$(selector) { return selector === '[data-sf-shot-id]' ? [context.card] : []; },
    card: { dataset: { sfShotId: 'shot-a' } },
    toast(message) { context.messages.push(message); },
    renderShortFilmSummary() {},
    renderShortFilmScenes() { context.renders++; context.renderShortFilmShotRequests(); },
    async loadJobs() {}, async loadShortFilmJobs() { context.listLoads++; },
    async api(url, options = {}) {
      context.calls.push(url);
      if (options.method === 'PUT') {
        context.savedIds.push(url.split('/').pop());
        if (context.failSaveAfterSubmit && context.savedIds.length > 1) throw new Error('save disconnected');
        const payload = JSON.parse(options.body), currentRevision = serverRevisions.get(payload.id);
        context.savedProjects.push(structuredClone(payload));
        if (payload.revision !== currentRevision) throw Object.assign(new Error('version conflict'), { status: 409, current_revision: currentRevision });
        serverRevisions.set(payload.id, currentRevision + 1);
        return { ...payload, revision: currentRevision + 1 };
      }
      if (url.endsWith('/compile')) return preparation.promise;
      if (url === '/api/render') return { id: 'job-new', status: 'queued' };
      throw new Error(`unexpected ${url}`);
    },
  });
  for (const name of ['activeShortFilmProject', 'shortFilmFlatten', 'setButtonBusy', 'saveShortFilmProject', 'getShortFilmSaveCoordinator', 'renderShortFilmSaveStatus',
    'shortFilmGenerationSignature', 'renderShortFilmShotRequests', 'confirmTranslationFallback', 'compileShortFilmShot', 'refreshShortFilmShotStatuses']) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
  const result = () => ({ project: structuredClone(context.shortFilmProjects[0]),
    compiled: { prompt: 'prepared prompt', asset_count: 1 }, warnings: [], payload: { prompt: 'prepared prompt' } });
  return { context, element, elements, preparation, result,
    flush: () => new Promise(resolve => setImmediate(resolve)),
    run: expression => vm.runInContext(expression, context) };
}

test('pending preparation explains the stage, locks preview and generate, and survives DOM replacement', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /儲存/);
  await f.flush();
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /整理提示詞.*完成後才會送入/);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').attributes['aria-busy'], 'true');
  assert.equal(f.element('shot-a:[data-sf-preview-shot]').disabled, true);
  // A new card is rendered while the old request is still pending.
  f.elements.delete('shot-a:[data-sf-generate-shot]');
  f.elements.delete('shot-a:[data-sf-preview-shot]');
  f.elements.delete('shot-a:[data-sf-shot-feedback]');
  f.context.card = { dataset: { sfShotId: 'shot-a' } };
  f.run('renderShortFilmShotRequests()');
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, true);
  assert.equal(await f.run('compileShortFilmShot("shot-a", false)'), null);
  assert.equal(f.context.calls.filter(url => url.endsWith('/compile')).length, 1);
  f.preparation.resolve(f.result());
  await request;
  assert.equal(f.context.shortFilmShotRequests.size, 0);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, false);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').textContent, '生成此鏡頭');
  assert.equal(f.context.shortFilmProjects[0].scenes[0].shots[0].job_id, 'job-new');
  assert.equal(f.context.listLoads, 1);
});

test('preview prepares content without posting a render', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", false)');
  await f.flush();
  assert.equal(f.element('shot-a:[data-sf-preview-shot]').attributes['aria-busy'], 'true');
  f.preparation.resolve(f.result());
  assert.equal(await request, null);
  assert.equal(f.context.calls.includes('/api/render'), false);
  assert.equal(f.context.shortFilmShotRequests.size, 0);
});

test('declining original-text generation leaves the shot editable and never posts a render', async () => {
  const f = fixture(); f.context.confirm = () => false;
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  const prepared = f.result(); prepared.payload.h3_translation = {status:'unavailable'};
  f.preparation.resolve(prepared);
  assert.equal(await request, null);
  assert.equal(f.context.calls.includes('/api/render'), false);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, false);
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /尚未送出/);
});

test('the submit stage remains locked and explains an English fallback before the job is accepted', async () => {
  const f = fixture();
  const submission = deferred();
  const originalApi = f.context.api;
  f.context.api = (url, options) => url === '/api/render' ? submission.promise : originalApi(url, options);
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  const prepared = f.result();
  prepared.payload.h3_translation = { status: 'unavailable' };
  f.preparation.resolve(prepared);
  await f.flush();
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').textContent, '正在送出…');
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, true);
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /英文整理未完成.*保留原文.*送入/);
  submission.resolve({id: 'accepted', status: 'queued'});
  assert.equal((await request).id, 'accepted');
});

test('another shot in the same project waits for the current preparation to finish', async () => {
  const f = fixture();
  f.context.shortFilmProjects[0].scenes[0].shots.push({id: 'another', title: 'other shot', action: 'sit'});
  const first = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  assert.equal(await f.run('compileShortFilmShot("another", true)'), null);
  assert.equal(f.context.calls.filter(url => url.endsWith('/compile')).length, 1);
  assert.match(f.context.messages.at(-1), /另一個鏡頭正在準備/);
  f.preparation.resolve(f.result());
  await first;
});

test('switching project while preparing still submits and saves the captured project', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  f.context.activeShortFilmId = 'b';
  f.context.card = { dataset: { sfShotId: 'shot-b' } };
  f.preparation.resolve(f.result());
  const job = await request;
  assert.equal(job.id, 'job-new');
  assert.deepEqual(f.context.savedIds, ['a', 'a']);
  assert.equal(f.context.shortFilmProjects[0].scenes[0].shots[0].job_id, job.id);
  assert.equal(f.context.shortFilmProjects[1].scenes[0].shots[0].job_id, null);
  assert.equal(f.context.listLoads, 0);
  assert.equal(f.context.renders, 0);
});

test('editing during preparation preserves edits and never submits stale content', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  const result = f.result();
  f.context.shortFilmProjects[0].scenes[0].shots[0].action = 'new action';
  f.preparation.resolve(result);
  await assert.rejects(request, /內容已變更/);
  assert.equal(f.context.calls.includes('/api/render'), false);
  assert.equal(f.context.shortFilmProjects[0].scenes[0].shots[0].action, 'new action');
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, false);
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /未送出/);
});

test('preparation failure leaves a readable error and permits retry', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  f.preparation.reject(new Error('model unavailable'));
  await assert.rejects(request, /model unavailable/);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, false);
  assert.equal(f.element('shot-a:[data-sf-shot-feedback]').classList.contains('error'), true);
  assert.equal(f.context.calls.includes('/api/render'), false);
  const retry = f.run('compileShortFilmShot("shot-a", true)');
  await assert.rejects(retry, /model unavailable/);
  assert.equal(f.context.calls.filter(url => url.endsWith('/compile')).length, 2);
});

test('accepted job is reported as submitted even if the final project save fails', async () => {
  const f = fixture();
  f.context.failSaveAfterSubmit = true;
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  f.preparation.resolve(f.result());
  const job = await request;
  assert.equal(job.id, 'job-new');
  assert.match(f.element('shot-a:[data-sf-shot-feedback]').textContent, /影片已送出.*job-new.*尚未同步/);
  assert.equal(f.element('shot-a:[data-sf-generate-shot]').disabled, false);
  assert.equal(f.context.calls.filter(url => url === '/api/render').length, 1);
  assert.equal(f.context.expandedShortFilmJobIds.has(job.id), true);
});

test('runtime status updates do not cancel preparation or overwrite the latest project', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  const result = f.result();
  f.context.shortFilmProjects[0].scenes[0].shots[0].status = 'completed';
  f.context.shortFilmProjects[0].updated_at = 'newer timestamp';
  f.preparation.resolve(result);
  assert.equal((await request).id, 'job-new');
  assert.equal(f.context.shortFilmProjects[0].updated_at, 'newer timestamp');
});

test('a continuation prepared by the server advances revision before saving the accepted job link', async () => {
  const f = fixture();
  const request = f.run('compileShortFilmShot("shot-a", true)');
  await f.flush();
  const prepared = f.result();
  prepared.project.revision += 1;
  prepared.project.scenes[0].shots[0].continuation_asset_id = 'prepared-tail';
  prepared.project.scenes[0].shots[0].continuation_job_id = 'previous-job';
  f.context.serverRevisions.set('a', prepared.project.revision);
  f.preparation.resolve(prepared);
  assert.equal((await request).id, 'job-new');
  assert.deepEqual(f.context.savedProjects.map(value => value.revision), [1, 3]);
  assert.equal(f.context.shortFilmProjects[0].revision, 4);
  assert.equal(f.context.shortFilmProjects[0].scenes[0].shots[0].continuation_asset_id, 'prepared-tail');
  assert.equal(f.context.shortFilmSaveCoordinator.status('a').state, 'saved');
});

test('view job action stays in the shortfilm workspace whose list contains the job', () => {
  const start = source.indexOf('} else if (shot && event.target.closest("[data-sf-open-jobs]"))');
  const action = source.slice(start, source.indexOf('} else return;', start));
  assert.match(action, /loadShortFilmJobs\(true\)/);
  assert.match(action, /shortfilm-jobs-section/);
  assert.doesNotMatch(action, /setWorkspace\("quick"\)/);
});

test('a status poll cannot save a project replaced by a newer submission', async () => {
  const f = fixture(), progress = deferred();
  const shot = f.context.shortFilmProjects[0].scenes[0].shots[0];
  shot.job_id = 'old-job'; shot.status = 'running';
  const originalApi = f.context.api;
  f.context.api = (url, options) => url === '/api/jobs/old-job' ? progress.promise : originalApi(url, options);
  const poll = f.run('refreshShortFilmShotStatuses()');
  await f.flush();
  f.context.shortFilmProjects[0] = structuredClone(f.context.shortFilmProjects[0]);
  f.context.shortFilmProjects[0].scenes[0].shots[0].job_id = 'new-job';
  progress.resolve({status: 'completed'});
  await poll;
  assert.equal(f.context.savedIds.length, 0);
  assert.equal(f.context.shortFilmProjects[0].scenes[0].shots[0].job_id, 'new-job');
  assert.equal(f.context.shortFilmStatusRefreshing, false);
});

test('a status poll already in flight does not save over a new preparation', async () => {
  const f = fixture(), progress = deferred();
  const shot = f.context.shortFilmProjects[0].scenes[0].shots[0];
  shot.job_id = 'old-job'; shot.status = 'running';
  const originalApi = f.context.api;
  f.context.api = (url, options) => url === '/api/jobs/old-job' ? progress.promise : originalApi(url, options);
  const poll = f.run('refreshShortFilmShotStatuses()');
  await f.flush();
  f.context.shortFilmShotRequests.set('a:shot-a', {phase:'preparing'});
  progress.resolve({status: 'completed'});
  await poll;
  assert.equal(f.context.savedIds.length, 0);
  assert.equal(f.context.shortFilmStatusRefreshing, false);
});
