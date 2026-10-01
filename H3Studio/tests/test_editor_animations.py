"""Synthetic CPU renders for clip motion, fixed-clock transitions and backups."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

import av
import numpy as np
from PIL import Image

from editor_animations import animate_main_frame, animation_at, transition_surface, validate_animations, validate_transition_setting
from editor_overlays import OverlayCompositor, validate_layer
from test_editor_overlays import frame, image_layer, text_layer
from test_video_editor_render import decoded_audio, make_source
import video_editor as editor


class AnimationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-animation-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.paths = {}
        for media_id, blue in (("a" * 32, False), ("b" * 32, True)):
            path = self.store.media_dir / f"{media_id}.source"
            temporary = path.with_suffix(".mp4")
            make_source(temporary, blue=blue)
            temporary.replace(path)
            self.store.media[media_id] = {"id": media_id, **editor.probe_media(path)}
            editor.atomic_json(self.store.media_dir / f"{media_id}.json", self.store.media[media_id])
            self.paths[media_id] = path

    def clip(self, clip_id="left", media_id="a" * 32, **settings):
        return {"id": clip_id, "media_id": media_id, "in": 0, "out": .75, "volume": 1, "speed": 1, **settings}

    def transition(self, kind="crossfade", **settings):
        return {"type": kind, "duration": .5, "next_id": "right", **settings}

    def layer(self, clip_id="left", media_id="a" * 32, start=0, **settings):
        return {**self.clip(clip_id, media_id), "kind": "video", "start": start, "end": start + .75,
                "track_id": "track", "x": .5, "y": .5, "width": .5, "rotation": 0, "opacity": 1, **settings}

    def test_presets_persist_for_main_text_image_and_video_without_changing_legacy(self):
        entry, exit = {"type": "slide_left", "duration": .25}, {"type": "zoom_out", "duration": .25}
        project = self.store.create_project({"clips": [self.clip(animation_in=entry, animation_out=exit)],
                                             "overlays": [text_layer(animation_in=entry, animation_out=exit),
                                                          self.layer("video", start=1, animation_in=entry)]})
        restored = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(restored.projects[project["id"]], project)
        self.assertEqual(project["clips"][0]["animation_in"], entry)
        self.assertEqual(project["overlays"][0]["animation_out"], exit)
        image = validate_layer(image_layer(animation_in=entry))
        self.assertEqual(image["animation_in"], entry)
        legacy = self.store.create_project({"clips": [self.clip()]})
        self.assertNotIn("animation_in", legacy["clips"][0])
        self.assertEqual(validate_animations({"animation_in": {"type": "none", "duration": 0}}, 1), {})
        self.assertEqual(validate_animations({"animation_in": {"type": "fade", "duration": 0}}, 1), {})

    def test_animation_schema_rejects_malformed_unbounded_and_overlapping_durations(self):
        for value in (None, "fade", {"type": "spin", "duration": .2}, {"type": "fade", "duration": True},
                      {"type": "fade", "duration": float("nan")}, {"type": "fade", "duration": -.1},
                      {"type": "fade", "duration": 5.1}, {"type": "fade", "duration": .8},
                      {"type": "fade", "duration": .2, "url": "ignored"}):
            with self.subTest(value=value), self.assertRaises(editor.EditorError):
                self.store.create_project({"clips": [self.clip(animation_in=value)]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"clips": [self.clip(animation_in={"type": "fade", "duration": .5},
                                                           animation_out={"type": "fade", "duration": .5})]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"audio_clips": [self.clip(animation_in={"type": "fade", "duration": .2})]})

    def test_transition_schema_validates_bound_adjacent_main_and_overlay_videos(self):
        for kind in ("crossfade", "wipe_left", "wipe_right"):
            setting = self.transition(kind)
            project = self.store.create_project({"clips": [self.clip(transition_out=setting), self.clip("right", "b" * 32)]})
            self.assertEqual(project["clips"][0]["transition_out"], setting)
            layered = self.store.create_project({"overlays": [self.layer(transition_out=setting), self.layer("right", "b" * 32, .75)]})
            self.assertEqual(layered["overlays"][0]["transition_out"], setting)
        for setting in (self.transition(duration=1), self.transition(next_id="other"), self.transition(next_id=""),
                        self.transition(kind="spin"), self.transition(duration=True),
                        {"type": "crossfade", "duration": .2}):
            with self.subTest(setting=setting), self.assertRaises(editor.EditorError):
                self.store.create_project({"clips": [self.clip(transition_out=setting), self.clip("right", "b" * 32)]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"clips": [self.clip(transition_out=self.transition())]})
        for following in (self.layer("right", "b" * 32, 1),
                          self.layer("right", "b" * 32, .75, track_id="other"),
                          text_layer(id="right", track_id="track", start=.75, end=1.5)):
            with self.subTest(following=following), self.assertRaises(editor.EditorError):
                self.store.create_project({"overlays": [self.layer(transition_out=self.transition()), following]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [text_layer(transition_out=self.transition())]})
        self.assertEqual(validate_transition_setting({"transition_out": {"type": "none", "duration": 0}}), {})

    def test_shared_motion_vectors_smoothstep_and_existing_position_animation(self):
        for kind, axis, sign in (("slide_left", "x", 1), ("slide_right", "x", -1),
                                 ("slide_up", "y", 1), ("slide_down", "y", -1)):
            entry, exit = {"animation_in": {"type": kind, "duration": .5}}, {"animation_out": {"type": kind, "duration": .5}}
            self.assertEqual(animation_at(entry, 0, 1)[axis], sign)
            self.assertEqual(animation_at(entry, .25, 1)[axis], sign * .5)
            self.assertEqual(animation_at(exit, 1, 1)[axis], -sign)
        self.assertAlmostEqual(animation_at({"animation_in": {"type": "fade", "duration": .5}}, .125, 1)["opacity"], .15625)
        for kind, scale in (("zoom_in", .15), ("zoom_out", 1.8)):
            self.assertAlmostEqual(animation_at({"animation_in": {"type": kind, "duration": .5}}, 0, 1)["scale"], scale)
            self.assertAlmostEqual(animation_at({"animation_out": {"type": kind, "duration": .5}}, 1, 1)["scale"], scale)

    def render(self, project, name):
        target = self.root / name
        result = editor.render_project(project, self.paths, target, threading.Event(), lambda _: None)
        with av.open(str(target)) as source:
            pixels = [item.to_ndarray(format="rgb24") for item in source.decode(video=0)]
        return target, result, pixels

    def test_real_main_crossfade_preserves_video_clock_and_audio(self):
        base = {"width": 96, "height": 96, "fps": 24, "clips": [self.clip(), self.clip("right", "b" * 32)]}
        plain, plain_result, _ = self.render(base, "plain.mp4")
        project = self.store.create_project({**base, "width": 360, "height": 360,
                                             "clips": [self.clip(transition_out=self.transition()), self.clip("right", "b" * 32)]})
        project.update(width=96, height=96)
        target, result, pixels = self.render(project, "transition.mp4")
        self.assertEqual(result, plain_result)
        self.assertEqual(result["frame_count"], 36)
        self.assertEqual(result["audio_samples"], 72000)
        self.assertGreater(pixels[12][48, 48, 0], 230)
        self.assertTrue(110 < pixels[18][48, 48, 0] < 145)
        self.assertTrue(110 < pixels[18][48, 48, 2] < 145)
        self.assertGreater(pixels[24][48, 48, 2], 230)
        _, audio = decoded_audio(target)
        _, original = decoded_audio(plain)
        self.assertEqual(audio.shape, original.shape)
        self.assertLess(float(np.max(np.abs(audio - original))), 1e-6)

    def test_real_main_wipe_directions_and_animation_fade_do_not_change_source_audio(self):
        for kind, incoming_x, outgoing_x in (("wipe_left", 72, 24), ("wipe_right", 24, 72)):
            project = {"width": 96, "height": 96, "fps": 24,
                       "clips": [self.clip(transition_out=self.transition(kind)), self.clip("right", "b" * 32)]}
            _, result, pixels = self.render(project, kind + ".mp4")
            self.assertGreater(pixels[18][48, incoming_x, 2], 230)
            self.assertGreater(pixels[18][48, outgoing_x, 0], 230)
            self.assertEqual(result["frame_count"], 36)
        project = {"width": 96, "height": 96, "fps": 24,
                   "clips": [self.clip(animation_in={"type": "fade", "duration": .5})]}
        animated, _, pixels = self.render(project, "fade.mp4")
        self.assertLess(pixels[0].max(), 3)
        self.assertTrue(110 < pixels[6][48, 48, 0] < 145)
        self.assertGreater(pixels[12][48, 48, 0], 230)
        plain, _, _ = self.render({**project, "clips": [self.clip()]}, "fade-plain.mp4")
        self.assertLess(float(np.max(np.abs(decoded_audio(animated)[1] - decoded_audio(plain)[1]))), 1e-6)

    def test_overlay_transition_is_one_translucent_surface_over_lower_track(self):
        layers = [self.layer(transition_out=self.transition(), opacity=.5), self.layer("right", "b" * 32, .75, opacity=.5)]
        with Image.new("RGBA", (96, 96), (0, 255, 0, 255)) as lower:
            background = av.VideoFrame.from_ndarray(np.asarray(lower.convert("RGB")), format="rgb24")
        compositor = OverlayCompositor(layers, self.paths, 96, 96)
        self.addCleanup(compositor.close)
        pixels = compositor.apply(background, .75).to_ndarray(format="rgb24")
        self.assertTrue(55 < pixels[48, 48, 0] < 72)
        self.assertTrue(122 < pixels[48, 48, 1] < 134)
        self.assertTrue(55 < pixels[48, 48, 2] < 72)
        np.testing.assert_array_equal(pixels[0, 0], [0, 255, 0])
        self.assertEqual(set(compositor.video_readers), {"left", "right"})
        compositor.apply(background, 1.1)
        self.assertEqual(set(compositor.video_readers), {"right"})

    def test_overlay_transition_boundary_hold_uses_project_fps_for_existing_fade(self):
        layers = [self.layer(transition_out=self.transition(), fade_out=.5), self.layer("right", "b" * 32, .75)]
        compositor = OverlayCompositor(layers, self.paths, 96, 96, fps=60)
        self.addCleanup(compositor.close)
        pixels = compositor.apply(frame(96, 96), .75).to_ndarray(format="rgb24")
        self.assertTrue(2 <= pixels[48, 48, 0] <= 6)  # 255 × (1/60)/.5 × .5
        self.assertTrue(120 <= pixels[48, 48, 2] <= 135)

    def test_main_spatial_presets_clip_to_output_canvas_and_keep_centered_zoom(self):
        pixels = np.zeros((96, 96, 3), dtype=np.uint8)
        pixels[16:80, :, 0] = 255
        original = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        for kind, point, blank in (("slide_left", (48, 72), (48, 24)),
                                   ("slide_right", (48, 24), (48, 72)),
                                   ("slide_up", (72, 48), (24, 48)),
                                   ("slide_down", (24, 48), (72, 48))):
            setting = {"animation_in": {"type": kind, "duration": .5}}
            first = animate_main_frame(original, setting, 0, 1, 96, 96).to_ndarray(format="rgb24")
            self.assertEqual(first.max(), 0)
            halfway = animate_main_frame(original, setting, .25, 1, 96, 96).to_ndarray(format="rgb24")
            self.assertGreater(halfway[point][0], 250)
            self.assertEqual(halfway[blank][0], 0)
        for kind in ("zoom_in", "zoom_out"):
            setting = {"animation_in": {"type": kind, "duration": .5}}
            first = animate_main_frame(original, setting, 0, 1, 96, 96).to_ndarray(format="rgb24")
            self.assertGreater(first[48, 48, 0], 250)
            if kind == "zoom_in":
                self.assertEqual(first[48, 24, 0], 0)
            else:
                self.assertGreater(first[48, 24, 0], 250)

    def test_image_slide_combines_with_keyframes_and_keeps_one_cached_source(self):
        path = self.root / "image.png"
        with Image.new("RGBA", (20, 10), (255, 0, 0, 255)) as image:
            image.save(path)
        layer = validate_layer(image_layer(width=.2, animation_in={"type": "slide_left", "duration": .5},
                                          position_keyframes=[{"time": 0, "x": .2, "y": .5}, {"time": .5, "x": .4, "y": .5}]))
        compositor = OverlayCompositor([layer], {"a" * 32: path}, 100, 100)
        self.addCleanup(compositor.close)
        self.assertEqual(compositor.apply(frame(), 0).to_ndarray(format="rgb24").max(), 0)
        midway = compositor.apply(frame(), .25).to_ndarray(format="rgb24")
        self.assertGreater(midway[50, 80, 0], 250)
        self.assertLess(midway[50, 30, 0], 2)
        final = compositor.apply(frame(), .5).to_ndarray(format="rgb24")
        self.assertGreater(final[50, 40, 0], 250)
        self.assertEqual(len(compositor.cache), 1)

    def test_transition_surface_crossfade_premultiplied_alpha_and_wipe_replacement(self):
        with Image.new("RGBA", (8, 2), (255, 0, 0, 128)) as left, Image.new("RGBA", (8, 2), (0, 0, 255, 128)) as right:
            with transition_surface(left, right, "crossfade", .5) as blended:
                red, green, blue, alpha = blended.getpixel((4, 1))
                self.assertEqual(alpha, 128)
                self.assertTrue(123 <= red <= 130 and 123 <= blue <= 130)
                self.assertEqual(green, 0)
            with transition_surface(left, right, "wipe_left", .5) as wipe:
                self.assertEqual(wipe.getpixel((1, 1)), left.getpixel((1, 1)))
                self.assertEqual(wipe.getpixel((6, 1)), right.getpixel((6, 1)))


if __name__ == "__main__":
    unittest.main()
