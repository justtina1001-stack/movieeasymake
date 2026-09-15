"""Conservative, local formatting of explicitly identified H3 spoken dialogue."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable


_DIALOGUE = re.compile(r"<d>.*?</d>", re.DOTALL)
_LANGUAGE_TAG = re.compile(r"^\s*\[[A-Za-z][A-Za-z -]*\]")
_SPEECH_END = re.compile(
    r"(?:台詞|台词|對白|对白|旁白|畫外音|画外音|低聲|低声|低語|低语|大喊|喊道|喊|"
    r"說道|说道|說|说|問道|问道|問|问|回答|答道|回應|回应|自言自語|自言自语|"
    r"喃喃自語|喃喃自语|念道|"
    r"\b(?:says?|said|asks?|asked|replies|replied|shouts?|whispers?|dialogue|"
    r"voice[ -]?over|narration|narrator))$",
    re.IGNORECASE,
)
_LANGUAGE_HINTS = (
    (r"中文|漢語|汉语|普通話|普通话|國語|国语|\b(?:Chinese|Mandarin)\b", "Chinese"),
    (r"英文|英語|英语|\bEnglish\b", "English"),
    (r"日文|日語|日语|\bJapanese\b", "Japanese"),
    (r"韓文|韓語|韩文|韩语|\bKorean\b", "Korean"),
    (r"法文|法語|法语|\bFrench\b", "French"),
    (r"德文|德語|德语|\bGerman\b", "German"),
    (r"西班牙文|西班牙語|西班牙语|\bSpanish\b", "Spanish"),
)
_LABEL_LINE = re.compile(
    r"^(?P<prefix>[ \t]*(?:[-*+]\s+|\#{1,6}\s+)?(?:\*\*|__)?)"
    r"(?P<head>[^\n:：]{1,100})[:：](?P<spacing>(?:\*\*|__)?[ \t]*)(?P<body>.*)$"
)
_NO_DIALOGUE = re.compile(r"^(?:無|无|沒有|没有|無對白|无对白|無台詞|无台词|none|n/a|no dialogue)[。.!！]?$", re.I)
_QUOTE_PAIRS = {"「": "」", "『": "』", "“": "”", '"': '"'}
_SILENT_HEADING = re.compile(
    r"(?:無|无|沒有|没有|不要|禁止|不需|不必|不)(?:任何|加入|新增|加上)?"
    r"(?:對白|对白|台詞|台词|旁白|畫外音|画外音|說|说|喊|低語|低语)|"
    r"\bno\s+(?:spoken\s+)?(?:dialogue|speech|narration|voice[ -]?over)\b", re.I,
)
_VISIBLE_TEXT = re.compile(r"字幕|字卡|招牌|標語|标语|螢幕文字|屏幕文字|\b(?:caption|subtitle|signage)\b", re.I)


def _speech_context(text: str) -> str:
    # Earlier sentences may contain another language, speaker, or a silent scene.
    return re.split(r"[。！？.!?；;\n]|</d>", text.rstrip())[-1]


def map_outside_dialogue(text: str, transform: Callable[[str], str]) -> str:
    """Apply visual-reference substitutions without changing the spoken words."""
    result: list[str] = []
    cursor = 0
    for match in _DIALOGUE.finditer(text):
        result.extend((transform(text[cursor:match.start()]), match.group()))
        cursor = match.end()
    result.append(transform(text[cursor:]))
    return "".join(result)


def _language(text: str, hint: str) -> str:
    hint = _speech_context(hint)
    for pattern, language in _LANGUAGE_HINTS:
        if re.search(pattern, hint, re.I):
            return language
    if re.search(r"[\u3040-\u30ff]", text):
        return "Japanese"
    if re.search(r"[\uac00-\ud7af]", text):
        return "Korean"
    if re.search(r"[\u3400-\u9fff]", text):
        return "Chinese"
    return "English"


def _wrap(text: str, hint: str = "") -> str:
    if not text.strip():
        return text
    if _LANGUAGE_TAG.match(text):
        return f"<d>{text}</d>"
    return f"<d>[{_language(text, hint)}] {text}</d>"


def _speech_heading(text: str, speakers: set[str]) -> bool:
    heading = _speech_context(text).strip(" \t\r\n*_`#:：")
    if _SILENT_HEADING.search(heading) or _VISIBLE_TEXT.search(heading):
        return False
    # A label may specify the language after the speaking cue, e.g. 台詞（中文）.
    without_language = re.sub(r"[（(\[].*?[）)\]]$", "", heading).rstrip()
    if _SPEECH_END.search(without_language):
        return True
    return without_language in speakers or without_language in {"角色", "男主角", "女主角"}


def _unquoted_labels(text: str, speakers: set[str]) -> str:
    lines: list[str] = []
    pending_heading = ""
    quote_spans = list(_quoted_spans(text))
    offset = 0
    for line in text.splitlines(keepends=True):
        body_line = line.rstrip("\r\n")
        ending = line[len(body_line):]
        # A multiline sign/quotation can itself contain a line such as 台詞：… .
        inside_quote = any(start <= offset + len(line) - len(line.lstrip()) < end for start, end in quote_spans)
        match = _LABEL_LINE.match(body_line)
        if not inside_quote and match and _speech_heading(match["head"], speakers):
            body = match["body"]
            pending_heading = match["head"] if not body.strip() else ""
            if body.strip() and not body.lstrip().startswith((*_QUOTE_PAIRS, "<d>")):
                # Keep Markdown around the line outside the model's speech tag.
                suffix = ""
                if match["prefix"].endswith(("**", "__")) and body.endswith(match["prefix"][-2:]):
                    suffix, body = body[-2:], body[:-2]
                body_line = body_line[:match.start("body")] + _wrap(body, match["head"]) + suffix
        else:
            if (pending_heading and not inside_quote and body_line.strip()
                    and not re.search(r"[:：]", body_line)
                    and not body_line.lstrip().startswith((*_QUOTE_PAIRS, "<", "#", "-", "*", "_"))):
                body_line = _wrap(body_line, pending_heading)
            pending_heading = ""
        lines.append(body_line + ending)
        offset += len(line)
    return "".join(lines)


def _quote_end(text: str, start: int) -> int | None:
    opener = text[start]
    closer = _QUOTE_PAIRS[opener]
    depth = 1
    position = start + 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == closer:
            depth -= 1
            if not depth:
                return position + 1
        elif char == opener:
            depth += 1
        position += 1
    return None


def _quoted_spans(text: str) -> Iterable[tuple[int, int]]:
    position = 0
    while position < len(text):
        if text[position] in _QUOTE_PAIRS:
            end = _quote_end(text, position)
            if end is not None:
                yield position, end
                position = end
                continue
        position += 1


def format_dialogue(text: str, *, speakers: Iterable[str] = (), dialogue_field: bool = False) -> str:
    """Tag explicit speech, leaving ambiguous prose and visible-text quotes alone.

    `dialogue_field` is for a form field whose entire bare value is spoken text.
    Existing language tags take precedence; no translation or voice assignment occurs.
    """
    if not text:
        return text
    speaker_set = {name for name in speakers if name}
    # Existing correct tags (including multiline content) must remain byte-for-byte intact.
    text = _DIALOGUE.sub(
        lambda match: match.group() if not match.group()[3:-4].strip() or _LANGUAGE_TAG.match(match.group()[3:-4])
        else _wrap(match.group()[3:-4]),
        text,
    )
    text = map_outside_dialogue(text, lambda part: _unquoted_labels(part, speaker_set))
    parts: list[str] = []
    cursor = position = 0
    while position < len(text):
        if text.startswith("<d>", position):
            tagged = _DIALOGUE.match(text, position)
            if tagged:
                position = tagged.end()
                continue
        if text[position] not in _QUOTE_PAIRS:
            position += 1
            continue
        end = _quote_end(text, position)
        if end is None:
            position += 1
            continue
        # Only the immediately preceding label/clause can mark this quote as speech.
        preceding = text[:position].rstrip().rsplit("\n", 1)[-1]
        content = text[position + 1:end - 1]
        if "<d>" not in content and _speech_heading(preceding, speaker_set):
            parts.extend((text[cursor:position], _wrap(content, preceding)))
            cursor = end
        position = end
    parts.append(text[cursor:])
    formatted = "".join(parts)
    if dialogue_field and "<d>" not in formatted and not _NO_DIALOGUE.fullmatch(formatted.strip()):
        # A bare quote or bare line in the dedicated dialogue field needs no speaker label.
        stripped = formatted.strip()
        if stripped and stripped[0] in _QUOTE_PAIRS and _quote_end(stripped, 0) == len(stripped):
            return _wrap(stripped[1:-1])
        return _wrap(formatted)
    return formatted
