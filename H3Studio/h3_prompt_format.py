"""Deterministic H3 prompt formatting, without changing user speech or sign text."""

from __future__ import annotations

import re
import math
from collections.abc import Callable

from dialogue import _quoted_spans

_OPAQUE = re.compile(r"<d>.*?</d>|<(?:Subject|Picture|Video|Audio)\s+\d+>", re.S)
_SHOT = re.compile(r"\[Shot\s+(\d+)(?:\s*,\s*([\d.]+)s\s*[-–]\s*([\d.]+)s)?\]", re.I)
_TIME = re.compile(r"\s*At\s+(\d+):(\d{2})\.(\d{3})\s*,", re.I)
_CORE = re.compile(r"^(subject_definitions|summary|retention_analysis|integrated_multimodal_description|detailed_description|overall_soundscape|non_diegetic_music):[ \t]*\n?", re.M)
BASE_SECTIONS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music")
_RANGE = r"(?P<start>\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[-–—～~至]\s*(?P<end>\d+(?:\.\d+)?)\s*(?:s|秒)"
_TIMED_NAMED = re.compile(
    r"^[ \t]*(?:#{1,6}\s*)?(?:\*\*|__)?(?:鏡頭|镜头|分鏡|分镜)\s*(?P<index>\d+)\s*[:：]?\s*"
    r"(?P<title>[^\r\n]*?)[（(]\s*" + _RANGE + r"\s*[）)](?:\*\*|__)?[ \t]*\r?$", re.M | re.I)
_TIMED_NUMBERED = re.compile(
    r"^[ \t]*(?:#{1,6}\s*)?(?:\*\*|__)?(?P<index>\d+)[.)、]\s*" + _RANGE +
    r"\s*(?:[｜|:：]\s*(?P<title>[^\r\n]*?))?(?:\*\*|__)?[ \t]*\r?$", re.M | re.I)
_TIMED_PLAIN = re.compile(
    r"^[ \t]*(?:#{1,6}\s*)?(?:\*\*|__)?(?:(?:時間碼|时间码|時間|时间)\s*[:：]\s*)?" + _RANGE +
    r"\s*(?:[｜|:：]\s*(?P<title>[^\r\n]*?))?(?:\*\*|__)?[ \t]*\r?$", re.M | re.I)
_TIMED_CANDIDATE = re.compile(
    r"^[ \t]*(?:#{1,6}\s*)?(?:\*\*|__)?(?:(?:鏡頭|镜头|分鏡|分镜)\s*(?:\d+|[一二三四五六七八九十]+)\s*[:：（(]|"
    r"\d+[.)、]\s*\d+(?:\.\d+)?\s*(?:s|秒)?\s*[-–—～~至]|"
    r"\d+[.)、]\s*\d+:\d|"
    r"\d+(?:\.\d+)?\s*(?:s|秒)?\s*[-–—～~至]\s*\d+(?:\.\d+)?\s*(?:s|秒)\s*[｜|:：])", re.M | re.I)


def _masked_content(text: str, *, labels: bool = True) -> str:
    pattern = _OPAQUE if labels else re.compile(r"<d>.*?</d>", re.S)
    spans = [(m.start(), m.end()) for m in pattern.finditer(text)] + list(_quoted_spans(text))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def parse_prompt_sections(text: str) -> dict[str, str] | None:
    """Recognize complete official prompts; never discard partial or duplicate sections.

    A first/last-frame alignment instruction may precede the three base fields.
    Ordinary narrative has no recognized section headers and returns ``None``.
    """
    matches = list(_CORE.finditer(_masked_content(text)))
    if not matches:
        return None
    names = tuple(match.group(1) for match in matches)
    if names not in {BASE_SECTIONS, REF_SECTIONS}:
        raise ValueError("完整 H3 提示詞需依序包含官方三段或六段，不能缺漏、重複或混用欄位；請補齊，或只貼影片敘述。")
    prefix = text[:matches[0].start()].strip()
    if prefix and not (names == BASE_SECTIONS and prefix.startswith(("For the target video,", "How the reference pictures align with the target video"))):
        raise ValueError("完整 H3 提示詞的區段前有無法辨識的內容；請將演出要求放入正式描述欄位。")
    values = {match.group(1): text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
              for index, match in enumerate(matches)}
    if any(not value for value in values.values()):
        raise ValueError("完整 H3 提示詞的每個區段都需要內容；沒有音樂請填 N/A。")
    return values


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


