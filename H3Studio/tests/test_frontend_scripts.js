const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { normalizeScriptLoras, scriptLoraSourceUrl, scriptLoraSelectionError, scriptLoraSaveConfirmed } = require('../static/scripts.js');
const loraHelpSource = fs.readFileSync(path.join(__dirname, '../static/lora-help.js'), 'utf8');
const { normalizeScriptProject, normalizeScriptReferences, createNewScriptProject, createMonkeyScriptExample, scriptProjectSignature, scriptSaveOutcome, scriptDraftCandidates, scriptJobPresentation, scriptCanDeleteJob, scriptAppearanceApplyTarget, scriptPipelinePresentation, scriptPipelineVideoUrl, scriptVideoReferencesError, scriptLocalEndpoint, normalizeScriptSkills, scriptSkillSourceUrl, scriptRecommendedWritingSkill, scriptVideoHistoryRecord, scriptVideoHistoryDownloadUrl } = require('../static/scripts.js');
const source = fs.readFileSync(path.join(__dirname, '../static/scripts.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/scripts.html'), 'utf8');

function skillCatalog() {
  return ['cinematic-story', 'suspense-horror', 'comedy-reaction', 'emotional-drama', 'fantasy-adventure', 'action-chase', 'product-commercial', 'product-demo', 'educational-explainer', 'documentary-lifestyle', 'game-trailer'].map((id, index) => ({ id, name: ['通用電影敘事', '懸疑／恐怖', '喜劇／角色反應', '情感／人物劇情', '奇幻／探險', '動作／追逐', '商品／品牌廣告', '產品／操作展示', '知識／教學解說', '紀錄／生活風格', '遊戲／角色宣傳'][index], category: '短片', description: `適合 ${id} 的短片方法`, recommended_for: ['角色短片'], guide: ['確定角色目的', '寫出可見轉折'], sources: [{ name: '開放創作方法', url: 'https://example.com/skill', license: 'MIT', license_url: 'https://example.com/license' }] }));
}

function fixture(handler, { confirm = () => true, referencesSupported = true, referencesSupportKnown = true, skillsSupported = true, skillsSupportKnown = true, skillsCatalogReady = true, lorasSupported = true, lorasSupportKnown = true } = {}) {
  const elements = new Map(), stored = new Map(), calls = [], downloads = [];
  class Option { constructor(text, value) { this.textContent = text; this.value = value; } }
  class Element {
    constructor(tagName = '') {
      this.tagName = tagName; this.events = new Map();
      this.value = ''; this.textContent = ''; this.disabled = false; this.options = []; this.children = []; this.attributes = {}; this.dataset = {};
      this.classes = new Set();
      this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name), toggle: (name, flag) => { const set = flag === undefined ? !this.classes.has(name) : flag; if (set) this.classes.add(name); else this.classes.delete(name); } };
    }
    add(option) { this.options.push(option); }
    replaceChildren(...children) { this.children = children; this.options = children.filter(child => child instanceof Option); }
    append(...children) { this.children.push(...children); }
    addEventListener(name, handler) { this.events.set(name, [...(this.events.get(name) || []), handler]); }
    async dispatch(name) { for (const handler of this.events.get(name) || []) await handler({ target: this, preventDefault() {} }); }
    click() { return this.dispatch('click'); }
    remove() { this.removed = true; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
    focus() { this.focused = true; }
    scrollIntoView(options) { this.lastScroll = options; }
  }
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  elements.get('scriptDuration').value = '15';
  elements.get('scriptGenre').value = '劇情';
  elements.get('scriptGenre').add(new Option('劇情', '劇情'));
  elements.get('scriptVideoSource').value = 'current';
  elements.get('scriptVideoSource').add(new Option('編輯稿／故事想法', 'current'));
  elements.get('scriptVideoSource').add(new Option('目前選擇的已完成劇本', 'preview'));
  const actions = ['outline', 'screenplay', 'rewrite', 'storyboard'].map(action => { const element = new Element(); element.dataset.scriptAction = action; return element; });
  class TestFormData { constructor() { this.entries = []; } append(...entry) { this.entries.push(entry); } }
  class TestURL extends URL { static createObjectURL(blob) { downloads.push(blob); return 'blob:script-test'; } static revokeObjectURL() {} }
  let uuids = 0;
  const context = vm.createContext({
    document: { hidden: false, body: new Element('body'), getElementById(id) { assert.ok(elements.has(id), `HTML has ${id}`); return elements.get(id); }, querySelectorAll: () => actions, createElement: name => new Element(name), addEventListener() {} },
    window: { confirm, location: { search: '' }, addEventListener() {} },
    localStorage: { get length() { return stored.size; }, key: i => Array.from(stored.keys())[i], getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) },
    crypto: { randomUUID: () => uuids++ === 0 ? 'this-tab' : `${String(uuids).padStart(8, '0')}-0000-4000-8000-000000000000` }, Option, URL: TestURL, URLSearchParams, FormData: TestFormData, Blob, setTimeout, clearTimeout, setInterval() {},
    async fetch(url, options) { calls.push({ url, options }); return handler ? handler(url, options) : response({}); },
  });
  const exposed = source.replace('start().catch(error => message(error.message, true));', 'globalThis.scriptTest = { state, message, writeProject, readProject, markDirty, saveProject, switchProject, applyMonkeyExample, loadJobs, renderHistory, renderPreview, selectJob, deleteJob, changeHistoryScope, openJobProject, applyResult, restoreDraft, offerDraft, generate, crossTabChanged, saveSettings, addReference, removeReference, uploadReferenceImage, renderReferences, exportDraft, refreshEngine, renderPipelines, selectPipeline, loadPipelines, startVideoPipeline, cancelPipeline, resumePipeline, selectedVideoSourceJob, bindEvents, renderWritingSkills, renderWritingSkillDetails, applyWritingSkillRecommendation, loadWritingSkills, receiveVideoHistory, renderVideoHistory, selectHistoryVideo, changeVideoHistoryScope, refreshVideoHistory }; writeProject(state.project);');
  vm.runInContext(loraHelpSource, context);
  vm.runInContext(exposed.replace('globalThis.scriptTest = { state,', 'globalThis.scriptTest = { loadScriptLoras, renderScriptLoras, addScriptLora, importStoryboard, state,'), context);
  Object.assign(context.scriptTest.state, { lorasSupported, lorasSupportKnown });
  context.scriptTest.state.referencesSupported = referencesSupported;
  context.scriptTest.state.referencesSupportKnown = referencesSupportKnown;
  Object.assign(context.scriptTest.state, { skillsSupported, skillsSupportKnown, skillsCatalogReady, writingSkills: skillsCatalogReady ? skillCatalog() : [] });
  context.scriptTest.renderWritingSkills(); context.scriptTest.renderPreview();
  return { ui: context.scriptTest, elements, stored, calls, actions, downloads };
}

function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, async json() { return data; } }; }
function deferred() { let resolve; const promise = new Promise(callback => { resolve = callback; }); return { promise, resolve }; }
function project(overrides = {}) { return { id: 'project-a', title: '雨停之前', idea: '咖啡店裡一段相遇', genre: '劇情', duration: 60, characters: '', character_references: [], custom_loras: [], style: '', instructions: '', content: '原始編輯稿', writing_skill: '', revision: 3, created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', ...overrides }; }
function reference(overrides = {}) { return { id: 'a'.repeat(32), name: '金猴爺', type: 'character', image_asset_id: 'b'.repeat(32), image_name: '金猴爺.png', appearance: '紅金色服裝、小巧體型、金色鬍鬚', ...overrides }; }
function appearanceJob(original = reference(), overrides = {}) { return { id: 'appearance-job', project_id: 'project-a', status: 'completed', action: 'appearance', reference_id: original.id, reference_name: original.name, content: '頭戴紅金帽，短腿與金色鬍鬚，胸前有金色飾物。', input: { project: project({ character_references: [original] }) }, ...overrides }; }

test('fresh projects use the 15-second detailed animation profile without assigning sample characters', async () => {
  const fresh = createNewScriptProject();
  assert.equal(fresh.duration, 15);
  assert.equal(fresh.style, '精緻 3D CG 遊戲動畫風格，色彩明亮；依場景保留少量神祕冒險氛圍。');
  assert.equal(fresh.characters, '');
  assert.equal(fresh.idea, '');
  assert.equal(fresh.content, '');
  assert.equal(fresh.writing_skill, 'cinematic-story');
  assert.match(fresh.instructions, /零 BGM/);
  for (const phrase of ['連續時間碼', '焦段與機位', '地點', '空間關係', '動作／行為與表情', '運鏡', 'VFX', '聲音', '對白', '切黑', '固定使用']) assert.ok(fresh.instructions.includes(phrase));
  assert.doesNotMatch(fresh.instructions, /金猴爺|神祕女子/);
  const { ui, elements } = fixture(async () => response({ items: [] }));
  assert.equal(elements.get('scriptDuration').value, 15);
  assert.equal(elements.get('scriptStyle').value, fresh.style);
  assert.equal(elements.get('scriptInstructions').value, fresh.instructions);
  ui.writeProject(project({ style: '舊寫實風格', instructions: '只用城市環境音' }));
  await ui.switchProject('');
  assert.equal(elements.get('scriptDuration').value, 15);
  assert.equal(elements.get('scriptStyle').value, fresh.style);
  assert.equal(elements.get('scriptInstructions').value, fresh.instructions);
});

test('legacy project loading and crash recovery do not backfill the new animation profile', async () => {
  const old = { id: 'project-a', revision: 3, title: '舊劇本', content: '舊的分場與配樂設定' };
  const normalized = normalizeScriptProject(old);
  assert.equal(normalized.duration, 60);
  assert.equal(normalized.style, '');
  assert.equal(normalized.instructions, '');
  assert.equal(normalized.writing_skill, '');
  const { ui, elements } = fixture(async url => response(url.includes('/projects/') ? old : { items: [] }));
  await ui.switchProject('project-a');
  assert.equal(elements.get('scriptStyle').value, '');
  assert.equal(elements.get('scriptInstructions').value, '');
  assert.equal(elements.get('scriptContent').value, old.content);
  ui.state.recovery = { writer: 'old-tab', savedAt: 20, project: { ...old, content: '舊草稿的未存劇本' } };
  await ui.restoreDraft();
  assert.equal(elements.get('scriptStyle').value, '');
  assert.equal(elements.get('scriptInstructions').value, '');
  assert.equal(elements.get('scriptDuration').value, 60);
  assert.equal(elements.get('scriptContent').value, '舊草稿的未存劇本');
  assert.deepEqual(old, { id: 'project-a', revision: 3, title: '舊劇本', content: '舊的分場與配樂設定' });
});

test('the optional monkey example starts a separate unsaved draft and preserves every field of current work', async () => {
  const { ui, elements, stored, calls } = fixture(async () => response({ items: [] }));
  const current = project({ title: '自己的故事', duration: 90, characters: '原創角色', style: '寫實黑白', instructions: '自己的要求', content: '不能覆蓋的未存劇本' });
  ui.writeProject(current); ui.markDirty();
  await ui.applyMonkeyExample();
  const example = createMonkeyScriptExample();
  assert.equal(ui.state.project.id, null);
  assert.equal(ui.state.project.revision, 0);
  assert.equal(ui.state.dirty, true);
  assert.equal(elements.get('scriptTitle').value, example.title);
  assert.equal(elements.get('scriptDuration').value, 15);
  assert.equal(elements.get('scriptCharacters').value, '金猴爺＝圖一；神祕女子＝圖二；古代遺跡＝圖三');
  assert.equal(elements.get('scriptInstructions').value, example.instructions);
  assert.equal(elements.get('scriptContent').value, '');
  assert.ok(calls.every(call => !call.options.method || call.options.method === 'GET'));
  const drafts = JSON.parse(stored.get('h3studio.scripts.draft.this-tab'));
  assert.deepEqual(drafts['project-a'].project, current);
  const freshDraft = Object.values(drafts).find(record => record.project.id === null);
  assert.equal(freshDraft.project.title, example.title);
  assert.equal(freshDraft.project.instructions, example.instructions);
  for (const dialogue of ['「哇……」', '「嗚！？」', '「對、對不起啊——！」', '「等等等等——！」', '「這些……都給你唷。」', '「欸——！？」']) assert.ok(example.instructions.includes(dialogue));
  const segments = example.instructions.split('\n').filter(line => /^\d\. /.test(line));
  assert.equal(segments.length, 8);
  const ranges = ['0.0–2.0s', '2.0–4.0s', '4.0–5.5s', '5.5–7.0s', '7.0–8.5s', '8.5–10.0s', '10.0–12.5s', '12.5–15.0s'];
  const lenses = ['18mm', '24mm', '35mm', '50mm', '28mm', '50mm', '24mm', '65mm'];
  segments.forEach((line, index) => { assert.ok(line.includes(ranges[index])); assert.ok(line.includes(lenses[index])); });
  assert.match(example.instructions, /不特寫胸部/);
  assert.match(elements.get('scriptMessage').textContent, /角色外觀對照.*分別上傳圖一、圖二、圖三/);
});

test('declining the standard draft switch or switching while busy cannot replace work with the example', async () => {
  const { ui, elements, calls } = fixture(async () => response({ items: [] }), { confirm: () => false });
  ui.writeProject(project({ content: '必須留下的修改' })); ui.markDirty();
  await ui.applyMonkeyExample();
  assert.equal(elements.get('scriptContent').value, '必須留下的修改');
  assert.equal(ui.state.project.id, 'project-a');
  assert.equal(calls.length, 0);
  ui.state.dirty = false;
  ui.state.saving = true;
  ui.renderPreview();
  assert.equal(elements.get('applyMonkeyScriptExample').disabled, true);
  await ui.applyMonkeyExample();
  assert.equal(elements.get('scriptContent').value, '必須留下的修改');
  assert.equal(calls.length, 0);
});

test('saving never replaces text typed after a save request; a second save commits the latest revision', async () => {
  const pending = deferred();
  let first = true;
  const fixtureData = fixture(async (_, options) => {
    if (first) { first = false; return pending.promise; }
    const sent = JSON.parse(options.body);
    assert.equal(sent.revision, 4);
    assert.equal(sent.content, '儲存途中新增的對白');
    return response(project({ ...sent, revision: 5 }));
  });
  const { ui, elements, stored } = fixtureData;
  ui.writeProject(project());
  elements.get('scriptContent').value = '送出時的內容'; ui.markDirty();
  const saving = ui.saveProject();
  elements.get('scriptContent').value = '儲存途中新增的對白'; ui.markDirty();
  pending.resolve(response(project({ content: '送出時的內容', revision: 4 })));
  assert.equal(await saving, false);
  assert.equal(elements.get('scriptContent').value, '儲存途中新增的對白');
  assert.equal(ui.state.project.revision, 4);
  assert.equal(ui.state.dirty, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.content, '儲存途中新增的對白');
  assert.equal(await ui.saveProject(), true);
  assert.equal(ui.state.dirty, false);
  assert.equal(ui.state.project.revision, 5);
  assert.equal(stored.has('h3studio.scripts.draft.this-tab'), false);
});

test('409 retains every local field, original revision, and browser draft while exposing current server version', async () => {
  const { ui, elements, stored } = fixture(async (url, options) => options.method === 'PUT' ? response({ error: 'version conflict' }, 409) : response(project({ revision: 8, content: '另一個分頁的編輯稿' })));
  ui.writeProject(project());
  elements.get('scriptContent').value = '本分頁的修改';
  elements.get('scriptCharacters').value = '新增角色';
  ui.markDirty();
  assert.equal(await ui.saveProject(), false);
  assert.equal(elements.get('scriptContent').value, '本分頁的修改');
  assert.equal(elements.get('scriptCharacters').value, '新增角色');
  assert.equal(ui.state.project.revision, 3);
  assert.equal(ui.state.conflict.server.revision, 8);
  assert.equal(ui.state.conflict.server.content, '另一個分頁的編輯稿');
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.content, '本分頁的修改');
  assert.equal(elements.get('saveScriptProject').disabled, true);
});

test('saving a conflict as a copy creates a new project without modifying the conflicting project', async () => {
  const { ui, elements, calls } = fixture(async (_, options) => response(project({ ...JSON.parse(options.body), id: 'copy-id', revision: 1 })));
  ui.writeProject(project());
  elements.get('scriptContent').value = '完整保留的本機草稿'; ui.markDirty();
  ui.state.conflict = { id: 'project-a', server: project({ revision: 4 }) };
  assert.equal(await ui.saveProject({ copy: true }), true);
  assert.equal(calls[0].url, '/api/scripts/projects');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(JSON.parse(calls[0].options.body).revision, undefined);
  assert.equal(ui.state.project.id, 'copy-id');
  assert.equal(elements.get('scriptContent').value, '完整保留的本機草稿');
  assert.equal(ui.state.conflict, null);
});

test('new manual drafts save with a usable default title, and failed network saves retain edits', async () => {
  const { ui, elements, calls, stored } = fixture(async () => { throw Error('offline'); });
  elements.get('scriptContent').value = '還沒有標題的故事'; ui.markDirty();
  assert.equal(await ui.saveProject(), false);
  assert.equal(JSON.parse(calls[0].options.body).title, '未命名劇本');
  assert.equal(elements.get('scriptContent').value, '還沒有標題的故事');
  assert.equal(ui.state.dirty, true);
  assert.equal(ui.state.saving, false);
  assert.ok(stored.has('h3studio.scripts.draft.this-tab'));
});

test('project switching is blocked during an in-flight save and stale polling cannot overwrite another project', async () => {
  const oldPoll = deferred();
  const { ui, elements, calls } = fixture(async url => url === '/api/scripts/jobs' ? oldPoll.promise : response(project({ id: 'project-b', title: '第二份劇本', content: '第二份稿' })));
  ui.writeProject(project());
  ui.state.saving = true;
  await ui.switchProject('project-b');
  assert.equal(calls.length, 0);
  assert.equal(ui.state.project.id, 'project-a');
  ui.state.saving = false;
  const polling = ui.loadJobs();
  await ui.switchProject('project-b');
  oldPoll.resolve(response({ items: [{ id: 'old-job', project_id: 'project-a', status: 'completed', content: '舊專案的 AI 結果' }] }));
  await polling;
  assert.equal(ui.state.project.id, 'project-b');
  assert.equal(elements.get('scriptContent').value, '第二份稿');
  assert.equal(ui.state.job, null);
  assert.equal(ui.state.selectedJobId, null);
});

test('restoring an older crash draft preserves text and requires explicit conflict resolution', async () => {
  const { ui, elements, stored } = fixture();
  ui.writeProject(project({ revision: 9, content: '主機最新版本' }));
  ui.state.recovery = { writer: 'crashed-tab', savedAt: 20, project: project({ revision: 3, content: '崩潰前尚未儲存的稿' }) };
  await ui.restoreDraft();
  assert.equal(elements.get('scriptContent').value, '崩潰前尚未儲存的稿');
  assert.equal(ui.state.project.revision, 9);
  assert.equal(ui.state.conflict.server.content, '主機最新版本');
  assert.equal(ui.state.dirty, true);
  assert.ok(stored.has('h3studio.scripts.draft.this-tab'));
});

test('draft recovery finds unsaved edits from this tab after switching projects, while excluding already saved contents', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project());
  elements.get('scriptContent').value = '還沒儲存的稿'; ui.markDirty();
  ui.writeProject(project({ content: '原始編輯稿' }));
  ui.state.dirty = false;
  ui.offerDraft();
  assert.equal(ui.state.recovery.project.content, '還沒儲存的稿');
  const records = [{ project: project(), savedAt: 3 }, { project: project({ content: '更新的草稿' }), savedAt: 5 }, { project: project({ id: 'different' }), savedAt: 9 }];
  assert.equal(scriptDraftCandidates(records, project(), project()).length, 1);
});

