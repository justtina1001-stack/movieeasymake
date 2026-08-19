from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from comfy_client import ComfyClient


MUSIC_JOB_DIR_NAME = "music_jobs"
MUSIC_OUTPUT_DIR_NAME = "music_outputs"
MUSIC3_DIT = "minimax_music3_dit_int8_convrot.safetensors"
MUSIC3_TEXT_ENCODER = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
MUSIC3_VAE = "minimax_music3_dav.safetensors"
HF_ROOT = "https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main"
MUSIC3_MODELS = (
    {
        "key": "diffusion_model",
        "label": "Music 3 INT8 擴散模型",
        "directory": "diffusion_models",
        "filename": MUSIC3_DIT,
        "url": f"{HF_ROOT}/diffusion_models/{MUSIC3_DIT}",
        "size": 2_502_161_682,
    },
    {
        "key": "text_encoder",
        "label": "Music 3 INT8 文字編碼器",
        "directory": "text_encoders",
        "filename": MUSIC3_TEXT_ENCODER,
        "url": f"{HF_ROOT}/text_encoders/{MUSIC3_TEXT_ENCODER}",
        "size": 9_196_611_886,
    },
    {
        "key": "vae",
        "label": "Music 3 音訊 VAE",
        "directory": "vae",
        "filename": MUSIC3_VAE,
        "url": f"{HF_ROOT}/vae/{MUSIC3_VAE}",
        "size": 216_696_128,
    },
)
MAX_SEED = 2**64 - 1
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Music3Error(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_name(value: Any) -> str:
    result = " ".join(str(value or "").split())
    if len(result) > 80:
        raise Music3Error("音樂任務名稱最多 80 個字。")
    return result


def filename_stem(value: Any) -> str:
    name = INVALID_FILENAME_CHARS.sub("_", clean_name(value)).rstrip(". ")
    return name or datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")


def _text(value: Any, fallback: str) -> str:
    result = " ".join(str(value or "").split())
    return result or fallback


def compile_music_request(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "instrumental").strip().lower()
    if mode not in {"song", "instrumental"}:
        raise Music3Error("音樂模式必須是歌曲或純音樂。")
    try:
        duration = float(payload.get("duration") or 60)
    except (TypeError, ValueError) as error:
        raise Music3Error("音樂時長格式錯誤。") from error
    if not 10 <= duration <= 300:
        raise Music3Error("音樂時長請設定在 10 到 300 秒之間。")
    try:
        seed = int(payload.get("seed"))
    except (TypeError, ValueError):
        seed = uuid.uuid4().int & MAX_SEED
    if not 0 <= seed <= MAX_SEED:
        raise Music3Error("Seed 超出可用範圍。")

    genre = _text(payload.get("genre"), "cinematic game music")
    mood = _text(payload.get("mood"), "energetic, polished, memorable")
    bpm = _text(payload.get("bpm"), "120 BPM")
    if bpm.isdigit():
        bpm = f"{bpm} BPM"
    musical_key = _text(payload.get("key"), "major key")
    use_case = _text(payload.get("use_case"), "game soundtrack")
    production = _text(
        payload.get("production"),
        "clean modern production, clear melody, controlled dynamics, wide stereo image",
    )
    instruments = _text(
        payload.get("instruments"),
        "punchy drums, bass, bright synths, cinematic percussion",
    )
    structure = _text(
        payload.get("structure"),
        "short intro, clear main theme, contrasting middle section, strong final cadence",
    )
    details = _text(payload.get("details"), "Keep the musical form coherent and transitions intentional.")
    avoid = " ".join(str(payload.get("avoid") or "").split())

    if mode == "instrumental":
        vocal_details = "Instrumental only. No singing, spoken words, chants, vocal chops, or human voice."
        lyrics = "[Intro]\n\n[Instrumental]\n\n[Bridge]\n\n[Instrumental]\n\n[Outro]"
    else:
        vocal_details = _text(
            payload.get("vocals"),
            "Expressive lead vocal with clear diction, consistent singer identity, and tasteful backing harmonies.",
        )
        lyrics = str(payload.get("lyrics") or "").strip()
        if not lyrics:
            raise Music3Error("歌曲模式需要填入歌詞；可使用 [Intro]、[Verse]、[Chorus]、[Bridge]、[Outro] 標籤。")
        if len(lyrics) > 20_000:
            raise Music3Error("歌詞內容過長，請控制在 20,000 字元內。")

    global_metadata = (
        f"Global Metadata: {genre}. {bpm}, {musical_key}. {mood}. "
        f"Designed for {use_case}. {production}."
    )
    arrangement = f"Arrangement: {instruments}. Form: {structure}. {details}"
    if avoid:
        arrangement += f" Avoid: {avoid}."
    caption = f"{global_metadata}\n\nVocal Details: {vocal_details}\n\n{arrangement}"
    output_format = str(payload.get("format") or "mp3").lower()
    if output_format not in {"mp3", "flac"}:
        raise Music3Error("輸出格式只支援 MP3 或 FLAC。")
    return {
        "name": clean_name(payload.get("job_name")),
        "mode": mode,
        "duration": round(duration, 2),
        "seed": seed,
        "caption": caption,
        "lyrics": lyrics,
        "format": output_format,
        "tiled_decode": bool(payload.get("tiled_decode", True)),
    }


def build_music3_workflow(compiled: dict[str, Any], output_stem: str) -> dict[str, Any]:
    decoder_class = "VAEDecodeAudioTiled" if compiled["tiled_decode"] else "VAEDecodeAudio"
    decoder_inputs: dict[str, Any] = {"samples": ["7", 0], "vae": ["3", 0]}
    if compiled["tiled_decode"]:
        decoder_inputs.update({"tile_size": 1536, "overlap": 64})
    save_inputs: dict[str, Any] = {
        "audio": ["8", 0],
        "filename_prefix": f"H3StudioMusic/{output_stem}",
        "format": compiled["format"],
    }
    if compiled["format"] == "mp3":
        save_inputs["format.quality"] = "V0"
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": MUSIC3_DIT, "weight_dtype": "default"},
            "_meta": {"title": "載入 Music 3 擴散模型"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": MUSIC3_TEXT_ENCODER, "type": "minimax", "device": "default"},
            "_meta": {"title": "載入 Music 3 文字編碼器"},
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": MUSIC3_VAE},
            "_meta": {"title": "載入 Music 3 音訊 VAE"},
        },
        "4": {
            "class_type": "MiniMaxMusic3TextEncode",
            "inputs": {
                "clip": ["2", 0],
                "caption": compiled["caption"],
                "lyrics": compiled["lyrics"],
                "seed": compiled["seed"],
                "max_duration": compiled["duration"],
                "cfg_scale": 1.7,
                "top_k": 50,
            },
            "_meta": {"title": "理解曲風、編曲與歌詞"},
        },
        "5": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["4", 0]},
            "_meta": {"title": "建立負面條件"},
        },
        "6": {
            "class_type": "EmptyMiniMaxMusic3LatentAudio",
            "inputs": {"seconds": ["4", 1], "batch_size": 1},
            "_meta": {"title": "建立音訊時間軸"},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": compiled["seed"],
                "steps": 30,
                "cfg": 1.7,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "denoise": 1.0,
            },
            "_meta": {"title": "生成音樂"},
        },
        "8": {
            "class_type": decoder_class,
            "inputs": decoder_inputs,
            "_meta": {"title": "低顯存分塊解碼" if compiled["tiled_decode"] else "解碼音訊"},
        },
        "9": {
            "class_type": "SaveAudioAdvanced",
            "inputs": save_inputs,
            "_meta": {"title": "儲存音樂"},
        },
    }