def normalize_shots(text: str, duration: float, *, add_opening: bool = True,
                    timeline_duration: float | None = None, allow_repeated_opening: bool = False) -> tuple[str, int]:
    """Convert legacy storyboard markers and reject invalid actual cut times."""
    visible = text
    # Obtain a same-length mask so dialogue/sign text cannot invent a shot.
    masked = _masked_content(text)
    # Only explicit standalone storyboard headings with both numeric endpoints
    # qualify. Keep quoted/dialogue content opaque and let the existing shot
    # validator handle ordering and duration after this syntax conversion.
    headings = sorted([match for pattern in (_TIMED_NAMED, _TIMED_NUMBERED, _TIMED_PLAIN)
                       for match in pattern.finditer(masked)], key=lambda match: match.start())
    candidates = list(_TIMED_CANDIDATE.finditer(masked))
    if any(not any(heading.start() <= candidate.start() < heading.end() for heading in headings)
           for candidate in candidates):
        raise ValueError("無法辨識分鏡時間碼；請使用「分鏡 1：段名（0–3秒）」或「1. 0.0–3.0s｜段名」，每段需有完整起訖時間。")
    if headings:
        if _SHOT.search(masked):
            raise ValueError("請勿混用時間碼稿與 [Shot N] 分鏡標記；請統一一種格式後再生成。")
        previous_end = 0.0
        changes = []
        for expected, heading in enumerate(headings, 1):
            index = int(heading.groupdict().get("index") or expected)
            start, end = float(heading.group("start")), float(heading.group("end"))
            if index != expected:
                raise ValueError(f"分鏡編號必須從 1 依序增加；目前預期 {expected}，收到 {index}。")
            if not all(math.isfinite(value) for value in (start, end)) or end <= start:
                raise ValueError("分鏡時間碼的結束必須大於起點。")
            if not math.isclose(start, previous_end, abs_tol=0.001, rel_tol=0):
                raise ValueError("分鏡時間碼必須從 0 秒開始且連續，不能空白、重疊或重複。")
            if end > duration + 0.05:
                raise ValueError("分鏡時間碼不得超過影片實際時長。")
            title = text[heading.start("title"):heading.end("title")].strip() if heading.group("title") is not None else ""
            replacement = "[Shot 1]" if expected == 1 else f"[Shot {expected}] At {cut_time(start)},"
            changes.append((heading.start(), heading.end(), replacement + (" " + title if title else "")))
            previous_end = end
        target = timeline_duration if timeline_duration is not None else duration
        # Effective H3 durations include a small frame-grid padding; manuscript
        # endpoints normally use requested seconds, while raw prompts may use actual seconds.
        if not (math.isclose(previous_end, target, rel_tol=0, abs_tol=0.001)
                or math.isclose(previous_end, duration, rel_tol=0, abs_tol=0.05)
                or timeline_duration is None and 0 <= duration - previous_end <= 0.2):
            raise ValueError(f"分鏡時間碼只到 {previous_end:g} 秒，未完整覆蓋目標 {target:g} 秒；請補齊或修正片長。")
        for start, end, replacement in reversed(changes):
            text = text[:start] + replacement + text[end:]
        return normalize_shots(text, duration, add_opening=add_opening, timeline_duration=timeline_duration)
    matches = list(_SHOT.finditer(masked))
    if len(list(re.finditer(r"\[Shot\b", masked, re.I))) != len(matches):
        raise ValueError("分鏡標記格式不完整，請使用 [Shot 1] 及 [Shot 2] At MM:SS.mmm,。")
    if not matches:
        return (("[Shot 1] " + text) if add_opening else text), 1
    changes = []
    expected, last_time = 1, -1.0
    legacy_end_previous = None
    final_shot = 1
    for match in matches:
        index = int(match.group(1))
        legacy_start, legacy_end = match.group(2), match.group(3)
        timing = _TIME.match(masked, match.end())
        if timing is None and re.match(r"\s*At\b", masked[match.end():], re.I):
            raise ValueError("分鏡時間碼格式不完整，請使用 At MM:SS.mmm,。")
        if allow_repeated_opening and index == 1 and expected == 2 and last_time < 0 and legacy_start is None and timing is None:
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
            if legacy_end_previous is not None and not math.isclose(start, legacy_end_previous, rel_tol=0, abs_tol=0.001):
                raise ValueError("分鏡時間範圍不能空白、重疊或重複。")
            legacy_end_previous = end
        elif timing:
            if int(timing.group(2)) >= 60:
                raise ValueError("分鏡時間碼的秒數需小於 60，請使用 MM:SS.mmm 格式。")
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
    if timeline_duration is not None and all(match.group(2) is not None for match in matches):
        if not (math.isclose(legacy_end_previous, timeline_duration, rel_tol=0, abs_tol=0.001)
                or math.isclose(legacy_end_previous, duration, rel_tol=0, abs_tol=0.05)):
            raise ValueError("分鏡時間範圍未完整涵蓋目標片長，請補齊或修正片長。")
    for start, end, replacement in reversed(changes):
        visible = visible[:start] + replacement + visible[end:]
    return visible, final_shot