test('AI results use literal text and do not update the manuscript until explicitly applied', () => {
  const { ui, elements } = fixture();
  const malicious = '<img src=x onerror="alert(1)">\n# 模型文字';
  ui.writeProject(project());
  ui.state.job = { id: 'job', project_id: 'project-a', status: 'completed', action: 'screenplay', content: malicious };
  ui.renderPreview();
  assert.equal(elements.get('scriptPreviewContent').textContent, malicious);
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  ui.applyResult();
  assert.equal(elements.get('scriptContent').value, malicious);
  assert.equal(ui.state.dirty, true);
  assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test('job details show the actual reduced output budget only when adjusted with a valid positive integer', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project());
  const base = { id: 'adjusted-job', project_id: 'project-a', status: 'running', action: 'screenplay', created_at: '2026-10-02T00:00:00Z', input: { settings: { num_predict: 2048 } } };
  ui.state.job = base;
  ui.renderPreview();
  const original = elements.get('scriptJobDetail').textContent;
  assert.doesNotMatch(original, /本次輸出上限調整/);
  ui.state.job = { ...base, output_budget_adjusted: true };
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, `${original} · 依稿件長度，本次輸出上限調整為 2048 Token。`);
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, `${original} · 依稿件長度，本次輸出上限調整為 2048 Token。`);
  ui.state.job = { ...base, status: 'completed', content: '已完成的文字', output_budget_adjusted: true };
  ui.renderPreview();
  assert.match(elements.get('scriptJobDetail').textContent, /結果已保存，確認後可套用到編輯稿。 · 依稿件長度，本次輸出上限調整為 2048 Token。$/);
  for (const invalid of [undefined, null, {}, { settings: null }, { settings: {} }, ...[undefined, null, 0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '2048'].map(num_predict => ({ settings: { num_predict } }))]) {
    ui.state.job = { ...base, output_budget_adjusted: true, input: invalid };
    ui.renderPreview();
    assert.equal(elements.get('scriptJobDetail').textContent, original);
  }
  for (const flag of [undefined, false, null, 'true', 1]) {
    ui.state.job = { ...base, output_budget_adjusted: flag };
    ui.renderPreview();
    assert.equal(elements.get('scriptJobDetail').textContent, original);
  }
});

test('normalized storyboard previews explain merged cuts while retaining raw output and the manuscript', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project({ duration: 15 }));
  const content = JSON.stringify({ shots: [2, 2, 2.5, 2.5, 3, 3].map(duration => ({ duration, action: '原始切鏡' })) });
  const metadata = { original_shot_count: 6, generation_clip_count: 1, clip_durations: [15] };
  const job = { id: 'merged-storyboard', project_id: 'project-a', action: 'storyboard', status: 'completed', content, storyboard: { shots: [{ duration: 15, action: '保留六個切鏡' }], storyboard_normalization: metadata } };
  const original = JSON.stringify(job);
  ui.state.job = job;
  ui.renderPreview();
  const detail = elements.get('scriptJobDetail').textContent;
  assert.match(detail, /已將6個切鏡整理為1個影片片段，總長15秒；原始分鏡保留。$/);
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, detail);
  assert.equal(elements.get('scriptPreviewContent').textContent, content);
  assert.equal(elements.get('importScriptStoryboard').disabled, false);
  ui.applyResult();
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(ui.state.dirty, false);
  assert.equal(JSON.stringify(job), original);
  ui.state.job = { ...job, storyboard: {}, storyboard_normalization: { original_shot_count: 3, generation_clip_count: 2, clip_durations: [6, 5.5] } };
  ui.renderPreview();
  assert.match(elements.get('scriptJobDetail').textContent, /已將3個切鏡整理為2個影片片段，總長11.5秒；原始分鏡保留。$/);
});

test('storyboard normalization captions require complete jobs and valid metadata', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project());
  const metadata = { original_shot_count: 6, generation_clip_count: 1, clip_durations: [15] };
  const base = { id: 'storyboard', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '原始分鏡', storyboard: { storyboard_normalization: metadata } };
  for (const change of [{ action: 'screenplay' }, { status: 'running' }, { status: 'failed', error: '連線失敗' }, { truncated: true }, { storyboard: undefined }]) {
    ui.state.job = { ...base, ...change }; ui.renderPreview();
    assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /個切鏡整理為|原始分鏡保留/);
  }
  for (const invalid of [null, {}, { ...metadata, original_shot_count: '6' }, { ...metadata, generation_clip_count: 0 }, { ...metadata, clip_durations: [15, 5] }, { ...metadata, clip_durations: ['15'] }, { ...metadata, clip_durations: [NaN] }, { ...metadata, clip_durations: [Infinity] }, { ...metadata, clip_durations: [-1] }]) {
    ui.state.job = { ...base, storyboard: { storyboard_normalization: invalid } }; ui.renderPreview();
    assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /個切鏡整理為|NaN|Infinity/);
    assert.equal(elements.get('scriptPreviewContent').textContent, base.content);
  }
});

test('old short-cut validation failures show a recovery hint without changing the error or draft', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project());
  const error = 'H3 分鏡的每個鏡頭必須介於 5～15 秒。';
  ui.state.job = { id: 'old-failure', project_id: 'project-a', action: 'storyboard', status: 'failed', content: '{"shots":[{"duration":2}]}', error };
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, `${error} · 短切鏡需合併成 5～15 秒生成片段，更新後可使用原設定接續。`);
  assert.equal(ui.state.job.error, error);
  assert.equal(elements.get('scriptPreviewContent').textContent, ui.state.job.content);
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(elements.get('importScriptStoryboard').disabled, true);
  for (const change of [{ error: '分鏡總長 30 秒與目標 15 秒不符' }, { action: 'screenplay' }, { status: 'completed' }]) {
    ui.state.job = { ...ui.state.job, error, action: 'storyboard', status: 'failed', ...change }; ui.renderPreview();
    assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /短切鏡需合併/);
  }
  assert.match(html, /scripts\.js\?v=20261005-script-loras/);
});

function cameraCorrection(overrides = {}) { return { scene_index: 0, shot_index: 0, original_camera: '35mm low-angle tracking shot', original_camera_detail: '鏡頭跟隨主角前進', camera: 'custom', camera_detail: '35mm low-angle tracking shot\n鏡頭跟隨主角前進', ...overrides }; }

test('camera-normalized complete storyboards explain retained lens and position without altering original output or manuscript', () => {
  const { ui, elements } = fixture(); ui.writeProject(project({ duration: 15 }));
  const content = JSON.stringify({ scenes: [{ shots: [{ duration: 15, camera: '35mm low-angle tracking shot', camera_detail: '鏡頭跟隨主角前進' }] }] });
  const metadata = { correction_count: 1, corrections: [cameraCorrection()] };
  const job = { id: 'camera-normalized', project_id: 'project-a', action: 'storyboard', status: 'completed', content, storyboard: { storyboard_camera_normalization: metadata } };
  const original = JSON.stringify(job); ui.state.job = job; ui.renderPreview();
  const detail = elements.get('scriptJobDetail').textContent;
  assert.match(detail, /已整理1個鏡頭的運鏡設定，原有焦段與機位描述已保留；原始分鏡未改。$/);
  assert.equal(elements.get('scriptPreviewContent').textContent, content); assert.equal(elements.get('importScriptStoryboard').disabled, false);
  ui.renderPreview(); assert.equal(elements.get('scriptJobDetail').textContent, detail);
  ui.applyResult(); assert.equal(elements.get('scriptContent').value, '原始編輯稿'); assert.equal(ui.state.dirty, false);
  assert.equal(JSON.stringify(job), original);
  ui.state.job = { ...job, storyboard: { ...job.storyboard, storyboard_normalization: { original_shot_count: 6, generation_clip_count: 1, clip_durations: [15] } } };
  ui.renderPreview();
  assert.match(elements.get('scriptJobDetail').textContent, /已將6個切鏡整理為1個影片片段，總長15秒；原始分鏡保留。 · 已整理1個鏡頭/);
  assert.equal(elements.get('scriptPreviewContent').textContent, content);
});

test('camera captions reject incomplete jobs and invalid correction metadata', () => {
  const { ui, elements } = fixture(); ui.writeProject(project());
  const metadata = { correction_count: 1, corrections: [cameraCorrection()] };
  const base = { id: 'camera-normalized', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '原始分鏡', storyboard: { storyboard_camera_normalization: metadata } };
  for (const change of [{ status: 'failed', error: '分鏡 camera 必須使用支援的鏡頭運動代號。' }, { status: 'running' }, { action: 'screenplay' }, { truncated: true }, { storyboard: undefined }]) {
    ui.state.job = { ...base, ...change }; ui.renderPreview();
    assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /運鏡設定|焦段與機位描述已保留|原始分鏡未改/);
  }
  for (const invalid of [null, {}, { ...metadata, correction_count: '1' }, { ...metadata, correction_count: 0 }, { ...metadata, correction_count: 2 }, { ...metadata, corrections: [null] }, ...[{ original_camera: {} }, { original_camera: '' }, { camera: 'static' }, { camera_detail: '' }, { original_camera_detail: undefined }, { scene_index: -1 }, { shot_index: '0' }].map(change => ({ ...metadata, corrections: [cameraCorrection(change)] }))]) {
    ui.state.job = { ...base, storyboard: { storyboard_camera_normalization: invalid } }; ui.renderPreview();
    assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /運鏡設定|焦段與機位描述已保留|原始分鏡未改/);
    assert.equal(elements.get('scriptPreviewContent').textContent, base.content);
  }
});

test('failed camera and truncated short-cut jobs stay unavailable without a recovery claim', () => {
  const { ui, elements } = fixture(); ui.writeProject(project());
  for (const job of [{ error: '分鏡 camera 必須使用支援的鏡頭運動代號。', truncated: false }, { error: 'H3 分鏡的每個鏡頭必須介於 5～15 秒。', truncated: true }, { error: '連線失敗', truncated: false }]) {
    ui.state.job = { id: 'failed-storyboard', project_id: 'project-a', action: 'storyboard', status: 'failed', content: '原始錯誤分鏡', ...job }; ui.renderPreview();
    assert.equal(elements.get('scriptJobDetail').textContent, job.error);
    assert.equal(elements.get('importScriptStoryboard').disabled, true); assert.equal(elements.get('applyScriptResult').disabled, true);
    assert.equal(elements.get('copyScriptResult').disabled, false); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  }
});

test('partial, failed, cancelled, and interrupted jobs cannot be applied or imported; storyboards never replace manuscripts', () => {
  for (const status of ['queued', 'running', 'failed', 'cancelled', 'interrupted']) {
    for (const action of ['screenplay', 'storyboard']) {
      const result = scriptJobPresentation({ status, action, content: '已有部分文字' });
      assert.equal(result.canApply, false);
      assert.equal(result.canImport, false);
    }
  }
  assert.equal(scriptJobPresentation({ status: 'completed', action: 'storyboard', content: '{}' }).canApply, false);
  assert.equal(scriptJobPresentation({ status: 'completed', action: 'storyboard', content: '{}' }).canImport, true);
});

test('historical completed jobs with truncated output remain visible and copyable but cannot replace or import a manuscript', () => {
  for (const action of ['screenplay', 'storyboard']) {
    const { ui, elements } = fixture();
    ui.writeProject(project());
    const content = action === 'storyboard' ? '{"shots":[{"action":"尚未結束' : '14.0–15.0s｜收尾\n尚未完成的動作';
    const historical = { id: 'old-truncated-job', project_id: 'project-a', status: 'completed', action, content, truncated: true };
    ui.state.job = historical;
    ui.renderPreview();
    assert.equal(elements.get('scriptJobBadge').textContent, '內容未完成');
    assert.equal(elements.get('scriptPreviewContent').textContent, content);
    assert.match(elements.get('scriptJobDetail').textContent, /已達輸出 Token 上限/);
    assert.equal(elements.get('applyScriptResult').disabled, true);
    assert.equal(elements.get('importScriptStoryboard').disabled, true);
    assert.equal(elements.get('copyScriptResult').disabled, false);
    assert.equal(elements.get('exportScript').disabled, false);
    assert.equal(scriptJobPresentation(historical).active, false);
    ui.applyResult();
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
    assert.equal(ui.state.dirty, false);
    assert.deepEqual(ui.state.job, historical);
  }
});

