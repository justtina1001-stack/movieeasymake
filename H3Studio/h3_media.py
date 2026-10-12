from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any, Callable

import av
from PIL import Image

from domain import FPS, RequestError, aligned_frame_count


def reference_video_ids(item: dict[str, Any]) -> list[str]:
    values = [str(value) for value in (item.get("video_asset_ids") or []) if value]
    single = str(item.get("video_asset_id") or "").strip()
    if single and single not in values:
        values.append(single)
    return values


def video_info(assets: Any, asset_id: str, probe: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    path = assets.path_for(asset_id)
    stat = path.stat()
    signature = [stat.st_size, stat.st_mtime_ns]
    metadata = assets.metadata(asset_id)
    info = metadata.get("video_info")
    if not isinstance(info, dict) or metadata.get("video_info_signature") != signature:
        info = probe(path)
        assets.update_metadata(asset_id, video_info=info, video_info_signature=signature)
    return info


def normalized_reference_video(
    assets: Any,
    asset_id: str,
    max_frames: int,
    probe: Callable[[Path], dict[str, Any]],
    normalize: Callable[[Path, Path, int], None],
) -> dict[str, Any]:
    with assets.media_lock:
        return _normalized_reference_video(assets, asset_id, max_frames, probe, normalize)


def _normalized_reference_video(assets, asset_id, max_frames, probe, normalize) -> dict[str, Any]:
    source_path = assets.path_for(asset_id)
    info = video_info(assets, asset_id, probe)
    source_frames = max(5, math.ceil(float(info.get("duration_exact", info["duration"])) * FPS))
    source_frames += (5 - source_frames) % 17
    frame_count = min(max_frames, source_frames)
    stat = source_path.stat()
    cache_key = json.dumps(["h3-reference-24fps-pcm-v2", asset_id, stat.st_size, stat.st_mtime_ns, frame_count])
    prepared_id = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:32]
    prepared_path = assets.directory / f"{prepared_id}.mov"
    metadata_path = assets.directory / f"{prepared_id}.json"
    try:
        cached = assets.metadata(prepared_id) if prepared_path.exists() and metadata_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        cached = {}
    prepared_stat = prepared_path.stat() if prepared_path.exists() else None
    prepared_signature = [prepared_stat.st_size, prepared_stat.st_mtime_ns] if prepared_stat else None
    if (not prepared_path.exists() or cached.get("frame_count") != frame_count
            or cached.get("cache_key") != cache_key or cached.get("prepared_signature") != prepared_signature):
        temporary = assets.directory / f"{prepared_id}.{uuid.uuid4().hex}.part.mov"
        try:
            normalize(source_path, temporary, frame_count)
            temporary.replace(prepared_path)
        finally:
            temporary.unlink(missing_ok=True)
        assets.register_derived_video(prepared_id, "h3-reference-24fps.mov", "h3-reference-video", {
            "source_asset_id": asset_id,
            "cache_key": cache_key,
            "frame_count": frame_count,
            "duration": round(frame_count / FPS, 3),
            "fps": FPS,
            "has_audio": bool(info["has_audio"]),
            "prepared_signature": [prepared_path.stat().st_size, prepared_path.stat().st_mtime_ns],
        }, extension=".mov")
    return {"asset_id": prepared_id, "path": str(prepared_path), "frames": frame_count,
            "duration": round(frame_count / FPS, 3), "source": info}


