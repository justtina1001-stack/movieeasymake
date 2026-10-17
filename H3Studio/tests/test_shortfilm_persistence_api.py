from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

import app as studio
from shortfilm import new_project


class ShortFilmPersistenceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(
            studio, DATA_DIR=self.root, ASSET_DIR=self.root / "assets",
            JOB_DIR=self.root / "jobs", OUTPUT_DIR=self.root / "outputs", CONFIG_PATH=self.root / "config.json",
        )
        self.paths.start()
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    async def test_api_revision_conflict_retains_the_confirmed_project_and_returns_current_version(self):
        response = await self.client.post("/api/shortfilms", json={"title": "原始"})
        self.assertEqual(response.status, 201)
        project = await response.json()
        self.assertEqual(project["revision"], 1)
        url = f"/api/shortfilms/{project['id']}"
        saved_response = await self.client.put(url, json={**project, "title": "第一視窗保存"})
        self.assertEqual(saved_response.status, 200)
        self.assertEqual((await saved_response.json())["revision"], 2)
        conflict_response = await self.client.put(url, json={**project, "title": "過期視窗"})
        self.assertEqual(conflict_response.status, 409)
        conflict = await conflict_response.json()
        self.assertEqual(conflict["code"], "shortfilm_revision_conflict")
        self.assertEqual(conflict["current_revision"], 2)
        fresh_response = await self.client.get(url)
        fresh = await fresh_response.json()
        self.assertEqual(fresh["project"]["title"], "第一視窗保存")
        self.assertEqual(fresh["project"]["revision"], 2)

    async def test_external_update_without_revision_cannot_overwrite_a_versioned_project(self):
        project = self.app["shortfilms"].create(new_project("版本保護"))
        payload = dict(project)
        payload.pop("revision")
        response = await self.client.put(f"/api/shortfilms/{project['id']}", json=payload)
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["current_revision"], 1)

    async def test_storage_status_and_list_expose_an_unreadable_index_without_overwriting_it(self):
        store = self.app["shortfilms"]
        damaged = b"[truncated-projects"
        store.index_path.write_bytes(damaged)
        store._load()  # Simulate the startup read against this isolated storage directory.
        response = await self.client.get("/api/shortfilm-storage")
        self.assertEqual(response.status, 200)
        status = await response.json()
        self.assertFalse(status["available"])
        self.assertIn("儲存已停止", status["message"])
        response = await self.client.get("/api/shortfilms")
        self.assertEqual(response.status, 503)
        self.assertIn("儲存已停止", (await response.json())["error"])
        response = await self.client.post("/api/shortfilms", json={"title": "不能覆寫"})
        self.assertEqual(response.status, 400)
        self.assertEqual(store.index_path.read_bytes(), damaged)

    async def test_storage_status_reports_backup_recovery_and_preserves_the_corrupt_file(self):
        store = self.app["shortfilms"]
        project = store.create(new_project("備份版本"))
        store.update(project["id"], {**project, "title": "後續版本"})
        damaged = b"{broken"
        store.index_path.write_bytes(damaged)
        store._load()
        response = await self.client.get("/api/shortfilm-storage")
        status = await response.json()
        self.assertTrue(status["available"])
        self.assertTrue(status["recovered"])
        self.assertIn("備份", status["message"])
        self.assertEqual((store.directory / status["corrupt_file"]).read_bytes(), damaged)
        response = await self.client.get("/api/shortfilms")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())[0]["title"], "備份版本")

    async def test_overlong_api_update_returns_a_clear_error_and_leaves_saved_words_unchanged(self):
        store = self.app["shortfilms"]
        project = store.create(new_project("原名"))
        response = await self.client.put(f"/api/shortfilms/{project['id']}", json={**project, "synopsis": "字" * 5001})
        self.assertEqual(response.status, 400)
        self.assertIn("5000 字上限", (await response.json())["error"])
        self.assertEqual(store.get(project["id"])["synopsis"], "")
        self.assertEqual(store.get(project["id"])["revision"], 1)


if __name__ == "__main__":
    unittest.main()
