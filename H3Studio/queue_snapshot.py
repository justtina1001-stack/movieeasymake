from __future__ import annotations

import asyncio
import math
from typing import Any

import aiohttp


def queue_unavailable(error: str = "暫時無法取得引擎佇列狀態。") -> dict[str, Any]:
    return {
        "available": False,
        "running_count": None,
        "pending_count": None,
        "running": [],
        "pending": [],
        "error": error,
    }


def normalize_queue_snapshot(payload: Any) -> dict[str, Any]:
    """Keep only queue positions and IDs; never return workflows or metadata."""
    if not isinstance(payload, dict):
        return queue_unavailable()
    rows: dict[str, list[dict[str, Any]]] = {}
    try:
        if "available" in payload:
            if payload["available"] is not True:
                return queue_unavailable()
            for key in ("running", "pending"):
                entries = payload[key]
                count = payload[f"{key}_count"]
                if not isinstance(entries, list) or type(count) is not int or count != len(entries):
                    raise ValueError
                clean = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError
                    prompt_id, position = entry["prompt_id"], entry["position"]
                    if prompt_id is not None and (not isinstance(prompt_id, str) or not prompt_id.strip()):
                        raise ValueError
                    if type(position) is not int:
                        raise ValueError
                    clean.append({"prompt_id": prompt_id, "position": position})
                clean.sort(key=lambda entry: entry["position"])
                expected = [0] * count if key == "running" else list(range(1, count + 1))
                if [entry["position"] for entry in clean] != expected:
                    raise ValueError
                rows[key] = clean
        else:
            for key in ("running", "pending"):
                entries = payload[f"queue_{key}"]
                if not isinstance(entries, list):
                    raise ValueError
                priorities = []
                for entry in entries:
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        raise ValueError
                    priority, prompt_id = entry[:2]
                    if type(priority) not in (int, float) or not math.isfinite(priority):
                        raise ValueError
                    if not isinstance(prompt_id, str) or not prompt_id.strip():
                        raise ValueError
                    priorities.append((priority, prompt_id))
                # ComfyUI returns the pending heap, which is not fully sorted.
                if key == "pending":
                    priorities.sort(key=lambda entry: (entry[0], entry[1]))
                rows[key] = [
                    {"prompt_id": prompt_id, "position": index if key == "pending" else 0}
                    for index, (_, prompt_id) in enumerate(priorities, start=1)
                ]
        ids = [row["prompt_id"] for entries in rows.values() for row in entries if row["prompt_id"] is not None]
        if len(ids) != len(set(ids)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        return queue_unavailable()
    return {
        "available": True,
        "running_count": len(rows["running"]),
        "pending_count": len(rows["pending"]),
        **rows,
    }


async def fetch_queue_snapshot(
    base_url: str, *, headers: dict[str, str] | None = None, timeout: float = 3,
) -> dict[str, Any]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=min(timeout, 3))) as session:
            async with session.get(f"{base_url.rstrip('/')}/queue", headers=headers) as response:
                if response.status in {401, 403}:
                    return queue_unavailable("無法讀取佇列，請確認共享引擎金鑰。")
                if response.status == 404:
                    return queue_unavailable("引擎尚未提供佇列狀態，請更新 GPU 主機上的 H3 Studio。")
                if response.status != 200:
                    return queue_unavailable()
                return normalize_queue_snapshot(await response.json())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
        return queue_unavailable()
