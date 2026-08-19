from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web


SAFE_NAME = re.compile(r"[^\w\-\u4e00-\u9fff]+", re.UNICODE)
ACTIVE_QUEUE_KEYS = {"queue_running", "queue_pending"}


class GatewayError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_label(value: Any) -> str:
    label = " ".join(str(value or "").split())
    if not label:
        raise GatewayError("請輸入使用者名稱。")
    if len(label) > 60:
        raise GatewayError("使用者名稱最多 60 個字。")
    return label


class GatewayStore:
    def __init__(self, data_dir: Path):
        self.config_path = data_dir / "shared_gateway.json"
        self.state_path = data_dir / "shared_gateway_state.json"
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        self.state = self._load_state()

    def _load_config(self) -> dict[str, Any]:
        defaults = {
            "enabled": False,
            "host": "0.0.0.0",
            "port": 8190,
            "upstream_url": "http://127.0.0.1:8188",
            "salt": secrets.token_hex(32),
            "users": [],
        }
        if not self.config_path.exists():
            self._write(self.config_path, defaults)
            return defaults
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            result = {**defaults, **payload}
            result["users"] = payload.get("users") if isinstance(payload.get("users"), list) else []
            return result
        except (OSError, json.JSONDecodeError, ValueError):
            return defaults

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"prompt_owners": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            owners = payload.get("prompt_owners") if isinstance(payload, dict) else {}
            return {"prompt_owners": owners if isinstance(owners, dict) else {}}
        except (OSError, json.JSONDecodeError):
            return {"prompt_owners": {}}

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def save_config(self) -> None:
        self._write(self.config_path, self.config)

    def save_state(self) -> None:
        owners = self.state.setdefault("prompt_owners", {})
        if len(owners) > 10_000:
            self.state["prompt_owners"] = dict(list(owners.items())[-8_000:])
        self._write(self.state_path, self.state)

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256((str(self.config["salt"]) + token).encode("utf-8")).hexdigest()

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        digest = self._token_hash(token)
        for user in self.config.get("users", []):
            if user.get("enabled", True) and hmac.compare_digest(str(user.get("token_hash") or ""), digest):
                return user
        return None

    def list_users(self) -> list[dict[str, Any]]:
        return [
            {key: user.get(key) for key in ("id", "name", "enabled", "created_at", "updated_at")}
            for user in self.config.get("users", [])
        ]

    def create_user(self, name: Any) -> tuple[dict[str, Any], str]:
        label = clean_label(name)
        user_id = uuid.uuid4().hex[:16]
        token = "h3g_" + secrets.token_urlsafe(32)
        user = {
            "id": user_id,
            "name": label,
            "token_hash": self._token_hash(token),
            "enabled": True,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.config.setdefault("users", []).append(user)
        self.save_config()
        return self.list_users()[-1], token

    def find_user(self, user_id: str) -> dict[str, Any]:
        user = next((item for item in self.config.get("users", []) if item.get("id") == user_id), None)
        if not user:
            raise GatewayError("找不到共享引擎使用者。")
        return user

    def rotate_user(self, user_id: str) -> tuple[dict[str, Any], str]:
        user = self.find_user(user_id)
        token = "h3g_" + secrets.token_urlsafe(32)
        user.update({"token_hash": self._token_hash(token), "enabled": True, "updated_at": utc_now()})
        self.save_config()
        public = next(item for item in self.list_users() if item["id"] == user_id)
        return public, token

    def set_user_enabled(self, user_id: str, enabled: bool) -> dict[str, Any]:
        user = self.find_user(user_id)
        user.update({"enabled": bool(enabled), "updated_at": utc_now()})
        self.save_config()
        return next(item for item in self.list_users() if item["id"] == user_id)

    def set_prompt_owner(self, prompt_id: str, user_id: str) -> None:
        self.state.setdefault("prompt_owners", {})[prompt_id] = {
            "user_id": user_id,
            "created_at": utc_now(),
        }
        self.save_state()

    def owns_prompt(self, prompt_id: str, user_id: str) -> bool:
        entry = self.state.get("prompt_owners", {}).get(prompt_id)
        return isinstance(entry, dict) and entry.get("user_id") == user_id

    def update_settings(self, enabled: bool, port: Any) -> None:
        try:
            parsed_port = int(port)
        except (TypeError, ValueError) as error:
            raise GatewayError("Gateway 連接埠格式錯誤。") from error
        if not 1024 <= parsed_port <= 65535:
            raise GatewayError("Gateway 連接埠必須在 1024 到 65535 之間。")
        if parsed_port in {8188, 8787}:
            raise GatewayError("Gateway 不可使用 ComfyUI 或 H3 Studio 已占用的連接埠。")
        self.config.update({"enabled": bool(enabled), "port": parsed_port})
        self.save_config()


class SharedComfyGateway:
    def __init__(self, data_dir: Path):
        self.store = GatewayStore(data_dir)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.last_error = ""
        self.upload_dir = data_dir / "gateway_uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def upstream_url(self) -> str:
        return str(self.store.config.get("upstream_url") or "http://127.0.0.1:8188").rstrip("/")

    @property
    def running(self) -> bool:
        return self.runner is not None

    @staticmethod
    def local_addresses(port: int) -> list[str]:
        values = {f"http://127.0.0.1:{port}"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if not address.startswith("127.") and address != "0.0.0.0":
                    values.add(f"http://{address}:{port}")
        except OSError:
            pass
        return sorted(values, key=lambda value: value.startswith("http://127."))

    def public_status(self) -> dict[str, Any]:
        port = int(self.store.config.get("port") or 8190)
        return {
            "enabled": bool(self.store.config.get("enabled")),
            "running": self.running,
            "port": port,
            "upstream_url": self.upstream_url,
            "urls": self.local_addresses(port),
            "users": self.store.list_users(),
            "last_error": self.last_error,
        }

    async def apply_settings(self, enabled: bool, port: Any) -> dict[str, Any]:
        old_port = int(self.store.config.get("port") or 8190)
        self.store.update_settings(enabled, port)
        new_port = int(self.store.config["port"])
        if self.running and (not enabled or old_port != new_port):
            await self.stop()
        if enabled and not self.running:
            await self.start()
        return self.public_status()

    async def start_if_enabled(self) -> None:
        if self.store.config.get("enabled"):
            try:
                await self.start()
            except Exception as error:
                self.last_error = str(error)

    async def start(self) -> None:
        if self.running:
            return
        app = self.create_app()
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(
                runner,
                str(self.store.config.get("host") or "0.0.0.0"),
                int(self.store.config.get("port") or 8190),
            )
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self.runner = runner
        self.site = site
        self.last_error = ""

    async def stop(self) -> None:
        runner = self.runner
        self.runner = None
        self.site = None
        if runner:
            await runner.cleanup()

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=3 * 1024**3)
        app.router.add_get("/system_stats", self.proxy_system_stats)
        app.router.add_get("/object_info/{tail:.*}", self.proxy_object_info)
        app.router.add_post("/upload/image", self.proxy_upload)
        app.router.add_post("/prompt", self.proxy_prompt)
        app.router.add_get("/history/{prompt_id}", self.proxy_history)
        app.router.add_get("/view", self.proxy_view)
        app.router.add_post("/interrupt", self.proxy_interrupt)
        app.router.add_get("/ws", self.proxy_websocket)
        return app

    def _authenticated_user(self, request: web.Request) -> dict[str, Any]:
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        token = token or request.headers.get("X-H3-Access-Token", "").strip()
        user = self.store.authenticate(token)
        if not user:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "共享引擎金鑰無效或已停用。"}, ensure_ascii=False),
                content_type="application/json",
            )
        return user

    async def _forward_json_get(self, request: web.Request, path: str) -> web.Response:
        self._authenticated_user(request)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(f"{self.upstream_url}{path}", params=request.query) as response:
                    body = await response.read()
                    return web.Response(body=body, status=response.status, content_type=response.content_type)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": f"無法連線 GPU 主機上的 ComfyUI：{error}"}, ensure_ascii=False),
                content_type="application/json",
            ) from error

    async def proxy_system_stats(self, request: web.Request) -> web.Response:
        return await self._forward_json_get(request, "/system_stats")

    async def proxy_object_info(self, request: web.Request) -> web.Response:
        tail = request.match_info.get("tail", "").strip("/")
        return await self._forward_json_get(request, f"/object_info/{tail}" if tail else "/object_info")

    async def proxy_upload(self, request: web.Request) -> web.Response:
        user = self._authenticated_user(request)
        reader = await request.multipart()
        filename = "asset.bin"
        content_type = "application/octet-stream"
        upstream_type = "input"
        requested_subfolder = "assets"
        temporary = self.upload_dir / f"{uuid.uuid4().hex}.upload"
        received = False
        try:
            with temporary.open("wb") as handle:
                async for part in reader:
                    if part.name == "image":
                        filename = Path(part.filename or filename).name
                        content_type = part.headers.get("Content-Type", content_type)
                        received = True
                        while chunk := await part.read_chunk(1024**2):
                            handle.write(chunk)
                    elif part.name == "type":
                        upstream_type = (await part.text()).strip() or "input"
                    elif part.name == "subfolder":
                        requested_subfolder = (await part.text()).strip() or "assets"
            if not received:
                raise web.HTTPBadRequest(text='{"error":"沒有收到素材檔案。"}', content_type="application/json")
            safe_subfolder = SAFE_NAME.sub("_", requested_subfolder.replace("\\", "/")).strip("_/") or "assets"
            forced_subfolder = f"H3Gateway/{user['id']}/{safe_subfolder}"
            timeout = aiohttp.ClientTimeout(total=3600)
            with temporary.open("rb") as handle:
                form = aiohttp.FormData()
                form.add_field("image", handle, filename=filename, content_type=content_type)
                form.add_field("type", upstream_type)
                form.add_field("subfolder", forced_subfolder)
                form.add_field("overwrite", "true")
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(f"{self.upstream_url}/upload/image", data=form) as response:
                        body = await response.read()
                        return web.Response(body=body, status=response.status, content_type=response.content_type)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _mapped_client_id(user_id: str, client_id: str) -> str:
        digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:20]
        return f"h3g-{user_id}-{digest}"

    @staticmethod
    def _safe_output_name(value: Any) -> str:
        final = str(value or "output").replace("\\", "/").split("/")[-1]
        final = SAFE_NAME.sub("_", final).strip("_.")
        return final or "output"

    def _rewrite_workflow(self, workflow: dict[str, Any], user_id: str) -> dict[str, Any]:
        rewritten = json.loads(json.dumps(workflow))
        prefix = f"H3Gateway/{user_id}/"
        for node in rewritten.values():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "")
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            if class_type.startswith("Save") and "filename_prefix" in inputs:
                inputs["filename_prefix"] = prefix + self._safe_output_name(inputs.get("filename_prefix"))
            if class_type in {"LoadImage", "LoadVideo", "LoadAudio"}:
                for field in ("image", "file", "audio"):
                    value = inputs.get(field)
                    if isinstance(value, str) and not value.replace("\\", "/").startswith(prefix):
                        raise GatewayError("工作流嘗試讀取其他使用者或未經 Gateway 上傳的素材。")
        return rewritten

    async def proxy_prompt(self, request: web.Request) -> web.Response:
        user = self._authenticated_user(request)
        try:
            payload = await request.json()
            workflow = payload.get("prompt")
            if not isinstance(workflow, dict):
                raise GatewayError("ComfyUI 工作流格式錯誤。")
            original_client_id = str(payload.get("client_id") or uuid.uuid4().hex)
            forwarded = {
                **payload,
                "prompt": self._rewrite_workflow(workflow, str(user["id"])),
                "client_id": self._mapped_client_id(str(user["id"]), original_client_id),
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.post(f"{self.upstream_url}/prompt", json=forwarded) as response:
                    body = await response.read()
                    if response.status == 200:
                        result = json.loads(body)
                        prompt_id = str(result.get("prompt_id") or "")
                        if prompt_id:
                            self.store.set_prompt_owner(prompt_id, str(user["id"]))
                    return web.Response(body=body, status=response.status, content_type=response.content_type)
        except (json.JSONDecodeError, GatewayError) as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}, ensure_ascii=False), content_type="application/json"
            ) from error

    async def proxy_history(self, request: web.Request) -> web.Response:
        user = self._authenticated_user(request)
        prompt_id = request.match_info["prompt_id"]
        if not self.store.owns_prompt(prompt_id, str(user["id"])):
            raise web.HTTPNotFound(text='{"error":"找不到這筆工作。"}', content_type="application/json")
        return await self._forward_json_get(request, f"/history/{prompt_id}")

    async def proxy_view(self, request: web.Request) -> web.StreamResponse:
        user = self._authenticated_user(request)
        subfolder = str(request.query.get("subfolder") or "").replace("\\", "/").strip("/")
        required_prefix = f"H3Gateway/{user['id']}"
        if not (subfolder == required_prefix or subfolder.startswith(required_prefix + "/")):
            raise web.HTTPNotFound(text='{"error":"找不到這個輸出檔案。"}', content_type="application/json")
        timeout = aiohttp.ClientTimeout(total=1800)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.upstream_url}/view", params=request.query) as response:
                headers = {}
                if response.headers.get("Content-Length"):
                    headers["Content-Length"] = response.headers["Content-Length"]
                stream = web.StreamResponse(status=response.status, headers=headers)
                stream.content_type = response.content_type
                await stream.prepare(request)
                async for chunk in response.content.iter_chunked(1024**2):
                    await stream.write(chunk)
                await stream.write_eof()
                return stream

    async def proxy_interrupt(self, request: web.Request) -> web.Response:
        user = self._authenticated_user(request)
        prompt_id = request.headers.get("X-H3-Prompt-ID", "").strip()
        if not prompt_id or not self.store.owns_prompt(prompt_id, str(user["id"])):
            raise web.HTTPNotFound(text='{"error":"找不到這筆工作。"}', content_type="application/json")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(f"{self.upstream_url}/queue") as response:
                queue = await response.json()
            serialized_running = json.dumps(queue.get("queue_running", []))
            serialized_pending = json.dumps(queue.get("queue_pending", []))
            if prompt_id in serialized_running:
                async with session.post(f"{self.upstream_url}/interrupt") as response:
                    return web.Response(body=await response.read(), status=response.status, content_type=response.content_type)
            if prompt_id in serialized_pending:
                async with session.post(f"{self.upstream_url}/queue", json={"delete": [prompt_id]}) as response:
                    return web.Response(body=await response.read(), status=response.status, content_type=response.content_type)
        raise web.HTTPConflict(text='{"error":"這筆工作目前不在 ComfyUI 佇列。"}', content_type="application/json")

    async def proxy_websocket(self, request: web.Request) -> web.WebSocketResponse:
        user = self._authenticated_user(request)
        original_client_id = str(request.query.get("clientId") or "")
        mapped = self._mapped_client_id(str(user["id"]), original_client_id)
        client_ws = web.WebSocketResponse(heartbeat=30)
        await client_ws.prepare(request)
        upstream_ws_url = self.upstream_url.replace("http://", "ws://").replace("https://", "wss://")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
                async with session.ws_connect(f"{upstream_ws_url}/ws?clientId={mapped}", heartbeat=30) as upstream_ws:
                    async def upstream_to_client() -> None:
                        async for message in upstream_ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                await client_ws.send_str(message.data)
                            elif message.type == aiohttp.WSMsgType.BINARY:
                                await client_ws.send_bytes(message.data)
                            elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                                break

                    async def client_to_upstream() -> None:
                        async for message in client_ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                await upstream_ws.send_str(message.data)
                            elif message.type == aiohttp.WSMsgType.BINARY:
                                await upstream_ws.send_bytes(message.data)
                            elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                                break

                    tasks = [asyncio.create_task(upstream_to_client()), asyncio.create_task(client_to_upstream())]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            await client_ws.close()
        return client_ws
