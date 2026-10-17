from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp

from remote_voice import RemoteVoiceConnection, RemoteVoiceError


VOICE_JOB_DIR_NAME = "voice_jobs"
VOICE_OUTPUT_DIR_NAME = "voice_outputs"
VOICE_RUNTIME_DIR_NAME = "voice_runtime"
VOICE_MODEL_DIR_NAME = "voice_models"
VOICE_RUNTIME_SCHEMA = 2
VOICE_SDK_VERSION = "0.1.1"
VOICE_TORCH_VERSION = "2.11.0"
VOICE_TORCHAUDIO_VERSION = "2.11.0"
VOICE_TORCH_INDEX_URL = os.environ.get(
    "H3STUDIO_VOICE_TORCH_INDEX_URL",
    "https://download.pytorch.org/whl/cu130",
).strip()
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_SEED = 2**63 - 1

VOICE_MODELS = {
    "custom": {
        "mode": "custom", "size": "0.6B", "supports_instruct": False,
        "label": "內建聲線 0.6B",
        "repo": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "directory": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "description": "九種官方聲線，下載最小，適合快速旁白與功能預覽。",
    },
    "design": {
        "mode": "design", "size": "1.7B", "supports_instruct": True,
        "label": "聲線設計 1.7B",
        "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "directory": "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "description": "直接用年齡、音色、情緒與說話方式創造新聲線。",
    },
    "clone": {
        "mode": "clone", "size": "0.6B", "supports_instruct": False,
        "label": "聲線複製 0.6B",
        "repo": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "directory": "Qwen3-TTS-12Hz-0.6B-Base",
        "description": "以短參考音訊與逐字稿複製音色，再朗讀新的台詞。",
    },
    "custom_1_7b": {
        "mode": "custom", "size": "1.7B", "supports_instruct": True,
        "label": "內建聲線 1.7B · 表演控制",
        "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "directory": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
        "description": "九種官方聲線，支援情緒、語氣與說話方式指令。",
    },
    "clone_1_7b": {
        "mode": "clone", "size": "1.7B", "supports_instruct": False,
        "label": "聲線複製 1.7B",
        "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "directory": "Qwen3-TTS-12Hz-1.7B-Base",
        "revision": "fd4b254389122332181a7c3db7f27e918eec64e3",
        "description": "以短參考音訊與逐字稿複製音色，使用較大的 1.7B 模型。",
    },
}
VOICE_MODES = {"custom", "design", "clone"}
VOICE_CAPABILITIES = {"model_selection": True, "custom_voice_instruct": True}

CUSTOM_SPEAKERS = {
    "Vivian": "明亮、略帶俐落感的年輕中文女聲",
    "Serena": "溫暖、柔和的年輕中文女聲",
    "Uncle_Fu": "成熟低沉、圓潤的中文男聲",
    "Dylan": "清楚自然、年輕的北京男聲",
    "Eric": "活潑、略帶沙啞亮度的成都男聲",
    "Ryan": "節奏感強、動態鮮明的英文男聲",
    "Aiden": "陽光清楚的美式英文男聲",
    "Ono_Anna": "輕巧活潑的日文女聲",
    "Sohee": "溫暖且富情緒的韓文女聲",
}

LANGUAGES = {
    "Auto", "Chinese", "English", "Japanese", "Korean", "German",
    "French", "Russian", "Portuguese", "Spanish", "Italian",
}


class VoiceError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_name(value: Any) -> str:
    result = " ".join(str(value or "").split())
    if len(result) > 80:
        raise VoiceError("語音任務名稱最多 80 個字。")
    return result


def filename_stem(value: Any) -> str:
    result = INVALID_FILENAME_CHARS.sub("_", clean_name(value)).rstrip(". ")
    return result or datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")