test('complete historical jobs without a truncation flag still allow their appropriate action', () => {
  for (const action of ['screenplay', 'storyboard']) {
    for (const truncated of [undefined, false]) {
      const { ui, elements } = fixture();
      ui.writeProject(project());
      ui.state.job = { id: 'complete-job', project_id: 'project-a', status: 'completed', action, content: '已完成內容', truncated };
      ui.renderPreview();
      assert.equal(elements.get('scriptJobBadge').textContent, '已完成');
      assert.equal(elements.get('applyScriptResult').disabled, action === 'storyboard');
      assert.equal(elements.get('importScriptStoryboard').disabled, action !== 'storyboard');
    }
  }
});

test('loopback settings reject credentials, cloud hosts, query strings, and API paths', () => {
  for (const address of ['http://127.0.0.1:11434', 'http://localhost:11434/', 'http://[::1]:11434']) assert.equal(scriptLocalEndpoint(address), true);
  for (const address of ['https://api.example.com', 'http://127.0.0.1:11434/api', 'http://user:pass@localhost:11434', 'http://localhost:11434?target=cloud', 'http://127.0.0.1.example.com:11434', 'file:///tmp/model']) assert.equal(scriptLocalEndpoint(address), false);
});

test('save reconciliation distinguishes a normalized server result from edits made in transit', () => {
  const sent = project({ content: '  角色對白  ' });
  const normalized = project({ content: '角色對白', revision: 4 });
  const stable = scriptSaveOutcome(normalized, sent, sent);
  assert.equal(stable.dirty, false);
  assert.equal(stable.project.content, '角色對白');
  const changed = scriptSaveOutcome(normalized, project({ content: '追加新的對白' }), sent);
  assert.equal(changed.dirty, true);
  assert.equal(changed.project.content, '追加新的對白');
  assert.equal(changed.project.revision, 4);
});

test('starting a second unsaved new project preserves the first complete draft under a separate identity', async () => {
  const { ui, elements, stored } = fixture(async () => response({ items: [] }));
  elements.get('scriptTitle').value = '草稿 A';
  elements.get('scriptContent').value = '第一份未存劇本'; ui.markDirty();
  await ui.switchProject('');
  elements.get('scriptTitle').value = '草稿 B';
  elements.get('scriptContent').value = '第二份未存劇本'; ui.markDirty();
  const drafts = Object.values(JSON.parse(stored.get('h3studio.scripts.draft.this-tab')));
  assert.equal(drafts.length, 2);
  assert.ok(drafts.some(draft => draft.project.title === '草稿 A' && draft.project.content === '第一份未存劇本'));
  assert.ok(drafts.some(draft => draft.project.title === '草稿 B' && draft.project.content === '第二份未存劇本'));
});

test('an old job-list response preserves a newly submitted active job and cannot enable duplicate generation', async () => {
  const stalePoll = deferred();
  const freshJob = { id: 'fresh-job', project_id: 'project-a', status: 'queued', action: 'outline', content: '' };
  const { ui, actions, calls } = fixture(async url => url === '/api/scripts/jobs' ? stalePoll.promise : response(freshJob));
  ui.writeProject(project());
  ui.state.ready = true;
  const polling = ui.loadJobs();
  ui.state.jobsRevision++;
  ui.state.jobs.unshift(freshJob);
  ui.state.selectedJobId = freshJob.id;
  ui.state.job = freshJob;
  stalePoll.resolve(response({ items: [] }));
  await polling;
  assert.equal(ui.state.jobs.length, 1);
  assert.equal(ui.state.jobs[0].id, 'fresh-job');
  assert.ok(actions.every(action => action.disabled));
  const previousCount = calls.length;
  await ui.generate('outline');
  assert.equal(calls.length, previousCount);
});

test('existing manuscripts can generate without duplicated ideas and concept-only projects can generate storyboards', async () => {
  for (const scenario of [{ action: 'outline', idea: '', content: '已寫好的劇本' }, { action: 'rewrite', idea: '', content: '已寫好的劇本' }, { action: 'storyboard', idea: '咖啡店相遇', content: '' }]) {
    const { ui, calls } = fixture(async (_, options) => response({ id: 'new-job', project_id: 'project-a', action: scenario.action, status: 'queued', content: '' }));
    ui.writeProject(project(scenario)); ui.state.ready = true;
    await ui.generate(scenario.action);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/scripts/jobs');
    assert.equal(JSON.parse(calls[0].options.body).action, scenario.action);
  }
});

test('model settings fields remain disabled during save and status refresh, preventing edits from being replaced', async () => {
  const saving = deferred();
  const settings = { base_url: 'http://127.0.0.1:11434', model: 'qwen3.8:27b', temperature: 0.8, num_ctx: 8192, num_predict: 4096 };
  const { ui, elements } = fixture(async url => url.endsWith('/settings') ? saving.promise : response({ ready: true, settings, models: [{ name: settings.model }] }));
  elements.get('scriptBaseUrl').value = settings.base_url;
  elements.get('scriptModel').value = settings.model;
  elements.get('scriptTemperature').value = settings.temperature;
  elements.get('scriptContext').value = settings.num_ctx;
  elements.get('scriptMaxOutput').value = settings.num_predict;
  const pending = ui.saveSettings({ preventDefault() {} });
  assert.equal(ui.state.settingsSaving, true);
  for (const id of ['scriptBaseUrl', 'scriptModel', 'scriptTemperature', 'scriptContext', 'scriptMaxOutput']) assert.equal(elements.get(id).disabled, true);
  saving.resolve(response(settings));
  await pending;
  assert.equal(ui.state.settingsSaving, false);
  for (const id of ['scriptBaseUrl', 'scriptModel', 'scriptTemperature', 'scriptContext', 'scriptMaxOutput']) assert.equal(elements.get(id).disabled, false);
});

test('character references normalize as separate deep copied records and participate in dirty signatures', () => {
  const original = reference();
  const normalized = normalizeScriptProject(project({ character_references: [original] }));
  assert.deepEqual(normalized.character_references, [original]);
  assert.notEqual(normalized.character_references[0], original);
  normalized.character_references[0].appearance = '新的外觀';
  assert.equal(original.appearance, reference().appearance);
  assert.notEqual(scriptProjectSignature(normalized), scriptProjectSignature(project({ character_references: [original] })));
  assert.deepEqual(normalizeScriptReferences({ appearance: '不可轉成字串' }), []);
  assert.deepEqual(normalizeScriptReferences([null, 'invalid', { id: 'bad' }]), []);
  assert.equal(normalizeScriptReferences([original, original]).length, 1);
  assert.equal(normalizeScriptReferences(Array.from({ length: 20 }, (_, index) => reference({ id: index.toString(16).padStart(32, '0') }))).length, 16);
  assert.deepEqual(normalizeScriptProject({ content: '舊劇本' }).character_references, []);
});

test('character cards safely render literal names and descriptions and allow manual appearance without a model', async () => {
  const { ui, elements, actions } = fixture();
  ui.writeProject(project({ character_references: [reference({ name: '<img src=x onerror=alert(1)>' })] }));
  const card = elements.get('scriptCharacterReferences').children[0];
  assert.equal(card.dataset.referenceId, reference().id);
  const controls = ui.state.referenceControls.get(reference().id);
  assert.equal(controls.name.value, '<img src=x onerror=alert(1)>');
  assert.equal(card.children.find(child => child.tagName === 'img').src, `/api/assets/${reference().image_asset_id}`);
  assert.equal(controls.analyze.disabled, true);
  controls.appearance.value = '手寫外觀：金色毛髮、紅金帽';
  await controls.appearance.dispatch('input');
  assert.equal(ui.readProject().character_references[0].appearance, controls.appearance.value);
  ui.state.ready = true; ui.renderPreview();
  assert.ok(actions.every(action => !action.disabled));
  assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test('adding and removing reference cards preserves independent identities and enforces the 16-card limit', () => {
  const { ui, elements } = fixture();
  ui.addReference(); ui.addReference();
  const initial = ui.readProject().character_references;
  assert.equal(initial.length, 2);
  assert.notEqual(initial[0].id, initial[1].id);
  assert.match(initial[0].id, /^[a-f0-9]{32}$/);
  for (let index = 0; index < 20; index++) ui.addReference();
  assert.equal(ui.readProject().character_references.length, 16);
  assert.equal(elements.get('addScriptReference').disabled, true);
  ui.removeReference(initial[0].id);
  assert.equal(ui.readProject().character_references.length, 15);
  assert.equal(elements.get('addScriptReference').disabled, false);
});

test('reference edits made during saving stay in the editor and crash draft while sent references reach the server', async () => {
  const pending = deferred();
  const { ui, calls, stored } = fixture(async () => pending.promise);
  ui.writeProject(project({ character_references: [reference()] })); ui.markDirty();
  const saving = ui.saveProject();
  assert.equal(JSON.parse(calls[0].options.body).character_references[0].appearance, reference().appearance);
  const controls = ui.state.referenceControls.get(reference().id);
  controls.appearance.value = '儲存途中新增的金色耳飾'; await controls.appearance.dispatch('input');
  pending.resolve(response(project({ revision: 4, character_references: [reference()] })));
  assert.equal(await saving, false);
  assert.equal(ui.readProject().character_references[0].appearance, controls.appearance.value);
  assert.equal(ui.state.project.revision, 4);
  const draft = JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'];
  assert.equal(draft.project.character_references[0].appearance, controls.appearance.value);
  assert.equal(ui.state.dirty, true);
});

test('reference-only drafts are recoverable and JSON export includes unsaved appearance and image mappings', async () => {
  const { ui, elements, stored, downloads } = fixture();
  const fresh = createNewScriptProject(); fresh.character_references = [reference()];
  ui.writeProject(fresh); ui.markDirty();
  const drafts = Object.values(JSON.parse(stored.get('h3studio.scripts.draft.this-tab')));
  assert.equal(scriptDraftCandidates(drafts, { id: null }, null).length, 1);
  ui.writeProject(createNewScriptProject()); ui.state.dirty = false; ui.offerDraft();
  await ui.restoreDraft();
  assert.equal(ui.readProject().character_references[0].appearance, reference().appearance);
  elements.get('scriptExportFormat').value = 'json'; ui.exportDraft();
  const exported = JSON.parse(await downloads[0].text());
  assert.deepEqual(exported.character_references, [reference()]);
  assert.equal(exported.id, null);
});

test('reference upload sends multipart without a JSON header and updates only image data', async () => {
  const newImage = 'c'.repeat(32);
  const { ui, calls, elements } = fixture(async () => response({ id: newImage, name: '新的角色.png' }));
  ui.writeProject(project({ character_references: [reference()] }));
  const file = { name: '新的角色.png', size: 100, type: 'image/png' };
  await ui.uploadReferenceImage(reference().id, file);
  assert.equal(calls[0].url, '/api/scripts/reference-images');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['Content-Type'], undefined);
  assert.equal(calls[0].options.body.entries[0][0], 'file');
  assert.equal(calls[0].options.body.entries[0][1], file);
  const current = ui.readProject().character_references[0];
  assert.equal(current.name, reference().name);
  assert.equal(current.image_asset_id, newImage);
  assert.equal(current.image_name, file.name);
  assert.equal(current.appearance, '');
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(ui.state.dirty, true);
});

test('invalid images and failed uploads preserve existing mappings and manual appearance', async () => {
  const { ui, calls, elements } = fixture(async () => { throw Error('network offline'); });
  ui.writeProject(project({ character_references: [reference()] }));
  for (const file of [{ name: 'file.svg', size: 10 }, { name: 'too-large.png', size: 8 * 1024 * 1024 + 1 }, { name: 'empty.jpg', size: 0 }]) await ui.uploadReferenceImage(reference().id, file);
  assert.equal(calls.length, 0);
  await ui.uploadReferenceImage(reference().id, { name: 'valid.png', size: 100 });
  assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().character_references)), [reference()]);
  assert.match(elements.get('scriptMessage').textContent, /無法連線/);
  assert.equal(ui.state.referenceUploads.size, 0);
});

test('a removed card or an older replaced upload cannot receive late image results', async () => {
  const first = deferred(), second = deferred();
  let uploads = 0;
  const { ui } = fixture(async () => uploads++ === 0 ? first.promise : second.promise);
  ui.writeProject(project({ character_references: [reference()] }));
  const uploadingFirst = ui.uploadReferenceImage(reference().id, { name: 'first.png', size: 100 });
  const uploadingSecond = ui.uploadReferenceImage(reference().id, { name: 'second.png', size: 100 });
  second.resolve(response({ id: 'd'.repeat(32), name: 'second.png' })); await uploadingSecond;
  first.resolve(response({ id: 'c'.repeat(32), name: 'first.png' })); await uploadingFirst;
  assert.equal(ui.readProject().character_references[0].image_name, 'second.png');
  const removed = deferred();
  const data = fixture(async () => removed.promise); data.ui.writeProject(project({ character_references: [reference()] }));
  const pending = data.ui.uploadReferenceImage(reference().id, { name: 'removed.png', size: 100 });
  data.ui.removeReference(reference().id);
  removed.resolve(response({ id: 'e'.repeat(32), name: 'removed.png' })); await pending;
  assert.equal(data.ui.readProject().character_references.length, 0);
});

test('image uploads cannot cross project switches and preserve appearance typed while an upload is pending', async () => {
  const late = deferred();
  const { ui } = fixture(async url => url === '/api/scripts/reference-images' ? late.promise : response(url.includes('/projects/') ? project({ id: 'project-b', character_references: [reference()] }) : { items: [] }));
  ui.writeProject(project({ character_references: [reference()] }));
  const uploading = ui.uploadReferenceImage(reference().id, { name: 'project-a.png', size: 100 });
  await ui.switchProject('project-b');
  late.resolve(response({ id: 'c'.repeat(32), name: 'project-a.png' })); await uploading;
  assert.equal(ui.state.project.id, 'project-b');
  assert.equal(ui.readProject().character_references[0].image_name, reference().image_name);
  const manual = deferred(); const data = fixture(async () => manual.promise);
  data.ui.writeProject(project({ character_references: [reference()] }));
  const pending = data.ui.uploadReferenceImage(reference().id, { name: 'updated.png', size: 100 });
  const controls = data.ui.state.referenceControls.get(reference().id); controls.appearance.value = '上傳期間手寫的新外觀'; await controls.appearance.dispatch('input');
  manual.resolve(response({ id: 'd'.repeat(32), name: 'updated.png' })); await pending;
  assert.equal(data.ui.readProject().character_references[0].appearance, controls.appearance.value);
});

test('appearance analysis saves reference-only projects before submission and works when the writer is unavailable', async () => {
  const { ui, calls } = fixture(async (url, options) => url === '/api/scripts/projects' ? response(project({ ...JSON.parse(options.body), revision: 1 })) : response(appearanceJob(reference(), { status: 'queued', content: '' })));
  ui.writeProject({ ...createNewScriptProject(), character_references: [reference()] }); ui.markDirty(); ui.state.visionReady = true;
  await ui.generate('appearance', reference().id);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/api/scripts/projects');
  assert.equal(calls[1].url, '/api/scripts/jobs');
  assert.deepEqual(JSON.parse(calls[1].options.body), { project_id: 'project-a', action: 'appearance', reference_id: reference().id });
  assert.equal(ui.state.selectedJobId, 'appearance-job');
});

test('appearance results require explicit application and never replace manuscript content', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project({ character_references: [reference()] })); ui.state.job = appearanceJob(); ui.renderPreview();
  assert.equal(elements.get('applyScriptResult').textContent, '套用角色外觀');
  assert.equal(elements.get('applyScriptResult').disabled, false);
  assert.equal(ui.readProject().character_references[0].appearance, reference().appearance);
  ui.applyResult();
  assert.equal(ui.readProject().character_references[0].appearance, ui.state.job.content);
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(ui.state.dirty, true);
});

test('caption application retains manual edits and rejects renamed, retyped, replaced, removed, or unverified cards', () => {
  for (const change of [{ name: '新角色' }, { type: 'background' }, { image_asset_id: 'c'.repeat(32) }, { appearance: '分析期間自己寫的外觀' }]) {
    const { ui, elements } = fixture(); ui.writeProject(project({ character_references: [reference(change)] })); ui.state.job = appearanceJob();
    ui.applyResult();
    assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().character_references)), [reference(change)]);
    assert.match(elements.get('scriptMessage').textContent, /請複製結果/);
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
    assert.equal(ui.state.dirty, false);
  }
  assert.ok(scriptAppearanceApplyTarget(appearanceJob(), project()).error);
  assert.ok(scriptAppearanceApplyTarget(appearanceJob(reference(), { input: {} }), project({ character_references: [reference()] })).error);
});

