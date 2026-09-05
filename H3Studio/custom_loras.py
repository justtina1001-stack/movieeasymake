"""User-selected model adapters; paths are relative to the active ComfyUI catalog."""
import math
import re


def normalize_loras(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("自訂 LoRA 最多 4 個。")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("LoRA 設定格式錯誤。")
        name = str(item.get("name") or "").strip().replace("\\", "/")
        if not name:
            continue
        if (not name.lower().endswith(".safetensors") or len(name) > 512
                or re.search(r'[\x00-\x1f:]', name)
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise ValueError("LoRA 必須是引擎清單中的相對路徑 .safetensors 檔案。")
        if name.casefold() in seen:
            raise ValueError("同一個 LoRA 不可重複加入。")
        seen.add(name.casefold())
        try:
            strength = float(item.get("strength", 0.5))
        except (ValueError, TypeError):
            raise ValueError("LoRA 強度必須是 0～2 的數字。") from None
        if not math.isfinite(strength) or not 0 <= strength <= 2:
            raise ValueError("LoRA 強度必須是 0～2 的數字。")
        family = item.get("family", "both")
        if family not in {"fl2va", "ref2va", "both"}:
            raise ValueError("LoRA 適用模型必須是 FL2VA、Ref2VA 或兩者。")
        result.append(dict(name=name, strength=strength, family=family,
                           enabled=item.get("enabled", True) is not False))
    return result


def active_loras(items, family):
    return [item for item in items if item["enabled"] and item["strength"] != 0
            and item["family"] in {family, "both"}]
