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
  mgReferences: [
    { id: "mg-background", alias: "背景圖", type: "background", description: "MG 底板與場景空間；維持原始構圖與可讀性", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" },
    { id: "mg-reels", alias: "轉輪帶", type: "object", description: "可見的轉輪窗、等尺寸停輪格與圖騰配置", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" },
    { id: "mg-character", alias: "角色", type: "character", description: "MG 畫面中的主要角色；保持臉部、服裝、比例與身份一致", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" },
  ],
  mgAnimation: {
    safeDefaultsApplied: false,
    characterPosition: "right",
    characterPositionDetail: "位於轉輪右側，不遮擋圖騰、標題、分數與 JP 數值",
    characterMotion: "角色先做克制的預備動作；停輪時視線追隨轉輪結果，重心自然轉移；中獎後做一次清楚的開心反應，衣袖與飾品延遲跟隨，最後穩定收勢。",
    reelMotionModel: "continuous",
    reelDirection: "top_down",
    reelStopOrder: "left_right",
    reelStopStagger: 0.18,
    reelMotion: "轉輪先平滑加速，再保持穩定速度；各軸依序減速，停輪時有短促機械回彈，但每格尺寸、中心與遮罩保持一致。",
    symbolPostStopMotion: "所有軸完全停穩後，中獎圖騰才做一次 1.00～1.05 倍的呼吸放大與乾淨高光掃過，隨後回到原本格位中心；其他圖騰保持穩定。",
    backgroundMotionLevel: "subtle",
    backgroundMotion: "遠景只做低對比、低幅度的環境光流動與少量粒子漂移，不與轉輪、角色搶焦，也不改變底板版面。",
    cameraMotion: "static",
    cameraMotionDetail: "固定鏡頭，不推拉、不平移、不晃動，完整保留轉輪窗與角色安全區。",
  },
  storyboards: [],
  replacement: {
    alias: "新角色",
    target: "動態參考影片中的主要角色",
    description: "",
    images: [],
    video: null,
    videoUseAudio: true,
    videoInfo: null,
    segmentPlan: [],
    autoSplit: true,
    continuity: true,
    splitStrategy: "smart",
    audioMode: "original",
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
  modePrompts: {},
  promptTemplateSnapshots: {},
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
let sharedGatewayStatus = null;
let installerPreflightData = null;
let lastInstallerStatus = "idle";
let engineModelInventory = {};
let musicMode = "instrumental";
let musicModelsInstalled = false;
let musicPage = 1;
let musicTotalPages = 1;
let lastMusicJobsSignature = "";

const modeLabels = { t2v: "文生影片", fl2va: "首尾圖片", r2v: "多模態參考", replace: "角色替換", symbol_loop: "圖騰循環", extend: "續接影片", popup_panel: "彈窗面板動畫", mg_animation: "MG 動畫" };
const promptGuideModeAdvice = {
  t2v: ["文生影片 · T2VA", "不使用參考圖片，直接描述完整的畫面、動作、鏡頭、對白與聲音時間線。"],
  r2v: ["多模態參考 · Ref2VA", "在敘述中直接使用素材名稱；工具會自動建立 Subject、Picture、Video 與 Audio 對應。"],
  replace: ["角色替換 · Ref2VA 影片編輯", "說明新角色要接手的原角色位置與表演，保留原場景、鏡頭、道具及其他人物。"],
  symbol_loop: ["圖騰循環 · FL2VA", "同一張擴邊圖片作為首尾錨點；只完成一個動作週期並平順回到起始狀態。"],
  extend: ["續接影片 · I2VA／Ref2VA", "只用尾幀時延續姿勢與動量；保留原影片作參考時則同時描述 video continuation 關係。"],
  popup_panel: ["彈窗面板 · Ref2VA", "背景全程固定，只讓面板、分數、按鈕、壓暗層、裝飾與特效依時間表演。"],
  mg_animation: ["MG 動畫 · Ref2VA", "角色、可見轉輪窗與背景分層描述；先定義空間位置，再安排旋轉、停輪、停輪後圖騰表演與最後收勢。"],
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
    restored.mgReferences = (restored.mgReferences || structuredClone(defaultState.mgReferences)).map(item => ({
      images: [], audio: null, video: null, videoUseAudio: false, voiceMode: "timbre", description: "", ...item,
    }));
    restored.mgAnimation = { ...structuredClone(defaultState.mgAnimation), ...(restored.mgAnimation || {}) };
    restored.storyboards = (restored.storyboards || []).map(shot => ({ motionBeats: "", effects: "", guideMode: "reference", ...shot }));
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
  element.className = "toast";
  void element.offsetWidth;
  element.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.className = "toast", 3300);
}

function replayAnimation(element, className) {
  if (!element || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
  element.addEventListener("animationend", () => element.classList.remove(className), { once: true });
}

function setButtonBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

function animateModeInterface(selectedCard) {
  replayAnimation(selectedCard, "mode-selected");
  $$(".editor-column > .panel:not(.hidden)").forEach((panel, index) => {
    panel.style.setProperty("--enter-delay", `${Math.min(index * 34, 136)}ms`);
    replayAnimation(panel, "interface-enter");
  });
}

function installInteractionMotion() {
  document.addEventListener("pointerdown", event => {
    if (event.button !== 0 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const control = event.target.closest("button:not(:disabled), a.button, .mode-card");
    if (!control) return;
    const bounds = control.getBoundingClientRect();
    const ripple = document.createElement("span");
    ripple.className = "interaction-ripple";
    ripple.style.left = `${event.clientX - bounds.left}px`;
    ripple.style.top = `${event.clientY - bounds.top}px`;
    control.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
  });
  document.addEventListener("click", event => {
    const control = event.target.closest("button:not(:disabled), a.button");
    if (control && !control.classList.contains("mode-card")) replayAnimation(control, "interaction-pop");
  });
  requestAnimationFrame(() => document.body.classList.add("ui-ready"));
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
    remote_access_token: $("#connectionRemoteToken").value.trim(),
  };
}

function refreshConnectionFields() {
  const remote = $("input[name='connectionMode']:checked")?.value === "remote";
  $("#comfyDirField").classList.toggle("hidden", remote);
  $("#autoStartField").classList.toggle("hidden", remote);
  $("#remoteTokenField").classList.toggle("hidden", !remote);
  $("#localInstallerSection").classList.toggle("hidden", remote);
  $("#connectionHint").textContent = remote
    ? "請填 GPU 主機管理者提供的 Gateway 網址（預設 8190）與個人金鑰。不要連接或公開原始 ComfyUI 8188 連接埠。"
    : "本機模式會使用這台電腦的模型；開啟自動啟動後，面板可代為啟動指定資料夾內的 ComfyUI。";
}

function applyStudioRole(settings = connectionSettings || {}) {
  const host = settings.studio_role === "host";
  const badge = $("#studioRoleBadge");
  badge.className = `studio-role-badge ${host ? "host" : "client"}`;
  badge.textContent = host ? "管理主機" : "一般使用者";
  $("#openGatewaySettings").classList.toggle("hidden", !host);
  const card = $("#studioRoleCard");
  card.className = `studio-role-card ${host ? "host" : "client"}`;
  $("#studioRoleIcon").textContent = host ? "ADMIN" : "USER";
  $("#studioRoleTitle").textContent = host ? "共享引擎管理主機" : "一般使用者工作站";
  $("#studioRoleDescription").textContent = host
    ? "可以啟用共享 GPU、建立／停用使用者及換發個人金鑰；原始 ComfyUI 仍維持本機封閉。"
    : "只能使用自己的本機引擎，或以個人金鑰連線管理主機；無法建立或管理共享金鑰。";
  if (!host && !$("#gatewayModal").classList.contains("hidden")) closeGatewaySettings();
}

async function loadConnectionSettings(openModal = false) {
  connectionSettings = await api("/api/connection");
  applyStudioRole(connectionSettings);
  const radio = $(`input[name='connectionMode'][value='${connectionSettings.mode}']`);
  if (radio) radio.checked = true;
  $("#connectionUrl").value = connectionSettings.base_url || "http://127.0.0.1:8188";
  $("#connectionComfyDir").value = connectionSettings.comfy_dir || "";
  $("#connectionAutoStart").checked = Boolean(connectionSettings.auto_start_local);
  $("#connectionRemoteToken").value = "";
  $("#connectionRemoteToken").placeholder = connectionSettings.has_remote_access_token
    ? "已儲存金鑰；留白可沿用"
    : "貼上 GPU 主機管理者提供的 h3g_... 金鑰";
  refreshConnectionFields();
  if (openModal) $("#connectionModal").classList.remove("hidden");
  await loadInstallerStatus().catch(error => console.warn(error));
}

function closeConnectionSettings() {
  $("#connectionModal").classList.add("hidden");
}

function renderGatewayStatus(status) {
  sharedGatewayStatus = status;
  $("#gatewayEnabled").checked = Boolean(status.enabled);
  $("#gatewayPort").value = status.port || 8190;
  const runtime = $("#gatewayRuntime");
  runtime.className = `gateway-runtime${status.running ? " running" : status.last_error ? " error" : ""}`;
  runtime.querySelector("strong").textContent = status.running
    ? `Gateway 運作中 · 共用 ComfyUI ${status.upstream_url}`
    : status.last_error || (status.enabled ? "Gateway 啟動失敗" : "Gateway 尚未啟用");
  $("#gatewayUrlList").innerHTML = (status.urls || []).map(url => `
    <div class="gateway-url-item">
      <code>${escapeHtml(url)}</code>
      <button class="button ghost small" type="button" data-gateway-copy-url="${escapeHtml(url)}">複製</button>
    </div>`).join("") || '<div class="gateway-empty">目前沒有可用網址。</div>';
  $("#gatewayUserList").innerHTML = (status.users || []).map(user => `
    <article class="gateway-user${user.enabled ? "" : " disabled"}">
      <div><strong>${escapeHtml(user.name)}</strong><small>ID ${escapeHtml(user.id)} · ${user.enabled ? "可使用" : "已停用"}</small></div>
      <div class="gateway-user-actions">
        <button class="button ghost small" type="button" data-gateway-rotate="${escapeHtml(user.id)}">換發金鑰</button>
        <button class="button ${user.enabled ? "danger" : "secondary"} small" type="button" data-gateway-enable="${escapeHtml(user.id)}" data-enabled="${user.enabled ? "false" : "true"}">${user.enabled ? "停用" : "重新啟用"}</button>
      </div>
    </article>`).join("") || '<div class="gateway-empty">尚未建立使用者。請為每位同事建立獨立帳號。</div>';
}

async function loadGatewayStatus(openModal = false) {
  renderGatewayStatus(await api("/api/gateway/status"));
  if (openModal) {
    $("#gatewayModal").classList.remove("hidden");
    requestAnimationFrame(() => $("#gatewayModal .modal-close").focus());
  }
}

function closeGatewaySettings() {
  $("#gatewayModal").classList.add("hidden");
  $("#gatewayTokenReveal").classList.add("hidden");
}

function revealGatewayToken(result) {
  $("#gatewayTokenTitle").textContent = `${result.user.name} 的個人金鑰（只顯示一次）`;
  $("#gatewayTokenValue").textContent = result.token;
  $("#gatewayTokenReveal").classList.remove("hidden");
  $("#gatewayTokenReveal").scrollIntoView({ behavior: "smooth", block: "center" });
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

function randomMusicSeed() {
  return Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
}

function setMusicMode(mode) {
  musicMode = mode === "song" ? "song" : "instrumental";
  $$('[data-music-mode]').forEach(button => button.classList.toggle("active", button.dataset.musicMode === musicMode));
  $("#musicVocalsField").classList.toggle("hidden", musicMode !== "song");
  $("#musicLyricsField").classList.toggle("hidden", musicMode !== "song");
}

function openMusicStudio() {
  $("#musicModal").classList.remove("hidden");
  if (!$("#musicSeed").value) $("#musicSeed").value = randomMusicSeed();
  loadMusicStatus().catch(error => toast(error.message, true));
  loadMusicJobs(true).catch(error => toast(error.message, true));
  requestAnimationFrame(() => $("#musicJobName").focus());
}

function closeMusicStudio() {
  $("#musicModal").classList.add("hidden");
  $$("#musicJobList audio").forEach(audio => audio.pause());
}

function humanBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function renderMusicStatus(data) {
  musicModelsInstalled = Boolean(data.installed);
  const dot = $("#musicStatusDot");
  dot.className = `music-status-dot${data.installed ? " ready" : data.active ? " active" : data.error ? " error" : ""}`;
  $("#musicModelTitle").textContent = data.installed
    ? "Music 3 INT8 模型已就緒"
    : data.active ? (data.current || "正在下載 Music 3 模型")
    : data.error ? "Music 3 模型安裝失敗"
    : "尚未安裝 Music 3 模型";
  const speed = data.active && data.speed_bps ? ` · ${humanBytes(data.speed_bps)}/s` : "";
  $("#musicModelDetail").textContent = data.error || `${humanBytes(data.downloaded)} / ${humanBytes(data.total)} · ${Number(data.progress || 0).toFixed(1)}%${speed}`;
  $("#musicInstallProgress").classList.toggle("hidden", !data.active && !data.downloaded);
  $("#musicInstallBar").style.width = `${Math.max(0, Math.min(100, Number(data.progress) || 0))}%`;
  $("#installMusicModels").classList.toggle("hidden", data.installed || data.active);
  $("#installMusicModels").disabled = !data.can_install;
  $("#installMusicModels").textContent = data.can_install ? "安裝 Music 3 模型" : "遠端主機需自行安裝";
  $("#cancelMusicInstall").classList.toggle("hidden", !data.active);
  $("#generateMusic").disabled = !data.installed;
}

async function loadMusicStatus() {
  const data = await api("/api/music/status");
  renderMusicStatus(data);
  return data;
}

function collectMusicPayload() {
  return {
    job_name: $("#musicJobName").value.trim(),
    mode: musicMode,
    use_case: $("#musicUseCase").value.trim(),
    genre: $("#musicGenre").value.trim(),
    mood: $("#musicMood").value.trim(),
    bpm: $("#musicBpm").value,
    key: $("#musicKey").value.trim(),
    duration: Number($("#musicDuration").value),
    instruments: $("#musicInstruments").value.trim(),
    structure: $("#musicStructure").value.trim(),
    vocals: $("#musicVocals").value.trim(),
    lyrics: $("#musicLyrics").value.trim(),
    production: $("#musicProduction").value.trim(),
    avoid: $("#musicAvoid").value.trim(),
    details: $("#musicDetails").value.trim(),
    seed: $("#musicSeed").value.trim(),
    format: $("#musicFormat").value,
    tiled_decode: $("#musicTiledDecode").checked,
  };
}

async function generateMusic() {
  const button = $("#generateMusic");
  setButtonBusy(button, true);
  button.textContent = "正在加入 GPU 佇列...";
  try {
    const job = await api("/api/music/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectMusicPayload()),
    });
    toast(`音樂工作 ${job.id.slice(0, 8)} 已加入佇列`);
    $("#musicSeed").value = randomMusicSeed();
    musicPage = 1;
    await loadMusicJobs(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setButtonBusy(button, false);
    button.textContent = "♫ 產生音樂";
    button.disabled = !musicModelsInstalled;
  }
}

function musicJobElapsed(job) {
  if (Number.isFinite(Number(job.execution_seconds))) return formatExecutionTime(job.execution_seconds);
  if (["preparing", "running"].includes(job.status) && job.generation_started_at) {
    return formatExecutionTime((Date.now() - new Date(job.generation_started_at).getTime()) / 1000);
  }
  return `${Number(job.duration || 0)} 秒`;
}

async function loadMusicJobs(force = false) {
  if ($("#musicModal").classList.contains("hidden") && !force) return;
  const data = await api(`/api/music/jobs?page=${musicPage}&page_size=20`);
  musicPage = data.page;
  musicTotalPages = data.total_pages;
  const signature = JSON.stringify(data.items.map(job => [job.id, job.status, job.progress, job.updated_at, job.name, job.favorite]));
  if (!force && signature === lastMusicJobsSignature) return;
  lastMusicJobsSignature = signature;
  $("#musicPageLabel").textContent = `${musicPage} / ${musicTotalPages}`;
  $("#previousMusicPage").disabled = musicPage <= 1;
  $("#nextMusicPage").disabled = musicPage >= musicTotalPages;
  $("#musicJobList").innerHTML = data.items.length ? data.items.map(job => {
    const active = ["queued", "preparing", "running"].includes(job.status);
    const created = job.created_at ? new Date(job.created_at).toLocaleString("zh-TW", { hour12: false }) : "";
    return `<article class="music-job${job.favorite ? " favorite" : ""}" data-music-job="${job.id}">
      <div class="music-job-head"><div class="music-job-title"><strong>${escapeHtml(job.name || (job.mode === "song" ? "未命名歌曲" : "未命名純音樂"))}</strong><small>${created} · ${escapeHtml(job.format || "mp3").toUpperCase()} · Seed ${escapeHtml(job.seed)}</small></div>
      <div class="music-job-controls"><button type="button" data-music-favorite="${job.id}" data-favorite="${Boolean(job.favorite)}" class="${job.favorite ? "active" : ""}" title="我的最愛">★</button><button type="button" data-music-rename="${job.id}" data-name="${escapeHtml(job.name || "")}" title="重新命名">✎</button></div></div>
      <div class="music-job-status"><b class="${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</b><span>${escapeHtml(job.current_node || musicJobElapsed(job))}${active ? ` · ${Math.round(Number(job.progress) || 0)}%` : ""}</span></div>
      ${active ? `<div class="progress-track"><span style="width:${Math.max(2, Number(job.progress) || 2)}%"></span></div>` : ""}
      ${job.status === "completed" ? `<audio controls preload="none" src="/api/music/jobs/${job.id}/audio"></audio>` : ""}
      ${job.error ? `<div class="music-job-error">${escapeHtml(job.error)}</div>` : ""}
      <div class="music-job-actions">
        ${job.status === "completed" ? `<a class="button secondary" href="/api/music/jobs/${job.id}/audio?download=1" download>下載</a>` : ""}
        ${active ? `<button class="button ghost" type="button" data-music-cancel="${job.id}">取消</button>` : ""}
        ${["failed", "cancelled", "interrupted"].includes(job.status) ? `<button class="button secondary" type="button" data-music-resume="${job.id}">重新送出</button>` : ""}
        <button class="button ghost" type="button" data-music-caption="${job.id}">查看提示詞</button>
      </div>
      <pre class="job-recipe hidden" data-music-caption-panel="${job.id}">${escapeHtml(job.caption || "")}${job.mode === "song" ? `\n\nLyrics:\n${escapeHtml(job.lyrics || "")}` : ""}</pre>
    </article>`;
  }).join("") : '<div class="empty-state">尚無音樂工作</div>';
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
  $("#installLocalEngine").textContent = installerPreflightData?.installed
    ? "套用此本機引擎"
    : installerPreflightData?.environment_repair_required ? "修復這台電腦的引擎環境" : "開始一鍵安裝";
}

function renderInstallerPreflight(data) {
  installerPreflightData = data;
  const panel = $("#installerPreflight");
  const lines = [];
  lines.push(`GPU：${data.gpu ? `${data.gpu.name} · ${data.gpu.vram_gb} GB VRAM` : "未偵測到 NVIDIA GPU"}`);
  lines.push(`記憶體：${data.ram_gb ?? "未知"} GB · 可用磁碟：${data.disk_free_gb} GiB`);
  lines.push(`本次還需要：約 ${data.required_gb} GiB · 模型完成：${data.models.filter(item => item.ready).length} / ${data.models.length}`);
  if (data.environment?.ready) lines.push(`✓ Python 環境可在這台電腦執行：${data.environment.executable}`);
  else if (data.environment_repair_required) lines.push("需要修復：偵測到從其他電腦複製來的 Python 環境；只會重建 .venv，不會刪除模型或生成檔。");
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
    if ((state.mode === "popup_panel" && item.id === "popup-background") || (state.mode === "mg_animation" && item.id === "mg-background")) {
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
    quality_mode: $("#qualityMode").value,
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
    qualityMode: form.quality_mode || "native",
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

function isReferenceMode(mode = state.mode) {
  return ["r2v", "replace", "popup_panel", "mg_animation"].includes(mode);
}

function turboProfile(width, height) {
  if (isReferenceMode()) return {
    key: "ref2v_544",
    steps: 4,
    title: "Ref2VA Turbo · 4 steps",
    hint: "多模態參考加速；鎖定 Euler、simple、Shift 12/3 與 match。建議先用 0.4～0.7MP 預覽角色一致性。",
  };
  if (width === 1344 && height === 768) return {
    key: "fl2v_768",
    steps: 4,
    title: "FL2VA Turbo 768p · 4 steps",
    hint: "針對 1344 × 768 訓練；鎖定 Euler、simple 與 Shift 6/3。",
  };
  return {
    key: "fl2v_544",
    steps: 8,
    title: "FL2VA Turbo · 8 steps",
    hint: "適用文生、首尾、循環與續接預覽；鎖定 Euler、simple 與 Shift 12/3。",
  };
}

function syncQualityMode(width, height) {
  const turbo = $("#qualityMode").value === "turbo";
  const profile = turboProfile(width, height);
  $("#steps").disabled = turbo;
  $("#scheduler").disabled = turbo;
  $("#refImageSize").disabled = turbo && isReferenceMode();
  if (turbo) {
    $("#steps").value = profile.steps;
    $("#scheduler").value = "simple";
    if (isReferenceMode()) $("#refImageSize").value = "match";
    const availability = engineModelInventory[`turbo_${profile.key}`];
    const modelNote = availability === true ? " Turbo LoRA 已就緒。" : availability === false ? " 目前引擎尚未偵測到這個 Turbo LoRA。" : "";
    $("#qualityModeTitle").textContent = profile.title;
    $("#qualityModeHint").textContent = profile.hint + modelNote;
  } else {
    $("#qualityModeTitle").textContent = "原生品質模式";
    $("#qualityModeHint").textContent = "保留目前採樣設定，適合正式成品；不載入 Turbo LoRA。";
  }
}

function changeQualityMode() {
  const turbo = $("#qualityMode").value === "turbo";
  if (turbo) {
    state.nativeSampling = {
      steps: Number($("#steps").value) || 20,
      scheduler: $("#scheduler").value,
      refImageSize: $("#refImageSize").value,
    };
  } else {
    const saved = state.nativeSampling || {};
    $("#steps").value = saved.steps || 20;
    $("#scheduler").value = saved.scheduler || (isReferenceMode() ? "beta" : "simple");
    $("#refImageSize").value = saved.refImageSize || "match";
  }
  updateSummary();
}

function activeReferences() {
  if (state.mode === "popup_panel") return state.popupReferences;
  if (state.mode === "mg_animation") return state.mgReferences;
  return state.references;
}

function lockedReferenceIds() {
  if (state.mode === "popup_panel") return new Set(["popup-background", "popup-panel"]);
  if (state.mode === "mg_animation") return new Set(["mg-background", "mg-reels", "mg-character"]);
  return new Set();
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
  syncQualityMode(width, height);
  updateKeyframeLayout();
  const longReplacement = state.mode === "replace" && state.replacement.autoSplit && Number(state.replacement.videoInfo?.duration) > 15;
  const displayDuration = longReplacement ? Number(state.replacement.videoInfo.duration) : actualDuration();
  const segmentSuffix = longReplacement ? ` · 自動 ${state.replacement.segmentPlan.length} 段` : "";
  $("#dimensionPreview").textContent = `預計輸出 ${width} × ${height} · 24 FPS · 約 ${displayDuration.toFixed(2)} 秒${segmentSuffix}`;
  $("#summaryMode").textContent = modeLabels[state.mode];
  $("#summarySize").textContent = `${width} × ${height}`;
  $("#summaryDuration").textContent = `約 ${displayDuration.toFixed(2)} 秒${segmentSuffix}`;
  const motionProfile = $("#motionProfile").value;
  $("#summaryMotion").textContent = `${motionLabels[motionProfile]} · ${$("#motionIntensity").value}`;
  $("#motionPresetHint").textContent = motionHints[motionProfile];
  $("#summaryAssets").textContent = `${assetCount()} 個`;
  $("#promptCount").textContent = `${$("#prompt").value.length} 字`;
  renderPromptKeywords();
  const filenameStem = filenameStemPreview($("#jobName").value);
  $("#filenamePreview").textContent = filenameStem ? `${filenameStem}_00001.mp4` : "未命名時：年-月-日_時-分-秒_微秒_00001.mp4";
  persistForm();
}

function setMode(mode) {
  const previousMode = state.mode;
  state.modePrompts ||= {};
  state.promptTemplateSnapshots ||= {};
  if (previousMode !== mode) {
    state.modePrompts[previousMode] = $("#prompt").value;
  }
  state.mode = mode;
  if (previousMode !== mode) {
    const savedPrompt = state.modePrompts[mode];
    if (savedPrompt !== undefined) {
      $("#prompt").value = savedPrompt;
    } else {
      applyPromptTemplate(mode);
    }
  }
  if (["extend", "r2v"].includes(mode)) $("#referencePanel").before($("#continuationPanel"));
  $$(".mode-card").forEach(card => card.classList.toggle("active", card.dataset.mode === mode));
  $("#keyframePanel").classList.toggle("hidden", mode !== "fl2va");
  $("#referencePanel").classList.toggle("hidden", !["r2v", "popup_panel", "mg_animation"].includes(mode));
  $("#mgDirectorPanel").classList.toggle("hidden", mode !== "mg_animation");
  $("#replacementPanel").classList.toggle("hidden", mode !== "replace");
  $("#symbolLoopPanel").classList.toggle("hidden", mode !== "symbol_loop");
  $("#continuationPanel").classList.toggle("hidden", !["extend", "r2v"].includes(mode));
  $("#refSizeWrap").classList.toggle("hidden", !["r2v", "replace", "popup_panel", "mg_animation"].includes(mode));
  $("#promptStep").textContent = mode === "t2v" ? "03" : mode === "mg_animation" ? "05" : "04";
  $("#storyboardStep").textContent = mode === "mg_animation" ? "06" : "05";
  $("#storyboardHint").textContent = ["r2v", "mg_animation"].includes(mode)
    ? "圖片可選柔性構圖參考，或用官方 Guide 精確錨定在本段開始"
    : "分鏡圖片會用官方 Guide 精確錨定在本段開始時間";
  if (isReferenceMode(mode) && $("#qualityMode").value !== "turbo" && $("#scheduler").value === "simple") $("#scheduler").value = "beta";
  if (!isReferenceMode(mode) && $("#qualityMode").value !== "turbo" && $("#scheduler").value === "beta") $("#scheduler").value = "simple";
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
  if (mode === "mg_animation" && !state.mgAnimation.safeDefaultsApplied) {
    $("#duration").value = "5";
    $("#motionProfile").value = "none";
    $("#motionIntensity").value = "2";
    $("#cameraResponse").value = "stable";
    state.mgAnimation.safeDefaultsApplied = true;
  }
  if (!$("#prompt").value.trim()) applyPromptTemplate(mode);
  const popupMode = mode === "popup_panel";
  const mgMode = mode === "mg_animation";
  $("#promptPanelTitle").textContent = mgMode ? "整體情境補充（選填）" : "影片敘述";
  $("#promptPanelDescription").textContent = mgMode
    ? "上方三層動態會自動整合；這裡只補充跨圖層事件、故事與聲音"
    : "描述事件、動作、鏡頭、台詞和聲音";
  $("#mgPromptInfo").classList.toggle("hidden", !mgMode);
  $("#prompt").placeholder = mgMode
    ? "例如：第三軸停輪後出現中獎結果，角色立刻看向中獎圖騰並歡呼；背景光線同步亮起，播放停輪聲與短促中獎音效。若沒有額外需求，可保留預設內容。"
    : "例如：小明走進神殿，看見金色佛像後停下腳步。攝影機從背後緩慢推進，小明轉頭說：「有人在這裡嗎？」背景只有微弱風聲與遠處鐘聲。";
  $("#insertPromptTemplate").textContent = mgMode ? "插入整體補充範本" : "插入提示詞範本";
  $("#referencePanelTitle").textContent = popupMode ? "彈窗面板素材" : mgMode ? "MG 分層素材" : "角色與參考素材";
  $("#referencePanelDescription").textContent = popupMode ? "背景圖固定；面板與其他表演素材可自由擴充" : mgMode ? "背景圖、轉輪帶與角色各自保留身份；仍可新增其他素材" : "在敘述詞中直接使用名稱代號";
  $("#referenceInfoTitle").textContent = popupMode ? "背景鎖定" : mgMode ? "三層分工" : "自動映射";
  $("#referenceInfoText").textContent = popupMode ? "背景圖全程固定不動；面板、分數、按鈕、特效與新增素材都可以獨立表演。" : mgMode ? "背景圖負責環境，轉輪帶只控制可見轉輪窗，角色依指定方位表演；不要改動數學轉輪表與圖騰機率。" : "圖片、動作影片與聲音會自動轉成模型參考標籤，不需要手動編號。";
  $("#addReference").classList.remove("hidden");
  state.replacement.defaultPrompt ||= mode === "replace" ? replacementPrompt() : "";
  renderReplacement();
  renderSymbolLoop();
  renderContinuation();
  renderMgAnimation();
  renderReferences();
  renderStoryboards();
  updateSummary();
  saveState();
  if (mode === "fl2va") refreshKeyframes();
  if (previousMode !== mode) requestAnimationFrame(() => animateModeInterface($(`.mode-card[data-mode='${mode}']`)));
}

function renderKeyframePreview(target, asset, label, optional) {
  const element = $(`#${target}ImagePreview`);
  element.classList.toggle("has-image", Boolean(asset));
  element.style.backgroundImage = asset ? `url("${asset.url}")` : "";
  const sourceName = asset?.source_name || asset?.name || "";
  const size = asset?.width && asset?.height ? `${asset.width} × ${asset.height}` : "";
  element.innerHTML = asset
    ? `<button class="keyframe-remove" type="button" data-remove-keyframe="${target}" aria-label="移除${escapeHtml(label)}" title="移除${escapeHtml(label)}">×</button><strong>${escapeHtml(label)}</strong><small>${escapeHtml(sourceName)}${size ? ` · ${size}` : ""}</small>`
    : `<span class="upload-plus">＋</span><strong>${escapeHtml(label)}</strong><small>${optional ? "選填" : "點擊或拖入圖片"}</small>`;
}

function clearKeyframe(target) {
  const stateKey = target === "first" ? "firstImage" : "lastImage";
  const label = target === "first" ? "起始圖片" : "結束圖片";
  if (!state[stateKey]) return;
  keyframePrepareVersion++;
  state[stateKey] = null;
  refreshPromptTemplateIfUntouched();
  const input = $(`#${target}ImageInput`);
  if (input) input.value = "";
  renderKeyframePreview(target, null, label, target === "last");
  updateSummary();
  saveState();
  toast(`${label}已移除。`);
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
  $("#replacementAutoSplit").checked = replacement.autoSplit !== false;
  $("#replacementContinuity").checked = replacement.continuity !== false;
  $("#replacementSplitStrategy").value = replacement.splitStrategy || "smart";
  $("#replacementAudioMode").value = replacement.audioMode || "original";
  const infoPanel = $("#replacementVideoInfo");
  if (replacement.video && replacement.videoInfo) {
    const info = replacement.videoInfo;
    const count = replacement.segmentPlan?.length || 1;
    const audio = info.has_audio ? "含原始音軌" : "沒有音軌";
    const long = Number(info.duration) > 15;
    infoPanel.classList.remove("empty");
    infoPanel.innerHTML = `<strong>${long ? `長片 · 預計 ${count} 段` : "單段影片"}</strong><span>${info.width} × ${info.height} · ${Number(info.fps).toFixed(2)} FPS · ${Number(info.duration).toFixed(2)} 秒 · ${audio}${long ? " · 每段含 0.5 秒連續性重疊" : ""}</span>`;
  } else {
    infoPanel.classList.add("empty");
    infoPanel.innerHTML = `<strong>尚未分析來源影片</strong><span>上傳後會顯示尺寸、時長、音軌與預計片段數。</span>`;
  }
  $("#duration").disabled = state.mode === "replace" && replacement.autoSplit !== false && Number(replacement.videoInfo?.duration) > 15;
}

async function prepareReplacementVideo() {
  if (!state.replacement.video) return;
  const result = await api("/api/replacement/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset_id: state.replacement.video.id,
      strategy: state.replacement.splitStrategy || "smart",
    }),
  });
  state.replacement.videoInfo = result.source;
  state.replacement.segmentPlan = result.segments || [];
  if (Number(result.source.duration) <= 15) {
    $("#duration").value = Math.max(5, Math.min(15, Number(result.source.duration))).toFixed(1);
  }
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
    state.replacement.videoInfo = null;
    state.replacement.segmentPlan = [];
    renderReplacement();
    toast("正在分析影片與智慧切點...");
    await prepareReplacementVideo();
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
  const mgMode = state.mode === "mg_animation";
  const lockedIds = lockedReferenceIds();
  $("#referenceEmpty").classList.toggle("hidden", references.length > 0);
  list.innerHTML = references.map((item, index) => `
    <article class="reference-card" data-reference-id="${item.id}">
      <div class="reference-head">
        <label>名稱代號<input data-ref-field="alias" value="${escapeHtml(item.alias)}" placeholder="例如：小明" ${lockedIds.has(item.id) ? "disabled" : ""}></label>
        <label>素材類型<select data-ref-field="type" ${lockedIds.has(item.id) ? "disabled" : ""}>${Object.entries(typeLabels).map(([value, label]) => `<option value="${value}" ${item.type === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <button class="delete-button ${lockedIds.has(item.id) ? "hidden" : ""}" data-ref-action="delete" title="刪除素材" type="button">×</button>
      </div>
      <div class="reference-body ${(popupMode && item.id === "popup-background") || (mgMode && item.id === "mg-background") ? "single" : ""}">
        <div class="asset-zone" data-drop-kind="images">
          <div class="asset-zone-title"><span>形象圖片 · ${item.images.length}/${((popupMode && item.id === "popup-background") || (mgMode && item.id === "mg-background")) ? 1 : 9}</span><button data-ref-action="add-images" type="button">${item.images.length && ((popupMode && item.id === "popup-background") || (mgMode && item.id === "mg-background")) ? "更換圖片" : "＋加入圖片"}</button></div>
          <div class="thumb-grid">
            ${item.images.length ? item.images.map((asset, assetIndex) => `<div class="asset-thumb" style="background-image:url('${asset.url}')" title="${escapeHtml(asset.name)}"><button data-ref-action="remove-image" data-asset-index="${assetIndex}" type="button">×</button></div>`).join("") : `<span class="asset-placeholder">拖入圖片，或加入正面、側面與全身圖</span>`}
          </div>
        </div>
        <div class="asset-zone ${(popupMode && item.id === "popup-background") || (mgMode && item.id === "mg-background") ? "hidden" : ""}" data-drop-kind="video">
          <div class="asset-zone-title"><span>動作參考影片 · 選填</span><button data-ref-action="add-video" type="button">${item.video ? "更換" : "＋加入影片"}</button></div>
          <div class="thumb-grid">
            ${item.video ? `<div class="asset-thumb video"><span>▶</span><span>${escapeHtml(item.video.name)}</span><button data-ref-action="remove-video" type="button">×</button></div>` : `<span class="asset-placeholder">拖入 MP4、MOV 或 WebM；建議 2–15 秒</span>`}
          </div>
          <label class="check-row"><input data-ref-field="videoUseAudio" type="checkbox" ${item.videoUseAudio ? "checked" : ""}><span>同時參考影片原聲</span></label>
        </div>
        <div class="asset-zone ${(popupMode && item.id === "popup-background") || (mgMode && item.id === "mg-background") ? "hidden" : ""}" data-drop-kind="audio">
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

function renderMgAnimation() {
  const settings = state.mgAnimation;
  const fields = {
    mgCharacterPosition: settings.characterPosition,
    mgCharacterPositionDetail: settings.characterPositionDetail,
    mgCharacterMotion: settings.characterMotion,
    mgReelMotionModel: settings.reelMotionModel,
    mgReelDirection: settings.reelDirection,
    mgReelStopOrder: settings.reelStopOrder,
    mgReelStopStagger: settings.reelStopStagger,
    mgReelMotion: settings.reelMotion,
    mgSymbolPostStopMotion: settings.symbolPostStopMotion,
    mgBackgroundMotionLevel: settings.backgroundMotionLevel,
    mgBackgroundMotion: settings.backgroundMotion,
    mgCameraMotion: settings.cameraMotion,
    mgCameraMotionDetail: settings.cameraMotionDetail,
  };
  for (const [id, value] of Object.entries(fields)) {
    const element = $(`#${id}`);
    if (element && document.activeElement !== element) element.value = value ?? "";
  }
}

function renderStoryboards() {
  const list = $("#storyboardList");
  $("#storyboardEmpty").classList.toggle("hidden", state.storyboards.length > 0);
  let cursor = 0;
  list.innerHTML = state.storyboards.map((shot, index) => {
    const start = cursor;
    cursor += Number(shot.duration) || 0;
    const softReferenceAllowed = ["r2v", "mg_animation"].includes(state.mode);
    const guideMode = softReferenceAllowed ? (shot.guideMode || "reference") : "exact";
    const guideOptions = softReferenceAllowed
      ? `<option value="reference" ${guideMode === "reference" ? "selected" : ""}>提示詞構圖參考</option><option value="exact" ${guideMode === "exact" ? "selected" : ""}>精確錨定本段開始</option>`
      : `<option value="exact" selected>精確錨定本段開始</option>`;
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
        <div class="shot-guide">
          <div class="shot-image ${shot.image ? "has-image" : ""}" data-shot-action="image" style="${shot.image ? `background-image:url('${shot.image.url}')` : ""}">${shot.image ? "已加入" : "＋ 分鏡圖"}</div>
          ${shot.image ? `<button class="shot-image-remove" data-shot-action="remove-image" title="移除分鏡圖" type="button">×</button>` : ""}
          <label>圖片用途<select data-shot-field="guideMode">${guideOptions}</select></label>
          <small>${guideMode === "exact" ? `鎖定 ${start.toFixed(3)} 秒畫面` : "作為柔性構圖參考"}</small>
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
    replacement_auto_split: state.mode === "replace" && state.replacement.autoSplit !== false,
    replacement_continuity: state.mode === "replace" && state.replacement.continuity !== false,
    replacement_split_strategy: state.replacement.splitStrategy || "smart",
    replacement_audio_mode: state.replacement.audioMode || "original",
    replacement_target: state.mode === "replace" ? state.replacement.target.trim() : "",
    mg_animation: {
      character_position: state.mgAnimation.characterPosition,
      character_position_detail: state.mgAnimation.characterPositionDetail.trim(),
      character_motion: state.mgAnimation.characterMotion.trim(),
      reel_motion_model: state.mgAnimation.reelMotionModel,
      reel_direction: state.mgAnimation.reelDirection,
      reel_stop_order: state.mgAnimation.reelStopOrder,
      reel_stop_stagger: Number(state.mgAnimation.reelStopStagger),
      reel_motion: state.mgAnimation.reelMotion.trim(),
      symbol_post_stop_motion: state.mgAnimation.symbolPostStopMotion.trim(),
      background_motion_level: state.mgAnimation.backgroundMotionLevel,
      background_motion: state.mgAnimation.backgroundMotion.trim(),
      camera_motion: state.mgAnimation.cameraMotion,
      camera_motion_detail: state.mgAnimation.cameraMotionDetail.trim(),
    },
    references,
    storyboards: state.storyboards.map(shot => ({
      duration: Number(shot.duration),
      description: shot.description.trim(),
      camera: shot.camera.trim(),
      dialogue: shot.dialogue.trim(),
      sound: shot.sound.trim(),
      motion_beats: shot.motionBeats.trim(),
      effects: shot.effects.trim(),
      image_asset_id: shot.image?.id || null,
      guide_mode: ["r2v", "mg_animation"].includes(state.mode) ? (shot.guideMode || "reference") : "exact",
    })),
  };
}

async function compilePreview() {
  const button = $("#compileButton");
  setButtonBusy(button, true);
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
    setButtonBusy(button, false);
  }
}

async function renderVideo() {
  const unresolvedKeywords = promptKeywords();
  if (unresolvedKeywords.length && !confirm(`提示詞還有 ${unresolvedKeywords.length} 個 {{關鍵字}} 尚未替換：\n\n${unresolvedKeywords.slice(0, 8).join("、")}${unresolvedKeywords.length > 8 ? "…" : ""}\n\n仍要送出生成嗎？`)) return;
  if (state.mode === "replace") {
    const batchDuration = state.replacement.autoSplit && Number(state.replacement.videoInfo?.duration) > 15
      ? Math.max(...(state.replacement.segmentPlan || []).map(segment => Number(segment.input_duration) || 0), 5)
      : Number($("#duration").value);
    const highRisk = batchDuration > 10 || (Number($("#megapixels").value) >= 0.9 && batchDuration > 5);
    if (highRisk && !confirm("目前的解析度／時長對 16GB VRAM 有較高爆顯存風險。建議先改成 0.4MP、5 秒。仍要送出嗎？")) return;
  }
  const button = $("#renderButton");
  setButtonBusy(button, true);
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
    setButtonBusy(button, false);
    button.querySelector("span").textContent = "開始生成影片";
  }
}

function statusLabel(status) {
  return ({ waiting: "等待中", queued: "等待中", preparing: "準備素材", running: "生成中", completed: "已完成", failed: "失敗", cancelled: "已取消", interrupted: "已中斷" })[status] || status;
}

function formatExecutionTime(seconds) {
  if (!Number.isFinite(Number(seconds))) return "尚未記錄";
  const total = Math.max(0, Math.round(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  if (hours) return `${hours} 小時 ${minutes} 分 ${remainder} 秒`;
  if (minutes) return `${minutes} 分 ${remainder} 秒`;
  return `${remainder} 秒`;
}

function batchSegmentsHtml(job) {
  if (job.batch_type !== "replace_long" || !Array.isArray(job.segments)) return "";
  const completed = job.segments.filter(segment => segment.status === "completed").length;
  return `<div class="batch-segments">
    <div class="batch-segments-heading"><strong>完整長片替換進度</strong><span>${completed} / ${job.segments.length} 段完成</span></div>
    ${job.segments.map(segment => {
      const progress = Number(segment.progress) || (segment.status === "completed" ? 100 : 0);
      const range = `${Number(segment.core_start).toFixed(2)}–${Number(segment.core_end).toFixed(2)} 秒`;
      return `<div class="batch-segment"><strong>第 ${segment.index} 段</strong><div><div class="progress-track"><span style="width:${progress}%"></span></div><small>${range}</small></div><span class="batch-segment-status">${escapeHtml(statusLabel(segment.status || "waiting"))}${progress ? ` · ${Math.round(progress)}%` : ""}</span></div>`;
    }).join("")}
  </div>`;
}

function jobExecutionSeconds(job) {
  if (job.execution_seconds !== null && job.execution_seconds !== undefined && Number.isFinite(Number(job.execution_seconds))) return Number(job.execution_seconds);
  if (["preparing", "running"].includes(job.status) && job.generation_started_at) {
    return Math.max(0, (Date.now() - new Date(job.generation_started_at).getTime()) / 1000);
  }
  return null;
}

function recipeAsset(assetId, assets) {
  return assetId && assets[assetId] ? { ...assets[assetId] } : null;
}

function recipeReference(reference, assets, id = uid()) {
  return {
    id,
    alias: reference.alias || "未命名素材",
    type: reference.type || "object",
    description: reference.description || "",
    images: (reference.image_asset_ids || []).map(assetId => recipeAsset(assetId, assets)).filter(Boolean),
    video: recipeAsset(reference.video_asset_id, assets),
    videoUseAudio: Boolean(reference.video_use_audio),
    audio: recipeAsset(reference.audio_asset_id, assets),
    voiceMode: reference.voice_mode || "timbre",
  };
}

function restoreLockedReferences(defaults, references, assets) {
  const restored = references.map(reference => recipeReference(reference, assets));
  const locked = defaults.map(preset => {
    const match = restored.find(item => item.alias.trim() === preset.alias);
    return match ? { ...match, id: preset.id, alias: preset.alias, type: preset.type } : structuredClone(preset);
  });
  const lockedAliases = new Set(defaults.map(item => item.alias));
  return [...locked, ...restored.filter(item => !lockedAliases.has(item.alias.trim()))];
}

async function loadJobRecipe(jobId) {
  return api(`/api/jobs/${jobId}/recipe`);
}

async function showJobPrompt(jobId) {
  const panel = $(`[data-job-recipe-panel="${jobId}"]`);
  if (!panel) return;
  if (!panel.classList.contains("hidden")) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const promptElement = $("[data-job-compiled-prompt]", panel);
  if (panel.dataset.loaded === "true") return;
  promptElement.textContent = "正在載入提示詞…";
  try {
    const recipe = await loadJobRecipe(jobId);
    promptElement.textContent = recipe.compiled_prompt || recipe.request?.prompt || "這筆工作沒有提示詞。";
    panel.dataset.loaded = "true";
  } catch (error) {
    promptElement.textContent = error.message;
    toast(error.message, true);
  }
}

async function applyJobRecipe(jobId) {
  const recipe = await loadJobRecipe(jobId);
  const raw = recipe.request || {};
  const assets = recipe.assets || {};
  const mode = raw.mode || "t2v";
  keyframePrepareVersion++;
  state.firstImage = null;
  state.lastImage = null;
  setMode(mode);
  $("#qualityMode").value = raw.quality_mode || "native";

  const formFields = {
    aspectRatio: "aspect_ratio", megapixels: "megapixels", duration: "duration", seed: "seed",
    steps: "steps", scheduler: "scheduler", refImageSize: "ref_image_size", qualityMode: "quality_mode", keyframeFit: "keyframe_fit",
    motionProfile: "motion_profile", motionIntensity: "motion_intensity", physicsStyle: "physics_style",
    cameraResponse: "camera_response", prompt: "prompt",
  };
  for (const [elementId, requestKey] of Object.entries(formFields)) {
    if (raw[requestKey] !== undefined && $(`#${elementId}`)) $(`#${elementId}`).value = raw[requestKey];
  }
  $("#jobName").value = recipe.job?.name || raw.job_name || "";
  state.modePrompts ||= {};
  state.modePrompts[mode] = raw.prompt || "";

  if (mode === "fl2va") {
    state.firstImage = recipeAsset(raw.first_image_asset_id, assets);
    state.lastImage = recipeAsset(raw.last_image_asset_id, assets);
  }
  if (mode === "symbol_loop") {
    const prepared = recipeAsset(raw.first_image_asset_id, assets);
    state.symbolLoop = {
      ...structuredClone(defaultState.symbolLoop),
      sourceAsset: prepared,
      preparedAsset: prepared,
      sourceName: prepared?.name || "已套用的圖騰素材",
    };
  }
  if (["extend", "r2v"].includes(mode)) {
    const frame = recipeAsset(raw.first_image_asset_id, assets);
    const sourceAsset = recipeAsset(raw.continuation_source_asset_id, assets);
    state.continuation = {
      ...structuredClone(defaultState.continuation),
      sourceJobId: raw.continuation_source_job_id || null,
      sourceAsset,
      lastFrame: frame,
      sourceName: sourceAsset?.name || frame?.name || recipe.job?.name || "已套用的續接來源",
      merge: Boolean(raw.continuation_merge),
      audio: raw.continuation_audio || "both",
    };
  }

  const references = raw.references || [];
  if (mode === "replace") {
    const reference = references[0] || {};
    const video = recipeAsset(reference.video_asset_id, assets);
    state.replacement = {
      ...structuredClone(defaultState.replacement),
      alias: reference.alias || "新角色",
      target: raw.replacement_target || "動態參考影片中的主要角色",
      description: reference.description || "",
      images: (reference.image_asset_ids || []).map(id => recipeAsset(id, assets)).filter(Boolean),
      video,
      videoUseAudio: Boolean(reference.video_use_audio),
      videoInfo: video?.video_info || null,
      segmentPlan: video?.replacement_plan_smart || video?.replacement_plan_balanced || [],
      autoSplit: raw.replacement_auto_split !== false,
      continuity: raw.replacement_continuity !== false,
      splitStrategy: raw.replacement_split_strategy || "smart",
      audioMode: raw.replacement_audio_mode || "original",
      defaultPrompt: raw.prompt || "",
      safeDefaultsApplied: true,
    };
  } else if (mode === "popup_panel") {
    state.popupReferences = restoreLockedReferences(defaultState.popupReferences, references, assets);
    state.popupPanel = { safeDefaultsApplied: true };
  } else if (mode === "mg_animation") {
    state.mgReferences = restoreLockedReferences(defaultState.mgReferences, references, assets);
    const mg = raw.mg_animation || {};
    state.mgAnimation = {
      ...structuredClone(defaultState.mgAnimation), safeDefaultsApplied: true,
      characterPosition: mg.character_position ?? defaultState.mgAnimation.characterPosition,
      characterPositionDetail: mg.character_position_detail ?? defaultState.mgAnimation.characterPositionDetail,
      characterMotion: mg.character_motion ?? defaultState.mgAnimation.characterMotion,
      reelMotionModel: mg.reel_motion_model ?? defaultState.mgAnimation.reelMotionModel,
      reelDirection: mg.reel_direction ?? defaultState.mgAnimation.reelDirection,
      reelStopOrder: mg.reel_stop_order ?? defaultState.mgAnimation.reelStopOrder,
      reelStopStagger: mg.reel_stop_stagger ?? defaultState.mgAnimation.reelStopStagger,
      reelMotion: mg.reel_motion ?? defaultState.mgAnimation.reelMotion,
      symbolPostStopMotion: mg.symbol_post_stop_motion ?? defaultState.mgAnimation.symbolPostStopMotion,
      backgroundMotionLevel: mg.background_motion_level ?? defaultState.mgAnimation.backgroundMotionLevel,
      backgroundMotion: mg.background_motion ?? defaultState.mgAnimation.backgroundMotion,
      cameraMotion: mg.camera_motion ?? defaultState.mgAnimation.cameraMotion,
      cameraMotionDetail: mg.camera_motion_detail ?? defaultState.mgAnimation.cameraMotionDetail,
    };
  } else {
    state.references = references.map(reference => recipeReference(reference, assets));
  }

  state.storyboards = (raw.storyboards || []).map(shot => ({
    id: uid(), duration: shot.duration ?? 2, description: shot.description || "", camera: shot.camera || "",
    dialogue: shot.dialogue || "", sound: shot.sound || "", motionBeats: shot.motion_beats || "",
    effects: shot.effects || "", guideMode: shot.guide_mode || "reference", image: recipeAsset(shot.image_asset_id, assets),
  }));

  if (mode === "replace" && state.replacement.video && !state.replacement.videoInfo) {
    try { await prepareReplacementVideo(); }
    catch (error) { toast(`影片設定已套用，但重新分析失敗：${error.message}`, true); }
  }

  renderKeyframePreview("first", state.firstImage, "起始圖片", false);
  renderKeyframePreview("last", state.lastImage, "結束圖片", true);
  renderReplacement();
  renderSymbolLoop();
  renderContinuation();
  renderMgAnimation();
  renderReferences();
  renderStoryboards();
  updateSummary();
  saveState();
  $("#modeGrid").scrollIntoView({ behavior: "smooth", block: "start" });
  toast(recipe.missing_assets?.length
    ? `設定已套用；有 ${recipe.missing_assets.length} 個舊素材已不存在，請重新加入。`
    : "已套用完整生成設定，可以直接微調後再次生成。", Boolean(recipe.missing_assets?.length));
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
    const hasActiveJob = jobs.some(job => ["queued", "preparing", "running"].includes(job.status));
    const signature = JSON.stringify({ jobs, meta, activeSecond: hasActiveJob ? Math.floor(Date.now() / 1000) : 0 });
    if (!force && signature === lastJobsSignature) return;
    if (!force && $$(".job-video").some(video => !video.paused)) return;
    lastJobsSignature = signature;
    $("#jobEmpty").classList.toggle("hidden", jobs.length > 0);
    $("#jobList").innerHTML = jobs.map(job => {
      const active = ["queued", "preparing", "running"].includes(job.status);
      const date = new Date(job.created_at).toLocaleString("zh-TW", { hour12: false });
      const fallbackName = `${modeLabels[job.mode] || job.mode} · ${job.width}×${job.height}`;
      const title = job.name || fallbackName;
      const executionSeconds = jobExecutionSeconds(job);
      const executionLabel = executionSeconds === null ? "生成耗時尚未記錄" : `${active ? "已執行" : "生成耗時"} ${formatExecutionTime(executionSeconds)}`;
      const batchLabel = job.batch_type === "replace_long" ? ` · 完整長片 ${job.segments?.length || 0} 段` : "";
      const subtitle = job.name ? `${fallbackName}${batchLabel} · ${date} · 影片 ${Number(job.duration).toFixed(2)} 秒 · ${executionLabel} · ${job.id.slice(0, 8)}` : `${date}${batchLabel} · 影片 ${Number(job.duration).toFixed(2)} 秒 · ${executionLabel} · ${job.id.slice(0, 8)}`;
      const open = active || expandedJobIds.has(job.id);
      return `
        <details class="job-card ${job.favorite ? "favorite" : ""}" data-job-id="${job.id}" ${open ? "open" : ""}>
          <summary class="job-summary">
            <div class="job-title"><button class="job-favorite ${job.favorite ? "active" : ""}" data-job-favorite="${job.id}" data-favorite="${job.favorite ? "true" : "false"}" type="button" title="${job.favorite ? "取消我的最愛" : "加入我的最愛"}">★</button><span class="job-badge ${job.status}">${statusLabel(job.status)}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle)}</small></div></div>
            <div class="job-progress"><div class="progress-track"><span style="width:${job.progress || 0}%"></span></div><small><span>${escapeHtml(job.current_node || statusLabel(job.status))}</span><span>${job.progress || 0}%</span></small></div>
            <span class="job-chevron">⌄</span>
          </summary>
          <div class="job-detail">
            <div class="job-detail-copy"><small>完整工作編號：${escapeHtml(job.id)}</small><small>生成執行時間：${escapeHtml(executionLabel)}</small>${job.output?.filename ? `<small>輸出檔名：${escapeHtml(job.output.filename)}</small>` : ""}</div>
            <div class="job-actions">
              <button class="button ghost" data-job-show-prompt="${job.id}" type="button">查看生成提示詞</button>
              <button class="button secondary" data-job-apply="${job.id}" type="button">快速套用</button>
              <button class="button ghost" data-job-rename="${job.id}" data-job-name="${escapeHtml(job.name || "")}" type="button">重新命名</button>
              ${job.status === "completed" ? `<a class="button secondary" href="/api/jobs/${job.id}/video?download=1" download>下載</a>` : ""}
              ${active ? `<button class="button ghost" data-job-cancel="${job.id}" type="button">取消</button>` : ""}
              ${job.batch_type === "replace_long" && ["failed", "cancelled", "interrupted"].includes(job.status) ? `<button class="button secondary" data-job-resume="${job.id}" type="button">從未完成片段接續</button>` : ""}
              ${["completed", "failed", "cancelled", "interrupted"].includes(job.status) ? `<button class="button danger" data-job-delete="${job.id}" type="button">刪除項目</button>` : ""}
            </div>
            ${batchSegmentsHtml(job)}
            ${job.preview_version ? `<div class="job-live-preview"><div><span>TAEH3 LIVE PREVIEW</span><strong>${active ? "生成中近似畫面" : "最後一張生成預覽"}</strong><small>這是低成本潛空間預覽，細節與最終影片可能不同。</small></div><img src="/api/jobs/${job.id}/preview?v=${encodeURIComponent(job.preview_version)}" alt="${escapeHtml(title)}生成中預覽"></div>` : ""}
            <div class="job-recipe hidden" data-job-recipe-panel="${job.id}"><div class="job-recipe-heading"><strong>實際送給 AI 的生成提示詞</strong><button class="text-button" data-job-copy-prompt="${job.id}" type="button">複製提示詞</button></div><pre data-job-compiled-prompt></pre></div>
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
    if (data.studio_role) applyStudioRole({ studio_role: data.studio_role });
    engineModelInventory = data.models || {};
    syncQualityMode(...dimensions());
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
  const mgMode = state.mode === "mg_animation";
  let alias;
  if (popupMode) {
    const usedAliases = new Set(references.map(item => item.alias.trim().toLocaleLowerCase()));
    let index = 1;
    do alias = `面板素材${index++}`; while (usedAliases.has(alias.toLocaleLowerCase()));
  } else if (mgMode) {
    const usedAliases = new Set(references.map(item => item.alias.trim().toLocaleLowerCase()));
    let index = 1;
    do alias = `MG素材${index++}`; while (usedAliases.has(alias.toLocaleLowerCase()));
  } else {
    alias = `角色${references.length + 1}`;
  }
  references.push({ id: uid(), alias, type: popupMode || mgMode ? "object" : "character", description: "", images: [], video: null, videoUseAudio: false, audio: null, voiceMode: "timbre" });
  renderReferences();
  updateSummary();
  saveState();
}

function addStoryboard() {
  const guideMode = ["r2v", "mg_animation"].includes(state.mode) ? "reference" : "exact";
  state.storyboards.push({ id: uid(), duration: 2, description: "", camera: "", dialogue: "", sound: "", motionBeats: "", effects: "", guideMode, image: null });
  renderStoryboards();
  saveState();
}

function replacementPrompt() {
  const alias = state.replacement?.alias?.trim() || "新角色";
  const target = state.replacement?.target?.trim() || "動態參考影片中的主要角色";
  return `[Shot 1] Create ${alias} as the only visual replacement for ${target} in the uploaded source video. Preserve ${alias}'s face, hairstyle, body proportions, costume, colors, and identifying details from the reference pictures throughout the entire shot. Transfer only the source character's screen position, pose sequence, timing, gaze direction, body mechanics, and interactions to ${alias}. The replacement performance is {{主要表演動作}}.

Remove every visual identity trait of the original character. Never show the original character beside ${alias}, never blend their faces, hair, clothing, or anatomy, and never add ${alias} to frames where the specified source character is absent. Preserve all other people, props, scenery, lighting, camera motion, framing, and edit rhythm from the source video.

Across automatically split segments, continue the incoming pose, motion vector, gaze, camera direction, lighting, and spatial position without a fresh start or pause. End with ${alias} in {{最後姿勢與畫面位置}}, while all non-replaced content remains consistent with the source video.

Sound direction: preserve {{原影片環境聲與同步音效}}. Audience-only score: {{配樂；不需要請整句刪除}}.`;
}

function symbolLoopPrompt() {
  return `[Shot 1] The uploaded slot symbol is the immutable design and the exact opening and closing frame. Keep the canvas, front-facing camera, crop, center pivot, visual scale, silhouette, text, materials, colors, border shape, and margins fixed. The complete symbol remains visible at all times.

The symbol performs one closed motion cycle: {{主要循環動作}}. Add only {{次要材質或特效表演}}. Build from a still opening state through clear anticipation, reach the largest readable motion near the middle, then reverse the motion path and progressively settle. Return the pose, position, rotation, scale, silhouette, material, lighting, particles, and effect state to the exact opening state at the final frame, without a second pause at the seam.

The camera remains completely static. Prohibit camera movement, crop changes, subject translation, scale drift, redesign, added objects, duplicated parts, melting deformation, text changes, background generation, lighting drift, flicker, and particles crossing the canvas boundary.

Sound direction: {{循環同步音效；不需要請整句刪除}}. Audience-only score: N/A.`;
}

function popupPanelPrompt() {
  const duration = actualDuration();
  const enterEnd = Math.min(0.75, Math.max(0.35, duration * 0.1));
  const exitStart = Math.max(enterEnd + 0.5, duration - enterEnd);
  return `[Shot 1] Use 背景圖 as a completely locked game background for the full ${duration.toFixed(2)}-second shot. Keep its position, scale, crop, pixels, lighting, depth of field, and parallax unchanged. The camera is a Static Shot with no push, pan, zoom, shake, or reframing. Animate only 面板 and the named foreground assets.

From 0.00s to ${enterEnd.toFixed(2)}s, an approximately 80%-opaque black dimming layer fades in above 背景圖. 面板 enters from {{進場方向或起始位置}}, scales from small to full size, makes one restrained overshoot, and settles at {{面板最後位置}}. Synchronize the entrance with {{進場音效}}.

From ${enterEnd.toFixed(2)}s to ${exitStart.toFixed(2)}s, keep the panel frame and anchor stable. {{面板主要內容}} performs {{面板內物件表演}}; use one readable primary action and restrained supporting glow or particles. All motion stays inside the panel safe area and never moves 背景圖.

From ${exitStart.toFixed(2)}s to ${duration.toFixed(2)}s, 面板 and every attached foreground element {{退場動作}}, then disappear completely as the dimming layer fades out. The final visible state contains only the original unchanged 背景圖.

Sound direction: {{面板動作同步音效與環境聲}}. Audience-only score: {{配樂；不需要請整句刪除}}.`;
}

function mgAnimationPrompt() {
  return `Cross-layer event: when {{觸發事件或指定軸停輪}} occurs, 角色 looks toward {{目標圖騰或介面位置}} and performs {{角色主要反應}}. The reaction begins with anticipation and a natural weight shift, reaches one clear peak, includes delayed secondary motion in clothing or accessories, and ends in {{角色最後姿勢}} without covering the reel window, title, score, payout values, or JP meters.

At the same moment, 轉輪帶 shows {{停輪結果或中獎圖騰表演}} only after the relevant reels have fully settled. 背景圖 responds only with {{低幅度背景回饋}}, remaining visually subordinate and preserving the complete game layout.

Sound direction: synchronize {{停輪聲、角色聲音與中獎音效}} with the visible contacts and reactions. Audience-only score: {{配樂；不需要請整句刪除}}. End on a clean, stable, readable final state.`;
}

function firstReferenceAlias(types, fallback) {
  return activeReferences().find(item => types.includes(item.type) && item.alias?.trim())?.alias.trim() || fallback;
}

function t2vPrompt() {
  return `[Shot 1] In {{場景}}, {{角色}} is {{初始姿勢與畫面位置}}. The visual style and lighting are {{美術風格與光線}}. The character begins with {{預備動作}}, then performs {{主要動作}} with visible weight shift, contact, reaction, follow-through, and a natural settle.

The camera {{運鏡方式、幅度與速度}} while keeping the subject readable and spatially consistent. Optional dialogue: {{角色}} (S1) says <d>[Chinese] {{台詞內容}}</d>. Visible on-screen text reads "{{畫面文字；不需要請整句刪除}}".

The shot ends with {{最後可見狀態}}, held clearly and stably. Sound direction: {{環境聲與同步動作音效}}. Audience-only score: {{配樂樂器、速度與強弱；不需要請整句刪除}}.`;
}

function keyframePrompt() {
  const subject = "{{主體}}";
  let path;
  if (state.firstImage && state.lastImage) {
    path = `The shot begins from the exact uploaded first frame and ends on the exact uploaded final frame. Preserve ${subject}'s identity, composition, object layout, and lighting while describing the visible path between the two anchors.`;
  } else if (state.firstImage) {
    path = `The shot begins from the exact uploaded first frame. Preserve ${subject}'s identity, composition, pose, object layout, and lighting at the start, then develop the action continuously.`;
  } else if (state.lastImage) {
    path = `The action begins from {{合理的前置狀態}} and progressively converges on the exact uploaded final frame. Narrow every difference in pose, composition, object layout, lighting, and camera angle before the ending.`;
  } else {
    path = `Use the uploaded frame anchors exactly as configured. Describe a continuous, observable path between the opening and required final state.`;
  }
  return `[Shot 1] ${path}

${subject} moves through {{中間連續動作}} with clear anticipation, weight transfer, interaction, reaction, follow-through, and deceleration. The camera {{運鏡方式、幅度與速度}} without sudden reframing. Avoid teleportation, pose resets, identity drift, or unexplained changes.

The final visible state is {{最後姿勢、構圖與光線}}, clearly settled${state.lastImage ? " and matching the uploaded final frame" : ""}. Optional dialogue: ${subject} (S1) says <d>[Chinese] {{台詞內容；不需要請整句刪除}}</d>. Sound direction: {{環境聲與同步音效}}. Audience-only score: {{配樂；不需要請整句刪除}}.`;
}

function referencePrompt() {
  const character = firstReferenceAlias(["character", "creature"], "{{角色名稱代號}}");
  const background = firstReferenceAlias(["background"], "{{背景名稱代號}}");
  return `[Shot 1] ${character} appears in ${background} at {{角色畫面方位與初始姿勢}}. Preserve ${character}'s face, body proportions, costume, colors, and voice ownership from the named reference assets. Preserve ${background}'s spatial layout, architecture, palette, and lighting without transferring its traits into the character.

${character} performs {{主要動作與互動對象}} through clear anticipation, weight shift, contact or reaction, follow-through, and a stable settle. The camera {{運鏡方式、幅度與速度}}. Any named motion reference controls only timing and body mechanics; it must not overwrite identity, costume, or scene design.

Optional dialogue: ${character} (S1) says <d>[Chinese] {{台詞內容}}</d>, using only the voice sample assigned to ${character}. The final visible state is {{所有角色、物件與鏡頭的結尾狀態}}. Sound direction: {{環境聲、動作音效與同步聲音}}. Audience-only score: {{配樂；不需要請整句刪除}}.`;
}

function continuationPrompt() {
  const character = firstReferenceAlias(["character", "creature"], "{{角色名稱代號}}");
  return `[Shot 1] Continue directly from the previous video's final frame with no restart, duplicated opening pose, or pause. Preserve ${character}'s identity, costume, exact incoming pose, gaze, screen position, motion vector, lighting, environment, and camera direction.

${character} follows the existing momentum into {{接續主要動作}}. Show natural acceleration or deceleration, weight transfer, contact, reaction, follow-through, and continuity of secondary motion. The camera {{接續運鏡}} without a cut or sudden reframing. If other named characters, objects, or scenes are referenced, preserve each alias independently and do not mix their attributes.

The continuation ends with {{新的最後姿勢與畫面狀態}}, clearly settled and ready for a possible next segment. Optional dialogue: ${character} (S1) says <d>[Chinese] {{台詞內容；不需要請整句刪除}}</d>. Sound direction: continue {{上一段環境聲}} and synchronize {{新的動作音效}}. Audience-only score: {{延續配樂；不需要請整句刪除}}.`;
}

function promptTemplate(mode = state.mode) {
  if (mode === "popup_panel") return popupPanelPrompt();
  if (mode === "mg_animation") return mgAnimationPrompt();
  if (mode === "r2v") return referencePrompt();
  if (mode === "extend") return continuationPrompt();
  if (mode === "replace") return replacementPrompt();
  if (mode === "symbol_loop") return symbolLoopPrompt();
  if (mode === "fl2va") return keyframePrompt();
  return t2vPrompt();
}

function applyPromptTemplate(mode = state.mode) {
  const template = promptTemplate(mode);
  $("#prompt").value = template;
  state.promptTemplateSnapshots ||= {};
  state.modePrompts ||= {};
  state.promptTemplateSnapshots[mode] = template;
  state.modePrompts[mode] = template;
  renderPromptKeywords();
  return template;
}

function refreshPromptTemplateIfUntouched() {
  state.promptTemplateSnapshots ||= {};
  const previous = state.promptTemplateSnapshots[state.mode];
  if (!previous || $("#prompt").value !== previous) return false;
  applyPromptTemplate(state.mode);
  updateSummary();
  return true;
}

function promptKeywords(value = $("#prompt")?.value || "") {
  return [...new Set([...value.matchAll(/\{\{([^{}\n]+)\}\}/g)].map(match => match[1].trim()))];
}

function renderPromptKeywords() {
  const panel = $("#promptTemplateHelp");
  if (!panel) return;
  const keywords = promptKeywords();
  panel.classList.toggle("hidden", !keywords.length);
  $("#promptKeywordStatus").textContent = keywords.length ? `尚有 ${keywords.length} 個待替換項目；點擊即可定位` : "範本關鍵字已全部替換";
  $("#promptKeywordList").innerHTML = keywords.map(keyword => `<button type="button" data-prompt-keyword="${escapeHtml(keyword)}">${escapeHtml(keyword)}</button>`).join("");
}

function selectPromptKeyword(keyword) {
  const textarea = $("#prompt");
  const token = `{{${keyword}}}`;
  let index = textarea.value.indexOf(token, textarea.selectionEnd);
  if (index < 0) index = textarea.value.indexOf(token);
  if (index < 0) return;
  textarea.focus();
  textarea.setSelectionRange(index, index + token.length);
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
  $("#duration").addEventListener("change", refreshPromptTemplateIfUntouched);
  $("#qualityMode").addEventListener("change", changeQualityMode);
  ["aspectRatio", "megapixels", "keyframeFit"].forEach(id => {
    $(`#${id}`).addEventListener("change", refreshKeyframes);
  });
  $("#mgDirectorPanel").addEventListener("input", event => {
    const field = event.target.dataset.mgField;
    if (!field) return;
    state.mgAnimation[field] = event.target.type === "number" ? Number(event.target.value) : event.target.value;
    saveState();
  });
  $("#mgDirectorPanel").addEventListener("change", event => {
    const field = event.target.dataset.mgField;
    if (!field) return;
    state.mgAnimation[field] = event.target.type === "number" ? Number(event.target.value) : event.target.value;
    saveState();
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
  $("#openGatewaySettings").addEventListener("click", async () => {
    try { await loadGatewayStatus(true); } catch (error) { toast(error.message, true); }
  });
  $("#openPromptGuide").addEventListener("click", openPromptGuide);
  $("#openMusicStudio").addEventListener("click", openMusicStudio);
  $$('[data-close-music]').forEach(element => element.addEventListener("click", closeMusicStudio));
  $$('[data-music-mode]').forEach(button => button.addEventListener("click", () => setMusicMode(button.dataset.musicMode)));
  $("#randomMusicSeed").addEventListener("click", () => { $("#musicSeed").value = randomMusicSeed(); });
  $("#generateMusic").addEventListener("click", generateMusic);
  $("#installMusicModels").addEventListener("click", async () => {
    try {
      renderMusicStatus(await api("/api/music/install", { method: "POST" }));
      toast("Music 3 模型開始下載；關閉面板不會中斷。")
    } catch (error) { toast(error.message, true); }
  });
  $("#cancelMusicInstall").addEventListener("click", async () => {
    try { renderMusicStatus(await api("/api/music/install/cancel", { method: "POST" })); toast("正在暫停下載，進度會保留。"); }
    catch (error) { toast(error.message, true); }
  });
  $("#refreshMusicJobs").addEventListener("click", () => loadMusicJobs(true).catch(error => toast(error.message, true)));
  $("#previousMusicPage").addEventListener("click", () => { if (musicPage > 1) { musicPage--; loadMusicJobs(true); } });
  $("#nextMusicPage").addEventListener("click", () => { if (musicPage < musicTotalPages) { musicPage++; loadMusicJobs(true); } });
  $("#musicJobList").addEventListener("click", async event => {
    const favorite = event.target.closest("[data-music-favorite]");
    const rename = event.target.closest("[data-music-rename]");
    const cancel = event.target.closest("[data-music-cancel]");
    const resume = event.target.closest("[data-music-resume]");
    const caption = event.target.closest("[data-music-caption]");
    try {
      if (favorite) {
        await api(`/api/music/jobs/${favorite.dataset.musicFavorite}/favorite`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ favorite: favorite.dataset.favorite !== "true" }),
        });
        musicPage = 1;
      } else if (rename) {
        const name = prompt("輸入音樂任務名稱（最多 80 個字）", rename.dataset.name || "");
        if (name === null) return;
        await api(`/api/music/jobs/${rename.dataset.musicRename}/rename`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
        });
      } else if (cancel) {
        await api(`/api/music/jobs/${cancel.dataset.musicCancel}/cancel`, { method: "POST" });
      } else if (resume) {
        await api(`/api/music/jobs/${resume.dataset.musicResume}/resume`, { method: "POST" });
      } else if (caption) {
        const panel = $(`[data-music-caption-panel="${caption.dataset.musicCaption}"]`);
        panel.classList.toggle("hidden");
        return;
      } else return;
      await loadMusicJobs(true);
    } catch (error) { toast(error.message, true); }
  });
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
    if (!$("#musicModal").classList.contains("hidden")) closeMusicStudio();
    else if (!$("#promptGuideModal").classList.contains("hidden")) closePromptGuide();
    else if (!$("#gatewayModal").classList.contains("hidden")) closeGatewaySettings();
    else if (!$("#connectionModal").classList.contains("hidden")) closeConnectionSettings();
  });
  $$("[data-close-connection]").forEach(element => element.addEventListener("click", closeConnectionSettings));
  $$("[data-close-gateway]").forEach(element => element.addEventListener("click", closeGatewaySettings));
  $("#saveGatewaySettings").addEventListener("click", async () => {
    const button = $("#saveGatewaySettings");
    setButtonBusy(button, true);
    try {
      const result = await api("/api/gateway/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: $("#gatewayEnabled").checked, port: Number($("#gatewayPort").value) }),
      });
      renderGatewayStatus(result);
      toast(result.running ? "共享引擎已啟用" : "共享引擎已停用");
    } catch (error) { toast(error.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#gatewayCreateUser").addEventListener("submit", async event => {
    event.preventDefault();
    const name = $("#gatewayUserName").value.trim();
    if (!name) return;
    const button = event.currentTarget.querySelector("button");
    setButtonBusy(button, true);
    try {
      const result = await api("/api/gateway/users", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      $("#gatewayUserName").value = "";
      revealGatewayToken(result);
      await loadGatewayStatus();
      toast(`已建立 ${result.user.name} 的共享帳號`);
    } catch (error) { toast(error.message, true); }
    finally { setButtonBusy(button, false); }
  });
  $("#gatewayUrlList").addEventListener("click", async event => {
    const button = event.target.closest("[data-gateway-copy-url]");
    if (!button) return;
    try { await navigator.clipboard.writeText(button.dataset.gatewayCopyUrl); toast("Gateway 網址已複製"); }
    catch { toast("無法複製網址", true); }
  });
  $("#copyGatewayToken").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#gatewayTokenValue").textContent); toast("個人金鑰已複製"); }
    catch { toast("無法複製金鑰", true); }
  });
  $("#gatewayUserList").addEventListener("click", async event => {
    const rotate = event.target.closest("[data-gateway-rotate]");
    const toggle = event.target.closest("[data-gateway-enable]");
    const button = rotate || toggle;
    if (!button) return;
    setButtonBusy(button, true);
    try {
      if (rotate) {
        const result = await api(`/api/gateway/users/${rotate.dataset.gatewayRotate}/rotate`, { method: "POST" });
        revealGatewayToken(result);
        toast(`${result.user.name} 的舊金鑰已失效`);
      } else {
        await api(`/api/gateway/users/${toggle.dataset.gatewayEnable}/enabled`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: toggle.dataset.enabled === "true" }),
        });
      }
      await loadGatewayStatus();
    } catch (error) { toast(error.message, true); }
    finally { setButtonBusy(button, false); }
  });
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
      applyStudioRole(connectionSettings);
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
    box.addEventListener("click", event => {
      const removeButton = event.target.closest("[data-remove-keyframe]");
      if (removeButton) {
        event.stopPropagation();
        clearKeyframe(removeButton.dataset.removeKeyframe);
        return;
      }
      input.click();
    });
    box.addEventListener("dragover", event => event.preventDefault());
    box.addEventListener("drop", async event => {
      event.preventDefault();
      if (event.dataTransfer.files[0]) await setKeyframe(target, event.dataTransfer.files[0]);
    });
    input.addEventListener("change", async () => {
      const file = input.files[0];
      input.value = "";
      if (file) await setKeyframe(target, file);
    });
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
    state.replacement.videoInfo = null;
    state.replacement.segmentPlan = [];
    renderReplacement(); updateSummary(); saveState();
  });
  $("#replacementUseAudio").addEventListener("change", event => {
    state.replacement.videoUseAudio = event.target.checked;
    saveState();
  });
  $("#replacementAutoSplit").addEventListener("change", event => {
    state.replacement.autoSplit = event.target.checked;
    renderReplacement(); updateSummary(); saveState();
  });
  $("#replacementContinuity").addEventListener("change", event => {
    state.replacement.continuity = event.target.checked;
    saveState();
  });
  $("#replacementAudioMode").addEventListener("change", event => {
    state.replacement.audioMode = event.target.value;
    saveState();
  });
  $("#replacementSplitStrategy").addEventListener("change", async event => {
    state.replacement.splitStrategy = event.target.value;
    if (state.replacement.video) {
      try {
        toast("正在重新規劃影片切點...");
        await prepareReplacementVideo();
        renderReplacement(); updateSummary();
      } catch (error) { toast(error.message, true); }
    }
    saveState();
  });

  $("#addReference").addEventListener("click", addReference);
  $("#referenceList").addEventListener("input", event => {
    const card = event.target.closest("[data-reference-id]");
    const field = event.target.dataset.refField;
    if (!card || !field) return;
    const item = activeReferences().find(value => value.id === card.dataset.referenceId);
    item[field] = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    if (["alias", "type"].includes(field)) refreshPromptTemplateIfUntouched();
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
      if (action === "delete" && !lockedReferenceIds().has(item.id)) {
        if (state.mode === "popup_panel") state.popupReferences = references.filter(value => value.id !== item.id);
        else if (state.mode === "mg_animation") state.mgReferences = references.filter(value => value.id !== item.id);
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
    if (["duration", "guideMode"].includes(field)) renderStoryboards();
  });
  $("#storyboardList").addEventListener("click", async event => {
    const actionElement = event.target.closest("[data-shot-action]");
    const card = event.target.closest("[data-shot-id]");
    if (!actionElement || !card) return;
    const shot = state.storyboards.find(value => value.id === card.dataset.shotId);
    if (actionElement.dataset.shotAction === "delete") state.storyboards = state.storyboards.filter(value => value.id !== shot.id);
    if (actionElement.dataset.shotAction === "remove-image") shot.image = null;
    if (actionElement.dataset.shotAction === "image") {
      const files = await chooseFiles("image/*");
      if (files[0]) {
        shot.image = await uploadFile(files[0], "storyboard-image");
        if (!["r2v", "mg_animation"].includes(state.mode)) shot.guideMode = "exact";
      }
    }
    renderStoryboards(); updateSummary(); saveState();
  });

  $("#insertPromptTemplate").addEventListener("click", () => {
    if ($("#prompt").value.trim() && !confirm("要用提示詞範本取代目前內容嗎？")) return;
    applyPromptTemplate();
    updateSummary();
    saveState();
    toast("已載入目前模式的官方格式範本；點擊下方關鍵字即可逐一替換。");
  });
  $("#promptKeywordList").addEventListener("click", event => {
    const button = event.target.closest("[data-prompt-keyword]");
    if (button) selectPromptKeyword(button.dataset.promptKeyword);
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
    const favoriteButton = event.target.closest("[data-job-favorite]");
    const showPromptButton = event.target.closest("[data-job-show-prompt]");
    const copyPromptButton = event.target.closest("[data-job-copy-prompt]");
    const applyButton = event.target.closest("[data-job-apply]");
    const cancelButton = event.target.closest("[data-job-cancel]");
    const resumeButton = event.target.closest("[data-job-resume]");
    const renameButton = event.target.closest("[data-job-rename]");
    const deleteButton = event.target.closest("[data-job-delete]");
    if (favoriteButton) {
      event.preventDefault();
      event.stopPropagation();
      try {
        const favorite = favoriteButton.dataset.favorite !== "true";
        await api(`/api/jobs/${favoriteButton.dataset.jobFavorite}/favorite`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ favorite }),
        });
        jobPage = 1;
        toast(favorite ? "已加入我的最愛並置頂。" : "已從我的最愛移除。");
        loadJobs(true);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (showPromptButton) {
      try { await showJobPrompt(showPromptButton.dataset.jobShowPrompt); }
      catch (error) { toast(error.message, true); }
      return;
    }
    if (copyPromptButton) {
      try {
        const jobId = copyPromptButton.dataset.jobCopyPrompt;
        const panel = $(`[data-job-recipe-panel="${jobId}"]`);
        if (panel?.dataset.loaded !== "true") await showJobPrompt(jobId);
        const promptText = $("[data-job-compiled-prompt]", panel)?.textContent || "";
        await navigator.clipboard.writeText(promptText);
        toast("已複製這次的生成提示詞。");
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (applyButton) {
      if (!confirm("要用這筆工作的模式、提示詞、素材、解析度與全部生成設定取代目前編輯內容嗎？")) return;
      applyButton.disabled = true;
      try { await applyJobRecipe(applyButton.dataset.jobApply); }
      catch (error) { toast(error.message, true); }
      finally { applyButton.disabled = false; }
      return;
    }
    if (cancelButton) {
      try { await api(`/api/jobs/${cancelButton.dataset.jobCancel}/cancel`, { method: "POST" }); toast("已送出取消要求"); loadJobs(true); }
      catch (error) { toast(error.message, true); }
      return;
    }
    if (resumeButton) {
      resumeButton.disabled = true;
      try {
        await api(`/api/jobs/${resumeButton.dataset.jobResume}/resume`, { method: "POST" });
        toast("已從第一個未完成片段接續工作。");
        loadJobs(true);
      } catch (error) { toast(error.message, true); }
      finally { resumeButton.disabled = false; }
      return;
    }
    if (deleteButton) {
      const confirmed = confirm(
        "確定要永久刪除這筆生成項目嗎？\n\n操作面板中的任務紀錄、提示詞、工作流、預覽與本機快取影片會一併刪除。ComfyUI 原始輸出會保留。此操作無法復原。"
      );
      if (!confirmed) return;
      deleteButton.disabled = true;
      try {
        const jobId = deleteButton.dataset.jobDelete;
        await api(`/api/jobs/${jobId}`, { method: "DELETE" });
        expandedJobIds.delete(jobId);
        toast("生成項目已刪除；ComfyUI 原始輸出仍保留。");
        await loadJobs(true);
      } catch (error) {
        toast(error.message, true);
        deleteButton.disabled = false;
      }
      return;
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
    refreshPromptTemplateIfUntouched();
    renderKeyframePreview(target, asset, target === "first" ? "起始圖片" : "結束圖片", target === "last");
    updateSummary(); saveState();
    toast(asset.transparency_filled ? "透明背景已填入螢光綠，並依輸出比例完成適配。" : "圖片已依輸出比例完成適配。");
  } catch (error) { toast(error.message, true); }
}

function initialize() {
  restoreForm();
  bindEvents();
  installInteractionMotion();
  setMusicMode("instrumental");
  $("#musicSeed").value = randomMusicSeed();
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
  setInterval(() => {
    if ($("#musicModal").classList.contains("hidden")) return;
    loadMusicStatus().catch(error => console.warn(error));
    loadMusicJobs().catch(error => console.warn(error));
  }, 2500);
}

initialize();
