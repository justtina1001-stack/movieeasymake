"""Independent MOSS sound-effect jobs sharing Studio's GPU ownership."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from aiohttp import web
from urllib.parse import quote

from video_generation_errors import video_generation_error_fields

MODEL_ID = "moss_soundeffect_v2"
MODEL_LABEL = "MOSS-SoundEffect v2.0"
MODEL_REPO = "OpenMOSS-Team/MOSS-SoundEffect-v2.0"
MAX_SEED = 2**53 - 1
ACTIVE = {"queued", "preparing", "running"}
SAFE_ID = re.compile(r"[a-f0-9]{32}")


class SoundEffectError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean_name(value):
    if value is not None and not isinstance(value, str):
        raise SoundEffectError("音效名稱格式錯誤。")
    name = " ".join((value or "").split())
    if len(name) > 80:
        raise SoundEffectError("音效名稱最多 80 個字。")
    return name


def filename_stem(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", clean_name(value)).rstrip(" .") or "音效"


def compile_sound_effect_request(payload):
    if not isinstance(payload, dict):
        raise SoundEffectError("音效設定必須是物件。")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SoundEffectError("請輸入要產生的音效描述。")
    prompt = prompt.strip()
    if len(prompt) > 2000:
        raise SoundEffectError("音效描述最多 2,000 個字元。")
    try:
        duration = float(payload.get("duration", 5))
    except (TypeError, ValueError) as error:
        raise SoundEffectError("音效長度格式錯誤。") from error
    if isinstance(payload.get("duration"), bool) or not math.isfinite(duration) or not 1 <= duration <= 30:
        raise SoundEffectError("音效長度請設定在 1 到 30 秒之間。")
    value = payload.get("seed")
    if value is None or value == "":
        seed = uuid.uuid4().int & MAX_SEED
    else:
        try:
            if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
                raise ValueError
            seed = int(value)
        except (TypeError, ValueError) as error:
            raise SoundEffectError("Seed 必須是非負整數。") from error
        if not 0 <= seed <= MAX_SEED:
            raise SoundEffectError("Seed 超出可用範圍。")
    steps = payload.get("steps", 100)
    if type(steps) is not int or not 20 <= steps <= 100:
        raise SoundEffectError("音效品質步數必須是 20 到 100 的整數。")
    return {"name": clean_name(payload.get("job_name")), "prompt": prompt,
            "duration": round(duration, 1), "seed": seed, "steps": steps, "model_id": MODEL_ID}


def _atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class SoundEffectJobManager:
    def __init__(self, data_dir, gpu_lock, *, comfy=None):
        self.data_dir = Path(data_dir)
        self.job_dir = self.data_dir / "sfx_jobs"
        self.output_dir = self.data_dir / "sfx_outputs"
        self.runtime_dir = self.data_dir / "sfx_runtime"
        self.python_path = self.runtime_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.model_dir = self.runtime_dir / "models/moss-soundeffect-v2.0"
        self.worker_path = Path(__file__).with_name("sound_effects_worker.py")
        self.gpu_lock, self.comfy = gpu_lock, comfy
        self.before_local_generate = None
        self.jobs, self.tasks, self.processes, self.cancel_events = {}, {}, {}, {}
        self.stopping_workers = {}
        for directory in (self.job_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)
        for path in self.job_dir.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict) or job.get("id") != path.stem or job.get("type") != MODEL_ID:
                    continue
                if job.get("status") in ACTIVE:
                    job = {**job, "status": "interrupted", "error": "Studio 重新啟動，音效尚未完成，可重新送出。", "current_node": None}
                self.jobs[job["id"]] = job
            except (OSError, ValueError, UnicodeError):
                continue

    def public_status(self):
        state = {"installed": False, "runtime_installed": False, "model_installed": False,
                 "active": False, "available": True, "state": "not_installed", "error": None,
                 "model_id": MODEL_ID, "model_label": MODEL_LABEL, "model_repo": MODEL_REPO,
                 "license": "Apache-2.0", "max_duration": 30, "sample_rate": 48000,
                 "setup_command": "setup_sound_effects.bat",
                 "connection_mode": getattr(self.comfy, "mode", "local"), "can_install": False}
        try:
            from setup_sound_effects import probe_installation
            result = probe_installation(self.data_dir)
            if isinstance(result, dict):
                state.update({key: result[key] for key in ("installed", "runtime_installed", "model_installed", "state", "error") if key in result})
        except (ImportError, OSError, ValueError):
            pass
        state["state"] = "ready" if state["installed"] else "not_installed"
        if state["connection_mode"] != "local":
            state.update(available=False, installed=False, state="unavailable", error="音效目前在這台 GPU 主機產生。請在已安裝 MOSS 的主機切換為本機引擎後使用。")
        elif not state["installed"]:
            state["error"] = state.get("error") or "尚未安裝 MOSS 音效環境或模型，請由主機執行 setup_sound_effects.bat。"
        return state

    def update(self, job_id, **changes):
        self.jobs[job_id].update(changes, updated_at=utc_now())
        _atomic_json(self.job_dir / f"{job_id}.json", self.jobs[job_id])

    def _require_ready(self):
        state = self.public_status()
        if not state["available"] or not state["installed"]:
            raise SoundEffectError(state["error"] or "MOSS 音效尚未準備好。")

    def create(self, payload):
        compiled = compile_sound_effect_request(payload)
        request_id = payload.get("request_id", "")
        if request_id and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", request_id)):
            raise SoundEffectError("音效送出識別碼格式錯誤。")
        # A retry after a lost HTTP response must not start the same GPU work twice.
        digest_source = {key: compiled[key] for key in compiled if key != "seed" or payload.get("seed") not in (None, "")}
        digest = hashlib.sha256(json.dumps(digest_source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if request_id:
            previous = next((job for job in self.jobs.values() if job.get("request_id") == request_id), None)
            if previous:
                if previous.get("request_digest") != digest:
                    raise SoundEffectError("同一個音效送出識別碼不能用於不同設定，請重新送出。")
                return previous
        self._require_ready()
        identifier = uuid.uuid4().hex
        job = {**compiled, "id": identifier, "type": MODEL_ID, "model_label": MODEL_LABEL,
               "model_repo": MODEL_REPO, "status": "queued", "progress": 0,
               "current_node": "等待 GPU", "error": None, "favorite": False,
               "local_output": None, "performance": None, "engine_mode": "local",
               "request_id": request_id, "request_digest": digest, "created_at": utc_now(), "updated_at": utc_now()}
        self.jobs[identifier] = job
        request = {"job_name": compiled["name"], **{key: compiled[key] for key in ("prompt", "duration", "seed", "steps")}}
        _atomic_json(self.job_dir / f"{identifier}.request.json", request)
        self.update(identifier)
        self.cancel_events[identifier] = asyncio.Event()
        self.tasks[identifier] = asyncio.create_task(self._run(identifier, compiled))
        return job

    async def _release_idle_comfy(self, identifier, cancel):
        if self.comfy is None:
            return
        while True:
            if cancel.is_set():
                raise asyncio.CancelledError
            snapshot = await self.comfy.queue_status()
            if not snapshot.get("available"):
                raise SoundEffectError("無法確認影片引擎佇列；音效尚未開始，請先恢復引擎連線。")
            if not snapshot.get("running_count") and not snapshot.get("pending_count"):
                break
            self.update(identifier, current_node="等待已送出的影片工作完成")
            try:
                await asyncio.wait_for(cancel.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(self.comfy.base_url.rstrip("/") + "/free",
                                    json={"unload_models": True, "free_memory": True},
                                    headers=self.comfy.auth_headers(), allow_redirects=False) as response:
                if response.status != 200:
                    raise SoundEffectError("影片引擎尚未確認釋放閒置模型，音效尚未開始，請稍後重試。")
        # /free acknowledges a flag; the engine thread unloads models later.
        # Do not race that acknowledgement with the independent CUDA worker.
        deadline = time.monotonic() + 30
        while True:
            if cancel.is_set():
                raise asyncio.CancelledError
            snapshot = await self.comfy.queue_status()
            if not snapshot.get("available"):
                raise SoundEffectError("無法確認影片引擎已釋放記憶體，音效尚未開始。")
            if snapshot.get("running_count") or snapshot.get("pending_count"):
                raise SoundEffectError("影片引擎收到新的工作，這次音效尚未開始；請稍後重新送出。")
            stats = await self.comfy.system_stats()
            cuda = [device for device in (stats or {}).get("devices", []) if device.get("type") == "cuda"]
            if cuda and all(isinstance(device.get("torch_vram_total"), (int, float))
                            and 0 <= device["torch_vram_total"] <= 1024**3 for device in cuda):
                return
            if time.monotonic() >= deadline:
                raise SoundEffectError("影片引擎尚未完成釋放閒置顯存，音效尚未開始；請稍後重試。")
            self.update(identifier, current_node="等待影片引擎釋放閒置顯存")
            try:
                await asyncio.wait_for(cancel.wait(), timeout=.25)
            except asyncio.TimeoutError:
                pass

    async def _terminate_worker_tree(self, process):
        """Only terminate a subprocess created and retained by this manager."""
        if process.returncode is not None:
            return
        if os.name == "nt":
            # Windows venv python.exe redirects to a child Python process.
            # Killing only the redirector can leave CUDA inference running.
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
            try:
                killer = await asyncio.create_subprocess_exec(
                    str(taskkill), "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            except OSError:
                pass
            # If Windows rejects termination, continue owning the GPU until the
            # worker actually exits rather than allowing overlapping generation.
            await process.wait()
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def _stop_worker(self, process):
        task = self.stopping_workers.get(process.pid)
        if task is None:
            if process.returncode is not None:
                return
            task = asyncio.create_task(self._terminate_worker_tree(process))
            self.stopping_workers[process.pid] = task
        interrupted = False
        try:
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    # A repeated cancel/shutdown must not abandon child cleanup
                    # and release the shared GPU lock early.
                    interrupted = True
                    if task.done():
                        task.result()
                        break
        finally:
            if task.done() and self.stopping_workers.get(process.pid) is task:
                self.stopping_workers.pop(process.pid, None)
        if interrupted:
            raise asyncio.CancelledError

    async def _communicate(self, identifier, process):
        stderr_tail = bytearray()
        async def drain_errors():
            while chunk := await process.stderr.read(8192):
                stderr_tail.extend(chunk)
                if len(stderr_tail) > 16000:
                    del stderr_tail[:-16000]
        async def progress():
            while line := await process.stdout.readline():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("stage"):
                        stages = {"loading": "載入 MOSS 音效模型", "encoding": "整理音效敘述", "sampling": "產生音效",
                                  "decoding": "整理 WAV 音訊", "completed": "檢查音效成品", "failed": "音效未完成"}
                        updates = {"current_node": stages.get(event["stage"], str(event["stage"])[:200])}
                        value = event.get("progress")
                        if isinstance(value, (int, float)) and math.isfinite(value):
                            updates["progress"] = max(self.jobs[identifier].get("progress", 0), min(95, max(0, value)))
                        self.update(identifier, **updates)
                except (ValueError, UnicodeError):
                    pass
        readers = [asyncio.create_task(drain_errors()), asyncio.create_task(progress())]
        try:
            await process.wait()
            await asyncio.gather(*readers)
            return bytes(stderr_tail)
        finally:
            await self._stop_worker(process)
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    async def _run(self, identifier, compiled):
        cancel = self.cancel_events[identifier]
        output = self.output_dir / f"{identifier}.wav"
        worker_request = self.job_dir / f"{identifier}.worker.json"
        metrics_path = self.job_dir / f"{identifier}.metrics.json"
        try:
            async with self.gpu_lock:
                if cancel.is_set():
                    raise asyncio.CancelledError
                self._require_ready()
                self.update(identifier, status="preparing", current_node="等待影片引擎完成並釋放閒置模型")
                if self.before_local_generate:
                    await self.before_local_generate(cancel)
                await self._release_idle_comfy(identifier, cancel)
                if cancel.is_set():
                    raise asyncio.CancelledError
                started = datetime.now(timezone.utc)
                output.unlink(missing_ok=True)
                metrics_path.unlink(missing_ok=True)
                self.update(identifier, status="running", progress=5, current_node="載入 MOSS 音效模型", generation_started_at=started.isoformat())
                _atomic_json(worker_request, {"prompt": compiled["prompt"], "seconds": compiled["duration"],
                                             "seed": compiled["seed"], "steps": compiled["steps"], "model_dir": str(self.model_dir)})
                process = await asyncio.create_subprocess_exec(
                    str(self.python_path), "-X", "utf8", "-u", str(self.worker_path), "generate",
                    "--request", str(worker_request), "--output", str(output), "--metrics", str(metrics_path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "TORCHDYNAMO_DISABLE": "1", "PYTHONUTF8": "1",
                         "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
                self.processes[identifier] = process
                stderr = await self._communicate(identifier, process)
                if cancel.is_set():
                    raise asyncio.CancelledError
                if process.returncode:
                    raise SoundEffectError(stderr.decode("utf-8", errors="replace")[-4000:] or f"音效程序代碼 {process.returncode}")
                if not output.is_file() or output.stat().st_size < 128:
                    raise SoundEffectError("MOSS 沒有產生有效的 WAV 音效。")
                metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
                if not isinstance(metrics, dict):
                    raise SoundEffectError("音效驗證結果格式錯誤。")
                finished = datetime.now(timezone.utc)
                self.update(identifier, status="completed", progress=100, current_node=None, error=None,
                            local_output=output.name, performance=metrics, finished_at=finished.isoformat(),
                            execution_seconds=round((finished-started).total_seconds(), 3))
        except asyncio.CancelledError:
            self.update(identifier, status="cancelled", current_node=None, error="音效工作已取消。")
        except Exception as error:
            failure = video_generation_error_fields(error)
            message = ("顯示卡記憶體不足，這次音效未完成。請先關閉其他占用顯示卡的程式，或等其他 GPU 工作完成後再重試；描述與 Seed 已保留。"
                       if failure["error_code"] else str(error))
            self.update(identifier, status="failed", current_node=None, error=message,
                        error_code=failure["error_code"], error_detail=failure["error_detail"])
        finally:
            self.processes.pop(identifier, None)
            worker_request.unlink(missing_ok=True)
            if self.jobs[identifier].get("status") != "completed":
                output.unlink(missing_ok=True)

    async def cancel(self, identifier):
        job = self.get(identifier)
        if job["status"] in ACTIVE:
            self.cancel_events.setdefault(identifier, asyncio.Event()).set()
            process = self.processes.get(identifier)
            if process is not None:
                await self._stop_worker(process)
            task = self.tasks.get(identifier)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if job["status"] in ACTIVE:
                self.update(identifier, status="cancelled", current_node=None, error="音效工作已取消。")
        return job

    async def shutdown(self):
        await asyncio.gather(*(self.cancel(identifier) for identifier, task in list(self.tasks.items()) if not task.done()), return_exceptions=True)

    def get(self, identifier):
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier) or identifier not in self.jobs:
            raise SoundEffectError("找不到音效工作。")
        return self.jobs[identifier]

    def resume(self, identifier):
        job = self.get(identifier)
        if job["status"] not in {"failed", "cancelled", "interrupted"}:
            raise SoundEffectError("只有失敗、取消或中斷的音效可以重新送出。")
        self._require_ready()
        compiled = compile_sound_effect_request(json.loads((self.job_dir / f"{identifier}.request.json").read_text(encoding="utf-8")))
        self.cancel_events[identifier] = asyncio.Event()
        self.update(identifier, status="queued", progress=0, error=None, error_code=None, error_detail=None,
                    current_node="等待 GPU", local_output=None, performance=None)
        self.tasks[identifier] = asyncio.create_task(self._run(identifier, compiled))
        return job

    def local_output_path(self, job):
        name = str(job.get("local_output") or "")
        if job.get("status") != "completed" or name != f"{job.get('id')}.wav":
            return None
        path = self.output_dir / name
        return path if path.resolve().parent == self.output_dir.resolve() and path.is_file() else None

    def list_page(self, page=1, page_size=20):
        page_size = min(20, max(1, int(page_size)))
        ordered = sorted(self.jobs.values(), key=lambda job: str(job.get("created_at") or ""), reverse=True)
        records = [job for job in ordered if job.get("favorite")] + [job for job in ordered if not job.get("favorite")]
        pages = max(1, math.ceil(len(records) / page_size))
        page = min(max(1, int(page)), pages)
        return {"items": records[(page-1)*page_size:page*page_size], "page": page, "page_size": page_size,
                "total": len(records), "total_pages": pages}


def register_sound_effect_routes(app):
    manager = app["sfx_jobs"]

    def error_response(error, status=400):
        return web.json_response({"error": str(error)}, status=status)

    async def status(request):
        return web.json_response(manager.public_status(), headers={"Cache-Control": "no-store"})

    async def jobs(request):
        try:
            page = max(1, int(request.query.get("page", "1")))
            size = min(20, max(1, int(request.query.get("page_size", "20"))))
            result = manager.list_page(page, size)
            focus = request.query.get("focus", "")
            if SAFE_ID.fullmatch(focus) and not any(job["id"] == focus for job in result["items"]):
                for index in range(1, result["total_pages"] + 1):
                    candidate = manager.list_page(index, size)
                    if any(job["id"] == focus for job in candidate["items"]):
                        result = candidate
                        break
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (SoundEffectError, ValueError) as error:
            return error_response(error)

    async def create(request):
        try:
            return web.json_response(manager.create(await request.json()), status=202)
        except (SoundEffectError, ValueError, OSError) as error:
            return error_response(error)

    async def get(request):
        try:
            return web.json_response(manager.get(request.match_info["job_id"]), headers={"Cache-Control": "no-store"})
        except SoundEffectError as error:
            return error_response(error, 404)

    async def cancel(request):
        try:
            return web.json_response(await manager.cancel(request.match_info["job_id"]))
        except SoundEffectError as error:
            return error_response(error, 404)

    async def resume(request):
        try:
            return web.json_response(manager.resume(request.match_info["job_id"]), status=202)
        except (SoundEffectError, ValueError, OSError) as error:
            return error_response(error, 409)

    async def rename(request):
        try:
            job = manager.get(request.match_info["job_id"])
            payload = await request.json()
            if not isinstance(payload, dict):
                raise SoundEffectError("音效名稱格式錯誤。")
            manager.update(job["id"], name=clean_name(payload.get("name")))
            return web.json_response(job)
        except (SoundEffectError, ValueError, OSError) as error:
            return error_response(error)

    async def favorite(request):
        try:
            job = manager.get(request.match_info["job_id"])
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("favorite"), bool):
                raise SoundEffectError("我的最愛狀態格式錯誤。")
            manager.update(job["id"], favorite=payload["favorite"])
            return web.json_response(job)
        except (SoundEffectError, ValueError, OSError) as error:
            return error_response(error)

    async def audio(request):
        try:
            job = manager.get(request.match_info["job_id"])
            path = manager.local_output_path(job)
            if path is None:
                raise SoundEffectError("音效尚未完成或檔案不存在。")
            headers = {"Content-Type": "audio/wav", "Cache-Control": "no-store"}
            if request.query.get("download") == "1":
                headers["Content-Disposition"] = f'attachment; filename="sound-effect.wav"; filename*=UTF-8\'\'{quote(filename_stem(job.get("name")) + ".wav", safe="")}'
            return web.FileResponse(path, headers=headers)
        except SoundEffectError as error:
            return error_response(error, 404)

    app.router.add_get("/api/sfx/status", status)
    app.router.add_get("/api/sfx/jobs", jobs)
    app.router.add_post("/api/sfx/jobs", create)
    app.router.add_get("/api/sfx/jobs/{job_id}", get)
    app.router.add_post("/api/sfx/jobs/{job_id}/cancel", cancel)
    app.router.add_post("/api/sfx/jobs/{job_id}/resume", resume)
    app.router.add_post("/api/sfx/jobs/{job_id}/rename", rename)
    app.router.add_post("/api/sfx/jobs/{job_id}/favorite", favorite)
    app.router.add_get("/api/sfx/jobs/{job_id}/audio", audio)
