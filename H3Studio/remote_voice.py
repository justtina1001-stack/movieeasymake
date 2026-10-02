"""Authenticated Qwen3-TTS transport over an existing H3 Studio Gateway."""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import json
from pathlib import Path
import re
import wave

import aiohttp


SAFE_ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_REFERENCE_BYTES = 50 * 1024**2


class RemoteVoiceError(ValueError):
    pass


@dataclass(frozen=True)
class RemoteVoiceConnection:
    base_url: str
    token: str = field(repr=False)

    @classmethod
    def from_comfy(cls, comfy):
        token = str(comfy.remote_access_token or "").strip()
        if not token:
            raise RemoteVoiceError("遠端語音需要 GPU 主機的 Gateway 網址與個人金鑰；請在引擎設定填入，不能直接使用 ComfyUI 網址。")
        return cls(str(comfy.base_url).rstrip("/"), token)

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def job_path(self, job_id, suffix=""):
        if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id):
            raise RemoteVoiceError("遠端主機回傳的語音工作識別碼格式錯誤。")
        return f"/api/voice/jobs/{job_id}{suffix}"

    async def check_response(self, response):
        if 200 <= response.status < 300:
            return
        if response.status in {401, 403}:
            raise RemoteVoiceError("遠端語音金鑰無效或已停用；請重新確認個人金鑰。")
        if response.status == 404:
            raise RemoteVoiceError("遠端語音服務或工作不存在；請確認連線的是 Gateway，並更新、重新啟動 GPU 主機的 Studio。")
        detail = ""
        try:
            payload = await response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("error") or "")[:1500].replace(self.token, "[金鑰]")
        except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise RemoteVoiceError(detail or f"遠端語音服務回應失敗（HTTP {response.status}）。")

    async def request(self, method, path, *, payload=None, data=None, timeout=30):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.request(method, f"{self.base_url}{path}", headers=self.headers,
                                           json=payload, data=data, allow_redirects=False) as response:
                    await self.check_response(response)
                    result = await response.json()
                    if not isinstance(result, dict):
                        raise RemoteVoiceError("遠端主機回傳的語音資料格式錯誤，請更新主機 Studio。")
                    return result
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RemoteVoiceError("無法連線或讀取遠端語音服務；請檢查 GPU 主機、Gateway 與網路連線。") from error

    async def status(self):
        result = await self.request("GET", "/api/voice/status", timeout=10)
        if not isinstance(result.get("models"), dict) or not isinstance(result.get("runtime_installed"), bool):
            raise RemoteVoiceError("遠端主機尚未提供相容的語音服務，請更新並重新啟動主機 Studio。")
        return result

    async def upload_reference(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
            raise RemoteVoiceError("遠端聲線參考音訊不存在或超過 50 MB；請裁切後重新上傳。")
        with path.open("rb") as source:
            form = aiohttp.FormData()
            form.add_field("file", source, filename=path.name, content_type="application/octet-stream")
            result = await self.request("POST", "/api/voice/references", data=form, timeout=180)
        if not SAFE_ID.fullmatch(str(result.get("id") or "")):
            raise RemoteVoiceError("遠端主機未回傳有效的參考音訊識別碼。")
        return result["id"]

    async def create(self, compiled, client_job_id, reference_id=""):
        payload = {key: value for key, value in compiled.items() if key != "name"}
        payload.update(job_name=compiled["name"], client_job_id=client_job_id)
        if compiled["mode"] == "clone":
            payload["reference_asset_id"] = reference_id
        result = await self.request("POST", "/api/voice/jobs", payload=payload)
        self.job_path(result.get("id"))
        return result

    async def get(self, job_id):
        return await self.request("GET", self.job_path(job_id))

    async def cancel(self, job_id):
        return await self.request("POST", self.job_path(job_id, "/cancel"))

    async def resume(self, job_id):
        return await self.request("POST", self.job_path(job_id, "/resume"))

    async def download(self, job_id, destination):
        destination = Path(destination)
        temporary = destination.with_suffix(".wav.part")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
                async with session.get(f"{self.base_url}{self.job_path(job_id, '/audio')}",
                                       headers=self.headers, allow_redirects=False) as response:
                    await self.check_response(response)
                    with temporary.open("wb") as output:
                        async for chunk in response.content.iter_chunked(1024**2):
                            output.write(chunk)
            with wave.open(str(temporary), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                    raise RemoteVoiceError("遠端主機沒有產生有效的 WAV 音訊。")
                remaining = audio.getnframes()
                frame_bytes = audio.getnchannels() * audio.getsampwidth()
                while remaining:
                    count = min(remaining, 65536)
                    if len(audio.readframes(count)) != count * frame_bytes:
                        raise RemoteVoiceError("遠端 WAV 音訊下載不完整；可重新送出以取回同一筆主機工作。")
                    remaining -= count
            temporary.replace(destination)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, wave.Error, EOFError) as error:
            raise RemoteVoiceError("遠端語音下載失敗或 WAV 不完整；可重新送出以取回同一筆主機工作。") from error
        finally:
            temporary.unlink(missing_ok=True)
