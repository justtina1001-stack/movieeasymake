import unittest
from custom_loras import normalize_loras
from domain import compile_request, build_workflow, RequestError, TURBO_LORA_CANDIDATES
from shortfilm import normalize_project, new_project, new_scene, new_shot, compile_shot_payload


class CustomLoraTests(unittest.TestCase):
    def payload(self, **changes):
        return dict(mode="t2v", prompt="A walking character", **changes)

    def test_stack_reaches_sampler_and_keeps_turbo(self):
        compiled = compile_request(self.payload(quality_mode="turbo", custom_loras=[
            dict(name="h3studio_custom/walk.safetensors", strength=0.3, family="fl2va"),
            dict(name="ref.safetensors", family="ref2va"),
            dict(name="off.safetensors", enabled=False),
            dict(name="zero.safetensors", strength=0),
        ]))
        workflow = build_workflow(compiled, {}, "test", custom_lora_names={
            "h3studio_custom/walk.safetensors": "h3studio_custom\\walk.safetensors"})
        loras = [(key, node) for key, node in workflow.items() if node["class_type"] == "LoraLoaderModelOnly"]
        self.assertEqual(len(loras), 2)
        key, custom = loras[-1]
        self.assertEqual(custom["inputs"]["strength_model"], 0.3)
        self.assertEqual(custom["inputs"]["lora_name"], "h3studio_custom\\walk.safetensors")
        guider = next(n for n in workflow.values() if n["class_type"] == "BasicGuider")
        self.assertEqual(guider["inputs"]["model"], [key, 0])

    def test_reference_mode_uses_reference_adapters(self):
        payload = dict(mode="r2v", prompt="Alice walks", references=[dict(alias="Alice", type="character", image_asset_ids=["a" * 32])],
                       custom_loras=[dict(name="ref.safetensors", family="ref2va"), dict(name="fl.safetensors", family="fl2va")])
        workflow = build_workflow(compile_request(payload), {"a" * 32: "a.png"}, "test")
        self.assertEqual([n["inputs"]["lora_name"] for n in workflow.values() if n["class_type"] == "LoraLoaderModelOnly"], ["ref.safetensors"])

    def test_invalid_adapter_settings_rejected(self):
        for name in ["../x.safetensors", "C:/x.safetensors", "/x.safetensors", "x.ckpt", "a//x.safetensors"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                normalize_loras([dict(name=name)])
        for strength in [float("nan"), float("inf"), -1, 3, "bad"]:
            with self.subTest(strength=strength), self.assertRaises(RequestError):
                compile_request(self.payload(custom_loras=[dict(name="x.safetensors", strength=strength)]))
        with self.assertRaises(ValueError):
            normalize_loras([dict(name="x.safetensors"), dict(name="x.safetensors")])

    def test_builtin_turbo_cannot_be_double_loaded(self):
        name = next(iter(TURBO_LORA_CANDIDATES.values()))[0]
        with self.assertRaises(RequestError):
            compile_request(self.payload(custom_loras=[dict(name=name)]))

    def test_shortfilm_retains_and_forwards_settings(self):
        project = new_project("LoRA test")
        project["custom_loras"] = [dict(name="h3studio_custom/style.safetensors", strength=0.4, family="both", enabled=True)]
        scene = new_scene()
        shot = new_shot()
        shot["action"] = "A character walks."
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        saved = normalize_project(project)
        payload, _ = compile_shot_payload(saved, scene["id"], shot["id"])
        self.assertEqual(payload["custom_loras"], project["custom_loras"])
        self.assertEqual(compile_request(payload).custom_loras, project["custom_loras"])


if __name__ == "__main__":
    unittest.main()
