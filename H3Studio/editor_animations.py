"""Bounded visual clip animations and fixed-clock, edge-held transitions.

Animations do not change audio timing or project duration. A transition is bound
to the next clip's ID and spans both sides of their existing edit boundary.
"""
from __future__ import annotations

import math

import av
import numpy as np
from PIL import Image

from editor_speed import clip_duration

ANIMATION_TYPES = ("fade", "slide_left", "slide_right", "slide_up", "slide_down", "zoom_in", "zoom_out")
IDLE_TYPES = ("pulse", "float", "shake", "blink")
REFERENCE_TRANSITION_TYPES = ("radial_zoom", "horizontal_blur")
EFFECT_TRANSITION_TYPES = ("white_flash", "blur_dissolve", "wave_distort", "rgb_glitch", *REFERENCE_TRANSITION_TYPES)
TRANSITION_TYPES = ("crossfade", "wipe_left", "wipe_right", *EFFECT_TRANSITION_TYPES)
MAX_ANIMATION_SECONDS = 5


def _setting(value, label, types, transition=False):
    from video_editor import CLIP_ID, EditorError, number
    if not isinstance(value, dict) or set(value) - ({"type", "duration", "next_id"} if transition else {"type", "duration"}):
        raise EditorError(f"{label}格式錯誤。")
    kind = value.get("type", "none")
    if not isinstance(kind, str) or kind not in ("none", *types):
        raise EditorError(f"{label}類型不支援。")
    duration = number(value.get("duration", 0), f"{label}時間", 0, MAX_ANIMATION_SECONDS)
    if kind == "none" or duration == 0:
        return None
    result = {"type": kind, "duration": duration}
    if transition:
        next_id = value.get("next_id")
        if not isinstance(next_id, str) or not CLIP_ID.fullmatch(next_id):
            raise EditorError("轉場需綁定下一個影片片段。")
        result["next_id"] = next_id
    return result


def validate_animations(clip, duration):
    from video_editor import EditorError
    clean = {}
    for name, label in (("animation_in", "進場動畫"), ("animation_out", "退場動畫")):
        if name in clip:
            value = _setting(clip[name], label, ANIMATION_TYPES)
            if value is not None:
                clean[name] = value
    if sum(value["duration"] for value in clean.values()) > duration + 1e-6:
        raise EditorError("進場與退場動畫總長度不能超過片段長度。")
    if "animation_idle" in clip:
        value = validate_idle_setting(clip["animation_idle"])
        if clip.get("kind") != "text":
            raise EditorError("待機特效只能使用文字圖層。")
        if value is not None:
            clean["animation_idle"] = value
    return clean


def validate_idle_setting(value):
    """Canonical text-only idle settings; disabled defaults never alter old drafts."""
    from video_editor import EditorError, number
    if not isinstance(value, dict) or set(value) - {"preset", "speed", "amount"}:
        raise EditorError("文字待機特效格式錯誤。")
    preset = value.get("preset", "none")
    if not isinstance(preset, str) or preset not in ("none", *IDLE_TYPES):
        raise EditorError("文字待機特效類型不支援。")
    speed = number(value.get("speed", 1), "文字待機特效速度", .25, 4)
    amount = number(value.get("amount", 1), "文字待機特效強度", 0, 2)
    if preset == "none" or amount == 0:
        return None
    return {"preset": preset, "speed": speed, "amount": amount}


def validate_transition_setting(clip):
    if "transition_out" not in clip:
        return {}
    value = _setting(clip["transition_out"], "轉場", TRANSITION_TYPES, transition=True)
    return {"transition_out": value} if value is not None else {}


def validate_transition_pairs(clips, *, overlays=False):
    """Validate bindings after media, lengths and track ordering are canonical."""
    from video_editor import EditorError
    groups = {}
    if overlays:
        for layer in clips:
            groups.setdefault(layer.get("track_id", layer["id"]), []).append(layer)
    else:
        groups["main"] = clips
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item["start"]) if overlays else group
        for index, previous in enumerate(ordered):
            setting = previous.get("transition_out")
            if not setting:
                continue
            if index + 1 == len(ordered):
                raise EditorError("轉場需要同軌道的下一個相鄰影片片段。")
            following = ordered[index + 1]
            if setting["next_id"] != following["id"]:
                raise EditorError("轉場綁定的下一個影片已改變，請重新選擇轉場。")
            if overlays and (previous["kind"] != "video" or following["kind"] != "video"
                             or abs(previous["end"] - following["start"]) > 1e-6):
                raise EditorError("影片轉場需要同軌道且首尾相接的兩段影片。")
            limit = min(clip_duration(previous), clip_duration(following), MAX_ANIMATION_SECONDS)
            if setting["duration"] > limit + 1e-6:
                raise EditorError("轉場時間不能超過相鄰兩段影片中較短的片段。")


