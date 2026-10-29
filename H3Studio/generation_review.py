"""Server-owned final prompt reviews; client previews never supply executable recipes."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone

from domain import CompiledRequest, required_asset_ids


class GenerationReviewError(ValueError):
    status = 409


def review_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def compiled_from_review(value):
    """Restore only a server-saved dataclass, never a client-supplied compilation."""
    return CompiledRequest(**copy.deepcopy(value))


def prompt_review_item(compiled, *, scene_id=None, shot_id=None, title=None, scene_title=None, prepared=None):
    return {"scene_id": scene_id, "shot_id": shot_id, "title": title,
            "scene_title": scene_title, "shot_title": title,
            "prompt": compiled.prompt, "video_model": compiled.video_model,
            "duration": compiled.actual_duration, "width": compiled.width, "height": compiled.height,
            "seed": compiled.seed, "steps": compiled.steps, "quality_mode": compiled.quality_mode,
            "asset_ids": required_asset_ids(compiled), "mapping": copy.deepcopy(compiled.mapping),
            "warnings": list(dict.fromkeys([*compiled.warnings, *((prepared or {}).get("h3_preparation_warnings") or [])])),
            "h3_translation": copy.deepcopy((prepared or {}).get("h3_translation") or {})}


class GenerationReviewStore:
    def __init__(self, *, ttl=900, limit=128):
        self.ttl, self.limit = ttl, limit
        self.records = OrderedDict()

    def issue(self, source, prepared, compiled, *, context=None):
        now = time.monotonic()
        for key in list(self.records):
            if self.records[key]["deadline"] <= now:
                self.records.pop(key)
        while len(self.records) >= self.limit:
            self.records.popitem(last=False)
        token = uuid.uuid4().hex
        self.records[token] = {"deadline": now + self.ttl, "claimed": False,
                               "digests": {review_digest(source), review_digest(prepared)},
                               "prepared": copy.deepcopy(prepared), "compiled": copy.deepcopy(compiled),
                               "context": copy.deepcopy(context)}
        expires = datetime.fromtimestamp(time.time() + self.ttl, timezone.utc).isoformat()
        return {"review_receipt": token, "review_expires_at": expires}

    def claim(self, submitted):
        if not isinstance(submitted, dict):
            raise GenerationReviewError("生成稿確認資料格式錯誤，請重新預覽。")
        payload = copy.deepcopy(submitted)
        token = payload.pop("review_receipt", None)
        record = self.records.get(token) if isinstance(token, str) else None
        if record is None or record["deadline"] <= time.monotonic() or record["claimed"]:
            raise GenerationReviewError("這份生成稿已逾期或已送出，請重新預覽並確認。")
        if review_digest(payload) not in record["digests"]:
            raise GenerationReviewError("生成設定、敘述或素材已改變，請重新預覽並確認生成稿。")
        record["claimed"] = True
        return token, copy.deepcopy(record)

    def release(self, token):
        if token in self.records:
            self.records[token]["claimed"] = False

    def consume(self, token):
        self.records.pop(token, None)
