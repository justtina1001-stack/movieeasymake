"""Qwen-Image-2.1 jobs on the existing local or shared ComfyUI engine."""
from __future__ import annotations

import asyncio
import json
import math
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image

from domain import RequestError
from queue_cancel import QueueCancelError
from qwen_image_models import MODEL_FILES, LICENSE_URL

ACTIVE = {"queued", "preparing", "running"}
MAX_SEED = 2**53 - 1


def is_image_lora(name):
    """Only expose the engine's explicitly separated 2.1 adapter collection."""
    if not isinstance(name, str):
        return False
    parts = name.replace("\\", "/").split("/")
    return (len(parts) >= 2 and parts[0] == "qwen_image_2_1"
            and all(p and p not in (".", "..") and ":" not in p for p in parts)
            and name.lower().endswith(".safetensors"))


def now():
    return datetime.now(timezone.utc).isoformat()


def compile_image_request(payload):
    if not isinstance(payload, dict):
        raise RequestError("圖片設定格式錯誤。")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise RequestError("請填寫圖片描述，最多 8,000 字元。")
    mode = payload.get("mode", "generate")
    if mode not in ("generate", "edit"):
        raise RequestError("圖片模式必須是文字生圖或參考圖編輯。")
    refs = payload.get("image_asset_ids", [])
    if not isinstance(refs, list) or len(refs) > 10 or any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{32}", x) for x in refs):
        raise RequestError("參考圖片最多 10 張，且必須先上傳。")
    if mode == "edit" and not refs:
        raise RequestError("圖片編輯至少需要一張參考圖片。")
    if mode == "generate" and refs:
        raise RequestError("使用參考圖片時，請選擇參考圖編輯模式。")
    def integer(key, default, lower, upper):
        value = payload.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise RequestError(f"{key} 必須是 {lower}～{upper} 的整數。")
        return value
    width, height = integer("width", 1024, 256, 2048), integer("height", 1024, 256, 2048)
    if width % 32 or height % 32:
        raise RequestError("圖片寬高必須是 32 的倍數。")
    steps = integer("steps", 25, 1, 50)
    resolution = integer("reference_resolution", 1024, 256, 2048)
    if resolution % 32:
        raise RequestError("參考圖處理解析度必須是 32 的倍數。")
    for flag in ("seed_auto", "transparent"):
        if flag in payload and not isinstance(payload[flag], bool):
            raise RequestError(f"{flag} 必須為布林值。")
    seed = secrets.randbelow(MAX_SEED + 1) if payload.get("seed_auto", True) else integer("seed", 42, 0, MAX_SEED)
    name = str(payload.get("name") or "Qwen 圖片").strip()
    if len(name) > 80:
        raise RequestError("圖片名稱最多 80 字。")
    text = prompt.strip()
    lora = payload.get("lora_name", "")
    if not isinstance(lora, str) or (lora and not is_image_lora(lora)):
        raise RequestError("請選擇 Qwen-Image-2.1 專用資料夾中的 LoRA。")
    strength = payload.get("lora_strength", 1.0)
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1.5:
        raise RequestError("LoRA 強度必須介於 0～1.5。")
    if payload.get("transparent", False):
        text = f"This is an RGBA format image with transparency. {text}\nThe image has an alpha channel and a transparent background."
    return dict(name=name, prompt=text, original_prompt=prompt, mode=mode, image_asset_ids=refs,
                width=width, height=height, steps=steps, seed=seed,
                transparent=payload.get("transparent", False), reference_resolution=resolution,
                lora_name=lora, lora_strength=strength)


