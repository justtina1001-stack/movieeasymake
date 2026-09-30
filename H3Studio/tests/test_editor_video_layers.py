"""Short real exports for visual fades and bounded video layer playback."""
from fractions import Fraction
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
from PIL import Image

import editor_overlays as overlays
from test_video_editor_render import decoded_audio, make_audio, make_source
from test_editor_overlays import image_layer, text_layer
import video_editor as editor


def video_layer(**values):
    return {"id": "layer", "kind": "video", "media_id": "red", "start": 0, "end": 1,
            "in": 0, "out": 1, "speed": 1, "volume": 1, "x": .5, "y": .5, "width": 1,
            "rotation": 0, "opacity": 1, "fade_in": 0, "fade_out": 0, **values}


def clip(**values):
    return {"id": "main", "media_id": "red", "in": 0, "out": 1, "speed": 1, "volume": 1,
            "fade_in": 0, "fade_out": 0, **values}


class VideoLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-video-layer-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.paths = {"red": self.root / "red.mp4", "blue": self.root / "blue.mp4"}
        make_source(self.paths["red"])
        make_source(self.paths["blue"], sound=False, blue=True)
        self.export_index = 0

    def export(self, clips, layers, audio_clips=None):
        self.export_index += 1
        path = self.root / f"render{self.export_index}.mp4"
        project = {"width": 96, "height": 96, "fps": 24, "clips": clips,
                   "overlays": layers, "audio_clips": audio_clips or []}
        info = editor.render_project(project, self.paths, path, threading.Event(), lambda _: None)
        with av.open(str(path)) as source:
            frames = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]
        return path, frames, info

    def test_main_video_fades_apply_to_picture_and_original_sound(self):
        path, frames, _ = self.export([clip(fade_in=.5, fade_out=.5)], [])
        self.assertLess(frames[0][48, 48].max(), 8)
        self.assertTrue(112 < frames[6][48, 48, 0] < 142)
        self.assertGreater(frames[12][48, 48, 0], 230)
        self.assertTrue(112 < frames[18][48, 48, 0] < 142)
        rate, audio = decoded_audio(path)
        rms = lambda start, end: float(np.sqrt(np.mean(audio[:, round(start * rate):round(end * rate)] ** 2)))
        self.assertGreater(rms(.42, .58), rms(.02, .08) * 5)
        self.assertGreater(rms(.42, .58), rms(.92, .98) * 5)

    def test_video_layer_opacity_fades_over_base_and_audio_independence(self):
        layer = video_layer(media_id="blue", opacity=.5, fade_in=.5, fade_out=.5, volume=0)
        _, frames, _ = self.export([clip(volume=0)], [layer])
        self.assertGreater(frames[0][48, 48, 0], 230)
        self.assertLess(frames[0][48, 48, 2], 20)
        self.assertTrue(45 < frames[6][48, 48, 2] < 82)
        self.assertTrue(110 < frames[12][48, 48, 2] < 145)
        self.assertTrue(110 < frames[12][48, 48, 0] < 145)
        audible = video_layer(fade_in=.4, fade_out=.4, opacity=0)
        hidden, _, _ = self.export([], [audible])
        visible, _, _ = self.export([], [{**audible, "opacity": 1}])
        _, hidden_sound = decoded_audio(hidden)
        _, visible_sound = decoded_audio(visible)
        np.testing.assert_allclose(hidden_sound, visible_sound, atol=1e-6)
        self.assertGreater(float(np.abs(hidden_sound).max()), .1)

    def test_video_only_start_gap_and_speed_source_trim_extend_main_tail(self):
        layer = video_layer(start=.25, end=.75, **{"in": .75, "out": 1.75, "speed": 2, "volume": 0})
        _, frames, info = self.export([clip(media_id="blue", out=.5, volume=0)], [layer])
        self.assertEqual(info["frame_count"], 18)
        self.assertGreater(frames[0][48, 48, 2], 230)
        self.assertGreater(frames[6][48, 48, 0], 230)
        self.assertGreater(frames[10][48, 48, 1], 230)
        self.assertGreater(frames[17][48, 48, 1], 230)
        _, only, info = self.export([], [layer])
        self.assertEqual(info["frame_count"], 18)
        self.assertLess(only[5].max(), 8)
        self.assertGreater(only[6][48, 48, 0], 230)
        self.assertLess(only[17][3, 48].max(), 8)  # transparent outside the native aspect

    def test_shared_track_sequential_videos_render_at_one_stacking_level(self):
        layers = [video_layer(id="first", track_id="shared", media_id="red", end=.5, out=.5, width=.6, volume=0),
                  video_layer(id="second", track_id="shared", media_id="blue", start=.5, end=1, out=.5, width=.6, volume=0)]
        _, frames, info = self.export([clip(media_id="blue", volume=0)], layers)
        self.assertEqual(info["frame_count"], 24)
        self.assertGreater(frames[6][48, 48, 0], 230)
        self.assertLess(frames[6][48, 48, 2], 20)
        self.assertGreater(frames[12][48, 48, 2], 230)
        self.assertLess(frames[12][48, 48, 0], 20)
        self.assertGreater(frames[18][48, 48, 2], 230)
        # A later group stays above both sequential clips at its own level.
        cover = text_layer(id="front", text="", width=.2, background="#00FF00", start=0, end=1)
        _, covered, _ = self.export([clip(media_id="blue", volume=0)], layers + [cover])
        for index in (6, 12, 18):
            self.assertGreater(covered[index][48, 48, 1], 230)
            self.assertLess(covered[index][48, 48, 0], 20)
            self.assertLess(covered[index][48, 48, 2], 20)

    def test_stacking_order_and_image_text_fade_preserve_static_cache(self):
        image = self.root / "green.png"
        with Image.new("RGBA", (10, 10), (0, 255, 0, 255)) as source:
            source.save(image)
        self.paths["image"] = image
        still = image_layer(media_id="image", width=.5, opacity=.5, fade_in=.5, fade_out=.5)
        video = video_layer(media_id="blue", volume=0)
        _, frames, _ = self.export([clip(volume=0)], [video, still])
        self.assertGreater(frames[0][48, 48, 2], 230)
        self.assertTrue(110 < frames[12][48, 48, 1] < 145)
        self.assertTrue(110 < frames[12][48, 48, 2] < 145)
        _, reverse, _ = self.export([clip(volume=0)], [still, video])
        self.assertGreater(reverse[12][48, 48, 2], 230)
        self.assertLess(reverse[12][48, 48, 1], 20)
        text = text_layer(text="", background="#FFFFFF", fade_in=.5, fade_out=.5)
        compositor = overlays.OverlayCompositor([text, still], self.paths, 96, 96)
        try:
            frame = av.VideoFrame.from_ndarray(np.zeros((96, 96, 3), np.uint8), format="rgb24")
            with patch.object(compositor, "_prepare", wraps=compositor._prepare) as prepare:
                compositor.apply(frame, .1)
                compositor.apply(frame, .2)
                compositor.apply(frame, .8)
                self.assertEqual(prepare.call_count, 2)
        finally:
            compositor.close()

    def test_smaller_four_by_three_video_preserves_lower_sixteen_by_nine_pixels(self):
        def solid_source(path, width, height, channel):
            with av.open(str(path), "w") as output:
                stream = output.add_stream("libx264", rate=12)
                stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
                stream.codec_context.thread_count = 1
                stream.options = {"crf": "12", "preset": "ultrafast", "threads": "1"}
                for index in range(6):
                    pixels = np.zeros((height, width, 3), np.uint8)
                    pixels[..., channel] = 255
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                    frame.pts, frame.time_base = index, Fraction(1, 12)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)

        lower, upper = self.root / "wide.mp4", self.root / "four-three.mp4"
        solid_source(lower, 160, 90, 0)
        solid_source(upper, 120, 90, 2)
        for rotation in (0, 45):
            with self.subTest(rotation=rotation):
                layer = video_layer(media_id="upper", width=.4, rotation=rotation, end=.5, out=.5, volume=0)
                project = {"width": 320, "height": 180, "fps": 24,
                           "clips": [clip(media_id="lower", out=.5, volume=0)], "overlays": [layer]}
                path = self.root / f"native-layer-{rotation}.mp4"
                editor.render_project(project, {"lower": lower, "upper": upper}, path,
                                      threading.Event(), lambda _: None)
                with av.open(str(path)) as source:
                    pixels = next(source.decode(video=0)).to_ndarray(format="rgb24")
                self.assertGreater(pixels[90, 160, 2], 230)
                self.assertLess(pixels[90, 160, 0], 20)
                # A smaller upper layer only replaces its native rectangle.
                # Every outside location must retain the lower red video,
                # including corners inside a rotated layer's bounding box.
                for x, y in ((20, 90), (300, 90), (160, 3), (160, 177), (86, 16), (234, 164)):
                    self.assertGreater(pixels[y, x, 0], 230, (rotation, x, y))
                    self.assertLess(pixels[y, x, 2], 20, (rotation, x, y))

    def test_video_original_sound_mixes_with_music_without_double_limiting(self):
        music = self.root / "music.wav"
        make_audio(music, frequency=660, amplitude=.15, duration=1)
        self.paths["music"] = music
        audio = {"id": "music", "media_id": "music", "in": 0, "out": 1, "speed": 1,
                 "start": 0, "track": 0, "volume": 1, "fade_in": 0, "fade_out": 0}
        target, _, _ = self.export([], [video_layer(volume=.5)], [audio])
        rate, values = decoded_audio(target)
        samples = values[0, round(.2 * rate):round(.8 * rate)]
        magnitudes = np.abs(np.fft.rfft(samples))
        frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
        peak = lambda hz: magnitudes[np.argmin(np.abs(frequencies - hz))]
        self.assertGreater(peak(440), peak(500) * 30)
        self.assertGreater(peak(660), peak(500) * 30)
        self.assertGreater(peak(660), peak(440) * 1.25)

    def test_reader_limit_and_cancel_close_every_active_video(self):
        active, maximum = set(), [0]
        active_audio, maximum_audio = set(), [0]
        original = editor.VideoReader
        class TrackedReader(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                active.add(id(self))
                maximum[0] = max(maximum[0], len(active))
            def close(self):
                active.discard(id(self))
                super().close()
        class TrackedAudioReader(editor.AudioReader):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                active_audio.add(id(self))
                maximum_audio[0] = max(maximum_audio[0], len(active_audio))
            def close(self):
                active_audio.discard(id(self))
                super().close()
        event = threading.Event()
        project = {"width": 96, "height": 96, "fps": 24, "clips": [clip(out=2, volume=.1)],
                   "overlays": [video_layer(id=f"v{i}", end=2, out=2, volume=.1, width=.5, x=.3 + .2 * i) for i in range(3)]}
        target = self.root / "cancelled.mp4"
        with patch.object(editor, "VideoReader", TrackedReader), patch.object(overlays, "VideoReader", TrackedReader), patch.object(editor, "AudioReader", TrackedAudioReader):
            with self.assertRaises(editor.ExportCancelled):
                editor.render_project(project, self.paths, target, event, lambda value: event.set() if value > 20 else None)
        self.assertEqual(maximum[0], 4)
        self.assertEqual(maximum_audio[0], 4)
        self.assertEqual(active, set())
        self.assertEqual(active_audio, set())
        self.assertFalse(target.exists())

    def test_video_reader_closes_when_layer_leaves_before_output_end(self):
        layer = video_layer(end=.5, out=.5, volume=0)
        compositor = overlays.OverlayCompositor([layer], self.paths, 96, 96)
        try:
            frame = av.VideoFrame.from_ndarray(np.zeros((96, 96, 3), np.uint8), format="rgb24")
            compositor.apply(frame, .25)
            self.assertEqual(len(compositor.video_readers), 1)
            with patch.object(next(iter(compositor.video_readers.values())), "close", wraps=next(iter(compositor.video_readers.values())).close) as close:
                self.assertIs(compositor.apply(frame, .5), frame)
                self.assertEqual(close.call_count, 1)
            self.assertEqual(compositor.video_readers, {})
        finally:
            compositor.close()


class VideoLayerSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-video-schema-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
        self.media_id = "a" * 32
        self.store.media[self.media_id] = {"id": self.media_id, "kind": "video", "duration": 2, "has_audio": True}
        (self.store.media_dir / f"{self.media_id}.source").write_bytes(b"fixture")

    def layer(self, **values):
        return video_layer(media_id=self.media_id, **values)

    def test_ranges_fades_count_and_canonical_end(self):
        layer = self.layer(start=.25, end=.7500005, **{"in": .5, "out": 1.5, "speed": 2, "fade_in": .2, "fade_out": .3})
        project = self.store.create_project({"overlays": [layer]})
        self.assertEqual(project["overlays"][0]["end"], .75)
        for values in ({"out": 2.1, "end": 2.1}, {"in": -1}, {"speed": True}, {"speed": 4.1}, {"volume": 2.1},
                       {"end": 1.01}, {"fade_in": -.1}, {"fade_out": True}, {"fade_in": .6, "fade_out": .5}):
            with self.subTest(values=values), self.assertRaises(editor.EditorError):
                self.store.create_project({"overlays": [self.layer(**values)]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [self.layer(id=f"v{i}") for i in range(4)]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [self.layer(**{
                "in": 2 - .25 / 24 + .0000005, "out": 2.0000005, "speed": .25, "end": 1 / 24})]})
        project = self.store.create_project({"overlays": [self.layer(id=f"v{i}") for i in range(3)] + [self.layer(id="next", start=1, end=2)]})
        self.assertEqual(len(project["overlays"]), 4)
        self.store.media[self.media_id]["kind"] = "image"
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [self.layer()]})

    def test_main_clip_fades_and_legacy_defaults(self):
        project = self.store.create_project({"clips": [clip(media_id=self.media_id, speed=2, fade_in=.25, fade_out=.25)]})
        self.assertEqual(project["clips"][0]["fade_in"], .25)
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"clips": [clip(media_id=self.media_id, speed=2, fade_in=.3, fade_out=.3)]})
        stored = project["clips"][0]
        stored.pop("fade_in")
        stored.pop("fade_out")
        editor.atomic_json(self.store.project_dir / f"{project['id']}.json", project)
        restored = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(restored.projects[project["id"]]["clips"][0]["fade_in"], 0)


class VideoLayerExportAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_overlay_only_export_job_snapshot_and_media_paths(self):
        with tempfile.TemporaryDirectory(prefix="video-layer-job-") as directory:
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            media_id = "a" * 32
            store.media[media_id] = {"id": media_id, "kind": "video", "duration": 2}
            (store.media_dir / f"{media_id}.source").write_bytes(b"fixture")
            project = store.create_project({"overlays": [video_layer(media_id=media_id, fade_in=.2)]})
            def render(snapshot, paths, output, event, progress):
                self.assertEqual(snapshot["clips"], [])
                self.assertEqual(snapshot["overlays"][0]["fade_in"], .2)
                self.assertEqual(set(paths), {media_id})
                output.write_bytes(b"rendered")
            with patch.object(editor, "render_project", side_effect=render):
                job = store.start_export(project["id"])
                await store.export_task
            self.assertEqual(store.exports[job["id"]]["status"], "completed")
