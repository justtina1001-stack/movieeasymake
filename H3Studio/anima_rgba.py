"""Native Anima RGBA generation; no background-removal or RGB conversion pass."""
from __future__ import annotations

import asyncio
from pathlib import Path
import re

import aiohttp

from domain import RequestError
from anima_rgba_models import (MODEL_FILES, LICENSE_URL, BASE_LICENSE_URL, MIN_COMFY_VERSION, STEPS, CFG, SAMPLER,
                               SCHEDULER, LORA_STRENGTH, MIN_DIMENSION, MAX_DIMENSION,
                               DIMENSION_STEP, MAX_PIXELS, QUALITY_PREFIX, NEGATIVE_PROMPT)


def validate_request(payload):
    if payload.get("mode", "generate") != "generate":
        raise RequestError("Anima RGBA 本版只支援文字生圖；參考圖編輯請切換 Qwen 或 FLUX 3。")
    if payload.get("image_asset_ids"):
        raise RequestError("Anima RGBA 文字生圖不使用參考圖片；請先切換模式或移除參考圖。")
    if payload.get("lora_name"):
        raise RequestError("Anima RGBA 已使用專用透明 LoRA，不支援疊加 Qwen LoRA。")
    if "batch_size" in payload and (type(payload["batch_size"]) is not int or payload["batch_size"] != 1):
        raise RequestError("Anima RGBA 本版每次只生成一張圖片。")
    for key in ("width", "height"):
        value = payload.get(key, 1024)
        if type(value) is not int or not MIN_DIMENSION <= value <= MAX_DIMENSION or value % DIMENSION_STEP:
            raise RequestError(f"Anima RGBA 寬高須為 {MIN_DIMENSION}～{MAX_DIMENSION}，並為 {DIMENSION_STEP} 的倍數。")
    negative = payload.get("negative_prompt", NEGATIVE_PROMPT)
    if not isinstance(negative, str) or len(negative) > 8000:
        raise RequestError("Anima RGBA 負面提示詞最多 8,000 字元。")


def prepare_prompt(prompt):
    """Add the author's explicit quality/alpha tags without an AI rewrite."""
    text = prompt.strip()
    if not re.search(r"transparent[\s_]+background", text, re.I):
        text = "transparent background, " + text
    if not text.lower().startswith(QUALITY_PREFIX.strip().lower()):
        text = QUALITY_PREFIX + text
    return text


def compiled_fields(payload):
    negative = payload.get("negative_prompt", NEGATIVE_PROMPT)
    return dict(prompt=prepare_prompt(payload["prompt"]), negative_prompt=negative.strip(),
                original_negative_prompt=negative, transparent=True, native_alpha=True,
                steps=STEPS, cfg=CFG, sampler_name=SAMPLER, scheduler=SCHEDULER, batch_size=1,
                model_adapter=MODEL_FILES[3][1], model_adapter_strength=LORA_STRENGTH,
                output_size_policy="requested_dimensions", output_format="RGBA PNG",
                prompt_preparation="anima_rgba_author_tags_v1")


def _path_name(value):
    return value.replace("\\", "/") if isinstance(value, str) else ""