test('vision readiness controls image analysis independently of writing and persists its model setting', async () => {
  const settings = { base_url: 'http://127.0.0.1:11434', model: 'h3-script-hermes4:14b-q5_k_m', vision_model: 'qwen3.8:27b', temperature: 0.8, num_ctx: 8192, num_predict: 4096 };
  const { ui, elements, calls, actions } = fixture(async url => url.endsWith('/settings') ? response(settings) : response({ ready: true, vision_ready: true, character_references_supported: true, settings, models: [{ name: settings.model }, { name: settings.vision_model }] }));
  ui.writeProject(project({ character_references: [reference()] }));
  await ui.refreshEngine();
  assert.equal(elements.get('scriptVisionModel').value, settings.vision_model);
  assert.equal(ui.state.referenceControls.get(reference().id).analyze.disabled, false);
  await ui.saveSettings({ preventDefault() {} });
  const saved = calls.find(call => call.url.endsWith('/settings'));
  assert.equal(JSON.parse(saved.options.body).vision_model, settings.vision_model);
  assert.equal(JSON.parse(saved.options.body).model, settings.model);
  ui.state.visionReady = false; ui.renderPreview();
  assert.equal(ui.state.referenceControls.get(reference().id).analyze.disabled, true);
  assert.ok(actions.every(action => !action.disabled));
});

test('reference conflicts preserve every local mapping and expose distinct current server appearances', async () => {
  const serverReference = reference({ appearance: '另一分頁設定的外觀', image_asset_id: 'e'.repeat(32), image_name: 'server.png' });
  const { ui, stored } = fixture(async (url, options) => options.method === 'PUT' ? response({ error: 'version conflict' }, 409) : response(project({ revision: 9, character_references: [serverReference] })));
  ui.writeProject(project({ character_references: [reference()] }));
  const controls = ui.state.referenceControls.get(reference().id); controls.name.value = '本分頁的金猴爺'; await controls.name.dispatch('input');
  assert.equal(await ui.saveProject(), false);
  assert.equal(ui.readProject().character_references[0].name, controls.name.value);
  assert.equal(ui.readProject().character_references[0].image_asset_id, reference().image_asset_id);
  assert.deepEqual(ui.state.conflict.server.character_references, [serverReference]);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.character_references[0].name, controls.name.value);
});

test('appearance jobs cannot be submitted for unnamed or imageless cards and stale results stay copyable after removal', async () => {
  for (const incomplete of [reference({ name: '' }), reference({ image_asset_id: '', image_name: '' })]) {
    const { ui, calls } = fixture(); ui.writeProject(project({ character_references: [incomplete] })); ui.state.visionReady = true;
    await ui.generate('appearance', incomplete.id); assert.equal(calls.length, 0);
  }
  const { ui, elements } = fixture(); ui.writeProject(project({ character_references: [reference()] })); ui.state.job = appearanceJob();
  ui.removeReference(reference().id); ui.renderPreview(); ui.applyResult();
  assert.equal(ui.readProject().character_references.length, 0);
  assert.equal(elements.get('scriptPreviewContent').textContent, ui.state.job.content);
  assert.equal(elements.get('copyScriptResult').disabled, false);
  assert.match(elements.get('scriptMessage').textContent, /請複製結果/);
});

test('older backends cannot silently discard character cards: saves stop and keep a browser draft', async () => {
  const { ui, elements, calls, stored } = fixture(async () => response(project()), { referencesSupported: false });
  ui.writeProject(project({ character_references: [reference()] }));
  assert.equal(await ui.saveProject(), false);
  assert.equal(calls.length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().character_references)), [reference()]);
  assert.equal(ui.state.dirty, true);
  assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.character_references, [reference()]);
  assert.match(elements.get('scriptMessage').textContent, /重新啟動更新後的 Studio/);
  ui.writeProject(project()); ui.state.dirty = false;
  assert.equal(await ui.saveProject(), true);
  assert.equal(calls.length, 1);
});

test('reference persistence requires an explicit backend capability flag and keeps existing manual drafts on status changes', async () => {
  for (const flag of [undefined, false, 'true']) {
    const { ui, stored } = fixture(async () => response({ ready: true, settings: {}, character_references_supported: flag }));
    ui.writeProject(project({ character_references: [reference()] })); ui.markDirty(); await ui.refreshEngine();
    assert.equal(ui.state.referencesSupported, false);
    assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.character_references, [reference()]);
  }
  const { ui } = fixture(async () => response({ ready: true, settings: {}, character_references_supported: true }));
  await ui.refreshEngine(); assert.equal(ui.state.referencesSupported, true);
});

function historyJob(overrides = {}) { return { id: 'history-job', project_id: 'project-a', project_title: '雨停之前', action: 'screenplay', status: 'completed', content: '15秒完整生成稿', model: 'local-model', created_at: '2026-10-02T01:00:00Z', ...overrides }; }
function historyData(items, overrides = {}) { return { items, total: items.length, has_more: false, active_items: items.filter(job => ['queued', 'running', 'cancelling'].includes(job.status)), history_persistent: true, persistence_error: null, ...overrides }; }

test('history defaults to every project and displays all records without a 50-record cutoff', async () => {
  const jobs = Array.from({ length: 75 }, (_, index) => historyJob({ id: `job-${index}`, project_id: index % 2 ? 'project-b' : 'project-a', project_title: index % 2 ? '另一部劇本' : '雨停之前' }));
  const { ui, elements, calls } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData(jobs)) : response(jobs.find(job => url.endsWith(job.id))));
  ui.writeProject(project()); await ui.loadJobs();
  assert.equal(ui.state.historyScope, 'all');
  assert.equal(elements.get('scriptJobHistory').children.length, 75);
  assert.equal(elements.get('scriptHistoryCount').textContent, '全部劇本 · 共 75 筆');
  assert.match(elements.get('scriptHistoryStorage').textContent, /已永久保存在 Studio 主機/);
  assert.equal(calls[0].url, '/api/scripts/jobs');
  assert.equal(elements.get('scriptJobHistory').children[1].children[0].children[0].children[1].textContent.startsWith('另一部劇本'), true);
});

test('a new unsaved project still sees prior project history and current scope clearly explains other records', async () => {
  const old = historyJob({ project_id: 'old-project', project_title: '舊劇本' });
  const { ui, elements } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([old])) : response(old));
  await ui.loadJobs();
  assert.equal(ui.state.project.id, null);
  assert.equal(elements.get('scriptJobHistory').children[0].dataset.jobId, old.id);
  assert.equal(elements.get('scriptPreviewContent').textContent, old.content);
  assert.equal(elements.get('applyScriptResult').disabled, true);
  await ui.changeHistoryScope('current');
  assert.equal(ui.state.jobs.length, 1);
  assert.match(elements.get('scriptJobHistory').children[0].textContent, /全部劇本.*其他劇本/);
  assert.equal(ui.state.job, null);
  await ui.changeHistoryScope('all');
  assert.equal(ui.state.selectedJobId, old.id);
  assert.equal(elements.get('copyScriptResult').disabled, false);
});

test('other-project history is previewable and copyable while manuscripts and storyboard imports remain protected', async () => {
  for (const action of ['screenplay', 'storyboard', 'appearance']) {
    const other = historyJob({ project_id: 'project-b', project_title: '另一部劇本', action });
    const { ui, elements } = fixture(async () => response(other));
    ui.writeProject(project()); ui.state.jobs = [other]; ui.state.projects = [project(), project({ id: 'project-b' })];
    await ui.selectJob(other.id);
    assert.equal(elements.get('scriptPreviewContent').textContent, other.content);
    assert.equal(elements.get('copyScriptResult').disabled, false);
    assert.equal(elements.get('applyScriptResult').disabled, true);
    assert.equal(elements.get('importScriptStoryboard').disabled, true);
    assert.equal(elements.get('openScriptJobProject').disabled, false);
    assert.equal(elements.get('openScriptJobProject').classes.has('hidden'), false);
    ui.applyResult();
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
    assert.equal(ui.state.dirty, false);
  }
});

test('opening the original project preserves the chosen historical result and enables its appropriate application', async () => {
  const chosen = historyJob({ id: 'older-result', project_id: 'project-b', project_title: '原劇本', content: '選擇的舊版本' });
  const newer = historyJob({ id: 'newer-result', project_id: 'project-b', content: '較新版本' });
  const { ui, elements } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([newer, chosen])) : url.includes('/projects/') ? response(project({ id: 'project-b', content: '原劇本的手寫稿' })) : response(url.endsWith(chosen.id) ? chosen : newer));
  ui.writeProject(project()); ui.state.projects = [project(), project({ id: 'project-b' })]; ui.state.jobs = [newer, chosen]; ui.state.job = chosen; ui.state.selectedJobId = chosen.id;
  await ui.openJobProject();
  assert.equal(ui.state.project.id, 'project-b');
  assert.equal(ui.state.selectedJobId, chosen.id);
  assert.equal(elements.get('scriptPreviewContent').textContent, chosen.content);
  assert.equal(elements.get('scriptContent').value, '原劇本的手寫稿');
  assert.equal(elements.get('applyScriptResult').disabled, false);
});

test('single-record deletion requires confirmation, never deletes active work, and only removes a confirmed response', async () => {
  for (const status of ['queued', 'running', 'cancelling', 'unknown']) {
    assert.equal(scriptCanDeleteJob(historyJob({ status })), false);
    const { ui, calls } = fixture(); ui.state.jobs = [historyJob({ status })]; await ui.deleteJob('history-job'); assert.equal(calls.length, 0);
  }
  const declined = fixture(async () => response({ deleted: true, id: 'history-job' }), { confirm: () => false });
  declined.ui.state.jobs = [historyJob()]; await declined.ui.deleteJob('history-job');
  assert.equal(declined.calls.length, 0); assert.equal(declined.ui.state.jobs.length, 1);
  const { ui, elements, calls, stored } = fixture(async (url, options) => options.method === 'DELETE' ? response({ deleted: true, id: 'history-job' }) : response(historyData([])));
  ui.writeProject(project()); ui.markDirty(); ui.state.jobs = [historyJob()]; ui.state.job = historyJob(); ui.state.selectedJobId = 'history-job';
  const draft = stored.get('h3studio.scripts.draft.this-tab'); await ui.deleteJob('history-job');
  assert.equal(calls[0].url, '/api/scripts/jobs/history-job'); assert.equal(calls[0].options.method, 'DELETE');
  assert.equal(ui.state.jobs.length, 0); assert.equal(ui.state.job, null); assert.equal(ui.state.selectedJobId, null);
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(stored.get('h3studio.scripts.draft.this-tab'), draft);
  assert.match(elements.get('scriptMessage').textContent, /已永久刪除.*劇本.*短片仍保留/);
});

test('deletion failures and a backend finishing conflict keep history available without local tombstones', async () => {
  for (const result of [response({ error: '工作仍在完成收尾，請稍後刪除' }, 409), response({}), response({ deleted: true, id: 'different-job' })]) {
    const { ui, elements } = fixture(async () => result); ui.state.jobs = [historyJob()]; ui.state.job = historyJob(); ui.state.selectedJobId = 'history-job';
    await ui.deleteJob('history-job');
    assert.equal(ui.state.jobs.length, 1); assert.equal(ui.state.job.content, historyJob().content);
    assert.equal(ui.state.deletedJobIds.has('history-job'), false); assert.equal(ui.state.deletingJobIds.size, 0);
    assert.equal(elements.get('scriptJobHistory').children.length, 1);
  }
});

test('a stale polling response cannot resurrect a deleted record or its preview', async () => {
  const stale = deferred(), removed = historyJob(), remaining = historyJob({ id: 'remaining-job', content: '仍保存的內容' });
  const { ui, elements } = fixture(async (url, options) => options.method === 'DELETE' ? response({ deleted: true, id: removed.id }) : url === '/api/scripts/jobs' ? stale.promise : response(remaining));
  ui.writeProject(project()); ui.state.jobs = [removed, remaining]; ui.state.job = removed; ui.state.selectedJobId = removed.id;
  const polling = ui.loadJobs(); await ui.deleteJob(removed.id);
  stale.resolve(response(historyData([removed, remaining]))); await polling;
  assert.deepEqual(Array.from(ui.state.jobs, job => job.id), [remaining.id]);
  assert.equal(ui.state.selectedJobId, remaining.id);
  assert.equal(elements.get('scriptPreviewContent').textContent, remaining.content);
  assert.equal(elements.get('scriptJobHistory').children.length, 1);
  assert.equal(elements.get('scriptJobHistory').children[0].dataset.jobId, remaining.id);
});

test('history read failures and malformed responses retain every previously loaded record', async () => {
  for (const data of [response({ error: '無法讀取保存記錄' }, 500), response({ items: null }), response({})]) {
    const { ui, elements } = fixture(async () => data); ui.state.jobs = [historyJob()]; ui.state.job = historyJob(); ui.state.selectedJobId = 'history-job';
    await ui.loadJobs();
    assert.equal(ui.state.jobs.length, 1); assert.equal(ui.state.job.content, historyJob().content);
    assert.equal(elements.get('scriptJobHistory').children.length, 1);
    assert.equal(ui.state.historyStorage, 'unavailable');
    assert.match(elements.get('scriptHistoryStorage').textContent, /已保留畫面/);
  }
});

test('history persistence errors stay visible while recovered records remain readable', async () => {
  const job = historyJob();
  const { ui, elements } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([job], { persistence_error: '磁碟寫入失敗，生成文字仍保留' })) : response(job));
  ui.writeProject(project()); await ui.loadJobs();
  assert.equal(ui.state.historyStorage, 'error');
  assert.match(elements.get('scriptHistoryStorage').textContent, /磁碟寫入失敗/);
  assert.equal(elements.get('scriptHistoryStorage').classes.has('error'), true);
  assert.equal(elements.get('scriptPreviewContent').textContent, job.content);
  assert.equal(elements.get('copyScriptResult').disabled, false);
});

test('unpaginated active job metadata blocks duplicate generation even when the visible history list lacks it', async () => {
  const done = historyJob(), active = historyJob({ id: 'active-job', status: 'running' });
  const { ui, calls, actions } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([done], { active_items: [active] })) : response(done));
  ui.writeProject(project()); ui.state.ready = true; await ui.loadJobs();
  assert.ok(actions.every(button => button.disabled));
  const previousCalls = calls.length; await ui.generate('screenplay'); assert.equal(calls.length, previousCalls);
});

test('a historical preview request completed after deletion cannot restore the deleted result', async () => {
  const detail = deferred();
  const { ui } = fixture(async (url, options) => options.method === 'DELETE' ? response({ deleted: true, id: 'history-job' }) : url === '/api/scripts/jobs' ? response(historyData([])) : detail.promise);
  ui.state.jobs = [historyJob()];
  const selecting = ui.selectJob('history-job'); await ui.deleteJob('history-job');
  detail.resolve(response(historyJob())); await selecting;
  assert.equal(ui.state.selectedJobId, null); assert.equal(ui.state.job, null);
});

function pipelineRecord(overrides = {}) { return { id: 'c'.repeat(32), project_id: 'project-a', title: '雨停之前', status: 'running', stage: 'screenplay', progress: 3, error: null, source_job_id: null, screenplay_job_id: null, storyboard_job_id: null, shortfilm_id: null, video_url: null, editor_project_id: null, export_id: null, video_job_ids: [], created_at: '2026-10-02T02:00:00Z', updated_at: '2026-10-02T02:00:00Z', ...overrides }; }
function prepareVideo(ui, overrides = {}) { ui.writeProject(project({ duration: 15, character_references: [reference()], ...overrides })); ui.state.ready = true; ui.state.videoPipelinesSupported = true; ui.state.videoReady = true; ui.renderPreview(); }

