const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { normalizeScriptLoras, scriptLoraSourceUrl, scriptLoraSelectionError, scriptLoraSaveConfirmed } = require('../static/scripts.js');
const { scriptPreviewData } = require('../static/scripts.js');
const { normalizeScriptVideoSettings, scriptVideoDimensions, scriptPipelineVideoFormat, scriptVideoFormatLabel } = require('../static/scripts.js');
const loraHelpSource = fs.readFileSync(path.join(__dirname, '../static/lora-help.js'), 'utf8');
const { normalizeScriptProject, normalizeScriptReferences, createNewScriptProject, createMonkeyScriptExample, scriptProjectSignature, scriptSaveOutcome, scriptDraftCandidates, scriptJobPresentation, scriptCanDeleteJob, scriptAppearanceApplyTarget, scriptPipelinePresentation, scriptPipelineVideoUrl, scriptVideoReferencesError, scriptLocalEndpoint, normalizeScriptSkills, scriptSkillSourceUrl, scriptRecommendedWritingSkill, scriptVideoHistoryRecord, scriptVideoHistoryDownloadUrl } = require('../static/scripts.js');
const source = fs.readFileSync(path.join(__dirname, '../static/scripts.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/scripts.html'), 'utf8');

function skillCatalog() {
  return ['cinematic-story', 'suspense-horror', 'comedy-reaction', 'emotional-drama', 'fantasy-adventure', 'action-chase', 'product-commercial', 'product-demo', 'educational-explainer', 'documentary-lifestyle', 'game-trailer'].map((id, index) => ({ id, name: ['通用電影敘事', '懸疑／恐怖', '喜劇／角色反應', '情感／人物劇情', '奇幻／探險', '動作／追逐', '商品／品牌廣告', '產品／操作展示', '知識／教學解說', '紀錄／生活風格', '遊戲／角色宣傳'][index], category: '短片', description: `適合 ${id} 的短片方法`, recommended_for: ['角色短片'], guide: ['確定角色目的', '寫出可見轉折'], sources: [{ name: '開放創作方法', url: 'https://example.com/skill', license: 'MIT', license_url: 'https://example.com/license' }] }));
}

function h3SkillCatalog() {
  return [...skillCatalog(), ...[
    ['h3-official-storyboard', 'H3 官方分鏡'], ['h3-3d-animation', 'H3 3D 動畫'],
    ['h3-performance-beats', 'H3 表演節拍'], ['h3-director-continuity', 'H3 導演與鏡頭銜接'], ['h3-reference-planning', 'H3 參考素材規劃'],
  ].map(([id, name], index) => ({
    id, name, category: 'H3', description: `${name}的本機改編方法`, version: '20261006-h3', recommended_for: ['H3 短片'], guide: ['保留原片長', '保留原台詞'],
    sources: [{ name: index < 2 ? '官方 H3 文件' : '社群 Skill', url: 'https://example.com/source', license: index < 2 ? '文件授權' : 'MIT', license_url: 'https://example.com/license',
      repo_url: 'https://github.com/example/source-repository', repository_stars: [9584, 9584, 175, 1139, 65][index], repository_forks: [709, 709, 4, 19, 6][index],
      checked_at: '2026-10-06', provenance: index < 2 ? 'official' : 'community', commit: 'a'.repeat(40), version: 'reviewed-source',
    }],
  }))];
}

function fixture(handler, { confirm = () => true, referencesSupported = true, referencesSupportKnown = true, skillsSupported = true, skillsSupportKnown = true, skillsCatalogReady = true, lorasSupported = true, lorasSupportKnown = true } = {}) {
  const elements = new Map(), stored = new Map(), calls = [], downloads = [], clipboard = [];
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
  elements.get('scriptVideoSource').add(new Option('直接使用編輯稿 · 跳過 AI 寫稿', 'manuscript'));
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
    navigator: { clipboard: { async writeText(value) { clipboard.push(value); } } },
    async fetch(url, options) { calls.push({ url, options }); return handler ? handler(url, options) : response({}); },
  });
  const exposed = source.replace('start().catch(error => message(error.message, true));', 'globalThis.scriptTest = { state, message, writeProject, readProject, markDirty, saveProject, switchProject, applyMonkeyExample, loadJobs, renderHistory, renderPreview, selectJob, deleteJob, changeHistoryScope, openJobProject, applyResult, restoreDraft, offerDraft, generate, crossTabChanged, saveSettings, addReference, removeReference, uploadReferenceImage, renderReferences, exportDraft, refreshEngine, renderPipelines, selectPipeline, loadPipelines, startVideoPipeline, cancelPipeline, resumePipeline, selectedVideoSourceJob, scriptVideoModelError, bindEvents, renderWritingSkills, renderWritingSkillDetails, applyWritingSkillRecommendation, loadWritingSkills, receiveVideoHistory, renderVideoHistory, selectHistoryVideo, changeVideoHistoryScope, refreshVideoHistory }; writeProject(state.project);');
  vm.runInContext(loraHelpSource, context);
  vm.runInContext(exposed.replace('globalThis.scriptTest = { state,', 'globalThis.scriptTest = { loadScriptLoras, renderScriptLoras, addScriptLora, importStoryboard, state,'), context);
  Object.assign(context.scriptTest.state, { lorasSupported, lorasSupportKnown, promptReviewSupported: true });
  context.scriptTest.state.referencesSupported = referencesSupported;
  context.scriptTest.state.referencesSupportKnown = referencesSupportKnown;
  Object.assign(context.scriptTest.state, { skillsSupported, skillsSupportKnown, skillsCatalogReady, writingSkills: skillsCatalogReady ? skillCatalog() : [] });
  context.scriptTest.renderWritingSkills(); context.scriptTest.renderPreview();
  return { ui: context.scriptTest, elements, stored, calls, actions, downloads, clipboard };
}

function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, async json() { return data; } }; }
function deferred() { let resolve; const promise = new Promise(callback => { resolve = callback; }); return { promise, resolve }; }
function project(overrides = {}) { return { id: 'project-a', title: '雨停之前', idea: '咖啡店裡一段相遇', genre: '劇情', duration: 60, characters: '', character_references: [], custom_loras: [], video_settings: { aspect_ratio: '16:9', megapixels: 0.4, output_resolution: 'native' }, style: '', instructions: '', content: '原始編輯稿', writing_skill: '', revision: 3, created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', ...overrides }; }
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

test('a completed storyboard defaults to the backend performance script and copies the chosen view without changing import data', async () => {
  const raw = '{"title":"原始分鏡","scenes":[]}';
  const readable = '1. 0.0–2.0s｜迎戰\n鏡頭／景別：24mm 中景\n動作／表情：抬眼→握緊兵器→重心前移\n2. 2.0–4.0s｜交鋒\n對白：原台詞';
  const job = { id: 'readable-storyboard', project_id: 'project-a', action: 'storyboard', status: 'completed', content: raw,
    readable_content: readable, readable_content_source: 'storyboard', readable_content_note: '只整理已提供欄位。',
    storyboard: { storyboard_normalization: { source_cuts: [{ cuts: [{ action: '[Shot 2] At 00:02.000, original action', duration: 2 }] }] } } };
  const original = JSON.stringify(job);
  const { ui, elements, calls, clipboard } = fixture(async () => response({ shortfilm_id: 'shortfilm-a' }));
  ui.bindEvents(); ui.writeProject(project()); ui.state.job = job; ui.state.selectedJobId = job.id; ui.renderPreview();
  assert.equal(elements.get('scriptPreviewViews').classes.has('hidden'), false);
  assert.equal(elements.get('scriptPreviewReadable').attributes['aria-pressed'], 'true');
  assert.equal(elements.get('scriptPreviewContent').textContent, readable);
  assert.equal(elements.get('scriptPreviewFormatNote').textContent, '只整理已提供欄位。');
  assert.equal(elements.get('applyScriptResult').classes.has('hidden'), true);
  assert.equal(elements.get('importScriptStoryboard').disabled, false);
  await elements.get('copyScriptResult').click(); assert.equal(clipboard.at(-1), readable);
  await elements.get('scriptPreviewRaw').click();
  assert.equal(elements.get('scriptPreviewContent').textContent, raw);
  assert.equal(elements.get('scriptPreviewRaw').attributes['aria-pressed'], 'true');
  await elements.get('copyScriptResult').click(); assert.equal(clipboard.at(-1), raw);
  await elements.get('scriptPreviewReadable').click();
  await ui.importStoryboard();
  assert.equal(calls[0].url, '/api/scripts/jobs/readable-storyboard/shortfilm');
  assert.equal(calls[0].options.body, '{}');
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  // Import adds only its existing shortfilm pointer; the displayed data remain intact.
  const after = { ...job }; delete after.shortfilm_id;
  assert.equal(JSON.stringify(after), original);
});

test('preview selection survives polling of the same job and a different job resets to readable', async () => {
  let job = { id: 'first-job', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '{"version":1}', readable_content: '第一份表演腳本' };
  const { ui, elements } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([job])) : response(job));
  ui.bindEvents(); ui.writeProject(project()); await ui.loadJobs();
  await elements.get('scriptPreviewRaw').click();
  job = { ...job, content: '{"version":2}', readable_content: '更新的表演腳本' };
  await ui.loadJobs();
  assert.equal(elements.get('scriptPreviewContent').textContent, job.content);
  assert.equal(elements.get('scriptPreviewRaw').attributes['aria-pressed'], 'true');
  job = { ...job, id: 'second-job', readable_content: '第二份表演腳本' };
  ui.state.jobs = [job]; await ui.selectJob(job.id);
  assert.equal(elements.get('scriptPreviewContent').textContent, '第二份表演腳本');
  assert.equal(elements.get('scriptPreviewReadable').attributes['aria-pressed'], 'true');
});

