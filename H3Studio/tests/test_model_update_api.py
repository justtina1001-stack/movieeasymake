import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

import app as studio
from model_updates import ModelUpdateError


class ModelUpdateAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        app_dir = root / "H3Studio"
        app_dir.mkdir()
        self.enterContext(patch.multiple(
            studio, APP_DIR=app_dir, DATA_DIR=app_dir / "data",
            ASSET_DIR=app_dir / "data/assets", JOB_DIR=app_dir / "data/jobs",
            OUTPUT_DIR=app_dir / "data/outputs", CONFIG_PATH=app_dir / "config.json",
        ))
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.start = self.enterContext(patch.object(
            self.app["model_updates"], "start", new=AsyncMock(return_value={"installer": {"status": "starting"}}),
        ))
        self.enterContext(patch.object(
            self.app["model_updates"], "inspect", return_value={"supported": True, "update_available": True},
        ))

    async def assert_blocked(self, code, message):
        status = await self.client.get("/api/model-updates")
        self.assertEqual(status.status, 200)
        advertised = (await status.json())["blockers"]
        response = await self.client.post("/api/model-updates/start")
        self.assertEqual(response.status, 409)
        payload = await response.json()
        self.assertEqual(payload["blockers"], advertised)
        self.assertIn(code, [item["code"] for item in payload["blockers"]])
        self.assertIn(message, payload["error"])
        self.start.assert_not_awaited()

    async def test_engine_starting_is_named_and_update_works_after_it_finishes(self):
        lock = self.app["comfy"].start_lock
        await lock.acquire()
        try:
            await self.assert_blocked("engine_starting", "啟動或等待連線")
        finally:
            lock.release()
        response = await self.client.post("/api/model-updates/start")
        self.assertEqual(response.status, 202)
        self.start.assert_awaited_once()
        status = await self.client.get("/api/model-updates")
        self.assertEqual((await status.json())["blockers"], [])

    async def test_generation_still_blocks_model_updates(self):
        lock = self.app["jobs"].gpu_lock
        await lock.acquire()
        try:
            await self.assert_blocked("generation", "準備或生成")
        finally:
            lock.release()

    async def test_engine_installation_names_its_progress_location(self):
        for state in ("starting", "running", "cancelling"):
            with self.subTest(state=state):
                self.app["installer"].status["status"] = state
                await self.assert_blocked("engine_installation", "引擎設定")

    async def test_music_download_is_named(self):
        with patch.object(self.app["music_installer"], "public_status", return_value={"active": True}):
            await self.assert_blocked("music_download", "Music 3")

    async def test_voice_download_is_named(self):
        with patch.object(self.app["voice_installer"], "public_status", return_value={"active": True}):
            await self.assert_blocked("voice_download", "語音")

    async def test_idle_local_client_can_update_without_becoming_a_host(self):
        self.assertEqual(self.app["settings"].current.studio_role, "client")
        self.assertEqual(self.app["settings"].current.mode, "local")
        response = await self.client.post("/api/model-updates/start")
        self.assertEqual(response.status, 202)
        self.start.assert_awaited_once()

    async def test_updater_rejections_are_preserved(self):
        self.start.side_effect = ModelUpdateError("目前連線遠端引擎；模型由該 GPU 主機的管理者更新。")
        response = await self.client.post("/api/model-updates/start")
        self.assertEqual(response.status, 409)
        self.assertIn("遠端引擎", (await response.json())["error"])


if __name__ == "__main__":
    unittest.main()
