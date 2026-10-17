"""Isolated, CPU-only speech recognition worker for the video editor.

Heavy dependencies are deliberately imported only when a caption job runs.
Stdout is a JSON-lines progress channel; the result is written atomically.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import re
import sys
import textwrap
import unicodedata
from typing import Any, Callable, Iterable, Mapping


MODEL_ID = "Systran/faster-whisper-small"
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MAX_DURATION = 600.0
MAX_CUE_CHARACTERS = 500
MAX_CUE_LINES = 2
LANGUAGES = ("auto", "zh", "en", "ja", "ko")
_ASIAN = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003134f]")


class CaptionError(ValueError):
    """A safe, user-facing error that does not contain download credentials."""


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _number(value: Any, default: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        if default is not None:
            return default
        raise CaptionError("字幕時間必須是有效的數字。") from None
    if not math.isfinite(number):
        if default is not None:
            return default
        raise CaptionError("字幕時間必須是有效的數字。")
    return number


def normalize_text(text: Any) -> str:
    value = unicodedata.normalize("NFC", str(text or ""))
    value = "".join(c for c in value if unicodedata.category(c) != "Cc" or c.isspace())
    return re.sub(r"\s+", " ", value).strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def split_caption_text(text: str) -> list[str]:
    """Wrap and split without throwing away words, punctuation or long tokens."""
    value = normalize_text(text)
    if not value:
        return []
    width = 22 if _ASIAN.search(value) else 42
    lines = textwrap.wrap(value, width=width, break_long_words=True,
                          break_on_hyphens=False, replace_whitespace=False)
    chunks = ["\n".join(lines[i:i + MAX_CUE_LINES])
              for i in range(0, len(lines), MAX_CUE_LINES)]
    if _compact("".join(chunks)) != _compact(value):
        raise CaptionError("字幕分行未能保留完整文字，請重試。")
    return chunks


def _join_text(left: str, right: str) -> str:
    left, right = normalize_text(left), normalize_text(right)
    if not left or not right:
        return left + right
    separator = " " if (left[-1].isascii() and (left[-1].isalnum() or left[-1] in ".!?;,:)")
                        and right[0].isascii() and right[0].isalnum()) else ""
    return left + separator + right


def _merged_text(left: str, right: str) -> str | None:
    chunks = split_caption_text(_join_text(left, right))
    return chunks[0] if len(chunks) == 1 else None


def _word_spans(words: Iterable[Any], text: str, start: float, end: float,
                converter: Callable[[str], str]) -> list[tuple[int, int, float, float]]:
    spans = []
    joined = []
    position = 0
    previous = start
    for word in words:
        value = normalize_text(converter(normalize_text(_get(word, "word", ""))))
        length = len(_compact(value))
        if not length:
            continue
        word_start = _number(_get(word, "start"), float("nan"))
        word_end = _number(_get(word, "end"), float("nan"))
        if not math.isfinite(word_start) or not math.isfinite(word_end) or word_end < word_start:
            return []
        word_start = min(end, max(start, previous, word_start))
        word_end = min(end, max(word_start, word_end))
        spans.append((position, position + length, word_start, word_end))
        joined.append(value)
        position += length
        previous = word_end
    if _compact("".join(joined)) != _compact(text):
        return []
    return spans


def _boundary(spans: list[tuple[int, int, float, float]], position: int,
              *, ending: bool) -> tuple[float, bool]:
    for index, (first, last, start, end) in enumerate(spans):
        if position == first:
            if ending and index:
                return spans[index - 1][3], False
            return start, False
        if first < position < last:
            return start + (end - start) * (position - first) / (last - first), True
        if position == last:
            if not ending and index + 1 < len(spans):
                return spans[index + 1][2], False
            return end, False
    return spans[-1][3], False


def build_cues(segments: Iterable[Any], *, duration: float, fps: float,
               converter: Callable[[str], str] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Produce bounded, non-overlapping cues; fail rather than silently lose text.

    The second return value reports approximate timing (missing word timestamps,
    a split within one recognized word, or a frame-duration correction).
    """
    duration, fps = _number(duration), _number(fps)
    if duration <= 0 or fps <= 0 or fps > 240:
        raise CaptionError("影片時長與影格率必須大於零，影格率不可超過 240。")
    limit = min(duration, MAX_DURATION)
    frame = 1.0 / fps
    convert = converter or (lambda text: text)
    pending: list[dict[str, Any]] = []
    approximate = False
    for segment in segments:
        text = normalize_text(convert(normalize_text(_get(segment, "text", ""))))
        if not text:
            continue
        start = min(limit, max(0.0, _number(_get(segment, "start"))))
        end = min(limit, max(start, _number(_get(segment, "end"))))
        chunks = split_caption_text(text)
        spans = _word_spans(_get(segment, "words", None) or [], text, start, end, convert)
        total = len(_compact(text))
        offset = 0
        for chunk in chunks:
            length = len(_compact(chunk))
            if spans:
                cue_start, start_approximate = _boundary(spans, offset, ending=False)
                cue_end, end_approximate = _boundary(spans, offset + length, ending=True)
                approximate |= start_approximate or end_approximate
            else:
                cue_start = start + (end - start) * offset / total
                cue_end = start + (end - start) * (offset + length) / total
                approximate = True
            pending.append({"start": cue_start, "end": cue_end, "text": chunk})
            offset += length

    cues: list[dict[str, Any]] = []
    for item in pending:
        original_start, original_end = item["start"], item["end"]
        start = max(original_start, cues[-1]["end"] if cues else 0.0)
        end = min(limit, original_end)
        if end - start < frame - 1e-9:
            merged = _merged_text(cues[-1]["text"], item["text"]) if cues else None
            if merged is not None:
                cues[-1]["text"] = merged
                cues[-1]["end"] = min(limit, max(cues[-1]["end"], end))
                approximate = True
                continue
            end = min(limit, start + frame)
            if end - start < frame - 1e-9:
                start = max(0.0, limit - frame)
                if cues and cues[-1]["end"] > start:
                    if start - cues[-1]["start"] >= frame - 1e-9:
                        cues[-1]["end"] = start
                    else:
                        raise CaptionError("這段語音太密集，無法保留所有文字與有效字幕時間；請縮短辨識範圍。")
                if end - start < frame - 1e-9:
                    raise CaptionError("影片不足一個影格，無法建立有效字幕。")
        approximate |= abs(start - original_start) > 1e-9 or abs(end - original_end) > 1e-9
        if len(item["text"]) > MAX_CUE_CHARACTERS or len(item["text"].splitlines()) > MAX_CUE_LINES:
            raise CaptionError("字幕文字超出單句限制，請縮短辨識範圍。")
        cues.append({"start": start, "end": end, "text": item["text"]})
    if _compact("".join(c["text"] for c in cues)) != _compact("".join(c["text"] for c in pending)):
        raise CaptionError("字幕未能保留完整辨識文字，請重試。")
    return cues, approximate