test('original performance scripts are identified as storyboard sources rather than translations of compiled data', () => {
  const { ui, elements } = fixture(); ui.writeProject(project());
  ui.state.job = { id: 'source-storyboard', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '{"action":"English engine description"}',
    readable_content: '1. 0.0–2.0s｜原稿\nVFX：原稿未提供。', readable_content_source: 'screenplay', readable_content_note: '本稿是生成分鏡時的原表演腳本。' };
  ui.renderPreview();
  assert.equal(elements.get('scriptPreviewReadable').textContent, '原表演腳本（分鏡來源）');
  assert.equal(elements.get('scriptPreviewFormatNote').textContent, '本稿是生成分鏡時的原表演腳本。');
  assert.equal(elements.get('scriptPreviewContent').textContent, ui.state.job.readable_content);
});

test('screenplays and rewrites display editable content while raw views and copying retain original structured responses', async () => {
  for (const action of ['screenplay', 'rewrite']) {
    const editable = '1. 0.0–2.0s｜開始\n動作／表情：抬頭→轉身→停步';
    const original = '{"shots":[{"action":"original"}]}';
    const job = { id: `${action}-job`, project_id: 'project-a', action, status: 'completed', content: original, raw_content: original, readable_content: editable };
    const { ui, elements, clipboard } = fixture(); ui.bindEvents(); ui.writeProject(project({ content: '' })); ui.state.job = job; ui.renderPreview();
    assert.equal(elements.get('scriptPreviewContent').textContent, editable);
    await elements.get('scriptPreviewRaw').click(); await elements.get('copyScriptResult').click();
    assert.equal(clipboard.at(-1), job.raw_content);
    assert.equal(elements.get('scriptPreviewContent').textContent, job.raw_content);
    assert.equal(ui.selectedVideoSourceJob(), job);
    ui.applyResult();
    assert.equal(elements.get('scriptContent').value, editable);
    assert.equal(job.raw_content, original);
    assert.equal(job.content, original);
  }
});

test('unknown structured legacy screenplays cannot masquerade as readable scripts and their backend explanation remains visible', () => {
  for (const content of ['  {"shots":[]}', "\n {'shots': []}", '  [ {"action":"legacy"} ]', '\n```json\n{"shots":[]}\n```', '```python\n{\'shots\': []}\n```']) {
    const job = { id: 'legacy-structured', project_id: 'project-a', action: 'screenplay', status: 'completed', content, readable_content_note: '舊紀錄尚未提供可讀文字，原資料已保留。' };
    const result = scriptPreviewData(job);
    assert.equal(result.readableReady, false); assert.equal(result.view, 'raw'); assert.equal(result.text, content);
    const { ui, elements } = fixture(); ui.writeProject(project()); ui.state.job = job; ui.renderPreview();
    assert.equal(elements.get('scriptPreviewReadable').disabled, true);
    assert.equal(elements.get('scriptPreviewContent').textContent, content);
    assert.match(elements.get('scriptPreviewFormatNote').textContent, /舊紀錄尚未提供可讀文字，原資料已保留/);
  }
  const plain = scriptPreviewData({ action: 'rewrite', status: 'completed', content: '1. 0.0–2.0s｜純文字稿\n動作／表情：抬頭→轉身' });
  assert.equal(plain.readableReady, true); assert.equal(plain.view, 'readable');
});

test('partial or legacy storyboard data remain raw and unavailable readable fields cannot invent performance details', () => {
  const raw = '{"action":"Only supplied action"}';
  for (const changes of [{}, { status: 'running', readable_content: '未驗證稿' }, { status: 'failed', readable_content: '未完成稿' }, { truncated: true, readable_content: '截斷稿' }, { readable_content: {} }, { readable_content: '   ' }]) {
    const job = { id: 'legacy-storyboard', action: 'storyboard', status: 'completed', content: raw, ...changes };
    const result = scriptPreviewData(job);
    assert.equal(result.text, raw); assert.equal(result.readableReady, false); assert.equal(result.view, 'raw');
  }
  assert.equal(scriptPreviewData({ action: 'screenplay', status: 'completed', content: '純文字完整劇本' }).text, '純文字完整劇本');
  assert.equal(scriptPreviewData({ action: 'outline', content: '故事大綱' }).supported, false);
  assert.equal(scriptPreviewData(null).text, '');
  const { ui, elements } = fixture(); ui.writeProject(project()); ui.state.job = { id: 'legacy', project_id: 'project-a', action: 'storyboard', status: 'completed', content: raw };
  ui.renderPreview();
  assert.equal(elements.get('scriptPreviewReadable').disabled, true);
  assert.match(elements.get('scriptPreviewFormatNote').textContent, /尚未提供可讀表演腳本/);
  assert.equal(elements.get('scriptPreviewContent').textContent, raw);
});

