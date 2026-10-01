"""Shared text rasterization and bounded, CPU-only timeline compositing."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from functools import lru_cache
from io import BytesIO
import json
import math
from pathlib import Path
import re
import threading

import av
import numpy as np
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont, ImageOps

from video_editor import CLIP_ID, EditorError, VideoReader, fade_gain, number
from editor_motion import position_at, validate_position_keyframes
from editor_speed import clip_duration, source_at, validate_speed_curve
from editor_animations import animation_at, transition_pairs, transition_surface, validate_animations, validate_transition_pairs, validate_transition_setting

MAX_LAYERS = 50
MAX_ACTIVE = 12
MAX_TEXT_PIXELS = 16 * 1024 * 1024
RASTER_CACHE_BYTES = 192 * 1024 * 1024
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
TEXT_STYLE_DEFAULTS = {"stroke_width": 0, "stroke_color": "#000000", "fill_mode": "solid",
                       "gradient_start": "#FFFFFF", "gradient_end": "#FF8A3D", "gradient_angle": 90}


def _validate_text_style(layer):
    """Keep legacy text snapshots unchanged and retain inactive editor choices."""
    style = {"stroke_width": number(layer.get("stroke_width", 0), "文字描邊寬度", 0, .25),
             "gradient_angle": number(layer.get("gradient_angle", 90), "文字漸層角度", 0, 360)}
    mode = layer.get("fill_mode", "solid")
    if not isinstance(mode, str) or mode not in ("solid", "linear_gradient"):
        raise EditorError("文字填色模式只能使用純色或線性漸層。")
    style["fill_mode"] = mode
    for key, label in (("stroke_color", "描邊顏色"), ("gradient_start", "漸層起始色"),
                       ("gradient_end", "漸層結束色")):
        value = layer.get(key, TEXT_STYLE_DEFAULTS[key])
        if not isinstance(value, str) or not COLOR.fullmatch(value):
            raise EditorError(f"文字{label}需使用 #RRGGBB。")
        style[key] = value.upper()
    return {key: value for key, value in style.items() if value != TEXT_STYLE_DEFAULTS[key]}


def validate_layer(layer, fps=24, ids=None):
    if not isinstance(layer, dict):
        raise EditorError("疊加圖層格式錯誤。")
    layer_id = layer.get("id")
    if not isinstance(layer_id, str) or not CLIP_ID.fullmatch(layer_id) or ids is not None and layer_id in ids:
        raise EditorError("圖層識別碼格式錯誤或重複。")
    kind = layer.get("kind")
    if kind not in ("text", "image", "video"):
        raise EditorError("圖層只能使用文字、圖片或影片。")
    if kind != "text" and any(key in layer for key in TEXT_STYLE_DEFAULTS):
        raise EditorError("文字描邊與漸層設定只能使用文字圖層。")
    start = number(layer.get("start"), "圖層起點", 0, 600)
    end = number(layer.get("end"), "圖層終點", 0, 600)
    if end - start + 1e-9 < 1 / fps:
        raise EditorError("圖層顯示時間至少需要一幀。")
    clean = {"id": layer_id, "kind": kind, "start": start, "end": end,
             "x": number(layer.get("x", .5), "水平位置", 0, 1),
             "y": number(layer.get("y", .5), "垂直位置", 0, 1),
             "width": number(layer.get("width", .5), "圖層寬度", .02, 2),
             "rotation": number(layer.get("rotation", 0), "旋轉角度", -180, 180),
             "opacity": number(layer.get("opacity", 1), "不透明度", 0, 1)}
    if "track_id" in layer:
        track_id = layer["track_id"]
        if not isinstance(track_id, str) or not CLIP_ID.fullmatch(track_id):
            raise EditorError("圖層軌道識別碼格式錯誤。")
        clean["track_id"] = track_id
    fade_in = number(layer.get("fade_in", 0), "淡入", 0, end - start)
    fade_out = number(layer.get("fade_out", 0), "淡出", 0, end - start)
    if fade_in + fade_out > end - start + 1e-6:
        raise EditorError("淡入與淡出總長度不能超過圖層長度。")
    clean.update(fade_in=fade_in, fade_out=fade_out)
    clean.update(validate_animations(layer, end - start))
    clean.update(validate_transition_setting(layer))
    if clean.get("transition_out") and kind != "video":
        raise EditorError("轉場只能使用相鄰影片，文字與圖片請使用進場／退場動畫。")
    points = validate_position_keyframes(layer)
    if points:
        clean["position_keyframes"] = points
    speed_points = validate_speed_curve(layer)
    if speed_points and kind != "video":
        raise EditorError("曲線變速只能使用影片圖層。")
    if speed_points:
        clean["speed_curve"] = speed_points
    if kind in ("image", "video"):
        clean["media_id"] = layer.get("media_id")
        if kind == "video":
            source_in, source_out = number(layer.get("in"), "影片入點", 0), number(layer.get("out"), "影片出點", 0)
            speed = number(layer.get("speed", 1), "影片速度", .25, 4)
            duration = clip_duration({"in": source_in, "out": source_out, "speed": speed, "speed_curve": speed_points})
            if duration + 1e-9 < 1 / fps or abs(end - start - duration) > 1e-6:
                raise EditorError("影片圖層終點需等於起點加上調速後的片段長度，且至少一幀。")
            clean.update({"in": source_in, "out": source_out, "speed": speed,
                          "volume": number(layer.get("volume", 1), "影片音量", 0, 2), "end": start + duration})
    else:
        text = layer.get("text")
        if isinstance(text, str):
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not isinstance(text, str) or len(text) > 500 or len(text.split("\n")) > 10:
            raise EditorError("文字最多 500 個字元，且最多 10 行。")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise EditorError("文字不能包含控制字元。")
        color, background = layer.get("color", "#FFFFFF"), layer.get("background", "transparent")
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise EditorError("文字顏色需使用 #RRGGBB。")
        if not isinstance(background, str) or background != "transparent" and not COLOR.fullmatch(background):
            raise EditorError("文字底色需使用 #RRGGBB 或 transparent。")
        bold, align = layer.get("bold", False), layer.get("align", "center")
        if type(bold) is not bool or align not in ("left", "center", "right"):
            raise EditorError("文字粗體或對齊設定錯誤。")
        clean.update(text=text, font_size=number(layer.get("font_size", .06), "字級", .01, .3),
                     color=color.upper(), background=background if background == "transparent" else background.upper(),
                     bold=bold, align=align)
        clean.update(_validate_text_style(layer))
    if ids is not None:
        ids.add(layer_id)
    return clean


def validate_overlays(layers, store, fps, ids):
    if not isinstance(layers, list) or len(layers) > MAX_LAYERS:
        raise EditorError("最多可加入 50 個文字／圖片／影片圖層。")
    clean = []
    for layer in layers:
        item = validate_layer(layer, fps, ids)
        if item["kind"] in ("image", "video"):
            media = store._get(store.media, item["media_id"], "圖層素材")
            store.media_path(media["id"])
            if media.get("kind", "video") != item["kind"]:
                raise EditorError("圖層素材類型不符。")
            if item["kind"] == "video":
                if item["out"] > media["duration"] + 1e-6:
                    raise EditorError("影片圖層出點不能超過來源長度。")
                item["out"] = min(item["out"], media["duration"])
                validate_speed_curve(item, media["duration"])
                duration = clip_duration(item)
                if duration + 1e-9 < 1 / fps:
                    raise EditorError("影片圖層調速後至少需要一幀。")
                if item["fade_in"] + item["fade_out"] > duration + 1e-6:
                    raise EditorError("淡入與淡出總長度不能超過影片圖層長度。")
                item["end"] = item["start"] + duration
                if item["end"] > 600 + 1e-6:
                    raise EditorError("影片時間軸最多 10 分鐘。")
        clean.append(item)
    # Legacy layers each occupy their own track. An explicit track_id lets
    # adjacent clips share that track without changing the flat render order.
    # Keeping each group contiguous gives every clip the same stacking level.
    tracks, previous_track = {}, None
    for item in clean:
        track_id = item.get("track_id", item["id"])
        if track_id != previous_track and track_id in tracks:
            raise EditorError("同一疊加軌道的片段必須連續排列，不能穿插其他軌道。")
        tracks.setdefault(track_id, []).append(item)
        previous_track = track_id
    for clips in tracks.values():
        ordered = sorted(clips, key=lambda item: item["start"])
        for previous, current in zip(ordered, ordered[1:]):
            if current["start"] < previous["end"] - 1e-6:
                raise EditorError("同一疊加軌道的片段不能重疊，請移至空白時間或另一個軌道。")
    active = 0
    for _, delta in sorted((time, delta) for item in clean for time, delta in ((item["start"], 1), (item["end"], -1))):
        active += delta
        if active > MAX_ACTIVE:
            raise EditorError("同一時間最多顯示 12 個文字／圖片／影片圖層。")
    active_video = 0
    for _, delta in sorted((time, delta) for item in clean if item["kind"] == "video"
                          for time, delta in ((item["start"], 1), (item["end"], -1))):
        active_video += delta
        if active_video > 3:
            raise EditorError("同一時間最多疊加 3 個影片圖層。")
    validate_transition_pairs(clean, overlays=True)
    return clean


def _font_candidates(bold):
    filename = "msjhbd.ttc" if bold else "msjh.ttc"
    weight = "Bold" if bold else "Regular"
    return [(f"C:/Windows/Fonts/{filename}", 0),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc", 3),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc", 0),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJKtc-{weight}.otf", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", 0),
            (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf", 0)]


@lru_cache(maxsize=32)
def _font(size, bold):
    for path, index in _font_candidates(bold):
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    raise EditorError("找不到可用的文字字型。請安裝微軟正黑體或 Noto Sans CJK 後重新啟動 Studio。", 422)


@lru_cache(maxsize=2048)
def _has_glyph(character, bold):
    font = _font(24, bold)
    missing, glyph = font.getmask("\U0010ffff"), font.getmask(character)
    return glyph.size != missing.size or bytes(glyph) != bytes(missing)


def font_information():
    try:
        regular, bold = _font(24, False), _font(24, True)
        return {"available": True, "family": regular.getname()[0], "bold_family": bold.getname()[0],
                "cjk": all(_has_glyph(char, False) and _has_glyph(char, True) for char in "中文測試"),
                "fixed": True}
    except EditorError as error:
        return {"available": False, "cjk": False, "fixed": True, "error": str(error)}


def raster_text(layer, width, height):
    """Unrotated opacity-1 PNG source; preview and encoder use this exact layout."""
    box_width = max(1, round(layer["width"] * width))
    size = max(1, round(layer["font_size"] * min(width, height)))
    font = _font(size, layer["bold"])
    for character in set(layer["text"]):
        if not character.isspace() and not _has_glyph(character, layer["bold"]):
            raise EditorError("目前字型缺少這段文字的字元。請改用支援的文字或安裝 Noto Sans CJK／微軟正黑體。", 422)
    stroke_width = round(size * layer.get("stroke_width", 0))
    # Outlines expand glyph bounds rather than increasing font advances. Reserve
    # their full radius on every side before wrapping and allocating the raster.
    padding = max(1, round(size * .18)) + stroke_width
    available = box_width - 2 * padding
    lines = []
    for explicit in layer["text"].replace("\t", "    ").split("\n"):
        line = ""
        for character in explicit:
            if font.getlength(character) > available:
                raise EditorError("文字框太窄，請加寬圖層或縮小字級。", 422)
            if line and font.getlength(line + character) > available:
                lines.append(line)
                line = character
            else:
                line += character
        lines.append(line)
        if len(lines) > 10:
            raise EditorError("文字換行後超過 10 行，請加寬圖層、縮小字級或減少文字。", 422)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + max(1, round(size * .12)) + 2 * stroke_width
    box_height = len(lines) * line_height + 2 * padding
    if box_width > 8192 or box_height > 8192 or box_width * box_height > MAX_TEXT_PIXELS:
        raise EditorError("文字圖層尺寸過大，請縮小字級或減少行數。", 422)
    background = (0, 0, 0, 0) if layer["background"] == "transparent" else layer["background"]
    result = Image.new("RGBA", (box_width, box_height), background)
    positions = []
    for index, line in enumerate(lines):
        length = font.getlength(line)
        x = padding if layer["align"] == "left" else box_width - padding - length if layer["align"] == "right" else (box_width - length) / 2
        positions.append(((x, padding + index * line_height + ascent), line))
    if layer.get("fill_mode", "solid") == "linear_gradient":
        _draw_gradient_text(result, positions, font, stroke_width, layer)
    else:
        draw = ImageDraw.Draw(result)
        for position, line in positions:
            draw.text(position, line, font=font, fill=layer["color"], anchor="ls",
                      stroke_width=stroke_width, stroke_fill=layer.get("stroke_color", "#000000"))
    return result


def _rgb(color):
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _draw_gradient_text(result, positions, font, stroke_width, layer):
    """Apply one continuous linear fill to glyph coverage, with a uniform outline.

    Work in short row blocks: even a maximum-size text box never needs a full
    float RGB/projection array. Alpha is applied once before compositing onto the
    optional text background; fades and layer opacity remain compositor duties.
    """
    with Image.new("L", result.size) as fill_mask:
        fill_draw = ImageDraw.Draw(fill_mask)
        for position, line in positions:
            fill_draw.text(position, line, font=font, fill=255, anchor="ls")
        bounds = fill_mask.getbbox()
        if bounds is None:
            return
        outline_mask = Image.new("L", result.size) if stroke_width else fill_mask
        try:
            if stroke_width:
                outline_draw = ImageDraw.Draw(outline_mask)
                for position, line in positions:
                    outline_draw.text(position, line, font=font, fill=255, anchor="ls",
                                      stroke_width=stroke_width, stroke_fill=255)
            paint_bounds = outline_mask.getbbox()
            left, top, right, bottom = bounds
            radians = math.radians(layer.get("gradient_angle", 90))
            cosine, sine = math.cos(radians), math.sin(radians)
            # Remove floating residuals at axis-aligned angles.
            cosine = 0 if abs(cosine) < 1e-12 else cosine
            sine = 0 if abs(sine) < 1e-12 else sine
            projections = [x * cosine + y * sine for x in (left, right - 1) for y in (top, bottom - 1)]
            low, high = min(projections), max(projections)
            span = max(high - low, 1)
            start = np.asarray(_rgb(layer.get("gradient_start", "#FFFFFF")), dtype=np.float32)
            end = np.asarray(_rgb(layer.get("gradient_end", "#FF8A3D")), dtype=np.float32)
            stroke = np.asarray(_rgb(layer.get("stroke_color", "#000000")), dtype=np.float32)
            px_left, px_top, px_right, px_bottom = paint_bounds
            xs = np.arange(px_left, px_right, dtype=np.float32)[None, :]
            for row in range(px_top, px_bottom, 32):
                row_end = min(row + 32, px_bottom)
                crop = (px_left, row, px_right, row_end)
                with fill_mask.crop(crop) as fill, outline_mask.crop(crop) as outline:
                    fill_alpha = np.asarray(fill, dtype=np.float32)
                    alpha = np.maximum(np.asarray(outline, dtype=np.float32), fill_alpha)
                    fraction = np.divide(fill_alpha, alpha, out=np.zeros_like(alpha), where=alpha > 0)
                    ys = np.arange(row, row_end, dtype=np.float32)[:, None]
                    ramp = np.clip((xs * cosine + ys * sine - low) / span, 0, 1)
                    colors = start + ramp[:, :, None] * (end - start)
                    colors = stroke + fraction[:, :, None] * (colors - stroke)
                    pixels = np.empty((*alpha.shape, 4), dtype=np.uint8)
                    pixels[:, :, :3] = np.clip(np.rint(colors), 0, 255).astype(np.uint8)
                    pixels[:, :, 3] = alpha.astype(np.uint8)
                    with Image.fromarray(pixels, mode="RGBA") as paint:
                        result.alpha_composite(paint, (px_left, row))
        finally:
            if outline_mask is not fill_mask:
                outline_mask.close()


def text_png(layer, width, height):
    with raster_text(layer, width, height) as raster, BytesIO() as data:
        raster.save(data, format="PNG")
        return data.getvalue()


class OverlayCompositor:
    def __init__(self, layers, media_paths, width, height, cache_bytes=RASTER_CACHE_BYTES, cancel_event=None, fps=24):
        self.layers, self.paths = layers, media_paths
        self.width, self.height = width, height
        self.fps = fps
        self.cache, self.cache_bytes, self.used_bytes = OrderedDict(), cache_bytes, 0
        self.video_readers = {}
        self.cancel_event = cancel_event or threading.Event()
        self.transitions = transition_pairs(layers, overlays=True)

    def _source(self, layer):
        if layer["kind"] == "text":
            return raster_text(layer, self.width, self.height)
        from editor_images import MAX_IMAGE_PIXELS, MAX_IMAGE_SIDE
        with Image.open(self.paths[layer["media_id"]]) as original:
            if original.width > MAX_IMAGE_SIDE or original.height > MAX_IMAGE_SIDE or original.width * original.height > MAX_IMAGE_PIXELS:
                raise EditorError("圖片尺寸過大，請縮小後再匯入。", 422)
            with ImageOps.exif_transpose(original) as upright:
                return upright.convert("RGBA")

    def _prepare(self, layer, source=None, display_aspect=None, close_source=True):
        if source is None:
            source = self._source(layer)
        try:
            sw, sh = source.size
            scale = layer["width"] * self.width / sw
            scale_y = scale if display_aspect is None else layer["width"] * self.width / display_aspect / sh
            angle = math.radians(layer["rotation"])
            cosine, sine = math.cos(angle), math.sin(angle)
            cx, cy = layer["x"] * self.width, layer["y"] * self.height
            corners = [(cx + scale * cosine * x - scale_y * sine * y, cy + scale * sine * x + scale_y * cosine * y)
                       for x in (-sw / 2, sw / 2) for y in (-sh / 2, sh / 2)]
            left = max(0, math.floor(min(x for x, _ in corners)))
            top = max(0, math.floor(min(y for _, y in corners)))
            right = min(self.width, math.ceil(max(x for x, _ in corners)))
            bottom = min(self.height, math.ceil(max(y for _, y in corners)))
            if right <= left or bottom <= top:
                return None, left, top
            a, b, d, e = cosine / scale, sine / scale, -sine / scale_y, cosine / scale_y
            coefficients = (a, b, sw / 2 + a * (left - cx) + b * (top - cy),
                            d, e, sh / 2 + d * (left - cx) + e * (top - cy))
            # Transform only visible pixels: a thin 1x8192 source cannot create
            # a multi-gigapixel intermediate when scaled or rotated.
            if layer["rotation"] == 0 and abs(scale - 1) < 1e-9 and abs(scale_y - 1) < 1e-9 and all(abs(value - round(value)) < 1e-9 for value in (coefficients[2], coefficients[5])):
                # Preserve exact PNG colors for an integral 1:1 placement; a
                # generic RGBA resampler needlessly rounds premultiplied alpha.
                source_left, source_top = round(coefficients[2]), round(coefficients[5])
                raster = source.crop((source_left, source_top, source_left + right - left, source_top + bottom - top))
            else:
                raster = source.transform((right - left, bottom - top), Image.Transform.AFFINE, coefficients,
                                          resample=Image.Resampling.BICUBIC)
            if layer["opacity"] != 1:
                with raster.getchannel("A") as alpha:
                    with alpha.point([round(value * layer["opacity"]) for value in range(256)]) as faded:
                        raster.putalpha(faded)
            return raster, left, top
        finally:
            if close_source:
                source.close()

    def _cached(self, layer, source_only=False):
        key = ("source", layer["id"]) if source_only else layer["id"]
        if layer["kind"] == "text":
            # IDs remain stable while text settings change. Include source
            # styles without frame-local fade or animated-position values.
            source_key = tuple(layer.get(name, TEXT_STYLE_DEFAULTS.get(name)) for name in
                               ("text", "width", "font_size", "color", "background", "bold", "align", *TEXT_STYLE_DEFAULTS))
            key = (key, source_key) if source_only else (key, source_key, layer["x"], layer["y"], layer["rotation"], layer["opacity"])
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key], True
        prepared = (self._source(layer), 0, 0) if source_only else self._prepare(layer)
        pixels = prepared[0]
        size = pixels.width * pixels.height * 4 if pixels else 0
        if size > self.cache_bytes:
            return prepared, False
        while self.cache and self.used_bytes + size > self.cache_bytes:
            _, (old, _, _) = self.cache.popitem(last=False)
            if old is not None:
                self.used_bytes -= old.width * old.height * 4
                old.close()
        self.cache[key] = prepared
        self.used_bytes += size
        return prepared, True

    def _visual(self, layer, seconds):
        local, duration = seconds - layer["start"], layer["end"] - layer["start"]
        transform = animation_at(layer, local, duration)
        gain = fade_gain(local, duration, layer) * transform["opacity"]
        if gain <= 0 or layer["opacity"] <= 0:
            return None, 0, 0, True, 0
        position = position_at(layer, local)
        animated = {**layer, "x": position["x"] + transform["x"],
                    "y": position["y"] + transform["y"], "width": layer["width"] * transform["scale"]}
        if layer["kind"] == "video":
            source_time = source_at(layer, local)
            if layer["id"] not in self.video_readers:
                self.video_readers[layer["id"]] = VideoReader(self.paths[layer["media_id"]], source_time, self.cancel_event)
            video = self.video_readers[layer["id"]].at(source_time)
            ratio = min(1, max(self.width, self.height) / max(video.width, video.height))
            pixels = video.reformat(width=max(1, round(video.width * ratio)), height=max(1, round(video.height * ratio)), format="rgba").to_ndarray()
            raster, left, top = self._prepare(animated, Image.fromarray(pixels), video.width / video.height)
            cached = False
        elif layer.get("position_keyframes") or layer.get("animation_in") or layer.get("animation_out"):
            # Keep one original source raster, never a cache entry per frame.
            (source, _, _), source_cached = self._cached(layer, source_only=True)
            try:
                raster, left, top = self._prepare(animated, source, close_source=False)
            finally:
                if not source_cached:
                    source.close()
            cached = False
        else:
            (raster, left, top), cached = self._cached(layer)
        return raster, left, top, cached, gain

    def _paint(self, canvas, layer, seconds):
        raster, left, top, cached, gain = self._visual(layer, seconds)
        if raster is None:
            return
        faded = None
        try:
            if gain < 1:
                faded = raster.copy()
                with raster.getchannel("A") as alpha:
                    with alpha.point([round(value * gain) for value in range(256)]) as adjusted:
                        faded.putalpha(adjusted)
            canvas.alpha_composite(faded if faded is not None else raster, (left, top))
        finally:
            if faded is not None:
                faded.close()
            if not cached:
                raster.close()

    def apply(self, frame, seconds):
        # A transition occupies the same single stacking level as its track.
        # Its two RGBA surfaces are combined before compositing over lower tracks.
        slots, paired, active_video = {}, set(), set()
        indices = {layer["id"]: index for index, layer in enumerate(self.layers)}
        for previous, following, cut, setting in self.transitions:
            length = setting["duration"]
            if cut - length / 2 <= seconds < cut + length / 2:
                ratio = (seconds - cut + length / 2) / length
                slots[min(indices[previous["id"]], indices[following["id"]])] = (previous, following, cut, setting, ratio)
                paired.update((previous["id"], following["id"]))
                active_video.update((previous["id"], following["id"]))
        active = {layer["id"] for layer in self.layers if layer["start"] <= seconds < layer["end"] and layer["opacity"] > 0}
        active_video.update(layer["id"] for layer in self.layers if layer["id"] in active and layer["kind"] == "video")
        for key in list(self.video_readers):
            if key not in active_video:
                self.video_readers.pop(key).close()
        if not active and not slots:
            return frame
        with Image.fromarray(frame.to_ndarray(format="rgb24")) as rgb:
            canvas = rgb.convert("RGBA")
        try:
            for index, layer in enumerate(self.layers):
                if index in slots:
                    previous, following, cut, setting, ratio = slots[index]
                    with Image.new("RGBA", (self.width, self.height)) as left, Image.new("RGBA", (self.width, self.height)) as right:
                        # Frozen edge frames keep the existing edit clock intact.
                        last = previous["end"] - 1 / self.fps
                        self._paint(left, previous, min(seconds, max(previous["start"], last)))
                        self._paint(right, following, max(seconds, following["start"]))
                        with transition_surface(left, right, setting["type"], ratio) as combined:
                            canvas.alpha_composite(combined)
                elif layer["id"] in active and layer["id"] not in paired:
                    self._paint(canvas, layer, seconds)
            with canvas.convert("RGB") as result:
                return av.VideoFrame.from_ndarray(np.asarray(result), format="rgb24")
        finally:
            canvas.close()

    def close(self):
        for reader in self.video_readers.values():
            reader.close()
        self.video_readers.clear()
        for raster, _, _ in self.cache.values():
            if raster is not None:
                raster.close()
        self.cache.clear()
        self.used_bytes = 0


def register_editor_overlays(app):
    in_flight = 0

    async def preview(request):
        nonlocal in_flight
        if in_flight >= 2:
            raise EditorError("文字預覽忙碌中，請稍後再試。", 409)
        in_flight += 1
        worker = None
        try:
            data = bytearray()
            async for chunk in request.content.iter_chunked(4096):
                data.extend(chunk)
                if len(data) > 16384:
                    raise EditorError("文字預覽設定過大。", 413)
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeDecodeError) as error:
                raise EditorError("文字預覽需使用有效 JSON。") from error
            if not isinstance(payload, dict):
                raise EditorError("文字預覽設定必須是物件。")
            width, height = payload.get("width"), payload.get("height")
            if type(width) is not int or type(height) is not int or not 1 <= width <= 1920 or not 1 <= height <= 1920:
                raise EditorError("預覽畫布尺寸必須是 1–1920 的整數。")
            # Preview is not tied to a project FPS; accept every valid 60fps layer.
            layer = validate_layer(payload.get("layer"), fps=60)
            if layer["kind"] != "text":
                raise EditorError("文字預覽只能使用文字圖層。")
            worker = asyncio.create_task(asyncio.to_thread(text_png, layer, width, height))
            body = await asyncio.shield(worker)
            return web.Response(body=body, content_type="image/png", headers={"Cache-Control": "no-store"})
        finally:
            if worker is not None and not worker.done():
                await asyncio.shield(asyncio.gather(worker, return_exceptions=True))
            in_flight -= 1

    async def fonts(request):
        return web.json_response(await asyncio.to_thread(font_information))

    app.router.add_post("/api/editor/text-preview", preview)
    app.router.add_get("/api/editor/fonts", fonts)
