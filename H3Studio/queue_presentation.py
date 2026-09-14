"""Combine engine queue facts with this Studio's jobs for display only."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


ACTIVE_STATUSES = {"queued", "preparing", "running"}
KIND_LABELS = {"video": "影片", "music": "音樂", "voice": "語音", "unknown": "工作"}


def build_queue_view(
    snapshot: dict[str, Any],
    video_jobs: dict[str, dict[str, Any]],
    music_jobs: dict[str, dict[str, Any]],
    voice_jobs: dict[str, dict[str, Any]],
    *,
    colleague_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Do not infer a global rank for jobs still behind the local GPU lock.

    The engine snapshot is already normalized/redacted by the transport. No
    workflow, prompt ID, access token, or submitted prompt is returned to the UI.
    Long replacement parents borrow their current child's state without counting
    both the parent and child as separate GPU tasks.
    """
    available = snapshot.get("available") is True
    all_jobs = {
        job_id: (kind, job)
        for kind, collection in (("video", video_jobs), ("music", music_jobs), ("voice", voice_jobs))
        for job_id, job in collection.items()
    }
    by_prompt = {
        str(job["prompt_id"]): job_id
        for job_id, (_, job) in all_jobs.items() if job.get("prompt_id")
    }
    colleagues = colleague_names or {}

    def own_row(job_id: str, phase: str) -> dict[str, Any]:
        kind, job = all_jobs[job_id]
        title = str(job.get("name") or job.get("shortfilm_shot_title") or f"{KIND_LABELS[kind]} {job_id[:8]}")
        parent_id = str(job.get("parent_job_id") or "")
        if parent_id in video_jobs:
            parent = video_jobs[parent_id]
            title = str(parent.get("name") or f"影片 {parent_id[:8]}")
            segment = job.get("segment_index")
            if isinstance(segment, int):
                title += f" · 第 {segment} 段"
        progress = None
        if phase == "engine_running":
            try:
                value = float(job.get("progress") or 0)
                if math.isfinite(value):
                    progress = min(100.0, max(0.0, value))
            except (TypeError, ValueError):
                pass
        return {
            "job_id": parent_id if parent_id in video_jobs else job_id,
            "kind": kind, "title": title, "owner": "我的工作", "phase": phase,
            "position": None, "ahead_count": None, "progress": progress,
        }

    jobs: dict[str, dict[str, Any]] = {}
    engine_job_ids: set[str] = set()
    running: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    if available:
        for queue_key, phase, target in (
            ("running", "engine_running", running), ("pending", "engine_waiting", pending),
        ):
            for entry in snapshot[queue_key]:
                prompt_id = entry.get("prompt_id")
                job_id = by_prompt.get(prompt_id)
                if job_id:
                    row = own_row(job_id, phase)
                    engine_job_ids.add(job_id)
                    jobs[job_id] = row
                else:
                    owner = colleagues.get(prompt_id) or "其他使用者"
                    row = {
                        "job_id": None, "kind": "unknown", "title": "共用引擎工作",
                        "owner": owner, "phase": phase, "position": None,
                        "ahead_count": None, "progress": None,
                    }
                row["position"] = entry["position"]
                row["ahead_count"] = (
                    snapshot["running_count"] + entry["position"] - 1
                    if phase == "engine_waiting" else 0
                )
                target.append(row)

    local_waiting: list[dict[str, Any]] = []
    local_active: list[dict[str, Any]] = []
    for job_id, (kind, job) in sorted(all_jobs.items(), key=lambda item: str(item[1][1].get("created_at") or "")):
        if job_id in engine_job_ids or job.get("status") not in ACTIVE_STATUSES:
            continue
        child_id = job.get("active_child_id")
        if child_id in all_jobs and all_jobs[child_id][1].get("status") in ACTIVE_STATUSES:
            continue
        if job.get("status") == "queued":
            phase = "local_waiting"
        elif job.get("status") == "preparing":
            phase = "finishing" if job.get("output") or job.get("current_node") == "合併替換片段並處理聲音" else "preparing"
        elif kind == "voice":
            phase = "local_processing"
        else:
            # A missing queue entry could be submission/download/reconnection in
            # flight; it is not evidence that the engine is generating this job.
            phase = "unknown" if job.get("prompt_id") else "preparing"
        row = own_row(job_id, phase)
        jobs[job_id] = row
        (local_waiting if phase == "local_waiting" else local_active).append(row)

    for job_id, (_, job) in all_jobs.items():
        child = jobs.get(job.get("active_child_id"))
        if child and job.get("status") in ACTIVE_STATUSES:
            jobs[job_id] = {**child, "job_id": job_id}

    return {
        "available": available,
        "running_count": snapshot["running_count"] if available else None,
        "pending_count": snapshot["pending_count"] if available else None,
        "local_waiting_count": len(local_waiting),
        "running": running, "pending": pending, "local_waiting": local_waiting,
        "local_active": local_active, "jobs": jobs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": str(snapshot.get("error") or "暫時無法取得引擎佇列。") if not available else None,
    }