test('readable previews and backend notes are literal text and new defaults request the six-beat editable script format', () => {
  const readable = '<img src=x onerror=alert(1)>\n1. 0.0–2.0s｜段名';
  const { ui, elements } = fixture(); ui.writeProject(project());
  ui.state.job = { id: 'safe-readable', project_id: 'project-a', action: 'storyboard', status: 'completed', content: '{}', readable_content: readable, readable_content_note: '<b>來源說明</b>' };
  ui.renderPreview();
  assert.equal(elements.get('scriptPreviewContent').textContent, readable);
  assert.equal(elements.get('scriptPreviewFormatNote').textContent, '<b>來源說明</b>');
  const fresh = createNewScriptProject();
  for (const phrase of ['不輸出 JSON 或 Python 字典', '1. 0.0–2.0s｜段名', '上述八個欄位分行', '2～4 個可見變化', '0–2、2–4、4–6.5、6.5–9、9–12、12–15 秒六段', '指定時間優先']) assert.ok(fresh.instructions.includes(phrase));
  assert.equal(normalizeScriptProject({ instructions: '沿用我的原格式' }).instructions, '沿用我的原格式');
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

test('context capacity adjustment preserves the manuscript and an unchanged 4096-token output budget', () => {
  const { ui, elements } = fixture();
  ui.writeProject(project({ content: '完整原稿與原始台詞必須保留。' }));
  const base = { id: 'context-adjusted-job', project_id: 'project-a', status: 'running', action: 'rewrite', created_at: '2026-10-02T00:00:00Z', input: { settings: { num_ctx: 16384, num_predict: 4096 } } };
  ui.state.job = base; ui.renderPreview();
  const original = elements.get('scriptJobDetail').textContent;
  ui.state.job = { ...base, context_budget_adjusted: true };
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, `${original} · 已依完整稿件調整本次處理容量，原文保留。`);
  assert.doesNotMatch(elements.get('scriptJobDetail').textContent, /本次輸出上限調整/);
  assert.equal(ui.state.job.input.settings.num_predict, 4096);
  assert.equal(elements.get('scriptContent').value, '完整原稿與原始台詞必須保留。');
  ui.renderPreview();
  assert.equal(elements.get('scriptJobDetail').textContent, `${original} · 已依完整稿件調整本次處理容量，原文保留。`);
});

test('job details can show both valid context and output budget adjustments', () => {
  const { ui, elements } = fixture(); ui.writeProject(project());
  ui.state.job = { id: 'both-adjusted-job', project_id: 'project-a', status: 'completed', action: 'rewrite', content: '完整處理結果',
    context_budget_adjusted: true, output_budget_adjusted: true, input: { settings: { num_ctx: 32768, num_predict: 2048 } } };
  ui.renderPreview();
  const detail = elements.get('scriptJobDetail').textContent;
  assert.match(detail, /已依完整稿件調整本次處理容量，原文保留。/);
  assert.match(detail, /依稿件長度，本次輸出上限調整為 2048 Token。/);
  assert.equal(detail.match(/已依完整稿件調整本次處理容量/g).length, 1);
  assert.equal(detail.match(/本次輸出上限調整/g).length, 1);
});

test('invalid context adjustment flags and capacities do not claim the manuscript capacity was adjusted', () => {
  const { ui, elements } = fixture(); ui.writeProject(project());
  const base = { id: 'invalid-context-job', project_id: 'project-a', status: 'running', action: 'rewrite', created_at: '2026-10-02T00:00:00Z', input: { settings: { num_ctx: 16384, num_predict: 4096 } } };
  ui.state.job = base; ui.renderPreview();
  const original = elements.get('scriptJobDetail').textContent;
  for (const flag of [undefined, false, null, 'true', 1, {}]) {
    ui.state.job = { ...base, context_budget_adjusted: flag };
    ui.renderPreview();
    assert.equal(elements.get('scriptJobDetail').textContent, original);
  }
  for (const input of [undefined, null, {}, { settings: null }, { settings: {} }, ...[undefined, null, 0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '16384'].map(num_ctx => ({ settings: { num_ctx, num_predict: 4096 } }))]) {
    ui.state.job = { ...base, context_budget_adjusted: true, input };
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
  assert.match(html, /scripts\.js\?v=20261029-prompt-review/);
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

test('a new unsaved project keeps all history without automatically previewing another project', async () => {
  const old = historyJob({ project_id: 'old-project', project_title: '舊劇本' });
  const { ui, elements } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData([old])) : response(old));
  await ui.loadJobs();
  assert.equal(ui.state.project.id, null);
  assert.equal(elements.get('scriptJobHistory').children[0].dataset.jobId, old.id);
  assert.equal(ui.state.selectedJobId, null); assert.equal(ui.state.job, null);
  assert.notEqual(elements.get('scriptPreviewContent').textContent, old.content);
  assert.equal(elements.get('applyScriptResult').disabled, true);
  await ui.changeHistoryScope('current');
  assert.equal(ui.state.jobs.length, 1);
  assert.match(elements.get('scriptJobHistory').children[0].textContent, /全部劇本.*其他劇本/);
  assert.equal(ui.state.job, null);
  await ui.changeHistoryScope('all');
  assert.equal(ui.state.selectedJobId, null); assert.equal(ui.state.job, null);
  await ui.selectJob(old.id);
  assert.equal(ui.state.selectedJobId, old.id);
  assert.equal(elements.get('copyScriptResult').disabled, false);
  assert.equal(elements.get('scriptPreviewTitle').textContent, '歷史生成預覽 · 舊劇本');
  assert.match(elements.get('scriptJobBadge').textContent, /其他劇本/);
});

test('automatic preview uses only the current project while explicit historical selection survives polling', async () => {
  const other = historyJob({ id: 'other-job', project_id: 'project-b', project_title: '大叔對抗雷霸龍' });
  const own = historyJob({ id: 'own-job', project_title: '武打測試', content: '妲己與姜子牙的分鏡' });
  let jobs = [other];
  const { ui, elements, calls } = fixture(async url => url === '/api/scripts/jobs' ? response(historyData(jobs)) : response(jobs.find(job => url.endsWith(job.id))));
  ui.writeProject(project({ title: '武打測試', idea: '妲己與姜子牙', content: '' }));
  await ui.loadJobs();
  assert.equal(ui.state.selectedJobId, null); assert.equal(ui.state.job, null);
  assert.equal(calls.length, 1);
  assert.equal(elements.get('scriptContent').value, '');
  jobs = [other, own]; await ui.loadJobs();
  assert.equal(ui.state.selectedJobId, own.id);
  assert.equal(elements.get('scriptPreviewContent').textContent, own.content);
  assert.equal(elements.get('scriptPreviewTitle').textContent, '生成預覽');
  await ui.selectJob(other.id); await ui.loadJobs();
  assert.equal(ui.state.selectedJobId, other.id);
  assert.equal(elements.get('scriptPreviewTitle').textContent, '歷史生成預覽 · 大叔對抗雷霸龍');
  assert.match(elements.get('scriptPreviewContext').textContent, /不是目前劇本的結果/);
  assert.equal(elements.get('scriptContent').value, '');
});

test('switching to a project with no records never falls back to all-project history', async () => {
  const other = historyJob({ project_title: '舊劇本' });
  const next = project({ id: 'project-b', title: '新故事', idea: '妲己與姜子牙', content: '' });
  const { ui, elements } = fixture(async url => url.includes('/projects/') ? response(next) : url === '/api/scripts/jobs' ? response(historyData([other])) : response(other));
  ui.writeProject(project()); ui.state.jobs = [other]; ui.state.job = other; ui.state.selectedJobId = other.id;
  assert.equal(await ui.switchProject(next.id), true);
  assert.equal(ui.state.selectedJobId, null); assert.equal(ui.state.job, null);
  assert.equal(elements.get('scriptJobHistory').children.length, 1);
  assert.equal(elements.get('scriptPreviewTitle').textContent, '生成預覽');
  assert.notEqual(elements.get('scriptPreviewContent').textContent, other.content);
  assert.equal(elements.get('scriptContent').value, '');
});

test('an unsuccessful request is marked as unconfirmed and a successful retry replaces the historical preview', async () => {
  const old = historyJob({ content: '10月5日已保存的舊結果' });
  const fresh = historyJob({ id: 'fresh-job', status: 'queued', content: '', input: { project: project({ idea: '妲己與姜子牙', content: '' }) } });
  let refused = true;
  const { ui, elements, calls } = fixture(async () => refused ? response({ error: '請先分析外型' }, 400) : response(fresh));
  ui.writeProject(project({ idea: '妲己與姜子牙', content: '' })); ui.state.ready = true;
  ui.state.job = old; ui.state.selectedJobId = old.id; ui.state.jobs = [old];
  await ui.generate('screenplay');
  assert.equal(ui.state.job.id, old.id); assert.equal(ui.state.job.status, 'completed');
  assert.equal(elements.get('scriptPreviewContent').textContent, old.content);
  assert.match(elements.get('scriptPreviewContext').textContent, /本次尚未確認新的生成工作.*已保存歷史結果.*重新整理生成紀錄確認/);
  assert.match(elements.get('scriptJobBadge').textContent, /本次尚未確認.*歷史結果/);
  assert.match(elements.get('scriptPreviewTitle').textContent, /歷史生成預覽/);
  assert.equal(elements.get('scriptPreviewContext').classes.has('hidden'), false);
  assert.match(elements.get('scriptMessage').textContent, /請先分析外型/);
  assert.equal(elements.get('applyScriptResult').disabled, false);
  assert.equal(elements.get('scriptContent').value, '');
  assert.deepEqual(JSON.parse(calls[0].options.body), { project_id: 'project-a', action: 'screenplay', instructions: '' });
  refused = false; await ui.generate('screenplay');
  assert.equal(ui.state.job.id, fresh.id);
  assert.equal(ui.state.previewRequestFailed, false);
  assert.equal(elements.get('scriptPreviewContext').classes.has('hidden'), true);
});

test('a generation blocked by a failed save also distinguishes old preview content from the new request', async () => {
  const old = historyJob();
  const { ui, elements, calls } = fixture(async () => response({ error: '主機無法保存' }, 503));
  ui.writeProject(project({ idea: '妲己與姜子牙', content: '' })); ui.markDirty(); ui.state.ready = true;
  ui.state.job = old; ui.state.selectedJobId = old.id;
  await ui.generate('storyboard');
  assert.ok(calls.every(call => call.url !== '/api/scripts/jobs'));
  assert.match(elements.get('scriptPreviewContext').textContent, /本次尚未確認新的生成工作.*重新整理生成紀錄確認/);
  assert.equal(ui.state.job.id, old.id);
  assert.equal(elements.get('scriptIdea').value, '妲己與姜子牙');
  assert.equal(elements.get('scriptContent').value, '');
  assert.equal(ui.state.dirty, true);
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
  assert.deepEqual(JSON.parse(calls[0].options.body), { project_id: 'project-a', require_prompt_review: true });
  assert.equal(elements.get('scriptContent').value, '原始編輯稿');
  assert.equal(ui.state.pipeline.id, pipeline.id); assert.equal(ui.state.dirty, false);
});

test('paste manuscript entry focuses the editor without changing any story settings or starting work', async () => {
  const { ui, elements, calls, stored } = fixture(); prepareVideo(ui); ui.bindEvents();
  elements.get('scriptContent').value = '自己貼上的完整劇本'; ui.markDirty();
  const before = JSON.stringify(ui.readProject()), drafts = stored.get('h3studio.scripts.draft.this-tab');
  await elements.get('scriptStartFromManuscript').click();
  assert.equal(elements.get('scriptVideoSource').value, 'manuscript');
  assert.equal(elements.get('scriptContent').focused, true);
  assert.equal(elements.get('scriptStartFromManuscript').attributes['aria-pressed'], 'true');
  assert.equal(JSON.stringify(ui.readProject()), before); assert.equal(stored.get('h3studio.scripts.draft.this-tab'), drafts); assert.equal(calls.length, 0);
  await elements.get('scriptStartFromIdea').click();
  assert.equal(elements.get('scriptVideoSource').value, 'current'); assert.equal(elements.get('scriptIdea').focused, true);
  assert.equal(JSON.stringify(ui.readProject()), before); assert.equal(calls.length, 0);
});

test('direct editor button sends an explicit manuscript snapshot with revision and no story idea or preview dependency', async () => {
  const pipeline = pipelineRecord({ source_mode: 'manuscript', stage: 'storyboard' });
  const { ui, elements, calls } = fixture(async () => response(pipeline));
  prepareVideo(ui, { idea: '', content: '0–15秒：紅色機器人揮手說「你好」。' }); ui.state.manuscriptVideoSupported = true;
  ui.bindEvents(); ui.renderPreview();
  assert.equal(elements.get('startManuscriptVideo').disabled, false);
  await elements.get('startManuscriptVideo').click();
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), { project_id: 'project-a', require_prompt_review: true, source_mode: 'manuscript', project_revision: 3 });
  assert.equal(elements.get('scriptContent').value, '0–15秒：紅色機器人揮手說「你好」。'); assert.equal(elements.get('scriptIdea').value, '');
  assert.equal(elements.get('scriptVideoStageList').children[0].textContent, '使用編輯稿'); assert.equal(elements.get('scriptVideoStageList').children[0].className, 'done');
  assert.match(elements.get('scriptMessage').textContent, /確認全部稿件後才生成影片/);
  assert.ok(calls.every(call => call.url !== '/api/scripts/jobs'));
});

