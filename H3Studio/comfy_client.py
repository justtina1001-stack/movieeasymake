from __future__ import annotations

import asyncio
import ast
import json
import mimetypes
import math
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import aiohttp

from domain import TURBO_LORA_CANDIDATES, VIDEO_VAE_FILENAMES
from video_models import (FASTH3_FILENAME, FASTH3_SIZE, VIDEO_MODEL_FAST, PIKA_LORA_FILENAMES,
                          PIKA_LORA_SIZE, REF_PATCH_FILENAME, REF_PATCH_SIZE, blackwell_device,
                          fasth3_node_support, input_choices, pika_node_support, ref_patch_node_support,
                          freevideo_node_support)

from settings import ConnectionSettings
from runtime_env import inspect_python_candidates
from queue_snapshot import fetch_queue_snapshot
from queue_cancel import cancel_queue_prompt
from engine_prompt_presence import EnginePromptPresenceGuard, ComfyJobMissingError


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
MODEL_INVENTORY_TIMEOUT = 7
MODEL_INVENTORY_CONCURRENCY = 4
PROMPT_PRESENCE_INTERVAL = 5.0
HISTORY_QUERY_TIMEOUT = 15.0


class ComfyExecutionError(RuntimeError):
    """An engine failure with its original message and failing node identity."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        details = details or {}
        self.node_id = details.get("node_id")
        self.node_type = details.get("node_type")
        self.exception_type = details.get("exception_type")


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
        self._model_inventory_task: asyncio.Task | None = None
        self._model_inventory_revision = 0

    def configure(self, settings: ConnectionSettings) -> None:
        self.mode = settings.mode
        self.comfy_dir = Path(settings.comfy_dir)
        self.base_url = settings.base_url.rstrip("/")
        self.auto_start_local = settings.auto_start_local
        self.remote_access_token = settings.remote_access_token
        self._model_cache = None
        self._model_inventory_revision += 1
        self._model_inventory_task = None

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

    async def queue_status(self) -> dict[str, Any]:
        return await fetch_queue_snapshot(self.base_url, headers=self.auth_headers())

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
                # On 16 GB hosts, release the external worker after each video
                # so switching back to native H3 does not retain its GPU cache.
                env={**os.environ, "H3_FACEREFINE_STRICT_PATHS": "1",
                     "FREEVIDEO_KEEP_MODELS": os.environ.get("FREEVIDEO_KEEP_MODELS", "0")},
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

    async def model_inventory(self, *, refresh: bool = False) -> dict[str, bool]:
        if not refresh and self._model_cache and self._model_cache[0] > time.monotonic():
            return dict(self._model_cache[1])
        task = self._model_inventory_task
        if task is None or task.done():
            task = asyncio.create_task(self._refresh_model_inventory(self._model_inventory_revision))
            self._model_inventory_task = task
            task.add_done_callback(self._finish_model_inventory)
        # A disconnected page must not cancel a refresh shared by other callers.
        return dict(await asyncio.shield(task))

    def _finish_model_inventory(self, task: asyncio.Task) -> None:
        if self._model_inventory_task is task:
            self._model_inventory_task = None
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every HTTP caller disconnected.

    async def _refresh_model_inventory(self, revision: int) -> dict[str, bool]:
        try:
            async with asyncio.timeout(MODEL_INVENTORY_TIMEOUT):
                result, cacheable = await self._fetch_model_inventory()
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as error:
            raise RuntimeError("暫時無法讀取引擎模型清單，請稍後重試；這不代表模型已移除。") from error
        if revision != self._model_inventory_revision:
            raise RuntimeError("引擎連線設定已變更，請重新讀取模型清單。")
        if cacheable:
            self._model_cache = (time.monotonic() + 60, dict(result))
        return result

    async def _fetch_model_inventory(self) -> tuple[dict[str, bool], bool]:
        expected = {
            "fl2va": ("UNETLoader", "unet_name", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
            "ref2va": ("UNETLoader", "unet_name", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"),
            "text_encoder": ("CLIPLoader", "clip_name", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
            **{f"video_vae_{precision}": ("VAELoader", "vae_name", filename)
               for precision, filename in VIDEO_VAE_FILENAMES.items()},
            "audio_vae": ("VAELoader", "vae_name", "minimax_h3_audio_vae_fp32.safetensors"),
            VIDEO_MODEL_FAST: ("UNETLoader", "unet_name", FASTH3_FILENAME),
            **{f"pika_{family}": ("LoraLoaderModelOnly", "lora_name", filename)
               for family, filename in PIKA_LORA_FILENAMES.items()},
        }
        result = {name: False for name in expected}
        result.update({f"turbo_{profile}": False for profile in TURBO_LORA_CANDIDATES})
        result.update({"h3_memory_optimization": False, "h3_optimizations": False,
                       "h3_sla_attention": False, "h3_sigma_shift": False,
                       "video_vae_int8_supported": False, "fasth3_blackwell": False,
                       "fasth3_core_supported": False, "fasth3_nodes_ready": False,
                       "pika_nodes_ready": False, "ref_patch_nodes_ready": False,
                       "freevideo_nodes_ready": False, "freevideo_runtime_ready": False})
        capability_nodes = (
            "H3MemoryOptimization", "H3SparseAttention", "BlockSparseAttention", "MiniMaxH3SigmaShift",
            "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "MiniMaxH3AddGuide",
            "ManualSigmas", "KSamplerSelect", "MiniMaxH3RefPatchLoader",
            "FreeVideoGenerate", "FreeVideoMedia", "SaveVideo",
        )
        nodes = tuple(dict.fromkeys([*(entry[0] for entry in expected.values()), *capability_nodes]))
        base_url, headers = self.base_url, self.auth_headers()
        semaphore = asyncio.Semaphore(MODEL_INVENTORY_CONCURRENCY)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=MODEL_INVENTORY_TIMEOUT)) as session:
            async def fetch_schema(node: str) -> dict[str, Any]:
                async with semaphore:
                    async with session.get(f"{base_url}/object_info/{node}", headers=headers) as response:
                        response.raise_for_status()
                        payload = await response.json()
                        if not isinstance(payload, dict) or not isinstance(payload.get(node, {}), dict):
                            raise ValueError("Invalid object_info response")
                        # Native ComfyUI returns HTTP 200 with {} for unknown nodes.
                        return payload.get(node, {})

            tasks = [asyncio.create_task(fetch_schema(node)) for node in nodes]
            try:
                schemas = dict(zip(nodes, await asyncio.gather(*tasks)))
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        for name, (node, field, filename) in expected.items():
            result[name] = filename in input_choices(schemas[node], field)
        available_loras = input_choices(schemas["LoraLoaderModelOnly"], "lora_name")
        for profile, candidates in TURBO_LORA_CANDIDATES.items():
            result[f"turbo_{profile}"] = any(candidate in available_loras for candidate in candidates)
        result["h3_memory_optimization"] = bool(schemas["H3MemoryOptimization"])
        result["h3_optimizations"] = result["h3_memory_optimization"] and bool(schemas["H3SparseAttention"])
        result["h3_sigma_shift"] = bool(schemas["MiniMaxH3SigmaShift"])
        result["fasth3_nodes_ready"] = fasth3_node_support(schemas)
        result["pika_nodes_ready"] = pika_node_support(schemas)
        result["ref_patch_nodes_ready"] = ref_patch_node_support(schemas)
        result["freevideo_nodes_ready"] = freevideo_node_support(schemas)
        if result["freevideo_nodes_ready"]:
            if self.mode == "local":
                try:
                    from freevideo_runtime import freevideo_runtime_status
                    runtime = await asyncio.to_thread(freevideo_runtime_status, self.comfy_dir)
                    result["freevideo_runtime_ready"] = isinstance(runtime, dict) and runtime.get("ready") is True
                    if not result["freevideo_runtime_ready"]:
                        result["freevideo_runtime_error"] = str(runtime.get("error") or "FreeVideo 獨立環境尚未完成。") if isinstance(runtime, dict) else "FreeVideo 獨立環境尚未完成。"
                except (ImportError, OSError, ValueError, TypeError, KeyError):
                    result["freevideo_runtime_error"] = "FreeVideo 獨立環境尚未完成，請在 GPU 主機執行 setup_freevideo.bat。"
            else:
                # Node presence alone does not prove the remote worker/models
                # exist. Read the author's setup readiness without caching its
                # settings, paths, credentials, or setup token in our inventory.
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as remote:
                        async with remote.get(f"{base_url}/freevideo/setup", headers=headers, allow_redirects=False) as response:
                            if response.status == 200:
                                runtime = await response.json()
                                result["freevideo_runtime_ready"] = isinstance(runtime, dict) and runtime.get("ready") is True
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
                    pass
                if not result["freevideo_runtime_ready"]:
                    result["freevideo_runtime_error"] = "尚未確認遠端主機的 FreeVideo 環境；請 GPU 主機管理者完成安裝與就緒檢查。"
        sparse_inputs = schemas["BlockSparseAttention"].get("input", {}).get("required", {})
        selection = sparse_inputs.get("selection", [])
        options = selection[1].get("options", []) if len(selection) > 1 and isinstance(selection[1], dict) else []
        result["h3_sla_attention"] = any(
            isinstance(option, dict) and option.get("key") == "sla"
            and "keep_percent" in option.get("inputs", {}).get("required", {})
            for option in options
        )
        stats = None
        try:
            stats = await self.system_stats()
            system = stats.get("system", {}) if isinstance(stats, dict) else {}
            version = system.get("comfyui_version") if isinstance(system, dict) else None
            match = re.fullmatch(
                r"v?(\d+)\.(\d+)\.(\d+)(?:dev\d*)?(?:[.+-][A-Za-z0-9][A-Za-z0-9.+-]*)?",
                version.strip(),
            ) if isinstance(version, str) else None
            # 0.37.0 is our tested baseline, not the earliest upstream support.
            result["video_vae_int8_supported"] = bool(
                match and tuple(map(int, match.group(1, 2, 3))) >= (0, 37, 0)
            )
            result["fasth3_blackwell"] = blackwell_device(stats)
            result["fasth3_core_supported"] = bool(match and tuple(map(int, match.group(1, 2, 3))) >= (0, 42, 0))
            if self.mode == "local":
                patch = self.comfy_dir / "models" / "model_patches" / REF_PATCH_FILENAME
                result["ref_patch_nodes_ready"] = result["ref_patch_nodes_ready"] and patch.is_file() and patch.stat().st_size == REF_PATCH_SIZE
                for family, filename in PIKA_LORA_FILENAMES.items():
                    adapter = self.comfy_dir / "models" / "loras" / filename
                    result[f"pika_{family}"] = result[f"pika_{family}"] and adapter.is_file() and adapter.stat().st_size == PIKA_LORA_SIZE
                path = self.comfy_dir / "models" / "diffusion_models" / FASTH3_FILENAME
                result[VIDEO_MODEL_FAST] = result[VIDEO_MODEL_FAST] and path.is_file() and path.stat().st_size == FASTH3_SIZE
                # Full PDD checkpoints need the output-head constructor added in
                # 0.42, rather than the older LoRA-only head-bank implementation.
                tree = ast.parse((self.comfy_dir / "comfy/ldm/minimax/model.py").read_text(encoding="utf-8"))
                layer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FinalLayer")
                constructor = next(node for node in layer.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
                result["fasth3_core_supported"] = result["fasth3_core_supported"] and any(arg.arg == "pdd_steps" for arg in constructor.args.args)
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError, OSError, StopIteration, SyntaxError):
            result["fasth3_core_supported"] = False
            pass
        result["video_vae"] = result["video_vae_fp16"] or (
            result["video_vae_int8"] and result["video_vae_int8_supported"]
        )
        return result, isinstance(stats, dict)

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
        # ComfyUI records failures as completed=false. A lost websocket must not
        # turn a terminal error into an indefinitely running Studio job.
        if status.get("status_str") == "error":
            return "error"
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

    @classmethod
    def history_exception(cls, history: dict[str, Any]) -> ComfyExecutionError:
        status = history.get("status") or {}
        for message in reversed(status.get("messages") or []):
            if (isinstance(message, list) and len(message) >= 2
                    and message[0] == "execution_error" and isinstance(message[1], dict)):
                return ComfyExecutionError(cls.history_error(history), message[1])
        return ComfyExecutionError(cls.history_error(history))

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

    async def get_history_snapshot(
        self, prompt_id: str, session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        """Distinguish a reliable empty history from an unavailable response."""
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HISTORY_QUERY_TIMEOUT))
        unavailable = {"available": False, "history": {}}
        try:
            try:
                async with session.get(f"{self.base_url}/history/{prompt_id}", headers=self.auth_headers(),
                                       timeout=aiohttp.ClientTimeout(total=HISTORY_QUERY_TIMEOUT),
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        return unavailable
                    payload = await response.json()
                if not isinstance(payload, dict):
                    return unavailable
                if prompt_id in payload:
                    history = payload[prompt_id]
                elif not payload:
                    history = {}
                elif "status" in payload or "outputs" in payload:
                    history = payload
                else:
                    return unavailable
                if not isinstance(history, dict):
                    return unavailable
                if history and not any(isinstance(history.get(key), dict) for key in ("status", "outputs")):
                    return unavailable
                if any(key in history and not isinstance(history[key], dict) for key in ("status", "outputs")):
                    return unavailable
                return {"available": True, "history": history}
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
                return unavailable
        finally:
            if owns_session:
                await session.close()

    async def _checked_prompt_history(self, prompt_id, guard, callback, session):
        snapshot = await self.get_history_snapshot(prompt_id, session)
        history = snapshot["history"]
        if self.history_state(history) in {"success", "error"}:
            guard.mark_present()
            await callback(guard.fields())
            return history
        queue = await self.queue_status() if snapshot["available"] and not history else None
        guard.observe(snapshot, queue)
        fields = guard.fields()
        if guard.state in {"monitoring", "unknown"}:
            fields["recovery_started_at"] = getattr(guard, "recovery_started_at", None)
            if fields["recovery_started_at"] is None:
                fields["recovery_started_at"] = guard.recovery_started_at = fields["recovery_last_checked_at"]
        await callback(fields)
        guard.raise_if_missing()
        return history

    async def run_prompt(
        self,
        workflow: dict[str, Any],
        callback: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> tuple[str, dict[str, Any]]:
        freevideo_nodes = {str(node_id) for node_id, node in workflow.items()
                           if isinstance(node, dict) and node.get("class_type") == "FreeVideoGenerate"}
        client_id = uuid.uuid4().hex
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                async with session.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": client_id},
                    headers=self.auth_headers(),
                ) as response:
                    result = await response.json()
                    if response.status == 409 and result.get("code") == "host_gpu_busy":
                        # This response guarantees that no prompt was accepted.
                        await callback({"status": "queued", "progress": 0,
                                        "current_node": "等待遠端 GPU 主機完成目前語音或其他工作"})
                    elif response.status != 200:
                        error = result.get("error")
                        raise RuntimeError((error.get("message") if isinstance(error, dict) else error) or str(result))
                    else:
                        prompt_id = result["prompt_id"]
                        break
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
            await callback({"prompt_id": prompt_id, "status": "running", "progress": None if freevideo_nodes else 0,
                            **({"progress_scope": "phase", "freevideo_phase": "preparing", "phase_progress": None,
                                "freevideo_phase_label": "準備 FreeVideo 工作"} if freevideo_nodes else {})})

            reconnect_delay = 1.0
            presence = EnginePromptPresenceGuard(prompt_id)
            while True:
                if cancel_event.is_set():
                    await self.interrupt(prompt_id)
                    raise asyncio.CancelledError

                history = await self._checked_prompt_history(prompt_id, presence, callback, session)
                state = self.history_state(history)
                if state == "success":
                    return prompt_id, history
                if state == "error":
                    raise self.history_exception(history)

                try:
                    async with session.ws_connect(
                        f"{ws_url}/ws?clientId={client_id}", heartbeat=30, headers=self.auth_headers()
                    ) as ws:
                        reconnect_delay = 1.0
                        last_history_check = time.monotonic()
                        while True:
                            if cancel_event.is_set():
                                await self.interrupt(prompt_id)
                                raise asyncio.CancelledError
                            # Check independently of websocket traffic: shared
                            # status events can otherwise keep a lost prompt
                            # watching forever without a receive timeout.
                            if time.monotonic() - last_history_check >= PROMPT_PRESENCE_INTERVAL:
                                history = await self._checked_prompt_history(prompt_id, presence, callback, session)
                                last_history_check = time.monotonic()
                                state = self.history_state(history)
                                if state == "success":
                                    return prompt_id, history
                                if state == "error":
                                    raise self.history_exception(history)
                            try:
                                message = await asyncio.wait_for(ws.receive(), timeout=2)
                            except asyncio.TimeoutError:
                                continue
                            if message.type == aiohttp.WSMsgType.TEXT:
                                event = json.loads(message.data)
                                if not isinstance(event, dict):
                                    continue
                                data = event.get("data") or {}
                                if not isinstance(data, dict):
                                    continue
                                if data.get("prompt_id") not in {None, prompt_id}:
                                    continue
                                if event.get("type") == "freevideo_progress":
                                    fields = self.freevideo_progress_fields(data, prompt_id, freevideo_nodes)
                                    if fields is None:
                                        continue
                                    presence.mark_present()
                                    await callback({**fields, "progress_is_stale": False, **presence.fields()})
                                    continue
                                if data.get("prompt_id") == prompt_id and event.get("type") in {"progress", "executing", "execution_start"}:
                                    presence.mark_present()
                                if event.get("type") == "progress":
                                    if freevideo_nodes:
                                        # FreeVideo also emits ordinary Comfy
                                        # counters for preparation frames and
                                        # model blocks. Their 100% is not the
                                        # completion of this video workflow.
                                        continue
                                    maximum = max(1, int(data.get("max") or 1))
                                    value = int(data.get("value") or 0)
                                    await callback({"status": "running", "progress": round(value / maximum * 100, 1),
                                                    **({"progress_is_stale": False} if data.get("prompt_id") == prompt_id else {}),
                                                    **(presence.fields() if presence.state == "present" else {})})
                                elif event.get("type") == "executing":
                                    if freevideo_nodes and data.get("prompt_id") != prompt_id:
                                        continue
                                    node = data.get("node")
                                    phase_fields = {}
                                    if freevideo_nodes:
                                        source = workflow.get(str(node), {})
                                        kind = source.get("class_type") if isinstance(source, dict) else None
                                        phase, label = {"FreeVideoMedia": ("preparing", "準備 FreeVideo 素材"),
                                                        "FreeVideoGenerate": ("generating", "執行 FreeVideo（包含素材編碼、採樣與解碼）"),
                                                        "SaveVideo": ("saving", "儲存 FreeVideo 影片")}.get(kind, ("checking", "確認 FreeVideo 輸出"))
                                        node = label
                                        phase_fields = {"progress": None, "progress_scope": "phase", "phase_progress": None,
                                                        "freevideo_phase": phase, "freevideo_phase_label": label}
                                    await callback({"status": "running", "current_node": node,
                                                    **phase_fields,
                                                    **({"progress_is_stale": False, **presence.fields()}
                                                       if data.get("prompt_id") == prompt_id else {})})
                                elif event.get("type") == "execution_error":
                                    if freevideo_nodes and data.get("prompt_id") != prompt_id:
                                        continue
                                    raise ComfyExecutionError(data.get("exception_message") or "ComfyUI 執行失敗。", data)
                            elif message.type == aiohttp.WSMsgType.BINARY:
                                preview = self.decode_preview_message(message.data)
                                if preview:
                                    image, content_type = preview
                                    await callback({"preview_bytes": image, "preview_mime": content_type})
                            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    pass

                history = await self._checked_prompt_history(prompt_id, presence, callback, session)
                state = self.history_state(history)
                if state == "success":
                    return prompt_id, history
                if state == "error":
                    raise self.history_exception(history)
                await callback({"status": "running", "current_node": "進度連線中斷，正在自動重連",
                                **({"progress": None, "progress_scope": "phase", "phase_progress": None,
                                    "freevideo_phase": "reconnecting", "freevideo_phase_label": "進度連線中斷，正在自動重連"}
                                   if freevideo_nodes else {})})
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(10.0, reconnect_delay * 2)

    @staticmethod
    def freevideo_progress_fields(data: dict[str, Any], prompt_id: str, nodes: set[str]) -> dict[str, Any] | None:
        """Phase-local evidence only, scoped to this accepted prompt and node.

        Estimated overall/display fractions are deliberately not displayed as
        generation completion. Only successful terminal history completes it.
        """
        if (not nodes or data.get("prompt_id") != prompt_id or not isinstance(data.get("node"), str)
                or data["node"] not in nodes):
            return None
        phase = data.get("phase") or data.get("timing_phase") or "preparing"
        phase = str(phase)[:60]
        label = data.get("label")
        if not isinstance(label, str) or not label.strip():
            label = {"preparing": "準備工作", "load": "載入模型", "encoding": "整理參考素材與敘述",
                     "sampling": "生成採樣", "decode": "解碼影片與聲音", "complete": "整理輸出檔案",
                     "recovery": "調整記憶體配置"}.get(phase, "處理工作")
        label = " ".join(label.split())[:240]
        done, total = data.get("done"), data.get("total")
        percent = None
        try:
            valid_count = (type(done) in (int, float) and type(total) in (int, float)
                           and math.isfinite(done) and math.isfinite(total) and total > 0 and done >= 0)
        except (OverflowError, ValueError):
            valid_count = False
        if valid_count:
            percent = round(min(1.0, done / total) * 100, 1)
        detail = "FreeVideo · " + label + (f" · 階段進度 {percent:g}%" if percent is not None else "")
        return {"status": "running", "progress": None, "progress_scope": "phase", "phase_progress": percent,
                "freevideo_phase": phase, "freevideo_phase_label": label, "current_node": detail}

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

    async def interrupt(self, prompt_id: str | None = None) -> bool:
        return await cancel_queue_prompt(self.base_url, prompt_id, headers=self.auth_headers(prompt_id))

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