test('automatic video stays off by default and ordinary writing requests never start a pipeline', async () => {
  const { ui, elements, calls } = fixture(async () => response(historyJob({ status: 'queued' })));
  ui.writeProject(project()); ui.state.ready = true;
  assert.equal(ui.state.autoVideo, false); assert.equal(elements.get('scriptAutoVideo').checked, false);
  await ui.generate('screenplay');
  assert.equal(JSON.parse(calls[0].options.body).auto_video, undefined);
  assert.ok(calls.every(call => !call.url.includes('video-pipelines')));
});

test('Ref2VA preflight accepts object or background stories and requires every configured character image and appearance', () => {
  assert.match(scriptVideoReferencesError(project()), /至少一張參考圖/);
  assert.equal(scriptVideoReferencesError(project({ character_references: [reference({ type: 'background' })] })), '');
  assert.equal(scriptVideoReferencesError(project({ character_references: [reference({ type: 'object' })] })), '');
  const noImage = reference({ id: 'd'.repeat(32), name: '神祕女子', image_asset_id: '' });
  assert.match(scriptVideoReferencesError(project({ character_references: [reference(), noImage] })), /每個角色/);
  assert.match(scriptVideoReferencesError(project({ character_references: [reference({ appearance: '' })] })), /外觀描述/);
  assert.match(scriptVideoReferencesError(project({ character_references: [reference({ appearance: '長'.repeat(1201) })] })), /1200/);
});

test('one-click video uses saved current manuscript by default and preserves it in the editor', async () => {
  const pipeline = pipelineRecord(); const { ui, elements, calls } = fixture(async () => response(pipeline)); prepareVideo(ui);
  await ui.startVideoPipeline();
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/scripts/video-pipelines');
  assert.deepEqual(JSON.parse(calls[0].options.body), { project_id: 'project-a' });
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(ui.state.pipeline.id, pipeline.id); assert.equal(ui.state.dirty, false);
});

test('selected completed screenplay or rewrite can source video without replacing a newer manuscript', async () => {
  for (const action of ['screenplay', 'rewrite']) {
    const pipeline = pipelineRecord(); const { ui, elements, calls } = fixture(async () => response(pipeline)); prepareVideo(ui);
    ui.state.job = historyJob({ action, content: '先前生成的完整劇本' }); ui.renderPreview(); elements.get('scriptVideoSource').value = 'preview';
    await ui.startVideoPipeline();
    assert.equal(JSON.parse(calls[0].options.body).source_job_id, 'history-job');
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  }
});

test('video source selection refuses other projects, incomplete results and storyboards', async () => {
  for (const change of [{ project_id: 'project-b' }, { status: 'running' }, { truncated: true }, { action: 'storyboard' }]) {
    const { ui, elements, calls } = fixture(); prepareVideo(ui); ui.state.job = historyJob(change); elements.get('scriptVideoSource').value = 'preview';
    assert.equal(ui.selectedVideoSourceJob(), null); await ui.startVideoPipeline(); assert.equal(calls.length, 0);
    assert.ok(elements.get('scriptMessage').textContent.length > 0);
  }
});

test('missing images, old backend and unready engines stop video before any request without altering drafts', async () => {
  for (const scenario of ['image', 'backend', 'engine']) {
    const { ui, elements, calls } = fixture(); prepareVideo(ui);
    if (scenario === 'image') ui.writeProject(project());
    if (scenario === 'backend') ui.state.videoPipelinesSupported = false;
    if (scenario === 'engine') { ui.state.videoReady = false; ui.state.videoError = '影片模型尚未安裝'; }
    await ui.startVideoPipeline(); assert.equal(calls.length, 0); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
    assert.ok(elements.get('scriptMessage').textContent.length > 0);
  }
});

test('draft saving precedes pipeline creation and in-transit edits cancel creation rather than discard new text', async () => {
  const pending = deferred(); const { ui, elements, calls } = fixture(async () => pending.promise); prepareVideo(ui); ui.markDirty();
  const starting = ui.startVideoPipeline();
  assert.equal(calls[0].options.method, 'PUT');
  elements.get('scriptContent').value = '儲存途中新增的細節'; ui.markDirty();
  pending.resolve(response(project({ revision: 4, duration: 15, character_references: [reference()] }))); await starting;
  assert.equal(calls.length, 1); assert.equal(ui.state.pipeline, null);
  assert.equal(elements.get('scriptContent').value, '儲存途中新增的細節'); assert.equal(ui.state.dirty, true);
});

test('automatic writing requests attach auto_video only to screenplay and rewrite and follow their persisted pipeline', async () => {
  for (const action of ['screenplay', 'rewrite', 'outline', 'storyboard']) {
    const pipeline = pipelineRecord();
    const { ui, elements, calls } = fixture(async url => url.startsWith('/api/scripts/video-pipelines/') ? response(pipeline) : response(historyJob({ action, status: 'queued', video_pipeline_id: ['screenplay', 'rewrite'].includes(action) ? pipeline.id : undefined })));
    prepareVideo(ui); ui.state.autoVideo = true; await ui.generate(action);
    const request = JSON.parse(calls[0].options.body);
    assert.equal(request.auto_video, ['screenplay', 'rewrite'].includes(action) ? true : undefined);
    if (['screenplay', 'rewrite'].includes(action)) assert.equal(ui.state.selectedPipelineId, pipeline.id);
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  }
});

test('active pipeline prevents duplicate one-click and automatic video work while keeping ordinary editing intact', async () => {
  const { ui, elements, calls } = fixture(); prepareVideo(ui); ui.state.pipelines = [pipelineRecord()]; ui.state.autoVideo = true;
  await ui.startVideoPipeline(); await ui.generate('screenplay');
  assert.equal(calls.length, 0); assert.equal(elements.get('scriptContent').disabled, false);
});

test('pipeline stages, progress, failed details and original-settings resume controls display safe literal text', () => {
  const { ui, elements } = fixture(); prepareVideo(ui); const pipeline = pipelineRecord({ status: 'failed', stage: 'render', progress: 67, error: '<img src=x>影片失敗' });
  ui.state.pipelines = [pipeline]; ui.state.pipeline = pipeline; ui.state.selectedPipelineId = pipeline.id; ui.renderPipelines();
  assert.equal(elements.get('scriptVideoPipelineStage').textContent, '雨停之前 · 生成影片');
  assert.equal(elements.get('scriptVideoPipelineProgress').value, 67);
  assert.equal(elements.get('scriptVideoPipelineDetail').textContent, pipeline.error);
  assert.equal(elements.get('resumeScriptVideoPipeline').classes.has('hidden'), false);
  assert.equal(elements.get('scriptVideoResumeHint').classes.has('hidden'), false);
  assert.match(html, /接續使用原設定與原參考圖/);
});

test('only completed local pipeline results become playable and polling does not restart playback', () => {
  const url = `/api/editor/exports/${'d'.repeat(32)}/file`;
  assert.equal(scriptPipelineVideoUrl(pipelineRecord({ status: 'completed', video_url: url })), url);
  for (const video_url of ['https://other.example/video.mp4', 'data:text/plain,unsafe', '/api/assets/../../secret', '/static/video.mp4']) assert.equal(scriptPipelineVideoUrl(pipelineRecord({ status: 'completed', video_url })), '');
  assert.equal(scriptPipelineVideoUrl(pipelineRecord({ video_url: url })), '');
  const { ui, elements } = fixture(); prepareVideo(ui); ui.state.pipeline = pipelineRecord({ status: 'completed', stage: 'completed', progress: 100, video_url: url, shortfilm_id: 'shortfilm-id', editor_project_id: 'editor-id' });
  let sourceWrites = 0; Object.defineProperty(elements.get('scriptPipelineVideo'), 'src', { set(value) { sourceWrites++; this.currentSource = value; }, get() { return `http://localhost${this.currentSource || ''}`; } });
  ui.renderPipelines(); ui.renderPipelines();
  assert.equal(sourceWrites, 1); assert.equal(elements.get('scriptPipelineVideoResult').classes.has('hidden'), false);
  assert.equal(elements.get('downloadScriptPipelineVideo').href, `${url}?download=1`);
  assert.equal(elements.get('openScriptPipelineShortfilm').href, '/?workspace=shortfilm&project=shortfilm-id');
  assert.equal(elements.get('openScriptPipelineEditor').href, '/editor?project=editor-id');
});

test('pipeline cancellation preserves script history and stale detail cannot make the cancelled workflow active again', async () => {
  const late = deferred(), active = pipelineRecord(), cancelled = pipelineRecord({ status: 'cancelled' });
  const { ui, calls } = fixture(async url => url.endsWith('/cancel') ? response(cancelled) : late.promise); prepareVideo(ui);
  ui.state.pipelines = [active]; ui.state.pipeline = active; ui.state.selectedPipelineId = active.id; ui.state.jobs = [historyJob()];
  const selecting = ui.selectPipeline(active.id); await ui.cancelPipeline();
  late.resolve(response(active)); await selecting;
  assert.equal(ui.state.pipeline.status, 'cancelled'); assert.equal(ui.state.jobs.length, 1);
  assert.equal(calls[1].url, `/api/scripts/video-pipelines/${active.id}/cancel`);
});

test('resume uses the original pipeline without saving changed manuscript or submitting replacement settings', async () => {
  const previous = pipelineRecord({ status: 'interrupted', stage: 'render' }); const { ui, elements, calls } = fixture(async () => response(pipelineRecord({ stage: 'render' }))); prepareVideo(ui);
  elements.get('scriptContent').value = '新的原稿仍未儲存'; ui.markDirty(); ui.state.pipeline = previous; ui.state.selectedPipelineId = previous.id;
  await ui.resumePipeline();
  assert.equal(calls.length, 1); assert.equal(calls[0].url, `/api/scripts/video-pipelines/${previous.id}/resume`); assert.equal(calls[0].options.body, '{}');
  assert.equal(elements.get('scriptContent').value, '新的原稿仍未儲存'); assert.equal(ui.state.dirty, true);
  assert.match(elements.get('scriptMessage').textContent, /原設定.*新的流程/);
});

test('pipeline history preserves every workflow and late polling cannot overwrite a switched project', async () => {
  const oldPoll = deferred(); const { ui, elements } = fixture(async url => url === '/api/scripts/video-pipelines' ? oldPoll.promise : url.includes('/projects/') ? response(project({ id: 'project-b', content: '第二份原稿' })) : response(historyData([])));
  prepareVideo(ui); const polling = ui.loadPipelines(); await ui.switchProject('project-b');
  oldPoll.resolve(response({ items: [pipelineRecord()] })); await polling;
  assert.equal(ui.state.project.id, 'project-b'); assert.equal(ui.state.pipeline, null); assert.equal(elements.get('scriptContent').value, '第二份原稿');
  ui.state.pipelines = Array.from({ length: 65 }, (_, index) => pipelineRecord({ id: index.toString(16).padStart(32, '0') })); ui.renderPipelines();
  assert.equal(elements.get('scriptVideoPipelineHistory').children.length, 65);
});

test('pipeline state failures retain loaded workflows and progress remains bounded', async () => {
  const { ui, elements } = fixture(async () => response({ error: '主機暫時無法讀取流程' }, 500)); prepareVideo(ui); ui.state.pipelines = [pipelineRecord()]; ui.state.pipeline = pipelineRecord();
  await ui.loadPipelines(); assert.equal(ui.state.pipelines.length, 1); assert.equal(ui.state.pipeline.title, '雨停之前');
  assert.match(elements.get('scriptVideoPipelineDetail').textContent, /暫時無法更新/);
  assert.equal(scriptPipelinePresentation(pipelineRecord({ progress: 500 })).progress, 100);
  assert.equal(scriptPipelinePresentation(pipelineRecord({ progress: -50 })).progress, 0);
});

test('saving references waits for the initial shared capability check and sends the latest edits exactly once', async () => {
  const pendingStatus = deferred();
  const { ui, calls } = fixture(async (url, options) => url === '/api/scripts/status' ? pendingStatus.promise : response(project({ ...JSON.parse(options.body), revision: 4 })), { referencesSupported: false, referencesSupportKnown: false });
  ui.writeProject(project({ character_references: [reference()] })); ui.markDirty();
  const checking = ui.refreshEngine(), saving = ui.saveProject();
  assert.equal(ui.state.referencesSaveWaiting, true); assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/scripts/status');
  const controls = ui.state.referenceControls.get(reference().id); controls.name.value = '等待檢查時修改的角色'; await controls.name.dispatch('input');
  assert.equal(await ui.saveProject(), false);
  pendingStatus.resolve(response({ ready: false, character_references_supported: true, models: [], settings: {} }));
  await checking; assert.equal(await saving, true);
  assert.equal(calls.length, 2); assert.equal(calls[1].options.method, 'PUT');
  assert.equal(JSON.parse(calls[1].options.body).character_references[0].name, controls.name.value);
  assert.equal(ui.state.referencesSupportKnown, true); assert.equal(ui.state.referencesSupported, true);
});

test('an unknown capability connection failure preserves the draft and never claims the host is outdated', async () => {
  const { ui, elements, calls, stored } = fixture(async () => { throw Error('offline'); }, { referencesSupported: false, referencesSupportKnown: false });
  ui.writeProject(project({ character_references: [reference()] }));
  assert.equal(await ui.saveProject(), false);
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/scripts/status');
  assert.equal(ui.state.referencesSupportKnown, false);
  assert.match(elements.get('scriptMessage').textContent, /暫時無法確認/);
  assert.doesNotMatch(elements.get('scriptMessage').textContent, /尚未提供|重新啟動更新後/);
  assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.character_references, [reference()]);
});

test('known reference support survives connection and malformed status failures independently of model readiness', async () => {
  for (const status of [response({ error: '暫時離線' }, 503), response({}), response({ ready: true, character_references_supported: true, models: {} })]) {
    const { ui } = fixture(async () => status); await ui.refreshEngine();
    assert.equal(ui.state.referencesSupported, true); assert.equal(ui.state.referencesSupportKnown, true);
    assert.equal(ui.state.ready, false);
  }
});

test('successful capability checks clear only their own refusal banner and retain drafts and unrelated conflict messages', async () => {
  const { ui, elements, stored } = fixture(async () => response({ ready: true, character_references_supported: true, models: [], settings: {} }), { referencesSupported: false });
  ui.writeProject(project({ character_references: [reference()] }));
  assert.equal(await ui.saveProject(), false); assert.equal(ui.state.messageKind, 'references-support');
  const draft = stored.get('h3studio.scripts.draft.this-tab'); await ui.refreshEngine();
  assert.equal(elements.get('scriptMessage').textContent, ''); assert.equal(elements.get('scriptMessage').classes.has('hidden'), true);
  assert.equal(stored.get('h3studio.scripts.draft.this-tab'), draft); assert.equal(ui.state.dirty, true);
  ui.state.conflict = { id: 'project-a', server: project({ revision: 9 }) }; ui.message('版本衝突：請檢查主機內容。', true);
  await ui.refreshEngine();
  assert.equal(elements.get('scriptMessage').textContent, '版本衝突：請檢查主機內容。');
  assert.equal(ui.state.conflict.server.revision, 9);
});

test('confirmed legacy status still refuses reference saves without sending card data to the older endpoint', async () => {
  const { ui, elements, calls } = fixture(async () => response({ ready: true, models: [], settings: {} }), { referencesSupported: false, referencesSupportKnown: false });
  ui.writeProject(project({ character_references: [reference()] }));
  assert.equal(await ui.saveProject(), false); assert.equal(ui.state.referencesSupportKnown, true);
  assert.equal(calls.length, 1); assert.match(elements.get('scriptMessage').textContent, /尚未提供角色外觀對照/);
});

test('a save response that ignores or reverts reference cards cannot replace the local editor or discard its draft', async () => {
  for (const cards of [undefined, [], [reference({ appearance: '忽略新修改的舊描述' })]]) {
    const { ui, stored } = fixture(async () => response(project({ revision: 4, character_references: cards })));
    ui.writeProject(project({ character_references: [reference()] }));
    assert.equal(await ui.saveProject(), false);
    assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().character_references)), [reference()]);
    assert.equal(ui.state.project.revision, 3); assert.equal(ui.state.referencesSupportKnown, false);
    assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.character_references, [reference()]);
  }
});

