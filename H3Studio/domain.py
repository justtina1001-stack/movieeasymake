from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from custom_loras import normalize_loras, active_loras, required_lora_trigger_specs, validate_lora_requirements
from dialogue import format_dialogue, visual_direction_text
from h3_prompt_format import base_prompt as format_base_prompt, combine_storyboards, cut_time, language_warning, number_speakers, rewrite_aliases, unpack_prompt
from typing import Any


FPS = 24
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_AUDIOS = 3
MAX_REFERENCE_VIDEOS = 3
ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
REFERENCE_SUBJECT_TYPES = {"character", "creature", "object", "background", "style", "motion", "effect"}
VIDEO_VAE_FILENAMES = {
    "int8": "minimax_h3_video_vae_int8_convrot.safetensors",
    "fp16": "minimax_h3_video_vae_fp16.safetensors",
}
VIDEO_VAE_MODES = {"auto", *VIDEO_VAE_FILENAMES}

TURBO_PROFILE_FL_544 = "fl2v_544"
TURBO_PROFILE_FL_768 = "fl2v_768"
TURBO_PROFILE_FL_768_FAST_V11 = "fl2v_768_fast_v11"
TURBO_PROFILE_FL_768_QUALITY_V10 = "fl2v_768_quality_v10"
TURBO_PROFILE_REF_544 = "ref2v_544"
TURBO_PROFILE_FL_768_SLA = "fl2v_768_sla"
TURBO_PROFILE_FL_768_AUDIO_V12 = "fl2v_768_audio_v12"
TURBO_PROFILE_REF_768_QUALITY_V10 = "ref2v_768_quality_v10"
FL_ONLY_QUALITY_MODES = {"turbo_fast", "turbo_quality", "sparse_experimental", "turbo_sla", "turbo_audio"}
QUALITY_MODES = {"native", "turbo", "turbo_ref_quality"} | FL_ONLY_QUALITY_MODES
TURBO_LORA_CANDIDATES: dict[str, tuple[str, ...]] = {
    TURBO_PROFILE_FL_544: (
        "minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768: (
        "minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768_FAST_V11: (
        "minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768_QUALITY_V10: (
        "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_REF_544: (
        "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768_SLA: (
        "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768_AUDIO_V12: (
        "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_REF_768_QUALITY_V10: (
        "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
    ),
}

MOTION_PROFILES = {
    "natural": "Use continuous natural body and object mechanics, with visible weight transfer, inertia, contact, reaction, and settling; avoid floating, teleportation, and limb twitching.",
    "impact": "Show anticipation, acceleration, contact, force transfer, rebound, and settling; emphasize weight and connected body reactions at impact.",
    "action": "Use readable continuous full-body action with natural transitions between starts, turns, steps, attacks, defenses, and stops; preserve silhouettes and spatial direction.",
    "dance": "Follow the beat with coordinated torso, limbs, weight, and ground contact; keep turns and pose changes fluid without foot sliding.",
    "chase": "Show speed, inertia, and terrain interaction with natural running cycles; keep the moving subject readable and spatially consistent.",
    "vfx": "Develop effects through activation, growth, burst, and dissipation; synchronize energy paths with action, contact points, and environmental reactions without hiding the main silhouette.",
    "none": "",
}

MOTION_INTENSITIES = {
    1: "Keep motion restrained, subtle, and physically credible.",
    2: "Keep motion stable with moderate variation.",
    3: "Use moderate motion intensity with natural, readable performance.",
    4: "Use pronounced motion, speed changes, and pose variation.",
    5: "Use very strong motion while preserving anatomy, physics, and temporal continuity.",
}

PHYSICS_STYLES = {
    "realistic": "Use realistic weight, collisions, fabric, hair, and secondary inertia.",
    "balanced": "Use natural mechanics with moderate cinematic exaggeration.",
    "stylized": "Exaggerated speed and poses are allowed, but preserve causal motion, contact points, and continuous weight transfer.",
}

CAMERA_RESPONSES = {
    "stable": "The camera remains stable, with smooth intentional movement and no aimless shake.",
    "follow": "The camera follows the subject smoothly with natural acceleration and deceleration, preserving composition and eyeline.",
    "handheld": "Use restrained handheld movement with small amplitude and physical weight, without subject drift.",
    "impact": "Use a brief shake and recovery only at an impact or burst, without continuous jitter.",
}


class RequestError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledRequest:
    mode: str
    prompt: str
    width: int
    height: int
    length: int
    requested_duration: float
    actual_duration: float
    seed: int
    steps: int
    scheduler: str
    ref_image_size: str
    quality_mode: str
    sampler_name: str
    turbo_profile: str | None
    turbo_lora: str | None
    turbo_lora_strength: float
    shift_video: float | None
    shift_audio: float | None
    first_image: str | None
    last_image: str | None
    reference_images: list[str]
    reference_videos: list[str]
    reference_video_use_audio: list[bool]
    reference_audios: list[str]
    guides: list[dict[str, Any]]
    continuation_source_job: str | None
    continuation_source_asset: str | None
    continuation_merge: bool
    continuation_audio: str
    mapping: list[dict[str, Any]]
    custom_loras: list[dict[str, Any]] = field(default_factory=list)
    memory_optimization: bool = False
    face_repair: dict[str, Any] | None = None
    video_vae: str = "auto"
    warnings: list[str] = field(default_factory=list)
    official_mode: str = "T2VA"


def resolve_video_vae(compiled: CompiledRequest, inventory: dict[str, bool]) -> str:
    """Resolve auto against the selected engine; explicit choices never fall back."""
    choice = compiled.video_vae
    if choice not in VIDEO_VAE_MODES:
        raise RequestError("不支援的影片解碼選項，請選擇自動、INT8 或 FP16。")
    # Older inventory producers only advertised the FP16 VAE under video_vae.
    fp16 = inventory.get("video_vae_fp16", inventory.get("video_vae", False))
    int8 = inventory.get("video_vae_int8", False)
    supported = inventory.get("video_vae_int8_supported", False)
    if choice == "int8" or (choice == "auto" and int8 and supported):
        if not supported:
            raise RequestError("INT8 影片解碼需要已驗證的 ComfyUI 0.37.0 以上版本。請更新運算引擎與配套依賴並重啟，或改選自動／FP16。")
        if not int8:
            raise RequestError("目前運算引擎找不到 INT8 影片 VAE。請執行模型更新，或改選自動／FP16。")
        return VIDEO_VAE_FILENAMES["int8"]
    if fp16:
        return VIDEO_VAE_FILENAMES["fp16"]
    if choice == "auto" and int8 and not supported:
        raise RequestError("目前只有 INT8 影片 VAE，但引擎版本尚未確認相容。請更新至 ComfyUI 0.37.0 以上及配套依賴，或補齊 FP16 影片 VAE。")
    raise RequestError("目前運算引擎找不到可用的影片 VAE。請補齊所選模型後重新整理，或改選自動。")