test('direct manuscript never falls back to an idea on blank text, unconfirmed backend, missing reference or engine', async () => {
  for (const scenario of ['blank', 'backend', 'reference', 'engine', 'model']) {
    const { ui, elements, calls } = fixture(); prepareVideo(ui); ui.state.manuscriptVideoSupported = true;
    if (scenario === 'blank') elements.get('scriptContent').value = '  \n';
    if (scenario === 'backend') ui.state.manuscriptVideoSupported = false;
    if (scenario === 'reference') ui.writeProject(project({ character_references: [] }));
    if (scenario === 'engine') ui.state.videoReady = false;
    if (scenario === 'model') ui.state.ready = false;
    elements.get('scriptVideoSource').value = 'manuscript'; await ui.startVideoPipeline();
    assert.equal(calls.length, 0, scenario); assert.ok(elements.get('scriptMessage').textContent.length > 0, scenario);
    assert.equal(elements.get('scriptIdea').value, '咖啡店裡一段相遇');
  }
});

test('direct manuscript saves pasted text before launch and protects edits or a changed source during saving', async () => {
  for (const change of ['none', 'content', 'source']) {
    const pending = deferred(); const pipeline = pipelineRecord({ source_mode: 'manuscript', stage: 'storyboard' });
    const { ui, elements, calls } = fixture(async url => url.includes('/projects/') ? pending.promise : response(pipeline));
    prepareVideo(ui, { idea: '' }); ui.state.manuscriptVideoSupported = true; ui.bindEvents();
    elements.get('scriptContent').value = '剛貼上的新劇本'; ui.markDirty(); elements.get('scriptVideoSource').value = 'manuscript';
    const starting = ui.startVideoPipeline();
    assert.equal(calls.length, 1); assert.equal(calls[0].options.method, 'PUT'); const saved = JSON.parse(calls[0].options.body);
    assert.equal(saved.content, '剛貼上的新劇本'); assert.equal(saved.idea, ''); assert.equal(elements.get('scriptVideoSource').disabled, true);
    if (change === 'content') { elements.get('scriptContent').value = '儲存中新增的另一段'; ui.markDirty(); }
    if (change === 'source') elements.get('scriptVideoSource').value = 'current';
    pending.resolve(response(project({ ...saved, revision: 4 }))); await starting;
    if (change === 'none') { assert.equal(calls.length, 2); assert.deepEqual(JSON.parse(calls[1].options.body), { project_id: 'project-a', require_prompt_review: true, source_mode: 'manuscript', project_revision: 4 }); }
    else { assert.equal(calls.length, 1); assert.equal(ui.state.pipeline, null); }
    assert.equal(elements.get('scriptContent').value, change === 'content' ? '儲存中新增的另一段' : '剛貼上的新劇本');
  }
});

test('direct manuscript capability is explicitly advertised and a failed status check disables it', async () => {
  let fails = false;
  const { ui, elements } = fixture(async url => url === '/api/scripts/status' ? fails ? response({ error: '離線' }, 503) : response({ ready: true, manuscript_video_supported: true, prompt_review_supported: true, video_pipelines_supported: true, video_ready: true }) : response({ items: [] }));
  prepareVideo(ui, { idea: '' }); await ui.refreshEngine(); ui.renderPreview();
  assert.equal(ui.state.manuscriptVideoSupported, true); assert.equal(elements.get('startManuscriptVideo').disabled, false);
  fails = true; await ui.refreshEngine();
  assert.equal(ui.state.manuscriptVideoSupported, false); assert.equal(elements.get('startManuscriptVideo').disabled, true);
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
  assert.equal(elements.get('scriptVideoPipelineDetail').textContent, `${pipeline.error} · MiniMax H3 · 生成 864 × 480 → 成片 864 × 480 · MP4 · 24 fps`);
  assert.equal(elements.get('resumeScriptVideoPipeline').classes.has('hidden'), false);
  assert.equal(elements.get('scriptVideoResumeHint').classes.has('hidden'), false);
  assert.match(html, /接續使用原設定與原參考圖/);
});

function reviewPipeline(overrides = {}) {
  return pipelineRecord({ status: 'awaiting_review', stage: 'awaiting_review', progress: 45, review: { id: 'review-one', prompts: [{ scene_id: 'scene-one', shot_id: 'shot-one', video_model: 'fasth3_omni_nvfp4', duration: 5.167, width: 864, height: 480, mapping: [{ alias: '<hero>', picture_tags: ['<Picture 1>'] }], warnings: ['保留角色外觀'], prompt: '[Video]\n<Subject 1> waves.\n[Audio]\nHello!', h3_translation: { status: 'translated' } }] }, ...overrides });
}

test('persisted awaiting-review pipelines expose full literal prompts and never approve during polling', async () => {
  const pipeline = reviewPipeline(), { ui, elements, calls } = fixture(async url => url === '/api/scripts/video-pipelines' ? response({ items: [pipeline] }) : response(pipeline));
  prepareVideo(ui); ui.state.pipeline = pipeline; ui.state.selectedPipelineId = pipeline.id; await ui.loadPipelines();
  assert.equal(scriptPipelinePresentation(pipeline).active, true);
  assert.equal(elements.get('scriptPromptReview').classes.has('hidden'), false);
  const text = flatText(elements.get('scriptPromptReviewList'));
  assert.match(text, /\[Video\]/); assert.match(text, /\[Audio\]/); assert.match(text, /<Picture 1>/); assert.match(text, /864 × 480/); assert.match(text, /FastH3-Omni/);
  assert.equal(elements.get('startManuscriptVideo').disabled, true);
  assert.equal(calls.some(call => call.url.endsWith('/approve')), false);
  await ui.startVideoPipeline(); assert.equal(calls.some(call => call.options?.method === 'POST'), false);
});

test('unchanged prompt review polling and original-text choice preserve card identity and reading positions', async () => {
  const pipeline = reviewPipeline(); pipeline.review.prompts[0].h3_translation = { status: 'unavailable' };
  const { ui, elements } = fixture(); prepareVideo(ui); ui.bindEvents(); ui.state.pipeline = pipeline; ui.renderPipelines();
  const list = elements.get('scriptPromptReviewList'), card = list.children[0], prompt = card.children[1];
  list.scrollTop = 360; prompt.scrollTop = 180;
  let replacements = 0; const replace = list.replaceChildren.bind(list); list.replaceChildren = (...nodes) => { replacements++; replace(...nodes); };
  ui.state.pipeline = JSON.parse(JSON.stringify(pipeline)); ui.state.pipeline.updated_at = '2026-10-29T10:00:00Z'; ui.renderPipelines(); ui.renderPipelines();
  assert.strictEqual(list.children[0], card); assert.strictEqual(list.children[0].children[1], prompt); assert.equal(replacements, 0);
  assert.equal(list.scrollTop, 360); assert.equal(prompt.scrollTop, 180);
  await elements.get('useOriginalScriptPromptReview').click();
  assert.strictEqual(list.children[0].children[1], prompt); assert.equal(replacements, 0);
  assert.match(elements.get('scriptPromptReviewStatus').textContent, /已選擇使用原文/);
  assert.equal(elements.get('approveScriptPromptReview').disabled, false);
});

test('new review content identity or selected pipeline refreshes the displayed review cards', () => {
  const { ui, elements } = fixture(); prepareVideo(ui); ui.state.pipeline = reviewPipeline(); ui.renderPipelines();
  const list = elements.get('scriptPromptReviewList'); let previous = list.children[0];
  ui.state.pipeline.review.id = 'review-two'; ui.renderPipelines(); assert.notStrictEqual(list.children[0], previous); previous = list.children[0];
  ui.state.pipeline.review.prompts[0].prompt = '[Video]\nThe new reviewed performance.'; ui.renderPipelines();
  assert.notStrictEqual(list.children[0], previous); assert.match(flatText(list), /new reviewed performance/); previous = list.children[0];
  ui.state.pipeline = { ...ui.state.pipeline, id: 'd'.repeat(32) }; ui.renderPipelines(); assert.notStrictEqual(list.children[0], previous);
});

test('script approval sends the exact review id once and does not save changed future manuscript settings', async () => {
  const pending = deferred(), pipeline = reviewPipeline();
  const { ui, elements, calls, clipboard } = fixture(async () => pending.promise); prepareVideo(ui); ui.bindEvents();
  ui.state.pipeline = pipeline; ui.state.selectedPipelineId = pipeline.id; ui.state.pipelines = [pipeline]; ui.renderPipelines();
  elements.get('scriptContent').value = '下一個流程的新稿'; ui.markDirty();
  await elements.get('copyScriptPromptReview').click(); assert.match(clipboard[0], /\[Video\].*\n/);
  const first = elements.get('approveScriptPromptReview').click(); await elements.get('approveScriptPromptReview').click();
  assert.equal(calls.length, 1); assert.ok(calls[0].url.endsWith('/approve')); assert.deepEqual(JSON.parse(calls[0].options.body), { review_id: 'review-one' });
  pending.resolve(response({ ...pipeline, status: 'running', stage: 'render' })); await first;
  assert.equal(elements.get('scriptContent').value, '下一個流程的新稿'); assert.equal(ui.state.dirty, true); assert.equal(elements.get('scriptPromptReview').classes.has('hidden'), true);
});