def animation_at(clip, local_time, duration):
    """Output normalized canvas translation, scale and visual-only opacity."""
    result = {"x": 0., "y": 0., "scale": 1., "opacity": 1.}
    for name, remaining in (("animation_in", False), ("animation_out", True)):
        value = clip.get(name)
        if not value or value.get("type") not in ANIMATION_TYPES or value.get("duration", 0) <= 0:
            continue
        ratio = (duration - local_time if remaining else local_time) / value["duration"]
        ratio = min(1., max(0., ratio))
        amount = 1 - ratio * ratio * (3 - 2 * ratio)
        kind = value["type"]
        if kind == "fade":
            result["opacity"] *= 1 - amount
        elif kind.startswith("slide_"):
            axis = "x" if kind in ("slide_left", "slide_right") else "y"
            direction = 1 if kind in ("slide_left", "slide_up") else -1
            result[axis] += amount * direction * (-1 if remaining else 1)
        elif kind == "zoom_in":
            result["scale"] *= 1 - .85 * amount
        elif kind == "zoom_out":
            result["scale"] *= 1 + .8 * amount
    idle = idle_at(clip, local_time, duration)
    result["opacity"] *= idle["opacity"]
    result["x"] += idle["x"]
    result["y"] += idle["y"]
    result["scale"] *= idle["scale"]
    return result


def idle_at(clip, local_time, duration):
    """Deterministic text motion during the gap between entrance and exit.

    Both ends ease back to the unanimated placement, so short gaps and seeking
    do not cause jumps. Frequency is in cycles per second; export and browser
    preview evaluate these exact same formulas from clip-local time.
    """
    result = {"x": 0., "y": 0., "scale": 1., "opacity": 1.}
    value = clip.get("animation_idle")
    if clip.get("kind") != "text" or not value or value.get("preset") not in IDLE_TYPES:
        return result
    entry, exit = clip.get("animation_in") or {}, clip.get("animation_out") or {}
    start = entry.get("duration", 0) if entry.get("type") in ANIMATION_TYPES else 0
    end = duration - (exit.get("duration", 0) if exit.get("type") in ANIMATION_TYPES else 0)
    length = end - start
    if length <= 1e-6 or local_time <= start or local_time >= end:
        return result
    elapsed, remaining = local_time - start, end - local_time
    ramp = min(.2, length / 2)
    ratio = min(1., elapsed / ramp, remaining / ramp)
    envelope = ratio * ratio * (3 - 2 * ratio)
    amount = value.get("amount", 1)
    phase = 2 * math.pi * elapsed * value.get("speed", 1)
    wave = math.sin(phase / 2) ** 2
    preset = value["preset"]
    if preset == "pulse":
        result["scale"] = 1 + .08 * amount * wave * envelope
    elif preset == "float":
        result["y"] = -.025 * amount * math.sin(phase) * envelope
    elif preset == "shake":
        result["x"] = .0125 * amount * math.sin(phase * 4) * envelope
    elif preset == "blink":
        result["opacity"] = 1 - min(1., .8 * amount) * wave * envelope
    return result


def transition_pairs(clips, *, overlays=False):
    groups, result = {}, []
    if overlays:
        for layer in clips:
            groups.setdefault(layer.get("track_id", layer["id"]), []).append(layer)
    else:
        groups["main"] = clips
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item["start"]) if overlays else group
        cut = 0.
        for index, previous in enumerate(ordered):
            cut = previous["end"] if overlays else cut + clip_duration(previous)
            setting = previous.get("transition_out")
            if setting and index + 1 < len(ordered):
                following = ordered[index + 1]
                if setting.get("next_id") == following.get("id"):
                    result.append((previous, following, cut, setting))
    return result


def transition_at(pairs, seconds):
    for previous, following, cut, setting in pairs:
        length = setting["duration"]
        start = cut - length / 2
        if start <= seconds < cut + length / 2:
            return previous, following, cut, setting, (seconds - start) / length
    return None


