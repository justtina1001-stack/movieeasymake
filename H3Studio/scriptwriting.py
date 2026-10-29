"""Local Ollama screenplay drafts, persisted separately from video generation."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web

from shortfilm import ASSET_TYPES, CAMERAS, SHOT_SIZES, new_asset, new_project, new_scene, new_shot, resolve_shot_asset_ids
from script_references import (
    ReferenceError, build_appearance_messages, normalize_character_references,
    prepare_reference_image, upload_reference_image, validate_reference_images,
)
from script_video import ScriptVideoError, ScriptVideoManager
from script_storyboard_cuts import StoryboardPackingError, pack_storyboard_cuts
from script_skills import SKILLS_VERSION, list_skills, normalize_skill_id, skill_prompt
from script_performance import normalize_screenplay, storyboard_to_performance
from script_video_format import ScriptVideoFormatError, normalize_video_settings
from custom_loras import normalize_loras, validate_loras_for_family, validate_lora_requirements
from video_models import VIDEO_MODEL_FAST
from h3_official_policy import official_storyboard_rules, OFFICIAL_PROMPT_VERSION


DEFAULT_SETTINGS = {
    "base_url": "http://127.0.0.1:11434", "model": "h3-script-hermes4:14b-q5_k_m",
    "vision_model": "qwen3.8:27b",
    "num_ctx": 8192, "num_predict": 4096, "temperature": 0.8,
}
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:\-]{0,199}$")
ACTIONS = {"outline", "screenplay", "rewrite", "storyboard", "appearance"}
ACTIVE = {"queued", "running"}
MAX_CONTENT = 300_000
MAX_WIRE_BYTES = 4_000_000
STORYBOARD_AUTO_CONTEXT_LIMIT = 16_384
CONTEXT_ALLOCATION_STEP = 2_048
FIELD_LIMITS = {
    "title": 120, "idea": 12000, "genre": 200, "characters": 12000,
    "style": 4000, "instructions": 12000, "content": MAX_CONTENT,
}
FIELD_LABELS = {
    "title": "標題", "idea": "故事概念", "genre": "類型", "characters": "角色設定",
    "style": "風格", "instructions": "創作要求", "content": "劇本內容",
}
DEFAULT_SCRIPT_STYLE = "精緻 3D CG 遊戲動畫風格，色彩明亮；依場景保留少量神祕冒險氛圍。"
DEFAULT_SCRIPT_INSTRUCTIONS = (
    "以繁體中文編寫可直接製作的劇本。全片不要配樂／零 BGM，只保留場景環境音、蟲鳴、樹葉摩擦、腳步聲、遺跡能量聲等適合情境的聲音，以及角色動作音效與對白。\n"
    "先列全片風格、聲音規則及角色／場景與參考圖片的固定對應，再以連續時間碼分段；起點為 0.0s，最後一段結束於目標片長，沒有空白或重疊。每段清楚列出：鏡頭／景別（焦段與機位）、地點、角色／位置與空間關係、動作／行為與表情、運鏡、VFX、聲音、對白；沒有對白請寫「無」。動作與對白須能在段落時長內完成，前後鏡頭保持角色外觀、位置、方向與因果連續，收尾交代最後反應、定格或切黑。\n"
    "若提供「角色＝圖一；角色＝圖二；場景＝圖三」等對應，全片固定使用；未提供圖片時不要虛構圖片內容。角色與故事依本次設定發展。"
    "\n詳細描寫應增加段落內的可見資訊，不增加片長。動作寫準備→移動／接觸→反應與收勢，補上視線、手勢、重心與位置變化；運鏡交代方向、速度及最後構圖。禁止用「同上」或重複結尾補長，目標片長到達後立即停止。"
    "\n完整劇本與潤飾稿使用繁體中文逐段文字，不輸出 JSON 或 Python 字典。段名採「1. 0.0–2.0s｜段名」，上述八個欄位分行；動作／表情列出 2～4 個可見變化及必要的段內時點，不能只寫「激烈戰鬥」「施法」等摘要。未指定節奏的 15 秒稿採 0–2、2–4、4–6.5、6.5–9、9–12、12–15 秒六段，指定時間優先。"
)
NO_BGM_SOUNDSCAPE = "No background music or score (zero BGM). Only scene ambience, synchronized action effects and dialogue."
TIMED_CUT = re.compile(r"\[Shot (\d+)\](?:\s+At (\d{2,}):(\d{2})\.(\d{3}))?")


class ScriptwritingError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_field(value: Any, label: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ScriptwritingError(f"{label}必須是文字，最多 {limit:,} 字。")
    value = value.strip()
    if required and not value:
        raise ScriptwritingError(f"請填寫{label}。")
    return value


def local_base_url(value: Any) -> str:
    """Allow only an explicitly local origin, without credentials or API paths."""
    if not isinstance(value, str) or len(value) > 200:
        raise ScriptwritingError("Ollama 位址格式錯誤。")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
        if (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
    except ValueError as error:
        raise ScriptwritingError("僅接受本機 Ollama 位址：http(s)://127.0.0.1、localhost 或 [::1]，不可包含帳密、路徑或查詢參數。") from error
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def is_cloud_model(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    names = [item.get("name", ""), item.get("model", "")]
    return (any(":cloud" in str(name).lower() for name in names)
            or bool(item.get("remote_host") or item.get("remote_model"))
            or any(is_cloud_model(item.get(key)) for key in ("details", "model_info")))


def normalize_settings(raw: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_SETTINGS):
        raise ScriptwritingError("寫作設定格式錯誤。")
    result = dict(previous or DEFAULT_SETTINGS)
    result.update(raw)
    result["base_url"] = local_base_url(result["base_url"])
    for key in ("model", "vision_model"):
        model = result[key]
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model) or is_cloud_model({"name": model}):
            raise ScriptwritingError("請選擇已下載的本機模型名稱，不可使用雲端模型。")
    for key, lower, upper in (("num_ctx", 2048, 131072), ("num_predict", 128, 32768)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ScriptwritingError(f"{key} 請設定為 {lower}～{upper} 的整數。")
    if result["num_predict"] >= result["num_ctx"]:
        raise ScriptwritingError("輸出字數預算 num_predict 必須小於上下文容量 num_ctx。")
    value = result["temperature"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2:
        raise ScriptwritingError("創意程度 temperature 必須介於 0～2。")
    return result


def normalize_project(raw: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ScriptwritingError("劇本專案格式錯誤。")
    now = utc_now()
    result = copy.deepcopy(previous) if previous else {
        "id": uuid.uuid4().hex, "created_at": now, "revision": 0,
        "title": "未命名劇本", "idea": "", "genre": "", "duration": 15,
        "writing_skill": "", "custom_loras": [],
        "characters": "", "character_references": [], "style": DEFAULT_SCRIPT_STYLE, "instructions": DEFAULT_SCRIPT_INSTRUCTIONS, "content": "",
    }
    try:
        result["character_references"] = normalize_character_references(raw.get("character_references", result.get("character_references", [])))
    except ReferenceError as error:
        raise ScriptwritingError(str(error)) from error
    try:
        result["writing_skill"] = normalize_skill_id(raw.get("writing_skill", result.get("writing_skill", "")))
        result["custom_loras"] = normalize_loras(raw.get("custom_loras", result.get("custom_loras", [])))
        result["video_settings"] = normalize_video_settings(raw.get("video_settings"), previous=result.get("video_settings"))
    except ValueError as error:
        raise ScriptwritingError(str(error)) from error
    for key, limit in FIELD_LIMITS.items():
        if key in raw:
            result[key] = text_field(raw[key], FIELD_LABELS[key], limit, required=key == "title")
    if "duration" in raw:
        duration = raw["duration"]
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or not 5 <= duration <= 7200):
            raise ScriptwritingError("目標片長必須介於 5～7200 秒。")
        result["duration"] = duration
    result["updated_at"] = now
    result["revision"] += 1
    return result


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # Do not silently replace a corrupt manuscript with an empty collection.
        raise ScriptwritingError(f"無法讀取寫作資料 {path.name}；請保留原檔並檢查 JSON 格式。", 500) from error


async def _bounded_body(content: aiohttp.StreamReader, limit: int) -> bytes:
    parts = []
    total = 0
    async for part in content.iter_chunked(65536):
        total += len(part)
        if total > limit:
            raise ScriptwritingError("JSON 資料過大，最多 2 MB。", 413)
        parts.append(part)
    return b"".join(parts)


def _storyboard_text(item: dict[str, Any], key: str, limit: int, *, required: bool = False) -> str:
    return text_field(item.get(key, ""), key, limit, required=required)


def _timed_action(action: str, duration: float) -> str:
    """The video compiler supplies Shot 1; subsequent cuts must fit this clip."""
    if action.startswith("[Shot 1]"):
        action = action[len("[Shot 1]"):].strip()
        if re.match(r"At\b", action):
            raise ScriptwritingError("首段從片段起點開始；[Shot 1] 不加 At 時間碼。")
    markers = list(re.finditer(r"\[Shot\b[^\]]*\]", action))
    previous = 0.0
    for number, marker in enumerate(markers, 2):
        timed = TIMED_CUT.match(action, marker.start())
        if not timed or int(timed[1]) != number or timed[2] is None:
            raise ScriptwritingError("片段內切鏡請依序使用 [Shot 2] At MM:SS.mmm、[Shot 3] 等時間碼。")
        minutes, seconds, milliseconds = map(int, timed.group(2, 3, 4))
        timestamp = minutes * 60 + seconds + milliseconds / 1000
        if seconds >= 60 or not previous < timestamp < duration:
            raise ScriptwritingError("片段內切鏡時間碼必須逐次增加，且嚴格位於該片段時長內。")
        previous = timestamp
    return action


def validate_script_timeline(content: str, target_duration: float, *, require_timeline: bool = False) -> None:
    """Check explicit timeline headings without interpreting lens/angle ranges as time.

    Older or explicitly requested prose formats without timed headings stay editable.
    Generated performance scripts require headings; saved or historical prose stays editable.
    Once a timeline is present, it must cover exactly the requested duration.
    """
    heading = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:\d+[.)、](?!\d)\s*|[-*+]\s*)?"
        r"(?:(?:時間碼|时间码|時間|时间)\s*[:：]\s*)?"
        r"(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[-–—~～至]\s*"
        r"(\d+(?:\.\d+)?)\s*(?:s|秒)(?=\W|$)", re.IGNORECASE,
    )
    ranges = []
    for line in content.splitlines():
        match = heading.match(line.replace("**", "").replace("__", ""))
        if match:
            ranges.append(tuple(map(float, match.groups())))
    structured = bool(re.match(r"^(?:\{|\[|```(?:json|python)\b)", content.lstrip(), re.IGNORECASE))
    if require_timeline and (not ranges or structured):
        raise ScriptwritingError(
            "生成稿缺少逐段時間碼表演腳本，不能以 JSON、字典或故事摘要當作完成。"
            "原生成內容已保留；請依「1. 0.0–2.0s｜段名」及鏡頭、位置、動作、運鏡、VFX、聲音、對白格式重新編寫。"
        )
    previous = 0.0
    for start, end in ranges:
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ScriptwritingError("劇本時間碼的結束必須大於起點；生成稿已保留，請修正後重試。")
        if not math.isclose(start, previous, rel_tol=0, abs_tol=0.001):
            raise ScriptwritingError("劇本時間碼必須從 0 秒開始且連續，不能有空白、重疊或重複段落；生成稿已保留。")
        if end > target_duration + 0.001:
            raise ScriptwritingError(f"劇本時間碼超過目標 {target_duration:g} 秒（寫到 {end:g} 秒）；生成稿已保留，請刪除多餘段落後重寫。")
        previous = end
    if ranges and not math.isclose(previous, target_duration, rel_tol=0, abs_tol=0.001):
        raise ScriptwritingError(f"劇本時間碼只到 {previous:g} 秒，未完整覆蓋目標 {target_duration:g} 秒；生成稿已保留。")


def validate_storyboard(raw: Any, *, target_duration: float | None = None,
                        _allow_short_cuts: bool = False) -> dict[str, Any]:
    """Validate every shot before allowing an import into the video workspace."""
    if not isinstance(raw, dict):
        raise ScriptwritingError("分鏡結果必須是 JSON 物件。")
    result = {
        "title": _storyboard_text(raw, "title", 80, required=True),
        "synopsis": _storyboard_text(raw, "synopsis", 5000),
        "characters": [], "scenes": [],
    }
    characters = raw.get("characters", [])
    scenes = raw.get("scenes")
    if not isinstance(characters, list) or len(characters) > 64:
        raise ScriptwritingError("分鏡角色清單格式錯誤，最多 64 位。")
    aliases = set()
    speakers = set()
    for character in characters:
        if not isinstance(character, dict):
            raise ScriptwritingError("分鏡角色格式錯誤。")
        name = _storyboard_text(character, "name", 60, required=True)
        if name.casefold() in aliases:
            raise ScriptwritingError(f"分鏡角色名稱「{name}」重複。")
        aliases.add(name.casefold())
        entry = {"name": name, "description": _storyboard_text(character, "description", 1200)}
        asset_type = character.get("type", "character")
        if not isinstance(asset_type, str) or asset_type not in ASSET_TYPES:
            raise ScriptwritingError("分鏡素材 type 必須使用支援的素材類型。")
        if "type" in character:
            entry["type"] = asset_type
        if "reference" in character:
            entry["reference"] = _storyboard_text(character, "reference", 60)
            if entry["reference"] and len(entry["description"]) + len(f"參考圖對應：{entry['reference']}。 ") > 1200:
                raise ScriptwritingError("素材描述與參考圖標籤合計過長，請縮短以免匯入時截斷。")
        if asset_type in {"character", "creature"}:
            speakers.add(name.casefold())
        result["characters"].append(entry)
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 100:
        raise ScriptwritingError("分鏡必須包含 1～100 場場景。")
    shot_count = 0
    total_duration = 0.0
    for index, item in enumerate(scenes, 1):
        if not isinstance(item, dict):
            raise ScriptwritingError("分鏡場景格式錯誤。")
        scene = {key: _storyboard_text(item, key, limit) for key, limit in (
            ("title", 80), ("location", 200), ("time_of_day", 120),
        )}
        scene["title"] = scene["title"] or f"場次 {index}"
        if "description" in item:
            scene["description"] = _storyboard_text(item, "description", 1800)
        shots = item.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ScriptwritingError("每個場景至少需要一個鏡頭。")
        scene["shots"] = []
        for item_shot in shots:
            shot_count += 1
            if shot_count > 500 or not isinstance(item_shot, dict):
                raise ScriptwritingError("分鏡鏡頭格式錯誤，總數最多 500 個。")
            duration = item_shot.get("duration")
            if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not math.isfinite(duration) or duration > 15
                    or (duration <= 0 if _allow_short_cuts else duration < 5)):
                raise ScriptwritingError("分鏡切鏡時長必須是大於 0 且不超過 15 秒的數字。" if _allow_short_cuts
                                         else "H3 分鏡的每個鏡頭必須介於 5～15 秒。")
            shot = {key: _storyboard_text(item_shot, key, limit, required=key == "action") for key, limit in (
                ("title", 80), ("action", 5000), ("dialogue", 1800),
                ("speaker", 60), ("sound", 1200 - len(NO_BGM_SOUNDSCAPE) - 1),
            )}
            shot["title"] = shot["title"] or f"鏡頭 {shot_count}"
            shot["duration"] = duration
            shot["action"] = _timed_action(shot["action"], duration)
            if not shot["action"]:
                raise ScriptwritingError("鏡頭 action 必須包含可見動作。")
            for key, limit in (("camera_detail", 800), ("ending", 1200)):
                if key in item_shot:
                    shot[key] = _storyboard_text(item_shot, key, limit)
            total_duration += duration
            shot["camera"] = item_shot.get("camera", "static")
            shot["shot_size"] = item_shot.get("shot_size", "medium")
            if not isinstance(shot["camera"], str) or shot["camera"] not in CAMERAS:
                raise ScriptwritingError("分鏡 camera 必須使用支援的鏡頭運動代號。")
            if not isinstance(shot["shot_size"], str) or shot["shot_size"] not in SHOT_SIZES:
                raise ScriptwritingError("分鏡 shot_size 必須使用支援的景別代號。")
            if shot["speaker"] and shot["speaker"].casefold() not in speakers:
                raise ScriptwritingError(f"對白說話者「{shot['speaker']}」必須列為角色或動物素材。")
            if shot["dialogue"] and not shot["speaker"]:
                raise ScriptwritingError("有對白的鏡頭必須指定 speaker。")
            if (shot["dialogue"] or shot["speaker"]) and "<d>" in shot["action"]:
                raise ScriptwritingError("action 已含對白標籤時，dialogue 與 speaker 請留空，避免台詞重複。")
            scene["shots"].append(shot)
        result["scenes"].append(scene)
    if total_duration > 3600:
        raise ScriptwritingError("目前短片工作區最多支援 3600 秒；請將分鏡拆成多個專案，避免匯入時縮短目標片長。")
    if target_duration is not None and not math.isclose(total_duration, target_duration, rel_tol=0, abs_tol=0.001):
        raise ScriptwritingError(f"分鏡總長 {total_duration:g} 秒與目標 {target_duration:g} 秒不符；生成稿已保留，請重新分配片段時長後重試。")
    return result


def _normalize_storyboard_fields(raw: Any) -> tuple[Any, dict[str, Any]]:
    """Correct model field placement without changing the strict public schema."""
    result = copy.deepcopy(raw)
    cameras, silent_pairs = [], []
    if not isinstance(result, dict) or not isinstance(result.get("scenes"), list):
        return result, {}
    characters = result.get("characters", [])
    named_speakers = {
        item["name"].strip().casefold() for item in characters
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and item.get("type", "character") in ("character", "creature")
    } if isinstance(characters, list) else set()
    no_dialogue = {"", "無", "无", "無對白", "无对白", "none", "n/a"}
    for scene_index, scene in enumerate(result["scenes"]):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            continue  # The complete schema validator reports malformed containers.
        for shot_index, shot in enumerate(scene["shots"]):
            if not isinstance(shot, dict):
                continue
            camera = shot.get("camera", "static")
            if not isinstance(camera, str) or not camera.strip() or "\x00" in camera:
                raise ScriptwritingError("分鏡 camera 必須是支援代號或非空的鏡頭描述文字。")
            if camera not in CAMERAS:
                detail = shot.get("camera_detail", "")
                if not isinstance(detail, str) or "\x00" in detail:
                    raise ScriptwritingError("分鏡 camera_detail 必須是文字。")
                combined = camera + ("\n" + detail if detail else "")
                # Parentheses protect original edge whitespace from text_field.strip().
                if combined != combined.strip():
                    combined = "(" + combined + ")"
                if len(combined) > 800:
                    raise ScriptwritingError("原始鏡頭描述與 camera_detail 合計超過 800 字；原稿已保留，不會截斷鏡頭資訊。")
                shot.update(camera="custom", camera_detail=combined)
                cameras.append({"scene_index": scene_index, "shot_index": shot_index,
                                "original_camera": camera, "original_camera_detail": detail,
                                "camera": "custom", "camera_detail": combined})
            dialogue, speaker = shot.get("dialogue", ""), shot.get("speaker", "")
            # Validate before clearing placeholders: padded or invalid values must
            # retain the public schema's type, NUL and length failures.
            text_field(dialogue, "dialogue", 1800)
            text_field(speaker, "speaker", 60)
            if (isinstance(dialogue, str) and isinstance(speaker, str)
                    and dialogue.strip().casefold() in no_dialogue
                    and speaker.strip().casefold() in no_dialogue
                    and speaker.strip().casefold() not in named_speakers
                    and (dialogue.strip() or speaker.strip())):
                shot.update(dialogue="", speaker="")
                silent_pairs.append({"scene_index": scene_index, "shot_index": shot_index,
                                     "original_dialogue": dialogue, "original_speaker": speaker,
                                     "dialogue": "", "speaker": ""})
    metadata = {}
    for key, corrections in (("storyboard_camera_normalization", cameras),
                             ("storyboard_dialogue_normalization", silent_pairs)):
        if corrections:
            metadata[key] = {"correction_count": len(corrections), "corrections": corrections}
    return result, metadata


def parse_storyboard(content: str, *, target_duration: float | None = None) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", candidate, re.IGNORECASE)
        if match:
            candidate = match.group(1)
    try:
        raw = json.loads(candidate)
    except (ValueError, TypeError) as error:
        raise ScriptwritingError("分鏡回應不是完整 JSON；請增加輸出預算或縮短故事後重試。") from error
    normalized, metadata = _normalize_storyboard_fields(raw)
    try:
        storyboard = pack_storyboard_cuts(normalized, target_duration=target_duration, validator=validate_storyboard)
    except StoryboardPackingError as error:
        raise ScriptwritingError(str(error)) from error
    storyboard.update(metadata)
    return storyboard


def readable_job(job: dict[str, Any]) -> dict[str, Any]:
    """Derive an editable-reading view without rewriting saved history or engine data."""
    result = copy.deepcopy(job)
    if job.get("status") != "completed":
        return result
    action = job.get("action")
    job_input = job.get("input")
    project = job_input.get("project") if isinstance(job_input, dict) else None
    project = project if isinstance(project, dict) else {}
    duration = project.get("duration")

    def valid_script(content: Any) -> str:
        if not isinstance(content, str) or not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return ""
        readable = normalize_screenplay(content)
        try:
            validate_script_timeline(readable, duration, require_timeline=True)
        except ScriptwritingError:
            return ""
        return readable

    if action in {"screenplay", "rewrite"}:
        readable = valid_script(job.get("content"))
        if readable:
            result.update(readable_content=readable, readable_content_source="screenplay",
                          readable_content_note="原表演腳本；時間碼、動作、聲音與台詞可逐段檢查。")
        else:
            result["readable_content_note"] = "這筆舊紀錄沒有完整的時間碼表演稿；保留原文供查看，新編寫會使用逐段表演腳本格式。"
    elif action == "storyboard":
        readable = valid_script(project.get("content"))
        if readable:
            result.update(readable_content=readable, readable_content_source="screenplay",
                          readable_content_note="這是產生分鏡時使用的原表演腳本，保留原時間分配；引擎分鏡可切換至原始資料查看。")
        else:
            readable = storyboard_to_performance(job.get("storyboard"))
            if readable:
                result.update(readable_content=readable, readable_content_source="storyboard",
                              readable_content_note="依原分鏡的切鏡時間顯示；原始敘述與台詞保留，未提供的表演細節不會補寫。")
    return result


def build_messages(project: dict[str, Any], action: str, instructions: str) -> list[dict[str, str]]:
    dialogue_budget = math.floor(project["duration"] * 2.5)
    duration = f"{project['duration']:g}"
    timed_heading = "時間碼段落標題用「1. 0.0–2.0s｜段名」格式，依序編號。" if action in {"screenplay", "rewrite"} else ""
    text_timeline = (
        "未指定時間分配且全長15秒時，採0–2、2–4、4–6.5、6.5–9、9–12、12–15s六段；已指定則保留其合法時間分配。"
        if action in {"screenplay", "rewrite"} else ""
    )
    prose_layout = (
        "預設採使用者範例的分鏡劇本方式：先列風格、聲音、固定參考圖對應，"
        "再以0秒至目標片長的連續時間碼逐段列出焦段/景別/機位、地點、人物位置/距離/方向、"
        "動作/表情、運鏡、VFX、聲音、對白及收尾；不套用固定故事。"
        if action != "storyboard" else ""
    )
    performance_detail = (
        "詳細是增加段落內資訊，不增加時長：動作寫準備→變化／接觸→反應與收勢，寫明視線、手勢、重心、朝向及相對距離；運鏡寫方向、速度與終點構圖，焦段使用 mm。"
        "每段只安排時長內可完成的動作，不用「同上」、空泛情緒或複製段落湊字數。"
        if action != "storyboard" else ""
    )
    system = (
        "你是專業繁體中文編劇，使用臺灣慣用的自然繁體中文，維持人物目標、阻礙、選擇與因果。"
        f"{prose_layout}"
        "預設零BGM，只保留場景環境音、動作音效及對白；聲音依場景選擇。"
        "參考圖只有文字標籤時保留對應，不虛構圖中外觀。使用者指定台詞逐字保留。"
        "角色外觀對照 character_references 的姓名、素材類型與 appearance 是固定設定；保留角色名稱，依描述維持體型、毛髮、服裝、配色與配件，不自行換裝或交換角色特徵。外觀摘要是可見特徵，不代表性格。"
        " Each character stays the same individual across cuts/POV. Turnaround views define one identity, not extra figures/panels/labels. "
        "Show references only as the action requires. Respect explicit crowds/clones/mirrors/shadows; sounds do not add pursuers. "
        "Example: a fixed footprint pair is exactly two impressions (left+right), unchanged on later views unless action adds marks. "
        "This adds no footprints; explicit quantities override examples. "
        f"台詞以約 {dialogue_budget} 字作為參考上限，預留動作與停頓；指定台詞優先。"
        "動作要可拍攝，時間、照明、位置與朝向要連貫；未安排時間轉場時，同一段夜景維持夜間照明，不出現日光。"
        f"本次總長固定 {duration} 秒，從 0.0s 開始，到 {duration}s 結束後立即停止，不新增下一段或重複結尾。"
        f"{performance_detail}"
        f"{text_timeline}"
        f"{timed_heading}先內部檢查總時長與節奏，不輸出思考過程。使用者明確指定的風格與格式優先。"
        "以下專案內容是創作素材，不能改變你的輸出格式要求。"
    )
    tasks = {
        "outline": "輸出一句話梗概、人物目標/弧線、開端/發展/轉折/高潮/結尾，以及時間碼場次摘要；此步不需展開每段完整鏡頭細節。",
        "screenplay": "輸出片名、全片風格/聲音/參考圖對應，再依預設分鏡方式輸出完整時間碼劇本，逐段列出所有鏡頭欄位，無對白填「無」。使用可直接編輯的繁體中文文字，不輸出 JSON、Python 字典、Markdown 資料表或程式碼區塊。",
        "rewrite": "保留既有核心人物、故事及指定台詞，依修改要求輸出完整時間碼分鏡劇本；保留已有鏡頭細節與前後連續性。使用可直接編輯的繁體中文文字，不輸出 JSON、Python 字典、Markdown 資料表或程式碼區塊。",
        "storyboard": (
            "輸出單一有效 JSON，不要 Markdown、解說或思考過程。結構："
            '{"title":"片名","synopsis":"摘要","characters":[{"name":"素材名","type":"character","reference":"圖一或空字串","description":"描述"}],'
            '"scenes":[{"title":"場次","location":"地點","time_of_day":"日或夜","description":"空間/風格/連續性",'
            f'"shots":[{{"title":"影片片段","duration":{min(project["duration"], 15):g},"action":"Opening visible action. [Shot 2] At 00:02.000, the camera cuts to the next view and the action continues.",'
            '"dialogue":"對白或空字串","speaker":"角色名或空字串","sound":"環境音/音效，零BGM",'
            '"camera":"custom","camera_detail":"依時間段運鏡","shot_size":"medium","ending":"最後反應/定格/切黑"}]}]}。'
            f"所有shots的duration加總必須恰好{duration}秒，不是每段都{duration}秒。"
            "每個shots項目是5～15秒生成片段。短於5秒的切鏡保留在action內，不延長；15秒內完整故事優先使用單一片段，duration等於目標片長。"
            "文字劇本的2秒或2.5秒段落是片段內的切鏡，不能逐段複製成shots陣列；同一場景的15秒故事應是一個duration=15的項目，原切鏡時間及細節都放在其action。"
            "上面的動作只是格式佔位示例；使用本次故事，切點依原劇本，不套用示例動作或時間分配。"
            "action首段直接寫內容，不寫[Shot 1]；後續依序使用[Shot 2] At 00:02.000等局部時間碼，嚴格遞增且小於片段duration。"
            "每段保留焦段、機位、位置/距離、表演、運鏡、VFX、聲音及收尾。敘述用英文，素材名稱保留原名，中文台詞不翻譯。"
            " Keep original asset names in English action. Check visible cast/count/positions/start/end state per cut. "
            "Prefer one event/reaction and few cuts unless prescribed. Show counted objects clearly; preserve events/times/dialogue without replay. "
            "多角色台詞放action：角色名(S1) says <d>[Chinese] 原台詞</d>；依首次實際發聲順序配置S1、S2並跨鏡保持；不發聲的角色與背景不佔編號；dialogue和speaker留空，避免重複。"
            "文字劇本的「對白：無」代表無對白；轉成JSON時dialogue與speaker都填空字串，不把「無」當作台詞或角色；實際指定台詞仍逐字保留。"
            "單句台詞也可用dialogue及speaker，speaker須為角色/動物。type限character/creature/object/background/style/motion/effect，遺跡為background。"
            "參考圖標籤放reference，無圖片不捏造外觀。action最多5000字、camera_detail800、ending1200、場景description1800。"
            f"camera 只填支援代號 {', '.join(CAMERAS)}；焦段、機位和運鏡描述放 camera_detail，不放 camera；shot_size 使用 {', '.join(SHOT_SIZES)}。"
            "優先依既有劇本製作分鏡；沒有既有劇本時依故事概念創作。"
            "既有劇本是表演稿：逐段保留原時碼、準備、接觸、反應、停頓、收勢、視線、VFX及台詞，不摘要或均分成5秒；只翻譯可見敘述，不新增動作、改變人物位置或捏造未提供的圖三等標籤。"
        ),
    }
    body = {key: project[key] for key in ("title", "idea", "genre", "duration", "characters", "style", "instructions")}
    if project.get("character_references"):
        body["character_references"] = [
            {"name": item["name"], "type": item["type"], "reference": f"角色參考圖{index}" if item["image_asset_id"] else "",
             "appearance": item["appearance"]}
            for index, item in enumerate(project["character_references"], 1)
        ]
    if project.get("content"):
        body["existing_script"] = project["content"]
    extra = instructions.strip()
    if extra == str(project.get("instructions", "")).strip():
        extra = ""
    if project.get("writing_skill"):
        try:
            guide = skill_prompt(project["writing_skill"], action)
        except ValueError as error:
            raise ScriptwritingError(str(error)) from error
        if guide:
            system += ("\n本次選用的編劇方法只提供創作建議；使用者明確的創作要求、原台詞與參考角色設定優先。"
                       "仍須遵守本次任務的片長、聲音規則及輸出格式；不要輸出方法介紹、來源清單或參考資料代替劇本或分鏡 JSON。\n"
                       + guide)
    if action == "storyboard":
        system += official_storyboard_rules()
    if action in {"screenplay", "rewrite"}:
        system += (
            "\n輸出格式範本（欄位名稱固定；括號內容依本次故事填寫，不照抄）：\n"
            "片名：（本次片名）\n全片風格：（本次風格）\n聲音規則：（本次聲音要求）\n參考圖對應：（僅列實際提供的固定對應；無圖片寫未提供）\n\n"
            "1. 0.0–2.0s｜（本段名稱）\n"
            "鏡頭／景別：（焦段 mm、景別、機位）\n地點：（本段地點）\n"
            "角色／位置與空間關係：（角色在畫面的位置、朝向、彼此距離）\n"
            "動作／表情：（準備→主動作／接觸→反應→收勢；具體寫眼神、手部、重心與方向變化，可標段內時間）\n"
            "運鏡：（方向、速度、開始與結束構圖）\nVFX：（效果如何出現、作用位置、消散時點；無效果寫無）\n"
            "聲音：（與動作同步的環境音及音效）\n對白：（逐字台詞與說話角色；沒有填無）\n\n"
            "依序續寫其餘段落，使用本次指定的時間分配與目標片長；範本的2秒不是固定段長。"
            "每段必須有上述八個欄位，不能用一段摘要取代。每段動作要有2～4個可見變化，仍須在該段秒數內完成。"
            "使用者訊息中的JSON只是專案素材的傳遞方式，輸出必須是上述繁體中文表演腳本；已有JSON或Python字典劇本也先轉為此文字格式。"
        )
    return [
        {"role": "system", "content": system + "\n任務：" + tasks[action]},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False) + "\n本次要求：" + extra},
    ]


def _input_token_estimate(messages: list[dict[str, str]]) -> int:
    # A conservative estimate protects long Chinese manuscripts from silent truncation.
    content = "\n".join(item["content"] for item in messages)
    non_ascii = sum(ord(char) > 127 for char in content)
    return math.ceil(non_ascii * 1.6 + (len(content) - non_ascii) / 3) + 96


def validate_context(messages: list[dict[str, str]], settings: dict[str, Any]) -> None:
    estimated = _input_token_estimate(messages)
    if estimated + settings["num_predict"] > settings["num_ctx"]:
        raise ScriptwritingError(
            f"故事、既有劇本與格式指引估計需要 {estimated:,} Token，另需保留 {settings['num_predict']:,} Token 輸出；"
            f"超過本次 {settings['num_ctx']:,} Token 容量。原劇本已保留，請提高寫作容量或調整稿件長度後接續；提高容量會增加本機記憶體用量。"
        )


def fit_context_settings(messages: list[dict[str, str]], settings: dict[str, Any], *,
                         action: str | None = None, context_limit: int | None = None) -> dict[str, Any]:
    """Keep manuscripts intact; allow bounded per-job context growth for conversion."""
    effective = copy.deepcopy(settings)
    estimated = _input_token_estimate(messages)
    ceiling = STORYBOARD_AUTO_CONTEXT_LIMIT
    if context_limit is not None:
        ceiling = min(ceiling, context_limit)
        effective["num_ctx"] = min(effective["num_ctx"], context_limit)
    if action == "storyboard" and effective["num_ctx"] < ceiling:
        needed = estimated + effective["num_predict"]
        if needed > effective["num_ctx"]:
            allocation = math.ceil(needed / CONTEXT_ALLOCATION_STEP) * CONTEXT_ALLOCATION_STEP
            effective["num_ctx"] = min(ceiling, allocation)
    available = effective["num_ctx"] - estimated
    if available < min(settings["num_predict"], 1024):
        validate_context(messages, effective)
    effective["num_predict"] = min(settings["num_predict"], available)
    validate_context(messages, effective)
    return effective


def _model_context_limit(shown: dict[str, Any]) -> int | None:
    info = shown.get("model_info")
    if not isinstance(info, dict):
        return None
    architecture = info.get("general.architecture")
    keys = [f"{architecture}.context_length"] if isinstance(architecture, str) else []
    keys.extend(key for key in info if isinstance(key, str) and key.endswith(".context_length") and key not in keys)
    for key in keys:
        value = info.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


class ScriptwritingManager:
    def __init__(self, app: web.Application, data_dir: Path):
        self.app = app
        self.directory = Path(data_dir) / "scriptwriting"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.directory / "settings.json"
        self.projects_path = self.directory / "projects.json"
        self.jobs_path = self.directory / "jobs.json"
        self.settings = normalize_settings(_load_json(self.settings_path, DEFAULT_SETTINGS))
        self.projects = self._load_records(self.projects_path)
        self.history_directory = self.directory / "history"
        self.history_directory.mkdir(exist_ok=True)
        self.history_persistence_error: str | None = None
        self._jobs_index_error: ScriptwritingError | None = None
        self.jobs = self._load_jobs()
        self.tasks: dict[str, asyncio.Task] = {}
        self.responses: dict[str, aiohttp.ClientResponse] = {}
        self.queue_lock = asyncio.Lock()
        self.gpu_lock = getattr(app.get("jobs"), "gpu_lock", self.queue_lock)
        self.closed = False
        interrupted = False
        for job in self.jobs.values():
            if job.get("status") in ACTIVE:
                job.update(status="interrupted", error="程式已重新啟動；先前寫作任務已中斷，保留已生成內容。", updated_at=utc_now())
                interrupted = True
        # Migrate legacy aggregate records without trimming history or its input snapshots.
        if interrupted or self.jobs or self._jobs_index_error:
            self._save_jobs()

    @staticmethod
    def _load_records(path: Path) -> dict[str, dict[str, Any]]:
        raw = _load_json(path, [])
        if not isinstance(raw, list) or any(not isinstance(item, dict) or not SAFE_ID.fullmatch(str(item.get("id", ""))) for item in raw):
            raise ScriptwritingError(f"寫作資料 {path.name} 格式錯誤；請保留原檔並檢查。", 500)
        return {item["id"]: item for item in raw}

    @staticmethod
    def _job_timestamp(job: dict[str, Any]) -> float:
        for key in ("updated_at", "created_at"):
            try:
                return datetime.fromisoformat(job.get(key, "")).timestamp()
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        return 0.0

    @contextmanager
    def _history_lock(self):
        # Serialize read/check/replace across Studio processes, including tombstones.
        with (self.history_directory / ".history.lock").open("a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _history_records(self) -> dict[str, dict[str, Any]]:
        records = {}
        for path in self.history_directory.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                value = _load_json(path, None)
                if (not isinstance(value, dict) or value.get("id") != path.stem
                        or ("deleted" in value and not isinstance(value["deleted"], bool))):
                    raise ScriptwritingError(f"生成記錄 {path.name} 格式錯誤；請保留原檔並檢查。", 500)
            except ScriptwritingError:
                # A healthy compatibility index can recover an individual damaged file.
                fallback = self._load_records(self.jobs_path).get(path.stem)
                if fallback is None:
                    raise
                preserved = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex}.bak")
                preserved.write_bytes(path.read_bytes())
                _atomic_json(path, fallback)
                value = fallback
            records[path.stem] = value
        return records

    def _load_jobs(self) -> dict[str, dict[str, Any]]:
        with self._history_lock():
            return self._load_jobs_locked()

    def _load_jobs_locked(self) -> dict[str, dict[str, Any]]:
        try:
            records = self._load_records(self.jobs_path)
        except ScriptwritingError as error:
            self._jobs_index_error = error
            records = {}
        history = self._history_records()
        if self._jobs_index_error and not history:
            raise self._jobs_index_error
        for job_id, value in history.items():
            if value.get("deleted"):
                records.pop(job_id, None)
            elif job_id not in records or self._job_timestamp(value) >= self._job_timestamp(records[job_id]):
                records[job_id] = value
        return records

    def _write_jobs_index(self) -> None:
        # Individual files are authoritative. This compatibility index may be rebuilt.
        if self._jobs_index_error and self.jobs_path.exists():
            preserved = self.jobs_path.with_name(f"jobs.json.corrupt-{uuid.uuid4().hex}.bak")
            preserved.write_bytes(self.jobs_path.read_bytes())
            self._jobs_index_error = None
        records = self._history_records()
        _atomic_json(self.jobs_path, [value for value in records.values() if not value.get("deleted")])

    def _save_jobs(self, job_id: str | None = None) -> None:
        try:
            with self._history_lock():
                self._save_jobs_locked(job_id)
        except (OSError, ScriptwritingError):
            self.history_persistence_error = "生成記錄未能寫入本機，請立即複製結果並檢查磁碟空間與權限。"
            raise

    def _save_jobs_locked(self, job_id: str | None = None) -> None:
        records = [self.jobs[job_id]] if job_id is not None else list(self.jobs.values())
        try:
            for job in records:
                path = self.history_directory / f"{job['id']}.json"
                try:
                    saved = _load_json(path, None)
                except ScriptwritingError:
                    saved = self._history_records().get(job["id"])
                if isinstance(saved, dict) and saved.get("deleted"):
                    # A stale Studio instance must not recreate explicitly deleted results.
                    if job_id is not None:
                        raise OSError("生成記錄已在其他 Studio 工作階段刪除。")
                    continue
                if isinstance(saved, dict) and self._job_timestamp(saved) > self._job_timestamp(job):
                    continue
                if saved != job:
                    _atomic_json(path, job)
        except (OSError, ScriptwritingError):
            self.history_persistence_error = "生成記錄未能寫入本機，請立即複製結果並檢查磁碟空間與權限。"
            raise
        try:
            self._write_jobs_index()
            self.history_persistence_error = None
        except (OSError, ScriptwritingError):
            # The individual result is already durable; an index error must not lose it.
            self.history_persistence_error = "生成記錄已逐筆保存，但清單索引未能更新；重新啟動後可從記錄檔恢復。"

    def _save_projects(self) -> None:
        _atomic_json(self.projects_path, list(self.projects.values()))

    def get_project(self, project_id: str) -> dict[str, Any]:
        if not isinstance(project_id, str) or project_id not in self.projects:
            raise ScriptwritingError("找不到劇本專案。", 404)
        value = copy.deepcopy(self.projects[project_id])
        value.setdefault("character_references", [])
        value.setdefault("custom_loras", [])
        return value

    def _validate_reference_assets(self, references: list[dict[str, Any]]) -> None:
        try:
            validate_reference_images(references, self.app.get("assets"))
        except ReferenceError as error:
            raise ScriptwritingError(str(error)) from error

    def create_project(self, raw: Any) -> dict[str, Any]:
        project = normalize_project(raw)
        self._validate_reference_assets(project["character_references"])
        self.projects[project["id"]] = project
        try:
            self._save_projects()
        except OSError:
            self.projects.pop(project["id"])
            raise
        return copy.deepcopy(project)

    def update_project(self, project_id: str, raw: Any) -> dict[str, Any]:
        current = self.get_project(project_id)
        if not isinstance(raw, dict):
            raise ScriptwritingError("劇本專案格式錯誤。")
        revision = raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ScriptwritingError("儲存時請提供目前版本 revision。")
        if revision != current["revision"]:
            raise ScriptwritingError("劇本已在另一個分頁更新；請重新載入後再儲存，避免覆蓋其他修改。", 409)
        project = normalize_project(raw, current)
        self._validate_reference_assets(project["character_references"])
        self.projects[project_id] = project
        try:
            self._save_projects()
        except OSError:
            self.projects[project_id] = current
            raise
        return copy.deepcopy(project)

    def get_job(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ScriptwritingError("找不到寫作任務。", 404)
        return readable_job(self.jobs[job_id])

    async def recover_storyboard(self, job_id: str) -> bool:
        """Recover complete saved drafts using trusted, strictly validated repairs."""
        job = self.jobs.get(job_id)
        task = self.tasks.get(job_id)
        if (not job or job.get("status") != "failed" or job.get("action") != "storyboard"
                or job.get("done_reason") != "stop" or job.get("truncated")
                or (task is not None and not task.done())
                or not isinstance(job.get("content"), str) or not job["content"].strip()):
            return False
        inputs = job.get("input")
        project = inputs.get("project") if isinstance(inputs, dict) else None
        if not isinstance(project, dict) or project.get("duration") is None:
            return False
        try:
            storyboard = parse_storyboard(job["content"], target_duration=project["duration"])
        except (ScriptwritingError, ValueError, TypeError):
            return False
        normalization = storyboard.get("storyboard_normalization")
        original_count = normalization.get("original_shot_count") if isinstance(normalization, dict) else None
        clip_count = normalization.get("generation_clip_count") if isinstance(normalization, dict) else None
        repaired_cuts = (type(original_count) is int and type(clip_count) is int
                         and 0 < clip_count < original_count)
        field_metadata = {
            key: storyboard[key] for key in ("storyboard_camera_normalization", "storyboard_dialogue_normalization")
            if isinstance(storyboard.get(key), dict)
            and type(storyboard[key].get("correction_count")) is int
            and storyboard[key]["correction_count"] > 0
            and isinstance(storyboard[key].get("corrections"), list)
            and len(storyboard[key]["corrections"]) == storyboard[key]["correction_count"]
        }
        if not repaired_cuts and not field_metadata:
            # An unrelated generation failure must not be relabelled completed.
            return False
        try:
            validated = validate_storyboard(storyboard, target_duration=project["duration"])
        except (ScriptwritingError, ValueError, TypeError):
            return False
        if repaired_cuts:
            validated["storyboard_normalization"] = copy.deepcopy(normalization)
        validated.update(copy.deepcopy(field_metadata))
        storyboard = validated
        if job.get("model_unloaded") is not True:
            settings = inputs.get("settings")
            if not isinstance(settings, dict) or not all(settings.get(key) for key in ("base_url", "model")):
                return False
            async with self.gpu_lock:
                if not await self._unload(settings):
                    return False
        # Check again after awaiting GPU ownership: another caller may have
        # removed or recovered the draft while the unload was pending.
        if self.jobs.get(job_id) is not job or job.get("status") != "failed":
            return False
        previous = copy.deepcopy(job)
        recovered_at = utc_now()
        job.update(storyboard=storyboard, status="completed", error=None, model_unloaded=True,
                   recovered_from_error=previous.get("error"), recovered_at=recovered_at,
                   finished_at=recovered_at, updated_at=recovered_at)
        try:
            self._save_jobs(job_id)
        except (OSError, ScriptwritingError):
            job.clear()
            job.update(previous)
            raise
        return True

    def delete_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        videos = self.app.get("script_video")
        if any(item.get("status") in ACTIVE and job_id in {
                item.get("source_job_id"), item.get("screenplay_job_id"), item.get("storyboard_job_id")}
                for item in getattr(videos, "pipelines", {}).values()):
            raise ScriptwritingError("自動影片流程仍使用此生成稿，請等流程結束或取消後再刪除。", 409)
        task = self.tasks.get(job_id)
        if job.get("status") in ACTIVE or (task is not None and not task.done()):
            raise ScriptwritingError("生成任務仍在執行或釋放模型，請取消並等待結束後再刪除。", 409)
        with self._history_lock():
            # Commit the deletion first. Old indexes and another instance cannot restore it.
            _atomic_json(self.history_directory / f"{job_id}.json",
                         {"id": job_id, "deleted": True, "deleted_at": utc_now()})
            self.jobs.pop(job_id)
            try:
                self._write_jobs_index()
                self.history_persistence_error = None
            except (OSError, ScriptwritingError):
                self.history_persistence_error = "刪除已保存，但清單索引未能更新；重新啟動後仍會保留刪除結果。"
        return {"deleted": True, "id": job_id}

    async def _json_request(self, session: aiohttp.ClientSession, method: str, url: str, payload: Any = None) -> Any:
        async with session.request(method, url, json=payload, allow_redirects=False) as response:
            if response.status >= 300:
                raise ScriptwritingError(f"本機 Ollama 回應 HTTP {response.status}；請確認服務與模型。", 503)
            body = await _bounded_body(response.content, 2_000_000)
            try:
                return json.loads(body)
            except (ValueError, UnicodeError) as error:
                raise ScriptwritingError("本機 Ollama 回應格式錯誤。", 502) from error

    async def _inventory(self, session: aiohttp.ClientSession, settings: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        raw = await self._json_request(session, "GET", settings["base_url"] + "/api/tags")
        if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
            raise ScriptwritingError("Ollama 模型清單格式錯誤。", 502)
        local = []
        excluded = 0
        for item in raw["models"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not MODEL_NAME.fullmatch(item["name"]):
                raise ScriptwritingError("Ollama 模型清單包含無效名稱。", 502)
            if is_cloud_model(item):
                excluded += 1
                continue
            size = item.get("size", 0)
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ScriptwritingError("Ollama 模型容量格式錯誤。", 502)
            local.append({"name": item["name"], "size": size})
        return local, excluded

    async def _check_model(self, session: aiohttp.ClientSession, settings: dict[str, Any], models: list[dict[str, Any]]) -> dict[str, Any]:
        model = settings["model"]
        names = {item["name"] for item in models}
        if model not in names and (":" in model or model + ":latest" not in names):
            raise ScriptwritingError(f"本機尚未安裝模型 {model}；請先下載模型，或在設定選擇已安裝模型。", 503)
        shown = await self._json_request(session, "POST", settings["base_url"] + "/api/show", {"model": model})
        if not isinstance(shown, dict):
            raise ScriptwritingError("Ollama 模型資訊格式錯誤。", 502)
        capabilities = shown.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ScriptwritingError("Ollama 模型能力資訊格式錯誤；請重新檢查本機模型。", 502)
        if is_cloud_model(shown):
            raise ScriptwritingError("選擇的模型會轉送到雲端；腳本編寫僅允許本機模型。", 400)
        return shown

    async def status(self) -> dict[str, Any]:
        settings = copy.deepcopy(self.settings)
        result = {"ready": False, "error": None, "settings": settings, "models": [], "excluded_cloud_models": 0,
                  "character_references_supported": True,
                  "vision_ready": False, "vision_error": None,
                  "local_only": True, "active_jobs": sum(job.get("status") in ACTIVE for job in self.jobs.values())}
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=8)) as session:
                result["models"], result["excluded_cloud_models"] = await self._inventory(session, settings)
                try:
                    await self._check_model(session, settings, result["models"])
                    result["ready"] = True
                except ScriptwritingError as error:
                    result["error"] = str(error)
                try:
                    vision = await self._check_model(session, {**settings, "model": settings["vision_model"]}, result["models"])
                    if "vision" not in vision.get("capabilities", []):
                        raise ScriptwritingError("外觀辨識模型不支援看圖；請選擇具備 vision 能力的本機模型，或自行填寫外觀描述。")
                    result["vision_ready"] = True
                except ScriptwritingError as error:
                    result["vision_error"] = str(error)
        except ScriptwritingError as error:
            result["error"] = str(error)
            result["vision_error"] = str(error)
        except (aiohttp.ClientError, TimeoutError, OSError):
            result["error"] = "無法連線至本機 Ollama；請先啟動 Ollama 並確認位址。"
            result["vision_error"] = result["error"]
        return result

    def submit(self, raw: Any, *, project_override: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.closed:
            raise ScriptwritingError("寫作服務正在關閉。", 503)
        if not isinstance(raw, dict):
            raise ScriptwritingError("寫作任務格式錯誤。")
        project = self.get_project(raw.get("project_id"))
        if project_override is not None:
            if not isinstance(project_override, dict) or project_override.get("id") != project["id"]:
                raise ScriptwritingError("自動影片劇本快照與專案不一致。")
            frozen = copy.deepcopy(project_override)
            # Older snapshots omitted the field, which meant no writing method.
            frozen.setdefault("writing_skill", "")
            frozen.setdefault("custom_loras", [])
            frozen.setdefault("video_settings", normalize_video_settings())
            project = normalize_project(frozen, project)
        action = raw.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ScriptwritingError("請選擇大綱、劇本、改寫、分鏡或角色外觀辨識。")
        instructions = text_field(raw.get("instructions", ""), "本次要求", 12000)
        if action == "rewrite" and not project["content"]:
            raise ScriptwritingError("請先儲存劇本內容再進行改寫。")
        if action != "appearance" and not project["idea"] and not project["content"]:
            raise ScriptwritingError("請先填寫故事概念或既有劇本。")
        if sum(job.get("status") in ACTIVE for job in self.jobs.values()) >= 20:
            raise ScriptwritingError("寫作佇列已滿，請等待現有任務完成。", 429)
        settings = copy.deepcopy(self.settings)
        references = project.get("character_references", [])
        self._validate_reference_assets(references)
        reference = None
        if action == "appearance":
            reference = next((item for item in references if item["id"] == raw.get("reference_id")), None)
            if reference is None or not reference["image_asset_id"]:
                raise ScriptwritingError("請先為角色外觀對照卡上傳圖片，再進行外觀辨識。")
            settings.update(model=settings["vision_model"], temperature=0.2,
                            num_ctx=max(settings["num_ctx"], 4096), num_predict=min(settings["num_predict"], 1024))
            messages = build_appearance_messages(reference)
        else:
            missing = [item["name"] for item in references if item["image_asset_id"] and not item["appearance"].strip()]
            if missing:
                raise ScriptwritingError("角色參考圖尚未填寫外觀描述：" + "、".join(missing) + "。請先分析外型並套用摘要，或自行填寫描述；Hermes 不會直接看圖。")
            messages = build_messages(project, action, instructions)
        requested_settings = copy.deepcopy(settings)
        settings = fit_context_settings(messages, settings, action=action)
        timestamp = utc_now()
        job = {"id": uuid.uuid4().hex, "project_id": project["id"], "project_title": project["title"],
               "action": action, "status": "queued", "content": "", "error": None,
               "created_at": timestamp, "updated_at": timestamp, "model": settings["model"],
               "output_budget_adjusted": action != "appearance" and settings["num_predict"] < requested_settings["num_predict"],
               "context_budget_adjusted": settings["num_ctx"] != requested_settings["num_ctx"],
               "context_budget": {"estimated_input_tokens": _input_token_estimate(messages),
                                  "requested_num_ctx": requested_settings["num_ctx"],
                                  "requested_num_predict": requested_settings["num_predict"]},
               "input": {"project": project, "settings": settings, "instructions": instructions},
               "shortfilm_id": None}
        if reference:
            job.update(reference_id=reference["id"], reference_name=reference["name"])
        self.jobs[job["id"]] = job
        try:
            self._save_jobs(job["id"])
        except OSError:
            self.jobs.pop(job["id"])
            raise
        task = asyncio.create_task(self._run(job["id"], messages), name=f"scriptwriting-{job['id']}")
        self.tasks[job["id"]] = task
        task.add_done_callback(lambda finished, job_id=job["id"]: self.tasks.pop(job_id, None))
        return copy.deepcopy(job)

    async def _unload(self, settings: dict[str, Any]) -> bool:
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=8)) as session:
                await self._json_request(session, "POST", settings["base_url"] + "/api/generate",
                                         {"model": settings["model"], "keep_alive": 0, "stream": False})
                return True
        except (ScriptwritingError, aiohttp.ClientError, TimeoutError, OSError):
            # A stopped Ollama cannot be unloaded, but should not hide the manuscript.
            return False

    async def _run(self, job_id: str, messages: list[dict[str, str]]) -> None:
        job = self.jobs[job_id]
        try:
            async with self.queue_lock:
                if self.gpu_lock is self.queue_lock:
                    await self._generate(job_id, messages)
                else:
                    async with self.gpu_lock:
                        await self._generate(job_id, messages)
        except asyncio.CancelledError:
            if job["status"] != "completed":
                job.update(status="interrupted" if self.closed else "cancelled", error=None, updated_at=utc_now())
        except (ScriptwritingError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as error:
            job.update(status="failed", error=str(error) if isinstance(error, ScriptwritingError) else "本機模型生成中斷；請確認 Ollama 服務與記憶體容量後重試。", updated_at=utc_now())
        except Exception:
            job.update(status="failed", error="寫作任務發生錯誤；已保留生成內容，請重試。", updated_at=utc_now())
        finally:
            self.responses.pop(job_id, None)
            try:
                self._save_jobs(job_id)
            except (OSError, ScriptwritingError):
                job.update(status="failed", error="無法儲存寫作結果；請確認磁碟空間與權限。生成內容仍保留於目前畫面。", updated_at=utc_now())

    async def _generate(self, job_id: str, messages: list[dict[str, str]]) -> None:
        job = self.jobs[job_id]
        settings = job["input"]["settings"]
        attempted = False
        try:
            timeout = aiohttp.ClientTimeout(total=3600, sock_connect=10, sock_read=180)
            async with aiohttp.ClientSession(trust_env=False, timeout=timeout) as session:
                async with asyncio.timeout(12):
                    models, _ = await self._inventory(session, settings)
                    shown = await self._check_model(session, settings, models)
                    if job["action"] == "appearance" and "vision" not in shown.get("capabilities", []):
                        raise ScriptwritingError("外觀辨識模型不支援看圖；請選擇具備 vision 能力的本機模型，或自行填寫外觀描述。")
                    limit = _model_context_limit(shown)
                    if job.get("context_budget_adjusted"):
                        job["context_budget"]["model_context_limit"] = limit
                    bound = limit if limit is not None else job.get("context_budget", {}).get("requested_num_ctx", settings["num_ctx"])
                    if job.get("context_budget_adjusted") and settings["num_ctx"] > bound:
                        try:
                            settings = fit_context_settings(messages, settings,
                                                            action=job["action"] if limit is not None else None,
                                                            context_limit=bound)
                        except ScriptwritingError as error:
                            reason = (f"所選本機模型的容量上限為 {limit:,} Token。" if limit is not None
                                      else "本機模型未提供有效容量資訊，無法確認可安全擴充本次容量。")
                            raise ScriptwritingError(reason + str(error)) from error
                        job["input"]["settings"] = settings
                        budget = job["context_budget"]
                        job["context_budget_adjusted"] = settings["num_ctx"] != budget["requested_num_ctx"]
                        job["output_budget_adjusted"] = settings["num_predict"] < budget["requested_num_predict"]
                await self._release_idle_comfy(job)
                job.update(status="running", started_at=utc_now(), updated_at=utc_now())
                job.pop("waiting_reason", None)
                self._save_jobs(job_id)
                payload = {"model": settings["model"], "messages": messages, "stream": True,
                           "keep_alive": 0,
                           "options": {key: settings[key] for key in ("num_ctx", "num_predict", "temperature")}}
                if "thinking" in shown.get("capabilities", []):
                    payload["think"] = False
                if job["action"] == "appearance":
                    reference = next(item for item in job["input"]["project"]["character_references"] if item["id"] == job["reference_id"])
                    try:
                        encoded = await asyncio.to_thread(prepare_reference_image, self.app.get("assets"), reference["image_asset_id"])
                    except ReferenceError as error:
                        raise ScriptwritingError(str(error)) from error
                    payload["messages"] = copy.deepcopy(messages)
                    payload["messages"][-1]["images"] = [encoded]
                if job["action"] == "storyboard":
                    payload["format"] = "json"
                attempted = True
                async with session.post(settings["base_url"] + "/api/chat", json=payload, allow_redirects=False) as response:
                    self.responses[job_id] = response
                    if response.status >= 300:
                        raise ScriptwritingError(f"Ollama 無法生成（HTTP {response.status}）；請確認模型與可用記憶體。", 502)
                    await self._read_stream(job, response)
                if job["action"] == "storyboard":
                    job["storyboard"] = parse_storyboard(job["content"], target_duration=job["input"]["project"]["duration"])
                elif job["action"] in {"screenplay", "rewrite"}:
                    readable = normalize_screenplay(job["content"])
                    if readable != job["content"]:
                        job["raw_content"] = job["content"]
                        job["content"] = readable
                    validate_script_timeline(job["content"], job["input"]["project"]["duration"], require_timeline=True)
                    job["content_format"] = "performance-script-v1"
                if job.get("truncated"):
                    raise ScriptwritingError("模型已達輸出 Token 上限，生成稿已保留但尚未完成。請先檢查是否重複續寫，修正創作要求後重試；只有實際內容未寫完時才調整輸出預算。")
                if job["action"] == "appearance":
                    job["content"] = text_field(job["content"], "角色外觀摘要", 2400, required=True)
                job.update(status="completed", finished_at=utc_now(), updated_at=utc_now())
        finally:
            self.responses.pop(job_id, None)
            if attempted:
                unload = asyncio.create_task(self._unload(settings))
                try:
                    released = await asyncio.shield(unload)
                except asyncio.CancelledError:
                    # Keep the GPU lock until an in-flight unload finishes.
                    await unload
                    raise
                job["model_unloaded"] = released
                if not released:
                    job["memory_warning"] = "Ollama 未確認模型卸載；如需生成影片，請先確認顯示記憶體是否已釋放。"

    async def _release_idle_comfy(self, job: dict[str, Any]) -> None:
        """Release cached video weights only after the local engine queue is idle."""
        comfy = self.app.get("comfy")
        if comfy is None or getattr(comfy, "mode", None) != "local":
            return
        try:
            base_url = local_base_url(comfy.base_url)
        except ScriptwritingError:
            return
        while True:
            try:
                async with asyncio.timeout(5):
                    snapshot = await comfy.queue_status()
            except (aiohttp.ClientError, TimeoutError, OSError):
                return
            if not isinstance(snapshot, dict) or snapshot.get("available") is not True:
                # An unavailable engine must never receive an unload request.
                return
            if snapshot.get("running_count") == 0 and snapshot.get("pending_count") == 0:
                break
            job.update(waiting_reason="等待本機影片引擎佇列完成，避免互相搶用顯示記憶體。", updated_at=utc_now())
            self._save_jobs(job["id"])
            await asyncio.sleep(2)
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.post(base_url + "/free", json={"unload_models": True, "free_memory": True},
                                        headers=comfy.auth_headers(), allow_redirects=False) as response:
                    if response.status >= 300:
                        job["memory_warning"] = "影片引擎未釋放快取；本機模型可能需要更多 CPU 記憶體。"
        except (aiohttp.ClientError, TimeoutError, OSError):
            job["memory_warning"] = "無法釋放影片引擎快取；本機模型可能需要更多 CPU 記憶體。"

    async def _read_stream(self, job: dict[str, Any], response: aiohttp.ClientResponse) -> None:
        buffer = b""
        total = 0
        done = False
        last_saved = time.monotonic()
        async for chunk in response.content.iter_any():
            total += len(chunk)
            buffer += chunk
            if total > MAX_WIRE_BYTES or len(buffer) > 1_000_000:
                raise ScriptwritingError("模型回應超過安全大小上限；請縮短故事後重試。", 502)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                if done:
                    raise ScriptwritingError("Ollama 在完成標記後仍回傳內容，回應格式錯誤。", 502)
                done = self._stream_item(job, line)
            if time.monotonic() - last_saved >= 0.25:
                job["updated_at"] = utc_now()
                self._save_jobs(job["id"])
                last_saved = time.monotonic()
        if buffer.strip():
            if done:
                raise ScriptwritingError("Ollama 回應格式錯誤。", 502)
            done = self._stream_item(job, buffer)
        if not done:
            raise ScriptwritingError("Ollama 串流提前中斷，已保留部分內容；請重試。", 502)
        if not job["content"].strip():
            raise ScriptwritingError("模型沒有回傳劇本內容，請檢查模型設定後重試。", 502)

    @staticmethod
    def _stream_item(job: dict[str, Any], line: bytes) -> bool:
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError) as error:
            raise ScriptwritingError("Ollama 串流包含無效 JSON，已保留部分內容。", 502) from error
        if not isinstance(item, dict):
            raise ScriptwritingError("Ollama 串流格式錯誤。", 502)
        if item.get("error"):
            message = str(item["error"])[:500]
            raise ScriptwritingError(f"本機 Ollama 生成失敗：{message}", 502)
        message = item.get("message", {})
        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
            raise ScriptwritingError("Ollama 訊息內容格式錯誤。", 502)
        job["content"] += message.get("content", "")
        if len(job["content"]) > MAX_CONTENT:
            job["content"] = job["content"][:MAX_CONTENT]
            raise ScriptwritingError("生成內容超過 300,000 字上限。", 502)
        done = item.get("done", False)
        if not isinstance(done, bool):
            raise ScriptwritingError("Ollama 完成標記格式錯誤。", 502)
        if done:
            job["done_reason"] = str(item.get("done_reason", "stop"))[:80]
            job["truncated"] = item.get("done_reason") == "length"
        return done

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] not in ACTIVE:
            return job
        # Mark first: cancelling a task before its coroutine starts skips its finally.
        self.jobs[job_id].update(status="cancelled", error=None, updated_at=utc_now())
        response = self.responses.get(job_id)
        if response:
            response.close()
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._save_jobs(job_id)
        return self.get_job(job_id)

    def import_shortfilm(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise ScriptwritingError("找不到寫作任務。", 404)
        if job.get("status") != "completed" or job.get("action") != "storyboard" or job.get("truncated"):
            raise ScriptwritingError("只有已完成且通過驗證的分鏡可以建立短片。", 409)
        store = self.app.get("shortfilms")
        if store is None:
            raise ScriptwritingError("短片功能尚未啟用。", 503)
        storyboard = validate_storyboard(job.get("storyboard"), target_duration=job["input"]["project"].get("duration"))
        normalization = job["storyboard"].get("storyboard_normalization")
        source_cuts = normalization.get("source_cuts") if isinstance(normalization, dict) else None
        original_speaker_ids = {}
        source_speakers = set()
        inline_speaker_ids = set()
        if isinstance(normalization, dict) and "source_cuts" in normalization:
            if not isinstance(source_cuts, list) or not source_cuts:
                raise ScriptwritingError("合併分鏡的來源切鏡資訊不完整，無法核對圖片對照與說話角色；請重新整理分鏡。")
            original_speaker_ids = {
                f"S{index}": item["name"]
                for index, item in enumerate(
                    (entry for entry in storyboard["characters"] if entry.get("type", "character") in {"character", "creature"}), 1,
                )
            }
            for group in source_cuts:
                if not isinstance(group, dict) or not isinstance(group.get("cuts"), list):
                    raise ScriptwritingError("合併分鏡的來源切鏡資訊不完整，無法核對說話角色；請重新整理分鏡。")
                for cut in group["cuts"]:
                    if not isinstance(cut, dict) or not isinstance(cut.get("speaker", ""), str):
                        raise ScriptwritingError("合併分鏡的來源說話角色資訊錯誤；請重新整理分鏡。")
                    if cut.get("speaker"):
                        source_speakers.add(cut["speaker"].casefold())
            inline_speaker_ids = {
                match.upper()
                for scene in storyboard["scenes"] for shot in scene["shots"]
                for field in ("action", "camera_detail", "ending", "sound")
                for match in re.findall(r"\(\s*(S\d+)\s*\)", re.sub(r"<d>.*?</d>", "", shot.get(field, ""), flags=re.DOTALL), re.IGNORECASE)
            }
        references = job["input"]["project"].get("character_references", [])
        self._validate_reference_assets(references)
        storyboard = copy.deepcopy(storyboard)
        characters_by_name = {item["name"].casefold(): item for item in storyboard["characters"]}
        for reference in references:
            if len(reference["appearance"]) > 1200:
                raise ScriptwritingError(f"角色「{reference['name']}」的外觀描述超過 H3 素材的 1200 字上限，請縮短後重新生成分鏡。")
            character = characters_by_name.get(reference["name"].casefold())
            if character is None:
                character = {"name": reference["name"], "description": "", "type": reference["type"]}
                storyboard["characters"].append(character)
            # Uploaded bindings are carried by image_asset_ids below; reserve the
            # whole H3 description limit for appearance rather than a duplicate label.
            character.update(type=reference["type"], description=reference["appearance"] or character["description"], reference="")
        if source_cuts is not None:
            final_speakers = [item for item in storyboard["characters"] if item.get("type", "character") in {"character", "creature"}]
            final_speaker_ids = {f"S{index}": item["name"] for index, item in enumerate(final_speakers, 1)}
            final_speaker_names = {item["name"].casefold() for item in final_speakers}
            for speaker in source_speakers:
                if speaker not in final_speaker_names:
                    name = next((item["name"] for item in storyboard["characters"] if item["name"].casefold() == speaker), speaker)
                    raise ScriptwritingError(f"圖片對照將合併分鏡的說話角色「{name}」改成場景或物件，無法安全沿用既有對白；請統一素材類型後重新整理分鏡。")
            for speaker_id in sorted(inline_speaker_ids):
                original_name = original_speaker_ids.get(speaker_id)
                final_name = final_speaker_ids.get(speaker_id)
                if original_name is None or final_name is None or original_name.casefold() != final_name.casefold():
                    raise ScriptwritingError(f"圖片對照會改變合併分鏡的對白編號 {speaker_id}（「{original_name or '未指定'}」→「{final_name or '不再是說話角色'}」），可能讓台詞換人；請統一素材類型後重新整理分鏡。")
        storyboard = validate_storyboard(storyboard, target_duration=job["input"]["project"].get("duration"))
        references_by_name = {item["name"].casefold(): item for item in references}
        if job.get("shortfilm_id"):
            project = getattr(store, "projects", {}).get(job["shortfilm_id"])
            if project is not None:
                return {"shortfilm_id": job["shortfilm_id"], "project": copy.deepcopy(project), "existing": True}
        try:
            video_settings = normalize_video_settings(job["input"]["project"].get("video_settings"))
            selected_loras = job["input"]["project"].get("custom_loras", [])
            if video_settings.get("video_model") == VIDEO_MODEL_FAST:
                custom_loras = normalize_loras(selected_loras)
            else:
                custom_loras = validate_loras_for_family(selected_loras, "ref2va")
                validate_lora_requirements(custom_loras, "ref2va")
        except ValueError as error:
            raise ScriptwritingError(str(error)) from error
        if job.get("shortfilm_id"):
            # Reserve before creation; a crash can resume the same import identity.
            project_id = job["shortfilm_id"]
        else:
            project_id = uuid.uuid4().hex
            job["shortfilm_id"] = project_id
            job["updated_at"] = utc_now()
            self._save_jobs(job_id)
        project = new_project(storyboard["title"])
        project.update(aspect_ratio=video_settings["aspect_ratio"], megapixels=video_settings["megapixels"])
        if "video_model" in video_settings:
            project["video_model"] = video_settings["video_model"]
        project["custom_loras"] = copy.deepcopy(custom_loras)
        project["id"] = project_id
        project["synopsis"] = storyboard["synopsis"]
        project["style"] = job["input"]["project"]["style"] or project["style"]
        aliases = {}
        for character in storyboard["characters"]:
            asset = new_asset(character.get("type", "character"), character["name"])
            reference = character.get("reference", "")
            asset["description"] = (f"參考圖對應：{reference}。 " if reference else "") + character["description"]
            bound_reference = references_by_name.get(character["name"].casefold())
            if bound_reference and bound_reference["image_asset_id"]:
                asset["image_asset_ids"] = [bound_reference["image_asset_id"]]
            project["assets"].append(asset)
            aliases[character["name"].casefold()] = asset
        duration = 0
        count = 0
        for index, raw_scene in enumerate(storyboard["scenes"], 1):
            scene = new_scene(index)
            scene.update({key: raw_scene[key] for key in ("title", "location", "time_of_day")})
            scene["description"] = raw_scene.get("description", "")
            for raw_shot in raw_scene["shots"]:
                count += 1
                shot = new_shot(count)
                shot.update({key: raw_shot[key] for key in ("title", "duration", "action", "dialogue", "sound", "camera", "shot_size")})
                shot["camera_detail"] = raw_shot.get("camera_detail", "")
                if "ending" in raw_shot:
                    shot["ending"] = raw_shot["ending"]
                shot["music"] = "N/A"
                shot["sound"] = f"{NO_BGM_SOUNDSCAPE} {shot['sound']}".strip()
                if raw_shot["speaker"]:
                    shot["speaker_alias"] = aliases[raw_shot["speaker"].casefold()]["alias"]
                if raw_shot["speaker"]:
                    shot["asset_ids"] = [aliases[raw_shot["speaker"].casefold()]["id"]]
                shot["asset_ids"] = resolve_shot_asset_ids(project, scene, shot)
                duration += shot["duration"]
                scene["shots"].append(shot)
            project["scenes"].append(scene)
        project["target_duration"] = duration
        # Existing ShortFilmStore uses create; compatible stores may expose save.
        saved = store.create(project) if hasattr(store, "create") else store.save(project)
        job["shortfilm_id"] = saved["id"]
        job["updated_at"] = utc_now()
        self._save_jobs(job_id)
        return {"shortfilm_id": saved["id"], "project": saved, "existing": False}

    async def close(self) -> None:
        self.closed = True
        for job_id, task in list(self.tasks.items()):
            job = self.jobs[job_id]
            if job["status"] in ACTIVE:
                job.update(status="interrupted", updated_at=utc_now())
                response = self.responses.get(job_id)
                if response:
                    response.close()
                task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self._save_jobs()


class UnavailableScriptwritingManager(ScriptwritingManager):
    """Keep the rest of the studio usable without touching damaged manuscripts."""
    def __init__(self, app: web.Application, error: str):
        self.app = app
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.projects = {}
        self.jobs = {}
        self.tasks = {}
        self.responses = {}
        self.closed = False
        self.unavailable_error = error

    async def status(self) -> dict[str, Any]:
        return {
            "ready": False, "available": False, "initialization_error": True,
            "error": self.unavailable_error, "settings": self.settings,
            "models": [], "excluded_cloud_models": 0, "local_only": True, "active_jobs": 0,
        }

    async def close(self) -> None:
        self.closed = True


async def _payload(request: web.Request) -> Any:
    try:
        body = await _bounded_body(request.content, 2_000_000)
        return json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise ScriptwritingError("請傳送有效 JSON。") from error


def register_scriptwriting_routes(app: web.Application, static_dir: Path, data_dir: Path) -> ScriptwritingManager:
    try:
        manager = ScriptwritingManager(app, data_dir)
    except (ScriptwritingError, OSError) as error:
        detail = str(error) if isinstance(error, ScriptwritingError) else "無法讀取或建立本機寫作資料，請確認磁碟與檔案權限。"
        manager = UnavailableScriptwritingManager(
            app, detail + " 腳本編寫暫時停用；請修復或還原原始資料並重新啟動 H3 Studio。其他工具仍可使用。",
        )
    app["scriptwriting"] = manager
    video_error = None
    try:
        videos = ScriptVideoManager(app, manager, data_dir)
    except (ScriptVideoError, OSError) as error:
        videos = None
        video_error = str(error)
    app["script_video"] = videos

    def video_manager():
        if videos is None:
            raise ScriptVideoError(video_error or "自動影片服務尚未啟用。", 503)
        return videos

    def endpoint(handler):
        async def wrapped(request):
            try:
                unavailable = getattr(manager, "unavailable_error", None)
                if unavailable and not (request.method == "GET" and request.path in {
                        "/scripts", "/api/scripts/status", "/api/scripts/settings", "/api/scripts/skills"}):
                    raise ScriptwritingError(unavailable, 503)
                return await handler(request)
            except ScriptwritingError as error:
                return web.json_response({"error": str(error)}, status=error.status)
            except ScriptVideoError as error:
                return web.json_response({"error": str(error)}, status=error.status)
            except OSError:
                return web.json_response({"error": "無法儲存寫作資料；請確認磁碟空間與檔案權限。"}, status=500)
        return wrapped

    async def page(request):
        return web.FileResponse(Path(static_dir) / "scripts.html")

    async def settings(request):
        if request.method == "POST":
            value = normalize_settings(await _payload(request), manager.settings)
            _atomic_json(manager.settings_path, value)
            manager.settings = value
        if getattr(manager, "unavailable_error", None):
            return web.json_response({**manager.settings, "available": False, "error": manager.unavailable_error})
        return web.json_response(manager.settings)

    async def status(request):
        value = await manager.status()
        video_status = await videos.readiness() if videos is not None else {"ready": False, "error": video_error}
        value.update(video_pipelines_supported=True, video_ready=video_status["ready"], video_error=video_status["error"])
        value["manuscript_video_supported"] = True
        value["prompt_review_supported"] = True
        value["final_review_supported"] = True
        value["official_prompt_version"] = OFFICIAL_PROMPT_VERSION
        value["video_model_supported"] = video_status.get("video_model_supported") is True
        value["video_models"] = video_status.get("video_models", [])
        value["writing_skills_supported"] = True
        value["custom_loras_supported"] = True
        return web.json_response(value)

    async def skills(request):
        return web.json_response({"items": list_skills(), "version": SKILLS_VERSION},
                                 headers={"Cache-Control": "no-store"})

    async def reference_image(request):
        try:
            value = await upload_reference_image(request, app.get("assets"))
        except ReferenceError as error:
            raise ScriptwritingError(str(error)) from error
        return web.json_response(value, status=201)

    async def projects(request):
        if request.method == "POST":
            return web.json_response(manager.create_project(await _payload(request)), status=201)
        items = sorted(manager.projects.values(), key=lambda item: item["updated_at"], reverse=True)
        return web.json_response({"items": items})

    async def project(request):
        project_id = request.match_info["project_id"]
        value = manager.update_project(project_id, await _payload(request)) if request.method == "PUT" else manager.get_project(project_id)
        return web.json_response(value)

    async def export(request):
        value = manager.get_project(request.match_info["project_id"])
        format_name = request.query.get("format", "txt")
        if format_name not in {"txt", "md", "json"}:
            raise ScriptwritingError("匯出格式必須是 txt、md 或 json。")
        if format_name == "json":
            body, mime = json.dumps(value, ensure_ascii=False, indent=2), "application/json"
        elif format_name == "md":
            body, mime = f"# {value['title']}\n\n{value['content']}\n", "text/markdown"
        else:
            body, mime = f"{value['title']}\n\n{value['content']}\n", "text/plain"
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value["title"]).rstrip(". ") or "劇本"
        headers = {"Content-Disposition": f"attachment; filename=screenplay.{format_name}; filename*=UTF-8''{quote(filename + '.' + format_name)}"}
        return web.Response(text=body, content_type=mime, charset="utf-8", headers=headers)

    async def jobs(request):
        if request.method == "POST":
            raw = await _payload(request)
            if isinstance(raw, dict) and "auto_video" in raw and not isinstance(raw["auto_video"], bool):
                raise ScriptwritingError("自動生成影片選項必須是布林值。")
            if isinstance(raw, dict) and "require_prompt_review" in raw and not isinstance(raw["require_prompt_review"], bool):
                raise ScriptwritingError("生成稿確認選項必須是布林值。")
            if isinstance(raw, dict) and raw.get("auto_video"):
                if raw.get("action") not in {"screenplay", "rewrite"}:
                    raise ScriptwritingError("自動生成影片僅適用編寫完整劇本或潤飾編輯稿。")
                video_manager()._check_duplicate(raw.get("project_id"))
                snapshot = manager.get_project(raw.get("project_id"))
                await video_manager().preflight(snapshot)
                value = manager.submit(raw, project_override=snapshot)
                pipeline = await video_manager().start({"project_id": value["project_id"], "source_job_id": value["id"],
                    **({"require_prompt_review": raw["require_prompt_review"]} if "require_prompt_review" in raw else {})}, allow_active_source=True)
                value["video_pipeline_id"] = pipeline["id"]
                value["video_pipeline"] = pipeline
                return web.json_response(value, status=202)
            return web.json_response(manager.submit(raw), status=202)
        items = sorted(manager.jobs.values(), key=lambda item: (manager._job_timestamp({"created_at": item.get("created_at", "")}), item["id"]), reverse=True)
        project_id = request.query.get("project_id")
        if project_id is not None:
            if not SAFE_ID.fullmatch(project_id):
                raise ScriptwritingError("劇本專案 ID 格式錯誤。")
            items = [item for item in items if item.get("project_id") == project_id]
        total = len(items)
        try:
            offset = int(request.query.get("offset", "0"))
            limit = int(request.query["limit"]) if "limit" in request.query else None
        except (ValueError, TypeError):
            raise ScriptwritingError("生成記錄分頁參數必須是整數。")
        if offset < 0 or (limit is not None and not 1 <= limit <= 200):
            raise ScriptwritingError("生成記錄 offset 不可小於 0，limit 必須介於 1～200。")
        page_items = items[offset:offset + limit] if limit is not None else items[offset:]
        public = lambda item: {key: value for key, value in item.items() if key != "input"}
        return web.json_response({"items": [public(item) for item in page_items], "total": total,
                                  "offset": offset, "limit": limit, "has_more": offset + len(page_items) < total,
                                  "active_items": [public(item) for item in manager.jobs.values() if item.get("status") in ACTIVE],
                                  "history_persistent": True, "persistence_error": manager.history_persistence_error},
                                 headers={"Cache-Control": "no-store"})

    async def job(request):
        if request.method == "DELETE":
            return web.json_response(manager.delete_job(request.match_info["job_id"]))
        return web.json_response(manager.get_job(request.match_info["job_id"]))

    async def cancel(request):
        return web.json_response(await manager.cancel(request.match_info["job_id"]))

    async def shortfilm(request):
        value = manager.import_shortfilm(request.match_info["job_id"])
        return web.json_response(value, status=200 if value["existing"] else 201)

    async def video_pipelines(request):
        service = video_manager()
        if request.method == "POST":
            return web.json_response(await service.start(await _payload(request)), status=202)
        items = service.list_pipelines()
        project_id = request.query.get("project_id")
        if project_id is not None:
            if not SAFE_ID.fullmatch(project_id):
                raise ScriptVideoError("劇本專案 ID 格式錯誤。")
            items = [item for item in items if item.get("project_id") == project_id]
        return web.json_response({"items": items}, headers={"Cache-Control": "no-store"})

    async def video_pipeline(request):
        return web.json_response(video_manager().get_pipeline(request.match_info["pipeline_id"]), headers={"Cache-Control": "no-store"})

    async def cancel_video_pipeline(request):
        return web.json_response(await video_manager().cancel(request.match_info["pipeline_id"]))

    async def resume_video_pipeline(request):
        raw = await _payload(request) if request.can_read_body else {}
        return web.json_response(await video_manager().resume(request.match_info["pipeline_id"], raw), status=202)

    async def approve_video_pipeline(request):
        return web.json_response(await video_manager().approve(request.match_info["pipeline_id"], await _payload(request)), status=202)

    app.router.add_get("/scripts", endpoint(page))
    app.router.add_get("/api/scripts/settings", endpoint(settings))
    app.router.add_post("/api/scripts/settings", endpoint(settings))
    app.router.add_get("/api/scripts/status", endpoint(status))
    app.router.add_get("/api/scripts/skills", endpoint(skills))
    app.router.add_post("/api/scripts/reference-images", endpoint(reference_image))
    app.router.add_get("/api/scripts/projects", endpoint(projects))
    app.router.add_post("/api/scripts/projects", endpoint(projects))
    app.router.add_get("/api/scripts/projects/{project_id}", endpoint(project))
    app.router.add_put("/api/scripts/projects/{project_id}", endpoint(project))
    app.router.add_get("/api/scripts/projects/{project_id}/export", endpoint(export))
    app.router.add_get("/api/scripts/jobs", endpoint(jobs))
    app.router.add_post("/api/scripts/jobs", endpoint(jobs))
    app.router.add_get("/api/scripts/jobs/{job_id}", endpoint(job))
    app.router.add_delete("/api/scripts/jobs/{job_id}", endpoint(job))
    app.router.add_post("/api/scripts/jobs/{job_id}/cancel", endpoint(cancel))
    app.router.add_post("/api/scripts/jobs/{job_id}/shortfilm", endpoint(shortfilm))
    app.router.add_get("/api/scripts/video-pipelines", endpoint(video_pipelines))
    app.router.add_post("/api/scripts/video-pipelines", endpoint(video_pipelines))
    app.router.add_get("/api/scripts/video-pipelines/{pipeline_id}", endpoint(video_pipeline))
    app.router.add_post("/api/scripts/video-pipelines/{pipeline_id}/cancel", endpoint(cancel_video_pipeline))
    app.router.add_post("/api/scripts/video-pipelines/{pipeline_id}/resume", endpoint(resume_video_pipeline))
    app.router.add_post("/api/scripts/video-pipelines/{pipeline_id}/approve", endpoint(approve_video_pipeline))

    async def cleanup(application):
        if videos is not None:
            await videos.close()
        await manager.close()
    app.on_cleanup.append(cleanup)
    return manager