def validate_runtime_inventory(compiled: CompiledRequest, inventory: dict[str, bool]) -> None:
    """Fail before asset upload when the selected engine cannot run this recipe."""
    family = "ref2va" if compiled.mode in {"r2v", "replace", "popup_panel", "mg_animation"} else "fl2va"
    required_models = {
        family: "H3 Ref2VA 主模型" if family == "ref2va" else "H3 FL2VA 主模型",
        "text_encoder": "H3 文字編碼器",
        "audio_vae": "H3 聲音 VAE",
    }
    missing = [label for key, label in required_models.items() if not inventory.get(key)]
    if missing:
        raise RequestError("目前運算引擎找不到必要模型：" + "、".join(missing) + "。請在該引擎補齊模型後重新整理。")
    resolve_video_vae(compiled, inventory)
    if compiled.quality_mode != "native" and not inventory.get("h3_sigma_shift"):
        raise RequestError("所選 Turbo 模式需要 MiniMaxH3SigmaShift 節點，請更新目前運算引擎的 ComfyUI 核心並重啟。")
    if compiled.memory_optimization and not inventory.get("h3_memory_optimization"):
        raise RequestError("獨立省顯存需要 H3MemoryOptimization 節點。請先更新 H3-Optimizations 並重啟目前運算引擎，或關閉省顯存選項。")
    if compiled.quality_mode == "sparse_experimental" and not inventory.get("h3_optimizations"):
        raise RequestError("實驗性稀疏加速需要 H3-Optimizations 的省顯存及稀疏節點。請先執行模型更新，並重新啟動目前運算引擎。")
    if compiled.quality_mode == "turbo_sla" and not inventory.get("h3_sla_attention"):
        raise RequestError("Turbo-SLA 需要 ComfyUI 0.35.0 以上的 BlockSparseAttention（sla）節點。請更新目前運算引擎的核心與配套依賴並重啟，或改選一般 Turbo。")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _with_lora_triggers(prompt: str, loras: list[dict[str, Any]], family: str) -> str:
    # Required cues belong to the visual direction, never to speech or sign text.
    visual = visual_direction_text(prompt)
    specs = required_lora_trigger_specs(loras, family)
    marker = re.match(r"\[Shot 1\]\s*", prompt)
    body = prompt[marker.end():] if marker else prompt
    prefix_cues = [item["cue"] for item in specs if item["placement"] == "prefix"]
    leading = set()
    remaining = body
    if prefix_cues:
        pattern = re.compile(r"^\s*(" + "|".join(re.escape(cue) for cue in prefix_cues)
                             + r")(?:\s*[,;]\s*|\s+|$)", re.IGNORECASE)
        while match := pattern.match(remaining):
            leading.add(match.group(1).casefold())
            remaining = remaining[match.end():]
    missing = [cue for cue in prefix_cues if cue.casefold() not in leading]
    missing += [item["cue"] for item in specs if item["placement"] != "prefix"
                and not re.search(r"(?<!\w)" + re.escape(item["cue"]) + r"(?!\w)", visual, re.IGNORECASE)]
    if not missing:
        return prompt
    prefix = ", ".join(missing) + ", "
    if marker:
        return "[Shot 1] " + prefix + body
    return prefix + prompt


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_dimensions(aspect_ratio: str, megapixels: float) -> tuple[int, int]:
    if aspect_ratio not in ASPECT_RATIOS:
        raise RequestError("不支援的畫面比例。")
    megapixels = min(1.0, max(0.2, megapixels))
    landscape_presets = {
        0.4: (864, 480),
        0.7: (1152, 640),
        0.9: (1280, 736),
        0.98: (1344, 768),
    }
    preset = landscape_presets.get(round(megapixels, 2))
    if preset and aspect_ratio == "16:9":
        return preset
    if preset and aspect_ratio == "9:16":
        return preset[1], preset[0]
    left, right = (int(part) for part in aspect_ratio.split(":"))
    ratio = left / right
    pixels = megapixels * 1_000_000
    width = math.sqrt(pixels * ratio)
    height = pixels / width
    width = max(32, round(width / 32) * 32)
    height = max(32, round(height / 32) * 32)
    return width, height


def aligned_frame_count(seconds: float) -> int:
    seconds = min(15.0, max(5.0, seconds))
    frame_count = max(5, round(seconds * FPS))
    while frame_count % 17 != 5:
        frame_count += 1
    return frame_count


def _rewrite_aliases(text: str, aliases: dict[str, str]) -> str:
    return rewrite_aliases(text, aliases)


def _spoken_text(text: str, speakers: tuple[str, ...], speaker_ids: dict[str, str], *, dialogue_field: bool = False) -> str:
    try:
        return number_speakers(format_dialogue(text, speakers=speakers, dialogue_field=dialogue_field), speakers, speaker_ids)
    except ValueError as error:
        raise RequestError(str(error)) from error


def _motion_direction(payload: dict[str, Any]) -> str:
    profile = _clean_text(payload.get("motion_profile")) or "natural"
    profile_text = MOTION_PROFILES.get(profile, MOTION_PROFILES["natural"])
    if not profile_text:
        return ""
    intensity = min(5, max(1, _safe_int(payload.get("motion_intensity"), 3)))
    physics = _clean_text(payload.get("physics_style")) or "balanced"
    locked = payload.get("mode") in {"symbol_loop", "popup_panel"}
    camera = "stable" if locked else (_clean_text(payload.get("camera_response")) or "follow")
    return " ".join([
        profile_text,
        MOTION_INTENSITIES[intensity],
        PHYSICS_STYLES.get(physics, PHYSICS_STYLES["balanced"]),
        "The camera is completely locked; allow no camera movement, crop changes, scale drift, parallax, flicker, or lighting drift." if locked else CAMERA_RESPONSES.get(camera, CAMERA_RESPONSES["follow"]),
    ])


