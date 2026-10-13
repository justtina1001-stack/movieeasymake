"""Guard the isolated fix verified with the failed person's exact seed/graph."""
import unittest
from copy import deepcopy
from dataclasses import replace

from domain import build_workflow, compile_request, required_asset_ids


IMAGE_A, IMAGE_B, VIDEO = (letter * 32 for letter in "abc")


def request_for(mode="r2v", quality="turbo"):
    payload = {
        "mode": mode, "quality_mode": quality,
        "prompt": "Hero sits alone drinking, smiling happily.",
        "seed": 3368518253466986, "duration": 5,
        "aspect_ratio": "16:9", "megapixels": 0.98,
        "memory_optimization": True, "video_vae": "auto",
        "motion_profile": "none",
    }
    if mode in {"fl2va", "extend", "symbol_loop"}:
        payload["first_image_asset_id"] = IMAGE_A
    if mode in {"r2v", "replace", "popup_panel", "mg_animation"}:
        payload["references"] = [
            {"alias": "Hero", "type": "character", "image_asset_ids": [IMAGE_A]},
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_B]},
        ]
    if mode == "replace":
        payload["references"] = payload["references"][:1]
        payload["references"][0]["video_asset_id"] = VIDEO
    if mode == "popup_panel":
        payload["references"].append({"alias": "面板", "type": "object", "image_asset_ids": [IMAGE_A]})
    if mode == "mg_animation":
        payload["references"][0]["alias"] = "角色"
        payload["references"].append({"alias": "轉輪帶", "type": "object", "image_asset_ids": [IMAGE_A]})
    return payload


def graph_for(compiled):
    assets = {asset: f"h3/{asset}.{'mp4' if asset == VIDEO else 'png'}"
              for asset in required_asset_ids(compiled)}
    return build_workflow(compiled, assets, "ref-strength-regression")


class ReferenceTurboStrengthTests(unittest.TestCase):
    def test_fixed_seed_graph_changes_only_reference_distillation_strength(self):
        for quality, steps in (("turbo", 4), ("turbo_ref_quality", 8)):
            for megapixels, dimensions in ((0.4, (864, 480)), (0.7, (1152, 640)), (0.98, (1344, 768))):
                with self.subTest(quality=quality, megapixels=megapixels):
                    payload = request_for(quality=quality)
                    payload["megapixels"] = megapixels
                    original_payload = deepcopy(payload)
                    compiled = compile_request(payload)
                    fixed = graph_for(compiled)
                    previous = graph_for(replace(compiled, turbo_lora_strength=0.75))
                    changed = [key for key in fixed if fixed[key] != previous[key]]
                    self.assertEqual(len(changed), 1)
                    adapter = fixed[changed[0]]
                    self.assertEqual(adapter["class_type"], "LoraLoaderModelOnly")
                    self.assertEqual(adapter["inputs"]["strength_model"], 1.0)
                    adapter["inputs"]["strength_model"] = 0.75
                    self.assertEqual(fixed, previous)
                    self.assertEqual(payload, original_payload)
                    self.assertEqual((compiled.width, compiled.height), dimensions)
                    self.assertEqual((compiled.seed, compiled.length), (3368518253466986, 124))
                    self.assertEqual((compiled.steps, compiled.sampler_name, compiled.scheduler), (steps, "euler", "simple"))
                    self.assertEqual((compiled.shift_video, compiled.shift_audio, compiled.ref_image_size), (12.0, 3.0, "match"))
                    model = next(node for node in previous.values() if node["class_type"] == "UNETLoader")
                    self.assertEqual(model["inputs"]["unet_name"], "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
                    self.assertTrue(any(node["class_type"] == "H3MemoryOptimization" for node in previous.values()))

    def test_reference_families_share_full_builtin_strength_without_changing_custom_loras(self):
        for mode in ("r2v", "replace", "popup_panel", "mg_animation"):
            for quality in ("turbo", "turbo_ref_quality"):
                with self.subTest(mode=mode, quality=quality):
                    payload = request_for(mode, quality)
                    payload["custom_loras"] = [{"name": "scene_detail.safetensors", "strength": 0.6}]
                    graph = graph_for(compile_request(payload))
                    adapters = [node["inputs"] for node in graph.values() if node["class_type"] == "LoraLoaderModelOnly"]
                    self.assertEqual(len(adapters), 2)
                    self.assertEqual(adapters[0]["strength_model"], 1.0)
                    self.assertEqual((adapters[1]["lora_name"], adapters[1]["strength_model"]), ("scene_detail.safetensors", 0.6))

    def test_fl_profiles_and_native_keep_existing_strengths(self):
        for mode in ("t2v", "fl2va", "extend", "symbol_loop"):
            for quality in ("turbo", "turbo_fast", "turbo_quality", "turbo_audio", "sparse_experimental", "turbo_sla"):
                with self.subTest(mode=mode, quality=quality):
                    payload = request_for(mode, quality)
                    if quality == "turbo_sla":
                        payload["memory_optimization"] = False
                    compiled = compile_request(payload)
                    self.assertEqual(compiled.turbo_lora_strength, 1.0 if quality == "turbo_sla" else 0.75)
            graph = graph_for(compile_request(request_for(mode, "native")))
            self.assertFalse(any(node["class_type"] == "LoraLoaderModelOnly" for node in graph.values()))


if __name__ == "__main__":
    unittest.main()
