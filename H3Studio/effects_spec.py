"""Validated, server-owned recipes for isolated effects animation assets."""
from __future__ import annotations

import math
import re

from video_models import VIDEO_MODEL_FREEVIDEO, normalize_freevideo_quality


EFFECTS_VERSION = "20261111-effects-freevideo"
EFFECT_MODELS = ("h3", "fasth3_omni_nvfp4", "pika_h3_flex", VIDEO_MODEL_FREEVIDEO)
MAX_OUTPUT_PIXELS = 120_000_000
MAX_RETIME_SOURCE_DURATION = 120
_LABELS = {"kind": "特效類型", "name": "工作名稱", "prompt": "補充描述", "source_mode": "製作來源",
           "playback": "播放方式", "anchor": "固定點", "style": "美術風格", "speed": "動態速度",
           "direction": "動態方向", "video_model": "影片模型", "duration": "成品時長", "fps": "輸出幀率",
           "width": "畫布寬度", "height": "畫布高度", "trim_start": "擷取起點", "source_duration": "生成原片長度",
           "seed": "Seed", "strength": "動態強度", "black_level": "黑底清理", "softness": "透明柔邊",
           "temporal_smoothing": "邊緣時序平滑", "loop_blend": "接縫混合長度", "timing_mode": "時長處理",
           "freevideo_quality": "FreeVideo 品質"}
_PRESETS = (
    ("flame", "火焰", "持續燃燒或單次噴發", "loop", "bottom_center", "up", "#FF8A22", "#FFF2A8", 4),
    ("lightning", "閃電", "單次放電或週期閃擊", "once", "center", "down", "#72BCFF", "#FFFFFF", 1),
    ("explosion", "爆炸", "蓄力、爆發、完整消散", "once", "center", "outward", "#FF7622", "#FFEAB0", 2),
    ("smoke", "煙塵", "柔軟煙流與擴散", "once", "bottom_center", "up", "#B8B0A4", "#E8E5DE", 3),
    ("glow", "光效", "光環、呼吸或掃光", "loop", "center", "static", "#AD72FF", "#E8D8FF", 4),
    ("particles", "火花／粒子", "單次噴發或持續散射", "once", "bottom_center", "outward", "#FFB63D", "#FFF1BC", 2),
)


def effect_catalog():
    return [{"id": row[0], "label": row[1], "description": row[2],
             "defaults": {"kind": row[0], "playback": row[3], "anchor": row[4], "direction": row[5],
                          "color": row[6], "secondary_color": row[7], "duration": row[8]}}
            for row in _PRESETS]


def _number(value, key, default, minimum, maximum, *, integer=False):
    key = _LABELS.get(key, key)
    if isinstance(value, bool):
        raise ValueError(f"{key} 必須是數值。")
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{key} 必須是數值。") from None
    if not math.isfinite(number) or not minimum <= number <= maximum or (integer and not number.is_integer()):
        raise ValueError(f"{key} 必須介於 {minimum:g}～{maximum:g}" + (" 的整數。" if integer else "。"))
    return int(number) if integer else number


def _choice(payload, key, allowed, default):
    value = payload.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{_LABELS.get(key, key)}選項不受支援。")
    return value


def _text(payload, key, limit, default=""):
    value = payload.get(key, default)
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{_LABELS.get(key, key)}必須是 {limit} 字以內的文字。")
    return value.strip()


