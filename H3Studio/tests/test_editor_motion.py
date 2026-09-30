"""Motion validation, cache lifetime and real moving pixels in an MP4."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
from PIL import Image

import editor_motion as motion
import editor_overlays as overlays
import video_editor as editor
from test_editor_overlays import text_layer, image_layer, frame
from test_video_editor_render import make_source


def point(time, x, y=.5, easing="linear"):
    return {"time": time, "x": x, "y": y, "easing": easing}


class MotionTests(unittest.TestCase):
    def test_holds_endpoints_interpolates_both_axes_and_static_fallback(self):
        layer = image_layer(position_keyframes=[point(0, .1, .2), point(4, .9, .8)])
        self.assertEqual(motion.position_at(layer, -1), {"x": .1, "y": .2})
        self.assertEqual(motion.position_at(layer, 2), {"x": .5, "y": .5})
        self.assertEqual(motion.position_at(layer, 4), {"x": .9, "y": .8})
        self.assertEqual(motion.position_at(layer, 10), {"x": .9, "y": .8})
        self.assertEqual(motion.position_at(image_layer(), 2), {"x": .5, "y": .5})
        self.assertEqual(motion.position_at(image_layer(position_keyframes=[point(1, .3)]), 3), {"x": .3, "y": .5})

    def test_all_easing_curves_match_the_browser(self):
        for easing, expected in (("linear", .25), ("ease_in", .0625), ("ease_out", .4375), ("ease_in_out", .125)):
            layer = image_layer(position_keyframes=[point(0, 0, 1, easing), point(4, 1, 0)])
            with self.subTest(easing=easing):
                self.assertAlmostEqual(motion.position_at(layer, 1)["x"], expected)
                self.assertAlmostEqual(motion.position_at(layer, 1)["y"], 1 - expected)
        layer["position_keyframes"][0]["easing"] = "ease_in_out"
        self.assertAlmostEqual(motion.position_at(layer, 3)["x"], .875)

    def test_strict_schema_rejects_malformed_nonfinite_duplicate_and_unbounded_data(self):
        good = point(0, .5)
        invalid = [None, {}, [None], [False], [dict(good, time=float("nan"))], [dict(good, time=True)],
                   [dict(good, x=float("inf"))], [dict(good, y=-.1)], [dict(good, time=601)],
                   [dict(good, time=-601)], [dict(good, easing=None)], [dict(good, easing="bezier")],
                   [dict(good, other=1)], [good, good], [point(2, .1), point(1, .9)],
                   [point(i, .5) for i in range(101)]]
        for points in invalid:
            with self.subTest(points=points), self.assertRaises(editor.EditorError):
                overlays.validate_layer(image_layer(position_keyframes=points))

    def test_video_keyframes_are_rejected_and_empty_legacy_fields_are_omitted(self):
        with self.assertRaises(editor.EditorError):
            motion.validate_position_keyframes({"kind": "video", "position_keyframes": [point(0, .5)]})
        self.assertNotIn("position_keyframes", overlays.validate_layer(text_layer(position_keyframes=[])))
        self.assertNotIn("position_keyframes", overlays.validate_layer(text_layer()))
        self.assertEqual(overlays.validate_layer(text_layer(position_keyframes=[{"time": 0, "x": .2, "y": .3}]))["position_keyframes"][0]["easing"], "linear")

    def test_persisted_trimmed_points_survive_store_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            layer = text_layer(start=1, end=2, position_keyframes=[point(-1, .1, .2, "ease_in_out"), point(3, .9, .8)])
            project = store.create_project({"overlays": [layer]})
            restored = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(restored.projects[project["id"]]["overlays"][0]["position_keyframes"], layer["position_keyframes"])
            self.assertAlmostEqual(motion.position_at(restored.projects[project["id"]]["overlays"][0], 0)["x"], .2)

    def test_image_positions_fades_rotation_and_z_order_use_one_bounded_source_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "badge.png"
            with Image.new("RGBA", (10, 10), (255, 0, 0, 255)) as source:
                source.save(path)
            layer = image_layer(width=.1, rotation=15, fade_in=.25, position_keyframes=[point(0, .2), point(1, .8)])
            compositor = overlays.OverlayCompositor([layer], {layer["media_id"]: path}, 100, 100)
            try:
                with patch.object(compositor, "_source", wraps=compositor._source) as load:
                    positions = []
                    for time in (.125, .25, .5, .75):
                        pixels = compositor.apply(frame(), time).to_ndarray(format="rgb24")
                        ys, xs = np.where(pixels[..., 0] > 80)
                        positions.append(xs.mean())
                        self.assertAlmostEqual(xs.mean(), (0.2 + .6 * time) * 100 - .5, delta=1)
                    self.assertEqual(load.call_count, 1)
                    self.assertEqual(len(compositor.cache), 1)
                    self.assertEqual(compositor.used_bytes, 400)
                    self.assertEqual(positions, sorted(positions))
            finally:
                compositor.close()
            self.assertEqual(compositor.used_bytes, 0)

    def test_text_is_rasterized_once_during_continuous_movement(self):
        layer = text_layer(text="MOVE", width=.5, position_keyframes=[point(0, .25), point(1, .75)])
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360)
        try:
            with patch.object(overlays, "raster_text", wraps=overlays.raster_text) as raster:
                centers = []
                for time in (0, .25, .5, .75):
                    pixels = compositor.apply(frame(360, 360), time).to_ndarray(format="rgb24")
                    centers.append(np.where(pixels[..., 0] > 100)[1].mean())
                self.assertEqual(raster.call_count, 1)
                for first, second in zip(centers, centers[1:]):
                    self.assertAlmostEqual(second - first, 45, delta=1)
        finally:
            compositor.close()

    def test_uncached_sources_are_closed_and_cache_eviction_keeps_animation_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "badge.png"
            with Image.new("RGBA", (10, 10), (255, 0, 0, 255)) as source:
                source.save(path)
            layer = image_layer(width=.1, position_keyframes=[point(0, .2), point(1, .8)])
            for budget in (0, 400):
                compositor = overlays.OverlayCompositor([layer, {**layer, "id": "other", "y": .7}], {layer["media_id"]: path}, 100, 100, cache_bytes=budget)
                try:
                    for time in (0, .5, .75):
                        compositor.apply(frame(), time)
                        self.assertLessEqual(compositor.used_bytes, budget)
                finally:
                    compositor.close()

    def test_real_mp4_export_moves_a_marker_in_both_axes_with_matching_easing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, image, target = root / "blue.mp4", root / "badge.png", root / "motion.mp4"
            make_source(source, sound=False, blue=True)
            with Image.new("RGBA", (20, 20), (255, 0, 0, 255)) as raster:
                raster.save(image)
            layer = image_layer(start=0, end=1, width=.1, position_keyframes=[point(0, .2, .2, "ease_in"), point(1, .8, .8)])
            project = {"width": 360, "height": 360, "fps": 24, "clips": [{"id": "v", "media_id": "video", "in": 0, "out": 1, "speed": 1, "volume": 0}], "audio_clips": [], "overlays": [layer]}
            info = editor.render_project(project, {"video": source, layer["media_id"]: image}, target, threading.Event(), lambda _: None)
            self.assertAlmostEqual(info["duration"], 1)
            with av.open(target) as output:
                frames = list(output.decode(video=0))
            self.assertEqual(len(frames), 24)
            for index in (0, 6, 12, 18, 23):
                pixels = frames[index].to_ndarray(format="rgb24")
                ys, xs = np.where((pixels[..., 0] > 160) & (pixels[..., 2] < 100))
                expected = motion.position_at(layer, index / 24)
                self.assertAlmostEqual(xs.mean(), expected["x"] * 360 - .5, delta=2)
                self.assertAlmostEqual(ys.mean(), expected["y"] * 360 - .5, delta=2)


if __name__ == "__main__":
    unittest.main()