def _premultiplied_dissolve(outgoing, incoming, ratio):
    """One float32 working surface; hidden RGB never bleeds into a dissolve."""
    with outgoing.convert("RGBA") as left_image, incoming.convert("RGBA") as right_image:
        left, right = np.asarray(left_image), np.asarray(right_image)
        pixels = np.empty(left.shape, dtype=np.float32)
        left_weight = left[..., 3].astype(np.float32) * ((1 - ratio) / 255)
        right_weight = right[..., 3].astype(np.float32) * (ratio / 255)
        for channel in range(3):
            pixels[..., channel] = left[..., channel] * left_weight + right[..., channel] * right_weight
        pixels[..., 3] = (left_weight + right_weight) * 255
    return pixels


def _box_blur_premultiplied(pixels, radius):
    """Separable edge-held box blur, with bounded per-channel scratch space."""
    if radius == 0:
        return
    width = 2 * radius + 1
    for channel in range(4):
        for axis in (1, 0):
            padding = ((0, 0), (radius, radius)) if axis == 1 else ((radius, radius), (0, 0))
            padded = np.pad(pixels[..., channel], padding, mode="edge")
            shape = list(padded.shape)
            shape[axis] += 1
            sums = np.empty(shape, dtype=np.float64)
            if axis == 1:
                sums[:, 0] = 0
                np.cumsum(padded, axis=axis, dtype=np.float64, out=sums[:, 1:])
                pixels[..., channel] = (sums[:, width:] - sums[:, :-width]) / width
            else:
                sums[0, :] = 0
                np.cumsum(padded, axis=axis, dtype=np.float64, out=sums[1:, :])
                pixels[..., channel] = (sums[width:, :] - sums[:-width, :]) / width


def _distort_premultiplied(pixels, kind, ratio, envelope):
    """Sample one row at a time, so export memory does not scale with copies."""
    height, width = pixels.shape[:2]
    positions = np.arange(width)
    band_height = max(1, math.floor(height / 12))
    split = math.floor(width * .012 * envelope + .5)
    phase = math.floor(ratio * 12)
    for y in range(height):
        row = pixels[y].copy()
        if kind == "wave_distort":
            shift = math.floor(math.sin(6 * math.pi * y / height + 4 * math.pi * ratio)
                               * width * .035 * envelope + .5)
            pixels[y] = row[np.clip(positions + shift, 0, width - 1)]
        else:
            band = math.floor(y / band_height)
            jump = math.floor(math.sin(band * 12.9898 + phase * 78.233) * width * .03 * envelope + .5)
            red = np.clip(positions + jump + split, 0, width - 1)
            green = np.clip(positions + jump, 0, width - 1)
            blue = np.clip(positions + jump - split, 0, width - 1)
            pixels[y, :, 0] = row[red, 0]
            pixels[y, :, 1] = row[green, 1]
            pixels[y, :, 2] = row[blue, 2]
            pixels[y, :, 3] = np.maximum(np.maximum(row[red, 3], row[green, 3]), row[blue, 3])


def _rgba_from_premultiplied(pixels):
    alpha = pixels[..., 3]
    scale = np.zeros(alpha.shape, dtype=np.float32)
    np.divide(255, alpha, out=scale, where=alpha > 0)
    for channel in range(3):
        pixels[..., channel] *= scale
    np.clip(pixels, 0, 255, out=pixels)
    pixels += .5
    np.floor(pixels, out=pixels)
    return Image.fromarray(pixels.astype(np.uint8))


def _ae_curve(progress, controls):
    """Match the reference timeline's ten-step cubic-Bezier time inversion."""
    low, high = 0., 1.
    x1, y1, x2, y2 = controls
    for _ in range(10):
        middle = (low + high) / 2
        remaining = 1 - middle
        x = 3 * remaining * remaining * middle * x1 + 3 * remaining * middle * middle * x2 + middle ** 3
        if x > progress:
            high = middle
        else:
            low = middle
    middle = (low + high) / 2
    remaining = 1 - middle
    return 3 * remaining * remaining * middle * y1 + 3 * remaining * middle * middle * y2 + middle ** 3


