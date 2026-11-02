"""An image adapter must never become an active H3 video adapter."""
import unittest

from domain import IMAGE_ONLY_LORA_FILENAMES, RequestError, build_workflow, compile_request


class AnimaVideoIsolationTests(unittest.TestCase):
    def test_anima_adapter_is_rejected_for_video_in_any_folder(self):
        for name in ("anima_rgba/anima_rgba_lora.safetensors", "anima_rgba_lora.safetensors"):
            for mode in ("t2v", "r2v"):
                with self.subTest(name=name, mode=mode):
                    row = dict(name=name, family="both", strength=1, enabled=True)
                    with self.assertRaisesRegex(RequestError, "僅供圖片"):
                        compile_request(dict(mode=mode, prompt="One static shot.", custom_loras=[row]))

    def test_inactive_image_adapter_can_be_preserved_without_loading(self):
        for override in (dict(enabled=False), dict(strength=0)):
            row = dict(name="anima_rgba/anima_rgba_lora.safetensors", family="both", strength=1, enabled=True)
            row.update(override)
            compiled = compile_request(dict(mode="t2v", prompt="A red cup.", custom_loras=[row]))
            workflow = build_workflow(compiled, {}, "test")
            self.assertEqual(compiled.custom_loras[0]["name"], row["name"])
            self.assertFalse(any(node["class_type"] == "LoraLoaderModelOnly" for node in workflow.values()))
            self.assertIn(row["name"].split("/")[-1], IMAGE_ONLY_LORA_FILENAMES)


if __name__ == "__main__":
    unittest.main()