def spoken_speaker_occurrences(text: str, names: tuple[str, ...]) -> list[tuple[str, int, str | None]]:
    """Locate actual named speech, excluding quoted labels and silent cast members."""
    result = []
    quotations = list(_quoted_spans(text))
    for block in re.finditer(r"<d>.*?</d>", text, re.S):
        if any(start <= block.start() < end for start, end in quotations):
            continue
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
        if any(left <= start < right for left, right in _quoted_spans(clause)):
            continue
        explicit = re.search(r"\((S[1-9]\d*(?:\s*,\s*S[1-9]\d*)*)\)", clause[end:])
        result.append((name, clause_start + end, explicit.group(1) if explicit else None))
    return result


_VOICE_VERB = (
    r"(?:says?|said|whispers?|replies|replied|asks?|asked|shouts?|sings?|calls?|cries)"
    r"(?:\s+(?:softly|quietly|shyly|gently|loudly))?"
    r"(?:\s+in\s+an?\s+off[- ]screen\s+voiceover)?"
    r"|(?:輕聲細語|轻声细语|害羞道|低聲道|低声道|輕聲道|轻声道|柔聲道|柔声道|"
    r"低語|低语|說道|说道|說|说|問道|问道|問|问|回答|答道|喊道|大喊)"
)
_INFER_SOURCE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<name>[A-Z][A-Za-z0-9_'’-]*(?:\s+[A-Z][A-Za-z0-9_'’-]*){0,2}|"
    r"[\u3400-\u9fff]{1,4})\s*(?:\(S[1-9]\d*\)\s*)?(?i:" + _VOICE_VERB + r")\s*[:：,，]?\s*$")
_VOICE_LABEL = re.compile(r"(?P<name>旁白|畫外音|画外音|(?i:Narrator|Narration|Voice[- ]?over)|[A-Z][A-Za-z0-9_'’-]*)"
                          r"\s*(?:\(S[1-9]\d*\)\s*)?[:：]\s*$")


def infer_spoken_sources(text: str) -> tuple[str, ...]:
    """Infer only explicitly named, adjacent speaking cues when no cast is supplied."""
    result = []
    masked = _masked_content(text, labels=False)
    quotations = list(_quoted_spans(text))
    for block in re.finditer(r"<d>.*?</d>", text, re.S):
        if any(start <= block.start() < end for start, end in quotations):
            continue
        start = max(text.rfind(char, 0, block.start()) for char in "\n。！？.!?;") + 1
        clause = masked[start:block.start()]
        match = _INFER_SOURCE.search(clause) or _VOICE_LABEL.search(clause)
        if not match:
            continue
        name = match.group("name")
        before = clause[:match.start("name")].rstrip()
        if (name.casefold() in {"he", "she", "they", "it", "someone", "the", "a", "we", "i", "dialogue", "speech", "sound", "sfx", "effects", "camera", "action", "text",
                               "他", "她", "它", "牠", "他們", "她們", "它們", "牠們", "他\u4eec", "她\u4eec", "有人", "大家"}
                or re.search(r"(?:\band|&|和|與|与|及|、)\s*$", before, re.I)
                or re.search(r"和|與|与|及|、", name)
                or re.search(r"[以用的向地在]$", name)
                or re.search(r"字幕|字卡|招牌|標語|标语|caption|subtitle|signage", clause, re.I)):
            continue
        if name not in result:
            result.append(name)
    return tuple(result)


