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
    mode: str = "local"
    base_url: str = "http://127.0.0.1:8188"
    comfy_dir: str = "../ComfyUI"
    auto_start_local: bool = True

    def normalized(self, app_dir: Path) -> "ConnectionSettings":
        mode = str(self.mode).strip().lower()
        if mode not in {"local", "remote"}:
            raise SettingsError("連線模式只能是 local 或 remote。")

        base_url = str(self.base_url).strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SettingsError("ComfyUI 網址必須是有效的 http:// 或 https:// 網址。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SettingsError("ComfyUI 網址不可包含帳號、密碼、查詢參數或片段。")

        comfy_value = str(self.comfy_dir).strip() or "../ComfyUI"
        comfy_path = Path(comfy_value).expanduser()
        if not comfy_path.is_absolute():
            comfy_path = (app_dir / comfy_path).resolve()
        else:
            comfy_path = comfy_path.resolve()

        return ConnectionSettings(
            mode=mode,
            base_url=base_url,
            comfy_dir=str(comfy_path),
            auto_start_local=bool(self.auto_start_local),
        )

    def public_dict(self) -> dict[str, Any]:
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
                mode=payload.get("mode", defaults.mode),
                base_url=payload.get("base_url", defaults.base_url),
                comfy_dir=payload.get("comfy_dir", defaults.comfy_dir),
                auto_start_local=payload.get("auto_start_local", defaults.auto_start_local),
            ).normalized(self.app_dir)
        except (OSError, json.JSONDecodeError, TypeError, SettingsError) as error:
            raise SettingsError(f"無法讀取連線設定：{error}") from error

    def update(self, payload: dict[str, Any]) -> ConnectionSettings:
        candidate = ConnectionSettings(
            mode=payload.get("mode", self.current.mode),
            base_url=payload.get("base_url", self.current.base_url),
            comfy_dir=payload.get("comfy_dir", self.current.comfy_dir),
            auto_start_local=payload.get("auto_start_local", self.current.auto_start_local),
        ).normalized(self.app_dir)
        self.path.write_text(
            json.dumps(candidate.public_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.current = candidate
        return candidate