def build_image_workflow(compiled, uploaded, job_id):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    workflow = {
        "1": node("UNETLoader", unet_name=MODEL_FILES[0][1], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=MODEL_FILES[1][1], type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name=MODEL_FILES[2][1]),
        "4": node("TextEncodeQwenImage21", clip=["2", 0], vae=["3", 0], prompt=compiled["prompt"],
                  negative_prompt="", resolution=compiled["reference_resolution"]),
        "5": node("EmptyLatentImage", width=compiled["width"], height=compiled["height"], batch_size=1),
        "6": node("KSampler", model=["1", 0], positive=["4", 0], negative=["4", 1],
                  latent_image=["4", 2] if compiled["mode"] == "edit" else ["5", 0],
                  seed=compiled["seed"], steps=compiled["steps"], cfg=1.0,
                  sampler_name="euler", scheduler="simple", denoise=1.0),
        "7": node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": node("SaveImage", images=["7", 0], filename_prefix=f"H3Studio/QwenImage21/{job_id}"),
    }
    if compiled.get("lora_name") and compiled.get("lora_strength", 1) > 0:
        workflow["9"] = node("LoraLoaderModelOnly", model=["1", 0],
                             lora_name=compiled["lora_name"], strength_model=compiled["lora_strength"])
        workflow["6"]["inputs"]["model"] = ["9", 0]
    for index, asset_id in enumerate(compiled["image_asset_ids"], 1):
        load, alpha = str(10 + index * 2), str(11 + index * 2)
        workflow[load] = node("LoadImage", image=uploaded[asset_id])
        workflow[alpha] = node("JoinImageWithAlpha", image=[load, 0], alpha=[load, 1])
        workflow["4"]["inputs"][f"images.image_{index}"] = [alpha, 0]
    return workflow


async def image_capabilities(comfy):
    result = {"ready": False, "nodes_ready": False, "models": [], "license_url": LICENSE_URL,
              "mode": comfy.mode, "error": None, "lora_supported": False, "loras": []}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async def info(name):
                async with session.get(f"{comfy.base_url}/object_info/{name}", headers=comfy.auth_headers()) as response:
                    response.raise_for_status()
                    return (await response.json()).get(name, {})
            encode, unet, clip, vae = await asyncio.gather(*(info(n) for n in (
                "TextEncodeQwenImage21", "UNETLoader", "CLIPLoader", "VAELoader")))
            result["nodes_ready"] = bool(encode)
            for model, schema, field in zip(MODEL_FILES, (unet, clip, vae), ("unet_name", "clip_name", "vae_name")):
                choices = schema.get("input", {}).get("required", {}).get(field, [[]])[0]
                found = model[1] in choices
                if comfy.mode == "local":
                    path = comfy.comfy_dir / "models" / model[0] / model[1]
                    found = found and path.is_file() and path.stat().st_size == model[2]
                result["models"].append({"filename": model[1], "installed": found, "size": model[2]})
            result["ready"] = result["nodes_ready"] and all(m["installed"] for m in result["models"])
            if not result["nodes_ready"]:
                result["error"] = "引擎尚未載入 Qwen-Image-2.1 節點，請主機管理者更新至支援此模型的 ComfyUI（本版驗證 v0.37.0）並重啟。"
            elif not result["ready"]:
                result["error"] = "模型尚未齊全，請在 GPU 主機執行 setup_qwen_image.bat。遠端使用者不需下載模型。"
            # Optional adapters must never prevent base-model generation.
            try:
                lora = await info("LoraLoaderModelOnly")
                result["lora_supported"] = bool(lora)
                choices = lora.get("input", {}).get("required", {}).get("lora_name", [[]])[0]
                result["loras"] = sorted(n for n in choices if is_image_lora(n))
            except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
                pass
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, OSError):
        result["error"] = "暫時無法檢查引擎；請確認 ComfyUI 已啟動或等待目前運算完成後重試。"
    return result


