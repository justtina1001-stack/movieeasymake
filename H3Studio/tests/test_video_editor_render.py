"""Small real PyAV encodes: no models, GPU, network, or existing Studio data."""
from fractions import Fraction
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import av
import numpy as np

import video_editor as editor
from video_editor import EditorError, ExportCancelled, probe_media, render_project


def make_source(path, *, sound=True, blue=False, rotation=0, audio_delay=0, vfr=False, webm=False):
    rate, audio_rate = 12, 44100
    with av.open(str(path), "w") as output:
        video = output.add_stream("libvpx-vp9" if webm else "libx264", rate=rate)
        video.width, video.height, video.pix_fmt = (80 if blue else 96), (80 if blue else 64), "yuv420p"
        video.codec_context.thread_count = 1
        video.options = ({"crf": "30", "deadline": "realtime", "cpu-used": "8", "threads": "1"}
                         if webm else {"crf": "12", "preset": "ultrafast", "threads": "1"})
        if rotation:
            video.set_display_rotation(rotation)
        audio = None
        if sound:
            if webm:
                audio_rate = 48000
            audio = output.add_stream("libopus" if webm else "aac", rate=audio_rate)
            audio.layout, audio.bit_rate = "stereo", 192000
            audio.codec_context.thread_count = 1
        previous_samples = 0
        points = [0, 1, 3, 6, 9, 12, 15, 18, 21, 23] if vfr else range(24)
        for index in points:
            pixels = np.zeros((video.height, video.width, 3), dtype=np.uint8)
            pixels[..., 2 if blue else (0 if index < 12 else 1)] = 255
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, rate)
            for packet in video.encode(frame):
                output.mux(packet)
            if audio is not None:
                count = round((index + 1) * audio_rate / rate) - previous_samples
                positions = np.arange(previous_samples, previous_samples + count)
                amplitude = np.where(positions < audio_rate, 0.2, 0.6)
                wave = (amplitude * np.sin(2 * np.pi * 440 * positions / audio_rate)).astype(np.float32)
                values = np.stack((wave, wave))
                sound_frame = av.AudioFrame.from_ndarray(values, format="fltp", layout="stereo")
                sound_frame.pts = previous_samples + round(audio_delay * audio_rate)
                sound_frame.time_base, sound_frame.sample_rate = Fraction(1, audio_rate), audio_rate
                for packet in audio.encode(sound_frame):
                    output.mux(packet)
                previous_samples += count
        for stream in (video, audio):
            if stream is not None:
                for packet in stream.encode():
                    output.mux(packet)


def decoded_audio(path):
    with av.open(str(path)) as source:
        frames = list(source.decode(audio=0))
        rate = source.streams.audio[0].sample_rate
    end = max(round(float(frame.pts * frame.time_base) * rate) + frame.samples for frame in frames)
    audio = np.zeros((2, end), dtype=np.float32)
    for frame in frames:
        start = round(float(frame.pts * frame.time_base) * rate)
        values = frame.to_ndarray()
        audio[:, max(0, start):start + frame.samples] = values[:, max(0, -start):]
    return rate, audio


def make_audio(path, *, codec="pcm_s16le", rate=48000, amplitude=0.3, frequency=660, duration=2):
    with av.open(str(path), "w") as output:
        audio = output.add_stream(codec, rate=rate)
        audio.layout = "stereo"
        audio.codec_context.thread_count = 1
        if codec == "vorbis":
            audio.options = {"strict": "experimental"}
        for position in range(0, round(duration * rate), 2048):
            count = min(2048, round(duration * rate) - position)
            wave = (amplitude * np.sin(2 * np.pi * frequency * np.arange(position, position + count) / rate)).astype(np.float32)
            frame = av.AudioFrame.from_ndarray(np.stack((wave, wave)), format="fltp", layout="stereo")
            frame.pts, frame.time_base, frame.sample_rate = position, Fraction(1, rate), rate
            for packet in audio.encode(frame):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)


class EditorRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-render-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def project(self, clips):
        return {"id": "test", "name": "CPU synthetic", "width": 96, "height": 96, "fps": 24, "clips": clips}

    def test_trim_join_resample_original_audio_silence_volume_and_letterbox(self):
        source, silent = self.root / "tone.mp4", self.root / "silent.mp4"
        make_source(source)
        make_source(silent, sound=False, blue=True)
        self.assertTrue(probe_media(source)["has_audio"])
        self.assertFalse(probe_media(silent)["has_audio"])
        project = self.project([
            {"media_id": "a", "in": 0.5, "out": 1.5, "volume": 0.5},
            {"media_id": "b", "in": 0.2, "out": 0.7, "volume": 1},
            {"media_id": "a", "in": 1.5, "out": 2, "volume": 0},
        ])
        target = self.root / "joined.mp4"
        progress = []
        result = render_project(project, {"a": source, "b": silent}, target, threading.Event(), progress.append)
        self.assertEqual(result["frame_count"], 48)
        self.assertEqual(result["audio_samples"], 96000)
        self.assertEqual(progress[-1], 99)
        self.assertEqual(progress, sorted(progress))
        with av.open(str(target)) as output:
            frames = list(output.decode(video=0))
            self.assertEqual(len(frames), 48)
            self.assertAlmostEqual(float(output.streams.video[0].duration * output.streams.video[0].time_base), 2, places=6)
        pixels = [frame.to_ndarray(format="rgb24") for frame in frames]
        self.assertGreater(pixels[0][48, 48, 0], 230)
        self.assertGreater(pixels[18][48, 48, 1], 230)
        self.assertGreater(pixels[28][48, 48, 2], 230)
        self.assertGreater(pixels[40][48, 48, 1], 230)
        self.assertLess(pixels[0][:12].mean(), 2)  # 96x64 fitted into 96x96.
        rate, audio = decoded_audio(target)
        self.assertEqual(rate, 48000)
        rms = lambda start, end: float(np.sqrt(np.mean(audio[:, round(start * rate):round(end * rate)] ** 2)))
        self.assertAlmostEqual(rms(0.1, 0.4), 0.1 / np.sqrt(2), delta=0.008)
        self.assertAlmostEqual(rms(0.6, 0.9), 0.3 / np.sqrt(2), delta=0.015)
        self.assertLess(rms(1.1, 1.4), 0.001)
        self.assertLess(rms(1.6, 1.9), 0.001)
        with av.open(str(target)) as output:
            duration = output.streams.audio[0].duration * output.streams.audio[0].time_base
            self.assertLessEqual(abs(float(duration) - 2), 1 / 24)

    def test_vfr_uses_source_timestamps_and_outputs_cfr(self):
        source = self.root / "vfr.mp4"
        make_source(source, sound=False, vfr=True)
        target = self.root / "cfr.mp4"
        render_project(self.project([{"media_id": "a", "in": 0.5, "out": 1.5, "volume": 1}]),
                       {"a": source}, target, threading.Event(), lambda value: None)
        with av.open(str(target)) as output:
            frames = list(output.decode(video=0))
        self.assertEqual(len(frames), 24)
        self.assertTrue(np.allclose(np.diff([frame.time for frame in frames]), 1 / 24))
        self.assertGreater(frames[0].to_ndarray(format="rgb24")[48, 48, 0], 230)
        self.assertGreater(frames[13].to_ndarray(format="rgb24")[48, 48, 1], 230)
        _, audio = decoded_audio(target)
        self.assertLess(float(np.max(np.abs(audio))), 0.001)

    def test_webm_vp9_opus_probe_and_nonzero_trim_to_mp4(self):
        source, target = self.root / "source.webm", self.root / "trimmed.mp4"
        make_source(source, webm=True)
        info = probe_media(source)
        self.assertEqual(info["mime"], "video/webm")
        self.assertTrue(info["has_audio"])
        result = render_project(self.project([{"media_id": "a", "in": 0.5, "out": 1.5, "volume": 0.5}]),
                                {"a": source}, target, threading.Event(), lambda value: None)
        self.assertEqual(result["frame_count"], 24)
        with av.open(str(target)) as output:
            self.assertEqual(output.streams.video[0].codec_context.name, "h264")
            self.assertEqual(output.streams.audio[0].codec_context.name, "aac")
            frames = list(output.decode(video=0))
        self.assertGreater(frames[0].to_ndarray(format="rgb24")[48, 48, 0], 225)
        self.assertGreater(frames[18].to_ndarray(format="rgb24")[48, 48, 1], 225)
        rate, audio = decoded_audio(target)
        rms = lambda start, end: float(np.sqrt(np.mean(audio[:, round(start * rate):round(end * rate)] ** 2)))
        self.assertAlmostEqual(rms(0.1, 0.4), 0.1 / np.sqrt(2), delta=0.012)
        self.assertAlmostEqual(rms(0.6, 0.9), 0.3 / np.sqrt(2), delta=0.018)

    def test_delayed_audio_timestamps_preserve_leading_silence_after_trim(self):
        source, target = self.root / "delay.mp4", self.root / "trim.mp4"
        make_source(source, audio_delay=0.5)
        render_project(self.project([{"media_id": "a", "in": 0.25, "out": 1.25, "volume": 1}]),
                       {"a": source}, target, threading.Event(), lambda value: None)
        rate, audio = decoded_audio(target)
        self.assertLess(float(np.abs(audio[:, round(0.02 * rate):round(0.15 * rate)]).max()), 0.003)
        self.assertGreater(float(np.sqrt(np.mean(audio[:, round(0.4 * rate):round(0.6 * rate)] ** 2))), 0.1)

    def test_cancel_removes_partial_output(self):
        source, target = self.root / "source.mp4", self.root / "partial.mp4"
        make_source(source, sound=False)
        event = threading.Event()
        def progress(value):
            if value >= 20:
                event.set()
        with self.assertRaises(ExportCancelled):
            render_project(self.project([{"media_id": "a", "in": 0, "out": 1, "volume": 1}]),
                           {"a": source}, target, event, progress)
        self.assertFalse(target.exists())

    @unittest.skipUnless(hasattr(av.VideoStream, "set_display_rotation"),
                         "PyAV <17.1 can read rotation but cannot write this synthetic fixture")
    def test_rotation_is_explicitly_rejected(self):
        source = self.root / "rotated.mp4"
        make_source(source, sound=False, rotation=90)
        with self.assertRaisesRegex(EditorError, "旋轉"):
            probe_media(source)

    def test_many_short_clips_round_cumulative_boundaries_not_each_clip(self):
        source, target = self.root / "short.mp4", self.root / "many.mp4"
        make_source(source, sound=False)
        duration = 1.49 / 24
        clips = [{"media_id": "a", "in": 0, "out": duration, "volume": 0} for _ in range(50)]
        result = render_project(self.project(clips), {"a": source}, target, threading.Event(), lambda value: None)
        self.assertEqual(result["frame_count"], 75)
        self.assertLessEqual(abs(result["duration"] - duration * 50), 0.5 / 24 + 1e-10)
        self.assertEqual(result["audio_samples"], 75 * 2000)
        with av.open(str(target)) as output:
            self.assertEqual(sum(1 for _ in output.decode(video=0)), 75)
        with self.assertRaisesRegex(EditorError, "一幀"):
            render_project(self.project([{"media_id": "a", "in": 0, "out": 0.01, "volume": 0}]),
                           {"a": source}, self.root / "too-short.mp4", threading.Event(), lambda value: None)

    def test_speed_changes_duration_and_video_timing_while_preserving_audio_pitch(self):
        source = self.root / "speed.mp4"
        make_source(source)
        for speed in (0.25, 0.5, 2, 4):
            with self.subTest(speed=speed):
                target = self.root / f"speed-{speed}.mp4"
                clip = {"media_id": "a", "in": 0.25, "out": 1.75, "volume": 1, "speed": speed}
                result = render_project(self.project([clip]), {"a": source}, target, threading.Event(), lambda _: None)
                self.assertEqual(result["frame_count"], round(1.5 / speed * 24))
                with av.open(str(target)) as output:
                    frames = list(output.decode(video=0))
                self.assertGreater(frames[0].to_ndarray(format="rgb24")[48, 48, 0], 230)
                self.assertGreater(frames[-1].to_ndarray(format="rgb24")[48, 48, 1], 230)
                rate, values = decoded_audio(target)
                wave = values[0, round(0.06 * rate):round((result["duration"] - 0.06) * rate)]
                spectrum = np.abs(np.fft.rfft(wave * np.hanning(len(wave))))
                frequency = np.fft.rfftfreq(len(wave), 1 / rate)[np.argmax(spectrum)]
                self.assertAlmostEqual(frequency, 440, delta=6)

    def test_audio_formats_probe_and_delayed_faded_music_mixing(self):
        source = self.root / "base.mp4"
        make_source(source, sound=False)
        cases = ((".wav", "pcm_s16le", "audio/wav"), (".mp3", "libmp3lame", "audio/mpeg"),
                 (".m4a", "aac", "audio/mp4"), (".aac", "aac", "audio/aac"),
                 (".flac", "flac", "audio/flac"), (".ogg", "vorbis", "audio/ogg"),
                 (".opus", "libopus", "audio/ogg"))
        for extension, codec, mime in cases:
            with self.subTest(extension=extension):
                path = self.root / f"tone{extension}"
                make_audio(path, codec=codec)
                info = probe_media(path)
                self.assertEqual(info["kind"], "audio")
                self.assertEqual(info["mime"], mime)
                self.assertAlmostEqual(info["duration"], 2, delta=0.06)
                project = self.project([{"media_id": "v", "in": 0, "out": 1, "volume": 0}])
                project["audio_clips"] = [{"media_id": "a", "in": 0.5, "out": 1.5, "start": 0,
                                           "volume": 1, "speed": 1, "track": 0}]
                target = self.root / f"audio-{extension[1:]}.mp4"
                render_project(project, {"v": source, "a": path}, target, threading.Event(), lambda _: None)
                rate, sound = decoded_audio(target)
                rms = float(np.sqrt(np.mean(sound[:, round(.1 * rate):round(.8 * rate)] ** 2)))
                self.assertAlmostEqual(rms, 0.3 / np.sqrt(2), delta=0.02)
        source, target = self.root / "base.mp4", self.root / "mixed.mp4"
        project = self.project([{"media_id": "v", "in": 0, "out": 2, "volume": 1}])
        project["audio_clips"] = [{"media_id": "a", "in": 0.25, "out": 1.75, "start": 0.25,
                                   "volume": 1, "speed": 1, "fade_in": 0.5, "fade_out": 0.5, "track": 0}]
        result = render_project(project, {"v": source, "a": self.root / "tone.wav"}, target,
                                threading.Event(), lambda _: None)
        self.assertEqual(result["audio_samples"], 96000)
        rate, values = decoded_audio(target)
        rms = lambda start, end: float(np.sqrt(np.mean(values[:, round(start * rate):round(end * rate)] ** 2)))
        self.assertLess(rms(0.02, 0.15), 0.001)
        self.assertLess(rms(1.85, 1.95), 0.001)
        self.assertAlmostEqual(rms(0.8, 1.2), 0.3 / np.sqrt(2), delta=0.012)
        self.assertLess(rms(0.3, 0.4), rms(0.6, 0.7) / 2)
        self.assertLess(rms(1.6, 1.7), rms(1.3, 1.4) / 2)

    def test_music_speed_overlap_clamping_and_video_duration_truncation(self):
        source, tone, target = self.root / "base.mp4", self.root / "loud.wav", self.root / "overlap.mp4"
        make_source(source, sound=False)
        make_audio(tone, amplitude=0.7)
        project = self.project([{"media_id": "v", "in": 0, "out": 1, "volume": 0}])
        project["audio_clips"] = [{"media_id": "a", "in": 0, "out": 2, "start": 0,
                                   "volume": 2, "speed": 0.5, "track": track} for track in range(4)]
        result = render_project(project, {"v": source, "a": tone}, target, threading.Event(), lambda _: None)
        self.assertEqual(result["duration"], 1)
        rate, values = decoded_audio(target)
        wave = values[0, round(.1 * rate):round(.8 * rate)]
        self.assertTrue(np.isfinite(wave).all())
        # Lossy AAC may overshoot clipped samples slightly; encoding remains finite.
        self.assertLess(np.max(np.abs(wave)), 1.3)
        frequency = np.fft.rfftfreq(len(wave), 1 / rate)[np.argmax(np.abs(np.fft.rfft(wave)))]
        self.assertAlmostEqual(frequency, 660, delta=5)

    def test_adjacent_music_clips_keep_at_most_four_decoders_even_within_one_video_frame(self):
        source, tone, target = self.root / "base.mp4", self.root / "tone.wav", self.root / "bounded.mp4"
        make_source(source, sound=False)
        make_audio(tone)
        project = self.project([{"media_id": "v", "in": 0, "out": 1, "volume": 0}])
        project["audio_clips"] = [
            {"media_id": "a", "in": 0, "out": duration, "start": start, "volume": 0.1, "track": track}
            for track in range(4) for start, duration in ((0, .37), (.37, .5))
        ]
        active = peak = 0
        class CountedReader(editor.TempoAudioReader):
            def __init__(self, *args):
                nonlocal active, peak
                super().__init__(*args)
                active += 1
                peak = max(peak, active)
            def close(self):
                nonlocal active
                super().close()
                active -= 1
        with patch.object(editor, "TempoAudioReader", CountedReader):
            render_project(project, {"v": source, "a": tone}, target, threading.Event(), lambda _: None)
        self.assertEqual(peak, 4)
        self.assertEqual(active, 0)


if __name__ == "__main__":
    unittest.main()
