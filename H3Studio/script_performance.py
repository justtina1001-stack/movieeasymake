"""Deterministic reading views for writing outputs; never alter generation data.

Labels are Traditional Chinese. Source prose, dialogue and reference labels retain
their original language. Unrecognized structured screenplays stay byte-for-byte
unchanged so the writing service's own validation remains authoritative.
"""
from __future__ import annotations

import ast
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_CONTENT_CHARS = 300_000
MAX_LITERAL_NODES = 20_000
MAX_LITERAL_DEPTH = 32
_CUT = re.compile(r"\[Shot\s+([1-9]\d*)\]\s+At\s+(\d{2,}):(\d{2})\.(\d{3}),\s*", re.I)
_NUMBER = re.compile(r"(?:\d+(?:\.\d+)?)(?:\s*(?:s|秒))?", re.I)
_CHAR_KEYS = {"name", "position", "action", "expression", "appearance", "description", "reference", "type"}
_ROW_KEYS = {
    "title", "start", "end", "duration", "shot_spec", "shot_size", "camera", "camera_detail",
    "location", "time_of_day", "description", "characters", "position", "spatial_relationship",
    "action", "expression", "camera_movement", "vfx", "effects", "sound", "dialogue",
    "speaker", "ending",
}
_SCENE_KEYS = {"title", "location", "time_of_day", "description", "shots"}
_TOP_KEYS = {
    "title", "genre", "style", "voice", "sound_rules", "synopsis", "character_references",
    "scene_references", "characters", "scenes", "shots", "duration", "storyboard_normalization",
}