def _horizontal_blur_parameters(ratio, width, height):
    time, peak = .5 * ratio, .233333
    linear_controls = (.166666667, .166666667, .833333333, .833333333)
    mix = _ae_curve(time / .4, linear_controls) if time < .4 else 1.
    if time < peak:
        progress = time / peak
        intensity = 120 * _ae_curve(progress, (.33333333, 0., 0., 1.000000013))
        light = _ae_curve(progress, linear_controls)
    elif time < .5:
        progress = (time - peak) / (.5 - peak)
        intensity = 120 * (1 - _ae_curve(progress, (.33333333, 0., 0., .99999969)))
        light = 1 - _ae_curve(progress, linear_controls)
    else:
        intensity, light = 0., 0.
    if intensity <= 10:
        sample, downscale = .8 * intensity, .8
    elif intensity <= 50:
        sample, downscale = (.7 * intensity + 2) * .78, .7
    elif intensity <= 200:
        sample, downscale = (.5 * intensity + 10) * .66, .5
    elif intensity <= 500:
        sample, downscale = (.25 * intensity + 60) * .7, .25
    else:
        sample, downscale = (.125 * intensity + 100) * .8, .125
    if sample < 2:
        sample = math.floor(sample + .5)
    radius, base = intensity / 1000, max(min(width, height), max(width, height) / 2)
    distance = radius / max(sample, 1e-5)
    # The reference script uses this Pi constant for its -180 degree direction.
    angle = -3.141592653
    return {"mix": mix, "intensity": intensity, "exposure": 2 * light, "correction": 1 + 3 * light,
            "sample": sample, "downscale": downscale, "sigma": radius / 3,
            "step_x": distance * math.cos(angle) * base / width,
            "step_y": distance * math.sin(angle) * base / height}


def _bilinear_premultiplied(pixels, u, v):
    """GL linear sampling at pixel centers, with clamped texture edges."""
    height, width = pixels.shape[:2]
    x = np.clip(u * width - .5, 0, width - 1)
    y = np.clip(v * height - .5, 0, height - 1)
    left, top = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    right, bottom = np.minimum(left + 1, width - 1), np.minimum(top + 1, height - 1)
    fx, fy = (x - left)[..., None].astype(np.float32), (y - top)[..., None].astype(np.float32)
    if x.shape[0] == 1 and y.shape[1] == 1:
        # Directional blur and resize share each horizontal lookup across a
        # row. Interpolate the smaller source rows before expanding in x.
        rows = pixels[top[:, 0]] * (1 - fy) + pixels[bottom[:, 0]] * fy
        return rows[:, left[0]] * (1 - fx) + rows[:, right[0]] * fx
    flat = pixels.reshape(-1, 4)
    upper = flat[top * width + left]
    upper *= 1 - fx
    neighbor = flat[top * width + right]
    neighbor *= fx
    upper += neighbor
    lower = flat[bottom * width + left]
    lower *= 1 - fx
    neighbor = flat[bottom * width + right]
    neighbor *= fx
    lower += neighbor
    upper *= 1 - fy
    lower *= fy
    upper += lower
    return upper


def _resize_premultiplied(pixels, width, height):
    result = np.empty((height, width, 4), dtype=np.float32)
    u = (np.arange(width, dtype=np.float64)[None, :] + .5) / width
    for start in range(0, height, 32):
        end = min(height, start + 32)
        v = (np.arange(start, end, dtype=np.float64)[:, None] + .5) / height
        result[start:end] = _bilinear_premultiplied(pixels, u, v)
    return result


def _radial_zoom_premultiplied(pixels, ratio):
    height, width = pixels.shape[:2]
    result = np.empty_like(pixels)
    x = np.arange(width, dtype=np.float64)[None, :] + .5
    u = x / width
    center_x, strength = .25 + .5 * ratio, .2 * (1 - math.cos(2 * math.pi * ratio))
    for start in range(0, height, 8):
        end = min(height, start + 8)
        y = np.arange(start, end, dtype=np.float64)[:, None] + .5
        v = y / height
        # Canonical double-precision fragment jitter is deterministic across
        # export and browser seeking. OpenGL's fragment origin is bottom-left.
        random = np.sin(x * 12.9898 + (height - y) * 78.233 + .5 * 151.7182) * 43758.5453
        offset = random - np.floor(random)
        accumulated = np.zeros((end - start, width, 4), dtype=np.float64)
        total = np.zeros((end - start, width), dtype=np.float64)
        for index in range(41):
            percent = (index + offset) / 40
            weight = 4 * (percent - percent * percent)
            movement = percent * strength
            sampled = _bilinear_premultiplied(pixels, u + (center_x - u) * movement,
                                              v + (.5 - v) * movement)
            accumulated += sampled * weight[..., None]
            total += weight
        result[start:end] = accumulated / total[..., None]
    return result