test('ordinary projects can save during an initial unknown capability check without waiting on model health', async () => {
  const { ui, calls } = fixture(async (url, options) => response(project({ ...JSON.parse(options.body), revision: 4 })), { referencesSupported: false, referencesSupportKnown: false });
  ui.writeProject(project()); assert.equal(await ui.saveProject(), true);
  assert.equal(calls.length, 1); assert.equal(calls[0].options.method, 'PUT');
});

function skillStatus(overrides = {}) { return { ready: true, character_references_supported: true, writing_skills_supported: true, models: [], settings: {}, ...overrides }; }

test('writing skills are explicit new defaults and independent project fields, without backfilling legacy or malformed drafts', () => {
  assert.equal(createNewScriptProject().writing_skill, 'cinematic-story');
  for (const value of [undefined, null, {}, ['suspense-horror'], 3]) assert.equal(normalizeScriptProject({ writing_skill: value }).writing_skill, '');
  assert.equal(normalizeScriptProject({ writing_skill: ' suspense-horror ' }).writing_skill, 'suspense-horror');
  const original = project(), selected = project({ writing_skill: 'suspense-horror' });
  assert.notEqual(scriptProjectSignature(original), scriptProjectSignature(selected));
  assert.equal(scriptDraftCandidates([{ writer: 'other', savedAt: 1, project: selected }], original, original).length, 1);
  const changed = scriptSaveOutcome(project({ ...selected, revision: 4 }), project({ writing_skill: 'comedy-reaction' }), selected);
  assert.equal(changed.project.writing_skill, 'comedy-reaction'); assert.equal(changed.dirty, true);
  assert.equal(scriptSaveOutcome(selected, selected, selected).project.writing_skill, 'suspense-horror');
});

