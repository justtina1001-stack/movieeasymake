from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


GIB = 1024**3
ACTIVE_STATES = {"starting", "running", "cancelling"}


class ModelUpdateError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative_path(value: Any) -> Path:
    path = Path(str(value or "").replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ModelUpdateError("模型版本清單包含不安全的檔案路徑。")
    return path


def load_model_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelUpdateError(f"無法讀取模型版本清單：{error}") from error
    if manifest.get("schema_version") != 1 or not str(manifest.get("version") or "").strip():
        raise ModelUpdateError("模型版本清單格式不支援。")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ModelUpdateError("模型版本清單沒有可更新的檔案。")
    normalized = []
    for item in files:
        if not isinstance(item, dict):
            raise ModelUpdateError("模型檔案資料格式錯誤。")
        size = int(item.get("size") or 0)
        repo_id = str(item.get("repo_id") or "").strip()
        revision = str(item.get("revision") or "").strip()
        source = _safe_relative_path(item.get("source"))
        target = _safe_relative_path(item.get("target"))
        if size <= 0 or not repo_id or not revision:
            raise ModelUpdateError("模型檔案缺少來源、版本或檔案大小。")
        normalized.append({
            "repo_id": repo_id,
            "revision": revision,
            "source": source.as_posix(),
            "target": target.as_posix(),
            "size": size,
            "sha256": str(item.get("sha256") or "").strip().lower() or None,
        })
        if normalized[-1]["sha256"] and not re.fullmatch(r"[a-f0-9]{64}", normalized[-1]["sha256"]):
            raise ModelUpdateError("模型檔案包含無效的 SHA-256。")
    custom_nodes = []
    for item in manifest.get("custom_nodes") or []:
        if not isinstance(item, dict):
            raise ModelUpdateError("自訂節點資料格式錯誤。")
        repo_url = str(item.get("repo_url") or "").strip()
        revision = str(item.get("revision") or "").strip().lower()
        target = _safe_relative_path(item.get("target"))
        if len(target.parts) != 1 or not repo_url.startswith("https://github.com/"):
            raise ModelUpdateError("自訂節點包含不安全的來源或安裝路徑。")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ModelUpdateError("自訂節點缺少固定的 Git 提交版本。")
        custom_nodes.append({"repo_url": repo_url, "revision": revision, "target": target.as_posix()})
    result = dict(manifest)
    result["files"] = normalized
    result["custom_nodes"] = custom_nodes
    return result


class ModelUpdateManager:
    def __init__(
        self,
        app_dir: Path,
        data_dir: Path,
        settings_provider: Callable[[], Any],
    ):
        self.app_dir = app_dir
        self.data_dir = data_dir
        self.settings_provider = settings_provider
        self.manifest_path = app_dir / "model_manifest.json"
        self.preferences_path = data_dir / "model_update_preferences.json"
        self.state_path = data_dir / "model_update_status.json"
        self.log_path = data_dir / "model_update.log"
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.status = self._load_status()

    def _load_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return dict(default)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            return {**default, **saved} if isinstance(saved, dict) else dict(default)
        except (OSError, json.JSONDecodeError):
            return dict(default)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_status(self) -> dict[str, Any]:
        default = {
            "status": "idle", "progress": 0, "step": "尚未開始", "error": None,
            "version": None, "started_at": None, "updated_at": utc_now(), "logs": [],
        }
        saved = self._load_json(self.state_path, default)
        if saved.get("status") in ACTIVE_STATES:
            saved.update(status="interrupted", error="上次關閉面板時模型更新尚未完成，可重新開始並續傳。")
        return saved

    def _persist_status(self) -> None:
        self.status["updated_at"] = utc_now()
        self._write_json(self.state_path, self.status)

    def _preferences(self) -> dict[str, Any]:
        return self._load_json(self.preferences_path, {
            "skipped_version": None, "remind_after": None, "installed_version": None,
        })

    def _model_target(self) -> tuple[Path | None, str | None]:
        settings = self.settings_provider()
        if getattr(settings, "mode", "local") != "local":
            return None, "目前連線遠端引擎；模型由該 GPU 主機的管理者更新。"
        comfy_dir = Path(str(getattr(settings, "comfy_dir", "") or "")).resolve()
        if not (comfy_dir / "main.py").is_file():
            return None, "尚未找到有效的本機 ComfyUI；請先在引擎設定安裝或指定本機引擎。"
        return comfy_dir, None

    def inspect(self, include_files: bool = True) -> dict[str, Any]:
        manifest = load_model_manifest(self.manifest_path)
        preferences = self._preferences()
        target, unavailable_reason = self._model_target()
        files: list[dict[str, Any]] = []
        missing_bytes = 0
        custom_nodes: list[dict[str, Any]] = []
        if target:
            model_root = target / "models"
            for item in manifest["files"]:
                destination = model_root / Path(item["target"])
                actual_size = destination.stat().st_size if destination.is_file() else 0
                ready = actual_size == item["size"]
                if not ready:
                    missing_bytes += item["size"]
                files.append({
                    "target": item["target"],
                    "filename": Path(item["target"]).name,
                    "expected_bytes": item["size"],
                    "actual_bytes": actual_size,
                    "ready": ready,
                })
            for item in manifest["custom_nodes"]:
                destination = target / "custom_nodes" / item["target"]
                actual_revision = None
                if (destination / ".git").is_dir():
                    completed = subprocess.run(
                        ["git", "-C", str(destination), "rev-parse", "HEAD"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if completed.returncode == 0:
                        actual_revision = completed.stdout.strip().lower()
                custom_nodes.append({
                    "target": item["target"],
                    "revision": item["revision"],
                    "actual_revision": actual_revision,
                    "ready": actual_revision == item["revision"],
                })
        update_available = bool(target and (
            any(not item["ready"] for item in files)
            or any(not item["ready"] for item in custom_nodes)
        ))
        deferred = False
        remind_after = preferences.get("remind_after")
        if remind_after:
            try:
                deferred = datetime.fromisoformat(str(remind_after)) > datetime.now(timezone.utc)
            except ValueError:
                deferred = False
        skipped = preferences.get("skipped_version") == manifest["version"]
        active = self.status.get("status") in ACTIVE_STATES
        disk_free = shutil.disk_usage(target).free if target else 0
        result = {
            "supported": target is not None,
            "unavailable_reason": unavailable_reason,
            "version": manifest["version"],
            "published_at": manifest.get("published_at"),
            "channel": manifest.get("channel", "stable"),
            "title": manifest.get("title") or manifest["version"],
            "summary": manifest.get("summary") or "",
            "changes": manifest.get("changes") or [],
            "minimum_vram_gb": manifest.get("minimum_vram_gb"),
            "recommended_vram_gb": manifest.get("recommended_vram_gb"),
            "target": str(target) if target else None,
            "update_available": update_available,
            "should_prompt": update_available and not skipped and not deferred and not active,
            "skipped": skipped,
            "deferred": deferred,
            "remind_after": remind_after,
            "installed_version": preferences.get("installed_version"),
            "required_bytes": missing_bytes,
            "required_gb": round(missing_bytes / GIB, 2),
            "disk_free_bytes": disk_free,
            "disk_free_gb": round(disk_free / GIB, 1) if target else None,
            "ready_files": sum(1 for item in files if item["ready"]),
            "total_files": len(files),
            "ready_custom_nodes": sum(1 for item in custom_nodes if item["ready"]),
            "total_custom_nodes": len(custom_nodes),
            "installer": dict(self.status),
        }
        if include_files:
            result["files"] = files
            result["custom_nodes"] = custom_nodes
        return result

    async def start(self) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise ModelUpdateError("模型正在更新中。")
        inspection = await asyncio.to_thread(self.inspect)
        if not inspection["supported"]:
            raise ModelUpdateError(inspection["unavailable_reason"] or "目前無法更新模型。")
        if not inspection["update_available"]:
            return inspection
        required = int(inspection["required_bytes"])
        free = int(inspection["disk_free_bytes"])
        if free < required + 2 * GIB:
            raise ModelUpdateError(
                f"磁碟空間不足；更新約需 {required / GIB:.1f} GiB，並需保留至少 2 GiB 暫存空間。"
            )
        self.status = {
            "status": "starting", "progress": 1, "step": "準備模型更新", "error": None,
            "version": inspection["version"], "started_at": utc_now(), "updated_at": utc_now(), "logs": [],
        }
        self._persist_status()
        self.task = asyncio.create_task(self._run_worker(Path(inspection["target"])))
        return self.inspect()

    async def _run_worker(self, target: Path) -> None:
        worker = self.app_dir / "model_update_worker.py"
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, str(worker), "--target", str(target), "--manifest", str(self.manifest_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=flags,
            )
            self.status["status"] = "running"
            self._persist_status()
            assert self.process.stdout is not None
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log_handle:
                async for raw_line in self.process.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    log_handle.write(line + "\n")
                    log_handle.flush()
                    if line.startswith("H3_MODEL_UPDATE_EVENT "):
                        try:
                            self.status.update(json.loads(line.removeprefix("H3_MODEL_UPDATE_EVENT ")))
                        except json.JSONDecodeError:
                            pass
                    elif line:
                        logs = list(self.status.get("logs") or [])
                        logs.append(line[-500:])
                        self.status["logs"] = logs[-40:]
                    self._persist_status()
            return_code = await self.process.wait()
            if return_code != 0:
                raise RuntimeError(f"模型更新程序結束，錯誤代碼 {return_code}。請查看 model_update.log。")
            self.status.update(status="completed", progress=100, step="模型更新完成", error=None)
            preferences = self._preferences()
            preferences.update(
                installed_version=self.status.get("version"), skipped_version=None, remind_after=None,
            )
            self._write_json(self.preferences_path, preferences)
            self._persist_status()
        except asyncio.CancelledError:
            self.status.update(status="cancelled", step="模型更新已取消", error=None)
            self._persist_status()
            raise
        except Exception as error:
            self.status.update(status="failed", step="模型更新失敗", error=str(error))
            self._persist_status()
        finally:
            self.process = None

    async def cancel(self) -> dict[str, Any]:
        if not self.process or self.process.returncode is not None:
            raise ModelUpdateError("目前沒有正在執行的模型更新。")
        process = self.process
        self.status.update(status="cancelling", step="正在停止模型更新")
        self._persist_status()
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
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
        self.status.update(status="cancelled", step="模型更新已取消", error=None)
        self._persist_status()
        return self.inspect()

    def remind_later(self, hours: int = 24) -> dict[str, Any]:
        hours = min(24 * 30, max(1, int(hours)))
        preferences = self._preferences()
        preferences.update(
            remind_after=(datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
            skipped_version=None,
        )
        self._write_json(self.preferences_path, preferences)
        return self.inspect()

    def skip_current(self) -> dict[str, Any]:
        manifest = load_model_manifest(self.manifest_path)
        preferences = self._preferences()
        preferences.update(skipped_version=manifest["version"], remind_after=None)
        self._write_json(self.preferences_path, preferences)
        return self.inspect()

    def restore_prompt(self) -> dict[str, Any]:
        preferences = self._preferences()
        preferences.update(skipped_version=None, remind_after=None)
        self._write_json(self.preferences_path, preferences)
        return self.inspect()
