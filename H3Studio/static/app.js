const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const defaultState = {
  mode: "t2v",
  firstImage: null,
  lastImage: null,
  references: [],
  popupReferences: [
    { id: "popup-background", alias: "背景圖", type: "background", description: "全程固定不動的遊戲底板環境", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" },
    { id: "popup-panel", alias: "面板", type: "object", description: "在背景圖上方進場、表演與退場的彈窗面板", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" },
  ],
  popupPanel: { safeDefaultsApplied: false },
  storyboards: [],
  replacement: {
    alias: "新角色",
    target: "動態參考影片中的主要角色",
    description: "",
    images: [],
    video: null,
    videoUseAudio: false,
    defaultPrompt: "",
    safeDefaultsApplied: false,
  },
  symbolLoop: {
    sourceAsset: null,
    preparedAsset: null,
    sourceName: "",
    sourceInfo: null,
  },
  continuation: {
    sourceJobId: null,
    sourceAsset: null,
    lastFrame: null,
    sourceName: "",
    sourceInfo: null,
    merge: true,
    audio: "both",
  },
};

let state = loadState();
let lastJobsSignature = "";
let jobPage = 1;
let jobTotalPages = 1;
let jobSearch = "";
let lastJobOptionsLoad = 0;
const expandedJobIds = new Set();
let toastTimer;
let engineStartingAt = 0;
let keyframePrepareVersion = 0;
let connectionSettings = null;
let installerPreflightData = null;
let lastInstallerStatus = "idle";

const modeLabels = { t2v: "文生影片", fl2va: "首尾圖片", r2v: "多模態參考", replace: "角色替換", symbol_loop: "圖騰循環", extend: "續接影片", popup_panel: "彈窗面板動畫" };
const promptGuideModeAdvice = {
  t2v: ["文生影片 · T2VA", "不使用參考圖片，直接描述完整的畫面、動作、鏡頭、對白與聲音時間線。"],
  r2v: ["多模態參考 · Ref2VA", "在敘述中直接使用素材名稱；工具會自動建立 Subject、Picture、Video 與 Audio 對應。"],
  replace: ["角色替換 · Ref2VA 影片編輯", "說明新角色要接手的原角色位置與表演，保留原場景、鏡頭、道具及其他人物。"],
  symbol_loop: ["圖騰循環 · FL2VA", "同一張擴邊圖片作為首尾錨點；只完成一個動作週期並平順回到起始狀態。"],
  extend: ["續接影片 · I2VA／Ref2VA", "只用尾幀時延續姿勢與動量；保留原影片作參考時則同時描述 video continuation 關係。"],
  popup_panel: ["彈窗面板 · Ref2VA", "背景全程固定，只讓面板、分數、按鈕、壓暗層、裝飾與特效依時間表演。"],
};
const keyframeFitHints = {
  contain: ["完整擴邊", "保留原圖比例，以邊緣顏色補足輸出畫布。"],
  cover: ["置中裁切", "保留原圖比例並填滿畫面，超出輸出比例的部分會被裁切。"],
  stretch: ["強制拉伸", "直接縮放到輸出尺寸，可能讓人物或物件變形。"],
};
const typeLabels = { character: "角色", creature: "動物／怪物", object: "物件", background: "背景", style: "美術風格", motion: "動作／運鏡", effect: "特效表現" };
const motionLabels = { natural: "自然動態", impact: "打擊與碰撞", action: "動作場面", dance: "舞蹈與節奏", chase: "追逐與奔跑", vfx: "特效與能量", none: "不自動強化" };
const motionHints = {
  natural: "自然力學、重心轉移與連續收勢",
  impact: "蓄力、接觸、受力、回彈與短促鏡頭反應",
  action: "連續全身動作、攻防轉換與空間方向",
  dance: "節拍、腳步接觸、軀幹與四肢協調",
  chase: "奔跑週期、速度、視差與追隨運鏡",
  vfx: "啟動、增長、爆發、消散與環境回饋",
  none: "完全依照手動提示詞，不加入動態指令",
};

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem("h3studio-state-v1"));
    const restored = { ...structuredClone(defaultState), ...saved };
    restored.references = (restored.references || []).map(item => ({
      images: [], audio: null, video: null, videoUseAudio: false, voiceMode: "timbre", description: "", ...item,
    }));
    restored.popupReferences = (restored.popupReferences || structuredClone(defaultState.popupReferences)).map(item => ({
      images: [], audio: null, video: null, videoUseAudio: false, voiceMode: "timbre", description: "", ...item,
    }));
    restored.popupPanel = { ...structuredClone(defaultState.popupPanel), ...(restored.popupPanel || {}) };
    restored.storyboards = (restored.storyboards || []).map(shot => ({ motionBeats: "", effects: "", ...shot }));
    restored.replacement = { ...structuredClone(defaultState.replacement), ...(restored.replacement || {}) };
    restored.symbolLoop = { ...structuredClone(defaultState.symbolLoop), ...(restored.symbolLoop || {}) };
    restored.continuation = { ...structuredClone(defaultState.continuation), ...(restored.continuation || {}) };
    return restored;
  } catch {
    return structuredClone(defaultState);
  }
}

function saveState() {
  localStorage.setItem("h3studio-state-v1", JSON.stringify(state));
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function uid() {
  return crypto.randomUUID().replaceAll("-", "");
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.className = "toast", 3300);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function connectionPayload() {
  const mode = $("input[name='connectionMode']:checked")?.value || "local";
  return {
    mode,
    base_url: $("#connectionUrl").value.trim(),
    comfy_dir: $("#connectionComfyDir").value.trim(),
    auto_start_local: $("#connectionAutoStart").checked,
  };
}

function refreshConnectionFields() {
  const remote = $("input[name='connectionMode']:checked")?.value === "remote";
  $("#comfyDirField").classList.toggle("hidden", remote);
  $("#autoStartField").classList.toggle("hidden", remote);
  $("#localInstallerSection").classList.toggle("hidden", remote);
  $("#connectionHint").textContent = remote
    ? "遠端主機需先啟動 ComfyUI，建議透過公司內網或 VPN 連線，不要直接把 8188 連接埠公開到網際網路。"
    : "本機模式會使用這台電腦的模型；開啟自動啟動後，面板可代為啟動指定資料夾內的 ComfyUI。";
}

async function loadConnectionSettings(openModal = false) {
  connectionSettings = await api("/api/connection");
  const radio = $(`input[name='connectionMode'][value='${connectionSettings.mode}']`);
  if (radio) radio.checked = true;
  $("#connectionUrl").value = connectionSettings.base_url || "http://127.0.0.1:8188";
  $("#connectionComfyDir").value = connectionSettings.comfy_dir || "";
  $("#connectionAutoStart").checked = Boolean(connectionSettings.auto_start_local);
  refreshConnectionFields();
  if (openModal) $("#connectionModal").classList.remove("hidden");
  await loadInstallerStatus().catch(error => console.warn(error));
}

function closeConnectionSettings() {
  $("#connectionModal").classList.add("hidden");
}

function promptGuideAdvice() {
  if (state.mode !== "fl2va") return promptGuideModeAdvice[state.mode] || [modeLabels[state.mode], "依照目前模式描述完整的可見與可聽事件。"];
  if (state.firstImage && state.lastImage) return ["首尾圖片 · FL2VA", "第一張與第二張是精確首尾幀；重點描述中間可觀察的連續變化與最後收斂路徑。"];
  if (state.firstImage) return ["首尾圖片 · I2VA", "第一張是 0.00 秒精確首幀；先保持圖片中的身份、構圖與空間，再描述後續動作。"];
  if (state.lastImage) return ["首尾圖片 · L2VA", "最後一張是精確尾幀；從合理的先前狀態逐步靠近指定姿勢、構圖與光線。"];
  return ["首尾圖片 · I2VA／FL2VA／L2VA", "上傳首圖、尾圖或兩張圖片後，工具會依實際錨點選擇對應的官方提示方式。"];
}

function openPromptGuide() {
  const [title, advice] = promptGuideAdvice();
  $("#guideCurrentMode").textContent = title;
  $("#guideModeAdvice").textContent = advice;
  $("#promptGuideModal").classList.remove("hidden");
  $(".prompt-guide-content").scrollTop = 0;
  $$(".prompt-guide-nav button").forEach((button, index) => button.classList.toggle("active", index === 0));
  requestAnimationFrame(() => $("#promptGuideModal .modal-close").focus());
}

function closePromptGuide() {
  $("#promptGuideModal").classList.add("hidden");
}

async function copyGuideCode(key) {
  const target = { base_schema: "guideCodeBase", camera_example: "guideCodeCamera", dialogue_example: "guideCodeDialogue" }[key.replaceAll("-", "_")];
  if (!target) return;
  await navigator.clipboard.writeText($(`#${target}`).textContent);
  toast("指南範例已複製");
}

function updateInstallerButton() {
  const accepted = $("#acceptH3License").checked && $("#confirmH3Territory").checked;
  const active = ["starting", "running", "cancelling"].includes(lastInstallerStatus);
  const ready = Boolean(installerPreflightData?.ready_to_install);
  $("#installLocalEngine").disabled = !accepted || !ready || active;
  $("#installLocalEngine").textContent = installerPreflightData?.installed ? "套用此本機引擎" : "開始一鍵安裝";
}

function renderInstallerPreflight(data) {
  installerPreflightData = data;
  const panel = $("#installerPreflight");
  const lines = [];
  lines.push(`GPU：${data.gpu ? `${data.gpu.name} · ${data.gpu.vram_gb} GB VRAM` : "未偵測到 NVIDIA GPU"}`);
  lines.push(`記憶體：${data.ram_gb ?? "未知"} GB · 可用磁碟：${data.disk_free_gb} GiB`);
  lines.push(`本次還需要：約 ${data.required_gb} GiB · 模型完成：${data.models.filter(item => item.ready).length} / ${data.models.length}`);
  if (data.installed) lines.push("✓ 這個資料夾已具備完整的 H3 本機引擎，可直接套用。");
  for (const warning of data.warnings || []) lines.push(`注意：${warning}`);
  for (const issue of data.issues || []) lines.push(`錯誤：${issue}`);
  panel.textContent = lines.join("\n");
  panel.className = `installer-preflight ${data.issues?.length ? "error" : "ready"}`;
  updateInstallerButton();
}

async function runInstallerPreflight() {
  const button = $("#checkLocalEngine");
  button.disabled = true;
  button.textContent = "檢查中...";
  try {
    const target = $("#connectionComfyDir").value.trim();
    const data = await api(`/api/engine-installer/preflight?comfy_dir=${encodeURIComponent(target)}`);
    renderInstallerPreflight(data);
    return data;
  } catch (error) {
    installerPreflightData = null;
    $("#installerPreflight").textContent = error.message;
    $("#installerPreflight").className = "installer-preflight error";
    updateInstallerButton();
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = "檢查這台電腦";
  }
}

function renderInstallerStatus(data) {
  const previous = lastInstallerStatus;
  lastInstallerStatus = data.status || "idle";
  const active = ["starting", "running", "cancelling"].includes(lastInstallerStatus);
  const showProgress = lastInstallerStatus !== "idle";
  $("#installerProgress").classList.toggle("hidden", !showProgress);
  $("#installerStep").textContent = data.step || "準備安裝";
  const progress = Math.max(0, Math.min(100, Number(data.progress) || 0));
  $("#installerPercent").textContent = `${progress}%`;
  $("#installerProgressBar").style.width = `${progress}%`;
  $("#installerError").textContent = data.error || "";
  $("#installerError").classList.toggle("hidden", !data.error);
  $("#installerLogs").textContent = (data.logs || []).join("\n");
  $("#cancelLocalEngine").classList.toggle("hidden", !active);
  $("#connectionComfyDir").disabled = active;
  updateInstallerButton();
  if (lastInstallerStatus === "completed" && previous !== "completed") {
    toast("本機引擎已安裝完成並套用");
    loadConnectionSettings().then(checkStatus).catch(error => console.warn(error));
  }
}

async function loadInstallerStatus() {
  const data = await api("/api/engine-installer/status");
  renderInstallerStatus(data);
  return data;
}

function chooseFiles(accept, multiple = false) {
  return new Promise(resolve => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.addEventListener("change", () => resolve([...input.files]));
    input.click();
  });
}

