"""User-facing video failures without altering or resubmitting a reviewed job."""
from __future__ import annotations

import re
from typing import Any


GPU_MEMORY_ERROR = "gpu_out_of_memory"


def video_generation_error_fields(
    error: Exception | str,
    *,
    workflow: dict[str, Any] | None = None,
    current_node: str | None = None,
) -> dict[str, Any]:
    """Keep engine diagnostics separate from a useful, localized explanation."""
    detail = str(error)
    exception_type = str(getattr(error, "exception_type", "") or "")
    # CPU allocation failures need a different remedy. These messages can occur
    # in the same workflow, so do not label every allocation error as GPU OOM.
    cpu_failure = re.search(
        r"DefaultCPUAllocator|CPUAllocator|\bCPU\b[^\n]*(?:out of memory|not enough memory)|not enough memory[^\n]*\bCPU\b",
        detail, re.I,
    )
    allocation_oom = re.search(r"allocation on device \d+[^\n]*exceed allowed memory", detail, re.I)
    memory_failure = re.search(r"\bout of memory\b|\bOutOfMemoryError\b", detail, re.I)
    gpu_context = re.search(r"\bCUDA\b|\bGPU\b", detail, re.I)
    torch_oom = re.search(r"\btorch\.(?:cuda\.)?OutOfMemoryError\b", exception_type + "\n" + detail)
    gpu_oom = bool(not cpu_failure and (allocation_oom or memory_failure and gpu_context or torch_oom))
    node_id = str(getattr(error, "node_id", "") or "")
    node_type = str(getattr(error, "node_type", "") or "")
    node = (workflow or {}).get(node_id, {})
    if not isinstance(node, dict):
        node = {}
    meta = node.get("_meta") or {}
    title = (meta.get("title") if isinstance(meta, dict) else None) or current_node
    node_type = node_type or str(node.get("class_type") or "")
    error_node = ({"id": node_id or None, "type": node_type or None, "title": title or node_type or None}
                  if node_id or node_type or title else None)
    return {
        "error": (
            "顯示卡記憶體不足，這次影片生成已停止。請先降低解析度；若仍不足，可縮短單段片長或減少參考素材，"
            "再確認稿件後重試。原稿、素材與設定已保留，系統不會自動重送。"
            if gpu_oom else detail
        ),
        "error_code": GPU_MEMORY_ERROR if gpu_oom else None,
        "error_detail": detail if gpu_oom else None,
        "error_node": error_node,
    }
