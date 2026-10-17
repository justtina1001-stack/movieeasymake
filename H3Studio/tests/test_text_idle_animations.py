"""Text idle motion is strict, persisted, CPU-rendered and shared with preview."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from editor_animations import animation_at, idle_at, validate_animations
from editor_overlays import OverlayCompositor, validate_layer
from test_editor_overlays import frame, image_layer, text_layer
from video_editor import EditorError, EditorStore


IDENTITY = {"x": 0., "y": 0., "scale": 1., "opacity": 1.}


class TextIdleAnimationTests(unittest.TestCase):
    def test_defaults_disabled_and_canonical_settings_preserve_legacy_projects(self):
        self.assertEqual(validate_animations(text_layer(), 1), {})
        for spec in ({}, {"preset": "none"}, {"preset": "none", "speed": 4, "amount": 2},
                     {"preset": "pulse", "amount": 0}):
            self.assertEqual(validate_animations(text_layer(animation_idle=spec), 1), {})
        self.assertEqual(validate_animations(text_layer(animation_idle={"preset": "pulse"}), 1),
                         {"animation_idle": {"preset": "pulse", "speed": 1., "amount": 1.}})
        with tempfile.TemporaryDirectory(prefix="editor-text-idle-test-") as temporary:
            directory = Path(temporary)
            store = EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            spec = {"preset": "float", "speed": 1.5, "amount": .7}
            project = store.create_project({"overlays": [text_layer(animation_idle=spec)]})
            restored = EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(restored.projects[project["id"]], project)
            self.assertEqual(project["overlays"][0]["animation_idle"], spec)

    def test_schema_rejects_unknown_fields_types_nonfinite_values_and_non_text_layers(self):
        invalid = (None, [], "pulse", True, {"preset": "spin"}, {"preset": None},
                   {"preset": "pulse", "speed": True}, {"preset": "pulse", "speed": "1"},
                   {"preset": "pulse", "speed": None}, {"preset": "pulse", "speed": float("nan")},
                   {"preset": "pulse", "speed": float("inf")}, {"preset": "pulse", "speed": .249},
                   {"preset": "pulse", "speed": 4.001}, {"preset": "pulse", "amount": True},
                   {"preset": "pulse", "amount": "1"}, {"preset": "pulse", "amount": None},
                   {"preset": "pulse", "amount": float("nan")}, {"preset": "pulse", "amount": -.001},
                   {"preset": "pulse", "amount": 2.001}, {"preset": "pulse", "url": "file://font"})
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(EditorError):
                validate_layer(text_layer(animation_idle=spec))
        for kind in ("image", "video", "audio", None):
            for preset in ("none", "pulse"):
                with self.subTest(kind=kind, preset=preset), self.assertRaisesRegex(EditorError, "文字圖層"):
                    validate_animations({"kind": kind, "animation_idle": {"preset": preset}}, 1)
        with self.assertRaises(EditorError):
            validate_layer(image_layer(animation_idle={"preset": "pulse"}))

    def test_idle_is_confined_to_the_middle_and_eases_to_unmodified_boundary_placement(self):
        for preset in ("pulse", "float", "shake", "blink"):
            layer = text_layer(end=2, animation_in={"type": "fade", "duration": .5},
                               animation_out={"type": "slide_left", "duration": .5},
                               animation_idle={"preset": preset, "speed": 1, "amount": 1})
            plain = {key: value for key, value in layer.items() if key != "animation_idle"}
            for seconds in (0, .25, .5, 1.5, 1.75, 2):
                self.assertEqual(animation_at(layer, seconds, 2), animation_at(plain, seconds, 2))
            self.assertNotEqual(idle_at(layer, .8, 2), IDENTITY)
            for seconds in (.5 + 1e-8, 1.5 - 1e-8):
                for key, value in idle_at(layer, seconds, 2).items():
                    self.assertAlmostEqual(value, IDENTITY[key], places=12)
            for end in (1, 1.0000005):
                self.assertEqual(idle_at(layer, .5, end), IDENTITY)

    def test_presets_use_canvas_relative_motion_scale_opacity_and_bounded_strength(self):
        expected = {"pulse": {**IDENTITY, "scale": 1.04},
                    "float": {**IDENTITY, "y": -.025},
                    "shake": {**IDENTITY, "x": .0125},
                    "blink": {**IDENTITY, "opacity": .6}}
        times = {"pulse": .25, "float": .25, "shake": .3125, "blink": .25}
        for preset, value in expected.items():
            actual = idle_at(text_layer(end=2, animation_idle={"preset": preset}), times[preset], 2)
            for key in IDENTITY:
                self.assertAlmostEqual(actual[key], value[key])
        for amount in (0, .5, 1, 2):
            for preset in expected:
                for seconds in np.linspace(0, 2, 31):
                    actual = idle_at(text_layer(end=2, animation_idle={"preset": preset, "speed": 4, "amount": amount}), seconds, 2)
                    self.assertTrue(0 <= actual["opacity"] <= 1)
                    self.assertTrue(1 <= actual["scale"] <= 1.16)
                    self.assertLessEqual(abs(actual["x"]), .025)
                    self.assertLessEqual(abs(actual["y"]), .05)
                    if amount == 0:
                        self.assertEqual(actual, IDENTITY)

    def test_cpu_text_compositor_changes_pixels_but_reuses_exactly_one_original_raster(self):
        for preset in ("pulse", "float", "shake", "blink"):
            layer = validate_layer(text_layer(end=2, text="IDLE", width=.5, font_size=.12,
                                              background="#FFFFFF", animation_idle={"preset": preset}))
            compositor = OverlayCompositor([layer], {}, 200, 200)
            try:
                original = compositor.apply(frame(200, 200), 0).to_ndarray(format="rgb24")
                changed = compositor.apply(frame(200, 200), .3).to_ndarray(format="rgb24")
                self.assertFalse(np.array_equal(original, changed), preset)
                for seconds in np.linspace(.02, 1.98, 25):
                    compositor.apply(frame(200, 200), float(seconds))
                self.assertEqual(len(compositor.cache), 1, preset)
                boundary = compositor.apply(frame(200, 200), 2 - 1e-8).to_ndarray(format="rgb24")
                np.testing.assert_array_equal(original, boundary)
            finally:
                compositor.close()

    @unittest.skipUnless(shutil.which("node"), "Node required to compare the browser evaluator")
    def test_browser_preview_matches_cpu_export_for_every_preset_and_short_gap(self):
        fixtures = []
        for preset in ("pulse", "float", "shake", "blink"):
            for length in (.03, 1, 2.07):
                for speed in (.25, 1, 4):
                    for amount in (0, .7, 2):
                        edge = length * .35
                        layer = text_layer(end=length, animation_idle={"preset": preset, "speed": speed, "amount": amount},
                                           animation_in={"type": "slide_up", "duration": edge},
                                           animation_out={"type": "zoom_out", "duration": edge})
                        for fraction in (0, .25, .35, .42, .5, .58, .65, .8, .999999):
                            fixtures.append([layer, length * fraction, length])
        source = Path(__file__).resolve().parents[1] / "static" / "editor_animations.js"
        code = "const core=require(process.argv[1]);let raw='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>raw+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(raw).map(x=>core.animationTransformAt(...x)))));"
        completed = subprocess.run([shutil.which("node"), "-e", code, str(source)], input=json.dumps(fixtures),
                                   text=True, capture_output=True, check=True)
        outputs = json.loads(completed.stdout)
        self.assertEqual(len(outputs), len(fixtures))
        for fixture, browser in zip(fixtures, outputs):
            exported = animation_at(*fixture)
            for key in IDENTITY:
                self.assertTrue(math.isclose(exported[key], browser[key], abs_tol=1e-12),
                                f"{fixture} {key}: export={exported[key]} preview={browser[key]}")


if __name__ == "__main__":
    unittest.main()
