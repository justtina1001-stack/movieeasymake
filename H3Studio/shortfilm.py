from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
ASSET_TYPES = {"character", "creature", "object", "background", "style", "motion", "effect"}
FORMATS = {"narrative", "dialogue", "commercial", "montage"}
SHOT_SIZES = {
    "wide": "wide establishing shot",
    "full": "full shot",
    "medium": "medium shot",
    "close": "close-up",
    "extreme_close": "extreme close-up",
    "pov": "point-of-view shot",
}
CAMERAS = {
    "static": "The camera remains in a stable Static Shot.",
    "push_in": "The camera pushes in with small amplitude at slow speed.",
    "pull_out": "The camera pulls out with small amplitude at slow speed.",
    "pan_left": "The camera pans left smoothly at slow speed.",
    "pan_right": "The camera pans right smoothly at slow speed.",
    "tracking": "The camera uses a smooth Tracking Shot that keeps the main subject readable.",
    "handheld": "The camera uses restrained handheld motion with small amplitude and natural weight.",
    "custom": "",
}
FORMAT_LABELS = {
    "narrative": "narrative short film",
    "dialogue": "character dialogue scene",
    "commercial": "cinematic promotional short",
    "montage": "visual montage",
}


class ShortFilmError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, *, limit: int = 4000) -> str:
    result = str(value or "").strip()
    return result[:limit]


def _bounded_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


def _id(value: Any = None) -> str:
    candidate = _text(value, limit=32).lower()
    return candidate if SAFE_ID.fullmatch(candidate) else uuid.uuid4().hex


def _asset_id(value: Any) -> str | None:
    candidate = _text(value, limit=32).lower()
    return candidate if SAFE_ID.fullmatch(candidate) else None


def new_project(title: Any = "未命名短片") -> dict[str, Any]:
    now = utc_now()
    return {
        "id": uuid.uuid4().hex,
        "title": _text(title, limit=80) or "未命名短片",
        "format": "narrative",
        "aspect_ratio": "16:9",
        "megapixels": 0.4,
        "quality_mode": "native",
        "target_duration": 30.0,
        "style": "cinematic visual storytelling, coherent lighting, stable character identity",
        "synopsis": "",
        "assets": [],
        "scenes": [],
        "created_at": now,
        "updated_at": now,
    }


def new_asset(asset_type: str = "character", alias: str = "角色") -> dict[str, Any]:
    safe_type = asset_type if asset_type in ASSET_TYPES else "character"
    return {
        "id": uuid.uuid4().hex,
        "alias": _text(alias, limit=60) or "未命名素材",
        "type": safe_type,
        "description": "",
        "image_asset_ids": [],
        "audio_asset_id": None,
        "voice_mode": "timbre",
    }


def new_scene(index: int = 1) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "title": f"場次 {index}",
        "location": "",
        "time_of_day": "",
        "description": "",
        "shots": [],
    }


def new_shot(index: int = 1) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "title": f"鏡頭 {index}",
        "duration": 5.0,
        "shot_size": "medium",
        "camera": "static",
        "camera_detail": "",
        "action": "",
        "ending": "The subjects settle into a readable final pose and the final composition remains stable.",
        "speaker_alias": "",
        "dialogue_language": "Chinese",
        "dialogue": "",
        "sound": "Natural room tone and synchronized physical action sounds.",
        "music": "N/A",
        "asset_ids": [],
        "storyboard_asset_id": None,
        "continue_previous": False,
        "continuation_asset_id": None,
        "seed": 1,
        "job_id": None,
        "status": "draft",
    }


