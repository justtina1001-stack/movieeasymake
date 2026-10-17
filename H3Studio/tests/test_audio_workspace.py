import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

from music3 import MUSIC3_MODELS, Music3Error, Music3Installer, MusicJobManager
from settings import SettingsError, SettingsStore


class ConnectionModeMemoryTests(unittest.TestCase):
    def test_each_mode_keeps_its_custom_address_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            store = SettingsStore(path, Path(folder))
            store.update({"base_url": "http://localhost:8288/custom/"})
            store.update({"mode": "remote", "base_url": "https://gpu.example.test/gateway"})
            local = store.update({"mode": "local"})
            self.assertEqual(local.base_url, "http://localhost:8288/custom")
            restarted = SettingsStore(path, Path(folder))
            self.assertEqual(restarted.update({"mode": "remote"}).base_url, "https://gpu.example.test/gateway")
            self.assertEqual(restarted.current.local_base_url, "http://localhost:8288/custom")

    def test_legacy_custom_local_url_is_not_replaced_with_loopback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"mode": "local", "base_url": "http://renderbox.test:8188"}), encoding="utf-8")
            store = SettingsStore(path, Path(folder))
            self.assertEqual(store.current.local_base_url, "http://renderbox.test:8188")
            self.assertEqual(store.update({"auto_start_local": False}).base_url, "http://renderbox.test:8188")

    def test_first_remote_switch_requires_an_explicit_remote_url(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "config.json", Path(folder))
            with self.assertRaisesRegex(SettingsError, "Gateway"):
                store.update({"mode": "remote"})
            self.assertFalse(store.path.exists())

    def test_legacy_remote_config_retains_remote_url_and_restores_local_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"mode": "remote", "base_url": "https://gpu.example.test"}), encoding="utf-8")
            store = SettingsStore(path, Path(folder))
            self.assertEqual(store.current.remote_base_url, "https://gpu.example.test")
            self.assertEqual(store.update({"mode": "local"}).base_url, "http://127.0.0.1:8188")
            store.set_studio_role("host")
            self.assertEqual(store.current.remote_base_url, "https://gpu.example.test")


class MusicRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_submission_is_one_job_including_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comfy = SimpleNamespace(mode="local", comfy_dir=root / "comfy")
            installer = SimpleNamespace(public_status=lambda: {"installed": True})
            with patch.object(MusicJobManager, "_run", new=AsyncMock()):
                manager = MusicJobManager(comfy, root / "data", asyncio.Lock(), installer)
                payload = {"request_id": "request_12345678", "duration": 30, "job_name": "測試", "lyrics": "未送出歌詞", "remote_access_token": "never-in-settings"}
                first = manager.create(payload)
                self.assertIs(manager.create(dict(payload)), first)
                await manager.tasks[first["id"]]
                self.assertEqual(len(manager.jobs), 1)
                restarted = MusicJobManager(comfy, root / "data", asyncio.Lock(), installer)
                self.assertEqual(restarted.create(payload)["id"], first["id"])
                self.assertFalse(restarted.tasks)
                self.assertNotIn("remote_access_token", restarted.list_page()["items"][0]["settings"])

    async def test_reusing_request_id_with_other_lyrics_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comfy = SimpleNamespace(mode="remote", comfy_dir=root / "comfy")
            with patch.object(MusicJobManager, "_run", new=AsyncMock()):
                manager = MusicJobManager(comfy, root, asyncio.Lock(), SimpleNamespace())
                job = manager.create({"request_id": "request_12345678", "mode": "song", "lyrics": "原本歌詞"})
                with self.assertRaisesRegex(Music3Error, "不同設定"):
                    manager.create({"request_id": "request_12345678", "mode": "song", "lyrics": "另一版歌詞"})
                await manager.tasks[job["id"]]
                self.assertEqual(len(manager.jobs), 1)

    async def test_legacy_history_reads_original_settings_without_rewriting_old_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comfy = SimpleNamespace(mode="remote", comfy_dir=root / "comfy")
            manager = MusicJobManager(comfy, root, asyncio.Lock(), SimpleNamespace())
            job_id = "a" * 32
            job = {"id": job_id, "type": "music3", "status": "completed", "name": "舊歌曲", "seed": 77}
            manager.jobs[job_id] = job
            request = {"genre": "jazz", "lyrics": "原始歌詞", "remote_access_token": "secret", "seed": ""}
            (manager.job_dir / f"{job_id}.request.json").write_text(json.dumps(request), encoding="utf-8")
            result = manager.list_page()["items"][0]
            self.assertEqual(result["settings"]["genre"], "jazz")
            self.assertEqual(result["settings"]["seed"], 77)
            self.assertEqual(result["settings"]["job_name"], "舊歌曲")
            self.assertNotIn("remote_access_token", result["settings"])
            self.assertNotIn("settings", job)


class MusicRemoteDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.response_status = 200
        self.missing_node = False
        self.missing_model = False
        self.seen_auth = []

        async def object_info(request):
            self.seen_auth.append(request.headers.get("Authorization"))
            if self.response_status != 200:
                return web.json_response({}, status=self.response_status)
            node = request.match_info["node"]
            if node == "MiniMaxMusic3TextEncode":
                return web.json_response({} if self.missing_node else {node: {"input": {}}})
            entries = {"UNETLoader": ("unet_name", 0), "CLIPLoader": ("clip_name", 1), "VAELoader": ("vae_name", 2)}
            field, index = entries[node]
            choices = [] if self.missing_model and index == 0 else [MUSIC3_MODELS[index]["filename"]]
            return web.json_response({node: {"input": {"required": {field: [choices]}}}})

        app = web.Application()
        app.router.add_get("/object_info/{node}", object_info)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.url = f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}"
        self.comfy = SimpleNamespace(mode="remote", comfy_dir=self.root / "comfy", base_url=self.url,
                                     auth_headers=lambda: {"Authorization": "Bearer test-token"})
        self.installer = Music3Installer(self.comfy)

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_ready_status_explicitly_describes_remote_compute_and_local_result(self):
        result = await self.installer.status_for_current_engine()
        self.assertTrue(result["installed"])
        self.assertTrue(result["available"])
        self.assertFalse(result["can_install"])
        self.assertEqual(result["compute_location"], "remote")
        self.assertEqual(result["model_location"], "remote")
        self.assertEqual(result["result_location"], "studio")
        self.assertEqual(self.seen_auth, ["Bearer test-token"] * 4)

    async def test_authentication_errors_do_not_ask_for_model_reinstallation(self):
        for status in (401, 403):
            self.response_status = status
            result = await self.installer.status_for_current_engine()
            self.assertEqual(result["error_code"], "authentication")
            self.assertFalse(result["available"])
            self.assertFalse(result["installed"])
            self.assertIn("金鑰", result["error"])
            self.assertNotIn("請先更新", result["error"])

    async def test_closed_host_is_a_connection_error(self):
        await self.site.stop()
        result = await self.installer.status_for_current_engine()
        self.assertEqual(result["error_code"], "connection")
        self.assertFalse(result["available"])
        self.assertIn("網路連線", result["error"])

    async def test_successful_empty_node_response_reports_missing_node(self):
        self.missing_node = True
        result = await self.installer.status_for_current_engine()
        self.assertEqual(result["error_code"], "nodes_missing")
        self.assertTrue(result["available"])
        self.assertFalse(result["installed"])

    async def test_missing_model_is_separate_from_missing_node(self):
        self.missing_model = True
        result = await self.installer.status_for_current_engine()
        self.assertEqual(result["error_code"], "models_missing")
        self.assertIn("管理者", result["error"])
        self.assertFalse(result["models"][0]["installed"])
        self.assertTrue(result["models"][1]["installed"])

    async def test_gateway_service_error_is_separate_from_missing_node(self):
        self.response_status = 503
        result = await self.installer.status_for_current_engine()
        self.assertEqual(result["error_code"], "connection")
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("HTTP 503", result["error"])


if __name__ == "__main__":
    unittest.main()
