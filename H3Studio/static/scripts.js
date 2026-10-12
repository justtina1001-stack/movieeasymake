"use strict";

const SCRIPT_FIELDS = ["title", "idea", "genre", "duration", "characters", "style", "instructions", "content", "writing_skill"];
const SCRIPT_PROJECT_FIELDS = [...SCRIPT_FIELDS, "character_references", "custom_loras"];
const SCRIPT_ACTIONS = { outline: "故事大綱", screenplay: "完整劇本", rewrite: "潤飾劇本", storyboard: "影片分鏡", appearance: "角色外觀" };
const SCRIPT_REFERENCE_TYPES = { character: "角色", creature: "生物", object: "物件", background: "場景" };
const SCRIPT_IMAGE_MAX_BYTES = 8 * 1024 * 1024;
const SCRIPT_DRAFT_PREFIX = "h3studio.scripts.draft.";
const SCRIPT_SAVED_PREFIX = "h3studio.scripts.saved.";
const SCRIPT_DEFAULT_STYLE = "精緻 3D CG 遊戲動畫風格，色彩明亮；依場景保留少量神祕冒險氛圍。";
const SCRIPT_DEFAULT_INSTRUCTIONS = "以繁體中文編寫可直接製作的劇本。全片不要配樂／零 BGM，只保留場景環境音、蟲鳴、樹葉摩擦、腳步聲、遺跡能量聲等適合情境的聲音，以及角色動作音效與對白。\n先列全片風格、聲音規則及角色／場景與參考圖片的固定對應，再以連續時間碼分段；起點為 0.0s，最後一段結束於目標片長，沒有空白或重疊。每段清楚列出：鏡頭／景別（焦段與機位）、地點、角色／位置與空間關係、動作／行為與表情、運鏡、VFX、聲音、對白；沒有對白請寫「無」。動作與對白須能在段落時長內完成，前後鏡頭保持角色外觀、位置、方向與因果連續，收尾交代最後反應、定格或切黑。\n若提供「角色＝圖一；角色＝圖二；場景＝圖三」等對應，全片固定使用；未提供圖片時不要虛構圖片內容。角色與故事依本次設定發展。\n詳細描寫應增加段落內的可見資訊，不增加片長。動作寫準備→移動／接觸→反應與收勢，補上視線、手勢、重心與位置變化；運鏡交代方向、速度及最後構圖。禁止用「同上」或重複結尾補長，目標片長到達後立即停止。";
const SCRIPT_MONKEY_EXAMPLE_INSTRUCTIONS = `範例全長固定 15.0 秒，共 8 段。金猴爺＝圖一；神祕女子＝圖二；古代遺跡＝圖三。這是參考圖對應設定，圖片須另行提供，不代表已上傳。叢林明亮，遺跡區帶少量神祕冒險氛圍。
1. 0.0–2.0s｜深入叢林：18mm 超廣角大全景，低機位貼地穿過熱帶葉片追至金猴爺後方。茂密熱帶叢林深處；金猴爺在中央偏前，背對鏡頭，以小短腿快走、雙手撥大葉片、左右好奇張望。低機位追焦→快速推進。金黃體積光、花粉塵埃、葉片水珠；蟲鳴、鳥叫、踩落葉「沙沙沙」；無對白。
2. 2.0–4.0s｜發現古代遺跡：24mm 中遠景，右後方 3/4 背影。金猴爺停在左下，圖三入口在前方約 15 公尺的中央；猛停、睜大眼、抬頭看石門與藍色傳送門。背後慢慢上仰揭露完整遺跡；青藍漩渦慢轉、金框暖光、少量紫粒子；低沉「嗡——」；金猴爺小聲「哇……」。
3. 4.0–5.5s｜偷偷靠近：35mm 中景側面，金猴爺由左向右靠近入口，踮腳、雙手微張保持平衡，帽寶石映藍光。平行橫移跟拍，輕微手持感；紅金服裝上冷暖反射；「噠、噠、噠」小腳步與能量旋轉聲；無對白。
4. 5.5–7.0s｜身後突然有聲音：50mm 正面半身近景，金猴爺面朝遺跡；女子無聲站在身後半步，只讓羽毛頭飾邊緣入鏡，不完整揭露她。葉片突然「沙啦！」，他僵住、瞪眼抬眉、耳朵鬍鬚微抖。微幅快速推臉；身後落葉、背景藍光掃過；「沙啦——」後環境音壓低 0.2 秒；無對白。
5. 7.0–8.5s｜猛然回頭＋意外撞上：28mm 中近景，攝影機在兩人側面，清楚交代女子在金猴爺背後不到半公尺。他「唰！」快轉身，臉瞬間撞到女子胸前；只做瞬間喜劇碰撞，不特寫胸部。女子微微後晃、低頭驚訝；快 Pan 跟轉身，碰撞點輕微 Camera Shake；淡卡通空氣波、兩三顆小星星彈開；「咚！」與金猴爺悶聲「嗚！？」。
6. 8.5–10.0s｜金猴爺當場害羞：50mm 近景稍俯拍，他退一步、臉迅速通紅、雙手在胸前慌揮、眼神亂飄不敢看女子，張嘴、鬍鬚晃動，轉身準備逃。攝影機跟後退稍拉遠；臉頰紅暈加深、頭頂兩三縷白色熱氣；金猴爺「對、對不起啊——！」。
7. 10.0–12.5s｜逃跑失敗，被一把抱住：24mm 全身中景，側後方大透視。他向右前方才跑一步，後方女子微笑伸雙手環抱上半身，把他溫柔抱回懷裡，不粗暴拉扯。他雙腳離地急踢兩下「等等等等——！」。快跟焦逃跑→出手時小幅環繞 15°；羽飾柔和藍紫拖影、金飾映暖光；「咻！」、「啪嗒！」及衣料摩擦。
8. 12.5–15.0s｜神祕低語收尾：65mm 雙人半身近景，前側約 30°。女子低頭靠近耳邊，神祕俏皮微笑，稍收緊手臂抱牢；金猴爺臉紅、睜圓眼、全身僵硬。女子很輕很慢說「這些……都給你唷。」；他立刻睜大眼、嘴成 O 型。極慢推近，最後 0.3 秒定格爆紅表情；藍色門漩渦持續轉、金黃夕光輪廓、紫藍粒子掠過前景；低語與低沉能量聲，金猴爺急促「欸——！？」後切黑。
以上時間碼、焦段、空間關係、喜劇節奏與指定對白須保留；所有聲音都屬環境音、動作音效或人聲，全片零 BGM。`;

// New-project defaults are deliberate. Normalization must preserve older projects and crash drafts.
function createNewScriptProject() {
  return normalizeScriptProject({ duration: 15, style: SCRIPT_DEFAULT_STYLE, instructions: SCRIPT_DEFAULT_INSTRUCTIONS, writing_skill: "cinematic-story" });
}

function createMonkeyScriptExample() {
  return { ...createNewScriptProject(), title: "金猴爺・叢林遺跡奇遇", genre: "動畫", characters: "金猴爺＝圖一；神祕女子＝圖二；古代遺跡＝圖三", idea: "金猴爺深入熱帶叢林，發現古代遺跡與藍色傳送門，踮腳偷偷靠近。背後葉片聲讓他僵住；他猛然回頭，意外撞上站得太近的神祕女子，害羞退開並道歉。才逃一步就被女子溫柔抱回懷裡，雙腳離地慌張踢動。女子在耳邊低語「這些……都給你唷。」，以金猴爺爆紅驚叫的反應切黑，形成 15 秒神祕冒險喜劇。", instructions: `${SCRIPT_DEFAULT_INSTRUCTIONS}\n\n${SCRIPT_MONKEY_EXAMPLE_INSTRUCTIONS}` };
}

function normalizeScriptProject(value = {}) {
  const result = { id: value.id || null, revision: Number(value.revision) || 0 };
  for (const field of SCRIPT_FIELDS) result[field] = field === "duration" ? Number(value[field] ?? 60) : String(value[field] ?? (field === "genre" ? "劇情" : ""));
  result.writing_skill = typeof value.writing_skill === "string" ? value.writing_skill.trim() : "";
  result.character_references = normalizeScriptReferences(value.character_references);
  result.custom_loras = normalizeScriptLoras(value.custom_loras);
  result.created_at = value.created_at || "";
  result.updated_at = value.updated_at || "";
  return result;
}

function normalizeScriptLoras(value) {
  if (!Array.isArray(value)) return [];
  return value.filter(item => item && typeof item === "object" && typeof item.name === "string" && item.name.trim()).map(item => ({
    name: item.name.trim().replace(/\\/g, "/"),
    strength: Number(item.strength ?? 0.5),
    enabled: item.enabled !== false,
    family: item.family === undefined ? "both" : ["fl2va", "ref2va", "both"].includes(item.family) ? item.family : "",
  }));
}

