const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture(initial = null) {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: '', innerHTML: '', disabled: false, dataset: {}, attributes: {},
      classList: { toggle() {} }, setAttribute(name, value) { this.attributes[name] = value; },
      hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    sharedQueueData: initial, sharedQueueLoading: false, annotations: [],
    $: element, $$: () => context.annotations, AbortController, setTimeout, clearTimeout,
    async api() { return emptyQueue(); },
  });
  for (const name of ['escapeHtml', 'statusLabel', 'queueJobPresentation', 'queueJobAttributes', 'queueJobBadgeHtml', 'queueProgressContent', 'queueJobProgressHtml', 'refreshQueueJobAnnotations', 'queueRowsHtml', 'renderSharedQueue', 'loadSharedQueue', 'formatExecutionTime', 'jobExecutionSeconds', 'jobExecutionLabel', 'batchSegmentsHtml']) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, `function ${name} is present`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
  return { context, element, run: expression => vm.runInContext(expression, context) };
}

const emptyQueue = () => ({ available: true, running_count: 0, pending_count: 0, local_waiting_count: 0, updated_at: '2026-09-14T08:00:00Z', running: [], pending: [], local_waiting: [], local_active: [], jobs: {} });
const waitingQueue = () => ({ ...emptyQueue(), running_count: 1, pending_count: 3, local_waiting_count: 1, jobs: {
  video: { job_id: 'video', kind: 'video', phase: 'engine_waiting', position: 2, ahead_count: 2, progress: 5 },
  local: { job_id: 'local', kind: 'video', phase: 'local_waiting', position: null, progress: 0 },
} });

function useFetchApi(context, response) {
  context.fetch = async () => response;
  const start = source.indexOf('async function api(');
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
}

test('engine waiting overrides misleading running status and percentages without mutating the job', () => {
  const { context, run } = fixture(waitingQueue());
  context.job = { id: 'video', status: 'running', progress: 5, current_node: '生成中' };
  const presentation = run('queueJobPresentation(job)');
  assert.equal(presentation.label, '排隊第 2 位');
  assert.equal(presentation.detail, '前方 2 筆工作');
  assert.equal(presentation.progress, null);
  assert.doesNotMatch(run('queueProgressContent(queueJobPresentation(job))'), /5%|生成中/);
  assert.equal(context.job.status, 'running');
  assert.equal(context.job.progress, 5);
});

test('the next engine job shows pending rank 1 and running work ahead separately', () => {
  const { context, run } = fixture(waitingQueue());
  Object.assign(context.sharedQueueData.jobs.video, { position: 1, ahead_count: 1 });
  const result = run('queueJobPresentation({id:"video", status:"running"})');
  assert.equal(result.label, '排隊第 1 位');
  assert.match(result.detail, /前方 1 筆工作 · 下一個輪到/);
});

test('local, preparation, finishing and local voice work never invent a global rank or GPU progress', () => {
  const { context, run } = fixture(emptyQueue());
  for (const [phase, label] of [['local_waiting', '本機待送出'], ['preparing', '準備素材'], ['finishing', '完成後處理'], ['local_processing', '本機處理中'], ['unknown', '確認狀態中']]) {
    context.sharedQueueData.jobs.video = { phase, position: 9, progress: 25 };
    const result = run('queueJobPresentation({id:"video",status:"running",progress:5})');
    assert.equal(result.label, label);
    assert.equal(result.progress, null);
    assert.doesNotMatch(result.label, /第 9/);
  }
});

test('confirmed engine running restores real progress and completed job ignores old queue entry', () => {
  const { context, run } = fixture(waitingQueue());
  context.sharedQueueData.jobs.video = { phase: 'engine_running', progress: 42.4 };
  let result = run('queueJobPresentation({id:"video",status:"running",progress:5,current_node:"採樣"})');
  assert.equal(result.label, '引擎生成中');
  assert.equal(result.progress, 42);
  assert.equal(result.detail, '採樣');
  result = run('queueJobPresentation({id:"video",status:"completed",progress:100})');
  assert.equal(result.label, '已完成');
  assert.equal(result.progress, 100);
});

