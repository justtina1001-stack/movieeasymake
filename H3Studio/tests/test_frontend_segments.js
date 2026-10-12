const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture(shots = []) {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {value: '', disabled: false, textContent: '', classList: {add() {}, remove() {}, toggle() {}}});
    return elements.get(selector);
  };
  const context = vm.createContext({
    project: {id: 'project', assets: [], scenes: [{id: 'scene', title: 'scene', shots}]},
    shortFilmBatchRunning: false, shortFilmBatch: null, shortFilmBatchLoading: false,
    shortFilmProjects: [], messages: [], submissions: [], waits: [], locks: [], calls: [],
    $: element, structuredClone, confirm: () => true,
    toast(message) { context.messages.push(message); },
    renderShortFilmSummary() {}, renderShortFilmScenes() {}, scheduleShortFilmSave() {},
    setShortFilmBatchEditing(locked) { context.locks.push(locked); },
    activeShortFilmProject() { return context.project; },
    async saveShortFilmProject() {}, async refreshShortFilmShotStatuses() {},
    async api(url) {
      context.calls.push(url);
      if (url.startsWith('/api/shortfilm-batches?')) return context.serverBatch ? [context.serverBatch] : [];
      if (url === '/api/shortfilms/project') return structuredClone(context.project);
      context.serverBatch = {id: 'batch', project_id: 'project', status: url.endsWith('/cancel') ? 'cancelled' : 'queued',
        shot_ids: context.project.scenes[0].shots.map(shot => shot.id), completed_shot_ids: []};
      return context.serverBatch;
    },
    async compileShortFilmShot(id) {
      context.submissions.push(id);
      context.project = structuredClone(context.project);
      const shots = context.project.scenes[0].shots;
      const index = shots.findIndex(s => s.id === id);
      shots[index].status = 'queued'; shots[index].job_id = `job-${id}`;
      for (let i = index + 1; i < shots.length && shots[i].continue_previous; i++) {
        shots[i].status = 'draft'; shots[i].job_id = null;
      }
      return {id: `job-${id}`};
    },
    async waitForShortFilmJob(id) { context.waits.push(id); return {status: 'completed'}; },
  });
  for (const name of ['shortFilmFlatten', 'shortFilmSegmentDraft', 'shortFilmSegmentPreview', 'shortFilmAliasMentioned', 'shortFilmShotAssetSelection', 'shortFilmShotReferenceUsage', 'shortFilmBatchBlockers', 'buildShortFilmSegmentRows', 'findShortFilmShot', 'renderShortFilmBatch', 'loadShortFilmBatchStatus', 'runShortFilmBatch', 'stopShortFilmBatch']) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
  context.renderShortFilmSegmentBuilder = () => {};
  return {context, element, run: expression => vm.runInContext(expression, context)};
}

const shot = (index, overrides = {}) => ({id: `s${index}`, title: `shot ${index}`, action: 'walk', duration: 5, status: 'draft', asset_ids: [], continue_previous: index > 0, ...overrides});

test('60 / 5 builds 12 editable rows and does not alter existing scenes', () => {
  const {context, element, run} = fixture([shot(0)]);
  element('#sfSegmentTarget').value = '60'; element('#sfSegmentDuration').value = '5';
  run('buildShortFilmSegmentRows()');
  assert.equal(context.project.segment_draft.prompts.length, 12);
  assert.equal(context.project.scenes[0].shots.length, 1);
  context.project.segment_draft.prompts[0] = 'keep me';
  run('buildShortFilmSegmentRows()');
  assert.equal(context.project.segment_draft.prompts[0], 'keep me');
  const preview = run('shortFilmSegmentPreview(project)');
  assert.equal(preview.scene.shots[0].continue_previous, false);
  assert.equal(preview.scene.shots[11].continue_previous, true);
});

test('non-multiple duration is rejected and shrinking populated draft requires confirmation', () => {
  const {context, element, run} = fixture();
  context.project.segment_draft = {duration: 5, target_duration: 10, prompts: ['a', 'b'], asset_ids: []};
  element('#sfSegmentTarget').value = '6'; element('#sfSegmentDuration').value = '5';
  run('buildShortFilmSegmentRows()');
  assert.equal(context.project.segment_draft.prompts.length, 2);
  element('#sfSegmentTarget').value = '5'; context.confirm = () => false;
  run('buildShortFilmSegmentRows()');
  assert.equal(context.project.segment_draft.prompts.length, 2);
});

test('counts shared multiimage assets, alias matches and tail frame before submission', async () => {
  const {context, run} = fixture([shot(0), shot(1)]);
  context.project.assets = [{id: 'a', alias: 'hero', image_asset_ids: Array(9).fill('image')}];
  context.project.scenes[0].shots.forEach(s => s.action = 'hero walks');
  assert.equal(run('shortFilmShotReferenceUsage(project, project.scenes[0].shots[1]).imageCount'), 10);
  await run('runShortFilmBatch()');
  assert.equal(context.submissions.length, 0);
  assert.match(context.messages[0], /10\/9/);
});

test('12 shots submit one host-owned batch without browser-owned rendering', async () => {
  const {context, run} = fixture(Array.from({length: 12}, (_, i) => shot(i)));
  await run('runShortFilmBatch()');
  assert.deepEqual(context.calls, ['/api/shortfilms/project/batch']);
  assert.equal(context.submissions.length, 0);
  assert.equal(context.waits.length, 0);
  assert.equal(context.shortFilmBatchRunning, true);
});

test('reload restores active host batch and stopped batches expose explicit resume', async () => {
  const {context, run} = fixture([shot(0), shot(1), shot(2)]);
  context.serverBatch = {id: 'saved', status: 'running', shot_ids: ['s0','s1','s2'], completed_shot_ids: ['s0']};
  await run('loadShortFilmBatchStatus()');
  assert.equal(context.shortFilmBatchRunning, true);
  context.serverBatch.status = 'interrupted';
  await run('loadShortFilmBatchStatus()');
  assert.equal(context.shortFilmBatchRunning, false);
  await run('runShortFilmBatch(true)');
  assert.equal(context.calls.at(-1), '/api/shortfilm-batches/saved/resume');
});

test('stop requests the server without cancelling the active video job', async () => {
  const {context, run} = fixture([shot(0), shot(1)]);
  await run('runShortFilmBatch()');
  await run('stopShortFilmBatch()');
  assert.equal(context.calls.at(-1), '/api/shortfilm-batches/batch/cancel');
  assert.equal(context.shortFilmBatchRunning, false);
  assert.ok(context.calls.every(url => !url.includes('/api/jobs/')));
});

test('submission failure unlocks editing and preserves unsent shot content', async () => {
  const {context, run} = fixture([shot(0), shot(1)]);
  context.api = async () => { throw Error('模型尚未準備好'); };
  await run('runShortFilmBatch()');
  assert.equal(context.project.scenes[0].shots[0].action, 'walk');
  assert.equal(context.shortFilmBatchRunning, false);
  assert.match(context.messages[0], /模型尚未準備好/);
  assert.equal(context.locks[0], true);
  assert.equal(context.locks.at(-1), false);
});
