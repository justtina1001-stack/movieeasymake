const test = require('node:test');
const assert = require('node:assert/strict');
const audio = require('../static/audio_workspace.js');

function storage() {
  const data = new Map();
  return { data, getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
}

test('audio drafts persist long unfinished text, modes, model choices and reference IDs without arbitrary metadata', () => {
  const local = storage();
  const store = audio.createDraftStore(local);
  const longText = '尚未送出的歌詞\n'.repeat(2000);
  assert.equal(store.save('music', { mode: 'song', fields: { lyrics: longText, seed: '42', tiled_decode: true, remote_access_token: 'secret' }, api_key: 'secret', binary: 'data:audio/base64,abc' }), true);
  assert.equal(store.load('music').fields.lyrics, longText);
  assert.equal(store.load('music').mode, 'song');
  assert.equal(store.save('voice', { mode: 'clone', fields: { text: '草稿台詞', reference_text: '逐字稿' }, modelSelection: { clone: 'clone' }, instructDrafts: { custom: '期待', design: '溫柔女聲' }, referenceAsset: { id: 'a'.repeat(32), name: '我的錄音', url: 'https://evil.example/token', data: 'binary', access_token: 'secret' } }), true);
  const restored = store.load('voice');
  assert.equal(restored.modelSelection.clone, 'clone');
  assert.equal(restored.instructDrafts.design, '溫柔女聲');
  assert.deepEqual(restored.referenceAsset, { id: 'a'.repeat(32), name: '我的錄音', url: `/api/assets/${'a'.repeat(32)}` });
  assert.doesNotMatch([...local.data.values()].join(''), /secret|binary|evil\.example|data:audio/);
});

test('corrupt drafts, unavailable storage and invalid asset IDs fail without throwing', () => {
  const local = storage();
  local.data.set('h3studio-music-draft-v1', '{bad');
  assert.equal(audio.createDraftStore(local).load('music'), null);
  const locked = audio.createDraftStore({ getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); }, removeItem() { throw Error('blocked'); } });
  assert.equal(locked.load('voice'), null);
  assert.equal(locked.save('voice', { mode: 'clone' }), false);
  assert.equal(locked.clear('voice'), false);
  assert.equal(audio.cleanDraft('voice', { referenceAsset: { id: '../../secret' }, modelSelection: { design: 'design_1_7b' } }).referenceAsset, null);
  assert.equal(audio.cleanDraft('voice', { modelSelection: { design: 'design_1_7b' } }).modelSelection.design, 'design');
});

test('history settings preserve original lyrics and seed and do not borrow a previous reference', () => {
  const music = audio.draftFromJob('music', { id: 'one', mode: 'song', name: '第一版', settings: { lyrics: '[Verse]\n好運來', seed: 333, genre: 'jazz', remote_access_token: 'secret' } });
  assert.deepEqual(music.fields, { job_name: '第一版', genre: 'jazz', lyrics: '[Verse]\n好運來', seed: 333 });
  assert.equal(audio.draftFromJob('music', { id: 'legacy', caption: 'compiled prompt' }), null);
  const voice = audio.draftFromJob('voice', { mode: 'clone', model_id: 'clone_1_7b', name: '複製聲線', text: '朗讀台詞', reference_text: '參考逐字稿' });
  assert.equal(voice.referenceAsset, null);
  assert.equal(voice.fields.reference_text, '參考逐字稿');
  assert.deepEqual(voice.instructDrafts, { custom: '', design: '', clone: '' });
});

test('local and remote connection drafts keep independent unsaved URLs', () => {
  const memory = audio.createConnectionMemory({ mode: 'local', base_url: 'http://local.test:8288' });
  assert.equal(memory.select('remote', 'http://local.test:8388'), '');
  assert.equal(memory.select('local', 'https://gpu.test/gateway'), 'http://local.test:8388');
  assert.equal(memory.select('remote', 'http://local.test:8488'), 'https://gpu.test/gateway');
  assert.equal(memory.current('local'), 'http://local.test:8488');
  memory.load({ mode: 'remote', base_url: 'https://saved.test', local_base_url: 'http://localhost:8588', remote_base_url: 'https://saved.test' });
  assert.equal(memory.select('local', 'https://saved.test'), 'http://localhost:8588');
});

class Element {
  constructor(tag, attrs = {}, text = '') {
    this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.children = []; this.parentNode = null;
    this.style = {}; this.textContent = text; this.detachCount = 0; this.currentTime = 0; this.paused = true; this.open = false;
  }
  get id() { return this.attrs.id || ''; }
  get className() { return this.attrs.class || ''; }
  set className(value) { this.attrs.class = value; }
  get classList() {
    const list = this.className.split(/\s+/).filter(Boolean);
    list.contains = value => list.includes(value);
    list.toggle = (value, force) => { const wanted = force === undefined ? !list.includes(value) : force; this.className = list.filter(item => item !== value).concat(wanted ? [value] : []).join(' '); };
    return list;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  querySelector() { return null; }
  get innerHTML() { return this.children.map(child => child.outerHTML).join('') || this.textContent; }
  set innerHTML(value) { this.children.forEach(child => child.detachCount++); this.children = []; this.textContent = value; }
  get outerHTML() { return `<${this.tagName} ${JSON.stringify(this.attrs)}>${this.innerHTML}</${this.tagName}>`; }
  remove() { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; this.detachCount++; } }
  appendChild(child) { if (child.parentNode) child.remove(); this.children.push(child); child.parentNode = this; return child; }
  insertBefore(child, before) { if (!before) return this.appendChild(child); if (child === before) return child; if (child.parentNode) child.remove(); this.children.splice(this.children.indexOf(before), 0, child); child.parentNode = this; return child; }
  replaceWith(child) { const parent = this.parentNode; const index = parent.children.indexOf(this); this.remove(); parent.children.splice(index, 0, child); child.parentNode = parent; }
}

