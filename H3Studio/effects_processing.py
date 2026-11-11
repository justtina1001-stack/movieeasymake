"""Bounded CPU processing for black-background effect assets.

The matte is an explicit black-plate approximation, not a recovered physical
alpha channel. Emissive RGB is also delivered separately for additive blending.
"""
from __future__ import annotations

from fractions import Fraction
import io
import json
import math
from pathlib import Path
import zipfile

import av
import numpy as np
from PIL import Image, ImageDraw

from effects_spec import MAX_RETIME_SOURCE_DURATION


MAX_FRAMES = 300
MAX_PIXEL_FRAMES = 120_000_000
MAX_SOURCE_PIXELS = 16_777_216
MAX_DECODED_FRAMES = 20_000
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
EMISSIVE_KINDS = {"flame", "lightning", "explosion", "glow", "particles"}


class EffectProcessingError(ValueError):
    pass


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise EffectProcessingError("特效處理已取消；原始素材仍保留。")


def _progress(callback, stage, fraction):
    if callback is not None:
        callback(stage, min(1.0, max(0.0, float(fraction))))


def _finite(value, field):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise EffectProcessingError(f"{field}格式錯誤。") from error
    if not math.isfinite(number):
        raise EffectProcessingError(f"{field}必須是有限數值。")
    return number


def _settings(spec):
    if not isinstance(spec, dict):
        raise EffectProcessingError("特效設定格式錯誤。")
    try:
        width, height = int(spec["width"]), int(spec["height"])
        fps = int(spec["fps"])
        duration = _finite(spec["duration"], "成品長度")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise EffectProcessingError("特效尺寸、FPS 或成品長度格式錯誤。") from error
    trim_start = _finite(spec.get("trim_start", 0), "擷取起點")
    timing_mode = spec.get("timing_mode", "trim")
    if not isinstance(timing_mode, str) or timing_mode not in {"trim", "retime"}:
        raise EffectProcessingError("不支援的時長處理方式。")
    if timing_mode == "retime":
        trim_start = 0.0
    if width % 32 or height % 32 or not 256 <= width <= 1536 or not 256 <= height <= 1536:
        raise EffectProcessingError("特效畫布須為 256～1536 像素且尺寸是 32 的倍數。")
    if fps not in {12, 24, 30} or not 0.25 <= duration <= 10 or not 0 <= trim_start <= 3600:
        raise EffectProcessingError("特效長度、擷取起點或 FPS 超出範圍。")
    frames = round(duration * fps)
    if not 1 <= frames <= MAX_FRAMES or frames * width * height > MAX_PIXEL_FRAMES:
        raise EffectProcessingError("特效序列過大，請降低尺寸、FPS 或成品長度。")
    if spec.get("kind") not in {*EMISSIVE_KINDS, "smoke"}:
        raise EffectProcessingError("不支援的特效類型。")
    if spec.get("playback") not in {"once", "loop", "pulse"}:
        raise EffectProcessingError("不支援的特效播放方式。")
    if spec.get("anchor", "center") not in {"center", "bottom_center"}:
        raise EffectProcessingError("不支援的固定點。")
    if spec["kind"] == "smoke":
        color = str(spec.get("color", "#bbbbbb")).lstrip("#")
        try:
            channels = [int(color[index:index + 2], 16) for index in (0, 2, 4)]
        except (ValueError, IndexError) as error:
            raise EffectProcessingError("煙塵顏色格式錯誤。") from error
        if len(color) != 6 or max(channels) < 24:
            raise EffectProcessingError("純黑煙無法由黑底可靠還原，請使用較明亮的煙色或提供獨立遮罩。")
    for key, default, low, high in (("black_level", .015, 0, .12), ("softness", .08, .01, .5),
                                   ("temporal_smoothing", .15, 0, .5), ("loop_blend", 0, 0, .5)):
        value = _finite(spec.get(key, default), key)
        if not low <= value <= high:
            raise EffectProcessingError(f"{key}超出範圍。")
    return width, height, fps, frames, frames / fps, trim_start, timing_mode