def build_workflow(compiled, uploaded, job_id):
    validate_request(compiled)
    if uploaded:
        raise RequestError("Anima RGBA 文字生圖不使用上傳素材。")
    recipe = {"steps": STEPS, "cfg": CFG, "sampler_name": SAMPLER, "scheduler": SCHEDULER,
              "model_adapter_strength": LORA_STRENGTH, "transparent": True}
    if any(compiled.get(key) != value for key, value in recipe.items()):
        raise RequestError("Anima RGBA 配方與已驗證的原生透明設定不符，請依原稿重新建立工作。")
    if _path_name(compiled.get("model_adapter")) != MODEL_FILES[3][1]:
        raise RequestError("Anima RGBA 專用透明 LoRA 不符，請重新建立工作。")

    names = compiled.get("native_model_names") or {}
    def name(field, model):
        selected = names.get(field, str(Path(model[1])))
        if _path_name(selected) != model[1]:
            raise RequestError("Anima RGBA 引擎模型檔名與已安裝配方不符。")
        return selected
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    return {
        "1": node("UNETLoader", unet_name=name("unet_name", MODEL_FILES[0]), weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=name("clip_name", MODEL_FILES[1]), type="stable_diffusion", device="default"),
        "3": node("VAELoader", vae_name=name("vae_name", MODEL_FILES[2])),
        "4": node("CLIPTextEncode", clip=["2", 0], text=compiled["prompt"]),
        "5": node("EmptyLatentImage", width=compiled["width"], height=compiled["height"], batch_size=1),
        "6": node("KSampler", model=["9", 0], positive=["4", 0], negative=["10", 0],
                  latent_image=["5", 0], seed=compiled["seed"], steps=STEPS, cfg=CFG,
                  sampler_name=SAMPLER, scheduler=SCHEDULER, denoise=1.0),
        "7": node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": node("SaveImage", images=["7", 0], filename_prefix=f"H3Studio/AnimaRGBA/{job_id}"),
        "9": node("LoraLoaderModelOnly", model=["1", 0], lora_name=name("lora_name", MODEL_FILES[3]), strength_model=LORA_STRENGTH),
        "10": node("CLIPTextEncode", clip=["2", 0], text=compiled["negative_prompt"]),
    }


def _input(schema, field):
    inputs = schema.get("input", {})
    if not isinstance(inputs, dict):
        return []
    for section in ("required", "optional"):
        values = inputs.get(section)
        if isinstance(values, dict) and field in values:
            spec = values[field]
            return spec if isinstance(spec, (list, tuple)) else []
    return []


def _choices(schema, field):
    spec = _input(schema, field)
    if not spec:
        return []
    if isinstance(spec[0], (list, tuple)):
        values = spec[0]
    else:
        meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        values = meta.get("options") if spec[0] == "COMBO" and not meta.get("multiselect", False) else None
    return list(values) if isinstance(values, (list, tuple)) and all(isinstance(n, str) for n in values) else []


def compatible_nodes(schemas):
    contracts = {
        "UNETLoader": ({}, ["MODEL"]),
        "CLIPLoader": ({}, ["CLIP"]),
        "VAELoader": ({}, ["VAE"]),
        "LoraLoaderModelOnly": ({"model": "MODEL", "strength_model": "FLOAT"}, ["MODEL"]),
        "CLIPTextEncode": ({"clip": "CLIP", "text": "STRING"}, ["CONDITIONING"]),
        "EmptyLatentImage": ({"width": "INT", "height": "INT", "batch_size": "INT"}, ["LATENT"]),
        "KSampler": ({"model": "MODEL", "seed": "INT", "steps": "INT", "cfg": "FLOAT", "positive": "CONDITIONING",
                      "negative": "CONDITIONING", "latent_image": "LATENT", "denoise": "FLOAT"}, ["LATENT"]),
        "VAEDecode": ({"samples": "LATENT", "vae": "VAE"}, ["IMAGE"]),
        "SaveImage": ({"images": "IMAGE", "filename_prefix": "STRING"}, []),
    }
    for name, (fields, outputs) in contracts.items():
        schema = schemas.get(name, {})
        actual = schema.get("output")
        if not isinstance(actual, (list, tuple)) or list(actual[:len(outputs)]) != outputs:
            return False
        if not all(_input(schema, field)[:1] == [kind] for field, kind in fields.items()):
            return False
    return ("stable_diffusion" in _choices(schemas["CLIPLoader"], "type")
            and "default" in _choices(schemas["CLIPLoader"], "device")
            and "default" in _choices(schemas["UNETLoader"], "weight_dtype")
            and SAMPLER in _choices(schemas["KSampler"], "sampler_name")
            and SCHEDULER in _choices(schemas["KSampler"], "scheduler")
            and schemas["SaveImage"].get("output_node") is True)


