"""Bounded Real-ESRGAN upscaling using built-in ComfyUI nodes."""
import asyncio
import aiohttp
from domain import RequestError

MODELS = {
    "general": {"label": "一般圖片／立體角色", "filename": "RealESRGAN_x4plus.pth", "tag": "v0.1.0",
                "size": 67040989, "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"},
    "anime": {"label": "動漫／平面插畫", "filename": "RealESRGAN_x4plus_anime_6B.pth", "tag": "v0.2.2.4",
              "size": 17938799, "sha256": "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da"},
}
MAX_SIDE = 4096
MAX_INPUT_PIXELS = 4_194_304


def compile_upscale(payload, size):
    if not isinstance(payload, dict):
        raise RequestError("放大設定格式錯誤。")
    factor = payload.get("scale", 2)
    model = payload.get("model", "general")
    if type(factor) is not int or factor not in (2, 4):
        raise RequestError("放大倍率只能選擇 2 或 4。")
    if not isinstance(model, str) or model not in MODELS:
        raise RequestError("請選擇一般圖片或動漫／插畫放大模型。")
    width, height = size
    if width * height > MAX_INPUT_PIXELS or max(width, height) * factor > MAX_SIDE:
        raise RequestError("放大輸入最多 419 萬畫素，輸出任一邊最多 4096 像素；請降低倍率或使用較小的來源圖片。")
    return {"scale": factor, "upscale_model": model, "width": width * factor, "height": height * factor,
            "source_width": width, "source_height": height}


def build_upscale_workflow(job, uploaded, job_id):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    return {
        "1": node("LoadImage", image=uploaded[job["image_asset_ids"][0]]),
        "2": node("UpscaleModelLoader", model_name=MODELS[job["upscale_model"]]["filename"]),
        "3": node("ImageUpscaleWithModel", upscale_model=["2", 0], image=["1", 0]),
        # Both models produce 4x internally. The 2x option downsamples that result.
        "4": node("ImageScale", image=["3", 0], upscale_method="lanczos",
                  width=job["width"], height=job["height"], crop="disabled"),
        # LoadImage returns an inverted alpha mask. Join resizes and inverts it
        # once, preserving transparency without passing alpha through ESRGAN.
        "7": node("JoinImageWithAlpha", image=["4", 0], alpha=["1", 1]),
        "8": node("SaveImage", images=["7", 0], filename_prefix=f"H3Studio/Upscale/{job_id}"),
    }


async def upscale_capabilities(comfy):
    result = {"ready": False, "models": [], "error": None, "max_side": MAX_SIDE}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async def info(name):
                async with session.get(f"{comfy.base_url}/object_info/{name}", headers=comfy.auth_headers()) as response:
                    response.raise_for_status()
                    return (await response.json()).get(name, {})
            names = ("UpscaleModelLoader", "ImageUpscaleWithModel", "ImageScale", "JoinImageWithAlpha", "LoadImage", "SaveImage")
            schemas = await asyncio.gather(*(info(name) for name in names))
            field = schemas[0].get("input", {}).get("required", {}).get("model_name", [[]])
            choices = field[1].get("options", []) if field[0] == "COMBO" else field[0]
            result["models"] = [{"id":key, "label":m["label"], "installed":m["filename"] in choices} for key,m in MODELS.items()]
            result["ready"] = all(schemas) and any(m["installed"] for m in result["models"])
            if not all(schemas):
                result["error"] = "引擎缺少內建圖片放大節點，請更新 ComfyUI。"
            elif not result["ready"]:
                result["error"] = "請在 GPU 主機執行 setup_image_upscale.bat 安裝放大模型，再重新檢查。"
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError):
        result["error"] = "無法取得放大模型狀態，請檢查目前引擎連線。"
    return result
