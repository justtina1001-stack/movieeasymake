"""Deterministic H3 prompt formatting, without changing user speech or sign text."""

from __future__ import annotations

import re
from collections.abc import Callable

from dialogue import _quoted_spans

_OPAQUE = re.compile(r"<d>.*?</d>|<(?:Subject|Picture|Video|Audio)\s+\d+>", re.S)
_SHOT = re.compile(r"\[Shot\s+(\d+)(?:\s*,\s*([\d.]+)s\s*[-–]\s*([\d.]+)s)?\]", re.I)
_TIME = re.compile(r"\s*At\s+(\d+):(\d{2})\.(\d{3})\s*,", re.I)
_MARKDOWN_SHOT = re.compile(
    r"^[ \t]*#{1,6}[ \t]+(?:\*\*)?鏡頭[ \t]+(?P<index>\d+)[ \t]*[：:]"
    r"(?P<title>[^\r\n]*?)[（(][ \t]*(?P<start>\d+(?:\.\d+)?)[ \t]*[-–—～~][ \t]*"
    r"(?P<end>\d+(?:\.\d+)?)[ \t]*秒[ \t]*[）)](?:\*\*)?[ \t]*\r?$", re.M)
_CORE = re.compile(r"^(subject_definitions|summary|retention_analysis|integrated_multimodal_description|detailed_description|overall_soundscape|non_diegetic_music):[ \t]*\n?", re.M)


def map_directions(text: str, transform: Callable[[str], str]) -> str:
    """Protect speech, visible quotations and existing reference labels."""
    spans = [(m.start(), m.end()) for m in _OPAQUE.finditer(text)]
    spans.extend(_quoted_spans(text))
    spans.sort()
    cursor = 0
    result = []
    for start, end in spans:
        if end <= cursor:
            continue
        if start >= cursor:
            result.append(transform(text[cursor:start]))
            result.append(text[start:end])
        else:
            result.append(text[cursor:end])
        cursor = end
    result.append(transform(text[cursor:]))
    return "".join(result)


def rewrite_aliases(text: str, aliases: dict[str, str]) -> str:
    if not aliases:
        return text
    # One substitution pass: inserted labels and aliases are never searched again.
    alternatives = []
    for name in sorted(aliases, key=len, reverse=True):
        if name:
            left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name[0]) else ""
            right = r"(?![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name[-1]) else ""
            alternatives.append(left + re.escape(name) + right)
    pattern = re.compile("|".join(alternatives))
    return map_directions(text, lambda part: pattern.sub(lambda match: aliases[match.group()], part))


def cut_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    minutes, remainder = divmod(millis, 60000)
    secs, ms = divmod(remainder, 1000)
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def normalize_shots(text: str, duration: float, *, add_opening: bool = True) -> tuple[str, int]:
    """Convert legacy storyboard markers and reject invalid actual cut times."""
    visible = text
    # Obtain a same-length mask so dialogue/sign text cannot invent a shot.
    masked = text
    opaque_spans = [(m.start(), m.end()) for m in _OPAQUE.finditer(text)] + list(_quoted_spans(text))
    for start, end in sorted(opaque_spans, reverse=True):
        masked = masked[:start] + " " * (end - start) + masked[end:]
    # Only explicit standalone storyboard headings with both numeric endpoints
    # qualify. Keep quoted/dialogue content opaque and let the existing shot
    # validator handle ordering and duration after this syntax conversion.
    headings = list(_MARKDOWN_SHOT.finditer(masked))
    if headings:
        for heading in reversed(headings):
            title = text[heading.start("title"):heading.end("title")].strip()
            replacement = (f"[Shot {heading.group('index')}, {heading.group('start')}s-"
                           f"{heading.group('end')}s] " + title)
            text = text[:heading.start()] + replacement + text[heading.end():]
        return normalize_shots(text, duration, add_opening=add_opening)
    matches = list(_SHOT.finditer(masked))
    if not matches:
        return (("[Shot 1] " + text) if add_opening else text), 1
    changes = []
    expected, last_time = 1, -1.0
    final_shot = 1
    for match in matches:
        index = int(match.group(1))
        legacy_start, legacy_end = match.group(2), match.group(3)
        timing = _TIME.match(masked, match.end())
        if index == 1 and expected == 2 and last_time < 0 and legacy_start is None and timing is None:
            # Existing short-film envelopes sometimes repeat their opening marker.
            changes.append((match.start(), match.end(), ""))
            continue
        if index != expected:
            raise ValueError(f"分鏡編號必須從 Shot 1 依序增加；目前預期 Shot {expected}，收到 Shot {index}。")
        if legacy_start is not None:
            start = float(legacy_start)
            end = float(legacy_end)
            if end <= start or end > duration + 0.05:
                raise ValueError("分鏡時間範圍必須遞增，且不得超過影片實際時長。")
        elif timing:
            start = int(timing.group(1)) * 60 + int(timing.group(2)) + int(timing.group(3)) / 1000
        else:
            start = 0.0 if index == 1 else None
        if index == 1:
            if start not in {0, 0.0, None}:
                raise ValueError("Shot 1 必須從影片開頭開始。")
            replacement = "[Shot 1]"
            stop = timing.end() if timing else match.end()
        else:
            if start is None:
                raise ValueError(f"Shot {index} 缺少切鏡時間；請使用 At MM:SS.mmm,。")
            if not (0 < start < duration) or start <= last_time:
                raise ValueError("切鏡時間必須嚴格遞增，且落在影片實際時長內。")
            replacement = f"[Shot {index}] At {cut_time(start)},"
            stop = timing.end() if timing else match.end()
            last_time = start
        changes.append((match.start(), stop, replacement))
        final_shot = index
        expected += 1
    for start, end, replacement in reversed(changes):
        visible = visible[:start] + replacement + visible[end:]
    return visible, final_shot