class ImageJobManager:
    def __init__(self, comfy, data_dir, gpu_lock, assets):
        self.comfy, self.gpu_lock, self.assets = comfy, gpu_lock, assets
        self.job_dir, self.output_dir = data_dir / "image_jobs", data_dir / "image_outputs"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jobs, self.tasks, self.cancel_events = {}, {}, {}
        for path in self.job_dir.glob("*.json"):
            if path.name.endswith(".workflow.json"):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[a-f0-9]{32}", job["id"]):
                    continue
                if job.get("status") in ACTIVE and not job.get("prompt_id"):
                    job.update(status="interrupted", error="Studio 重啟前尚未送出；可依原設定重新生成。")
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def update(self, job_id, **changes):
        job = self.jobs[job_id]
        job.update(changes, updated_at=now())
        path = self.job_dir / f"{job_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def create(self, payload):
        compiled = compile_image_request(payload)
        for asset_id in compiled["image_asset_ids"]:
            path = self.assets.path_for(asset_id)
            try:
                with Image.open(path) as image:
                    if image.width * image.height > 40_000_000:
                        raise RequestError("參考圖過大，請先縮小到 4,000 萬畫素以內。")
                    image.verify()
            except (OSError, ValueError) as error:
                raise RequestError("參考素材必須是有效圖片。") from error
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = {**compiled, "id": job_id, "type": "qwen_image", "status": "queued",
                             "progress": 0, "prompt_id": None, "error": None, "created_at": now()}
        self.update(job_id)
        self.cancel_events[job_id] = asyncio.Event()
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        return self.jobs[job_id]

    async def _complete(self, job_id, history):
        # Accept only the final SaveImage node, never previews or input references.
        candidates = history.get("outputs", {}).get("8", {}).get("images", [])
        output = next((o for o in candidates if o.get("type") == "output" and str(o.get("filename", "")).lower().endswith(".png")), None)
        if not output:
            raise RequestError("引擎完成但未回傳最終 PNG。")
        self.update(job_id, status="preparing", current_node="下載完成圖片", output=output)
        content, _ = await self.comfy.fetch_output(output)
        path = self.output_dir / f"{job_id}.png"
        temporary = path.with_suffix(".tmp")
        await asyncio.to_thread(temporary.write_bytes, content)
        with Image.open(temporary) as image:
            width, height = image.size
            rgba = image.mode == "RGBA"
            image.verify()
        temporary.replace(path)
        self.update(job_id, status="completed", progress=100, current_node=None, error=None,
                    local_output=path.name, width=width, height=height, rgba=rgba, finished_at=now())

    async def _run(self, job_id):
        job = self.jobs[job_id]
        event = self.cancel_events[job_id]
        try:
            async with self.gpu_lock:
                if event.is_set():
                    raise asyncio.CancelledError
                self.update(job_id, status="preparing", current_node="檢查圖片模型")
                await self.comfy.ensure_running()
                status = await image_capabilities(self.comfy)
                if not status["ready"]:
                    raise RequestError(status["error"])
                if job.get("lora_name") and job.get("lora_strength", 1) > 0:
                    if not status.get("lora_supported") or job["lora_name"] not in status.get("loras", []):
                        raise RequestError("所選 LoRA 未在目前引擎提供；請重新檢查模型，或選擇不使用 LoRA。")
                uploaded = {}
                for asset_id in job["image_asset_ids"]:
                    uploaded[asset_id] = await self.comfy.upload_asset(self.assets.path_for(asset_id), f"h3studio/{job_id}")
                if event.is_set():
                    raise asyncio.CancelledError
                workflow = build_image_workflow(job, uploaded, job_id)
                (self.job_dir / f"{job_id}.workflow.json").write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
                self.update(job_id, status="running", current_node="送出圖片工作", generation_started_at=now())
                async def callback(update):
                    # Binary previews cannot be stored as job JSON.
                    changes = {k: v for k, v in update.items() if k in {"prompt_id", "progress", "status", "current_node"}}
                    if changes:
                        self.update(job_id, **changes)
                _, history = await self.comfy.run_prompt(workflow, callback, event)
                if event.is_set():
                    raise asyncio.CancelledError
                await self._complete(job_id, history)
        except asyncio.CancelledError:
            self.update(job_id, status="cancelled" if event.is_set() else "interrupted", current_node=None)
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=str(error))

    async def recover(self, job_id):
        # Recover the existing engine prompt; never submit a duplicate after restart.
        job = self.jobs[job_id]
        misses = 0
        try:
            async with self.gpu_lock:
                self.update(job_id, status="running", current_node="重新連線既有圖片工作")
                while True:
                    history = await self.comfy.get_history(job["prompt_id"])
                    state = self.comfy.history_state(history)
                    if state == "success":
                        await self._complete(job_id, history)
                        return
                    if state == "error":
                        raise RequestError(self.comfy.history_error(history))
                    queue = await self.comfy.queue_status()
                    if queue["available"]:
                        found = any(row["prompt_id"] == job["prompt_id"] for row in queue["running"] + queue["pending"])
                        misses = 0 if found else misses + 1
                        if misses >= 3:
                            self.update(job_id, status="interrupted", error="引擎已無此圖片工作或歷史，可依原設定重新生成。")
                            return
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            if job["status"] != "cancelled":
                self.update(job_id, status="interrupted")
        except Exception as error:
            self.update(job_id, status="failed", error=str(error))

    async def cancel(self, job_id):
        job = self.jobs[job_id]
        if job["status"] not in ACTIVE:
            return job
        if job.get("prompt_id"):
            cancelled = await self.comfy.interrupt(job["prompt_id"])
            if not cancelled:
                raise RequestError("工作可能已完成，請重新整理確認結果。")
        self.cancel_events.setdefault(job_id, asyncio.Event()).set()
        if job.get("prompt_id") or job["status"] == "queued":
            task = self.tasks.get(job_id)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.update(job_id, status="cancelled", current_node=None)
        return job

    def output_path(self, job_id):
        if job_id not in self.jobs or self.jobs[job_id]["status"] != "completed":
            raise RequestError("圖片尚未完成。")
        path = self.output_dir / f"{job_id}.png"
        if not path.is_file():
            raise RequestError("找不到圖片檔案。")
        return path


