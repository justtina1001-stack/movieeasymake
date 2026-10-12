"""Validated generation and final-output sizes for screenplay video projects."""
from __future__ import annotations

import copy
import math
from typing import Any

from domain import ASPECT_RATIOS, compute_dimensions


DEFAULT_VIDEO_SETTINGS = {"aspect_ratio": "16:9", "megapixels": 0.4, "output_resolution": "native"}
GENERATION_MEGAPIXELS = (0.4, 0.7, 0.9, 0.98)
OUTPUT_RESOLUTIONS = {"native", "720p", "1080p"}
OUTPUT_DIMENSIONS = {
    "720p": {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720),
             "4:3": (960, 720), "3:4": (720, 960), "21:9": (1680, 720)},
    "1080p": {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
              "4:3": (1440, 1080), "3:4": (1080, 1440), "21:9": (2520, 1080)},
}


class ScriptVideoFormatError(ValueError):
    pass


def normalize_video_settings(value: Any = None, *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result = normalize_video_settings(previous) if previous is not None else copy.deepcopy(DEFAULT_VIDEO_SETTINGS)
    if value is None:
        return result
    if not isinstance(value, dict) or set(value) - set(DEFAULT_VIDEO_SETTINGS):
        raise ScriptVideoFormatError("影片格式設定錯誤，請重新選擇畫面比例與解析度。")
    result.update(value)
    if not isinstance(result["aspect_ratio"], str) or result["aspect_ratio"] not in ASPECT_RATIOS:
        raise ScriptVideoFormatError("請選擇支援的影片畫面比例。")
    megapixels = result["megapixels"]
    if (isinstance(megapixels, bool) or not isinstance(megapixels, (int, float))
            or not math.isfinite(megapixels) or megapixels not in GENERATION_MEGAPIXELS):
        raise ScriptVideoFormatError("生成精細度請選擇 0.4、0.7、0.9 或 0.98 MP。")
    result["megapixels"] = float(megapixels)
    if not isinstance(result["output_resolution"], str) or result["output_resolution"] not in OUTPUT_RESOLUTIONS:
        raise ScriptVideoFormatError("成片解析度請選擇生成尺寸、720p 或 1080p。")
    return result


def video_dimensions(value: Any = None) -> dict[str, int]:
    settings = normalize_video_settings(value)
    generated = compute_dimensions(settings["aspect_ratio"], settings["megapixels"])
    output = (generated if settings["output_resolution"] == "native"
              else OUTPUT_DIMENSIONS[settings["output_resolution"]][settings["aspect_ratio"]])
    return {"generation_width": generated[0], "generation_height": generated[1],
            "output_width": output[0], "output_height": output[1], "fps": 24}
