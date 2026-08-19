from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import mimetypes
from fractions import Fraction
import re
import uuid
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import av
import numpy as np
from aiohttp import web
from PIL import Image, ImageOps

from comfy_client import ComfyClient
from domain import CompiledRequest, RequestError, build_workflow, compile_request, compute_dimensions, required_asset_ids
from engine_installer import EngineInstaller, InstallerError, installer_preflight, resolve_install_target
from music3 import (
    Music3Error,
    Music3Installer,
    MusicJobManager,
    clean_name as clean_music_name,
    filename_stem as music_filename_stem,
)
from shared_gateway import GatewayError, SharedComfyGateway
from settings import ConnectionSettings, SettingsError, SettingsStore


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
ASSET_DIR = DATA_DIR / "assets"
JOB_DIR = DATA_DIR / "jobs"
OUTPUT_DIR = DATA_DIR / "outputs"
CONFIG_PATH = APP_DIR / "config.json"
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_JOB_NAME_LENGTH = 80
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp",
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".mp4", ".mov", ".webm", ".mkv", ".avi",
}
TRANSPARENCY_EXTENSIONS = {".png", ".webp", ".bmp"}
VIDEO_FPS = 24
REPLACEMENT_MAX_INPUT_FRAMES = 15 * VIDEO_FPS
REPLACEMENT_MAX_CORE_FRAMES = 14 * VIDEO_FPS
REPLACEMENT_MIN_CORE_FRAMES = 5 * VIDEO_FPS
REPLACEMENT_OVERLAP_FRAMES = VIDEO_FPS // 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def output_timestamp(value: datetime | None = None) -> str:
    moment = value or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d_%H-%M-%S_%f")


def output_filename_stem(job_name: Any, value: datetime | None = None) -> str:
    stem = INVALID_FILENAME_CHARS.sub("_", clean_job_name(job_name)).rstrip(". ")
    if not stem:
        return output_timestamp(value)
    if stem.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return stem


def clean_job_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if len(name) > MAX_JOB_NAME_LENGTH:
        raise RequestError(f"任務名稱最多 {MAX_JOB_NAME_LENGTH} 個字。")
    return name


def paginate_job_records(records: list[dict[str, Any]], page: int, page_size: int, query: str = "") -> dict[str, Any]:
    needle = query.strip().casefold()
    if needle:
        records = [
            job for job in records
            if needle in str(job.get("name") or "").casefold()
            or needle in str(job.get("id") or "").casefold()
            or needle in str(job.get("mode") or "").casefold()
        ]
    total = len(records)
    total_pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    return {
        "items": records[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def sort_job_records(records: Any) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda job: (bool(job.get("favorite")), str(job.get("created_at") or "")),
        reverse=True,
    )


def history_execution_timing(history: dict[str, Any]) -> dict[str, Any]:
    messages = (history.get("status") or {}).get("messages") or []
    started_ms: float | None = None
    finished_ms: float | None = None
    for message in messages:
        if not isinstance(message, list) or len(message) < 2 or not isinstance(message[1], dict):
            continue
        event, data = message[0], message[1]
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            continue
        if event == "execution_start":
            started_ms = float(timestamp)
        elif event in {"execution_success", "execution_error", "execution_interrupted"}:
            finished_ms = float(timestamp)
    if started_ms is None or finished_ms is None or finished_ms < started_ms:
        return {}
    return {
        "generation_started_at": datetime.fromtimestamp(started_ms / 1000, timezone.utc).isoformat(),
        "finished_at": datetime.fromtimestamp(finished_ms / 1000, timezone.utc).isoformat(),
        "execution_seconds": round((finished_ms - started_ms) / 1000, 3),
    }


def probe_video(path: Path) -> dict[str, Any]:
    """Read stable source metadata without changing the uploaded video."""
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise RequestError("選擇的檔案沒有影片軌。")
            stream = container.streams.video[0]
            rate = float(stream.average_rate or stream.base_rate or VIDEO_FPS)
            duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
            if duration <= 0 and container.duration:
                duration = float(container.duration / av.time_base)
            if duration <= 0:
                last_time = 0.0
                decoded = 0
                for frame in container.decode(video=0):
                    last_time = float(frame.time) if frame.time is not None else decoded / rate
                    decoded += 1
                duration = last_time + (1 / rate if decoded else 0)
            if duration <= 0:
                raise RequestError("無法讀取影片時長。")
            return {
                "width": int(stream.width),
                "height": int(stream.height),
                "fps": round(rate, 3),
                "duration": round(duration, 3),
                "target_frames": max(1, round(duration * VIDEO_FPS)),
                "has_audio": bool(container.streams.audio),
            }
    except RequestError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as error:
        raise RequestError(f"無法分析影片：{error}") from error


def analyze_video_cut_scores(path: Path, sample_frames: int = 6) -> list[tuple[int, float]]:
    """Return inexpensive frame-difference scores at 24 fps timeline positions."""
    scores: list[tuple[int, float]] = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                return scores
            stream = container.streams.video[0]
            source_rate = float(stream.average_rate or stream.base_rate or VIDEO_FPS)
            previous: np.ndarray | None = None
            previous_sample = -sample_frames
            decoded = 0
            for frame in container.decode(video=0):
                seconds = float(frame.time) if frame.time is not None else decoded / source_rate
                decoded += 1
                target_frame = max(0, round(seconds * VIDEO_FPS))
                if target_frame - previous_sample < sample_frames:
                    continue
                gray = frame.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.int16)
                if previous is not None:
                    scores.append((target_frame, float(np.abs(gray - previous).mean())))
                previous = gray
                previous_sample = target_frame
    except (av.error.FFmpegError, OSError, ValueError):
        return []
    return scores


