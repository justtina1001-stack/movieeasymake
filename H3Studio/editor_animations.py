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
TRANSITION_TYPES = ("crossfade", "wipe_left", "wipe_right")
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
    return clean


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


def transition_surface(outgoing, incoming, kind, ratio):
    """Return a single RGBA surface, preserving transparent lower layers."""
    ratio = min(1., max(0., ratio))
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


def animate_main_frame(frame, clip, local_time, duration, width, height):
    transform = animation_at(clip, local_time, duration)
    if transform == {"x": 0., "y": 0., "scale": 1., "opacity": 1.}:
        return frame
    scale = transform["scale"]
    cx, cy = width * (.5 + transform["x"]), height * (.5 + transform["y"])
    with Image.fromarray(frame.to_ndarray(format="rgb24")) as source:
        coefficients = (1 / scale, 0, width / 2 - cx / scale, 0, 1 / scale, height / 2 - cy / scale)
        with source.transform((width, height), Image.Transform.AFFINE, coefficients,
                              resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0)) as moved:
            pixels = np.asarray(moved)
            if transform["opacity"] < 1:
                pixels = np.rint(pixels * transform["opacity"]).astype(np.uint8)
            return av.VideoFrame.from_ndarray(pixels, format="rgb24")


class MainTransitionCompositor:
    """At most one neighboring video decoder alongside the current segment."""

    def __init__(self, clips, paths, width, height, fps, cancel_event):
        self.pairs = transition_pairs(clips)
        self.paths, self.width, self.height, self.fps = paths, width, height, fps
        self.cancel_event, self.readers = cancel_event, {}

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
        frame = fit_frame(self.readers[key].at(source), self.width, self.height)
        duration = clip_duration(clip)
        gain = fade_gain(local, duration, clip)
        if gain < 1:
            pixels = np.rint(frame.to_ndarray(format="rgb24") * gain).astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        return animate_main_frame(frame, clip, local, duration, self.width, self.height)

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