def compile_voice_request(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "custom").strip().lower()
    if mode not in VOICE_MODES:
        raise VoiceError("不支援的語音生成模式。")
    explicit_model = "model_id" in payload
    model_id = str(payload["model_id"]).strip() if explicit_model else mode
    if model_id not in VOICE_MODELS:
        raise VoiceError("選擇的語音模型不存在。")
    if VOICE_MODELS[model_id]["mode"] != mode:
        raise VoiceError("選擇的語音模型與生成模式不符。")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise VoiceError("請輸入要朗讀的台詞。")
    if len(text) > 5000:
        raise VoiceError("單筆台詞最多 5,000 個字元；長篇內容請分段生成。")
    language = str(payload.get("language") or "Chinese").strip().title()
    if language not in LANGUAGES:
        raise VoiceError("不支援的語言選項。")
    instruct = str(payload.get("instruct") or "").strip()
    if len(instruct) > 1000:
        raise VoiceError("聲線／表演指令最多 1,000 個字元。")
    if instruct and not VOICE_MODELS[model_id]["supports_instruct"]:
        if explicit_model:
            raise VoiceError("所選語音模型不支援表演指令；請清空指令，或選擇內建聲線 1.7B。")
        # Older clients submitted the hidden design description in every mode.
        # The 0.6B/clone SDK ignored it, so legacy requests keep that behavior.
        instruct = ""
    try:
        seed = int(payload.get("seed"))
    except (TypeError, ValueError):
        seed = uuid.uuid4().int & MAX_SEED
    if not 0 <= seed <= MAX_SEED:
        raise VoiceError("Seed 超出可用範圍。")

    speaker = str(payload.get("speaker") or "Vivian").strip()
    if mode == "custom" and speaker not in CUSTOM_SPEAKERS:
        raise VoiceError("選擇的內建聲線不存在。")
    reference_asset_id = str(payload.get("reference_asset_id") or "").strip()
    reference_text = str(payload.get("reference_text") or "").strip()
    x_vector_only = bool(payload.get("x_vector_only", False))
    voice_authorized = bool(payload.get("voice_authorized", False))
    if mode == "design" and not instruct:
        raise VoiceError("聲線設計模式需要描述聲音的年齡、音色、情緒或說話方式。")
    if mode == "clone":
        if not reference_asset_id:
            raise VoiceError("聲線複製模式需要上傳參考音訊。")
        if not reference_text and not x_vector_only:
            raise VoiceError("請填入參考音訊的逐字稿；若無逐字稿，可開啟僅複製音色模式。")
        if len(reference_text) > 3000:
            raise VoiceError("參考音訊逐字稿最多 3,000 個字元。")
        if not voice_authorized:
            raise VoiceError("請確認你有權使用這段聲音，再進行聲線複製。")
    return {
        "name": clean_name(payload.get("job_name")),
        "mode": mode,
        "model_id": model_id,
        "text": text,
        "language": language,
        "instruct": instruct,
        "speaker": speaker,
        "reference_asset_id": reference_asset_id,
        "reference_text": reference_text,
        "x_vector_only": x_vector_only,
        "voice_authorized": voice_authorized,
        "seed": seed,
    }