def replacement_segment_plan(
    duration: float,
    cut_scores: list[tuple[int, float]] | None = None,
    smart: bool = True,
) -> list[dict[str, Any]]:
    """Plan 5-15 second Ref2VA inputs with half-second continuity overlaps."""
    total_frames = max(1, round(float(duration) * VIDEO_FPS))
    if total_frames <= REPLACEMENT_MAX_INPUT_FRAMES:
        return [{
            "index": 1,
            "core_start_frame": 0,
            "core_end_frame": total_frames,
            "input_start_frame": 0,
            "input_end_frame": total_frames,
            "core_start": 0.0,
            "core_end": round(total_frames / VIDEO_FPS, 3),
            "input_start": 0.0,
            "input_end": round(total_frames / VIDEO_FPS, 3),
            "input_duration": round(total_frames / VIDEO_FPS, 3),
            "cut_reason": "single",
        }]

    segment_count = max(2, math.ceil(total_frames / REPLACEMENT_MAX_CORE_FRAMES))
    scores = cut_scores or []
    boundaries = [0]
    reasons: list[str] = []
    for boundary_index in range(1, segment_count):
        previous = boundaries[-1]
        remaining = segment_count - boundary_index
        nominal = round(total_frames * boundary_index / segment_count)
        allowed_min = max(
            previous + REPLACEMENT_MIN_CORE_FRAMES,
            total_frames - remaining * REPLACEMENT_MAX_CORE_FRAMES,
        )
        allowed_max = min(
            previous + REPLACEMENT_MAX_CORE_FRAMES,
            total_frames - remaining * REPLACEMENT_MIN_CORE_FRAMES,
        )
        chosen = min(max(nominal, allowed_min), allowed_max)
        reason = "balanced"
        if smart and scores:
            window = [
                (frame, score) for frame, score in scores
                if allowed_min <= frame <= allowed_max and abs(frame - nominal) <= 36
            ]
            if window:
                values = np.asarray([score for _, score in window], dtype=np.float32)
                median = float(np.median(values))
                high_frame, high_score = max(window, key=lambda item: item[1])
                if high_score >= max(18.0, median * 3.5):
                    chosen, reason = high_frame, "scene_cut"
                else:
                    chosen, _ = min(window, key=lambda item: (item[1], abs(item[0] - nominal)))
                    reason = "low_motion"
        boundaries.append(int(chosen))
        reasons.append(reason)
    boundaries.append(total_frames)

    segments: list[dict[str, Any]] = []
    for index, (core_start, core_end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        input_start = max(0, core_start - REPLACEMENT_OVERLAP_FRAMES)
        input_end = min(total_frames, core_end + REPLACEMENT_OVERLAP_FRAMES)
        if input_end - input_start > REPLACEMENT_MAX_INPUT_FRAMES:
            overflow = input_end - input_start - REPLACEMENT_MAX_INPUT_FRAMES
            trim_left = min(core_start - input_start, math.ceil(overflow / 2))
            input_start += trim_left
            input_end -= overflow - trim_left
        segments.append({
            "index": index,
            "core_start_frame": core_start,
            "core_end_frame": core_end,
            "input_start_frame": input_start,
            "input_end_frame": input_end,
            "core_start": round(core_start / VIDEO_FPS, 3),
            "core_end": round(core_end / VIDEO_FPS, 3),
            "input_start": round(input_start / VIDEO_FPS, 3),
            "input_end": round(input_end / VIDEO_FPS, 3),
            "input_duration": round((input_end - input_start) / VIDEO_FPS, 3),
            "cut_reason": "start" if index == 1 else reasons[index - 2],
        })
    return segments


def request_asset_ids(value: Any, key: str = "") -> set[str]:
    found: set[str] = set()
    if key.endswith("_asset_id") and isinstance(value, str) and SAFE_ID.fullmatch(value):
        found.add(value)
    elif key.endswith("_asset_ids") and isinstance(value, list):
        found.update(item for item in value if isinstance(item, str) and SAFE_ID.fullmatch(item))
    elif isinstance(value, dict):
        for child_key, child in value.items():
            found.update(request_asset_ids(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            found.update(request_asset_ids(child, key))
    return found


def replacement_source_asset_id(payload: dict[str, Any]) -> str:
    references = payload.get("references") or []
    if len(references) != 1:
        raise RequestError("角色替換模式需要一支原始表演影片。")
    reference = references[0]
    asset_id = str(reference.get("video_asset_id") or "").strip()
    if not asset_id:
        values = reference.get("video_asset_ids") or []
        asset_id = str(values[0] if values else "").strip()
    if not SAFE_ID.fullmatch(asset_id):
        raise RequestError("角色替換模式需要一支原始表演影片。")
    return asset_id


def flatten_transparent_image(path: Path) -> bool:
    if path.suffix.lower() not in TRANSPARENCY_EXTENSIONS:
        return False
    with Image.open(path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    if source.getchannel("A").getextrema()[0] == 255:
        return False
    background = Image.new("RGBA", source.size, (0, 255, 0, 255))
    background.alpha_composite(source)
    flattened = background.convert("RGB")
    image_format = {".png": "PNG", ".webp": "WEBP", ".bmp": "BMP"}[path.suffix.lower()]
    save_options = {"lossless": True, "quality": 100} if image_format == "WEBP" else {}
    flattened.save(path, format=image_format, **save_options)
    return True


def choose_symbol_canvas(source_width: int, source_height: int) -> dict[str, Any]:
    if source_width <= 0 or source_height <= 0:
        raise RequestError("圖片尺寸無效。")
    source_ratio = source_width / source_height
    candidates: list[dict[str, Any]] = []
    for aspect_ratio in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
        for megapixels in (0.4, 0.7, 0.9, 0.98):
            width, height = compute_dimensions(aspect_ratio, megapixels)
            scale = min(1.0, width / source_width, height / source_height)
            contains_original = width >= source_width and height >= source_height
            aspect_error = abs(math.log((width / height) / source_ratio))
            padding_ratio = 1 - min(1.0, (source_width * source_height) / (width * height))
            if contains_original:
                score = (0, aspect_error, padding_ratio, width * height)
            else:
                score = (1, -scale, aspect_error, -(width * height))
            candidates.append({
                "aspect_ratio": aspect_ratio,
                "megapixels": megapixels,
                "width": width,
                "height": height,
                "scale": scale,
                "contains_original": contains_original,
                "score": score,
            })
    return min(candidates, key=lambda candidate: candidate["score"])


def prepare_symbol_canvas(path: Path, assets: "AssetStore") -> dict[str, Any]:
    with Image.open(path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    transparency_filled = source.getchannel("A").getextrema()[0] < 255
    if transparency_filled:
        background = Image.new("RGBA", source.size, (0, 255, 0, 255))
        background.alpha_composite(source)
        source = background
    source_width, source_height = source.size
    choice = choose_symbol_canvas(source_width, source_height)
    scale = choice["scale"]
    if scale < 1:
        content_width = max(1, round(source_width * scale))
        content_height = max(1, round(source_height * scale))
        content = source.resize((content_width, content_height), Image.Resampling.LANCZOS)
    else:
        content = source
        content_width, content_height = source_width, source_height

    pixels = np.asarray(content)
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]), axis=0)
    if float(np.median(border[:, 3])) < 16:
        fill = (0, 0, 0, 0)
    else:
        color = np.median(border[:, :3], axis=0).astype(np.uint8)
        fill = (int(color[0]), int(color[1]), int(color[2]), 255)
    canvas = Image.new("RGBA", (choice["width"], choice["height"]), fill)
    left = (choice["width"] - content_width) // 2
    top = (choice["height"] - content_height) // 2
    canvas.alpha_composite(content, (left, top))
    metadata = assets.save_image(canvas, f"{path.stem}_symbol_canvas.png", "symbol-loop-canvas")
    metadata["url"] = f"/api/assets/{metadata['id']}"
    return {
        "prepared_asset": metadata,
        "source_width": source_width,
        "source_height": source_height,
        "content_width": content_width,
        "content_height": content_height,
        "canvas_width": choice["width"],
        "canvas_height": choice["height"],
        "aspect_ratio": choice["aspect_ratio"],
        "megapixels": choice["megapixels"],
        "scale": round(scale, 6),
        "pixel_size_preserved": scale == 1.0,
        "padding": {
            "left": left,
            "top": top,
            "right": choice["width"] - content_width - left,
            "bottom": choice["height"] - content_height - top,
        },
        "fill_rgba": fill,
        "transparency_filled": transparency_filled,
    }


def prepare_keyframe_canvas(
    path: Path,
    assets: "AssetStore",
    width: int,
    height: int,
    fit_mode: str = "contain",
) -> dict[str, Any]:
    if fit_mode not in {"contain", "cover", "stretch"}:
        raise RequestError("不支援的首尾圖片適配方式。")
    with Image.open(path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    transparency_filled = source.getchannel("A").getextrema()[0] < 255
    if transparency_filled:
        background = Image.new("RGBA", source.size, (0, 255, 0, 255))
        background.alpha_composite(source)
        source = background
    source_width, source_height = source.size

    if fit_mode == "stretch":
        canvas = source.resize((width, height), Image.Resampling.LANCZOS)
        content_width, content_height = width, height
        padding = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    elif fit_mode == "cover":
        canvas = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        scale = max(width / source_width, height / source_height)
        content_width = round(source_width * scale)
        content_height = round(source_height * scale)
        padding = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    else:
        content = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS)
        content_width, content_height = content.size
        pixels = np.asarray(source)
        border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]), axis=0)
        opaque_border = border[border[:, 3] >= 16]
        if not len(opaque_border):
            opaque_border = pixels.reshape(-1, 4)[pixels.reshape(-1, 4)[:, 3] >= 16]
        if len(opaque_border):
            color = np.median(opaque_border[:, :3], axis=0).astype(np.uint8)
            fill = (int(color[0]), int(color[1]), int(color[2]), 255)
        else:
            fill = (0, 0, 0, 255)
        canvas = Image.new("RGBA", (width, height), fill)
        left = (width - content_width) // 2
        top = (height - content_height) // 2
        canvas.alpha_composite(content, (left, top))
        padding = {
            "left": left,
            "top": top,
            "right": width - content_width - left,
            "bottom": height - content_height - top,
        }

    metadata = assets.save_image(canvas, f"{path.stem}_{fit_mode}_{width}x{height}.png", "keyframe-canvas")
    metadata.update({
        "url": f"/api/assets/{metadata['id']}",
        "source_width": source_width,
        "source_height": source_height,
        "width": width,
        "height": height,
        "fit_mode": fit_mode,
        "transparency_filled": transparency_filled,
        "background_mode": "chroma_green",
    })
    return {
        "prepared_asset": metadata,
        "source_width": source_width,
        "source_height": source_height,
        "width": width,
        "height": height,
        "fit_mode": fit_mode,
        "transparency_filled": transparency_filled,
        "background_mode": "chroma_green",
        "content_width": content_width,
        "content_height": content_height,
        "padding": padding,
    }


def _encode_video_frame(output: Any, stream: Any, frame: Any, frame_index: int) -> None:
    frame = frame.reformat(width=stream.width, height=stream.height, format="yuv420p")
    frame.pts = frame_index
    frame.time_base = Fraction(1, 24)
    for packet in stream.encode(frame):
        output.mux(packet)


def _append_video(output: Any, stream: Any, path: Path, skip_frames: int = 0, start_frame: int = 0) -> int:
    written = 0
    produced = 0
    with av.open(str(path)) as source:
        video_stream = source.streams.video[0]
        source_rate = float(video_stream.average_rate or 24)
        previous = None
        previous_time = 0.0
        decoded = 0
        for frame in source.decode(video=0):
            frame_time = float(frame.time) if frame.time is not None else decoded / source_rate
            decoded += 1
            if previous is not None:
                while produced / 24 < frame_time:
                    if produced >= skip_frames:
                        _encode_video_frame(output, stream, previous, start_frame + written)
                        written += 1
                    produced += 1
            previous = frame
            previous_time = frame_time
        if previous is None:
            raise RuntimeError(f"影片沒有可解碼的畫面：{path.name}")
        duration = float(video_stream.duration * video_stream.time_base) if video_stream.duration else previous_time + 1 / source_rate
        while produced / 24 < duration:
            if produced >= skip_frames:
                _encode_video_frame(output, stream, previous, start_frame + written)
                written += 1
            produced += 1
    return written


def _append_video_range(
    output: Any,
    stream: Any,
    path: Path,
    range_start_frame: int,
    range_end_frame: int,
    start_frame: int = 0,
) -> int:
    """Append an exact 24 fps timeline range, resampling the source when needed."""
    range_start_frame = max(0, int(range_start_frame))
    range_end_frame = max(range_start_frame, int(range_end_frame))
    written = 0
    produced = 0
    with av.open(str(path)) as source:
        if not source.streams.video:
            raise RuntimeError(f"影片沒有可解碼的畫面：{path.name}")
        video_stream = source.streams.video[0]
        source_rate = float(video_stream.average_rate or video_stream.base_rate or VIDEO_FPS)
        previous = None
        previous_time = 0.0
        decoded = 0

        def emit(frame: Any) -> None:
            nonlocal written
            if range_start_frame <= produced < range_end_frame:
                _encode_video_frame(output, stream, frame, start_frame + written)
                written += 1

        for frame in source.decode(video=0):
            frame_time = float(frame.time) if frame.time is not None else decoded / source_rate
            decoded += 1
            if previous is not None:
                while produced / VIDEO_FPS < frame_time and produced < range_end_frame:
                    emit(previous)
                    produced += 1
            previous = frame
            previous_time = frame_time
            if produced >= range_end_frame:
                break
        if previous is None:
            raise RuntimeError(f"影片沒有可解碼的畫面：{path.name}")
        duration = float(video_stream.duration * video_stream.time_base) if video_stream.duration else previous_time + 1 / source_rate
        while produced / VIDEO_FPS < duration and produced < range_end_frame:
            emit(previous)
            produced += 1
    return written