def normalize_project(raw: Any, *, existing_id: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ShortFilmError("短片專案格式錯誤。")
    base = new_project(raw.get("title"))
    base["id"] = existing_id or _id(raw.get("id"))
    base["format"] = raw.get("format") if raw.get("format") in FORMATS else "narrative"
    base["aspect_ratio"] = raw.get("aspect_ratio") if raw.get("aspect_ratio") in ASPECT_RATIOS else "16:9"
    base["megapixels"] = _bounded_number(raw.get("megapixels"), 0.4, 0.2, 1.0)
    base["quality_mode"] = "turbo" if raw.get("quality_mode") == "turbo" else "native"
    base["target_duration"] = _bounded_number(raw.get("target_duration"), 30, 5, 3600)
    base["style"] = _text(raw.get("style"), limit=1000)
    base["synopsis"] = _text(raw.get("synopsis"), limit=5000)
    base["created_at"] = _text(raw.get("created_at"), limit=80) or base["created_at"]
    base["updated_at"] = utc_now()

    assets: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for item in list(raw.get("assets") or [])[:64]:
        if not isinstance(item, dict):
            continue
        alias = _text(item.get("alias"), limit=60) or "未命名素材"
        key = alias.casefold()
        if key in aliases:
            raise ShortFilmError(f"素材名稱代號「{alias}」重複。")
        aliases.add(key)
        image_ids = []
        for value in list(item.get("image_asset_ids") or [])[:9]:
            if (asset_id := _asset_id(value)) and asset_id not in image_ids:
                image_ids.append(asset_id)
        assets.append({
            "id": _id(item.get("id")),
            "alias": alias,
            "type": item.get("type") if item.get("type") in ASSET_TYPES else "character",
            "description": _text(item.get("description"), limit=1200),
            "image_asset_ids": image_ids,
            "audio_asset_id": _asset_id(item.get("audio_asset_id")),
            "voice_mode": "reuse" if item.get("voice_mode") == "reuse" else "timbre",
        })
    base["assets"] = assets
    valid_asset_ids = {item["id"] for item in assets}

    scenes: list[dict[str, Any]] = []
    shot_count = 0
    for scene_raw in list(raw.get("scenes") or [])[:100]:
        if not isinstance(scene_raw, dict):
            continue
        scene = {
            "id": _id(scene_raw.get("id")),
            "title": _text(scene_raw.get("title"), limit=80) or f"場次 {len(scenes) + 1}",
            "location": _text(scene_raw.get("location"), limit=200),
            "time_of_day": _text(scene_raw.get("time_of_day"), limit=120),
            "description": _text(scene_raw.get("description"), limit=1800),
            "shots": [],
        }
        for shot_raw in list(scene_raw.get("shots") or []):
            if shot_count >= 500 or not isinstance(shot_raw, dict):
                break
            selected_ids = [value for value in list(shot_raw.get("asset_ids") or []) if value in valid_asset_ids]
            shot = new_shot(shot_count + 1)
            shot.update({
                "id": _id(shot_raw.get("id")),
                "title": _text(shot_raw.get("title"), limit=80) or shot["title"],
                "duration": _bounded_number(shot_raw.get("duration"), 5, 5, 15),
                "shot_size": shot_raw.get("shot_size") if shot_raw.get("shot_size") in SHOT_SIZES else "medium",
                "camera": shot_raw.get("camera") if shot_raw.get("camera") in CAMERAS else "static",
                "camera_detail": _text(shot_raw.get("camera_detail"), limit=800),
                "action": _text(shot_raw.get("action"), limit=5000),
                "ending": _text(shot_raw.get("ending"), limit=1200) or shot["ending"],
                "speaker_alias": _text(shot_raw.get("speaker_alias"), limit=60),
                "dialogue_language": _text(shot_raw.get("dialogue_language"), limit=40) or "Chinese",
                "dialogue": _text(shot_raw.get("dialogue"), limit=1800),
                "sound": _text(shot_raw.get("sound"), limit=1200) or shot["sound"],
                "music": _text(shot_raw.get("music"), limit=1200) or "N/A",
                "asset_ids": list(dict.fromkeys(selected_ids)),
                "storyboard_asset_id": _asset_id(shot_raw.get("storyboard_asset_id")),
                "continue_previous": bool(shot_raw.get("continue_previous")),
                "continuation_asset_id": _asset_id(shot_raw.get("continuation_asset_id")),
                "seed": max(0, int(_bounded_number(shot_raw.get("seed"), 1, 0, 2**53 - 1))),
                "job_id": _asset_id(shot_raw.get("job_id")),
                "status": shot_raw.get("status") if shot_raw.get("status") in {
                    "draft", "preparing", "queued", "running", "completed", "failed", "cancelled", "interrupted",
                } else "draft",
            })
            scene["shots"].append(shot)
            shot_count += 1
        scenes.append(scene)
    base["scenes"] = scenes
    return base


def flatten_shots(project: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(scene, shot) for scene in project.get("scenes", []) for shot in scene.get("shots", [])]


def project_warnings(project: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    shots = flatten_shots(project)
    total = sum(float(shot.get("duration") or 0) for _, shot in shots)
    target = float(project.get("target_duration") or 0)
    if not shots:
        warnings.append("尚未建立任何分鏡鏡頭。")
    elif abs(total - target) > 1.0:
        warnings.append(f"分鏡合計 {total:.1f} 秒，與目標 {target:.1f} 秒相差 {abs(total - target):.1f} 秒。")
    aliases = {asset["alias"] for asset in project.get("assets", [])}
    for shot_index, (scene, shot) in enumerate(shots):
        label = f"{scene['title']}／{shot['title']}"
        if not shot.get("action"):
            warnings.append(f"{label} 尚未填寫可見動作。")
        speaker = shot.get("speaker_alias")
        if shot.get("dialogue") and not speaker:
            warnings.append(f"{label} 有台詞但尚未指定說話角色。")
        elif speaker and speaker not in aliases:
            warnings.append(f"{label} 的說話角色「{speaker}」不在素材中心。")
        if shot.get("continue_previous") and shot_index == 0:
            warnings.append(f"{label} 是第一鏡，無法沿用上一鏡尾幀。")
    return warnings[:80]


def _speaker_ids(project: dict[str, Any]) -> dict[str, str]:
    speakers = [
        asset["alias"] for asset in project.get("assets", [])
        if asset.get("type") in {"character", "creature"}
    ]
    return {alias: f"S{index}" for index, alias in enumerate(speakers, start=1)}


def compile_shot_payload(
    project: dict[str, Any],
    scene_id: str,
    shot_id: str,
    *,
    continuation_asset_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    project = normalize_project(project, existing_id=project.get("id"))
    scene = next((item for item in project["scenes"] if item["id"] == scene_id), None)
    if not scene:
        raise ShortFilmError("找不到指定場次。")
    shot = next((item for item in scene["shots"] if item["id"] == shot_id), None)
    if not shot:
        raise ShortFilmError("找不到指定鏡頭。")

    asset_lookup = {item["id"]: item for item in project["assets"]}
    selected = [asset_lookup[item_id] for item_id in shot["asset_ids"] if item_id in asset_lookup]
    warnings: list[str] = []
    if not shot["action"]:
        raise ShortFilmError("請先填寫這個鏡頭的畫面與動作。")
    if not selected:
        warnings.append("這個鏡頭沒有選擇參考素材，會以純文字生成。")
    for asset in selected:
        if not asset["image_asset_ids"] and not asset["audio_asset_id"]:
            warnings.append(f"素材「{asset['alias']}」沒有圖片或聲音，會只使用名稱與文字描述。")
    referenced = [asset for asset in selected if asset["image_asset_ids"] or asset["audio_asset_id"]]

    location = ", ".join(value for value in [scene["location"], scene["time_of_day"]] if value) or "the established story location"
    style = project["style"] or "cinematic visual storytelling with coherent lighting"
    camera = CAMERAS.get(shot["camera"], "")
    if shot["camera_detail"]:
        camera = f"{camera} {shot['camera_detail']}".strip()
    if not camera:
        camera = "The camera movement follows the user's shot direction while preserving spatial continuity."
    subjects = ", ".join(
        f"{asset['alias']} ({asset['description']})" if asset["description"] else asset["alias"]
        for asset in selected
    ) or "the described subjects"
    speaker_ids = _speaker_ids(project)
    dialogue = ""
    if shot["dialogue"]:
        speaker = shot["speaker_alias"]
        if not speaker:
            raise ShortFilmError("這個鏡頭有台詞，請指定說話角色。")
        if speaker not in speaker_ids:
            raise ShortFilmError(f"說話角色「{speaker}」必須先建立為角色或動物素材。")
        dialogue = (
            f" {speaker} ({speaker_ids[speaker]}) says "
            f"<d>[{shot['dialogue_language']}] {shot['dialogue']}</d>."
        )

    storyboard_sentence = ""
    if shot["storyboard_asset_id"]:
        storyboard_sentence = " Follow the uploaded storyboard for composition, relative positions, and camera angle without treating it as an exact frame."
    continuity_sentence = ""
    prepared_continuation = (continuation_asset_id or shot.get("continuation_asset_id")) if shot["continue_previous"] else None
    if shot["continue_previous"]:
        if prepared_continuation:
            continuity_sentence = " Begin from the preceding shot's final frame, preserving position, pose, gaze, lighting, camera direction, and ongoing momentum before the new action develops."
        else:
            warnings.append("已要求沿用上一鏡尾幀，但上一鏡尚未完成；生成前必須先完成上一鏡。")

    narrative = " ".join(part.strip() for part in [
        f"[Shot 1] A {FORMAT_LABELS[project['format']]} in {style}.",
        f"The scene takes place in {location}. {scene['description']}".strip(),
        f"Use a {SHOT_SIZES[shot['shot_size']]} featuring {subjects}.",
        camera,
        continuity_sentence,
        storyboard_sentence,
        shot["action"],
        dialogue,
        shot["ending"],
    ] if part).replace("  ", " ")

    references = [{
        "alias": asset["alias"],
        "type": asset["type"],
        "description": asset["description"],
        "image_asset_ids": asset["image_asset_ids"],
        "video_asset_id": None,
        "video_use_audio": False,
        "audio_asset_id": asset["audio_asset_id"],
        "voice_mode": asset["voice_mode"],
    } for asset in referenced]
    use_reference_mode = bool(references or shot["storyboard_asset_id"] or prepared_continuation)
    payload: dict[str, Any] = {
        "mode": "r2v" if use_reference_mode else "t2v",
        "prompt_profile": "shortfilm",
        "prompt": narrative,
        "shortfilm_summary": f"A single shot from {project['title']}: {scene['title']} — {shot['title']}.",
        "shortfilm_soundscape": shot["sound"],
        "shortfilm_music": shot["music"],
        "job_name": f"{project['title']}_{scene['title']}_{shot['title']}",
        "aspect_ratio": project["aspect_ratio"],
        "megapixels": project["megapixels"],
        "quality_mode": project["quality_mode"],
        "duration": shot["duration"],
        "seed": shot["seed"],
        "steps": 20,
        "scheduler": "beta" if use_reference_mode else "simple",
        "ref_image_size": "match",
        "motion_profile": "none",
        "motion_intensity": 3,
        "physics_style": "balanced",
        "camera_response": "stable",
        "first_image_asset_id": prepared_continuation if use_reference_mode else None,
        "last_image_asset_id": None,
        "continuation_source_job_id": None,
        "continuation_source_asset_id": None,
        "continuation_merge": False,
        "continuation_audio": "new",
        "references": references,
        "storyboards": [{
            "duration": shot["duration"],
            "description": shot["action"],
            "camera": camera,
            "dialogue": dialogue.strip(),
            "sound": shot["sound"],
            "motion_beats": "",
            "effects": "",
            "image_asset_id": shot["storyboard_asset_id"],
            "guide_mode": "reference",
        }] if shot["storyboard_asset_id"] else [],
    }
    return payload, warnings


class ShortFilmStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "projects.json"
        self.projects: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            try:
                project = normalize_project(item)
            except ShortFilmError:
                continue
            self.projects[project["id"]] = project

    def _persist(self) -> None:
        temp_path = self.index_path.with_suffix(".tmp")
        ordered = sorted(self.projects.values(), key=lambda item: item.get("updated_at", ""), reverse=True)
        temp_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.index_path)

    def list(self) -> list[dict[str, Any]]:
        return sorted((deepcopy(item) for item in self.projects.values()), key=lambda item: item["updated_at"], reverse=True)

    def get(self, project_id: str) -> dict[str, Any]:
        if project_id not in self.projects:
            raise ShortFilmError("找不到短片專案。")
        return deepcopy(self.projects[project_id])

    def create(self, raw: Any) -> dict[str, Any]:
        project = normalize_project(raw if isinstance(raw, dict) else {})
        while project["id"] in self.projects:
            project["id"] = uuid.uuid4().hex
        self.projects[project["id"]] = project
        self._persist()
        return deepcopy(project)

    def update(self, project_id: str, raw: Any) -> dict[str, Any]:
        if project_id not in self.projects:
            raise ShortFilmError("找不到短片專案。")
        project = normalize_project(raw, existing_id=project_id)
        project["created_at"] = self.projects[project_id].get("created_at", project["created_at"])
        self.projects[project_id] = project
        self._persist()
        return deepcopy(project)

    def delete(self, project_id: str) -> dict[str, Any]:
        if project_id not in self.projects:
            raise ShortFilmError("找不到短片專案。")
        removed = self.projects.pop(project_id)
        self._persist()
        return {"deleted": True, "id": removed["id"]}
