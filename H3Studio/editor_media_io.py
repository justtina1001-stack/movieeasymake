"""Streaming editor uploads and optional, reusable seek-friendly previews."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from fractions import Fraction
import logging
import math
from pathlib import Path
import threading
import uuid

import av
from aiohttp import web
from editor_images import IMAGE_SUFFIXES, MAX_IMAGE_BYTES, probe_image, write_thumbnail

from video_editor import (AudioReader, EditorError, ExportCancelled, SAMPLE_RATE,
                          VideoReader, atomic_json, now, probe_media)

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 2 * 1024**3
UPLOAD_SUFFIXES = {".mp4", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"} | IMAGE_SUFFIXES
ACTIVE_PREVIEW = {"queued", "running"}


async def finish_io(function, *args):
    """Finish an in-flight file operation before cancellation closes its file."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def upload_media(request, store):
    if not request.content_type.startswith("multipart/"):
        raise EditorError("請使用檔案上傳格式。")
    identifier = uuid.uuid4().hex
    temporary = store.media_dir / f"{identifier}.uploading"
    destination = store.media_dir / f"{identifier}.source"
    committed = False
    try:
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file" or not part.filename:
            raise EditorError("請選擇影片、音訊或圖片檔案。")
        name = part.filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
        if Path(name).suffix.lower() not in UPLOAD_SUFFIXES:
            raise EditorError("不支援此檔案類型；請上傳 MP4／WebM、常用音訊或 PNG／JPEG／WebP 圖片。")
        is_image = Path(name).suffix.lower() in IMAGE_SUFFIXES
        size = 0
        with temporary.open("xb") as output:
            while chunk := await part.read_chunk(256 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise EditorError("單一素材最多 2 GB。", 413)
                if is_image and size > MAX_IMAGE_BYTES:
                    raise EditorError("圖片最多 25 MB。", 413)
                await finish_io(output.write, chunk)
        if not size:
            raise EditorError("上傳的檔案是空的。")
        if await reader.next() is not None:
            raise EditorError("每次請上傳一個檔案。")
        info = await finish_io(probe_image if is_image else probe_media, temporary)
        item = {"id": identifier, "name": name, **info, "created_at": now(),
                "url": f"/api/editor/media/{identifier}/file"}
        async with store.import_lock:
            if store.closing:
                raise EditorError("Studio 正在關閉，請稍後再上傳。", 503)
            temporary.replace(destination)
            atomic_json(store.media_dir / f"{identifier}.json", item)
            store.media[identifier] = item
            committed = True
        return web.json_response(item, status=201)
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            destination.unlink(missing_ok=True)


def render_preview(source, media, destination, cancel_event, progress):
    """Bounded frame/audio decoding; short GOPs reduce work after a trim seek."""
    fps = 24
    scale = min(1, 960 / media["width"], 540 / media["height"])
    width = max(2, int(media["width"] * scale) // 2 * 2)
    height = max(2, int(media["height"] * scale) // 2 * 2)
    total_frames = max(1, math.ceil(media["duration"] * fps))
    try:
        with ExitStack() as resources:
            reader = VideoReader(source, 0, cancel_event)
            resources.callback(reader.close)
            audio_reader = AudioReader(source, 0, cancel_event) if media["has_audio"] else None
            if audio_reader:
                resources.callback(audio_reader.close)
            output = resources.enter_context(av.open(str(destination), "w", format="mp4", options={"movflags": "+faststart"}))
            video = output.add_stream("libx264", rate=fps)
            video.width, video.height, video.pix_fmt = width, height, "yuv420p"
            video.codec_context.thread_count = 2
            video.options = {"preset": "veryfast", "crf": "28", "tune": "zerolatency", "g": "12", "keyint_min": "12", "sc_threshold": "0", "bf": "0"}
            audio = output.add_stream("aac", rate=SAMPLE_RATE) if audio_reader else None
            if audio:
                audio.layout = "stereo"
                audio.bit_rate = 128000
                audio.codec_context.thread_count = 1
            audio_cursor, last_percent = 0, -1
            for index in range(total_frames):
                if cancel_event.is_set():
                    raise ExportCancelled()
                # Reformat and copy the AVFrame metadata: VideoReader may return
                # the same frame for successive CFR samples.
                frame = reader.at(index / fps).reformat(width=width, height=height, format="yuv420p")
                frame.pts, frame.time_base = index, Fraction(1, fps)
                frame.pict_type = av.video.frame.PictureType.NONE
                for packet in video.encode(frame):
                    output.mux(packet)
                if audio:
                    end_sample = round(min((index + 1) / fps, media["duration"]) * SAMPLE_RATE)
                    if end_sample > audio_cursor:
                        samples = audio_reader.read(audio_cursor, end_sample - audio_cursor)
                        sound = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
                        sound.sample_rate = SAMPLE_RATE
                        sound.pts, sound.time_base = audio_cursor, Fraction(1, SAMPLE_RATE)
                        for packet in audio.encode(sound):
                            output.mux(packet)
                        audio_cursor = end_sample
                percent = int((index + 1) * 99 / total_frames)
                if percent > last_percent:
                    progress(percent)
                    last_percent = percent
            for stream in (video, audio):
                if stream:
                    for packet in stream.encode():
                        output.mux(packet)
        if cancel_event.is_set():
            raise ExportCancelled()
        return {"width": width, "height": height, "fps": fps, "duration": media["duration"]}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


class PreviewCache:
    def __init__(self, store):
        self.store = store
        self.directory = store.root / "previews"
        self.directory.mkdir(exist_ok=True)
        self.jobs = store._load(self.directory)
        self.tasks, self.events = {}, {}
        self.slot = asyncio.Semaphore(1)
        self.closing = False
        for identifier, job in self.jobs.items():
            if job.get("status") in ACTIVE_PREVIEW:
                job.update(status="cancelled", error="Studio 重新啟動，可重新建立預覽。")
                atomic_json(self.directory / f"{identifier}.json", job)
            (self.directory / f"{identifier}.partial.mp4").unlink(missing_ok=True)

    def get(self, identifier):
        self.store.media_path(identifier)
        job = self.jobs.get(identifier)
        if job and job.get("status") == "completed" and not (self.directory / f"{identifier}.mp4").is_file():
            job = None
        return dict(job or {"id": identifier, "media_id": identifier, "status": "missing", "progress": 0})

    def start(self, identifier):
        job = self.get(identifier)
        media = self.store.media[identifier]
        if media.get("kind", "video") != "video":
            raise EditorError("音訊與圖片素材不需要建立影片預覽。")
        if media["duration"] > 600:
            raise EditorError("流暢預覽目前支援 10 分鐘以內的來源影片，仍可直接使用原始素材剪輯。")
        if self.closing:
            raise EditorError("Studio 正在關閉。", 503)
        if job["status"] in ACTIVE_PREVIEW or job["status"] == "completed":
            return job
        if sum(not task.done() for task in self.tasks.values()) >= 50:
            raise EditorError("預覽建立佇列已滿，請稍後再試。", 409)
        job = {"id": identifier, "media_id": identifier, "status": "queued", "progress": 0, "updated_at": now()}
        self.jobs[identifier] = job
        atomic_json(self.directory / f"{identifier}.json", job)
        event = self.events[identifier] = threading.Event()
        self.tasks[identifier] = asyncio.create_task(self._run(identifier, event))
        return dict(job)

    def update(self, identifier, **changes):
        job = self.jobs[identifier]
        job.update(changes, updated_at=now())
        atomic_json(self.directory / f"{identifier}.json", job)

    def progress(self, identifier, value):
        if self.jobs[identifier]["status"] == "running":
            # Progress does not need 100 durable JSON writes per preview.
            self.jobs[identifier].update(progress=value)

    async def _run(self, identifier, event):
        temporary = self.directory / f"{identifier}.partial.mp4"
        destination = self.directory / f"{identifier}.mp4"
        worker = None
        try:
            async with self.slot:
                if event.is_set():
                    raise ExportCancelled()
                self.update(identifier, status="running")
                loop = asyncio.get_running_loop()
                worker = asyncio.create_task(asyncio.to_thread(render_preview, self.store.media_path(identifier),
                    self.store.media[identifier], temporary, event,
                    lambda value: loop.call_soon_threadsafe(self.progress, identifier, value)))
                info = await asyncio.shield(worker)
                if event.is_set():
                    raise ExportCancelled()
                temporary.replace(destination)
                self.update(identifier, status="completed", progress=100, output=info,
                            url=f"/api/editor/media/{identifier}/preview/file")
        except (ExportCancelled, asyncio.CancelledError):
            event.set()
            if worker:
                await asyncio.gather(worker, return_exceptions=True)
            temporary.unlink(missing_ok=True)
            self.update(identifier, status="cancelled", error="已停止建立預覽，原始素材仍可播放。")
        except Exception:
            temporary.unlink(missing_ok=True)
            LOGGER.exception("Preview cache failed: %s", identifier)
            self.update(identifier, status="failed", error="流暢預覽建立失敗，仍可播放原始素材。")

    async def cancel(self, identifier):
        job = self.get(identifier)
        if job["status"] in ACTIVE_PREVIEW:
            self.events[identifier].set()
            if job["status"] == "queued":
                self.tasks[identifier].cancel()
            await asyncio.gather(self.tasks[identifier], return_exceptions=True)
            if self.jobs[identifier]["status"] == "queued":
                self.update(identifier, status="cancelled", error="已停止建立預覽。")
        return self.get(identifier)

    async def shutdown(self):
        self.closing = True
        for identifier, task in self.tasks.items():
            if not task.done():
                self.events[identifier].set()
                if self.jobs[identifier]["status"] == "queued":
                    task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def register_editor_media_io(app):
    store = app["editor"]
    previews = PreviewCache(store)
    app["editor_previews"] = previews
    image_thumbnail_dir = store.root / "image_thumbnails"
    image_thumbnail_dir.mkdir(exist_ok=True)
    image_thumbnail_slot = asyncio.Semaphore(2)

    async def upload(request):
        return await upload_media(request, store)

    async def capabilities(request):
        return web.json_response({"schema_version": 8, "clip_animations": True, "clip_transitions": True, "generated_video_thumbnails": True, "text_style": True, "overlay_tracks": True, "audio_tracks": 4, "speed_min": 0.25,
                                  "speed_max": 4, "preview_cache": True, "direct_upload": True,
                                  "project_archives": True, "text_overlays": True,
                                  "image_overlays": True, "max_overlays": 50,
                                  "visual_fades": True, "video_overlays": True,
                                  "position_keyframes": True, "max_position_keyframes": 100,
                                  "speed_curves": True, "max_speed_curve_points": 50,
                                  "max_video_overlays": 3},
                                 headers={"Cache-Control": "no-store"})

    async def get_preview(request):
        return web.json_response(previews.get(request.match_info["media_id"]), headers={"Cache-Control": "no-store"})

    async def image_thumbnail(request):
        identifier = request.match_info["media_id"]
        item = store._get(store.media, identifier, "圖片素材")
        if item.get("kind") != "image":
            raise EditorError("這份素材不是圖片。")
        destination = image_thumbnail_dir / f"{identifier}.png"
        if not destination.exists():
            async with image_thumbnail_slot:
                if not destination.exists():
                    temporary = image_thumbnail_dir / f"{identifier}.{uuid.uuid4().hex}.partial"
                    try:
                        await finish_io(write_thumbnail, store.media_path(identifier), temporary)
                        temporary.replace(destination)
                    finally:
                        temporary.unlink(missing_ok=True)
        return web.FileResponse(destination, headers={"Content-Type": "image/png",
            "Cache-Control": "private, max-age=31536000, immutable"})

    async def start_preview(request):
        job = previews.start(request.match_info["media_id"])
        return web.json_response(job, status=200 if job["status"] == "completed" else 202)

    async def cancel_preview(request):
        return web.json_response(await previews.cancel(request.match_info["media_id"]))

    async def preview_file(request):
        identifier = request.match_info["media_id"]
        if previews.get(identifier)["status"] != "completed":
            raise EditorError("流暢預覽尚未建立完成。", 404)
        return web.FileResponse(previews.directory / f"{identifier}.mp4", headers={
            "Content-Type": "video/mp4", "Cache-Control": "private, max-age=31536000, immutable"})

    async def cleanup(_):
        await previews.shutdown()

    app.router.add_post("/api/editor/upload", upload)
    app.router.add_get("/api/editor/capabilities", capabilities)
    app.router.add_get("/api/editor/media/{media_id}/thumbnail", image_thumbnail)
    app.router.add_get("/api/editor/media/{media_id}/preview", get_preview)
    app.router.add_post("/api/editor/media/{media_id}/preview", start_preview)
    app.router.add_post("/api/editor/media/{media_id}/preview/cancel", cancel_preview)
    app.router.add_get("/api/editor/media/{media_id}/preview/file", preview_file)
    app.on_cleanup.append(cleanup)
    return previews
