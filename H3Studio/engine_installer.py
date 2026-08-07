from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


MODEL_REPO = "Comfy-Org/MiniMax-H3"
MODEL_REVISION = "0543966fbdce5ba05709a8f2031c94bdba629b4a"
COMFY_REPO = "https://github.com/Comfy-Org/ComfyUI.git"
COMFY_REVISION = "9a9fdb10ed144ce760d9682cb247526ea23cc525"
LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
EXCLUDED_TERRITORIES = "歐盟、英國、韓國與美國"
GIB = 1024**3
MODEL_FILES: tuple[tuple[str, int], ...] = (
    ("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", 20_970_379_616),
    ("diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", 20_970_379_616),
    ("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15_687_142_551),
    ("vae/minimax_h3_audio_vae_fp32.safetensors", 605_254_808),
    ("vae/minimax_h3_video_vae_fp16.safetensors", 5_207_808_496),
)


class InstallerError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_install_target(value: Any, app_dir: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raw = "../ComfyUI"
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = app_dir / target
    target = target.resolve()
    if target == Path(target.anchor) or len(target.parts) < 2:
        raise InstallerError("不可把磁碟根目錄當成 ComfyUI 安裝資料夾。")
    return target


def model_state(target: Path) -> list[dict[str, Any]]:
    result = []
    for relative, expected_size in MODEL_FILES:
        path = target / "models" / Path(relative)
        actual_size = path.stat().st_size if path.is_file() else 0
        result.append({
            "path": relative,
            "filename": Path(relative).name,
            "expected_bytes": expected_size,
            "actual_bytes": actual_size,
            "ready": actual_size == expected_size,
        })
    return result


def detect_nvidia_gpu() -> dict[str, Any] | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=12,
            check=True,
        )
        first = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
        name, memory = (part.strip() for part in first.rsplit(",", 1))
        return {"name": name, "vram_mb": int(memory), "vram_gb": round(int(memory) / 1024, 1)}
    except (OSError, subprocess.SubprocessError, StopIteration, ValueError):
        return None


def system_memory_gb() -> float | None:
    if sys.platform != "win32":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return round(status.ullTotalPhys / GIB, 1)


def installer_preflight(target: Path) -> dict[str, Any]:
    existing_unknown = target.exists() and any(target.iterdir()) and not (target / "main.py").is_file()
    models = model_state(target)
    missing_model_bytes = sum(item["expected_bytes"] for item in models if not item["ready"])
    environment_ready = any((target / relative).is_file() for relative in (
        ".venv/Scripts/python.exe",
        "venv/Scripts/python.exe",
        "../python_embeded/python.exe",
    ))
    overhead = 5 * GIB if environment_ready else 15 * GIB
    required_bytes = missing_model_bytes + overhead
    disk_probe = target if target.exists() else target.parent
    while not disk_probe.exists() and disk_probe.parent != disk_probe:
        disk_probe = disk_probe.parent
    disk = shutil.disk_usage(disk_probe)
    gpu = detect_nvidia_gpu()
    ram_gb = system_memory_gb()
    issues: list[str] = []
    warnings: list[str] = []
    if sys.platform != "win32":
        issues.append("目前的一鍵安裝器只支援 Windows。")
    if not shutil.which("git"):
        issues.append("找不到 Git，請先安裝 Git for Windows。")
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        issues.append("需要 Python 3.11～3.13。")
    if gpu is None:
        issues.append("找不到 NVIDIA 顯示卡或 nvidia-smi，H3 本機引擎需要 NVIDIA CUDA。")
    elif gpu["vram_mb"] < 12 * 1024:
        warnings.append("顯示記憶體低於 12 GB，H3 很可能無法穩定生成。")
    if ram_gb is not None and ram_gb < 48:
        warnings.append("系統記憶體低於 48 GB，模型卸載到 RAM 時可能不足。")
    if existing_unknown:
        issues.append("目標資料夾已有其他檔案，而且不是 ComfyUI；請選擇空資料夾。")
    if disk.free < required_bytes:
        issues.append(f"磁碟空間不足，還需要約 {required_bytes / GIB:.1f} GiB，目前只有 {disk.free / GIB:.1f} GiB。")
    installed = (
        (target / "main.py").is_file()
        and environment_ready
        and all(item["ready"] for item in models)
    )
    return {
        "target": str(target),
        "ready_to_install": not issues,
        "installed": installed,
        "issues": issues,
        "warnings": warnings,
        "git": bool(shutil.which("git")),
        "python": ".".join(map(str, sys.version_info[:3])),
        "gpu": gpu,
        "ram_gb": ram_gb,
        "disk_free_bytes": disk.free,
        "disk_free_gb": round(disk.free / GIB, 1),
        "required_bytes": required_bytes,
        "required_gb": round(required_bytes / GIB, 1),
        "missing_model_bytes": missing_model_bytes,
        "models": models,
        "license_url": LICENSE_URL,
        "excluded_territories": EXCLUDED_TERRITORIES,
    }


CompleteCallback = Callable[[Path], Awaitable[None]]


class EngineInstaller:
    def __init__(self, app_dir: Path, data_dir: Path, on_complete: CompleteCallback | None = None):
        self.app_dir = app_dir
        self.data_dir = data_dir
        self.state_path = data_dir / "installer.json"
        self.log_path = data_dir / "installer.log"
        self.on_complete = on_complete
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.status = self._load_status()

    def _load_status(self) -> dict[str, Any]:
        default = {
            "status": "idle", "progress": 0, "step": "尚未開始", "error": None,
            "target": None, "started_at": None, "updated_at": utc_now(), "logs": [],
        }
        if not self.state_path.exists():
            return default
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            default.update(saved)
        except (OSError, json.JSONDecodeError):
            return default
        if default["status"] in {"starting", "running", "cancelling"}:
            default.update(status="interrupted", error="面板上次關閉時安裝尚未完成，可再次按安裝繼續。")
        return default

    def _persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.status["updated_at"] = utc_now()
        self.state_path.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_status(self) -> dict[str, Any]:
        return dict(self.status)

    async def start(self, target: Path, accepted_license: bool) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise InstallerError("本機引擎正在安裝中。")
        if not accepted_license:
            raise InstallerError("必須先閱讀並同意 MiniMax H3 授權及地區限制。")
        preflight = await asyncio.to_thread(installer_preflight, target)
        if preflight["installed"]:
            self.status.update(status="completed", progress=100, step="本機引擎已安裝", target=str(target), error=None)
            self._persist()
            if self.on_complete:
                await self.on_complete(target)
            return self.public_status()
        if not preflight["ready_to_install"]:
            raise InstallerError("；".join(preflight["issues"]))
        self.status = {
            "status": "starting", "progress": 1, "step": "準備安裝", "error": None,
            "target": str(target), "started_at": utc_now(), "updated_at": utc_now(), "logs": [],
        }
        self._persist()
        self.task = asyncio.create_task(self._run_worker(target))
        return self.public_status()

    async def _run_worker(self, target: Path) -> None:
        worker = self.app_dir / "install_engine_worker.py"
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, str(worker), "--target", str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=flags,
            )
            self.status["status"] = "running"
            self._persist()
            assert self.process.stdout is not None
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log_handle:
                async for raw_line in self.process.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    log_handle.write(line + "\n")
                    log_handle.flush()
                    if line.startswith("H3_INSTALL_EVENT "):
                        try:
                            event = json.loads(line.removeprefix("H3_INSTALL_EVENT "))
                            self.status.update(event)
                        except json.JSONDecodeError:
                            pass
                    elif line:
                        logs = list(self.status.get("logs") or [])
                        logs.append(line[-500:])
                        self.status["logs"] = logs[-40:]
                    self._persist()
            return_code = await self.process.wait()
            if return_code != 0:
                raise RuntimeError(f"安裝程序結束，錯誤代碼 {return_code}。請查看 installer.log。")
            self.status.update(status="completed", progress=100, step="本機引擎安裝完成", error=None)
            self._persist()
            if self.on_complete:
                await self.on_complete(target)
        except asyncio.CancelledError:
            self.status.update(status="cancelled", step="安裝已取消", error=None)
            self._persist()
            raise
        except Exception as error:
            self.status.update(status="failed", step="安裝失敗", error=str(error))
            self._persist()
        finally:
            self.process = None

    async def cancel(self) -> dict[str, Any]:
        if not self.process or self.process.returncode is not None:
            raise InstallerError("目前沒有正在執行的安裝。")
        process = self.process
        self.status.update(status="cancelling", step="正在停止安裝")
        self._persist()
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        if self.task and not self.task.done():
            self.task.cancel()
        self.status.update(status="cancelled", step="安裝已取消", error=None)
        self._persist()
        return self.public_status()