def emit_progress(stage: str, progress: float) -> None:
    print(json.dumps({"stage": stage, "progress": round(min(100.0, max(0.0, progress)))},
                     ensure_ascii=False), flush=True)


def _load_model(model_dir: Path) -> Any:
    # Fixed repository and data-file allowlist: no arbitrary model or remote code.
    from faster_whisper import WhisperModel
    if not all((model_dir / name).is_file() for name in MODEL_FILES):
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=MODEL_ID, local_dir=str(model_dir),
                          allow_patterns=list(MODEL_FILES) + ["preprocessor_config.json"],
                          token=False)
    if not all((model_dir / name).is_file() for name in MODEL_FILES):
        raise CaptionError("語音字幕模型未完整下載；請確認網路後重試。")
    return WhisperModel(str(model_dir), device="cpu", compute_type="int8",
                        cpu_threads=4, num_workers=1, local_files_only=True)


def _audio_duration(input_path: Path, *, maximum: float) -> float:
    # PyAV supports both FLOAT PCM and S16 WAV; stdlib wave rejects FLOAT PCM.
    import av
    try:
        with av.open(str(input_path), mode="r") as audio:
            if not audio.streams.audio:
                raise CaptionError("準備好的音軌沒有可辨識的聲音資料，請重試。")
            stream = audio.streams.audio[0]
            duration = None
            if stream.duration is not None and stream.time_base is not None:
                duration = _number(float(stream.duration * stream.time_base), -1)
            if duration is None or duration < 0:
                duration = _number(audio.duration / av.time_base, -1) if audio.duration is not None else -1
            if duration >= 0:
                return duration
            duration = 0.0
            for frame in audio.decode(stream):
                rate = frame.sample_rate or stream.codec_context.sample_rate
                if not rate:
                    raise CaptionError("無法讀取音軌的取樣率，請重試。")
                duration += frame.samples / rate
                if duration > maximum:
                    return duration
            return duration
    except CaptionError:
        raise
    except Exception:
        raise CaptionError("無法讀取準備好的語音音軌，請重試。") from None