def normalize_effect_spec(payload):
    if not isinstance(payload, dict):
        raise ValueError("特效設定格式錯誤。")
    kind = _choice(payload, "kind", {p[0] for p in _PRESETS}, "flame")
    defaults = next(item["defaults"] for item in effect_catalog() if item["id"] == kind)
    source_mode = _choice(payload, "source_mode", {"text", "image", "video"}, "text")
    if source_mode == "video":
        # Model preferences do not affect CPU-only imported footage. Do not
        # make a stale/unknown generator selection block an existing video.
        candidate = payload.get("video_model", "h3")
        video_model = candidate if isinstance(candidate, str) and candidate in EFFECT_MODELS else "h3"
    else:
        video_model = _choice(payload, "video_model", EFFECT_MODELS, "h3")
    try:
        freevideo_quality = normalize_freevideo_quality(payload.get("freevideo_quality"))
    except ValueError:
        if source_mode != "video" and video_model == VIDEO_MODEL_FREEVIDEO:
            raise
        freevideo_quality = "medium"
    spec = {"version": EFFECTS_VERSION, "kind": kind,
            "name": _text(payload, "name", 80), "prompt": _text(payload, "prompt", 2000),
            "source_mode": source_mode,
            "timing_mode": _choice(payload, "timing_mode", {"trim", "retime"}, "trim"),
            "playback": _choice(payload, "playback", {"once", "loop", "pulse"}, defaults["playback"]),
            "anchor": _choice(payload, "anchor", {"center", "bottom_center"}, defaults["anchor"]),
            "style": _choice(payload, "style", {"game_cg", "cartoon", "realistic"}, "game_cg"),
            "speed": _choice(payload, "speed", {"slow", "normal", "fast"}, "normal"),
            "direction": _choice(payload, "direction", {"up", "outward", "down", "left", "right", "static"}, defaults["direction"]),
            "video_model": video_model, "freevideo_quality": freevideo_quality}
    for key in ("color", "secondary_color"):
        value = _text(payload, key, 7, defaults[key])
        if not re.fullmatch(r"#[A-Fa-f0-9]{6}", value):
            raise ValueError("特效顏色請使用 #RRGGBB 色碼。")
        spec[key] = value.upper()
    if kind == "smoke" and max(int(spec["color"][i:i + 2], 16) for i in (1, 3, 5)) < 40:
        raise ValueError("純黑／深黑煙與黑底無法可靠分離，請使用灰白或較亮煙色；黑煙需要獨立遮罩製作。")
    numbers = {
        "duration": (defaults["duration"], .25, 10, False), "fps": (24, 12, 30, True),
        "width": (640, 256, 1536, True), "height": (640, 256, 1536, True),
        "trim_start": (0, 0, 3600, False), "source_duration": (5, 5, 15, False),
        "seed": (1, 0, 2**53 - 1, True), "strength": (3, 1, 5, True),
        "black_level": (.015, 0, .12, False), "softness": (.08, .01, .5, False),
        "temporal_smoothing": (.15, 0, .5, False), "loop_blend": (0, 0, .5, False),
    }
    for key, (default, low, high, integer) in numbers.items():
        spec[key] = _number(payload.get(key), key, default, low, high, integer=integer)
    if spec["fps"] not in {12, 24, 30}:
        raise ValueError("序列幀率請選擇 12、24 或 30 FPS。")
    if spec["width"] % 32 or spec["height"] % 32:
        raise ValueError("畫布寬高請使用 32 的倍數。")
    spec["frame_count"] = max(1, round(spec["duration"] * spec["fps"]))
    spec["duration"] = spec["frame_count"] / spec["fps"]
    if spec["width"] * spec["height"] * spec["frame_count"] > MAX_OUTPUT_PIXELS:
        raise ValueError("序列總像素過大，請降低畫布、幀率或成品時長。")
    if spec["loop_blend"] > spec["duration"] / 3:
        raise ValueError("接縫混合長度不可超過成品時長的三分之一。")
    if spec["playback"] == "once" and spec["loop_blend"]:
        raise ValueError("單次特效不使用循環接縫混合，請將混合設為 0。")
    asset = payload.get("source_asset_id")
    if spec["source_mode"] != "text":
        if not isinstance(asset, str) or not re.fullmatch(r"[a-f0-9]{32}", asset):
            raise ValueError("請先上傳這次特效使用的圖片或影片。")
        spec["source_asset_id"] = asset
    else:
        spec["source_asset_id"] = None
    if spec["timing_mode"] == "retime" and spec["trim_start"] != 0:
        raise ValueError("完整原片變速會使用整段來源，取用起點必須為 0。")
    if (spec["timing_mode"] == "trim" and spec["source_mode"] != "video" and
            spec["trim_start"] + spec["duration"] > spec["source_duration"] + 1e-8):
        raise ValueError("成品取用區間超過生成原片長度，請延長原片或縮短成品。")
    if re.search(r"<\s*/?\s*d\b|<Subject\b|<Picture\b|integrated_multimodal_description\s*:", spec["prompt"], re.I):
        raise ValueError("補充描述只填特效外觀與動作，請移除對白標籤或完整模型格式。")
    # These are enforced product properties, never client-controlled bypasses.
    spec.update(background="black", silent=True, alpha_mode="straight", fixed_canvas=True)
    return spec


_VISUALS = {
    "flame": "A single isolated flame effect. Detailed tongues of fire flow naturally from a stationary emitter, with a bright core, softer colored outer flame and restrained sparks.",
    "lightning": "An isolated electrical discharge effect with a readable main arc, branching fine filaments and a brief soft halo. Keep its source and destination stationary.",
    "explosion": "A single isolated explosion effect: a small buildup, a rapid expanding luminous burst, a clear shockwave, and a complete natural dissipation. Keep the blast origin fixed.",
    "smoke": "An isolated light-colored smoke or dust effect with soft translucent wisps, billowing volume and readable continuous drift from a stationary emission area.",
    "glow": "An isolated luminous energy effect with broad smooth gradients, a defined light core and soft halo. Preserve weak peripheral glow without grain.",
    "particles": "An isolated shower of sparks and luminous particles from a stationary emitter, with readable trajectories, short trails and natural fading.",
}