async function uploadFile(file, kind) {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  toast(`正在匯入 ${file.name}...`);
  const asset = await api("/api/assets", { method: "POST", body: form });
  if (asset.transparency_filled) toast(`${file.name} 的透明區域已自動填入螢光綠。`);
  return asset;
}

function fileExtension(file) {
  const name = String(file?.name || "").toLowerCase();
  return name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
}

function acceptsReferenceFile(file, kind) {
  const extension = fileExtension(file);
  if (kind === "images") return file.type.startsWith("image/") || [".png", ".jpg", ".jpeg", ".webp", ".bmp"].includes(extension);
  if (kind === "video") return file.type.startsWith("video/") || [".mp4", ".mov", ".webm", ".mkv", ".avi"].includes(extension);
  return file.type.startsWith("audio/") || file.type.startsWith("video/") || [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".mp4", ".mov", ".webm"].includes(extension);
}

async function addReferenceFiles(item, kind, files) {
  const accepted = [...files].filter(file => acceptsReferenceFile(file, kind));
  if (!accepted.length) throw new Error(kind === "images" ? "請拖入圖片檔案。" : kind === "video" ? "請拖入影片檔案。" : "請拖入聲音或含聲音的影片檔案。");
  if (kind === "images") {
    if (state.mode === "popup_panel" && item.id === "popup-background") {
      item.images = [await uploadFile(accepted[0], "popup-panel-image")];
      return;
    }
    const remaining = Math.max(0, 9 - item.images.length);
    if (!remaining) throw new Error("這組素材已經有 9 張圖片。")
    for (const file of accepted.slice(0, remaining)) item.images.push(await uploadFile(file, "reference-image"));
    if (accepted.length > remaining) toast(`只加入前 ${remaining} 張；每組素材最多 9 張圖片。`, true);
  } else if (kind === "video") {
    item.video = await uploadFile(accepted[0], "reference-video");
  } else {
    item.audio = await uploadFile(accepted[0], "reference-audio");
  }
}

function currentSettings() {
  return {
    aspect_ratio: $("#aspectRatio").value,
    megapixels: Number($("#megapixels").value),
    duration: Number($("#duration").value),
    seed: Number($("#seed").value),
    steps: Number($("#steps").value),
    scheduler: $("#scheduler").value,
    ref_image_size: $("#refImageSize").value,
    keyframe_fit: $("#keyframeFit").value,
    motion_profile: $("#motionProfile").value,
    motion_intensity: Number($("#motionIntensity").value),
    physics_style: $("#physicsStyle").value,
    camera_response: $("#cameraResponse").value,
    prompt: $("#prompt").value.trim(),
    job_name: $("#jobName").value.trim(),
  };
}

function persistForm() {
  state.form = currentSettings();
  saveState();
}

function restoreForm() {
  const form = state.form || {};
  for (const [id, value] of Object.entries({
    aspectRatio: form.aspect_ratio,
    megapixels: form.megapixels,
    duration: form.duration,
    seed: form.seed,
    steps: form.steps,
    scheduler: form.scheduler,
    refImageSize: form.ref_image_size,
    keyframeFit: form.keyframe_fit || "contain",
    motionProfile: form.motion_profile,
    motionIntensity: form.motion_intensity,
    physicsStyle: form.physics_style,
    cameraResponse: form.camera_response,
    prompt: form.prompt,
    jobName: form.job_name,
  })) {
    if (value !== undefined && $(`#${id}`)) $(`#${id}`).value = value;
  }
}

function dimensions() {
  const presets = { "0.4": [864, 480], "0.7": [1152, 640], "0.9": [1280, 736], "0.98": [1344, 768] };
  const preset = presets[$("#megapixels").value];
  if (preset && $("#aspectRatio").value === "16:9") return preset;
  if (preset && $("#aspectRatio").value === "9:16") return [preset[1], preset[0]];
  const [left, right] = $("#aspectRatio").value.split(":").map(Number);
  const ratio = left / right;
  const pixels = Number($("#megapixels").value) * 1_000_000;
  const rawWidth = Math.sqrt(pixels * ratio);
  const rawHeight = pixels / rawWidth;
  return [Math.max(32, Math.round(rawWidth / 32) * 32), Math.max(32, Math.round(rawHeight / 32) * 32)];
}

function updateKeyframeLayout() {
  const [width, height] = dimensions();
  const pair = $("#keyframePanel .upload-pair");
  pair.style.setProperty("--keyframe-aspect", `${width} / ${height}`);
  pair.classList.toggle("portrait", height > width);
  const [title, description] = keyframeFitHints[$("#keyframeFit").value] || keyframeFitHints.contain;
  $("#keyframeFitHint").innerHTML = `<strong>${title}</strong><span>${description}</span>`;
}

function actualDuration() {
  let frames = Math.max(5, Math.round(Math.max(5, Math.min(15, Number($("#duration").value) || 5)) * 24));
  while (frames % 17 !== 5) frames += 1;
  return frames / 24;
}

function activeReferences() {
  return state.mode === "popup_panel" ? state.popupReferences : state.references;
}

function assetCount() {
  if (state.mode === "replace") return state.replacement.images.length + (state.replacement.video ? 1 : 0);
  if (state.mode === "symbol_loop") return state.symbolLoop.preparedAsset ? 1 : 0;
  let count = 0;
  if (state.firstImage) count++;
  if (state.lastImage) count++;
  if (["extend", "r2v"].includes(state.mode) && state.continuation.lastFrame) count++;
  activeReferences().forEach(item => count += item.images.length + (item.video ? 1 : 0) + (item.audio ? 1 : 0));
  state.storyboards.forEach(shot => count += shot.image ? 1 : 0);
  return count;
}

function filenameStemPreview(value) {
  let stem = String(value || "").trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/[. ]+$/g, "");
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(stem)) stem = `_${stem}`;
  return stem;
}

