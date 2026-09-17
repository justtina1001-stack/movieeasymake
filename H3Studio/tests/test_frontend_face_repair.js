const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/face-repair.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture(api) {
  const elements = new Map();
  const element = key => {
    if (!elements.has(key)) elements.set(key, {
      value: '', disabled: false, listeners: {}, textContent: '',
      classList: {add() {}, remove() {}}, focus() {}, pause() {},
      addEventListener(name, fn) { this.listeners[name] = fn; },
      querySelectorAll() { return []; },
    });
    return elements.get(key);
  };
  const document = element('document');
  document.querySelector = element;
  const context = vm.createContext({document, api, toast() {},
    uploadFile: async file => ({id: 'reference-image', name: file.name}),
    loadJobs: async () => {}, loadSharedQueue: async () => {},
  });
  vm.runInContext(source, context);
  const el = suffix => element(`#faceRepair${suffix}`);
  for (const [key, value] of Object.entries({Strength: '.3', Canvas: '512', Seed: '42', Selection: 'largest_face'})) el(key).value = value;
  return {el, async open() {
    document.listeners.click({target: {closest: () => ({dataset: {faceRepair: 'source-job'}, focus() {}})}, preventDefault() {}});
    await flush();
  }, async upload() { await el('Image').listeners.change({target: {files: [{name: 'face.png'}]}}); }};
}

test('old backend explains restart and cannot submit even after image upload', async () => {
  const calls = [];
  const f = fixture(async url => { calls.push(url); throw Object.assign(new Error('missing'), {status: 404}); });
  await f.open();
  await f.upload();
  await flush();
  assert.match(f.el('Status').textContent, /重新啟動 Studio/);
  assert.equal(f.el('Submit').disabled, true);
  await f.el('Submit').listeners.click();
  assert.ok(calls.every(url => url === '/api/face-repair/status'));
});

test('ready engine still requires a reference and submits a separate repair request once', async () => {
  const posts = [];
  let finish;
  const f = fixture(async (url, options) => {
    if (!options) return {ready: true};
    posts.push({url, body: JSON.parse(options.body)});
    return new Promise(resolve => { finish = resolve; });
  });
  await f.open();
  assert.equal(f.el('Submit').disabled, true);
  await f.upload();
  assert.equal(f.el('Submit').disabled, false);
  const pending = f.el('Submit').listeners.click();
  await f.el('Submit').listeners.click();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/api/jobs/source-job/face-repair');
  assert.equal(posts[0].body.reference_image_asset_id, 'reference-image');
  assert.equal(posts[0].body.strength, .3);
  assert.equal(f.el('Submit').disabled, true);
  finish({id: 'repair-job'});
  await pending;
});

test('a late upload from a closed dialog cannot enable another job dialog', async () => {
  const f = fixture(async () => ({ready: true}));
  await f.open();
  const pending = f.upload();
  f.el('Modal').listeners.keydown({key: 'Escape'});
  await f.open();
  await pending;
  assert.equal(f.el('Submit').disabled, true);
});