function scriptLoraSourceUrl(value) {
  if (typeof value !== "string" || !/^https?:\/\//i.test(value) || /[\\\x00-\x20]/.test(value)) return "";
  try { const url = new URL(value); return !url.username && !url.password && ["https:", "http:"].includes(url.protocol) ? url.href : ""; } catch (_) { return ""; }
}

function scriptLoraSelectionError(value, { video = false, catalog = null } = {}) {
  const rows = normalizeScriptLoras(value), seen = new Set();
  if (rows.length > 4) return "影片 LoRA 最多 4 個，請移除多餘項目。";
  for (const row of rows) {
    if (row.name.length > 512 || !/\.safetensors$/i.test(row.name) || /[\x00-\x1f:]/.test(row.name) || row.name.split("/").some(part => !part || part === "." || part === "..")) return "LoRA 必須是引擎清單中的相對路徑 .safetensors 檔案。";
    if (seen.has(row.name.toLowerCase())) return "同一個 LoRA 不可重複加入。";
    seen.add(row.name.toLowerCase());
    if (!Number.isFinite(row.strength) || row.strength < 0 || row.strength > 2) return "LoRA 強度請填寫 0～2 的數字。";
    if (!["fl2va", "ref2va", "both"].includes(row.family)) return `請明確選擇「${row.name}」的適用模型；未知 LoRA 請依作者說明確認。`;
    const metadata = catalog?.items?.find(item => item.name === row.name);
    if (row.enabled && row.strength > 0 && catalog?.builtin_items?.some(item => item.name === row.name)) return "Turbo 加速由影片品質模式管理，請移除此自訂 Turbo 項目。";
    if (video && row.enabled && row.strength > 0 && (row.family === "fl2va" || metadata?.known === true && metadata.family === "fl2va")) return `「${row.name}」只適用 FL2VA，腳本自動影片使用 Ref2VA；請停用或選擇 Ref2VA 相容檔案。`;
  }
  return "";
}

function scriptLoraCanonicalFamily(sent, saved) {
  return sent?.name?.split("/").pop().toLowerCase() === "wushu_h3_r2v_v7_2000.safetensors" && sent.family === "both" && saved.family === "ref2va";
}

function scriptLoraSaveConfirmed(sent, saved) {
  if (!Array.isArray(saved)) return false;
  const source = normalizeScriptLoras(sent), returned = normalizeScriptLoras(saved);
  return source.length === returned.length && source.every((row, index) => {
    const item = returned[index];
    return item.name === row.name && item.strength === row.strength && item.enabled === row.enabled && (item.family === row.family || scriptLoraCanonicalFamily(row, item));
  });
}

function normalizeScriptReferences(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value.filter(item => {
    if (!item || typeof item !== "object" || typeof item.id !== "string" || !/^[a-f0-9]{32}$/i.test(item.id) || seen.has(item.id)) return false;
    seen.add(item.id); return true;
  }).slice(0, 16).map(item => ({
    id: item.id,
    name: typeof item.name === "string" ? item.name : "",
    type: Object.hasOwn(SCRIPT_REFERENCE_TYPES, item.type) ? item.type : "character",
    image_asset_id: typeof item.image_asset_id === "string" ? item.image_asset_id : "",
    image_name: typeof item.image_name === "string" ? item.image_name : "",
    appearance: typeof item.appearance === "string" ? item.appearance : "",
  }));
}

function scriptAppearanceApplyTarget(job, project) {
  const referenceId = job?.reference_id || job?.input?.reference_id;
  const current = normalizeScriptReferences(project?.character_references).find(item => item.id === referenceId);
  const original = normalizeScriptReferences(job?.input?.project?.character_references).find(item => item.id === referenceId);
  if (!current || !original || ["name", "type", "image_asset_id"].some(field => current[field] !== original[field])) return { error: "角色名稱、類型或圖片已變更。請複製結果，檢查後填入正確角色的外觀描述。" };
  if (current.appearance !== original.appearance) return { error: "外觀描述在分析後已修改，已保留你的文字。請複製結果，檢查後自行合併。" };
  return { reference: current };
}

function scriptProjectSignature(project) {
  const normalized = normalizeScriptProject(project);
  return JSON.stringify(SCRIPT_PROJECT_FIELDS.map(field => normalized[field]));
}

function scriptSaveOutcome(saved, live, sent) {
  const changed = scriptProjectSignature(live) !== scriptProjectSignature(sent);
  const project = { ...normalizeScriptProject(changed ? live : saved), id: saved.id, revision: saved.revision, created_at: saved.created_at, updated_at: saved.updated_at };
  if (changed && scriptLoraSaveConfirmed(sent.custom_loras, saved.custom_loras)) {
    for (const row of project.custom_loras) { const original = normalizeScriptLoras(sent.custom_loras).find(item => item.name === row.name), returned = normalizeScriptLoras(saved.custom_loras).find(item => item.name === row.name); if (original && returned && row.family === original.family && scriptLoraCanonicalFamily(original, returned)) row.family = returned.family; }
  }
  return { project, dirty: changed };
}

function scriptDraftCandidates(records, project, serverProject) {
  const id = project?.id || null;
  return records.filter(record => record && record.project && (record.project.id || null) === id &&
    (SCRIPT_FIELDS.some(field => String(record.project[field] ?? "").trim()) || normalizeScriptReferences(record.project.character_references).length > 0) &&
    (!serverProject || scriptProjectSignature(record.project) !== scriptProjectSignature(serverProject)))
    .sort((a, b) => Number(b.savedAt || 0) - Number(a.savedAt || 0));
}

function scriptJobPresentation(job) {
  if (!job) return { label: "等待靈感", active: false, canApply: false, canImport: false };
  const labels = { queued: "本機排隊中", pending: "本機排隊中", running: "本機模型生成中", completed: "已完成", failed: "生成失敗", cancelled: "已取消", canceled: "已取消", cancelling: "正在取消", interrupted: "重新啟動後已中斷" };
  const truncated = job.truncated === true;
  const completed = job.status === "completed" && !truncated;
  const hasContent = Boolean(String(job.content || "").trim());
  return { label: job.status === "completed" && truncated ? "內容未完成" : labels[job.status] || "確認工作狀態中", active: ["queued", "pending", "running", "cancelling"].includes(job.status), canApply: completed && hasContent && job.action !== "storyboard", canImport: completed && hasContent && job.action === "storyboard" };
}

function scriptCanDeleteJob(job) {
  return Boolean(job?.id) && ["completed", "failed", "cancelled", "canceled", "interrupted"].includes(job.status);
}

const SCRIPT_PIPELINE_STAGES = { queued: "排隊", screenplay: "編寫劇本", storyboard: "建立分鏡", import: "建立短片", render: "生成影片", assemble: "組合影片", completed: "影片完成" };

function scriptPipelinePresentation(pipeline) {
  if (!pipeline) return { label: "尚未開始", stage: "準備角色與劇本", active: false, progress: 0 };
  const statuses = { queued: "已排入流程", pending: "已排入流程", running: "正在製作", cancelling: "正在取消", completed: "影片完成", failed: "製作失敗", cancelled: "已取消", canceled: "已取消", interrupted: "重新啟動後中斷" };
  const progress = Number(pipeline.progress);
  return { label: statuses[pipeline.status] || "確認流程狀態中", stage: SCRIPT_PIPELINE_STAGES[pipeline.stage] || pipeline.stage || "準備流程", active: ["queued", "pending", "running", "cancelling"].includes(pipeline.status), progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0 };
}

function scriptPipelineVideoUrl(pipeline) {
  if (pipeline?.status !== "completed") return "";
  if (/^[a-f0-9]{32}$/i.test(pipeline.video_asset_id || "")) return `/api/assets/${pipeline.video_asset_id}`;
  const value = typeof pipeline.video_url === "string" ? pipeline.video_url : "";
  return /^\/api\/(?:assets\/[a-f0-9]{32}|jobs\/[a-f0-9]{32}\/video|scripts\/video-pipelines\/[a-f0-9]{32}\/video|editor\/exports\/[a-f0-9]{32}\/file)(?:\?[^#\s]*)?$/i.test(value) ? value : "";
}

function scriptVideoHistoryRecord(pipeline) {
  const safeId = value => typeof value === "string" && /^[a-f0-9]{32}$/i.test(value);
  if (!pipeline || pipeline.status !== "completed" || !safeId(pipeline.id)) return null;
  let url = "";
  if (pipeline.video_url !== undefined && pipeline.video_url !== null && pipeline.video_url !== "") {
    if (typeof pipeline.video_url !== "string" || /[\\#]|\.\./.test(pipeline.video_url)) return null;
    url = scriptPipelineVideoUrl({ status: "completed", video_url: pipeline.video_url });
  } else if (safeId(pipeline.video_asset_id)) url = `/api/assets/${pipeline.video_asset_id}`;
  else if (safeId(pipeline.export_id)) url = `/api/editor/exports/${pipeline.export_id}/file`;
  if (!url) return null;
  const title = [pipeline.project_title, pipeline.title, pipeline.snapshot?.title].find(value => typeof value === "string" && value.trim()) || "未命名影片";
  const duration = pipeline.snapshot?.duration ?? pipeline.duration;
  return { id: pipeline.id, project_id: typeof pipeline.project_id === "string" ? pipeline.project_id : "", title, completed_at: pipeline.completed_at || pipeline.updated_at || pipeline.created_at || "", duration: typeof duration === "number" && Number.isFinite(duration) && duration > 0 ? duration : null, video_url: url, editor_url: safeId(pipeline.editor_project_id) ? `/editor?project=${pipeline.editor_project_id}` : "" };
}

function scriptVideoHistoryDownloadUrl(url) {
  if (!scriptPipelineVideoUrl({ status: "completed", video_url: url }) || /[\\#]|\.\./.test(url)) return "";
  const parsed = new URL(url, "http://studio.local"); parsed.searchParams.set("download", "1"); return `${parsed.pathname}${parsed.search}`;
}

function scriptVideoReferencesError(project) {
  const references = normalizeScriptReferences(project?.character_references);
  const characters = references.filter(item => ["character", "creature"].includes(item.type));
  if (!references.some(item => item.image_asset_id)) return "請先在「角色外觀對照」新增角色、場景或物件，並上傳至少一張參考圖。";
  if (references.some(item => !item.name.trim())) return "請先填寫每張圖片對照卡的名稱。";
  if (characters.some(item => !item.image_asset_id || !item.appearance.trim())) return "每個角色或生物都需要參考圖片及外觀描述，才能自動生成多模態影片。";
  if (references.some(item => item.image_asset_id && !item.appearance.trim())) return "請先分析圖片並套用外觀描述，或自行填寫描述，再自動生成影片。";
  if (references.some(item => item.appearance.length > 1200)) return "自動影片使用的外觀描述最多 1200 字，請先縮短較長的描述。";
  return "";
}

function scriptOutputBudgetNote(job) {
  const budget = job?.input?.settings?.num_predict;
  const capacity = job?.input?.settings?.num_ctx;
  const notes = [];
  if (job?.context_budget_adjusted === true && Number.isSafeInteger(capacity) && capacity > 0) notes.push("已依完整稿件調整本次處理容量，原文保留。");
  if (job?.output_budget_adjusted === true && Number.isSafeInteger(budget) && budget > 0) notes.push(`依稿件長度，本次輸出上限調整為 ${budget} Token。`);
  return notes.join(" ");
}

function scriptStoryboardNote(job) {
  if (job?.action !== "storyboard" || job.truncated) return "";
  if (job.status === "failed" && String(job.error || "").includes("H3 分鏡的每個鏡頭必須介於 5～15 秒。")) return "短切鏡需合併成 5～15 秒生成片段，更新後可使用原設定接續。";
  if (job.status !== "completed" || job.truncated) return "";
  const metadata = job.storyboard?.storyboard_normalization || job.storyboard_normalization;
  if (!metadata || !Number.isSafeInteger(metadata.original_shot_count) || metadata.original_shot_count < 1 || !Number.isSafeInteger(metadata.generation_clip_count) || metadata.generation_clip_count < 1 || !Array.isArray(metadata.clip_durations) || metadata.clip_durations.length !== metadata.generation_clip_count || metadata.clip_durations.some(value => typeof value !== "number" || !Number.isFinite(value) || value <= 0)) return "";
  const duration = metadata.clip_durations.reduce((total, value) => total + value, 0);
  if (!Number.isFinite(duration)) return "";
  return `已將${metadata.original_shot_count}個切鏡整理為${metadata.generation_clip_count}個影片片段，總長${Number(duration.toFixed(3))}秒；原始分鏡保留。`;
}

function scriptStoryboardCameraNote(job) {
  if (job?.action !== "storyboard" || job.status !== "completed" || job.truncated) return "";
  const metadata = job.storyboard?.storyboard_camera_normalization || job.storyboard_camera_normalization;
  if (!metadata || !Number.isSafeInteger(metadata.correction_count) || metadata.correction_count < 1 || !Array.isArray(metadata.corrections) || metadata.corrections.length !== metadata.correction_count || metadata.corrections.some(item => !item || !Number.isSafeInteger(item.scene_index) || item.scene_index < 0 || !Number.isSafeInteger(item.shot_index) || item.shot_index < 0 || typeof item.original_camera !== "string" || !item.original_camera.trim() || typeof item.original_camera_detail !== "string" || item.camera !== "custom" || typeof item.camera_detail !== "string" || !item.camera_detail.trim())) return "";
  return `已整理${metadata.correction_count}個鏡頭的運鏡設定，原有焦段與機位描述已保留；原始分鏡未改。`;
}

function scriptLocalEndpoint(value) {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname.toLowerCase()) && !parsed.username && !parsed.password && !parsed.search && !parsed.hash && (!parsed.pathname || parsed.pathname === "/");
  } catch (_) { return false; }
}

function normalizeScriptSkills(value) {
  if (!Array.isArray(value) || !value.length) throw new Error("創作 Skill 清單回應不完整，請重新檢查。");
  const seen = new Set();
  const lines = value => typeof value === "string" ? [value] : Array.isArray(value) ? value.filter(item => typeof item === "string") : [];
  return value.map(item => {
    if (!item || typeof item.id !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(item.id) || seen.has(item.id) || typeof item.name !== "string" || !item.name.trim()) throw new Error("創作 Skill 清單回應不完整，請重新檢查。");
    seen.add(item.id);
    return { id: item.id, name: item.name, category: typeof item.category === "string" ? item.category : "", description: typeof item.description === "string" ? item.description : "", recommended_for: lines(item.recommended_for), guide: lines(item.guide), sources: Array.isArray(item.sources) ? item.sources.filter(source => source && typeof source === "object").map(source => ({ name: typeof source.name === "string" ? source.name : "參考來源", url: typeof source.url === "string" ? source.url : "", license: typeof source.license === "string" ? source.license : "", license_url: typeof source.license_url === "string" ? source.license_url : "" })) : [] };
  });
}

function scriptSkillSourceUrl(value) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : ""; } catch (_) { return ""; }
}

function scriptRecommendedWritingSkill(project, skills) {
  const text = `${project?.genre || ""}\n${project?.idea || ""}`;
  const rules = [["suspense-horror", /恐怖|懸疑|驚悚|鬼|詭異|異常腳步|背後.*腳步|horror|suspense/i], ["game-trailer", /遊戲.*(?:預告|宣傳)|(?:預告|宣傳).*遊戲|game.*trailer/i], ["product-demo", /產品.*(?:示範|教學|操作)|(?:示範|操作教學).*產品|product.*demo/i], ["product-commercial", /廣告|商品|產品|commercial/i], ["educational-explainer", /科普|知識|教學|解說|原理|educational|explainer/i], ["comedy-reaction", /喜劇|搞笑|笑點|尷尬|comedy/i], ["emotional-drama", /情感|親情|愛情|失戀|告白|重逢|思念|emotional/i], ["fantasy-adventure", /奇幻|魔法|冒險|遺跡|fantasy|adventure/i], ["action-chase", /動作|追逐|追車|打鬥|action|chase/i], ["documentary-lifestyle", /紀錄片|生活紀錄|旅遊|紀實|documentary|lifestyle/i]];
  const id = rules.find(([id, expression]) => skills.some(skill => skill.id === id) && expression.test(text))?.[0] || "cinematic-story";
  return skills.find(skill => skill.id === id) || null;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { normalizeScriptProject, normalizeScriptReferences, createNewScriptProject, createMonkeyScriptExample, scriptProjectSignature, scriptSaveOutcome, scriptDraftCandidates, scriptJobPresentation, scriptCanDeleteJob, scriptAppearanceApplyTarget, scriptPipelinePresentation, scriptPipelineVideoUrl, scriptVideoReferencesError, scriptLocalEndpoint, normalizeScriptSkills, scriptSkillSourceUrl, scriptRecommendedWritingSkill, scriptVideoHistoryRecord, scriptVideoHistoryDownloadUrl };
}

if (typeof module !== "undefined" && module.exports) Object.assign(module.exports, { normalizeScriptLoras, scriptLoraSourceUrl, scriptLoraSelectionError, scriptLoraSaveConfirmed });

if (typeof document !== "undefined") {
  const $ = id => document.getElementById(id);
  const clientId = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const fieldIds = { title: "scriptTitle", idea: "scriptIdea", genre: "scriptGenre", duration: "scriptDuration", characters: "scriptCharacters", style: "scriptStyle", instructions: "scriptInstructions", content: "scriptContent", writing_skill: "scriptWritingSkill" };
  const state = { project: createNewScriptProject(), projects: [], jobs: [], job: null, selectedJobId: null, ready: false, visionReady: false, referencesSupported: false, referencesSupportKnown: false, referencesSaveWaiting: false, engineCheckPromise: null, messageKind: "", settings: null, dirty: false, loading: false, saving: false, starting: false, importing: false, pollBusy: false, settingsDirty: false, settingsSaving: false, conflict: null, recovery: null, ownDrafts: {}, newDraftCounter: 1, newDraftId: "__new__-1", draftAvailable: true, jobsRevision: 0, generation: 0, referenceControls: new Map(), referenceUploads: new Map(), referenceCounter: 0, historyScope: "all", historyPersistent: false, historyStorage: "loading", historyError: "", activeJobs: [], deletingJobIds: new Set(), deletedJobIds: new Set(), historyDeleteControls: new Map(), autoVideo: false, videoPipelinesSupported: false, videoReady: false, videoError: "", pipelines: [], pipeline: null, selectedPipelineId: null, pipelineStarting: false, pipelinePollBusy: false, pipelinesRevision: 0 };
  Object.assign(state, { writingSkills: [], skillsSupported: false, skillsSupportKnown: false, skillsCatalogReady: false, skillsLoading: false, skillsError: "", skillsSaveWaiting: false });
  Object.assign(state, { videoHistoryScope: "all", historyVideos: [], selectedHistoryVideoId: null, historyPreviewVideo: null, historyPreviewError: "", videoHistoryStatus: "loading", videoHistoryError: "", videoHistoryWarning: "", videoHistoryBusy: false });
  Object.assign(state, { lorasSupported: false, lorasSupportKnown: false, lorasSaveWaiting: false, loraCatalog: { names: [], items: [], builtin_items: [] }, loraCatalogReady: false, loraCatalogBusy: false, loraCatalogError: "", loraCatalogSerial: 0, loraControls: [] });

  function message(text, error = false, kind = "") {
    state.messageKind = kind;
    $("scriptMessage").textContent = text;
    $("scriptMessage").classList.toggle("hidden", !text);
    $("scriptMessage").classList.toggle("error", error);
  }

  async function api(path, options = {}) {
    let response;
    const multipart = typeof FormData !== "undefined" && options.body instanceof FormData;
    try { response = await fetch(path, { ...options, headers: { ...(multipart ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) }, cache: "no-store" }); }
    catch (_) { throw new Error("無法連線到 Studio 主機。編輯稿仍保留在這個分頁；恢復連線後可再儲存。"); }
    let data;
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : Array.isArray(data.detail) ? data.detail.map(item => item.msg || "欄位格式錯誤").join("；") : "";
      const error = new Error(response.status === 404 && !detail ? "目前 Studio 尚未提供腳本編寫 API。請重新啟動更新後的 H3 Studio。" : detail || `Studio 請求失敗（${response.status}）。`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function listItems(data) { return Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []; }
  function dateLabel(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-TW", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  function readProject() { const value = { ...state.project, character_references: normalizeScriptReferences(state.project.character_references), custom_loras: normalizeScriptLoras(state.project.custom_loras) }; for (const [field, id] of Object.entries(fieldIds)) value[field] = field === "duration" ? Number($(id).value) : $(id).value; return value; }
  function writeProject(project) {
    const value = normalizeScriptProject(project);
    if (!Array.from($("scriptGenre").options).some(option => option.value === value.genre)) $("scriptGenre").add(new Option(value.genre, value.genre));
    renderWritingSkills(value.writing_skill);
    for (const [field, id] of Object.entries(fieldIds)) $(id).value = value[field];
    state.project = value;
    renderWritingSkillDetails();
    renderReferences();
    renderScriptLoras();
    renderVideoHistory();
    updateControls();
  }

  function loraMetadata(name) {
    const help = globalThis.H3LoraHelp;
    return help?.item ? help.item(state.loraCatalog, name, { model: "h3" }) : state.loraCatalog.items.find(item => item.name === name) || { name, title: name, purpose: "自訂 LoRA；用途尚未確認，請查看作者說明。", known: false };
  }

  function loraFamily(item) { return item?.known === true && ["fl2va", "ref2va", "both"].includes(item.family) ? item.family : null; }

  function renderLoraHelp(container, metadata) {
    if (globalThis.H3LoraHelp?.render) { globalThis.H3LoraHelp.render(container, metadata); return; }
    container.replaceChildren();
    const title = document.createElement("strong"); title.textContent = metadata.title || metadata.name || "自訂 LoRA"; container.append(title);
    for (const [label, value] of [["用途", metadata.purpose], ["適用", loraFamily(metadata) === "both" ? "FL2VA／Ref2VA" : loraFamily(metadata)?.toUpperCase() || "未知；請依作者说明選擇"], ["觸發詞", metadata.trigger], ["用法", metadata.usage], ["限制", metadata.limits]]) {
      if (typeof value !== "string" || !value.trim()) continue;
      const line = document.createElement("p"); line.textContent = `${label}：${value}`; container.append(line);
    }
    const url = scriptLoraSourceUrl(metadata.source_url);
    if (url) { const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "作者與來源 ↗"; container.append(link); }
  }

  function renderScriptLoras() {
    const container = $("scriptLoraRows"); container.replaceChildren(); state.loraControls = [];
    const rows = state.project.custom_loras || [];
    $("scriptLoraCount").textContent = `${rows.length} / 4`;
    $("scriptLoraCatalogStatus").textContent = state.loraCatalogBusy ? "正在重新掃描引擎 LoRA…" : state.loraCatalogError ? `${state.loraCatalogError} 已保留目前選擇與上次清單。` : !state.loraCatalogReady ? "尚未讀取引擎清單；已保存的選擇仍可查看。" : state.loraCatalog.names.length ? `引擎目前有 ${state.loraCatalog.names.length} 個自訂 LoRA。` : "引擎尚無可選的自訂 LoRA；內建 Turbo 由影片品質模式管理。";
    $("scriptLoraCompatibility").textContent = rows.some(row => row.name) && !state.lorasSupported ? state.lorasSupportKnown ? "目前主機尚未支援劇本 LoRA 儲存；選擇與本機草稿會保留，請更新主機後重新檢查。" : "暫時無法確認主機的劇本 LoRA 儲存功能；選擇仍保留。" : scriptLoraSelectionError(rows, { video: true, catalog: state.loraCatalog });
    if (!rows.length) { const empty = document.createElement("p"); empty.className = "script-hint"; empty.textContent = "目前未啟用 LoRA。可自由選用，保持空白即可使用原影片設定。"; container.append(empty); }
    rows.forEach((row, index) => {
      const card = document.createElement("article"), grid = document.createElement("div"), modelLabel = document.createElement("label"), select = document.createElement("select"), strengthLabel = document.createElement("label"), strength = document.createElement("input"), familyLabel = document.createElement("label"), family = document.createElement("select"), enabledLabel = document.createElement("label"), enabled = document.createElement("input"), remove = document.createElement("button"), details = document.createElement("details"), summary = document.createElement("summary"), help = document.createElement("div");
      card.className = "script-lora-card"; grid.className = "script-lora-grid";
      modelLabel.textContent = "LoRA 檔案"; select.add(new Option("選擇引擎中的 LoRA", ""));
      const names = [...new Set([...state.loraCatalog.names, ...(row.name ? [row.name] : [])])];
      for (const name of names) { const metadata = loraMetadata(name), option = new Option(globalThis.H3LoraHelp?.label ? globalThis.H3LoraHelp.label(metadata) : `${metadata.title || name} · ${metadata.purpose || "用途未確認"}`, name); option.disabled = loraFamily(metadata) === "fl2va" || state.loraCatalog.builtin_items.some(item => item.name === name); if (option.disabled) option.textContent += "（不適用腳本 Ref2VA）"; else if (!state.loraCatalog.names.includes(name)) option.textContent += "（尚未在清單確認）"; select.add(option); }
      select.value = row.name; modelLabel.append(select);
      strengthLabel.textContent = "強度"; strength.type = "number"; strength.min = "0"; strength.max = "2"; strength.step = "0.05"; strength.value = row.strength; strengthLabel.append(strength);
      familyLabel.textContent = "適用模型"; family.add(new Option("請依作者說明選擇", "")); family.add(new Option("Ref2VA 多模態", "ref2va")); family.add(new Option("FL2VA／Ref2VA 兩者", "both")); const fl = new Option("FL2VA（不適用自動影片）", "fl2va"); fl.disabled = true; family.add(fl); family.value = row.family; familyLabel.append(family);
      enabledLabel.className = "script-lora-enabled"; enabled.type = "checkbox"; enabled.checked = row.enabled; const enabledText = document.createElement("span"); enabledText.textContent = "啟用"; enabledLabel.append(enabled, enabledText);
      remove.type = "button"; remove.className = "button ghost small"; remove.textContent = "移除";
      grid.append(modelLabel, strengthLabel, familyLabel, enabledLabel, remove); card.append(grid);
      help.className = "script-lora-help"; renderLoraHelp(help, loraMetadata(row.name)); card.append(help);
      select.addEventListener("change", () => { const metadata = loraMetadata(select.value); if (loraFamily(metadata) === "fl2va" || state.loraCatalog.builtin_items.some(item => item.name === select.value)) { select.value = row.name; message("這個檔案不能用在脚本 Ref2VA；請選擇相容 LoRA。", true); return; } row.name = select.value; row.family = loraFamily(metadata) || ""; row.enabled = false; markDirty(); renderScriptLoras(); updateControls(); });
      strength.addEventListener("input", () => { row.strength = strength.value === "" ? NaN : Number(strength.value); markDirty(); renderLoraStatus(); });
      family.addEventListener("change", () => { const fixed = loraFamily(loraMetadata(row.name)); if (fixed && fixed !== "both" && family.value !== fixed) { family.value = row.family; message("已知 LoRA 的適用模型依作者設定固定，請改選正確版本。", true); return; } row.family = family.value; markDirty(); renderLoraStatus(); });
      enabled.addEventListener("change", () => { if (enabled.checked && (row.family === "fl2va" || loraFamily(loraMetadata(row.name)) === "fl2va")) { enabled.checked = row.enabled; message("FL2VA LoRA 不能啟用於脚本 Ref2VA，請保留停用或移除。", true); return; } row.enabled = enabled.checked; markDirty(); renderLoraStatus(); });
      remove.addEventListener("click", () => { state.project.custom_loras.splice(index, 1); markDirty(); renderScriptLoras(); updateControls(); });
      state.loraControls.push({ select, strength, family, enabled, remove, row }); container.append(card);
    });
  }

  function renderLoraStatus() { $("scriptLoraCompatibility").textContent = scriptLoraSelectionError(readProject().custom_loras, { video: true, catalog: state.loraCatalog }) || (readProject().custom_loras.length && !state.lorasSupported ? "主機尚未確認劇本 LoRA 儲存功能；選擇與草稿仍保留。" : ""); updateControls(); }

  function addScriptLora() { if (state.loading || state.project.custom_loras.length >= 4) return; state.project.custom_loras.push({ name: "", strength: 0.5, family: "", enabled: false }); markDirty(); renderScriptLoras(); updateControls(); }

  async function loadScriptLoras() {
    const serial = ++state.loraCatalogSerial; state.loraCatalogBusy = true; state.loraCatalogError = ""; renderScriptLoras(); updateControls();
    try {
      const data = await api("/api/loras"); if (serial !== state.loraCatalogSerial) return false;
      if (!data || !Array.isArray(data.names)) throw new Error("主機回傳的 LoRA 清單不完整。");
      const help = globalThis.H3LoraHelp;
      state.loraCatalog = help?.catalog ? help.catalog(data, { model: "h3" }) : { names: data.names.filter(name => typeof name === "string"), items: [], builtin_items: Array.isArray(data.builtin_items) ? data.builtin_items : [] };
      state.loraCatalogReady = true; return true;
    } catch (error) { if (serial === state.loraCatalogSerial) state.loraCatalogError = error.message || "暫時無法讀取引擎 LoRA。"; return false; }
    finally { if (serial === state.loraCatalogSerial) { state.loraCatalogBusy = false; renderScriptLoras(); updateControls(); } }
  }

  function writingSkillsAvailable() { return state.skillsSupported && state.skillsSupportKnown && state.skillsCatalogReady; }
  function writingSkillNeedsCheck() { const selected = $("scriptWritingSkill").value; return Boolean(selected) && (!writingSkillsAvailable() || !state.writingSkills.some(skill => skill.id === selected)); }

  function renderWritingSkills(selected = readProject().writing_skill) {
    const select = $("scriptWritingSkill");
    select.replaceChildren(new Option("不套用／保留原設定", ""));
    for (const skill of state.writingSkills) select.add(new Option(`${skill.name}${skill.category ? ` · ${skill.category}` : ""}`, skill.id));
    if (selected && !state.writingSkills.some(skill => skill.id === selected)) {
      const option = new Option(`${selected === "cinematic-story" ? "電影敘事" : `已選 Skill：${selected}`} · ${state.skillsSupportKnown ? "清單未提供" : "正在讀取"}`, selected); option.disabled = true; select.add(option);
    }
    select.value = selected;
    renderWritingSkillDetails();
  }

  function renderWritingSkillDetails() {
    const selected = $("scriptWritingSkill").value, skill = state.writingSkills.find(item => item.id === selected);
    const available = writingSkillsAvailable();
    $("scriptSkillStatus").textContent = state.skillsLoading ? "正在讀取創作方法；所選 Skill 與稿件會保留。" : !state.skillsSupportKnown ? state.skillsError || "正在確認創作 Skill 功能；可選擇不套用，保留原設定。" : !state.skillsSupported ? "主機尚未提供創作 Skill 功能。所選設定仍保留；重新啟動更新後的 Studio 並重新檢查，或選擇不套用。" : state.skillsError ? `${state.skillsError} 所選設定與稿件仍保留。` : "一次選擇一種創作方法；你的風格、創作要求與角色設定會保留。";
    $("scriptSkillStatus").classList.toggle("error", Boolean(state.skillsError) || state.skillsSupportKnown && !state.skillsSupported);
    $("scriptSkillSummary").textContent = skill ? skill.description : selected ? "所選創作方法尚未讀取，可繼續編輯稿件。" : "依照目前的故事設定、風格與創作要求編寫。";
    $("scriptSkillSuitable").textContent = skill?.recommended_for.length ? `適用：${skill.recommended_for.join("、")}` : "";
    $("scriptSkillGuide").replaceChildren(); $("scriptSkillSources").replaceChildren();
    $("scriptSkillDetails").classList.toggle("hidden", !skill);
    if (skill) {
      for (const method of skill.guide) { const item = document.createElement("li"); item.textContent = method; $("scriptSkillGuide").append(item); }
      for (const source of skill.sources) {
        const item = document.createElement("li"), url = scriptSkillSourceUrl(source.url), licenseUrl = scriptSkillSourceUrl(source.license_url);
        if (url) { const link = document.createElement("a"); link.textContent = source.name; link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link); }
        else { const name = document.createElement("span"); name.textContent = source.name; item.append(name); }
        const license = document.createElement(licenseUrl ? "a" : "span"); license.textContent = ` · ${source.license || "來源未列出授權"}`;
        if (licenseUrl) { license.href = licenseUrl; license.target = "_blank"; license.rel = "noopener noreferrer"; } item.append(license); $("scriptSkillSources").append(item);
      }
    }
    const recommendation = available ? scriptRecommendedWritingSkill(readProject(), state.writingSkills) : null;
    $("scriptSkillRecommendation").textContent = recommendation ? `依類型與故事想法推薦：${recommendation.name}${recommendation.id === selected ? "（目前已選）" : "；按下按鈕才會套用。"}` : "讀取清單後，可依類型與故事想法提供推薦。";
    $("applyScriptSkillRecommendation").disabled = state.loading || !recommendation || recommendation.id === selected;
  }

  function applyWritingSkillRecommendation() {
    if (state.loading || !writingSkillsAvailable()) return;
    const skill = scriptRecommendedWritingSkill(readProject(), state.writingSkills); if (!skill) return;
    $("scriptWritingSkill").value = skill.id; markDirty();
  }

  async function loadWritingSkills() {
    state.skillsLoading = true; renderWritingSkillDetails(); updateControls();
    try {
      const catalog = await api("/api/scripts/skills");
      state.writingSkills = normalizeScriptSkills(catalog?.items); state.skillsCatalogReady = true; state.skillsError = "";
      if (state.messageKind === "skills-support") message("");
      return true;
    } catch (error) { state.skillsError = error.status === 404 ? "主機尚未提供創作 Skill 清單，請重新啟動更新後的 Studio 並重新檢查。" : "暫時無法讀取創作 Skill 清單，請重新檢查主機連線。"; return false; }
    finally { state.skillsLoading = false; renderWritingSkills(); updateControls(); }
  }

  function referenceById(id) { return state.project.character_references.find(item => item.id === id); }

  function renderReferences() {
    const container = $("scriptCharacterReferences");
    container.replaceChildren();
    state.referenceControls.clear();
    $("scriptReferenceCount").textContent = `${state.project.character_references.length} / 16`;
    if (!state.project.character_references.length) {
      const empty = document.createElement("p"); empty.className = "script-empty"; empty.textContent = "新增角色，將參考圖片對應到名稱。也可以直接手寫外觀描述。"; container.append(empty);
    }
    for (const reference of state.project.character_references) {
      const card = document.createElement("article"), heading = document.createElement("div"), nameLabel = document.createElement("label"), name = document.createElement("input"), typeLabel = document.createElement("label"), type = document.createElement("select"), remove = document.createElement("button");
      card.className = "script-reference-card"; card.dataset.referenceId = reference.id;
      heading.className = "script-reference-heading";
      nameLabel.textContent = "名稱"; name.value = reference.name; name.maxLength = 60; name.placeholder = "例如：金猴爺"; name.dataset.referenceField = "name"; nameLabel.append(name);
      typeLabel.textContent = "類型"; for (const [value, label] of Object.entries(SCRIPT_REFERENCE_TYPES)) type.add(new Option(label, value)); type.value = reference.type; type.dataset.referenceField = "type"; typeLabel.append(type);
      remove.type = "button"; remove.className = "button ghost small"; remove.textContent = "移除"; remove.setAttribute("aria-label", `移除 ${reference.name || "未命名角色"}`);
      heading.append(nameLabel, typeLabel, remove); card.append(heading);
      if (/^[a-f0-9]{32}$/i.test(reference.image_asset_id)) {
        const image = document.createElement("img"); image.className = "script-reference-image"; image.src = `/api/assets/${encodeURIComponent(reference.image_asset_id)}`; image.alt = `${reference.name || "未命名角色"}的參考圖片`; image.loading = "lazy"; card.append(image);
      }
      const uploadLabel = document.createElement("label"), file = document.createElement("input"), imageName = document.createElement("small");
      uploadLabel.textContent = reference.image_asset_id ? "更換參考圖片" : "參考圖片"; file.type = "file"; file.accept = "image/png,image/jpeg,image/webp,image/bmp,.png,.jpg,.jpeg,.webp,.bmp"; file.dataset.referenceField = "image"; uploadLabel.append(file); imageName.className = "script-reference-filename"; imageName.textContent = reference.image_name || "PNG、JPG、WebP、BMP；每張最多 8 MiB。"; card.append(uploadLabel, imageName);
      const appearanceLabel = document.createElement("label"), appearance = document.createElement("textarea"), actions = document.createElement("div"), analyze = document.createElement("button"), status = document.createElement("small");
      appearanceLabel.textContent = "外觀描述"; appearance.value = reference.appearance; appearance.rows = 4; appearance.maxLength = 2400; appearance.placeholder = "體型、毛髮、臉部、服裝、配件、主要色彩；可分析圖片後自行修正。"; appearance.dataset.referenceField = "appearance"; appearanceLabel.append(appearance);
      actions.className = "script-reference-actions"; analyze.type = "button"; analyze.className = "button secondary small"; analyze.textContent = "分析外型"; status.className = "script-hint"; status.setAttribute("role", "status"); actions.append(analyze, status); card.append(appearanceLabel, actions);
      name.addEventListener("input", () => { const current = referenceById(reference.id); if (current) { current.name = name.value; markDirty(); } });
      type.addEventListener("change", () => { const current = referenceById(reference.id); if (current) { current.type = type.value; markDirty(); } });
      appearance.addEventListener("input", () => { const current = referenceById(reference.id); if (current) { current.appearance = appearance.value; markDirty(); } });
      file.addEventListener("change", () => { if (file.files?.[0]) uploadReferenceImage(reference.id, file.files[0]); });
      analyze.addEventListener("click", () => generate("appearance", reference.id));
      remove.addEventListener("click", () => removeReference(reference.id));
      state.referenceControls.set(reference.id, { name, type, appearance, file, remove, analyze, status }); container.append(card);
    }
  }

  function addReference() {
    if (state.loading || state.project.character_references.length >= 16) return;
    const id = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : `${Date.now().toString(16)}${(++state.referenceCounter).toString(16)}${Math.random().toString(16).slice(2)}`.padEnd(32, "0").slice(0, 32);
    state.project.character_references.push({ id, name: "", type: "character", image_asset_id: "", image_name: "", appearance: "" });
    markDirty(); renderReferences(); updateControls(); state.referenceControls.get(id)?.name.focus();
  }

  function removeReference(id) {
    if (state.loading) return;
    state.project.character_references = state.project.character_references.filter(item => item.id !== id);
    state.referenceUploads.delete(id); markDirty(); renderReferences(); updateControls();
  }

  async function uploadReferenceImage(id, file) {
    const reference = referenceById(id);
    if (!reference || state.loading) return;
    if (!file || file.size <= 0 || file.size > SCRIPT_IMAGE_MAX_BYTES || !/\.(png|jpe?g|webp|bmp)$/i.test(file.name || "")) { message("請選擇 8 MiB 以內的 PNG、JPG、WebP 或 BMP 圖片。", true); return; }
    const generation = state.generation, previousImage = reference.image_asset_id, previousAppearance = reference.appearance, token = {};
    state.referenceUploads.set(id, token); updateControls();
    try {
      const body = new FormData(); body.append("file", file);
      const image = await api("/api/scripts/reference-images", { method: "POST", body });
      const current = referenceById(id);
      if (generation !== state.generation || state.referenceUploads.get(id) !== token || !current || current.image_asset_id !== previousImage) return;
      if (!/^[a-f0-9]{32}$/i.test(image.id || "")) throw new Error("主機未回傳有效的參考圖片，請重新上傳。");
      const appearanceChanged = current.appearance !== previousAppearance;
      current.image_asset_id = image.id; current.image_name = image.name || file.name; if (!appearanceChanged) current.appearance = "";
      markDirty(); renderReferences(); message(appearanceChanged ? "圖片已更新；上傳期間手寫的外觀描述已保留，請確認是否符合新圖片。" : "圖片已對應到角色。按「分析外型」取得描述，確認後再套用；也可以直接手寫。更換圖片時已清除舊外觀描述。");
    } catch (error) { if (generation === state.generation && state.referenceUploads.get(id) === token && referenceById(id)) message(error.message, true); }
    finally { if (state.referenceUploads.get(id) === token) state.referenceUploads.delete(id); updateControls(); }
  }

  function draftKey(project) { return project.id || state.newDraftId; }
  function storeDraft() {
    const project = readProject();
    if (state.dirty) state.ownDrafts[draftKey(project)] = { project, savedAt: Date.now(), writer: clientId };
    else delete state.ownDrafts[draftKey(project)];
    try {
      if (Object.keys(state.ownDrafts).length) localStorage.setItem(SCRIPT_DRAFT_PREFIX + clientId, JSON.stringify(state.ownDrafts));
      else localStorage.removeItem(SCRIPT_DRAFT_PREFIX + clientId);
      state.draftAvailable = true;
    } catch (_) { state.draftAvailable = false; }
  }

  function allDrafts() {
    const result = [];
    try {
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (!key?.startsWith(SCRIPT_DRAFT_PREFIX)) continue;
        try { const entries = JSON.parse(localStorage.getItem(key) || "{}"); for (const [entryKey, record] of Object.entries(entries)) result.push({ ...record, storageKey: key, entryKey }); } catch (_) { /* An incomplete old draft cannot prevent startup. */ }
      }
    } catch (_) { state.draftAvailable = false; }
    return result;
  }

  function offerDraft() {
    const candidates = scriptDraftCandidates(allDrafts(), state.project, state.project.id ? state.project : null);
    state.recovery = candidates[0] || null;
    $("scriptDraftRecovery").classList.toggle("hidden", !state.recovery);
    if (state.recovery) $("scriptDraftDetail").textContent = `${state.recovery.project.title || "未命名劇本"} · ${dateLabel(state.recovery.savedAt)}。按恢復可接續編輯；主機版本不會立即變更。`;
  }

  function markDirty() {
    state.dirty = true;
    const loraRows = state.project.custom_loras;
    state.project = readProject();
    state.project.custom_loras = loraRows;
    storeDraft();
    renderWritingSkillDetails();
    updateControls();
  }

  function updateControls() {
    const busy = state.loading || state.saving || state.referencesSaveWaiting || state.skillsSaveWaiting || state.lorasSaveWaiting || state.starting || state.pipelineStarting;
    $("scriptProjects").disabled = busy || state.importing;
    $("newScriptProject").disabled = busy || state.importing;
    $("applyMonkeyScriptExample").disabled = busy || state.importing;
    $("saveScriptProject").disabled = busy || Boolean(state.conflict);
    $("saveScriptProject").textContent = state.saving ? "正在儲存…" : state.referencesSaveWaiting || state.skillsSaveWaiting || state.lorasSaveWaiting ? "確認儲存功能…" : "儲存劇本";
    $("addScriptLora").disabled = state.loading || state.project.custom_loras.length >= 4;
    $("refreshScriptLoras").disabled = state.loraCatalogBusy;
    for (const controls of state.loraControls) { for (const key of ["select", "strength", "enabled", "remove"]) controls[key].disabled = state.loading; const family = loraFamily(loraMetadata(controls.row.name)); controls.family.disabled = state.loading || Boolean(family && family !== "both"); }
    $("exportScript").disabled = state.loading || state.saving;
    for (const id of Object.values(fieldIds)) $(id).disabled = state.loading;
    for (const option of Array.from($("scriptWritingSkill").options)) option.disabled = Boolean(option.value) && (!writingSkillsAvailable() || !state.writingSkills.some(skill => skill.id === option.value));
    const recommendation = writingSkillsAvailable() ? scriptRecommendedWritingSkill(readProject(), state.writingSkills) : null;
    $("applyScriptSkillRecommendation").disabled = state.loading || !recommendation || recommendation.id === $("scriptWritingSkill").value;
    const activeForProject = [...state.jobs, ...state.activeJobs].some(job => job.project_id === state.project.id && scriptJobPresentation(job).active) || (state.job?.project_id === state.project.id && scriptJobPresentation(state.job).active);
    document.querySelectorAll("[data-script-action]").forEach(button => { button.disabled = busy || state.settingsSaving || !state.ready || writingSkillNeedsCheck() || Boolean(state.conflict) || activeForProject; button.setAttribute("aria-busy", String(state.starting)); });
    $("addScriptReference").disabled = state.loading || state.project.character_references.length >= 16;
    for (const [id, controls] of state.referenceControls) {
      for (const field of ["name", "type", "appearance", "file", "remove"]) controls[field].disabled = state.loading;
      const reference = referenceById(id), uploading = state.referenceUploads.has(id);
      controls.analyze.disabled = busy || state.settingsSaving || Boolean(state.conflict) || activeForProject || !state.visionReady || uploading || !reference?.name.trim() || !reference?.image_asset_id;
      controls.status.textContent = uploading ? "正在上傳圖片…" : !state.visionReady ? "圖片分析模型尚未就緒；可先手寫外觀。" : !reference?.name.trim() ? "先填寫名稱。" : !reference?.image_asset_id ? "上傳圖片後可分析。" : "分析結果會先顯示在生成預覽。";
    }
    const presentation = scriptJobPresentation(state.job);
    const ownJob = state.job?.project_id === state.project.id;
    const deletingSelected = state.deletingJobIds.has(state.job?.id);
    $("applyScriptResult").disabled = !presentation.canApply || !ownJob || busy || deletingSelected;
    $("importScriptStoryboard").disabled = !presentation.canImport || !ownJob || busy || state.importing || deletingSelected;
    $("openScriptJobProject").disabled = busy || state.importing || !state.projects.some(project => project.id === state.job?.project_id);
    const activePipeline = state.pipelines.some(pipeline => pipeline.project_id === state.project.id && scriptPipelinePresentation(pipeline).active) || (state.pipeline?.project_id === state.project.id && scriptPipelinePresentation(state.pipeline).active);
    $("scriptAutoVideo").checked = state.autoVideo;
    $("scriptAutoVideo").disabled = busy || !state.videoPipelinesSupported;
    $("scriptVideoSource").disabled = busy;
    const previewOption = Array.from($("scriptVideoSource").options).find(option => option.value === "preview");
    if (previewOption) previewOption.disabled = !selectedVideoSourceJob();
    if ($("scriptVideoSource").value === "preview" && !selectedVideoSourceJob()) $("scriptVideoSource").value = "current";
    $("startScriptVideoPipeline").disabled = busy || state.settingsSaving || !state.videoPipelinesSupported || !state.videoReady || !state.ready || writingSkillNeedsCheck() || Boolean(state.conflict) || activePipeline || activeForProject;
    $("startScriptVideoPipeline").textContent = state.pipelineStarting ? "正在啟動製作…" : "劇本 → 自動生成影片";
    $("cancelScriptVideoPipeline").disabled = !state.videoPipelinesSupported || !scriptPipelinePresentation(state.pipeline).active || state.pipeline?.status === "cancelling";
    $("resumeScriptVideoPipeline").disabled = busy || !state.videoPipelinesSupported || !state.videoReady || !["failed", "cancelled", "canceled", "interrupted"].includes(state.pipeline?.status);
    for (const [id, button] of state.historyDeleteControls) button.disabled = state.loading || state.importing || state.deletingJobIds.has(id) || !scriptCanDeleteJob(state.jobs.find(job => job.id === id));
    $("importScriptStoryboard").textContent = state.importing ? "正在建立分鏡…" : "建立短片分鏡 →";
    $("saveScriptCopy").disabled = busy;
    $("retryScriptSave").disabled = busy || !state.conflict?.server;
    $("viewScriptServer").disabled = busy;
    $("scriptWordCount").textContent = `${Array.from($("scriptContent").value).length.toLocaleString("zh-TW")} 字`;
    $("scriptRevision").textContent = state.project.id ? `主機版本 ${state.project.revision} · ${dateLabel(state.project.updated_at)}` : "手寫與 AI 生成可以接續編輯";
    const status = $("scriptSaveStatus");
    status.classList.toggle("saved", Boolean(state.project.id) && !state.dirty && !state.saving && !state.conflict);
    status.classList.toggle("dirty", state.dirty || Boolean(state.conflict));
    status.textContent = state.saving ? "正在儲存至 Studio 主機…" : state.loading ? "正在讀取劇本…" : state.conflict ? "版本衝突 · 編輯內容已保留" : state.dirty ? state.draftAvailable ? "有未存修改 · 本機草稿已保留" : "有未存修改 · 瀏覽器無法保存草稿，請儲存或匯出" : state.project.id ? `已儲存至 Studio 主機 · 版本 ${state.project.revision}` : "尚未儲存至 Studio 主機";
  }

  function renderProjects() {
    const select = $("scriptProjects");
    select.replaceChildren(new Option("新劇本 · 尚未建立", ""));
    for (const project of state.projects) select.add(new Option(project.title || "未命名劇本", project.id));
    select.value = state.project.id || "";
  }

  async function loadProjects() { state.projects = listItems(await api("/api/scripts/projects")); renderProjects(); }
  function clearConflict() { state.conflict = null; $("scriptConflict").classList.add("hidden"); $("scriptServerVersion").classList.add("hidden"); $("scriptServerVersion").textContent = ""; }
  async function recordConflict() {
    const id = state.project.id;
    state.conflict = { id, server: null };
    $("scriptConflict").classList.remove("hidden");
    $("scriptConflictDetail").textContent = "另一個分頁或使用者更新了這份劇本。你的編輯稿與本機草稿均已保留。";
    updateControls();
    try { const server = await api(`/api/scripts/projects/${encodeURIComponent(id)}`); if (state.project.id === id && state.conflict?.id === id) state.conflict.server = server; }
    catch (error) { message(error.message, true); }
    updateControls();
  }

  function validateProject(project) {
    if (!Number.isInteger(project.duration) || project.duration < 5 || project.duration > 7200) { $("scriptDuration").focus(); throw new Error("目標片長請填寫 5 至 7200 秒的整數。"); }
    const error = scriptLoraSelectionError(project.custom_loras, { catalog: state.loraCatalog }); if (error) throw new Error(error);
  }

  async function saveProject({ copy = false } = {}) {
    if (state.saving || state.loading || state.referencesSaveWaiting || state.skillsSaveWaiting || state.lorasSaveWaiting) return false;
    if (state.conflict && !copy) { message("請先處理版本衝突。你可以另存副本，或檢查主機版本後覆寫。", true); return false; }
    const checkingProject = readProject();
    const checkReferences = checkingProject.character_references.length && !state.referencesSupportKnown;
    const checkSkills = checkingProject.writing_skill && (!state.skillsSupportKnown || state.skillsSupported && !state.skillsCatalogReady);
    const checkLoras = checkingProject.custom_loras.length && !state.lorasSupportKnown;
    if (checkReferences || checkSkills || checkLoras) {
      const generation = state.generation;
      state.referencesSaveWaiting = Boolean(checkReferences); state.skillsSaveWaiting = Boolean(checkSkills); state.lorasSaveWaiting = Boolean(checkLoras); storeDraft(); updateControls();
      try { await refreshEngine(); }
      finally { state.referencesSaveWaiting = false; state.skillsSaveWaiting = false; state.lorasSaveWaiting = false; updateControls(); }
      if (generation !== state.generation || state.saving || state.loading || state.conflict && !copy) return false;
    }
    const sent = readProject();
    if (sent.custom_loras.length && !state.lorasSupported) { state.dirty = true; storeDraft(); renderScriptLoras(); updateControls(); message("目前主機尚未確認支援劇本 LoRA 儲存，所選 LoRA、編輯稿與本機草稿已保留；請更新主機後重新檢查，或明確移除選擇再儲存。", true, "loras-support"); return false; }
    if (sent.character_references.length && !state.referencesSupported) {
      state.dirty = true; storeDraft(); updateControls();
      message(state.referencesSupportKnown ? "目前 Studio 主機尚未提供角色外觀對照儲存功能，對照卡與本機草稿已保留。請先儲存其他工作，再重新啟動更新後的 Studio，按「重新檢查」後再儲存這份劇本。" : "暫時無法確認主機的角色外觀對照儲存功能，對照卡與本機草稿已保留。請確認 Studio 連線，按「重新檢查」後再儲存。", true, "references-support"); return false;
    }
    if (sent.writing_skill && (!writingSkillsAvailable() || !state.writingSkills.some(skill => skill.id === sent.writing_skill))) {
      state.dirty = true; storeDraft(); updateControls();
      const reason = !state.skillsSupportKnown ? "暫時無法確認主機的創作 Skill 儲存功能，請確認連線並重新檢查。" : !state.skillsSupported ? "目前主機尚未提供創作 Skill 儲存功能，請重新啟動更新後的 Studio 並重新檢查。" : !state.skillsCatalogReady ? "暫時無法讀取創作 Skill 清單，請確認連線並重新檢查。" : "所選 Skill 不在目前清單中，請重新檢查或選擇其他創作方法。";
      message(`${reason}所選 Skill、編輯稿與本機草稿已保留；也可選擇「不套用／保留原設定」後儲存。`, true, "skills-support"); return false;
    }
    validateProject(sent);
    const oldDraftKey = draftKey(sent);
    const payload = Object.fromEntries(SCRIPT_PROJECT_FIELDS.map(field => [field, sent[field]]));
    payload.title = payload.title.trim() || "未命名劇本";
    if (copy) payload.title = `${payload.title.slice(0, 114)}（副本）`;
    else if (sent.id) payload.revision = sent.revision;
    state.saving = true;
    updateControls();
    try {
      const saved = await api(sent.id && !copy ? `/api/scripts/projects/${encodeURIComponent(sent.id)}` : "/api/scripts/projects", { method: sent.id && !copy ? "PUT" : "POST", body: JSON.stringify(payload) });
      if (sent.custom_loras.length && !scriptLoraSaveConfirmed(sent.custom_loras, saved.custom_loras)) { state.lorasSupportKnown = false; state.lorasSupported = false; state.dirty = true; const error = new Error("主機未確認完整保存所選 LoRA，編輯稿與本機草稿已保留；請重新檢查主機功能再儲存。"); error.kind = "loras-support"; throw error; }
      if (sent.writing_skill && saved.writing_skill !== sent.writing_skill.trim()) {
        state.skillsSupportKnown = false; state.skillsSupported = false; state.dirty = true;
        const error = new Error("主機未確認保存所選創作 Skill，編輯稿與本機草稿已保留。請重新檢查主機功能後再儲存。"); error.kind = "skills-support"; throw error;
      }
      const returnedReferences = Array.isArray(saved.character_references) ? normalizeScriptReferences(saved.character_references) : null;
      if (sent.character_references.length && (!returnedReferences || returnedReferences.length !== sent.character_references.length || sent.character_references.some(reference => {
        const returned = returnedReferences.find(item => item.id === reference.id);
        return !returned || ["name", "type", "image_asset_id", "image_name", "appearance"].some(field => returned[field] !== reference[field].trim());
      }))) {
        state.referencesSupportKnown = false; state.referencesSupported = false; state.dirty = true;
        const error = new Error("主機未確認完整保存角色外觀對照，編輯稿與本機草稿已保留。請重新檢查主機功能後再儲存。"); error.kind = "references-support"; throw error;
      }
      const live = readProject();
      const outcome = scriptSaveOutcome(saved, live, sent);
      delete state.ownDrafts[oldDraftKey];
      state.project = outcome.project;
      state.dirty = outcome.dirty;
      if (!outcome.dirty) writeProject(saved);
      else renderScriptLoras();
      clearConflict();
      storeDraft();
      const index = state.projects.findIndex(item => item.id === saved.id);
      if (index < 0) state.projects.unshift(saved); else state.projects[index] = saved;
      renderProjects();
      try { localStorage.setItem(SCRIPT_SAVED_PREFIX + saved.id, JSON.stringify({ revision: saved.revision, writer: clientId, savedAt: Date.now() })); } catch (_) { /* Server save still succeeded. */ }
      message(outcome.dirty ? "已儲存送出時的版本；儲存期間新增的修改仍保留在編輯稿，請再儲存一次。" : copy ? "已另存為新的劇本副本。" : "劇本已儲存至 Studio 主機。");
      if (copy) { state.generation++; state.referenceUploads.clear(); state.job = null; state.selectedJobId = null; renderPreview(); renderHistory(); }
      return !outcome.dirty;
    } catch (error) {
      storeDraft();
      if (error.status === 409) await recordConflict();
      message(error.status === 409 ? "儲存遇到版本衝突；你的修改完整保留，尚未覆寫主機內容。" : error.message, true, error.kind || "");
      return false;
    } finally { state.saving = false; updateControls(); }
  }

  async function switchProject(id, newProject = null) {
    if (state.loading || state.saving || state.referencesSaveWaiting || state.skillsSaveWaiting || state.lorasSaveWaiting || state.starting || state.pipelineStarting || state.importing) { renderProjects(); return false; }
    if (state.dirty && !window.confirm("目前編輯稿尚未儲存至 Studio 主機。切換後會保留本機草稿，要繼續嗎？")) { renderProjects(); return false; }
    storeDraft();
    const generation = ++state.generation;
    state.referenceUploads.clear();
    state.loading = true;
    updateControls();
    try {
      const project = id ? await api(`/api/scripts/projects/${encodeURIComponent(id)}`) : newProject || createNewScriptProject();
      if (generation !== state.generation) return false;
      state.dirty = false;
      clearConflict();
      if (!id) state.newDraftId = `__new__-${++state.newDraftCounter}`;
      writeProject(project);
      state.selectedJobId = null;
      state.job = null;
      state.autoVideo = false;
      state.selectedPipelineId = null; state.pipeline = null;
      message("");
      renderProjects();
      renderHistory();
      renderPreview();
      renderPipelines();
      offerDraft();
      await loadJobs();
      if (state.videoPipelinesSupported) await loadPipelines();
      return true;
    } catch (error) { renderProjects(); message(error.message, true); return false; }
    finally { if (generation === state.generation) { state.loading = false; updateControls(); } }
  }

  async function applyMonkeyExample() {
    if (!await switchProject("", createMonkeyScriptExample())) return;
    markDirty();
    message("已建立「金猴爺・叢林遺跡奇遇」範例草稿。可在「角色外觀對照」新增金猴爺、神祕女子與古代遺跡，分別上傳圖一、圖二、圖三並確認外觀描述。");
  }

  function populateSettings(settings, models = []) {
    $("scriptBaseUrl").value = settings.base_url || "http://127.0.0.1:11434";
    $("scriptTemperature").value = settings.temperature ?? 0.8;
    $("scriptContext").value = settings.num_ctx || 8192;
    $("scriptMaxOutput").value = settings.num_predict || 4096;
    for (const [id, setting, fallback] of [["scriptModel", "model", ""], ["scriptVisionModel", "vision_model", "qwen3.8:27b"]]) {
    const select = $(id);
    select.replaceChildren();
    if (!models.length) select.add(new Option("尚未找到已安裝模型", ""));
    for (const model of models) {
      const name = typeof model === "string" ? model : model.name;
      if (!name) continue;
      const size = Number(model.size);
      select.add(new Option(`${name}${size > 0 ? ` · ${(size / 1e9).toFixed(1)} GB` : ""}`, name));
    }
    const selected = settings[setting] || fallback;
    if (selected && !Array.from(select.options).some(option => option.value === selected)) select.add(new Option(`${selected}（尚未安裝）`, selected));
    if (selected) select.value = selected;
    }
  }

  function refreshEngine(options = {}) {
    if (!state.engineCheckPromise) state.engineCheckPromise = checkEngine(options).finally(() => { state.engineCheckPromise = null; });
    return state.engineCheckPromise;
  }

  async function checkEngine({ resetSettings = false } = {}) {
    $("refreshScriptEngine").disabled = true;
    try {
      const status = await api("/api/scripts/status");
      if (!status || typeof status !== "object" || typeof status.ready !== "boolean") throw new Error("主機狀態回應不完整，暫時無法確認連線與功能；請稍後重新檢查。");
      state.ready = Boolean(status.ready);
      state.visionReady = Boolean(status.vision_ready);
      state.referencesSupported = status.character_references_supported === true;
      state.referencesSupportKnown = true;
      if (state.referencesSupported && state.messageKind === "references-support") message("");
      state.skillsSupported = status.writing_skills_supported === true; state.skillsSupportKnown = true;
      state.lorasSupported = status.custom_loras_supported === true; state.lorasSupportKnown = true;
      if (state.lorasSupported && state.messageKind === "loras-support") message("");
      renderScriptLoras();
      if (!state.skillsSupported) { state.skillsError = ""; renderWritingSkills(); }
      state.videoPipelinesSupported = status.video_pipelines_supported === true;
      state.videoReady = status.video_ready === true;
      state.videoError = typeof status.video_error === "string" ? status.video_error : "";
      state.settings = status.settings || state.settings || {};
      if (!state.settingsDirty || resetSettings) { populateSettings(state.settings, status.models || []); state.settingsDirty = false; }
      $("scriptEngineDot").className = `script-dot ${state.ready ? "ready" : "unavailable"}`;
      $("scriptEngineStatus").textContent = state.ready ? `本機模型已就緒 · ${state.settings.model || "Ollama"}` : "本機模型尚未就緒";
      $("scriptEngineDetail").textContent = state.ready ? `Ollama ${state.settings.base_url || "http://127.0.0.1:11434"} · 生成在 Studio 主機執行` : status.error || "請執行 setup_scriptwriting.bat 安裝 Ollama 與模型，再重新檢查。";
      $("scriptVisionStatus").textContent = state.visionReady ? `圖片分析已就緒 · ${state.settings.vision_model || "qwen3.8:27b"}` : status.vision_error || "圖片分析模型尚未就緒；仍可手寫外觀描述並編寫劇本。";
      $("scriptVideoReadyStatus").textContent = !state.videoPipelinesSupported ? "自動影片功能尚未就緒；請重新啟動更新後的 Studio。" : state.videoReady ? "多模態影片已就緒；完成劇本後可接續製作。" : state.videoError || "影片引擎尚未就緒；先在影片工作室檢查連線與模型。";
      if (state.skillsSupported) await loadWritingSkills();
      if (state.videoPipelinesSupported) await loadPipelines();
    } catch (error) {
      state.ready = false;
      state.visionReady = false;
      state.videoReady = false;
      $("scriptEngineDot").className = "script-dot unavailable";
      $("scriptEngineStatus").textContent = "無法取得本機模型狀態";
      $("scriptEngineDetail").textContent = error.message;
      $("scriptVisionStatus").textContent = "暫時無法取得圖片分析狀態；可先手寫外觀。";
      $("scriptVideoReadyStatus").textContent = "暫時無法取得影片引擎狀態。";
      if (!state.skillsCatalogReady) state.skillsError = "暫時無法確認主機連線與創作 Skill 功能；可繼續編輯。";
    } finally { $("refreshScriptEngine").disabled = false; renderWritingSkillDetails(); renderScriptLoras(); updateControls(); }
  }

  async function saveSettings(event) {
    event.preventDefault();
    const baseUrl = $("scriptBaseUrl").value.trim().replace(/\/$/, "");
    if (!scriptLocalEndpoint(baseUrl)) { message("Ollama 網址請使用本機位址，例如 http://127.0.0.1:11434。", true); return; }
    if (!$("scriptModel").value) { message("請先安裝本機模型，再重新檢查並選擇模型。", true); return; }
    if (Number($("scriptMaxOutput").value) >= Number($("scriptContext").value)) { message("最大輸出 Token 必須小於上下文 Token。上下文也需要保留空間給故事設定與既有劇本。", true); return; }
    state.settingsSaving = true;
    $("saveScriptSettings").disabled = true;
    ["scriptBaseUrl", "scriptModel", "scriptVisionModel", "scriptTemperature", "scriptContext", "scriptMaxOutput"].forEach(id => { $(id).disabled = true; });
    updateControls();
    try {
      state.settings = await api("/api/scripts/settings", { method: "POST", body: JSON.stringify({ base_url: baseUrl, model: $("scriptModel").value, vision_model: $("scriptVisionModel").value || "qwen3.8:27b", temperature: Number($("scriptTemperature").value), num_ctx: Number($("scriptContext").value), num_predict: Number($("scriptMaxOutput").value) }) });
      state.settingsDirty = false;
      await refreshEngine({ resetSettings: true });
      message("本機模型設定已儲存。設定會套用至接下來送出的寫作工作。");
    } catch (error) { message(error.message, true); }
    finally { state.settingsSaving = false; $("saveScriptSettings").disabled = false; ["scriptBaseUrl", "scriptModel", "scriptVisionModel", "scriptTemperature", "scriptContext", "scriptMaxOutput"].forEach(id => { $(id).disabled = false; }); updateControls(); }
  }

  function renderHistory() {
    const container = $("scriptJobHistory");
    container.replaceChildren();
    state.historyDeleteControls.clear();
    const jobs = visibleHistoryJobs();
    const active = Array.from(new Map([...state.jobs, ...state.activeJobs].filter(job => scriptJobPresentation(job).active).map(job => [job.id, job])).values());
    $("scriptQueue").textContent = active.length ? `Studio 主機有 ${active.filter(job => job.status === "running").length} 筆生成中、${active.filter(job => ["queued", "pending"].includes(job.status)).length} 筆本機排隊工作。` : "本機生成佇列目前沒有待處理工作。";
    $("scriptHistoryScope").value = state.historyScope;
    $("scriptHistoryCount").textContent = state.historyScope === "all" ? `全部劇本 · 共 ${state.jobs.length} 筆` : `目前劇本 ${jobs.length} 筆 · 全部 ${state.jobs.length} 筆`;
    $("scriptHistoryStorage").textContent = state.historyStorage === "saved" ? "已永久保存在 Studio 主機；更新工具與重新啟動後仍會保留，直到你手動刪除。" : state.historyStorage === "error" ? `主機保存狀態異常：${state.historyError || "請保留目前文字並檢查主機磁碟。"}` : state.historyStorage === "unavailable" ? "暫時無法確認主機保存狀態；已保留畫面上讀取到的記錄。" : state.historyStorage === "loading" ? "正在讀取主機保存的生成記錄…" : "生成記錄已讀取；主機尚未確認永久保存功能，請重新啟動更新後的 Studio。";
    $("scriptHistoryStorage").classList.toggle("error", ["error", "unavailable"].includes(state.historyStorage));
    if (!jobs.length) { const empty = document.createElement("p"); empty.className = "script-empty"; empty.textContent = state.historyScope === "all" ? "主機目前沒有生成紀錄。" : "這份劇本尚無生成紀錄；切換「全部劇本」可查看其他劇本的歷史。"; container.append(empty); return; }
    for (const job of jobs) {
      const row = document.createElement("div"), button = document.createElement("button"), remove = document.createElement("button");
      row.className = "script-job-item"; row.dataset.jobId = job.id;
      button.type = "button";
      button.className = `script-job-row${job.id === state.selectedJobId ? " selected" : ""}`;
      button.setAttribute("aria-pressed", String(job.id === state.selectedJobId));
      const detail = document.createElement("div"), name = document.createElement("strong"), time = document.createElement("small"), status = document.createElement("span");
      name.textContent = `${SCRIPT_ACTIONS[job.action] || job.action || "寫作工作"}${job.action === "appearance" && job.reference_name ? ` · ${job.reference_name}` : ""}`;
      time.textContent = `${state.historyScope === "all" ? `${job.project_title || state.projects.find(project => project.id === job.project_id)?.title || "原劇本"} · ` : ""}${dateLabel(job.created_at)}${job.model ? ` · ${job.model}` : ""}`;
      status.textContent = scriptJobPresentation(job).label;
      detail.append(name, time); button.append(detail, status);
      button.addEventListener("click", () => selectJob(job.id));
      remove.type = "button"; remove.className = "button ghost small script-job-delete"; remove.textContent = state.deletingJobIds.has(job.id) ? "刪除中…" : "刪除"; remove.disabled = !scriptCanDeleteJob(job) || state.deletingJobIds.has(job.id) || state.loading || state.importing; remove.setAttribute("aria-label", `刪除 ${job.project_title || "劇本"} 的${SCRIPT_ACTIONS[job.action] || "生成"}紀錄`); remove.title = scriptCanDeleteJob(job) ? "刪除這筆生成紀錄" : "請先完成或取消生成，再刪除紀錄"; remove.addEventListener("click", () => deleteJob(job.id));
      state.historyDeleteControls.set(job.id, remove);
      row.append(button, remove); container.append(row);
    }
  }

  function visibleHistoryJobs() { return state.historyScope === "all" ? state.jobs : state.jobs.filter(job => job.project_id === state.project.id); }

  async function changeHistoryScope(value) {
    state.historyScope = value === "current" ? "current" : "all";
    const visible = visibleHistoryJobs();
    if (!visible.some(job => job.id === state.selectedJobId)) {
      state.selectedJobId = null; state.job = null;
      if (visible[0]) await selectJob(visible[0].id);
    }
    renderHistory(); renderPreview(); renderPipelines();
  }

  async function deleteJob(id) {
    const job = state.jobs.find(item => item.id === id) || (state.job?.id === id ? state.job : null);
    if (!scriptCanDeleteJob(job) || state.deletingJobIds.has(id) || state.loading || state.importing) return;
    if (!window.confirm(`刪除「${job.project_title || "未命名劇本"}」的${SCRIPT_ACTIONS[job.action] || "生成"}紀錄？\n這會永久刪除這一筆生成記錄。已套用的劇本與已建立的短片會保留。`)) return;
    const generation = state.generation;
    state.deletingJobIds.add(id); renderHistory(); updateControls();
    try {
      const result = await api(`/api/scripts/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (result.deleted !== true || result.id !== id) throw new Error("主機尚未確認刪除成功，記錄仍保留在畫面；請重新整理後確認。");
      state.jobsRevision++; state.deletedJobIds.add(id); state.jobs = state.jobs.filter(item => item.id !== id); state.activeJobs = state.activeJobs.filter(item => item.id !== id);
      if (state.selectedJobId === id) { state.selectedJobId = null; state.job = null; }
      renderHistory(); renderPreview();
      if (generation === state.generation) message("這筆生成紀錄已永久刪除。已套用的劇本與已建立的短片仍保留。");
      await loadJobs();
    } catch (error) { if (generation === state.generation) message(error.message, true); }
    finally { state.deletingJobIds.delete(id); renderHistory(); updateControls(); }
  }

  async function openJobProject() {
    const job = state.job;
    if (!job || !state.projects.some(project => project.id === job.project_id)) return;
    if (await switchProject(job.project_id)) await selectJob(job.id);
  }

  function selectedVideoSourceJob() {
    const job = state.job;
    return job && !state.deletingJobIds.has(job.id) && job.project_id === state.project.id && ["screenplay", "rewrite"].includes(job.action) && scriptJobPresentation(job).canApply ? job : null;
  }

  function videoPreflight(project) {
    if (!state.videoPipelinesSupported) return "目前 Studio 尚未提供自動影片功能，請重新啟動更新後的 Studio，再按「重新檢查」。";
    if (!state.ready) return "本機劇本模型尚未就緒，請先重新檢查模型。";
    if (!state.videoReady) return state.videoError || "影片引擎尚未就緒，請先在影片工作室檢查連線與模型。";
    if (state.referenceUploads.size) return "角色參考圖還在上傳，請完成後再開始製作影片。";
    if (project.custom_loras?.length && !state.lorasSupported) return "主機尚未確認支援劇本 LoRA；選擇已保留，請更新主機後重新檢查，或明確移除選擇。";
    const loraError = scriptLoraSelectionError(project.custom_loras, { video: true, catalog: state.loraCatalog }); if (loraError) return loraError;
    return scriptVideoReferencesError(project);
  }

  function upsertPipeline(pipeline) {
    const index = state.pipelines.findIndex(item => item.id === pipeline.id);
    if (index < 0) state.pipelines.unshift(pipeline); else state.pipelines[index] = pipeline;
    cacheHistoryVideos([pipeline]);
  }

  function cacheHistoryVideos(items) {
    const records = new Map(state.historyVideos.map(item => [item.id, item]));
    for (const pipeline of items) {
      const record = scriptVideoHistoryRecord(pipeline); if (!record) continue;
      const previous = records.get(record.id);
      if (previous && Date.parse(previous.completed_at) > Date.parse(record.completed_at)) continue;
      records.set(record.id, record);
    }
    state.historyVideos = Array.from(records.values()).sort((a, b) => (Date.parse(b.completed_at) || 0) - (Date.parse(a.completed_at) || 0) || b.id.localeCompare(a.id));
  }

  function receiveVideoHistory(data) {
    if (!Array.isArray(data) && !Array.isArray(data?.items)) throw new Error("主機回傳的影片紀錄格式不完整；先前影片仍保留。");
    const items = listItems(data); cacheHistoryVideos(items);
    state.videoHistoryStatus = "ready"; state.videoHistoryError = "";
    state.videoHistoryWarning = data?.has_more === true || Number(data?.total) > items.length ? "主機尚未回傳完整流程清單；已保留先前讀取的影片，可重新整理確認。" : !items.some(item => scriptVideoHistoryRecord(item)) && state.historyVideos.length ? "本次未回傳可播放的完成影片；已保留先前讀取的成品。" : "";
  }

  function historyVideoDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "完成日期未提供" : date.toLocaleString("zh-TW", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function historyVideoDetail(record) { return `完成於 ${historyVideoDate(record.completed_at)} · ${record.duration ? `目標片長 ${Number(record.duration.toFixed(3))} 秒` : "片長未提供"}`; }

  function renderVideoHistory() {
    const records = state.videoHistoryScope === "all" ? state.historyVideos : state.historyVideos.filter(item => item.project_id === state.project.id);
    $("scriptVideoHistoryScope").value = state.videoHistoryScope;
    $("scriptVideoHistoryCount").textContent = state.videoHistoryScope === "all" ? `全部劇本 · ${records.length} 部完成影片` : `目前劇本 ${records.length} 部 · 全部 ${state.historyVideos.length} 部`;
    $("refreshScriptVideoHistory").disabled = state.videoHistoryBusy;
    $("scriptVideoHistoryStatus").textContent = state.videoHistoryBusy ? "正在重新整理完成影片；目前預覽與已讀取的影片會保留。" : state.videoHistoryStatus === "error" ? `歷史影片暫時無法更新：${state.videoHistoryError}` : state.videoHistoryWarning || (state.videoHistoryStatus === "loading" ? "正在讀取已完成影片…" : "已讀取 Studio 主機的完成影片。可跨劇本預覽、下載與開啟原剪輯。");
    $("scriptVideoHistoryStatus").classList.toggle("error", state.videoHistoryStatus === "error");
    const cards = $("scriptVideoHistoryCards"); cards.replaceChildren();
    if (!records.length) { const empty = document.createElement("p"); empty.className = "script-empty"; empty.textContent = state.videoHistoryScope === "current" && state.historyVideos.length ? "這份劇本尚無完成影片；選擇「全部劇本」可查看其他作品。" : "尚無可播放的完成影片。製作完成後，成品會出現在這裡。"; cards.append(empty); }
    for (const record of records) {
      const card = document.createElement("article"), title = document.createElement("h3"), detail = document.createElement("p"), actions = document.createElement("div"), preview = document.createElement("button"), download = document.createElement("a");
      card.className = `script-video-history-card${record.id === state.selectedHistoryVideoId ? " selected" : ""}`; card.dataset.videoHistoryId = record.id;
      title.textContent = record.title; detail.className = "script-hint"; detail.textContent = historyVideoDetail(record); actions.className = "script-history-video-actions";
      preview.type = "button"; preview.className = "button secondary small"; preview.textContent = record.id === state.selectedHistoryVideoId ? "預覽中" : "預覽"; preview.setAttribute("aria-pressed", String(record.id === state.selectedHistoryVideoId)); preview.addEventListener("click", () => selectHistoryVideo(record.id));
      download.className = "button ghost small"; download.textContent = "下載"; download.href = scriptVideoHistoryDownloadUrl(record.video_url); download.setAttribute("download", ""); actions.append(preview, download);
      if (record.editor_url) { const editor = document.createElement("a"); editor.className = "button ghost small"; editor.textContent = "開啟原剪輯 ↗"; editor.href = record.editor_url; editor.target = "_blank"; editor.rel = "noopener noreferrer"; actions.append(editor); }
      card.append(title, detail, actions); cards.append(card);
    }
    const record = state.historyPreviewVideo, video = $("scriptHistoryVideo");
    $("scriptHistoryVideoPreview").classList.toggle("hidden", !record);
    if (record) {
      $("scriptHistoryVideoTitle").textContent = record.title; $("scriptHistoryVideoDetail").textContent = historyVideoDetail(record);
      if (video.dataset.historyUrl !== record.video_url) { video.pause?.(); video.src = record.video_url; video.dataset.historyUrl = record.video_url; }
      $("downloadScriptHistoryVideo").href = scriptVideoHistoryDownloadUrl(record.video_url);
      $("openScriptHistoryVideoEditor").classList.toggle("hidden", !record.editor_url); if (record.editor_url) $("openScriptHistoryVideoEditor").href = record.editor_url;
    }
    $("scriptHistoryVideoError").textContent = state.historyPreviewError; $("scriptHistoryVideoError").classList.toggle("hidden", !state.historyPreviewError);
  }

  function selectHistoryVideo(id) {
    const record = state.historyVideos.find(item => item.id === id); if (!record) return false;
    const retry = Boolean(state.historyPreviewError) && state.historyPreviewVideo?.video_url === record.video_url;
    state.selectedHistoryVideoId = id; state.historyPreviewVideo = { ...record }; state.historyPreviewError = ""; renderVideoHistory();
    if (retry) $("scriptHistoryVideo").load?.();
    $("scriptHistoryVideoPreview").scrollIntoView({ behavior: "smooth", block: "nearest" }); return true;
  }

  function changeVideoHistoryScope(value) { state.videoHistoryScope = value === "current" ? "current" : "all"; renderVideoHistory(); }

  async function refreshVideoHistory() {
    if (state.videoHistoryBusy) return;
    state.videoHistoryBusy = true; renderVideoHistory();
    try { receiveVideoHistory(await api("/api/scripts/video-pipelines")); }
    catch (error) { state.videoHistoryStatus = "error"; state.videoHistoryError = error.message; }
    finally { state.videoHistoryBusy = false; renderVideoHistory(); }
  }

  function pipelineTitle(pipeline) { return pipeline?.project_title || pipeline?.title || state.projects.find(project => project.id === pipeline?.project_id)?.title || "未命名劇本"; }

  function renderPipelines() {
    const list = $("scriptVideoPipelineHistory"); list.replaceChildren();
    const pipelines = state.historyScope === "all" ? state.pipelines : state.pipelines.filter(item => item.project_id === state.project.id);
    if (!pipelines.length) { const empty = document.createElement("p"); empty.className = "script-empty"; empty.textContent = "尚無自動影片流程。完成故事設定與圖片對照後，即可開始製作。"; list.append(empty); }
    for (const pipeline of pipelines) {
      const button = document.createElement("button"), title = document.createElement("strong"), detail = document.createElement("small");
      button.type = "button"; button.className = `script-pipeline-row${pipeline.id === state.selectedPipelineId ? " selected" : ""}`; button.dataset.pipelineId = pipeline.id; button.setAttribute("aria-pressed", String(pipeline.id === state.selectedPipelineId));
      title.textContent = pipelineTitle(pipeline); const view = scriptPipelinePresentation(pipeline); detail.textContent = `${dateLabel(pipeline.created_at)} · ${view.label} · ${view.stage}`;
      button.append(title, detail); button.addEventListener("click", () => selectPipeline(pipeline.id)); list.append(button);
    }
    const pipeline = state.pipeline, view = scriptPipelinePresentation(pipeline);
    $("scriptVideoPipelineBadge").textContent = view.label; $("scriptVideoPipelineBadge").className = `script-job-badge ${pipeline?.status || ""}`;
    $("scriptVideoPipelineStage").textContent = pipeline ? `${pipelineTitle(pipeline)} · ${view.stage}` : view.stage;
    $("scriptVideoPipelineDetail").textContent = pipeline?.error || pipeline?.waiting_reason || (pipeline ? view.active ? "由 Studio 主機接續製作；生成稿會保留，可繼續編輯原稿。" : pipeline.status === "completed" ? "影片製作完成，可播放、下載或開啟短片接續編輯。" : "目前成果與生成紀錄已保留，檢查後可接續製作。" : "劇本 → 分鏡 → 短片 → 多模態影片 → 組片；原編輯稿不會被覆寫。");
    $("scriptVideoPipelineDetail").classList.toggle("error", pipeline?.status === "failed");
    $("scriptVideoPipelineProgress").value = view.progress; $("scriptVideoPipelineProgressText").textContent = `${Math.round(view.progress)}%`;
    const steps = $("scriptVideoStageList"); steps.replaceChildren(); const ordered = ["screenplay", "storyboard", "import", "render", "assemble"], current = pipeline?.status === "completed" ? ordered.length : ordered.indexOf(pipeline?.stage);
    for (const [index, stage] of ordered.entries()) { const item = document.createElement("li"); item.textContent = SCRIPT_PIPELINE_STAGES[stage]; item.className = index < current ? "done" : index === current ? "current" : "pending"; steps.append(item); }
    $("cancelScriptVideoPipeline").classList.toggle("hidden", !view.active); $("resumeScriptVideoPipeline").classList.toggle("hidden", !["failed", "cancelled", "canceled", "interrupted"].includes(pipeline?.status));
    $("scriptVideoResumeHint").classList.toggle("hidden", !["failed", "cancelled", "canceled", "interrupted"].includes(pipeline?.status));
    $("openScriptPipelineShortfilm").classList.toggle("hidden", !pipeline?.shortfilm_id); if (pipeline?.shortfilm_id) $("openScriptPipelineShortfilm").href = `/?workspace=shortfilm&project=${encodeURIComponent(pipeline.shortfilm_id)}`;
    $("openScriptPipelineEditor").classList.toggle("hidden", !pipeline?.editor_project_id); if (pipeline?.editor_project_id) $("openScriptPipelineEditor").href = `/editor?project=${encodeURIComponent(pipeline.editor_project_id)}`;
    const url = scriptPipelineVideoUrl(pipeline), video = $("scriptPipelineVideo"); $("scriptPipelineVideoResult").classList.toggle("hidden", !url);
    if (url) { if (video.dataset.pipelineUrl !== url) { video.src = url; video.dataset.pipelineUrl = url; } $("downloadScriptPipelineVideo").href = `${url}${url.includes("?") ? "&" : "?"}download=1`; }
    else if (video.dataset.pipelineUrl) { video.pause?.(); video.removeAttribute("src"); video.dataset.pipelineUrl = ""; video.load?.(); }
    renderVideoHistory();
    updateControls();
  }

  async function selectPipeline(id) {
    const generation = state.generation, revision = state.pipelinesRevision; state.selectedPipelineId = id; state.pipeline = state.pipelines.find(item => item.id === id) || null; renderPipelines();
    try { const pipeline = await api(`/api/scripts/video-pipelines/${encodeURIComponent(id)}`); if (generation === state.generation && revision === state.pipelinesRevision && state.selectedPipelineId === id) { upsertPipeline(pipeline); state.pipeline = pipeline; renderPipelines(); } }
    catch (error) { if (generation === state.generation && state.selectedPipelineId === id) $("scriptVideoPipelineDetail").textContent = error.message; }
  }

  async function loadPipelines() {
    if (!state.videoPipelinesSupported || state.pipelinePollBusy) return;
    const generation = state.generation, revision = state.pipelinesRevision; state.pipelinePollBusy = true; let historyReceived = false;
    try {
      const data = await api("/api/scripts/video-pipelines"); if (generation !== state.generation) return;
      if (!Array.isArray(data) && !Array.isArray(data?.items)) throw new Error("主機回傳的影片流程格式不完整；先前流程仍保留。");
      receiveVideoHistory(data); historyReceived = true;
      let pipelines = listItems(data); if (revision !== state.pipelinesRevision) pipelines = [...state.pipelines, ...pipelines.filter(item => !state.pipelines.some(pipeline => pipeline.id === item.id))]; state.pipelines = pipelines;
      if (!state.selectedPipelineId) state.selectedPipelineId = pipelines.find(item => item.project_id === state.project.id)?.id || (state.historyScope === "all" ? pipelines[0]?.id : null) || null;
      const id = state.selectedPipelineId;
      if (id) { const pipeline = await api(`/api/scripts/video-pipelines/${encodeURIComponent(id)}`); if (generation === state.generation && revision === state.pipelinesRevision && state.selectedPipelineId === id) { state.pipeline = pipeline; upsertPipeline(pipeline); } }
      if (generation === state.generation) renderPipelines();
    } catch (error) { if (generation === state.generation) { $("scriptVideoPipelineDetail").textContent = `流程狀態暫時無法更新：${error.message}`; if (!historyReceived) { state.videoHistoryStatus = "error"; state.videoHistoryError = error.message; renderVideoHistory(); } } }
    finally { state.pipelinePollBusy = false; updateControls(); }
  }

  async function startVideoPipeline() {
    if (state.loading || state.saving || state.starting || state.pipelineStarting || state.settingsSaving || state.conflict) return;
    if ([...state.jobs, ...state.activeJobs].some(job => job.project_id === state.project.id && scriptJobPresentation(job).active)) { message("目前劇本還在生成中，請等待完成後再開始影片流程。", true); return; }
    if (state.pipelines.some(item => item.project_id === state.project.id && scriptPipelinePresentation(item).active)) { message("這份劇本已有製作中的影片流程，可在右側查看進度。", true); return; }
    const initial = readProject(), source = $("scriptVideoSource").value === "preview" ? selectedVideoSourceJob() : null;
    if ($("scriptVideoSource").value === "preview" && !source) { message("請選擇目前劇本已完成的完整劇本或潤飾結果，再使用預覽稿製作。", true); return; }
    if (!source && !initial.idea.trim() && !initial.content.trim()) { message("請先填寫故事想法或編輯稿，再開始自動製作。", true); return; }
    const error = videoPreflight(initial); if (error) { message(error, true); return; }
    state.pipelineStarting = true; updateControls();
    try {
      if (state.dirty || !state.project.id || writingSkillNeedsCheck()) { if (!await saveProject()) return; }
      const projectId = state.project.id, generation = state.generation;
      const pipeline = await api("/api/scripts/video-pipelines", { method: "POST", body: JSON.stringify({ project_id: projectId, ...(source ? { source_job_id: source.id } : {}) }) });
      state.pipelinesRevision++; upsertPipeline(pipeline);
      if (generation === state.generation && state.project.id === projectId) { state.selectedPipelineId = pipeline.id; state.pipeline = pipeline; renderPipelines(); message("自動影片製作已開始。劇本、分鏡與影片會接續完成，原編輯稿保留。"); }
    } catch (error) { message(error.message, true); }
    finally { state.pipelineStarting = false; updateControls(); }
  }

  async function cancelPipeline() {
    const id = state.pipeline?.id; if (!id || !state.videoPipelinesSupported || !scriptPipelinePresentation(state.pipeline).active) return;
    $("cancelScriptVideoPipeline").disabled = true;
    try { const pipeline = await api(`/api/scripts/video-pipelines/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" }); state.pipelinesRevision++; upsertPipeline(pipeline); if (state.selectedPipelineId === id) { state.pipeline = pipeline; renderPipelines(); } message("已送出取消要求，完成的劇本、分鏡與影片片段會保留。"); }
    catch (error) { message(error.message, true); updateControls(); }
  }

  async function resumePipeline() {
    const pipeline = state.pipeline; if (!pipeline || !["failed", "cancelled", "canceled", "interrupted"].includes(pipeline.status) || state.pipelineStarting || state.loading || state.saving) return;
    if (!state.videoPipelinesSupported || !state.videoReady) { message(state.videoError || "影片引擎尚未就緒，請先重新檢查。", true); return; }
    const loras = pipeline.snapshot?.custom_loras || pipeline.snapshot?.project?.custom_loras || [];
    if (loras.length && !state.lorasSupported) { message("主機尚未確認支援原流程的 LoRA 設定；原設定與已完成片段仍保留，請重新檢查後再接續。", true, "loras-support"); return; }
    const loraError = scriptLoraSelectionError(loras, { video: true, catalog: state.loraCatalog }); if (loraError) { message(loraError, true); return; }
    state.pipelineStarting = true; updateControls();
    try {
      const result = await api(`/api/scripts/video-pipelines/${encodeURIComponent(pipeline.id)}/resume`, { method: "POST", body: "{}" }); state.pipelinesRevision++; upsertPipeline(result); if (state.selectedPipelineId === pipeline.id) { state.pipeline = result; renderPipelines(); } message("已使用原設定接續影片製作，完成的片段會保留。修改圖片或劇本後，請開始新的流程。");
    } catch (error) { message(error.message, true); }
    finally { state.pipelineStarting = false; updateControls(); }
  }

  function renderPreview() {
    const job = state.job;
    const presentation = scriptJobPresentation(job);
    $("scriptJobBadge").textContent = presentation.label;
    $("scriptJobBadge").className = `script-job-badge ${job?.status || ""}`;
    $("scriptPreviewContent").textContent = job?.content || (presentation.active ? "本機模型正在準備，生成文字會逐步顯示…" : "每一個故事，都從一個想法開始。");
    $("scriptJobDetail").textContent = job ? job.error || `${SCRIPT_ACTIONS[job.action] || "寫作工作"} · ${dateLabel(job.created_at)}${job.model ? ` · ${job.model}` : ""}${job.waiting_reason ? ` · ${job.waiting_reason}` : ""}${job.truncated ? " · 已達輸出 Token 上限，內容可能未寫完。可提高輸出預算後重新生成，或接續手寫。" : presentation.active ? " · 生成中可繼續編輯，結果不會自動覆寫編輯稿。" : job.status === "completed" ? job.action === "storyboard" ? " · 分鏡可建立為新的短片專案。" : " · 結果已保存，確認後可套用到編輯稿。" : " · 已保留目前收到的文字；未完成結果不會套用。"}` : "選擇左側寫作功能，結果會逐步出現在這裡。";
    if (job?.action === "appearance") $("scriptJobDetail").textContent = job.error || `${job.reference_name || "角色"} · 外觀分析${presentation.active ? " · 可繼續編輯，結果不會自動套用。" : job.status === "completed" && !job.truncated ? " · 確認圖片與描述後，按「套用角色外觀」。" : " · 未完成結果可複製參考。"}`;
    if (job && job.project_id !== state.project.id) $("scriptJobDetail").textContent += " · 這筆記錄屬於其他劇本，可先開啟原劇本再套用。";
    const budgetNote = scriptOutputBudgetNote(job);
    if (budgetNote) $("scriptJobDetail").textContent += ` · ${budgetNote}`;
    const storyboardNote = scriptStoryboardNote(job);
    if (storyboardNote) $("scriptJobDetail").textContent += ` · ${storyboardNote}`;
    const cameraNote = scriptStoryboardCameraNote(job);
    if (cameraNote) $("scriptJobDetail").textContent += ` · ${cameraNote}`;
    $("applyScriptResult").textContent = job?.action === "appearance" ? "套用角色外觀" : "套用到編輯稿";
    $("applyScriptResult").classList.toggle("hidden", job?.action === "storyboard");
    $("openScriptJobProject").classList.toggle("hidden", !job || job.project_id === state.project.id);
    $("openScriptJobPipeline").classList.toggle("hidden", !job?.video_pipeline_id);
    $("openScriptJobPipeline").disabled = !state.videoPipelinesSupported;
    $("importScriptStoryboard").classList.toggle("hidden", job?.action !== "storyboard");
    $("cancelScriptJob").classList.toggle("hidden", !presentation.active);
    $("cancelScriptJob").disabled = job?.status === "cancelling";
    $("copyScriptResult").disabled = !String(job?.content || "").trim();
    const imported = job?.shortfilm_id || job?.imported_shortfilm_id;
    $("openImportedShortfilm").classList.toggle("hidden", !imported);
    if (imported) $("openImportedShortfilm").href = `/?workspace=shortfilm&project=${encodeURIComponent(imported)}`;
    updateControls();
  }

  async function selectJob(id) {
    if (state.deletedJobIds.has(id)) return;
    const generation = state.generation;
    state.selectedJobId = id;
    state.job = state.jobs.find(job => job.id === id) || null;
    renderHistory();
    renderPreview();
    try { const job = await api(`/api/scripts/jobs/${encodeURIComponent(id)}`); if (generation === state.generation && state.selectedJobId === id && !state.deletedJobIds.has(id)) { state.job = job; renderPreview(); } }
    catch (error) { if (state.selectedJobId === id) message(error.message, true); }
  }

  async function loadJobs() {
    if (state.pollBusy) return;
    const generation = state.generation;
    const jobsRevision = state.jobsRevision;
    state.pollBusy = true;
    try {
      const data = await api("/api/scripts/jobs");
      if (generation !== state.generation) return;
      if (!Array.isArray(data) && !Array.isArray(data?.items)) throw new Error("主機回傳的生成記錄格式不完整；已保留目前讀取到的記錄。");
      let jobs = listItems(data);
      if (jobsRevision !== state.jobsRevision) jobs = [...state.jobs.filter(job => !jobs.some(item => item.id === job.id)), ...jobs];
      state.jobs = jobs.filter(job => !state.deletedJobIds.has(job.id));
      state.activeJobs = (Array.isArray(data?.active_items) ? data.active_items : state.jobs.filter(job => scriptJobPresentation(job).active)).filter(job => !state.deletedJobIds.has(job.id));
      state.historyPersistent = data?.history_persistent === true;
      state.historyError = typeof data?.persistence_error === "string" ? data.persistence_error : "";
      state.historyStorage = state.historyError ? "error" : state.historyPersistent ? "saved" : "unknown";
      if (!state.selectedJobId) state.selectedJobId = state.jobs.find(job => job.project_id === state.project.id)?.id || (state.historyScope === "all" ? state.jobs[0]?.id : null) || null;
      const selectedId = state.selectedJobId;
      if (selectedId) {
        try {
          const detail = await api(`/api/scripts/jobs/${encodeURIComponent(selectedId)}`);
          if (generation === state.generation && selectedId === state.selectedJobId && !state.deletedJobIds.has(selectedId)) state.job = detail;
        } catch (error) { if (generation === state.generation && selectedId === state.selectedJobId && !state.deletedJobIds.has(selectedId)) throw error; }
      }
      if (generation !== state.generation) return;
      renderHistory(); renderPreview();
    } catch (error) {
      if (generation === state.generation) { state.historyStorage = "unavailable"; renderHistory(); $("scriptQueue").textContent = `工作狀態暫時無法更新：${error.message}`; }
    } finally { state.pollBusy = false; }
  }

  async function generate(action, referenceId = null) {
    if (state.loading || state.saving || state.starting || state.settingsSaving || (action === "appearance" ? !state.visionReady : !state.ready) || state.conflict) return;
    if ([...state.jobs, ...state.activeJobs].some(job => job.project_id === state.project.id && scriptJobPresentation(job).active) || (state.job?.project_id === state.project.id && scriptJobPresentation(state.job).active)) return;
    const initial = readProject();
    if (initial.custom_loras.length && !state.lorasSupported) { state.dirty = true; storeDraft(); renderScriptLoras(); message("主機尚未確認支援劇本 LoRA，所選設定與本機草稿已保留；請重新檢查主機後再生成，或明確移除選擇。", true, "loras-support"); return; }
    const loraError = scriptLoraSelectionError(initial.custom_loras, { catalog: state.loraCatalog }); if (loraError) { message(loraError, true); return; }
    if (action === "appearance") {
      const reference = initial.character_references.find(item => item.id === referenceId);
      if (!reference?.name.trim() || !reference.image_asset_id || state.referenceUploads.has(referenceId)) { message("請先填寫角色名稱並上傳參考圖片，再分析外型。", true); return; }
    } else if (!initial.idea.trim() && !initial.content.trim()) { $("scriptIdea").focus(); message("先寫下故事想法，或在編輯稿貼上既有劇本，再開始 AI 編寫。", true); return; }
    if (action === "rewrite" && !initial.content.trim()) { $("scriptContent").focus(); message("請先編寫劇本或套用完整劇本，再進行潤飾。", true); return; }
    const autoVideo = state.autoVideo && ["screenplay", "rewrite"].includes(action);
    if (autoVideo) {
      const error = videoPreflight(initial); if (error) { message(error, true); return; }
      if (state.pipelines.some(item => item.project_id === state.project.id && scriptPipelinePresentation(item).active)) { message("這份劇本已有製作中的影片流程，請等待完成或取消後再開始新的自動製作。", true); return; }
    }
    state.starting = true;
    updateControls();
    try {
      if (state.dirty || !state.project.id || writingSkillNeedsCheck()) {
        const saved = await saveProject();
        if (!saved) return;
      }
      const projectId = state.project.id;
      const generation = state.generation;
      const job = await api("/api/scripts/jobs", { method: "POST", body: JSON.stringify({ project_id: projectId, action, ...(autoVideo ? { auto_video: true } : {}), ...(action === "appearance" ? { reference_id: referenceId } : { instructions: $("scriptInstructions").value }) }) });
      state.jobsRevision++;
      state.jobs.unshift(job);
      if (generation === state.generation && state.project.id === projectId) { state.selectedJobId = job.id; state.job = job; renderHistory(); renderPreview(); message(`${SCRIPT_ACTIONS[action]}已加入本機生成佇列。你可以繼續修改${action === "appearance" ? "外觀描述" : "編輯稿"}。`); }
      if (generation === state.generation && job.video_pipeline_id) { state.pipelinesRevision++; await selectPipeline(job.video_pipeline_id); message("劇本生成後將接續製作多模態影片；原編輯稿與各階段記錄會保留。"); }
    } catch (error) { message(error.message, true); }
    finally { state.starting = false; updateControls(); }
  }

  function applyResult() {
    if (!scriptJobPresentation(state.job).canApply || state.job.project_id !== state.project.id || state.loading || state.saving) return;
    if (state.job.action === "appearance") {
      const target = scriptAppearanceApplyTarget(state.job, readProject());
      if (target.error) { message(target.error, true); return; }
      const reference = referenceById(target.reference.id);
      reference.appearance = state.job.content; markDirty(); renderReferences(); updateControls();
      state.referenceControls.get(reference.id)?.appearance.focus(); message("已套用角色外觀；可修正描述後儲存劇本，下一次編寫會使用這份外觀設定。"); return;
    }
    if ($("scriptContent").value.trim() && !window.confirm("套用會取代目前編輯稿的全文。生成紀錄仍會保留；要套用這個版本嗎？")) return;
    $("scriptContent").value = state.job.content;
    markDirty();
    $("scriptContent").focus();
    message("已套用到編輯稿。你可以接續修改，完成後按「儲存劇本」。");
  }

  async function cancelJob() {
    const id = state.job?.id;
    if (!id || !scriptJobPresentation(state.job).active) return;
    $("cancelScriptJob").disabled = true;
    try { await api(`/api/scripts/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" }); if (state.selectedJobId === id) { state.job.status = "cancelling"; renderPreview(); } await loadJobs(); message("已送出取消要求。已收到的文字與編輯稿會保留。"); }
    catch (error) { message(error.message, true); $("cancelScriptJob").disabled = false; }
  }

  async function importStoryboard() {
    if (!scriptJobPresentation(state.job).canImport || state.importing || state.job.project_id !== state.project.id) return;
    const loras = state.job.input?.project?.custom_loras || [];
    if (loras.length && !state.lorasSupported) { message("主機尚未確認支援劇本 LoRA，生成紀錄與原選擇仍保留；請更新主機後重新檢查再匯入分鏡。", true, "loras-support"); return; }
    const loraError = scriptLoraSelectionError(loras, { video: true, catalog: state.loraCatalog }); if (loraError) { message(loraError, true); return; }
    const id = state.job.id;
    state.importing = true;
    updateControls();
    try {
      const result = await api(`/api/scripts/jobs/${encodeURIComponent(id)}/shortfilm`, { method: "POST", body: "{}" });
      const shortfilmId = result.shortfilm_id || result.project_id || result.project?.id;
      if (!shortfilmId) throw new Error("主機未回傳短片專案編號，請重新整理生成紀錄後確認。");
      if (state.selectedJobId === id) { state.job.shortfilm_id = shortfilmId; renderPreview(); }
      message(result.existing ? "這份分鏡已有短片專案，可按下方連結開啟。" : "短片分鏡已建立，角色與場次可在短片創作工作區接續編輯。");
    } catch (error) { message(error.message, true); }
    finally { state.importing = false; updateControls(); }
  }

  function exportDraft() {
    const project = readProject(), format = $("scriptExportFormat").value;
    const title = project.title || "未命名劇本";
    const text = format === "json" ? JSON.stringify(project, null, 2) : format === "md" ? `# ${title}\n\n${project.content}\n` : `${title}\n\n${project.content}\n`;
    const blob = new Blob([text], { type: format === "json" ? "application/json;charset=utf-8" : "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = `${title.replace(/[<>:"/\\|?*\x00-\x1F]/g, "_").slice(0, 100) || "劇本"}.${format}`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    message(state.dirty || !state.project.id ? "已匯出目前編輯稿（包含尚未儲存的修改）。" : "已匯出目前劇本。");
  }

  async function restoreDraft() {
    const record = state.recovery;
    if (!record || state.loading || state.saving || state.starting || state.pipelineStarting) return;
    if (state.dirty && !window.confirm("目前已有未存修改。恢復草稿會取代畫面內容；目前修改會另存為本機草稿。要繼續嗎？")) return;
    if (state.dirty) {
      const previous = { project: readProject(), savedAt: Date.now(), writer: clientId };
      state.ownDrafts[`backup-${Date.now()}`] = previous;
    }
    const server = state.project;
    state.generation++; state.referenceUploads.clear();
    if (!server.id) state.newDraftId = record.writer === clientId && record.entryKey ? record.entryKey : `__new__-${++state.newDraftCounter}`;
    writeProject({ ...record.project, id: server.id, revision: server.revision, created_at: server.created_at, updated_at: server.updated_at });
    state.dirty = true;
    if (server.id && Number(record.project.revision) !== Number(server.revision)) {
      state.conflict = { id: server.id, server };
      $("scriptConflict").classList.remove("hidden");
      $("scriptConflictDetail").textContent = "已恢復的草稿來自較早的主機版本。編輯內容已保留，請檢查主機內容，再選擇另存副本或覆寫。";
    }
    $("scriptDraftRecovery").classList.add("hidden");
    state.recovery = null;
    storeDraft(); updateControls(); message("已恢復本機草稿，尚未寫入 Studio 主機。");
  }

  async function crossTabChanged(event) {
    if (event.key?.startsWith(SCRIPT_SAVED_PREFIX) && event.newValue && state.project.id && event.key === SCRIPT_SAVED_PREFIX + state.project.id) {
      try {
        const update = JSON.parse(event.newValue);
        if (update.writer === clientId || Number(update.revision) <= Number(state.project.revision)) return;
        if (state.dirty || state.saving || state.starting) { storeDraft(); await recordConflict(); }
        else { message("另一個分頁已儲存新版本。重新選擇這份專案可讀取最新內容。"); }
      } catch (_) { /* Ignore malformed storage notifications. */ }
    } else if (event.key?.startsWith(SCRIPT_DRAFT_PREFIX) && event.key !== SCRIPT_DRAFT_PREFIX + clientId && event.newValue && state.dirty) {
      try {
        const draft = Object.values(JSON.parse(event.newValue)).find(record => record?.project && (record.project.id || null) === (state.project.id || null));
        if (draft) message("另一個分頁也正在編輯這份劇本。每個分頁的本機草稿會分別保留；儲存時會檢查主機版本。");
      } catch (_) { /* Ignore unrelated drafts. */ }
    }
  }

  function bindEvents() {
    for (const id of Object.values(fieldIds)) $(id).addEventListener("input", markDirty);
    $("scriptWritingSkill").addEventListener("change", markDirty);
    $("scriptGenre").addEventListener("change", markDirty);
    $("applyScriptSkillRecommendation").addEventListener("click", applyWritingSkillRecommendation);
    $("scriptProjects").addEventListener("change", event => switchProject(event.target.value));
    $("newScriptProject").addEventListener("click", () => switchProject(""));
    $("applyMonkeyScriptExample").addEventListener("click", applyMonkeyExample);
    $("addScriptReference").addEventListener("click", addReference);
    $("addScriptLora").addEventListener("click", addScriptLora);
    $("refreshScriptLoras").addEventListener("click", loadScriptLoras);
    $("saveScriptProject").addEventListener("click", () => saveProject().catch(error => message(error.message, true)));
    $("exportScript").addEventListener("click", exportDraft);
    $("refreshScriptEngine").addEventListener("click", () => refreshEngine());
    $("openScriptSettings").addEventListener("click", () => { $("scriptSettings").open = !$("scriptSettings").open; if ($("scriptSettings").open) $("scriptSettings").scrollIntoView({ behavior: "smooth", block: "nearest" }); });
    $("scriptSettingsForm").addEventListener("submit", saveSettings);
    $("scriptSettingsForm").addEventListener("input", () => { state.settingsDirty = true; });
    document.querySelectorAll("[data-script-action]").forEach(button => button.addEventListener("click", () => generate(button.dataset.scriptAction)));
    $("refreshScriptJobs").addEventListener("click", loadJobs);
    $("scriptHistoryScope").addEventListener("change", event => changeHistoryScope(event.target.value));
    $("openScriptJobProject").addEventListener("click", openJobProject);
    $("openScriptJobPipeline").addEventListener("click", () => { if (state.job?.video_pipeline_id) selectPipeline(state.job.video_pipeline_id); });
    $("scriptAutoVideo").addEventListener("change", event => { state.autoVideo = event.target.checked === true; updateControls(); });
    $("scriptVideoSource").addEventListener("change", updateControls);
    $("startScriptVideoPipeline").addEventListener("click", startVideoPipeline);
    $("refreshScriptVideoPipelines").addEventListener("click", loadPipelines);
    $("openScriptVideoHistory").addEventListener("click", () => { $("scriptVideoHistory").scrollIntoView({ behavior: "smooth", block: "start" }); $("scriptVideoHistory").focus({ preventScroll: true }); if (state.videoHistoryStatus === "loading") refreshVideoHistory(); });
    $("refreshScriptVideoHistory").addEventListener("click", refreshVideoHistory);
    $("scriptVideoHistoryScope").addEventListener("change", event => changeVideoHistoryScope(event.target.value));
    $("scriptHistoryVideo").addEventListener("error", () => { if (state.historyPreviewVideo) { state.historyPreviewError = "這部影片暫時無法播放，檔案可能無法讀取；可稍後再按「預覽」重試或下載確認。"; renderVideoHistory(); } });
    $("cancelScriptVideoPipeline").addEventListener("click", cancelPipeline);
    $("resumeScriptVideoPipeline").addEventListener("click", resumePipeline);
    $("applyScriptResult").addEventListener("click", applyResult);
    $("cancelScriptJob").addEventListener("click", cancelJob);
    $("importScriptStoryboard").addEventListener("click", importStoryboard);
    $("copyScriptResult").addEventListener("click", async () => { try { await navigator.clipboard.writeText(state.job?.content || ""); message("已複製生成結果。"); } catch (_) { message("瀏覽器未允許剪貼簿存取。可在預覽區選取文字並複製。", true); } });
    $("restoreScriptDraft").addEventListener("click", restoreDraft);
    $("dismissScriptDraft").addEventListener("click", () => { $("scriptDraftRecovery").classList.add("hidden"); });
    $("saveScriptCopy").addEventListener("click", () => saveProject({ copy: true }).catch(error => message(error.message, true)));
    $("viewScriptServer").addEventListener("click", async () => {
      if (!state.conflict?.server) await recordConflict();
      if (state.conflict?.server) {
        const references = normalizeScriptReferences(state.conflict.server.character_references).map(item => `${item.name || "未命名角色"}（${SCRIPT_REFERENCE_TYPES[item.type]}）\n圖片：${item.image_name || "未提供"}\n外觀：${item.appearance || "未填寫"}`).join("\n\n");
        const skillId = normalizeScriptProject(state.conflict.server).writing_skill, skillName = state.writingSkills.find(skill => skill.id === skillId)?.name || skillId || "不套用／保留原設定";
        $("scriptServerVersion").textContent = `主機版本 ${state.conflict.server.revision}\n${state.conflict.server.title || "未命名劇本"}\n\n${state.conflict.server.content || "（主機編輯稿為空）"}\n\n故事想法：\n${state.conflict.server.idea || ""}\n\n創作 Skill：${skillName}\n\n角色外觀對照：\n${references || "未提供"}`;
        $("scriptServerVersion").classList.toggle("hidden");
      }
    });
    $("retryScriptSave").addEventListener("click", async () => {
      if (!state.conflict?.server || state.saving) return;
      if (!window.confirm("這會以目前編輯稿覆寫剛取得的主機版本。請確認已檢查主機內容，是否繼續？")) return;
      state.project.revision = state.conflict.server.revision;
      clearConflict();
      try { await saveProject(); } catch (error) { message(error.message, true); }
    });
    window.addEventListener("storage", crossTabChanged);
    window.addEventListener("beforeunload", event => { if (state.dirty) { storeDraft(); event.preventDefault(); event.returnValue = ""; } });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { loadJobs(); loadPipelines(); } });
    document.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (!state.saving && !state.loading && !state.starting && !state.pipelineStarting) saveProject().catch(error => message(error.message, true)); } });
  }

  async function start() {
    bindEvents(); writeProject(state.project); renderPipelines();
    await Promise.allSettled([refreshEngine(), loadScriptLoras(), loadProjects().catch(error => message(error.message, true))]);
    const requestedId = new URLSearchParams(window.location.search).get("project");
    if (requestedId && state.projects.some(project => project.id === requestedId)) await switchProject(requestedId);
    else {
      const records = allDrafts().sort((a, b) => Number(b.savedAt || 0) - Number(a.savedAt || 0));
      const latest = records[0];
      if (latest?.project.id && state.projects.some(project => project.id === latest.project.id)) await switchProject(latest.project.id);
      else { offerDraft(); await loadJobs(); }
    }
    setInterval(() => { if (!document.hidden && !state.loading) { if (!state.pollBusy) loadJobs(); if (!state.pipelinePollBusy) loadPipelines(); } }, 2200);
  }
  start().catch(error => message(error.message, true));
}
