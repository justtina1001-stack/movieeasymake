"""Small cached stills of completed, locally available Studio video outputs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import uuid

import av
from aiohttp import web


LOGGER = logging.getLogger(__name__)
SAFE_ID = re.compile(r"[a-f0-9]{32}\Z")
THUMBNAIL_VERSION = "rgb-jpeg-320x180-v1"
THUMBNAIL_SIZE = (320, 180)


class ThumbnailError(Exception):
    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status


def write_video_thumbnail(source, destination):
    """Decode one video frame on the CPU, retaining its full aspect ratio."""
    try:
        with av.open(str(source)) as container:
            if not container.streams.video:
                raise ThumbnailError("這份生成作品沒有可讀取的影片畫面。", 422)
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 1
            frame = next(container.decode(stream), None)
            if frame is None:
                raise ThumbnailError("這份生成作品沒有可讀取的影片畫面。", 422)
            scale = min(1, THUMBNAIL_SIZE[0] / frame.width, THUMBNAIL_SIZE[1] / frame.height)
            width, height = max(1, int(frame.width * scale)), max(1, int(frame.height * scale))
            image = frame.reformat(width=width, height=height, format="rgb24").to_image()
    except ThumbnailError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as error:
        raise ThumbnailError("無法讀取這份生成作品的影片縮圖，影片可能已損壞。", 422) from error
    with image:
        with image.convert("RGB") as rgb:
            rgb.save(destination, format="JPEG", quality=82)


class JobThumbnailCache:
    def __init__(self, jobs, output_dir):
        self.jobs = jobs
        self.directory = Path(output_dir).resolve()
        self.slots = asyncio.Semaphore(2)
        self.inflight = {}
        self.closing = False

    def source(self, identifier):
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier):
            raise ThumbnailError("找不到這份生成作品。")
        job = self.jobs.jobs.get(identifier)
        if not job or job.get("status") != "completed":
            raise ThumbnailError("這份生成作品尚未完成，無法提供影片縮圖。")
        try:
            path = self.jobs.local_output_path(job)
            if path is None:
                raise ThumbnailError("這份生成作品的本機影片不存在。")
            path = Path(path).resolve()
            if self.directory not in path.parents or not path.is_file():
                raise ThumbnailError("這份生成作品的本機影片不存在。")
            stat = path.stat()
        except OSError as error:
            raise ThumbnailError("這份生成作品的本機影片不存在或無法讀取。") from error
        signature = json.dumps([THUMBNAIL_VERSION, str(path), stat.st_size, stat.st_mtime_ns], ensure_ascii=False)
        fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        return path, fingerprint

    async def get(self, identifier):
        # Validate even on cache hits; removed local files must not expose stale stills.
        self.source(identifier)
        if self.closing:
            raise ThumbnailError("Studio 正在關閉，請稍後再試。", 503)
        task = self.inflight.get(identifier)
        if task is None:
            task = asyncio.create_task(self.generate(identifier))
            self.inflight[identifier] = task
            task.add_done_callback(lambda completed: self.finished(identifier, completed))
        # A disconnected image request must not release its slot while decoding
        # is still active, or remove a temporary file the worker is writing.
        return await asyncio.shield(task)

    def finished(self, identifier, task):
        if self.inflight.get(identifier) is task:
            self.inflight.pop(identifier, None)
        if not task.cancelled():
            task.exception()

    async def generate(self, identifier):
        async with self.slots:
            for _ in range(2):
                source, fingerprint = self.source(identifier)
                destination = self.directory / f"{identifier}.thumbnail.{fingerprint}.jpg"
                if destination.is_file() and destination.stat().st_size:
                    return destination
                temporary = self.directory / f"{identifier}.thumbnail.{fingerprint}.{uuid.uuid4().hex}.partial"
                try:
                    try:
                        await asyncio.to_thread(write_video_thumbnail, source, temporary)
                    except ThumbnailError:
                        # Report deletion as missing rather than a corrupt-video error.
                        self.source(identifier)
                        raise
                    current_source, current_fingerprint = self.source(identifier)
                    if current_source != source or current_fingerprint != fingerprint:
                        continue
                    temporary.replace(destination)
                    self.prune(identifier, destination, source)
                    return destination
                finally:
                    temporary.unlink(missing_ok=True)
            raise ThumbnailError("生成作品的影片已更新，請重新整理縮圖。", 422)

    def prune(self, identifier, current, source):
        pattern = re.compile(rf"{identifier}\.thumbnail\.[a-f0-9]{{24}}\.jpg\Z")
        for old in self.directory.glob(f"{identifier}.thumbnail.*.jpg"):
            if old != current and pattern.fullmatch(old.name) and old.is_file() and old.resolve() != source:
                try:
                    old.unlink()
                except OSError:
                    LOGGER.warning("Could not remove an obsolete generated-video thumbnail: %s", old.name)

    async def shutdown(self):
        self.closing = True
        await asyncio.gather(*tuple(self.inflight.values()), return_exceptions=True)


def register_job_thumbnails(app, output_dir):
    cache = JobThumbnailCache(app["jobs"], output_dir)
    app["job_thumbnails"] = cache

    async def thumbnail(request):
        try:
            path = await cache.get(request.match_info["job_id"])
            return web.FileResponse(path, headers={"Content-Type": "image/jpeg",
                "Cache-Control": "private, max-age=60, must-revalidate"})
        except ThumbnailError as error:
            return web.json_response({"error": str(error)}, status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except OSError:
            LOGGER.exception("Could not store a generated-video thumbnail")
            return web.json_response({"error": "無法建立影片縮圖，請確認磁碟空間與檔案權限。"},
                                     status=500, headers={"Cache-Control": "no-store"})

    async def cleanup(_):
        await cache.shutdown()

    app.router.add_get("/api/jobs/{job_id}/thumbnail", thumbnail)
    app.on_cleanup.append(cleanup)
    return cache