def find_audio_output(value: Any) -> dict[str, str] | None:
    if isinstance(value, dict):
        filename = value.get("filename")
        if filename and Path(str(filename)).suffix.lower() in {".mp3", ".flac", ".opus", ".wav"}:
            return {
                "filename": str(filename),
                "subfolder": str(value.get("subfolder") or ""),
                "type": str(value.get("type") or "output"),
            }
        for child in value.values():
            result = find_audio_output(child)
            if result:
                return result
    if isinstance(value, list):
        for child in value:
            result = find_audio_output(child)
            if result:
                return result
    return None


class Music3Installer:
    def __init__(self, comfy: ComfyClient):
        self.comfy = comfy
        self.task: asyncio.Task | None = None
        self.cancel_event = asyncio.Event()
        self.state = "idle"
        self.current = ""
        self.error = ""
        self.downloaded = 0
        self.total = sum(int(model["size"]) for model in MUSIC3_MODELS)
        self.speed_bps = 0.0

    def _model_path(self, model: dict[str, Any]) -> Path:
        models_root = (self.comfy.comfy_dir / "models").resolve()
        path = (models_root / str(model["directory"]) / str(model["filename"])).resolve()
        if models_root not in path.parents:
            raise Music3Error("Music 3 模型路徑超出 ComfyUI models 資料夾。")
        return path

    def public_status(self) -> dict[str, Any]:
        models = []
        installed_bytes = 0
        for model in MUSIC3_MODELS:
            path = self._model_path(model)
            part = path.with_suffix(path.suffix + ".part")
            complete = path.exists() and path.stat().st_size >= int(model["size"])
            if complete:
                local_bytes = int(model["size"])
            else:
                local_bytes = part.stat().st_size if part.exists() else 0
                local_bytes += sum(chunk.stat().st_size for chunk in part.parent.glob(f"{part.name}.*.chunk"))
            installed_bytes += min(local_bytes, int(model["size"]))
            models.append({
                **{key: model[key] for key in ("key", "label", "filename", "size")},
                "installed": complete,
                "downloaded": local_bytes,
            })
        active = self.task is not None and not self.task.done()
        if not active and all(model["installed"] for model in models):
            state = "complete"
        else:
            state = self.state
        return {
            "state": state,
            "active": active,
            "can_install": self.comfy.mode == "local",
            "current": self.current,
            "error": self.error,
            "downloaded": installed_bytes,
            "total": self.total,
            "progress": round(installed_bytes / max(1, self.total) * 100, 2),
            "speed_bps": round(self.speed_bps),
            "models": models,
            "installed": all(model["installed"] for model in models),
        }

    async def status_for_current_engine(self) -> dict[str, Any]:
        status = self.public_status()
        if self.comfy.mode != "remote":
            return status
        available: dict[str, bool] = {model["key"]: False for model in MUSIC3_MODELS}
        lookups = (
            ("UNETLoader", "unet_name", MUSIC3_DIT, "diffusion_model"),
            ("CLIPLoader", "clip_name", MUSIC3_TEXT_ENCODER, "text_encoder"),
            ("VAELoader", "vae_name", MUSIC3_VAE, "vae"),
        )
        nodes_ready = False
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.comfy.base_url}/object_info/MiniMaxMusic3TextEncode",
                    headers=self.comfy.auth_headers(),
                ) as response:
                    nodes_ready = response.status == 200
                for node, field, filename, key in lookups:
                    async with session.get(
                        f"{self.comfy.base_url}/object_info/{node}",
                        headers=self.comfy.auth_headers(),
                    ) as response:
                        if response.status != 200:
                            continue
                        payload = await response.json()
                    choices = payload.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])[0]
                    available[key] = filename in choices
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
            pass
        for model in status["models"]:
            model["installed"] = available.get(model["key"], False)
            model["downloaded"] = model["size"] if model["installed"] else 0
        status.update({
            "installed": nodes_ready and all(available.values()),
            "downloaded": sum(model["size"] for model in status["models"] if model["installed"]),
            "state": "complete" if nodes_ready and all(available.values()) else "remote_missing",
            "progress": round(sum(model["size"] for model in status["models"] if model["installed"]) / self.total * 100, 2),
            "error": "" if nodes_ready else "遠端 ComfyUI 尚未提供 Music 3 節點，請先更新遠端 ComfyUI。",
        })
        return status

    async def start(self) -> dict[str, Any]:
        if self.comfy.mode != "local":
            raise Music3Error("遠端引擎的模型要由遠端主機管理員安裝；請切換本機引擎後再使用自動安裝。")
        if not (self.comfy.comfy_dir / "main.py").exists():
            raise Music3Error("找不到目前設定的本機 ComfyUI 資料夾。")
        if self.task and not self.task.done():
            return self.public_status()
        self.cancel_event = asyncio.Event()
        self.state = "downloading"
        self.error = ""
        self.task = asyncio.create_task(self._run())
        return self.public_status()

    async def cancel(self) -> dict[str, Any]:
        if self.task and not self.task.done():
            self.state = "cancelling"
            self.cancel_event.set()
        return self.public_status()

    async def _run(self) -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                pending = [
                    (model, self._model_path(model)) for model in MUSIC3_MODELS
                    if not self._model_path(model).exists()
                    or self._model_path(model).stat().st_size < int(model["size"])
                ]
                if pending:
                    self.current = f"並行下載 {len(pending)} 個 Music 3 模型"
                    for _, target in pending:
                        target.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.gather(*(self._download(session, model, target) for model, target in pending))
            self.state = "complete"
            self.current = "模型安裝完成"
            self.comfy._model_cache = None
        except asyncio.CancelledError:
            self.state = "cancelled"
            self.current = "下載已暫停，可稍後續傳"
        except Exception as error:
            self.state = "failed"
            self.error = str(error)
            self.current = "模型安裝失敗"

    async def _download(self, session: aiohttp.ClientSession, model: dict[str, Any], target: Path) -> None:
        part = target.with_suffix(target.suffix + ".part")
        expected = int(model["size"])
        offset = part.stat().st_size if part.exists() else 0
        if offset == expected:
            part.replace(target)
            return
        if offset > expected:
            part.unlink()
            offset = 0
        if expected - offset > 512 * 1024**2:
            await self._download_segmented(session, model, part, offset, expected)
            part.replace(target)
            return
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        started = time.monotonic()
        start_offset = offset
        async with session.get(str(model["url"]), headers=headers, allow_redirects=True) as response:
            if response.status not in ({206} if offset else {200, 206}):
                raise Music3Error(f"下載 {model['label']} 失敗：HTTP {response.status}")
            if offset and response.status == 200:
                offset = 0
                start_offset = 0
            mode = "ab" if offset else "wb"
            with part.open(mode) as handle:
                unflushed = 0
                async for chunk in response.content.iter_chunked(4 * 1024**2):
                    if self.cancel_event.is_set():
                        raise asyncio.CancelledError
                    handle.write(chunk)
                    offset += len(chunk)
                    unflushed += len(chunk)
                    if unflushed >= 64 * 1024**2:
                        handle.flush()
                        unflushed = 0
                    elapsed = max(0.01, time.monotonic() - started)
                    self.speed_bps = (offset - start_offset) / elapsed
        if part.stat().st_size < expected:
            raise Music3Error(f"{model['label']} 下載不完整，將保留進度供下次續傳。")
        part.replace(target)

    async def _download_segmented(
        self,
        session: aiohttp.ClientSession,
        model: dict[str, Any],
        part: Path,
        offset: int,
        expected: int,
    ) -> None:
        segment_count = 8
        remaining = expected - offset
        segment_size = math.ceil(remaining / segment_count)
        segments: list[tuple[int, int, Path]] = []
        for index in range(segment_count):
            start = offset + index * segment_size
            if start >= expected:
                break
            end = min(expected - 1, start + segment_size - 1)
            chunk = part.parent / f"{part.name}.{start}-{end}.chunk"
            segments.append((start, end, chunk))

        async def download_segment(start: int, end: int, chunk: Path) -> None:
            required = end - start + 1
            completed = chunk.stat().st_size if chunk.exists() else 0
            if completed > required:
                chunk.unlink()
                completed = 0
            if completed == required:
                return
            headers = {"Range": f"bytes={start + completed}-{end}"}
            began = time.monotonic()
            initial = completed
            async with session.get(str(model["url"]), headers=headers, allow_redirects=True) as response:
                if response.status != 206:
                    raise Music3Error(f"{model['label']} 分段續傳失敗：HTTP {response.status}")
                with chunk.open("ab") as handle:
                    unflushed = 0
                    async for data in response.content.iter_chunked(4 * 1024**2):
                        if self.cancel_event.is_set():
                            raise asyncio.CancelledError
                        handle.write(data)
                        completed += len(data)
                        unflushed += len(data)
                        if unflushed >= 32 * 1024**2:
                            handle.flush()
                            unflushed = 0
                        elapsed = max(0.01, time.monotonic() - began)
                        self.speed_bps = (completed - initial) / elapsed
            if chunk.stat().st_size != required:
                raise Music3Error(f"{model['label']} 的一個下載分段不完整，將保留進度供下次續傳。")

        await asyncio.gather(*(download_segment(*segment) for segment in segments))

        def join_segments() -> None:
            with part.open("ab") as output:
                for _, _, chunk in segments:
                    with chunk.open("rb") as source:
                        while data := source.read(16 * 1024**2):
                            output.write(data)
                    chunk.unlink()
            if part.stat().st_size != expected:
                raise Music3Error(f"{model['label']} 合併後大小不正確。")

        await asyncio.to_thread(join_segments)