test('script original-text fallback requires a separate inline choice before final approval', async () => {
  const pipeline = reviewPipeline(); pipeline.review.prompts[0].h3_translation = { status: 'unavailable', reason_code: 'timeout' };
  const { ui, elements, calls } = fixture(async () => response({ ...pipeline, status: 'running' })); prepareVideo(ui); ui.bindEvents(); ui.state.pipeline = pipeline; ui.state.selectedPipelineId = pipeline.id; ui.renderPipelines();
  assert.equal(elements.get('approveScriptPromptReview').disabled, true);
  await elements.get('approveScriptPromptReview').click(); assert.equal(calls.length, 0);
  await elements.get('useOriginalScriptPromptReview').click(); assert.equal(elements.get('approveScriptPromptReview').disabled, false);
  await elements.get('approveScriptPromptReview').click(); assert.equal(calls.length, 1);
});

test('old hosts cannot silently skip full prompt review for one-click automatic writing or resume', async () => {
  for (const action of ['start', 'auto', 'resume']) {
    const { ui, calls } = fixture(); prepareVideo(ui); ui.state.promptReviewSupported = false; ui.state.autoVideo = true;
    if (action === 'start') await ui.startVideoPipeline();
    if (action === 'auto') await ui.generate('screenplay');
    if (action === 'resume') { ui.state.pipeline = pipelineRecord({ status: 'interrupted' }); await ui.resumePipeline(); }
    assert.equal(calls.length, 0, action);
  }
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
  assert.equal(calls.length, 1); assert.equal(calls[0].url, `/api/scripts/video-pipelines/${previous.id}/resume`); assert.equal(calls[0].options.body, JSON.stringify({ require_prompt_review: true }));
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

test('H3 sources retain reviewed provenance and repository counts without presenting a profile rating', async () => {
  const catalog = h3SkillCatalog();
  const { ui, elements } = fixture(async url => response(url === '/api/scripts/skills' ? { items: catalog, version: '20261006-h3' } : skillStatus()), { skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false });
  ui.writeProject(project({ writing_skill: 'h3-official-storyboard' }));
  await ui.refreshEngine();
  assert.equal(elements.get('scriptWritingSkill').options.length, 17);
  assert.equal(ui.readProject().writing_skill, 'h3-official-storyboard');
  const record = ui.state.writingSkills.find(item => item.id === 'h3-official-storyboard');
  assert.equal(record.version, '20261006-h3');
  assert.equal(record.sources[0].commit, 'a'.repeat(40));
  assert.equal(record.sources[0].version, 'reviewed-source');
  assert.equal(record.sources[0].repository_stars, 9584);
  assert.equal(record.sources[0].repository_forks, 709);
  const rendered = elements.get('scriptSkillSources').children[0];
  const note = rendered.children[2];
  assert.match(note.textContent, /官方來源.*倉庫 ⭐ 9,584.*Forks 709.*查核 2026-10-06.*來源版本 aaaaaaaa/);
  assert.equal(note.title, 'a'.repeat(40));
  assert.equal(rendered.children[3].href, 'https://github.com/example/source-repository');
  assert.equal(rendered.children[3].rel, 'noopener noreferrer');
  assert.equal(elements.get('scriptSkillVersion').textContent, '方法版本：20261006-h3');
  assert.match(html, /Stars 為倉庫關注度，並非成片品質評分/);
  assert.match(html, /scripts\.js\?v=20261029-prompt-review/);
  elements.get('scriptWritingSkill').value = 'h3-performance-beats'; ui.renderWritingSkillDetails();
  assert.match(elements.get('scriptSkillSources').children[0].children[2].textContent, /社群來源.*倉庫 ⭐ 175.*Forks 4/);
});

test('source metadata treats unverified counts as absent and renders untrusted metadata as literal text', async () => {
  const catalog = h3SkillCatalog(), upstream = catalog.at(-1).sources[0];
  upstream.repository_stars = '9584'; upstream.repository_forks = -1;
  upstream.provenance = '<b>official</b>'; upstream.checked_at = '<img src=x onerror=alert(1)>';
  upstream.repo_url = 'javascript:alert(1)'; upstream.commit = '<script>source</script>';
  const normalized = normalizeScriptSkills(catalog).at(-1).sources[0];
  assert.equal(normalized.repository_stars, null); assert.equal(normalized.repository_forks, null); assert.equal(normalized.provenance, '');
  const { ui, elements } = fixture(async () => response({ items: catalog }));
  ui.writeProject(project({ writing_skill: 'h3-reference-planning' })); await ui.loadWritingSkills();
  const rendered = elements.get('scriptSkillSources').children[0];
  assert.equal(rendered.children.length, 3);
  assert.doesNotMatch(rendered.children[2].textContent, /⭐|Forks|官方來源/);
  assert.match(rendered.children[2].textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(rendered.children[2].title, '<script>source</script>');
  for (const value of [NaN, Infinity, -1, 2.5, Number.MAX_SAFE_INTEGER + 1, true, null]) {
    const invalid = h3SkillCatalog(); invalid.at(-1).sources[0].repository_stars = value;
    assert.equal(normalizeScriptSkills(invalid).at(-1).sources[0].repository_stars, null);
  }
  const zero = h3SkillCatalog(); zero.at(-1).sources[0].repository_stars = 0;
  assert.equal(normalizeScriptSkills(zero).at(-1).sources[0].repository_stars, 0);
  const legacy = normalizeScriptSkills(skillCatalog())[0];
  assert.equal(legacy.version, ''); assert.equal(legacy.sources[0].commit, ''); assert.equal(legacy.sources[0].repository_stars, null);
});

test('explicit H3 and animation recommendations only apply after a click and preserve existing selections', async () => {
  const catalog = h3SkillCatalog();
  for (const [idea, id] of [
    ['H3 官方分鏡', 'h3-official-storyboard'], ['精緻 3D 動畫故事', 'h3-3d-animation'],
    ['H3 動作表演節拍', 'h3-performance-beats'], ['導演規劃鏡頭銜接與軸線', 'h3-director-continuity'],
    ['H3 多模態參考圖規劃', 'h3-reference-planning'], ['H3 3D 動畫', 'h3-3d-animation'],
  ]) assert.equal(scriptRecommendedWritingSkill({ idea }, catalog).id, id);
  assert.equal(scriptRecommendedWritingSkill({ idea: '家人重逢', style: '3D 動畫', instructions: '使用 H3' }, catalog).id, 'emotional-drama');
  assert.equal(scriptRecommendedWritingSkill({ idea: 'H3 官方分鏡' }, skillCatalog()).id, 'cinematic-story');
  const { ui, elements } = fixture(async () => response({ items: catalog })); ui.bindEvents();
  const original = project({ idea: '3D 動畫故事', writing_skill: 'suspense-horror', style: '我的獨立風格', instructions: '保留我的台詞', character_references: [reference()] });
  ui.writeProject(original); await ui.loadWritingSkills();
  assert.equal(ui.readProject().writing_skill, 'suspense-horror');
  assert.match(elements.get('scriptSkillRecommendation').textContent, /H3 3D 動畫/);
  await elements.get('applyScriptSkillRecommendation').click();
  assert.equal(ui.readProject().writing_skill, 'h3-3d-animation'); assert.equal(ui.state.dirty, true);
  for (const field of ['style', 'instructions', 'content', 'characters', 'idea']) assert.equal(ui.readProject()[field], original[field]);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().character_references)), original.character_references);
  assert.equal(createNewScriptProject().writing_skill, 'cinematic-story');
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
  assert.match(html, /scripts\.css\?v=20261029-prompt-review/);
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

function loraRecommendations(installed = []) {
  const candidates = [
    { name: 'h3studio_custom/camera.safetensors', title: 'Camera Motion', family: null, default_strength: 0.8, trigger: 'camera motion', trigger_required: true, trigger_placement: 'prefix' },
    { name: 'h3studio_custom/physics.safetensors', title: 'Spatial Physics', family: null, default_strength: 0.3 },
    { name: 'h3studio_custom/minimax_h3_flf2v_lora_v1.safetensors', title: '360 Orbit', family: 'fl2va', default_strength: 1 },
    { name: 'h3studio_custom/h3_character_swap_pro4500_1000.safetensors', title: 'Character Swap', family: 'ref2va', default_strength: 1, requires_source_video: true },
    { name: 'h3studio_custom/realism.safetensors', title: 'Realism People', family: 'both', default_strength: 0.8 },
    { name: 'h3studio_custom/3d.safetensors', title: '3D Animation', family: null, default_strength: 0.4, availability: 'gated' },
  ].map(item => ({ known: true, purpose: `用途：${item.title}`, usage: '可自由調整強度', limits: '尚未在本機推論驗證', source_url: `https://example.com/${item.name.split('/').pop()}`, availability: 'missing', ...item, installed: installed.includes(item.name), manual_selectable: installed.includes(item.name) }));
  const base = loraCatalog();
  return { ...base, names: [...base.names, ...installed], items: [...base.items, ...candidates.filter(item => item.installed).map(item => ({ ...item, availability: 'installed' }))], recommended_items: [...base.items.slice(0, 1).map(item => ({ ...item, installed: true, availability: 'installed', manual_selectable: true })), ...candidates.map(item => item.installed ? { ...item, availability: 'installed' } : item)] };
}

test('scripts show every recommendation and protected source even with no selected LoRAs without editing the project', async () => {
  const catalog = loraRecommendations(); catalog.recommended_items[1].title = '<iframe>Camera Motion';
  const { ui, elements, stored } = fixture(async () => response(catalog)); const before = scriptProjectSignature(ui.readProject());
  await ui.loadScriptLoras();
  assert.equal(scriptProjectSignature(ui.readProject()), before); assert.equal(ui.state.dirty, false); assert.equal(stored.size, 0); assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().custom_loras)), []);
  const list = elements.get('scriptLoraRecommendationList'), text = flatText(list);
  for (const title of ['<iframe>Camera Motion', 'Spatial Physics', '360 Orbit', 'Character Swap', 'Realism People', '3D Animation']) assert.ok(text.includes(title));
  assert.match(text, /尚未安裝/); assert.match(text, /受限下載/); assert.match(text, /作者未明確區分 FL2VA／Ref2VA/);
  const links = allNodes(list).filter(node => node.tagName === 'a'); assert.equal(links.length, 7); for (const link of links) { assert.match(link.href, /^https:\/\//); assert.equal(link.rel, 'noopener noreferrer'); }
  assert.equal(elements.get('scriptLoraRecommendations').classes.has('hidden'), false); assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test('scripts disable missing gated FL-only and source-video options and reject programmatic attempts without dirtying rows', async () => {
  const orbit = 'h3studio_custom/minimax_h3_flf2v_lora_v1.safetensors', swap = 'h3studio_custom/h3_character_swap_pro4500_1000.safetensors';
  const { ui, elements } = fixture(async () => response(loraRecommendations([orbit, swap]))); await ui.loadScriptLoras(); ui.addScriptLora(); ui.state.dirty = false;
  const controls = ui.state.loraControls[0], original = JSON.stringify(ui.readProject().custom_loras);
  for (const name of ['h3studio_custom/camera.safetensors', orbit, swap, 'h3studio_custom/3d.safetensors']) {
    assert.equal(controls.select.options.find(option => option.value === name).disabled, true);
    controls.select.value = name; await controls.select.dispatch('change'); assert.equal(JSON.stringify(ui.readProject().custom_loras), original); assert.equal(ui.state.dirty, false);
  }
  assert.match(elements.get('scriptMessage').textContent, /受限下載/);
  assert.match(controls.select.options.find(option => option.value === swap).textContent, /需來源影片，請到影片工作室→角色替換/);
});

test('new script selections use candidate strengths and require manual family confirmation when the author did not specify it', async () => {
  const names = ['h3studio_custom/camera.safetensors', 'h3studio_custom/physics.safetensors', 'h3studio_custom/realism.safetensors'];
  const { ui, elements, stored } = fixture(async () => response(loraRecommendations(names))); await ui.loadScriptLoras(); ui.addScriptLora();
  for (const [index, strength] of [[0, 0.8], [1, 0.3], [2, 0.8]]) {
    ui.state.loraControls[0].select.value = names[index]; await ui.state.loraControls[0].select.dispatch('change');
    assert.equal(ui.readProject().custom_loras[0].strength, strength); assert.equal(ui.readProject().custom_loras[0].enabled, false);
    assert.equal(ui.readProject().custom_loras[0].family, index === 2 ? 'both' : '');
    assert.equal(ui.state.loraControls[0].family.disabled, false);
    if (index < 2) { assert.match(elements.get('scriptLoraCompatibility').textContent, /明確選擇/); ui.state.loraControls[0].family.value = 'ref2va'; await ui.state.loraControls[0].family.dispatch('change'); }
  }
  const controls = ui.state.loraControls[0]; controls.strength.value = '0.65'; await controls.strength.dispatch('input'); controls.enabled.checked = true; await controls.enabled.dispatch('change');
  await ui.loadScriptLoras(); assert.equal(ui.readProject().custom_loras[0].strength, 0.65); assert.equal(ui.readProject().custom_loras[0].enabled, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))[ui.state.newDraftId].project.custom_loras[0].strength, 0.65);
});

test('unavailable saved choices remain editable and saveable but cannot start script video until explicitly disabled', async () => {
  const name = 'h3studio_custom/h3_character_swap_pro4500_1000.safetensors', catalog = loraRecommendations([name]), row = lora({ name, strength: 0.7 });
  assert.match(scriptLoraSelectionError([row], { video: true, catalog }), /需來源影片/);
  assert.equal(scriptLoraSelectionError([row], { catalog }), '');
  for (const change of [{ enabled: false }, { strength: 0 }]) assert.equal(scriptLoraSelectionError([{ ...row, ...change }], { video: true, catalog }), '');
  const { ui, elements, calls } = fixture(async () => response(catalog)); ui.writeProject(project({ custom_loras: [row], character_references: [reference()] })); await ui.loadScriptLoras();
  const before = JSON.stringify(ui.readProject().custom_loras); Object.assign(ui.state, { ready: true, videoReady: true, videoPipelinesSupported: true });
  await ui.startVideoPipeline(); assert.equal(calls.length, 1); assert.equal(JSON.stringify(ui.readProject().custom_loras), before); assert.match(elements.get('scriptMessage').textContent, /需來源影片/);
  ui.state.loraControls[0].enabled.checked = false; await ui.state.loraControls[0].enabled.dispatch('change'); assert.equal(ui.readProject().custom_loras[0].enabled, false);
  const missing = lora({ name: 'h3studio_custom/camera.safetensors' }); assert.match(scriptLoraSelectionError([missing], { video: true, catalog }), /尚未安裝/); assert.equal(scriptLoraSelectionError([missing], { catalog }), '');
});

test('malformed recommendation records cannot inject links or imply installation while legacy inventories remain selectable', async () => {
  const catalog = loraRecommendations(); catalog.recommended_items.push(null, {}, '<img>'); Object.assign(catalog.recommended_items[1], { installed: 'true', default_strength: '0.8', family: 'anything', source_url: 'javascript:evil()' });
  const { ui, elements } = fixture(async () => response(catalog)); await ui.loadScriptLoras(); ui.addScriptLora();
  const metadata = ui.state.loraCatalog.recommended_items.find(item => item.name === 'h3studio_custom/camera.safetensors'); assert.equal(metadata.installed, false); assert.equal(metadata.default_strength, 0.5); assert.equal(metadata.family, null);
  assert.ok(allNodes(elements.get('scriptLoraRecommendationList')).filter(node => node.tagName === 'a').every(link => !link.href.startsWith('javascript:')));
  const legacy = fixture(async () => response({ names: ['legacy.safetensors'] })); await legacy.ui.loadScriptLoras(); legacy.ui.addScriptLora();
  assert.equal(legacy.ui.state.loraControls[0].select.options.find(option => option.value === 'legacy.safetensors').disabled, false);
  legacy.ui.state.loraControls[0].select.value = 'legacy.safetensors'; await legacy.ui.state.loraControls[0].select.dispatch('change'); assert.equal(legacy.ui.readProject().custom_loras[0].strength, 0.5); assert.equal(legacy.ui.readProject().custom_loras[0].family, '');
});

test('save acknowledgement permits only the precise known Swap and Orbit family canonicalizations', () => {
  for (const [name, family] of [['H3STUDIO_CUSTOM/H3_CHARACTER_SWAP_PRO4500_1000.SAFETENSORS', 'ref2va'], ['h3studio_custom/minimax_h3_flf2v_lora_v1.safetensors', 'fl2va']]) {
    assert.equal(scriptLoraSaveConfirmed([lora({ name, family: 'both' })], [lora({ name, family })]), true);
    for (const change of [{ family: family === 'ref2va' ? 'fl2va' : 'ref2va' }, { strength: 0.9 }, { enabled: false }, { name: 'unknown.safetensors' }]) assert.equal(scriptLoraSaveConfirmed([lora({ name, family: 'both' })], [lora({ name, family, ...change })]), false);
  }
  assert.equal(scriptLoraSaveConfirmed([lora({ name: 'unknown.safetensors', family: 'both' })], [lora({ name: 'unknown.safetensors', family: 'fl2va' })]), false);
});

test('Orbit family canonicalization acknowledges a save while preserving stronger edits made during the request', async () => {
  const name = 'h3studio_custom/minimax_h3_flf2v_lora_v1.safetensors', pending = deferred();
  const { ui, stored } = fixture(async () => pending.promise); ui.writeProject(project({ custom_loras: [lora({ name, family: 'both', enabled: false })] })); ui.markDirty(); const saving = ui.saveProject();
  ui.state.loraControls[0].strength.value = '0.85'; await ui.state.loraControls[0].strength.dispatch('input');
  pending.resolve(response(project({ custom_loras: [lora({ name, family: 'fl2va', enabled: false })], revision: 4 })));
  assert.equal(await saving, false); assert.equal(ui.readProject().custom_loras[0].family, 'fl2va'); assert.equal(ui.readProject().custom_loras[0].strength, 0.85); assert.equal(ui.state.dirty, true); assert.equal(ui.state.lorasSupported, true);
  assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.custom_loras[0].strength, 0.85);
});

