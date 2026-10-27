"""Persistent, explicitly requested screenplay-to-local-video workflows."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from custom_loras import normalize_loras, validate_loras_for_family, validate_lora_requirements
from script_video_format import normalize_video_settings, video_dimensions
from video_models import VIDEO_MODEL_FAST


SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
ACTIVE = {"queued", "running"}
VIDEO_ACTIVE = {"queued", "preparing", "running"}


class ScriptVideoError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class ScriptVideoManager:
    def __init__(self, app: Any, script_manager: Any, data_dir: Path):
        self.app = app
        self.scripts = script_manager
        self.directory = Path(data_dir) / "scriptwriting" / "video-pipelines"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pipelines: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.closed = False
        self.start_lock = asyncio.Lock()
        for path in self.directory.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ScriptVideoError(f"無法讀取自動影片流程 {path.name}；請保留資料並檢查。", 500) from error
            if not isinstance(value, dict) or value.get("id") != path.stem:
                raise ScriptVideoError(f"自動影片流程 {path.name} 格式錯誤；請保留資料並檢查。", 500)
            if value.get("status") in ACTIVE:
                value.update(status="interrupted", error="Studio 重新啟動，自動影片流程已暫停；按接續可保留已完成片段。", updated_at=utc_now())
                _atomic_json(path, value)
            self.pipelines[path.stem] = value

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        if not isinstance(pipeline_id, str) or not SAFE_ID.fullmatch(pipeline_id) or pipeline_id not in self.pipelines:
            raise ScriptVideoError("找不到自動影片流程。", 404)
        return copy.deepcopy(self.pipelines[pipeline_id])

    def list_pipelines(self) -> list[dict[str, Any]]:
        return sorted((copy.deepcopy(item) for item in self.pipelines.values()),
                      key=lambda item: (item.get("created_at", ""), item["id"]), reverse=True)

    def _save(self, pipeline: dict[str, Any]) -> None:
        _atomic_json(self.directory / f"{pipeline['id']}.json", pipeline)

    def _update(self, pipeline: dict[str, Any], **changes: Any) -> None:
        pipeline.update(changes)
        pipeline["updated_at"] = utc_now()
        self._save(pipeline)

    def _renderer(self) -> Any:
        renderer = self.app.get("script_video_renderer")
        if renderer is None:
            raise ScriptVideoError("自動影片服務尚未啟用，請重新啟動更新後的 Studio。", 503)
        return renderer

    async def readiness(self) -> dict[str, Any]:
        try:
            result = await self._renderer().readiness()
            if not isinstance(result, dict):
                raise ScriptVideoError("影片引擎狀態格式錯誤。", 503)
            value = {"ready": result.get("ready") is True, "error": result.get("error")}
            if result.get("video_model_supported") is True:
                value.update(video_model_supported=True, video_models=result.get("video_models", []))
            return value
        except Exception as error:
            return {"ready": False, "error": str(error)}

    def _check_duplicate(self, project_id: str, *, exclude: str | None = None) -> None:
        for item in self.pipelines.values():
            task = self.tasks.get(item["id"])
            if (item["id"] != exclude and item.get("project_id") == project_id
                    and (item.get("status") in ACTIVE or task is not None and not task.done())):
                raise ScriptVideoError("這份劇本已有自動影片流程正在執行，請等待完成或取消後再試。", 409)

    def _check_snapshot(self, snapshot: dict[str, Any]) -> None:
        try:
            settings = normalize_video_settings(snapshot.get("video_settings"))
            if settings.get("video_model") == VIDEO_MODEL_FAST:
                snapshot["custom_loras"] = normalize_loras(snapshot.get("custom_loras", []))
            else:
                snapshot["custom_loras"] = validate_loras_for_family(snapshot.get("custom_loras", []), "ref2va")
                validate_lora_requirements(snapshot["custom_loras"], "ref2va")
        except ValueError as error:
            raise ScriptVideoError(str(error)) from error
        duration = snapshot.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 5 <= duration <= 600:
            raise ScriptVideoError("自動影片目前支援 5～600 秒，最多 50 個生成片段。")
        references = snapshot.get("character_references", [])
        if not any(item.get("image_asset_id") for item in references):
            raise ScriptVideoError("自動影片需要至少一張角色、物件或場景參考圖片。請先上傳圖片，不會改用純文字生成。")
        for item in references:
            if item.get("type") in {"character", "creature"}:
                if not item.get("image_asset_id") or not str(item.get("appearance", "")).strip():
                    raise ScriptVideoError(f"角色「{item.get('name', '')}」需要參考圖片與外觀描述，請補齊後再生成影片。")
            if len(str(item.get("appearance", ""))) > 1200:
                raise ScriptVideoError(f"素材「{item.get('name', '')}」的外觀描述超過 H3 的 1200 字上限，請縮短。")
        self.scripts._validate_reference_assets(references)

    async def preflight(self, snapshot: dict[str, Any]) -> None:
        self._check_snapshot(snapshot)
        await self._renderer().preflight(copy.deepcopy(snapshot))
        status = await self.readiness()
        if not status["ready"]:
            raise ScriptVideoError(status["error"] or "本機影片引擎尚未準備完成，請先啟動並補齊模型。", 503)

    @staticmethod
    def _validate_timeline(snapshot: dict[str, Any]) -> None:
        if snapshot.get("content"):
            from scriptwriting import validate_script_timeline
            validate_script_timeline(snapshot["content"], snapshot["duration"])

    async def start(self, raw: Any, *, allow_active_source: bool = False) -> dict[str, Any]:
        if self.closed:
            raise ScriptVideoError("自動影片服務正在關閉。", 503)
        if not isinstance(raw, dict) or set(raw) - {"project_id", "source_job_id"}:
            raise ScriptVideoError("自動影片流程格式錯誤。")
        async with self.start_lock:
            project = self.scripts.get_project(raw.get("project_id"))
            self._check_duplicate(project["id"])
            source_id = raw.get("source_job_id")
            if source_id is not None and (not isinstance(source_id, str) or not SAFE_ID.fullmatch(source_id)):
                raise ScriptVideoError("來源生成紀錄 ID 格式錯誤。")
            source = self.scripts.get_job(source_id) if source_id else None
            if source:
                if source.get("project_id") != project["id"] or source.get("action") not in {"screenplay", "rewrite"}:
                    raise ScriptVideoError("請選擇同一份劇本已完成的劇本編寫或改寫紀錄。")
                allowed = {"completed", "queued", "running"} if allow_active_source else {"completed"}
                if source.get("status") not in allowed or source.get("truncated"):
                    raise ScriptVideoError("來源劇本尚未完成或內容已截斷，請先完成劇本再生成影片。", 409)
                if source.get("status") == "completed" and not str(source.get("content", "") or "").strip():
                    raise ScriptVideoError("來源劇本沒有可使用的正文，請先完成劇本再生成影片。")
                snapshot = copy.deepcopy(source["input"]["project"] if allow_active_source else project)
                if source.get("status") == "completed":
                    snapshot["content"] = source["content"]
            else:
                snapshot = copy.deepcopy(project)
            if not source or source.get("status") == "completed":
                self._validate_timeline(snapshot)
            await self.preflight(snapshot)
            snapshot["video_settings"] = normalize_video_settings(snapshot.get("video_settings"))
            self._check_duplicate(project["id"])
            timestamp = utc_now()
            pipeline = {
                "id": uuid.uuid4().hex, "project_id": project["id"], "title": snapshot["title"], "project_title": snapshot["title"],
                "status": "queued", "stage": "storyboard" if snapshot.get("content") and not source else "screenplay",
                "progress": 0, "error": None, "source_job_id": source_id,
                "screenplay_job_id": source_id, "storyboard_job_id": None, "shortfilm_id": None,
                "video_job_ids": [], "video_shots": [], "render_plan": [], "shortfilm_snapshot": None,
                "video_url": None, "editor_project_id": None, "export_id": None,
                "created_at": timestamp, "updated_at": timestamp, "snapshot": snapshot,
                "video_settings": copy.deepcopy(snapshot["video_settings"]),
                "video_format": {**video_dimensions(snapshot["video_settings"]), "container": "mp4"},
                "automatic_source": allow_active_source, "source_action": source.get("action") if source else None,
            }
            self._save(pipeline)
            self.pipelines[pipeline["id"]] = pipeline
            if source:
                try:
                    self._link_script(source["id"], pipeline["id"])
                except Exception:
                    self._update(pipeline, status="failed", error="自動影片流程已保存，但來源紀錄連結未能保存；請檢查磁碟後接續。")
                    raise
            self._launch(pipeline["id"])
            return copy.deepcopy(pipeline)

    def _link_script(self, job_id: str, pipeline_id: str) -> None:
        self.scripts.jobs[job_id]["video_pipeline_id"] = pipeline_id
        self.scripts.jobs[job_id]["updated_at"] = utc_now()
        self.scripts._save_jobs(job_id)

    def _launch(self, pipeline_id: str) -> None:
        self.cancel_events[pipeline_id] = asyncio.Event()
        task = asyncio.create_task(self._run(pipeline_id), name=f"script-video-{pipeline_id}")
        self.tasks[pipeline_id] = task
        def finished(done: asyncio.Task) -> None:
            if self.tasks.get(pipeline_id) is done:
                self.tasks.pop(pipeline_id, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    def _check_cancelled(self, pipeline_id: str) -> None:
        if self.closed or self.cancel_events[pipeline_id].is_set():
            raise asyncio.CancelledError

    async def _wait_script(self, pipeline_id: str, job_id: str) -> dict[str, Any]:
        while True:
            self._check_cancelled(pipeline_id)
            task = self.scripts.tasks.get(job_id)
            if task is not None and not task.done():
                await asyncio.wait({task}, timeout=0.1)
                continue
            job = self.scripts.get_job(job_id)
            if job.get("status") in ACTIVE:
                await asyncio.sleep(0.1)
                continue
            if job.get("status") != "completed" or job.get("truncated"):
                raise ScriptVideoError(job.get("error") or "劇本或分鏡未能完成，流程已停下。")
            if not isinstance(job.get("content"), str) or not job["content"].strip():
                raise ScriptVideoError("劇本或分鏡沒有可使用的內容，流程已停下。")
            if job.get("model_unloaded") is False:
                async with self.scripts.gpu_lock:
                    released = await self.scripts._unload(job["input"]["settings"])
                if not released:
                    raise ScriptVideoError("寫作模型尚未確認卸載，請檢查本機 Ollama 後再接續，避免佔用影片顯示記憶體。")
            return job

    def _usable_script(self, job_id: str | None) -> bool:
        job = self.scripts.jobs.get(job_id) if job_id else None
        return bool(job and job.get("status") in {*ACTIVE, "completed"} and not job.get("truncated"))

    async def _track_script_child(self, pipeline: dict[str, Any], job_id: str, field: str) -> None:
        try:
            self._link_script(job_id, pipeline["id"])
            self._update(pipeline, **{field: job_id})
        except OSError:
            await self.scripts.cancel(job_id)
            raise

    async def _wait_video(self, pipeline: dict[str, Any], job_id: str, index: int, count: int) -> dict[str, Any]:
        manager = self.app.get("jobs")
        while True:
            self._check_cancelled(pipeline["id"])
            job = manager.jobs.get(job_id) if manager is not None else None
            if job is None:
                raise ScriptVideoError("影片子工作不存在，請檢查工作紀錄後接續。")
            task = getattr(manager, "tasks", {}).get(job_id)
            if job.get("status") == "completed" and (task is None or task.done()):
                return copy.deepcopy(job)
            if job.get("status") not in VIDEO_ACTIVE and job.get("status") != "completed":
                raise ScriptVideoError(job.get("error") or "影片片段未完成，流程已停下並保留已完成片段。")
            progress = 30 + 60 * (index + min(100, max(0, float(job.get("progress") or 0))) / 100) / count
            if abs(progress - float(pipeline.get("progress") or 0)) >= 0.5:
                self._update(pipeline, progress=progress)
            if task is not None and not task.done():
                await asyncio.wait({task}, timeout=0.2)
            else:
                await asyncio.sleep(0.2)

    async def _run(self, pipeline_id: str) -> None:
        pipeline = self.pipelines[pipeline_id]
        try:
            self._update(pipeline, status="running", error=None)
            await self.preflight(pipeline["snapshot"])
            self._check_cancelled(pipeline_id)
            snapshot = copy.deepcopy(pipeline["snapshot"])
            if pipeline.get("source_job_id") and not pipeline.get("screenplay_resolved"):
                if not self._usable_script(pipeline.get("screenplay_job_id")):
                    if not pipeline.get("automatic_source"):
                        raise ScriptVideoError("來源劇本紀錄已刪除或無法讀取；請從保留的編輯稿開始新的影片流程。")
                    source_action = pipeline.get("source_action") or "screenplay"
                    child = self.scripts.submit({"project_id": pipeline["project_id"], "action": source_action}, project_override=snapshot)
                    await self._track_script_child(pipeline, child["id"], "screenplay_job_id")
                source = await self._wait_script(pipeline_id, pipeline["screenplay_job_id"])
                snapshot["content"] = source["content"]
                self._validate_timeline(snapshot)
                self._update(pipeline, snapshot=snapshot, screenplay_resolved=True)
            elif not snapshot.get("content"):
                self._update(pipeline, stage="screenplay", progress=3)
                if not self._usable_script(pipeline.get("screenplay_job_id")):
                    child = self.scripts.submit({"project_id": pipeline["project_id"], "action": "screenplay"}, project_override=snapshot)
                    await self._track_script_child(pipeline, child["id"], "screenplay_job_id")
                child = await self._wait_script(pipeline_id, pipeline["screenplay_job_id"])
                snapshot["content"] = child["content"]
                self._validate_timeline(snapshot)
                self._update(pipeline, snapshot=snapshot)
            if not pipeline.get("shortfilm_id"):
                self._update(pipeline, stage="storyboard", progress=12)
                if pipeline.get("storyboard_job_id") and not self._usable_script(pipeline["storyboard_job_id"]):
                    await self.scripts.recover_storyboard(pipeline["storyboard_job_id"])
                if not self._usable_script(pipeline.get("storyboard_job_id")):
                    child = self.scripts.submit({"project_id": pipeline["project_id"], "action": "storyboard"}, project_override=snapshot)
                    await self._track_script_child(pipeline, child["id"], "storyboard_job_id")
                await self._wait_script(pipeline_id, pipeline["storyboard_job_id"])
                self._update(pipeline, stage="import", progress=25)
                imported = self.scripts.import_shortfilm(pipeline["storyboard_job_id"])
                self._update(pipeline, shortfilm_id=imported["shortfilm_id"], shortfilm_snapshot=copy.deepcopy(imported["project"]))
            renderer = self._renderer()
            if not pipeline.get("render_plan"):
                plan = await renderer.compile_all(pipeline["shortfilm_id"], pipeline_id)
                if not isinstance(plan, list) or not plan:
                    raise ScriptVideoError("分鏡沒有可生成的多模態片段。")
                if len(plan) > 50 or sum(float(item.get("payload", {}).get("duration", 0)) for item in plan) > 600:
                    raise ScriptVideoError("自動影片最多 50 個片段、合計最多 600 秒。")
                for item in plan:
                    if not isinstance(item, dict) or not all(key in item for key in ("scene_id", "shot_id", "payload")):
                        raise ScriptVideoError("影片渲染計畫格式錯誤。")
                    payload = item["payload"]
                    image_ids = [value for reference in payload.get("references", []) for value in reference.get("image_asset_ids", [])]
                    image_ids += [payload.get("first_image_asset_id")]
                    image_ids += [value.get("image_asset_id") for value in payload.get("storyboards", [])]
                    if payload.get("mode") != "r2v" or not any(image_ids):
                        raise ScriptVideoError("分鏡缺少圖片參考，已停止；不會改用純文字生成。")
                # Freeze all validated raw requests before submitting the first GPU job.
                self._update(pipeline, render_plan=copy.deepcopy(plan))
            plan = pipeline["render_plan"]
            self._update(pipeline, stage="render", progress=30)
            manager = self.app.get("jobs")
            for index, item in enumerate(plan):
                self._check_cancelled(pipeline_id)
                tracked = next((value for value in pipeline["video_shots"] if value["shot_id"] == item["shot_id"]), None)
                existing = manager.jobs.get(tracked["job_id"]) if manager is not None and tracked else None
                if existing is None or existing.get("status") not in {*VIDEO_ACTIVE, "completed"}:
                    child = await renderer.render_shot(pipeline["shortfilm_id"], item["scene_id"], item["shot_id"], pipeline_id,
                                                       expected_payload=copy.deepcopy(item["payload"]))
                    if not isinstance(child, dict) or not SAFE_ID.fullmatch(str(child.get("id", ""))):
                        raise ScriptVideoError("影片子工作格式錯誤。")
                    tracked = {"scene_id": item["scene_id"], "shot_id": item["shot_id"], "job_id": child["id"]}
                    pipeline["video_shots"] = [value for value in pipeline["video_shots"] if value["shot_id"] != item["shot_id"]] + [tracked]
                    try:
                        self._update(pipeline, video_job_ids=[value["job_id"] for value in pipeline["video_shots"]])
                    except OSError:
                        if manager is not None:
                            await manager.cancel(child["id"])
                        raise
                await self._wait_video(pipeline, tracked["job_id"], index, len(plan))
            ordered_ids = [next(value["job_id"] for value in pipeline["video_shots"] if value["shot_id"] == item["shot_id"]) for item in plan]
            self._update(pipeline, stage="assemble", progress=92, video_job_ids=ordered_ids)
            self._check_cancelled(pipeline_id)
            async def progress(value: Any) -> None:
                if pipeline.get("status") == "running":
                    changes = {key: value[key] for key in ("editor_project_id", "export_id") if key in value} if isinstance(value, dict) else {}
                    percentage = value.get("progress") if isinstance(value, dict) else value
                    if percentage is not None:
                        changes["progress"] = min(99, max(float(pipeline.get("progress") or 92), float(percentage)))
                    try:
                        self._update(pipeline, **changes)
                    except OSError:
                        await renderer.cancel_export(pipeline)
                        raise
            result = await renderer.assemble(pipeline["shortfilm_id"], ordered_ids, pipeline_id, progress)
            self._check_cancelled(pipeline_id)
            if not isinstance(result, dict) or not result.get("video_url"):
                raise ScriptVideoError("影片組片沒有回傳成品連結。")
            self._update(pipeline, **{key: result.get(key) for key in ("video_url", "editor_project_id", "export_id")},
                         status="completed", stage="completed", progress=100, error=None)
        except asyncio.CancelledError:
            self._update(pipeline, status="interrupted" if self.closed else "cancelled",
                         error="Studio 關閉，已保留自動影片流程與已完成片段。" if self.closed else "自動影片流程已取消，已完成片段仍保留。")
        except Exception as error:
            try:
                self._update(pipeline, status="failed", error=str(error) or "自動影片流程中斷；已保留紀錄，請檢查後接續。")
            except OSError:
                pipeline.update(status="failed", error="無法保存自動影片流程，請檢查磁碟空間與權限。")

    async def _cancel_children(self, pipeline: dict[str, Any]) -> None:
        for job_id in (pipeline.get("screenplay_job_id"), pipeline.get("storyboard_job_id")):
            if job_id and self.scripts.jobs.get(job_id, {}).get("status") in ACTIVE:
                await self.scripts.cancel(job_id)
        manager = self.app.get("jobs")
        if manager is not None:
            for job_id in pipeline.get("video_job_ids", []):
                if manager.jobs.get(job_id, {}).get("status") in VIDEO_ACTIVE:
                    await manager.cancel(job_id)
        renderer = self.app.get("script_video_renderer")
        if renderer is not None:
            await renderer.cancel_export(pipeline)

    async def cancel(self, pipeline_id: str) -> dict[str, Any]:
        self.get_pipeline(pipeline_id)
        pipeline = self.pipelines[pipeline_id]
        task = self.tasks.get(pipeline_id)
        if pipeline.get("status") not in ACTIVE and (task is None or task.done()):
            return copy.deepcopy(pipeline)
        self.cancel_events.setdefault(pipeline_id, asyncio.Event()).set()
        try:
            await self._cancel_children(pipeline)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if pipeline.get("status") in ACTIVE:
                self._update(pipeline, status="cancelled", error="自動影片流程已取消，已完成片段仍保留。")
        return copy.deepcopy(pipeline)

    async def resume(self, pipeline_id: str) -> dict[str, Any]:
        if self.closed:
            raise ScriptVideoError("自動影片服務正在關閉。", 503)
        async with self.start_lock:
            pipeline = self.pipelines.get(pipeline_id)
            if pipeline is None:
                raise ScriptVideoError("找不到自動影片流程。", 404)
            task = self.tasks.get(pipeline_id)
            if task is not None and not task.done() or pipeline.get("status") in ACTIVE:
                raise ScriptVideoError("自動影片流程仍在執行。", 409)
            if pipeline.get("status") == "completed":
                return copy.deepcopy(pipeline)
            self._check_duplicate(pipeline["project_id"], exclude=pipeline_id)
            await self.preflight(pipeline["snapshot"])
            self._update(pipeline, status="queued", error=None)
            self._launch(pipeline_id)
            return copy.deepcopy(pipeline)

    async def close(self) -> None:
        self.closed = True
        for pipeline_id, task in list(self.tasks.items()):
            self.cancel_events.setdefault(pipeline_id, asyncio.Event()).set()
            if not task.done():
                try:
                    await self._cancel_children(self.pipelines[pipeline_id])
                except Exception:
                    # Shutdown still preserves and interrupts its own workflow.
                    pass
                # Persist the restart boundary; do not delete or resubmit GPU jobs.
                task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