async def capabilities(comfy):
    result = dict(id="anima_rgba", label="Anima RGBA · 原生透明動漫", ready=False,
                  description="Anima 專用 RGBA VAE 與 LoRA，文字直接生成帶 Alpha 的動漫 PNG。",
                  modes=["generate"], mode_ready={"generate": False, "edit": False}, mode_errors={},
                  reference_limit=0, transparent_supported=True, native_alpha=True, lora_supported=False,
                  steps=STEPS, fixed_steps=STEPS, cfg=CFG, sampler_name=SAMPLER, scheduler=SCHEDULER,
                  generation_min_dimension=MIN_DIMENSION, generation_max_dimension=MAX_DIMENSION,
                  generation_dimension_step=DIMENSION_STEP, generation_max_pixels=MAX_PIXELS,
                  negative_prompt=NEGATIVE_PROMPT, license="Anima base: CircleStone Labs Non-Commercial License; RGBA add-on: Apache-2.0",
                  adapter_license="Apache-2.0", base_license="CircleStone Labs Non-Commercial License",
                  base_license_url=BASE_LICENSE_URL, license_url=LICENSE_URL,
                  model_filename=MODEL_FILES[0][1], nodes_ready=False, core_supported=False,
                  minimum_comfyui_version=MIN_COMFY_VERSION, models=[], loader_names={}, error=None)
    names = ("UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "CLIPTextEncode",
             "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async def info(name):
                try:
                    async with session.get(f"{comfy.base_url}/object_info/{name}", headers=comfy.auth_headers()) as response:
                        response.raise_for_status()
                        payload = await response.json()
                        value = payload.get(name, {}) if isinstance(payload, dict) else {}
                        return value if isinstance(value, dict) else {}
                except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
                    return {}
            loaders = names[:4]
            schemas = dict(zip(loaders, await asyncio.gather(*(info(name) for name in loaders))))
            # Qwen/FLUX-only hosts need no Anima-specific node probes until the
            # distinct base model is advertised by their engine.
            if MODEL_FILES[0][1] in _choices(schemas["UNETLoader"], "unet_name"):
                schemas.update(zip(names[4:], await asyncio.gather(*(info(name) for name in names[4:]))))
            try:
                async with session.get(f"{comfy.base_url}/system_stats", headers=comfy.auth_headers()) as response:
                    response.raise_for_status()
                    stats = await response.json()
                    version = stats.get("system", {}).get("comfyui_version", "")
                    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[-+])", version) if isinstance(version, str) else None
                    result["comfyui_version"] = version if isinstance(version, str) else None
                    result["core_supported"] = bool(match and tuple(map(int, match.groups())) >= (0, 42, 0))
            except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, AttributeError):
                pass
        result["nodes_ready"] = compatible_nodes(schemas)
        for model, loader, field in zip(MODEL_FILES, names[:4], ("unet_name", "clip_name", "vae_name", "lora_name")):
            native = next((value for value in _choices(schemas[loader], field) if _path_name(value) == model[1]), None)
            found = native is not None
            if comfy.mode == "local":
                path = Path(comfy.comfy_dir) / "models" / model[0] / model[1]
                found = found and path.is_file() and path.stat().st_size == model[2]
            result["models"].append({"filename": model[1], "installed": bool(found), "size": model[2]})
            if found:
                result["loader_names"][field] = native
        models_ready = all(item["installed"] for item in result["models"])
        result["ready"] = result["core_supported"] and result["nodes_ready"] and models_ready
        result["mode_ready"]["generate"] = result["ready"]
        if not models_ready:
            result["error"] = "Anima RGBA 模型尚未齊全，請在 GPU 主機執行 setup_anima_rgba.bat；遠端使用者不需下載。"
        elif not result["core_supported"]:
            result["error"] = "Anima RGBA 需要可確認的 ComfyUI 0.42.0 以上核心以載入 Anima 與四通道 VAE；請主機管理者檢查版本。"
        elif not result["nodes_ready"]:
            result["error"] = "Anima RGBA 需要相容的 Anima、RGBA VAE 與 er_sde 採樣節點；請主機管理者檢查引擎（本版驗證 ComfyUI 0.42.0）。"
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, OSError):
        result["error"] = "暫時無法檢查 Anima RGBA；請確認 ComfyUI 已啟動或等待目前運算完成後重試。"
    result["mode_errors"]["edit"] = "Anima RGBA 本版只支援文字生圖；參考圖編輯請切換 Qwen 或 FLUX 3。"
    if not result["ready"]:
        result["mode_errors"]["generate"] = result["error"]
    return result