def _seconds(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Invalid time")
    text = str(value).strip()
    if not _NUMBER.fullmatch(text):
        raise ValueError("Invalid time")
    number = Decimal(re.sub(r"\s*(?:s|秒)$", "", text, flags=re.I))
    if not number.is_finite() or number < 0 or number > 86_400:
        raise ValueError("Invalid time")
    return number


def _stamp(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)
    return text if "." in text else text + ".0"


def _range(start: Decimal, end: Decimal) -> str:
    return f"{_stamp(start)}–{_stamp(end)}s"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Expected source text")
    return value


def _present(*values: Any) -> str:
    return "\n".join(_text(value) for value in values if value is not None and value != "")


def _names_and_details(values: Any) -> str:
    if values is None:
        return ""
    if not isinstance(values, list):
        raise ValueError("Invalid character list")
    parts = []
    for item in values:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            name = _text(item.get("name", ""))
            detail = _present(item.get("position"), item.get("description"), item.get("appearance"))
            parts.append(f"{name}：{detail}" if name and detail else name or detail)
        else:
            raise ValueError("Invalid character")
    return "\n".join(part for part in parts if part)


def _character_actions(values: Any) -> str:
    if values is None:
        return ""
    if not isinstance(values, list):
        raise ValueError("Invalid characters")
    parts = []
    for item in values:
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            raise ValueError("Invalid character")
        action = _present(item.get("action"), item.get("expression"))
        if action:
            name = _text(item.get("name", ""))
            parts.append(f"{name}：{action}" if name else action)
    return "\n".join(parts)


def _references(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ValueError("Invalid reference labels")
        return "\n".join(f"{name}＝{reference}" for name, reference in value.items())
    if not isinstance(value, list):
        raise ValueError("Invalid reference labels")
    parts = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Invalid reference card")
        name = _text(item.get("name", ""))
        reference = _text(item.get("reference", ""))
        appearance = _present(item.get("appearance"), item.get("description"))
        parts.append("；".join(part for part in (
            f"{name}＝{reference}" if reference else name, appearance,
        ) if part))
    return "\n".join(part for part in parts if part)


def _header(value: dict[str, Any]) -> list[str]:
    lines = [f"片名：{_text(value.get('title', '')) or '未提供'}"]
    for label, key in (("類型", "genre"), ("全片風格", "style"), ("故事摘要", "synopsis")):
        if value.get(key):
            lines.append(f"{label}：{_text(value[key])}")
    sound = _present(value.get("sound_rules"), value.get("voice"))
    lines.append(f"全片聲音規則：{sound or '依分鏡原始聲音設定'}")
    refs = _present(_references(value.get("character_references")), _references(value.get("scene_references")))
    if not refs and value.get("characters"):
        refs = _references(value["characters"])
    if refs:
        lines.extend(["角色／場景參考（依原稿，圖片以對照卡為準）：", refs])
    return lines


def _fields(row: dict[str, Any], scene: dict[str, Any], *, clip_shared: bool = False) -> list[str]:
    scope = "（生成片段原文）" if clip_shared else ""
    framing = _present(row.get("shot_spec"), row.get("shot_size"))
    location = _present(row.get("location"), row.get("time_of_day"), scene.get("location"), scene.get("time_of_day"))
    positions = _present(_names_and_details(row.get("characters")), row.get("position"),
                         row.get("spatial_relationship"), row.get("description"), scene.get("description"))
    action = _present(row.get("action"), row.get("expression"), _character_actions(row.get("characters")))
    camera = _present(row.get("camera_movement"), row.get("camera"), row.get("camera_detail"))
    sound = _text(row.get("sound", ""))
    dialogue = _text(row.get("dialogue", ""))
    speaker = _text(row.get("speaker", ""))
    if speaker:
        dialogue = f"{speaker}：{dialogue}" if dialogue else f"說話角色：{speaker}（未另提供台詞）"
    if not dialogue and "<d>" in action:
        dialogue = "台詞已包含於動作原文，未另列對白。"
    elif not dialogue:
        dialogue = "無（原文未另提供對白）"
    return [
        f"鏡頭／景別{scope}：{framing or '未提供'}",
        f"地點：{location or '未提供'}",
        f"角色／位置與空間關係：{positions or '未提供；請參閱動作原文'}",
        f"動作／表情：{action or '未提供'}",
        f"運鏡{scope}：{camera or '未提供'}",
        f"VFX{scope}：{_present(row.get('vfx'), row.get('effects')) or '未提供'}",
        f"聲音{scope}：{sound or '未提供'}",
        f"對白{scope}：{dialogue}",
        f"收尾{scope}：{_text(row.get('ending', '')) or '未提供'}",
    ]


def _internal_cuts(shot: dict[str, Any]) -> list[tuple[Decimal, Decimal, dict[str, Any]]]:
    """Only split existing, valid local timestamps; never infer cuts from prose."""
    duration = _seconds(shot["duration"])
    action = _text(shot.get("action", ""))
    matches = list(_CUT.finditer(action))
    if not matches:
        return [(Decimal(0), duration, shot)]
    starts = [Decimal(0)]
    for index, match in enumerate(matches, 2):
        moment = Decimal(match[2]) * 60 + Decimal(match[3]) + Decimal(match[4]) / 1000
        if int(match[1]) != index or int(match[3]) >= 60 or not starts[-1] < moment < duration:
            return [(Decimal(0), duration, shot)]
        starts.append(moment)
    if not action[:matches[0].start()].strip():
        return [(Decimal(0), duration, shot)]
    offsets = [0, *[match.start() for match in matches], len(action)]
    result = []
    for index, start in enumerate(starts):
        row = dict(shot)
        # Keep the [Shot] marker and exact local time in the original action text.
        row["action"] = action[offsets[index]:offsets[index + 1]]
        result.append((start, starts[index + 1] if index + 1 < len(starts) else duration, row))
    return result


def storyboard_to_performance(storyboard: dict[str, Any]) -> str:
    """Render a reading view, retaining source cuts and their local/global ranges.

    Unsupported or inconsistent structures return an empty string. This function
    does not validate model readiness, translate prose, or mutate the storyboard.
    """
    try:
        if not isinstance(storyboard, dict) or not isinstance(storyboard.get("scenes"), list):
            return ""
        source = (storyboard.get("storyboard_normalization") or {}).get("source_cuts", [])
        if not isinstance(source, list):
            return ""
        provenance = {}
        for group in source:
            if not isinstance(group, dict):
                return ""
            scene_index, clip_index = group.get("scene_index"), group.get("generation_clip_index")
            if type(scene_index) is not int or type(clip_index) is not int or min(scene_index, clip_index) < 0:
                return ""
            key = scene_index, clip_index
            if key in provenance or not isinstance(group.get("cuts"), list) or not group["cuts"]:
                return ""
            provenance[key] = group
        lines = _header(storyboard)
        elapsed = Decimal(0)
        number = clip_number = 0
        visited = set()
        for scene_index, scene in enumerate(storyboard["scenes"]):
            if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
                return ""
            for clip_index, clip in enumerate(scene["shots"]):
                if not isinstance(clip, dict):
                    return ""
                duration = _seconds(clip.get("duration"))
                if duration <= 0:
                    return ""
                clip_number += 1
                group = provenance.get((scene_index, clip_index))
                rows = []
                if group:
                    visited.add((scene_index, clip_index))
                    indices = group.get("source_shot_indices")
                    if not isinstance(indices, list) or len(indices) != len(group["cuts"]):
                        return ""
                    local = Decimal(0)
                    for original_index, row in zip(indices, group["cuts"]):
                        if type(original_index) is not int or original_index < 0 or not isinstance(row, dict):
                            return ""
                        end = local + _seconds(row.get("duration"))
                        if end <= local:
                            return ""
                        rows.append((local, end, row, original_index + 1))
                        local = end
                    if abs(local - duration) > Decimal("0.001"):
                        return ""
                else:
                    rows = [(start, end, row, None) for start, end, row in _internal_cuts(clip)]
                shared = group is None and len(rows) > 1
                for start, end, row, original_number in rows:
                    number += 1
                    title = _text(row.get("title", "")) or f"段落 {number}"
                    lines.extend(["", f"{number}. {_range(elapsed + start, elapsed + end)}｜{title}"])
                    mapping = f"生成片段 {clip_number}（全片 {_range(elapsed, elapsed + duration)}）；片段內 {_range(start, end)}"
                    if original_number is not None:
                        mapping += f"；場次 {scene_index + 1} 原始鏡頭 {original_number}"
                    lines.append(f"時間對應：{mapping}")
                    if shared:
                        lines.append("未提供逐切鏡欄位；運鏡、聲音、對白及收尾保留生成片段原文。")
                    lines.extend(_fields(row, scene, clip_shared=shared))
                elapsed += duration
        if not number or visited != set(provenance):
            return ""
        return "\n".join(lines)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, OverflowError):
        return ""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def _bounded_tree(tree: Any, *, literal: bool = False) -> bool:
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_LITERAL_NODES or depth > MAX_LITERAL_DEPTH:
            return False
        if literal:
            if not isinstance(node, (ast.Expression, ast.Dict, ast.List, ast.Tuple, ast.Constant,
                                     ast.Load, ast.UnaryOp, ast.USub, ast.UAdd)):
                return False
            if isinstance(node, ast.Dict):
                keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
                if len(keys) != len(node.keys) or len(keys) != len(set(keys)):
                    return False
            pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        elif isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                return False
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, (list, tuple)):
            pending.extend((child, depth + 1) for child in node)
        elif not isinstance(node, (str, int, float, bool, type(None))):
            return False
    return True


