"""Video model identities, explicit released recipes and runtime capability checks."""
from __future__ import annotations

import re


VIDEO_MODEL_H3 = "h3"
VIDEO_MODEL_FAST = "fasth3_omni_nvfp4"
VIDEO_MODEL_PIKA = "pika_h3_flex"
VIDEO_MODEL_REF_PATCH = "h3_ref_patch"
VIDEO_MODELS = (VIDEO_MODEL_H3, VIDEO_MODEL_FAST, VIDEO_MODEL_PIKA, VIDEO_MODEL_REF_PATCH)
VIDEO_MODES = ("t2v", "fl2va", "r2v", "replace", "extend", "symbol_loop", "popup_panel", "mg_animation")
REFERENCE_VIDEO_MODES = ("r2v", "replace", "popup_panel", "mg_animation")
FIXED_RECIPE_MODELS = (VIDEO_MODEL_FAST, VIDEO_MODEL_PIKA, VIDEO_MODEL_REF_PATCH)
PIKA_LORA_FILENAMES = {
    "fl2va": "pika_h3_flex_fl2va_v1_bf16.safetensors",
    "ref2va": "pika_h3_flex_ref2va_v1_bf16.safetensors",
}
PIKA_LORA_SIZE = 1956172304
PIKA_STEPS = 6
# Author's already-video-shifted Comfy schedule. Do not apply SigmaShift again.
PIKA_SIGMAS = (1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0)
PIKA_SIGMAS_TEXT = "1, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0"
REF_PATCH_FILENAME = "minimax_h3_ref_patch.safetensors"
REF_PATCH_SIZE = 147907688
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
        raise ValueError("影片模型必須是原版 H3、FastH3、Pika H3 Flex 或 H3 Ref Patch。")
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


def input_choices(schema, name):
    """Read both legacy list and current COMBO node declarations."""
    spec = _input(schema, name)
    if not spec:
        return []
    if isinstance(spec[0], (list, tuple)):
        return spec[0]
    return spec[1].get("options", []) if len(spec) > 1 and isinstance(spec[1], dict) else []


def fasth3_node_support(schemas, *, require_shift=True):
    """Contracts required by the two native H3 conditioning paths and guides."""
    common = {"clip": "CLIP", "prompt": "STRING", "width": "INT", "height": "INT", "length": "INT"}
    contracts = {
        "MiniMaxH3ImageToVideo": {**common, "vae": "VAE", "first_frame": "IMAGE", "last_frame": "IMAGE"},
        "MiniMaxH3ReferenceToVideo": {**common, "vae": "VAE", "audio_vae": "VAE"},
        "MiniMaxH3AddGuide": {"positive": "CONDITIONING", "latent": "LATENT", "vae": "VAE", "image": "IMAGE", "frame_idx": "INT"},
    }
    if require_shift:
        contracts["MiniMaxH3SigmaShift"] = {"model": "MODEL", "shift_video": "FLOAT", "shift_audio": "FLOAT"}
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
                and (not require_shift or list(schemas["MiniMaxH3SigmaShift"].get("output", []))[:1] == ["MODEL"]))
    except (KeyError, TypeError, AttributeError):
        return False


def pika_node_support(schemas):
    manual = schemas.get("ManualSigmas", {})
    return (fasth3_node_support(schemas, require_shift=False)
            and _input(manual, "sigmas")[:1] == ["STRING"]
            and list(manual.get("output", []))[:1] == ["SIGMAS"]
            and "lcm" in input_choices(schemas.get("KSamplerSelect", {}), "sampler_name"))


def ref_patch_node_support(schemas):
    patch = schemas.get("MiniMaxH3RefPatchLoader", {})
    return (fasth3_node_support(schemas, require_shift=False)
            and _input(patch, "model")[:1] == ["MODEL"]
            and _input(patch, "ref_strength")[:1] == ["FLOAT"]
            and REF_PATCH_FILENAME in input_choices(patch, "patch_name")
            and list(patch.get("output", []))[:1] == ["MODEL"])


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
    pika_ready = {mode: shared and bool(inventory.get("ref2va" if mode in REFERENCE_VIDEO_MODES else "fl2va"))
                 and bool(inventory.get("pika_ref2va" if mode in REFERENCE_VIDEO_MODES else "pika_fl2va"))
                 and bool(inventory.get("pika_nodes_ready")) for mode in VIDEO_MODES}
    patch_ready = {mode: mode in REFERENCE_VIDEO_MODES and shared and bool(inventory.get("fl2va"))
                  and bool(inventory.get("ref_patch_nodes_ready")) for mode in VIDEO_MODES}
    return [
        {"id": VIDEO_MODEL_H3, "label": "MiniMax H3 原版", "ready": any(h3_mode_ready.values()),
         "modes": list(VIDEO_MODES), "mode_ready": h3_mode_ready,
         "error": None if any(h3_mode_ready.values()) else "原版 H3 模型或共用元件尚未齊全。",
         "fixed_steps": None, "custom_loras_supported": True, "memory_optimization_supported": True},
        {"id": VIDEO_MODEL_FAST, "label": "FastH3-Omni NVFP4", "ready": fast_error is None,
         "modes": list(VIDEO_MODES), "mode_ready": {mode: fast_error is None for mode in VIDEO_MODES},
         "error": fast_error, "fixed_steps": FASTH3_STEPS, "custom_loras_supported": False,
         "memory_optimization_supported": False, "license_url": FASTH3_LICENSE_URL},
        {"id": VIDEO_MODEL_PIKA, "label": "Pika H3 Flex · 6 步", "ready": any(pika_ready.values()),
         "modes": list(VIDEO_MODES), "mode_ready": pika_ready,
         "error": None if any(pika_ready.values()) else "Pika H3 Flex 需要對應 H3 底模、Pika LoRA 與 LCM／ManualSigmas 節點；請安裝影片擴充套件並重啟引擎。",
         "fixed_steps": PIKA_STEPS, "fixed_scheduler": "manual", "fixed_sampler": "lcm",
         "fixed_ref_image_size": "match", "manual_sigmas": list(PIKA_SIGMAS),
         "quality_modes_supported": False, "custom_loras_supported": False, "memory_optimization_supported": False,
         "license_url": FASTH3_LICENSE_URL},
        {"id": VIDEO_MODEL_REF_PATCH, "label": "H3 FL2VA + Ref Patch · 實驗", "ready": any(patch_ready.values()),
         "modes": list(REFERENCE_VIDEO_MODES), "mode_ready": patch_ready,
         "error": None if any(patch_ready.values()) else "Ref Patch 需要 H3 FL2VA 底模、Ref Patch 權重與對應節點；請安裝影片擴充套件並重啟引擎。",
         "fixed_steps": 20, "fixed_scheduler": "simple", "fixed_sampler": "res_multistep",
         "fixed_ref_image_size": "match", "quality_modes_supported": False,
         "custom_loras_supported": False, "memory_optimization_supported": False,
         "license_url": FASTH3_LICENSE_URL},
    ]