function card(id, { progress = '100', name = '原名', completed = false, favorite = false } = {}) {
  const root = new Element('article', { [`data-music-job`]: id, class: `music-job${favorite ? ' favorite' : ''}` });
  root.appendChild(new Element('div', { class: 'music-job-head' }, name));
  root.appendChild(new Element('div', { class: 'music-job-status' }, completed ? 'completed' : 'running'));
  if (completed) root.appendChild(new Element('audio', { src: `/api/music/jobs/${id}/audio` }));
  else root.appendChild(new Element('div', { class: 'job-progress', 'data-queue-progress-panel': '' }, progress));
  root.appendChild(new Element('div', { class: 'music-job-actions' }, 'actions'));
  root.appendChild(new Element('pre', { class: 'job-recipe hidden', 'data-music-caption-panel': id }, '原設定'));
  root.appendChild(new Element('details', {}, '進階設定'));
  return root;
}

test('another job changing leaves playing audio and expanded settings attached to their original card', () => {
  const container = new Element('div');
  const playing = card('done', { completed: true });
  audio.reconcileHistory(container, [playing, card('running', { progress: '10' })], 'music');
  const player = playing.children.find(child => child.tagName === 'AUDIO');
  player.currentTime = 12.5; player.paused = false;
  playing.children.find(child => child.tagName === 'PRE').className = 'job-recipe';
  playing.children.find(child => child.tagName === 'DETAILS').open = true;
  audio.reconcileHistory(container, [card('done', { completed: true }), card('running', { progress: '55' })], 'music');
  assert.equal(container.children[0], playing);
  assert.equal(player.parentNode, playing);
  assert.equal(player.detachCount, 0);
  assert.equal(player.currentTime, 12.5);
  assert.equal(player.paused, false);
  assert.equal(playing.children.find(child => child.tagName === 'PRE').className, 'job-recipe');
  assert.equal(playing.children.find(child => child.tagName === 'DETAILS').open, true);
  assert.equal(container.children[1].children.find(child => child.hasAttribute('data-queue-progress-panel')).textContent, '55');
});

test('renaming, favoriting and prepending new jobs preserve the live player rather than recreating or moving its card', () => {
  const container = new Element('div');
  const playing = card('done', { completed: true });
  audio.reconcileHistory(container, [playing], 'music');
  const player = playing.children.find(child => child.tagName === 'AUDIO');
  const recipe = playing.children.find(child => child.tagName === 'PRE');
  recipe.className = 'job-recipe'; player.currentTime = 9; player.paused = false;
  audio.reconcileHistory(container, [card('new', { progress: '1' }), card('done', { completed: true, name: '新名', favorite: true })], 'music');
  assert.equal(playing.style.order, '1');
  assert.equal(playing.detachCount, 0);
  assert.equal(player.detachCount, 0);
  assert.equal(player.currentTime, 9);
  assert.equal(playing.children.find(child => child.tagName === 'PRE'), recipe);
  assert.equal(recipe.className, 'job-recipe');
  assert.match(playing.className, /favorite/);
  assert.equal(playing.children[0].textContent, '新名');
});

test('polling cannot unlock a music submission and duplicate attempts are rejected until it finishes', () => {
  const elements = new Map();
  const doc = { getElementById(id) { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); } };
  const button = doc.getElementById('generateMusic');
  assert.equal(audio.beginSubmission(button), true);
  for (let index = 0; index < 10; index++) {
    audio.renderMusicStatus(doc, { installed: true, connection_mode: 'local', models: [] }, 'local');
    assert.equal(button.disabled, true);
    assert.equal(audio.beginSubmission(button), false);
  }
  audio.endSubmission(button, true);
  assert.equal(button.disabled, false);
  assert.equal(audio.beginSubmission(button), true);
  audio.endSubmission(button, false);
  assert.equal(button.disabled, true);
});

test('remote authentication, offline, node and model states give distinct guidance and execution location', () => {
  const auth = audio.musicStatusPresentation({ error_code: 'authentication', available: false, error: '請檢查金鑰' }, 'remote');
  assert.match(auth.title, /認證失敗/);
  assert.doesNotMatch(auth.title, /安裝失敗/);
  const offline = audio.musicStatusPresentation({ error_code: 'connection', available: false }, 'remote');
  assert.match(offline.title, /無法連線/);
  const nodes = audio.musicStatusPresentation({ error_code: 'nodes_missing' }, 'remote');
  assert.match(nodes.title, /節點/);
  const models = audio.musicStatusPresentation({ error_code: 'models_missing' }, 'remote');
  assert.match(models.title, /模型/);
  const ready = audio.musicStatusPresentation({ installed: true, connection_mode: 'remote', connection_url: 'https://gpu.test' });
  assert.equal(ready.canInstall, false);
  assert.match(ready.description, /遠端 GPU 主機/);
  assert.match(ready.detail, /https:\/\/gpu.test/);
  assert.match(ready.note, /回存這台電腦/);
});