def _storyboard_text(
    storyboards: list[dict[str, Any]],
    aliases: dict[str, str],
    speakers: tuple[str, ...] = (),
    speaker_ids: dict[str, str] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    lines: list[str] = []
    image_assets: list[str] = []
    guides: list[dict[str, Any]] = []
    cursor = 0.0
    for index, shot in enumerate(storyboards, start=1):
        duration = min(15.0, max(0.5, _safe_float(shot.get("duration"), 2.0)))
        start = cursor
        end = cursor + duration
        cursor = end
        speaker_ids = speaker_ids if speaker_ids is not None else {}
        description = _rewrite_aliases(_spoken_text(_clean_text(shot.get("description")), speakers, speaker_ids), aliases)
        camera = _clean_text(shot.get("camera"))
        dialogue = _rewrite_aliases(_spoken_text(_clean_text(shot.get("dialogue")), speakers, speaker_ids, dialogue_field=True), aliases)
        sound = _clean_text(shot.get("sound"))
        motion_beats = _rewrite_aliases(_clean_text(shot.get("motion_beats")), aliases)
        effects = _rewrite_aliases(_clean_text(shot.get("effects")), aliases)
        image_id = _clean_text(shot.get("image_asset_id"))
        guide_mode = "exact" if shot.get("guide_mode") == "exact" else "reference"
        parts = ["[Shot 1]" if index == 1 else f"[Shot {index}] At {cut_time(start)},"]
        if image_id:
            if guide_mode == "exact":
                frame_idx = min(max(0, round(start * FPS)), 3599)
                guides.append({
                    "asset_id": image_id,
                    "frame_idx": frame_idx,
                    "shot_index": index,
                    "time": round(frame_idx / FPS, 3),
                })
                parts.append(
                    f"The exact storyboard guide anchors composition, subject positions, and viewpoint at {frame_idx / FPS:.3f} seconds (frame {frame_idx}); "
                    "motion must converge continuously into this frame and leave it naturally, without restarting the scene."
                )
            else:
                image_assets.append(image_id)
                parts.append("Use storyboard {STORYBOARD_TAG} as this shot's composition anchor for subject positions and viewpoint, without copying unrelated text or watermarks.")
        if description:
            parts.append(description)
        if camera:
            parts.append(f"Camera: {camera}")
        if dialogue:
            parts.append(f"Dialogue: {dialogue}")
        if sound:
            parts.append(f"Audio: {sound}")
        if motion_beats:
            parts.append(f"Motion beats: {motion_beats}")
        if effects:
            parts.append(f"Effects: {effects}")
        lines.append(" ".join(parts))
    return lines, image_assets, guides


def _mg_animation_description(payload: dict[str, Any], aliases: dict[str, str], duration: float) -> str:
    settings = payload.get("mg_animation") or {}
    position_labels = {
        "top": "upper area",
        "upper_left": "upper-left area",
        "upper_right": "upper-right area",
        "left": "left side",
        "center": "center",
        "right": "right side",
        "lower_left": "lower-left area",
        "lower_right": "lower-right area",
        "bottom": "lower area",
        "custom": "custom position",
    }
    reel_model_labels = {
        "continuous": "continuous full-strip reels",
        "independent": "independent single-cell reels",
        "cascade": "cascading or hybrid reel field",
    }
    direction_labels = {
        "top_down": "from top to bottom",
        "bottom_up": "from bottom to top",
        "custom": "in the custom direction described below",
    }
    stop_order_labels = {
        "left_right": "left to right",
        "right_left": "right to left",
        "simultaneous": "all reels at the same time",
        "custom": "in the custom order described below",
    }
    background_labels = {
        "locked": "remains completely static",
        "subtle": "uses only subtle low-amplitude environmental motion",
        "active": "performs the requested environmental animation without disturbing the reel geometry",
    }
    camera_labels = {
        "static": "The camera remains completely static.",
        "push_in": "The camera slowly pushes in with small amplitude.",
        "pull_out": "The camera slowly pulls out with small amplitude.",
        "pan": "The camera makes one gentle, low-amplitude pan while keeping the reel window readable.",
        "custom": "Follow the custom camera direction below.",
    }

    character = aliases.get("角色", "角色")
    reels = aliases.get("轉輪帶", "轉輪帶")
    background = aliases.get("背景圖", "背景圖")
    position_key = _clean_text(settings.get("character_position")) or "right"
    position = position_labels.get(position_key, position_labels["right"])
    position_detail = _clean_text(settings.get("character_position_detail"))
    character_motion = _rewrite_aliases(_clean_text(settings.get("character_motion")), aliases)
    reel_model = reel_model_labels.get(_clean_text(settings.get("reel_motion_model")) or "continuous", reel_model_labels["continuous"])
    reel_direction = direction_labels.get(_clean_text(settings.get("reel_direction")) or "top_down", direction_labels["top_down"])
    stop_order = stop_order_labels.get(_clean_text(settings.get("reel_stop_order")) or "left_right", stop_order_labels["left_right"])
    stagger = min(1.5, max(0.0, _safe_float(settings.get("reel_stop_stagger"), 0.18)))
    reel_motion = _rewrite_aliases(_clean_text(settings.get("reel_motion")), aliases)
    symbol_motion = _rewrite_aliases(_clean_text(settings.get("symbol_post_stop_motion")), aliases)
    background_level = background_labels.get(_clean_text(settings.get("background_motion_level")) or "subtle", background_labels["subtle"])
    background_motion = _rewrite_aliases(_clean_text(settings.get("background_motion")), aliases)
    camera_key = _clean_text(settings.get("camera_motion")) or "static"
    camera_motion = camera_labels.get(camera_key, camera_labels["static"])
    camera_detail = _clean_text(settings.get("camera_motion_detail"))

    opening_end = min(0.5, duration * 0.1)
    spin_end = duration * 0.7
    performance_end = duration * 0.9
    character_position = f"{position}; {position_detail}" if position_detail else position
    lines = [
        "[Shot 1] The target video is a polished slot-game main-game animation in one continuous shot.",
        f"{camera_motion}{(' ' + camera_detail) if camera_detail else ''}",
        f"From 0.00s to {opening_end:.2f}s, establish {background}, {reels}, and {character} as three visually separate layers. Keep the reel window fully readable and keep the character clear of reel symbols, titles, meters, and dynamic values.",
        f"Character layer — Place {character} in the {character_position}. {character_motion or 'The character performs one restrained anticipation, then reacts naturally to the reel result with clear weight transfer, readable expression, and a stable final pose.'}",
        f"Visible reel-window layer — Treat {reels} as {reel_model}. From {opening_end:.2f}s to {spin_end:.2f}s, the visible symbols travel {reel_direction}, accelerate smoothly, maintain equal cell size and center anchors, then decelerate and stop {stop_order} with about {stagger:.2f}s between stops. {reel_motion or 'Use readable motion blur during travel, a short mechanical settle on each stop, and no symbol identity changes while the reels are moving.'}",
        f"Post-stop symbol performance — From {spin_end:.2f}s to {performance_end:.2f}s, begin only after the relevant reel has fully settled. {symbol_motion or 'The winning symbols perform one clear primary action with one supporting glow response, then return to their exact cell centers without changing reel geometry.'}",
        f"Background layer — {background} {background_level}. {background_motion or 'Keep distant motion soft, low contrast, and subordinate to the reels and character.'}",
        f"From {performance_end:.2f}s to {duration:.2f}s, all layers settle into a clean readable final state; preserve the original layout, subject identities, text legibility, and exact reel-stop geometry.",
    ]
    return "\n".join(lines)


def _sound_sections(payload: dict[str, Any], body: str, embedded_sound: str, embedded_music: str, mode: str) -> tuple[str, str]:
    sound = _clean_text(payload.get("shortfilm_soundscape")) or _clean_text(payload.get("overall_soundscape")) or embedded_sound
    music = _clean_text(payload.get("shortfilm_music")) or _clean_text(payload.get("non_diegetic_music")) or embedded_music
    if not music:
        # Quick-generation users describe music in the narrative, without a separate
        # field. Carry explicit audience-only score instructions into its own section.
        directions = visual_direction_text(body)
        clauses = [part.strip() for part in re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n", directions) if part.strip()]
        music_clauses = [part for part in clauses if re.search(
            r"背景音[樂乐]|配[樂乐]|\b(?:background (?:music|score)|non[- ]diegetic music|audience[- ]only score|BGM)\b", part, re.I)]
        negative = re.compile(
            r"(?:無|无|不要|禁止|沒有|没有)(?:任何|加入)?(?:背景音[樂乐]|配[樂乐]|BGM)|"
            r"\b(?:no|without|zero)\s+(?:background (?:music|score)|non[- ]diegetic music|audience[- ]only score|BGM)\b", re.I)
        evolving = re.compile(r"直到|之後|之后|再|\b(?:until|then|later|after)\b", re.I)
        requested = [part for part in music_clauses if not negative.search(part) or evolving.search(part)]
        music = " ".join(requested[:3]) if requested else "N/A"
    if not sound:
        complete_silence = re.search(r"完全(?:靜音|静音|無聲|无声)|全程(?:靜音|静音|無聲|无声)|\b(?:complete silence|silent throughout|no sound at all)\b", visual_direction_text(body), re.I)
        sound = "N/A" if complete_silence else (
            "Synchronized reel-spin whirr, controlled deceleration, distinct stop clicks, and a restrained win chime after the symbols settle."
            if mode == "mg_animation" else "Natural ambience and synchronized physical action sounds appropriate to the scene."
        )
    return sound, music


def compile_request(payload: dict[str, Any]) -> CompiledRequest:
    video_vae = _clean_text(payload.get("video_vae")) or "auto"
    if video_vae not in VIDEO_VAE_MODES:
        raise RequestError("不支援的影片解碼選項，請選擇自動、INT8 或 FP16。")
    mode = _clean_text(payload.get("mode")) or "t2v"
    if mode not in {"t2v", "fl2va", "r2v", "replace", "extend", "symbol_loop", "popup_panel", "mg_animation"}:
        raise RequestError("不支援的生成模式。")

    aspect_ratio = _clean_text(payload.get("aspect_ratio")) or "16:9"
    megapixels = _safe_float(payload.get("megapixels"), 0.4)
    width, height = compute_dimensions(aspect_ratio, megapixels)
    try:
        custom_loras = normalize_loras(payload.get("custom_loras"))
    except ValueError as error:
        raise RequestError(str(error)) from error
    reserved_loras = {name for candidates in TURBO_LORA_CANDIDATES.values() for name in candidates}
    if any(item["name"].split("/")[-1] in reserved_loras for item in custom_loras if item["enabled"]):
        raise RequestError("內建 Turbo LoRA 請從生成品質選擇，避免重複載入或使用錯誤的取樣設定。")
    requested_duration = min(15.0, max(5.0, _safe_float(payload.get("duration"), 5.0)))
    length = aligned_frame_count(requested_duration)
    seed = _safe_int(payload.get("seed"), 1)
    if seed < 0:
        seed = 1
    steps = min(40, max(4, _safe_int(payload.get("steps"), 20)))
    reference_mode = mode in {"r2v", "replace", "popup_panel", "mg_animation"}
    scheduler = _clean_text(payload.get("scheduler")) or "simple"
    if scheduler not in {"simple", "beta", "normal", "sgm_uniform", "karras"}:
        scheduler = "simple"
    ref_image_size = "max" if payload.get("ref_image_size") == "max" else "match"
    quality_mode = _clean_text(payload.get("quality_mode")) or "native"
    if quality_mode not in QUALITY_MODES:
        quality_mode = "native"
    if reference_mode and quality_mode in FL_ONLY_QUALITY_MODES:
        raise RequestError("這個新版加速模式目前只支援文生、首尾、續接與圖騰循環；多模態參考請改用 Turbo 穩定或 Ref 8 步品質模式。")
    if not reference_mode and quality_mode == "turbo_ref_quality":
        raise RequestError("Ref 8 步品質模式只支援多模態參考、角色替換、彈窗面板與 MG 動畫；文生與首尾請改用 FL2VA 品質模式。")
    memory_optimization = payload.get("memory_optimization") is True
    if quality_mode == "turbo_sla" and memory_optimization:
        raise RequestError("SLA 已內建分塊 QKV，請關閉額外省顯存以避免節點衝突。")
    sampler_name = "res_multistep"
    turbo_profile = None
    turbo_lora = None
    turbo_lora_strength = 0.0
    shift_video = None
    shift_audio = None
    if quality_mode != "native":
        scheduler = "simple"
        sampler_name = "euler"
        turbo_lora_strength = 0.75
        shift_audio = 3.0
        if quality_mode == "turbo" and reference_mode:
            steps = 4
            ref_image_size = "match"
            turbo_profile = TURBO_PROFILE_REF_544
            # Ref2VA Turbo adapters already include alpha/rank scaling.
            # https://github.com/ModelTC/Minimax-H3-Turbo/blob/main/example_workflows/video_minimax_h3_ref2v_lightx2v_turbo.json
            turbo_lora_strength = 1.0
            shift_video = 12.0
        elif quality_mode == "turbo" and (width, height) == (1344, 768):
            steps = 4
            turbo_profile = TURBO_PROFILE_FL_768
            shift_video = 6.0
        elif quality_mode == "turbo":
            steps = 8
            turbo_profile = TURBO_PROFILE_FL_544
            shift_video = 12.0
        elif quality_mode in {"turbo_fast", "sparse_experimental"}:
            steps = 4
            turbo_profile = TURBO_PROFILE_FL_768_FAST_V11
            shift_video = 6.0
        elif quality_mode == "turbo_sla":
            steps = 4
            turbo_profile = TURBO_PROFILE_FL_768_SLA
            turbo_lora_strength = 1.0
            shift_video = 6.0
        elif quality_mode == "turbo_audio":
            steps = 4
            turbo_profile = TURBO_PROFILE_FL_768_AUDIO_V12
            shift_video = 6.0
        elif quality_mode == "turbo_ref_quality":
            steps = 8
            ref_image_size = "match"
            turbo_profile = TURBO_PROFILE_REF_768_QUALITY_V10
            turbo_lora_strength = 1.0
            shift_video = 12.0
        else:
            steps = 8
            turbo_profile = TURBO_PROFILE_FL_768_QUALITY_V10
            shift_video = 6.0
        turbo_lora = TURBO_LORA_CANDIDATES[turbo_profile][0]

    base_prompt = _clean_text(payload.get("prompt"))
    if not base_prompt:
        raise RequestError("請輸入影片敘述。")
    prompt_profile = _clean_text(payload.get("prompt_profile"))
    shortfilm_reference_continuity = prompt_profile == "shortfilm" and mode == "r2v"
    base_prompt, embedded_soundscape, embedded_music = unpack_prompt(base_prompt)
    warnings = [str(value) for value in (payload.get("h3_preparation_warnings") or [])]
    user_directions = "\n".join([base_prompt, *[
        _clean_text(item.get("description")) for item in (payload.get("references") or [])
    ], *[_clean_text(payload.get(key)) for key in ("summary", "reference_style", "shortfilm_summary", "shortfilm_soundscape", "shortfilm_music", "overall_soundscape", "non_diegetic_music", "replacement_target")], *[
        _clean_text(shot.get(key)) for shot in (payload.get("storyboards") or [])
        for key in ("description", "camera", "sound", "motion_beats", "effects")
    ], *[value for value in (payload.get("mg_animation") or {}).values() if isinstance(value, str)]])
    alias_names = {_clean_text(item.get("alias")): "" for item in (payload.get("references") or []) if _clean_text(item.get("alias"))}
    if language_warning(rewrite_aliases(user_directions, alias_names)) and not warnings:
        warnings.append("部分畫面描述仍為中文；官方 H3 格式建議使用英文描述，對白與畫面文字會保留原文。")

    first_image = _clean_text(payload.get("first_image_asset_id")) or None
    last_image = _clean_text(payload.get("last_image_asset_id")) or None
    if mode == "fl2va" and not first_image and not last_image:
        raise RequestError("首尾圖片模式至少需要一張圖片。")
    if mode == "extend" and not first_image:
        raise RequestError("續接影片模式需要先選擇上一段影片並擷取最後畫面。")
    if mode == "symbol_loop" and not first_image:
        raise RequestError("圖騰循環模式需要先上傳圖片並完成自動擴邊。")
    if mode == "symbol_loop":
        last_image = first_image
    continuation_source_job = _clean_text(payload.get("continuation_source_job_id")) or None
    continuation_source_asset = _clean_text(payload.get("continuation_source_asset_id")) or None
    continuation_merge = bool(payload.get("continuation_merge"))
    continuation_audio = _clean_text(payload.get("continuation_audio")) or "both"
    if continuation_audio not in {"both", "new", "mute"}:
        continuation_audio = "both"
    if mode == "extend" and continuation_merge and not (continuation_source_job or continuation_source_asset):
        raise RequestError("自動串接需要保留上一段影片來源。")

    references = payload.get("references") or []
    try:
        validate_lora_requirements(custom_loras, "ref2va" if reference_mode else "fl2va",
                                   has_source_video=reference_mode and any(
                                       _clean_text(item.get("video_asset_id")) or any(item.get("video_asset_ids") or [])
                                       for item in references),
                                   has_replacement_image=reference_mode and any(
                                       (_clean_text(item.get("type")) or "character") in {"character", "creature"}
                                       and any(item.get("image_asset_ids") or []) for item in references))
    except ValueError as error:
        raise RequestError(str(error)) from error
    if mode == "popup_panel":
        backgrounds = [item for item in references if _clean_text(item.get("alias")) == "背景圖"]
        panels = [item for item in references if _clean_text(item.get("alias")) == "面板"]
        if len(backgrounds) != 1 or len(panels) != 1:
            raise RequestError("彈窗面板動畫需要保留唯一的『背景圖』與『面板』素材欄位；其他素材可以自由新增。")
        background = backgrounds[0]
        panel = panels[0]
        if background.get("type") != "background" or panel.get("type") != "object":
            raise RequestError("『背景圖』必須是背景類型，『面板』必須是物件類型。")
        if len(background.get("image_asset_ids") or []) != 1:
            raise RequestError("彈窗面板動畫的『背景圖』需要剛好一張圖片，以便鎖定整段影片的底板。")
        if not (panel.get("image_asset_ids") or []):
            raise RequestError("彈窗面板動畫的『面板』需要至少一張圖片；同一欄位可以加入多張面板素材。")
        extra_references = [item for item in references if item is not background and item is not panel]
        references = [background, panel, *extra_references]
    if mode == "mg_animation":
        backgrounds = [item for item in references if _clean_text(item.get("alias")) == "背景圖"]
        reels = [item for item in references if _clean_text(item.get("alias")) == "轉輪帶"]
        characters = [item for item in references if _clean_text(item.get("alias")) == "角色"]
        if len(backgrounds) != 1 or len(reels) != 1 or len(characters) != 1:
            raise RequestError("MG 動畫需要保留唯一的『背景圖』、『轉輪帶』與『角色』素材欄位；其他素材可以自由新增。")
        background, reel, character = backgrounds[0], reels[0], characters[0]
        if background.get("type") != "background" or reel.get("type") != "object" or character.get("type") != "character":
            raise RequestError("MG 動畫的『背景圖』必須是背景類型、『轉輪帶』必須是物件類型、『角色』必須是角色類型。")
        if len(background.get("image_asset_ids") or []) != 1:
            raise RequestError("MG 動畫的『背景圖』需要剛好一張圖片，以便維持底板構圖。")
        if not (reel.get("image_asset_ids") or []):
            raise RequestError("MG 動畫的『轉輪帶』需要至少一張可見轉輪窗或轉輪帶參考圖片。")
        if not (character.get("image_asset_ids") or []):
            raise RequestError("MG 動畫的『角色』需要至少一張形象參考圖片。")
        extra_references = [
            item for item in references
            if item is not background and item is not reel and item is not character
        ]
        references = [background, reel, character, *extra_references]
    replacement_source_images = []
    if mode == "replace":
        if len(references) != 1:
            raise RequestError("角色替換模式只設定一個新角色；要辨識被替換的人物，請使用『原角色參考圖』欄位。")
        replacement = references[0]
        if not (replacement.get("image_asset_ids") or []):
            raise RequestError("角色替換模式需要至少一張新角色圖片。")
        if not (_clean_text(replacement.get("video_asset_id")) or replacement.get("video_asset_ids")):
            raise RequestError("角色替換模式需要一支原始表演影片。")
        replacement_source_images = payload.get("replacement_source_image_asset_ids", [])
        if not isinstance(replacement_source_images, list) or any(
            not isinstance(value, str) or not value.strip() for value in replacement_source_images
        ):
            raise RequestError("原角色參考圖必須是圖片素材編號清單。")
    speakers = tuple(
        _clean_text(item.get("alias")) for item in references
        if item.get("type") in {"character", "creature"}
    )
    speaker_ids: dict[str, str] = {}
    base_prompt = _spoken_text(base_prompt, speakers, speaker_ids)
    if mode != "mg_animation":
        base_prompt = _with_lora_triggers(base_prompt, custom_loras, "ref2va" if reference_mode else "fl2va")
    storyboards = payload.get("storyboards") or []
    storyboard_duration = sum(min(15.0, max(0.5, _safe_float(shot.get("duration"), 2.0))) for shot in storyboards)
    if storyboard_duration > (length / FPS) + 0.05:
        raise RequestError(f"分鏡合計 {storyboard_duration:.1f} 秒，超過影片實際時長 {length / FPS:.2f} 秒。")
    aliases_seen: set[str] = set()
    aliases: dict[str, str] = {}
    mapping: list[dict[str, Any]] = []
    reference_images: list[str] = []
    reference_videos: list[str] = []
    reference_video_use_audio: list[bool] = []
    reference_audios: list[str] = []
    guides: list[dict[str, Any]] = []
    definitions: list[str] = []
    assignments: list[str] = []
    motion_direction = _motion_direction(payload)

    if reference_mode:
        subject_index = 0
        picture_index = 0
        video_index = 0

        if mode == "r2v" and first_image:
            picture_index = 1
            reference_images.append(first_image)
            definitions.append("<Picture 1> is the first-frame composition anchor from the preceding video's final frame.")
            assignments.append("The shot begins from <Picture 1>, preserving subject positions, pose, composition, camera direction, lighting, and ongoing momentum before continuing the action; do not add another subject or freeze the whole video.")

        def item_video_ids(item: dict[str, Any]) -> list[str]:
            values = [str(value) for value in (item.get("video_asset_ids") or []) if value]
            single = _clean_text(item.get("video_asset_id"))
            if single and single not in values:
                values.append(single)
            return values

        def item_video_audio_flags(item: dict[str, Any]) -> list[bool]:
            values = item_video_ids(item)
            supplied = item.get("video_audio_flags")
            if isinstance(supplied, list) and len(supplied) == len(values):
                return [value is True for value in supplied]
            return [bool(item.get("video_use_audio"))] * len(values)

        video_soundtrack_count = sum(
            sum(item_video_audio_flags(item)) for item in references
        )
        standalone_audio_index = video_soundtrack_count
        video_audio_index = 0
        for item in references:
            alias = _clean_text(item.get("alias"))
            if not alias:
                raise RequestError("每個參考素材都需要名稱代號。")
            key = alias.casefold()
            if key in aliases_seen:
                raise RequestError(f"名稱代號「{alias}」重複。")
            aliases_seen.add(key)
            item_type = _clean_text(item.get("type")) or "character"
            image_ids = [str(value) for value in (item.get("image_asset_ids") or []) if value]
            video_ids = item_video_ids(item)
            video_audio_flags = item_video_audio_flags(item)
            audio_id = _clean_text(item.get("audio_asset_id")) or None
            if not image_ids and not video_ids and not audio_id:
                raise RequestError(f"參考素材「{alias}」至少需要圖片、影片或聲音其中一種。")
            description = _clean_text(item.get("description"))
            picture_tags: list[str] = []
            for image_id in image_ids:
                picture_index += 1
                reference_images.append(image_id)
                picture_tags.append(f"<Picture {picture_index}>")
            video_tags: list[str] = []
            video_audio_tags: list[str] = []
            for video_id, video_use_audio in zip(video_ids, video_audio_flags):
                video_index += 1
                reference_videos.append(video_id)
                reference_video_use_audio.append(video_use_audio)
                video_tag = f"<Video {video_index}>"
                video_tags.append(video_tag)
                if video_use_audio:
                    video_audio_index += 1
                    video_audio_tags.append(f"<Audio {video_audio_index}>")
            audio_tag = None
            if audio_id:
                standalone_audio_index += 1
                reference_audios.append(audio_id)
                audio_tag = f"<Audio {standalone_audio_index}>"

            subject_tag = None
            if item_type in REFERENCE_SUBJECT_TYPES and not (mode == "popup_panel" and alias == "背景圖"):
                subject_index += 1
                subject_tag = f"<Subject {subject_index}>"
                aliases[alias] = f"{subject_tag}（{alias}）"
                visual_sources = picture_tags + video_tags
                visual = ", ".join(visual_sources) if visual_sources else "the textual description"
                role = {"character": "character", "creature": "creature", "object": "object", "background": "scene and background", "style": "visual style", "motion": "action and pose", "effect": "visual effect"}[item_type]
                definitions.append(f'{subject_tag} is the {role} named "{alias}", defined by {visual}. {description}'.strip())
                if shortfilm_reference_continuity and item_type in {"character", "creature"}:
                    definitions[-1] += (
                        " Unless the story explicitly defines this alias as a group or distinct additional individuals, "
                        "it is one and the same physical individual across all views and cuts. "
                        "Multiple views or figures in a turnaround/reference sheet show the same individual from different angles, not extra cast members; "
                        "use appearance only, without reproducing the sheet layout, panels, labels, or multiple figures in the scene. "
                        "Explicit story directions for twins, duplication, shadows, or reflections take precedence."
                    )
                if picture_tags:
                    if item_type == "motion":
                        assignments.append(
                            f"{', '.join(picture_tags)} supply {subject_tag}'s action and pose reference at the described moments; "
                            "transfer only the specified pose, movement direction, and body mechanics to the acting subject, "
                            "without copying the reference person's identity, clothing, or background. Allow the described motion to develop continuously rather than freezing the reference pose."
                        )
                    else:
                        assignments.append(f"{', '.join(picture_tags)} supply only {subject_tag}'s defined appearance and fixed visual attributes.")
                for tag in video_tags:
                    if mode == "replace":
                        assignments.append(
                            f"{tag} is the source performance video: {subject_tag} replaces only the specified original character at their original positions, actions, poses, timing, and camera relationships. "
                            "Preserve the scene, lighting, props, and all other people, without retaining or blending the original person's identity, face, hair, clothing, or body."
                        )
                    else:
                        assignments.append(f"{tag} supplies {subject_tag}'s timing, body mechanics, pose transitions, and camera rhythm; do not copy unrelated people, clothing, or backgrounds unless explicitly requested.")
                if audio_tag:
                    voice_mode = _clean_text(item.get("voice_mode")) or "timbre"
                    if voice_mode == "reuse":
                        assignments.append(f"{audio_tag} is reused for {subject_tag}'s original audio content and performance; preserve its spoken words and language without substituting new dialogue.")
                    else:
                        assignments.append(f"{audio_tag} supplies only {subject_tag}'s voice timbre and delivery; generate the requested new dialogue without copying the source words.")
            else:
                source_tags = picture_tags + video_tags
                first_tag = source_tags[0] if source_tags else alias
                aliases[alias] = f"{first_tag}（{alias}）"
                role_name = {"background": "scene and background", "style": "visual style", "motion": "motion and camera rhythm", "effect": "effect timing and appearance"}.get(item_type, "reference content")
                if picture_tags:
                    definitions.append(f'{", ".join(picture_tags)} define the {role_name} composition anchor named "{alias}". {description}'.strip())
                for tag in video_tags:
                    if item_type == "effect":
                        assignments.append(f'{tag} supplies "{alias}" effect timing, energy paths, and impact response while preserving the main subjects and scene. {description}'.strip())
                    else:
                        assignments.append(f'{tag} supplies "{alias}" {role_name}; transfer only the specified temporal and camera information. {description}'.strip())
                if audio_tag:
                    assignments.append(f'{audio_tag} is the ambience or effect-sound reference for "{alias}".')
            enabled_video_tags = [tag for tag, enabled in zip(video_tags, video_audio_flags) if enabled]
            for tag, video_audio_tag in zip(enabled_video_tags, video_audio_tags):
                definitions.append(f"{video_audio_tag} is the enabled synchronized audio track from {tag}.")
                assignments.append(f"Reference {video_audio_tag} for synchronized action, impact, and effect sounds at the corresponding visual events.")
            for tag in video_tags:
                definitions.append(f"{tag} is {'the source video being edited' if mode == 'replace' else 'the temporal and camera-structure reference'} for {subject_tag or alias}.")
            if audio_tag:
                reuse = item.get("voice_mode") == "reuse"
                vocal_id = f" ({speaker_ids[alias]})" if alias in speaker_ids else ""
                definitions.append(f"{audio_tag} is {'the source audio to reuse' if reuse else 'the audio reference'} for {subject_tag or alias}{vocal_id}.")
            mapping.append({
                "alias": alias,
                "type": item_type,
                "subject_tag": subject_tag,
                "picture_tags": picture_tags,
                "video_tags": video_tags,
                "video_audio_tags": video_audio_tags,
                "audio_tag": audio_tag,
                "voice_mode": "reuse" if item.get("voice_mode") == "reuse" else "timbre",
            })

        if replacement_source_images:
            source_tags = []
            for image_id in replacement_source_images:
                picture_index += 1
                reference_images.append(image_id)
                source_tags.append(f"<Picture {picture_index}>")
            target = _clean_text(payload.get("replacement_target")) or "the specified original character"
            assignments.append(
                f"{', '.join(source_tags)} are identification-only references for the original character "
                f"to replace in <Video 1>: {target}. Locate that person using these images and the target description. "
                "They are not appearance references for <Subject 1>, not output keyframes, and not additional cast members. "
                "Replace only the identified person with <Subject 1>, whose appearance comes exclusively from the new-character pictures. "
                "Never blend the original face, hair, clothes, or body with the replacement. Preserve all other people; "
                "do not add <Subject 1> to frames where the identified original character is absent."
            )
            mapping.append({
                "alias": "原角色辨識", "type": "replacement_source", "subject_tag": None,
                "picture_tags": source_tags, "video_tags": [], "video_audio_tags": [], "audio_tag": None,
            })

        shot_lines, storyboard_images, storyboard_guides = _storyboard_text(storyboards, aliases, speakers, speaker_ids)
        guides.extend(storyboard_guides)
        for shot_index, image_id in enumerate(storyboard_images, start=1):
            picture_index += 1
            reference_images.append(image_id)
            tag = f"<Picture {picture_index}>"
            marker = "{STORYBOARD_TAG}"
            for line_index, line in enumerate(shot_lines):
                if marker in line:
                    shot_lines[line_index] = line.replace(marker, tag, 1)
                    break
            definitions.append(f"{tag} is storyboard composition anchor {shot_index}, defining relative positions, viewpoint, and scene arrangement for its described shot.")

        if len(reference_images) > MAX_REFERENCE_IMAGES:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_IMAGES} 張圖片，目前為 {len(reference_images)} 張。")
        if len(reference_audios) > MAX_REFERENCE_AUDIOS:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_AUDIOS} 段獨立聲音，目前為 {len(reference_audios)} 段。")
        if len(reference_videos) > MAX_REFERENCE_VIDEOS:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_VIDEOS} 支影片，目前為 {len(reference_videos)} 支。")
        if not reference_images and not reference_videos and not reference_audios:
            raise RequestError("多模態模式至少需要圖片、影片或聲音其中一種。")

        rewritten_prompt = _rewrite_aliases(base_prompt, aliases)
        if mode == "mg_animation":
            timeline = _mg_animation_description(payload, aliases, length / FPS)
            rewritten_prompt = timeline + "\nAdditional user direction: " + re.sub(r"^\s*\[Shot 1\]\s*", "", rewritten_prompt)
        try:
            narrative, final_shot = combine_storyboards(rewritten_prompt, shot_lines, length / FPS)
        except ValueError as error:
            raise RequestError(str(error)) from error
        retention: list[str] = []
        if mode == "r2v" and first_image:
            retention.append("<Picture 1>: fully_preserved in the opening frame as the preceding shot's exact continuity anchor; preserve its pose, composition, camera direction, lighting, and momentum.")
        for item in mapping:
            alias = item["alias"]
            subject_tag = item.get("subject_tag")
            if item["type"] == "replacement_source":
                for tag in item["picture_tags"]:
                    definitions.append(f"{tag} identifies the original person to replace in <Video 1>; it is not a target appearance or keyframe.")
                    retention.append(f"{tag}: weak_reference only to identify the original person to replace in <Video 1>; do not transfer appearance to the output.")
            elif subject_tag:
                if item["type"] == "object" and shortfilm_reference_continuity:
                    preservation = ("keep its explicitly stated quantity, left/right pairing, appearance, positions, and state consistent across cuts until an explicit action changes them; a different view does not create an additional object or set.")
                elif item["type"] in {"character", "creature"}:
                    preservation = ("keep the same character identity and distinguishing features; preserve face, body proportions, clothing, and colors except for changes explicitly described in the action. "
                                    "An explicitly described transformation, proportion change, or state change takes precedence over the corresponding fixed appearance attribute, without creating a different individual.")
                    if shortfilm_reference_continuity:
                        preservation += " Retain the same physical individual across views and cuts; reference images and turnaround-sheet views supply appearance only, not extra people, bodies, panels, or labels. Explicit story directions for twins, duplication, shadows, or reflections take precedence."
                elif item["type"] == "object":
                    preservation = "preserve appearance, dimensions, material, positions, orientation, and state unless the described action changes them."
                elif item["type"] == "motion":
                    preservation = ("preserve only the referenced action and pose at the described moments, while allowing continuous movement before and after them; "
                                    "do not transfer the reference person's identity, appearance, clothing, or background to the acting subject.")
                else:
                    preservation = "preserve the defined visual role, features, and reference characteristics where the action uses them."
                retention.append(f"{subject_tag}: fully_preserved whenever {alias} appears; " + preservation)
            else:
                for tag in item["picture_tags"]:
                    retention.append(f"{tag}: fully_preserved as the composition anchor for {alias}, including layout, palette, lighting, and spatial identity.")
            for tag in item["video_tags"]:
                marker = "partially_preserved" if mode == "replace" else "attribute_transfer"
                retention.append(f"{tag}: {marker} for {alias}; {'preserve source actions, timing, camera, environment, and all non-target subjects while replacing only the target identity' if mode == 'replace' else 'transfer timing, motion mechanics, and camera rhythm without copying unrelated identities'}.")
            for tag in item["video_audio_tags"]:
                retention.append(f"{tag}: reference for synchronized physical action and effect sounds from the enabled source-video soundtrack.")
            if item.get("audio_tag"):
                tag = item["audio_tag"]
                if item.get("voice_mode") == "reuse":
                    retention.append(f"{tag}: partially_copy; reuse the original audio content and performance at the matching target phase, preserving spoken words and language alongside the target scene's other sounds.")
                else:
                    retention.append(f"{tag}: reference for {alias}; use timbre, delivery, or sound texture without copying unrelated words.")
        storyboard_tags = [f"<Picture {picture_index - len(storyboard_images) + index}>" for index in range(1, len(storyboard_images) + 1)]
        retention.extend(f"{tag}: weak_reference as the described shot's composition and viewpoint planning anchor." for tag in storyboard_tags)
        detail_parts = [narrative]
        if assignments:
            detail_parts.append("Reference behavior in the corresponding described shots: " + " ".join(assignments))
        if motion_direction and prompt_profile != "shortfilm" and mode != "mg_animation":
            detail_parts.append(motion_direction)
        if mode == "replace":
            detail_parts.append("Replace only the identified original person with <Subject 1>; never show both identities or blend their faces, hair, clothing, or bodies. Preserve other people and do not add <Subject 1> in source frames where the target person is absent.")
            batch_segment = payload.get("replacement_batch_segment") or {}
            if batch_segment:
                segment_index = max(1, _safe_int(batch_segment.get("index"), 1))
                segment_total = max(segment_index, _safe_int(batch_segment.get("total"), segment_index))
                source_start = max(0.0, _safe_float(batch_segment.get("source_start"), 0.0))
                source_end = max(source_start, _safe_float(batch_segment.get("source_end"), length / FPS))
                detail_parts.append(f"This is replacement segment {segment_index} of {segment_total}, corresponding to source time {source_start:.3f}s–{source_end:.3f}s. Preserve exact source timing, camera, spatial layout, lighting, and non-target subjects across adjacent segments without restarting or resetting pose. Additional appearance pictures from the preceding generated segment control identity only; they are not a keyframe and must not override <Video 1>.")
        elif mode == "popup_panel":
            detail_parts.append("Keep <Picture 1> fixed from the opening to the ending frame: prohibit camera or background movement, pan, zoom, deformation, flicker, brightness drift, depth-of-field changes, parallax, and new background objects. Animate only named foreground panels, text, scores, buttons, decorations, and effects. After the panel exits, end with only the unchanged <Picture 1> background.")
        elif mode == "mg_animation":
            detail_parts.append("Keep the background, visible reel window, and character as independent layers. Preserve reel cell size, pitch, center anchors, masks, and symbol identities; never infer or alter mathematical reel-strip order or symbol frequency. Preserve titles, payout values, JP meters, and surrounding UI.")
        style = _clean_text(payload.get("reference_style")) or "The target video follows the visual style, composition, and lighting established by its reference definitions and user direction."
        detailed = style + "\n" + _with_lora_triggers("\n\n".join(detail_parts), custom_loras, "ref2va")
        task_types = []
        if (mode == "r2v" and first_image) or storyboard_guides:
            task_types.append("keyframe completion")
        if mode == "replace":
            task_types.append("video editing")
        task_types.append("reference generation")
        if any(item.get("audio_tag") and item.get("voice_mode") == "reuse" for item in mapping):
            task_types.append("audio reuse")
        if any(item.get("video_audio_tags") or (item.get("audio_tag") and item.get("voice_mode") != "reuse") for item in mapping):
            task_types.append("audio reference")
        summary = _clean_text(payload.get("shortfilm_summary")) or _clean_text(payload.get("summary")) or "Generate the described audiovisual timeline using the defined references at their stated roles."
        if mode == "replace":
            summary = "The target video is an edited version of <Video 1>. " + summary
        soundscape, music = _sound_sections(payload, base_prompt, embedded_soundscape, embedded_music, mode)
        final_prompt = "\n\n".join([
            "subject_definitions:\n" + ("\n".join(definitions) or "N/A"),
            "summary:\n[" + " + ".join(task_types) + "] " + summary,
            "retention_analysis:\n" + ("\n".join(retention) or "N/A"),
            "detailed_description:\n" + detailed,
            "overall_soundscape:\n" + soundscape,
            "non_diegetic_music:\n" + music,
        ])
        official_mode = "Ref2VA"
    else:
        shot_lines, storyboard_images, storyboard_guides = _storyboard_text(storyboards, {}, speakers, speaker_ids)
        guides.extend(storyboard_guides)
        if storyboard_images:
            raise RequestError("中間分鏡圖片需要使用多模態參考模式；此模式仍可使用純文字分鏡。")
        try:
            narrative, final_shot = combine_storyboards(base_prompt, shot_lines, length / FPS)
        except ValueError as error:
            raise RequestError(str(error)) from error
        if mode == "extend":
            narrative += "\nBegin immediately from the preceding video's final-frame pose, preserving positions, body momentum, gaze, camera direction, lighting, and environmental state; continue without restarting or holding still."
        elif mode == "symbol_loop":
            if final_shot != 1:
                raise RequestError("圖騰循環需要單一連續鏡頭，請移除切鏡。")
            narrative += "\nUse one continuous action cycle. Lock canvas, center anchor, symbol scale, silhouette, margins, camera, crop, background, lighting, and material appearance. Permit only the named foreground motion; progressively return pose and effects to the opening state at the final frame without an extra pause at the seam."
        if motion_direction and prompt_profile != "shortfilm":
            narrative += "\n" + motion_direction
        soundscape, music = _sound_sections(payload, base_prompt, embedded_soundscape, embedded_music, mode)
        final_prompt, official_mode = format_base_prompt(narrative, soundscape, music, first=bool(first_image), last=bool(last_image), duration=length / FPS, final_shot=final_shot)

    return CompiledRequest(
        mode=mode,
        prompt=final_prompt,
        width=width,
        height=height,
        length=length,
        custom_loras=custom_loras,
        memory_optimization=memory_optimization,
        video_vae=video_vae,
        requested_duration=requested_duration,
        actual_duration=round(length / FPS, 3),
        seed=seed,
        steps=steps,
        scheduler=scheduler,
        ref_image_size=ref_image_size,
        quality_mode=quality_mode,
        sampler_name=sampler_name,
        turbo_profile=turbo_profile,
        turbo_lora=turbo_lora,
        turbo_lora_strength=turbo_lora_strength,
        shift_video=shift_video,
        shift_audio=shift_audio,
        first_image=first_image,
        last_image=last_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_video_use_audio=reference_video_use_audio,
        reference_audios=reference_audios,
        guides=guides,
        continuation_source_job=continuation_source_job,
        continuation_source_asset=continuation_source_asset,
        continuation_merge=continuation_merge,
        continuation_audio=continuation_audio,
        mapping=mapping,
        warnings=list(dict.fromkeys(warnings)),
        official_mode=official_mode,
    )


