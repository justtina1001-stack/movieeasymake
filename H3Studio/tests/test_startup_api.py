import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

import app as studio


class StartupAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.enterContext(patch.multiple(
            studio,
            DATA_DIR=root,
            ASSET_DIR=root / "assets",
            JOB_DIR=root / "jobs",
            OUTPUT_DIR=root / "outputs",
            CONFIG_PATH=root / "config.json",
        ))
        self.app = studio.create_app()
        # Exercise HTTP handlers without engines, job recovery, or gateway startup.
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    async def assert_lora_unavailable(self, error):
        with patch.object(self.app["comfy"], "list_loras", new=AsyncMock(side_effect=error)) as list_loras:
            response = await self.client.get("/api/loras")
        list_loras.assert_awaited_once()
        self.assertEqual(response.status, 503)
        self.assertEqual(response.content_type, "application/json")
        payload = await response.json()
        self.assertIn("無法掃描 LoRA，請先啟動或連線引擎。", payload["error"])
        self.assertIn(str(error), payload["error"])

    async def test_missing_engine_lora_catalog_returns_helpful_json(self):
        await self.assert_lora_unavailable(RuntimeError("找不到 ComfyUI 主程式或 Python 環境"))

    async def test_disconnected_engine_lora_catalog_returns_helpful_json(self):
        await self.assert_lora_unavailable(aiohttp.ClientError("Engine connection failed"))

    async def test_new_install_status_is_available_without_an_engine(self):
        with (
            patch.object(self.app["comfy"], "system_stats", new=AsyncMock(return_value=None)),
            patch.object(self.app["comfy"], "model_inventory", new=AsyncMock()) as inventory,
        ):
            response = await self.client.get("/api/status")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["models"], {})
        self.assertEqual(payload["studio_role"], "client")
        inventory.assert_not_awaited()

    async def test_new_install_does_not_expose_gateway_administration(self):
        response = await self.client.get("/api/gateway/status")
        self.assertEqual(response.status, 403)
        payload = await response.json()
        self.assertIn("一般使用者工作站", payload["error"])


if __name__ == "__main__":
    unittest.main()