test('missing or invalid progress stays unknown, and actual percent is bounded', () => {
  const { context, run } = fixture(emptyQueue());
  for (const value of [null, undefined, 'bad']) {
    context.sharedQueueData.jobs.video = { phase: 'engine_running', progress: value };
    assert.equal(run('queueJobPresentation({id:"video",status:"running"}).progress'), null);
  }
  context.sharedQueueData.jobs.video = { phase: 'engine_running', progress: 105 };
  assert.equal(run('queueJobPresentation({id:"video",status:"running"}).progress'), 100);
});

test('transport failure clears all engine ranks and marks preserved local information stale', async () => {
  const { context, element, run } = fixture(waitingQueue());
  context.api = async () => { throw Error('offline'); };
  await run('loadSharedQueue()');
  assert.equal(context.sharedQueueData.available, false);
  assert.equal(context.sharedQueueData.jobs.video, undefined);
  assert.equal(context.sharedQueueData.jobs.local.phase, 'local_waiting');
  assert.equal(context.sharedQueueData.stale, true);
  assert.equal(element('#queueRunningCount').textContent, '—');
  assert.equal(element('#queuePendingCount').textContent, '—');
  assert.equal(element('#queueLocalCount').textContent, '1');
  assert.match(element('#queueUpdatedAt').textContent, /上次取得/);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /沒有.*工作/);
  assert.equal(run('queueJobPresentation({id:"video",status:"running",progress:5}).progress'), null);
  assert.equal(element('#refreshSharedQueue').disabled, false);
});

for (const [format, json] of [
  ['JSON', async () => ({ error: 'Not Found' })],
  ['plain text', async () => { throw new SyntaxError('404: Not Found'); }],
]) {
  test(`old backend ${format} 404 identifies the restart requirement and clears misleading engine information`, async () => {
    const { context, element, run } = fixture(waitingQueue());
    useFetchApi(context, { ok: false, status: 404, json });
    await assert.rejects(run('api("/api/queue")'), error => error.status === 404);
    await run('loadSharedQueue()');
    assert.equal(context.sharedQueueData.available, false);
    assert.equal(context.sharedQueueData.error_code, 'studio_restart_required');
    assert.equal(context.sharedQueueData.jobs.video, undefined);
    assert.equal(context.sharedQueueData.running.length, 0);
    assert.equal(context.sharedQueueData.pending.length, 0);
    assert.match(element('#queueStatusMessage').textContent, /(?:重新啟動|重啟) Studio/);
    assert.equal(element('#queueRunningCount').textContent, '—');
    assert.equal(element('#queuePendingCount').textContent, '—');
    assert.equal(element('#queueLocalCount').textContent, '1');
    assert.match(element('#queueUpdatedAt').textContent, /上次取得/);
    const active = run('queueJobPresentation({id:"video",status:"running",progress:5,current_node:"生成中"})');
    assert.equal(active.label, 'Studio 待重啟');
    assert.match(active.detail, /工作完成後.*重啟 Studio/);
    assert.equal(active.progress, null);
    assert.doesNotMatch(run('queueProgressContent(queueJobPresentation({id:"video",status:"running",progress:5}))'), /5%|前方 \d|排隊第/);
    assert.equal(run('queueJobPresentation({id:"video",status:"completed",progress:100}).label'), '已完成');
    assert.equal(context.sharedQueueLoading, false);
    assert.equal(element('#refreshSharedQueue').disabled, false);
  });
}

test('successful queue response clears the restart hint and restores the confirmed engine state', async () => {
  const { context, element, run } = fixture();
  useFetchApi(context, { ok: false, status: 404, json: async () => ({ error: 'Not Found' }) });
  await run('loadSharedQueue()');
  assert.equal(context.sharedQueueData.error_code, 'studio_restart_required');
  assert.equal(element('#queueLocalCount').textContent, '—');
  context.api = async () => ({ ...emptyQueue(), running_count: 1, jobs: { video: { phase: 'engine_running', progress: 42 } } });
  await run('loadSharedQueue()');
  assert.notEqual(context.sharedQueueData.error_code, 'studio_restart_required');
  assert.equal(context.sharedQueueData.stale, false);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /重啟|重新啟動/);
  const active = run('queueJobPresentation({id:"video",status:"running",progress:5})');
  assert.equal(active.label, '引擎生成中');
  assert.equal(active.progress, 42);
});

