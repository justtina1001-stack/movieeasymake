"""Character images pass through local vision, manuscript snapshots and H3 import."""
import asyncio
import base64
import copy
import io
import json
import unittest

from aiohttp import FormData
from PIL import Image

from scriptwriting import DEFAULT_SETTINGS, ScriptwritingManager
from tests import test_scriptwriting as scripts
from tests import test_script_references as references


class CharacterReferenceAPITests(unittest.IsolatedAsyncioTestCase):
    # Share the existing fake stream/lock harness without inheriting its tests.
    fake_tags = scripts.ScriptAPITests.fake_tags
    fake_chat = scripts.ScriptAPITests.fake_chat
    fake_unload = scripts.ScriptAPITests.fake_unload
    redirect_target = scripts.ScriptAPITests.redirect_target
    fake_free = scripts.ScriptAPITests.fake_free
    create = scripts.ScriptAPITests.create
    submit = scripts.ScriptAPITests.submit
    complete = scripts.ScriptAPITests.complete
    asyncTearDown = scripts.ScriptAPITests.asyncTearDown

    async def asyncSetUp(self):
        self.vision_show = {"capabilities": ["completion", "vision", "thinking"]}
        await scripts.ScriptAPITests.asyncSetUp(self)
        self.tags.append({"name": DEFAULT_SETTINGS["vision_model"], "size": 18_000_000_000})
        self.assets = references.Assets(self.root)
        self.app["assets"] = self.assets
        self.assets.save_image(Image.new("RGBA", (20, 10), (200, 50, 10, 100)), "角色.png", "script-character-reference")
        self.reference = references.card(
            name="阿青", image_asset_id=references.IMAGE_ID, image_name="角色.png",
            appearance="圓臉、金色短毛，穿紅色短外套，戴小圓帽。",
        )

    async def fake_show(self, request):
        model = (await request.json())["model"]
        from aiohttp import web
        return web.json_response(self.vision_show if model == DEFAULT_SETTINGS["vision_model"] else self.show)

    async def test_named_images_persist_export_and_legacy_projects_stay_empty(self):
        old = await self.create()
        self.assertEqual(old["character_references"], [])
        project = await self.create(character_references=[self.reference])
        saved = ScriptwritingManager(self.app, self.root).get_project(project["id"])
        self.assertEqual(saved["character_references"], [self.reference])
        exported = await self.client.get(f"/api/scripts/projects/{project['id']}/export?format=json")
        self.assertEqual((await exported.json())["character_references"], [self.reference])
        # Saving another field preserves the image binding.
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "title": "更新標題"})
        self.assertEqual((await response.json())["character_references"], [self.reference])

    async def test_image_upload_checks_real_image_content_and_preserves_alpha(self):
        form = FormData()
        form.add_field("file", references.picture(), filename="portrait.png", content_type="image/png")
        response = await self.client.post("/api/scripts/reference-images", data=form)
        self.assertEqual(response.status, 201, await response.text())
        metadata = await response.json()
        self.assertEqual(metadata["url"], f"/api/assets/{metadata['id']}")
        self.assertEqual(self.assets.saved[-1]["kind"], "script-character-reference")
        with Image.open(self.assets.path_for(metadata["id"])) as image:
            self.assertEqual(image.mode, "RGBA")
        invalid = FormData()
        invalid.add_field("file", b"not an image", filename="portrait.png", content_type="image/png")
        before = len(self.assets.saved)
        response = await self.client.post("/api/scripts/reference-images", data=invalid)
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.assets.saved), before)

    async def test_writing_requires_appearance_and_never_sends_images_to_hermes(self):
        unanalysed = {**self.reference, "appearance": ""}
        project = await self.create(character_references=[unanalysed])
        response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": "screenplay"})
        self.assertEqual(response.status, 400)
        self.assertIn("外觀", (await response.json())["error"])
        self.assertEqual(self.chat_requests, [])
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "character_references": [self.reference]})
        project = await response.json()
        result = await self.complete(await self.submit(project))
        self.assertEqual(result["status"], "completed", result)
        payload = self.chat_requests[-1]
        self.assertEqual(payload["model"], DEFAULT_SETTINGS["model"])
        self.assertTrue(all("images" not in message for message in payload["messages"]))
        body = json.loads(payload["messages"][1]["content"].split("\n", 1)[0])
        self.assertEqual(body["character_references"][0]["name"], "阿青")
        self.assertEqual(body["character_references"][0]["appearance"], self.reference["appearance"])
        self.assertNotIn("image_asset_id", body["character_references"][0])

    async def test_local_vision_caption_uses_snapshot_and_never_changes_manuscript(self):
        self.reply = "圓臉角色穿紅色短外套；頭頂有小圓帽，身體比例短小。"
        self.mode = "hold"
        self.manager.settings.update(num_ctx=2048, num_predict=1024)
        project = await self.create(idea="", content="", character_references=[self.reference])
        job = await self.submit(project, action="appearance", reference_id=self.reference["id"])
        await asyncio.wait_for(self.started.wait(), 2)
        renamed = {**self.reference, "name": "阿藍", "appearance": "使用者後來修改的造型。"}
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "character_references": [renamed]})
        self.assertEqual(response.status, 200)
        self.finish.set()
        result = await self.complete(job)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["reference_name"], "阿青")
        self.assertEqual(result["input"]["project"]["character_references"], [self.reference])
        current = self.manager.get_project(project["id"])
        self.assertEqual(current["character_references"], [renamed])
        self.assertEqual(current["content"], "")
        payload = self.chat_requests[-1]
        self.assertEqual(payload["model"], DEFAULT_SETTINGS["vision_model"])
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(self.manager.settings["num_ctx"], 2048)
        self.assertFalse(payload["think"])
        with Image.open(io.BytesIO(base64.b64decode(payload["messages"][-1]["images"][0]))) as image:
            self.assertEqual(image.format, "JPEG")
        self.assertEqual(self.unloads[-1]["payload"]["model"], DEFAULT_SETTINGS["vision_model"])
        self.assertTrue(self.unloads[-1]["lock_held"])
        self.assertEqual(self.manager.settings["model"], DEFAULT_SETTINGS["model"])

    async def test_text_only_vision_model_is_rejected_before_inference(self):
        self.vision_show = {"capabilities": ["completion"]}
        project = await self.create(character_references=[self.reference])
        result = await self.complete(await self.submit(project, action="appearance", reference_id=self.reference["id"]))
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("不支援看圖", result["error"])
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])
        status = await (await self.client.get("/api/scripts/status")).json()
        self.assertTrue(status["ready"])
        self.assertFalse(status["vision_ready"])

    async def test_malformed_vision_capabilities_report_a_useful_error(self):
        for capabilities in (None, "vision", {"vision": True}):
            self.vision_show = {"capabilities": capabilities}
            status = await (await self.client.get("/api/scripts/status")).json()
            self.assertTrue(status["ready"])
            self.assertFalse(status["vision_ready"])
            self.assertIn("能力資訊格式錯誤", status["vision_error"])

    async def test_cloud_vision_model_and_missing_image_bindings_are_rejected(self):
        response = await self.client.post("/api/scripts/settings", json={"vision_model": "qwen:cloud"})
        self.assertEqual(response.status, 400)
        missing = {**self.reference, "image_asset_id": "f" * 32}
        response = await self.client.post("/api/scripts/projects", json={"title": "缺圖", "character_references": [missing]})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.manager.projects, {})

    async def test_cancel_caption_preserves_partial_and_releases_vision_model(self):
        self.mode = "hold"
        self.reply = "圓臉角色穿紅色短外套，戴圓帽。"
        project = await self.create(character_references=[self.reference])
        job = await self.submit(project, action="appearance", reference_id=self.reference["id"])
        await asyncio.wait_for(self.started.wait(), 2)
        for _ in range(100):
            if self.manager.jobs[job["id"]]["content"]:
                break
            await asyncio.sleep(0.01)
        result = await (await self.client.post(f"/api/scripts/jobs/{job['id']}/cancel")).json()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["content"])
        self.assertEqual(self.manager.get_project(project["id"])["character_references"], [self.reference])
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.unloads[-1]["payload"]["model"], DEFAULT_SETTINGS["vision_model"])

    async def test_import_binds_named_images_and_preserves_references_omitted_by_model(self):
        extra = {**self.reference, "id": "3" * 32, "name": "石門", "type": "background", "appearance": "金色石框與中央藍色光環。"}
        project = await self.create(duration=5, character_references=[self.reference, extra])
        self.reply = json.dumps(scripts.STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(project, action="storyboard"))
        imported = self.manager.import_shortfilm(result["id"])["project"]
        assets = {item["alias"]: item for item in imported["assets"]}
        for reference in (self.reference, extra):
            self.assertEqual(assets[reference["name"]]["image_asset_ids"], [references.IMAGE_ID])
            self.assertIn(reference["appearance"], assets[reference["name"]]["description"])
            self.assertEqual(assets[reference["name"]]["type"], reference["type"])
        self.assertEqual(imported["target_duration"], 5)
        self.assertEqual(self.manager.import_shortfilm(result["id"])["shortfilm_id"], imported["id"])
