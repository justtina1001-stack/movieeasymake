const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const H3AudioWorkspace = require('../static/audio_workspace.js');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture(mode = 'local') {
  const elements = new Map();
  const messages = [];
  function element(selector) {
    if (!elements.has(selector)) {
      const classes = new Set();
      const attrs = new Map();
      elements.set(selector, {
        value: '', checked: false, disabled: false, textContent: '', className: '', style: {},
        classList: { contains: name => classes.has(name), toggle(name, force) { if (force === undefined ? !classes.has(name) : force) classes.add(name); else classes.delete(name); } },
        setAttribute(name, value) { attrs.set(name, value); }, hasAttribute(name) { return attrs.has(name); }, removeAttribute(name) { attrs.delete(name); },
        querySelector() { return null; },
      });
    }
    return elements.get(selector);
  }
  let id = 0;
  const context = vm.createContext({
    H3AudioWorkspace, $: element, document: { getElementById: id => element(`#${id}`) },
    connectionSettings: { mode }, musicModelsInstalled: true, musicStatusData: null, musicPendingSubmission: null,
    musicMode: 'instrumental', musicPage: 1, musicTotalPages: 1, lastMusicJobsSignature: '', audioWorkspace: { save() {} },
    uid() { return `request_${String(++id).padStart(12, '0')}`; }, randomMusicSeed() { return '999'; },
    toast(message, error) { messages.push({ message, error }); },
    loadMusicJobs: async () => {}, api: async () => { throw Error('unexpected request'); },
  });
  for (const name of ['setButtonBusy', 'humanBytes', 'renderMusicStatus', 'loadMusicStatus', 'collectMusicPayload', 'generateMusic']) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, `${name} exists`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
  for (const [key, value] of Object.entries({ musicDuration: '60', musicBpm: '120', musicSeed: '123', musicFormat: 'mp3', musicLyrics: '', musicJobName: '回歸測試' })) element(`#${key}`).value = value;
  element('#musicTiledDecode').checked = true;
  context.renderMusicStatus({ installed: true, available: true, connection_mode: mode, can_install: mode === 'local' });
  return { context, element, messages };
}

test('actual music submit handler blocks re-entry while status polling continues to report installed models', async () => {
  const ui = fixture();
  let finish;
  const requests = [];
  ui.context.api = async (url, options) => {
    if (url === '/api/music/status') return { installed: true, available: true, connection_mode: 'local' };
    assert.equal(url, '/api/music/jobs');
    requests.push(JSON.parse(options.body));
    return new Promise(resolve => { finish = resolve; });
  };
  const pending = ui.context.generateMusic();
  assert.equal(ui.element('#generateMusic').hasAttribute('aria-busy'), true);
  for (let index = 0; index < 5; index++) {
    await ui.context.loadMusicStatus();
    assert.equal(ui.element('#generateMusic').disabled, true);
    await ui.context.generateMusic();
  }
  assert.equal(requests.length, 1);
  assert.match(requests[0].request_id, /^request_/);
  finish({ id: 'completed_job' });
  await pending;
  assert.equal(ui.element('#generateMusic').hasAttribute('aria-busy'), false);
  assert.equal(ui.element('#generateMusic').disabled, false);
});

test('the actual submit handler reuses its request ID after a network failure and clears it after success', async () => {
  const ui = fixture();
  const requests = [];
  ui.context.api = async (url, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) throw new Error('network response lost');
    return { id: `job_${requests.length}` };
  };
  await ui.context.generateMusic();
  assert.equal(ui.element('#generateMusic').disabled, false);
  assert.equal(ui.element('#musicSeed').value, '123');
  await ui.context.generateMusic();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.deepEqual(requests[0], requests[1]);
  ui.element('#musicSeed').value = '123';
  await ui.context.generateMusic();
  assert.notEqual(requests[2].request_id, requests[1].request_id);
});

test('an explicitly rejected request and an edited draft each receive a fresh submission ID', async () => {
  const ui = fixture();
  const requests = [];
  ui.context.api = async (url, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) { const error = Error('invalid lyrics'); error.status = 400; throw error; }
    if (requests.length === 2) throw Error('network unavailable');
    return { id: 'new_job' };
  };
  await ui.context.generateMusic();
  await ui.context.generateMusic();
  assert.notEqual(requests[0].request_id, requests[1].request_id);
  ui.element('#musicMood').value = '更平靜的版本';
  await ui.context.generateMusic();
  assert.notEqual(requests[1].request_id, requests[2].request_id);
});

test('failed music status polling clears stale model readiness and disables generation until recovery', async () => {
  const ui = fixture('remote');
  assert.equal(ui.context.musicModelsInstalled, true);
  ui.context.api = async () => { const error = Error('HTTP 503'); error.status = 503; throw error; };
  await ui.context.loadMusicStatus().catch(() => {});
  assert.equal(ui.context.musicModelsInstalled, false);
  assert.equal(ui.element('#generateMusic').disabled, true);
  assert.match(ui.element('#musicModelDetail').textContent, /HTTP 503/);
  assert.doesNotMatch(ui.element('#musicModelTitle').textContent, /模型安裝失敗/);
  ui.context.api = async () => ({ installed: true, available: true, connection_mode: 'remote' });
  await ui.context.loadMusicStatus();
  assert.equal(ui.context.musicModelsInstalled, true);
  assert.equal(ui.element('#generateMusic').disabled, false);
});
