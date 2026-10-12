const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture() {
  const project = { style: 'Realistic', title: 'Old title', assets: [{ id: 'a', alias: 'Alice', type: 'character', description: 'Red coat' }],
    scenes: [{ id: 'scene', location: 'Station', shots: [
      { id: 's0', action: 'Alice waves.', asset_ids: [], continue_previous: false },
      { id: 's1', action: 'The wave continues.', asset_ids: [], continue_previous: true },
      { id: 's2', action: 'An unrelated object.', asset_ids: [], continue_previous: false },
    ] }] };
  for (const shot of project.scenes[0].shots) Object.assign(shot, { job_id: `job-${shot.id}`, status: 'completed', continuation_asset_id: 'tail', continuation_job_id: 'old-job' });
  const history = project.scenes[0].shots.map(shot => ({ id: shot.job_id, status: 'completed' }));
  const c = vm.createContext({ project, history });
  for (const name of ['shortFilmFlatten', 'shortFilmAliasMentioned', 'shortFilmShotAssetSelection', 'shortFilmAssetShotIds', 'invalidateShortFilmRenders', 'updateShortFilmGenerationField']) {
    const start = source.search(new RegExp(`function ${name}\\(`));
    assert.notEqual(start, -1);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), c);
  }
  return c;
}

test('editing a completed shot invalidates it and its connected descendants while history survives', () => {
  const c = fixture();
  const [first, connected, independent] = c.project.scenes[0].shots;
  c.updateShortFilmGenerationField(c.project, first, 'action', 'Alice sits.', { shotId: first.id });
  for (const shot of [first, connected]) {
    assert.equal(shot.status, 'draft');
    assert.equal(shot.job_id, null);
    assert.equal(shot.continuation_asset_id, null);
    assert.equal(shot.continuation_job_id, null);
  }
  assert.equal(independent.job_id, 'job-s2');
  assert.equal(c.history.length, 3);
  assert.ok(c.history.every(job => job.status === 'completed'));
});

test('scene changes invalidate only that scene and any connected next-scene shot', () => {
  const c = fixture();
  const moved = c.project.scenes[0].shots.splice(2);
  moved[0].continue_previous = true;
  c.project.scenes.push({ id: 'next', shots: moved });
  c.updateShortFilmGenerationField(c.project, c.project.scenes[0], 'location', 'A new station.', { sceneId: 'scene' });
  assert.ok(c.project.scenes.flatMap(scene => scene.shots).every(shot => shot.status === 'draft'));
});

test('asset appearance and alias edits invalidate automatically matched users and connected shots', () => {
  const c = fixture();
  c.updateShortFilmGenerationField(c.project, c.project.assets[0], 'description', 'Blue coat', { assetId: 'a' });
  assert.equal(c.project.scenes[0].shots[0].status, 'draft');
  assert.equal(c.project.scenes[0].shots[1].status, 'draft');
  assert.equal(c.project.scenes[0].shots[2].status, 'completed');
  const renamed = fixture();
  renamed.updateShortFilmGenerationField(renamed.project, renamed.project.assets[0], 'alias', 'Ann', { assetId: 'a' });
  assert.equal(renamed.project.scenes[0].shots[0].status, 'draft');
});

test('global rendering changes invalidate all shots while title and unchanged edits preserve them', () => {
  const c = fixture();
  c.updateShortFilmGenerationField(c.project, c.project, 'title', 'A new title');
  c.updateShortFilmGenerationField(c.project, c.project.scenes[0].shots[0], 'action', 'Alice waves.', { shotId: 's0' });
  assert.ok(c.project.scenes[0].shots.every(shot => shot.status === 'completed'));
  c.updateShortFilmGenerationField(c.project, c.project, 'style', 'Watercolor');
  assert.ok(c.project.scenes[0].shots.every(shot => shot.status === 'draft'));
});
