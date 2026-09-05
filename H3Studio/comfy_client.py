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

from domain import TURBO_LORA_CANDIDATES

from settings import ConnectionSettings
from runtime_env import inspect_python_candidates


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ComfyClient:
    def __init__(self, settings: ConnectionSettings, data_dir: Path):
        self.mode = settings.mode
        self.comfy_dir = Path(settings.comfy_dir)
        self.data_dir = data_dir
        self.base_url = settings.base_url.rstrip("/")
        self.auto_start_local = settings.auto_start_local
        self.remote_access_token = settings.remote_access_token
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.start_lock = asyncio.Lock()
        self._model_cache: tuple[float, dict[str, bool]] | None = None

    def configure(self, settings: ConnectionSettings) -> None:
        self.mode = settings.mode
        self.comfy_dir = Path(settings.comfy_dir)
        self.base_url = settings.base_url.rstrip("/")
        self.auto_start_local = settings.auto_start_local
        self.remote_access_token = settings.remote_access_token
        self._model_cache = None

    @property
    def can_start(self) -> bool:
        return self.mode == "local" and self.auto_start_local

    @property
    def is_starting(self) -> bool:
        return self.start_lock.locked()

    def auth_headers(self, prompt_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.mode == "remote" and self.remote_access_token:
            headers["Authorization"] = f"Bearer {self.remote_access_token}"
        if prompt_id:
            headers["X-H3-Prompt-ID"] = prompt_id
        return headers

    async def is_ready(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/system_stats", headers=self.auth_headers()) as response:
                    return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def system_stats(self) -> dict[str, Any] | None:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/system_stats", headers=self.auth_headers()) as response:
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
            environment = await asyncio.to_thread(
                inspect_python_candidates,
                python_candidates,
                ("torch",),
            )
            python = Path(str(environment["executable"])) if environment["ready"] else None
            if python is None or not (self.comfy_dir / "main.py").exists():
                if (self.comfy_dir / "main.py").exists() and any(candidate.is_file() for candidate in python_candidates):
                    raise RuntimeError(
                        "ComfyUI 的 Python 環境來自另一台電腦，目前無法執行。"
                        "請在引擎設定中執行本機引擎修復；模型與影片不會被刪除。"
                    )
                raise RuntimeError("找不到 ComfyUI 主程式或 Python 環境，請在引擎設定中確認本機 ComfyUI 資料夾。")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.log_handle = (self.data_dir / "comfyui.log").open("a", encoding="utf-8")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(
                [
                    str(python), "main.py", "--lowvram", "--reserve-vram", "1.5",
                    "--preview-method", "taesd", "--listen", "127.0.0.1",
                ],
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
                    async with session.get(f"{self.base_url}/object_info/{node}", headers=self.auth_headers()) as response:
                        if response.status != 200:
                            continue
                        payload = await response.json()
                    values = payload.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])[0]
                    result[name] = filename in values
                async with session.get(f"{self.base_url}/object_info/LoraLoaderModelOnly", headers=self.auth_headers()) as response:
                    if response.status == 200:
                        payload = await response.json()
                        available_loras = payload.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [[]])[0]
                        for profile, candidates in TURBO_LORA_CANDIDATES.items():
                            result[f"turbo_{profile}"] = any(candidate in available_loras for candidate in candidates)
                optimizer_nodes = []
                for node in ("H3MemoryOptimization", "H3SparseAttention"):
                    async with session.get(f"{self.base_url}/object_info/{node}", headers=self.auth_headers()) as response:
                        optimizer_nodes.append(response.status == 200)
                result["h3_optimizations"] = all(optimizer_nodes)
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
            pass
        self._model_cache = (time.monotonic() + 60, dict(result))
        return result

    async def list_loras(self) -> list[str]:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.base_url}/object_info/LoraLoaderModelOnly", headers=self.auth_headers()) as response:
                if response.status != 200:
                    raise RuntimeError("讀取 LoRA 清單失敗，請確認引擎連線與遠端金鑰。")
                payload = await response.json()
        values = payload.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [[]])[0]
        return sorted({name for name in values
                       if isinstance(name, str) and name.lower().endswith(".safetensors")})

    async def resolve_turbo_lora(self, profile: str | None) -> str | None:
        if not profile or profile not in TURBO_LORA_CANDIDATES:
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/object_info/LoraLoaderModelOnly", headers=self.auth_headers()) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
            values = payload.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [[]])[0]
            return next((candidate for candidate in TURBO_LORA_CANDIDATES[profile] if candidate in values), None)
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
            return None

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
                async with session.post(f"{self.base_url}/upload/image", data=form, headers=self.auth_headers()) as response:
                    payload = await response.json()
                    if response.status != 200:
                        raise RuntimeError(f"素材上傳到 ComfyUI 失敗：{payload}")
        return f"{payload.get('subfolder')}/{payload['name']}".replace("\\", "/").lstrip("/")

    @staticmethod
    def history_state(history: dict[str, Any]) -> str:
        status = history.get("status") or {}
        if not status.get("completed"):
            return "running"
        return "success" if status.get("status_str") == "success" else "error"

    @staticmethod
    def history_error(history: dict[str, Any]) -> str:
        status = history.get("status") or {}
        for message in reversed(status.get("messages") or []):
            if not isinstance(message, list) or len(message) < 2 or message[0] != "execution_error":
                continue
            data = message[1] if isinstance(message[1], dict) else {}
            return str(data.get("exception_message") or data.get("exception_type") or "ComfyUI 執行失敗。")
        return "ComfyUI 執行失敗。"

    async def get_history(
        self,
        prompt_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        try:
            try:
                async with session.get(f"{self.base_url}/history/{prompt_id}", headers=self.auth_headers()) as response:
                    if response.status != 200:
                        return {}
                    payload = await response.json()
                if not isinstance(payload, dict):
                    return {}
                entry = payload.get(prompt_id, payload)
                return entry if isinstance(entry, dict) else {}
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, ValueError):
                return {}
        finally:
            if owns_session:
                await session.close()

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
            async with session.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                headers=self.auth_headers(),
            ) as response:
                result = await response.json()
                if response.status != 200:
                    raise RuntimeError(result.get("error", {}).get("message") or str(result))
                prompt_id = result["prompt_id"]
            await callback({"prompt_id": prompt_id, "status": "running", "progress": 0})

            reconnect_delay = 1.0
            while True:
                if cancel_event.is_set():
                    await session.post(f"{self.base_url}/interrupt", headers=self.auth_headers(prompt_id))
                    raise asyncio.CancelledError

                history = await self.get_history(prompt_id, session)
                state = self.history_state(history)
                if state == "success":
                    return prompt_id, history
                if state == "error":
                    raise RuntimeError(self.history_error(history))

                try:
                    async with session.ws_connect(
                        f"{ws_url}/ws?clientId={client_id}", heartbeat=30, headers=self.auth_headers()
                    ) as ws:
                        reconnect_delay = 1.0
                        last_history_check = time.monotonic()
                        while True:
                            if cancel_event.is_set():
                                await session.post(f"{self.base_url}/interrupt", headers=self.auth_headers(prompt_id))
                                raise asyncio.CancelledError
                            try:
                                message = await asyncio.wait_for(ws.receive(), timeout=2)
                            except asyncio.TimeoutError:
                                if time.monotonic() - last_history_check < 5:
                                    continue
                                last_history_check = time.monotonic()
                                history = await self.get_history(prompt_id, session)
                                state = self.history_state(history)
                                if state == "success":
                                    return prompt_id, history
                                if state == "error":
                                    raise RuntimeError(self.history_error(history))
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
                                    await callback({"status": "running", "current_node": data.get("node")})
                                elif event.get("type") == "execution_error":
                                    raise RuntimeError(data.get("exception_message") or "ComfyUI 執行失敗。")
                            elif message.type == aiohttp.WSMsgType.BINARY:
                                preview = self.decode_preview_message(message.data)
                                if preview:
                                    image, content_type = preview
                                    await callback({"preview_bytes": image, "preview_mime": content_type})
                            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    pass

                history = await self.get_history(prompt_id, session)
                state = self.history_state(history)
                if state == "success":
                    return prompt_id, history
                if state == "error":
                    raise RuntimeError(self.history_error(history))
                await callback({"status": "running", "current_node": "進度連線中斷，正在自動重連"})
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(10.0, reconnect_delay * 2)

    @staticmethod
    def decode_preview_message(data: bytes) -> tuple[bytes, str] | None:
        if len(data) < 8:
            return None
        event_type = int.from_bytes(data[:4], "big")
        if event_type == 1:
            image_type = int.from_bytes(data[4:8], "big")
            content_type = "image/png" if image_type == 2 else "image/jpeg"
            return data[8:], content_type
        if event_type == 4:
            metadata_length = int.from_bytes(data[4:8], "big")
            image_start = 8 + metadata_length
            if metadata_length < 2 or image_start >= len(data):
                return None
            try:
                metadata = json.loads(data[8:image_start].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            return data[image_start:], str(metadata.get("image_type") or "image/jpeg")
        return None

    async def interrupt(self, prompt_id: str | None = None) -> None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                await session.post(f"{self.base_url}/interrupt", headers=self.auth_headers(prompt_id))
        except aiohttp.ClientError:
            return

    async def fetch_output(self, output: dict[str, str]) -> tuple[bytes, str]:
        query = urlencode({
            "filename": output["filename"],
            "subfolder": output.get("subfolder", ""),
            "type": output.get("type", "output"),
        })
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            async with session.get(f"{self.base_url}/view?{query}", headers=self.auth_headers()) as response:
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