test('video settings default for legacy projects and valid selections are deep copied signature fields', () => {
  const defaults = { aspect_ratio: '16:9', megapixels: 0.4, output_resolution: 'native' };
  for (const value of [undefined, null, [], false, { aspect_ratio: 'invalid', megapixels: '0.7', output_resolution: '4k' }]) assert.deepEqual(normalizeScriptVideoSettings(value), defaults);
  assert.deepEqual(normalizeScriptProject({}).video_settings, defaults);
  const settings = { aspect_ratio: '9:16', megapixels: 0.7, output_resolution: '1080p' };
  const original = project({ video_settings: settings }), normalized = normalizeScriptProject(original);
  assert.deepEqual(normalized.video_settings, settings); assert.notEqual(normalized.video_settings, settings);
  normalized.video_settings.output_resolution = '720p'; assert.equal(settings.output_resolution, '1080p');
  assert.notEqual(scriptProjectSignature(normalized), scriptProjectSignature(original));
  const outcome = scriptSaveOutcome(project({ ...original, revision: 4 }), normalized, original);
  assert.equal(outcome.dirty, true); assert.equal(outcome.project.video_settings.output_resolution, '720p');
});

test('Fast video model is preserved in settings without rewriting the legacy default shape', () => {
  assert.equal(Object.hasOwn(normalizeScriptVideoSettings({}), 'video_model'), false);
  const settings = { video_model: 'fasth3_omni_nvfp4', aspect_ratio: '9:16', megapixels: 0.7, output_resolution: '1080p' };
  assert.deepEqual(normalizeScriptVideoSettings(settings), settings);
  assert.notEqual(scriptProjectSignature(project({ video_settings: settings })), scriptProjectSignature(project()));
});

