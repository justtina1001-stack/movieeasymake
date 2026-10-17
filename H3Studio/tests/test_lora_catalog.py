import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import app as studio
from custom_loras import normalize_loras, required_lora_triggers, validate_loras_for_family
from domain import TURBO_LORA_CANDIDATES
from lora_catalog import WUSHU_FILE, COMBAT_FILE, describe_loras, builtin_lora_items
from qwen_image import image_capabilities
from qwen_image_models import MODEL_FILES


WUSHU = "h3studio_custom/" + WUSHU_FILE
COMBAT = "h3studio_custom/" + COMBAT_FILE


class LoraMetadataTests(unittest.TestCase):
    def test_known_author_descriptions_are_distinct_and_licenses_not_invented(self):
        wushu, combat = describe_loras([WUSHU, COMBAT])
        self.assertEqual(wushu["family"], "ref2va")
        self.assertEqual(wushu["trigger"], "wushu_action")
        self.assertTrue(wushu["trigger_required"])
        self.assertEqual(wushu["license"], "other")
        self.assertEqual(combat["trigger"], "")
        self.assertFalse(combat["trigger_required"])
        self.assertIn("prfight2", combat["usage"])
        self.assertIn("prfin1", combat["usage"])
        self.assertEqual(combat["license"], "undeclared")
        self.assertIn("未明確區分", combat["limits"])
        self.assertNotEqual(wushu["purpose"], combat["purpose"])
        for item in (wushu, combat):
            self.assertTrue(item["source_url"].startswith("https://huggingface.co/"))
            self.assertEqual(len(item["revision"]), 40)
            self.assertEqual(len(item["sha256"]), 64)

    def test_unknown_filename_is_not_guessed_and_results_are_independent(self):
        names = ["my_physics_magic.safetensors"]
        result = describe_loras(names)
        self.assertFalse(result[0]["known"])
        self.assertEqual(result[0]["trigger"], "")
        self.assertEqual(result[0]["source_url"], "")
        self.assertIn("尚未確認", result[0]["limits"])
        self.assertNotIn("修復", result[0]["purpose"])
        first = describe_loras([WUSHU])
        first[0]["usage"] = "changed"
        self.assertNotEqual(describe_loras([WUSHU])[0]["usage"], "changed")
        self.assertEqual(names, ["my_physics_magic.safetensors"])

    def test_builtin_metadata_matches_all_existing_compiler_profiles(self):
        names = [candidates[0] for candidates in TURBO_LORA_CANDIDATES.values()]
        items = builtin_lora_items(names)
        self.assertEqual(len(items), 8)
        self.assertEqual({item["key"] for item in items}, set(TURBO_LORA_CANDIDATES))
        for item in items:
            self.assertEqual(item["key"], item["profile"])
            self.assertIn(item["name"], TURBO_LORA_CANDIDATES[item["key"]])
            self.assertFalse(item["manual_selectable"])
            self.assertFalse(item["trigger_required"])
            self.assertTrue(item["installed"])
        self.assertTrue(all(not item["installed"] for item in builtin_lora_items([])))

    def test_qwen_unknown_keeps_exact_engine_name_and_image_family(self):
        name = r"qwen_image_2_1\Unknown_style.safetensors"
        item = describe_loras([name], image=True)[0]
        self.assertEqual(item["name"], name)
        self.assertEqual(item["family"], "qwen-image-2.1")
        self.assertFalse(item["known"])
        self.assertFalse(item["trigger_required"])
        self.assertEqual(item["source_url"], "")

    def test_known_ref_weights_correct_legacy_both_but_reject_explicit_fl(self):
        for family in (None, "both", "ref2va"):
            row = dict(name=WUSHU, enabled=True, strength=0.3)
            if family:
                row["family"] = family
            before = copy.deepcopy(row)
            self.assertEqual(normalize_loras([row])[0]["family"], "ref2va")
            self.assertEqual(row, before)
        with self.assertRaisesRegex(ValueError, "只適用 Ref2VA"):
            normalize_loras([dict(name=WUSHU, family="fl2va")])
        self.assertEqual(normalize_loras([dict(name="unknown.safetensors", family="fl2va")])[0]["family"], "fl2va")

    def test_trigger_requires_enabled_nonzero_matching_family_and_is_not_combat_optional(self):
        selected = [dict(name=WUSHU), dict(name=COMBAT)]
        self.assertEqual(required_lora_triggers(selected, "ref2va"), ["wushu_action"])
        self.assertEqual(required_lora_triggers(selected, "fl2va"), [])
        for settings in (dict(enabled=False), dict(strength=0)):
            self.assertEqual(required_lora_triggers([dict(name=WUSHU, **settings)], "ref2va"), [])
        self.assertEqual(required_lora_triggers([dict(name="unknown.safetensors"), dict(name=COMBAT)], "ref2va"), [])
        self.assertEqual(required_lora_triggers([], "ref2va"), [])

    def test_family_validation_rejects_active_mismatch_but_preserves_disabled_rows(self):
        for name, configured, target in ((WUSHU, "both", "fl2va"), ("other.safetensors", "fl2va", "ref2va")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "不適用"):
                validate_loras_for_family([dict(name=name, family=configured)], target)
            for flags in (dict(enabled=False), dict(strength=0)):
                rows = validate_loras_for_family([dict(name=name, family=configured, **flags)], target)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["name"], name)
        self.assertEqual(validate_loras_for_family([], "ref2va"), [])
        with self.assertRaises(ValueError):
            validate_loras_for_family([], "unknown")


class LoraMetadataAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.enterContext(patch.multiple(studio, DATA_DIR=root, ASSET_DIR=root/"assets",
                                       JOB_DIR=root/"jobs", OUTPUT_DIR=root/"outputs", CONFIG_PATH=root/"config.json"))
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    async def test_catalog_keeps_names_mode_folder_and_excludes_builtin_manual_choices(self):
        builtin = next(iter(TURBO_LORA_CANDIDATES.values()))[0]
        choices = [WUSHU.replace("/", "\\"), COMBAT, "unknown.safetensors", builtin]
        with patch.object(self.app["comfy"], "list_loras", new=AsyncMock(return_value=choices)), \
             patch.object(self.app["comfy"], "ensure_running", new=AsyncMock()) as start:
            response = await self.client.get("/api/loras")
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["names"], [WUSHU, COMBAT, "unknown.safetensors"])
        self.assertEqual([item["name"] for item in data["items"]], data["names"])
        self.assertEqual(data["mode"], self.app["comfy"].mode)
        self.assertIn("folder", data)
        self.assertEqual(len(data["builtin_items"]), 8)
        self.assertEqual(len(data["recommended_items"]), 8)
        self.assertEqual([item["name"] for item in data["recommended_items"] if item["installed"]], [WUSHU, COMBAT])
        self.assertTrue(all(not item["manual_selectable"] for item in data["recommended_items"] if not item["installed"]))
        self.assertNotIn(builtin, data["names"])
        self.assertEqual(self.app["jobs"].jobs, {})
        start.assert_not_awaited()

    async def test_image_status_adds_metadata_without_changing_loras_or_starting_generation(self):
        async def info(request):
            name = request.match_info["name"]
            if name == "TextEncodeQwenImage21":
                return web.json_response({name: {"input": {}}})
            if name == "LoraLoaderModelOnly":
                field, files = "lora_name", [WUSHU, r"qwen_image_2_1\style.safetensors"]
            else:
                index, field = {"UNETLoader": (0, "unet_name"), "CLIPLoader": (1, "clip_name"), "VAELoader": (2, "vae_name")}[name]
                files = [MODEL_FILES[index][1]]
            return web.json_response({name: {"input": {"required": {field: [files]}}}})
        engine = web.Application()
        engine.router.add_get("/object_info/{name}", info)
        server = TestServer(engine)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        comfy = SimpleNamespace(base_url=str(server.make_url("/")).rstrip("/"), mode="remote", auth_headers=lambda: {})
        self.app["image_jobs"].comfy = comfy
        response = await self.client.get("/api/images/status")
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertTrue(data["ready"])
        self.assertEqual(data["loras"], [r"qwen_image_2_1\style.safetensors"])
        self.assertEqual(data["lora_items"][0]["name"], data["loras"][0])
        self.assertEqual(data["lora_items"][0]["family"], "qwen-image-2.1")
        self.assertEqual(self.app["image_jobs"].jobs, {})


if __name__ == "__main__":
    unittest.main()