def _audio_arrays(path: Path, skip_samples: int, target_samples: int):
    remaining = target_samples
    skipped = 0
    with av.open(str(path)) as source:
        if not source.streams.audio:
            return
        audio_stream = source.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
        for frame in source.decode(audio=0):
            for converted in resampler.resample(frame):
                values = converted.to_ndarray()
                if skipped < skip_samples:
                    remove = min(skip_samples - skipped, values.shape[1])
                    values = values[:, remove:]
                    skipped += remove
                if remaining <= 0:
                    return
                if not values.shape[1]:
                    continue
                values = values[:, :remaining]
                remaining -= values.shape[1]
                yield values
        for converted in resampler.resample(None):
            values = converted.to_ndarray()
            if skipped < skip_samples:
                remove = min(skip_samples - skipped, values.shape[1])
                values = values[:, remove:]
                skipped += remove
            if values.shape[1] and remaining > 0:
                values = values[:, :remaining]
                remaining -= values.shape[1]
                yield values


def _path_has_audio(path: Path) -> bool:
    try:
        with av.open(str(path)) as source:
            return bool(source.streams.audio)
    except (av.error.FFmpegError, OSError, ValueError):
        return False


def _padded_audio_arrays(path: Path, skip_samples: int, target_samples: int):
    written = 0
    for values in _audio_arrays(path, skip_samples, target_samples):
        values = values[:, :target_samples - written]
        if values.shape[1]:
            written += values.shape[1]
            yield values
        if written >= target_samples:
            return
    while written < target_samples:
        size = min(4096, target_samples - written)
        written += size
        yield np.zeros((2, size), dtype=np.float32)


def _write_audio_values(output: Any, audio: Any, chunks: Any, target_samples: int) -> None:
    cursor = 0
    written = 0

    def write(values: np.ndarray) -> None:
        nonlocal cursor, written
        frame = av.AudioFrame.from_ndarray(values.astype(np.float32, copy=False), format="fltp", layout="stereo")
        frame.sample_rate = 48000
        frame.pts = cursor
        frame.time_base = Fraction(1, 48000)
        cursor += frame.samples
        written += frame.samples
        for packet in audio.encode(frame):
            output.mux(packet)

    for values in chunks:
        if written >= target_samples:
            break
        values = values[:, :target_samples - written]
        if values.shape[1]:
            write(values)
    while written < target_samples:
        size = min(4096, target_samples - written)
        write(np.zeros((2, size), dtype=np.float32))


def extract_replacement_segment(
    source_path: Path,
    output_path: Path,
    start_frame: int,
    end_frame: int,
    include_audio: bool,
) -> float:
    """Create an exact 24 fps reference segment for one H3 replacement child job."""
    info = probe_video(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(output_path), mode="w") as output:
        video = output.add_stream("libx264", rate=VIDEO_FPS)
        video.width = info["width"]
        video.height = info["height"]
        video.pix_fmt = "yuv420p"
        video.options = {"crf": "18", "preset": "fast"}
        audio = None
        if include_audio and info["has_audio"]:
            audio = output.add_stream("aac", rate=48000)
            audio.layout = "stereo"
            audio.bit_rate = 192000
        written = _append_video_range(output, video, source_path, start_frame, end_frame)
        for packet in video.encode():
            output.mux(packet)
        if audio is not None:
            skip_samples = start_frame * 2000
            target_samples = written * 2000
            _write_audio_values(
                output,
                audio,
                _audio_arrays(source_path, skip_samples, target_samples),
                target_samples,
            )
            for packet in audio.encode():
                output.mux(packet)
    return written / VIDEO_FPS


def merge_replacement_segments(
    source_path: Path,
    segment_paths: list[Path],
    segments: list[dict[str, Any]],
    output_path: Path,
    width: int,
    height: int,
    audio_mode: str,
) -> float:
    """Trim overlap/padding, concatenate children, and restore the requested soundtrack."""
    if len(segment_paths) != len(segments) or not segments:
        raise RuntimeError("角色替換片段不完整，無法合併。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(output_path), mode="w") as output:
        video = output.add_stream("libx264", rate=VIDEO_FPS)
        video.width = width
        video.height = height
        video.pix_fmt = "yuv420p"
        video.options = {"crf": "20", "preset": "fast"}
        has_original_audio = audio_mode == "original" and _path_has_audio(source_path)
        has_generated_audio = audio_mode == "generated" and any(_path_has_audio(path) for path in segment_paths)
        audio = None
        if has_original_audio or has_generated_audio:
            audio = output.add_stream("aac", rate=48000)
            audio.layout = "stereo"
            audio.bit_rate = 192000

        total_frames = 0
        for path, segment in zip(segment_paths, segments):
            trim_start = int(segment["core_start_frame"]) - int(segment["input_start_frame"])
            core_frames = int(segment["core_end_frame"]) - int(segment["core_start_frame"])
            total_frames += _append_video_range(
                output,
                video,
                path,
                trim_start,
                trim_start + core_frames,
                total_frames,
            )
        for packet in video.encode():
            output.mux(packet)

        if audio is not None and has_original_audio:
            target_samples = total_frames * 2000
            _write_audio_values(output, audio, _audio_arrays(source_path, 0, target_samples), target_samples)
        elif audio is not None and has_generated_audio:
            def generated_chunks():
                for path, segment in zip(segment_paths, segments):
                    trim_start = int(segment["core_start_frame"]) - int(segment["input_start_frame"])
                    core_frames = int(segment["core_end_frame"]) - int(segment["core_start_frame"])
                    yield from _padded_audio_arrays(path, trim_start * 2000, core_frames * 2000)

            _write_audio_values(output, audio, generated_chunks(), total_frames * 2000)
        if audio is not None:
            for packet in audio.encode():
                output.mux(packet)
    return total_frames / VIDEO_FPS


def merge_continuation(source_path: Path, continuation_path: Path, output_path: Path, width: int, height: int, audio_mode: str) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(output_path), mode="w") as output:
        video = output.add_stream("libx264", rate=24)
        video.width = width
        video.height = height
        video.pix_fmt = "yuv420p"
        video.options = {"crf": "20", "preset": "fast"}
        audio = None
        if audio_mode != "mute":
            audio = output.add_stream("aac", rate=48000)
            audio.layout = "stereo"
            audio.bit_rate = 192000
        source_frames = _append_video(output, video, source_path)
        continuation_frames = _append_video(output, video, continuation_path, skip_frames=1, start_frame=source_frames)
        for packet in video.encode():
            output.mux(packet)

        if audio is not None:
            cursor = 0

            def write(values: np.ndarray) -> None:
                nonlocal cursor
                frame = av.AudioFrame.from_ndarray(values.astype(np.float32, copy=False), format="fltp", layout="stereo")
                frame.sample_rate = 48000
                frame.pts = cursor
                frame.time_base = Fraction(1, 48000)
                cursor += frame.samples
                for packet in audio.encode(frame):
                    output.mux(packet)

            segments = [
                (source_path, 0, source_frames * 2000, audio_mode == "both"),
                (continuation_path, 2000, continuation_frames * 2000, True),
            ]
            for path, skip, samples, keep in segments:
                written = 0
                if keep:
                    for values in _audio_arrays(path, skip, samples):
                        write(values)
                        written += values.shape[1]
                while written < samples:
                    size = min(4096, samples - written)
                    write(np.zeros((2, size), dtype=np.float32))
                    written += size
            for packet in audio.encode():
                output.mux(packet)
    return (source_frames + continuation_frames) / 24


class AssetStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, asset_id: str) -> Path:
        if not SAFE_ID.fullmatch(asset_id):
            raise RequestError("素材識別碼格式錯誤。")
        matches = [path for path in self.directory.glob(f"{asset_id}.*") if path.suffix != ".json"]
        if not matches:
            raise RequestError(f"找不到素材：{asset_id}")
        return matches[0]

    def metadata(self, asset_id: str) -> dict[str, Any]:
        metadata_path = self.directory / f"{asset_id}.json"
        if not metadata_path.exists():
            path = self.path_for(asset_id)
            return {"id": asset_id, "name": path.name, "kind": "unknown", "size": path.stat().st_size}
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def update_metadata(self, asset_id: str, **changes: Any) -> dict[str, Any]:
        metadata = self.metadata(asset_id)
        metadata.update(changes)
        (self.directory / f"{asset_id}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata

    def save_image(self, image: Any, name: str, kind: str) -> dict[str, Any]:
        asset_id = uuid.uuid4().hex
        path = self.directory / f"{asset_id}.png"
        image.save(path, format="PNG")
        metadata = {
            "id": asset_id,
            "name": name,
            "kind": kind,
            "extension": ".png",
            "size": path.stat().st_size,
            "created_at": utc_now(),
        }
        (self.directory / f"{asset_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    def register_derived_video(
        self,
        asset_id: str,
        name: str,
        kind: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self.directory / f"{asset_id}.mp4"
        if not SAFE_ID.fullmatch(asset_id) or not path.exists():
            raise RequestError("衍生影片素材不存在。")
        metadata = {
            "id": asset_id,
            "name": Path(name).name,
            "kind": kind,
            "extension": ".mp4",
            "size": path.stat().st_size,
            "created_at": utc_now(),
            **(extra or {}),
        }
        (self.directory / f"{asset_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    async def save_upload(self, request: web.Request) -> dict[str, Any]:
        reader = await request.multipart()
        kind = "reference"
        original_name = ""
        asset_id = uuid.uuid4().hex
        temp_path = self.directory / f"{asset_id}.upload"
        received_file = False
        with temp_path.open("wb") as handle:
            async for part in reader:
                if part.name == "kind":
                    kind = (await part.text()).strip() or "reference"
                elif part.name == "file":
                    original_name = Path(part.filename or "asset").name
                    extension = Path(original_name).suffix.lower()
                    if extension not in ALLOWED_EXTENSIONS:
                        raise RequestError(f"不支援的素材格式：{extension or '無副檔名'}")
                    received_file = True
                    while chunk := await part.read_chunk(size=1024 * 1024):
                        handle.write(chunk)
        if not received_file:
            temp_path.unlink(missing_ok=True)
            raise RequestError("沒有收到素材檔案。")
        extension = Path(original_name).suffix.lower()
        final_path = self.directory / f"{asset_id}{extension}"
        temp_path.replace(final_path)
        transparency_filled = flatten_transparent_image(final_path)
        metadata = {
            "id": asset_id,
            "name": original_name,
            "kind": kind,
            "extension": extension,
            "size": final_path.stat().st_size,
            "created_at": utc_now(),
            "transparency_filled": transparency_filled,
        }
        (self.directory / f"{asset_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata


def extract_continuation_frame(path: Path, assets: AssetStore) -> dict[str, Any]:
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise RequestError("選擇的檔案沒有影片軌。")
        stream = container.streams.video[0]
        rate = float(stream.average_rate or 24)
        last_frame = None
        frame_count = 0
        last_time = 0.0
        for frame in container.decode(video=0):
            last_frame = frame
            frame_count += 1
            if frame.time is not None:
                last_time = float(frame.time)
        if last_frame is None:
            raise RequestError("影片沒有可擷取的畫面。")
        width, height = last_frame.width, last_frame.height
        duration = float(stream.duration * stream.time_base) if stream.duration else last_time + 1 / rate
        image = last_frame.to_image()
    metadata = assets.save_image(image, f"{path.stem}_last_frame.png", "continuation-frame")
    metadata.update({
        "url": f"/api/assets/{metadata['id']}",
        "width": width,
        "height": height,
        "fps": round(rate, 3),
        "duration": round(duration, 3),
        "frames": frame_count,
    })
    return metadata


class JobManager:
    def __init__(self, assets: AssetStore, comfy: ComfyClient):
        self.assets = assets
        self.comfy = comfy
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.gpu_lock = asyncio.Lock()
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._load_jobs()

    def _load_jobs(self) -> None:
        for path in JOB_DIR.glob("*.json"):
            if path.name.endswith(".workflow.json") or path.name.endswith(".request.json"):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") in {"queued", "preparing", "running"}:
                job["status"] = "interrupted"
                job["error"] = "工具上次關閉時工作尚未完成。"
            job.setdefault("name", "")
            job.setdefault("favorite", False)
            job.setdefault("hidden", False)
            self.jobs[job["id"]] = job

    def _persist(self, job: dict[str, Any]) -> None:
        (JOB_DIR / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(self, job_id: str, **changes: Any) -> None:
        job = self.jobs[job_id]
        job.update(changes)
        job["updated_at"] = utc_now()
        self._persist(job)

    def create(
        self,
        compiled: CompiledRequest,
        raw_request: dict[str, Any],
        *,
        hidden: bool = False,
        parent_job_id: str | None = None,
        segment_index: int | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "name": clean_job_name(raw_request.get("job_name")),
            "favorite": False,
            "hidden": hidden,
            "parent_job_id": parent_job_id,
            "segment_index": segment_index,
            "mode": compiled.mode,
            "status": "queued",
            "progress": 0,
            "current_node": None,
            "error": None,
            "prompt_id": None,
            "output": None,
            "width": compiled.width,
            "height": compiled.height,
            "duration": compiled.actual_duration,
            "continuation_source_job": compiled.continuation_source_job,
            "continuation_merge": compiled.continuation_merge,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.jobs[job_id] = job
        self.cancel_events[job_id] = asyncio.Event()
        self._persist(job)
        (JOB_DIR / f"{job_id}.request.json").write_text(json.dumps(raw_request, ensure_ascii=False, indent=2), encoding="utf-8")
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, compiled))
        return job

    def create_replacement_batch(
        self,
        compiled: CompiledRequest,
        raw_request: dict[str, Any],
        source_info: dict[str, Any],
        segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "name": clean_job_name(raw_request.get("job_name")),
            "favorite": False,
            "hidden": False,
            "mode": "replace",
            "batch_type": "replace_long",
            "status": "queued",
            "progress": 0,
            "current_node": "等待切割長影片",
            "error": None,
            "prompt_id": None,
            "output": None,
            "width": compiled.width,
            "height": compiled.height,
            "duration": source_info["duration"],
            "source_info": source_info,
            "segments": [{**segment, "status": "waiting", "child_job_id": None} for segment in segments],
            "active_child_id": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.jobs[job_id] = job
        self.cancel_events[job_id] = asyncio.Event()
        self._persist(job)
        (JOB_DIR / f"{job_id}.request.json").write_text(json.dumps(raw_request, ensure_ascii=False, indent=2), encoding="utf-8")
        self.tasks[job_id] = asyncio.create_task(self._run_replacement_batch(job_id, raw_request))
        return job

    def local_output_path(self, job: dict[str, Any]) -> Path | None:
        value = str(job.get("local_output") or "").strip()
        if not value:
            return None
        output_root = OUTPUT_DIR.resolve()
        path = (output_root / value).resolve()
        if output_root not in path.parents or not path.exists():
            return None
        return path

    async def cache_output(self, job_id: str, output: dict[str, str], suffix: str | None = None) -> Path:
        content, _ = await self.comfy.fetch_output(output)
        extension = suffix or Path(output["filename"]).suffix or ".mp4"
        path = OUTPUT_DIR / f"{job_id}{extension}"
        await asyncio.to_thread(path.write_bytes, content)
        self.update(job_id, local_output=path.name)
        return path

    async def complete_job(
        self,
        job_id: str,
        compiled: CompiledRequest,
        prompt_id: str,
        output: dict[str, str],
        history: dict[str, Any] | None = None,
    ) -> None:
        continuation_path = await self.cache_output(job_id, output)
        job = self.jobs[job_id]
        output_stem = str(job.get("output_stem") or output_filename_stem(job.get("name")))
        updates: dict[str, Any] = {
            "status": "completed",
            "progress": 100,
            "current_node": None,
            "error": None,
            "prompt_id": prompt_id,
            "output": output,
            **(history_execution_timing(history or {}) or self.finish_timing(job_id)),
        }
        if compiled.mode == "extend" and compiled.continuation_merge:
            self.update(job_id, progress=99, current_node="串接上一段影片")
            try:
                source_path = await self.continuation_source_path(compiled)
                merged_name = f"{output_stem}_extended.mp4"
                merged_path = OUTPUT_DIR / f"{job_id}_extended.mp4"
                merged_duration = await asyncio.to_thread(
                    merge_continuation,
                    source_path,
                    continuation_path,
                    merged_path,
                    compiled.width,
                    compiled.height,
                    compiled.continuation_audio,
                )
                updates["segment_output"] = output
                updates["local_output"] = merged_path.name
                updates["download_name"] = merged_name
                updates["duration"] = merged_duration
            except Exception as merge_error:
                updates["merge_error"] = f"續集片段已完成，但自動串接失敗：{merge_error}"
        self.update(job_id, **updates)

    def finish_timing(self, job_id: str) -> dict[str, Any]:
        finished = datetime.now(timezone.utc)
        updates: dict[str, Any] = {"finished_at": finished.isoformat()}
        started_value = self.jobs[job_id].get("generation_started_at")
        if not started_value:
            return updates
        try:
            started = datetime.fromisoformat(str(started_value))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            updates["execution_seconds"] = round(max(0.0, (finished - started).total_seconds()), 3)
        except ValueError:
            pass
        return updates

    async def backfill_completed_timings(self) -> None:
        candidates = [
            job for job in self.jobs.values()
            if job.get("status") == "completed" and job.get("prompt_id") and job.get("execution_seconds") is None
        ]
        semaphore = asyncio.Semaphore(8)

        async def backfill(job: dict[str, Any]) -> None:
            async with semaphore:
                history = await self.comfy.get_history(str(job["prompt_id"]))
            timing = history_execution_timing(history)
            if timing:
                self.update(str(job["id"]), **timing)

        await asyncio.gather(*(backfill(job) for job in candidates), return_exceptions=True)

    async def reconcile_job(self, job_id: str, wait: bool = False) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.get("prompt_id") or job.get("status") in {"completed", "cancelled"}:
            return False
        prompt_id = str(job["prompt_id"])
        self.cancel_events.setdefault(job_id, asyncio.Event())
        while not self.cancel_events[job_id].is_set():
            history = await self.comfy.get_history(prompt_id)
            state = self.comfy.history_state(history)
            if state == "success":
                output = find_video_output(history)
                if not output:
                    self.update(job_id, status="failed", current_node=None, error="ComfyUI 已完成，但沒有回傳影片檔案。")
                    return False
                try:
                    raw_request = json.loads((JOB_DIR / f"{job_id}.request.json").read_text(encoding="utf-8"))
                    compiled = compile_request(raw_request)
                    await self.complete_job(job_id, compiled, prompt_id, output, history)
                    return True
                except Exception as error:
                    self.update(job_id, status="failed", current_node=None, error=f"影片已生成，但匯入操作面板失敗：{error}")
                    return False
            if state == "error":
                timing = history_execution_timing(history) or self.finish_timing(job_id)
                self.update(job_id, status="failed", current_node=None, error=self.comfy.history_error(history), **timing)
                return False
            if not wait:
                return False
            self.update(job_id, status="running", current_node="已恢復監控，等待 ComfyUI 完成", error=None)
            await asyncio.sleep(10)
        return False

    def resume_recoverable_jobs(self) -> None:
        for job_id, job in self.jobs.items():
            error = str(job.get("error") or "")
            recoverable = job.get("status") == "interrupted" or "進度連線中斷" in error
            if not recoverable or not job.get("prompt_id"):
                continue
            task = self.tasks.get(job_id)
            if task and not task.done():
                continue
            self.cancel_events.setdefault(job_id, asyncio.Event())
            self.tasks[job_id] = asyncio.create_task(self._recover(job_id))
        for job_id, job in self.jobs.items():
            if job.get("batch_type") != "replace_long" or job.get("status") != "interrupted":
                continue
            task = self.tasks.get(job_id)
            if task and not task.done():
                continue
            request_path = JOB_DIR / f"{job_id}.request.json"
            if not request_path.exists():
                self.update(job_id, status="failed", error="找不到長片角色替換的原始設定，無法接續。")
                continue
            try:
                raw_request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self.update(job_id, status="failed", error=f"無法讀取長片角色替換設定：{error}")
                continue
            self.cancel_events[job_id] = asyncio.Event()
            self.tasks[job_id] = asyncio.create_task(self._run_replacement_batch(job_id, raw_request))

    async def _recover(self, job_id: str) -> None:
        try:
            # Recovery only watches an already-submitted ComfyUI prompt and
            # imports its output. Holding the submission lock here lets one
            # stale prompt block every newer job from being reconciled.
            await self.reconcile_job(job_id, wait=True)
        except asyncio.CancelledError:
            self.update(job_id, status="cancelled", current_node=None, error="工作已取消。")
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=f"恢復工作監控失敗：{error}")

    async def continuation_source_path(self, compiled: CompiledRequest) -> Path:
        if compiled.continuation_source_asset:
            return self.assets.path_for(compiled.continuation_source_asset)
        source_job = self.jobs.get(compiled.continuation_source_job or "")
        if not source_job or not source_job.get("output"):
            raise RequestError("找不到要續接的上一段影片。")
        return await self.job_output_path(source_job)

    async def job_output_path(self, source_job: dict[str, Any]) -> Path:
        cached = self.local_output_path(source_job)
        if cached:
            return cached
        try:
            return self.comfy.output_path(source_job["output"])
        except RuntimeError:
            return await self.cache_output(source_job["id"], source_job["output"])

    async def _prepare_replacement_segment_asset(
        self,
        parent_id: str,
        source_path: Path,
        segment: dict[str, Any],
        include_audio: bool,
    ) -> str:
        existing = str(segment.get("source_asset_id") or "")
        if existing:
            try:
                self.assets.path_for(existing)
                return existing
            except RequestError:
                pass
        asset_id = uuid.uuid4().hex
        temporary = self.assets.directory / f"{asset_id}.part.mp4"
        final_path = self.assets.directory / f"{asset_id}.mp4"
        temporary.unlink(missing_ok=True)
        try:
            await asyncio.to_thread(
                extract_replacement_segment,
                source_path,
                temporary,
                int(segment["input_start_frame"]),
                int(segment["input_end_frame"]),
                include_audio,
            )
            temporary.replace(final_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        self.assets.register_derived_video(
            asset_id,
            f"replacement_part_{int(segment['index']):02}.mp4",
            "replacement-segment",
            {
                "parent_job_id": parent_id,
                "segment_index": int(segment["index"]),
                "start": segment["input_start"],
                "end": segment["input_end"],
            },
        )
        return asset_id

    def _replacement_child_payload(
        self,
        raw_request: dict[str, Any],
        parent: dict[str, Any],
        segment: dict[str, Any],
        previous_continuity_asset: str | None,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(raw_request)
        payload["replacement_auto_split"] = False
        payload["duration"] = float(segment["input_duration"])
        payload["job_name"] = f"{parent.get('name') or '角色替換'}_片段{int(segment['index']):02}"
        payload["replacement_batch_segment"] = {
            "index": int(segment["index"]),
            "total": len(parent.get("segments") or []),
            "source_start": segment["input_start"],
            "source_end": segment["input_end"],
            "core_start": segment["core_start"],
            "core_end": segment["core_end"],
        }
        references = payload.get("references") or []
        if references:
            references[0]["video_asset_id"] = segment["source_asset_id"]
            references[0].pop("video_asset_ids", None)
            references[0]["video_use_audio"] = bool(
                references[0].get("video_use_audio") and (parent.get("source_info") or {}).get("has_audio")
            )
            image_ids = list(references[0].get("image_asset_ids") or [])
            if (
                previous_continuity_asset
                and payload.get("replacement_continuity", True)
                and len(image_ids) < 9
                and previous_continuity_asset not in image_ids
            ):
                image_ids.append(previous_continuity_asset)
            references[0]["image_asset_ids"] = image_ids
        return payload

    async def _wait_for_batch_child(
        self,
        parent_id: str,
        segment_position: int,
        child_id: str,
    ) -> dict[str, Any]:
        parent = self.jobs[parent_id]
        total = len(parent.get("segments") or [])
        while True:
            if self.cancel_events[parent_id].is_set():
                await self.cancel(child_id)
                raise asyncio.CancelledError
            child = self.jobs[child_id]
            segment = parent["segments"][segment_position]
            segment["status"] = child.get("status")
            segment["progress"] = child.get("progress", 0)
            segment["current_node"] = child.get("current_node")
            child_progress = float(child.get("progress") or 0) / 100
            progress = round(5 + ((segment_position + child_progress) / max(1, total)) * 88, 1)
            self.update(
                parent_id,
                segments=parent["segments"],
                progress=min(93, progress),
                current_node=f"第 {segment_position + 1}/{total} 段 · {child.get('current_node') or child.get('status')}",
                active_child_id=child_id,
            )
            if child.get("status") in {"completed", "failed", "cancelled"}:
                return child
            task = self.tasks.get(child_id)
            if task and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=1)
                except asyncio.TimeoutError:
                    pass
            elif child.get("status") == "interrupted" and child.get("prompt_id"):
                await self.reconcile_job(child_id, wait=True)
            else:
                await asyncio.sleep(1)

    async def _run_replacement_batch(self, parent_id: str, raw_request: dict[str, Any]) -> None:
        parent = self.jobs[parent_id]
        cancel_event = self.cancel_events.setdefault(parent_id, asyncio.Event())
        try:
            source_asset_id = replacement_source_asset_id(raw_request)
            source_path = self.assets.path_for(source_asset_id)
            references = raw_request.get("references") or []
            include_audio = bool(references and references[0].get("video_use_audio"))
            if not parent.get("generation_started_at"):
                self.update(parent_id, generation_started_at=datetime.now().astimezone().isoformat())
            self.update(parent_id, status="preparing", error=None, progress=1, current_node="分析並切割來源影片")
            segments = parent.get("segments") or []
            previous_continuity_asset: str | None = None
            segment_paths: list[Path] = []

            for position, segment in enumerate(segments):
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                previous = segments[position - 1] if position else None
                previous_continuity_asset = str(previous.get("continuity_asset_id") or "") if previous else None
                child_id = str(segment.get("child_job_id") or "")
                child = self.jobs.get(child_id) if child_id else None

                if not segment.get("source_asset_id"):
                    self.update(
                        parent_id,
                        current_node=f"切割第 {position + 1}/{len(segments)} 段來源影片",
                        progress=round(1 + position / max(1, len(segments)) * 4, 1),
                    )
                    segment["source_asset_id"] = await self._prepare_replacement_segment_asset(
                        parent_id, source_path, segment, include_audio
                    )
                    self.update(parent_id, segments=segments)

                if child and child.get("status") == "completed":
                    try:
                        child_path = await self.job_output_path(child)
                    except Exception:
                        child = None
                    else:
                        segment["status"] = "completed"
                        segment_paths.append(child_path)
                if not child or child.get("status") in {"failed", "cancelled"} or (
                    child.get("status") == "interrupted" and not child.get("prompt_id")
                ):
                    child_payload = self._replacement_child_payload(
                        raw_request, parent, segment, previous_continuity_asset or None
                    )
                    compiled = compile_request(child_payload)
                    child = self.create(
                        compiled,
                        child_payload,
                        hidden=True,
                        parent_job_id=parent_id,
                        segment_index=position + 1,
                    )
                    child_id = child["id"]
                    segment["child_job_id"] = child_id
                    segment["status"] = "queued"
                    self.update(parent_id, segments=segments, active_child_id=child_id)
                    prompt_path = JOB_DIR / f"{parent_id}.prompt.txt"
                    if not prompt_path.exists():
                        prompt_path.write_text(compiled.prompt, encoding="utf-8")

                if child and child.get("status") != "completed":
                    child = await self._wait_for_batch_child(parent_id, position, child["id"])
                    if child.get("status") != "completed":
                        raise RuntimeError(
                            f"第 {position + 1}/{len(segments)} 段角色替換失敗：{child.get('error') or child.get('status')}"
                        )
                    segment_paths.append(await self.job_output_path(child))

                segment["status"] = "completed"
                segment["progress"] = 100
                if raw_request.get("replacement_continuity", True) and position < len(segments) - 1:
                    if not segment.get("continuity_asset_id"):
                        frame = await asyncio.to_thread(
                            extract_continuation_frame,
                            segment_paths[-1],
                            self.assets,
                        )
                        segment["continuity_asset_id"] = frame["id"]
                self.update(parent_id, segments=segments)

            if cancel_event.is_set():
                raise asyncio.CancelledError
            self.update(parent_id, status="preparing", progress=96, current_node="合併替換片段並處理聲音")
            output_stem = output_filename_stem(parent.get("name"))
            final_path = OUTPUT_DIR / f"{parent_id}_replaced.mp4"
            temporary = OUTPUT_DIR / f"{parent_id}_replaced.part.mp4"
            temporary.unlink(missing_ok=True)
            try:
                duration = await asyncio.to_thread(
                    merge_replacement_segments,
                    source_path,
                    segment_paths,
                    segments,
                    temporary,
                    int(parent["width"]),
                    int(parent["height"]),
                    str(raw_request.get("replacement_audio_mode") or "original"),
                )
                if cancel_event.is_set():
                    temporary.unlink(missing_ok=True)
                    raise asyncio.CancelledError
                temporary.replace(final_path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            self.update(
                parent_id,
                status="completed",
                progress=100,
                current_node=None,
                error=None,
                active_child_id=None,
                local_output=final_path.name,
                download_name=f"{output_stem}_角色替換完整影片.mp4",
                output={"filename": final_path.name, "subfolder": "", "type": "local"},
                duration=duration,
                **self.finish_timing(parent_id),
            )
        except asyncio.CancelledError:
            self.update(
                parent_id,
                status="cancelled",
                current_node=None,
                active_child_id=None,
                error="完整角色替換工作已取消；已完成片段會保留，可稍後接續。",
                **self.finish_timing(parent_id),
            )
        except Exception as error:
            self.update(
                parent_id,
                status="failed",
                current_node=None,
                active_child_id=None,
                error=str(error),
                **self.finish_timing(parent_id),
            )

    async def resume_batch(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job or job.get("batch_type") != "replace_long":
            raise RequestError("這筆工作不是可接續的長片角色替換工作。")
        task = self.tasks.get(job_id)
        if task and not task.done():
            raise RequestError("這筆長片工作目前仍在執行。")
        if job.get("status") == "completed":
            raise RequestError("這筆長片工作已經完成。")
        request_path = JOB_DIR / f"{job_id}.request.json"
        if not request_path.exists():
            raise RequestError("找不到這筆長片工作的原始設定。")
        raw_request = json.loads(request_path.read_text(encoding="utf-8"))
        self.cancel_events[job_id] = asyncio.Event()
        self.update(job_id, status="queued", error=None, current_node="準備接續未完成片段")
        self.tasks[job_id] = asyncio.create_task(self._run_replacement_batch(job_id, raw_request))
        return self.jobs[job_id]

    async def _run(self, job_id: str, compiled: CompiledRequest) -> None:
        cancel_event = self.cancel_events[job_id]
        try:
            async with self.gpu_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                self.update(job_id, status="preparing", progress=0)
                await self.comfy.ensure_running()
                turbo_lora_name = None
                if compiled.quality_mode == "turbo":
                    turbo_lora_name = await self.comfy.resolve_turbo_lora(compiled.turbo_profile)
                    if not turbo_lora_name:
                        raise RuntimeError(
                            "Turbo 快速預覽需要對應的 H3 Turbo LoRA。"
                            "請在本機引擎安裝器補齊 Turbo 模型，或在遠端 ComfyUI 的 models/loras 安裝相容 LoRA。"
                        )
                uploaded: dict[str, str] = {}
                asset_ids = required_asset_ids(compiled)
                for index, asset_id in enumerate(asset_ids, start=1):
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    path = self.assets.path_for(asset_id)
                    uploaded[asset_id] = await self.comfy.upload_asset(path, f"h3studio/{job_id}")
                    self.update(job_id, progress=round(index / max(1, len(asset_ids)) * 5, 1))

                generation_started_at = datetime.now().astimezone()
                output_stem = output_filename_stem(self.jobs[job_id].get("name"), generation_started_at)
                self.update(
                    job_id,
                    generation_started_at=generation_started_at.isoformat(),
                    output_stem=output_stem,
                )
                workflow = build_workflow(compiled, uploaded, output_stem, turbo_lora_name)
                (JOB_DIR / f"{job_id}.workflow.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
                (JOB_DIR / f"{job_id}.prompt.txt").write_text(compiled.prompt, encoding="utf-8")
                node_titles = {node_id: node.get("_meta", {}).get("title", node["class_type"]) for node_id, node in workflow.items()}

                async def progress(event: dict[str, Any]) -> None:
                    node_id = event.get("current_node")
                    changes = dict(event)
                    if node_id:
                        changes["current_node"] = node_titles.get(str(node_id), str(node_id))
                    if event.get("progress") is not None:
                        changes["progress"] = max(5, event["progress"])
                    self.update(job_id, **changes)

                prompt_id, history = await self.comfy.run_prompt(workflow, progress, cancel_event)
                output = find_video_output(history)
                if not output:
                    raise RuntimeError("ComfyUI 已完成，但沒有回傳影片檔案。")
                await self.complete_job(job_id, compiled, prompt_id, output, history)
        except asyncio.CancelledError:
            self.update(job_id, status="cancelled", current_node=None, error="工作已取消。", **self.finish_timing(job_id))
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=str(error), **self.finish_timing(job_id))

    async def cancel(self, job_id: str) -> None:
        if job_id not in self.jobs:
            raise RequestError("找不到工作。")
        self.cancel_events.setdefault(job_id, asyncio.Event()).set()
        active_child = str(self.jobs[job_id].get("active_child_id") or "")
        if active_child and active_child in self.jobs:
            await self.cancel(active_child)
        if self.jobs[job_id].get("status") == "running":
            await self.comfy.interrupt(str(self.jobs[job_id].get("prompt_id") or "") or None)
        if self.jobs[job_id].get("status") in {"queued", "preparing", "running", "interrupted"}:
            self.update(
                job_id,
                status="cancelled",
                current_node=None,
                error="工作已取消。",
                **self.finish_timing(job_id),
            )


def find_video_output(value: Any) -> dict[str, str] | None:
    if isinstance(value, dict):
        filename = value.get("filename")
        if isinstance(filename, str) and Path(filename).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
            return {
                "filename": filename,
                "subfolder": str(value.get("subfolder") or ""),
                "type": str(value.get("type") or "output"),
            }
        for child in value.values():
            found = find_video_output(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_video_output(child)
            if found:
                return found
    return None


def json_response_error(error: Exception, status: int = 400) -> web.Response:
    return web.json_response({"error": str(error)}, status=status)


def create_app() -> web.Application:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    assets = AssetStore(ASSET_DIR)
    settings = SettingsStore(CONFIG_PATH, APP_DIR)
    comfy = ComfyClient(settings.current, DATA_DIR)
    jobs = JobManager(assets, comfy)
    music_installer = Music3Installer(comfy)
    music_jobs = MusicJobManager(comfy, DATA_DIR, jobs.gpu_lock, music_installer)
    gateway = SharedComfyGateway(DATA_DIR)

    async def use_installed_engine(target: Path) -> None:
        updated = settings.update({
            "mode": "local",
            "base_url": "http://127.0.0.1:8188",
            "comfy_dir": str(target),
            "auto_start_local": True,
        })
        comfy.configure(updated)

    installer = EngineInstaller(APP_DIR, DATA_DIR, use_installed_engine)
    app = web.Application(client_max_size=2 * 1024**3)
    app["assets"] = assets
    app["comfy"] = comfy
    app["jobs"] = jobs
    app["settings"] = settings
    app["installer"] = installer
    app["music_installer"] = music_installer
    app["music_jobs"] = music_jobs
    app["shared_gateway"] = gateway

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def status(_: web.Request) -> web.Response:
        stats = await comfy.system_stats()
        models = await comfy.model_inventory() if stats else {}
        device = None
        if stats and stats.get("devices"):
            device = stats["devices"][0].get("name")
        return web.json_response({
            "ready": bool(stats),
            "starting": comfy.is_starting,
            "device": device,
            "models": models,
            "connection_mode": comfy.mode,
            "base_url": comfy.base_url,
            "can_start": comfy.can_start,
            "studio_role": settings.current.studio_role,
        })

    async def connection(_: web.Request) -> web.Response:
        return web.json_response(settings.current.public_dict())

    async def update_connection(request: web.Request) -> web.Response:
        if jobs.gpu_lock.locked() or comfy.is_starting or music_installer.public_status()["active"]:
            return json_response_error(RequestError("目前有影片／音樂正在生成或 Music 3 模型正在下載，請完成後再切換引擎。"), 409)
        try:
            payload = await request.json()
            updated = settings.update(payload)
            comfy.configure(updated)
            return web.json_response(updated.public_dict())
        except (SettingsError, json.JSONDecodeError, TypeError) as error:
            return json_response_error(error)

    async def test_connection(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            candidate = ConnectionSettings(
                studio_role=settings.current.studio_role,
                mode=payload.get("mode", settings.current.mode),
                base_url=payload.get("base_url", settings.current.base_url),
                comfy_dir=payload.get("comfy_dir", settings.current.comfy_dir),
                auto_start_local=payload.get("auto_start_local", settings.current.auto_start_local),
                remote_access_token=(
                    str(payload.get("remote_access_token") or "").strip()
                    or settings.current.remote_access_token
                ),
            ).normalized(APP_DIR)
            probe = ComfyClient(candidate, DATA_DIR)
            stats = await probe.system_stats()
            return web.json_response({"ready": bool(stats), "device": (stats.get("devices") or [{}])[0].get("name") if stats else None})
        except (SettingsError, json.JSONDecodeError, TypeError) as error:
            return json_response_error(error)

    async def engine_installer_preflight(request: web.Request) -> web.Response:
        try:
            target = resolve_install_target(request.query.get("comfy_dir"), APP_DIR)
            result = await asyncio.to_thread(installer_preflight, target)
            return web.json_response(result)
        except (InstallerError, OSError, ValueError) as error:
            return json_response_error(error)

    async def engine_installer_status(_: web.Request) -> web.Response:
        return web.json_response(installer.public_status())

    async def start_engine_installer(request: web.Request) -> web.Response:
        if jobs.gpu_lock.locked() or music_installer.public_status()["active"]:
            return json_response_error(RequestError("目前有影片／音樂正在生成或 Music 3 模型正在下載，請完成後再安裝本機引擎。"), 409)
        try:
            payload = await request.json()
            target = resolve_install_target(payload.get("comfy_dir"), APP_DIR)
            result = await installer.start(target, payload.get("accepted_license") is True)
            return web.json_response(result, status=202)
        except (InstallerError, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def cancel_engine_installer(_: web.Request) -> web.Response:
        try:
            return web.json_response(await installer.cancel())
        except InstallerError as error:
            return json_response_error(error)

    async def start_comfy(_: web.Request) -> web.Response:
        try:
            await comfy.ensure_running()
            return web.json_response({"ready": True})
        except Exception as error:
            return json_response_error(error, 500)

    async def upload(request: web.Request) -> web.Response:
        try:
            metadata = await assets.save_upload(request)
            metadata["url"] = f"/api/assets/{metadata['id']}"
            return web.json_response(metadata)
        except RequestError as error:
            return json_response_error(error)

    async def replacement_preparation(asset_id: str, smart: bool) -> dict[str, Any]:
        path = assets.path_for(asset_id)
        metadata = assets.metadata(asset_id)
        cache_key = "replacement_plan_smart" if smart else "replacement_plan_balanced"
        source_info = metadata.get("video_info")
        plan = metadata.get(cache_key)
        if not isinstance(source_info, dict):
            source_info = await asyncio.to_thread(probe_video, path)
        if not isinstance(plan, list):
            scores = []
            if smart and float(source_info["duration"]) > 15:
                scores = await asyncio.to_thread(analyze_video_cut_scores, path)
            plan = replacement_segment_plan(float(source_info["duration"]), scores, smart)
            assets.update_metadata(asset_id, video_info=source_info, **{cache_key: plan})
        return {"source": source_info, "segments": plan, "smart": smart}

    async def prepare_replacement(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            asset_id = str(payload.get("asset_id") or "").strip()
            if not asset_id:
                raise RequestError("請先上傳原始表演影片。")
            result = await replacement_preparation(asset_id, payload.get("strategy") != "balanced")
            return web.json_response(result)
        except (RequestError, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def prepare_continuation(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            source_job_id = str(payload.get("job_id") or "").strip() or None
            source_asset_id = str(payload.get("asset_id") or "").strip() or None
            if source_job_id:
                source_job = jobs.jobs.get(source_job_id)
                if not source_job or source_job.get("status") != "completed" or not source_job.get("output"):
                    raise RequestError("只能續接已完成且仍有影片檔案的工作。")
                path = await jobs.job_output_path(source_job)
                source_name = source_job["output"]["filename"]
            elif source_asset_id:
                path = assets.path_for(source_asset_id)
                source_name = assets.metadata(source_asset_id).get("name") or path.name
            else:
                raise RequestError("請選擇已完成工作或上傳影片。")
            frame = await asyncio.to_thread(extract_continuation_frame, path, assets)
            ratio = frame["width"] / frame["height"]
            ratio_values = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1, "4:3": 4 / 3, "3:4": 3 / 4, "21:9": 21 / 9}
            aspect_ratio = min(ratio_values, key=lambda name: abs(math.log(ratio / ratio_values[name])))
            megapixels = min((0.4, 0.7, 0.9, 0.98), key=lambda value: abs(value - frame["width"] * frame["height"] / 1_000_000))
            return web.json_response({
                "last_frame": frame,
                "source_job_id": source_job_id,
                "source_asset_id": source_asset_id,
                "source_name": source_name,
                "source": {
                    "job_id": source_job_id,
                    "asset_id": source_asset_id,
                    "name": source_name,
                },
                "aspect_ratio": aspect_ratio,
                "megapixels": megapixels,
                "width": frame["width"],
                "height": frame["height"],
                "fps": frame["fps"],
                "duration": frame["duration"],
            })
        except (RequestError, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def prepare_symbol(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            source_asset_id = str(payload.get("asset_id") or "").strip()
            if not source_asset_id:
                raise RequestError("請先上傳圖騰圖片。")
            source_path = assets.path_for(source_asset_id)
            result = await asyncio.to_thread(prepare_symbol_canvas, source_path, assets)
            result["source_asset_id"] = source_asset_id
            result["source_name"] = assets.metadata(source_asset_id).get("name") or source_path.name
            return web.json_response(result)
        except (RequestError, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def prepare_keyframe(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            source_asset_id = str(payload.get("asset_id") or "").strip()
            if not source_asset_id:
                raise RequestError("請先上傳首尾圖片。")
            aspect_ratio = str(payload.get("aspect_ratio") or "16:9").strip()
            megapixels = float(payload.get("megapixels") or 0.4)
            fit_mode = str(payload.get("fit_mode") or "contain").strip()
            width, height = compute_dimensions(aspect_ratio, megapixels)
            source_path = assets.path_for(source_asset_id)
            source_metadata = assets.metadata(source_asset_id)
            result = await asyncio.to_thread(prepare_keyframe_canvas, source_path, assets, width, height, fit_mode)
            result["source_asset_id"] = source_asset_id
            result["source_name"] = source_metadata.get("name") or source_path.name
            result["transparency_filled"] = bool(source_metadata.get("transparency_filled") or result["transparency_filled"])
            result["prepared_asset"].update({
                "source_asset_id": source_asset_id,
                "source_name": result["source_name"],
                "transparency_filled": result["transparency_filled"],
            })
            return web.json_response(result)
        except (RequestError, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def asset(request: web.Request) -> web.StreamResponse:
        try:
            path = assets.path_for(request.match_info["asset_id"])
        except RequestError as error:
            return json_response_error(error, 404)
        return web.FileResponse(path, headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})

    async def compile_api(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            compiled = compile_request(payload)
            for asset_id in required_asset_ids(compiled):
                assets.path_for(asset_id)
            if compiled.continuation_source_asset:
                assets.path_for(compiled.continuation_source_asset)
            if compiled.continuation_source_job:
                await jobs.continuation_source_path(compiled)
            data = asdict(compiled)
            data["asset_count"] = len(required_asset_ids(compiled))
            return web.json_response(data)
        except (RequestError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def render(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            compiled = compile_request(payload)
            for asset_id in required_asset_ids(compiled):
                assets.path_for(asset_id)
            if compiled.continuation_source_asset:
                assets.path_for(compiled.continuation_source_asset)
            if compiled.continuation_source_job:
                await jobs.continuation_source_path(compiled)
            if compiled.mode == "replace" and payload.get("replacement_auto_split", True):
                audio_mode = str(payload.get("replacement_audio_mode") or "original")
                if audio_mode not in {"original", "generated", "mute"}:
                    raise RequestError("角色替換的聲音處理選項錯誤。")
                source_id = replacement_source_asset_id(payload)
                prepared = await replacement_preparation(
                    source_id, payload.get("replacement_split_strategy") != "balanced"
                )
                if float(prepared["source"]["duration"]) > 15:
                    job = jobs.create_replacement_batch(
                        compiled, payload, prepared["source"], prepared["segments"]
                    )
                    return web.json_response(job, status=202)
            job = jobs.create(compiled, payload)
            return web.json_response(job, status=202)
        except (RequestError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def list_jobs(request: web.Request) -> web.Response:
        ordered = sort_job_records(job for job in jobs.jobs.values() if not job.get("hidden"))
        if not any(key in request.query for key in ("page", "page_size", "q")):
            return web.json_response(ordered[:50])
        try:
            page = max(1, int(request.query.get("page", "1")))
            page_size = min(20, max(1, int(request.query.get("page_size", "20"))))
        except ValueError:
            raise web.HTTPBadRequest(text=json.dumps({"error": "頁碼格式錯誤。"}, ensure_ascii=False), content_type="application/json")
        return web.json_response(paginate_job_records(ordered, page, page_size, request.query.get("q", "")))

    async def job_options(_: web.Request) -> web.Response:
        ordered = sort_job_records(job for job in jobs.jobs.values() if not job.get("hidden"))
        return web.json_response([
            job for job in ordered if job.get("status") == "completed" and job.get("output")
        ])

    async def get_job(request: web.Request) -> web.Response:
        job = jobs.jobs.get(request.match_info["job_id"])
        if not job:
            return json_response_error(RequestError("找不到工作。"), 404)
        return web.json_response(job)

    async def cancel_job(request: web.Request) -> web.Response:
        try:
            await jobs.cancel(request.match_info["job_id"])
            return web.json_response(jobs.jobs[request.match_info["job_id"]])
        except RequestError as error:
            return json_response_error(error, 404)

    async def resume_job(request: web.Request) -> web.Response:
        try:
            return web.json_response(await jobs.resume_batch(request.match_info["job_id"]), status=202)
        except (RequestError, OSError, json.JSONDecodeError) as error:
            return json_response_error(error, 409)

    async def rename_job(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if job_id not in jobs.jobs:
            return json_response_error(RequestError("找不到工作。"), 404)
        try:
            payload = await request.json()
            jobs.update(job_id, name=clean_job_name(payload.get("name")))
            return web.json_response(jobs.jobs[job_id])
        except (RequestError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def favorite_job(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if job_id not in jobs.jobs:
            return json_response_error(RequestError("找不到工作。"), 404)
        try:
            payload = await request.json()
            if not isinstance(payload.get("favorite"), bool):
                raise RequestError("我的最愛狀態格式錯誤。")
            jobs.update(job_id, favorite=payload["favorite"])
            return web.json_response(jobs.jobs[job_id])
        except (RequestError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def job_recipe(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        job = jobs.jobs.get(job_id)
        if not job:
            return json_response_error(RequestError("找不到工作。"), 404)
        request_path = JOB_DIR / f"{job_id}.request.json"
        if not request_path.exists():
            return json_response_error(RequestError("這筆舊工作沒有保存可套用的生成設定。"), 404)
        try:
            raw_request = json.loads(request_path.read_text(encoding="utf-8"))
            prompt_path = JOB_DIR / f"{job_id}.prompt.txt"
            if prompt_path.exists():
                compiled_prompt = prompt_path.read_text(encoding="utf-8")
            else:
                compiled_prompt = compile_request(raw_request).prompt
            recipe_assets: dict[str, Any] = {}
            missing_assets: list[str] = []
            for asset_id in sorted(request_asset_ids(raw_request)):
                try:
                    metadata = assets.metadata(asset_id)
                    asset_path = assets.path_for(asset_id)
                    if asset_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        with Image.open(asset_path) as image:
                            metadata["width"], metadata["height"] = image.size
                    if asset_id in {raw_request.get("first_image_asset_id"), raw_request.get("last_image_asset_id")}:
                        metadata["fit_mode"] = str(raw_request.get("keyframe_fit") or "contain")
                        metadata["background_mode"] = "chroma_green"
                    recipe_assets[asset_id] = {**metadata, "url": f"/api/assets/{asset_id}"}
                except (RequestError, OSError, json.JSONDecodeError):
                    missing_assets.append(asset_id)
            return web.json_response({
                "job": job,
                "request": raw_request,
                "compiled_prompt": compiled_prompt,
                "assets": recipe_assets,
                "missing_assets": missing_assets,
            })
        except (RequestError, OSError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def job_video(request: web.Request) -> web.Response:
        job = jobs.jobs.get(request.match_info["job_id"])
        if not job or (not job.get("output") and not jobs.local_output_path(job)):
            return json_response_error(RequestError("影片尚未完成。"), 404)
        try:
            cached = jobs.local_output_path(job)
            if cached:
                content = await asyncio.to_thread(cached.read_bytes)
                content_type = mimetypes.guess_type(cached.name)[0] or "video/mp4"
            else:
                content, content_type = await comfy.fetch_output(job["output"])
            headers = {}
            if request.query.get("download") == "1":
                stored_filename = Path(job.get("download_name") or job["output"]["filename"]).name
                suffix = Path(stored_filename).suffix or ".mp4"
                filename = f"{output_filename_stem(job.get('name'))}{suffix}" if job.get("name") else stored_filename
                headers["Content-Disposition"] = (
                    f'attachment; filename="video{suffix}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
                )
            return web.Response(body=content, content_type=content_type, headers=headers)
        except Exception as error:
            return json_response_error(error, 502)

    async def music_status(_: web.Request) -> web.Response:
        result = await music_installer.status_for_current_engine()
        result["engine_ready"] = await comfy.is_ready()
        result["connection_mode"] = comfy.mode
        return web.json_response(result)

    async def install_music_models(_: web.Request) -> web.Response:
        try:
            return web.json_response(await music_installer.start(), status=202)
        except Music3Error as error:
            return json_response_error(error, 409)

    async def cancel_music_install(_: web.Request) -> web.Response:
        return web.json_response(await music_installer.cancel())

    async def list_music_jobs(request: web.Request) -> web.Response:
        try:
            page = max(1, int(request.query.get("page", "1")))
            page_size = min(20, max(1, int(request.query.get("page_size", "20"))))
        except ValueError:
            return json_response_error(Music3Error("頁碼格式錯誤。"))
        return web.json_response(music_jobs.list_page(page, page_size))

    async def create_music_job(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            return web.json_response(music_jobs.create(payload), status=202)
        except (Music3Error, json.JSONDecodeError, OSError, ValueError) as error:
            return json_response_error(error)

    async def cancel_music_job(request: web.Request) -> web.Response:
        try:
            return web.json_response(await music_jobs.cancel(request.match_info["job_id"]))
        except Music3Error as error:
            return json_response_error(error, 404)

    async def resume_music_job(request: web.Request) -> web.Response:
        try:
            return web.json_response(music_jobs.resume(request.match_info["job_id"]), status=202)
        except (Music3Error, OSError, json.JSONDecodeError) as error:
            return json_response_error(error, 409)

    async def rename_music_job(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if job_id not in music_jobs.jobs:
            return json_response_error(Music3Error("找不到音樂工作。"), 404)
        try:
            payload = await request.json()
            music_jobs.update(job_id, name=clean_music_name(payload.get("name")))
            return web.json_response(music_jobs.jobs[job_id])
        except (Music3Error, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def favorite_music_job(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if job_id not in music_jobs.jobs:
            return json_response_error(Music3Error("找不到音樂工作。"), 404)
        try:
            payload = await request.json()
            if not isinstance(payload.get("favorite"), bool):
                raise Music3Error("我的最愛狀態格式錯誤。")
            music_jobs.update(job_id, favorite=payload["favorite"])
            return web.json_response(music_jobs.jobs[job_id])
        except (Music3Error, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def music_audio(request: web.Request) -> web.Response:
        job = music_jobs.jobs.get(request.match_info["job_id"])
        path = music_jobs.local_output_path(job or {})
        if not job or not path:
            return json_response_error(Music3Error("音樂尚未完成或檔案不存在。"), 404)
        headers = {}
        if request.query.get("download") == "1":
            suffix = path.suffix or ".mp3"
            download_name = f"{music_filename_stem(job.get('name'))}{suffix}"
            headers["Content-Disposition"] = (
                f'attachment; filename="music{suffix}"; filename*=UTF-8\'\'{quote(download_name, safe="")}'
            )
        return web.FileResponse(path, headers=headers)

    def require_gateway_admin() -> None:
        if settings.current.studio_role != "host":
            raise web.HTTPForbidden(
                text=json.dumps({"error": "此 H3 Studio 是一般使用者工作站，沒有共享引擎管理權限。"}, ensure_ascii=False),
                content_type="application/json",
            )

    async def gateway_status(_: web.Request) -> web.Response:
        require_gateway_admin()
        return web.json_response(gateway.public_status())

    async def update_gateway_settings(request: web.Request) -> web.Response:
        require_gateway_admin()
        try:
            payload = await request.json()
            return web.json_response(await gateway.apply_settings(payload.get("enabled") is True, payload.get("port", 8190)))
        except (GatewayError, OSError, json.JSONDecodeError) as error:
            return json_response_error(error, 409)

    async def create_gateway_user(request: web.Request) -> web.Response:
        require_gateway_admin()
        try:
            payload = await request.json()
            user, token = gateway.store.create_user(payload.get("name"))
            return web.json_response({"user": user, "token": token}, status=201)
        except (GatewayError, OSError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def rotate_gateway_user(request: web.Request) -> web.Response:
        require_gateway_admin()
        try:
            user, token = gateway.store.rotate_user(request.match_info["user_id"])
            return web.json_response({"user": user, "token": token})
        except (GatewayError, OSError) as error:
            return json_response_error(error, 404)

    async def set_gateway_user_enabled(request: web.Request) -> web.Response:
        require_gateway_admin()
        try:
            payload = await request.json()
            if not isinstance(payload.get("enabled"), bool):
                raise GatewayError("使用者啟用狀態格式錯誤。")
            return web.json_response(
                gateway.store.set_user_enabled(request.match_info["user_id"], payload["enabled"])
            )
        except (GatewayError, OSError, json.JSONDecodeError) as error:
            return json_response_error(error, 404)

    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/connection", connection)
    app.router.add_post("/api/connection", update_connection)
    app.router.add_post("/api/connection/test", test_connection)
    app.router.add_get("/api/engine-installer/preflight", engine_installer_preflight)
    app.router.add_get("/api/engine-installer/status", engine_installer_status)
    app.router.add_post("/api/engine-installer/start", start_engine_installer)
    app.router.add_post("/api/engine-installer/cancel", cancel_engine_installer)
    app.router.add_post("/api/comfy/start", start_comfy)
    app.router.add_post("/api/assets", upload)
    app.router.add_post("/api/replacement/prepare", prepare_replacement)
    app.router.add_post("/api/keyframes/prepare", prepare_keyframe)
    app.router.add_post("/api/continuation/prepare", prepare_continuation)
    app.router.add_post("/api/symbol/prepare", prepare_symbol)
    app.router.add_get("/api/assets/{asset_id}", asset)
    app.router.add_post("/api/compile", compile_api)
    app.router.add_post("/api/render", render)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_get("/api/jobs/options", job_options)
    app.router.add_get("/api/jobs/{job_id}", get_job)
    app.router.add_post("/api/jobs/{job_id}/cancel", cancel_job)
    app.router.add_post("/api/jobs/{job_id}/resume", resume_job)
    app.router.add_post("/api/jobs/{job_id}/rename", rename_job)
    app.router.add_post("/api/jobs/{job_id}/favorite", favorite_job)
    app.router.add_get("/api/jobs/{job_id}/recipe", job_recipe)
    app.router.add_get("/api/jobs/{job_id}/video", job_video)
    app.router.add_get("/api/music/status", music_status)
    app.router.add_post("/api/music/install", install_music_models)
    app.router.add_post("/api/music/install/cancel", cancel_music_install)
    app.router.add_get("/api/music/jobs", list_music_jobs)
    app.router.add_post("/api/music/jobs", create_music_job)
    app.router.add_post("/api/music/jobs/{job_id}/cancel", cancel_music_job)
    app.router.add_post("/api/music/jobs/{job_id}/resume", resume_music_job)
    app.router.add_post("/api/music/jobs/{job_id}/rename", rename_music_job)
    app.router.add_post("/api/music/jobs/{job_id}/favorite", favorite_music_job)
    app.router.add_get("/api/music/jobs/{job_id}/audio", music_audio)
    app.router.add_get("/api/gateway/status", gateway_status)
    app.router.add_post("/api/gateway/settings", update_gateway_settings)
    app.router.add_post("/api/gateway/users", create_gateway_user)
    app.router.add_post("/api/gateway/users/{user_id}/rotate", rotate_gateway_user)
    app.router.add_post("/api/gateway/users/{user_id}/enabled", set_gateway_user_enabled)
    app.router.add_static("/static", STATIC_DIR)

    async def auto_start_engine(_: web.Application) -> None:
        async def start_in_background() -> None:
            try:
                if comfy.can_start:
                    await comfy.ensure_running()
                jobs.resume_recoverable_jobs()
                await jobs.backfill_completed_timings()
            except Exception as error:
                print(f"ComfyUI 自動啟動失敗：{error}", flush=True)

        app["comfy_autostart_task"] = asyncio.create_task(start_in_background())

    async def auto_start_gateway(_: web.Application) -> None:
        if settings.current.studio_role == "host":
            await gateway.start_if_enabled()

    async def cleanup_gateway(_: web.Application) -> None:
        await gateway.stop()

    app.on_startup.append(auto_start_engine)
    app.on_startup.append(auto_start_gateway)
    app.on_cleanup.append(cleanup_gateway)
    return app


async def open_browser(port: int) -> None:
    await asyncio.sleep(1)
    webbrowser.open(f"http://127.0.0.1:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniMax H3 Studio")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app()
    if not args.no_browser:
        app.on_startup.append(lambda _: asyncio.create_task(open_browser(args.port)))
    try:
        web.run_app(app, host="127.0.0.1", port=args.port, print=lambda message: print(message, flush=True))
    except OSError as error:
        if getattr(error, "winerror", None) == 10048 or error.errno == 10048:
            url = f"http://127.0.0.1:{args.port}"
            print(f"MiniMax H3 Studio 已經在執行：{url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
        raise


if __name__ == "__main__":
    main()