test('HTTP status takes precedence over an unrelated error message mentioning HTTP 404', async () => {
  const { context, element, run } = fixture(waitingQueue());
  useFetchApi(context, { ok: false, status: 403, json: async () => ({ error: 'Access denied; upstream previously returned HTTP 404' }) });
  await run('loadSharedQueue()');
  assert.equal(context.sharedQueueData.available, false);
  assert.notEqual(context.sharedQueueData.error_code, 'studio_restart_required');
  assert.equal(context.sharedQueueData.jobs.video, undefined);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /重啟|重新啟動/);
  const active = run('queueJobPresentation({id:"video",status:"running",progress:5})');
  assert.notEqual(active.label, 'Studio 待重啟');
  assert.equal(active.progress, null);
});

test('engine-unavailable response retains fresh local work while suppressing all engine rows', async () => {
  const { context, element, run } = fixture(waitingQueue());
  context.api = async () => ({ ...emptyQueue(), available: false, running_count: null, pending_count: null, local_waiting_count: 1, local_waiting: [{ title: '本機影片', phase: 'local_waiting', kind: 'video' }], error: '無法連線引擎' });
  await run('loadSharedQueue()');
  assert.equal(context.sharedQueueData.stale, false);
  assert.equal(element('#queuePendingCount').textContent, '—');
  assert.equal(element('#queueLocalCount').textContent, '1');
  assert.match(element('#queueWorkList').innerHTML, /本機影片/);
  assert.match(element('#queueStatusMessage').textContent, /無法連線引擎/);
});

test('polling coalesces simultaneous refreshes and recovers after the response', async () => {
  const { context, element, run } = fixture();
  let resolve;
  let calls = 0;
  context.api = async () => { calls++; return new Promise(done => { resolve = done; }); };
  const first = run('loadSharedQueue()');
  await run('loadSharedQueue()');
  assert.equal(calls, 1);
  assert.equal(element('#refreshSharedQueue').disabled, true);
  resolve(emptyQueue());
  await first;
  assert.equal(context.sharedQueueLoading, false);
  assert.equal(element('#sharedQueue').attributes['aria-busy'], 'false');
  assert.equal(element('#queueRunningCount').textContent, '0');
});

test('incomplete success response is unavailable instead of a false empty engine', async () => {
  const { context, element, run } = fixture();
  context.api = async () => ({ available: true, jobs: {}, running_count: null, pending_count: null });
  await run('loadSharedQueue()');
  assert.equal(element('#queueRunningCount').textContent, '—');
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /沒有.*工作/);
});

test('local active processing remains visible when engine has no queued work', () => {
  const { context, element, run } = fixture(emptyQueue());
  context.sharedQueueData.local_active = [{ kind: 'voice', title: '角色語音', phase: 'local_processing', owner: '本機' }];
  run('renderSharedQueue()');
  assert.match(element('#queueStatusMessage').textContent, /本機另有 1 筆正在準備或處理/);
  assert.match(element('#queueWorkList').innerHTML, /角色語音/);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /GPU.*閒置|全部.*空閒/);
});

test('unknown in-flight jobs are distinguished from confirmed local processing', () => {
  const { context, element, run } = fixture(emptyQueue());
  context.sharedQueueData.local_active = [{ kind: 'video', title: '待確認工作', phase: 'unknown' }];
  run('renderSharedQueue()');
  assert.match(element('#queueStatusMessage').textContent, /1 筆工作正在確認引擎狀態/);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /正在準備或處理/);
});

test('active time is explicitly waiting-inclusive while completed generation time is unchanged', () => {
  const { context, run } = fixture(waitingQueue());
  context.job = { id: 'video', status: 'running', created_at: new Date(Date.now() - 60000).toISOString(), generation_started_at: new Date(Date.now() - 50000).toISOString() };
  const label = run('jobExecutionLabel(job)');
  assert.match(label, /工作經過 1 分/);
  assert.match(label, /含等待與處理/);
  assert.doesNotMatch(label, /已執行|生成耗時/);
  context.job.status = 'completed';
  context.job.execution_seconds = 90;
  assert.equal(run('jobExecutionLabel(job)'), '生成耗時 1 分 30 秒');
});

test('long replacement child segment uses its actual engine rank with no fake percentage', () => {
  const { context, run } = fixture(waitingQueue());
  context.job = { batch_type: 'replace_long', segments: [{ index: 1, child_job_id: 'video', status: 'running', progress: 5, core_start: 0, core_end: 5 }] };
  const html = run('batchSegmentsHtml(job)');
  assert.match(html, /排隊第 2 位/);
  assert.match(html, /前方 2 筆工作/);
  assert.doesNotMatch(html, /5%/);
});