test('skill catalogs display all methods, recommendations and licensed links as safe literal text', async () => {
  const catalog = skillCatalog(); catalog[0].name = '<img src=x onerror=alert(1)>'; catalog[0].description = '<b>自由描述</b>';
  catalog[0].guide = ['<script>方法</script>', '保持空間關係'];
  catalog[0].sources.push({ name: '<img>不安全來源', url: 'javascript:alert(1)', license: '<b>MIT</b>', license_url: 'data:text/html,unsafe' });
  const { ui, elements } = fixture(async url => response(url === '/api/scripts/skills' ? { items: catalog, version: '20261003' } : skillStatus()), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
  await ui.refreshEngine();
  const select = elements.get('scriptWritingSkill');
  assert.equal(select.options.length, 12); assert.equal(select.options[0].value, ''); assert.equal(select.options[0].disabled, false);
  assert.equal(select.value, 'cinematic-story'); assert.ok(select.options.slice(1).every(option => option.disabled === false));
  assert.equal(elements.get('scriptSkillSummary').textContent, '<b>自由描述</b>');
  assert.equal(elements.get('scriptSkillGuide').children[0].textContent, '<script>方法</script>');
  const sources = elements.get('scriptSkillSources').children;
  assert.equal(sources[0].children[0].tagName, 'a'); assert.equal(sources[0].children[0].href, 'https://example.com/skill');
  assert.equal(sources[0].children[0].rel, 'noopener noreferrer'); assert.equal(sources[0].children[1].textContent, ' · MIT');
  assert.equal(sources[0].children[1].href, 'https://example.com/license');
  assert.equal(sources[1].children[0].tagName, 'span'); assert.equal(sources[1].children[0].textContent, '<img>不安全來源');
  assert.equal(sources[1].children[1].tagName, 'span');
  for (const url of ['javascript:alert(1)', 'file:///C:/secret', 'data:text/html,x', 'https://user:pass@example.com/', '/relative']) assert.equal(scriptSkillSourceUrl(url), '');
  assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test('genre and idea recommendations require an explicit click and never replace custom creative fields or references', async () => {
  const { ui, elements } = fixture(); ui.bindEvents();
  const original = project({ genre: '劇情', idea: '回家途中聽見詭異腳步聲', writing_skill: 'cinematic-story', style: '自訂黑白風格', instructions: '保持我的格式與對白', character_references: [reference()] });
  ui.writeProject(original);
  assert.match(elements.get('scriptSkillRecommendation').textContent, /懸疑／恐怖/);
  assert.equal(elements.get('scriptWritingSkill').value, 'cinematic-story');
  elements.get('scriptGenre').value = '懸疑'; await elements.get('scriptGenre').dispatch('change');
  assert.equal(ui.readProject().writing_skill, 'cinematic-story');
  await elements.get('applyScriptSkillRecommendation').click();
  assert.equal(ui.readProject().writing_skill, 'suspense-horror'); assert.equal(ui.state.dirty, true);
  const current = ui.readProject();
  for (const field of ['style', 'instructions', 'content', 'characters', 'idea']) assert.equal(current[field], original[field]);
  assert.deepEqual(JSON.parse(JSON.stringify(current.character_references)), original.character_references);
  elements.get('scriptWritingSkill').value = ''; await elements.get('scriptWritingSkill').dispatch('change');
  assert.equal(ui.readProject().writing_skill, ''); assert.match(elements.get('scriptSkillSummary').textContent, /目前的故事設定/);
  for (const [idea, id] of [['搞笑誤會', 'comedy-reaction'], ['親情重逢', 'emotional-drama'], ['魔法遺跡冒險', 'fantasy-adventure'], ['高速追車', 'action-chase'], ['商品廣告', 'product-commercial'], ['產品操作教學', 'product-demo'], ['科普解說', 'educational-explainer'], ['旅行生活紀錄', 'documentary-lifestyle'], ['遊戲角色預告', 'game-trailer']]) assert.equal(scriptRecommendedWritingSkill({ idea }, skillCatalog()).id, id);
});

test('skill changes during a save preserve the newer choice and draft, while the next save commits it', async () => {
  const pending = deferred(); let writes = 0;
  const { ui, elements, calls, stored } = fixture(async (_, options) => ++writes === 1 ? pending.promise : response(project({ ...JSON.parse(options.body), revision: 5 })));
  ui.bindEvents(); ui.writeProject(project({ writing_skill: 'suspense-horror', character_references: [reference()] })); ui.markDirty();
  const saving = ui.saveProject();
  assert.equal(elements.get('scriptWritingSkill').disabled, false);
  elements.get('scriptWritingSkill').value = 'comedy-reaction'; await elements.get('scriptWritingSkill').dispatch('change');
  pending.resolve(response(project({ ...JSON.parse(calls[0].options.body), revision: 4 })));
  assert.equal(await saving, false); assert.equal(JSON.parse(calls[0].options.body).writing_skill, 'suspense-horror');
  assert.equal(ui.readProject().writing_skill, 'comedy-reaction'); assert.equal(ui.state.project.revision, 4); assert.equal(ui.state.dirty, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.writing_skill, 'comedy-reaction');
  assert.equal(await ui.saveProject(), true); assert.equal(JSON.parse(calls[1].options.body).writing_skill, 'comedy-reaction');
  assert.equal(ui.state.dirty, false); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
});

test('skill-only changes survive draft recovery and JSON export while legacy recovery keeps its empty choice', async () => {
  const { ui, elements, downloads } = fixture();
  ui.writeProject(project()); elements.get('scriptWritingSkill').value = 'fantasy-adventure'; ui.markDirty();
  ui.writeProject(project()); ui.state.dirty = false; ui.offerDraft();
  assert.equal(ui.state.recovery.project.writing_skill, 'fantasy-adventure'); await ui.restoreDraft();
  assert.equal(ui.readProject().writing_skill, 'fantasy-adventure'); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  elements.get('scriptExportFormat').value = 'json'; ui.exportDraft();
  assert.equal(JSON.parse(await downloads[0].text()).writing_skill, 'fantasy-adventure');
  const legacy = project(); delete legacy.writing_skill;
  ui.state.recovery = { writer: 'old-tab', savedAt: 3, project: legacy }; await ui.restoreDraft();
  assert.equal(ui.readProject().writing_skill, ''); assert.equal(elements.get('scriptWritingSkill').value, '');
});

test('initial skill capability and catalog checks share one request sequence before saving the latest draft', async () => {
  const pendingStatus = deferred(), pendingCatalog = deferred();
  const { ui, elements, calls } = fixture(async (url, options) => url === '/api/scripts/status' ? pendingStatus.promise : url === '/api/scripts/skills' ? pendingCatalog.promise : response(project({ ...JSON.parse(options.body), revision: 4 })), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
  ui.writeProject(project({ writing_skill: 'suspense-horror' })); ui.markDirty();
  const checking = ui.refreshEngine(), saving = ui.saveProject();
  assert.equal(ui.state.skillsSaveWaiting, true); assert.equal(calls.length, 1);
  assert.equal(elements.get('scriptWritingSkill').options.find(option => option.value === '').disabled, false);
  assert.equal(elements.get('scriptWritingSkill').options.find(option => option.value === 'suspense-horror').disabled, true);
  assert.equal(await ui.saveProject(), false);
  pendingStatus.resolve(response(skillStatus())); await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 2); assert.equal(calls[1].url, '/api/scripts/skills'); assert.equal(ui.state.skillsSaveWaiting, true);
  elements.get('scriptStyle').value = '等待清單時修改的風格'; ui.markDirty();
  pendingCatalog.resolve(response({ items: skillCatalog() })); await checking;
  assert.equal(await saving, true); assert.equal(calls.length, 3);
  assert.equal(JSON.parse(calls[2].options.body).writing_skill, 'suspense-horror');
  assert.equal(JSON.parse(calls[2].options.body).style, '等待清單時修改的風格');
  assert.equal(ui.state.skillsSaveWaiting, false);
});

test('late catalog responses use the current project choice and cannot restore a previous project skill', async () => {
  const pending = deferred();
  const { ui, elements } = fixture(async url => url === '/api/scripts/status' ? response(skillStatus()) : url === '/api/scripts/skills' ? pending.promise : url.includes('/projects/') ? response(project({ id: 'project-b', writing_skill: 'comedy-reaction', content: '另一份稿件' })) : response({ items: [] }), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
  ui.writeProject(project({ writing_skill: 'suspense-horror' }));
  const checking = ui.refreshEngine(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(await ui.switchProject('project-b'), true);
  pending.resolve(response({ items: skillCatalog() })); await checking;
  assert.equal(ui.state.project.id, 'project-b'); assert.equal(ui.readProject().writing_skill, 'comedy-reaction');
  assert.equal(elements.get('scriptContent').value, '另一份稿件'); assert.equal(ui.state.dirty, false);
  assert.match(elements.get('scriptSkillSummary').textContent, /comedy-reaction/);
});

test('unknown connections and confirmed old hosts retain selected skills, and empty choices can still save', async () => {
  for (const offline of [true, false]) {
    const { ui, elements, calls, stored } = fixture(async (url, options) => url === '/api/scripts/status' ? offline ? Promise.reject(Error('offline')) : response(skillStatus({ writing_skills_supported: undefined })) : response(project({ ...JSON.parse(options.body), revision: 4 })), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
    ui.writeProject(project({ writing_skill: 'suspense-horror' }));
    assert.equal(await ui.saveProject(), false); assert.equal(calls.length, 1); assert.equal(ui.readProject().writing_skill, 'suspense-horror');
    assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.writing_skill, 'suspense-horror');
    assert.match(elements.get('scriptMessage').textContent, offline ? /暫時無法確認/ : /尚未提供創作 Skill/);
    if (offline) assert.doesNotMatch(elements.get('scriptMessage').textContent, /尚未提供|重新啟動更新後/);
    assert.equal(elements.get('scriptWritingSkill').options.find(option => option.value === '').disabled, false);
    elements.get('scriptWritingSkill').value = ''; ui.markDirty();
    assert.equal(await ui.saveProject(), true); assert.equal(calls.length, 2); assert.equal(JSON.parse(calls[1].options.body).writing_skill, '');
    assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  }
});

test('catalog failures and malformed records preserve selections and block only unverified nonempty skills', async () => {
  for (const invalid of [response({}, 503), response({}), response({ items: [] }), response({ items: [{ id: 'bad', name: '' }] }), response({ items: [skillCatalog()[0], skillCatalog()[0]] })]) {
    const { ui, elements, calls } = fixture(async url => url === '/api/scripts/status' ? response(skillStatus()) : invalid, { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
    ui.writeProject(project({ writing_skill: 'suspense-horror' }));
    await ui.refreshEngine(); assert.equal(ui.state.ready, true); assert.equal(ui.state.skillsCatalogReady, false);
    assert.equal(ui.readProject().writing_skill, 'suspense-horror'); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
    assert.match(elements.get('scriptSkillStatus').textContent, /無法讀取/);
    assert.equal(await ui.saveProject(), false); assert.ok(calls.every(call => !call.options.method));
    assert.equal(ui.state.messageKind, 'skills-support');
  }
  for (const invalid of [null, [], {}, [{ id: '<script>', name: '錯誤' }]]) assert.throws(() => normalizeScriptSkills(invalid));
  assert.deepEqual(normalizeScriptSkills([{ ...skillCatalog()[0], guide: '單一步驟', recommended_for: '一般短片' }])[0].guide, ['單一步驟']);
});

test('servers that ignore or revert a selected skill cannot discard the draft or claim a successful save', async () => {
  for (const returned of [undefined, '', 'cinematic-story']) {
    const { ui, elements, stored } = fixture(async () => response(project({ writing_skill: returned, revision: 4 })));
    ui.writeProject(project({ writing_skill: 'suspense-horror' }));
    assert.equal(await ui.saveProject(), false); assert.equal(ui.readProject().writing_skill, 'suspense-horror');
    assert.equal(ui.state.project.revision, 3); assert.equal(ui.state.dirty, true); assert.equal(ui.state.skillsSupportKnown, false);
    assert.match(elements.get('scriptMessage').textContent, /未確認保存所選創作 Skill/);
    assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.writing_skill, 'suspense-horror');
  }
});

test('failed refreshes retain a known catalog and choice, and success clears only its own capability message', async () => {
  let failing = true;
  const { ui, elements } = fixture(async url => failing ? response({}, 503) : response(url === '/api/scripts/status' ? skillStatus() : { items: skillCatalog() }));
  ui.writeProject(project({ writing_skill: 'suspense-horror' })); await ui.refreshEngine();
  assert.equal(ui.state.skillsSupported, true); assert.equal(ui.state.skillsCatalogReady, true); assert.equal(ui.state.writingSkills.length, 11);
  assert.equal(ui.readProject().writing_skill, 'suspense-horror');
  failing = false; ui.message('創作 Skill 功能需重新檢查。', true, 'skills-support'); await ui.refreshEngine();
  assert.equal(elements.get('scriptMessage').textContent, '');
  ui.message('版本衝突：請保留本機修改。', true); await ui.refreshEngine();
  assert.equal(elements.get('scriptMessage').textContent, '版本衝突：請保留本機修改。');
});

test('changing a skill during generation keeps the job snapshot and uses the newer skill on regeneration', async () => {
  const pendingJob = deferred(); let jobs = 0, snapshot = project({ writing_skill: 'suspense-horror' });
  const { ui, elements, calls } = fixture(async (url, options) => {
    if (url.includes('/projects/')) { snapshot = project({ ...JSON.parse(options.body), revision: 4 }); return response(snapshot); }
    return ++jobs === 1 ? pendingJob.promise : response(historyJob({ id: 'second-job', input: { project: snapshot } }));
  });
  ui.bindEvents(); ui.writeProject(snapshot); ui.state.ready = true;
  const generating = ui.generate('screenplay');
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/scripts/jobs');
  elements.get('scriptWritingSkill').value = 'comedy-reaction'; await elements.get('scriptWritingSkill').dispatch('change');
  pendingJob.resolve(response(historyJob({ input: { project: snapshot } }))); await generating;
  assert.equal(ui.state.job.input.project.writing_skill, 'suspense-horror'); assert.equal(ui.readProject().writing_skill, 'comedy-reaction');
  assert.equal(ui.state.dirty, true); assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  await ui.generate('screenplay');
  assert.equal(calls[1].options.method, 'PUT'); assert.equal(JSON.parse(calls[1].options.body).writing_skill, 'comedy-reaction');
  assert.equal(calls[2].url, '/api/scripts/jobs'); assert.equal(ui.state.job.input.project.writing_skill, 'comedy-reaction');
});

test('clearing the new default while catalog loading is pending stays empty when the catalog arrives', async () => {
  const pending = deferred(); let firstCatalog = true;
  const { ui, elements } = fixture(async url => url === '/api/scripts/status' ? response(skillStatus()) : firstCatalog ? pending.promise : response({ items: skillCatalog() }), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
  const checking = ui.refreshEngine(); await new Promise(resolve => setImmediate(resolve));
  elements.get('scriptWritingSkill').value = ''; ui.markDirty();
  pending.resolve(response({ items: skillCatalog() })); await checking;
  assert.equal(ui.readProject().writing_skill, ''); assert.equal(elements.get('scriptWritingSkill').value, '');
  assert.equal(ui.state.dirty, true); firstCatalog = false;
  ui.writeProject(project()); await ui.refreshEngine(); assert.equal(elements.get('scriptWritingSkill').value, '');
});

test('skill conflicts preserve the current choice and show the server choice before a deliberate copy or overwrite', async () => {
  const { ui, elements } = fixture(async (url, options) => options.method === 'PUT' ? response({ error: 'version conflict' }, 409) : response(project({ revision: 8, writing_skill: 'emotional-drama' })));
  ui.bindEvents(); ui.writeProject(project({ writing_skill: 'suspense-horror' })); ui.markDirty();
  assert.equal(await ui.saveProject(), false); assert.equal(ui.readProject().writing_skill, 'suspense-horror');
  assert.equal(ui.state.conflict.server.writing_skill, 'emotional-drama');
  await elements.get('viewScriptServer').click();
  assert.match(elements.get('scriptServerVersion').textContent, /創作 Skill：情感／人物劇情/);
  assert.equal(elements.get('scriptWritingSkill').value, 'suspense-horror'); assert.equal(ui.state.dirty, true);
});

function completedVideo(overrides = {}) { return pipelineRecord({ status: 'completed', stage: 'completed', progress: 100, project_id: 'older-project', project_title: '回家之路', video_url: `/api/editor/exports/${'d'.repeat(32)}/file`, editor_project_id: 'e'.repeat(32), export_id: 'd'.repeat(32), snapshot: { title: '原始片名', duration: 15 }, ...overrides }); }

test('history video records accept only completed local results and keep safe metadata from the original project', () => {
  const original = completedVideo(); assert.equal(scriptVideoHistoryRecord(original).title, '回家之路');
  assert.equal(scriptVideoHistoryRecord({ ...original, project_title: '', title: '第二順位' }).title, '第二順位');
  assert.equal(scriptVideoHistoryRecord({ ...original, project_title: '', title: '' }).title, '原始片名');
  assert.equal(scriptVideoHistoryRecord({ ...original, project_title: '', title: '', snapshot: {} }).title, '未命名影片');
  assert.equal(scriptVideoHistoryRecord(original).editor_url, `/editor?project=${'e'.repeat(32)}`);
  for (const status of ['running', 'failed', 'queued', 'interrupted', 'cancelled']) assert.equal(scriptVideoHistoryRecord({ ...original, status }), null);
  for (const id of ['../../file', 'javascript:alert(1)', 'not-an-id', '<iframe>', null]) assert.equal(scriptVideoHistoryRecord({ ...original, id }), null);
  for (const video_url of ['https://example.com/video.mp4', '//example.com/video.mp4', 'javascript:alert(1)', 'data:video/mp4,bytes', '/api/assets/../../file', '/api/assets/invalid', `/api/editor/exports/${'d'.repeat(32)}/file#part`, `/api/editor/exports/${'d'.repeat(32)}/file?x=\\unsafe`]) assert.equal(scriptVideoHistoryRecord({ ...original, video_url, video_asset_id: 'f'.repeat(32) }), null);
  assert.equal(scriptVideoHistoryRecord({ ...original, video_url: null }).video_url, `/api/editor/exports/${'d'.repeat(32)}/file`);
  assert.equal(scriptVideoHistoryRecord({ ...original, video_url: '', video_asset_id: 'f'.repeat(32) }).video_url, `/api/assets/${'f'.repeat(32)}`);
  assert.equal(scriptVideoHistoryRecord({ ...original, video_url: `/api/jobs/${'a'.repeat(32)}/video`, editor_project_id: 'bad' }).editor_url, '');
  assert.equal(scriptVideoHistoryRecord({ ...original, video_url: '', export_id: 'bad' }), null);
  assert.equal(scriptVideoHistoryRecord({ ...original, snapshot: { duration: NaN } }).duration, null);
  const download = scriptVideoHistoryDownloadUrl(`${original.video_url}?download=0&download=2&version=old`);
  const parsed = new URL(download, 'http://local'); assert.deepEqual(parsed.searchParams.getAll('download'), ['1']); assert.equal(parsed.searchParams.get('version'), 'old');
  assert.equal(scriptVideoHistoryDownloadUrl(download), download);
  assert.equal(scriptVideoHistoryDownloadUrl('https://example.com/video.mp4'), '');
});

test('completed video cards default to every project and preview without modifying the current draft or active workflow', async () => {
  const old = completedVideo(), own = completedVideo({ id: 'a'.repeat(32), project_id: 'project-a', project_title: '目前劇本的舊版本' }), active = pipelineRecord({ id: 'b'.repeat(32), title: '夏日恐怖' });
  const { ui, elements, calls } = fixture(async () => response({ items: [active, old, own, completedVideo({ id: 'f'.repeat(32), status: 'failed' })] }));
  ui.writeProject(project({ writing_skill: 'suspense-horror', character_references: [reference()], content: '還在修改的原稿' }));
  ui.state.pipeline = active; ui.state.selectedPipelineId = active.id;
  const draft = JSON.stringify(ui.readProject()); await ui.refreshVideoHistory();
  assert.equal(ui.state.videoHistoryScope, 'all'); assert.equal(ui.state.historyVideos.length, 2); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 2);
  assert.match(elements.get('scriptVideoHistoryCount').textContent, /全部劇本 · 2 部完成影片/);
  const card = elements.get('scriptVideoHistoryCards').children.find(card => card.dataset.videoHistoryId === old.id);
  assert.equal(card.children[0].textContent, '回家之路'); assert.match(card.children[1].textContent, /2026/); assert.match(card.children[1].textContent, /目標片長 15 秒/);
  const [preview, download, editor] = card.children[2].children;
  assert.equal(download.href, `${old.video_url}?download=1`); assert.equal(editor.href, `/editor?project=${old.editor_project_id}`);
  assert.equal(editor.target, '_blank'); assert.equal(editor.rel, 'noopener noreferrer'); await preview.click();
  assert.equal(ui.state.selectedHistoryVideoId, old.id); assert.equal(elements.get('scriptHistoryVideo').src, old.video_url);
  assert.equal(elements.get('scriptHistoryVideoTitle').textContent, '回家之路'); assert.equal(elements.get('openScriptHistoryVideoEditor').href, editor.href);
  assert.equal(ui.state.selectedPipelineId, active.id); assert.equal(ui.state.pipeline, active); assert.equal(JSON.stringify(ui.readProject()), draft);
  assert.equal(ui.state.dirty, false); assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/scripts/video-pipelines'); assert.equal(calls[0].options.method, undefined);
});

test('video history scope and the top entry operate independently of generated-text scope and never mark the draft dirty', async () => {
  const { ui, elements, calls } = fixture(); ui.bindEvents(); ui.writeProject(project());
  ui.receiveVideoHistory({ items: [completedVideo(), completedVideo({ id: 'a'.repeat(32), project_id: 'project-a' })] }); ui.renderVideoHistory();
  ui.selectHistoryVideo(completedVideo().id); await ui.changeHistoryScope('current');
  assert.equal(ui.state.videoHistoryScope, 'all'); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 2);
  elements.get('scriptVideoHistoryScope').value = 'current'; await elements.get('scriptVideoHistoryScope').dispatch('change');
  assert.equal(ui.state.videoHistoryScope, 'current'); assert.equal(ui.state.historyScope, 'current'); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 1);
  elements.get('scriptVideoHistoryScope').value = 'all'; await elements.get('scriptVideoHistoryScope').dispatch('change');
  assert.equal(ui.state.historyScope, 'current'); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 2);
  await elements.get('openScriptVideoHistory').click();
  assert.equal(elements.get('scriptVideoHistory').lastScroll.block, 'start'); assert.equal(elements.get('scriptVideoHistory').focused, true);
  assert.equal(ui.state.selectedHistoryVideoId, completedVideo().id); assert.equal(ui.state.dirty, false); assert.equal(calls.length, 0);
  assert.match(html, /id="scriptHistoryVideo" controls preload="none" tabindex="0"/);
});

test('active pipeline polls and new generation replies never steal a historical preview or reset its video source', async () => {
  const active = pipelineRecord({ id: 'b'.repeat(32), title: '夏日恐怖' }), old = completedVideo(), newJob = historyJob({ status: 'queued', video_pipeline_id: active.id });
  const pending = deferred();
  const { ui, elements } = fixture(async (url, options) => url === '/api/scripts/jobs' ? options.method === 'POST' ? pending.promise : response(historyData([newJob])) : url === '/api/scripts/video-pipelines' ? response({ items: [active, { ...old, project_title: '主機更新過的標題' }] }) : url.includes('/video-pipelines/') ? response(active) : response(newJob));
  ui.writeProject(project()); ui.state.ready = true; ui.state.videoPipelinesSupported = true;
  ui.receiveVideoHistory({ items: [old] });
  const video = elements.get('scriptHistoryVideo'); let sourceWrites = 0; Object.defineProperty(video, 'src', { set(value) { sourceWrites++; this.currentSource = value; }, get() { return this.currentSource; } });
  ui.selectHistoryVideo(old.id); const originalPreview = JSON.stringify(ui.state.historyPreviewVideo);
  const generating = ui.generate('screenplay'); pending.resolve(response(newJob)); await generating;
  assert.equal(ui.state.selectedPipelineId, active.id); assert.equal(ui.state.selectedHistoryVideoId, old.id);
  await ui.loadPipelines(); await ui.loadJobs(); ui.renderPipelines(); ui.renderVideoHistory();
  assert.equal(JSON.stringify(ui.state.historyPreviewVideo), originalPreview); assert.equal(video.src, old.video_url); assert.equal(sourceWrites, 1);
  assert.equal(ui.state.pipeline.title, '夏日恐怖'); assert.equal(elements.get('scriptHistoryVideoTitle').textContent, '回家之路');
  assert.equal(elements.get('scriptContent').value, '原始編輯稿'); assert.equal(ui.state.dirty, false);
});

test('empty, active-only, malformed and failed history responses retain cached films and the selected preview', async () => {
  let data = response({ items: [completedVideo()] });
  const { ui, elements, calls } = fixture(async () => data); ui.writeProject(project());
  await ui.refreshVideoHistory(); ui.selectHistoryVideo(completedVideo().id);
  const preview = JSON.stringify(ui.state.historyPreviewVideo), draft = JSON.stringify(ui.readProject());
  for (const next of [response({ items: [] }), response([]), response({ items: [pipelineRecord()] }), response({}), response({ error: '主機離線' }, 503), null]) {
    data = next || Promise.reject(Error('offline'));
    await ui.refreshVideoHistory();
    assert.equal(ui.state.historyVideos.length, 1); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 1);
    assert.equal(JSON.stringify(ui.state.historyPreviewVideo), preview); assert.equal(JSON.stringify(ui.readProject()), draft);
    assert.equal(elements.get('scriptHistoryVideoPreview').classes.has('hidden'), false); assert.equal(ui.state.dirty, false);
    assert.match(elements.get('scriptVideoHistoryStatus').textContent, /已保留|暫時無法更新/);
  }
  assert.ok(calls.every(call => call.url === '/api/scripts/video-pipelines' && !call.options.method));
  const fresh = fixture(async () => response({ items: [] })); await fresh.ui.refreshVideoHistory();
  assert.equal(fresh.ui.state.historyVideos.length, 0); assert.match(fresh.elements.get('scriptVideoHistoryCards').children[0].textContent, /尚無可播放的完成影片/);
});

test('legacy arrays and incomplete future pages accumulate all history without a cutoff or overwriting the preview', async () => {
  const many = Array.from({ length: 66 }, (_, index) => completedVideo({ id: index.toString(16).padStart(32, '0'), project_title: `影片 ${index}` }));
  let data = many; const { ui, elements } = fixture(async () => response(data));
  await ui.refreshVideoHistory(); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 66);
  ui.selectHistoryVideo(many[0].id);
  data = { items: [completedVideo({ id: 'a'.repeat(32), project_title: '分頁的新片' })], total: 100, has_more: true };
  await ui.refreshVideoHistory();
  assert.equal(ui.state.historyVideos.length, 67); assert.match(elements.get('scriptVideoHistoryStatus').textContent, /尚未回傳完整流程清單/);
  assert.equal(ui.state.historyPreviewVideo.title, '影片 0'); assert.equal(ui.state.selectedHistoryVideoId, many[0].id);
});

test('project switches, refreshes and scope changes preserve historical playback while explicit preview changes its source', async () => {
  const one = completedVideo(), two = completedVideo({ id: 'a'.repeat(32), video_url: `/api/jobs/${'f'.repeat(32)}/video` });
  const { ui, elements } = fixture(async url => url.includes('/projects/') ? response(project({ id: 'project-b', writing_skill: 'comedy-reaction' })) : response(historyData([])));
  ui.writeProject(project()); ui.receiveVideoHistory({ items: [one, two] });
  const video = elements.get('scriptHistoryVideo'); let writes = 0; Object.defineProperty(video, 'src', { set(value) { writes++; this.currentSource = value; }, get() { return this.currentSource; } });
  ui.selectHistoryVideo(one.id); ui.changeVideoHistoryScope('current'); ui.renderVideoHistory(); await ui.switchProject('project-b');
  assert.equal(ui.state.historyPreviewVideo.id, one.id); assert.equal(video.src, one.video_url); assert.equal(writes, 1);
  assert.equal(ui.readProject().writing_skill, 'comedy-reaction'); assert.equal(ui.state.dirty, false);
  assert.equal(ui.selectHistoryVideo('javascript:alert(1)'), false); assert.equal(writes, 1);
  ui.selectHistoryVideo(two.id); assert.equal(writes, 2); assert.equal(video.src, two.video_url);
});

test('video playback errors preserve the history card and retry only through an explicit preview action', async () => {
  const { ui, elements } = fixture(); ui.bindEvents(); ui.receiveVideoHistory({ items: [completedVideo()] }); ui.selectHistoryVideo(completedVideo().id);
  const video = elements.get('scriptHistoryVideo'); let retries = 0; video.load = () => { retries++; };
  await video.dispatch('error');
  assert.match(elements.get('scriptHistoryVideoError').textContent, /暫時無法播放/); assert.equal(elements.get('scriptVideoHistoryCards').children.length, 1);
  assert.equal(ui.state.historyPreviewVideo.id, completedVideo().id); ui.renderVideoHistory(); assert.equal(retries, 0);
  ui.selectHistoryVideo(completedVideo().id); assert.equal(retries, 1); assert.equal(ui.state.historyPreviewError, ''); assert.equal(ui.state.dirty, false);
});

test('history titles render literal text and invalid editor identifiers never produce an edit link', async () => {
  const title = '<iframe src="https://evil.example"></iframe>';
  const { ui, elements } = fixture(async () => response({ items: [completedVideo({ project_title: title, editor_project_id: '../editor' })] }));
  await ui.refreshVideoHistory(); const card = elements.get('scriptVideoHistoryCards').children[0];
  assert.equal(card.children[0].textContent, title); assert.equal(card.children[2].children.length, 2);
  ui.selectHistoryVideo(completedVideo().id); assert.equal(elements.get('scriptHistoryVideoTitle').textContent, title);
  assert.equal(elements.get('openScriptHistoryVideoEditor').classes.has('hidden'), true); assert.doesNotMatch(source, /\.innerHTML\s*=/);
  assert.match(html, /scripts\.css\?v=20261005-script-loras/);
});

function lora(overrides = {}) { return { name: 'wushu_h3_r2v_v7_2000.safetensors', strength: 0.5, enabled: true, family: 'ref2va', ...overrides }; }
function loraCatalog() { return { names: ['wushu_h3_r2v_v7_2000.safetensors', 'fl-only.safetensors', 'unknown.safetensors', 'turbo.safetensors'], items: [{ name: 'wushu_h3_r2v_v7_2000.safetensors', title: 'Wushu v7', purpose: '武術動作', known: true, family: 'ref2va', trigger: 'wushu_action', trigger_required: true, usage: '從低強度測試', limits: '不保證人物身份', source_url: 'https://huggingface.co/Jojocodex/model' }, { name: 'fl-only.safetensors', title: 'FL2VA 動作', purpose: '動作測試', known: true, family: 'fl2va' }], builtin_items: [{ name: 'turbo.safetensors', title: '內建 Turbo', purpose: '品質模式管理', known: true, family: 'both' }] }; }
function flatText(element) { return [element.textContent || '', ...element.children.map(flatText)].join('\n'); }
function allNodes(element) { return [element, ...element.children.flatMap(allNodes)]; }

test('optional video LoRAs default empty and normalized selections are independent dirty-signature fields', () => {
  assert.deepEqual(createNewScriptProject().custom_loras, []);
  assert.deepEqual(normalizeScriptProject({}).custom_loras, []);
  const sourceProject = project({ custom_loras: [lora()] }), normalized = normalizeScriptProject(sourceProject);
  normalized.custom_loras[0].strength = 0.7;
  assert.equal(sourceProject.custom_loras[0].strength, 0.5);
  assert.notEqual(scriptProjectSignature(sourceProject), scriptProjectSignature(normalized));
  assert.deepEqual(normalizeScriptLoras([lora({ name: 'folder\\motion.safetensors', enabled: false })]), [lora({ name: 'folder/motion.safetensors', enabled: false })]);
});

test('LoRA validation rejects unsafe names, duplicate selections, invalid strengths and unconfirmed families', () => {
  for (const name of ['../motion.safetensors', '/motion.safetensors', 'https://evil/model.safetensors', 'model.ckpt']) assert.match(scriptLoraSelectionError([lora({ name })]), /相對路徑/);
  for (const strength of [-0.1, 2.1, NaN, Infinity]) assert.match(scriptLoraSelectionError([lora({ strength })]), /強度/);
  assert.match(scriptLoraSelectionError([lora(), lora({ name: lora().name.toUpperCase() })]), /重複/);
  assert.match(scriptLoraSelectionError([lora({ family: '' })]), /明確選擇/);
  assert.match(scriptLoraSelectionError(Array.from({ length: 5 }, (_, i) => lora({ name: `motion${i}.safetensors` }))), /最多 4/);
  assert.match(scriptLoraSelectionError([lora({ family: 'fl2va' })], { video: true }), /Ref2VA/);
  for (const disabled of [lora({ family: 'fl2va', enabled: false }), lora({ family: 'fl2va', strength: 0 })]) assert.equal(scriptLoraSelectionError([disabled], { video: true }), '');
  assert.equal(scriptLoraSourceUrl('javascript:alert(1)'), ''); assert.equal(scriptLoraSourceUrl('https://user:pass@example.com/model'), '');
});

test('LoRA rows require explicit enable and unknown model-family choice while keeping all edits in drafts', async () => {
  const { ui, elements, stored } = fixture(async () => response(loraCatalog()));
  await ui.loadScriptLoras(); ui.addScriptLora();
  assert.equal(ui.state.project.custom_loras.length, 1); assert.equal(ui.state.project.custom_loras[0].enabled, false);
  let controls = ui.state.loraControls[0]; controls.select.value = 'unknown.safetensors'; await controls.select.dispatch('change');
  controls = ui.state.loraControls[0]; assert.equal(ui.state.project.custom_loras[0].family, ''); assert.match(elements.get('scriptLoraCompatibility').textContent, /明確選擇/);
  controls.family.value = 'ref2va'; await controls.family.dispatch('change');
  controls.enabled.checked = true; await controls.enabled.dispatch('change');
  controls.strength.value = '0.75'; await controls.strength.dispatch('input');
  assert.equal(ui.readProject().custom_loras[0].strength, 0.75); assert.equal(ui.readProject().custom_loras[0].enabled, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))[ui.state.newDraftId].project.custom_loras[0].strength, 0.75);
  for (let i = 0; i < 4; i++) ui.addScriptLora(); assert.equal(ui.state.project.custom_loras.length, 4); assert.equal(elements.get('addScriptLora').disabled, true);
  await ui.state.loraControls[0].remove.click(); assert.equal(ui.state.project.custom_loras.length, 3);
});