function updateSummary() {
  const [width, height] = dimensions();
  updateKeyframeLayout();
  $("#dimensionPreview").textContent = `預計輸出 ${width} × ${height} · 24 FPS · 實際約 ${actualDuration().toFixed(2)} 秒`;
  $("#summaryMode").textContent = modeLabels[state.mode];
  $("#summarySize").textContent = `${width} × ${height}`;
  $("#summaryDuration").textContent = `約 ${actualDuration().toFixed(2)} 秒`;
  const motionProfile = $("#motionProfile").value;
  $("#summaryMotion").textContent = `${motionLabels[motionProfile]} · ${$("#motionIntensity").value}`;
  $("#motionPresetHint").textContent = motionHints[motionProfile];
  $("#summaryAssets").textContent = `${assetCount()} 個`;
  $("#promptCount").textContent = `${$("#prompt").value.length} 字`;
  const filenameStem = filenameStemPreview($("#jobName").value);
  $("#filenamePreview").textContent = filenameStem ? `${filenameStem}_00001.mp4` : "未命名時：年-月-日_時-分-秒_微秒_00001.mp4";
  persistForm();
}

function setMode(mode) {
  const previousMode = state.mode;
  state.modePrompts ||= {};
  if (previousMode !== mode) {
    state.modePrompts[previousMode] = $("#prompt").value;
    const savedPrompt = state.modePrompts[mode];
    const defaultPrompt = mode === "replace" ? replacementPrompt() : mode === "symbol_loop" ? symbolLoopPrompt() : mode === "popup_panel" ? popupPanelPrompt() : $("#prompt").value;
    $("#prompt").value = savedPrompt !== undefined ? savedPrompt : defaultPrompt;
  }
  state.mode = mode;
  if (["extend", "r2v"].includes(mode)) $("#referencePanel").before($("#continuationPanel"));
  $$(".mode-card").forEach(card => card.classList.toggle("active", card.dataset.mode === mode));
  $("#keyframePanel").classList.toggle("hidden", mode !== "fl2va");
  $("#referencePanel").classList.toggle("hidden", !["r2v", "popup_panel"].includes(mode));
  $("#replacementPanel").classList.toggle("hidden", mode !== "replace");
  $("#symbolLoopPanel").classList.toggle("hidden", mode !== "symbol_loop");
  $("#continuationPanel").classList.toggle("hidden", !["extend", "r2v"].includes(mode));
  $("#refSizeWrap").classList.toggle("hidden", !["r2v", "replace", "popup_panel"].includes(mode));
  $("#promptStep").textContent = mode === "t2v" ? "03" : "04";
  $("#storyboardHint").textContent = mode === "r2v"
    ? "分鏡圖片會作為構圖參考；出現時間為近似控制"
    : "可加入文字分鏡；中間參考圖片需切換到多模態模式";
  if (["r2v", "replace", "popup_panel"].includes(mode) && $("#scheduler").value === "simple") $("#scheduler").value = "beta";
  if (!["r2v", "replace", "popup_panel"].includes(mode) && $("#scheduler").value === "beta") $("#scheduler").value = "simple";
  if (mode === "replace" && !state.replacement.safeDefaultsApplied) {
    $("#megapixels").value = "0.4";
    $("#duration").value = "5";
    $("#motionIntensity").value = "2";
    $("#cameraResponse").value = "stable";
    state.replacement.safeDefaultsApplied = true;
  }
  if (mode === "popup_panel" && !state.popupPanel.safeDefaultsApplied) {
    $("#duration").value = "5";
    $("#motionProfile").value = "none";
    $("#motionIntensity").value = "2";
    $("#cameraResponse").value = "stable";
    state.popupPanel.safeDefaultsApplied = true;
  }
  if (mode === "replace" && !$("#prompt").value.trim()) $("#prompt").value = replacementPrompt();
  if (mode === "symbol_loop" && !$("#prompt").value.trim()) $("#prompt").value = symbolLoopPrompt();
  if (mode === "popup_panel" && !$("#prompt").value.trim()) $("#prompt").value = popupPanelPrompt();
  const popupMode = mode === "popup_panel";
  $("#referencePanelTitle").textContent = popupMode ? "彈窗面板素材" : "角色與參考素材";
  $("#referencePanelDescription").textContent = popupMode ? "背景圖固定；面板與其他表演素材可自由擴充" : "在敘述詞中直接使用名稱代號";
  $("#referenceInfoTitle").textContent = popupMode ? "背景鎖定" : "自動映射";
  $("#referenceInfoText").textContent = popupMode ? "背景圖全程固定不動；面板、分數、按鈕、特效與新增素材都可以獨立表演。" : "圖片、動作影片與聲音會自動轉成模型參考標籤，不需要手動編號。";
  $("#addReference").classList.remove("hidden");
  state.replacement.defaultPrompt ||= mode === "replace" ? replacementPrompt() : "";
  renderReplacement();
  renderSymbolLoop();
  renderContinuation();
  renderReferences();
  renderStoryboards();
  updateSummary();
  saveState();
  if (mode === "fl2va") refreshKeyframes();
}

function renderKeyframePreview(target, asset, label, optional) {
  const element = $(`#${target}ImagePreview`);
  element.classList.toggle("has-image", Boolean(asset));
  element.style.backgroundImage = asset ? `url("${asset.url}")` : "";
  const sourceName = asset?.source_name || asset?.name || "";
  const size = asset?.width && asset?.height ? `${asset.width} × ${asset.height}` : "";
  element.innerHTML = asset
    ? `<strong>${escapeHtml(label)}</strong><small>${escapeHtml(sourceName)}${size ? ` · ${size}` : ""}</small>`
    : `<span class="upload-plus">＋</span><strong>${escapeHtml(label)}</strong><small>${optional ? "選填" : "點擊或拖入圖片"}</small>`;
}

function renderReplacement() {
  const replacement = state.replacement;
  $("#replacementAlias").value = replacement.alias;
  $("#replacementTarget").value = replacement.target;
  $("#replacementDescription").value = replacement.description;
  $("#replacementImageCount").textContent = `${replacement.images.length}/9`;
  $("#replacementImageList").innerHTML = replacement.images.length
    ? replacement.images.map((asset, index) => `<div class="asset-thumb" style="background-image:url('${asset.url}')" title="${escapeHtml(asset.name)}"><button data-replacement-remove-image="${index}" type="button">×</button></div>`).join("")
    : `<span class="asset-placeholder">拖入 1～9 張新角色圖片；建議包含臉部、半身與全身</span>`;
  $("#replacementVideoPreview").innerHTML = replacement.video
    ? `<div class="asset-thumb video"><span>▶</span><span>${escapeHtml(replacement.video.name)}</span><button id="removeReplacementVideo" type="button">×</button></div>`
    : `<span class="asset-placeholder">拖入含有要被替換角色的原影片</span>`;
  $("#replacementUseAudio").checked = replacement.videoUseAudio;
}

async function addReplacementFiles(kind, files) {
  const accepted = [...files].filter(file => acceptsReferenceFile(file, kind));
  if (!accepted.length) throw new Error(kind === "images" ? "請拖入新角色圖片。" : "請拖入原始表演影片。");
  if (kind === "images") {
    const remaining = Math.max(0, 9 - state.replacement.images.length);
    if (!remaining) throw new Error("新角色圖片最多 9 張。");
    for (const file of accepted.slice(0, remaining)) state.replacement.images.push(await uploadFile(file, "replacement-character"));
  } else {
    state.replacement.video = await uploadFile(accepted[0], "replacement-performance-video");
  }
  renderReplacement();
  updateSummary();
  saveState();
}

function renderSymbolLoop() {
  const symbol = state.symbolLoop;
  const preview = $("#symbolPreview");
  const hasAsset = Boolean(symbol.preparedAsset);
  preview.classList.toggle("empty", !hasAsset);
  $(".symbol-canvas-preview", preview).style.backgroundImage = hasAsset ? `url("${symbol.preparedAsset.url}")` : "";
  $("#symbolSourceName").textContent = hasAsset ? symbol.sourceName : "尚未選擇圖片";
  if (hasAsset && symbol.sourceInfo) {
    const info = symbol.sourceInfo;
    $("#symbolSourceInfo").textContent = `原圖 ${info.source_width}×${info.source_height} → 畫布 ${info.canvas_width}×${info.canvas_height} · 四邊擴張 ${info.padding.left}/${info.padding.top}/${info.padding.right}/${info.padding.bottom}px`;
    $("#symbolScaleInfo").textContent = info.pixel_size_preserved
      ? "原圖像素尺寸完整保留，只增加四周畫布"
      : `原圖超過 H3 最大畫布，已等比例縮放至 ${(info.scale * 100).toFixed(1)}%，沒有變形`;
  } else {
    $("#symbolSourceInfo").textContent = "上傳後會自動選擇畫布與解析度";
    $("#symbolScaleInfo").textContent = "原圖容得下時保留原始像素尺寸";
  }
}

