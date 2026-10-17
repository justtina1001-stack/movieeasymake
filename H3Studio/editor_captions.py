"""Persistent, cancellable CPU speech-caption jobs for saved editor timelines.

The ASR dependencies live in a dedicated virtual environment. The Studio
process only prepares audio and supervises its own worker, never loading an
ASR model or accepting user-supplied paths or commands.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from aiohttp import web

from editor_caption_audio import render_caption_audio
from video_editor import (ACTIVE, EditorError, ExportCancelled, atomic_json,
                          check_id, clip_duration, now)


LOGGER = logging.getLogger(__name__)
MODEL = "small"
LANGUAGES = {"auto", "zh", "en", "ja", "ko"}
PUBLIC_KEYS = {"id", "project_id", "status", "stage", "progress", "created_at",
               "updated_at", "language", "traditional", "source_audio_snapshot",
               "error", "cues", "detected_language", "duration", "cancel_requested"}
STAGES = {"runtime", "model", "audio", "transcribing"}
MAX_LOG_BYTES = 256 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024


class CaptionFailure(Exception):
    """A user-facing failure with no dependency output or local paths."""


def audio_snapshot(project):
    """Keep a stable snapshot for checking whether recognition is still current."""
    return copy.deepcopy({"fps": project["fps"], "canvas_duration": project.get("canvas_duration", 0), "clips": project["clips"],
                          "audio_clips": project.get("audio_clips", []),
                          "overlays": [layer for layer in project.get("overlays", [])
                                       if layer["kind"] == "video"]})


def timeline_duration(project):
    return max(sum(clip_duration(clip) for clip in project["clips"]),
               max((layer["end"] for layer in project.get("overlays", [])
                    if layer["kind"] == "video"), default=0), project.get("canvas_duration", 0))


def validate_result(value, duration, fps=None):
    """Reject malformed or oversized worker output before publishing any cues."""
    if not isinstance(value, dict) or not isinstance(value.get("cues"), list):
        raise CaptionFailure("字幕辨識結果格式錯誤，請重新產生。")
    cues, previous_end = [], 0.0
    if len(value["cues"]) > 5000:
        raise CaptionFailure("辨識結果過多，請縮短影片後再試。")
    for cue in value["cues"]:
        if not isinstance(cue, dict):
            raise CaptionFailure("字幕辨識結果格式錯誤，請重新產生。")
        start, end, text = cue.get("start"), cue.get("end"), cue.get("text")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or end > duration + 1e-6
                or start < previous_end - 1e-6 or not isinstance(text, str)
                or fps is not None and end - start + 1e-9 < 1 / fps
                or not text.strip() or len(text) > 500 or len(text.splitlines()) > 10
                or any(ord(char) < 32 and char not in "\n\t" for char in text)):
            raise CaptionFailure("字幕時間或文字不完整，請重新產生。")
        cues.append({"start": float(start), "end": float(end), "text": text.strip()})
        previous_end = float(end)
    if not cues:
        raise CaptionFailure("沒有辨識到語音；請確認影片有人聲，且音量沒有靜音。")
    language = value.get("language", value.get("detected_language"))
    if language is not None and (not isinstance(language, str) or len(language) > 20
                                 or not language.replace("-", "").isalpha()):
        raise CaptionFailure("字幕辨識結果格式錯誤，請重新產生。")
    return {"cues": cues, "detected_language": language, "duration": duration}


class CaptionService:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "captions"
        self.runtime = self.root / "runtime"
        self.models = self.root / "models"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = store._load(self.root)
        self.task = None
        self.cancel_event = threading.Event()
        self.process = None
        self.closing = False
        for job in self.jobs.values():
            if job.get("status") in ACTIVE:
                job.update(status="failed", error="Studio 重新啟動，語音辨識已中斷，請重新產生字幕。",
                           updated_at=now())
                atomic_json(self.root / f"{job['id']}.json", job)
            self._clean_work(job["id"])

    @property
    def python(self):
        return self.runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def runtime_ready(self):
        sites = [self.runtime / "Lib/site-packages"]
        sites.extend((self.runtime / "lib").glob("python*/site-packages"))
        return self.python.is_file() and any(
            all((site / module).is_dir() for module in ("faster_whisper", "ctranslate2", "opencc", "av"))
            for site in sites)

    def model_cached(self):
        return all((self.models / MODEL / name).is_file()
                   for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"))

    def status(self):
        active = next((self.public(job) for job in self.jobs.values()
                       if job.get("status") in ACTIVE), None)
        return {"available": True, "engine": "faster-whisper", "model": MODEL,
                "runtime_ready": self.runtime_ready(), "model_cached": self.model_cached(),
                "model_download_mb": 486, "active_job": active}

    @staticmethod
    def public(job):
        return copy.deepcopy({key: value for key, value in job.items() if key in PUBLIC_KEYS
                              and (key != "cues" or job.get("status") == "completed")})

    def _update(self, job_id, **changes):
        job = self.jobs[job_id]
        job.update(changes, updated_at=now())
        atomic_json(self.root / f"{job_id}.json", job)

    def _progress(self, job_id, stage, value):
        job = self.jobs[job_id]
        if job["status"] != "running" or stage not in STAGES:
            return
        if type(value) is not int or not 0 <= value <= 100:
            return
        if job.get("stage") != stage or value > job.get("progress", 0):
            self._update(job_id, stage=stage, progress=min(99, value))

    def start(self, project_id, payload):
        if not isinstance(payload, dict) or set(payload) - {"language", "traditional"}:
            raise EditorError("字幕設定僅接受語言與繁體中文選項。")
        language, traditional = payload.get("language", "auto"), payload.get("traditional", True)
        if not isinstance(language, str) or language not in LANGUAGES or type(traditional) is not bool:
            raise EditorError("請選擇支援的辨識語言，繁體中文選項必須是開啟或關閉。")
        if self.closing or self.active:
            raise EditorError("已有語音字幕正在產生，請完成或取消後再試。", 409)
        if (self.store.closing or self.store.export_task is not None and not self.store.export_task.done()
                or any(job.get("status") in ACTIVE for job in self.store.exports.values())):
            raise EditorError("影片正在匯出，請完成或取消匯出後再產生語音字幕。", 409)
        project = self.store._get(self.store.projects, project_id, "剪輯專案")
        snapshot = self.store._project(project, project)
        snapshot["updated_at"] = project["updated_at"]
        if not snapshot["clips"] and not any(layer["kind"] == "video" for layer in snapshot["overlays"]) and not snapshot.get("canvas_duration"):
            raise EditorError("請先加入影片，或設定背景片長並加入配音，再產生語音字幕。")
        duration = timeline_duration(snapshot)
        if duration <= 0 or duration > 600 + 1e-6:
            raise EditorError("語音字幕支援最長 10 分鐘的影片。")
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "project_id": project_id, "status": "queued", "stage": "runtime",
               "progress": 0, "created_at": now(), "updated_at": now(), "snapshot": snapshot,
               "source_audio_snapshot": audio_snapshot(snapshot), "language": language,
               "traditional": traditional, "cancel_requested": False}
        atomic_json(self.root / f"{job_id}.json", job)
        self.jobs[job_id] = job
        self.cancel_event = threading.Event()
        self.task = asyncio.create_task(self._run(job_id, self.cancel_event))
        return self.public(job)

    async def _ensure_runtime(self, job_id, event):
        if self.runtime_ready():
            return
        self._update(job_id, stage="runtime", progress=0)
        requirements = Path(__file__).resolve().with_name("caption_requirements.txt")
        if not requirements.is_file():
            raise CaptionFailure("語音字幕的安裝檔案不完整，請更新 Studio 後再試。")
        try:
            async with asyncio.timeout(900):
                if not self.python.is_file():
                    await self._subprocess(job_id, event, [sys.executable, "-m", "venv", str(self.runtime)],
                                           timeout=900, failure="語音字幕環境建立失敗，請確認磁碟空間後重試。")
                await self._subprocess(job_id, event,
                                       [str(self.python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                                        "--only-binary=:all:",
                                        "--index-url", "https://pypi.org/simple", "-r", str(requirements)],
                                       timeout=900, failure="語音字幕套件安裝失敗，請確認網路連線後重試。")
        except TimeoutError as error:
            raise CaptionFailure("語音字幕環境安裝逾時，請確認網路連線後重試。") from error
        if not self.runtime_ready():
            raise CaptionFailure("語音字幕環境尚未準備完成，請重新產生字幕。")

    async def _subprocess(self, job_id, event, command, *, timeout, failure, progress=False):
        """Drain bounded diagnostics and await only a process created by this job."""
        if event.is_set():
            raise ExportCancelled()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        work = self.root / job_id
        work.mkdir(exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=65536, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}, **options)
        self.process = process

        async def stdout():
            pending = b""
            dropping = False
            while True:
                block = await process.stdout.read(8192)
                if not block:
                    return
                if not progress:
                    continue
                for part in block.splitlines(keepends=True):
                    ended = part.endswith(b"\n")
                    if dropping:
                        if ended:
                            dropping = False
                        continue
                    pending += part
                    if len(pending) > 16384:
                        pending = b""
                        dropping = not ended
                        continue
                    if ended:
                        try:
                            item = json.loads(pending)
                            if isinstance(item, dict):
                                self._progress(job_id, item.get("stage"), item.get("progress"))
                        except (ValueError, TypeError):
                            pass
                        pending = b""

        async def stderr():
            log_path = work / "worker.log"
            written = log_path.stat().st_size if log_path.exists() else 0
            with log_path.open("ab") as log:
                while True:
                    block = await process.stderr.read(8192)
                    if not block:
                        return
                    amount = min(len(block), MAX_LOG_BYTES - written)
                    if amount > 0:
                        log.write(block[:amount])
                        written += amount

        readers = [asyncio.create_task(stdout()), asyncio.create_task(stderr())]
        waiter = asyncio.create_task(process.wait())
        try:
            async with asyncio.timeout(timeout):
                while not waiter.done():
                    if event.is_set():
                        raise ExportCancelled()
                    await asyncio.wait([waiter], timeout=.2)
                code = await waiter
                await asyncio.gather(*readers)
                if event.is_set():
                    raise ExportCancelled()
                if code:
                    raise CaptionFailure(failure)
        except TimeoutError as error:
            raise CaptionFailure("語音字幕處理逾時，請確認網路或縮短影片後重試。") from error
        finally:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), 5)
                except TimeoutError:
                    process.kill()
                    await asyncio.shield(waiter)
            await asyncio.gather(*readers, return_exceptions=True)
            if not waiter.done():
                await asyncio.shield(waiter)
            if self.process is process:
                self.process = None

    async def _recognize(self, job_id, event, input_path, output_path, duration):
        job = self.jobs[job_id]
        command = [str(self.python), "-u", str(Path(__file__).resolve().with_name("editor_caption_worker.py")),
                   "--input", str(input_path), "--output", str(output_path),
                   "--model-dir", str(self.models), "--language", job["language"],
                   "--fps", str(job["snapshot"]["fps"]), "--duration", str(duration)]
        if job["traditional"]:
            command.append("--traditional")
        self._update(job_id, stage="model" if not self.model_cached() else "transcribing", progress=0)
        await self._subprocess(job_id, event, command, timeout=1800, progress=True,
                               failure="語音辨識失敗；請確認模型下載與音訊正常後重試。")
        if not output_path.is_file() or output_path.stat().st_size > MAX_RESULT_BYTES:
            raise CaptionFailure("沒有取得完整字幕結果，請重新產生。")
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise CaptionFailure("字幕辨識結果無法讀取，請重新產生。") from error
        return validate_result(value, duration, job["snapshot"]["fps"])

    async def _run(self, job_id, event):
        job = self.jobs[job_id]
        worker = None
        work = self.root / job_id
        work.mkdir(exist_ok=True)
        input_path, output_path = work / "audio.wav", work / "result.json"
        try:
            self._update(job_id, status="running")
            await self._ensure_runtime(job_id, event)
            if event.is_set():
                raise ExportCancelled()
            source = job["snapshot"]
            all_clips = source["clips"] + source.get("audio_clips", [])
            all_clips += [layer for layer in source.get("overlays", []) if layer["kind"] == "video"]
            paths = {clip["media_id"]: self.store.media_path(clip["media_id"]) for clip in all_clips}
            self._update(job_id, stage="audio", progress=0)
            loop = asyncio.get_running_loop()
            callback = lambda value: loop.call_soon_threadsafe(self._progress, job_id, "audio", value)
            worker = asyncio.create_task(asyncio.to_thread(
                render_caption_audio, source, paths, input_path, event, callback))
            stats = await asyncio.shield(worker)
            if event.is_set():
                raise ExportCancelled()
            if (not isinstance(stats, dict) or not isinstance(stats.get("peak"), (int, float))
                    or not math.isfinite(stats["peak"]) or stats["peak"] < 1e-5):
                raise CaptionFailure("時間軸沒有可辨識的音訊；請確認影片或配音有人聲，且沒有靜音。")
            duration = min(stats["duration"], timeline_duration(source))
            result = await self._recognize(job_id, event, input_path, output_path, duration)
            if event.is_set():
                raise ExportCancelled()
            self._update(job_id, status="completed", stage="transcribing", progress=100, **result)
        except (ExportCancelled, asyncio.CancelledError):
            event.set()
            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            self._update(job_id, status="cancelled", error="語音字幕已取消。")
        except Exception as error:
            LOGGER.exception("Editor caption job failed: %s", job_id)
            message = str(error) if isinstance(error, CaptionFailure) else "語音字幕產生失敗，請確認素材可播放後重試。"
            self._update(job_id, status="failed", error=message)
        finally:
            # A cancelled to_thread task still owns this WAV until it finishes.
            if worker is not None and not worker.done():
                event.set()
                await asyncio.gather(worker, return_exceptions=True)
            self._clean_work(job_id)

    def _clean_work(self, job_id):
        check_id(job_id)
        work = self.root / job_id
        # A Studio crash can leave a previous worker holding its WAV on
        # Windows. That must not prevent the next Studio instance starting.
        if work.exists() and work.resolve().parent != self.root.resolve():
            LOGGER.warning("Skipping caption work outside its job directory: %s", job_id)
            return
        for name in ("audio.wav", "result.json", "worker.log"):
            try:
                (work / name).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Caption temporary file is still in use: %s/%s", job_id, name)
        try:
            work.rmdir()
        except (FileNotFoundError, OSError):
            pass

    async def cancel(self, job_id):
        job = self.store._get(self.jobs, job_id, "語音字幕工作")
        if job["status"] in ACTIVE:
            self.cancel_event.set()
            self._update(job_id, cancel_requested=True)
            # Return immediately while the owned audio worker/subprocess winds
            # down. Polling continues until cleanup marks it cancelled.
        return self.public(job)

    async def shutdown(self):
        self.closing = True
        if self.active:
            self.cancel_event.set()
            await asyncio.shield(self.task)


def register_editor_captions(app):
    service = CaptionService(app["editor"])
    app["editor_captions"] = service

    async def status(request):
        return web.json_response(service.status(), headers={"Cache-Control": "no-store"})

    async def start(request):
        job = service.start(request.match_info["project_id"], await request.json())
        return web.json_response({"job": job}, status=202)

    async def get_job(request):
        job = service.store._get(service.jobs, request.match_info["caption_id"], "語音字幕工作")
        return web.json_response({"job": service.public(job)}, headers={"Cache-Control": "no-store"})

    async def cancel(request):
        return web.json_response({"job": await service.cancel(request.match_info["caption_id"])})

    async def cleanup(_):
        await service.shutdown()

    app.router.add_get("/api/editor/captions/status", status)
    app.router.add_post("/api/editor/projects/{project_id}/captions", start)
    app.router.add_get("/api/editor/captions/{caption_id}", get_job)
    app.router.add_post("/api/editor/captions/{caption_id}/cancel", cancel)
    app.on_cleanup.append(cleanup)
    return service
