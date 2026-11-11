"""Model-specific quality survives saved scripts and short-film compilation."""
import copy
import tempfile
import unittest
from pathlib import Path

from domain import compile_request
from script_video_format import DEFAULT_VIDEO_SETTINGS, ScriptVideoFormatError, normalize_video_settings
from shortfilm import (ShortFilmError, ShortFilmStore, compile_shot_payload, new_project,
                       new_scene, new_shot, normalize_project)


class FreeVideoPreferencesTests(unittest.TestCase):
    def test_legacy_script_shapes_do_not_gain_a_new_model_or_quality(self):
        self.assertEqual(normalize_video_settings(), DEFAULT_VIDEO_SETTINGS)
        self.assertNotIn("freevideo_quality", normalize_video_settings({"video_model": "h3"}))

    def test_script_quality_is_independent_and_strict(self):
        prior = {**DEFAULT_VIDEO_SETTINGS, "video_model": "freevideo_vdn_h3", "freevideo_quality": "high"}
        for quality in ("light", "medium", "high", "max"):
            result = normalize_video_settings({"freevideo_quality": quality}, previous=prior)
            self.assertEqual(result["freevideo_quality"], quality)
            self.assertEqual(result["video_model"], "freevideo_vdn_h3")
        self.assertEqual(prior["freevideo_quality"], "high")
        self.assertEqual(normalize_video_settings({"video_model": "freevideo_vdn_h3"})["freevideo_quality"], "medium")
        for invalid in (True, [], "turbo", 8):
            with self.subTest(invalid=invalid), self.assertRaises(ScriptVideoFormatError):
                normalize_video_settings({"video_model": "freevideo_vdn_h3", "freevideo_quality": invalid})

    def test_shortfilm_reload_and_shot_payload_keep_freevideo_and_h3_preferences_separate(self):
        for quality, steps in (("light", 8), ("medium", 8), ("high", 16), ("max", 20)):
            with self.subTest(quality=quality), tempfile.TemporaryDirectory() as directory:
                project = new_project("Model comparison")
                scene, shot = new_scene(), new_shot()
                shot["action"] = "A lantern sways gently while the camera remains still."
                scene["shots"] = [shot]
                project.update(video_model="freevideo_vdn_h3", freevideo_quality=quality,
                               quality_mode="turbo_fast_v13", video_vae="int8", memory_optimization=True,
                               custom_loras=[{"name": "kept.safetensors", "strength": .7}], scenes=[scene])
                saved = ShortFilmStore(Path(directory)).create(project)
                restored = ShortFilmStore(Path(directory)).get(saved["id"])
                self.assertEqual(restored["freevideo_quality"], quality)
                self.assertEqual(restored["video_vae"], "int8")
                self.assertTrue(restored["memory_optimization"])
                self.assertEqual(restored["quality_mode"], "turbo_fast_v13")
                before = copy.deepcopy(restored)
                payload, _ = compile_shot_payload(restored, scene["id"], shot["id"])
                self.assertEqual(payload["video_model"], "freevideo_vdn_h3")
                self.assertEqual(payload["freevideo_quality"], quality)
                self.assertEqual(payload["video_vae"], "auto")
                self.assertFalse(payload["memory_optimization"])
                self.assertEqual(payload["custom_loras"], [])
                compiled = compile_request(payload)
                self.assertEqual(compiled.freevideo_quality, quality)
                self.assertEqual(compiled.steps, steps)
                self.assertEqual(restored, before)

    def test_shortfilm_invalid_quality_is_rejected_without_replacing_the_project(self):
        project = new_project()
        project.update(video_model="freevideo_vdn_h3", freevideo_quality="unsupported")
        before = copy.deepcopy(project)
        with self.assertRaises(ShortFilmError):
            normalize_project(project)
        self.assertEqual(project, before)


if __name__ == "__main__":
    unittest.main()