class MusicJobManager:
    def __init__(self, comfy: ComfyClient, data_dir: Path, gpu_lock: asyncio.Lock, installer: Music3Installer):
        self.comfy = comfy
        self.job_dir = data_dir / MUSIC_JOB_DIR_NAME
        self.output_dir = data_dir / MUSIC_OUTPUT_DIR_NAME
        self.gpu_lock = gpu_lock
        self.installer = installer
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        for path in self.job_dir.glob("*.json"):
            if path.name.endswith((".request.json", ".workflow.json")):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") in {"queued", "preparing", "running"}:
                job["status"] = "interrupted"
                job["error"] = "工具上次關閉時音樂尚未完成，可按接續重新送出。"
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
        compiled = compile_music_request(payload)
        if self.comfy.mode == "local" and not self.installer.public_status()["installed"]:
            raise Music3Error("Music 3 模型尚未安裝完成，請先按「安裝 Music 3 模型」。")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "type": "music3",
            "name": compiled["name"],
            "favorite": False,
            "mode": compiled["mode"],
            "status": "queued",
            "progress": 0,
            "current_node": "等待 GPU",
            "error": None,
            "prompt_id": None,
            "output": None,
            "duration": compiled["duration"],
            "seed": compiled["seed"],
            "format": compiled["format"],
            "caption": compiled["caption"],
            "lyrics": compiled["lyrics"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.jobs[job_id] = job
        self.cancel_events[job_id] = asyncio.Event()
        self._persist(job)
        (self.job_dir / f"{job_id}.request.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.job_dir / f"{job_id}.prompt.txt").write_text(compiled["caption"], encoding="utf-8")
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, compiled))
        return job

    async def _run(self, job_id: str, compiled: dict[str, Any]) -> None:
        cancel_event = self.cancel_events[job_id]
        try:
            async with self.gpu_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                self.update(job_id, status="preparing", current_node="檢查 ComfyUI 與 Music 3 模型", error=None)
                await self.comfy.ensure_running()
                output_stem = filename_stem(self.jobs[job_id].get("name"))
                workflow = build_music3_workflow(compiled, output_stem)
                (self.job_dir / f"{job_id}.workflow.json").write_text(
                    json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                started = datetime.now(timezone.utc)
                self.update(job_id, status="running", generation_started_at=started.isoformat(), current_node="送出 Music 3 工作流")

                async def callback(event: dict[str, Any]) -> None:
                    changes = dict(event)
                    node_id = str(changes.get("current_node") or "")
                    if node_id in workflow:
                        changes["current_node"] = workflow[node_id].get("_meta", {}).get("title", node_id)
                    self.update(job_id, **changes)

                prompt_id, history = await self.comfy.run_prompt(workflow, callback, cancel_event)
                output = find_audio_output(history)
                if not output:
                    raise Music3Error("ComfyUI 已完成，但沒有回傳音訊檔案。")
                content, _ = await self.comfy.fetch_output(output)
                suffix = Path(output["filename"]).suffix or f".{compiled['format']}"
                local_path = self.output_dir / f"{job_id}{suffix}"
                await asyncio.to_thread(local_path.write_bytes, content)
                finished = datetime.now(timezone.utc)
                self.update(
                    job_id,
                    status="completed",
                    progress=100,
                    current_node=None,
                    error=None,
                    prompt_id=prompt_id,
                    output=output,
                    local_output=local_path.name,
                    finished_at=finished.isoformat(),
                    execution_seconds=round((finished - started).total_seconds(), 3),
                )
        except asyncio.CancelledError:
            self.update(job_id, status="cancelled", current_node=None, error="音樂工作已取消。")
        except Exception as error:
            self.update(job_id, status="failed", current_node=None, error=str(error))

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise Music3Error("找不到音樂工作。")
        if job.get("status") not in {"queued", "preparing", "running"}:
            return job
        self.cancel_events.setdefault(job_id, asyncio.Event()).set()
        self.update(job_id, current_node="正在取消")
        return self.jobs[job_id]

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise Music3Error("找不到音樂工作。")
        if job.get("status") not in {"failed", "cancelled", "interrupted"}:
            raise Music3Error("只有失敗、取消或中斷的音樂工作可以接續。")
        request_path = self.job_dir / f"{job_id}.request.json"
        if not request_path.exists():
            raise Music3Error("這筆舊工作沒有保留生成設定。")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        compiled = compile_music_request(payload)
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
        records = sorted(
            self.jobs.values(),
            key=lambda job: (not bool(job.get("favorite")), str(job.get("created_at") or "")),
            reverse=False,
        )
        favorites = [job for job in records if job.get("favorite")]
        others = sorted((job for job in records if not job.get("favorite")), key=lambda job: str(job.get("created_at") or ""), reverse=True)
        records = sorted(favorites, key=lambda job: str(job.get("created_at") or ""), reverse=True) + others
        total = len(records)
        pages = max(1, math.ceil(total / page_size))
        page = min(max(1, page), pages)
        start = (page - 1) * page_size
        return {"items": records[start:start + page_size], "page": page, "page_size": page_size, "total": total, "total_pages": pages}