def build_effect_generation_payload(value):
    spec = normalize_effect_spec(value)
    if spec["source_mode"] == "video":
        raise ValueError("匯入影片直接進入後製，不需影片生成配方。")
    styles = {"game_cg": "stylized polished game visual effects", "cartoon": "clean hand-painted cartoon visual effects", "realistic": "realistic physical visual effects"}
    direction = {"up": "upward", "down": "downward", "left": "toward frame-left", "right": "toward frame-right", "outward": "radially outward", "static": "internally around a fixed center"}[spec["direction"]]
    anchor = "bottom-center emitter" if spec["anchor"] == "bottom_center" else "center anchor"
    start, end = spec["trim_start"], spec["trim_start"] + spec["duration"]
    # Export samples the half-open interval: its last delivered frame occurs
    # one output-frame period before `end`. Finish the fade at that frame so a
    # short one-shot does not end while the source is still dissipating.
    last_frame = start + (spec["frame_count"] - 1) / spec["fps"]
    retime = spec["timing_mode"] == "retime"
    if retime:
        # The model still produces its normal aligned source clip. Event timing
        # belongs to that source, not the shorter CPU-retimed deliverable.
        from domain import FPS, aligned_frame_count
        source_frames = aligned_frame_count(spec["source_duration"])
        start, end = 0.0, source_frames / FPS
        last_frame = (source_frames - 1) / FPS
    event_span = last_frame - start
    peak = start + event_span * .25
    fade = start + event_span * .7
    once_opening = ("Start from the effect state already visible in the supplied first frame; do not replace that first frame with black."
                    if spec["source_mode"] == "image" else "Begin from an almost empty black frame.")
    event_interval = (f"Use the complete source clip from {start:.3f} to {end:.3f} seconds for one complete event."
                      if retime else f"The retained event interval is {start:.3f} to {end:.3f} seconds, not the entire source clip.")
    final_frame_label = "the last source frame" if retime else "the last exported frame"
    pulse_period = end - start if retime else spec["duration"]
    timeline = {
        "once": (f"{once_opening} {event_interval} Develop the visible event inside this interval, reach its main burst near {peak:.3f} seconds, "
                 f"then dissipate from {fade:.3f} seconds so all effect content has faded to black by {last_frame:.6f} seconds, "
                 f"{final_frame_label}. "
                 "Remain black afterwards. Do not repeat the event or postpone the burst beyond the retained interval."),
        "loop": "Maintain a continuous established effect throughout the clip, with stable energy and no startup, shutdown, fade to black or extra pause. Preserve the natural forward flow; never reverse the action. Make the final motion phase join smoothly to the opening.",
        "pulse": (f"Use a repeating period of {pulse_period:.3f} seconds, aligned with the retained interval "
                  f"{start:.3f} to {end:.3f} seconds. Each period includes a complete activation, bright pulse and full "
                  "dissipation, followed by a short calm black interval. Keep the cadence even, with no reverse playback."),
    }[spec["playback"]]
    prompt = (f"{styles[spec['style']]}. {_VISUALS[spec['kind']]} "
              f"Primary color {spec['color']}, highlight color {spec['secondary_color']}; "
              f"{spec['speed']} speed and intensity {spec['strength']} out of 5, flowing {direction}. "
              f"Lock the {anchor} in the same screen coordinates. "
              "Use one uninterrupted static shot with a uniformly pure black RGB(0,0,0) background. "
              "Keep the camera, framing, scale, perspective and crop completely fixed. "
              "Keep all effect extents inside the canvas with generous safe margins. "
              "Show only the isolated effect: no people, creatures, objects, scenery, ground, horizon, reflections, text, logos, subtitles or additional light sources. "
              f"{timeline} Complete silence, without speech, narration, music or sound effects.")
    if spec["prompt"]:
        prompt += "\nAdditional visual details, subordinate to the fixed isolation and camera rules: " + spec["prompt"]
    ratios = {"1:1": 1, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "21:9": 21 / 9}
    aspect = min(ratios, key=lambda key: abs(math.log((spec["width"] / spec["height"]) / ratios[key])))
    payload = {"mode": "fl2va" if spec["source_mode"] == "image" else "t2v",
               "prompt": prompt, "job_name": spec["name"] or f"特效動畫 · {spec['kind']}",
               "duration": spec["source_duration"], "aspect_ratio": aspect,
               "megapixels": min(.98, max(.25, spec["width"] * spec["height"] / 1e6)),
               "video_model": spec["video_model"], "freevideo_quality": spec["freevideo_quality"], "seed": spec["seed"], "steps": 20,
               "quality_mode": "native", "video_vae": "auto", "memory_optimization": False,
               "motion_profile": "none", "overall_soundscape": "N/A", "non_diegetic_music": "N/A",
               "export_frames": False, "translate_directions": True}
    if spec["source_mode"] == "image":
        payload["first_image_asset_id"] = spec["source_asset_id"]
    return payload


def validate_effect_compiled(compiled):
    """Visual-only products cannot acquire dialogue through prompt normalization."""
    if re.search(r"<\s*d\b|\(S\d+\)", compiled.prompt, re.I):
        raise ValueError("特效稿件包含對白或說話角色，請移除補充描述中的語音指示後重新預覽。")
    if compiled.video_model not in EFFECT_MODELS or compiled.mode not in {"t2v", "fl2va"}:
        raise ValueError("特效生成目前只支援已列出的文生／單圖模型配方。")
    if not re.search(r"overall_soundscape:\s*N/A", compiled.prompt) or not re.search(r"non_diegetic_music:\s*N/A", compiled.prompt):
        raise ValueError("特效生成必須維持靜音與無配樂設定。")