def _directional_blur_premultiplied(pixels, width, height, parameters):
    """Collapse shared horizontal taps before filtering each source row.

    Linear vertical interpolation commutes with this horizontal operator. The
    source script's rounded Pi leaves a vertical displacement below 1e-10 UV;
    evaluating its intended horizontal direction avoids repeated row sampling.
    """
    source_width = pixels.shape[1]
    u = (np.arange(width, dtype=np.float64) + .5) / width
    dx, dy, sigma = parameters["step_x"], parameters["step_y"], parameters["sigma"]
    taps = min(1024, math.floor(parameters["sample"]))
    weights = [math.exp(-.5 * ((index * dx) ** 2 + (index * dy) ** 2) / (sigma * sigma))
               for index in range(1, taps + 1)] if sigma > 0 else []
    # At the largest supported canvas this operator is at most 15 MiB.
    operator = np.zeros((source_width, width), dtype=np.float32)
    columns = np.arange(width)
    total = np.zeros(width, dtype=np.float64)
    for index, weight in [(0, 1.), *[(sign * index, weight) for index, weight in enumerate(weights, 1)
                                   for sign in (-1, 1)]]:
        sampled_u = u + index * dx
        valid = (sampled_u >= 0) & (sampled_u <= 1)
        x = np.clip(sampled_u * source_width - .5, 0, source_width - 1)
        left = np.floor(x).astype(np.int32)
        right = np.minimum(left + 1, source_width - 1)
        fraction = x - left
        contribution = weight * valid
        operator[left, columns] += contribution * (1 - fraction)
        operator[right, columns] += contribution * fraction
        total += contribution
    operator /= total.astype(np.float32)[None, :]
    filtered = np.einsum("yxc,xw->ywc", pixels, operator, optimize=True)
    # A resize pass only interpolates y here: filtered already has output width.
    result = _resize_premultiplied(filtered, width, height)
    np.clip(result, 0, 255, out=result)
    return result


def _expose_premultiplied(pixels, exposure, correction):
    for start in range(0, pixels.shape[0], 16):
        block = pixels[start:start + 16]
        alpha = block[..., 3].astype(np.float64)
        for channel in range(3):
            color = np.divide(block[..., channel], alpha, out=np.zeros(alpha.shape), where=alpha > 0)
            color = np.power(np.power(np.power(np.clip(color, 0, 1), 2.2) * 2 ** exposure,
                                     1 / correction), 1 / 2.2)
            block[..., channel] = np.clip(color, 0, 1) * alpha


