"""Local, CPU-only video editing, audio mixing, persistence and MP4 export.

Editor sources are independent copies of known Studio assets or generated jobs.
Rendering keeps only adjacent video frames and small audio blocks in memory;
all clip boundaries share one CFR video/sample timeline. No model/GPU imports.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
from fractions import Fraction
import json
import logging
import math
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import quote
import uuid

import av
import numpy as np
from aiohttp import web

from editor_speed import clip_duration, source_at, validate_speed_curve


LOGGER = logging.getLogger(__name__)
SAFE_ID = re.compile(r"[a-f0-9]{32}\Z")
CLIP_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
DIMENSIONS = {360, 480, 512, 576, 640, 720, 768, 854, 864, 960, 1024, 1080, 1280, 1440, 1920}
FRAME_RATES = {24, 25, 30, 60}
SAMPLE_RATE = 48000
ACTIVE = {"queued", "running"}


class EditorError(ValueError):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


class ExportCancelled(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def check_id(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise EditorError("識別碼格式錯誤。")
    return value


def number(value, label, minimum=None, maximum=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise EditorError(f"{label}必須是有限數字。")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise EditorError(f"{label}超出允許範圍。")
    return float(value)


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def open_source(path):
    # Exclude playlist/network demuxers even if a user-controlled upload was
    # mislabeled. Only self-contained browser-previewable containers are used.
    return av.open(str(path), options={"protocol_whitelist": "file",
                                      "format_whitelist": "mov,matroska,webm,mp3,wav,aac,flac,ogg"})


def origin_seconds(container, stream):
    if stream.start_time is not None and stream.time_base is not None:
        return float(stream.start_time * stream.time_base)
    return float(container.start_time or 0) / av.time_base


def check_orientation(frame, stream):
    try:
        rotation = float(stream.metadata.get("rotate", "0"))
    except (ValueError, TypeError):
        rotation = 1.0
    matrix = any(item.type.name == "DISPLAYMATRIX" for item in frame.side_data)
    if rotation % 360 or frame.rotation or matrix:
        raise EditorError("這段影片含旋轉／顯示矩陣，請先轉成已轉正的 MP4 再匯入。")
    aspect = stream.sample_aspect_ratio
    if aspect is not None and aspect != 1:
        raise EditorError("這段影片使用非方形像素，請先轉成方形像素 MP4 再匯入。")


def probe_media(path):
    from editor_images import is_image_file, probe_image
    if is_image_file(path):
        return probe_image(path)
    try:
        with open_source(path) as source:
            if not source.streams.video:
                if not source.streams.audio:
                    raise EditorError("素材沒有可用的音訊或影片。")
                audio = source.streams.audio[0]
                audio.codec_context.thread_count = 1
                container, codec = source.format.name, audio.codec_context.name
                types = {"mp3": "audio/mpeg", "wav": "audio/wav", "aac": "audio/aac",
                         "flac": "audio/flac", "ogg": "audio/ogg"}
                mime = next((value for key, value in types.items() if key in container.split(",")), None)
                if "mov" in container and codec == "aac":
                    mime = "audio/mp4"
                if ("matroska" in container or "webm" in container) and codec in {"opus", "vorbis"}:
                    mime = "audio/webm"
                if mime is None or not (codec in {"mp3", "mp3float", "aac", "flac", "opus", "vorbis"}
                                        or codec.startswith("pcm_")):
                    raise EditorError("音訊支援 MP3、WAV、M4A／AAC、FLAC、OGG／Opus，請先轉檔後再匯入。")
                first = next(source.decode(audio=0), None)
                if first is None or first.pts is None or first.time_base is None:
                    raise EditorError("音訊沒有可用內容或有效時間戳。")
                duration = float(audio.duration * audio.time_base) if audio.duration is not None else 0.0
                if duration <= 0 and source.duration is not None:
                    duration = source.duration / av.time_base
                if not math.isfinite(duration) or duration <= 0 or duration > 600 + 1e-6:
                    raise EditorError("音訊需有有效長度，且最長 10 分鐘。")
                return {"kind": "audio", "duration": duration, "width": 0, "height": 0,
                        "fps": 0, "has_audio": True, "mime": mime}
            video = source.streams.video[0]
            video.codec_context.thread_count = 1
            container = source.format.name
            codec = video.codec_context.name
            is_mp4 = "mov" in container and source.metadata.get("major_brand", "").strip() != "qt"
            is_webm = "matroska" in container or "webm" in container
            audio_codecs = [stream.codec_context.name for stream in source.streams.audio]
            if is_mp4 and codec == "h264" and all(codec == "aac" for codec in audio_codecs):
                mime = "video/mp4"
            elif is_webm and codec in {"vp8", "vp9"} and all(codec in {"opus", "vorbis"} for codec in audio_codecs):
                mime = "video/webm"
            else:
                raise EditorError("目前支援 MP4（H.264／AAC）或 WebM（VP8、VP9／Opus、Vorbis）；請先轉檔再匯入。")
            first = next(source.decode(video=0), None)
            if first is None or first.pts is None or first.time_base is None:
                raise EditorError("影片沒有可用畫面或有效時間戳。")
            check_orientation(first, video)
            if first.width > 8192 or first.height > 8192:
                raise EditorError("來源影片尺寸過大；任一邊最多 8192 像素。")
            duration = float(video.duration * video.time_base) if video.duration is not None else 0.0
            if duration <= 0 and source.duration is not None:
                duration = source.duration / av.time_base
            if not math.isfinite(duration) or duration <= 0:
                raise EditorError("無法取得影片長度，請先轉成正常時間戳的 MP4。")
            fps = float(video.average_rate or video.base_rate or 24)
            if not math.isfinite(fps) or fps <= 0:
                fps = 24.0
            return {"kind": "video", "duration": duration, "width": first.width, "height": first.height,
                    "fps": fps, "has_audio": bool(source.streams.audio), "mime": mime}
    except EditorError:
        raise
    except (av.error.FFmpegError, OSError, StopIteration, ValueError) as error:
        raise EditorError("無法讀取素材；請使用支援且可正常播放的影片或音訊檔。") from error


def seek_source(container, stream, seconds):
    if seconds <= 0 or stream.time_base is None:
        return
    try:
        container.seek(int(seconds / float(stream.time_base)), stream=stream, backward=True, any_frame=False)
    except av.error.FFmpegError:
        # A failed seek is not a timestamp approximation: restart and decode
        # forward using actual PTS values instead.
        container.seek(0, backward=True)


class VideoReader:
    def __init__(self, path, start, cancel_event):
        self.source = open_source(path)
        self.stream = self.source.streams.video[0]
        self.stream.codec_context.thread_count = 1
        self.origin = origin_seconds(self.source, self.stream)
        self.cancel_event = cancel_event
        try:
            seek_source(self.source, self.stream, start + self.origin)
            self.decoder = iter(self.source.decode(video=0))
            self.current = None
            self.next_frame = self._next()
            if self.next_frame is None:
                raise EditorError("剪輯來源沒有可解碼畫面。")
        except BaseException:
            self.close()
            raise

    def _next(self):
        if self.cancel_event.is_set():
            raise ExportCancelled()
        frame = next(self.decoder, None)
        if frame is None:
            return None
        if frame.pts is None or frame.time_base is None:
            raise EditorError("影片含缺失時間戳，無法可靠同步；請先轉檔。")
        check_orientation(frame, self.stream)
        return (float(frame.pts * frame.time_base) - self.origin, frame)

    def at(self, seconds):
        while self.next_frame is not None and self.next_frame[0] <= seconds + 1e-9:
            self.current = self.next_frame
            following = self._next()
            if following is not None and following[0] < self.current[0] - 1e-6:
                raise EditorError("影片畫面時間戳不連續，請先轉檔。")
            self.next_frame = following
        return (self.current or self.next_frame)[1]

    def close(self):
        self.source.close()


class AudioReader:
    def __init__(self, path, start, cancel_event):
        self.source = open_source(path)
        self.cancel_event = cancel_event
        self.block = None
        try:
            primary = (self.source.streams.video or self.source.streams.audio)[0]
            self.origin = origin_seconds(self.source, primary)
            if self.source.streams.audio:
                self.stream = self.source.streams.audio[0]
                self.stream.codec_context.thread_count = 1
                seek_source(self.source, self.stream, start + self.origin)
                self.blocks = self._blocks()
            else:
                self.blocks = iter(())
        except BaseException:
            self.close()
            raise

    def _blocks(self):
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        last_start = None
        for frame in self.source.decode(audio=0):
            if self.cancel_event.is_set():
                raise ExportCancelled()
            if frame.pts is None or frame.time_base is None:
                raise EditorError("音訊含缺失時間戳，無法可靠同步；請先轉檔。")
            for converted in resampler.resample(frame):
                start = round((float(converted.pts * converted.time_base) - self.origin) * SAMPLE_RATE)
                if last_start is not None and start < last_start:
                    raise EditorError("音訊時間戳倒退，請先轉檔。")
                last_start = start
                yield start, converted.to_ndarray()
        for converted in resampler.resample(None):
            start = round((float(converted.pts * converted.time_base) - self.origin) * SAMPLE_RATE)
            yield start, converted.to_ndarray()

    def read(self, start, count):
        values = np.zeros((2, count), dtype=np.float32)
        end = start + count
        while True:
            if self.cancel_event.is_set():
                raise ExportCancelled()
            if self.block is None:
                self.block = next(self.blocks, None)
            if self.block is None:
                break
            position, block = self.block
            if position >= end:
                break
            block_end = position + block.shape[1]
            left, right = max(start, position), min(end, block_end)
            if right > left:
                values[:, left - start:right - start] = block[:, left - position:right - position]
            if block_end <= end:
                self.block = None
            else:
                break
        return values

    def close(self):
        blocks = getattr(self, "blocks", None)
        if hasattr(blocks, "close"):
            blocks.close()
        self.source.close()


class TempoAudioReader:
    """Sequential pitch-preserving tempo conversion with bounded filter buffers."""

    def __init__(self, path, clip, cancel_event):
        self.source = AudioReader(path, clip["in"], cancel_event)
        self.clip, self.cancel_event = clip, cancel_event
        self.blocks = self._blocks()
        self.block = None
        self.offset = 0

    def _blocks(self):
        speed = self.clip.get("speed", 1)
        curve = self.clip.get("speed_curve", [])
        if curve:
            yield from self._curve_blocks()
            return
        source_start = round(self.clip["in"] * SAMPLE_RATE)
        source_samples = round((self.clip["out"] - self.clip["in"]) * SAMPLE_RATE)
        graph = None
        if speed != 1:
            graph = av.filter.Graph()
            previous = graph.add_abuffer(sample_rate=SAMPLE_RATE, format="fltp", layout="stereo",
                                         time_base=Fraction(1, SAMPLE_RATE))
            # Each atempo remains in its high quality 0.5–2 range. Chaining
            # supports 0.25–4 without sample skipping at large tempo values.
            factors = []
            while speed < 0.5:
                factors.append(0.5)
                speed /= 0.5
            while speed > 2:
                factors.append(2.0)
                speed /= 2
            factors.append(speed)
            for factor in factors:
                current = graph.add("atempo", args=str(factor))
                previous.link_to(current)
                previous = current
            # atempo negotiates packed float on some FFmpeg builds; normalize
            # back to planar stereo before interpreting ndarray channel axes.
            planar = graph.add("aformat", args="sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
            previous.link_to(planar)
            sink = graph.add("abuffersink")
            planar.link_to(sink)
            graph.configure()
        for position in range(0, source_samples, 4096):
            if self.cancel_event.is_set():
                raise ExportCancelled()
            count = min(4096, source_samples - position)
            values = self.source.read(source_start + position, count)
            if graph is None:
                yield values
                continue
            frame = av.AudioFrame.from_ndarray(values, format="fltp", layout="stereo")
            frame.sample_rate, frame.time_base, frame.pts = SAMPLE_RATE, Fraction(1, SAMPLE_RATE), position
            graph.push(frame)
            while True:
                try:
                    yield graph.pull().to_ndarray()
                except (av.error.BlockingIOError, av.error.EOFError):
                    break
        if graph is not None:
            graph.push(None)
            while True:
                try:
                    yield graph.pull().to_ndarray()
                except (av.error.BlockingIOError, av.error.EOFError):
                    break

    def _curve_blocks(self):
        """Bounded waveform overlap-add on the exact curve presentation clock.

        Copying original samples into short overlapping grains preserves pitch.
        Each grain center uses the analytic inverse time map; a small phase
        alignment search cannot accumulate tempo drift or move a source event
        farther than one grain plus the search radius. Stereo uses one shared
        alignment offset. Only source context and adjacent grains stay in RAM.
        """
        # The 85 ms grain and ±27 ms phase search cover a full half-period
        # down to 20 Hz. Shorter speech-sized windows shift or cancel bass.
        window_size, hop, search = 4096, 1024, 1280
        half = window_size // 2
        weights = np.hanning(window_size).astype(np.float32)
        total = round(clip_duration(self.clip) * SAMPLE_RATE)
        source_start, source_end = round(self.clip["in"] * SAMPLE_RATE), round(self.clip["out"] * SAMPLE_RATE)
        context, context_start = np.zeros((2, 0), np.float32), source_start
        accumulator = np.zeros((2, window_size + hop), np.float32)
        normalization = np.zeros(window_size + hop, np.float32)
        previous = None
        center = cursor = 0

        def context_at(start, count):
            nonlocal context, context_start
            left, right = max(start, source_start), min(start + count, source_end)
            result = np.zeros((2, count), np.float32)
            if right <= left:
                return result
            # Requested context advances monotonically because source_at does.
            drop = min(max(0, left - context_start), context.shape[1])
            context = context[:, drop:]
            context_start += drop
            buffered_end = context_start + context.shape[1]
            if left > buffered_end:
                context, context_start = np.zeros((2, 0), np.float32), left
                buffered_end = left
            if right > buffered_end:
                context = np.concatenate((context, self.source.read(buffered_end, right - buffered_end)), axis=1)
            result[:, left - start:right - start] = context[:, left - context_start:right - context_start]
            return result

        while cursor < total:
            if self.cancel_event.is_set():
                raise ExportCancelled()
            count = min(hop, total - cursor)
            # Include every grain that can overlap the block before releasing
            # it. The lookahead is 43 ms, independent of project duration.
            while center - half < cursor + count:
                nominal = round(source_at(self.clip, center / SAMPLE_RATE) * SAMPLE_RATE) - half
                candidates = context_at(nominal - search, window_size + 2 * search)
                offset = search
                if previous is not None:
                    reference = previous[:, hop:].astype(np.float64)
                    incoming = candidates.astype(np.float64)
                    reference_energy = float(np.sum(reference ** 2))
                    if reference_energy > 1e-10:
                        size = reference.shape[1]
                        correlation = sum(np.correlate(incoming[channel, :size + 2 * search], reference[channel], mode="valid")
                                          for channel in range(2))
                        sums = np.concatenate(([0.], np.cumsum(np.sum(incoming[:, :size + 2 * search] ** 2, axis=0))))
                        energy = sums[size:] - sums[:-size]
                        scores = correlation / np.sqrt(np.maximum(energy * reference_energy, 1e-20))
                        # Prefer the analytic center when correlation is tied,
                        # e.g. pure tones, rather than arbitrarily jumping phases.
                        scores -= abs(np.arange(2 * search + 1) - search) * 1e-6
                        offset = int(np.argmax(scores))
                grain = candidates[:, offset:offset + window_size]
                left = center - half - cursor
                grain_left, output_left = max(0, -left), max(0, left)
                length = min(window_size - grain_left, accumulator.shape[1] - output_left)
                if length > 0:
                    accumulator[:, output_left:output_left + length] += grain[:, grain_left:grain_left + length] * weights[grain_left:grain_left + length]
                    normalization[output_left:output_left + length] += weights[grain_left:grain_left + length]
                previous = grain
                center += hop
            yield accumulator[:, :count] / np.maximum(normalization[:count], 1e-12)
            accumulator[:, :-count] = accumulator[:, count:]
            accumulator[:, -count:] = 0
            normalization[:-count] = normalization[count:]
            normalization[-count:] = 0
            cursor += count
    def read(self, count):
        values = np.zeros((2, count), dtype=np.float32)
        written = 0
        while written < count:
            if self.cancel_event.is_set():
                raise ExportCancelled()
            if self.block is None:
                self.block, self.offset = next(self.blocks, None), 0
                if self.block is None:
                    break
            available = min(count - written, self.block.shape[1] - self.offset)
            values[:, written:written + available] = self.block[:, self.offset:self.offset + available]
            self.offset += available
            written += available
            if self.offset == self.block.shape[1]:
                self.block = None
        return values

    def close(self):
        self.blocks.close()
        self.source.close()


def fade_gain(relative, duration, clip):
    """Linear output-time envelope shared by image opacity and source audio."""
    gain = 1.0
    if clip.get("fade_in", 0):
        gain = np.minimum(1, np.maximum(0, relative / clip["fade_in"]))
    if clip.get("fade_out", 0):
        gain = gain * np.minimum(1, np.maximum(0, (duration - relative) / clip["fade_out"]))
    return gain


class AudioMixer:
    """Keep at most one decoder per music track, including at clip boundaries."""

    def __init__(self, clips, media_paths, cancel_event, tracks=4):
        self.pending = {track: sorted((clip for clip in clips if clip.get("track", 0) == track),
                                      key=lambda clip: clip["start"]) for track in range(tracks)}
        self.active = {}
        self.media_paths, self.cancel_event = media_paths, cancel_event

    def mix(self, values, cursor, clip_output=True):
        count = values.shape[1]
        end = cursor + count
        for track in self.pending:
            while True:
                if track not in self.active:
                    if not self.pending[track] or round(self.pending[track][0]["start"] * SAMPLE_RATE) >= end:
                        break
                    clip = self.pending[track].pop(0)
                    start = round(clip["start"] * SAMPLE_RATE)
                    length = round(clip_duration(clip) * SAMPLE_RATE)
                    if not clip["volume"] or start + length <= cursor:
                        continue
                    reader = TempoAudioReader(self.media_paths[clip["media_id"]], clip, self.cancel_event)
                    self.active[track] = (clip, start, start + length, reader)
                clip, start, limit, reader = self.active[track]
                left, right = max(cursor, start), min(end, limit)
                if right > left:
                    sound = reader.read(right - left)
                    gain = np.full(right - left, clip["volume"], dtype=np.float32)
                    relative = np.arange(left - start, right - start, dtype=np.float64) / SAMPLE_RATE
                    duration = clip_duration(clip)
                    gain *= fade_gain(relative, duration, clip)
                    values[:, left - cursor:right - cursor] += sound * gain
                if limit > end:
                    break
                reader.close()
                del self.active[track]
        if clip_output:
            np.clip(values, -1, 1, out=values)

    def close(self):
        for _, _, _, reader in self.active.values():
            reader.close()
        self.active.clear()


def video_layer_audio(layers):
    """Interval-color validated video layers into at most three audio readers."""
    ends, clips = [0.0] * 3, []
    for layer in sorted((item for item in layers if item["kind"] == "video"), key=lambda item: item["start"]):
        available = next((index for index, end in enumerate(ends) if end <= layer["start"]), None)
        if available is None:
            raise EditorError("同一時間最多疊加 3 個影片圖層。")
        ends[available] = layer["end"]
        clips.append({**layer, "track": available})
    return clips


def fit_frame(frame, width, height):
    scale = min(width / frame.width, height / frame.height)
    fitted_width, fitted_height = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
    pixels = frame.reformat(width=fitted_width, height=fitted_height, format="rgb24").to_ndarray()
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    left, top = (width - fitted_width) // 2, (height - fitted_height) // 2
    canvas[top:top + fitted_height, left:left + fitted_width] = pixels
    return av.VideoFrame.from_ndarray(canvas, format="rgb24")


def render_project(project, media_paths, output_path, cancel_event, progress_callback):
    """Render a validated snapshot; audio and video share frame-aligned cuts.

    Cumulative clip boundaries round to the nearest output frame. Audio is
    resampled/trimmed by source PTS, then padded exactly to that same boundary.
    Encoder threads are bounded; decoding uses one thread per codec.
    """
    fps, width, height = project["fps"], project["width"], project["height"]
    counts, cumulative, previous_boundary = [], 0.0, 0
    for clip in project["clips"]:
        duration = clip_duration(clip)
        if duration + 1e-9 < 1 / fps:
            raise EditorError("每個片段至少需要一幀，請增加入點與出點間距。")
        cumulative += duration
        # Half-up rounding keeps exactly one-frame intervals one frame long,
        # including boundaries at .5; per-clip rounding accumulates errors.
        boundary = math.floor(cumulative * fps + 0.5 + 1e-7)
        counts.append(boundary - previous_boundary)
        previous_boundary = boundary
    main_frames = sum(counts)
    overlay_end = max((layer["end"] for layer in project.get("overlays", []) if layer["kind"] == "video"), default=0)
    total_frames = max(main_frames, math.floor(overlay_end * fps + .5 + 1e-7))
    if not total_frames:
        raise EditorError("請先在時間軸加入影片。")
    segments = list(zip(project["clips"], counts))
    if total_frames > main_frames:
        segments.append((None, total_frames - main_frames))
    video_index = audio_cursor = 0
    try:
        with ExitStack() as timeline, av.open(str(output_path), mode="w", format="mp4", options={"movflags": "+faststart"}) as output:
            mixer = AudioMixer(project.get("audio_clips", []), media_paths, cancel_event)
            timeline.callback(mixer.close)
            layer_mixer = AudioMixer(video_layer_audio(project.get("overlays", [])), media_paths, cancel_event, tracks=3)
            timeline.callback(layer_mixer.close)
            compositor = None
            if project.get("overlays"):
                from editor_overlays import OverlayCompositor
                compositor = OverlayCompositor(project["overlays"], media_paths, width, height, cancel_event=cancel_event)
                timeline.callback(compositor.close)
            video = output.add_stream("libx264", rate=fps)
            video.width, video.height, video.pix_fmt = width, height, "yuv420p"
            video.codec_context.thread_count = 2
            video.options = {"crf": "20", "preset": "veryfast", "threads": "2"}
            audio = output.add_stream("aac", rate=SAMPLE_RATE)
            audio.layout, audio.bit_rate = "stereo", 192000
            audio.codec_context.thread_count = 1
            for clip, frames in segments:
                path = media_paths[clip["media_id"]] if clip else None
                with ExitStack() as resources:
                    reader = VideoReader(path, clip["in"], cancel_event) if clip else None
                    if reader is not None:
                        resources.callback(reader.close)
                    sound = TempoAudioReader(path, clip, cancel_event) if clip and clip["volume"] else None
                    if sound is not None:
                        resources.callback(sound.close)
                    duration = clip_duration(clip) if clip else frames / fps
                    clip_audio_limit = round(duration * SAMPLE_RATE)
                    clip_audio_cursor = 0
                    for frame_index in range(frames):
                        if cancel_event.is_set():
                            raise ExportCancelled()
                        frame = (fit_frame(reader.at(source_at(clip, frame_index / fps)), width, height) if reader else
                                 av.VideoFrame.from_ndarray(np.zeros((height, width, 3), np.uint8), format="rgb24"))
                        if clip:
                            gain = fade_gain(frame_index / fps, duration, clip)
                            if gain < 1:
                                pixels = np.rint(frame.to_ndarray(format="rgb24") * gain).astype(np.uint8)
                                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                        if compositor is not None:
                            frame = compositor.apply(frame, video_index / fps)
                        frame.pts, frame.time_base = video_index, Fraction(1, fps)
                        for packet in video.encode(frame):
                            output.mux(packet)
                        video_index += 1
                        target_samples = round(video_index * SAMPLE_RATE / fps)
                        count = target_samples - audio_cursor
                        valid = min(count, max(0, clip_audio_limit - clip_audio_cursor))
                        values = np.zeros((2, count), dtype=np.float32)
                        if sound is not None and valid:
                            values[:, :valid] = sound.read(valid)
                            relative = np.arange(clip_audio_cursor, clip_audio_cursor + valid, dtype=np.float64) / SAMPLE_RATE
                            values[:, :valid] *= clip["volume"] * fade_gain(relative, duration, clip)
                        layer_mixer.mix(values, audio_cursor, clip_output=False)
                        mixer.mix(values, audio_cursor)
                        audio_frame = av.AudioFrame.from_ndarray(values, format="fltp", layout="stereo")
                        audio_frame.sample_rate, audio_frame.time_base = SAMPLE_RATE, Fraction(1, SAMPLE_RATE)
                        audio_frame.pts = audio_cursor
                        for packet in audio.encode(audio_frame):
                            output.mux(packet)
                        audio_cursor += count
                        clip_audio_cursor += count
                        progress_callback(min(99, int(video_index * 99 / total_frames)))
            for stream in (video, audio):
                for packet in stream.encode():
                    output.mux(packet)
        if cancel_event.is_set():
            raise ExportCancelled()
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return {"duration": total_frames / fps, "frame_count": total_frames,
            "width": width, "height": height, "fps": fps, "has_audio": True,
            "audio_sample_rate": SAMPLE_RATE, "audio_samples": audio_cursor}


class EditorStore:
    def __init__(self, data_dir, assets, jobs):
        self.root = Path(data_dir) / "editor"
        self.media_dir, self.project_dir, self.export_dir = (self.root / name for name in ("media", "projects", "exports"))
        for directory in (self.media_dir, self.project_dir, self.export_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.assets, self.jobs = assets, jobs
        self.media = self._load(self.media_dir)
        self.projects = self._load(self.project_dir)
        self.exports = self._load(self.export_dir)
        # Existing projects and media remain readable without a migration step.
        for item in self.media.values():
            item.setdefault("kind", "video")
        for project in self.projects.values():
            project.setdefault("audio_clips", [])
            project.setdefault("overlays", [])
            for clip in project.get("clips", []):
                clip.setdefault("speed", 1)
                clip.setdefault("fade_in", 0)
                clip.setdefault("fade_out", 0)
            for layer in project.get("overlays", []):
                layer.setdefault("fade_in", 0)
                layer.setdefault("fade_out", 0)
        self.export_task = None
        self.cancel_event = threading.Event()
        self.import_lock = asyncio.Lock()
        self.closing = False
        for job in self.exports.values():
            if job.get("status") in ACTIVE:
                job.update(status="failed", error="Studio 重新啟動，先前匯出已中斷，請重新匯出。", updated_at=now())
                atomic_json(self.export_dir / f"{job['id']}.json", job)
            (self.export_dir / f"{job['id']}.partial.mp4").unlink(missing_ok=True)

    @staticmethod
    def _load(directory):
        items = {}
        for path in directory.glob("*.json"):
            if not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict) and item.get("id") == path.stem:
                    items[path.stem] = item
            except (ValueError, OSError):
                LOGGER.warning("Skipping unreadable editor record: %s", path.name)
        return items

    @staticmethod
    def _get(items, item_id, name):
        check_id(item_id)
        if item_id not in items:
            raise EditorError(f"找不到{name}。", 404)
        return items[item_id]

    def media_path(self, media_id):
        self._get(self.media, media_id, "剪輯素材")
        path = (self.media_dir / f"{media_id}.source").resolve()
        if path.parent != self.media_dir.resolve() or not path.is_file():
            raise EditorError("剪輯素材檔案不存在。", 404)
        return path

    async def import_media(self, payload):
        if not isinstance(payload, dict) or set(payload) not in ({"asset_id"}, {"job_id"}):
            raise EditorError("請指定一個 asset_id 或 job_id，不能指定路徑或網址。")
        source_id = check_id(next(iter(payload.values())))
        # Assets/jobs have immutable IDs; the editor already owns an independent
        # copy, so re-importing that source needs no filesystem copy or decode.
        async with self.import_lock:
            for item in self.media.values():
                if item.get("source") == payload and (self.media_dir / f"{item['id']}.source").is_file():
                    return item
            return await self._import_source(payload, source_id)

    async def _import_source(self, payload, source_id):
        if "asset_id" in payload:
            try:
                path = Path(self.assets.path_for(source_id)).resolve()
                metadata = self.assets.metadata(source_id)
            except (ValueError, KeyError, OSError) as error:
                raise EditorError("找不到來源素材。", 404) from error
            asset_root = getattr(self.assets, "directory", None)
            if asset_root is not None and Path(asset_root).resolve() not in path.parents:
                raise EditorError("來源素材不在素材目錄內。")
            name = metadata.get("name") or path.name
        else:
            job = self.jobs.jobs.get(source_id)
            if job is None or job.get("status") != "completed":
                raise EditorError("找不到已完成的影片工作。", 404)
            path = self.jobs.local_output_path(job)
            if path is None:
                raise EditorError("這項工作的本機影片不存在。", 404)
            path = Path(path)
            name = job.get("name") or job.get("title") or path.name
        if not path.is_file():
            raise EditorError("來源影片不存在。", 404)
        if path.suffix.lower() not in {".mp4", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".png", ".jpg", ".jpeg", ".webp"}:
            raise EditorError("支援 MP4／WebM 影片、常用音訊及 PNG／JPEG／WebP 圖片。")
        media_id = uuid.uuid4().hex
        destination = self.media_dir / f"{media_id}.source"
        temporary = self.media_dir / f"{media_id}.copying"
        def copy_and_probe():
            try:
                shutil.copyfile(path, temporary)
                info = probe_media(temporary)
                temporary.replace(destination)
                return info
            finally:
                temporary.unlink(missing_ok=True)
        info = await asyncio.to_thread(copy_and_probe)
        item = {"id": media_id, "name": str(name)[:200], "kind": "video", **info, "created_at": now(),
                "source": dict(payload), "url": f"/api/editor/media/{media_id}/file"}
        try:
            atomic_json(self.media_dir / f"{media_id}.json", item)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        self.media[media_id] = item
        return item

    def _project(self, payload, current):
        if not isinstance(payload, dict):
            raise EditorError("專案設定必須是物件。")
        if payload.get("updated_at") is not None and payload["updated_at"] != current.get("updated_at"):
            raise EditorError("專案已在其他視窗更新，請重新載入或另存。", 409, project=current)
        name = payload.get("name", current.get("name", "未命名剪輯"))
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise EditorError("專案名稱需為 1–200 個字元。")
        width, height = payload.get("width", current.get("width", 1280)), payload.get("height", current.get("height", 720))
        fps = payload.get("fps", current.get("fps", 24))
        if type(width) is not int or type(height) is not int or width not in DIMENSIONS or height not in DIMENSIONS:
            raise EditorError("請使用支援的常用偶數尺寸，任一邊最多 1920 像素。")
        if type(fps) is not int or fps not in FRAME_RATES:
            raise EditorError("幀率只能使用 24、25、30 或 60。")
        clips = payload.get("clips", current.get("clips", []))
        if not isinstance(clips, list) or len(clips) > 50:
            raise EditorError("時間軸最多 50 個片段。")
        clean, ids, duration = [], set(), 0.0
        for clip in clips:
            if not isinstance(clip, dict):
                raise EditorError("片段格式錯誤。")
            clip_id = clip.get("id")
            if not isinstance(clip_id, str) or not CLIP_ID.fullmatch(clip_id) or clip_id in ids:
                raise EditorError("片段識別碼格式錯誤或重複。")
            ids.add(clip_id)
            media = self._get(self.media, clip.get("media_id"), "剪輯素材")
            self.media_path(media["id"])
            if media.get("kind", "video") != "video":
                raise EditorError("影片時間軸只能加入影片；請把音訊加入配樂軌。")
            start = number(clip.get("in"), "入點", 0)
            end = number(clip.get("out"), "出點", 0)
            volume = number(clip.get("volume", 1), "音量", 0, 2)
            speed = number(clip.get("speed", 1), "速度", 0.25, 4)
            if end <= start or end > media["duration"] + 1e-6:
                raise EditorError("出點必須大於入點，且不能超過來源影片長度。")
            end = min(end, media["duration"])
            points = validate_speed_curve(clip, media["duration"])
            timing = {"in": start, "out": end, "speed": speed, "speed_curve": points}
            length = clip_duration(timing)
            if length + 1e-9 < 1 / fps:
                raise EditorError(f"每個片段至少需要一幀（目前約 {1 / fps:.6f} 秒）。")
            duration += length
            fade_in = number(clip.get("fade_in", 0), "淡入", 0, length)
            fade_out = number(clip.get("fade_out", 0), "淡出", 0, length)
            if fade_in + fade_out > length + 1e-6:
                raise EditorError("淡入與淡出總長度不能超過影片片段長度。")
            clean.append({"id": clip_id, "media_id": media["id"], "in": start, "out": end,
                          "volume": volume, "speed": speed, "fade_in": fade_in, "fade_out": fade_out})
            if points:
                clean[-1]["speed_curve"] = points
        if duration > 600 + 1e-6:
            raise EditorError("時間軸總長度最多 10 分鐘。")
        audio_clips = payload.get("audio_clips", current.get("audio_clips", []))
        if not isinstance(audio_clips, list) or len(audio_clips) > 50:
            raise EditorError("配樂軌最多 50 個音訊片段。")
        clean_audio = []
        for clip in audio_clips:
            if not isinstance(clip, dict):
                raise EditorError("音訊片段格式錯誤。")
            clip_id = clip.get("id")
            if not isinstance(clip_id, str) or not CLIP_ID.fullmatch(clip_id) or clip_id in ids:
                raise EditorError("片段識別碼格式錯誤或重複。")
            ids.add(clip_id)
            media = self._get(self.media, clip.get("media_id"), "剪輯素材")
            self.media_path(media["id"])
            if not media.get("has_audio"):
                raise EditorError("這個素材沒有音訊，無法加入配樂軌。")
            start, end = number(clip.get("in"), "入點", 0), number(clip.get("out"), "出點", 0)
            speed = number(clip.get("speed", 1), "速度", 0.25, 4)
            if end <= start or end > media["duration"] + 1e-6:
                raise EditorError("音訊出點必須大於入點，且不能超過來源長度。")
            end = min(end, media["duration"])
            points = validate_speed_curve(clip, media["duration"])
            length = clip_duration({"in": start, "out": end, "speed": speed, "speed_curve": points})
            if length + 1e-9 < 1 / fps or length > 600 + 1e-6:
                raise EditorError("音訊片段調速後需至少一幀，且最長 10 分鐘。")
            fade_in = number(clip.get("fade_in", 0), "淡入", 0, length)
            fade_out = number(clip.get("fade_out", 0), "淡出", 0, length)
            if fade_in + fade_out > length + 1e-6:
                raise EditorError("淡入與淡出總長度不能超過音訊片段長度。")
            track = clip.get("track", 0)
            if type(track) is not int or track not in range(4):
                raise EditorError("配樂軌只能使用 1–4 軌。")
            clean_audio.append({"id": clip_id, "media_id": media["id"], "in": start, "out": end,
                                "start": number(clip.get("start", 0), "時間軸起點", 0, 600),
                                "volume": number(clip.get("volume", 1), "音量", 0, 2), "speed": speed,
                                "fade_in": fade_in, "fade_out": fade_out, "track": track})
            if points:
                clean_audio[-1]["speed_curve"] = points
        for track in range(4):
            previous_end = 0.0
            for clip in sorted((clip for clip in clean_audio if clip["track"] == track), key=lambda clip: clip["start"]):
                if clip["start"] < previous_end - 1e-6:
                    raise EditorError(f"配樂軌 {track + 1} 的片段不能重疊；請移動起點或換到其他配樂軌。")
                previous_end = clip["start"] + clip_duration(clip)
        from editor_overlays import validate_overlays
        overlays = validate_overlays(payload.get("overlays", current.get("overlays", [])), self, fps, ids)
        return {"id": current["id"], "name": name.strip(), "clips": clean, "audio_clips": clean_audio, "overlays": overlays, "width": width,
                "height": height, "fps": fps, "updated_at": now()}

    def create_project(self, payload):
        project = self._project(payload, {"id": uuid.uuid4().hex})
        atomic_json(self.project_dir / f"{project['id']}.json", project)
        self.projects[project["id"]] = project
        return project

    def update_project(self, project_id, payload):
        current = self._get(self.projects, project_id, "剪輯專案")
        project = self._project(payload, current)
        atomic_json(self.project_dir / f"{project_id}.json", project)
        self.projects[project_id] = project
        return project

    def start_export(self, project_id):
        if self.closing or self.export_task is not None and not self.export_task.done():
            raise EditorError("已有影片正在匯出，請完成或取消後再試。", 409)
        project = self._get(self.projects, project_id, "剪輯專案")
        snapshot = self._project(project, project)
        snapshot["updated_at"] = project["updated_at"]
        if not snapshot["clips"] and not any(layer["kind"] == "video" for layer in snapshot["overlays"]):
            raise EditorError("請先在時間軸加入影片。")
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "project_id": project_id, "status": "queued", "progress": 0,
               "created_at": now(), "updated_at": now(), "snapshot": snapshot}
        atomic_json(self.export_dir / f"{job_id}.json", job)
        self.exports[job_id] = job
        self.cancel_event = threading.Event()
        self.export_task = asyncio.create_task(self._export(job_id, self.cancel_event))
        return self.public_export(job)

    @staticmethod
    def public_export(job):
        return {key: value for key, value in job.items() if key != "snapshot"}

    def _update_export(self, job_id, **changes):
        job = self.exports[job_id]
        job.update(changes, updated_at=now())
        atomic_json(self.export_dir / f"{job_id}.json", job)

    def _progress(self, job_id, value):
        job = self.exports[job_id]
        if job["status"] == "running" and value > job["progress"]:
            self._update_export(job_id, progress=min(99, value))

    async def _export(self, job_id, event):
        job = self.exports[job_id]
        temporary, final = (self.export_dir / f"{job_id}{suffix}" for suffix in (".partial.mp4", ".mp4"))
        worker = None
        try:
            if event.is_set():
                raise ExportCancelled()
            all_clips = job["snapshot"]["clips"] + job["snapshot"].get("audio_clips", [])
            all_clips += [layer for layer in job["snapshot"].get("overlays", []) if layer["kind"] in ("image", "video")]
            paths = {clip["media_id"]: self.media_path(clip["media_id"]) for clip in all_clips}
            self._update_export(job_id, status="running")
            loop = asyncio.get_running_loop()
            progress = lambda value: loop.call_soon_threadsafe(self._progress, job_id, value)
            worker = asyncio.create_task(asyncio.to_thread(render_project, job["snapshot"], paths, temporary, event, progress))
            result = await asyncio.shield(worker)
            if event.is_set():
                raise ExportCancelled()
            temporary.replace(final)
            self._update_export(job_id, status="completed", progress=100,
                                url=f"/api/editor/exports/{job_id}/file", output=result or {})
        except (ExportCancelled, asyncio.CancelledError):
            event.set()
            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            self._update_export(job_id, status="cancelled", error="匯出已取消。")
        except Exception as error:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            LOGGER.exception("Editor export failed: %s", job_id)
            self._update_export(job_id, status="failed", error=f"匯出失敗：{error}")

    async def cancel_export(self, job_id):
        job = self._get(self.exports, job_id, "匯出工作")
        if job["status"] in ACTIVE:
            self.cancel_event.set()
            if self.export_task is not None:
                await asyncio.shield(self.export_task)
        return self.public_export(job)

    def export_path(self, job_id):
        job = self._get(self.exports, job_id, "匯出工作")
        if job["status"] != "completed":
            raise EditorError("匯出尚未完成。", 409)
        path = (self.export_dir / f"{job_id}.mp4").resolve()
        if path.parent != self.export_dir.resolve() or not path.is_file():
            raise EditorError("匯出檔案不存在。", 404)
        return path

    async def shutdown(self):
        self.closing = True
        if self.export_task is not None and not self.export_task.done():
            self.cancel_event.set()
            await asyncio.shield(self.export_task)


def register_editor_routes(app, static_dir, data_dir):
    store = EditorStore(data_dir, app["assets"], app["jobs"])
    app["editor"] = store

    @web.middleware
    async def errors(request, handler):
        if not request.path.startswith("/api/editor/"):
            return await handler(request)
        try:
            return await handler(request)
        except EditorError as error:
            return web.json_response({"error": str(error), **error.extra}, status=error.status)
        except (ValueError, TypeError, KeyError) as error:
            return web.json_response({"error": "剪輯請求格式錯誤。"}, status=400)
        except OSError:
            LOGGER.exception("Editor storage operation failed")
            return web.json_response({"error": "無法讀寫剪輯檔案，請確認磁碟空間與檔案權限。"}, status=500)

    app.middlewares.append(errors)

    async def page(request):
        return web.FileResponse(Path(static_dir) / "editor.html")

    async def list_media(request):
        return web.json_response(sorted(store.media.values(), key=lambda item: item["created_at"], reverse=True), headers={"Cache-Control": "no-store"})

    async def import_media(request):
        return web.json_response(await store.import_media(await request.json()), status=201)

    async def media_file(request):
        media_id = request.match_info["media_id"]
        path = store.media_path(media_id)
        return web.FileResponse(path, headers={"Content-Type": store.media[media_id]["mime"],
                                               "Cache-Control": "private, max-age=31536000, immutable"})

    async def list_projects(request):
        return web.json_response(sorted(store.projects.values(), key=lambda item: item["updated_at"], reverse=True), headers={"Cache-Control": "no-store"})

    async def create_project(request):
        payload = await request.json() if request.can_read_body else {}
        return web.json_response(store.create_project(payload), status=201)

    async def get_project(request):
        return web.json_response(store._get(store.projects, request.match_info["project_id"], "剪輯專案"), headers={"Cache-Control": "no-store"})

    async def update_project(request):
        return web.json_response(store.update_project(request.match_info["project_id"], await request.json()))

    async def start_export(request):
        return web.json_response(store.start_export(request.match_info["project_id"]), status=202)

    async def get_export(request):
        return web.json_response(store.public_export(store._get(store.exports, request.match_info["export_id"], "匯出工作")), headers={"Cache-Control": "no-store"})

    async def cancel_export(request):
        return web.json_response(await store.cancel_export(request.match_info["export_id"]))

    async def export_file(request):
        export_id = request.match_info["export_id"]
        path = store.export_path(export_id)
        headers = {"Content-Type": "video/mp4"}
        if request.query.get("download") == "1":
            name = store.exports[export_id].get("snapshot", {}).get("name", "剪輯影片")
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(name)).strip(" .")[:100] or "剪輯影片"
            ascii_name = name.encode("ascii", "ignore").decode("ascii").strip(" .") or f"edited-{path.stem}"
            headers["Content-Disposition"] = (f'attachment; filename="{ascii_name}.mp4"; '
                                               f"filename*=UTF-8''{quote(name + '.mp4', safe='')}")
        return web.FileResponse(path, headers=headers)

    async def cleanup(_):
        await store.shutdown()

    app.router.add_get("/editor", page)
    app.router.add_get("/api/editor/media", list_media)
    app.router.add_post("/api/editor/media", import_media)
    app.router.add_get("/api/editor/media/{media_id}/file", media_file)
    app.router.add_get("/api/editor/projects", list_projects)
    app.router.add_post("/api/editor/projects", create_project)
    app.router.add_get("/api/editor/projects/{project_id}", get_project)
    app.router.add_put("/api/editor/projects/{project_id}", update_project)
    app.router.add_post("/api/editor/projects/{project_id}/exports", start_export)
    app.router.add_get("/api/editor/exports/{export_id}", get_export)
    app.router.add_post("/api/editor/exports/{export_id}/cancel", cancel_export)
    app.router.add_get("/api/editor/exports/{export_id}/file", export_file)
    app.on_cleanup.append(cleanup)
    return store