async function useSymbolFile(file) {
  if (!acceptsReferenceFile(file, "images")) throw new Error("請拖入 PNG、JPG、WebP 或 BMP 圖片。");
  const sourceAsset = await uploadFile(file, "symbol-loop-source");
  const result = await api("/api/symbol/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_id: sourceAsset.id }),
  });
  state.symbolLoop.sourceAsset = sourceAsset;
  state.symbolLoop.preparedAsset = result.prepared_asset;
  state.symbolLoop.sourceName = result.source_name;
  state.symbolLoop.sourceInfo = result;
  $("#aspectRatio").value = result.aspect_ratio;
  $("#megapixels").value = String(result.megapixels);
  renderSymbolLoop();
  updateSummary();
  saveState();
  toast(result.pixel_size_preserved ? "已擴張畫布，圖騰像素尺寸保持不變。" : "已等比例縮小並擴張畫布，圖騰沒有變形。");
}

function renderReferences() {
  const list = $("#referenceList");
  const references = activeReferences();
  const popupMode = state.mode === "popup_panel";
  $("#referenceEmpty").classList.toggle("hidden", references.length > 0);
  list.innerHTML = references.map((item, index) => `
    <article class="reference-card" data-reference-id="${item.id}">
      <div class="reference-head">
        <label>名稱代號<input data-ref-field="alias" value="${escapeHtml(item.alias)}" placeholder="例如：小明" ${popupMode && ["popup-background", "popup-panel"].includes(item.id) ? "disabled" : ""}></label>
        <label>素材類型<select data-ref-field="type" ${popupMode && ["popup-background", "popup-panel"].includes(item.id) ? "disabled" : ""}>${Object.entries(typeLabels).map(([value, label]) => `<option value="${value}" ${item.type === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <button class="delete-button ${popupMode && ["popup-background", "popup-panel"].includes(item.id) ? "hidden" : ""}" data-ref-action="delete" title="刪除素材" type="button">×</button>
      </div>
      <div class="reference-body ${popupMode && item.id === "popup-background" ? "single" : ""}">
        <div class="asset-zone" data-drop-kind="images">
          <div class="asset-zone-title"><span>形象圖片 · ${item.images.length}/${popupMode && item.id === "popup-background" ? 1 : 9}</span><button data-ref-action="add-images" type="button">${item.images.length && popupMode && item.id === "popup-background" ? "更換圖片" : "＋加入圖片"}</button></div>
          <div class="thumb-grid">
            ${item.images.length ? item.images.map((asset, assetIndex) => `<div class="asset-thumb" style="background-image:url('${asset.url}')" title="${escapeHtml(asset.name)}"><button data-ref-action="remove-image" data-asset-index="${assetIndex}" type="button">×</button></div>`).join("") : `<span class="asset-placeholder">拖入圖片，或加入正面、側面與全身圖</span>`}
          </div>
        </div>
        <div class="asset-zone ${popupMode && item.id === "popup-background" ? "hidden" : ""}" data-drop-kind="video">
          <div class="asset-zone-title"><span>動作參考影片 · 選填</span><button data-ref-action="add-video" type="button">${item.video ? "更換" : "＋加入影片"}</button></div>
          <div class="thumb-grid">
            ${item.video ? `<div class="asset-thumb video"><span>▶</span><span>${escapeHtml(item.video.name)}</span><button data-ref-action="remove-video" type="button">×</button></div>` : `<span class="asset-placeholder">拖入 MP4、MOV 或 WebM；建議 2–15 秒</span>`}
          </div>
          <label class="check-row"><input data-ref-field="videoUseAudio" type="checkbox" ${item.videoUseAudio ? "checked" : ""}><span>同時參考影片原聲</span></label>
        </div>
        <div class="asset-zone ${popupMode && item.id === "popup-background" ? "hidden" : ""}" data-drop-kind="audio">
          <div class="asset-zone-title"><span>對應聲音 · 選填</span><button data-ref-action="add-audio" type="button">${item.audio ? "更換" : "＋加入聲音"}</button></div>
          <div class="thumb-grid">
            ${item.audio ? `<div class="asset-thumb audio"><span>◉</span><span>${escapeHtml(item.audio.name)}</span><button data-ref-action="remove-audio" type="button">×</button></div>` : `<span class="asset-placeholder">拖入 WAV、MP3、FLAC 或影片音訊</span>`}
          </div>
          <label>聲音用途<select data-ref-field="voiceMode"><option value="timbre" ${item.voiceMode === "timbre" ? "selected" : ""}>只參考音色</option><option value="reuse" ${item.voiceMode === "reuse" ? "selected" : ""}>沿用原聲內容</option></select></label>
        </div>
      </div>
      <label class="description-row">固定特徵與素材用途<input data-ref-field="description" value="${escapeHtml(item.description)}" placeholder="例如：固定黑色短髮、藍色外套；背景只參考建築與燈光"></label>
    </article>
  `).join("");
}

function renderStoryboards() {
  const list = $("#storyboardList");
  $("#storyboardEmpty").classList.toggle("hidden", state.storyboards.length > 0);
  let cursor = 0;
  list.innerHTML = state.storyboards.map((shot, index) => {
    const start = cursor;
    cursor += Number(shot.duration) || 0;
    return `
      <article class="shot-card" data-shot-id="${shot.id}">
        <div class="shot-index"><span>SHOT</span><strong>${String(index + 1).padStart(2, "0")}</strong><small>${start.toFixed(1)}–${cursor.toFixed(1)}s</small></div>
        <div class="shot-fields">
          <label>秒數<input data-shot-field="duration" type="number" min="0.5" max="15" step="0.5" value="${escapeHtml(shot.duration)}"></label>
          <label>鏡頭與運鏡<input data-shot-field="camera" value="${escapeHtml(shot.camera)}" placeholder="低角度緩慢推進"></label>
          <label>台詞<input data-shot-field="dialogue" value="${escapeHtml(shot.dialogue)}" placeholder="小明說：「……」"></label>
          <label class="wide-field">畫面與動作<textarea data-shot-field="description" rows="2" placeholder="描述這個鏡頭發生的動作">${escapeHtml(shot.description)}</textarea></label>
          <label class="wide-field">動態節拍<input data-shot-field="motionBeats" value="${escapeHtml(shot.motionBeats)}" placeholder="例如：蓄力 → 快速揮擊 → 接觸停頓 → 回彈收勢"></label>
          <label class="wide-field">特效時序<input data-shot-field="effects" value="${escapeHtml(shot.effects)}" placeholder="例如：接觸點閃光 → 衝擊波擴散 → 火花拖尾消散"></label>
          <label class="wide-field">聲音與音樂<input data-shot-field="sound" value="${escapeHtml(shot.sound)}" placeholder="腳步聲、風聲、低沉配樂"></label>
        </div>
        <div>
          <div class="shot-image ${shot.image ? "has-image" : ""}" data-shot-action="image" style="${shot.image ? `background-image:url('${shot.image.url}')` : ""}">${state.mode === "r2v" ? (shot.image ? "已加入" : "＋ 分鏡圖") : "文字分鏡"}</div>
          <button class="delete-button" data-shot-action="delete" title="刪除鏡頭" type="button">×</button>
        </div>
      </article>
    `;
  }).join("");
}

function collectPayload() {
  const form = currentSettings();
  const references = state.mode === "replace" ? [{
    alias: state.replacement.alias.trim(),
    type: "character",
    description: state.replacement.description.trim(),
    image_asset_ids: state.replacement.images.map(asset => asset.id),
    video_asset_id: state.replacement.video?.id || null,
    video_use_audio: Boolean(state.replacement.video && state.replacement.videoUseAudio),
    audio_asset_id: null,
    voice_mode: "timbre",
  }] : activeReferences().map(item => ({
    alias: item.alias.trim(),
    type: item.type,
    description: item.description.trim(),
    image_asset_ids: item.images.map(asset => asset.id),
    video_asset_id: item.video?.id || null,
    video_use_audio: Boolean(item.video && item.videoUseAudio),
    audio_asset_id: item.audio?.id || null,
    voice_mode: item.voiceMode,
  }));
  return {
    mode: state.mode,
    ...form,
    first_image_asset_id: ["extend", "r2v"].includes(state.mode)
      ? state.continuation.lastFrame?.id || null
      : state.mode === "symbol_loop"
        ? state.symbolLoop.preparedAsset?.id || null
        : state.firstImage?.id || null,
    last_image_asset_id: state.mode === "symbol_loop"
      ? state.symbolLoop.preparedAsset?.id || null
      : state.mode === "fl2va" ? state.lastImage?.id || null : null,
    continuation_source_job_id: state.mode === "extend" ? state.continuation.sourceJobId : null,
    continuation_source_asset_id: state.mode === "extend" ? state.continuation.sourceAsset?.id || null : null,
    continuation_merge: state.mode === "extend" && state.continuation.merge,
    continuation_audio: state.continuation.audio,
    references,
    storyboards: state.storyboards.map(shot => ({
      duration: Number(shot.duration),
      description: shot.description.trim(),
      camera: shot.camera.trim(),
      dialogue: shot.dialogue.trim(),
      sound: shot.sound.trim(),
      motion_beats: shot.motionBeats.trim(),
      effects: shot.effects.trim(),
      image_asset_id: state.mode === "r2v" ? shot.image?.id || null : null,
    })),
  };
}

async function compilePreview() {
  const button = $("#compileButton");
  button.disabled = true;
  try {
    const result = await api("/api/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    $("#compiledPrompt").textContent = result.prompt;
    $("#compiledPanel").classList.remove("hidden");
    toast(`提示詞已編譯，使用 ${result.asset_count} 個素材`);
    return result;
  } catch (error) {
    toast(error.message, true);
    throw error;
  } finally {
    button.disabled = false;
  }
}

async function renderVideo() {
  if (state.mode === "replace") {
    const highRisk = Number($("#duration").value) > 10 || (Number($("#megapixels").value) >= 0.9 && Number($("#duration").value) > 5);
    if (highRisk && !confirm("目前的解析度／時長對 16GB VRAM 有較高爆顯存風險。建議先改成 0.4MP、5 秒。仍要送出嗎？")) return;
  }
  const button = $("#renderButton");
  button.disabled = true;
  button.querySelector("span").textContent = "送入工作佇列...";
  try {
    const job = await api("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    toast(`工作 ${job.id.slice(0, 8)} 已加入佇列`);
    await loadJobs(true);
    $(".jobs-section").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "開始生成影片";
  }
}

function statusLabel(status) {
  return ({ queued: "等待中", preparing: "準備素材", running: "生成中", completed: "已完成", failed: "失敗", cancelled: "已取消", interrupted: "已中斷" })[status] || status;
}

function renderContinuation() {
  const continuation = state.continuation;
  const multimodal = state.mode === "r2v";
  const preview = $("#continuationPreview");
  const frame = $(".continuation-frame", preview);
  const hasSource = Boolean(continuation.lastFrame);
  preview.classList.toggle("empty", !hasSource);
  frame.style.backgroundImage = hasSource ? `url("${continuation.lastFrame.url}")` : "";
  $("#continuationSourceName").textContent = hasSource ? continuation.sourceName : "尚未選擇影片";
  if (hasSource && continuation.sourceInfo) {
    const info = continuation.sourceInfo;
    $("#continuationSourceInfo").textContent = `${info.width} × ${info.height} · ${Number(info.duration || 0).toFixed(2)} 秒 · 已擷取最後一幀`;
  } else {
    $("#continuationSourceInfo").textContent = "選擇後會顯示最後一幀與影片資訊";
  }
  $("#continuationTitle").textContent = multimodal ? "參考上一段影片結尾" : "選擇上一段影片";
  $("#continuationDescription").textContent = multimodal
    ? "自動擷取最後一幀，作為多模態新影片的開頭構圖與連續性參考"
    : "自動擷取最後一幀，作為新影片的精確起始畫面";
  $("#continuationSourceKind").textContent = multimodal ? "OPENING REFERENCE" : "CONTINUATION SOURCE";
  $("#multimodalContinuationNote").classList.toggle("hidden", !multimodal);
  $("#continuationOptions").classList.toggle("hidden", multimodal);
  $("#continuationMerge").checked = Boolean(continuation.merge);
  $("#continuationAudio").value = continuation.audio || "both";
}

function renderContinuationJobs(jobs) {
  const select = $("#continuationJob");
  const selected = state.continuation.sourceJobId || select.value;
  const completed = jobs.filter(job => job.status === "completed" && job.output);
  select.innerHTML = `<option value="">選擇一支已完成影片</option>${completed.map(job => {
    const date = new Date(job.created_at).toLocaleString("zh-TW", { hour12: false });
    const label = job.name ? `${job.name} · ${modeLabels[job.mode] || job.mode}` : (modeLabels[job.mode] || job.mode);
    return `<option value="${escapeHtml(job.id)}">${escapeHtml(label)} · ${job.width}×${job.height} · ${escapeHtml(date)} · ${job.id.slice(0, 8)}</option>`;
  }).join("")}`;
  if (completed.some(job => job.id === selected)) select.value = selected;
}

async function prepareContinuation(source) {
  const result = await api("/api/continuation/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(source),
  });
  state.continuation.sourceJobId = result.source_job_id || null;
  state.continuation.sourceAsset = result.source_asset_id
    ? { id: result.source_asset_id, name: result.source_name }
    : null;
  state.continuation.lastFrame = result.last_frame;
  state.continuation.sourceName = result.source_name;
  state.continuation.sourceInfo = {
    width: result.width,
    height: result.height,
    duration: result.duration,
    fps: result.fps,
  };
  $("#aspectRatio").value = result.aspect_ratio;
  $("#megapixels").value = String(result.megapixels);
  renderContinuation();
  updateSummary();
  saveState();
  toast(state.mode === "r2v"
    ? "已加入上一段最後一幀，將作為多模態影片的開頭參考。"
    : "已擷取上一段最後一幀，解析度也已自動匹配。");
}

async function useContinuationFile(file) {
  if (!acceptsReferenceFile(file, "video")) throw new Error("請拖入 MP4、MOV、WebM、MKV 或 AVI 影片。");
  const asset = await uploadFile(file, "continuation-video");
  await prepareContinuation({ asset_id: asset.id });
}

async function loadJobs(force = false) {
  try {
    const query = encodeURIComponent(jobSearch);
    const response = await api(`/api/jobs?page=${jobPage}&page_size=20&q=${query}`);
    let jobs;
    let meta;
    if (Array.isArray(response)) {
      const filtered = jobSearch ? response.filter(job => `${job.name || ""} ${job.id} ${job.mode}`.toLowerCase().includes(jobSearch.toLowerCase())) : response;
      jobTotalPages = Math.max(1, Math.ceil(filtered.length / 20));
      jobPage = Math.min(jobPage, jobTotalPages);
      jobs = filtered.slice((jobPage - 1) * 20, jobPage * 20);
      meta = { total: filtered.length, total_pages: jobTotalPages, page: jobPage };
    } else {
      jobs = response.items || [];
      jobPage = response.page || 1;
      jobTotalPages = response.total_pages || 1;
      meta = response;
    }
    if (force || Date.now() - lastJobOptionsLoad > 10000) {
      try {
        renderContinuationJobs(await api("/api/jobs/options"));
        lastJobOptionsLoad = Date.now();
      } catch {
        renderContinuationJobs(jobs);
      }
    }
    $("#jobPagination").classList.toggle("hidden", jobTotalPages <= 1);
    $("#jobPageLabel").textContent = `第 ${jobPage} / ${jobTotalPages} 頁 · 共 ${meta.total || 0} 筆`;
    $("#previousJobPage").disabled = jobPage <= 1;
    $("#nextJobPage").disabled = jobPage >= jobTotalPages;
    const signature = JSON.stringify({ jobs, meta });
    if (!force && signature === lastJobsSignature) return;
    if (!force && $$(".job-video").some(video => !video.paused)) return;
    lastJobsSignature = signature;
    $("#jobEmpty").classList.toggle("hidden", jobs.length > 0);
    $("#jobList").innerHTML = jobs.map(job => {
      const active = ["queued", "preparing", "running"].includes(job.status);
      const date = new Date(job.created_at).toLocaleString("zh-TW", { hour12: false });
      const fallbackName = `${modeLabels[job.mode] || job.mode} · ${job.width}×${job.height}`;
      const title = job.name || fallbackName;
      const subtitle = job.name ? `${fallbackName} · ${date} · ${Number(job.duration).toFixed(2)} 秒 · ${job.id.slice(0, 8)}` : `${date} · ${Number(job.duration).toFixed(2)} 秒 · ${job.id.slice(0, 8)}`;
      const open = active || expandedJobIds.has(job.id);
      return `
        <details class="job-card" data-job-id="${job.id}" ${open ? "open" : ""}>
          <summary class="job-summary">
            <div class="job-title"><span class="job-badge ${job.status}">${statusLabel(job.status)}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle)}</small></div></div>
            <div class="job-progress"><div class="progress-track"><span style="width:${job.progress || 0}%"></span></div><small><span>${escapeHtml(job.current_node || statusLabel(job.status))}</span><span>${job.progress || 0}%</span></small></div>
            <span class="job-chevron">⌄</span>
          </summary>
          <div class="job-detail">
            <div class="job-detail-copy"><small>完整工作編號：${escapeHtml(job.id)}</small>${job.output?.filename ? `<small>輸出檔名：${escapeHtml(job.output.filename)}</small>` : ""}</div>
            <div class="job-actions">
              <button class="button ghost" data-job-rename="${job.id}" data-job-name="${escapeHtml(job.name || "")}" type="button">重新命名</button>
              ${job.status === "completed" ? `<a class="button secondary" href="/api/jobs/${job.id}/video?download=1" download>下載</a>` : ""}
              ${active ? `<button class="button ghost" data-job-cancel="${job.id}" type="button">取消</button>` : ""}
            </div>
            ${job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ""}
            ${job.merge_error ? `<div class="job-error">${escapeHtml(job.merge_error)}</div>` : ""}
            ${job.status === "completed" ? `<video class="job-video" controls preload="none" data-src="/api/jobs/${job.id}/video"></video>` : ""}
          </div>
        </details>
      `;
    }).join("");
    $$(".job-card[open] .job-video").forEach(video => {
      if (!video.src) video.src = video.dataset.src;
    });
  } catch (error) {
    console.warn(error);
  }
}

function showEngineStarting() {
  if (!engineStartingAt) engineStartingAt = Date.now();
  const elapsed = Math.max(0, Math.floor((Date.now() - engineStartingAt) / 1000));
  $("#statusDot").className = "status-dot starting";
  $("#statusText").textContent = `ComfyUI 啟動中 · ${elapsed} 秒`;
  $("#deviceName").textContent = "正在載入模型引擎";
  $("#startEngine").classList.remove("hidden");
  $("#startEngine").disabled = true;
  $("#startEngine").textContent = "啟動中...";
}

async function checkStatus() {
  try {
    const data = await api("/api/status");
    const remote = data.connection_mode === "remote";
    $("#executionMode").textContent = remote ? "遠端 GPU" : "本機 GPU";
    $("#renderNote").textContent = remote
      ? "素材會傳送到你設定的遠端 ComfyUI；完成影片會存回這台面板電腦。"
      : "工作會依序進入本機 GPU 佇列。";
    if (data.ready) {
      engineStartingAt = 0;
      $("#statusDot").className = "status-dot ready";
      $("#statusText").textContent = remote ? "遠端 ComfyUI 已連線" : "本機 ComfyUI 已就緒";
      $("#deviceName").textContent = data.device ? data.device.replace(/^cuda:\d+\s*/, "") : "引擎已就緒";
      $("#startEngine").classList.add("hidden");
      $("#startEngine").disabled = false;
      $("#startEngine").textContent = "啟動引擎";
    } else if (data.starting || engineStartingAt) {
      showEngineStarting();
    } else {
      $("#statusDot").className = "status-dot error";
      $("#statusText").textContent = remote ? "遠端 ComfyUI 無法連線" : "本機 ComfyUI 尚未啟動";
      $("#deviceName").textContent = remote ? "等待遠端主機" : "等待引擎";
      $("#startEngine").classList.toggle("hidden", !data.can_start);
      $("#startEngine").disabled = !data.can_start;
      $("#startEngine").textContent = "啟動引擎";
    }
  } catch {
    if (engineStartingAt) showEngineStarting();
    else {
      $("#statusDot").className = "status-dot error";
      $("#statusText").textContent = "無法檢查引擎";
    }
  }
}

function addReference() {
  const references = activeReferences();
  const popupMode = state.mode === "popup_panel";
  let alias;
  if (popupMode) {
    const usedAliases = new Set(references.map(item => item.alias.trim().toLocaleLowerCase()));
    let index = 1;
    do alias = `面板素材${index++}`; while (usedAliases.has(alias.toLocaleLowerCase()));
  } else {
    alias = `角色${references.length + 1}`;
  }
  references.push({ id: uid(), alias, type: popupMode ? "object" : "character", description: "", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" });
  renderReferences();
  updateSummary();
  saveState();
}

function addStoryboard() {
  state.storyboards.push({ id: uid(), duration: 2, description: "", camera: "", dialogue: "", sound: "", motionBeats: "", effects: "", image: null });
  renderStoryboards();
  saveState();
}

function replacementPrompt() {
  const alias = state.replacement?.alias?.trim() || "新角色";
  const target = state.replacement?.target?.trim() || "動態參考影片中的主要角色";
  return `${alias}完整取代${target}。\n\n${alias}必須始終保持參考圖片中的臉部、髮型、服裝與身材特徵。\n完全沿用原角色的動作、姿勢、表演節奏、畫面位置和鏡頭運動，\n但不要保留或生成原角色的臉部、髮型、服裝與身份特徵。\n全程只出現${alias}，不要同時出現${alias}與原角色，也不要混合兩者外觀。\n除指定角色外，盡量維持原影片的場景、構圖、光線、道具與其他人物。`;
}

function symbolLoopPrompt() {
  return "輸入圖片是不可修改的遊戲圖騰設計。保持固定畫布、固定正面攝影機、固定中心 Pivot、固定視覺比例，完整輪廓全程可見。\n\n在一個緩慢循環中，圖騰只做非常克制的 1～2% 呼吸脈動；加入一次寬而乾淨的材質高光掃過。動作在中段達到最大幅度，然後沿相反路徑平順返回原始靜止姿勢。第一幀與最後一幀的姿勢、位置、旋轉、比例、輪廓、材質、光線與效果狀態必須一致。\n\n保持原始身份、文字、材質、顏色、外框形狀與所有比例。禁止攝影機移動、縮放、裁切、重新構圖、主體平移、比例漂移、造型重設、增加物件、複製部件、融化變形、文字改變、背景場景、粗糙顆粒或碰到畫布邊界的雜亂粒子。";
}

function popupPanelPrompt() {
  return `鏡頭固定。背景圖是全程鎖定不動的遊戲底板環境，不得平移、縮放、變形、閃爍或產生景深變化。面板位於背景圖上方；面板出現期間，面板與背景圖之間加入約 80% 不透明度的黑色壓暗圖層。

[0.0秒～0.5秒] 面板從畫面正中央由小到大快速縮放彈出，帶有清楚但不過度的 overshoot 回彈，最後穩定停在畫面中央。背景圖保持完全靜止，黑色壓暗圖層同步淡入。

[0.5秒～4.5秒] 面板的位置與外框保持穩定。面板上的分數在 1.00～1.05 倍之間緩慢來回縮放；其他面板物件依需求表演，例如數字跳動、光效掃過、粒子閃爍、按鈕呼吸或裝飾物輕微擺動。所有動態都限制在面板範圍內，不得帶動背景圖或整體鏡頭。

[4.5秒～5.0秒] 面板連同面板上的所有物件整體快速縮小並完全消失，黑色壓暗圖層同步淡出。最後只剩原本的背景圖，背景圖的位置、比例、亮度與最初畫面完全一致。

全程規則：固定鏡頭、固定背景圖、禁止背景運動、禁止鏡頭推拉與晃動；只允許面板、面板內容與壓暗圖層產生動畫。`;
}

function promptTemplate() {
  if (state.mode === "popup_panel") return popupPanelPrompt();
  if (state.mode === "r2v") {
    return "場景概述：角色名稱出現在背景名稱所代表的場景中。\n\n[Shot 1, 0s-5s]\n描述角色動作、表情與其他角色的互動。攝影機以中景緩慢推進。\n\nDialogue：角色名稱說：「台詞內容。」\nAudio：描述環境聲、動作聲與背景音樂。\n\n保持所有已命名角色的臉部、服裝與聲音一致，不要混合不同參考素材的特徵。";
  }
  if (state.mode === "extend") {
    return "延續上一段最後一幀的動作與運鏡，不要重新起步或停頓。角色維持相同外觀、姿勢慣性、視線、光線與空間位置。\n\n[Shot 1, 0s-5s]\n角色順著原本的動量繼續完成動作，鏡頭平順跟隨並呈現自然的加速、減速與身體重心變化。\n\nAudio：延續現場環境音與動作音效。";
  }
  if (state.mode === "replace") return replacementPrompt();
  if (state.mode === "symbol_loop") return symbolLoopPrompt();
  if (state.mode === "fl2va") {
    return "從起始畫面自然開始，描述主體如何移動與改變，最後準確抵達結束畫面。\n\nCamera：描述運鏡。\nAudio：描述台詞、環境聲、音效與音樂。";
  }
  return "場景概述：描述地點、角色與正在發生的事件。\n\n[Shot 1, 0s-5s]\n描述動作、表情、鏡位與攝影機移動。\n\nDialogue：描述台詞。\nAudio：描述環境聲、音效與背景音樂。";
}

function bindEvents() {
  $("#modeGrid").addEventListener("click", event => {
    const card = event.target.closest(".mode-card");
    if (card) setMode(card.dataset.mode);
  });
  ["aspectRatio", "megapixels", "duration", "seed", "steps", "scheduler", "refImageSize", "keyframeFit", "motionProfile", "motionIntensity", "physicsStyle", "cameraResponse", "prompt", "jobName"].forEach(id => {
    $(`#${id}`).addEventListener("input", updateSummary);
    $(`#${id}`).addEventListener("change", updateSummary);
  });
  ["aspectRatio", "megapixels", "keyframeFit"].forEach(id => {
    $(`#${id}`).addEventListener("change", refreshKeyframes);
  });
  $("#randomSeed").addEventListener("click", () => {
    $("#seed").value = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
    updateSummary();
  });
  $("#startEngine").addEventListener("click", async () => {
    engineStartingAt = Date.now();
    showEngineStarting();
    try { await api("/api/comfy/start", { method: "POST" }); toast("ComfyUI 已啟動"); } catch (error) { toast(error.message, true); }
    finally { checkStatus(); }
  });
  $("#openConnectionSettings").addEventListener("click", async () => {
    try { await loadConnectionSettings(true); } catch (error) { toast(error.message, true); }
  });
  $("#openPromptGuide").addEventListener("click", openPromptGuide);
  $$('[data-close-prompt-guide]').forEach(element => element.addEventListener("click", closePromptGuide));
  $$(".prompt-guide-nav button").forEach(button => button.addEventListener("click", () => {
    $$(".prompt-guide-nav button").forEach(item => item.classList.toggle("active", item === button));
    $(`#${button.dataset.guideTarget}`).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  $$('[data-copy-guide]').forEach(button => button.addEventListener("click", async () => {
    try { await copyGuideCode(button.dataset.copyGuide); } catch (error) { toast("無法複製指南範例", true); }
  }));
  $("#guideGoToPrompt").addEventListener("click", () => {
    closePromptGuide();
    $("#prompt").scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => $("#prompt").focus(), 350);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (!$("#promptGuideModal").classList.contains("hidden")) closePromptGuide();
    else if (!$("#connectionModal").classList.contains("hidden")) closeConnectionSettings();
  });
  $$("[data-close-connection]").forEach(element => element.addEventListener("click", closeConnectionSettings));
  $$("input[name='connectionMode']").forEach(element => element.addEventListener("change", refreshConnectionFields));
  $("#connectionComfyDir").addEventListener("input", () => {
    installerPreflightData = null;
    $("#installerPreflight").textContent = "安裝路徑已變更，請重新檢查這台電腦。";
    $("#installerPreflight").className = "installer-preflight muted";
    updateInstallerButton();
  });
  $("#checkLocalEngine").addEventListener("click", async () => {
    try { await runInstallerPreflight(); } catch (error) { toast(error.message, true); }
  });
  ["acceptH3License", "confirmH3Territory"].forEach(id => {
    $(`#${id}`).addEventListener("change", updateInstallerButton);
  });
  $("#installLocalEngine").addEventListener("click", async () => {
    const button = $("#installLocalEngine");
    button.disabled = true;
    try {
      if (!installerPreflightData) await runInstallerPreflight();
      const result = await api("/api/engine-installer/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          comfy_dir: $("#connectionComfyDir").value.trim(),
          accepted_license: $("#acceptH3License").checked && $("#confirmH3Territory").checked,
        }),
      });
      renderInstallerStatus(result);
      toast(installerPreflightData?.installed ? "已套用本機引擎" : "已開始安裝，可關閉視窗後稍後回來查看");
    } catch (error) { toast(error.message, true); }
    finally { updateInstallerButton(); }
  });
  $("#cancelLocalEngine").addEventListener("click", async () => {
    try {
      renderInstallerStatus(await api("/api/engine-installer/cancel", { method: "POST" }));
      toast("安裝已取消；已下載的完整檔案會保留，之後可以續裝");
    } catch (error) { toast(error.message, true); }
  });
  $("#testConnection").addEventListener("click", async () => {
    const button = $("#testConnection");
    button.disabled = true;
    button.textContent = "測試中...";
    try {
      const result = await api("/api/connection/test", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(connectionPayload()),
      });
      toast(result.ready ? `連線成功${result.device ? `：${result.device}` : ""}` : "目前無法連線到 ComfyUI", !result.ready);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = "測試連線"; }
  });
  $("#saveConnection").addEventListener("click", async () => {
    const button = $("#saveConnection");
    button.disabled = true;
    try {
      connectionSettings = await api("/api/connection", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(connectionPayload()),
      });
      closeConnectionSettings();
      engineStartingAt = 0;
      toast("引擎設定已儲存");
      await checkStatus();
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });

  $$(".upload-box").forEach(box => {
    const target = box.dataset.uploadTarget;
    const input = $(`#${target}ImageInput`);
    box.addEventListener("click", () => input.click());
    box.addEventListener("dragover", event => event.preventDefault());
    box.addEventListener("drop", async event => {
      event.preventDefault();
      if (event.dataTransfer.files[0]) await setKeyframe(target, event.dataTransfer.files[0]);
    });
    input.addEventListener("change", async () => input.files[0] && setKeyframe(target, input.files[0]));
  });

  const symbolUpload = $("#symbolUpload");
  const symbolInput = $("#symbolImageInput");
  symbolUpload.addEventListener("click", () => symbolInput.click());
  symbolUpload.addEventListener("dragover", event => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    symbolUpload.classList.add("drag-active");
  });
  symbolUpload.addEventListener("dragleave", event => {
    if (event.relatedTarget && symbolUpload.contains(event.relatedTarget)) return;
    symbolUpload.classList.remove("drag-active");
  });
  symbolUpload.addEventListener("drop", async event => {
    event.preventDefault();
    symbolUpload.classList.remove("drag-active");
    const file = event.dataTransfer.files[0];
    if (!file) return;
    try { await useSymbolFile(file); }
    catch (error) { toast(error.message, true); }
  });
  symbolInput.addEventListener("change", async () => {
    const file = symbolInput.files[0];
    symbolInput.value = "";
    if (!file) return;
    try { await useSymbolFile(file); }
    catch (error) { toast(error.message, true); }
  });

  $("#useContinuationJob").addEventListener("click", async () => {
    const jobId = $("#continuationJob").value;
    if (!jobId) return toast("請先選擇一支已完成影片。", true);
    try { await prepareContinuation({ job_id: jobId }); }
    catch (error) { toast(error.message, true); }
  });
  const continuationUpload = $("#continuationUpload");
  const continuationInput = $("#continuationVideoInput");
  continuationUpload.addEventListener("click", () => continuationInput.click());
  continuationUpload.addEventListener("dragover", event => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    continuationUpload.classList.add("drag-active");
  });
  continuationUpload.addEventListener("dragleave", event => {
    if (event.relatedTarget && continuationUpload.contains(event.relatedTarget)) return;
    continuationUpload.classList.remove("drag-active");
  });
  continuationUpload.addEventListener("drop", async event => {
    event.preventDefault();
    continuationUpload.classList.remove("drag-active");
    const file = event.dataTransfer.files[0];
    if (!file) return;
    try { await useContinuationFile(file); }
    catch (error) { toast(error.message, true); }
  });
  continuationInput.addEventListener("change", async () => {
    const file = continuationInput.files[0];
    continuationInput.value = "";
    if (!file) return;
    try { await useContinuationFile(file); }
    catch (error) { toast(error.message, true); }
  });
  $("#continuationMerge").addEventListener("change", event => {
    state.continuation.merge = event.target.checked;
    saveState();
  });
  $("#continuationAudio").addEventListener("change", event => {
    state.continuation.audio = event.target.value;
    saveState();
  });

  for (const [id, field] of [["replacementAlias", "alias"], ["replacementTarget", "target"]]) {
    $(`#${id}`).addEventListener("input", event => {
      const previousDefault = state.replacement.defaultPrompt || replacementPrompt();
      state.replacement[field] = event.target.value;
      const nextDefault = replacementPrompt();
      if (!$("#prompt").value.trim() || $("#prompt").value === previousDefault) $("#prompt").value = nextDefault;
      state.replacement.defaultPrompt = nextDefault;
      updateSummary();
      saveState();
    });
  }
  $("#replacementDescription").addEventListener("input", event => {
    state.replacement.description = event.target.value;
    saveState();
  });
  $("#addReplacementImages").addEventListener("click", () => $("#replacementImageInput").click());
  $("#addReplacementVideo").addEventListener("click", () => $("#replacementVideoInput").click());
  $("#replacementImageInput").addEventListener("change", async event => {
    try { await addReplacementFiles("images", event.target.files); }
    catch (error) { toast(error.message, true); }
    event.target.value = "";
  });
  $("#replacementVideoInput").addEventListener("change", async event => {
    try { await addReplacementFiles("video", event.target.files); }
    catch (error) { toast(error.message, true); }
    event.target.value = "";
  });
  $$("[data-replacement-kind]").forEach(zone => {
    zone.addEventListener("dragover", event => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      zone.classList.add("drag-active");
    });
    zone.addEventListener("dragleave", event => {
      if (event.relatedTarget && zone.contains(event.relatedTarget)) return;
      zone.classList.remove("drag-active");
    });
    zone.addEventListener("drop", async event => {
      event.preventDefault();
      zone.classList.remove("drag-active");
      try { await addReplacementFiles(zone.dataset.replacementKind, event.dataTransfer.files); }
      catch (error) { toast(error.message, true); }
    });
  });
  $("#replacementImageList").addEventListener("click", event => {
    const button = event.target.closest("[data-replacement-remove-image]");
    if (!button) return;
    state.replacement.images.splice(Number(button.dataset.replacementRemoveImage), 1);
    renderReplacement(); updateSummary(); saveState();
  });
  $("#replacementVideoPreview").addEventListener("click", event => {
    if (!event.target.closest("#removeReplacementVideo")) return;
    state.replacement.video = null;
    state.replacement.videoUseAudio = false;
    renderReplacement(); updateSummary(); saveState();
  });
  $("#replacementUseAudio").addEventListener("change", event => {
    state.replacement.videoUseAudio = event.target.checked;
    saveState();
  });

  $("#addReference").addEventListener("click", addReference);
  $("#referenceList").addEventListener("input", event => {
    const card = event.target.closest("[data-reference-id]");
    const field = event.target.dataset.refField;
    if (!card || !field) return;
    const item = activeReferences().find(value => value.id === card.dataset.referenceId);
    item[field] = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    updateSummary();
    saveState();
  });
  $("#referenceList").addEventListener("dragover", event => {
    const zone = event.target.closest("[data-drop-kind]");
    if (!zone) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    zone.classList.add("drag-active");
  });
  $("#referenceList").addEventListener("dragleave", event => {
    const zone = event.target.closest("[data-drop-kind]");
    if (!zone || (event.relatedTarget && zone.contains(event.relatedTarget))) return;
    zone.classList.remove("drag-active");
  });
  $("#referenceList").addEventListener("drop", async event => {
    const zone = event.target.closest("[data-drop-kind]");
    const card = event.target.closest("[data-reference-id]");
    if (!zone || !card) return;
    event.preventDefault();
    zone.classList.remove("drag-active");
    const item = activeReferences().find(value => value.id === card.dataset.referenceId);
    try {
      await addReferenceFiles(item, zone.dataset.dropKind, event.dataTransfer.files);
      renderReferences(); updateSummary(); saveState();
      toast("素材已加入。")
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#referenceList").addEventListener("click", async event => {
    const actionElement = event.target.closest("[data-ref-action]");
    const card = event.target.closest("[data-reference-id]");
    if (!actionElement || !card) return;
    const references = activeReferences();
    const item = references.find(value => value.id === card.dataset.referenceId);
    const action = actionElement.dataset.refAction;
    try {
      if (action === "delete" && !(state.mode === "popup_panel" && ["popup-background", "popup-panel"].includes(item.id))) {
        if (state.mode === "popup_panel") state.popupReferences = references.filter(value => value.id !== item.id);
        else state.references = references.filter(value => value.id !== item.id);
      }
      if (action === "add-images") {
        const files = await chooseFiles("image/*", true);
        if (files.length) await addReferenceFiles(item, "images", files);
      }
      if (action === "add-video") {
        const files = await chooseFiles("video/mp4,video/webm,video/quicktime,.mov,.mkv,.avi");
        if (files[0]) await addReferenceFiles(item, "video", files);
      }
      if (action === "add-audio") {
        const files = await chooseFiles("audio/*,video/mp4,video/webm,video/quicktime");
        if (files[0]) await addReferenceFiles(item, "audio", files);
      }
      if (action === "remove-image") item.images.splice(Number(actionElement.dataset.assetIndex), 1);
      if (action === "remove-video") { item.video = null; item.videoUseAudio = false; }
      if (action === "remove-audio") item.audio = null;
      renderReferences(); updateSummary(); saveState();
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#addStoryboard").addEventListener("click", addStoryboard);
  $("#storyboardList").addEventListener("input", event => {
    const card = event.target.closest("[data-shot-id]");
    const field = event.target.dataset.shotField;
    if (!card || !field) return;
    const shot = state.storyboards.find(value => value.id === card.dataset.shotId);
    shot[field] = event.target.value;
    saveState();
    if (field === "duration") renderStoryboards();
  });
  $("#storyboardList").addEventListener("click", async event => {
    const actionElement = event.target.closest("[data-shot-action]");
    const card = event.target.closest("[data-shot-id]");
    if (!actionElement || !card) return;
    const shot = state.storyboards.find(value => value.id === card.dataset.shotId);
    if (actionElement.dataset.shotAction === "delete") state.storyboards = state.storyboards.filter(value => value.id !== shot.id);
    if (actionElement.dataset.shotAction === "image") {
      if (state.mode !== "r2v") return toast("中間分鏡圖片需要使用多模態參考模式。", true);
      const files = await chooseFiles("image/*");
      if (files[0]) shot.image = await uploadFile(files[0], "storyboard-image");
    }
    renderStoryboards(); updateSummary(); saveState();
  });

  $("#insertPromptTemplate").addEventListener("click", () => {
    if ($("#prompt").value.trim() && !confirm("要用提示詞範本取代目前內容嗎？")) return;
    $("#prompt").value = promptTemplate();
    updateSummary();
  });
  $("#compileButton").addEventListener("click", () => compilePreview().catch(() => {}));
  $("#copyCompiled").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#compiledPrompt").textContent);
    toast("已複製編譯後提示詞");
  });
  $("#renderButton").addEventListener("click", renderVideo);
  $("#refreshJobs").addEventListener("click", () => loadJobs(true));
  let searchTimer;
  $("#jobSearch").addEventListener("input", event => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      jobSearch = event.target.value.trim();
      jobPage = 1;
      loadJobs(true);
    }, 250);
  });
  $("#previousJobPage").addEventListener("click", () => {
    if (jobPage <= 1) return;
    jobPage--;
    loadJobs(true);
    $(".jobs-section").scrollIntoView({ behavior: "smooth" });
  });
  $("#nextJobPage").addEventListener("click", () => {
    if (jobPage >= jobTotalPages) return;
    jobPage++;
    loadJobs(true);
    $(".jobs-section").scrollIntoView({ behavior: "smooth" });
  });
  $("#jobList").addEventListener("toggle", event => {
    const card = event.target.closest(".job-card");
    if (!card) return;
    if (card.open) {
      expandedJobIds.add(card.dataset.jobId);
      const video = $(".job-video", card);
      if (video && !video.getAttribute("src")) video.src = video.dataset.src;
    } else {
      expandedJobIds.delete(card.dataset.jobId);
      const video = $(".job-video", card);
      if (video) video.pause();
    }
  }, true);
  $("#jobList").addEventListener("click", async event => {
    const cancelButton = event.target.closest("[data-job-cancel]");
    const renameButton = event.target.closest("[data-job-rename]");
    if (cancelButton) {
      try { await api(`/api/jobs/${cancelButton.dataset.jobCancel}/cancel`, { method: "POST" }); toast("已送出取消要求"); loadJobs(true); }
      catch (error) { toast(error.message, true); }
    }
    if (renameButton) {
      const name = prompt("輸入任務名稱（最多 80 個字）", renameButton.dataset.jobName || "");
      if (name === null) return;
      try {
        await api(`/api/jobs/${renameButton.dataset.jobRename}/rename`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        toast("任務名稱已更新。");
        loadJobs(true);
      } catch (error) { toast(error.message, true); }
    }
  });
}