def has_unassigned_dialogue(text: str) -> bool:
    """Report speech without an identifiable speaker ID, excluding printed examples."""
    masked = _masked_content(text, labels=False)
    quotations = list(_quoted_spans(text))
    for block in re.finditer(r"<d>.*?</d>", text, re.S):
        if any(start <= block.start() < end for start, end in quotations):
            continue
        if not block.group()[3:-4].strip():
            continue
        start = max(text.rfind(char, 0, block.start()) for char in "\n。！？.!?;") + 1
        clause = masked[start:block.start()]
        if not re.search(r"\(S[1-9]\d*(?:\s*,\s*S[1-9]\d*)*\)", clause):
            return True
    return False


def number_speakers(text: str, names: tuple[str, ...], assigned: dict[str, str]) -> str:
    """Number actual vocal sources by first speech, preserving explicit user IDs."""
    if not names:
        names = infer_spoken_sources(text)
        if not names:
            return text
    occurrences = spoken_speaker_occurrences(text, names)
    vocal_names = {item[0] for item in occurrences}
    for name, _, explicit in occurrences:
        if explicit and "," not in explicit:
            if name in assigned and assigned[name] != explicit:
                raise ValueError(f"說話角色「{name}」使用了不同的 speaker ID；請保持同一個 (Sx)。")
            assigned[name] = explicit
    def seed_ids(part: str) -> str:
        for name in vocal_names:
            left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name[0]) else ""
            for match in re.finditer(left + re.escape(name) + r"\s*\((S[1-9]\d*)\)", part):
                existing = assigned.get(name)
                if existing and existing != match.group(1):
                    raise ValueError(f"說話角色「{name}」使用了不同的 speaker ID；請保持同一個 (Sx)。")
                assigned[name] = match.group(1)
        return part
    seed_ids(_masked_content(text, labels=False))
    owners = {}
    for name in names:
        value = assigned.get(name)
        if value and value in owners and owners[value] != name:
            raise ValueError(f"不同說話角色共用了 {value}；請為每位實際發聲者保留獨立編號。")
        if value:
            owners[value] = name
    used = {value[1:] for value in assigned.values()}
    compound = [explicit for _, _, explicit in occurrences if explicit and "," in explicit]
    used.update(value.strip()[1:] for explicit in compound for value in explicit.split(","))
    changes = []
    for name, position, explicit in occurrences:
        if explicit:
            continue
        if compound and name not in assigned:
            raise ValueError("稿件已有同聲發話的 (S1,S2) 編號，個別發話者的對應不明；請明確標出個別 (Sx)，不會自動猜測聲源。")
        if name not in assigned:
            index = 1
            while str(index) in used:
                index += 1
            assigned[name] = f"S{index}"
            used.add(str(index))
        changes.append((position, " (" + assigned[name] + ")"))
    for position, value in reversed(changes):
        text = text[:position] + value + text[position:]
    return text


def combine_storyboards(body: str, lines: list[str], duration: float, *,
                        timeline_duration: float | None = None,
                        allow_repeated_opening: bool = False) -> tuple[str, int]:
    normalized, count = normalize_shots(body, duration, timeline_duration=timeline_duration,
                                      allow_repeated_opening=allow_repeated_opening)
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
    values = parse_prompt_sections(text) or {}
    body = values.get("detailed_description", values.get("integrated_multimodal_description", text))
    return body, values.get("overall_soundscape", ""), values.get("non_diegetic_music", "")


def normalize_prompt_source(text: str, duration: float, *, speakers=(),
                            timeline_duration: float | None = None) -> str:
    """Deterministically normalize before translation, preserving full schema fields.

    This does not translate, assign reference labels, or create a GPU request.
    ``duration`` is the actual padded duration; ``timeline_duration`` is the user's
    requested duration when known. Complete structured prompts retain all fields.
    """
    from dialogue import format_dialogue
    values = parse_prompt_sections(text)
    key = "detailed_description" if values and "detailed_description" in values else "integrated_multimodal_description"
    body = values[key] if values else text
    body = format_dialogue(body, speakers=speakers)
    body, _ = normalize_shots(body, duration, add_opening=False, timeline_duration=timeline_duration)
    if values is None:
        return body
    matches = list(_CORE.finditer(_masked_content(text)))
    index = next(index for index, match in enumerate(matches) if match.group(1) == key)
    start = matches[index].end()
    end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
    original = text[start:end]
    leading = original[:len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()):]
    return text[:start] + leading + body + trailing + text[end:]


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
