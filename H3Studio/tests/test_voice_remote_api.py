import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer

import app as studio
from remote_voice import RemoteVoiceConnection, RemoteVoiceError
from voice import VOICE_MODELS


class VoiceRemoteAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.multiple(
            studio,
            DATA_DIR=self.root,
            ASSET_DIR=self.root / "assets",
            JOB_DIR=self.root / "jobs",
            OUTPUT_DIR=self.root / "outputs",
            CONFIG_PATH=self.root / "config.json",
        ))
        self.app = studio.create_app()
        # Exercise production HTTP handlers without engine or Gateway startup.
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.app["voice_installer"].shutdown)
        self.addAsyncCleanup(self.app["voice_jobs"].shutdown)
        await self.client.start_server()

    def remote_mode(self):
        comfy = self.app["comfy"]
        comfy.mode = "remote"
        comfy.base_url = "http://127.0.0.1:9"
        comfy.remote_access_token = "isolated-test-personal-token"

    @staticmethod
    def host_status():
        return {
            "state": "complete", "active": False, "current": "GPU 主機模型已就緒",
            "runtime_installed": True, "installed": True, "error": "",
            "requested_mode": "custom",
            "models": {mode: {**definition, "installed": True} for mode, definition in VOICE_MODELS.items()},
        }

    async def test_application_shares_voice_services_with_gateway(self):
        manager = self.app["voice_jobs"]
        gateway = self.app["shared_gateway"]
        self.assertIs(manager.comfy, self.app["comfy"])
        self.assertIs(manager.installer, self.app["voice_installer"])
        self.assertIs(manager.gpu_lock, self.app["jobs"].gpu_lock)
        self.assertIs(manager.asset_path.__self__, self.app["assets"])
        self.assertIs(gateway.voice_jobs, manager)
        self.assertIs(gateway.voice_installer, self.app["voice_installer"])
        self.assertIs(gateway.voice_assets, self.app["assets"])

    async def test_status_uses_remote_host_instead_of_local_installer(self):
        self.remote_mode()
        with (
            patch.object(RemoteVoiceConnection, "status", new=AsyncMock(return_value=self.host_status())) as host_status,
            patch.object(self.app["voice_installer"], "public_status", new=Mock(side_effect=AssertionError("local status read"))) as local_status,
        ):
            response = await self.client.get("/api/voice/status")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        host_status.assert_awaited_once()
        local_status.assert_not_called()
        self.assertEqual(payload["connection_mode"], "remote")
        self.assertFalse(payload["can_install"])
        self.assertTrue(payload["available"])
        self.assertTrue(payload["runtime_installed"])
        self.assertTrue(payload["models"]["clone"]["installed"])
        self.assertEqual(payload["current"], "GPU 主機模型已就緒")
        self.assertNotIn(self.app["comfy"].remote_access_token, json.dumps(payload))

    async def test_remote_status_failure_is_actionable_json(self):
        self.remote_mode()
        with patch.object(RemoteVoiceConnection, "status", new=AsyncMock(side_effect=RemoteVoiceError("請確認個人金鑰。"))):
            response = await self.client.get("/api/voice/status")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["connection_mode"], "remote")
        self.assertFalse(payload["available"])
        self.assertFalse(payload["can_install"])
        self.assertFalse(payload["runtime_installed"])
        self.assertIn("金鑰", payload["error"])

    async def test_remote_install_is_rejected_without_starting_client_installer(self):
        self.remote_mode()
        with patch.object(self.app["voice_installer"], "start", new=AsyncMock()) as start:
            response = await self.client.post("/api/voice/install", json={"mode": "clone"})
        self.assertEqual(response.status, 409)
        self.assertIn("主機", (await response.json())["error"])
        start.assert_not_awaited()

    async def test_remote_install_cancel_is_rejected_without_cancelling_client_installer(self):
        self.remote_mode()
        with patch.object(self.app["voice_installer"], "cancel", new=AsyncMock()) as cancel:
            response = await self.client.post("/api/voice/install/cancel")
        self.assertEqual(response.status, 409)
        self.assertIn("主機", (await response.json())["error"])
        cancel.assert_not_awaited()

    async def test_local_status_retains_installer_support(self):
        self.app["comfy"].mode = "local"
        with (
            patch.object(self.app["voice_installer"], "public_status", new=Mock(return_value=self.host_status())) as local_status,
            patch.object(RemoteVoiceConnection, "status", new=AsyncMock()) as remote_status,
        ):
            response = await self.client.get("/api/voice/status")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["connection_mode"], "local")
        self.assertTrue(payload["can_install"])
        local_status.assert_called_once_with()
        remote_status.assert_not_awaited()

    async def test_local_install_still_dispatches_selected_mode(self):
        self.app["comfy"].mode = "local"
        result = {"state": "starting", "active": True, "requested_mode": "design"}
        with patch.object(self.app["voice_installer"], "start", new=AsyncMock(return_value=result)) as start:
            response = await self.client.post("/api/voice/install", json={"mode": "design"})
        self.assertEqual(response.status, 202)
        self.assertEqual(await response.json(), result)
        start.assert_awaited_once_with("design")

    async def test_local_install_cancel_still_dispatches_installer(self):
        self.app["comfy"].mode = "local"
        result = {"state": "cancelling", "active": True}
        with patch.object(self.app["voice_installer"], "cancel", new=AsyncMock(return_value=result)) as cancel:
            response = await self.client.post("/api/voice/install/cancel")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), result)
        cancel.assert_awaited_once_with()

    async def test_remote_job_is_queued_without_client_model_checks(self):
        self.remote_mode()
        manager = self.app["voice_jobs"]
        with (
            patch.object(self.app["voice_installer"], "runtime_installed", new=Mock(side_effect=AssertionError("client runtime inspected"))) as runtime,
            patch.object(self.app["voice_installer"], "model_installed", new=Mock(side_effect=AssertionError("client model inspected"))) as model,
            patch.object(manager, "_run", new=AsyncMock()) as run,
        ):
            response = await self.client.post("/api/voice/jobs", json={
                "mode": "custom", "text": "新的遠端旁白", "job_name": "同事語音", "seed": 7,
            })
            self.assertEqual(response.status, 202)
            job = await response.json()
            await manager.tasks[job["id"]]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["engine_mode"], "remote")
        self.assertEqual(job["remote_base_url"], self.app["comfy"].base_url)
        self.assertEqual(job["name"], "同事語音")
        self.assertEqual(job["seed"], 7)
        self.assertIn(job["id"], manager.jobs)
        self.assertTrue((manager.job_dir / f"{job['id']}.json").exists())
        runtime.assert_not_called()
        model.assert_not_called()
        run.assert_awaited_once()
        self.assertFalse((self.root / "voice_runtime").exists())

    async def test_local_job_still_requires_installed_runtime(self):
        self.app["comfy"].mode = "local"
        with patch.object(self.app["voice_installer"], "runtime_installed", new=Mock(return_value=False)):
            response = await self.client.post("/api/voice/jobs", json={"mode": "custom", "text": "本機旁白"})
        self.assertEqual(response.status, 400)
        self.assertIn("尚未安裝", (await response.json())["error"])
        self.assertEqual(self.app["voice_jobs"].jobs, {})

    async def test_remote_job_requires_gateway_personal_token(self):
        self.remote_mode()
        self.app["comfy"].remote_access_token = ""
        response = await self.client.post("/api/voice/jobs", json={"mode": "custom", "text": "遠端旁白"})
        self.assertEqual(response.status, 400)
        self.assertIn("個人金鑰", (await response.json())["error"])
        self.assertEqual(self.app["voice_jobs"].jobs, {})


if __name__ == "__main__":
    unittest.main()
