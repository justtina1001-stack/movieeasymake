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


VOICE_JOB_DIR_NAME = "voice_jobs"
VOICE_OUTPUT_DIR_NAME = "voice_outputs"
VOICE_RUNTIME_DIR_NAME = "voice_runtime"
VOICE_MODEL_DIR_NAME = "voice_models"
VOICE_RUNTIME_SCHEMA = 2
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
        "label": "內建聲線 0.6B",
        "repo": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "directory": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "description": "九種官方聲線，下載最小，適合快速旁白與功能預覽。",
    },
    "design": {
        "label": "聲線設計 1.7B",
        "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "directory": "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "description": "直接用年齡、音色、情緒與說話方式創造新聲線。",
    },
    "clone": {
        "label": "聲線複製 0.6B",
        "repo": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "directory": "Qwen3-TTS-12Hz-0.6B-Base",
        "description": "以短參考音訊與逐字稿複製音色，再朗讀新的台詞。",
    },
}

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
    if mode not in VOICE_MODELS:
        raise VoiceError("不支援的語音生成模式。")
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
        return (
            (path / "config.json").exists()
            and any(path.rglob("*.safetensors"))
            and (path / ".h3studio_complete.json").exists()
        )

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
        if not active and self.runtime_installed() and all(item["installed"] for item in models.values()):
            state = "complete"
        else:
            state = self.state
        return {
            "state": state,
            "active": active,
            "current": self.current,
            "error": self.error,
            "requested_mode": self.requested_mode,
            "runtime_installed": self.runtime_installed(),
            "models": models,
            "installed": self.runtime_installed() and self.model_installed(self.requested_mode),
        }

    async def start(self, mode: str) -> dict[str, Any]:
        if mode not in VOICE_MODELS:
            raise VoiceError("不支援的語音模型。")
        if self.task and not self.task.done():
            if mode != self.requested_mode:
                raise VoiceError("另一個語音模型正在安裝，請等它完成或先取消。")
            return self.public_status()
        self.requested_mode = mode
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
                    str(self.python_path), "-m", "pip", "install", "--upgrade", "qwen-tts", "soundfile",
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
                await self._command(
                    str(self.python_path), str(self.worker_path), "download",
                    "--repo", VOICE_MODELS[mode]["repo"],
                    "--model-dir", str(target),
                    label=f"下載{VOICE_MODELS[mode]['label']}（可續傳）",
                )
                (target / ".h3studio_complete.json").write_text(
                    json.dumps({"repo": VOICE_MODELS[mode]["repo"], "installed_at": utc_now()}, ensure_ascii=False, indent=2),
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
    ):
        self.job_dir = data_dir / VOICE_JOB_DIR_NAME
        self.output_dir = data_dir / VOICE_OUTPUT_DIR_NAME
        self.gpu_lock = gpu_lock
        self.installer = installer
        self.asset_path = asset_path
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        for path in self.job_dir.glob("*.json"):
            if path.name.endswith(".request.json"):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") in {"queued", "preparing", "running"}:
                job["status"] = "interrupted"
                job["error"] = "工具上次關閉時語音尚未完成，可按重新送出。"
            job.setdefault("favorite", False)
            self.jobs[str(job["id"])] = job

    def _persist(self, job: dict[str, Any]) -> None:
        (self.job_dir / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def update(self, job_id: str, **changes: Any) -> None:
        self.jobs[job_id].update(changes)
        self.jobs[job_id]["updated_at"] = utc_now()
        self._persist(self.jobs[job_id])

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_voice_request(payload)
        mode = compiled["mode"]
        if not self.installer.runtime_installed() or not self.installer.model_installed(mode):
            raise VoiceError(f"{VOICE_MODELS[mode]['label']}尚未安裝，請先按安裝。")
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
            "seed": compiled["seed"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.jobs[job_id] = job
        self.cancel_events[job_id] = asyncio.Event()
        self._persist(job)
        (self.job_dir / f"{job_id}.request.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, compiled))
        return job

    async def _run(self, job_id: str, compiled: dict[str, Any]) -> None:
        cancel_event = self.cancel_events[job_id]
        try:
            async with self.gpu_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                started = datetime.now(timezone.utc)
                self.update(
                    job_id,
                    status="running",
                    progress=35,
                    generation_started_at=started.isoformat(),
                    current_node=f"{VOICE_MODELS[compiled['mode']]['label']}正在生成語音",
                    error=None,
                )
                output_path = self.output_dir / f"{job_id}.wav"
                worker_request = {**compiled, "model_dir": str(self.installer.model_path(compiled["mode"]))}
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
                stdout, stderr = await process.communicate()
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
            self.update(job_id, status="cancelled", current_node=None, error="語音工作已取消。")
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=str(error))
        finally:
            self.processes.pop(job_id, None)
            (self.job_dir / f"{job_id}.worker.json").unlink(missing_ok=True)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise VoiceError("找不到語音工作。")
        if job.get("status") not in {"queued", "preparing", "running"}:
            return job
        self.cancel_events.setdefault(job_id, asyncio.Event()).set()
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            process.terminate()
        self.update(job_id, current_node="正在取消")
        return self.jobs[job_id]

    async def shutdown(self) -> None:
        for job_id, task in list(self.tasks.items()):
            if task.done():
                continue
            self.cancel_events.setdefault(job_id, asyncio.Event()).set()
            process = self.processes.get(job_id)
            if process and process.returncode is None:
                process.terminate()
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
        compiled = compile_voice_request(json.loads(request_path.read_text(encoding="utf-8")))
        if not self.installer.model_installed(compiled["mode"]):
            raise VoiceError("這筆工作需要的語音模型尚未安裝。")
        self.cancel_events[job_id] = asyncio.Event()
        self.update(job_id, status="queued", progress=0, current_node="等待 GPU", error=None)
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
