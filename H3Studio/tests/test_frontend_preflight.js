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
      value: '5', textContent: '', innerHTML: '', classes: new Set(),
      classList: { add() {}, remove() {}, toggle() {} }, scrollIntoView() {},
      getAttribute: () => 'false', querySelector: () => ({ textContent: '' }),
    });
    return elements.get(selector);
  };
  const c = vm.createContext({
    $: element, structuredClone, state: { mode: 't2v' }, modeLabels: { t2v: '文生影片' }, generationPreflight: null,
    messages: [], calls: [], input: { mode: 't2v', prompt: '角色揮手。', seed: 1 },
    collectPayload: () => ({ ...c.input }), promptKeywords: () => [], setButtonBusy() {},
    async loadJobs() {}, persistForm() {}, toast: message => c.messages.push(message),
    async api(url, options) {
      c.calls.push({ url, payload: JSON.parse(options.body) });
      return url === '/api/compile' ? c.compiled : { id: 'job-test' };
    },
  });
  for (const name of ['escapeHtml', 'originalGenerationRequest', 'validateReplacementSourceCompilation', 'generationPreflightSignature', 'showGenerationPreflight', 'prepareGeneration', 'showGenerationStage', 'confirmTranslationFallback', 'compilePreview', 'renderVideo']) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), c);
  }
  c.compiled = { official_mode: 'T2VA', actual_duration: 5.1666667, asset_count: 0, prompt: 'English scene',
    warnings: [], mapping: [], prepared_payload: { ...c.input, prompt: 'The actor waves.', h3_translation: { status: 'translated' } } };
  return { c, element };
}

test('every generation mode compiles before submitting and reuses prepared narration', async () => {
  const { c } = fixture();
  await c.renderVideo();
  assert.deepEqual(c.calls.map(item => item.url), ['/api/compile', '/api/render']);
  assert.equal(c.calls[1].payload.prompt, 'The actor waves.');
});

test('preview is reused after a seed change but edited creative inputs compile again', async () => {
  const { c } = fixture();
  await c.compilePreview();
  c.input.seed = 42;
  await c.renderVideo();
  assert.deepEqual(c.calls.map(item => item.url), ['/api/compile', '/api/render']);
  assert.equal(c.calls[1].payload.seed, 42);
  c.input.prompt = '角色坐下。';
  await c.renderVideo();
  assert.equal(c.calls.filter(item => item.url === '/api/compile').length, 2);
});

test('nonblocking warnings and material mapping use escaped content with Chinese summaries', async () => {
  const { c, element } = fixture();
  c.compiled.official_mode = 'Ref2VA';
  c.compiled.warnings = ['<img src=x> 原敘述保留'];
  c.compiled.mapping = [{ alias: '<b>Alice</b>', type: 'character', subject_tag: '<Subject 1>', picture_tags: ['<Picture 1>'] }];
  await c.renderVideo();
  assert.match(element('#compileSummary').innerHTML, /多素材參考/);
  assert.match(element('#compileSummary').innerHTML, /5\.17 秒/);
  assert.match(element('#compileSummary').innerHTML, /已整理成英文/);
  assert.match(element('#compileWarnings').innerHTML, /&lt;img src=x&gt;/);
  assert.match(element('#compileMapping').innerHTML, /&lt;b&gt;Alice&lt;\/b&gt;/);
  assert.match(element('#compileMapping').innerHTML, /&lt;Subject 1&gt;/);
  assert.equal(c.calls.at(-1).url, '/api/render');
});

test('failed validation cannot submit generation', async () => {
  const { c } = fixture();
  c.api = async url => { c.calls.push({ url }); throw Error('素材不存在'); };
  await c.renderVideo();
  assert.deepEqual(c.calls.map(item => item.url), ['/api/compile']);
  assert.match(c.messages[0], /素材不存在/);
});

test('reusing a prepared recipe restores original narration and only existing approved text fields', () => {
  const { c } = fixture();
  const request = { prompt: 'A woman waves.', references: [{ description: 'A red coat.' }], seed: 42,
    h3_translation: { source_texts: { prompt: '女子揮手。', 'references.0.description': '紅外套。',
      'references.1.description': '不應新建素材', seed: '不應變更設定', '__proto__.polluted': 'bad' } } };
  const restored = c.originalGenerationRequest(request);
  assert.equal(restored.prompt, '女子揮手。');
  assert.equal(restored.references[0].description, '紅外套。');
  assert.equal(restored.references.length, 1);
  assert.equal(restored.seed, 42);
  assert.equal({}.polluted, undefined);
  assert.equal(request.prompt, 'A woman waves.');
});
