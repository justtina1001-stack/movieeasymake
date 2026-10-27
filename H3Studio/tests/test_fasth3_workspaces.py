"""A selected video model survives project saves and frozen render plans."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

from domain import compile_request
from script_video_format import DEFAULT_VIDEO_SETTINGS, ScriptVideoFormatError, normalize_video_settings
from script_video_renderer import ScriptVideoRenderer
from script_video import ScriptVideoManager, ScriptVideoError
from scriptwriting import ScriptwritingError
from shortfilm import ShortFilmStore, compile_shot_payload, new_asset, new_project, new_scene, new_shot, normalize_project
from video_models import VIDEO_MODEL_FAST, VIDEO_MODEL_H3


def project():
    value, scene, shot, asset = new_project("模型切換"), new_scene(), new_shot(), new_asset("object", "Robot")
    asset.update(image_asset_ids=["a" * 32], description="One red toy robot.")
    shot.update(duration=5, action="Robot raises one arm and lowers it.", asset_ids=[asset["id"]])
    scene["shots"] = [shot]
    value.update(assets=[asset], scenes=[scene], video_model=VIDEO_MODEL_FAST,
                 quality_mode="turbo_ref_quality", memory_optimization=True,
                 custom_loras=[dict(name="personal.safetensors", family="ref2va", strength=0.6, enabled=True)])
    return value


class ModelProjectTests(unittest.TestCase):
    def test_script_formats_preserve_legacy_shape_and_validate_explicit_models(self):
        self.assertEqual(normalize_video_settings(), DEFAULT_VIDEO_SETTINGS)
        self.assertNotIn("video_model", normalize_video_settings())
        selected = normalize_video_settings({"video_model": VIDEO_MODEL_FAST})
        self.assertEqual(normalize_video_settings({"megapixels": 0.7}, previous=selected)["video_model"], VIDEO_MODEL_FAST)
        with self.assertRaises(ScriptVideoFormatError):
            normalize_video_settings({"video_model": "unrecognized"})

    def test_shortfilm_save_projects_fast_recipe_without_erasing_h3_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ShortFilmStore(Path(directory))
            saved = store.create(project())
            before = copy.deepcopy(saved)
            scene, shot = saved["scenes"][0], saved["scenes"][0]["shots"][0]
            raw, _ = compile_shot_payload(saved, scene["id"], shot["id"])
            compiled = compile_request(raw)
            self.assertEqual((compiled.video_model, compiled.steps), (VIDEO_MODEL_FAST, 8))
            self.assertEqual((raw["quality_mode"], raw["memory_optimization"], raw["custom_loras"]), ("native", False, []))
            self.assertEqual(saved, before)
            loaded = ShortFilmStore(Path(directory)).get(saved["id"])
            self.assertEqual(loaded["video_model"], VIDEO_MODEL_FAST)
            loaded["quality_mode"] = "turbo_fast"
            _, warnings = compile_shot_payload(loaded, scene["id"], shot["id"])
            self.assertFalse(any("Turbo" in warning for warning in warnings))
            loaded["quality_mode"] = before["quality_mode"]
            loaded["video_model"] = VIDEO_MODEL_H3
            raw, _ = compile_shot_payload(loaded, scene["id"], shot["id"])
            self.assertEqual(raw["quality_mode"], "turbo_ref_quality")
            self.assertTrue(raw["memory_optimization"])
            self.assertEqual(raw["custom_loras"], before["custom_loras"])

    def test_legacy_project_has_no_new_generation_key_and_model_changes_break_frozen_plan(self):
        legacy = normalize_project(new_project("legacy"))
        self.assertNotIn("video_model", legacy)
        selected = project()
        settings = {**DEFAULT_VIDEO_SETTINGS, "video_model": VIDEO_MODEL_FAST}
        ScriptVideoRenderer._validate_frozen_format(selected, {"video_settings": settings})
        with self.assertRaises(ScriptwritingError):
            ScriptVideoRenderer._validate_frozen_format(selected, {})
        selected["video_model"] = VIDEO_MODEL_H3
        with self.assertRaises(ScriptwritingError) as failure:
            ScriptVideoRenderer._validate_frozen_format(selected, {"video_settings": settings})
        self.assertEqual(failure.exception.status, 409)

    def test_fast_script_snapshot_keeps_inactive_h3_adapters_without_requiring_their_family(self):
        manager = object.__new__(ScriptVideoManager)
        manager.scripts = SimpleNamespace(_validate_reference_assets=lambda refs: None)
        snapshot = {"duration": 5, "video_settings": {"video_model": VIDEO_MODEL_FAST},
                    "character_references": [{"name": "Robot", "type": "object", "image_asset_id": "a" * 32,
                                               "appearance": "A red robot."}],
                    "custom_loras": [{"name": "personal.safetensors", "family": "fl2va", "enabled": True, "strength": 0.5}]}
        manager._check_snapshot(snapshot)
        self.assertEqual(snapshot["custom_loras"][0]["family"], "fl2va")
        snapshot["video_settings"]["video_model"] = VIDEO_MODEL_H3
        with self.assertRaises(ScriptVideoError):
            manager._check_snapshot(snapshot)


class ModelReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_status_can_use_fast_without_original_h3_checkpoint(self):
        inventory = dict(text_encoder=True, audio_vae=True, video_vae=True, video_vae_fp16=True,
                         fasth3_omni_nvfp4=True, fasth3_blackwell=True, fasth3_core_supported=True,
                         fasth3_nodes_ready=True, h3_sigma_shift=True)
        app = web.Application()
        app["comfy"] = SimpleNamespace(mode="local", base_url="http://127.0.0.1:8188",
                                       is_ready=AsyncMock(return_value=True), model_inventory=AsyncMock(return_value=inventory))
        app["editor"] = SimpleNamespace()
        renderer = ScriptVideoRenderer(app)
        status = await renderer.readiness()
        self.assertTrue(status["ready"])
        self.assertTrue(status["video_model_supported"])
        self.assertTrue((await renderer.readiness(VIDEO_MODEL_FAST))["ready"])
        self.assertFalse((await renderer.readiness(VIDEO_MODEL_H3))["ready"])


if __name__ == "__main__":
    unittest.main()