def prepare_media_payload(
    payload: dict[str, Any],
    assets: Any,
    probe: Callable[[Path], dict[str, Any]],
    normalize: Callable[[Path, Path, int], None],
) -> dict[str, Any]:
    """Inspect real streams before assigning labels; preserve source IDs in recipes."""
    if not isinstance(payload, dict):
        raise RequestError("生成設定格式錯誤。")
    prepared = copy.deepcopy(payload)
    warnings: list[str] = []
    mode = prepared.get("mode") or "t2v"
    references = (prepared.get("references") or []) if mode in {"r2v", "replace", "popup_panel", "mg_animation"} else []
    image_ids = [prepared.get("first_image_asset_id"), prepared.get("last_image_asset_id")]
    image_ids.extend(shot.get("image_asset_id") for shot in prepared.get("storyboards") or [] if isinstance(shot, dict))
    if mode == "replace":
        image_ids.extend(prepared.get("replacement_source_image_asset_ids") or [])
    videos: dict[str, dict[str, Any]] = {}
    for item in references:
        if not isinstance(item, dict):
            raise RequestError("參考素材格式錯誤。")
        ids = reference_video_ids(item)
        flags = item.get("video_audio_flags")
        enabled = []
        for index, asset_id in enumerate(ids):
            info = video_info(assets, asset_id, probe)
            videos[asset_id] = info
            requested = bool(flags[index]) if isinstance(flags, list) and index < len(flags) else bool(item.get("video_use_audio"))
            enabled.append(requested and bool(info["has_audio"]))
            if requested and not info["has_audio"]:
                warnings.append(f"「{item.get('alias') or '參考素材'}」的影片沒有音軌，已關閉原聲參考。")
        if ids:
            # A single reference card normally has one video. Per-video flags also
            # keep imported multi-video recipes aligned with the actual streams.
            item["video_audio_flags"] = enabled
            item["video_use_audio"] = any(enabled)
        image_ids.extend(item.get("image_asset_ids") or [])
        audio_id = item.get("audio_asset_id")
        if audio_id:
            try:
                with av.open(str(assets.path_for(str(audio_id)))) as container:
                    if not container.streams.audio:
                        raise RequestError("聲音參考沒有音軌，請重新上傳聲音檔案。")
            except (av.error.FFmpegError, OSError, ValueError) as error:
                if isinstance(error, RequestError):
                    raise
                raise RequestError("聲音參考無法解碼，請重新上傳。") from error

    for asset_id in dict.fromkeys(value for value in image_ids if value):
        try:
            with Image.open(assets.path_for(str(asset_id))) as image:
                image.verify()
        except (OSError, ValueError) as error:
            raise RequestError("參考圖片無法解碼，請重新上傳。") from error

    source_id = reference_video_ids(references[0])[0] if mode == "replace" and references and reference_video_ids(references[0]) else None
    source_info = videos.get(source_id) if source_id else None
    batch = bool(mode == "replace" and prepared.get("replacement_auto_split", True)
                 and source_info and float(source_info["duration"]) > 15)
    if mode == "replace":
        audio_mode = str(prepared.get("replacement_audio_mode") or "original")
        if audio_mode not in {"original", "generated", "mute"}:
            raise RequestError("角色替換的聲音處理選項錯誤。")
        prepared["replacement_audio_mode"] = audio_mode
        if source_info and prepared.get("replacement_auto_split", True):
            prepared["duration"] = min(15.0, float(source_info["duration"]))

    try:
        duration = min(15.0, max(5.0, float(prepared.get("duration") or 5)))
    except (ValueError, TypeError) as error:
        raise RequestError("影片時長格式錯誤。") from error
    target_frames = aligned_frame_count(duration)
    normalized: dict[str, dict[str, Any]] = {}
    if not batch:
        for asset_id, info in videos.items():
            value = normalized_reference_video(assets, asset_id, target_frames, probe, normalize)
            normalized[asset_id] = {key: value[key] for key in ("asset_id", "frames", "duration")}
            if float(info["duration"]) > target_frames / FPS + 1 / FPS:
                warnings.append(f"參考影片長於生成時長，使用前 {target_frames / FPS:.2f} 秒；影像與原聲已同步裁切。")
    prepared["media_preflight"] = {"videos": videos, "normalized_videos": normalized,
                                  "warnings": list(dict.fromkeys(warnings)),
                                  "replacement_source_asset_id": source_id}
    previous_media_warnings = (payload.get("media_preflight") or {}).get("warnings") or []
    other_warnings = [value for value in prepared.get("h3_preparation_warnings") or []
                      if value not in previous_media_warnings]
    prepared["h3_preparation_warnings"] = list(dict.fromkeys([*other_warnings, *warnings]))
    return prepared