def number_speakers(text: str, names: tuple[str, ...], assigned: dict[str, str]) -> str:
    """Number explicitly named vocal sources, preserving IDs supplied by the user."""
    if not names:
        return text
    def seed_ids(part: str) -> str:
        for name in names:
            for match in re.finditer(re.escape(name) + r"\s*\((S\d+)\)", part):
                existing = assigned.get(name)
                if existing and existing != match.group(1):
                    raise ValueError(f"說話角色「{name}」使用了不同的 speaker ID；請保持同一個 (Sx)。")
                assigned[name] = match.group(1)
        return part
    map_directions(text, seed_ids)
    used = set(re.findall(r"\(S(\d+)\)", text)) | {value[1:] for value in assigned.values()}
    changes = []
    for block in re.finditer(r"<d>.*?</d>", text, re.S):
        clause_start = max(text.rfind(char, 0, block.start()) for char in "\n。！？.!?;") + 1
        clause = text[clause_start:block.start()]
        candidates = []
        for name in names:
            left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name[0]) else ""
            right = r"(?![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name[-1]) else ""
            candidates.extend((m.start(), m.end(), name) for m in re.finditer(left + re.escape(name) + right, clause))
        if not candidates:
            continue
        start, end, name = max(candidates)
        # The name must be in the speaking clause, not merely quoted on a sign.
        if any(left <= start < right for left, right in _quoted_spans(clause)):
            continue
        explicit = re.search(r"\(S(\d+)\)", clause[end:])
        if explicit:
            assigned[name] = "S" + explicit.group(1)
            continue
        if name not in assigned:
            index = 1
            while str(index) in used:
                index += 1
            assigned[name] = f"S{index}"
            used.add(str(index))
        changes.append((clause_start + end, " (" + assigned[name] + ")"))
    for position, value in reversed(changes):
        text = text[:position] + value + text[position:]
    return text


def combine_storyboards(body: str, lines: list[str], duration: float) -> tuple[str, int]:
    normalized, count = normalize_shots(body, duration)
    if not lines:
        return normalized, count
    timeline, _ = normalize_shots("\n".join(lines), duration)
    if count > 1:
        planning = map_directions(timeline, lambda part: _SHOT.sub(lambda m: f"Planning for existing shot {m.group(1)}:", part))
        return normalized + "\n\nAdditional storyboard guidance:\n" + planning, count
    overview = re.sub(r"^\s*\[Shot 1\]\s*", "", normalized)
    timeline = timeline.replace("[Shot 1]", "[Shot 1] " + overview + " ", 1)
    return normalize_shots(timeline, duration)


def unpack_prompt(text: str) -> tuple[str, str, str]:
    """Accept an already structured prompt without nesting its outer sections."""
    mask = text
    for start, end in sorted([(m.start(), m.end()) for m in _OPAQUE.finditer(text)] + list(_quoted_spans(text)), reverse=True):
        mask = mask[:start] + " " * (end - start) + mask[end:]
    matches = list(_CORE.finditer(mask))
    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        values[match.group(1)] = text[match.end():end].strip()
    body = values.get("detailed_description", values.get("integrated_multimodal_description", text))
    return body, values.get("overall_soundscape", ""), values.get("non_diegetic_music", "")


def base_prompt(body: str, sound: str, music: str, *, first: bool, last: bool, duration: float, final_shot: int) -> tuple[str, str]:
    mode = "FL2VA" if first and last else "I2VA" if first else "L2VA" if last else "T2VA"
    alignment = ""
    if mode == "I2VA":
        alignment = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    elif mode == "FL2VA":
        alignment = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
                     f"Picture 2 (from Shot {final_shot}) aligns with the {duration:.2f}-second mark of the target video.")
    elif mode == "L2VA":
        alignment = (f"How the reference pictures align with the target video — <Picture 1> (from [Shot {final_shot}]) "
                     f"aligns with the {duration:.2f}-second mark of the target video.")
    sections = ["integrated_multimodal_description: " + body, "overall_soundscape: " + sound, "non_diegetic_music: " + music]
    return ((alignment + "\n\n") if alignment else "") + "\n\n".join(sections), mode


def language_warning(text: str) -> bool:
    directions = map_directions(text, lambda part: re.sub(r"[\u3400-\u9fff]", "\u0001", part))
    return "\u0001" in directions
