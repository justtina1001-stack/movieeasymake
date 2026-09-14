const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture(shots = []) {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {value: '', disabled: false, textContent: '', classList: {add() {}, remove() {}}});
    return elements.get(selector);
  };
  const context = vm.createContext({
    project: {id: 'project', assets: [], scenes: [{id: 'scene', title: 'scene', shots}]},
    shortFilmBatchRunning: false, messages: [], submissions: [], waits: [], locks: [],
    $: element, structuredClone, confirm: () => true,
    toast(message) { context.messages.push(message); },
    renderShortFilmSummary() {}, renderShortFilmScenes() {}, scheduleShortFilmSave() {},
    setShortFilmBatchEditing(locked) { context.locks.push(locked); },
    activeShortFilmProject() { return context.project; },
    async saveShortFilmProject() {}, async refreshShortFilmShotStatuses() {},
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
  for (const name of ['shortFilmFlatten', 'shortFilmSegmentDraft', 'shortFilmSegmentPreview', 'shortFilmAliasMentioned', 'shortFilmShotAssetSelection', 'shortFilmShotReferenceUsage', 'shortFilmBatchBlockers', 'buildShortFilmSegmentRows', 'findShortFilmShot', 'runShortFilmBatch']) {
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

test('12 shots are strictly submitted then awaited in sequence', async () => {
  const {context, run} = fixture(Array.from({length: 12}, (_, i) => shot(i)));
  context.waitForShortFilmJob = async id => {
    assert.equal(context.submissions.length, context.waits.length + 1);
    context.waits.push(id); return {status: 'completed'};
  };
  await run('runShortFilmBatch()');
  assert.equal(context.submissions.length, 12);
  assert.equal(context.waits.length, 12);
  assert.ok(context.project.scenes[0].shots.every(s => s.status === 'completed'));
  assert.deepEqual(context.locks, [true, false]);
});

test('failure stops downstream work; retry skips completed and continues remaining', async () => {
  const {context, run} = fixture([shot(0), shot(1), shot(2)]);
  context.waitForShortFilmJob = async id => {
    if (id === 'job-s1') { context.project.scenes[0].shots[1].status = 'failed'; throw Error('OOM'); }
    return {status: 'completed'};
  };
  await run('runShortFilmBatch()');
  assert.deepEqual(context.submissions, ['s0', 's1']);
  context.waitForShortFilmJob = async () => ({status: 'completed'});
  await run('runShortFilmBatch()');
  assert.deepEqual(context.submissions, ['s0', 's1', 's1', 's2']);
});

test('regenerating an earlier shot does not skip invalidated completed descendants', async () => {
  const {context, run} = fixture([shot(0), shot(1, {status: 'completed', job_id: 'old'})]);
  await run('runShortFilmBatch()');
  assert.deepEqual(context.submissions, ['s0', 's1']);
});

test('existing running job is awaited without duplication and stop sends no next job', async () => {
  const {context, run} = fixture([shot(0, {status: 'running', job_id: 'existing'}), shot(1)]);
  context.waitForShortFilmJob = async id => { context.waits.push(id); context.shortFilmBatchRunning = false; return null; };
  await run('runShortFilmBatch()');
  assert.deepEqual(context.waits, ['existing']);
  assert.deepEqual(context.submissions, []);
  assert.deepEqual(context.locks, [true, false]);
});
