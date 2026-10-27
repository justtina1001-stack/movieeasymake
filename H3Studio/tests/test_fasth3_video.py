import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from comfy_client import ComfyClient
from domain import CompiledRequest, RequestError, build_workflow, compile_request, required_asset_ids, validate_runtime_inventory
from settings import ConnectionSettings
from video_models import (FASTH3_FILENAME, VIDEO_MODEL_FAST, VIDEO_MODEL_H3, VIDEO_MODES,
                          blackwell_device, normalize_video_model, video_model_profiles)


A, B, C, V, AUDIO = (character * 32 for character in "abcde")


def payload(mode="t2v"):
    return dict(video_model=VIDEO_MODEL_FAST, mode=mode, prompt="A red cup rests on a table.",
                duration=5, megapixels=0.4, aspect_ratio="16:9", seed=42, quality_mode="native")


def native_schemas():
    def schema(fields, output, optional=None):
        return {"input": {"required": {name: [kind] for name, kind in fields.items()},
                          "optional": {name: [kind] for name, kind in (optional or {}).items()}}, "output": output}
    common = dict(clip="CLIP", prompt="STRING", width="INT", height="INT", length="INT")
    result = {
        "UNETLoader": {"input": {"required": {"unet_name": [[FASTH3_FILENAME, "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"]]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": [["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"]]}}},
        "VAELoader": {"input": {"required": {"vae_name": [["minimax_h3_video_vae_fp16.safetensors", "minimax_h3_audio_vae_fp32.safetensors"]]}}},
        "MiniMaxH3ImageToVideo": schema({**common, "vae": "VAE"}, ["CONDITIONING", "LATENT"], dict(first_frame="IMAGE", last_frame="IMAGE")),
        "MiniMaxH3ReferenceToVideo": schema(common, ["CONDITIONING", "LATENT"], dict(vae="VAE", audio_vae="VAE")),
        "MiniMaxH3AddGuide": schema(dict(positive="CONDITIONING", latent="LATENT", frame_idx="INT"), ["CONDITIONING"], dict(vae="VAE", image="IMAGE")),
        "MiniMaxH3SigmaShift": schema(dict(model="MODEL", shift_video="FLOAT", shift_audio="FLOAT"), ["MODEL"]),
    }
    for name, prefix, maximum, kind in (("ref_images", "ref_image_", 9, "IMAGE"), ("ref_videos", "ref_video_", 3, "IMAGE"),
                                         ("ref_video_audios", "ref_video_audio_", 3, "AUDIO"), ("ref_audios", "ref_audio_", 3, "AUDIO")):
        result["MiniMaxH3ReferenceToVideo"]["input"]["optional"][name] = ["COMFY_AUTOGROW_V3", {
            "template": {"prefix": prefix, "max": maximum, "input": {"required": {name[:-1]: [kind]}}}}]
    return result


class FastH3CompileTests(unittest.TestCase):
    def test_legacy_recipe_default_and_explicit_original_model_are_unchanged(self):
        source = payload()
        del source["video_model"]
        original = compile_request(source)
        self.assertEqual(original.video_model, VIDEO_MODEL_H3)
        explicit = compile_request({**source, "video_model": VIDEO_MODEL_H3})
        self.assertEqual(asdict(original), asdict(explicit))
        old = asdict(original)
        del old["video_model"]
        self.assertEqual(CompiledRequest(**old).video_model, VIDEO_MODEL_H3)
        self.assertIn("minimax_h3_fl2va", next(n for n in build_workflow(original, {}, "old").values() if n["class_type"] == "UNETLoader")["inputs"]["unet_name"])

    def test_model_identity_is_explicit_and_invalid_values_reject(self):
        for value in ("fasth3", "unknown", True, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_video_model(value)
            with self.subTest(value=value), self.assertRaises(RequestError):
                compile_request({**payload(), "video_model": value})
        self.assertEqual(normalize_video_model(None), VIDEO_MODEL_H3)

    def test_fast_uses_distilled_recipe_without_h3_adapters(self):
        request = compile_request({**payload(), "steps": 40, "scheduler": "beta"})
        self.assertEqual((request.steps, request.scheduler, request.sampler_name), (8, "simple", "euler"))
        self.assertEqual((request.shift_video, request.shift_audio), (12.0, 3.0))
        self.assertIsNone(request.turbo_lora)
        workflow = build_workflow(request, {}, "fast")
        kinds = [node["class_type"] for node in workflow.values()]
        self.assertNotIn("LoraLoaderModelOnly", kinds)
        self.assertNotIn("BlockSparseAttention", kinds)
        self.assertNotIn("H3MemoryOptimization", kinds)
        self.assertEqual(next(n for n in workflow.values() if n["class_type"] == "UNETLoader")["inputs"]["unet_name"], FASTH3_FILENAME)
        shift = next(n for n in workflow.values() if n["class_type"] == "MiniMaxH3SigmaShift")["inputs"]
        self.assertEqual((shift["shift_video"], shift["shift_audio"]), (12.0, 3.0))
        self.assertIn("BasicGuider", kinds)
        self.assertNotIn("CFGGuider", kinds)

    def test_fast_rejects_h3_turbo_memory_and_active_custom_loras(self):
        for changes in ({"quality_mode": "turbo"}, {"quality_mode": "turbo_sla"}, {"quality_mode": "garbage"},
                        {"memory_optimization": True},
                        {"custom_loras": [{"name": "custom.safetensors", "strength": 0.5, "family": "both"}]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RequestError, "FastH3"):
                compile_request({**payload(), **changes})
        for enabled, strength in ((False, 0.5), (True, 0)):
            compile_request({**payload(), "custom_loras": [{"name": "custom.safetensors", "enabled": enabled, "strength": strength}]})

    def test_fast_builder_does_not_accept_tampered_sampling(self):
        compiled = compile_request(payload())
        for altered in (replace(compiled, steps=20), replace(compiled, scheduler="beta"), replace(compiled, quality_mode="turbo")):
            with self.subTest(altered=altered), self.assertRaisesRegex(RequestError, "FastH3"):
                build_workflow(altered, {}, "invalid")
        unknown = replace(compiled, video_model="future-model")
        with self.assertRaisesRegex(RequestError, "影片模型"):
            build_workflow(unknown, {}, "unknown")
        with self.assertRaisesRegex(RequestError, "影片模型"):
            validate_runtime_inventory(unknown, {})

    def test_all_existing_modes_keep_their_conditioning_and_media_inputs(self):
        cases = {
            "t2v": {},
            "fl2va": {"first_image_asset_id": A, "last_image_asset_id": B},
            "extend": {"first_image_asset_id": A},
            "symbol_loop": {"first_image_asset_id": A},
            "r2v": {"references": [{"alias": "Actor", "type": "character", "image_asset_ids": [A], "video_asset_id": V,
                                      "video_use_audio": True, "audio_asset_id": AUDIO}]},
            "replace": {"references": [{"alias": "Actor", "type": "character", "image_asset_ids": [A], "video_asset_id": V}]},
            "popup_panel": {"references": [{"alias": "背景圖", "type": "background", "image_asset_ids": [A]},
                                             {"alias": "面板", "type": "object", "image_asset_ids": [B]}]},
            "mg_animation": {"references": [{"alias": "背景圖", "type": "background", "image_asset_ids": [A]},
                                              {"alias": "轉輪帶", "type": "object", "image_asset_ids": [B]},
                                              {"alias": "角色", "type": "character", "image_asset_ids": [C]}]},
        }
        self.assertEqual(set(cases), set(VIDEO_MODES))
        for mode, changes in cases.items():
            with self.subTest(mode=mode):
                compiled = compile_request({**payload(mode), **changes})
                workflow = build_workflow(compiled, {asset: "uploads/" + asset for asset in required_asset_ids(compiled)}, mode)
                condition = next(node for node in workflow.values() if node["class_type"] in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"})
                expected = "MiniMaxH3ReferenceToVideo" if mode in {"r2v", "replace", "popup_panel", "mg_animation"} else "MiniMaxH3ImageToVideo"
                self.assertEqual(condition["class_type"], expected)
                self.assertEqual(compiled.video_model, VIDEO_MODEL_FAST)
                if mode == "fl2va":
                    self.assertIn("first_frame", condition["inputs"])
                    self.assertIn("last_frame", condition["inputs"])
                if mode == "r2v":
                    self.assertIn("ref_videos.ref_video_0", condition["inputs"])
                    self.assertIn("ref_video_audios.ref_video_audio_0", condition["inputs"])
                    self.assertIn("ref_audios.ref_audio_0", condition["inputs"])

    def test_fast_inventory_independence_does_not_require_original_diffusion_models(self):
        inventory = dict(text_encoder=True, audio_vae=True, video_vae=True, video_vae_fp16=True,
                         fasth3_omni_nvfp4=True, fasth3_blackwell=True, fasth3_core_supported=True, fasth3_nodes_ready=True)
        validate_runtime_inventory(compile_request(payload()), inventory)
        profiles = {p["id"]: p for p in video_model_profiles(inventory)}
        self.assertTrue(profiles[VIDEO_MODEL_FAST]["ready"])
        self.assertFalse(profiles[VIDEO_MODEL_H3]["ready"])
        for key in (VIDEO_MODEL_FAST, "fasth3_blackwell", "fasth3_core_supported", "fasth3_nodes_ready", "text_encoder"):
            with self.subTest(key=key), self.assertRaises(RequestError):
                validate_runtime_inventory(compile_request(payload()), {**inventory, key: False})
        inventory.update(fl2va=True, ref2va=True, fasth3_omni_nvfp4=False)
        validate_runtime_inventory(compile_request({**payload(), "video_model": VIDEO_MODEL_H3}), inventory)


class FastH3InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schemas = native_schemas()
        self.stats = {"system": {"comfyui_version": "0.42.0"}, "devices": [{"type": "cuda", "name": "cuda:0 NVIDIA GeForce RTX 5060 Ti : cudaMallocAsync"}]}
        self.headers = []
        app = web.Application()
        async def info(request):
            self.headers.append(request.headers.get("Authorization"))
            name = request.match_info["node"]
            return web.json_response({name: self.schemas[name]} if name in self.schemas else {})
        async def stats(request):
            self.headers.append(request.headers.get("Authorization"))
            return web.json_response(self.stats)
        app.router.add_get("/object_info/{node}", info)
        app.router.add_get("/system_stats", stats)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = ComfyClient(ConnectionSettings(mode="remote", base_url=f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}",
                                                     comfy_dir=str(self.root), auto_start_local=False, remote_access_token="test-fast-key"), self.root)

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_native_schemas_blackwell_and_new_core_are_required(self):
        inventory = await self.client.model_inventory()
        self.assertTrue(video_model_profiles(inventory)[1]["ready"])
        self.assertEqual(set(self.headers), {"Bearer test-fast-key"})
        for version in ("0.41.3", "unknown", None):
            self.stats["system"]["comfyui_version"] = version
            inventory = await self.client.model_inventory(refresh=True)
            self.assertFalse(inventory["fasth3_core_supported"])
            self.assertFalse(video_model_profiles(inventory)[1]["ready"])
            self.assertTrue(inventory["fl2va"])
        self.stats["system"]["comfyui_version"] = "0.42.0"
        self.schemas["MiniMaxH3ReferenceToVideo"]["output"] = ["IMAGE"]
        self.assertFalse((await self.client.model_inventory(refresh=True))["fasth3_nodes_ready"])

    async def test_older_or_unknown_hardware_does_not_advertise_nvfp4(self):
        for devices in ([], None, [{"name": "NVIDIA RTX 4090", "type": "cuda"}], [{"name": "NVIDIA RTX 5060 Ti", "type": "cpu"}]):
            self.stats["devices"] = devices
            self.assertFalse((await self.client.model_inventory(refresh=True))["fasth3_blackwell"])
        for name in ("NVIDIA GeForce RTX 5090", "NVIDIA GB200", "NVIDIA RTX PRO 6000 Blackwell"):
            self.assertTrue(blackwell_device({"devices": [{"type": "cuda", "name": name}]}))

    async def test_local_full_pdd_constructor_and_model_size_are_checked(self):
        self.client.mode = "local"
        model = self.root / "models/diffusion_models" / FASTH3_FILENAME
        model.parent.mkdir(parents=True)
        model.write_bytes(b"model")
        source = self.root / "comfy/ldm/minimax/model.py"
        source.parent.mkdir(parents=True)
        source.write_text("class FinalLayer:\n def __init__(self): pass\n", "utf-8")
        with patch("comfy_client.FASTH3_SIZE", 5):
            inventory = await self.client.model_inventory(refresh=True)
            self.assertTrue(inventory[VIDEO_MODEL_FAST])
            self.assertFalse(inventory["fasth3_core_supported"])
            source.write_text("class FinalLayer:\n def __init__(self, pdd_steps=1): pass\n", "utf-8")
            self.assertTrue(video_model_profiles(await self.client.model_inventory(refresh=True))[1]["ready"])
            model.write_bytes(b"bad")
            inventory = await self.client.model_inventory(refresh=True)
            self.assertFalse(inventory[VIDEO_MODEL_FAST])
            self.assertTrue(inventory["ref2va"])


if __name__ == "__main__":
    unittest.main()
