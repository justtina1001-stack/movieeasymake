from __future__ import annotations

import asyncio
import json
import mimetypes
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import aiohttp

from settings import ConnectionSettings


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ComfyClient:
    def __init__(self, settings: ConnectionSettings, data_dir: Path):
        self.mode = settings.mode
        self.comfy_dir = Path(settings.comfy_dir)
        self.data_dir = data_dir
        self.base_url = settings.base_url.rstrip("/")
        self.auto_start_local = settings.auto_start_local
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.start_lock = asyncio.Lock()
        self._model_cache: tuple[float, dict[str, bool]] | None = None

    def configure(self, settings: ConnectionSettings) -> None:
        self.mode = settings.mode
        self.comfy_dir = Path(settings.comfy_dir)
        self.base_url = settings.base_url.rstrip("/")
        self.auto_start_local = settings.auto_start_local
        self._model_cache = None

    @property
    def can_start(self) -> bool:
        return self.mode == "local" and self.auto_start_local

    @property
    def is_starting(self) -> bool:
        return self.start_lock.locked()

    async def is_ready(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/system_stats") as response:
                    return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def system_stats(self) -> dict[str, Any] | None:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/system_stats") as response:
                    if response.status != 200:
                        return None
                    return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def ensure_running(self) -> None:
        async with self.start_lock:
            if await self.is_ready():
                return
            if self.mode == "remote":
                raise RuntimeError("無法連線遠端 ComfyUI，請確認主機已啟動、網址正確，且網路或 VPN 可以連線。")
            if not self.auto_start_local:
                raise RuntimeError("本機 ComfyUI 尚未啟動，而且自動啟動已關閉。")
            for _ in range(5):
                await asyncio.sleep(1)
                if await self.is_ready():
                    return
            python_candidates = [
                self.comfy_dir / ".venv" / "Scripts" / "python.exe",
                self.comfy_dir / "venv" / "Scripts" / "python.exe",
                self.comfy_dir.parent / "python_embeded" / "python.exe",
            ]
            python = next((candidate for candidate in python_candidates if candidate.exists()), None)
            if python is None or not (self.comfy_dir / "main.py").exists():
                raise RuntimeError("找不到 ComfyUI 主程式或 Python 環境，請在引擎設定中確認本機 ComfyUI 資料夾。")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.log_handle = (self.data_dir / "comfyui.log").open("a", encoding="utf-8")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(
                [str(python), "main.py", "--lowvram", "--reserve-vram", "1.5", "--listen", "127.0.0.1"],
                cwd=self.comfy_dir,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            for _ in range(120):
                if await self.is_ready():
                    return
                if self.process.poll() is not None:
                    if await self.is_ready():
                        return
                    raise RuntimeError("ComfyUI 啟動失敗，請查看 H3Studio/data/comfyui.log。")
                await asyncio.sleep(1)
            raise RuntimeError("等待 ComfyUI 啟動逾時。")

    async def model_inventory(self) -> dict[str, bool]:
        if self._model_cache and self._model_cache[0] > time.monotonic():
            return dict(self._model_cache[1])
        expected = {
            "fl2va": ("UNETLoader", "unet_name", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
            "ref2va": ("UNETLoader", "unet_name", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"),
            "text_encoder": ("CLIPLoader", "clip_name", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
            "video_vae": ("VAELoader", "vae_name", "minimax_h3_video_vae_fp16.safetensors"),
            "audio_vae": ("VAELoader", "vae_name", "minimax_h3_audio_vae_fp32.safetensors"),
        }
        result = {name: False for name in expected}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for name, (node, field, filename) in expected.items():
                    async with session.get(f"{self.base_url}/object_info/{node}") as response:
                        if response.status != 200:
                            continue
                        payload = await response.json()
                    values = payload.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])[0]
                    result[name] = filename in values
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
            pass
        self._model_cache = (time.monotonic() + 60, dict(result))
        return result

    async def upload_asset(self, path: Path, subfolder: str) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        timeout = aiohttp.ClientTimeout(total=1800)
        with path.open("rb") as handle:
            form = aiohttp.FormData()
            form.add_field("image", handle, filename=path.name, content_type=content_type)
            form.add_field("type", "input")
            form.add_field("subfolder", subfolder)
            form.add_field("overwrite", "true")
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.base_url}/upload/image", data=form) as response:
                    payload = await response.json()
                    if response.status != 200:
                        raise RuntimeError(f"素材上傳到 ComfyUI 失敗：{payload}")
        return f"{payload.get('subfolder')}/{payload['name']}".replace("\\", "/").lstrip("/")

    async def run_prompt(
        self,
        workflow: dict[str, Any],
        callback: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> tuple[str, dict[str, Any]]:
        client_id = uuid.uuid4().hex
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(f"{ws_url}/ws?clientId={client_id}", heartbeat=30) as ws:
                async with session.post(f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": client_id}) as response:
                    result = await response.json()
                    if response.status != 200:
                        raise RuntimeError(result.get("error", {}).get("message") or str(result))
                    prompt_id = result["prompt_id"]
                await callback({"prompt_id": prompt_id, "status": "running", "progress": 0})

                while True:
                    if cancel_event.is_set():
                        await session.post(f"{self.base_url}/interrupt")
                        raise asyncio.CancelledError
                    try:
                        message = await asyncio.wait_for(ws.receive(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(message.data)
                        data = event.get("data") or {}
                        if data.get("prompt_id") not in {None, prompt_id}:
                            continue
                        if event.get("type") == "progress":
                            maximum = max(1, int(data.get("max") or 1))
                            value = int(data.get("value") or 0)
                            await callback({"status": "running", "progress": round(value / maximum * 100, 1)})
                        elif event.get("type") == "executing":
                            node = data.get("node")
                            await callback({"status": "running", "current_node": node})
                            if node is None:
                                break
                        elif event.get("type") == "execution_error":
                            raise RuntimeError(data.get("exception_message") or "ComfyUI 執行失敗。")
                    elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        raise RuntimeError("與 ComfyUI 的進度連線中斷。")

                async with session.get(f"{self.base_url}/history/{prompt_id}") as response:
                    history = await response.json()
        return prompt_id, history.get(prompt_id, history)

    async def interrupt(self) -> None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                await session.post(f"{self.base_url}/interrupt")
        except aiohttp.ClientError:
            return

    async def fetch_output(self, output: dict[str, str]) -> tuple[bytes, str]:
        query = urlencode({
            "filename": output["filename"],
            "subfolder": output.get("subfolder", ""),
            "type": output.get("type", "output"),
        })
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            async with session.get(f"{self.base_url}/view?{query}") as response:
                if response.status != 200:
                    raise RuntimeError("無法讀取輸出影片。")
                return await response.read(), response.headers.get("Content-Type", "video/mp4")

    def output_path(self, output: dict[str, str]) -> Path:
        output_dir = (self.comfy_dir / "output").resolve()
        path = (output_dir / output.get("subfolder", "") / output["filename"]).resolve()
        if output_dir not in path.parents:
            raise RuntimeError("影片輸出路徑超出 ComfyUI output 資料夾。")
        if not path.exists():
            raise RuntimeError(f"找不到影片輸出：{path.name}")
        return path
