import math
import re
import unittest

from domain import compile_request
from effects_spec import (effect_catalog, normalize_effect_spec, build_effect_generation_payload,
                          validate_effect_compiled)


class EffectSpecTests(unittest.TestCase):
    def test_all_presets_compile_to_visual_only_official_recipes(self):
        for preset in effect_catalog():
            for model in ("h3", "fasth3_omni_nvfp4", "pika_h3_flex"):
                with self.subTest(kind=preset["id"], model=model):
                    spec = normalize_effect_spec({"kind": preset["id"], "video_model": model})
                    source = build_effect_generation_payload(spec)
                    compiled = compile_request(source)
                    validate_effect_compiled(compiled)
                    self.assertEqual(compiled.video_model, model)
                    self.assertEqual(compiled.mode, "t2v")
                    self.assertIn("RGB(0,0,0)", compiled.prompt)
                    self.assertIn("static shot", compiled.prompt)
                    self.assertNotIn("<d>", compiled.prompt)
                    self.assertFalse(source["export_frames"])

    def test_image_is_an_exact_frame_anchor_and_all_receipt_values_are_stable(self):
        spec = normalize_effect_spec({"source_mode": "image", "source_asset_id": "a" * 32,
                                      "name": "紫色火焰", "color": "#aB12fF", "duration": 1.01})
        self.assertEqual(spec, normalize_effect_spec(spec))
        self.assertEqual(spec["color"], "#AB12FF")
        self.assertEqual(spec["duration"], spec["frame_count"] / spec["fps"])
        compiled = compile_request(build_effect_generation_payload(spec))
        self.assertEqual(compiled.first_image, "a" * 32)
        self.assertIsNone(compiled.last_image)
        validate_effect_compiled(compiled)

    def test_freevideo_quality_survives_the_frozen_effect_generation_recipe(self):
        for quality, steps in (("light", 8), ("medium", 8), ("high", 16), ("max", 20)):
            for source_mode in ("text", "image"):
                with self.subTest(quality=quality, source_mode=source_mode):
                    spec = normalize_effect_spec({"video_model": "freevideo_vdn_h3", "freevideo_quality": quality,
                                                  "source_mode": source_mode, "source_asset_id": "a" * 32,
                                                  "timing_mode": "retime", "source_duration": 5, "duration": 2})
                    raw = build_effect_generation_payload(spec)
                    compiled = compile_request(raw)
                    validate_effect_compiled(compiled)
                    self.assertEqual(compiled.video_model, "freevideo_vdn_h3")
                    self.assertEqual(compiled.freevideo_quality, quality)
                    self.assertEqual(compiled.steps, steps)
                    self.assertEqual(raw["freevideo_quality"], quality)
                    self.assertEqual(spec, normalize_effect_spec(spec))
                    self.assertEqual(compiled.first_image, "a" * 32 if source_mode == "image" else None)
                    self.assertIn("RGB(0,0,0)", compiled.prompt)
                    self.assertIn("overall_soundscape: N/A", compiled.prompt)
                    self.assertIn("non_diegetic_music: N/A", compiled.prompt)
                    self.assertFalse(compiled.custom_loras)
                    self.assertFalse(compiled.memory_optimization)
                    self.assertEqual(compiled.video_vae, "auto")
                    self.assertAlmostEqual(compiled.actual_duration, 5.167, places=3)
                    self.assertNotIn("retime_duration", raw)

    def test_freevideo_quality_defaults_and_invalid_active_quality_are_explicit(self):
        self.assertEqual(normalize_effect_spec({"video_model": "freevideo_vdn_h3"})["freevideo_quality"], "medium")
        for invalid in ([], {}, True, "ultra", "Medium"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "FreeVideo"):
                normalize_effect_spec({"video_model": "freevideo_vdn_h3", "freevideo_quality": invalid})
        imported = normalize_effect_spec({"source_mode": "video", "source_asset_id": "a" * 32,
                                          "video_model": ["unavailable model"], "freevideo_quality": {"unavailable": True}})
        self.assertEqual(imported["video_model"], "h3")
        self.assertEqual(imported["freevideo_quality"], "medium")
        with self.assertRaisesRegex(ValueError, "不需影片生成"):
            build_effect_generation_payload(imported)

    def test_invalid_and_unbounded_inputs_are_rejected(self):
        for changes in ({"duration": math.nan}, {"duration": math.inf}, {"fps": 25},
                        {"width": 513}, {"width": 1536, "height": 1536, "duration": 5},
                        {"seed": True}, {"source_mode": []}, {"kind": {}},
                        {"source_mode": "video", "source_asset_id": "../private"},
                        {"source_mode": "image"}, {"color": "red"},
                        {"duration": 6, "source_duration": 5},
                        {"trim_start": 3, "duration": 4, "source_duration": 5},
                        {"playback": "once", "loop_blend": .1},
                        {"video_model": "h3_ref_patch"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_effect_spec(changes)

    def test_import_does_not_request_gpu_and_product_locks_cannot_be_overridden(self):
        spec = normalize_effect_spec({"source_mode": "video", "source_asset_id": "b" * 32,
                                      "background": "white", "silent": False,
                                      "fixed_canvas": False, "alpha_mode": "premultiplied"})
        self.assertEqual(spec["background"], "black")
        self.assertTrue(spec["silent"])
        self.assertTrue(spec["fixed_canvas"])
        self.assertEqual(spec["alpha_mode"], "straight")
        with self.assertRaises(ValueError):
            build_effect_generation_payload(spec)

    def test_dark_smoke_and_embedded_speech_templates_require_correction(self):
        for changes in ({"kind": "smoke", "color": "#000000"},
                        {"prompt": '<d>[Chinese]念出這段</d>'},
                        {"prompt": 'integrated_multimodal_description: another template'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_effect_spec(changes)
        source = build_effect_generation_payload({"prompt": 'A woman says "Hello!".'})
        compiled = compile_request(source)
        with self.assertRaises(ValueError):
            validate_effect_compiled(compiled)

    def test_short_events_follow_export_interval_and_image_opening(self):
        source = build_effect_generation_payload({"kind": "explosion", "duration": 2, "trim_start": 1})
        self.assertIn("1.000 to 3.000 seconds", source["prompt"])
        self.assertIn("main burst near 1.490 seconds", source["prompt"])
        self.assertIn("faded to black by 2.958333 seconds, the last exported frame", source["prompt"])
        image = build_effect_generation_payload({"kind": "explosion", "source_mode": "image", "source_asset_id": "a" * 32})
        self.assertIn("effect state already visible", image["prompt"])
        self.assertNotIn("Begin from an almost empty black frame", image["prompt"])
        pulse = build_effect_generation_payload({"kind": "lightning", "playback": "pulse", "duration": 1})
        self.assertIn("repeating period of 1.000 seconds", pulse["prompt"])

    def test_once_finishes_at_the_last_exported_frame_with_valid_short_event_order(self):
        for fps in (12, 24, 30):
            for start in (0, 1.25):
                with self.subTest(fps=fps, trim_start=start):
                    spec = normalize_effect_spec({"kind": "lightning", "playback": "once",
                                                  "duration": .25, "fps": fps, "trim_start": start})
                    source = build_effect_generation_payload(spec)
                    prompt = source["prompt"]
                    peak = float(re.search(r"main burst near ([0-9.]+) seconds", prompt).group(1))
                    fade = float(re.search(r"dissipate from ([0-9.]+) seconds", prompt).group(1))
                    deadline = float(re.search(r"faded to black by ([0-9.]+) seconds", prompt).group(1))
                    expected = start + (spec["frame_count"] - 1) / fps
                    self.assertAlmostEqual(deadline, expected, places=6)
                    self.assertLess(start, peak)
                    self.assertLess(peak, fade)
                    self.assertLess(fade, deadline)
                    self.assertLess(deadline, start + spec["duration"])
                    self.assertIn("Remain black afterwards", prompt)

    def test_retime_is_explicit_and_legacy_specs_keep_original_speed(self):
        self.assertEqual(normalize_effect_spec({})["timing_mode"], "trim")
        for mode in (None, "crop", "", [], True):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                normalize_effect_spec({"timing_mode": mode})
        with self.assertRaisesRegex(ValueError, "起點必須為 0"):
            normalize_effect_spec({"timing_mode": "retime", "trim_start": .5})
        for duration in (2, 8):
            spec = normalize_effect_spec({"timing_mode": "retime", "duration": duration, "width": 256, "height": 256})
            self.assertEqual(spec["duration"], duration)
            self.assertEqual(spec, normalize_effect_spec(spec))
            compiled = compile_request(build_effect_generation_payload(spec))
            self.assertAlmostEqual(compiled.actual_duration, 5.167, places=3)
            self.assertEqual(spec["frame_count"], 24 * duration)

    def test_retimed_generation_uses_full_source_timeline_before_cpu_compression(self):
        spec = {"kind": "explosion", "timing_mode": "retime", "source_duration": 5, "duration": 2}
        raw = build_effect_generation_payload(spec)
        self.assertEqual(raw["duration"], 5)
        self.assertNotIn("retime_duration", raw)
        self.assertIn("complete source clip from 0.000 to 5.167 seconds", raw["prompt"])
        self.assertIn("faded to black by 5.125000 seconds, the last source frame", raw["prompt"])
        self.assertNotIn("retained event interval is 0.000 to 2.000", raw["prompt"])
        pulse = build_effect_generation_payload({**spec, "playback": "pulse"})
        self.assertIn("repeating period of 5.167 seconds", pulse["prompt"])
        image = build_effect_generation_payload({**spec, "source_mode": "image", "source_asset_id": "a" * 32})
        self.assertIn("effect state already visible", image["prompt"])


if __name__ == "__main__":
    unittest.main()
