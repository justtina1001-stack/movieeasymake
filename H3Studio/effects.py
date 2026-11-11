"""Reviewed effect jobs reuse Studio's generation queue and preserve their source."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
import av
from PIL import Image, ImageOps

from domain import compile_request, required_asset_ids
from effects_spec import EFFECT_MODELS, MAX_RETIME_SOURCE_DURATION
from generation_review import GenerationReviewError, GenerationReviewStore, prompt_review_item, review_digest
from queue_cancel import QueueCancelError
from video_models import video_model_profiles


SAFE_ID = re.compile(r"[a-f0-9]{32}\Z")
SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\Z")
ACTIVE = {"queued", "generating", "matting", "looping", "checking", "exporting"}
CHILD_ACTIVE = {"queued", "preparing", "running", "awaiting_review"}
MODELS = set(EFFECT_MODELS)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
OUTPUT_EXTENSIONS = {".zip", ".mp4", ".png", ".json"}
POSTPROCESS_FIELDS = {"timing_mode", "trim_start", "duration", "fps", "width", "height", "black_level", "softness",
                      "temporal_smoothing", "loop_blend", "playback", "anchor"}
MAX_SOURCE_BYTES = 1024**3
STAGE_LABELS = {"queued": "等待處理", "generating": "生成原始影片", "matting": "整理透明邊緣",
                "looping": "整理循環接縫", "checking": "檢查透明與循環品質", "exporting": "匯出 PNG 序列", "completed": "處理完成",
                "needs_adjustment": "需要檢查與調整", "failed": "處理失敗", "cancelled": "已取消",
                "interrupted": "處理中斷"}


class EffectStateError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def browser_spec(value):
    """Match JSON.stringify's number representation before issuing receipts."""
    if isinstance(value, dict):
        return {key: browser_spec(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_spec(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_snapshot(path):
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("來源檔案不存在或超過 1 GB，請提供較短的特效素材。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def inspect_video(path):
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("請上傳 MP4、MOV、WebM、MKV、AVI 或 M4V 影片。")
    try:
        with av.open(str(path)) as source:
            if not source.streams.video:
                raise ValueError("來源檔案沒有影片畫面。")
            stream = source.streams.video[0]
            width, height = stream.codec_context.width, stream.codec_context.height
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise ValueError("來源影片尺寸無效或超過 8192 像素，請先縮小。")
            fps = float(stream.average_rate or stream.guessed_rate or 24)
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else (
                float(source.duration / av.time_base) if source.duration is not None else None)
            if duration is None or not math.isfinite(duration) or duration <= 0:
                raise ValueError("來源影片無法確認長度，請先轉成一般 MP4 後重試。")
            return {"width": width, "height": height, "fps": round(fps, 5), "duration": duration,
                    "has_audio": bool(source.streams.audio)}
    except av.error.FFmpegError as error:
        raise ValueError("來源影片無法解碼，請重新上傳或先轉成 MP4。") from error


def check_source_range(spec, info):
    try:
        source_duration = float(info["duration"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("來源影片無法確認有效長度，請重新匯入來源。") from None
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise ValueError("來源影片無法確認有效長度，請重新匯入來源。")
    if spec.get("timing_mode", "trim") == "retime":
        if float(spec["trim_start"]) != 0:
            raise ValueError("整段變速必須從原片起點開始，請將擷取起點設為 0。")
        if source_duration > MAX_RETIME_SOURCE_DURATION:
            raise ValueError(f"整段變速的來源影片不可超過 {MAX_RETIME_SOURCE_DURATION:g} 秒，請先縮短來源。")
        return
    duration = round(float(spec["duration"]) * int(spec["fps"])) / int(spec["fps"])
    if float(spec["trim_start"]) + duration > source_duration + 0.001:
        raise ValueError("成品取用區間超過原片長度；請縮短成品或調整擷取起點，不會自動拉長或變速。")


def process_video(*args, **kwargs):
    from effects_processing import process_effect_video
    return process_effect_video(*args, **kwargs)


class EffectJobManager:
    def __init__(self, app, data_dir):
        self.app = app
        self.assets, self.generator = app["assets"], app["jobs"]
        self.job_dir, self.output_dir = Path(data_dir) / "effect_jobs", Path(data_dir) / "effect_outputs"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.reviews = GenerationReviewStore()
        self.jobs, self.tasks, self.cancel_events = {}, {}, {}
        self.workers = {}
        self.job_locks = {}
        self.processing_lock = asyncio.Lock()
        self.closing = False
        for path in self.job_dir.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict) or job.get("id") != path.stem or job.get("kind") != "effect":
                    continue
                if job.get("status") in ACTIVE:
                    job.update(status="interrupted", stage="處理中斷", error="Studio 已重新啟動；原片已保留，可重試後製，未自動重新生成。")
                    atomic_json(path, job)
                self.jobs[job["id"]] = job
            except (OSError, ValueError, UnicodeError):
                continue

    def get(self, identifier):
        if not SAFE_ID.fullmatch(identifier or "") or identifier not in self.jobs:
            raise KeyError(identifier)
        return self.jobs[identifier]

    def update(self, identifier, **changes):
        job = self.jobs[identifier]
        job.update(changes, updated_at=utc_now())
        if "status" in changes and "stage" not in changes:
            job["stage"] = STAGE_LABELS.get(changes["status"], changes["status"])
        atomic_json(self.job_dir / f"{identifier}.json", job)

    def public(self, job):
        result = copy.deepcopy(job)
        result.pop("prepared", None)
        result.pop("compiled", None)
        result.pop("source_snapshot", None)
        for file in result.get("files", []):
            file.pop("relative_path", None)
        return result

    def source_asset(self, spec):
        identifier = str(spec.get("source_asset_id") or "")
        if not SAFE_ID.fullmatch(identifier):
            raise ValueError("請先上傳特效來源素材。")
        path = self.assets.path_for(identifier)
        if spec["source_mode"] == "image" and path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("參考圖模式需要 PNG、JPG、WebP 或 BMP 圖片。")
        return path

    def prepare_image(self, path, spec, width, height):
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGBA")
            contained = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            left = (width - contained.width) // 2
            top = height - contained.height if spec["anchor"] == "bottom_center" else (height - contained.height) // 2
            canvas.alpha_composite(contained, (left, top))
            return self.assets.save_image(canvas.convert("RGB"), "effect_reference_black.png", "effects-prepared")

    async def preview(self, payload):
        from effects_spec import normalize_effect_spec, build_effect_generation_payload, validate_effect_compiled
        spec = normalize_effect_spec(payload)
        info, snapshot, compiled, prepared, generation = None, None, None, None, None
        warnings = ["特效透明度由黑底素材推估，請在白底與棋盤格預覽確認柔光、煙塵及邊緣。"]
        if spec["source_mode"] in {"image", "video"}:
            path = self.source_asset(spec)
            snapshot = await asyncio.to_thread(source_snapshot, path)
            if spec["source_mode"] == "video":
                info = await asyncio.to_thread(inspect_video, path)
                check_source_range(spec, info)
        if spec["source_mode"] != "video":
            raw = build_effect_generation_payload(spec)
            if spec["source_mode"] == "image":
                initial = compile_request(raw)
                reference = await asyncio.to_thread(self.prepare_image, path, spec, initial.width, initial.height)
                raw["first_image_asset_id"] = reference["id"]
            prepared = await self.app["prepare_generation_payload"](raw)
            compiled = compile_request(prepared)
            validate_effect_compiled(compiled)
            for identifier in required_asset_ids(compiled):
                self.assets.path_for(identifier)
            if compiled.video_model not in MODELS:
                raise ValueError("特效動畫目前支援 H3、FastH3、Pika H3 Flex 與 FreeVideo／VDN-H3。")
            check_source_range(spec, {"duration": compiled.actual_duration})
            generation = prompt_review_item(compiled, prepared=prepared)
            warnings.extend(generation.get("warnings") or [])
        context = {"kind": "effects", "spec": spec, "prepared": prepared, "source_info": info,
                   "source_snapshot": snapshot}
        public_spec = browser_spec(spec)
        receipt = self.reviews.issue(public_spec, public_spec, compiled, context=context)
        translation = copy.deepcopy((prepared or {}).get("h3_translation") or {})
        if translation.get("status") in {"unavailable", "failed"}:
            warnings.append("英文整理未完成；目前顯示原文稿，須明確確認使用目前原文稿後才會送出。")
        return {"spec": public_spec, "prompt": compiled.prompt if compiled else "使用匯入影片，執行黑底透明處理、循環檢查及固定畫布序列輸出；不送出影片生成。",
                "generation": generation, "source_info": info, "warnings": list(dict.fromkeys(warnings)), **receipt}

    async def create(self, submitted):
        if not isinstance(submitted, dict):
            raise GenerationReviewError("特效確認資料格式錯誤，請重新預覽。")
        submitted = copy.deepcopy(submitted)
        original_confirmed = submitted.pop("original_prompt_confirmed", False) is True
        token, reviewed = self.reviews.claim(submitted)
        try:
            context = reviewed.get("context") or {}
            if context.get("kind") != "effects":
                raise GenerationReviewError("這份確認稿不屬於特效動畫，請重新預覽。")
            if ((context.get("prepared") or {}).get("h3_translation") or {}).get("status") in {"unavailable", "failed"} and not original_confirmed:
                raise GenerationReviewError("英文整理未完成，請先明確確認使用目前原文稿，或重新預覽重試整理；尚未送出生成。")
            spec = context["spec"]
            if spec["source_mode"] in {"video", "image"}:
                current = await asyncio.to_thread(source_snapshot, self.source_asset(spec))
                if current != context["source_snapshot"]:
                    raise GenerationReviewError("來源素材內容已改變，請重新預覽。")
            identifier = uuid.uuid4().hex
            job = {"id": identifier, "kind": "effect", "name": spec.get("name") or "特效動畫", "spec": spec,
                   "original_spec": copy.deepcopy(spec),
                   "status": "queued", "stage": STAGE_LABELS["queued"], "progress": 0, "error": None,
                   "child_job_id": None, "source_info": context.get("source_info"), "source_snapshot": context.get("source_snapshot"),
                   "source_file": None, "source_sha256": None, "prepared": context.get("prepared"),
                   "reviewed_prompt": reviewed["compiled"].prompt if reviewed.get("compiled") else None,
                   "original_prompt_confirmed": original_confirmed,
                   "recipe_digest": review_digest({"spec": spec, "prompt": reviewed["compiled"].prompt if reviewed.get("compiled") else None}),
                   "files": [], "qa": None, "result": None, "revision": 0, "attempts": [],
                   "operation_revision": 0,
                   "created_at": utc_now(), "updated_at": utc_now()}
            self.jobs[identifier] = job
            self.update(identifier)
            self.cancel_events[identifier] = threading.Event()
            self.tasks[identifier] = asyncio.create_task(self._run(identifier, reviewed.get("compiled")))
            self.reviews.consume(token)
            return self.public(job)
        except BaseException:
            self.reviews.release(token)
            raise

    async def _source_from_child(self, identifier):
        job = self.get(identifier)
        child = self.generator.jobs.get(job.get("child_job_id"))
        if not child or child.get("parent_job_id") != identifier:
            raise ValueError("找不到這筆特效的原始生成工作；請保留來源重新建立特效。")
        if child.get("status") != "completed":
            raise ValueError("原始影片尚未完成，不能重試後製；不會自動重新送出生成。")
        return await self.generator.job_output_path(child)

    async def _copy_source(self, identifier, source, *, spec=None):
        job = self.get(identifier)
        directory = self.output_dir / identifier
        directory.mkdir(parents=True, exist_ok=True)
        extension = source.suffix.lower() if source.suffix.lower() in VIDEO_EXTENSIONS else ".mp4"
        target = directory / ("source" + extension)
        temporary = directory / ("source.part" + extension)
        try:
            await asyncio.to_thread(shutil.copyfile, source, temporary)
            snapshot = await asyncio.to_thread(source_snapshot, temporary)
            if job["spec"]["source_mode"] == "video" and job.get("source_snapshot") and snapshot != job["source_snapshot"]:
                raise ValueError("來源影片在確認後已改變，請重新預覽後建立特效。")
            info = await asyncio.to_thread(inspect_video, temporary)
            check_source_range(spec if spec is not None else job["spec"], info)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        self.update(identifier, source_file=target.name, source_sha256=snapshot["sha256"], source_info=info,
                    files=[{"name": target.name, "label": "原始影片", "kind": "source", "relative_path": target.name,
                            "url": f"/api/effects/jobs/{identifier}/files/{target.name}"}])
        return target

    def _progress(self, identifier, stage, fraction, revision):
        job = self.jobs.get(identifier)
        if self.closing or not job or job.get("revision") != revision or job["status"] not in ACTIVE or self.cancel_events[identifier].is_set():
            return
        state = stage if stage in {"matting", "looping", "checking", "exporting"} else "matting"
        fraction = max(0.0, min(1.0, float(fraction)))
        self.update(identifier, status=state, progress=round(fraction * 100, 1), stage_progress=round(fraction * 100, 1))

    async def _run(self, identifier, compiled=None, *, retry=False):
        job = self.get(identifier)
        cancel = self.cancel_events[identifier]
        try:
            if not job.get("source_file"):
                if job["spec"]["source_mode"] == "video":
                    source = self.source_asset(job["spec"])
                elif retry:
                    source = await self._source_from_child(identifier)
                else:
                    if compiled is None:
                        raise ValueError("找不到已確認的生成配方，請重新預覽並建立特效。")
                    child = self.generator.create(compiled, job["prepared"], hidden=True, parent_job_id=identifier)
                    self.update(identifier, status="generating", child_job_id=child["id"])
                    while True:
                        if cancel.is_set():
                            raise asyncio.CancelledError
                        child = self.generator.jobs[child["id"]]
                        if child["status"] == "completed":
                            break
                        if child["status"] not in CHILD_ACTIVE:
                            raise ValueError(child.get("error") or "原始影片生成未完成，請檢查原片工作。")
                        self.update(identifier, generation_progress=child.get("progress", 0), progress=child.get("progress", 0),
                                    stage="等待生成佇列" if child["status"] == "queued" else child.get("current_node") or STAGE_LABELS["generating"])
                        await asyncio.sleep(0.5)
                    source = await self._source_from_child(identifier)
                if cancel.is_set():
                    raise asyncio.CancelledError
                source = await self._copy_source(identifier, source)
            else:
                source = self.output_dir / identifier / job["source_file"]
                if not source.is_file() or (await asyncio.to_thread(source_snapshot, source))["sha256"] != job["source_sha256"]:
                    raise ValueError("保留的原片不存在或已改變，請重新匯入來源；不會自動重新生成。")
            async with self.processing_lock:
                if cancel.is_set():
                    raise asyncio.CancelledError
                revision = int(job.get("revision", 0)) + 1
                relative = f"r{revision:04d}"
                directory = self.output_dir / identifier / relative
                directory.mkdir(parents=True, exist_ok=False)
                self.update(identifier, status="matting", progress=0, revision=revision)
                attempts = [*job.get("attempts", []), {"revision": revision, "status": "running",
                            "spec": copy.deepcopy(job["spec"]), "started_at": utc_now()}]
                self.update(identifier, attempts=attempts)
                loop = asyncio.get_running_loop()
                def report(stage, fraction):
                    try:
                        loop.call_soon_threadsafe(self._progress, identifier, stage, fraction, revision)
                    except RuntimeError:
                        pass
                started = time.monotonic()
                worker = asyncio.create_task(asyncio.to_thread(process_video, source, directory, job["spec"], progress=report, cancelled=cancel.is_set))
                self.workers[identifier] = worker
                worker.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                try:
                    result = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    # Keep the CPU queue occupied until cooperative cancellation
                    # stops writes from this revision, then permit a retry.
                    await asyncio.gather(worker, return_exceptions=True)
                    raise
                if cancel.is_set():
                    raise asyncio.CancelledError
                # Published URLs include their revision; older approved output
                # stays usable while a new postprocessing attempt is produced.
                published = copy.deepcopy(job.get("files", []))
                for file in result.get("files", []):
                    name = file.get("name", "")
                    if not SAFE_FILE.fullmatch(name) or Path(name).suffix not in OUTPUT_EXTENSIONS or not (directory / name).is_file():
                        raise ValueError("特效輸出檔案不完整，請保留原片後重試後製。")
                    public_name = relative + "_" + name
                    published.append({"name": public_name, "label": file.get("label") or name, "kind": file.get("kind", "metadata"),
                                      "relative_path": relative + "/" + name,
                                      "url": f"/api/effects/jobs/{identifier}/files/{public_name}"})
                qa = result.get("qa") or {"status": "needs_adjustment", "warnings": ["輸出缺少檢查結果，請人工確認。"]}
                if qa.get("status") not in {"passed", "needs_adjustment"}:
                    raise ValueError("特效檢查結果格式錯誤，請重新執行後製。")
                status = "completed" if qa.get("status") == "passed" else "needs_adjustment"
                summary = {key: value for key, value in result.items() if key != "files"}
                attempts = copy.deepcopy(job.get("attempts", []))
                attempts[-1].update(status=status, elapsed_seconds=round(time.monotonic() - started, 3), finished_at=utc_now())
                self.update(identifier, status=status, progress=100, result=summary, qa=qa, files=published, error=None,
                            attempts=attempts, finished_at=utc_now())
        except asyncio.CancelledError:
            state = "interrupted" if self.closing else "cancelled"
            self._finish_failed_attempt(identifier, state, "處理已中止。")
            self.update(identifier, status=state, error="Studio 關閉，原片已保留，可重試後製。" if self.closing else "特效工作已取消，原片仍保留。")
        except Exception as error:
            state = "cancelled" if cancel.is_set() else "failed"
            self._finish_failed_attempt(identifier, state, str(error))
            self.update(identifier, status=state, error=str(error))

    def _finish_failed_attempt(self, identifier, state, error):
        job = self.get(identifier)
        attempts = copy.deepcopy(job.get("attempts", []))
        if attempts and attempts[-1].get("revision") == job.get("revision") and attempts[-1].get("status") == "running":
            attempts[-1].update(status=state, error=error, finished_at=utc_now())
            self.update(identifier, attempts=attempts)

    async def retry(self, identifier, payload=None):
        expected_operation = self.get(identifier).get("operation_revision", 0)
        async with self.job_locks.setdefault(identifier, asyncio.Lock()):
            if self.get(identifier).get("operation_revision", 0) != expected_operation:
                raise EffectStateError("特效狀態已更新，請重新檢查目前工作後再重試；未重複建立後製。")
            return await self._retry(identifier, payload)

    async def _retry(self, identifier, payload=None):
        from effects_spec import normalize_effect_spec
        job = self.get(identifier)
        if (job["status"] in ACTIVE or (self.tasks.get(identifier) and not self.tasks[identifier].done())
                or (self.workers.get(identifier) and not self.workers[identifier].done())):
            raise EffectStateError("特效仍在處理，請完成或取消後再重試。")
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or set(payload) - {"postprocess"}:
            raise ValueError("重試只接受 postprocess 後製設定，不可改動來源、模型、敘述或 Seed。")
        changes = payload.get("postprocess", {})
        if not isinstance(changes, dict) or set(changes) - POSTPROCESS_FIELDS:
            raise ValueError("後製只能調整時間模式、擷取區間、畫布、幀率、去背與循環設定。")
        # A generated source can contain aligned extra frames beyond its nominal
        # requested length. Validate the postprocessing spec independently, then
        # check its actual source interval instead of silently clamping it.
        candidate = {**job["spec"], **changes, "source_mode": "video",
                     "source_asset_id": job["spec"].get("source_asset_id") or "0" * 32}
        spec = normalize_effect_spec(candidate)
        for key in ("source_mode", "source_asset_id", "source_duration"):
            spec[key] = job["spec"][key]
        source = None
        if job.get("source_file"):
            source = self.output_dir / identifier / job["source_file"]
            snapshot = await asyncio.to_thread(source_snapshot, source)
            if snapshot["sha256"] != job["source_sha256"]:
                raise ValueError("保留原片已改變，請重新匯入；未修改特效設定。")
        else:
            source = self.source_asset(job["spec"]) if job["spec"]["source_mode"] == "video" else await self._source_from_child(identifier)
        info = await asyncio.to_thread(inspect_video, source)
        check_source_range(spec, info)
        if not job.get("source_file"):
            await self._copy_source(identifier, source, spec=spec)
        self.cancel_events[identifier] = threading.Event()
        self.update(identifier, status="queued", progress=0, error=None, spec=spec,
                    original_spec=job.get("original_spec") or copy.deepcopy(job["spec"]),
                    operation_revision=job.get("operation_revision", 0) + 1)
        self.tasks[identifier] = asyncio.create_task(self._run(identifier, retry=True))
        return self.public(job)

    async def cancel(self, identifier):
        self.get(identifier)
        async with self.job_locks.setdefault(identifier, asyncio.Lock()):
            return await self._cancel(identifier)

    async def _cancel(self, identifier):
        job = self.get(identifier)
        if job["status"] not in ACTIVE:
            return self.public(job)
        child = self.generator.jobs.get(job.get("child_job_id"))
        if child and child.get("parent_job_id") == identifier and child.get("status") in CHILD_ACTIVE:
            await self.generator.cancel(child["id"])
        self.cancel_events[identifier].set()
        task = self.tasks.get(identifier)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.update(identifier, status="cancelled", error="特效工作已取消，已完成的原片仍保留。",
                    operation_revision=job.get("operation_revision", 0) + 1)
        return self.public(job)

    def file_path(self, identifier, name):
        job = self.get(identifier)
        if not SAFE_FILE.fullmatch(name or ""):
            raise KeyError(name)
        file = next((entry for entry in job.get("files", []) if entry.get("name") == name), None)
        if not file:
            raise KeyError(name)
        root = (self.output_dir / identifier).resolve()
        path = (root / file["relative_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError(name)
        return path

    async def shutdown(self):
        self.closing = True
        for identifier, task in self.tasks.items():
            if not task.done():
                self.cancel_events[identifier].set()
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await asyncio.gather(*self.workers.values(), return_exceptions=True)


def register_effect_routes(app, static_dir, data_dir):
    manager = EffectJobManager(app, data_dir)
    app["effect_jobs"] = manager
    app["effects_reviews"] = manager.reviews

    @web.middleware
    async def errors(request, handler):
        if not request.path.startswith("/api/effects/"):
            return await handler(request)
        try:
            return await handler(request)
        except KeyError:
            return web.json_response({"error": "找不到特效工作或輸出檔案。"}, status=404)
        except GenerationReviewError as error:
            return web.json_response({"error": str(error)}, status=409)
        except EffectStateError as error:
            return web.json_response({"error": str(error)}, status=409)
        except QueueCancelError as error:
            return web.json_response({"error": str(error)}, status=error.status)
        except (ValueError, TypeError, OSError) as error:
            return web.json_response({"error": str(error)}, status=400)
    app.middlewares.append(errors)

    def json_response(value, status=200):
        return web.json_response(value, status=status, headers={"Cache-Control": "no-store"})

    async def page(request):
        return web.FileResponse(Path(static_dir) / "effects.html")

    async def catalog(request):
        from effects_spec import effect_catalog, normalize_effect_spec
        return json_response({"items": effect_catalog(), "defaults": normalize_effect_spec({}),
                              "limits": {"max_frames": 300, "max_source_bytes": MAX_SOURCE_BYTES,
                                         "source_duration_min": 5, "source_duration_max": 15},
                              "models": sorted(MODELS)})

    async def status(request):
        try:
            stats = await app["comfy"].system_stats()
            inventory = await app["comfy"].model_inventory() if stats else {}
            profiles = [item for item in video_model_profiles(inventory) if item["id"] in MODELS]
            for profile in profiles:
                profile["ready"] = any(profile.get("mode_ready", {}).get(mode) is True for mode in ("t2v", "fl2va"))
                if not profile["ready"]:
                    profile["error"] = profile.get("error") or "特效需要對應的文生／單圖影片配方模型，請先補齊。"
            return json_response({"ready": bool(stats) and any(item["ready"] for item in profiles), "import_ready": True,
                                  "models": profiles, "error": None if stats else "影片引擎暫時無法連線；仍可匯入既有影片進行特效後製。"})
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            return json_response({"ready": False, "import_ready": True, "models": [], "error": str(error)})

    async def preview(request):
        return json_response(await manager.preview(await request.json()))

    async def create(request):
        return json_response(await manager.create(await request.json()), 202)

    async def listing(request):
        items = sorted(manager.jobs.values(), key=lambda item: item["created_at"], reverse=True)
        return json_response({"items": [manager.public(item) for item in items]})

    async def detail(request):
        return json_response(manager.public(manager.get(request.match_info["job_id"])))

    async def retry(request):
        payload = await request.json() if request.can_read_body else {}
        return json_response(await manager.retry(request.match_info["job_id"], payload), 202)

    async def cancel(request):
        return json_response(await manager.cancel(request.match_info["job_id"]))

    async def file(request):
        path = manager.file_path(request.match_info["job_id"], request.match_info["name"])
        headers = {"Cache-Control": "no-store"}
        if request.query.get("download") == "1" or path.suffix == ".zip":
            headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
        return web.FileResponse(path, headers=headers)

    async def cleanup(_):
        await manager.shutdown()

    app.router.add_get("/effects", page)
    app.router.add_get("/api/effects/catalog", catalog)
    app.router.add_get("/api/effects/status", status)
    app.router.add_post("/api/effects/preview", preview)
    app.router.add_get("/api/effects/jobs", listing)
    app.router.add_post("/api/effects/jobs", create)
    app.router.add_get("/api/effects/jobs/{job_id}", detail)
    app.router.add_post("/api/effects/jobs/{job_id}/retry", retry)
    app.router.add_post("/api/effects/jobs/{job_id}/cancel", cancel)
    app.router.add_get("/api/effects/jobs/{job_id}/files/{name}", file)
    app.on_cleanup.append(cleanup)
    return manager