def _reference_transition_surface(outgoing, incoming, kind, ratio):
    if ratio == 0:
        return outgoing.convert("RGBA")
    if ratio == 1:
        return incoming.convert("RGBA")
    width, height = outgoing.size
    if kind == "radial_zoom":
        dissolve = .5 * 2 ** (20 * ratio - 10) if ratio < .5 else 1 - .5 * 2 ** (-20 * ratio + 10)
        pixels = _premultiplied_dissolve(outgoing, incoming, dissolve)
        pixels = _radial_zoom_premultiplied(pixels, ratio)
    else:
        parameters = _horizontal_blur_parameters(ratio, width, height)
        pixels = _premultiplied_dissolve(outgoing, incoming, parameters["mix"])
        scale = parameters["downscale"]
        pixels = _resize_premultiplied(pixels, max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
        pixels = _directional_blur_premultiplied(pixels, width, height, parameters)
        _expose_premultiplied(pixels, parameters["exposure"], parameters["correction"])
    return _rgba_from_premultiplied(pixels)


def _effect_transition_surface(outgoing, incoming, kind, ratio):
    # Endpoints retain even RGB data hidden behind zero alpha, and are copies.
    if ratio == 0:
        return outgoing.convert("RGBA")
    if ratio == 1:
        return incoming.convert("RGBA")
    pixels = _premultiplied_dissolve(outgoing, incoming, ratio)
    height, width = pixels.shape[:2]
    envelope = math.sin(math.pi * ratio) ** 2
    if kind == "white_flash":
        flash = envelope ** 2
        for channel in range(3):
            pixels[..., channel] *= 1 - flash
            pixels[..., channel] += pixels[..., 3] * flash
    elif kind == "blur_dissolve":
        _box_blur_premultiplied(pixels, math.floor(min(width, height) * .018 * envelope + .5))
    else:
        _distort_premultiplied(pixels, kind, ratio, envelope)
    return _rgba_from_premultiplied(pixels)


def transition_surface(outgoing, incoming, kind, ratio):
    """Return a single RGBA surface, preserving transparent lower layers."""
    ratio = min(1., max(0., ratio))
    if kind in REFERENCE_TRANSITION_TYPES:
        return _reference_transition_surface(outgoing, incoming, kind, ratio)
    if kind in EFFECT_TRANSITION_TYPES:
        return _effect_transition_surface(outgoing, incoming, kind, ratio)
    if kind == "crossfade":
        # Blend premultiplied channels so two translucent clips do not dim or
        # incorrectly expose the layers below them at the middle of a dissolve.
        with outgoing.convert("RGBa") as left, incoming.convert("RGBa") as right:
            with Image.blend(left, right, ratio) as blended:
                return blended.convert("RGBA")
    result = outgoing.copy()
    width, height = outgoing.size
    reveal = min(width, max(0, math.floor(width * ratio + .5)))
    if reveal:
        box = (width - reveal, 0, width, height) if kind == "wipe_left" else (0, 0, reveal, height)
        with incoming.crop(box) as part:
            result.paste(part, box[:2])
    return result


def animate_main_frame(frame, clip, local_time, duration, width, height, background=(0, 0, 0)):
    transform = animation_at(clip, local_time, duration)
    if transform == {"x": 0., "y": 0., "scale": 1., "opacity": 1.}:
        return frame
    scale = transform["scale"]
    cx, cy = width * (.5 + transform["x"]), height * (.5 + transform["y"])
    with Image.fromarray(frame.to_ndarray(format="rgb24")) as source:
        coefficients = (1 / scale, 0, width / 2 - cx / scale, 0, 1 / scale, height / 2 - cy / scale)
        with source.transform((width, height), Image.Transform.AFFINE, coefficients,
                              resample=Image.Resampling.BICUBIC, fillcolor=background) as moved:
            pixels = np.asarray(moved)
            if transform["opacity"] < 1:
                pixels = np.rint(pixels * transform["opacity"] + np.asarray(background) * (1 - transform["opacity"])).astype(np.uint8)
            return av.VideoFrame.from_ndarray(pixels, format="rgb24")


class MainTransitionCompositor:
    """At most one neighboring video decoder alongside the current segment."""

    def __init__(self, clips, paths, width, height, fps, cancel_event, background=(0, 0, 0)):
        self.pairs = transition_pairs(clips)
        self.paths, self.width, self.height, self.fps = paths, width, height, fps
        self.cancel_event, self.readers = cancel_event, {}
        self.background = background

    def _neighbor(self, clip, local):
        from video_editor import VideoReader, fade_gain, fit_frame
        from editor_speed import source_at
        source = source_at(clip, local)
        key = clip["id"]
        for old in list(self.readers):
            if old != key:
                self.readers.pop(old).close()
        if key not in self.readers:
            self.readers[key] = VideoReader(self.paths[clip["media_id"]], source, self.cancel_event)
        frame = fit_frame(self.readers[key].at(source), self.width, self.height, self.background)
        duration = clip_duration(clip)
        gain = fade_gain(local, duration, clip)
        if gain < 1:
            pixels = np.rint(frame.to_ndarray(format="rgb24") * gain + np.asarray(self.background) * (1 - gain)).astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        return animate_main_frame(frame, clip, local, duration, self.width, self.height, self.background)

    def apply(self, frame, clip, seconds):
        state = transition_at(self.pairs, seconds)
        if state is None:
            self.close()
            return frame
        previous, following, cut, setting, ratio = state
        previous_length, following_length = clip_duration(previous), clip_duration(following)
        previous_local = min(max(0, seconds - (cut - previous_length)), max(0, previous_length - 1 / self.fps))
        following_local = min(max(0, seconds - cut), max(0, following_length - 1 / self.fps))
        left_frame = frame if previous is clip else self._neighbor(previous, previous_local)
        right_frame = frame if following is clip else self._neighbor(following, following_local)
        with Image.fromarray(left_frame.to_ndarray(format="rgb24")) as left_rgb, Image.fromarray(right_frame.to_ndarray(format="rgb24")) as right_rgb:
            with left_rgb.convert("RGBA") as left, right_rgb.convert("RGBA") as right:
                with transition_surface(left, right, setting["type"], ratio) as combined:
                    with combined.convert("RGB") as pixels:
                        return av.VideoFrame.from_ndarray(np.asarray(pixels), format="rgb24")

    def close(self):
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()