class VoiceInstaller:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.runtime_dir = data_dir / VOICE_RUNTIME_DIR_NAME
        self.model_root = data_dir / VOICE_MODEL_DIR_NAME
        self.marker_path = self.runtime_dir / "installed.json"
        self.worker_path = Path(__file__).resolve().with_name("voice_worker.py")
        self.task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.state = "idle"
        self.current = ""
        self.error = ""
        self.requested_mode = "custom"
        self.requested_model_id = "custom"
        self.model_root.mkdir(parents=True, exist_ok=True)

    @property
    def python_path(self) -> Path:
        return self.runtime_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def model_path(self, mode: str) -> Path:
        if mode not in VOICE_MODELS:
            raise VoiceError("不支援的語音模型。")
        return self.model_root / VOICE_MODELS[mode]["directory"]

    def model_installed(self, mode: str) -> bool:
        path = self.model_path(mode)
        installed = (
            (path / "config.json").exists()
            and any(path.rglob("*.safetensors"))
            and (path / ".h3studio_complete.json").exists()
        )
        revision = VOICE_MODELS[mode].get("revision")
        if not installed or not revision:
            return installed
        try:
            marker = json.loads((path / ".h3studio_complete.json").read_text(encoding="utf-8"))
            return marker.get("repo") == VOICE_MODELS[mode]["repo"] and marker.get("revision") == revision
        except (OSError, ValueError, AttributeError):
            return False

    def runtime_installed(self) -> bool:
        if not self.python_path.exists() or not self.marker_path.exists():
            return False
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            int(marker.get("schema") or 0) == VOICE_RUNTIME_SCHEMA
            and str(marker.get("machine") or "").casefold() == str(os.environ.get("COMPUTERNAME") or "").casefold()
            and Path(str(marker.get("python") or "")).resolve() == self.python_path.resolve()
            and str(marker.get("torch_index_url") or "") == VOICE_TORCH_INDEX_URL
        )

    def public_status(self) -> dict[str, Any]:
        active = self.task is not None and not self.task.done()
        models = {
            mode: {
                **definition,
                "installed": self.model_installed(mode),
            }
            for mode, definition in VOICE_MODELS.items()
        }
        if not active and self.runtime_installed() and models[self.requested_model_id]["installed"]:
            state = "complete"
        else:
            state = self.state
        return {
            "state": state,
            "active": active,
            "current": self.current,
            "error": self.error,
            "requested_mode": self.requested_mode,
            "requested_model_id": self.requested_model_id,
            "capabilities": dict(VOICE_CAPABILITIES),
            "runtime_installed": self.runtime_installed(),
            "models": models,
            "installed": self.runtime_installed() and self.model_installed(self.requested_model_id),
        }

    async def start(self, mode: str) -> dict[str, Any]:
        if mode not in VOICE_MODELS:
            raise VoiceError("不支援的語音模型。")
        if self.task and not self.task.done():
            if mode != self.requested_model_id:
                raise VoiceError("另一個語音模型正在安裝，請等它完成或先取消。")
            return self.public_status()
        self.requested_model_id = mode
        self.requested_mode = VOICE_MODELS[mode]["mode"]
        self.state = "starting"
        self.current = "準備獨立語音環境"
        self.error = ""
        self.task = asyncio.create_task(self._run(mode))
        return self.public_status()

    async def cancel(self) -> dict[str, Any]:
        if self.task and not self.task.done():
            self.state = "cancelling"
            self.current = "正在取消安裝；已完成的檔案會保留"
            if self.process and self.process.returncode is None:
                self.process.terminate()
            self.task.cancel()
        return self.public_status()

    async def shutdown(self) -> None:
        if self.task and not self.task.done():
            if self.process and self.process.returncode is None:
                self.process.terminate()
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _command(self, *args: str, label: str) -> None:
        self.current = label
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await self.process.communicate()
        if self.process.returncode:
            detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise VoiceError(f"{label}失敗：{detail[-1600:] or f'程序代碼 {self.process.returncode}'}")

    async def _run(self, mode: str) -> None:
        try:
            if not self.runtime_installed():
                self.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
                if self.runtime_dir.exists():
                    shutil.rmtree(self.runtime_dir)
                await self._command(
                    sys.executable, "-m", "venv", str(self.runtime_dir),
                    label="建立 Qwen3-TTS 獨立環境",
                )
                await self._command(
                    str(self.python_path), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel",
                    label="更新語音環境安裝工具",
                )
                await self._command(
                    str(self.python_path), "-m", "pip", "install", "--upgrade",
                    f"torch=={VOICE_TORCH_VERSION}", f"torchaudio=={VOICE_TORCHAUDIO_VERSION}",
                    "--index-url", VOICE_TORCH_INDEX_URL,
                    label="安裝 CUDA 版 PyTorch 語音環境",
                )
                await self._command(
                    str(self.python_path), "-m", "pip", "install", f"qwen-tts=={VOICE_SDK_VERSION}", "soundfile",
                    label="安裝 Qwen3-TTS 執行環境",
                )
                await self._command(
                    str(self.python_path), str(self.worker_path), "probe",
                    label="檢查 Qwen3-TTS 與 GPU",
                )
                self.marker_path.parent.mkdir(parents=True, exist_ok=True)
                self.marker_path.write_text(
                    json.dumps({
                        "schema": VOICE_RUNTIME_SCHEMA,
                        "installed_at": utc_now(),
                        "python": str(self.python_path.resolve()),
                        "machine": os.environ.get("COMPUTERNAME") or "",
                        "torch_index_url": VOICE_TORCH_INDEX_URL,
                        "torch_version": VOICE_TORCH_VERSION,
                        "torchaudio_version": VOICE_TORCHAUDIO_VERSION,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if not self.model_installed(mode):
                target = self.model_path(mode)
                target.mkdir(parents=True, exist_ok=True)
                revision_args = ["--revision", VOICE_MODELS[mode]["revision"]] if VOICE_MODELS[mode].get("revision") else []
                await self._command(
                    str(self.python_path), str(self.worker_path), "download",
                    "--repo", VOICE_MODELS[mode]["repo"],
                    "--model-dir", str(target),
                    *revision_args,
                    label=f"下載{VOICE_MODELS[mode]['label']}（可續傳）",
                )
                (target / ".h3studio_complete.json").write_text(
                    json.dumps({"repo": VOICE_MODELS[mode]["repo"], "revision": VOICE_MODELS[mode].get("revision"),
                                "installed_at": utc_now()}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            self.state = "complete"
            self.current = f"{VOICE_MODELS[mode]['label']}已可使用"
            self.error = ""
        except asyncio.CancelledError:
            self.state = "cancelled"
            self.current = "安裝已取消；下載快取會保留供下次續傳"
        except Exception as error:
            self.state = "failed"
            self.current = "語音模型安裝失敗"
            self.error = str(error)
        finally:
            self.process = None


class VoiceJobManager:
    def __init__(
        self,
        data_dir: Path,
        gpu_lock: asyncio.Lock,
        installer: VoiceInstaller,
        asset_path: Callable[[str], Path],
        *,
        comfy=None,
    ):
        self.job_dir = data_dir / VOICE_JOB_DIR_NAME
        self.output_dir = data_dir / VOICE_OUTPUT_DIR_NAME
        self.gpu_lock = gpu_lock
        self.installer = installer
        self.asset_path = asset_path
        self.comfy = comfy
        self.remote_connections: dict[str, RemoteVoiceConnection] = {}
        self.remote_poll_interval = 1.0
        self.shutting_down = False
        self.before_local_generate = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        for path in self.job_dir.glob("*.json"):
            if path.name.endswith((".request.json", ".worker.json")):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict):
                continue
            job_id = job.get("id")
            if (not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                    or job_id != path.stem or job.get("type") != "qwen3_tts"):
                continue
            if (not isinstance(job.get("status"), str)
                    or job["status"] not in {"queued", "preparing", "running", "completed", "failed", "cancelled", "interrupted"}
                    or not isinstance(job.get("mode", "custom"), str)
                    or job.get("mode", "custom") not in VOICE_MODES
                    or not isinstance(job.get("model_id", job.get("mode", "custom")), str)):
                continue
            if job.get("status") in {"queued", "preparing", "running"}:
                job["status"] = "interrupted"
                job["error"] = "工具上次關閉時語音尚未完成，可按重新送出。"
            job.setdefault("favorite", False)
            job.setdefault("model_id", job.get("mode", "custom"))
            definition = VOICE_MODELS.get(job["model_id"])
            if definition:
                job.setdefault("model_label", definition["label"])
                job.setdefault("model_repo", definition["repo"])
            self.jobs[job_id] = job

    def _persist(self, job: dict[str, Any]) -> None:
        (self.job_dir / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def update(self, job_id: str, **changes: Any) -> None:
        self.jobs[job_id].update(changes)
        self.jobs[job_id]["updated_at"] = utc_now()
        self._persist(self.jobs[job_id])

    def remote_connection(self):
        if self.comfy is None or self.comfy.mode != "remote":
            return None
        try:
            return RemoteVoiceConnection.from_comfy(self.comfy)
        except RemoteVoiceError as error:
            raise VoiceError(str(error)) from error

    async def public_status(self):
        if self.comfy is None or self.comfy.mode != "remote":
            return {**self.installer.public_status(), "connection_mode": "local", "can_install": True, "available": True}
        try:
            status = await self.remote_connection().status()
            return {**status, "connection_mode": "remote", "can_install": False, "available": True}
        except (RemoteVoiceError, VoiceError) as error:
            return {"models": {mode: {**value, "installed": False} for mode, value in VOICE_MODELS.items()},
                    "runtime_installed": False, "installed": False, "active": False,
                    "state": "unavailable", "error": str(error), "connection_mode": "remote",
                    "can_install": False, "available": False}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_voice_request(payload)
        mode = compiled["mode"]
        model_id = compiled["model_id"]
        remote = self.remote_connection()
        if remote is None and (not self.installer.runtime_installed() or not self.installer.model_installed(model_id)):
            raise VoiceError(f"{VOICE_MODELS[model_id]['label']}尚未安裝，請先按安裝。")
        if mode == "clone":
            try:
                reference_path = self.asset_path(compiled["reference_asset_id"])
            except Exception as error:
                raise VoiceError("找不到聲線複製的參考音訊。") from error
            if reference_path.suffix.lower() not in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}:
                raise VoiceError("聲線複製的參考素材必須是音訊檔。")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "type": "qwen3_tts",
            "name": compiled["name"],
            "favorite": False,
            "mode": mode,
            "model_id": model_id,
            "model_label": VOICE_MODELS[model_id]["label"],
            "model_repo": VOICE_MODELS[model_id]["repo"],
            "status": "queued",
            "progress": 0,
            "current_node": "等待 GPU",
            "error": None,
            "local_output": None,
            "text": compiled["text"],
            "language": compiled["language"],
            "instruct": compiled["instruct"],
            "speaker": compiled["speaker"],
            "reference_text": compiled["reference_text"],
            "reference_asset_id": compiled["reference_asset_id"],
            "x_vector_only": compiled["x_vector_only"],
            "seed": compiled["seed"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "engine_mode": "remote" if remote else "local",
        }
        if remote:
            job["remote_base_url"] = remote.base_url
            self.remote_connections[job_id] = remote
            job["current_node"] = "等待遠端 GPU 主機"
        self.jobs[job_id] = job
        self.cancel_events[job_id] = asyncio.Event()
        self._persist(job)
        (self.job_dir / f"{job_id}.request.json").write_text(
            json.dumps({**payload, "model_id": model_id, "instruct": compiled["instruct"], "seed": compiled["seed"]},
                       ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, compiled))
        return job

    async def _release_idle_comfy(self, job_id: str, cancel_event: asyncio.Event) -> None:
        """Release local cached video models without interrupting queued work."""
        comfy = self.comfy
        if comfy is None or getattr(comfy, "mode", None) != "local" or not callable(getattr(comfy, "queue_status", None)):
            return
        base_url = str(getattr(comfy, "base_url", "")).rstrip("/")
        try:
            url = urlsplit(base_url)
            if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return
        except ValueError:
            return
        while True:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            try:
                async with asyncio.timeout(5):
                    snapshot = await comfy.queue_status()
            except (aiohttp.ClientError, TimeoutError, OSError):
                return
            if not isinstance(snapshot, dict) or snapshot.get("available") is not True:
                return
            if snapshot.get("running_count") == 0 and snapshot.get("pending_count") == 0:
                break
            self.update(job_id, current_node="等待本機影片引擎工作完成")
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        if cancel_event.is_set():
            raise asyncio.CancelledError
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.post(base_url + "/free", json={"unload_models": True, "free_memory": True},
                                        headers=comfy.auth_headers(), allow_redirects=False) as response:
                    if response.status >= 300:
                        self.update(job_id, memory_warning="影片引擎未確認釋放閒置快取，語音可能需要更多顯示記憶體。")
        except (aiohttp.ClientError, TimeoutError, OSError):
            self.update(job_id, memory_warning="無法釋放影片引擎閒置快取，語音可能需要更多顯示記憶體。")

    @staticmethod
    async def _communicate_worker(process):
        communication = asyncio.create_task(process.communicate())
        try:
            return await asyncio.shield(communication)
        except BaseException:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=10)
            except asyncio.CancelledError:
                await communication
            except asyncio.TimeoutError:
                if process.returncode is None:
                    process.kill()
                await communication
            raise

    async def _run(self, job_id: str, compiled: dict[str, Any]) -> None:
        cancel_event = self.cancel_events[job_id]
        try:
            if self.jobs[job_id].get("engine_mode") == "remote":
                await self._run_remote(job_id, compiled)
                return
            async with self.gpu_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                if self.before_local_generate:
                    self.update(job_id, current_node="等待主機現有影片工作完成")
                    await self.before_local_generate(cancel_event)
                await self._release_idle_comfy(job_id, cancel_event)
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                started = datetime.now(timezone.utc)
                self.update(
                    job_id,
                    status="running",
                    progress=35,
                    generation_started_at=started.isoformat(),
                    current_node=f"{VOICE_MODELS[compiled['model_id']]['label']}正在生成語音",
                    error=None,
                )
                output_path = self.output_dir / f"{job_id}.wav"
                worker_request = {**compiled, "model_dir": str(self.installer.model_path(compiled["model_id"]))}
                if compiled["mode"] == "clone":
                    worker_request["reference_audio"] = str(self.asset_path(compiled["reference_asset_id"]))
                worker_request_path = self.job_dir / f"{job_id}.worker.json"
                worker_request_path.write_text(
                    json.dumps(worker_request, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                process = await asyncio.create_subprocess_exec(
                    str(self.installer.python_path), str(self.installer.worker_path), "generate",
                    "--request", str(worker_request_path), "--output", str(output_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.processes[job_id] = process
                stdout, stderr = await self._communicate_worker(process)
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                if process.returncode:
                    detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
                    raise VoiceError(detail[-2000:] or f"語音程序代碼 {process.returncode}")
                if not output_path.exists() or output_path.stat().st_size < 128:
                    raise VoiceError("Qwen3-TTS 沒有產生有效的 WAV 音訊。")
                finished = datetime.now(timezone.utc)
                self.update(
                    job_id,
                    status="completed",
                    progress=100,
                    current_node=None,
                    error=None,
                    local_output=output_path.name,
                    finished_at=finished.isoformat(),
                    execution_seconds=round((finished - started).total_seconds(), 3),
                )
        except asyncio.CancelledError:
            if self.shutting_down and self.jobs[job_id].get("engine_mode") == "remote":
                self.update(job_id, status="interrupted", current_node=None, error="遠端主機工作仍保留；重新送出可接回進度或取回音訊。")
            else:
                self.update(job_id, status="cancelled", current_node=None, error="語音工作已取消。")
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=str(error))
        finally:
            self.processes.pop(job_id, None)
            (self.job_dir / f"{job_id}.worker.json").unlink(missing_ok=True)

    async def _run_remote(self, job_id, compiled):
        job = self.jobs[job_id]
        remote = self.remote_connections[job_id]
        cancel_event = self.cancel_events[job_id]

        async def check_cancel():
            if cancel_event.is_set():
                if job.get("remote_job_id"):
                    await remote.cancel(job["remote_job_id"])
                raise asyncio.CancelledError

        await check_cancel()
        self.update(job_id, status="preparing", current_node="連線遠端語音服務")
        if job.get("remote_job_id"):
            result = await remote.get(job["remote_job_id"])
            await check_cancel()
            if result.get("status") in {"failed", "cancelled", "interrupted"}:
                if compiled["model_id"] != compiled["mode"]:
                    remote.require_model_support(await remote.status(), compiled)
                result = await remote.resume(job["remote_job_id"])
        else:
            status = await remote.status()
            remote.require_model_support(status, compiled)
            if not status.get("runtime_installed") or not status["models"].get(compiled["model_id"], {}).get("installed"):
                raise VoiceError(f"遠端 GPU 主機的{VOICE_MODELS[compiled['model_id']]['label']}尚未安裝；請由主機管理者安裝，同事端不需安裝模型。")
            await check_cancel()
            reference_id = job.get("remote_reference_id", "")
            if compiled["mode"] == "clone" and not reference_id:
                self.update(job_id, current_node="上傳參考音訊至遠端主機")
                reference_id = await remote.upload_reference(self.asset_path(compiled["reference_asset_id"]))
                self.update(job_id, remote_reference_id=reference_id)
            await check_cancel()
            result = await remote.create(compiled, job_id, reference_id)
            self.update(job_id, remote_job_id=result["id"])
        while True:
            await check_cancel()
            status = result.get("status")
            if status == "completed":
                self.update(job_id, status="running", progress=95, current_node="下載遠端語音成品")
                output = self.output_dir / f"{job_id}.wav"
                await remote.download(job["remote_job_id"], output)
                await check_cancel()
                timing = {field: result[field] for field in ("generation_started_at", "finished_at", "execution_seconds") if field in result}
                self.update(job_id, status="completed", progress=100, current_node=None, error=None,
                            local_output=output.name, **timing)
                return
            if status in {"failed", "interrupted"}:
                raise VoiceError(str(result.get("error") or "遠端主機的語音工作未完成。"))
            if status == "cancelled":
                raise asyncio.CancelledError
            if status not in {"queued", "preparing", "running"}:
                raise VoiceError("遠端主機回傳的語音工作狀態格式錯誤。")
            progress = result.get("progress", 0)
            if not isinstance(progress, (int, float)) or not math.isfinite(progress):
                progress = 0
            self.update(job_id, status=status, progress=min(90, max(0, progress)),
                        current_node="遠端主機：" + str(result.get("current_node") or "等待 GPU"), error=None)
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=self.remote_poll_interval)
            except asyncio.TimeoutError:
                pass
            await check_cancel()
            result = await remote.get(job["remote_job_id"])

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise VoiceError("找不到語音工作。")
        if job.get("status") not in {"queued", "preparing", "running"}:
            return job
        self.cancel_events.setdefault(job_id, asyncio.Event()).set()
        if job.get("engine_mode") == "remote" and job.get("remote_job_id"):
            try:
                await self.remote_connections[job_id].cancel(job["remote_job_id"])
            except RemoteVoiceError as error:
                raise VoiceError(str(error)) from error
            task = self.tasks.get(job_id)
            if task and not task.done():
                task.cancel()
            self.update(job_id, status="cancelled", current_node=None, error="語音工作已取消。")
            return self.jobs[job_id]
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        if job.get("status") == "queued":
            self.update(job_id, status="cancelled", current_node=None, error="語音工作已取消。")
        else:
            self.update(job_id, current_node="正在取消")
        return self.jobs[job_id]

    async def shutdown(self) -> None:
        self.shutting_down = True
        for job_id, task in list(self.tasks.items()):
            if task.done():
                continue
            if self.jobs[job_id].get("engine_mode") != "remote":
                self.cancel_events.setdefault(job_id, asyncio.Event()).set()
            process = self.processes.get(job_id)
            if process and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            task.cancel()
        await asyncio.gather(*(task for task in self.tasks.values() if not task.done()), return_exceptions=True)

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise VoiceError("找不到語音工作。")
        if job.get("status") not in {"failed", "cancelled", "interrupted"}:
            raise VoiceError("只有失敗、取消或中斷的語音工作可以重新送出。")
        request_path = self.job_dir / f"{job_id}.request.json"
        if not request_path.exists():
            raise VoiceError("這筆舊工作沒有保留生成設定。")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        # Earlier SDK versions ignored these hidden fields. Preserve that behavior
        # only when resuming a request recorded before explicit model selection.
        if not payload.get("model_id") and payload.get("mode", "custom") in {"custom", "clone"}:
            payload["instruct"] = ""
        compiled = compile_voice_request(payload)
        remote = self.remote_connection()
        origin = job.get("engine_mode", "local")
        if origin == "remote":
            if not remote or remote.base_url != job.get("remote_base_url"):
                raise VoiceError("請連回這筆語音工作原本的 Gateway，再接回遠端工作；不會改由本機或另一台主機重做。")
            self.remote_connections[job_id] = remote
        elif remote:
            raise VoiceError("這筆是本機語音工作，請切換回本機引擎後重新送出。")
        if origin != "remote" and not self.installer.model_installed(compiled["model_id"]):
            raise VoiceError("這筆工作需要的語音模型尚未安裝。")
        self.cancel_events[job_id] = asyncio.Event()
        definition = VOICE_MODELS[compiled["model_id"]]
        self.update(job_id, model_id=compiled["model_id"], model_label=definition["label"], model_repo=definition["repo"],
                    status="queued", progress=0,
                    current_node="等待遠端 GPU 主機" if remote else "等待 GPU", error=None)
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, compiled))
        return self.jobs[job_id]

    def local_output_path(self, job: dict[str, Any]) -> Path | None:
        name = str(job.get("local_output") or "")
        if not name:
            return None
        root = self.output_dir.resolve()
        path = (root / name).resolve()
        return path if root in path.parents and path.exists() else None

    def list_page(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        favorites = sorted(
            (job for job in self.jobs.values() if job.get("favorite")),
            key=lambda job: str(job.get("created_at") or ""), reverse=True,
        )
        others = sorted(
            (job for job in self.jobs.values() if not job.get("favorite")),
            key=lambda job: str(job.get("created_at") or ""), reverse=True,
        )
        records = favorites + others
        total = len(records)
        pages = max(1, math.ceil(total / page_size))
        page = min(max(1, page), pages)
        start = (page - 1) * page_size
        return {
            "items": records[start:start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": pages,
        }
