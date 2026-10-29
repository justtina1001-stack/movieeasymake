"""Persist short-film submissions on the Studio host, independently of browser tabs."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shortfilm import flatten_shots, validate_shot_references
from generation_review import review_digest

SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
ACTIVE = {"queued", "running"}
VIDEO_ACTIVE = {"queued", "preparing", "running"}
RUNTIME_FIELDS = {"job_id", "status", "continuation_asset_id", "continuation_job_id"}


class ShortFilmBatchError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def generation_source(project):
    """Keep every generation setting, excluding only persisted execution state."""
    source = copy.deepcopy(project)
    for field in ("created_at", "updated_at", "revision", "segment_draft"):
        source.pop(field, None)
    for _, shot in flatten_shots(source):
        for field in RUNTIME_FIELDS:
            shot.pop(field, None)
    return source


class ShortFilmBatchManager:
    def __init__(self, app, data_dir, compile_submit, *, poll_interval=0.25, prepare_review=None, submit_review=None):
        self.app = app
        self.compile_submit = compile_submit
        self.prepare_review, self.submit_review = prepare_review, submit_review
        self.poll_interval = poll_interval
        self.directory = Path(data_dir) / "shortfilm-batches"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.batches, self.tasks = {}, {}
        self.lock = asyncio.Lock()
        self.closed = False
        for path in self.directory.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("id") != path.stem:
                    raise ValueError("invalid batch identity")
                if value.get("status") in ACTIVE:
                    value.update(status="interrupted", error="Studio 已重新啟動。已送出的鏡頭保留，按接續可繼續後續鏡頭。", updated_at=_now())
                    value["approved_review_id"] = None
                    self._save(value)
                self.batches[value["id"]] = value
            except (OSError, ValueError) as error:
                raise ShortFilmBatchError(f"無法讀取短片依序生成記錄 {path.name}；請保留資料並檢查。", 500) from error

    def _save(self, batch):
        path = self.directory / f"{batch['id']}.json"
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _update(self, batch, **changes):
        batch.update(changes, updated_at=_now())
        self._save(batch)

    def get(self, batch_id):
        if not isinstance(batch_id, str) or not SAFE_ID.fullmatch(batch_id) or batch_id not in self.batches:
            raise ShortFilmBatchError("找不到短片依序生成記錄。", 404)
        return copy.deepcopy(self.batches[batch_id])

    def list(self, project_id=None):
        return sorted((copy.deepcopy(item) for item in self.batches.values()
                       if project_id is None or item["project_id"] == project_id),
                      key=lambda item: (item["created_at"], item["id"]), reverse=True)

    def _check_duplicate(self, project_id, exclude=None):
        for batch in self.batches.values():
            task = self.tasks.get(batch["id"])
            if batch["id"] != exclude and batch["project_id"] == project_id and (
                    batch["status"] in {*ACTIVE, "awaiting_review"} or task is not None and not task.done()):
                raise ShortFilmBatchError("這份短片已有依序生成流程，可查看進度或先停止。", 409)

    def _current(self, batch):
        project = self.app["shortfilms"].get(batch["project_id"])
        if generation_source(project) != generation_source(batch["snapshot"]):
            raise ShortFilmBatchError("短片內容或素材已修改，依序生成已停止；已完成與已送出的鏡頭保留。請使用新設定重新開始。", 409)
        return project

    def _sync_runtime(self, batch):
        current = self._current(batch)
        source_shots = {shot["id"]: shot for _, shot in flatten_shots(batch["snapshot"])}
        for _, shot in flatten_shots(current):
            runtime = source_shots[shot["id"]]
            for field in RUNTIME_FIELDS:
                if field in runtime:
                    shot[field] = copy.deepcopy(runtime[field])
        self.app["shortfilms"].update(current["id"], current)

    def _dispatch(self, batch):
        self.tasks[batch["id"]] = asyncio.create_task(self._run(batch["id"]))

    @staticmethod
    def _review_flag(raw):
        if raw is None:
            return False
        if (not isinstance(raw, dict) or set(raw) - {"require_prompt_review"}
                or "require_prompt_review" in raw and not isinstance(raw["require_prompt_review"], bool)):
            raise ShortFilmBatchError("生成稿確認選項格式錯誤。")
        return raw.get("require_prompt_review", False)

    async def start(self, project_id, raw=None):
        review_required = self._review_flag(raw)
        async with self.lock:
            if self.closed:
                raise ShortFilmBatchError("Studio 正在關閉，請稍後再試。", 503)
            self._check_duplicate(project_id)
            snapshot = self.app["shortfilms"].get(project_id)
            shots = flatten_shots(snapshot)
            if not shots:
                raise ShortFilmBatchError("請先建立至少一個鏡頭。")
            for scene, shot in shots:
                if not str(shot.get("action", "")).strip():
                    raise ShortFilmBatchError(f"「{shot['title']}」尚未填寫畫面與動作。")
                validate_shot_references(snapshot, scene, shot)
            now = _now()
            batch = {"id": uuid.uuid4().hex, "project_id": project_id, "project_title": snapshot["title"],
                     "status": "queued", "snapshot": snapshot, "shot_ids": [shot["id"] for _, shot in shots],
                     "shot_jobs": {}, "completed_shot_ids": [], "current_shot_id": None,
                     "current_shot_title": "", "progress": 0, "error": None,
                     "created_at": now, "updated_at": now, "require_prompt_review": review_required,
                     "pending_review": None, "review": None, "approved_review_id": None}
            self._save(batch)
            self.batches[batch["id"]] = batch
            self._dispatch(batch)
            return self.get(batch["id"])

    async def resume(self, batch_id, raw=None):
        review_required = self._review_flag(raw)
        async with self.lock:
            batch = self.batches[self.get(batch_id)["id"]]
            if self.closed:
                raise ShortFilmBatchError("Studio 正在關閉，請稍後再試。", 503)
            if batch["status"] not in {"failed", "cancelled", "interrupted", "awaiting_review"}:
                raise ShortFilmBatchError("只有已停止的依序生成可以接續。", 409)
            self._check_duplicate(batch["project_id"], exclude=batch_id)
            task = self.tasks.get(batch_id)
            if task is not None and not task.done():
                raise ShortFilmBatchError("流程尚在停止中，請稍後再按接續。", 409)
            self._current(batch)
            self._update(batch, status="queued", error=None, approved_review_id=None,
                         require_prompt_review=review_required or batch.get("require_prompt_review", False))
            self._dispatch(batch)
            return self.get(batch_id)

    async def cancel(self, batch_id):
        batch = self.batches[self.get(batch_id)["id"]]
        if batch["status"] in {*ACTIVE, "awaiting_review"}:
            self._update(batch, status="cancelled", error="已停止提交後續鏡頭；目前已送出的工作仍會完成。")
            task = self.tasks.get(batch_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return self.get(batch_id)

    async def approve(self, batch_id, raw):
        async with self.lock:
            batch = self.batches[self.get(batch_id)["id"]]
            review, packet = batch.get("review"), batch.get("pending_review")
            task = self.tasks.get(batch_id)
            if (not isinstance(raw, dict) or set(raw) != {"review_id"} or not isinstance(review, dict)
                    or raw.get("review_id") != review.get("id") or batch["status"] != "awaiting_review"
                    or not packet or review.get("packet_digest") != review_digest(packet)
                    or task is not None and not task.done()):
                raise ShortFilmBatchError("這份生成稿已失效，請重新檢查目前鏡頭。", 409)
            if self.closed:
                raise ShortFilmBatchError("Studio 正在關閉，請稍後再試。", 503)
            self._current(batch)
            self._update(batch, approved_review_id=review["id"], status="queued", error=None)
            self._dispatch(batch)
            return self.get(batch_id)

    async def _wait_job(self, batch, job_id):
        while True:
            self._current(batch)
            job = self.app["jobs"].jobs.get(job_id)
            if job is None:
                raise ShortFilmBatchError("鏡頭工作記錄已不存在，請檢查後重新開始。")
            task = getattr(self.app["jobs"], "tasks", {}).get(job_id)
            if job["status"] == "completed" and (task is None or task.done()):
                return job
            if job["status"] not in VIDEO_ACTIVE and job["status"] != "completed":
                raise ShortFilmBatchError(job.get("error") or "目前鏡頭未完成，後續鏡頭已暫停。")
            await asyncio.sleep(self.poll_interval)

    async def _run(self, batch_id):
        batch = self.batches[batch_id]
        try:
            self._update(batch, status="running", error=None)
            shots = flatten_shots(batch["snapshot"])
            for index, (_, shot) in enumerate(shots):
                self._current(batch)
                self._update(batch, current_shot_id=shot["id"], current_shot_title=shot["title"])
                job_id = batch["shot_jobs"].get(shot["id"]) or shot.get("job_id")
                existing = self.app["jobs"].jobs.get(job_id)
                if existing is None or existing.get("status") not in {*VIDEO_ACTIVE, "completed"}:
                    if batch.get("require_prompt_review"):
                        if not callable(self.prepare_review) or not callable(self.submit_review):
                            raise ShortFilmBatchError("主機尚未支援生成稿確認，請先更新 Studio。", 503)
                        review, packet = batch.get("review"), batch.get("pending_review")
                        if not isinstance(review, dict) or review.get("shot_id") != shot["id"] or not packet:
                            packet = await self.prepare_review(batch["project_id"], shot["id"], batch["snapshot"])
                            self._current(batch)
                            review = {"id": uuid.uuid4().hex, "created_at": _now(), "shot_id": shot["id"],
                                      "prompts": [copy.deepcopy(packet["prompt"])], "packet_digest": review_digest(packet)}
                            self._update(batch, pending_review=packet, review=review, approved_review_id=None)
                        if review.get("packet_digest") != review_digest(packet):
                            raise ShortFilmBatchError("生成稿內容已變更，請開始新的影片流程。", 409)
                        if batch.get("approved_review_id") != review["id"]:
                            self._update(batch, status="awaiting_review")
                            return
                        self._current(batch)
                        child = await self.submit_review(batch["project_id"], shot["id"], batch["snapshot"], copy.deepcopy(packet))
                        batch.update(pending_review=None, review=None, approved_review_id=None)
                    else:
                        child = await self.compile_submit(batch["project_id"], shot["id"], batch["snapshot"])
                    if not isinstance(child, dict) or not SAFE_ID.fullmatch(str(child.get("id", ""))):
                        raise ShortFilmBatchError("鏡頭生成服務沒有回傳有效工作記錄。")
                    job_id = child["id"]
                    shot.update(job_id=job_id, status=child.get("status", "queued"))
                    batch["shot_jobs"][shot["id"]] = job_id
                    # A new earlier render changes the tail frame used by its connected descendants.
                    for _, descendant in shots[index + 1:]:
                        if not descendant.get("continue_previous"):
                            break
                        descendant.update(job_id=None, status="draft", continuation_asset_id=None, continuation_job_id=None)
                        batch["shot_jobs"].pop(descendant["id"], None)
                        batch["completed_shot_ids"] = [value for value in batch["completed_shot_ids"] if value != descendant["id"]]
                    # Persist the accepted job before any await; cancellation never deletes it.
                    self._save(batch)
                    self._sync_runtime(batch)
                else:
                    batch["shot_jobs"][shot["id"]] = job_id
                    shot.update(job_id=job_id, status=existing["status"])
                    self._save(batch)
                await self._wait_job(batch, job_id)
                shot["status"] = "completed"
                self._sync_runtime(batch)
                if shot["id"] not in batch["completed_shot_ids"]:
                    batch["completed_shot_ids"].append(shot["id"])
                self._update(batch, progress=round(100 * len(batch["completed_shot_ids"]) / len(batch["shot_ids"]), 1))
            self._update(batch, status="completed", progress=100, current_shot_id=None, current_shot_title="", error=None)
        except asyncio.CancelledError:
            if self.closed:
                self._update(batch, status="interrupted", error="Studio 已關閉。已送出的鏡頭保留，重新啟動後可按接續。")
            elif batch["status"] in ACTIVE:
                self._update(batch, status="cancelled", error="已停止提交後續鏡頭；目前已送出的工作仍會完成。")
        except Exception as error:
            try:
                self._update(batch, status="failed", error=str(error) or "依序生成已暫停，已送出的鏡頭保留。")
            except OSError:
                batch.update(status="failed", error="無法保存依序生成記錄，請檢查磁碟；已送出的工作仍保留。")

    async def shutdown(self):
        self.closed = True
        pending = [task for task in self.tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for batch in self.batches.values():
            if batch["status"] in ACTIVE:
                self._update(batch, status="interrupted", error="Studio 已關閉。已送出的鏡頭保留，重新啟動後可按接續。")