test('known metadata fixes family on selection, excludes builtin Turbo and renders literal metadata with protected sources', async () => {
  const catalog = loraCatalog(); catalog.items[0].title = '<img src=x onerror=alert(1)>'; catalog.items[0].purpose = '<script>evil</script>';
  const { ui, elements } = fixture(async () => response(catalog)); await ui.loadScriptLoras(); ui.addScriptLora();
  let controls = ui.state.loraControls[0];
  assert.equal(controls.select.options.some(option => option.value === 'turbo.safetensors'), false);
  assert.equal(controls.select.options.find(option => option.value === 'fl-only.safetensors').disabled, true);
  controls.select.value = lora().name; await controls.select.dispatch('change'); controls = ui.state.loraControls[0];
  assert.equal(ui.state.project.custom_loras[0].family, 'ref2va'); assert.equal(controls.family.disabled, true); assert.equal(controls.enabled.checked, false);
  const card = ui.state.loraControls[0].select; assert.ok(card.options.find(option => option.value === lora().name).textContent.includes('<img'));
  const help = ui.state.loraControls[0].row; assert.equal(help.enabled, false);
  const rendered = ui.state.loraCatalog.items[0]; assert.equal(rendered.title, catalog.items[0].title);
  const sourceLink = allNodes(elements.get('scriptLoraRows')).find(node => node.tagName === 'a'); assert.equal(sourceLink.href, catalog.items[0].source_url); assert.equal(sourceLink.target, '_blank'); assert.equal(sourceLink.rel, 'noopener noreferrer');
  const container = fixture(async () => response({ names: ['unsafe.safetensors'], items: [{ name: 'unsafe.safetensors', known: true, title: '<iframe>', purpose: '<svg>', family: 'ref2va', source_url: 'javascript:evil()' }] }));
  await container.ui.loadScriptLoras(); container.ui.writeProject(project({ custom_loras: [lora({ name: 'unsafe.safetensors' })] }));
  const content = container.elements.get('scriptLoraRows'); assert.match(flatText(content), /<iframe>/); assert.doesNotMatch(flatText(content), /javascript:evil/); assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test('scan failures and late catalog requests retain saved selections and never replace current project edits', async () => {
  let fail = false; const waiting = deferred(); let count = 0;
  const { ui, elements } = fixture(async () => { count++; if (count === 1) return response(loraCatalog()); if (fail) return response({ detail: 'engine offline' }, 503); return waiting.promise; });
  ui.writeProject(project({ custom_loras: [lora({ family: 'both', enabled: false })] })); await ui.loadScriptLoras();
  assert.equal(ui.readProject().custom_loras[0].family, 'both');
  fail = true; await ui.loadScriptLoras(); assert.ok(ui.state.loraCatalog.names.includes(lora().name)); assert.match(elements.get('scriptLoraCatalogStatus').textContent, /已保留/);
  fail = false; const pending = ui.loadScriptLoras(); ui.writeProject(project({ id: 'project-b', custom_loras: [lora({ name: 'saved-offline.safetensors', strength: 0.3, family: 'both' })] }));
  elements.get('scriptContent').value = '掃描途中寫的稿'; ui.markDirty(); waiting.resolve(response(loraCatalog())); await pending;
  assert.equal(ui.state.project.id, 'project-b'); assert.equal(ui.readProject().custom_loras[0].name, 'saved-offline.safetensors'); assert.equal(ui.readProject().custom_loras[0].strength, 0.3); assert.equal(elements.get('scriptContent').value, '掃描途中寫的稿');
});

test('LoRA strengths edited during server save remain dirty and are committed by a later save', async () => {
  const waiting = deferred(); let first = true;
  const { ui, stored } = fixture(async (_, options) => { if (first) { first = false; return waiting.promise; } return response(project({ ...JSON.parse(options.body), revision: 5 })); });
  ui.writeProject(project({ custom_loras: [lora()] })); ui.markDirty(); const saving = ui.saveProject();
  ui.state.loraControls[0].strength.value = '0.8'; await ui.state.loraControls[0].strength.dispatch('input');
  waiting.resolve(response(project({ custom_loras: [lora()], revision: 4 }))); assert.equal(await saving, false);
  assert.equal(ui.readProject().custom_loras[0].strength, 0.8); assert.equal(ui.state.dirty, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.custom_loras[0].strength, 0.8);
  ui.state.loraControls[0].strength.value = '0.9'; await ui.state.loraControls[0].strength.dispatch('input');
  assert.equal(await ui.saveProject(), true); assert.equal(ui.readProject().custom_loras[0].strength, 0.9);
});

test('crash recovery, version conflicts and JSON exports preserve disabled and active LoRA rows', async () => {
  const rows = [lora(), lora({ name: 'fl-only.safetensors', family: 'fl2va', enabled: false, strength: 0.2 })];
  const { ui, elements, downloads } = fixture(async url => url.includes('/projects/') ? response(project({ revision: 4, custom_loras: [] })) : response({ items: [] }));
  ui.writeProject(project({ revision: 4 })); ui.state.recovery = { writer: 'other-tab', savedAt: 2, project: project({ revision: 3, custom_loras: rows }) }; await ui.restoreDraft();
  assert.equal(ui.state.conflict.server.revision, 4); assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().custom_loras)), rows);
  elements.get('scriptExportFormat').value = 'json'; ui.exportDraft(); const exported = JSON.parse(await downloads[0].text()); assert.deepEqual(exported.custom_loras, rows);
});

test('old hosts and falsely advertised saves cannot silently drop selected LoRAs or submit generation', async () => {
  for (const support of [false, true]) {
    const { ui, elements, calls, stored } = fixture(async (_, options) => response(project({ ...JSON.parse(options.body), custom_loras: undefined, revision: 4 })), { lorasSupported: support });
    ui.writeProject(project({ custom_loras: [lora()] })); ui.state.ready = true; ui.markDirty(); assert.equal(await ui.saveProject(), false);
    const count = calls.length; await ui.generate('screenplay'); assert.equal(calls.length, count); assert.equal(ui.readProject().custom_loras[0].name, lora().name);
    assert.match(elements.get('scriptMessage').textContent, /LoRA.*保留|LoRA.*保存/); assert.equal(ui.state.dirty, true); assert.ok(stored.get('h3studio.scripts.draft.this-tab'));
  }
});

test('status requires explicit custom_loras_supported and does not infer support from a valid catalog', async () => {
  const { ui } = fixture(async () => response({ ready: true, settings: {}, models: [], writing_skills_supported: false, character_references_supported: true, custom_loras_supported: 'true' }), { lorasSupported: false, lorasSupportKnown: false });
  ui.writeProject(project({ custom_loras: [lora()] })); await ui.refreshEngine(); assert.equal(ui.state.lorasSupported, false); assert.equal(ui.state.lorasSupportKnown, true);
});

test('active FL2VA rows block automatic Ref2VA generation and frozen-storyboard import while disabled rows can be saved', async () => {
  const { ui, elements, calls } = fixture(async (_, options) => response(project({ ...JSON.parse(options.body), revision: 4 })));
  ui.writeProject(project({ character_references: [reference()], custom_loras: [lora({ family: 'fl2va' })] })); Object.assign(ui.state, { ready: true, videoReady: true, videoPipelinesSupported: true });
  await ui.startVideoPipeline(); assert.equal(calls.length, 0); assert.match(elements.get('scriptMessage').textContent, /只適用 FL2VA/);
  ui.state.job = { id: 'storyboard-lora', project_id: 'project-a', status: 'completed', action: 'storyboard', content: '{}', input: { project: project({ custom_loras: [lora({ family: 'fl2va' })] }) } };
  await ui.importStoryboard(); assert.equal(calls.length, 0);
  ui.state.project.custom_loras[0].enabled = false; ui.markDirty(); assert.equal(await ui.saveProject(), true); assert.equal(JSON.parse(calls[0].options.body).custom_loras[0].family, 'fl2va');
});

test('applying historical text preserves current LoRA choice rather than replacing it with frozen input', () => {
  const { ui } = fixture(); ui.writeProject(project({ custom_loras: [lora({ strength: 0.3 })] }));
  ui.state.job = { id: 'history', project_id: 'project-a', action: 'screenplay', status: 'completed', content: '新的正文', input: { project: project({ custom_loras: [lora({ strength: 1.2 })] }) } }; ui.applyResult();
  assert.equal(ui.readProject().custom_loras[0].strength, 0.3); assert.equal(ui.readProject().content, '新的正文');
});

test('saving accepts only the pinned Wushu Ref2VA family canonicalization and adopts it without losing transit edits', async () => {
  assert.equal(scriptLoraSaveConfirmed([lora({ family: 'both' })], [lora({ family: 'ref2va' })]), true);
  for (const name of ['unknown.safetensors', 'H3_Combat_V2.safetensors']) assert.equal(scriptLoraSaveConfirmed([lora({ name, family: 'both' })], [lora({ name, family: 'ref2va' })]), false);
  for (const change of [{ strength: 0.9 }, { enabled: false }, { name: 'other.safetensors' }]) assert.equal(scriptLoraSaveConfirmed([lora({ family: 'both' })], [lora({ family: 'ref2va', ...change })]), false);
  const pending = deferred(); const { ui } = fixture(async () => pending.promise);
  ui.writeProject(project({ custom_loras: [lora({ family: 'both' })] })); ui.markDirty(); const saving = ui.saveProject();
  ui.state.loraControls[0].strength.value = '0.85'; await ui.state.loraControls[0].strength.dispatch('input');
  pending.resolve(response(project({ custom_loras: [lora({ family: 'ref2va' })], revision: 4 }))); assert.equal(await saving, false);
  assert.equal(ui.readProject().custom_loras[0].family, 'ref2va'); assert.equal(ui.readProject().custom_loras[0].strength, 0.85); assert.equal(ui.state.lorasSupported, true);
});

test('known both-family adapters remain freely selectable as Ref2VA without changing the recorded metadata', async () => {
  const catalog = loraCatalog(); catalog.names.push('H3_Combat_V2.safetensors'); catalog.items.push({ name: 'H3_Combat_V2.safetensors', known: true, family: 'both', title: 'Combat V2', purpose: '戰鬥動作' });
  const { ui } = fixture(async () => response(catalog)); await ui.loadScriptLoras(); ui.addScriptLora();
  ui.state.loraControls[0].select.value = 'H3_Combat_V2.safetensors'; await ui.state.loraControls[0].select.dispatch('change');
  const controls = ui.state.loraControls[0]; assert.equal(controls.family.disabled, false); controls.family.value = 'ref2va'; await controls.family.dispatch('change');
  assert.equal(ui.readProject().custom_loras[0].family, 'ref2va'); assert.equal(ui.state.loraCatalog.items.find(item => item.name === 'H3_Combat_V2.safetensors').family, 'both');
});

test('resuming and importing validate frozen LoRA snapshots independently of the currently edited selection', async () => {
  const { ui, calls } = fixture(async (url, options) => response(url.endsWith('/resume') ? { id: 'frozen-pipeline', project_id: 'project-a', status: 'running', stage: 'render', snapshot: { custom_loras: [lora()] } } : { shortfilm_id: 'shortfilm-id' }));
  ui.writeProject(project({ custom_loras: [lora({ family: 'fl2va' })] })); Object.assign(ui.state, { videoPipelinesSupported: true, videoReady: true, selectedPipelineId: 'frozen-pipeline', pipeline: { id: 'frozen-pipeline', project_id: 'project-a', status: 'interrupted', snapshot: { custom_loras: [lora()] } } });
  await ui.resumePipeline(); assert.ok(calls.some(call => call.url.endsWith('/resume')));
  ui.state.job = { id: 'frozen-job', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '{}', input: { project: project({ custom_loras: [lora()] }) } }; await ui.importStoryboard();
  assert.ok(calls.some(call => call.url.endsWith('/shortfilm'))); assert.equal(ui.readProject().custom_loras[0].family, 'fl2va');
});
