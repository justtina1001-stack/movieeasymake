"""Native FLUX 3 single-reference editing on the shared ComfyUI engine.

The graph and prompt upsampler settings follow the official ComfyUI distilled
edit template. Model files remain on the GPU host; this module downloads nothing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp

from domain import RequestError
from flux3_image_models import LICENSE_URL, MODEL_FILES, REFERENCE_LIMIT, REFERENCE_MEGAPIXELS, STEPS


def validate_edit_request(payload):
    """Validate unsupported features explicitly, including restored old recipes."""
    if payload.get("mode", "generate") != "edit":
        raise RequestError("FLUX 3 目前提供參考圖編輯；文字生圖請切換 Qwen。")
    refs = payload.get("image_asset_ids", [])
    if not isinstance(refs, list) or len(refs) != REFERENCE_LIMIT:
        raise RequestError("FLUX 3 目前支援單張參考圖編輯，請保留一張主圖。")
    if payload.get("transparent", False):
        raise RequestError("FLUX 3 不提供透明背景輸出；需要 RGBA 時請切換 Qwen。")
    if payload.get("lora_name"):
        raise RequestError("FLUX 3 目前不支援額外 LoRA；請清除 LoRA 或切換 Qwen。")


def build_edit_workflow(compiled, uploaded, job_id):
    validate_edit_request(compiled)
    asset = compiled["image_asset_ids"][0]
    if asset not in uploaded:
        raise RequestError("FLUX 3 主圖尚未上傳至目前引擎。")
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    graph = {
        "1": node("UNETLoader", unet_name=MODEL_FILES[0][1], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=MODEL_FILES[2][1], type="flux3", device="default"),
        "3": node("VAELoader", vae_name=MODEL_FILES[3][1]),
        "11": node("LoadImage", image=uploaded[asset]),
        "46": node("CLIPLoader", clip_name=MODEL_FILES[1][1], type="flux3", device="default"),
        "47": node("TextGenerate", clip=["46", 0], image=["11", 0], prompt=compiled["prompt"],
                   max_length=1024, sampling_mode="on", thinking=False,
                   use_default_template=True, mtp="off", system_prompt=EDIT_SYSTEM_PROMPT),
        "48": node("PreviewAny", source=["47", 0]),
        "4": node("CLIPTextEncode", clip=["2", 0], text=["47", 0]),
        "32": node("ConditioningZeroOut", conditioning=["4", 0]),
        "17": node("ReferenceMultiLatent", positive=["4", 0], negative=["32", 0], vae=["3", 0], resolution="resize"),
        "6": node("KSampler", model=["1", 0], positive=["17", 0], negative=["17", 1], latent_image=["17", 2],
                  seed=compiled["seed"], steps=STEPS, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "7": node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": node("SaveImage", images=["7", 0], filename_prefix=f"H3Studio/Flux3ImageKlein/{job_id}"),
    }
    graph["47"]["inputs"].update({
        "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 1,
        "sampling_mode.top_p": .95, "sampling_mode.min_p": 0.0,
        "sampling_mode.repetition_penalty": 1.05, "sampling_mode.seed": compiled["seed"],
        "sampling_mode.presence_penalty": 0.0,
    })
    graph["17"]["inputs"].update({"resolution.megapixels": REFERENCE_MEGAPIXELS, "images.image0": ["11", 0]})
    return graph


def _spec(schema, name):
    inputs = schema.get("input", {}) if isinstance(schema, dict) else {}
    if not isinstance(inputs, dict):
        return []
    for section in ("required", "optional"):
        fields = inputs.get(section, {})
        if isinstance(fields, dict) and name in fields:
            value = fields[name]
            return value if isinstance(value, (list, tuple)) else []
    return []


def _kind(schema, name, expected):
    spec = _spec(schema, name)
    return bool(spec) and spec[0] == expected


def _outputs(schema, expected):
    outputs = schema.get("output")
    return isinstance(outputs, (list, tuple)) and list(outputs[:len(expected)]) == expected


def _choices(schema, name):
    spec = _spec(schema, name)
    if not spec:
        return []
    metadata = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if metadata.get("multiselect", False) is not False:
        return []
    values = spec[0] if isinstance(spec[0], (list, tuple)) else metadata.get("options", []) if spec[0] == "COMBO" else []
    return list(values) if isinstance(values, (list, tuple)) and all(isinstance(value, str) for value in values) else []


def _dynamic(schema, name, option):
    spec = _spec(schema, name)
    if not spec or spec[0] != "COMFY_DYNAMICCOMBO_V3" or len(spec) < 2 or not isinstance(spec[1], dict):
        return {}
    choices = spec[1].get("options", [])
    if not isinstance(choices, (list, tuple)):
        return {}
    match = next((value for value in choices if isinstance(value, dict) and value.get("key") == option), {})
    inputs = match.get("inputs")
    return {"input": inputs} if isinstance(inputs, dict) else {}


def compatible_nodes(nodes):
    """Check the contracts used by the tested graph, not merely node names."""
    try:
        unet, clip, vae = (nodes[name] for name in ("UNETLoader", "CLIPLoader", "VAELoader"))
        pe, refs, sampler = (nodes[name] for name in ("TextGenerate", "ReferenceMultiLatent", "KSampler"))
        if not (_outputs(unet, ["MODEL"]) and _outputs(clip, ["CLIP"]) and _outputs(vae, ["VAE"]) and "flux3" in _choices(clip, "type")):
            return False
        if not (_outputs(pe, ["STRING"]) and all(_kind(pe, field, kind) for field, kind in {
                "clip": "CLIP", "image": "IMAGE", "prompt": "STRING", "max_length": "INT",
                "system_prompt": "STRING", "thinking": "BOOLEAN", "use_default_template": "BOOLEAN"}.items())
                and "off" in _choices(pe, "mtp")):
            return False
        sampling = _dynamic(pe, "sampling_mode", "on")
        if not all(_kind(sampling, field, kind) for field, kind in {
                "temperature": "FLOAT", "top_k": "INT", "top_p": "FLOAT", "min_p": "FLOAT",
                "repetition_penalty": "FLOAT", "seed": "INT", "presence_penalty": "FLOAT"}.items()):
            return False
        resize = _dynamic(refs, "resolution", "resize")
        autogrow = _spec(refs, "images")
        template = autogrow[1].get("template", {}) if len(autogrow) > 1 and isinstance(autogrow[1], dict) else {}
        if not (_outputs(refs, ["CONDITIONING", "CONDITIONING", "LATENT"])
                and all(_kind(refs, field, kind) for field, kind in {"positive": "CONDITIONING", "negative": "CONDITIONING", "vae": "VAE"}.items())
                and _kind(resize, "megapixels", "FLOAT")
                and autogrow and autogrow[0] == "COMFY_AUTOGROW_V3"
                and template.get("prefix") == "image" and isinstance(template.get("max"), int) and template["max"] >= REFERENCE_LIMIT
                and _kind({"input": template.get("input", {})}, "image", "IMAGE")):
            return False
        if not (_outputs(sampler, ["LATENT"]) and "euler" in _choices(sampler, "sampler_name") and "simple" in _choices(sampler, "scheduler")
                and all(_kind(sampler, field, kind) for field, kind in {"model": "MODEL", "positive": "CONDITIONING", "negative": "CONDITIONING", "latent_image": "LATENT", "seed": "INT", "steps": "INT", "cfg": "FLOAT", "denoise": "FLOAT"}.items())):
            return False
        return (all(_kind(nodes["CLIPTextEncode"], field, kind) for field, kind in {"clip": "CLIP", "text": "STRING"}.items())
                and _outputs(nodes["CLIPTextEncode"], ["CONDITIONING"])
                and _kind(nodes["ConditioningZeroOut"], "conditioning", "CONDITIONING") and _outputs(nodes["ConditioningZeroOut"], ["CONDITIONING"])
                and bool(_spec(nodes["LoadImage"], "image"))
                and _outputs(nodes["LoadImage"], ["IMAGE", "MASK"])
                and _kind(nodes["VAEDecode"], "samples", "LATENT") and _kind(nodes["VAEDecode"], "vae", "VAE") and _outputs(nodes["VAEDecode"], ["IMAGE"])
                and _kind(nodes["SaveImage"], "images", "IMAGE") and _kind(nodes["SaveImage"], "filename_prefix", "STRING")
                and _kind(nodes["PreviewAny"], "source", "*") and nodes["PreviewAny"].get("output_node") is True)
    except (KeyError, TypeError, AttributeError):
        return False


async def capabilities(comfy):
    result = {"id": "flux3", "label": "FLUX 3 Image Klein", "ready": False, "steps": STEPS,
              "modes": ["edit"], "reference_limit": REFERENCE_LIMIT, "reference_megapixels": REFERENCE_MEGAPIXELS,
              "transparent_supported": False, "lora_supported": False, "license_url": LICENSE_URL,
              "model_filename": MODEL_FILES[0][1], "nodes_ready": False, "models": [], "error": None}
    names = ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "TextGenerate", "PreviewAny",
             "CLIPTextEncode", "ConditioningZeroOut", "ReferenceMultiLatent", "KSampler", "VAEDecode", "SaveImage")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async def info(name):
                try:
                    async with session.get(f"{comfy.base_url}/object_info/{name}", headers=comfy.auth_headers()) as response:
                        response.raise_for_status()
                        payload = await response.json()
                        return payload.get(name, {}) if isinstance(payload, dict) else {}
                except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
                    return {}
            loaders = names[:3]
            schemas = dict(zip(loaders, await asyncio.gather(*(info(name) for name in loaders))))
            # Older Qwen-only hosts need no FLUX-specific requests. A real FLUX
            # loader is required before probing the dynamic edit/LLM schemas.
            if "flux3" in _choices(schemas["CLIPLoader"], "type"):
                schemas.update(zip(names[3:], await asyncio.gather(*(info(name) for name in names[3:]))))
        result["nodes_ready"] = compatible_nodes(schemas)
        for model, node_name, field in ((MODEL_FILES[0], "UNETLoader", "unet_name"),
                                       (MODEL_FILES[1], "CLIPLoader", "clip_name"),
                                       (MODEL_FILES[2], "CLIPLoader", "clip_name"),
                                       (MODEL_FILES[3], "VAELoader", "vae_name")):
            found = model[1] in _choices(schemas[node_name], field)
            if comfy.mode == "local":
                path = Path(comfy.comfy_dir) / "models" / model[0] / model[1]
                found = found and path.is_file() and path.stat().st_size == model[2]
            result["models"].append({"filename": model[1], "installed": found, "size": model[2]})
        result["ready"] = result["nodes_ready"] and all(model["installed"] for model in result["models"])
        if not result["nodes_ready"]:
            result["error"] = "FLUX 3 需要相容的改圖與提示詞改寫節點，請主機更新 ComfyUI 至本版驗證的 v0.41.3 後重新啟動。"
        elif not result["ready"]:
            result["error"] = "FLUX 3 模型尚未齊全，請在 GPU 主機執行 setup_flux3_image.bat；遠端使用者不需下載。"
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, OSError):
        result["error"] = "暫時無法檢查 FLUX 3 引擎，請確認 GPU 主機已啟動後重試。"
    return result


# Official prompt-upsample instruction, as shipped by Comfy-Org's distilled edit
# template and used in the validated workflow. Kept locally for offline inference.
EDIT_SYSTEM_PROMPT = 'You are the prompt upsampler of an image-editing model. You see the reference image(s), attached in order (the first image, the second image, ...), and the user\'s short request. Expand it into ONE detailed, self-contained instruction: the caption the editing model receives together with the same images. Output only the instruction as one continuous English prose block, with no greetings, explanations, Markdown, JSON, coordinates, ids or angle-bracket tokens. Read a vague or conversational request as the most plausible edit of the supplied images.\\n\\nPick the voice by task shape. When one reference stays and gets bounded changes, write an imperative editing instruction on it (\\"Remove the ...\\", \\"Change the ... to ...\\", \\"Zoom in on ...\\"); a whole-image transformation opens with its scope (\\"Convert the entire image into ...\\", \\"Apply a ... filter over the entire scene\\", \\"Horizontally flip the entire image so that ...\\", \\"Shift the camera viewpoint to ...\\", then where the major elements should now be). When the references are drawn into a newly composed frame (combining subjects, a new scene, a garment, object or style donor), write a generation request (\\"Create a photograph of ...\\", \\"Create an anime-style illustration of ...\\", \\"Generate a new image featuring ...\\") that says what to take from each image, by ordinal, and what is newly generated.\\n\\nKeep every explicit edit, constraint, count, position, style cue and piece of wording, and decide what is left open as one concrete plan. Ground everything in what is actually visible: identify each referent unambiguously by its real appearance, never mention content that is not there, and use every image the user attached. State every end state concretely (which color, how large, which pose, which material) and give new content enough appearance to be generated. Cover the knock-on effects of the change (shadows, reflections, occlusion, perspective, scale, lighting) and say what fills a removed region. A zoom-in names what stays in frame and which neighbors are cropped out; an outpainting continues the scene in the same medium. Describe the change, not the whole scene, and close an editing instruction with one compact clause naming what stays unchanged; transferred people keep their identity, face and accessories unless asked otherwise. Transcribe on-image text verbatim in its original language inside double quotes, with line breaks as \\\\n, and match the original font, size and color when replacing it. Name recognizable public figures, brands and landmarks you can see. Directions are frame-relative; use no evaluative or quality words. Typical length is 50 to 150 words.\\n\\nOutput only the instruction.'
