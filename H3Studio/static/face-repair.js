/* Separate dialog state keeps repair settings out of the user's generation draft. */
(() => {
  let jobId = null, reference = null, ready = false, busy = false, version = 0, returnFocus = null;
  const modal = document.querySelector('#faceRepairModal');
  const el = id => document.querySelector(`#faceRepair${id}`);
  const update = () => { el('Submit').disabled = busy || !ready || !reference; };
  const close = () => {
    if (busy) return;
    version++;
    modal.classList.add('hidden');
    el('Video').pause();
    returnFocus?.focus();
  };
  async function check() {
    const attempt = version;
    ready = false; update();
    el('Status').textContent = '正在檢查運算引擎的臉部修復節點…';
    try {
      const result = await api('/api/face-repair/status');
      if (attempt !== version) return;
      ready = result.ready === true;
      el('Status').textContent = ready ? '修復節點已就緒。修復會加入目前引擎的工作佇列。' : result.error;
    } catch (error) {
      if (attempt !== version) return;
      el('Status').textContent = error.status === 404
        ? 'Studio 後端尚未更新，請等工作完成後重新啟動 Studio。'
        : `檢查失敗：${error.message}`;
    }
    update();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-face-repair]');
    if (!button || busy) return;
    event.preventDefault();
    version++;
    returnFocus = button;
    jobId = button.dataset.faceRepair;
    reference = null;
    el('Image').value = '';
    el('ImageName').textContent = '請選擇清楚的單人臉部圖片，用來維持修復後的外觀。';
    el('Start').value = 0;
    el('Duration').value = 5;
    el('Video').src = `/api/jobs/${jobId}/video`;
    modal.classList.remove('hidden');
    el('Start').focus();
    check();
  });
  modal.querySelectorAll('[data-close-face-repair]').forEach(button => button.addEventListener('click', close));
  modal.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
    if (event.key === 'Tab') {
      const focusable = [...modal.querySelectorAll('button,input,select,video[controls]')].filter(item => !item.disabled && !item.hidden);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  el('Check').addEventListener('click', check);
  el('Start').addEventListener('change', () => { el('Video').currentTime = Math.max(0, Number(el('Start').value) || 0); });
  el('Image').addEventListener('change', async event => {
    const file = event.target.files[0];
    const attempt = ++version;
    reference = null; update();
    if (!file) return;
    el('ImageName').textContent = '正在上傳參考圖…';
    try {
      const asset = await uploadFile(file, 'face-repair-reference');
      if (attempt !== version) return;
      reference = asset;
      el('ImageName').textContent = asset.name || file.name;
    } catch (error) { if (attempt === version) el('ImageName').textContent = error.message; }
    update();
    if (!ready) check();
  });
  el('Submit').addEventListener('click', async () => {
    if (busy || !ready || !reference || !jobId) return;
    busy = true; update();
    el('Status').textContent = '正在準備來源片段與建立修復工作…';
    try {
      const job = await api(`/api/jobs/${jobId}/face-repair`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reference_image_asset_id: reference.id, start: Number(el('Start').value),
          duration: Number(el('Duration').value), strength: Number(el('Strength').value),
          selection: el('Selection').value, canvas: Number(el('Canvas').value), seed: Number(el('Seed').value)}),
      });
      busy = false; close();
      toast(`臉部修復 ${job.id.slice(0, 8)} 已加入佇列，原影片保留。修復片段會列在快速生成工作。`);
      await loadJobs(true);
      await loadSharedQueue();
    } catch (error) { el('Status').textContent = error.message; }
    finally { busy = false; update(); }
  });
})();
