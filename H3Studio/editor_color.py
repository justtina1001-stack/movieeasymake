"""Bounded timeline color adjustments shared with the browser's sRGB filter.

Intervals apply to the completed visual composite in array order. They never
extend the movie and never modify editor media, proxy previews, or audio.
"""
from __future__ import annotations

import av
import numpy as np

from video_editor import CLIP_ID, EditorError, number


MAX_ADJUSTMENTS = 20
MAX_ACTIVE_ADJUSTMENTS = 4
COLOR_DEFAULTS = {"strength": 100, "exposure": 0, "brightness": 0, "contrast": 0,
                  "saturation": 0, "temperature": 0, "tint": 0,
                  "highlights": 0, "shadows": 0}
COLOR_FIELDS = {"id", "name", "start", "end", "enabled", *COLOR_DEFAULTS}
COLOR_LABELS = {"strength": "調色強度", "exposure": "曝光", "brightness": "亮度",
                "contrast": "對比", "saturation": "飽和度", "temperature": "色溫",
                "tint": "色偏", "highlights": "高光", "shadows": "陰影"}
LUMA = (.2126, .7152, .0722)


def validate_color_adjustments(items, fps, ids=None):
    """Canonical nonempty records; the caller omits an empty project field."""
    if not isinstance(items, list) or len(items) > MAX_ADJUSTMENTS:
        raise EditorError("時間軸最多可加入 20 個調色片段。")
    identifiers = ids if ids is not None else set()
    clean = []
    for item in items:
        if not isinstance(item, dict) or set(item) - COLOR_FIELDS:
            raise EditorError("調色片段格式錯誤或含不支援的設定。")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not CLIP_ID.fullmatch(identifier) or identifier in identifiers:
            raise EditorError("調色片段識別碼格式錯誤或重複。")
        name = item.get("name", "調色片段")
        if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise EditorError("調色片段名稱需為 1–80 個字元，且不能含控制字元。")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise EditorError("調色片段啟用設定必須是布林值。")
        start = number(item.get("start"), "調色起點", 0, 600)
        end = number(item.get("end"), "調色終點", 0, 600)
        if end - start + 1e-9 < 1 / fps:
            raise EditorError("調色片段至少需要一幀。")
        result = {"id": identifier, "name": name.strip(), "start": start,
                  "end": end, "enabled": enabled}
        for field, default in COLOR_DEFAULTS.items():
            low, high = ((0, 100) if field == "strength" else (-2, 2) if field == "exposure" else (-100, 100))
            result[field] = number(item.get(field, default), COLOR_LABELS[field], low, high)
        identifiers.add(identifier)
        clean.append(result)
    # Half-open ranges: a finishing interval releases its slot before another
    # starts. Disabled records count so re-enabling one cannot invalidate a save.
    active = 0
    for _, delta in sorted((time, delta) for item in clean
                           for time, delta in ((item["start"], 1), (item["end"], -1))):
        active += delta
        if active > MAX_ACTIVE_ADJUSTMENTS:
            raise EditorError("同一時間最多可疊加 4 個調色片段。")
    return clean


def is_neutral(item):
    return (not item.get("enabled", True) or item.get("strength", 100) == 0
            or all(item.get(field, 0) == 0 for field in COLOR_DEFAULTS if field != "strength"))


def color_transform(item):
    """Return the SVG feColorMatrix and 257-point feComponentTransfer table."""
    strength = item.get("strength", 100) / 100
    exposure = 2 ** (item.get("exposure", 0) * strength)
    temperature = item.get("temperature", 0) * strength / 100
    tint = item.get("tint", 0) * strength / 100
    gains = (exposure * (1 + .25 * temperature + .10 * tint),
             exposure * (1 - .20 * tint),
             exposure * (1 - .25 * temperature + .10 * tint))
    saturation = 1 + item.get("saturation", 0) * strength / 100
    contrast = 1 + item.get("contrast", 0) * strength / 100
    offset = .5 * (1 - contrast) + .25 * (item.get("brightness", 0) * strength) / 100
    matrix = np.array([[contrast * (saturation * (row == column) + (1 - saturation) * LUMA[column])
                        * gains[column] for column in range(3)] + [offset] for row in range(3)], dtype=np.float64)
    x = np.arange(257, dtype=np.float64) / 256
    shadows = item.get("shadows", 0) * strength / 100
    highlights = item.get("highlights", 0) * strength / 100
    table = np.clip(x + .8 * shadows * x * (1 - x) ** 2 + .8 * highlights * x * x * (1 - x), 0, 1)
    return matrix, table


def apply_color_pixels(pixels, item, transform=None):
    """Apply one adjustment to uint8 RGB or RGBA; preserve alpha exactly.

    Row blocks bound temporary float storage even for a 2520×1080 project. Each
    record ends in half-up 8-bit quantization, matching the browser math helper.
    """
    if is_neutral(item):
        return pixels
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("Color adjustment input must be uint8 RGB or RGBA")
    matrix, table = transform if transform is not None else color_transform(item)
    result = pixels.copy()
    for top in range(0, pixels.shape[0], 128):
        rgb = pixels[top:top + 128, :, :3].astype(np.float64) / 255
        for channel in range(3):
            row = matrix[channel]
            value = np.clip(((rgb[..., 0] * row[0] + rgb[..., 1] * row[1])
                             + rgb[..., 2] * row[2]) + row[3], 0, 1)
            position = value * 256
            index = np.minimum(np.floor(position).astype(np.intp), 255)
            fraction = position - index
            mapped = table[index] + (table[index + 1] - table[index]) * fraction
            result[top:top + 128, :, channel] = np.floor(mapped * 255 + .5).astype(np.uint8)
    return result


class ColorCompositor:
    def __init__(self, items):
        self.items = [(item, color_transform(item)) for item in items if not is_neutral(item)]

    def apply(self, frame, seconds):
        active = [(item, transform) for item, transform in self.items if item["start"] <= seconds < item["end"]]
        if not active:
            return frame
        pixels = frame.to_ndarray(format="rgb24")
        for item, transform in active:
            pixels = apply_color_pixels(pixels, item, transform)
        return av.VideoFrame.from_ndarray(pixels, format="rgb24")
