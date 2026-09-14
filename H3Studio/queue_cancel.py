from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

from queue_snapshot import fetch_queue_snapshot


class QueueCancelError(RuntimeError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _contains(snapshot: dict[str, Any], key: str, prompt_id: str) -> bool:
    return any(row["prompt_id"] == prompt_id for row in snapshot[key])


async def cancel_queue_prompt(
    base_url: str, prompt_id: str | None, *, headers: dict[str, str] | None = None,
) -> bool:
    """Cancel only this prompt; never fall back to a global interruption."""
    if not prompt_id:
        return False
    base_url = base_url.rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.post(
                f"{base_url}/api/jobs/{quote(prompt_id, safe='')}/cancel", headers=headers,
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if not isinstance(result, dict) or type(result.get("cancelled")) is not bool:
                        raise QueueCancelError("引擎未回傳明確的取消結果，請重新整理佇列後確認。", 502)
                    if result["cancelled"]:
                        return True
                    snapshot = await fetch_queue_snapshot(base_url, headers=headers)
                    if not snapshot["available"]:
                        raise QueueCancelError("暫時無法確認工作是否已結束，請重新整理佇列後再試。", 502)
                    if _contains(snapshot, "running", prompt_id) or _contains(snapshot, "pending", prompt_id):
                        raise QueueCancelError("工作狀態剛剛改變，尚未取消；請再試一次。")
                    return False
                if response.status not in {404, 405}:
                    if response.status in {401, 403}:
                        raise QueueCancelError("無法取消工作，請確認共享引擎金鑰。", 403)
                    if response.status == 409:
                        raise QueueCancelError("引擎目前無法安全取消這筆工作；請確認佇列，並更新 GPU 主機上的 ComfyUI 與 H3 Studio。")
                    raise QueueCancelError("引擎未確認取消成功，請重新整理佇列後再試。", 502)

            # Older engines can still delete one pending item safely. A running
            # item needs the atomic endpoint; a global interrupt can hit a peer.
            snapshot = await fetch_queue_snapshot(base_url, headers=headers)
            if not snapshot["available"] or _contains(snapshot, "running", prompt_id):
                raise QueueCancelError("目前引擎不支援安全取消這筆工作，請更新 GPU 主機上的 ComfyUI 與 H3 Studio 後再試。")
            if not _contains(snapshot, "pending", prompt_id):
                return False
            async with session.post(
                f"{base_url}/queue", headers=headers, json={"delete": [prompt_id]},
            ) as response:
                if response.status != 200:
                    raise QueueCancelError("引擎未確認移除排隊工作，請重新整理佇列後再試。", 502)
            after = await fetch_queue_snapshot(base_url, headers=headers)
            if not after["available"]:
                raise QueueCancelError("暫時無法確認排隊工作是否已移除，請重新整理佇列後再試。", 502)
            if _contains(after, "running", prompt_id):
                raise QueueCancelError("這筆工作剛開始生成，舊版引擎無法安全中斷；請更新 GPU 主機上的 ComfyUI 與 H3 Studio。")
            if _contains(after, "pending", prompt_id):
                raise QueueCancelError("排隊工作尚未移除，請再試一次。", 502)
            return True
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError) as error:
        raise QueueCancelError("無法確認引擎已取消工作，請檢查連線並重新整理佇列。", 502) from error
