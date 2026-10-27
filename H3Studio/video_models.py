"""Video model identities and pinned FastH3 Omni NVFP4 artifact."""
from __future__ import annotations

import re


VIDEO_MODEL_H3 = "h3"
VIDEO_MODEL_FAST = "fasth3_omni_nvfp4"
VIDEO_MODELS = (VIDEO_MODEL_H3, VIDEO_MODEL_FAST)
VIDEO_MODES = ("t2v", "fl2va", "r2v", "replace", "extend", "symbol_loop", "popup_panel", "mg_animation")
FASTH3_REPO = "FastVideo/FastH3-Omni-NVFP4-QAD-Comfy"
FASTH3_REVISION = "36dbf15cd11113471114b1040c4f38646c031549"
FASTH3_FILENAME = "fasth3_omniref_qad_nvfp4_comfy.safetensors"
FASTH3_SIZE = 13773998700
FASTH3_SHA256 = "bf25ee58df65b4dfee349d612f7ac62a2fea0dddd1e68eb37e75c8ce8b166f5a"
FASTH3_LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
FASTH3_COMFY_REVISION = "b1a247c9cf9dbfda4b2c0a0c1d50950667eeaa0c"
FASTH3_MIN_COMFY_VERSION = "0.42.0"
FASTH3_STEPS = 8


def normalize_video_model(value):
    if value is None or value == "":
        return VIDEO_MODEL_H3
    if not isinstance(value, str) or value not in VIDEO_MODELS:
        raise ValueError("影片模型必須是原版 H3 或 FastH3-Omni NVFP4。")
    return value


def blackwell_device(stats):
    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    if not isinstance(devices, list):
        return False
    for device in devices:
        if not isinstance(device, dict) or device.get("type") != "cuda":
            continue
        name = device.get("name", "")
        if isinstance(name, str) and re.search(r"\b(?:RTX\s+50\d{2}|Blackwell|(?:GB|B)(?:100|200|300))\b", name, re.I):
            return True
    return False


def _input(schema, name):
    inputs = schema.get("input", {})
    for section in ("required", "optional"):
        values = inputs.get(section, {})
        if isinstance(values, dict) and name in values:
            return values[name]
    return []


def fasth3_node_support(schemas):
    """Contracts required by the two native H3 conditioning paths and guides."""
    common = {"clip": "CLIP", "prompt": "STRING", "width": "INT", "height": "INT", "length": "INT"}
    contracts = {
        "MiniMaxH3ImageToVideo": {**common, "vae": "VAE", "first_frame": "IMAGE", "last_frame": "IMAGE"},
        "MiniMaxH3ReferenceToVideo": {**common, "vae": "VAE", "audio_vae": "VAE"},
        "MiniMaxH3AddGuide": {"positive": "CONDITIONING", "latent": "LATENT", "vae": "VAE", "image": "IMAGE", "frame_idx": "INT"},
        "MiniMaxH3SigmaShift": {"model": "MODEL", "shift_video": "FLOAT", "shift_audio": "FLOAT"},
    }
    try:
        for name, fields in contracts.items():
            schema = schemas[name]
            if not all(_input(schema, field) and _input(schema, field)[0] == kind for field, kind in fields.items()):
                return False
        if any(list(schemas[name].get("output", []))[:2] != ["CONDITIONING", "LATENT"]
               for name in ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo")):
            return False
        ref = schemas["MiniMaxH3ReferenceToVideo"]
        for name, prefix, maximum, kind in (("ref_images", "ref_image_", 9, "IMAGE"),
                                             ("ref_videos", "ref_video_", 3, "IMAGE"),
                                             ("ref_video_audios", "ref_video_audio_", 3, "AUDIO"),
                                             ("ref_audios", "ref_audio_", 3, "AUDIO")):
            spec = _input(ref, name)
            template = spec[1].get("template", {}) if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if (not spec or spec[0] != "COMFY_AUTOGROW_V3" or template.get("prefix") != prefix
                    or template.get("max", 0) < maximum
                    or _input({"input": template.get("input", {})}, name[:-1])[:1] != [kind]):
                return False
        return (list(schemas["MiniMaxH3AddGuide"].get("output", []))[:1] == ["CONDITIONING"]
                and list(schemas["MiniMaxH3SigmaShift"].get("output", []))[:1] == ["MODEL"])
    except (KeyError, TypeError, AttributeError):
        return False


def video_model_profiles(inventory):
    inventory = inventory if isinstance(inventory, dict) else {}
    shared = bool(inventory.get("text_encoder") and inventory.get("audio_vae") and inventory.get("video_vae"))
    h3_mode_ready = {mode: shared and bool(inventory.get("ref2va" if mode in {"r2v", "replace", "popup_panel", "mg_animation"} else "fl2va"))
                     for mode in VIDEO_MODES}
    fast_error = None
    if not inventory.get(VIDEO_MODEL_FAST):
        fast_error = "FastH3-Omni NVFP4 尚未安裝，請在 GPU 主機執行 setup_fasth3_omni.bat。"
    elif not shared:
        fast_error = "FastH3 需要現有 H3 的文字編碼器、影片 VAE 與聲音 VAE，請在 GPU 主機補齊。"
    elif not inventory.get("fasth3_blackwell"):
        fast_error = "FastH3 NVFP4 需要可確認的 NVIDIA Blackwell GPU；請使用 RTX 50 系列或其他相容 Blackwell 主機。"
    elif not inventory.get("fasth3_core_supported"):
        fast_error = "FastH3 完整 PDD 模型需要 ComfyUI 0.42.0 以上與對應依賴；請主機更新引擎後重啟。"
    elif not inventory.get("fasth3_nodes_ready"):
        fast_error = "FastH3 所需的 H3 條件、首尾引導或 Sigma Shift 節點尚未就緒，請更新 GPU 主機引擎後重啟。"
    return [
        {"id": VIDEO_MODEL_H3, "label": "MiniMax H3 原版", "ready": any(h3_mode_ready.values()),
         "modes": list(VIDEO_MODES), "mode_ready": h3_mode_ready,
         "error": None if any(h3_mode_ready.values()) else "原版 H3 模型或共用元件尚未齊全。",
         "fixed_steps": None, "custom_loras_supported": True, "memory_optimization_supported": True},
        {"id": VIDEO_MODEL_FAST, "label": "FastH3-Omni NVFP4", "ready": fast_error is None,
         "modes": list(VIDEO_MODES), "mode_ready": {mode: fast_error is None for mode in VIDEO_MODES},
         "error": fast_error, "fixed_steps": FASTH3_STEPS, "custom_loras_supported": False,
         "memory_optimization_supported": False, "license_url": FASTH3_LICENSE_URL},
    ]
