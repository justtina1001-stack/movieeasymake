from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


FPS = 24
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_AUDIOS = 3
MAX_REFERENCE_VIDEOS = 3
ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
REFERENCE_SUBJECT_TYPES = {"character", "creature", "object"}

TURBO_PROFILE_FL_544 = "fl2v_544"
TURBO_PROFILE_FL_768 = "fl2v_768"
TURBO_PROFILE_REF_544 = "ref2v_544"
TURBO_LORA_CANDIDATES: dict[str, tuple[str, ...]] = {
    TURBO_PROFILE_FL_544: (
        "minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_FL_768: (
        "minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    ),
    TURBO_PROFILE_REF_544: (
        "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    ),
}

MOTION_PROFILES = {
    "natural": "以自然、連續的人體與物體力學呈現動作；重心轉移、慣性、接觸、反作用力和收勢都要清楚，避免漂浮、瞬移或肢體抽動。",
    "impact": "動作要有清楚的蓄力、加速、接觸、受力、回彈與穩定階段；打擊瞬間強調重量、速度差、身體連鎖反應與短促鏡頭回饋。",
    "action": "安排連續且可讀的全身動作，讓起步、轉向、跨步、攻防與停頓之間有合理過渡，保持角色輪廓和空間方向一致。",
    "dance": "動作遵循節拍，保持軀幹、四肢、重心與地面接觸協調；轉身和姿態切換流暢，避免腳步滑動。",
    "chase": "強調速度、慣性、地形互動與前後景視差；角色奔跑週期自然，鏡頭追隨時保持主體可讀且空間連續。",
    "vfx": "特效依照啟動、增長、爆發、消散的時序發展；能量軌跡與角色動作、接觸點和環境反應同步，不遮蔽主要肢體輪廓。",
    "none": "",
}

MOTION_INTENSITIES = {
    1: "動態幅度克制，以細微且可信的動作為主。",
    2: "動態偏穩定，保留適量動作變化。",
    3: "動態強度中等，兼顧自然度與畫面表現。",
    4: "動態明顯，增加速度差、姿態變化與鏡頭反應。",
    5: "動態非常強烈，但仍須維持解剖、物理與時間連續性。",
}

PHYSICS_STYLES = {
    "realistic": "物理風格偏寫實：重量、碰撞、布料、頭髮和次級動態符合真實慣性。",
    "balanced": "物理風格平衡：自然力學為基礎，允許適度電影化誇張。",
    "stylized": "物理風格偏風格化：可誇張速度與姿勢，但動作因果、接觸點和重心必須連續。",
}

CAMERA_RESPONSES = {
    "stable": "攝影機保持穩定，運鏡平滑，不使用無目的晃動。",
    "follow": "攝影機平滑跟隨主體，以合理加減速維持構圖與視線方向。",
    "handheld": "使用克制的手持感，晃動幅度小且有重量，不造成主體漂移。",
    "impact": "只在碰撞或爆發瞬間加入短促震動與回穩，避免全程抖動。",
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


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


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
    for alias in sorted(aliases, key=len, reverse=True):
        if alias:
            text = text.replace(alias, aliases[alias])
    return text


def _motion_direction(payload: dict[str, Any]) -> str:
    profile = _clean_text(payload.get("motion_profile")) or "natural"
    profile_text = MOTION_PROFILES.get(profile, MOTION_PROFILES["natural"])
    if not profile_text:
        return ""
    intensity = min(5, max(1, _safe_int(payload.get("motion_intensity"), 3)))
    physics = _clean_text(payload.get("physics_style")) or "balanced"
    camera = _clean_text(payload.get("camera_response")) or "follow"
    return " ".join([
        profile_text,
        MOTION_INTENSITIES[intensity],
        PHYSICS_STYLES.get(physics, PHYSICS_STYLES["balanced"]),
        CAMERA_RESPONSES.get(camera, CAMERA_RESPONSES["follow"]),
    ])


def _storyboard_text(
    storyboards: list[dict[str, Any]],
    aliases: dict[str, str],
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
        description = _rewrite_aliases(_clean_text(shot.get("description")), aliases)
        camera = _clean_text(shot.get("camera"))
        dialogue = _rewrite_aliases(_clean_text(shot.get("dialogue")), aliases)
        sound = _clean_text(shot.get("sound"))
        motion_beats = _rewrite_aliases(_clean_text(shot.get("motion_beats")), aliases)
        effects = _rewrite_aliases(_clean_text(shot.get("effects")), aliases)
        image_id = _clean_text(shot.get("image_asset_id"))
        guide_mode = "exact" if shot.get("guide_mode") == "exact" else "reference"
        parts = [f"[Shot {index}, {start:.1f}s-{end:.1f}s]"]
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
                    f"精確分鏡 Guide 會在 {frame_idx / FPS:.3f} 秒（第 {frame_idx} 幀）鎖定這張圖的構圖、人物位置與鏡位；"
                    "前後動作必須連續收斂並自然離開該畫面，不要把它當成切鏡或重新開場。"
                )
            else:
                image_assets.append(image_id)
                parts.append("使用分鏡參考圖 {STORYBOARD_TAG} 的構圖、人物位置與鏡位，不要複製其中不相關的文字或浮水印。")
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


def compile_request(payload: dict[str, Any]) -> CompiledRequest:
    mode = _clean_text(payload.get("mode")) or "t2v"
    if mode not in {"t2v", "fl2va", "r2v", "replace", "extend", "symbol_loop", "popup_panel", "mg_animation"}:
        raise RequestError("不支援的生成模式。")

    aspect_ratio = _clean_text(payload.get("aspect_ratio")) or "16:9"
    megapixels = _safe_float(payload.get("megapixels"), 0.4)
    width, height = compute_dimensions(aspect_ratio, megapixels)
    requested_duration = min(15.0, max(5.0, _safe_float(payload.get("duration"), 5.0)))
    length = aligned_frame_count(requested_duration)
    seed = _safe_int(payload.get("seed"), 1)
    if seed < 0:
        seed = 1
    steps = min(40, max(4, _safe_int(payload.get("steps"), 20)))
    reference_mode = mode in {"r2v", "replace", "popup_panel", "mg_animation"}
    scheduler = _clean_text(payload.get("scheduler")) or ("beta" if reference_mode else "simple")
    if scheduler not in {"simple", "beta", "normal", "sgm_uniform", "karras"}:
        scheduler = "beta" if reference_mode else "simple"
    ref_image_size = "max" if payload.get("ref_image_size") == "max" else "match"
    quality_mode = "turbo" if payload.get("quality_mode") == "turbo" else "native"
    sampler_name = "res_multistep"
    turbo_profile = None
    turbo_lora = None
    turbo_lora_strength = 0.0
    shift_video = None
    shift_audio = None
    if quality_mode == "turbo":
        scheduler = "simple"
        sampler_name = "euler"
        turbo_lora_strength = 0.75
        shift_audio = 3.0
        if reference_mode:
            steps = 4
            ref_image_size = "match"
            turbo_profile = TURBO_PROFILE_REF_544
            shift_video = 12.0
        elif (width, height) == (1344, 768):
            steps = 4
            turbo_profile = TURBO_PROFILE_FL_768
            shift_video = 6.0
        else:
            steps = 8
            turbo_profile = TURBO_PROFILE_FL_544
            shift_video = 12.0
        turbo_lora = TURBO_LORA_CANDIDATES[turbo_profile][0]

    base_prompt = _clean_text(payload.get("prompt"))
    if not base_prompt:
        raise RequestError("請輸入影片敘述。")
    prompt_profile = _clean_text(payload.get("prompt_profile"))

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
    if mode == "replace":
        if len(references) != 1:
            raise RequestError("角色替換模式只需要一個新角色，請勿另外加入原角色。")
        replacement = references[0]
        if not (replacement.get("image_asset_ids") or []):
            raise RequestError("角色替換模式需要至少一張新角色圖片。")
        if not (_clean_text(replacement.get("video_asset_id")) or replacement.get("video_asset_ids")):
            raise RequestError("角色替換模式需要一支原始表演影片。")
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
            assignments.append(
                "<Picture 1> 是上一段動畫的最後一幀，只作為新影片開頭的連續性參考："
                "開場延續其中的人物位置、姿勢、構圖、鏡頭方向、場景、光線與動作趨勢，"
                "再依照敘述自然發展；不要把它當成額外角色、額外場景或要求全片保持靜止。"
            )

        def item_video_ids(item: dict[str, Any]) -> list[str]:
            values = [str(value) for value in (item.get("video_asset_ids") or []) if value]
            single = _clean_text(item.get("video_asset_id"))
            if single and single not in values:
                values.append(single)
            return values

        video_soundtrack_count = sum(
            len(item_video_ids(item)) for item in references if bool(item.get("video_use_audio"))
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
            video_use_audio = bool(item.get("video_use_audio"))
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
            for video_id in video_ids:
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
            if item_type in REFERENCE_SUBJECT_TYPES:
                subject_index += 1
                subject_tag = f"<Subject {subject_index}>"
                aliases[alias] = f"{subject_tag}（{alias}）"
                visual_sources = picture_tags or video_tags
                visual = "、".join(visual_sources) if visual_sources else "文字描述"
                definitions.append(f"{subject_tag} 是「{alias}」，形象參考來自 {visual}。{description}".strip())
                if picture_tags:
                    assignments.append(f"{'、'.join(picture_tags)} 只用於保留 {subject_tag} 的身份、外觀與固定特徵。")
                for tag in video_tags:
                    if mode == "replace":
                        assignments.append(
                            f"{tag} 是原始表演影片：讓 {subject_tag} 接手指定原角色的畫面位置、動作、姿勢、表演節奏與鏡頭關係；"
                            "保留場景、光線、道具與未被指定替換的人物，但禁止保留或混入原角色的臉、髮型、服裝、身材與身份特徵。"
                        )
                    else:
                        assignments.append(f"{tag} 只參考 {subject_tag} 的動作節奏、身體力學、姿態轉換與運鏡；除非文字明確要求，不要複製影片中的其他人物、服裝或背景。")
                if audio_tag:
                    voice_mode = _clean_text(item.get("voice_mode")) or "timbre"
                    if voice_mode == "reuse":
                        assignments.append(f"{audio_tag} 是 {subject_tag} 要沿用的原始聲音內容與聲音表現。")
                    else:
                        assignments.append(f"{audio_tag} 只作為 {subject_tag} 的說話音色參考；生成新台詞時不要照抄原音訊文字。")
            else:
                source_tags = picture_tags + video_tags
                first_tag = source_tags[0] if source_tags else alias
                aliases[alias] = f"{first_tag}（{alias}）"
                role_name = {"background": "場景與背景", "style": "美術風格", "motion": "動作與運鏡", "effect": "特效時序與表現"}.get(item_type, "參考素材")
                if picture_tags:
                    assignments.append(f"{'、'.join(picture_tags)} 是「{alias}」的{role_name}參考。{description}".strip())
                for tag in video_tags:
                    if item_type == "effect":
                        assignments.append(f"{tag} 是「{alias}」的特效時序、能量軌跡、衝擊回饋與鏡頭反應參考；保留主影片的角色、服裝與場景。{description}".strip())
                    else:
                        assignments.append(f"{tag} 是「{alias}」的{role_name}參考；只採用動態、節奏與鏡頭資訊，不複製未被指定的內容。{description}".strip())
                if audio_tag:
                    assignments.append(f"{audio_tag} 是「{alias}」所對應的環境或效果聲參考。")
            for tag, video_audio_tag in zip(video_tags, video_audio_tags):
                assignments.append(f"{video_audio_tag} 是 {tag} 的同步聲音參考；讓動作、碰撞或特效聲與畫面時序一致。")
            mapping.append({
                "alias": alias,
                "type": item_type,
                "subject_tag": subject_tag,
                "picture_tags": picture_tags,
                "video_tags": video_tags,
                "video_audio_tags": video_audio_tags,
                "audio_tag": audio_tag,
            })

        shot_lines, storyboard_images, storyboard_guides = _storyboard_text(storyboards, aliases)
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
            assignments.append(f"{tag} 是第 {shot_index} 張分鏡構圖參考，只控制相對位置、鏡位與場景安排。")

        if len(reference_images) > MAX_REFERENCE_IMAGES:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_IMAGES} 張圖片，目前為 {len(reference_images)} 張。")
        if len(reference_audios) > MAX_REFERENCE_AUDIOS:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_AUDIOS} 段獨立聲音，目前為 {len(reference_audios)} 段。")
        if len(reference_videos) > MAX_REFERENCE_VIDEOS:
            raise RequestError(f"多模態模式最多可使用 {MAX_REFERENCE_VIDEOS} 支影片，目前為 {len(reference_videos)} 支。")
        if not reference_images and not reference_videos and not reference_audios:
            raise RequestError("多模態模式至少需要圖片、影片或聲音其中一種。")

        rewritten_prompt = _rewrite_aliases(base_prompt, aliases)
        if prompt_profile == "shortfilm":
            retention: list[str] = []
            if first_image:
                retention.append(
                    "<Picture 1>: fully_preserved in the opening frame as the preceding shot's exact continuity anchor; "
                    "its pose, composition, camera direction, lighting, and momentum lead naturally into the new action."
                )
            for item in mapping:
                alias = item["alias"]
                subject_tag = item.get("subject_tag")
                picture_tags = item.get("picture_tags") or []
                video_tags = item.get("video_tags") or []
                audio_tag = item.get("audio_tag")
                if subject_tag:
                    retention.append(
                        f"{subject_tag}: fully_preserved whenever {alias} appears; keep identity, face, body proportions, "
                        "clothing, colors, and distinguishing features consistent across the shot."
                    )
                    for tag in picture_tags:
                        retention.append(
                            f"{tag}: attribute_transfer to {subject_tag}; transfer appearance and fixed visual attributes only."
                        )
                else:
                    for tag in picture_tags:
                        retention.append(
                            f"{tag}: fully_preserved as the visual reference for {alias}, including layout, palette, lighting, and spatial identity."
                        )
                for tag in video_tags:
                    retention.append(
                        f"{tag}: attribute_transfer for {alias}; transfer timing, motion mechanics, and camera rhythm without copying unrelated identities."
                    )
                if audio_tag:
                    retention.append(
                        f"{audio_tag}: reference for {alias}; use voice timbre and delivery characteristics without copying unrelated words."
                    )
            if not retention:
                retention.append("N/A")
            detail_parts = [rewritten_prompt]
            if shot_lines:
                detail_parts.append("Storyboard guidance:\n" + "\n".join(shot_lines))
            summary_prefix = "keyframe completion + reference generation" if first_image else "reference generation"
            summary = _clean_text(payload.get("shortfilm_summary")) or "A coherent narrative short-film shot generated from the selected references."
            soundscape = _clean_text(payload.get("shortfilm_soundscape")) or "Natural ambience and synchronized physical action sounds appropriate to the scene."
            music = _clean_text(payload.get("shortfilm_music")) or "N/A"
            sections = [
                "subject_definitions:\n" + ("\n".join(definitions) if definitions else "N/A"),
                f"summary:\n{summary_prefix}. {summary}",
                "retention_analysis:\n" + "\n".join(retention),
                "detailed_description:\n" + "\n\n".join(detail_parts),
                "overall_soundscape:\n" + soundscape,
                "non_diegetic_music:\n" + music,
            ]
            final_prompt = "\n\n".join(sections)
        elif mode == "mg_animation":
            timeline = _mg_animation_description(payload, aliases, length / FPS)
            retention = assignments + [
                "Keep the background, visible reel window, and character as independent visual layers; do not merge, duplicate, or deform their identities.",
                "Preserve every reel cell's width, height, pitch, center anchor, mask, and symbol identity. Animate only the visible reel-window presentation; never infer or alter mathematical reel-strip order or symbol frequency.",
                "Keep titles, payout values, JP meters, and product-owned top or bottom UI readable and unchanged. Any glow or overflow effect must remain a separate restrained overlay.",
            ]
            detail_parts = [timeline]
            if motion_direction:
                detail_parts.append("Motion direction: " + motion_direction)
            detail_parts.append("Additional user direction:\n" + rewritten_prompt)
            if shot_lines:
                detail_parts.append("Storyboard timeline:\n" + "\n".join(shot_lines))
            sections = [
                "subject_definitions:\n" + ("\n".join(definitions) if definitions else "N/A"),
                "summary:\nA polished slot-game main-game performance in which the character, visible reel window, and background follow separate, readable animation responsibilities.",
                "retention_analysis:\n" + "\n".join(retention),
                "detailed_description:\n" + "\n\n".join(detail_parts),
                "overall_soundscape:\nSynchronized reel-spin whirr, controlled deceleration, distinct stop clicks, and a restrained win chime after the symbols settle. No spoken dialogue unless explicitly requested.",
                "non_diegetic_music:\nN/A unless explicitly requested in the user direction.",
            ]
            final_prompt = "\n\n".join(sections)
        else:
            sections = []
            if definitions:
                sections.append("subject_definitions:\n" + "\n".join(definitions))
            if assignments:
                sections.append("reference_assignments:\n" + "\n".join(assignments))
            if motion_direction:
                sections.append("motion_direction:\n" + motion_direction)
            sections.append("detailed_description:\n" + rewritten_prompt)
            if shot_lines:
                sections.append("storyboard_timeline:\n" + "\n".join(shot_lines))
            if mode == "replace":
                sections.append(
                    "replacement_rules:\n只生成 <Subject 1> 作為指定位置的新角色；原角色的身份與外觀必須完全消失，"
                    "不得同時出現原角色與新角色，也不得把兩者的臉、頭髮、服裝或身體特徵混合。"
                    "除指定角色外，盡量維持原影片的場景、構圖、鏡頭、道具與其他人物。"
                    "如果指定原角色在某些畫面中沒有出現，保持那些畫面原樣，不得憑空加入 <Subject 1>。"
                )
                batch_segment = payload.get("replacement_batch_segment") or {}
                if batch_segment:
                    segment_index = max(1, _safe_int(batch_segment.get("index"), 1))
                    segment_total = max(segment_index, _safe_int(batch_segment.get("total"), segment_index))
                    source_start = max(0.0, _safe_float(batch_segment.get("source_start"), 0.0))
                    source_end = max(source_start, _safe_float(batch_segment.get("source_end"), length / FPS))
                    sections.append(
                        "batch_continuity_rules:\n"
                        f"This is replacement segment {segment_index} of {segment_total}, corresponding to source time "
                        f"{source_start:.3f}s–{source_end:.3f}s. Preserve the source video's exact action timing, "
                        "camera direction, spatial layout, lighting, props, and all non-target subjects. "
                        "The opening and ending motion must continue naturally across adjacent segments without "
                        "restarting the performance, adding a pause, changing identity, or resetting the pose. "
                        "Any additional appearance picture attached to <Subject 1> from the preceding generated "
                        "segment controls identity continuity only; it is not a keyframe and must not override <Video 1>."
                    )
            elif mode == "popup_panel":
                sections.append(
                    "popup_panel_rules:\n"
                    "<Picture 1> 是背景圖，必須從第一幀到最後一幀逐像素保持固定；禁止平移、縮放、變形、閃爍、亮度漂移、景深變化、視差或新增物件。"
                    "除 <Picture 1> 外，其餘已命名參考素材都屬於前景表演素材，可包含一個或多個面板、文字、分數、按鈕、標題、裝飾與特效；"
                    "這些素材只能在前景或面板範圍內依照敘述產生動畫，不得改變、遮換或重新生成背景圖。"
                    "鏡頭必須完全鎖定。彈窗面板消失後，最後畫面必須只剩與 <Picture 1> 完全一致的背景圖。"
                )
            else:
                sections.append("retention_rules:\n嚴格保持每個已命名角色的身份、臉部、服裝與聲音歸屬；不要把背景、風格或其他角色的特徵互相混合；動作參考只控制動態與時序，不得覆蓋角色身份；保持肢體結構、重心轉移、接觸和反作用力連續。")
            final_prompt = "\n\n".join(sections)
    else:
        shot_lines, storyboard_images, storyboard_guides = _storyboard_text(storyboards, {})
        guides.extend(storyboard_guides)
        if storyboard_images:
            raise RequestError("中間分鏡圖片需要使用多模態參考模式；此模式仍可使用純文字分鏡。")
        if prompt_profile == "shortfilm":
            soundscape = _clean_text(payload.get("shortfilm_soundscape")) or "Natural ambience and synchronized physical action sounds appropriate to the scene."
            music = _clean_text(payload.get("shortfilm_music")) or "N/A"
            final_prompt = "\n\n".join([
                "integrated_multimodal_description: " + base_prompt,
                "overall_soundscape: " + soundscape,
                "non_diegetic_music: " + music,
            ])
            return CompiledRequest(
                mode=mode,
                prompt=final_prompt,
                width=width,
                height=height,
                length=length,
                requested_duration=requested_duration,
                actual_duration=length / FPS,
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
            )
        intro: list[str] = []
        if mode == "fl2va" and first_image:
            intro.append("<Picture 1> 是影片的精確起始畫面。")
        if mode == "fl2va" and last_image:
            tag_index = 2 if first_image else 1
            intro.append(f"<Picture {tag_index}> 是影片的精確結束畫面。")
        if mode == "extend":
            intro.append("<Picture 1> 是上一段影片的最後一幀，也是本段影片的精確起始畫面。")
            intro.append("從這個姿勢與時間點立即繼續表演；延續角色位置、肢體動量、視線、攝影機方向、場景光線與環境狀態，不要重新起步、定格或重設構圖。")
        if mode == "symbol_loop":
            intro.append("<Picture 1> 同時是影片的精確起始畫面與精確結束畫面。")
            intro.append("畫布、中心錨點、圖騰比例、完整輪廓與四周留白必須固定；攝影機鎖定，不得縮放、平移、旋轉、裁切或重新構圖。")
            intro.append("動畫只完成一個循環，結尾平順返回起始姿勢、材質、光線與效果狀態；禁止加入重複的停頓端點。")
        final_prompt = "\n".join(intro)
        if motion_direction:
            final_prompt += ("\n\n" if final_prompt else "") + "motion_direction:\n" + motion_direction
        final_prompt += ("\n\n" if final_prompt else "") + base_prompt
        if shot_lines:
            final_prompt += "\n\nstoryboard_timeline:\n" + "\n".join(shot_lines)

    return CompiledRequest(
        mode=mode,
        prompt=final_prompt,
        width=width,
        height=height,
        length=length,
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
    )


def build_workflow(
    compiled: CompiledRequest,
    uploaded_assets: dict[str, str],
    output_stem: str,
    turbo_lora_name: str | None = None,
) -> dict[str, Any]:
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
    if compiled.quality_mode == "turbo":
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
    clip = add("CLIPLoader", {
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "type": "minimax",
        "device": "default",
    }, "MiniMax H3 Text Encoder")
    video_vae = add("VAELoader", {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}, "Video VAE")
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
    return workflow


def required_asset_ids(compiled: CompiledRequest) -> list[str]:
    values = [
        compiled.first_image,
        compiled.last_image,
        *compiled.reference_images,
        *compiled.reference_videos,
        *compiled.reference_audios,
        *(guide["asset_id"] for guide in compiled.guides),
    ]
    return list(dict.fromkeys(value for value in values if value))
