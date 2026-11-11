"""FreeVideo's isolated recipe must retain reviewed media/prompt semantics."""
import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from comfy_client import ComfyClient
from domain import (CompiledRequest, RequestError, build_workflow, compile_request,
                    required_asset_ids, resolve_video_vae, validate_runtime_inventory)
from settings import ConnectionSettings
from video_models import (FREEVIDEO_QUALITY_RECIPES, VIDEO_MODEL_FREEVIDEO, VIDEO_MODEL_H3,
                          VIDEO_MODES, freevideo_node_support, normalize_freevideo_quality, video_model_profiles)


A, B, C, V, AUDIO = (letter * 32 for letter in "abcde")


def payload(mode="t2v", **updates):
    return {"video_model": VIDEO_MODEL_FREEVIDEO, "mode": mode, "prompt": "A red cup rests on a table.",
            "duration": 5, "megapixels": 0.4, "aspect_ratio": "16:9", "seed": 42, "quality_mode": "native", **updates}


def freevideo_schemas():
    def schema(required, output, optional=None):
        return {"input": {"required": {key: [value] for key, value in required.items()},
                          "optional": {key: [value] for key, value in (optional or {}).items()}}, "output": output}
    generate = schema(dict(text="STRING", width="INT", height="INT", seconds="FLOAT", seed="INT"), ["VIDEO", "STRING"],
                      dict(media="FREEVIDEO_MEDIA", frames="INT", two_pass="BOOLEAN", base_steps="INT",
                           refine_steps="INT", force_regenerate="BOOLEAN", preview="BOOLEAN"))
    generate["input"]["optional"].update(step_cache=[["none", "easycache-av", "fbcache"]], upscale_factor=[["1.2", "1.5", "2"]])
    return {"FreeVideoGenerate": generate, "FreeVideoMedia": schema(dict(assets="STRING"), ["FREEVIDEO_MEDIA"]),
            "SaveVideo": schema(dict(video="VIDEO", filename_prefix="STRING"), [])}


