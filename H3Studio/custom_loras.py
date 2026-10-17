"""User-selected model adapters; paths are relative to the active ComfyUI catalog."""
import math
import re
from lora_catalog import known_lora_family, required_trigger, trigger_placement, requires_source_video


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
        known_family = known_lora_family(name)
        if known_family:
            if family not in {known_family, "both"}:
                label = "Ref2VA" if known_family == "ref2va" else "FL2VA"
                raise ValueError(f"LoRA「{name}」只適用 {label}，請選擇對應版本。")
            family = known_family
        result.append(dict(name=name, strength=strength, family=family,
                           enabled=item.get("enabled", True) is not False))
    return result


def active_loras(items, family):
    return [item for item in items if item["enabled"] and item["strength"] != 0
            and item["family"] in {family, "both"}]


def validate_loras_for_family(items, family):
    """Validate an explicit workflow choice without discarding disabled rows."""
    if family not in {"fl2va", "ref2va"}:
        raise ValueError("生成模型家族必須是 FL2VA 或 Ref2VA。")
    normalized = normalize_loras(items)
    for item in normalized:
        if item["enabled"] and item["strength"] != 0 and item["family"] not in {family, "both"}:
            target = "Ref2VA" if family == "ref2va" else "FL2VA"
            raise ValueError(f"LoRA「{item['name']}」不適用 {target}；請停用或選擇適用版本。")
    return normalized


def required_lora_triggers(items, family):
    """Only required upstream cues from enabled, applicable, nonzero adapters."""
    return [item["cue"] for item in required_lora_trigger_specs(items, family)]


def required_lora_trigger_specs(items, family):
    result = []
    seen = set()
    for item in active_loras(normalize_loras(items), family):
        cue = required_trigger(item["name"])
        if cue and cue not in seen:
            result.append(dict(cue=cue, placement=trigger_placement(item["name"])))
            seen.add(cue)
    return result


def validate_lora_requirements(items, family, *, has_source_video=False, has_replacement_image=False):
    """Enforce known requirements; preserve legacy filtering for unknown files."""
    normalized = normalize_loras(items)
    for item in normalized:
        if not item["enabled"] or item["strength"] == 0:
            continue
        known_family = known_lora_family(item["name"])
        if known_family and known_family != family:
            target = "Ref2VA" if family == "ref2va" else "FL2VA"
            raise ValueError(f"LoRA「{item['name']}」不適用 {target}；請停用或選擇對應模式。")
        if item["family"] in {family, "both"} and requires_source_video(item["name"]) and not has_source_video:
            raise ValueError("Character Swap LoRA 需要來源影片；請到影片工作室的角色替換模式提供原始表演影片與新角色圖片，或停用此 LoRA。")
        if item["family"] in {family, "both"} and requires_source_video(item["name"]) and not has_replacement_image:
            raise ValueError("Character Swap LoRA 需要新角色參考圖片；請在角色或生物參考欄位提供圖片，或停用此 LoRA。")
    return normalized
