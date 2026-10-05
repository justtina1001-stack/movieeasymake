"""Pack narrative cuts into H3 generation clips without rewriting their content."""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from typing import Any


class StoryboardPackingError(ValueError):
    """The original cut boundaries cannot be represented safely by H3 clips."""


_NESTED_CUTS_ERROR = (
    "需要合併的切鏡已含片段內 [Shot] 時間碼；無法安全重新編號。"
    "請先將它整理為一個合法的 5～15 秒片段，原稿已保留。"
)


def _groups(shots: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Find an ordered partition; greedy packing can leave an unusable short tail."""
    count = len(shots)
    nested_counts = [0]
    for shot in shots:
        nested = bool(re.search(r"\[Shot\b", shot["action"]))
        if nested and shot["duration"] < 5:
            raise StoryboardPackingError(_NESTED_CUTS_ERROR)
        nested_counts.append(nested_counts[-1] + nested)
    costs: list[int | None] = [None] * (count + 1)
    ends: list[int | None] = [None] * count
    costs[count] = 0
    for start in range(count - 1, -1, -1):
        durations: list[float] = []
        for end in range(start + 1, count + 1):
            durations.append(shots[end - 1]["duration"])
            duration = math.fsum(durations)
            if duration > 15:
                break
            if end - start > 1 and nested_counts[end] != nested_counts[start]:
                # An already legal clip can remain intact while adjacent short cuts pack.
                continue
            if duration >= 5 and costs[end] is not None:
                cost = 1 + costs[end]
                # Prefer fewer clips, then the longer first group for a stable result.
                if costs[start] is None or cost <= costs[start]:
                    costs[start], ends[start] = cost, end
    if costs[0] is None:
        raise StoryboardPackingError(
            "同一場景的敘事切鏡無法按原順序組成 5～15 秒片段；"
            "請重新分配切鏡邊界。系統不會延長片長、拆開動作或跨場景補秒。"
        )
    result = []
    start = 0
    while start < count:
        end = ends[start]
        if end is None:  # Defensive: a reachable partition must have a successor.
            raise StoryboardPackingError("無法完整保留原始切鏡邊界。")
        result.append((start, end))
        start = end
    return result


def _stamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, fraction = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{fraction:03d}"


def _speech(shot: dict[str, Any], speaker_ids: dict[str, str]) -> str:
    dialogue, speaker = shot["dialogue"], shot["speaker"]
    if not dialogue:
        return f"Speaker: {speaker}." if speaker else ""
    if "<d>" in dialogue or "</d>" in dialogue:
        # Retain an explicitly tagged line verbatim. Mixed/unbalanced tags are ambiguous.
        if not re.fullmatch(r"<d>(?:(?!<d>|</d>).)*</d>", dialogue, re.DOTALL):
            raise StoryboardPackingError("對白含混合或未配對的標籤，無法安全合併；請先整理該段對白。")
        tagged = dialogue
    else:
        # Dedicated dialogue fields already use the short-film workspace's Chinese default.
        tagged = f"<d>[Chinese] {dialogue}</d>"
    return f"{speaker} ({speaker_ids[speaker.casefold()]}) says {tagged}."


def _merge(shots: list[dict[str, Any]], speaker_ids: dict[str, str], number: int) -> dict[str, Any]:
    if any(shot["duration"] < 0.001 for shot in shots):
        raise StoryboardPackingError(
            "切鏡間隔小於可表示的毫秒時間碼，合併會造成零秒或重複切點；請先調整切鏡邊界。"
        )
    if any(re.search(r"\[Shot\b", shot["action"]) for shot in shots):
        raise StoryboardPackingError(_NESTED_CUTS_ERROR)
    duration = math.fsum(shot["duration"] for shot in shots)
    parts, camera_parts, sound_parts = [], [], []
    elapsed_durations: list[float] = []
    previous_ms = 0
    for index, shot in enumerate(shots):
        start = math.fsum(elapsed_durations)
        end = math.fsum([*elapsed_durations, shot["duration"]])
        start_ms = round(start * 1000)
        if index and (start_ms <= previous_ms or start_ms / 1000 >= duration):
            raise StoryboardPackingError(
                "切鏡間隔小於可表示的毫秒時間碼，合併會造成零秒或重複切點；請先調整切鏡邊界。"
            )
        if index:
            previous_ms = start_ms
        prefix = f"[Shot {index + 1}] At {_stamp(start_ms)}, " if index else ""
        camera = f"Camera: {shot['camera']}; shot size: {shot['shot_size']}."
        detail = shot.get("camera_detail", "")
        label = f"Cut title: {shot['title']}."
        body = [label, camera, detail, shot["action"], _speech(shot, speaker_ids)]
        if shot["sound"]:
            body.append(f"Sound for this cut: {shot['sound']}")
            sound_parts.append(f"{start:g}–{end:g}s: {shot['sound']}")
        # Earlier endings belong to their cut, while the last remains the clip's ending.
        if index < len(shots) - 1 and shot.get("ending"):
            body.append(f"At the end of this cut: {shot['ending']}")
        parts.append(prefix + " ".join(part for part in body if part))
        camera_parts.append(f"{start:g}–{end:g}s: {camera} {detail}".strip())
        elapsed_durations.append(shot["duration"])
    merged = {
        "title": f"生成片段 {number}（{duration:g} 秒）", "duration": duration,
        "action": "\n".join(parts), "dialogue": "", "speaker": "",
        "sound": "\n".join(sound_parts), "camera": "custom",
        "camera_detail": "\n".join(camera_parts), "shot_size": shots[0]["shot_size"],
    }
    if "ending" in shots[-1]:
        merged["ending"] = shots[-1]["ending"]
    return merged


def pack_storyboard_cuts(
    raw: Any, *, target_duration: float | None = None, validator: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Normalize only short narrative cuts, then validate the actual generation clips.

    The supplied validator uses the same complete schema for source cuts; its private
    flag only permits a positive duration below five seconds during this conversion.
    The final pass always enforces H3's normal five-to-fifteen-second constraint.
    Neither the original model response nor the caller's data is modified.
    """
    source = validator(raw, target_duration=target_duration, _allow_short_cuts=True)
    if not any(shot["duration"] < 5 for scene in source["scenes"] for shot in scene["shots"]):
        return validator(raw, target_duration=target_duration)
    packed = copy.deepcopy(source)
    speaker_ids = {
        character["name"].casefold(): f"S{number}"
        for number, character in enumerate(
            (item for item in source["characters"] if item.get("type", "character") in {"character", "creature"}), 1,
        )
    }
    original_count = sum(len(scene["shots"]) for scene in source["scenes"])
    provenance = []
    clip_number = 0
    for scene_index, scene in enumerate(packed["scenes"]):
        original_shots = source["scenes"][scene_index]["shots"]
        groups = _groups(original_shots) if any(shot["duration"] < 5 for shot in original_shots) else [
            (index, index + 1) for index in range(len(original_shots))
        ]
        scene["shots"] = []
        for start, end in groups:
            clip_number += 1
            group = original_shots[start:end]
            scene["shots"].append(copy.deepcopy(group[0]) if len(group) == 1 else _merge(group, speaker_ids, clip_number))
            provenance.append({
                "scene_index": scene_index, "generation_clip_index": len(scene["shots"]) - 1,
                "source_shot_indices": list(range(start, end)),
                "cuts": copy.deepcopy(raw["scenes"][scene_index]["shots"][start:end]),
            })
    result = validator(packed, target_duration=target_duration)
    result["storyboard_normalization"] = {
        "original_shot_count": original_count,
        "generation_clip_count": clip_number,
        "clip_durations": [shot["duration"] for scene in result["scenes"] for shot in scene["shots"]],
        "source_cuts": provenance,
    }
    return result