def register_image_routes(app, static_dir, data_dir):
    manager = ImageJobManager(app["comfy"], data_dir, app["jobs"].gpu_lock, app["assets"])
    app["image_jobs"] = manager

    @web.middleware
    async def errors(request, handler):
        if not request.path.startswith("/api/images/"):
            return await handler(request)
        try:
            return await handler(request)
        except QueueCancelError as error:
            return web.json_response({"error": str(error)}, status=error.status)
        except (RequestError, ValueError, OSError, TypeError) as error:
            return web.json_response({"error": str(error)}, status=400)
        except KeyError:
            return web.json_response({"error": "找不到圖片工作。"}, status=404)
    app.middlewares.append(errors)

    async def page(request):
        return web.FileResponse(static_dir / "images.html")
    async def status(request):
        return web.json_response(await image_capabilities(manager.comfy), headers={"Cache-Control": "no-store"})
    async def create(request):
        payload = await request.json()
        return web.json_response(manager.create(payload), status=202)
    async def listing(request):
        page_number = max(1, int(request.query.get("page", 1)))
        records = sorted(manager.jobs.values(), key=lambda j: j["created_at"], reverse=True)
        pages = max(1, math.ceil(len(records) / 12))
        page_number = min(page_number, pages)
        return web.json_response({"items": records[(page_number - 1)*12:page_number*12], "page": page_number,
                                  "total_pages": pages, "total": len(records)}, headers={"Cache-Control": "no-store"})
    async def cancel(request):
        return web.json_response(await manager.cancel(request.match_info["job_id"]))
    async def output(request):
        path = manager.output_path(request.match_info["job_id"])
        headers = {"Content-Disposition": f'attachment; filename="{path.name}"'} if request.query.get("download") == "1" else {}
        return web.FileResponse(path, headers=headers)
    async def use_reference(request):
        path = manager.output_path(request.match_info["job_id"])
        def save():
            with Image.open(path) as image:
                return manager.assets.save_image(image, "Qwen-reference.png", "qwen-image-reference")
        asset = await asyncio.to_thread(save)
        return web.json_response({**asset, "url": f"/api/assets/{asset['id']}"})
    async def startup(_):
        for job_id, job in manager.jobs.items():
            if job.get("prompt_id") and job["status"] in ACTIVE | {"interrupted"}:
                manager.tasks[job_id] = asyncio.create_task(manager.recover(job_id))
    async def shutdown(_):
        tasks = [t for t in manager.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    app.router.add_get("/images", page)
    app.router.add_get("/api/images/status", status)
    app.router.add_get("/api/images/jobs", listing)
    app.router.add_post("/api/images/jobs", create)
    app.router.add_post("/api/images/jobs/{job_id}/cancel", cancel)
    app.router.add_get("/api/images/jobs/{job_id}/image", output)
    app.router.add_post("/api/images/jobs/{job_id}/reference", use_reference)
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)
    return manager
