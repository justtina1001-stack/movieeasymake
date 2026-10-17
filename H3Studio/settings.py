from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class SettingsError(ValueError):
    pass


@dataclass(slots=True)
class ConnectionSettings:
    studio_role: str = "client"
    mode: str = "local"
    base_url: str = "http://127.0.0.1:8188"
    comfy_dir: str = "../ComfyUI"
    auto_start_local: bool = True
    remote_access_token: str = ""
    local_base_url: str = ""
    remote_base_url: str = ""

    def normalized(self, app_dir: Path) -> "ConnectionSettings":
        studio_role = str(self.studio_role).strip().lower()
        if studio_role not in {"host", "client"}:
            raise SettingsError("工作站角色只能是 host 或 client。")
        mode = str(self.mode).strip().lower()
        if mode not in {"local", "remote"}:
            raise SettingsError("連線模式只能是 local 或 remote。")

        def normalize_url(value: Any, *, optional: bool = False) -> str:
            result = str(value or "").strip().rstrip("/")
            if not result and optional:
                return ""
            parsed = urlparse(result)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise SettingsError("ComfyUI 網址必須是有效的 http:// 或 https:// 網址。")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise SettingsError("ComfyUI 網址不可包含帳號、密碼、查詢參數或片段。")
            return result

        base_url = normalize_url(self.base_url)
        local_base_url = normalize_url(self.local_base_url, optional=True)
        remote_base_url = normalize_url(self.remote_base_url, optional=True)
        # Preserve existing custom URLs. Mode chooses the compute policy, so it is
        # not safe to silently replace a non-loopback local installation's URL.
        if mode == "local":
            local_base_url = base_url
        else:
            remote_base_url = base_url
        local_base_url = local_base_url or "http://127.0.0.1:8188"

        comfy_value = str(self.comfy_dir).strip() or "../ComfyUI"
        comfy_path = Path(comfy_value).expanduser()
        if not comfy_path.is_absolute():
            comfy_path = (app_dir / comfy_path).resolve()
        else:
            comfy_path = comfy_path.resolve()

        return ConnectionSettings(
            studio_role=studio_role,
            mode=mode,
            base_url=base_url,
            comfy_dir=str(comfy_path),
            auto_start_local=bool(self.auto_start_local),
            remote_access_token=str(self.remote_access_token or "").strip(),
            local_base_url=local_base_url,
            remote_base_url=remote_base_url,
        )

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("remote_access_token", None)
        result["has_remote_access_token"] = bool(self.remote_access_token)
        return result

    def persisted_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsStore:
    def __init__(self, path: Path, app_dir: Path):
        self.path = path
        self.app_dir = app_dir
        self.load_error: str | None = None
        try:
            self.current = self._load()
        except SettingsError as error:
            self.load_error = str(error)
            self.current = ConnectionSettings().normalized(self.app_dir)

    def _load(self) -> ConnectionSettings:
        defaults = ConnectionSettings().normalized(self.app_dir)
        if not self.path.exists():
            return defaults
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return ConnectionSettings(
                studio_role=payload.get("studio_role", defaults.studio_role),
                mode=payload.get("mode", defaults.mode),
                base_url=payload.get("base_url", defaults.base_url),
                comfy_dir=payload.get("comfy_dir", defaults.comfy_dir),
                auto_start_local=payload.get("auto_start_local", defaults.auto_start_local),
                remote_access_token=payload.get("remote_access_token", defaults.remote_access_token),
                local_base_url=payload.get("local_base_url", ""),
                remote_base_url=payload.get("remote_base_url", ""),
            ).normalized(self.app_dir)
        except (OSError, json.JSONDecodeError, TypeError, SettingsError) as error:
            raise SettingsError(f"無法讀取連線設定：{error}") from error

    def update(self, payload: dict[str, Any]) -> ConnectionSettings:
        submitted_token = str(payload.get("remote_access_token") or "").strip()
        remote_access_token = submitted_token or self.current.remote_access_token
        if payload.get("clear_remote_access_token") is True:
            remote_access_token = ""
        mode = str(payload.get("mode", self.current.mode)).strip().lower()
        remembered_url = self.current.remote_base_url if mode == "remote" else self.current.local_base_url
        base_url = payload.get("base_url", self.current.base_url if mode == self.current.mode else remembered_url)
        if not base_url and mode == "remote":
            raise SettingsError("第一次切換遠端模式時，請填寫 GPU 主機的 Gateway 網址。")
        candidate = ConnectionSettings(
            studio_role=self.current.studio_role,
            mode=mode,
            base_url=base_url,
            comfy_dir=payload.get("comfy_dir", self.current.comfy_dir),
            auto_start_local=payload.get("auto_start_local", self.current.auto_start_local),
            remote_access_token=remote_access_token,
            local_base_url=self.current.local_base_url,
            remote_base_url=self.current.remote_base_url,
        ).normalized(self.app_dir)
        self.path.write_text(
            json.dumps(candidate.persisted_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.current = candidate
        return candidate

    def set_studio_role(self, role: str) -> ConnectionSettings:
        candidate = ConnectionSettings(
            studio_role=role,
            mode=self.current.mode,
            base_url=self.current.base_url,
            comfy_dir=self.current.comfy_dir,
            auto_start_local=self.current.auto_start_local,
            remote_access_token=self.current.remote_access_token,
            local_base_url=self.current.local_base_url,
            remote_base_url=self.current.remote_base_url,
        ).normalized(self.app_dir)
        self.path.write_text(
            json.dumps(candidate.persisted_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.current = candidate
        return candidate