def _known_screenplay(value: dict[str, Any]) -> str:
    if not set(value) <= _TOP_KEYS:
        return ""
    scenes = value.get("scenes")
    if isinstance(scenes, list) and scenes and all(isinstance(scene, dict) and "shots" in scene for scene in scenes):
        if any(not set(scene) <= _SCENE_KEYS or not isinstance(scene["shots"], list) or
               any(not isinstance(row, dict) or not set(row) <= _ROW_KEYS for row in scene["shots"])
               for scene in scenes):
            return ""
        return storyboard_to_performance(value)
    rows = value.get("shots") if "shots" in value else scenes
    if not isinstance(rows, list) or not rows:
        return ""
    lines = _header(value)
    elapsed = Decimal(0)
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not set(row) <= _ROW_KEYS:
            return ""
        characters = row.get("characters")
        if characters is not None:
            if not isinstance(characters, list) or any(not isinstance(item, (str, dict)) or
                    isinstance(item, dict) and not set(item) <= _CHAR_KEYS for item in characters):
                return ""
        if "start" in row and "end" in row:
            start, end = _seconds(row["start"]), _seconds(row["end"])
        elif "duration" in row and "start" not in row and "end" not in row:
            start, end = elapsed, elapsed + _seconds(row["duration"])
        else:
            return ""
        if end <= start:
            return ""
        if "duration" in row and abs(_seconds(row["duration"]) - (end - start)) > Decimal("0.001"):
            return ""
        if not any(row.get(key) for key in ("action", "shot_spec", "characters")):
            return ""
        title = _text(row.get("title", "")) or f"段落 {number}"
        lines.extend(["", f"{number}. {_range(start, end)}｜{title}", *_fields(row, {})])
        elapsed = end
    return "\n".join(lines)


def normalize_screenplay(content: str) -> str:
    """Convert only bounded, complete JSON/literal screenplay structures to prose.

    Ordinary text, ambiguous fields and unsafe/truncated structured outputs are
    returned unchanged. No eval, imports, calls, translations or model requests.
    """
    if not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS:
        return content
    candidate = content.strip()
    fence = re.fullmatch(r"```(?:json|python)?\s*\n(.*?)\n```", candidate, re.S | re.I)
    if fence:
        candidate = fence[1].strip()
    if not candidate.startswith("{") or not candidate.endswith("}"):
        return content
    try:
        try:
            value = json.loads(candidate, object_pairs_hook=_unique_pairs)
        except json.JSONDecodeError:
            tree = ast.parse(candidate, mode="eval")
            if not _bounded_tree(tree, literal=True):
                return content
            value = ast.literal_eval(tree)
        if not isinstance(value, dict) or not _bounded_tree(value):
            return content
        readable = _known_screenplay(value)
        return readable or content
    except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError, InvalidOperation, OverflowError):
        return content