class FreeVideoCompileTests(unittest.TestCase):
    def graph(self, compiled):
        assets = {asset: f"uploads/{asset}.png" for asset in required_asset_ids(compiled)}
        assets.update({V: "uploads/motion.mp4", AUDIO: "uploads/voice.wav"})
        return build_workflow(compiled, assets, "測試")

    def test_old_requests_and_old_recipes_still_default_to_original_h3(self):
        old = payload()
        del old["video_model"]
        compiled = compile_request(old)
        self.assertEqual(compiled.video_model, VIDEO_MODEL_H3)
        self.assertEqual(compiled.sampler_name, "res_multistep")
        legacy = asdict(compiled)
        legacy.pop("freevideo_quality")
        self.assertEqual(CompiledRequest(**legacy).freevideo_quality, "medium")
        self.assertIn("UNETLoader", [row["class_type"] for row in build_workflow(compiled, {}, "legacy").values()])

    def test_all_quality_recipes_use_author_steps_and_no_native_sampler(self):
        for quality, recipe in FREEVIDEO_QUALITY_RECIPES.items():
            with self.subTest(quality=quality):
                compiled = compile_request(payload(freevideo_quality=quality, steps=40, scheduler="beta"))
                workflow = self.graph(compiled)
                self.assertEqual(compiled.steps, recipe["base_steps"])
                self.assertEqual(compiled.scheduler, "freevideo")
                self.assertEqual(set(row["class_type"] for row in workflow.values()), {"FreeVideoGenerate", "SaveVideo"})
                inputs = workflow["2"]["inputs"]
                self.assertEqual({key: inputs[key] for key in recipe}, recipe)
                self.assertEqual(inputs["text"], compiled.prompt)
                self.assertEqual(inputs["frames"], compiled.length)
                self.assertEqual(inputs["seconds"], compiled.actual_duration)
                self.assertEqual(inputs["frames"] % 17, 5)
                self.assertTrue(inputs["force_regenerate"])
                self.assertFalse(inputs["preview"])
                self.assertEqual(workflow["3"]["inputs"]["filename_prefix"], "H3Studio/測試")

    def test_quality_validation_does_not_accept_numbers_or_unknown_labels(self):
        self.assertEqual(normalize_freevideo_quality(None), "medium")
        for bad in (True, 8, [], {}, "ultra", "Medium"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_freevideo_quality(bad)
            with self.subTest(bad=bad), self.assertRaises(RequestError):
                compile_request(payload(freevideo_quality=bad))

    def test_multimodal_assets_keep_per_modality_order_and_video_sound_switches(self):
        compiled = compile_request(payload("r2v", prompt="Actor says:『你好。』", references=[
            {"alias": "Actor", "type": "character", "image_asset_ids": [A, B], "video_asset_id": V,
             "video_use_audio": True, "audio_asset_id": AUDIO}]))
        workflow = self.graph(compiled)
        rows = json.loads(workflow["1"]["inputs"]["assets"])
        self.assertEqual([row["file"] for row in rows], [f"uploads/{A}.png", f"uploads/{B}.png", "uploads/motion.mp4", "uploads/voice.wav"])
        self.assertEqual([row["role"] for row in rows], ["reference"] * 4)
        self.assertTrue(rows[2]["sound"])
        self.assertEqual(rows[3]["use"], "reference")
        self.assertIn("<Video 1>", compiled.prompt)
        self.assertIn("<Audio 2>", compiled.prompt)
        self.assertEqual(workflow["2"]["inputs"]["text"], compiled.prompt)
        self.assertIn("你好。", workflow["2"]["inputs"]["text"])
        self.assertTrue(any("實驗" in warning for warning in compiled.warnings))
        muted = replace(compiled, reference_video_use_audio=[False])
        self.assertIs(json.loads(self.graph(muted)["1"]["inputs"]["assets"])[2]["sound"], False)

    def test_supported_modes_preserve_reference_and_keyframe_roles(self):
        cases = {"t2v": {}, "fl2va": {"first_image_asset_id": A, "last_image_asset_id": B},
                 "extend": {"first_image_asset_id": A}, "symbol_loop": {"first_image_asset_id": A},
                 "r2v": {"references": [{"alias": "Actor", "type": "character", "image_asset_ids": [A]}]},
                 "replace": {"references": [{"alias": "Actor", "type": "character", "image_asset_ids": [A], "video_asset_id": V}]},
                 "popup_panel": {"references": [{"alias": "背景圖", "type": "background", "image_asset_ids": [A]},
                                                    {"alias": "面板", "type": "object", "image_asset_ids": [B]}]},
                 "mg_animation": {"references": [{"alias": "背景圖", "type": "background", "image_asset_ids": [A]},
                                                     {"alias": "轉輪帶", "type": "object", "image_asset_ids": [B]},
                                                     {"alias": "角色", "type": "character", "image_asset_ids": [C]}]}}
        self.assertEqual(set(cases), set(VIDEO_MODES))
        for mode, changes in cases.items():
            with self.subTest(mode=mode):
                compiled = compile_request(payload(mode, **changes))
                graph = self.graph(compiled)
                rows = json.loads(graph["1"]["inputs"]["assets"]) if "1" in graph else []
                if mode in {"fl2va", "symbol_loop"}:
                    self.assertEqual([row["role"] for row in rows], ["first", "last"])
                if mode == "symbol_loop":
                    self.assertEqual(rows[0]["file"], rows[1]["file"])
                if mode in {"r2v", "replace", "popup_panel", "mg_animation"}:
                    self.assertTrue(rows)
                    self.assertTrue(all(row["role"] == "reference" for row in rows))

    def test_audio_reuse_warns_about_reperformance_without_changing_reference_conditioning(self):
        source = payload("r2v", references=[{"alias": "Actor", "type": "character", "image_asset_ids": [A],
                                             "audio_asset_id": AUDIO, "voice_mode": "timbre"}])
        timbre = compile_request(source)
        self.assertFalse(any("沿用原聲內容" in warning for warning in timbre.warnings))
        source["references"][0]["voice_mode"] = "reuse"
        reused = compile_request(source)
        warnings = [warning for warning in reused.warnings if "沿用原聲內容" in warning]
        self.assertEqual(len(warnings), 1)
        self.assertIn("剪輯室加入原音軌", warnings[0])
        self.assertTrue(set(timbre.warnings).issubset(set(reused.warnings)))
        self.assertIn("partially_copy", reused.prompt)
        for compiled in (timbre, reused):
            graph = self.graph(compiled)
            rows = json.loads(graph["1"]["inputs"]["assets"])
            self.assertEqual(rows[-1]["file"], "uploads/voice.wav")
            self.assertEqual(rows[-1]["use"], "reference")
            self.assertEqual(graph["2"]["inputs"]["text"], compiled.prompt)

    def test_exact_storyboard_images_fail_instead_of_silent_weak_reference(self):
        with self.assertRaisesRegex(RequestError, "逐幀錨定"):
            compile_request(payload(storyboards=[{"description": "The cup turns.", "duration": 2,
                                                   "image_asset_id": A, "guide_mode": "exact"}]))
        textual = compile_request(payload(storyboards=[{"description": "The cup turns.", "duration": 2}]))
        self.assertIn("The cup turns.", self.graph(textual)["2"]["inputs"]["text"])

    def test_conflicting_native_settings_are_not_silently_dropped(self):
        for changes in ({"quality_mode": "turbo"}, {"quality_mode": "garbage"}, {"memory_optimization": True},
                        {"video_vae": "fp16"}, {"video_vae": "int8"},
                        {"custom_loras": [{"name": "other.safetensors", "strength": .5}]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RequestError, "FreeVideo"):
                compile_request(payload(**changes))
        with self.assertRaisesRegex(RequestError, "Seed"):
            compile_request(payload(seed=2**53))

    def test_tampered_review_or_missing_uploads_fail_before_submission(self):
        compiled = compile_request(payload())
        for fields in ({"steps": 40}, {"freevideo_quality": "high"}, {"sampler_name": "euler"},
                       {"length": 125}, {"actual_duration": 6}, {"video_vae": "fp16"}, {"face_repair": {"frames": 124}},
                       {"model_adapter": "foo"}):
            with self.subTest(fields=fields), self.assertRaises(RequestError):
                self.graph(replace(compiled, **fields))
        with self.assertRaisesRegex(RequestError, "素材"):
            build_workflow(compile_request(payload("fl2va", first_image_asset_id=A)), {}, "missing")

    def test_freevideo_inventory_is_independent_of_old_h3_files(self):
        compiled = compile_request(payload())
        ready = {"freevideo_nodes_ready": True, "freevideo_runtime_ready": True}
        validate_runtime_inventory(compiled, ready)
        self.assertIsNone(resolve_video_vae(compiled, ready))
        for key in ready:
            with self.subTest(key=key), self.assertRaisesRegex(RequestError, "FreeVideo"):
                validate_runtime_inventory(compiled, {**ready, key: False})
        profiles = {row["id"]: row for row in video_model_profiles(ready)}
        self.assertTrue(profiles[VIDEO_MODEL_FREEVIDEO]["ready"])
        self.assertFalse(profiles[VIDEO_MODEL_H3]["ready"])

    def test_nodes_require_exact_media_and_sampling_contract(self):
        schemas = freevideo_schemas()
        self.assertTrue(freevideo_node_support(schemas))
        altered = copy.deepcopy(schemas)
        altered["FreeVideoGenerate"]["input"]["optional"]["media"] = ["CONDITIONING"]
        self.assertFalse(freevideo_node_support(altered))
        del altered["SaveVideo"]
        self.assertFalse(freevideo_node_support(altered))


class FreeVideoInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schemas = freevideo_schemas()
        self.remote_state = {"ready": False}
        app = web.Application()
        async def object_info(request):
            name = request.match_info["node"]
            return web.json_response({name: self.schemas[name]} if name in self.schemas else {})
        async def stats(request):
            return web.json_response({"system": {"comfyui_version": "0.42.0"}, "devices": []})
        async def setup(request):
            return web.json_response(self.remote_state)
        app.router.add_get("/object_info/{node}", object_info)
        app.router.add_get("/system_stats", stats)
        app.router.add_get("/freevideo/setup", setup)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.temporary = tempfile.TemporaryDirectory()
        settings = ConnectionSettings(mode="remote", base_url=f"http://127.0.0.1:{port}")
        self.client = ComfyClient(settings, Path(self.temporary.name))

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temporary.cleanup()

    async def test_remote_node_presence_without_ready_worker_never_claims_ready(self):
        inventory = await self.client.model_inventory(refresh=True)
        self.assertTrue(inventory["freevideo_nodes_ready"])
        self.assertFalse(inventory["freevideo_runtime_ready"])
        self.remote_state = {"ready": "true", "token": "must-not-leak"}
        self.assertFalse((await self.client.model_inventory(refresh=True))["freevideo_runtime_ready"])
        self.remote_state = {"ready": True, "token": "must-not-leak", "root": "private"}
        inventory = await self.client.model_inventory(refresh=True)
        self.assertTrue(inventory["freevideo_runtime_ready"])
        self.assertNotIn("must-not-leak", json.dumps(inventory))
        self.assertNotIn("private", json.dumps(inventory))

    async def test_local_requires_verified_installer_receipt_instead_of_old_h3_inventory(self):
        self.client.mode = "local"
        with patch("freevideo_runtime.freevideo_runtime_status", return_value={"ready": False, "error": "missing pack"}):
            inventory = await self.client.model_inventory(refresh=True)
        self.assertFalse(inventory["freevideo_runtime_ready"])
        self.assertEqual(inventory["freevideo_runtime_error"], "missing pack")
        with patch("freevideo_runtime.freevideo_runtime_status", return_value={"ready": True}):
            self.assertTrue((await self.client.model_inventory(refresh=True))["freevideo_runtime_ready"])


if __name__ == "__main__":
    unittest.main()
