const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture() {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', classList: {add() {}, remove() {}}, scrollIntoView() {},
      getAttribute: () => 'false', querySelector: () => ({textContent: ''}),
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    structuredClone, $: element, keyframePrepareVersion: 0, uploads: [],
    currentSettings: () => ({prompt: 'Replace the original person', seed: 42}),
    activeReferences: () => [], escapeHtml: value => value, uid: () => 'id',
    acceptsReferenceFile: (file, kind) => kind === 'images' && file.type.startsWith('image/'),
    async uploadFile(file, kind) { context.uploads.push({file, kind}); return {id: file.name, name: file.name, url: '/test.png'}; },
    updateSummary() {}, saveState() {}, toast() {}, renderLoraPanels() {},
    renderKeyframePreview() {}, renderSymbolLoop() {}, renderContinuation() {},
    renderMgAnimation() {}, renderReferences() {}, renderStoryboards() {},
    setMode(mode) { context.state.mode = mode; },
    async loadJobRecipe() { return context.recipe; },
    async prepareReplacementVideo() {},
    promptKeywords: () => [], setButtonBusy() {}, async loadJobs() {},
  });
  const start = source.indexOf('const defaultState =');
  vm.runInContext(source.slice(start, source.indexOf('\n};', start) + 3) + '\nglobalThis.defaultState = defaultState;', context);
  context.state = structuredClone(context.defaultState);
  context.state.mode = 'replace';
  for (const name of ['renderReplacement', 'addReplacementFiles', 'collectPayload', 'recipeAsset', 'applyJobRecipe', 'validateReplacementSourceCompilation', 'renderVideo']) {
    const index = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(index, -1);
    vm.runInContext(source.slice(index, source.indexOf('\n}', index) + 2), context);
  }
  return context;
}

test('uploads original and new images into distinct fields and request roles', async () => {
  const c = fixture();
  await c.addReplacementFiles('sourceImages', [{name: 'original', type: 'image/png'}]);
  await c.addReplacementFiles('images', [{name: 'new', type: 'image/png'}]);
  c.state.replacement.video = {id: 'source-video'};
  const payload = JSON.parse(JSON.stringify(c.collectPayload()));
  assert.deepEqual(payload.replacement_source_image_asset_ids, ['original']);
  assert.deepEqual(payload.references[0].image_asset_ids, ['new']);
  assert.equal(payload.references.length, 1);
  assert.equal(payload.references[0].video_asset_id, 'source-video');
  assert.equal(c.uploads[0].kind, 'replacement-source-character');
  assert.equal(c.uploads[1].kind, 'replacement-character');
  c.state.mode = 't2v';
  assert.equal(c.collectPayload().replacement_source_image_asset_ids.length, 0);
});

test('shared cap rejects an oversized upload before uploading any file', async () => {
  const c = fixture();
  c.state.replacement.images = Array.from({length: 8}, (_, i) => ({id: `new${i}`}));
  await c.addReplacementFiles('sourceImages', [{name: 'original', type: 'image/png'}]);
  await assert.rejects(c.addReplacementFiles('images', [{name: 'extra', type: 'image/png'}]), /合計最多 9 張/);
  await assert.rejects(c.addReplacementFiles('sourceImages', [{name: 'movie', type: 'video/mp4'}]), /角色圖片/);
  assert.equal(c.uploads.length, 1);
});

test('quick apply restores original references and clears them for legacy recipes', async () => {
  const c = fixture();
  c.recipe = {
    request: {mode: 'replace', replacement_source_image_asset_ids: ['original'], references: [
      {alias: 'new', image_asset_ids: ['new'], video_asset_id: 'video'},
    ]},
    assets: {original: {id: 'original', name: 'original'}, new: {id: 'new', name: 'new'}, video: {id: 'video', name: 'video'}},
  };
  await c.applyJobRecipe('job');
  assert.equal(c.state.replacement.sourceImages[0].id, 'original');
  assert.equal(c.state.replacement.images[0].id, 'new');
  assert.equal(c.collectPayload().replacement_source_image_asset_ids[0], 'original');
  delete c.recipe.request.replacement_source_image_asset_ids;
  await c.applyJobRecipe('legacy');
  assert.equal(c.state.replacement.sourceImages.length, 0);
});

test('old backend cannot silently ignore original images', () => {
  const c = fixture();
  const payload = {mode: 'replace', replacement_source_image_asset_ids: ['original']};
  assert.throws(() => c.validateReplacementSourceCompilation(payload, {mapping: [], reference_images: ['new']}), /後端尚未支援/);
  assert.throws(() => c.validateReplacementSourceCompilation(payload, {mapping: [{type: 'replacement_source'}], reference_images: ['new']}), /後端尚未支援/);
  c.validateReplacementSourceCompilation(payload, {mapping: [{type: 'replacement_source'}], reference_images: ['new', 'original']});
  c.validateReplacementSourceCompilation({mode: 'replace'}, {});
});

test('render preflights original-image support and never queues on an old backend', async () => {
  const c = fixture();
  c.state.replacement.sourceImages = [{id: 'original'}];
  c.state.replacement.images = [{id: 'new'}];
  c.$('#duration').value = '5';
  c.$('#megapixels').value = '0.4';
  const calls = [], errors = [];
  c.toast = message => errors.push(message);
  c.api = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return {mapping: [], reference_images: ['new']};
  };
  await c.renderVideo();
  assert.deepEqual(calls.map(call => call.url), ['/api/compile']);
  assert.match(errors[0], /後端尚未支援/);
  calls.length = 0;
  c.api = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return url === '/api/compile'
      ? {mapping: [{type: 'replacement_source'}], reference_images: ['new', 'original']}
      : {id: 'test-job'};
  };
  await c.renderVideo();
  assert.deepEqual(calls.map(call => call.url), ['/api/compile', '/api/render']);
  assert.deepEqual(calls[0].payload, calls[1].payload);
});
