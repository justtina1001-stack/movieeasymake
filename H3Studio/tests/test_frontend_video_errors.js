const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function fixture() {
  const context = vm.createContext({});
  for (const name of ['escapeHtml', 'videoJobErrorPresentation', 'videoJobErrorHtml']) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  }
  return context;
}

test('new GPU memory errors show the server advice and keep technical context folded', () => {
  const ui = fixture();
  const job = {error_code:'gpu_out_of_memory', error:'顯示卡記憶體不足。請降低解析度。', error_detail:'CUDA out of memory. Requested 3.14 GiB.', error_node:{id:'14',type:'SamplerCustomAdvanced'}};
  const html = ui.videoJobErrorHtml(job);
  assert.match(html, /<p>顯示卡記憶體不足。請降低解析度。<\/p>/);
  assert.match(html, /<details class="job-error-technical"><summary>查看技術原文<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /失敗步驟：SamplerCustomAdvanced · 節點 14/);
  assert.match(html, /<pre>CUDA out of memory\. Requested 3\.14 GiB\.<\/pre>/);
});

test('the existing device allocation error is recognized without rewriting stored job data', () => {
  const ui = fixture();
  const job = {error:'Allocation on device 0 would exceed allowed memory. (out of memory)\nCurrently allocated : 12.40 GiB\nTIPS: batch_size'};
  const original = JSON.stringify(job);
  const view = ui.videoJobErrorPresentation(job);
  assert.equal(view.oom, true);
  assert.match(view.message, /顯示卡記憶體不足/);
  assert.match(view.message, /確認生成稿後重試/);
  assert.doesNotMatch(view.message, /batch_size/);
  assert.equal(view.detail, job.error);
  assert.equal(JSON.stringify(job), original);
});

test('legacy CUDA and torch OOM forms get memory recovery guidance', () => {
  const ui = fixture();
  for (const error of ['CUDA out of memory', 'CUDA error: out of memory', 'torch.OutOfMemoryError: allocation failed', 'torch.cuda.OutOfMemoryError: failed']) {
    assert.equal(ui.videoJobErrorPresentation({error}).oom, true, error);
  }
});

test('CPU allocator failures stay CPU errors even when CUDA environment details are appended', () => {
  const ui = fixture();
  for (const error of ['DefaultCPUAllocator: out of memory (CUDA available)', 'CPUAllocator: out of memory; GPU memory available', 'CPU out of memory (CUDA available)', 'CPU not enough memory; GPU active']) {
    const view = ui.videoJobErrorPresentation({error});
    assert.equal(view.oom, false, error);
    assert.equal(view.message, error);
  }
});

test('device allocation limits are recognized without an appended out of memory suffix', () => {
  const ui = fixture();
  const error = 'Allocation on device 0 would exceed allowed memory.';
  assert.equal(ui.videoJobErrorPresentation({error}).oom, true);
  assert.match(ui.videoJobErrorHtml({error}), /顯示卡記憶體不足/);
});

test('ordinary errors and CPU memory failures keep their existing message', () => {
  const ui = fixture();
  for (const error of ['No room to save output', 'CUDA kernel launch failed', 'CPU out of memory', 'RuntimeError: image dimensions not supported', 'bloom effect unavailable']) {
    const view = ui.videoJobErrorPresentation({error});
    assert.equal(view.oom, false, error);
    assert.equal(view.message, error);
    assert.equal(ui.videoJobErrorHtml({error}), `<div class="job-error">${error}</div>`);
  }
  assert.equal(ui.videoJobErrorHtml({}), '');
});

test('diagnostic messages, node names and raw errors are escaped as text', () => {
  const ui = fixture();
  const html = ui.videoJobErrorHtml({error_code:'gpu_out_of_memory',error:'建議 <img src=x onerror=alert(1)>',error_detail:'CUDA out of memory <script>alert(2)</script>',error_node:{title:'<svg onload=alert(3)>',id:'"><img src=x>'}});
  assert.doesNotMatch(html, /<img|<script|<svg/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
  assert.match(html, /&lt;svg onload=alert\(3\)&gt;/);
  assert.match(ui.videoJobErrorHtml({error:'<script>ordinary</script>'}), /&lt;script&gt;ordinary&lt;\/script&gt;/);
});

test('memory errors without a separate original or node still present safe guidance', () => {
  const ui = fixture();
  const html = ui.videoJobErrorHtml({error_code:'gpu_out_of_memory',error:'CUDA out of memory'});
  assert.match(html, /這次影片未完成/);
  assert.doesNotMatch(html, /失敗步驟：/);
});

test('quick video and short film cards use the same escaped error renderer', () => {
  assert.equal((source.match(/\$\{videoJobErrorHtml\(job\)\}/g) || []).length, 2);
});