def decode_audio(input_path: Path, *, duration: float) -> Any:
    """Decode bounded mono float32 audio with the supported public PyAV API.

    Supplying this 16 kHz array to Whisper avoids its file decoder's obsolete
    av.open keyword on PyAV 18. A short track is padded; a longer track is cropped
    to the requested editor range. Memory never grows with source-file duration.
    """
    import av
    import numpy as np

    duration = _number(duration)
    if not 0 < duration <= MAX_DURATION:
        raise CaptionError("自動字幕每次最多辨識 10 分鐘，請選擇有效的時長。")
    sample_count = math.floor(duration * 16000 + 1e-6)
    if sample_count < 1:
        raise CaptionError("辨識音軌太短，請選擇較長的範圍。")
    samples = np.zeros(sample_count, dtype=np.float32)
    written = 0

    def append_frames(frames: Iterable[Any]) -> None:
        nonlocal written
        for frame in frames:
            values = frame.to_ndarray().reshape(-1)
            count = min(len(values), sample_count - written)
            if count > 0:
                samples[written:written + count] = values[:count]
                written += count

    try:
        with av.open(str(input_path), mode="r") as audio:
            if not audio.streams.audio:
                raise CaptionError("準備好的音軌沒有可辨識的聲音資料，請重試。")
            stream = audio.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            for frame in audio.decode(stream):
                frame.pts = None
                append_frames(resampler.resample(frame))
                if written >= sample_count:
                    break
            append_frames(resampler.resample(None))
    except CaptionError:
        raise
    except Exception:
        raise CaptionError("無法解碼準備好的語音音軌，請重試。") from None
    if not np.isfinite(samples).all():
        raise CaptionError("語音音軌包含無效樣本，請重新匯入音訊後重試。")
    return samples


def transcribe_audio(input_path: Path, output_path: Path, *, model_dir: Path,
                     language: str, traditional: bool, fps: float, duration: float) -> dict[str, Any]:
    if language not in LANGUAGES:
        raise CaptionError("不支援這個字幕語言。")
    duration, fps = _number(duration), _number(fps)
    if not 0 < duration <= MAX_DURATION or not 0 < fps <= 240:
        raise CaptionError("自動字幕每次最多辨識 10 分鐘；影片時長與影格率必須有效。")
    audio_duration = _audio_duration(input_path, maximum=MAX_DURATION + 1 / fps)
    if audio_duration > MAX_DURATION + 1 / fps:
        raise CaptionError("語音音軌超過 10 分鐘，請縮短辨識範圍。")
    audio_samples = decode_audio(input_path, duration=duration)
    converter: Callable[[str], str] | None = None
    model_dir = model_dir / "small"
    model_dir.mkdir(parents=True, exist_ok=True)
    emit_progress("model", 0)
    with contextlib.redirect_stdout(sys.stderr):
        model = _load_model(model_dir)
    emit_progress("model", 100)
    emit_progress("transcribing", 0)
    with contextlib.redirect_stdout(sys.stderr):
        segments, info = model.transcribe(audio_samples, task="transcribe",
                                         language=None if language == "auto" else language,
                                         beam_size=5, vad_filter=True, word_timestamps=True,
                                         condition_on_previous_text=False)
    recognized_language = str(_get(info, "language", language))
    if traditional and (language == "zh" or recognized_language.lower().startswith("zh")):
        from opencc import OpenCC
        converter = OpenCC("s2t.json").convert
    collected = []
    iterator = iter(segments)
    while True:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                segment = next(iterator)
            except StopIteration:
                break
        collected.append(segment)
        emit_progress("transcribing", min(99, _number(_get(segment, "end"), 0) / duration * 100))
    cues, approximate = build_cues(collected, duration=duration, fps=fps, converter=converter)
    result = {"cues": cues, "language": recognized_language,
              "language_probability": min(1.0, max(0.0, _number(_get(info, "language_probability"), 0))),
              "model": "small", "duration": duration, "timing_approximate": approximate}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)
    emit_progress("transcribing", 100)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local CPU speech captions.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--language", choices=LANGUAGES, default="auto")
    parser.add_argument("--traditional", action="store_true")
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        transcribe_audio(args.input, args.output, model_dir=args.model_dir,
                         language=args.language, traditional=args.traditional,
                         fps=args.fps, duration=args.duration)
    except Exception as exc:
        # Download exception strings can embed authenticated URLs; never echo them.
        message = str(exc) if isinstance(exc, CaptionError) else "語音字幕工作失敗，請確認字幕環境與網路後重試。"
        print(json.dumps({"error": message, "type": type(exc).__name__}, ensure_ascii=False),
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