async function prepareKeyframeAsset(sourceAsset) {
  const result = await api("/api/keyframes/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset_id: sourceAsset.source_asset_id || sourceAsset.id,
      aspect_ratio: $("#aspectRatio").value,
      megapixels: Number($("#megapixels").value),
      fit_mode: $("#keyframeFit").value,
    }),
  });
  return result.prepared_asset;
}

function keyframeNeedsRefresh(asset) {
  if (!asset) return false;
  const [width, height] = dimensions();
  return asset.width !== width || asset.height !== height || asset.fit_mode !== $("#keyframeFit").value || asset.background_mode !== "chroma_green";
}

async function refreshKeyframes() {
  if (state.mode !== "fl2va" || (!state.firstImage && !state.lastImage)) return;
  if (!keyframeNeedsRefresh(state.firstImage) && !keyframeNeedsRefresh(state.lastImage)) return;
  const version = ++keyframePrepareVersion;
  try {
    const prepared = await Promise.all([
      keyframeNeedsRefresh(state.firstImage) ? prepareKeyframeAsset(state.firstImage) : state.firstImage,
      keyframeNeedsRefresh(state.lastImage) ? prepareKeyframeAsset(state.lastImage) : state.lastImage,
    ]);
    if (version !== keyframePrepareVersion) return;
    [state.firstImage, state.lastImage] = prepared;
    renderKeyframePreview("first", state.firstImage, "起始圖片", false);
    renderKeyframePreview("last", state.lastImage, "結束圖片", true);
    updateSummary();
    saveState();
    toast("首尾圖片已依新比例重新適配。");
  } catch (error) {
    if (version === keyframePrepareVersion) toast(error.message, true);
  }
}

