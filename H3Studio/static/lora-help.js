(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const text = value => typeof value === 'string' ? value.trim() : Array.isArray(value) ? value.filter(item => typeof item === 'string').join('；') : '';
  const families = {fl2va:'FL2VA · 文生／首尾／續接',ref2va:'Ref2VA · 多模態參考／角色替換',both:'FL2VA + Ref2VA'};
  const key = name => String(name || '').replace(/\\/g, '/');
  function safeSource(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value) || /[\u0000-\u0020\\]/.test(value)) return '';
    try {
      const url = new URL(value);
      return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch { return ''; }
  }
  function normalizeItem(raw, name, {model='h3'}={}) {
    raw = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    const known = raw.known === true;
    return {
      ...raw, name, model, known,
      title:text(raw.title) || name,
      purpose:known ? text(raw.purpose) || '用途尚未提供，請查看作者說明。' : '用途尚未登錄；請查看作者說明，不依檔名推測。',
      family:Object.hasOwn(families, raw.family) ? raw.family : null,
      trigger:text(raw.trigger), trigger_required:raw.trigger_required === true,
      usage:text(raw.usage) || '請依作者建議設定強度與描述，先以短片段／單張圖片比較。',
      limits:text(raw.limits) || (known ? '效果仍取決於輸入與模型；不能保證動作或畫面結果。' : '尚未確認基礎模型、觸發詞及相容限制。'),
      source_url:safeSource(raw.source_url),
    };
  }
  function catalog(payload, options={}) {
    const source = payload && typeof payload === 'object' ? payload : {};
    const builtin = (Array.isArray(source.builtin_items) ? source.builtin_items : []).filter(record => record && typeof record.name === 'string');
    const names = [...new Set((Array.isArray(source.names) ? source.names : Array.isArray(source.loras) ? source.loras : []).filter(name => typeof name === 'string' && name && !builtin.some(record => key(record.name) === key(name))))];
    const records = Array.isArray(source.items) ? source.items : Array.isArray(source.lora_items) ? source.lora_items : [];
    return {
      ...source, names,
      items:names.map(name => normalizeItem(records.find(record => record && key(record.name) === key(name)), name, options)),
      builtin_items:builtin.map(record => normalizeItem(record, record.name, options)),
    };
  }
  function item(source, name, options={}) {
    return (source?.items || []).find(record => key(record.name) === key(name)) || normalizeItem(null, name, options);
  }
  function family(record) { return record?.known === true && Object.hasOwn(families, record.family) ? record.family : null; }
  function label(record) { return `${record.title} · ${record.purpose}`; }
  function description(record) {
    const applicability = record.model === 'qwen' ? '圖片模型 LoRA；作者須明確支援 Qwen-Image-2.1。' : family(record) ? families[record.family] : '適用模型尚未登錄；請依作者說明確認。';
    const trigger = record.trigger ? `${record.trigger}${record.trigger_required ? '（啟用時必需，生成時自動加入）' : '（依作者用法選擇；不自動加入）'}` : record.known ? '未指定必需觸發詞。' : '尚未登錄，請查看作者說明。';
    const fields = [['檔案',record.name],['用途',record.purpose],['適用',applicability],['觸發詞',trigger],['用法',record.usage],['限制',record.limits]];
    if (typeof record.installed === 'boolean') fields.push(['安裝狀態',record.installed ? '目前引擎已找到這個版本。' : '目前引擎尚未找到這個版本。']);
    return fields;
  }
  function html(record) {
    const sourceUrl = safeSource(record.source_url);
    const source = sourceUrl ? `<a href="${escape(sourceUrl)}" target="_blank" rel="noopener noreferrer">查看作者／模型來源</a>` : '尚未登錄來源。';
    return `<details class="lora-help"><summary><strong>${escape(record.title)}</strong><span>${escape(record.purpose)}</span></summary><dl>${description(record).map(([label,value])=>`<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join('')}<dt>來源</dt><dd>${source}</dd></dl></details>`;
  }
  function render(element, record) {
    const document = element.ownerDocument || globalThis.document;
    const node = (tag, value='') => { const result=document.createElement(tag); result.textContent=value; return result; };
    const details=node('details'); details.className='lora-help';
    const summary=node('summary'); summary.append(node('strong',record.title),node('span',record.purpose)); details.append(summary);
    const list=node('dl');
    for (const [label,value] of description(record)) list.append(node('dt',label),node('dd',value));
    const source=node('dd');
    const sourceUrl = safeSource(record.source_url);
    if (sourceUrl) { const link=node('a','查看作者／模型來源'); link.href=sourceUrl; link.target='_blank'; link.rel='noopener noreferrer'; source.append(link); }
    else source.textContent='尚未登錄來源。';
    list.append(node('dt','來源'),source); details.append(list); element.replaceChildren(details); return details;
  }
  globalThis.H3LoraHelp = Object.freeze({catalog,item,family,label,html,render,safeSource});
})();
