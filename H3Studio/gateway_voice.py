"""Personal-key access to the GPU host's independent Qwen3-TTS service."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from aiohttp import web

from voice import VoiceError, compile_voice_request, filename_stem, utc_now


SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
MAX_REFERENCE_BYTES = 50 * 1024**2
PUBLIC_JOB_FIELDS = {
    "id", "type", "name", "favorite", "mode", "status", "progress", "current_node",
    "text", "language", "instruct", "speaker", "reference_text", "seed",
    "created_at", "updated_at", "generation_started_at", "finished_at", "execution_seconds",
}


def _error(message: str, status: int = 400, code: str | None = None) -> web.Response:
    result = {"error": message}
    if code:
        result["error_code"] = code
    return web.json_response(result, status=status)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {key: job[key] for key in PUBLIC_JOB_FIELDS if key in job}
    # Worker stderr can contain host paths. Detailed errors stay on the GPU host.
    result["error"] = "GPU 主機語音工作未完成，請聯絡主機管理者查看詳細錯誤。" if job.get("error") else None
    name = str(job.get("local_output") or "").replace("\\", "/").rsplit("/", 1)[-1]
    result["local_output"] = name if name and SAFE_ID.fullmatch(Path(name).stem) else None
    return result


class GatewayVoice:
    def __init__(self, gateway):
        self.gateway = gateway
        self.store = gateway.store

    def _user(self, request: web.Request) -> dict[str, Any]:
        user = self.gateway._authenticated_user(request)
        if any(service is None for service in (
            self.gateway.voice_installer, self.gateway.voice_jobs, self.gateway.voice_assets,
        )):
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "GPU 主機尚未提供遠端語音服務，請更新並重新啟動主機 Studio。",
                                 "error_code": "remote_voice_unavailable"}, ensure_ascii=False),
                content_type="application/json",
            )
        comfy = getattr(self.gateway.voice_jobs, "comfy", None)
        if comfy is not None and getattr(comfy, "mode", None) != "local":
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "GPU 主機的語音服務必須使用本機引擎，請主機管理者檢查引擎設定。",
                                 "error_code": "remote_voice_host_not_local"}, ensure_ascii=False),
                content_type="application/json",
            )
        return user

    def _owns(self, category: str, item_id: str, user_id: str) -> bool:
        entry = self.store.state.get(category, {}).get(item_id)
        return isinstance(entry, dict) and entry.get("user_id") == user_id

    def _owned_job(self, request: web.Request, user: dict[str, Any]) -> dict[str, Any]:
        job_id = request.match_info["job_id"]
        job = self.gateway.voice_jobs.jobs.get(job_id)
        if not SAFE_ID.fullmatch(job_id) or not job or not self._owns("voice_job_owners", job_id, str(user["id"])):
            raise web.HTTPNotFound(text='{"error":"找不到這筆語音工作。"}', content_type="application/json")
        return job

    async def status(self, request: web.Request) -> web.Response:
        self._user(request)
        state = self.gateway.voice_installer.public_status()
        result = {key: state.get(key) for key in (
            "state", "active", "current", "requested_mode", "runtime_installed", "installed",
        )}
        result["models"] = {
            mode: {key: model.get(key) for key in ("label", "repo", "description", "installed")}
            for mode, model in state.get("models", {}).items() if isinstance(model, dict)
        }
        result["error"] = "GPU 主機語音模型安裝未完成，請聯絡主機管理者。" if state.get("error") else ""
        result.update(connection_mode="remote", can_install=False, remote_voice=True)
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    async def upload_reference(self, request: web.Request) -> web.Response:
        user = self._user(request)
        root = Path(self.gateway.voice_assets.directory).resolve()
        asset_id = uuid.uuid4().hex
        temporary = root / f"{asset_id}.upload"
        final: Path | None = None
        metadata_path = root / f"{asset_id}.json"
        saved = False
        try:
            reader = await request.multipart()
            received = False
            size = 0
            name = ""
            extension = ""
            with temporary.open("xb") as handle:
                async for part in reader:
                    if part.name != "file" or received:
                        return _error("請上傳單一語音參考檔案。")
                    name = unquote(str(part.filename or "")).replace("\\", "/").rsplit("/", 1)[-1]
                    extension = Path(name).suffix.lower()
                    if extension not in AUDIO_EXTENSIONS:
                        return _error("參考素材必須是 WAV、MP3、FLAC、M4A、AAC 或 OGG 音訊。")
                    received = True
                    while chunk := await part.read_chunk(1024**2):
                        size += len(chunk)
                        if size > MAX_REFERENCE_BYTES:
                            return _error("語音參考檔案不可超過 50 MB。", 413)
                        handle.write(chunk)
            if not received or size == 0:
                return _error("沒有收到有效的參考音訊。")
            final = root / f"{asset_id}{extension}"
            temporary.replace(final)
            metadata = {"id": asset_id, "name": name, "kind": "voice-reference", "extension": extension,
                        "size": size, "created_at": utc_now()}
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self.store.state.setdefault("voice_asset_owners", {})[asset_id] = {
                "user_id": str(user["id"]), "created_at": utc_now(),
            }
            self.store.save_state()
            saved = True
            return web.json_response(metadata, status=201)
        except (ValueError, OSError):
            return _error("無法接收參考音訊，請確認檔案格式與 GPU 主機的儲存空間。")
        finally:
            temporary.unlink(missing_ok=True)
            if not saved:
                if final is not None:
                    final.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                self.store.state.get("voice_asset_owners", {}).pop(asset_id, None)

    async def create_job(self, request: web.Request) -> web.Response:
        user = self._user(request)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                return _error("語音生成設定格式錯誤。")
            client_id = payload.get("client_job_id")
            if not isinstance(client_id, str) or not SAFE_ID.fullmatch(client_id):
                return _error("語音 client_job_id 必須是 32 位十六進位識別碼。")
            request_key = f"{user['id']}:{client_id}"
            existing = self.store.state.get("voice_client_jobs", {}).get(request_key)
            existing_id = existing.get("job_id") if isinstance(existing, dict) else None
            if existing_id:
                job = self.gateway.voice_jobs.jobs.get(existing_id)
                if job and self._owns("voice_job_owners", existing_id, str(user["id"])):
                    return web.json_response(_public_job(job), status=202)
                return _error("這筆遠端語音工作已不存在，請建立新的語音工作。", 409)
            compiled = compile_voice_request(payload)
            if compiled["mode"] == "clone":
                asset_id = compiled["reference_asset_id"]
                if not SAFE_ID.fullmatch(asset_id) or not self._owns("voice_asset_owners", asset_id, str(user["id"])):
                    return _error("找不到你上傳的語音參考素材。", 404)
                root = Path(self.gateway.voice_assets.directory).resolve()
                try:
                    reference = self.gateway.voice_assets.path_for(asset_id).resolve()
                except (OSError, ValueError):
                    return _error("找不到你上傳的語音參考素材。", 404)
                if reference.parent != root or reference.suffix.lower() not in AUDIO_EXTENSIONS or not reference.is_file():
                    return _error("找不到你上傳的語音參考素材。", 404)
            installer = self.gateway.voice_installer
            if not installer.runtime_installed() or not installer.model_installed(compiled["mode"]):
                return _error("GPU 主機尚未安裝所選語音模型，請主機管理者先在主機完成安裝。", 409,
                              "remote_voice_model_missing")
            submitted = {key: value for key, value in payload.items() if key != "client_job_id"}
            job = self.gateway.voice_jobs.create(submitted)
            self.store.state.setdefault("voice_job_owners", {})[job["id"]] = {
                "user_id": str(user["id"]), "created_at": utc_now(),
            }
            self.store.state.setdefault("voice_client_jobs", {})[request_key] = {
                "job_id": job["id"], "created_at": utc_now(),
            }
            self.store.save_state()
            return web.json_response(_public_job(job), status=202)
        except VoiceError as error:
            return _error(str(error))
        except (json.JSONDecodeError, TypeError):
            return _error("語音生成設定無效，請確認台詞、聲線及參考音訊設定。")
        except OSError:
            return _error("GPU 主機無法儲存語音工作，請聯絡主機管理者。", 503)

    async def get_job(self, request: web.Request) -> web.Response:
        user = self._user(request)
        return web.json_response(_public_job(self._owned_job(request, user)), headers={"Cache-Control": "no-store"})

    async def cancel_job(self, request: web.Request) -> web.Response:
        user = self._user(request)
        job = self._owned_job(request, user)
        try:
            result = await self.gateway.voice_jobs.cancel(job["id"])
            return web.json_response(_public_job(result))
        except VoiceError:
            return _error("這筆語音工作目前無法取消。", 409)

    async def resume_job(self, request: web.Request) -> web.Response:
        user = self._user(request)
        job = self._owned_job(request, user)
        try:
            result = self.gateway.voice_jobs.resume(job["id"]) if job.get("status") in {
                "failed", "cancelled", "interrupted",
            } else job
            return web.json_response(_public_job(result), status=202)
        except (VoiceError, OSError, json.JSONDecodeError):
            return _error("GPU 主機無法重新送出語音工作，請確認主機模型與參考音訊。", 409)

    async def audio(self, request: web.Request) -> web.StreamResponse:
        user = self._user(request)
        job = self._owned_job(request, user)
        path = self.gateway.voice_jobs.local_output_path(job)
        if job.get("status") != "completed" or path is None:
            return _error("語音尚未完成或檔案不存在。", 404)
        root = self.gateway.voice_jobs.output_dir.resolve()
        resolved = path.resolve()
        if resolved.parent != root or resolved.suffix.lower() != ".wav" or not resolved.is_file():
            return _error("語音尚未完成或檔案不存在。", 404)
        headers = {"Cache-Control": "no-store"}
        if request.query.get("download") == "1":
            download_name = f"{filename_stem(job.get('name'))}.wav"
            headers["Content-Disposition"] = (
                f'attachment; filename="voice.wav"; filename*=UTF-8\'\'{quote(download_name, safe="")}'
            )
        return web.FileResponse(resolved, headers=headers)


def register_gateway_voice(app: web.Application, gateway) -> None:
    service = GatewayVoice(gateway)
    app.router.add_get("/api/voice/status", service.status)
    app.router.add_post("/api/voice/references", service.upload_reference)
    app.router.add_post("/api/voice/jobs", service.create_job)
    app.router.add_get("/api/voice/jobs/{job_id}", service.get_job)
    app.router.add_post("/api/voice/jobs/{job_id}/cancel", service.cancel_job)
    app.router.add_post("/api/voice/jobs/{job_id}/resume", service.resume_job)
    app.router.add_get("/api/voice/jobs/{job_id}/audio", service.audio)
