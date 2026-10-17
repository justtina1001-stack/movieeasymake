"""Audio-only export clock for speech captions; no video decode or encoding."""
from contextlib import ExitStack
from fractions import Fraction
import math
from pathlib import Path

import av
import numpy as np

from video_editor import (AudioMixer, EditorError, ExportCancelled, SAMPLE_RATE,
                          TempoAudioReader, clip_duration, fade_gain,
                          video_layer_audio)


def render_caption_audio(project, media_paths, output_path, cancel_event, progress_callback):
    """Write the final audible timeline to 48 kHz stereo float PCM WAV.

    The caller supplies a validated project snapshot and resolved editor media
    paths. Keep the same cumulative frame rounding, clip padding, tempo,
    envelopes, and final clipping as MP4 export, including video-layer tails.
    Static overlay and standalone audio tails do not extend the video clock.
    """
    output_path = Path(output_path)
    try:
        fps = project["fps"]
        counts, cumulative, previous_boundary = [], 0.0, 0
        for clip in project["clips"]:
            length = clip_duration(clip)
            if length + 1e-9 < 1 / fps:
                raise EditorError("每個片段至少需要一幀，請增加入點與出點間距。")
            cumulative += length
            boundary = math.floor(cumulative * fps + .5 + 1e-7)
            counts.append(boundary - previous_boundary)
            previous_boundary = boundary
        main_frames = sum(counts)
        overlay_end = max((layer["end"] for layer in project.get("overlays", [])
                           if layer["kind"] == "video"), default=0)
        total_frames = max(main_frames, math.floor(max(overlay_end, project.get("canvas_duration", 0)) * fps + .5 + 1e-7))
        if not total_frames:
            raise EditorError("請先在時間軸加入影片。")
        segments = list(zip(project["clips"], counts))
        if total_frames > main_frames:
            segments.append((None, total_frames - main_frames))
        frame_cursor = audio_cursor = 0
        peak = 0.0
        with ExitStack() as timeline, av.open(str(output_path), mode="w", format="wav") as output:
            mixer = AudioMixer(project.get("audio_clips", []), media_paths, cancel_event)
            timeline.callback(mixer.close)
            layer_mixer = AudioMixer(video_layer_audio(project.get("overlays", [])),
                                     media_paths, cancel_event, tracks=3)
            timeline.callback(layer_mixer.close)
            audio = output.add_stream("pcm_f32le", rate=SAMPLE_RATE)
            audio.layout = "stereo"
            audio.codec_context.thread_count = 1
            for clip, frames in segments:
                with ExitStack() as resources:
                    sound = (TempoAudioReader(media_paths[clip["media_id"]], clip, cancel_event)
                             if clip and clip["volume"] else None)
                    if sound is not None:
                        resources.callback(sound.close)
                    length = clip_duration(clip) if clip else frames / fps
                    clip_audio_limit = round(length * SAMPLE_RATE)
                    clip_audio_cursor = 0
                    for _ in range(frames):
                        if cancel_event.is_set():
                            raise ExportCancelled()
                        frame_cursor += 1
                        target_samples = round(frame_cursor * SAMPLE_RATE / fps)
                        count = target_samples - audio_cursor
                        valid = min(count, max(0, clip_audio_limit - clip_audio_cursor))
                        values = np.zeros((2, count), dtype=np.float32)
                        if sound is not None and valid:
                            values[:, :valid] = sound.read(valid)
                            relative = np.arange(clip_audio_cursor, clip_audio_cursor + valid,
                                                 dtype=np.float64) / SAMPLE_RATE
                            values[:, :valid] *= clip["volume"] * fade_gain(relative, length, clip)
                        layer_mixer.mix(values, audio_cursor, clip_output=False)
                        mixer.mix(values, audio_cursor)
                        peak = max(peak, float(np.max(np.abs(values))))
                        frame = av.AudioFrame.from_ndarray(values, format="fltp", layout="stereo")
                        frame.sample_rate, frame.time_base = SAMPLE_RATE, Fraction(1, SAMPLE_RATE)
                        frame.pts = audio_cursor
                        for packet in audio.encode(frame):
                            output.mux(packet)
                        audio_cursor += count
                        clip_audio_cursor += count
                        progress_callback(min(99, int(frame_cursor * 99 / total_frames)))
            for packet in audio.encode():
                output.mux(packet)
        if cancel_event.is_set():
            raise ExportCancelled()
        return {"duration": total_frames / fps, "frames": total_frames,
                "sample_rate": SAMPLE_RATE, "samples": audio_cursor, "peak": peak}
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