def _letterbox(frame, width, height):
    image = frame.to_image().convert("RGB")
    ratio = min(width / image.width, height / image.height)
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    canvas.paste(resized, ((width - size[0]) // 2, (height - size[1]) // 2))
    return np.asarray(canvas).copy()


def _read_frames(source, width, height, fps, count, start, cancelled, timing_mode="trim"):
    """Sample a half-open interval, retaining source coordinates and speed."""
    if timing_mode == "retime":
        return _read_retimed_frames(source, width, height, fps, count, cancelled)
    target = start + np.arange(count, dtype=np.float64) / fps
    end = start + count / fps
    selected = []
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise EffectProcessingError("素材沒有可處理的影片畫面。")
        stream = container.streams.video[0]
        if stream.width * stream.height > MAX_SOURCE_PIXELS:
            raise EffectProcessingError("原片畫面過大，請先縮小素材。")
        rate = float(stream.average_rate or stream.guessed_rate or fps)
        if not math.isfinite(rate) or rate <= 0:
            rate = float(fps)
        stamp_tolerance = max(.000001, float(stream.time_base or 0) / 2 + .000001)
        declared_duration = (float(stream.duration * stream.time_base) if stream.duration is not None else
                             float(container.duration / av.time_base) if container.duration is not None else None)
        if declared_duration is not None and end > declared_duration + .0001:
            raise EffectProcessingError("擷取區間超過原片長度，請縮短成品長度或提前擷取起點。")
        origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else None
        seek_start = 0.0
        if origin is not None and start > 2:
            seek_start = start - 2
            container.seek(int((seek_start + origin) / stream.time_base), stream=stream, backward=True)
        previous = None
        previous_time = None
        last_time = 0.0
        last_image = None
        last_key = None
        source_dimensions = (stream.width, stream.height)
        maximum_gap = 0.0
        decoded = 0
        for decoded, frame in enumerate(container.decode(video=0), start=1):
            _check_cancel(cancelled)
            if decoded > MAX_DECODED_FRAMES:
                raise EffectProcessingError("原片擷取範圍過遠，請先裁短影片再匯入。")
            stamp = float(frame.time) if frame.time is not None else seek_start + (decoded - 1) / rate
            if origin is None:
                origin = stamp
            stamp -= origin
            if previous_time is not None:
                maximum_gap = max(maximum_gap, stamp - previous_time)
            while len(selected) < count and stamp + stamp_tolerance >= target[len(selected)]:
                chosen = frame if abs(stamp - target[len(selected)]) <= stamp_tolerance or previous is None else previous
                key = decoded if chosen is frame else decoded - 1
                if last_key != key:
                    last_image = _letterbox(chosen, width, height)
                    last_key = key
                selected.append(last_image)
            previous, previous_time, last_time = frame, stamp, stamp
            if len(selected) == count and stamp + 1 / rate >= end - stamp_tolerance:
                break
        if not decoded:
            raise EffectProcessingError("原片沒有可解碼的畫面。")
        # A final frame occupies one source-frame interval; this is resampling,
        # not permission to extend a genuinely truncated source indefinitely.
        observed_end = last_time + 1 / rate
        while len(selected) < count and target[len(selected)] < observed_end - .000001:
            if last_key != decoded:
                last_image = _letterbox(previous, width, height)
                last_key = decoded
            selected.append(last_image)
        if len(selected) != count or end > observed_end + stamp_tolerance:
            raise EffectProcessingError("原片有效畫面不足，無法依指定區間輸出；未補幀或延長素材。")
    ratio = min(width / source_dimensions[0], height / source_dimensions[1])
    content_width, content_height = max(1, round(source_dimensions[0] * ratio)), max(1, round(source_dimensions[1] * ratio))
    return selected, {"fps": round(rate, 6), "width": source_dimensions[0], "height": source_dimensions[1],
                      "duration": round(declared_duration or observed_end, 6), "timing_mode": "trim",
                      "selected_source_duration": round(end - start, 6), "playback_speed": 1.0,
                      "sampled_first_time": round(float(target[0]), 6),
                      "sampled_last_time": round(float(target[-1]), 6), "trim_start": round(start, 6),
                      "trim_end": round(end, 6), "maximum_frame_gap": round(maximum_gap, 6),
                      "content_rect": [(width - content_width) // 2, (height - content_height) // 2, content_width, content_height],
                      "resampled": abs(rate - fps) > .01}


def _inspect_retime_source(source, fps, cancelled):
    """Read actual video timestamps without retaining full-resolution RGB frames.

    The container duration can describe a longer audio track. A bounded first
    pass establishes the video endpoint before a second pass selects RGB frames.
    """
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise EffectProcessingError("素材沒有可處理的影片畫面。")
        stream = container.streams.video[0]
        if stream.width * stream.height > MAX_SOURCE_PIXELS:
            raise EffectProcessingError("原片畫面過大，請先縮小素材。")
        rate = float(stream.average_rate or stream.guessed_rate or fps)
        if not math.isfinite(rate) or rate <= 0:
            rate = float(fps)
        tolerance = max(.000001, float(stream.time_base or 0) / 2 + .000001)
        if stream.duration is not None and float(stream.duration * stream.time_base) > MAX_RETIME_SOURCE_DURATION + tolerance:
            raise EffectProcessingError(f"整片變速僅支援 {MAX_RETIME_SOURCE_DURATION:g} 秒內的原片，請先裁短素材。")
        origin, last_time, last_interval, maximum_gap, decoded = None, 0.0, 1 / rate, 0.0, 0
        for decoded, frame in enumerate(container.decode(video=0), start=1):
            _check_cancel(cancelled)
            if decoded > MAX_DECODED_FRAMES:
                raise EffectProcessingError("整片變速的原片影格過多，請先裁短素材。")
            stamp = float(frame.time) if frame.time is not None else (decoded - 1) / rate
            if not math.isfinite(stamp):
                raise EffectProcessingError("原片時間戳無效，無法進行整片變速。")
            if origin is None:
                origin = stamp
            stamp -= origin
            if decoded > 1:
                if stamp < last_time - tolerance:
                    raise EffectProcessingError("原片時間戳順序異常，無法進行整片變速。")
                maximum_gap = max(maximum_gap, stamp - last_time)
            duration = float(getattr(frame, "duration", 0) or 0) * float(frame.time_base or stream.time_base or 0)
            last_interval = duration if math.isfinite(duration) and duration > 0 else 1 / rate
            last_time = stamp
            if stamp + last_interval > MAX_RETIME_SOURCE_DURATION + tolerance:
                raise EffectProcessingError(f"整片變速僅支援 {MAX_RETIME_SOURCE_DURATION:g} 秒內的原片，請先裁短素材。")
        if not decoded:
            raise EffectProcessingError("原片沒有可解碼的畫面。")
        return {"fps": rate, "width": stream.width, "height": stream.height,
                "duration": last_time + last_interval, "last_time": last_time,
                "frame_count": decoded, "maximum_frame_gap": maximum_gap, "tolerance": tolerance}


def _read_retimed_frames(source, width, height, fps, count, cancelled):
    """Resample the complete source, including its last frame, without interpolation."""
    observed = _inspect_retime_source(source, fps, cancelled)
    target = np.linspace(0, observed["last_time"], count, dtype=np.float64)
    selected, origin, previous, last_image, last_key = [], None, None, None, None
    last_time, decoded = 0.0, 0
    with av.open(str(source)) as container:
        for decoded, frame in enumerate(container.decode(video=0), start=1):
            _check_cancel(cancelled)
            if decoded > MAX_DECODED_FRAMES or decoded > observed["frame_count"]:
                raise EffectProcessingError("原片在處理期間變更，請重新匯入素材。")
            stamp = float(frame.time) if frame.time is not None else (decoded - 1) / observed["fps"]
            if origin is None:
                origin = stamp
            stamp -= origin
            if not math.isfinite(stamp) or stamp + 1 / observed["fps"] > MAX_RETIME_SOURCE_DURATION + observed["tolerance"]:
                raise EffectProcessingError("原片時間範圍異常，請重新匯入素材。")
            while len(selected) < count and stamp + observed["tolerance"] >= target[len(selected)]:
                chosen = frame if abs(stamp - target[len(selected)]) <= observed["tolerance"] or previous is None else previous
                key = decoded if chosen is frame else decoded - 1
                if last_key != key:
                    last_image = _letterbox(chosen, width, height)
                    last_key = key
                selected.append(last_image)
            previous, last_time = frame, stamp
        if (decoded != observed["frame_count"] or len(selected) != count or
                abs(last_time - observed["last_time"]) > observed["tolerance"]):
            raise EffectProcessingError("原片在處理期間變更，請重新匯入素材。")
        # The N output frames occupy [0, N/fps). Sampling first through last
        # source frames makes a one-shot's final state survive compression.
        selected[-1] = _letterbox(previous, width, height)
    ratio = min(width / observed["width"], height / observed["height"])
    content_width = max(1, round(observed["width"] * ratio))
    content_height = max(1, round(observed["height"] * ratio))
    duration, output_duration = observed["duration"], count / fps
    return selected, {"fps": round(observed["fps"], 6), "width": observed["width"], "height": observed["height"],
                      "duration": round(duration, 6), "timing_mode": "retime", "trim_start": 0.0,
                      "trim_end": round(duration, 6), "selected_source_duration": round(duration, 6),
                      "playback_speed": round(duration / output_duration, 6),
                      "sampled_first_time": 0.0, "sampled_last_time": round(observed["last_time"], 6),
                      "sampling": "first_to_last_frame", "interpolated": False,
                      "maximum_frame_gap": round(observed["maximum_frame_gap"], 6),
                      "content_rect": [(width - content_width) // 2, (height - content_height) // 2, content_width, content_height],
                      "resampled": True}


def _border(image):
    """Use a fixed border across time, never a moving subject bounding box."""
    band = max(1, min(image.shape[:2]) // 64)
    return np.concatenate((image[:band].reshape(-1, 3), image[-band:].reshape(-1, 3),
                           image[band:-band, :band].reshape(-1, 3),
                           image[band:-band, -band:].reshape(-1, 3)), axis=0)


def _black_plate(frames, source_info):
    x, y, width, height = source_info["content_rect"]
    samples = []
    for index in np.unique(np.linspace(0, len(frames) - 1, min(24, len(frames)), dtype=int)):
        border = _border(frames[index][y:y + height, x:x + width])
        samples.append(border[::max(1, len(border) // 1024)])
    border = np.concatenate(samples).astype(np.float32) / 255
    # Low percentile avoids treating a brief edge spark as background. A colored
    # or bright border remains a QA failure rather than being silently accepted.
    plate = np.percentile(border, 15, axis=0).astype(np.float32)
    return np.minimum(plate, .12), {"border_mean": float(border.max(axis=1).mean()),
                                  "border_p95": float(np.percentile(border.max(axis=1), 95)),
                                  "estimated_black_rgb": [round(float(x), 6) for x in plate]}


def _clean_rgb(image, plate, black_level):
    rgb = np.maximum(image.astype(np.float32) / 255 - plate[None, None, :], 0)
    if black_level > 0:
        strength = np.clip(rgb.max(axis=2) / black_level, 0, 1)
        strength = strength * strength * (3 - 2 * strength)
        rgb *= strength[:, :, None]
    return rgb


def _rgba(cleaned, previous, following, spec):
    base = cleaned.max(axis=2)
    opacity = base.copy()
    smoothing = float(spec.get("temporal_smoothing", .15))
    softness = float(spec.get("softness", .08))
    if smoothing and spec["kind"] != "particles":
        median = np.median(np.stack((previous.max(axis=2), base, following.max(axis=2))), axis=0)
        uncertain = np.clip((softness - base) / softness, 0, 1)
        # Only refine low-opacity uncertainty, preserving bright moving cores.
        # Never lower alpha below premultiplied RGB: straight RGB must stay <=1.
        opacity = np.maximum(base, base + smoothing * uncertain * (median - base))
    alpha = np.rint(np.clip(opacity, 0, 1) * 255).astype(np.uint8)
    # Quantization may round opacity down. Clamp it to the encoded RGB maximum
    # before division so recompositing over black does not darken the effect.
    black = np.rint(np.clip(cleaned, 0, 1) * 255).astype(np.uint8)
    alpha = np.maximum(alpha, black.max(axis=2))
    straight = np.zeros_like(cleaned)
    np.divide(black.astype(np.float32), alpha[:, :, None], out=straight,
              where=alpha[:, :, None] > 0)
    rgba = np.dstack((np.rint(np.clip(straight, 0, 1) * 255).astype(np.uint8), alpha))
    rgba[alpha == 0, :3] = 0
    return rgba, black


def _encode_preview(path, images, fps, cancelled):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=Fraction(fps, 1))
        stream.width, stream.height = images[0].shape[1], images[0].shape[0]
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "19", "preset": "fast"}
        for index, image in enumerate(images):
            _check_cancel(cancelled)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def _backgrounds(width, height):
    yy, xx = np.indices((height, width))
    checker = np.where(((xx // 24 + yy // 24) % 2)[..., None], 178, 100).astype(np.uint8)
    return np.repeat(checker, 3, axis=2), np.full((height, width, 3), 242, dtype=np.uint8)


def _composite(rgba, background):
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255
    return np.rint(rgba[:, :, :3] * alpha + background * (1 - alpha)).clip(0, 255).astype(np.uint8)


def _qa(rgba, black, spec, border_metrics, source_info):
    first, last = rgba[0], rgba[-1]
    first_rgb, last_rgb = black[0].astype(np.float32) / 255, black[-1].astype(np.float32) / 255
    alpha_first, alpha_last = first[:, :, 3].astype(np.float32) / 255, last[:, :, 3].astype(np.float32) / 255
    union = (alpha_first > .02) | (alpha_last > .02)
    seam_rgb = float(np.abs(first_rgb - last_rgb)[union].mean()) if union.any() else 0.0
    seam_alpha = float(np.abs(alpha_first - alpha_last)[union].mean()) if union.any() else 0.0
    energies = [float(image.max(axis=2).mean()) / 255 for image in black]
    edge_frames, occupancy, empty_frames = 0, [], 0
    for image in rgba:
        alpha = image[:, :, 3]
        occupancy.append(float((alpha > 5).mean()))
        edge_frames += int(bool((alpha[0] > 8).any() or (alpha[-1] > 8).any() or
                                (alpha[:, 0] > 8).any() or (alpha[:, -1] > 8).any()))
        empty_frames += int(alpha.max() < 3)
    relative_brightness_jump = abs(energies[-1] - energies[0]) / max(.005, max(energies))
    tail_count = min(len(energies), max(1, round(int(spec["fps"]) * .12)))
    peak_energy = max(energies)
    tail_energy_ratio = float(np.mean(energies[-tail_count:])) / max(.00001, peak_energy)
    final_energy_ratio = energies[-1] / max(.00001, peak_energy)
    tail_occupancy_ratio = float(np.mean(occupancy[-tail_count:])) / max(.0001, max(occupancy))
    adjacent_rgb, adjacent_alpha = [], []
    for index in range(1, len(rgba)):
        before_alpha = rgba[index - 1][:, :, 3].astype(np.float32) / 255
        after_alpha = rgba[index][:, :, 3].astype(np.float32) / 255
        visible = (before_alpha > .02) | (after_alpha > .02)
        if visible.any():
            adjacent_rgb.append(float(np.abs(black[index].astype(np.float32) - black[index - 1])[visible].mean()) / 255)
            adjacent_alpha.append(float(np.abs(after_alpha - before_alpha)[visible].mean()))
        else:
            adjacent_rgb.append(0.0); adjacent_alpha.append(0.0)
    typical_rgb = float(np.median(adjacent_rgb)) if adjacent_rgb else 0.0
    typical_alpha = float(np.median(adjacent_alpha)) if adjacent_alpha else 0.0
    def center(image):
        energy = image.max(axis=2)
        yy, xx = np.indices(energy.shape)
        total = float(energy.sum())
        return ((float((xx * energy).sum()) / total, float((yy * energy).sum()) / total)
                if total > .01 else None)
    first_center, last_center = center(first_rgb), center(last_rgb)
    seam_center_shift = math.dist(first_center, last_center) if first_center and last_center else None
    warnings, notes = [], []
    if max(occupancy) < .0001:
        warnings.append("沒有偵測到足夠的特效內容，請檢查原片與去背強度。")
    if max(occupancy) > .8:
        warnings.append("特效幾乎覆蓋整個畫布，可能包含背景或多餘場景，請檢查透明預覽。")
    if edge_frames:
        warnings.append(f"有 {edge_frames} 幀內容碰到畫布邊緣，請確認是否裁切到光暈或煙塵。")
    if border_metrics["border_mean"] > .06 or border_metrics["border_p95"] > .25:
        warnings.append("原片邊界並非乾淨黑底，透明度僅為推估；請檢查是否保留了場景或灰底。")
    if (spec["playback"] == "once" and peak_energy > .0001 and tail_energy_ratio > .15 and
            final_energy_ratio > .08 and tail_occupancy_ratio > .1):
        warnings.append("單次特效在成品末段仍有明顯內容，可能尚未完全消散；請延長成品或調整擷取區間。")
    if spec["playback"] in {"loop", "pulse"}:
        if (seam_rgb > .12 or seam_alpha > .18 or relative_brightness_jump > .25 or
                (seam_center_shift is not None and seam_center_shift > .03 * min(first.shape[:2]))):
            warnings.append("循環接縫有可見亮度或透明度差異，請調整擷取區間並連播檢查。")
        if np.array_equal(first, last) and len(rgba) > 2 and max(energies) > .005:
            warnings.append("首尾畫面完全相同，請確認循環接縫是否有重複停頓。")
    if source_info.get("timing_mode") == "retime":
        notes.append("已依整支原片時間軸變速取樣並保留首尾畫面；沒有插值補畫，放慢時可能有重複影格。")
    elif source_info["resampled"]:
        warnings.append("輸出 FPS 與原片不同，已依原時間軸重採樣，沒有新增動作細節。")
    if source_info["maximum_frame_gap"] > max(.2, 2.5 / source_info["fps"]):
        warnings.append("原片時間戳有較大間隔，請檢查動態是否跳格。")
    if float(spec.get("loop_blend", 0)) > 0 and spec["playback"] in {"loop", "pulse"}:
        warnings.append("已套用短尾端接縫混合；未倒放，仍須連播檢查重影或短暫停頓。")
    if spec["kind"] == "explosion":
        warnings.append("爆炸的煙塵與發光尚未獨立分層，建議先使用一般透明版；黑底版僅供純發光區域的合成檢查。")
    return {"status": "needs_adjustment" if warnings else "passed", "warnings": warnings, "notes": notes,
            "review_note": "數值檢查通過仍需觀看透明預覽；黑底素材的真實透明度無法唯一還原。",
            "metrics": {**border_metrics, "seam_rgb_difference": round(seam_rgb, 6),
                        "seam_alpha_difference": round(seam_alpha, 6),
                        "relative_brightness_jump": round(relative_brightness_jump, 6),
                        "tail_energy_to_peak_ratio": round(tail_energy_ratio, 6),
                        "final_energy_to_peak_ratio": round(final_energy_ratio, 6),
                        "tail_occupancy_to_peak_ratio": round(tail_occupancy_ratio, 6),
                        "adjacent_rgb_median_difference": round(typical_rgb, 6),
                        "adjacent_alpha_median_difference": round(typical_alpha, 6),
                        "adjacent_rgb_p95_difference": round(float(np.percentile(adjacent_rgb, 95)), 6) if adjacent_rgb else 0,
                        "adjacent_alpha_p95_difference": round(float(np.percentile(adjacent_alpha, 95)), 6) if adjacent_alpha else 0,
                        "seam_to_adjacent_rgb_ratio": round(seam_rgb / max(typical_rgb, .0001), 4),
                        "seam_to_adjacent_alpha_ratio": round(seam_alpha / max(typical_alpha, .0001), 4),
                        "seam_energy_center_shift_px": round(seam_center_shift, 4) if seam_center_shift is not None else None,
                        "edge_touch_frames": edge_frames, "empty_frames": empty_frames,
                        "maximum_occupancy": round(max(occupancy), 6),
                        "minimum_occupancy": round(min(occupancy), 6)}}


def process_effect_video(source: Path, output_dir: Path, spec: dict, *, progress=None, cancelled=None) -> dict:
    """Export fixed-canvas straight-alpha and additive assets without GPU work."""
    width, height, fps, frame_count, duration, trim_start, timing_mode = _settings(spec)
    spec = {**spec, "timing_mode": timing_mode, "trim_start": trim_start}
    source, output_dir = Path(source), Path(output_dir)
    _check_cancel(cancelled)
    names = ("sequence.zip", "preview.mp4", "preview-checker.mp4", "preview-white.mp4",
             "contact-sheet.png", "sequence.json", "qa.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    if any((output_dir / name).exists() for name in names):
        raise EffectProcessingError("這個輸出版本已有成品，請建立新的處理版本。")
    try:
        _progress(progress, "matting", 0)
        frames, source_info = _read_frames(source, width, height, fps, frame_count, trim_start, cancelled, timing_mode)
        plate, border_metrics = _black_plate(frames, source_info)
        _progress(progress, "matting", .1)
        rgba, black = [], []
        level = float(spec.get("black_level", .015))
        previous = _clean_rgb(frames[0], plate, level)
        current = previous
        following = _clean_rgb(frames[min(1, frame_count - 1)], plate, level)
        for index in range(frame_count):
            _check_cancel(cancelled)
            result, emitted = _rgba(current, previous, following, spec)
            rgba.append(result); black.append(emitted)
            previous, current = current, following
            following = _clean_rgb(frames[min(index + 2, frame_count - 1)], plate, level)
            _progress(progress, "matting", .1 + .9 * (index + 1) / frame_count)
        del frames
        _progress(progress, "looping", 0)
        blend_frames = min(round(float(spec.get("loop_blend", 0)) * fps), frame_count // 4)
        if spec["playback"] in {"loop", "pulse"} and blend_frames:
            opening = black[0].astype(np.float32)
            for index in range(blend_frames):
                target = frame_count - blend_frames + index
                weight = (index + 1) / (blend_frames + 1)
                mixed = ((1 - weight) * black[target] + weight * opening) / 255
                rgba[target], black[target] = _rgba(mixed, mixed, mixed, {**spec, "temporal_smoothing": 0})
        _progress(progress, "looping", 1)
        _progress(progress, "checking", 0)
        qa = _qa(rgba, black, spec, border_metrics, source_info)
        _progress(progress, "checking", 1)
        pivot = {"x": width / 2, "y": height if spec.get("anchor") == "bottom_center" else height / 2,
                 "normalized_x": .5, "normalized_y": 1 if spec.get("anchor") == "bottom_center" else .5,
                 "origin": "top_left"}
        emissive = spec["kind"] in EMISSIVE_KINDS
        manifest = {"format": "PNG", "frame_count": frame_count, "fps": fps, "width": width,
                    "height": height, "duration": duration, "timing_mode": timing_mode, "alpha_mode": "straight",
                    "blend_mode": "additive" if spec["kind"] in EMISSIVE_KINDS - {"explosion"} else "normal", "pivot": pivot,
                    "playback": spec["playback"], "loop": spec["playback"] in {"loop", "pulse"},
                    "loop_frame_range": [0, frame_count], "endpoint_convention": "half_open",
                    "filename_pattern": "rgba/frame_%06d.png", "additive_filename_pattern": "additive/frame_%06d.png" if emissive else None,
                    "matting_method": "sequence_black_plate_soft_alpha", "alpha_is_estimated": True,
                    "mixed_layers_separated": False,
                    "color_space": "sRGB", "channel_depth": 8,
                    "matting_note": "由整段黑底素材估計透明度；發光效果優先使用黑底 Additive，RGBA 為一般合成相容版。",
                    "source": source_info, "settings": {key: value for key, value in spec.items()
                                                           if key not in {"source_asset_id", "image_asset_id", "source_path"}},
                    "qa": qa}
        _progress(progress, "exporting", 0)
        total = 0
        with zipfile.ZipFile(output_dir / "sequence.zip", "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, image in enumerate(rgba):
                _check_cancel(cancelled)
                layers = [("rgba", image, "RGBA")]
                if emissive:
                    layers.append(("additive", black[index], "RGB"))
                for folder, array, mode in layers:
                    buffer = io.BytesIO()
                    Image.fromarray(array, mode).save(buffer, "PNG", compress_level=3)
                    total += buffer.tell()
                    if total > MAX_OUTPUT_BYTES:
                        raise EffectProcessingError("輸出檔案過大，請降低尺寸、FPS 或成品長度。")
                    archive.writestr(f"{folder}/frame_{index + 1:06d}.png", buffer.getvalue())
                _progress(progress, "exporting", .55 * (index + 1) / frame_count)
            for name, value in (("sequence.json", manifest), ("qa.json", qa)):
                text = json.dumps(value, ensure_ascii=False, indent=2)
                archive.writestr(name, text.encode("utf-8"))
                (output_dir / name).write_text(text, encoding="utf-8")
        checker, white = _backgrounds(width, height)
        _encode_preview(output_dir / "preview.mp4", black, fps, cancelled)
        _progress(progress, "exporting", .7)
        _encode_preview(output_dir / "preview-checker.mp4", [_composite(image, checker) for image in rgba], fps, cancelled)
        _progress(progress, "exporting", .85)
        _encode_preview(output_dir / "preview-white.mp4", [_composite(image, white) for image in rgba], fps, cancelled)
        tile_width = min(width, 256)
        tile_height = max(1, round(height * tile_width / width))
        sheet = Image.new("RGB", (tile_width * 4, (tile_height + 24) * 2), (26, 28, 32))
        for slot, index in enumerate(np.linspace(0, frame_count - 1, 8, dtype=int)):
            image = Image.fromarray(_composite(rgba[index], checker)).resize((tile_width, tile_height), Image.Resampling.LANCZOS)
            x, y = (slot % 4) * tile_width, (slot // 4) * (tile_height + 24)
            sheet.paste(image, (x, y + 24))
            ImageDraw.Draw(sheet).text((x + 8, y + 5), f"{index + 1:06d}  {index / fps:.2f}s", fill=(230, 230, 230))
        sheet.save(output_dir / "contact-sheet.png")
        _check_cancel(cancelled)
        _progress(progress, "exporting", 1)
        return {key: manifest[key] for key in ("frame_count", "fps", "width", "height", "duration", "timing_mode", "pivot", "alpha_mode", "blend_mode", "qa")} | {
            "matting_method": manifest["matting_method"], "alpha_is_estimated": True,
            "source": source_info,
            "files": [{"name": name, "label": label, "kind": kind} for name, label, kind in (
                ("sequence.zip", "透明 PNG 序列與設定檔", "archive"),
                ("preview.mp4", "黑底預覽", "preview"), ("preview-checker.mp4", "棋盤格預覽", "preview"),
                ("preview-white.mp4", "白底預覽", "preview"), ("contact-sheet.png", "序列總覽", "image"),
                ("sequence.json", "序列設定", "metadata"), ("qa.json", "檢查結果", "metadata"))]}
    except EffectProcessingError:
        raise
    except (av.FFmpegError, OSError, ValueError, OverflowError) as error:
        raise EffectProcessingError("特效處理失敗，請檢查來源影片；原始素材仍保留。") from error