async function setKeyframe(target, file) {
  try {
    const sourceAsset = await uploadFile(file, `${target}-frame-source`);
    const asset = await prepareKeyframeAsset(sourceAsset);
    state[target === "first" ? "firstImage" : "lastImage"] = asset;
    renderKeyframePreview(target, asset, target === "first" ? "起始圖片" : "結束圖片", target === "last");
    updateSummary(); saveState();
    toast(asset.transparency_filled ? "透明背景已填入螢光綠，並依輸出比例完成適配。" : "圖片已依輸出比例完成適配。");
  } catch (error) { toast(error.message, true); }
}

function initialize() {
  restoreForm();
  bindEvents();
  renderKeyframePreview("first", state.firstImage, "起始圖片", false);
  renderKeyframePreview("last", state.lastImage, "結束圖片", true);
  renderContinuation();
  setMode(state.mode || "t2v");
  renderReferences();
  renderStoryboards();
  updateSummary();
  loadConnectionSettings().catch(error => console.warn(error));
  checkStatus();
  loadJobs(true);
  setInterval(checkStatus, 10000);
  setInterval(() => loadInstallerStatus().catch(error => console.warn(error)), 2500);
  setInterval(() => engineStartingAt && showEngineStarting(), 1000);
  setInterval(loadJobs, 3000);
}

initialize();