def build_workflow(
    compiled: CompiledRequest,
    uploaded_assets: dict[str, str],
    output_stem: str,
    turbo_lora_name: str | None = None,
    custom_lora_names: dict[str, str] | None = None,
    video_vae_name: str | None = None,
) -> dict[str, Any]:
    if compiled.video_vae not in VIDEO_VAE_MODES:
        raise RequestError("不支援的影片解碼選項。")
    selected_vae = video_vae_name or VIDEO_VAE_FILENAMES.get(compiled.video_vae, VIDEO_VAE_FILENAMES["int8"])
    if selected_vae not in VIDEO_VAE_FILENAMES.values():
        raise RequestError("不支援的影片 VAE 模型。")
    if compiled.video_vae != "auto" and selected_vae != VIDEO_VAE_FILENAMES[compiled.video_vae]:
        raise RequestError("影片 VAE 模型與指定解碼選項不一致。")
    workflow: dict[str, Any] = {}
    next_id = 1

    def add(class_type: str, inputs: dict[str, Any], title: str) -> str:
        nonlocal next_id
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}
        return node_id

    diffusion_model = (
        "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
        if compiled.mode in {"r2v", "replace", "popup_panel", "mg_animation"}
        else "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    )
    model = add("UNETLoader", {"unet_name": diffusion_model, "weight_dtype": "default"}, "MiniMax H3 Model")
    if compiled.quality_mode != "native":
        lora_name = turbo_lora_name or compiled.turbo_lora
        if not lora_name or compiled.shift_video is None or compiled.shift_audio is None:
            raise RequestError("Turbo 工作流缺少相容的 LoRA 或 Sigma Shift 設定。")
        model = add("LoraLoaderModelOnly", {
            "model": [model, 0],
            "lora_name": lora_name,
            "strength_model": compiled.turbo_lora_strength,
        }, "H3 Turbo LoRA")
        model = add("MiniMaxH3SigmaShift", {
            "model": [model, 0],
            "shift_video": compiled.shift_video,
            "shift_audio": compiled.shift_audio,
        }, "H3 Turbo Sigma Shift")
    if compiled.quality_mode == "turbo_sla" and compiled.memory_optimization:
        raise RequestError("SLA 已內建分塊 QKV，請關閉額外省顯存以避免節點衝突。")
    if compiled.memory_optimization or compiled.quality_mode == "sparse_experimental":
        model = add("H3MemoryOptimization", {
            "model": [model, 0],
            "fused_qkv": "auto",
            "mlp_memory": "auto",
            "chunk_rows": 4096,
            "preserve_precision": True,
            "precision_mode": "Auto",
            "qkv_streaming_mode": "Auto",
            "embedding_memory_mode": "Auto",
            "kitchen_v_memory_mode": "Standard",
        }, "H3 Memory Optimization")
    if compiled.quality_mode == "sparse_experimental":
        model = add("H3SparseAttention", {
            "model": [model, 0],
            "video_budget": 0.30,
            "denser_early_late_steps": True,
        }, "H3 Sparse Attention · Experimental 30%")
    elif compiled.quality_mode == "turbo_sla":
        # ComfyUI 0.35 supplies its own H3 chunked QKV producer. H3-Optimizations
        # currently replaces block.forward without the required attention kwarg,
        # so neither its memory patch nor H3SparseAttention may be stacked here.
        model = add("BlockSparseAttention", {
            "model": [model, 0],
            "selection": "sla",
            "selection.keep_percent": 15.0,
            "start_percent": 0.0,
            "end_percent": 1.0,
            "dense_blocks": "",
            "min_tokens": 0,
            "extra_tokens": 0,
            "sink_conditioning": "exact_kv_and_rows",
            "verbose": True,
        }, "H3 Turbo-SLA · Experimental 15%")
    family = "ref2va" if compiled.mode in {"r2v", "replace", "popup_panel", "mg_animation"} else "fl2va"
    for item in active_loras(compiled.custom_loras, family):
        model = add("LoraLoaderModelOnly", {
            "model": [model, 0], "lora_name": (custom_lora_names or {}).get(item["name"], item["name"]), "strength_model": item["strength"],
        }, "自訂 LoRA · " + item["name"])
    clip = add("CLIPLoader", {
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "type": "minimax",
        "device": "default",
    }, "MiniMax H3 Text Encoder")
    video_vae = add("VAELoader", {"vae_name": selected_vae}, "Video VAE")
    audio_vae = add("VAELoader", {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}, "Audio VAE")

    image_nodes: dict[str, str] = {}
    audio_nodes: dict[str, str] = {}
    video_nodes: dict[str, str] = {}

    def image_node(asset_id: str) -> str:
        if asset_id not in image_nodes:
            image_nodes[asset_id] = add("LoadImage", {"image": uploaded_assets[asset_id]}, "Reference Image")
        return image_nodes[asset_id]

    def audio_node(asset_id: str) -> str:
        if asset_id not in audio_nodes:
            audio_nodes[asset_id] = add("LoadAudio", {"audio": uploaded_assets[asset_id]}, "Reference Audio")
        return audio_nodes[asset_id]

    def video_components_node(asset_id: str) -> str:
        if asset_id not in video_nodes:
            loaded = add("LoadVideo", {"file": uploaded_assets[asset_id]}, "Reference Motion Video")
            video_nodes[asset_id] = add("GetVideoComponents", {"video": [loaded, 0]}, "Reference Video Components")
        return video_nodes[asset_id]

    condition_inputs: dict[str, Any] = {
        "clip": [clip, 0],
        "vae": [video_vae, 0],
        "prompt": compiled.prompt,
        "width": compiled.width,
        "height": compiled.height,
        "length": compiled.length,
    }
    if compiled.mode in {"r2v", "replace", "popup_panel", "mg_animation"}:
        condition_inputs["audio_vae"] = [audio_vae, 0]
        condition_inputs["ref_image_size"] = compiled.ref_image_size
        for index, asset_id in enumerate(compiled.reference_images):
            condition_inputs[f"ref_images.ref_image_{index}"] = [image_node(asset_id), 0]
        for index, asset_id in enumerate(compiled.reference_videos):
            components = video_components_node(asset_id)
            condition_inputs[f"ref_videos.ref_video_{index}"] = [components, 0]
            if compiled.reference_video_use_audio[index]:
                condition_inputs[f"ref_video_audios.ref_video_audio_{index}"] = [components, 1]
        for index, asset_id in enumerate(compiled.reference_audios):
            condition_inputs[f"ref_audios.ref_audio_{index}"] = [audio_node(asset_id), 0]
        conditioning = add("MiniMaxH3ReferenceToVideo", condition_inputs, "MiniMax H3 Reference to Video")
    else:
        if compiled.first_image:
            condition_inputs["first_frame"] = [image_node(compiled.first_image), 0]
        if compiled.last_image:
            condition_inputs["last_frame"] = [image_node(compiled.last_image), 0]
        conditioning = add("MiniMaxH3ImageToVideo", condition_inputs, "MiniMax H3 Image to Video")

    latent_source = conditioning
    positive_source = conditioning
    for guide in compiled.guides:
        positive_source = add("MiniMaxH3AddGuide", {
            "positive": [positive_source, 0],
            "vae": [video_vae, 0],
            "latent": [latent_source, 1],
            "image": [image_node(guide["asset_id"]), 0],
            "frame_idx": guide["frame_idx"],
        }, f"Exact Storyboard Guide · Shot {guide['shot_index']}")

    noise = add("RandomNoise", {"noise_seed": compiled.seed}, "Seed")
    sampler = add("KSamplerSelect", {"sampler_name": compiled.sampler_name}, "Sampler")
    sigmas = add("BasicScheduler", {
        "model": [model, 0],
        "scheduler": compiled.scheduler,
        "steps": compiled.steps,
        "denoise": 1.0,
    }, "Scheduler")
    guider = add("BasicGuider", {"model": [model, 0], "conditioning": [positive_source, 0]}, "Guider")
    sampled = add("SamplerCustomAdvanced", {
        "noise": [noise, 0],
        "guider": [guider, 0],
        "sampler": [sampler, 0],
        "sigmas": [sigmas, 0],
        "latent_image": [latent_source, 1],
    }, "Generate")
    decoded_video = add("VAEDecode", {"samples": [sampled, 0], "vae": [video_vae, 0]}, "Decode Video")
    decoded_audio = add("VAEDecodeAudio", {"samples": [sampled, 0], "vae": [audio_vae, 0]}, "Decode Audio")
    video = add("CreateVideo", {
        "images": [decoded_video, 0],
        "audio": [decoded_audio, 0],
        "fps": FPS,
        "bit_depth": 8,
    }, "Create MP4")
    add("SaveVideo", {
        "video": [video, 0],
        "filename_prefix": f"H3Studio/{output_stem}",
        "format": "auto",
        "codec": "auto",
    }, "Save Video")
    if compiled.face_repair:
        from face_repair import wire_face_repair
        return wire_face_repair(workflow, compiled, uploaded_assets)
    return workflow


def required_asset_ids(compiled: CompiledRequest) -> list[str]:
    values = [
        compiled.first_image,
        compiled.last_image,
        *compiled.reference_images,
        *compiled.reference_videos,
        *compiled.reference_audios,
        *(guide["asset_id"] for guide in compiled.guides),
        (compiled.face_repair or {}).get("source_asset_id"),
    ]
    return list(dict.fromkeys(value for value in values if value))
