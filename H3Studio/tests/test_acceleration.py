import unittest
from dataclasses import asdict, replace

from domain import (
    FL_ONLY_QUALITY_MODES,
    TURBO_LORA_CANDIDATES,
    RequestError,
    build_workflow,
    compile_request,
    validate_runtime_inventory,
)
from shortfilm import compile_shot_payload, new_project, new_scene, new_shot, normalize_project


IMAGE = "a" * 32
VIDEO = "b" * 32


class AccelerationTests(unittest.TestCase):
    def payload(self, mode="t2v", **changes):
        payload = dict(mode=mode, prompt="A character waves.", motion_profile="none")
        if mode in {"fl2va", "extend", "symbol_loop"}:
            payload["first_image_asset_id"] = IMAGE
        if mode in {"r2v", "replace", "popup_panel", "mg_animation"}:
            payload["references"] = [dict(alias="Hero", type="character", image_asset_ids=[IMAGE])]
            if mode == "replace":
                payload["references"][0]["video_asset_id"] = VIDEO
            if mode == "popup_panel":
                payload["references"].extend([
                    dict(alias="背景圖", type="background", image_asset_ids=[IMAGE]),
                    dict(alias="面板", type="object", image_asset_ids=[IMAGE]),
                ])
            if mode == "mg_animation":
                payload["references"][0]["alias"] = "角色"
                payload["references"].extend([
                    dict(alias="背景圖", type="background", image_asset_ids=[IMAGE]),
                    dict(alias="轉輪帶", type="object", image_asset_ids=[IMAGE]),
                ])
        payload.update(changes)
        return payload

    def inventory(self, **changes):
        result = dict(fl2va=True, ref2va=True, text_encoder=True, video_vae=True, audio_vae=True,
                      h3_sigma_shift=True, h3_memory_optimization=True,
                      h3_optimizations=True, h3_sla_attention=True)
        result.update(changes)
        return result

    def test_old_recipe_memory_default_is_false(self):
        for value in (None, False, "false", "true", 1):
            compiled = compile_request(self.payload(memory_optimization=value))
            self.assertFalse(compiled.memory_optimization)
            workflow = build_workflow(compiled, {}, "legacy")
            self.assertNotIn("H3MemoryOptimization", [node["class_type"] for node in workflow.values()])
        self.assertFalse(compile_request(self.payload()).memory_optimization)

    def test_independent_memory_reaches_guider_for_every_family(self):
        for mode in ("t2v", "fl2va", "extend", "symbol_loop", "r2v", "replace", "popup_panel", "mg_animation"):
            for quality in ("native", "turbo"):
                with self.subTest(mode=mode, quality=quality):
                    compiled = compile_request(self.payload(mode, quality_mode=quality, memory_optimization=True))
                    self.assertTrue(asdict(compiled)["memory_optimization"])
                    workflow = build_workflow(compiled, {IMAGE: "image.png", VIDEO: "video.mp4"}, "memory")
                    memory_nodes = [(key, node) for key, node in workflow.items() if node["class_type"] == "H3MemoryOptimization"]
                    self.assertEqual(len(memory_nodes), 1)
                    guider = next(node for node in workflow.values() if node["class_type"] == "BasicGuider")
                    self.assertEqual(guider["inputs"]["model"], [memory_nodes[0][0], 0])
                    self.assertNotIn("H3SparseAttention", [node["class_type"] for node in workflow.values()])

    def test_legacy_sparse_keeps_exactly_one_memory_patch(self):
        for memory in (True, False):
            compiled = compile_request(self.payload(quality_mode="sparse_experimental", memory_optimization=memory))
            types = [node["class_type"] for node in build_workflow(compiled, {}, "sparse").values()]
            self.assertEqual(types.count("H3MemoryOptimization"), 1)
            self.assertEqual(types.count("H3SparseAttention"), 1)
            self.assertNotIn("BlockSparseAttention", types)

    def test_sla_uses_matching_adapter_and_native_dynamic_combo(self):
        compiled = compile_request(self.payload("fl2va", quality_mode="turbo_sla", megapixels=0.98))
        self.assertEqual(compiled.steps, 4)
        self.assertEqual((compiled.sampler_name, compiled.scheduler), ("euler", "simple"))
        self.assertEqual((compiled.shift_video, compiled.shift_audio), (6.0, 3.0))
        self.assertEqual(compiled.turbo_lora_strength, 1.0)
        self.assertIn("_sla_comfyui_", compiled.turbo_lora)
        workflow = build_workflow(compiled, {IMAGE: "image.png"}, "sla")
        types = [node["class_type"] for node in workflow.values()]
        self.assertEqual(types.count("LoraLoaderModelOnly"), 1)
        self.assertNotIn("H3SparseAttention", types)
        self.assertNotIn("H3MemoryOptimization", types)
        sparse = next(node for node in workflow.values() if node["class_type"] == "BlockSparseAttention")
        self.assertEqual(sparse["inputs"]["selection"], "sla")
        self.assertEqual(sparse["inputs"]["selection.keep_percent"], 15.0)
        self.assertEqual(sparse["inputs"]["start_percent"], 0.0)
        self.assertEqual(sparse["inputs"]["min_tokens"], 0)
        with self.assertRaisesRegex(RequestError, "關閉額外省顯存"):
            compile_request(self.payload(quality_mode="turbo_sla", memory_optimization=True))
        with self.assertRaisesRegex(RequestError, "關閉額外省顯存"):
            build_workflow(replace(compiled, memory_optimization=True), {IMAGE: "image.png"}, "unsafe")

    def test_new_quality_profiles_preserve_distinct_family_settings(self):
        audio = compile_request(self.payload(quality_mode="turbo_audio"))
        self.assertIn("fl2v_turbo_4step_v1.2_768p_comfyui", audio.turbo_lora)
        self.assertEqual((audio.steps, audio.shift_video, audio.shift_audio), (4, 6.0, 3.0))
        ref = compile_request(self.payload("r2v", quality_mode="turbo_ref_quality", ref_image_size="max"))
        self.assertIn("ref2v_turbo_8step_v1.0_768p_comfyui", ref.turbo_lora)
        self.assertEqual((ref.steps, ref.shift_video, ref.shift_audio), (8, 12.0, 3.0))
        self.assertEqual(ref.ref_image_size, "match")
        for quality in FL_ONLY_QUALITY_MODES:
            with self.subTest(quality=quality), self.assertRaisesRegex(RequestError, "多模態參考"):
                compile_request(self.payload("r2v", quality_mode=quality))
        with self.assertRaisesRegex(RequestError, "Ref 8 步"):
            compile_request(self.payload(quality_mode="turbo_ref_quality"))

    def test_new_builtin_adapters_cannot_be_stacked_as_custom_loras(self):
        for key in ("fl2v_768_sla", "fl2v_768_audio_v12", "ref2v_768_quality_v10"):
            with self.subTest(key=key), self.assertRaisesRegex(RequestError, "內建 Turbo"):
                compile_request(self.payload(custom_loras=[dict(name="subfolder/" + TURBO_LORA_CANDIDATES[key][0])]))

    def test_capability_validation_is_explicit_and_family_specific(self):
        native = compile_request(self.payload())
        validate_runtime_inventory(native, self.inventory(ref2va=False, h3_memory_optimization=False))
        with self.assertRaisesRegex(RequestError, "FL2VA 主模型"):
            validate_runtime_inventory(native, self.inventory(fl2va=False))
        with self.assertRaisesRegex(RequestError, "H3MemoryOptimization"):
            validate_runtime_inventory(compile_request(self.payload(memory_optimization=True)),
                                       self.inventory(h3_memory_optimization=False))
        sla = compile_request(self.payload(quality_mode="turbo_sla"))
        validate_runtime_inventory(sla, self.inventory(h3_memory_optimization=False, h3_optimizations=False))
        with self.assertRaisesRegex(RequestError, "BlockSparseAttention"):
            validate_runtime_inventory(sla, self.inventory(h3_sla_attention=False))
        with self.assertRaisesRegex(RequestError, "MiniMaxH3SigmaShift"):
            validate_runtime_inventory(sla, self.inventory(h3_sigma_shift=False))

    def test_shortfilm_roundtrip_forwards_memory_and_new_quality(self):
        project = new_project("Acceleration")
        scene, shot = new_scene(), new_shot()
        shot["action"] = "The character walks."
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        self.assertFalse(normalize_project(project)["memory_optimization"])
        for quality in ("native", "turbo_audio", "turbo_ref_quality", "turbo_sla"):
            project.update(quality_mode=quality, memory_optimization=quality != "turbo_sla")
            saved = normalize_project(project)
            payload, warnings = compile_shot_payload(saved, scene["id"], shot["id"])
            self.assertEqual(payload["memory_optimization"], project["memory_optimization"])
            if quality == "turbo_ref_quality":
                self.assertEqual(payload["quality_mode"], "turbo")
                self.assertTrue(any("沒有 Ref2VA" in warning for warning in warnings))
            else:
                self.assertEqual(payload["quality_mode"], quality)
            self.assertEqual(compile_request(payload).memory_optimization, project["memory_optimization"])


if __name__ == "__main__":
    unittest.main()