test('queue titles, owner labels and job nodes are escaped as untrusted text', () => {
  const { context, run } = fixture(emptyQueue());
  context.rows = [{ title: '<script>alert(1)</script>', owner: '<img src=x>', kind: 'video', phase: 'engine_waiting', position: 1, ahead_count: 1 }];
  const rows = run('queueRowsHtml(rows, "pending")');
  assert.doesNotMatch(rows, /<script>|<img/);
  assert.match(rows, /&lt;script&gt;/);
  context.job = { id: '" onmouseover="bad', status: 'running', current_node: '<svg>' };
  assert.doesNotMatch(run('queueJobBadgeHtml(job)'), /data-queue-job="" onmouseover/);
  assert.match(run('queueJobAttributes(job)'), /&quot;/);
});

test('rank refresh changes annotations without touching other card contents', () => {
  const { context, element, run } = fixture(waitingQueue());
  const badge = element('badge');
  badge.dataset = { queueJob: 'video', queueStatus: 'running', queueProgress: '5', queueNode: '生成中' };
  badge.attributes['data-queue-badge'] = '';
  const progress = element('progress');
  progress.dataset = badge.dataset;
  progress.attributes['data-queue-progress-panel'] = '';
  const card = element('card');
  card.innerHTML = '<video controls></video><textarea>未儲存內容</textarea>';
  context.annotations = [badge, progress];
  run('refreshQueueJobAnnotations()');
  assert.equal(badge.textContent, '排隊第 2 位');
  assert.doesNotMatch(progress.innerHTML, /5%/);
  context.sharedQueueData.jobs.video = { phase: 'engine_running', progress: 60 };
  run('refreshQueueJobAnnotations()');
  assert.equal(badge.textContent, '引擎生成中');
  assert.match(progress.innerHTML, /60%/);
  assert.equal(card.innerHTML, '<video controls></video><textarea>未儲存內容</textarea>');
});

test('remote voice phases show host state and progress without inventing an engine rank', () => {
  const { context, run } = fixture(emptyQueue());
  context.job = { id: 'voice', engine_mode: 'remote', status: 'running', progress: 35, current_node: '主機回報的語音進度' };
  for (const [phase, label, expectedProgress, waiting] of [
    ['remote_waiting', '遠端排隊中', null, true],
    ['remote_preparing', '遠端準備中', null, false],
    ['remote_processing', '遠端生成中', 61, false],
  ]) {
    context.sharedQueueData.jobs.voice = { phase, position: 7, ahead_count: 8, progress: 60.7 };
    const result = run('queueJobPresentation(job)');
    assert.equal(result.label, label);
    assert.equal(result.progress, expectedProgress);
    assert.equal(result.waiting, waiting);
    assert.equal(result.detail, context.job.current_node);
    assert.doesNotMatch(result.label + result.detail, /本機|第 7|前方 8/);
  }
});

test('remote job metadata keeps its host status when queue data is missing, offline, or unknown', () => {
  const { context, run } = fixture();
  for (const queue of [
    null,
    { available: false, jobs: {} },
    { available: false, error_code: 'studio_restart_required', jobs: {} },
    { ...emptyQueue(), jobs: { voice: { phase: 'unknown' } } },
    { ...emptyQueue(), jobs: { voice: { phase: 'local_processing' } } },
  ]) {
    context.sharedQueueData = queue;
    for (const [status, label, progress] of [
      ['queued', '遠端排隊中', null],
      ['preparing', '遠端準備中', null],
      ['running', '遠端生成中', 42],
    ]) {
      context.job = { id: 'voice', engine_mode: 'remote', status, progress: 41.7 };
      const result = run('queueJobPresentation(job)');
      assert.equal(result.label, label);
      assert.equal(result.progress, progress);
      assert.doesNotMatch(result.label + result.detail, /本機|引擎順位|排隊第/);
    }
  }
});