test('script model selector persists Fast drafts and keeps H3 LoRA choices available for switching back', async () => {
  const {ui,elements,stored}=fixture();
  ui.writeProject(project({custom_loras:[lora({name:'h3-kept.safetensors'})]}));ui.bindEvents();
  elements.get('scriptVideoModel').value='fasth3_omni_nvfp4';await elements.get('scriptVideoModel').dispatch('change');
  assert.equal(ui.readProject().video_settings.video_model,'fasth3_omni_nvfp4');
  assert.equal(ui.readProject().custom_loras[0].name,'h3-kept.safetensors');
  assert.equal(elements.get('scriptVideoLoraSection').classes.has('hidden'),true);
  const saved=JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project;
  ui.writeProject(saved);assert.equal(elements.get('scriptVideoModel').value,'fasth3_omni_nvfp4');
  elements.get('scriptVideoModel').value='h3';await elements.get('scriptVideoModel').dispatch('change');
  assert.equal(ui.readProject().video_settings.video_model,'h3');
  assert.equal(ui.readProject().custom_loras[0].name,'h3-kept.safetensors');
  assert.equal(elements.get('scriptVideoLoraSection').classes.has('hidden'),false);
});

test('script model gating distinguishes old hosts and each model readiness', () => {
  const {ui}=fixture();const fast={video_settings:{video_model:'fasth3_omni_nvfp4'}};
  assert.match(ui.scriptVideoModelError(fast),/重新啟動/);assert.equal(ui.scriptVideoModelError(project()),'');
  ui.state.videoModelSupported=true;
  ui.state.videoModels=[{id:'h3',ready:false,error:'H3 missing'},{id:'fasth3_omni_nvfp4',ready:true,modes:['r2v'],mode_ready:{r2v:true}}];
  assert.equal(ui.scriptVideoModelError(fast),'');assert.equal(ui.scriptVideoModelError(project()),'H3 missing');
  ui.state.videoModels[1].ready=false;ui.state.videoModels[1].error='Fast unsupported GPU';
  assert.equal(ui.scriptVideoModelError(fast),'Fast unsupported GPU');
});

test('all six aspect ratios and four precision choices show exact H3 dimensions and configured MP4 dimensions', () => {
  const grids = {
    '16:9': [[864,480],[1152,640],[1280,736],[1344,768]],
    '9:16': [[480,864],[640,1152],[736,1280],[768,1344]],
    '1:1': [[640,640],[832,832],[960,960],[992,992]],
    '4:3': [[736,544],[960,736],[1088,832],[1152,864]],
    '3:4': [[544,736],[736,960],[832,1088],[864,1152]],
    '21:9': [[960,416],[1280,544],[1440,608],[1504,640]],
  };
  const hd = { '16:9':[1280,720], '9:16':[720,1280], '1:1':[720,720], '4:3':[960,720], '3:4':[720,960], '21:9':[1680,720] };
  for (const [aspect_ratio, sizes] of Object.entries(grids)) {
    [0.4,0.7,0.9,0.98].forEach((megapixels, index) => {
      for (const output_resolution of ['native','720p','1080p']) {
        const format = scriptVideoDimensions({ aspect_ratio, megapixels, output_resolution });
        assert.deepEqual([format.generation_width,format.generation_height], sizes[index]);
        assert.deepEqual([format.output_width,format.output_height], output_resolution === 'native' ? sizes[index] : hd[aspect_ratio].map(size => output_resolution === '1080p' ? size * 1.5 : size));
        assert.equal(format.fps,24); assert.equal(format.container,'mp4');
      }
    });
  }
});

test('video selectors persist changes in crash drafts and JSON export while previewing actual dimensions', async () => {
  const { ui, elements, stored, downloads } = fixture(); ui.bindEvents(); ui.writeProject(project());
  elements.get('scriptVideoAspectRatio').value = '9:16'; await elements.get('scriptVideoAspectRatio').dispatch('change');
  elements.get('scriptVideoMegapixels').value = '0.9'; await elements.get('scriptVideoMegapixels').dispatch('change');
  elements.get('scriptVideoResolution').value = '1080p'; await elements.get('scriptVideoResolution').dispatch('change');
  const expected = { aspect_ratio:'9:16',megapixels:0.9,output_resolution:'1080p' };
  assert.equal(ui.state.dirty,true); assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().video_settings)),expected);
  assert.equal(elements.get('scriptVideoFormatSummary').textContent,'生成 736 × 1280 → 成片 1080 × 1920 · MP4 · 24 fps');
  assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.video_settings,expected);
  elements.get('scriptExportFormat').value = 'json'; ui.exportDraft();
  assert.deepEqual(JSON.parse(await downloads[0].text()).video_settings,expected);
  assert.match(html,/升尺度不會新增細節/); assert.match(html,/必要黑邊/); assert.doesNotMatch(html,/多模態 Ref2VA · 16:9/);
});

test('video settings survive draft recovery and settings-only drafts remain recoverable', async () => {
  const settings = { aspect_ratio:'1:1',megapixels:0.98,output_resolution:'720p' };
  const draft = { writer:'other-tab',savedAt:20,project:project({video_settings:settings}) };
  const { ui, elements } = fixture(); ui.writeProject(project()); ui.state.recovery=draft; await ui.restoreDraft();
  assert.equal(elements.get('scriptVideoAspectRatio').value,'1:1'); assert.equal(elements.get('scriptVideoMegapixels').value,'0.98');
  assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().video_settings)),settings);
  const settingsOnly={writer:'tab',savedAt:2,project:{video_settings:settings}};
  assert.equal(scriptDraftCandidates([settingsOnly], {}, null).length,1);
});

test('one-click and automatic writing save changed video settings before creating their frozen pipeline', async () => {
  const chosen={aspect_ratio:'3:4',megapixels:0.7,output_resolution:'1080p'};
  for (const action of ['pipeline','screenplay','rewrite']) {
    let saved;
    const pipeline=pipelineRecord({video_settings:chosen});
    const {ui,elements,calls}=fixture(async (url,options) => {
      if(options.method==='PUT') { saved=JSON.parse(options.body); return response(project({...saved,revision:4})); }
      if(url==='/api/scripts/video-pipelines' || url.startsWith('/api/scripts/video-pipelines/')) return response(pipeline);
      return response(historyJob({action,status:'queued',video_pipeline_id:pipeline.id}));
    });
    prepareVideo(ui); ui.bindEvents();
    elements.get('scriptVideoAspectRatio').value=chosen.aspect_ratio; await elements.get('scriptVideoAspectRatio').dispatch('change');
    elements.get('scriptVideoMegapixels').value=String(chosen.megapixels); await elements.get('scriptVideoMegapixels').dispatch('change');
    elements.get('scriptVideoResolution').value=chosen.output_resolution; await elements.get('scriptVideoResolution').dispatch('change');
    if(action==='pipeline') await ui.startVideoPipeline(); else { ui.state.autoVideo=true; await ui.generate(action); }
    assert.equal(calls[0].options.method,'PUT'); assert.deepEqual(saved.video_settings,chosen);
    assert.equal(calls[1].url,action==='pipeline'?'/api/scripts/video-pipelines':'/api/scripts/jobs');
    assert.equal(ui.state.pipeline.id,pipeline.id); assert.equal(elements.get('scriptContent').value,'原始編輯稿');
  }
});

