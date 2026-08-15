from __future__ import annotations

import argparse
import asyncio
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
                if not values.shape[1] or remaining <= 0:
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
            self.jobs[job["id"]] = job

    def _persist(self, job: dict[str, Any]) -> None:
        (JOB_DIR / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(self, job_id: str, **changes: Any) -> None:
        job = self.jobs[job_id]
        job.update(changes)
        job["updated_at"] = utc_now()
        self._persist(job)

    def create(self, compiled: CompiledRequest, raw_request: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "name": clean_job_name(raw_request.get("job_name")),
            "favorite": False,
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

    async def _run(self, job_id: str, compiled: CompiledRequest) -> None:
        cancel_event = self.cancel_events[job_id]
        try:
            async with self.gpu_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                self.update(job_id, status="preparing", progress=0)
                await self.comfy.ensure_running()
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
                workflow = build_workflow(compiled, uploaded, output_stem)
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
        if self.jobs[job_id].get("status") == "running":
            await self.comfy.interrupt()
        if self.jobs[job_id].get("status") == "queued":
            self.update(job_id, status="cancelled", error="工作已取消。", finished_at=utc_now())


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
        })

    async def connection(_: web.Request) -> web.Response:
        return web.json_response(settings.current.public_dict())

    async def update_connection(request: web.Request) -> web.Response:
        if jobs.gpu_lock.locked() or comfy.is_starting:
            return json_response_error(RequestError("目前有影片正在生成，請等工作完成後再切換引擎。"), 409)
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
                mode=payload.get("mode", settings.current.mode),
                base_url=payload.get("base_url", settings.current.base_url),
                comfy_dir=payload.get("comfy_dir", settings.current.comfy_dir),
                auto_start_local=payload.get("auto_start_local", settings.current.auto_start_local),
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
        if jobs.gpu_lock.locked():
            return json_response_error(RequestError("目前有影片正在生成，請完成後再安裝本機引擎。"), 409)
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
            job = jobs.create(compiled, payload)
            return web.json_response(job, status=202)
        except (RequestError, json.JSONDecodeError) as error:
            return json_response_error(error)

    async def list_jobs(request: web.Request) -> web.Response:
        ordered = sort_job_records(jobs.jobs.values())
        if not any(key in request.query for key in ("page", "page_size", "q")):
            return web.json_response(ordered[:50])
        try:
            page = max(1, int(request.query.get("page", "1")))
            page_size = min(20, max(1, int(request.query.get("page_size", "20"))))
        except ValueError:
            raise web.HTTPBadRequest(text=json.dumps({"error": "頁碼格式錯誤。"}, ensure_ascii=False), content_type="application/json")
        return web.json_response(paginate_job_records(ordered, page, page_size, request.query.get("q", "")))

    async def job_options(_: web.Request) -> web.Response:
        ordered = sort_job_records(jobs.jobs.values())
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
        if not job or not job.get("output"):
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
    app.router.add_post("/api/jobs/{job_id}/rename", rename_job)
    app.router.add_post("/api/jobs/{job_id}/favorite", favorite_job)
    app.router.add_get("/api/jobs/{job_id}/recipe", job_recipe)
    app.router.add_get("/api/jobs/{job_id}/video", job_video)
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

    app.on_startup.append(auto_start_engine)
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