test('remote processing progress remains bounded and unknown host percentages stay unmeasured', () => {
  const { context, run } = fixture();
  for (const [value, expected] of [[null, null], [undefined, null], ['', null], ['bad', null], [-10, 0], [101, 100]]) {
    context.job = { id: 'voice', engine_mode: 'remote', status: 'running', progress: value };
    assert.equal(run('queueJobPresentation(job).progress'), expected);
  }
  context.job.status = 'completed';
  context.job.progress = 100;
  assert.equal(run('queueJobPresentation(job).label'), '已完成');
  assert.equal(run('queueJobPresentation(job).progress'), 100);
});

test('remote metadata survives HTML annotations and refresh even before queue API is available', () => {
  const { context, element, run } = fixture();
  context.job = { id: 'voice', engine_mode: 'remote', status: 'running', progress: 37, current_node: '遠端主機' };
  assert.match(run('queueJobAttributes(job)'), /data-queue-engine-mode="remote"/);
  assert.match(run('queueJobBadgeHtml(job)'), /遠端生成中/);
  const badge = element('remote-badge');
  badge.dataset = { queueJob: 'voice', queueStatus: 'running', queueProgress: '37', queueNode: '遠端主機', queueEngineMode: 'remote' };
  badge.attributes['data-queue-badge'] = '';
  const progress = element('remote-progress');
  progress.dataset = badge.dataset;
  progress.attributes['data-queue-progress-panel'] = '';
  context.annotations = [badge, progress];
  run('refreshQueueJobAnnotations()');
  assert.equal(badge.textContent, '遠端生成中');
  assert.match(progress.innerHTML, /37%/);
  assert.doesNotMatch(progress.innerHTML, /本機/);
  badge.dataset.queueStatus = 'queued';
  run('refreshQueueJobAnnotations()');
  assert.equal(badge.textContent, '遠端排隊中');
  assert.doesNotMatch(progress.innerHTML, /37%/);
});

test('remote queue rows use remote labels and only host processing reports a percentage', () => {
  const { context, run } = fixture(emptyQueue());
  context.rows = [
    { title: '遠端待處理', phase: 'remote_waiting', kind: 'voice', position: 3, progress: 9 },
    { title: '遠端準備', phase: 'remote_preparing', kind: 'voice', progress: 15 },
    { title: '遠端生成', phase: 'remote_processing', kind: 'voice', progress: 55.8 },
  ];
  const html = run('queueRowsHtml(rows, "remote")');
  for (const label of ['遠端排隊中', '遠端準備中', '遠端生成中']) assert.match(html, new RegExp(label));
  assert.match(html, /56%/);
  assert.doesNotMatch(html, /本機|第 3 位|9%|15%/);
});

test('remote voices in local_active are grouped as remote and excluded from local processing counts', () => {
  const { context, element, run } = fixture(emptyQueue());
  context.sharedQueueData.local_active = [
    { kind: 'voice', title: '遠端角色語音', phase: 'remote_processing', progress: 75, owner: '我的工作' },
  ];
  run('renderSharedQueue()');
  assert.match(element('#queueStatusMessage').textContent, /遠端另有 1 筆/);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /本機另有/);
  assert.match(element('#queueWorkList').innerHTML, /遠端工作狀態/);
  assert.match(element('#queueWorkList').innerHTML, /遠端生成中.*遠端角色語音.*75%/);
  assert.doesNotMatch(element('#queueWorkList').innerHTML, /本機工作狀態/);
  context.sharedQueueData.local_active.push({ kind: 'music', title: '本機音樂素材', phase: 'preparing' });
  run('renderSharedQueue()');
  assert.match(element('#queueStatusMessage').textContent, /本機另有 1 筆/);
  assert.match(element('#queueStatusMessage').textContent, /遠端另有 1 筆/);
  assert.match(element('#queueWorkList').innerHTML, /本機工作狀態/);
});

test('a failed queue refresh keeps remote voices out of stale local processing descriptions', async () => {
  const { context, element, run } = fixture(emptyQueue());
  context.sharedQueueData.local_active = [{ kind: 'voice', title: '遠端角色語音', phase: 'remote_waiting' }];
  context.api = async () => { throw Error('offline'); };
  await run('loadSharedQueue()');
  assert.match(element('#queueStatusMessage').textContent, /遠端另有 1 筆/);
  assert.match(element('#queueStatusMessage').textContent, /遠端資訊為上次取得/);
  assert.doesNotMatch(element('#queueStatusMessage').textContent, /本機另有|本機資訊/);
  assert.match(element('#queueWorkList').innerHTML, /遠端工作狀態/);
});