test('video selection changed during saving aborts both pipeline launch routes and preserves newer settings', async () => {
  for(const action of ['pipeline','screenplay']) {
    const pending=deferred(), first={aspect_ratio:'9:16',megapixels:0.7,output_resolution:'720p'};
    const {ui,elements,calls,stored}=fixture(async()=>pending.promise); prepareVideo(ui,{video_settings:first}); ui.markDirty();
    ui.state.autoVideo=true; const starting=action==='pipeline'?ui.startVideoPipeline():ui.generate(action);
    assert.equal(calls[0].options.method,'PUT');
    elements.get('scriptVideoResolution').value='1080p'; ui.markDirty();
    pending.resolve(response(project({duration:15,character_references:[reference()],video_settings:first,revision:4}))); await starting;
    assert.equal(calls.length,1); assert.equal(ui.state.pipeline,null); assert.equal(ui.state.dirty,true);
    assert.equal(ui.readProject().video_settings.output_resolution,'1080p');
    assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.video_settings.output_resolution,'1080p');
  }
});

test('a backend that drops chosen video settings cannot start a pipeline or discard its draft', async () => {
  const chosen={aspect_ratio:'21:9',megapixels:0.98,output_resolution:'1080p'};
  const {ui,elements,calls,stored}=fixture(async()=>response(project({duration:15,character_references:[reference()],revision:4})));
  prepareVideo(ui,{video_settings:chosen}); ui.markDirty(); await ui.startVideoPipeline();
  assert.equal(calls.length,1); assert.equal(ui.state.pipeline,null); assert.equal(ui.state.dirty,true);
  assert.match(elements.get('scriptMessage').textContent,/未完整保存影片格式/);
  assert.deepEqual(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.video_settings,chosen);
});

test('running pipelines and completed history display frozen exact formats while live changes affect future work', async () => {
  const chosen={aspect_ratio:'9:16',megapixels:0.9,output_resolution:'1080p'}, frozen={aspect_ratio:'21:9',megapixels:0.7,output_resolution:'720p'};
  const actual={generation_width:1280,generation_height:544,output_width:1680,output_height:720,fps:24,container:'mp4'};
  const pipeline=pipelineRecord({video_settings:frozen,video_format:actual,snapshot:{video_settings:chosen}});
  const before=JSON.stringify(pipeline),{ui,elements}=fixture(); prepareVideo(ui,{video_settings:chosen}); ui.bindEvents();
  ui.state.pipelines=[pipeline]; ui.state.pipeline=pipeline; ui.renderPipelines();
  assert.match(elements.get('scriptVideoPipelineDetail').textContent,/生成 1280 × 544 → 成片 1680 × 720/);
  assert.equal(elements.get('scriptVideoFormatFutureHint').classes.has('hidden'),false);
  assert.equal(elements.get('scriptVideoAspectRatio').disabled,false);
  elements.get('scriptVideoAspectRatio').value='1:1'; await elements.get('scriptVideoAspectRatio').dispatch('change'); ui.renderPipelines();
  assert.equal(JSON.stringify(pipeline),before); assert.match(elements.get('scriptVideoPipelineDetail').textContent,/生成 1280 × 544 → 成片 1680 × 720/);
  const completed={...pipeline,status:'completed',video_url:`/api/editor/exports/${'d'.repeat(32)}/file`};
  assert.deepEqual(scriptVideoHistoryRecord(completed).video_format,actual);
  ui.receiveVideoHistory({items:[completed]}); ui.selectHistoryVideo(completed.id);
  assert.match(elements.get('scriptHistoryVideoDetail').textContent,/生成 1280 × 544 → 成片 1680 × 720 · MP4 · 24 fps/);
  assert.equal(scriptVideoFormatLabel(scriptPipelineVideoFormat(pipelineRecord())),'生成 864 × 480 → 成片 864 × 480 · MP4 · 24 fps');
});

test('resume uses its frozen format without saving or sending the edited future video settings', async () => {
  const frozen={aspect_ratio:'9:16',megapixels:0.7,output_resolution:'720p'}, edited={aspect_ratio:'1:1',megapixels:0.98,output_resolution:'1080p'};
  const previous=pipelineRecord({status:'interrupted',video_settings:frozen});
  const {ui,elements,calls}=fixture(async()=>response({...previous,status:'running'})); prepareVideo(ui,{video_settings:edited}); ui.markDirty();
  ui.state.pipeline=previous; ui.state.selectedPipelineId=previous.id; await ui.resumePipeline();
  assert.equal(calls.length,1); assert.equal(calls[0].options.body,JSON.stringify({require_prompt_review:true})); assert.ok(calls[0].url.endsWith('/resume'));
  assert.deepEqual(JSON.parse(JSON.stringify(ui.readProject().video_settings)),edited);
  assert.match(elements.get('scriptVideoPipelineDetail').textContent,/生成 640 × 1152 → 成片 720 × 1280/);
});

test('Fast resume uses its frozen model and omits retained H3 adapters from its compatibility gate', async () => {
  const frozen = { aspect_ratio: '9:16', megapixels: 0.4, output_resolution: 'native', video_model: 'fasth3_omni_nvfp4' };
  const previous = pipelineRecord({ status: 'interrupted', snapshot: { project: project({ video_settings: frozen, custom_loras: [lora({ family: 'fl2va' })] }) } });
  delete previous.video_settings;
  const { ui, calls } = fixture(async () => response({ ...previous, status: 'running' }), { lorasSupported: false });
  prepareVideo(ui, { custom_loras: [lora({ family: 'fl2va' })] });
  Object.assign(ui.state, { videoModelSupported: true, videoModels: [{ id: 'h3', ready: false, error: 'H3 absent' }, { id: 'fasth3_omni_nvfp4', ready: true, modes: ['r2v'] }] });
  ui.state.pipeline = previous; ui.state.selectedPipelineId = previous.id;
  await ui.resumePipeline();
  assert.equal(calls.length, 1); assert.ok(calls[0].url.endsWith('/resume')); assert.equal(calls[0].options.body, JSON.stringify({ require_prompt_review: true }));
  assert.equal(ui.readProject().video_settings.video_model, undefined);
  assert.equal(ui.readProject().custom_loras[0].family, 'fl2va');
});

test('queued pipeline and finished video history identify the frozen model after future settings change', () => {
  const { ui, elements } = fixture(); prepareVideo(ui);
  const pipeline = pipelineRecord({ video_settings: { ...project().video_settings, video_model: 'fasth3_omni_nvfp4' } });
  ui.state.pipelines = [pipeline]; ui.state.pipeline = pipeline; ui.renderPipelines();
  assert.match(elements.get('scriptVideoPipelineDetail').textContent, /FastH3-Omni NVFP4/);
  assert.match(flatText(elements.get('scriptVideoPipelineHistory')), /FastH3-Omni NVFP4/);
  assert.equal(ui.readProject().video_settings.video_model, undefined);
  const completed = { ...pipeline, status: 'completed', video_url: `/api/editor/exports/${'d'.repeat(32)}/file` };
  ui.receiveVideoHistory({ items: [completed] }); ui.selectHistoryVideo(completed.id);
  assert.match(elements.get('scriptHistoryVideoDetail').textContent, /FastH3-Omni NVFP4/);
  assert.equal(scriptVideoHistoryRecord(completed).video_model, 'fasth3_omni_nvfp4');
  const legacy = { ...completed }; delete legacy.video_settings;
  assert.equal(scriptVideoHistoryRecord(legacy).video_model, 'h3');
});

test('unknown model drafts and exports preserve identity while saves imports and frozen resumes are blocked', async () => {
  const unknown={...project().video_settings,video_model:'future_video_model'};
  assert.equal(normalizeScriptVideoSettings(unknown).video_model,'future_video_model');
  const {ui,elements,calls,downloads,stored}=fixture();prepareVideo(ui,{video_settings:unknown});ui.bindEvents();
  assert.equal(elements.get('scriptVideoModel').value,'future_video_model');
  assert.ok(elements.get('scriptVideoModel').options.some(option=>option.value==='future_video_model'&&/不支援/.test(option.textContent)));
  assert.match(ui.scriptVideoModelError(ui.readProject()),/尚未支援/);
  ui.markDirty();assert.equal(await ui.saveProject(),false);await ui.startVideoPipeline();
  assert.equal(calls.length,0);assert.equal(JSON.parse(stored.get('h3studio.scripts.draft.this-tab'))['project-a'].project.video_settings.video_model,'future_video_model');
  elements.get('scriptExportFormat').value='json';ui.exportDraft();assert.equal(JSON.parse(await downloads[0].text()).video_settings.video_model,'future_video_model');
  const pipeline=pipelineRecord({status:'interrupted',video_settings:unknown});ui.state.pipeline=pipeline;ui.state.selectedPipelineId=pipeline.id;
  await ui.resumePipeline();assert.equal(calls.length,0);
  ui.state.job={id:'unknown-storyboard',project_id:'project-a',status:'completed',action:'storyboard',content:'{}',input:{project:project({video_settings:unknown})}};
  await ui.importStoryboard();assert.equal(calls.length,0);assert.match(elements.get('scriptMessage').textContent,/未匯入分鏡/);
  elements.get('scriptVideoModel').value='h3';await elements.get('scriptVideoModel').dispatch('change');assert.equal(ui.readProject().video_settings.video_model,'h3');
});
