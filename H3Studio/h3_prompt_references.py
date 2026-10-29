"""Validate labels in complete user-authored Ref2VA prompts against uploaded slots."""
from __future__ import annotations

import re

from dialogue import _quoted_spans

_LABEL = re.compile(r"<(Subject|Picture|Video|Audio) ([1-9]\d*)>")


def _visible(text: str) -> str:
    for match in reversed(list(re.finditer(r"<d>.*?</d>", text, re.S))):
        text = text[:match.start()] + " " * (match.end() - match.start()) + text[match.end():]
    for start, end in reversed(list(_quoted_spans(text))):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def validate_uploaded_labels(text: str, mapping: list[dict], *, pictures: int, videos: int, audios: int) -> None:
    """Validate labels in narrative while keeping speech/sign text opaque."""
    subjects = {item["subject_tag"]: item for item in mapping if item.get("subject_tag")}
    allowed = set(subjects)
    for kind, count in (("Picture", pictures), ("Video", videos), ("Audio", audios)):
        allowed.update(f"<{kind} {index}>" for index in range(1, count + 1))
    all_text = _visible(text)
    labels = {match.group() for match in _LABEL.finditer(all_text)}
    malformed = re.sub(_LABEL, "", all_text)
    if re.search(r"<(?:Subject|Picture|Video|Audio)\b", malformed):
        raise ValueError("參考標籤格式錯誤，請使用 <Subject 1>、<Picture 1>、<Video 1> 或 <Audio 1>。")
    unknown = labels - allowed
    if unknown:
        raise ValueError("完整提示詞引用了不存在的上傳素材或角色標籤：" + "、".join(sorted(unknown)) + "；請依素材對照修正，不會自動猜測編號。")


def validate_reference_sections(sections: dict[str, str], mapping: list[dict], *,
                                pictures: int, videos: int, audios: int) -> None:
    """Refuse dangling, rebound, or ambiguous labels without renumbering a user's rules."""
    validate_uploaded_labels("\n".join(sections.values()), mapping, pictures=pictures, videos=videos, audios=audios)
    subjects = {item["subject_tag"]: item for item in mapping if item.get("subject_tag")}
    visible = {key: _visible(value) for key, value in sections.items()}
    labels = {match.group() for match in _LABEL.finditer("\n".join(visible.values()))}
    declarations = {}
    sources = set()
    definitions = visible["subject_definitions"]
    starts = list(re.finditer(r"^[ \t]*(<(?:Subject|Picture|Video|Audio) [1-9]\d*>)", definitions, re.M))
    if definitions.strip() != "N/A" and not starts:
        raise ValueError("subject_definitions 需逐行定義參考標籤，不能只有未綁定的敘述。")
    if starts and definitions[:starts[0].start()].strip():
        raise ValueError("subject_definitions 有無法歸屬的內容，請逐行以參考標籤開始定義。")
    for index, start in enumerate(starts):
        label = start.group(1)
        if label in declarations:
            raise ValueError(f"完整提示詞重複定義 {label}；請讓每個標籤保持唯一意義。")
        block = definitions[start.end():starts[index + 1].start() if index + 1 < len(starts) else len(definitions)]
        declarations[label] = block
        bound = {match.group() for match in _LABEL.finditer(block)}
        if label in subjects:
            item = subjects[label]
            physical = {tag for tag in bound if not tag.startswith("<Subject ")}
            expected = set(item.get("picture_tags", [])) | set(item.get("video_tags", []))
            if physical - expected - {item.get("audio_tag")}:
                raise ValueError(f"{label} 的來源與實際素材對照不同；請確認圖片／影片編號，不會重新綁定或猜測角色。")
            if expected and not physical.intersection(expected):
                raise ValueError(f"{label} 缺少對應的圖片／影片來源；請在定義中寫明實際素材標籤。")
            sources.update(physical)
    undefined = {label for label in labels if label not in declarations and label not in sources}
    if undefined:
        raise ValueError("完整提示詞有未定義的參考標籤：" + "、".join(sorted(undefined)) + "；請補齊其角色與來源。")
    retention_labels = {match.group() for match in _LABEL.finditer(visible["retention_analysis"])}
    missing_retention = set(declarations) - retention_labels
    if missing_retention:
        raise ValueError("retention_analysis 缺少已定義素材的保留規則：" + "、".join(sorted(missing_retention)))
    summary = visible["summary"]
    prefix = re.match(r"\[([^\]]+)\]", summary)
    tasks = {"keyframe completion", "reference generation", "video editing", "video continuation", "audio reuse", "audio reference"}
    chosen = [item.strip() for item in prefix.group(1).split("+")] if prefix else []
    if not prefix or len(chosen) != len(set(chosen)) or any(item not in tasks for item in chosen):
        raise ValueError("完整 Ref2VA summary 需先填官方任務類型，例如 [reference generation]。")
