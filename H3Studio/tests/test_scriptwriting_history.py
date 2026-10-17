"""Durable generated drafts, migration, pagination, and safe history deletion."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

import scriptwriting
from scriptwriting import DEFAULT_SETTINGS, ScriptwritingManager
from tests import test_scriptwriting as fixture_module

STORYBOARD = fixture_module.STORYBOARD


def history_job(number, *, status="completed", project_id=None, updated_at=None):
    """Synthetic records use no real project or user files."""
    timestamp = f"2026-10-02T01:00:{number:02d}+00:00"
    return {
        "id": f"{number:032x}",
        "project_id": project_id or "a" * 32,
        "project_title": "測試劇本",
        "action": "screenplay",
        "status": status,
        "content": f"第 {number} 稿：角色走向車站。",
        "error": None,
        "created_at": timestamp,
        "updated_at": updated_at or timestamp,
        "model": DEFAULT_SETTINGS["model"],
        "input": {
            "project": {"title": "測試劇本", "idea": "角色搭車。", "content": "原始草稿"},
            "settings": copy.deepcopy(DEFAULT_SETTINGS),
            "instructions": "保留角色語氣。",
        },
        "shortfilm_id": None,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ScriptHistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / "scriptwriting"
        self.directory.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        return ScriptwritingManager(web.Application(), self.root)

    def test_legacy_index_migrates_complete_input_to_individual_archives(self):
        records = [history_job(1), history_job(2, status="failed")]
        records[1].update(content="未完成的第一句。", error="模型串流中斷")
        write_json(self.directory / "jobs.json", records)

        loaded = self.load()

        self.assertEqual(loaded.history_directory, self.directory / "history")
        for record in records:
            self.assertEqual(fixture_module.stored_record(loaded.get_job(record["id"])), record)
            archived = json.loads((loaded.history_directory / f"{record['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(archived, record)
        self.assertEqual(self.load().jobs, loaded.jobs)
        self.assertFalse(list(self.directory.rglob("*.tmp")))

    def test_newest_timestamp_wins_in_both_index_and_archive_directions(self):
        old_first, old_second = history_job(1), history_job(2)
        new_first, new_second = copy.deepcopy(old_first), copy.deepcopy(old_second)
        new_first.update(content="索引的新稿。", updated_at="2026-10-02T02:00:00+00:00")
        new_second.update(content="單筆檔案的新稿。", updated_at="2026-10-02T02:00:00+00:00")
        write_json(self.directory / "jobs.json", [new_first, old_second])
        write_json(self.directory / "history" / f"{old_first['id']}.json", old_first)
        write_json(self.directory / "history" / f"{new_second['id']}.json", new_second)

        loaded = self.load()

        self.assertEqual(fixture_module.stored_record(loaded.get_job(new_first["id"])), new_first)
        self.assertEqual(fixture_module.stored_record(loaded.get_job(new_second["id"])), new_second)
        self.assertEqual(self.load().jobs, loaded.jobs)

    def test_saved_running_and_queued_jobs_restart_as_interrupted(self):
        running = history_job(1, status="running")
        queued = history_job(2, status="queued")
        write_json(self.directory / "jobs.json", [running, queued])

        loaded = self.load()

        for original in (running, queued):
            result = loaded.get_job(original["id"])
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["content"], original["content"])
            self.assertEqual(result["input"], original["input"])
            self.assertTrue(result["error"])
            self.assertEqual(self.load().get_job(original["id"]), result)
        self.assertEqual(loaded.tasks, {})

    def test_separate_instances_adding_jobs_do_not_overwrite_other_history(self):
        first, second = self.load(), self.load()
        one, two = history_job(1), history_job(2)
        first.jobs[one["id"]] = copy.deepcopy(one)
        second.jobs[two["id"]] = copy.deepcopy(two)

        first._save_jobs(one["id"])
        second._save_jobs(two["id"])

        expected = {one["id"]: one, two["id"]: two}
        self.assertEqual(self.load().jobs, expected)
        indexed = json.loads(first.jobs_path.read_text(encoding="utf-8"))
        self.assertEqual({item["id"]: item for item in indexed}, expected)
        # A subsequent aggregate save from the stale first instance must retain two.
        first._save_jobs()
        self.assertEqual(self.load().jobs, expected)

    def test_stale_instance_cannot_resurrect_deleted_job(self):
        first = self.load()
        job = history_job(1)
        first.jobs[job["id"]] = copy.deepcopy(job)
        first._save_jobs(job["id"])
        stale = self.load()

        first.delete_job(job["id"])
        stale.jobs[job["id"]].update(content="另一工作階段的舊稿。", updated_at="2099-01-01T00:00:00+00:00")
        stale._save_jobs()

        self.assertNotIn(job["id"], self.load().jobs)
        tombstone = json.loads((first.history_directory / f"{job['id']}.json").read_text(encoding="utf-8"))
        self.assertTrue(tombstone["deleted"])

    def test_archive_is_committed_before_index_failure_and_exposes_warning(self):
        manager = self.load()
        job = history_job(1)
        manager.jobs[job["id"]] = copy.deepcopy(job)
        atomic = scriptwriting._atomic_json

        def fail_index(path, value):
            if Path(path) == manager.jobs_path:
                raise OSError("test: index volume full")
            return atomic(path, value)

        with patch("scriptwriting._atomic_json", side_effect=fail_index):
            manager._save_jobs(job["id"])

        self.assertIsInstance(manager.history_persistence_error, str)
        self.assertTrue(manager.history_persistence_error)
        self.assertEqual(fixture_module.stored_record(self.load().get_job(job["id"])), job)
        manager._save_jobs(job["id"])
        self.assertIsNone(manager.history_persistence_error)

    def test_missing_index_is_rebuilt_from_individual_history(self):
        manager = self.load()
        records = [history_job(1), history_job(2, status="failed")]
        for record in records:
            manager.jobs[record["id"]] = record
        manager._save_jobs()
        manager.jobs_path.unlink()

        restored = self.load()

        self.assertEqual(restored.jobs, {record["id"]: record for record in records})
        indexed = json.loads(restored.jobs_path.read_text(encoding="utf-8"))
        self.assertEqual({item["id"]: item for item in indexed}, restored.jobs)

    def test_corrupt_index_recovers_archives_and_preserves_original_bytes(self):
        manager = self.load()
        job = history_job(1)
        manager.jobs[job["id"]] = job
        manager._save_jobs(job["id"])
        broken = b'{"unfinished index":'
        manager.jobs_path.write_bytes(broken)

        restored = self.load()

        self.assertEqual(fixture_module.stored_record(restored.get_job(job["id"])), job)
        preserved = [path for path in restored.directory.glob("jobs*") if path.is_file() and path.read_bytes() == broken]
        self.assertTrue(preserved, "The malformed index must remain available for recovery or diagnosis")
        self.assertEqual(fixture_module.stored_record(self.load().get_job(job["id"])), job)

    def test_corrupt_archive_at_startup_recovers_from_healthy_index_and_preserves_bytes(self):
        manager = self.load()
        job = history_job(1)
        manager.jobs[job["id"]] = copy.deepcopy(job)
        manager._save_jobs(job["id"])
        archive = manager.history_directory / f"{job['id']}.json"
        broken = b'{"startup archive":'
        archive.write_bytes(broken)

        restored = self.load()

        self.assertEqual(fixture_module.stored_record(restored.get_job(job["id"])), job)
        self.assertEqual(json.loads(archive.read_text(encoding="utf-8")), job)
        backups = list(restored.history_directory.glob(f"{archive.name}.corrupt-*.bak"))
        self.assertTrue(backups)
        self.assertTrue(any(path.read_bytes() == broken for path in backups))
        self.assertEqual(fixture_module.stored_record(self.load().get_job(job["id"])), job)
        self.assertIsNone(restored.history_persistence_error)

    def test_corrupt_archive_during_save_recovers_old_record_and_commits_new_content(self):
        manager = self.load()
        old_job = history_job(1)
        manager.jobs[old_job["id"]] = copy.deepcopy(old_job)
        manager._save_jobs(old_job["id"])
        archive = manager.history_directory / f"{old_job['id']}.json"
        broken = b'{"runtime archive":'
        archive.write_bytes(broken)
        manager.jobs[old_job["id"]].update(content="新增完成稿：阿青登上列車。", updated_at="2026-10-02T02:00:00+00:00")
        new_job = copy.deepcopy(manager.jobs[old_job["id"]])

        manager._save_jobs(old_job["id"])

        self.assertEqual(fixture_module.stored_record(manager.get_job(old_job["id"])), new_job)
        self.assertEqual(json.loads(archive.read_text(encoding="utf-8")), new_job)
        indexed = json.loads(manager.jobs_path.read_text(encoding="utf-8"))
        self.assertEqual({item["id"]: item for item in indexed}, {new_job["id"]: new_job})
        backups = list(manager.history_directory.glob(f"{archive.name}.corrupt-*.bak"))
        self.assertTrue(backups)
        self.assertTrue(any(path.read_bytes() == broken for path in backups))
        self.assertEqual(fixture_module.stored_record(self.load().get_job(old_job["id"])), new_job)
        self.assertIsNone(manager.history_persistence_error)


class ScriptHistoryAPITests(unittest.IsolatedAsyncioTestCase):
    # Reuse the fake local Ollama service without inheriting and rerunning its tests.
    asyncSetUp = fixture_module.ScriptAPITests.asyncSetUp
    asyncTearDown = fixture_module.ScriptAPITests.asyncTearDown
    fake_tags = fixture_module.ScriptAPITests.fake_tags
    fake_show = fixture_module.ScriptAPITests.fake_show
    fake_chat = fixture_module.ScriptAPITests.fake_chat
    fake_unload = fixture_module.ScriptAPITests.fake_unload
    redirect_target = fixture_module.ScriptAPITests.redirect_target
    fake_free = fixture_module.ScriptAPITests.fake_free
    create = fixture_module.ScriptAPITests.create
    submit = fixture_module.ScriptAPITests.submit
    complete = fixture_module.ScriptAPITests.complete

    def reload(self):
        return ScriptwritingManager(web.Application(), self.root)

    async def test_completed_stream_preserves_result_and_full_input_after_restart(self):
        project = await self.create(content="原始稿：阿青在月台等待。", style="自然對話")
        result = await self.complete(await self.submit(project, action="rewrite", instructions="保留結尾。"))

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["content"], self.reply)
        self.assertEqual(result["input"]["project"], project)
        self.assertEqual(result["input"]["instructions"], "保留結尾。")
        archived = json.loads((self.manager.history_directory / f"{result['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(archived, fixture_module.stored_record(result))
        self.assertEqual(self.reload().get_job(result["id"]), result)
        response = await self.client.get("/api/scripts/jobs")
        listing = await response.json()
        self.assertTrue(listing["history_persistent"])
        self.assertIsNone(listing["persistence_error"])
        self.assertEqual(listing["total"], 1)
        self.assertFalse(listing["has_more"])
        self.assertEqual(listing["active_items"], [])
        self.assertNotIn("input", listing["items"][0])

    async def test_failed_stream_keeps_partial_content_and_input_after_restart(self):
        self.mode = "stream_error"
        project = await self.create()
        result = await self.complete(await self.submit(project, instructions="保留人物口吻。"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["content"], self.reply[:9])
        self.assertIn("out of memory", result["error"])
        self.assertEqual(self.reload().get_job(result["id"]), result)
        self.assertEqual(result["input"]["project"], project)
        self.assertEqual(result["input"]["instructions"], "保留人物口吻。")

    async def test_real_running_stream_restarts_with_saved_partial_draft(self):
        self.mode = "hold"
        job = await self.submit(await self.create())
        await asyncio.wait_for(self.started.wait(), 3)
        for _ in range(100):
            if self.manager.jobs[job["id"]]["content"]:
                break
            await asyncio.sleep(0.005)
        original = copy.deepcopy(self.manager.jobs[job["id"]])
        self.assertEqual(original["status"], "running")
        self.assertEqual(original["content"], self.reply[:9])
        # Persist a streaming checkpoint, as the periodic stream saver does.
        self.manager._save_jobs(job["id"])

        restored = self.reload().get_job(job["id"])

        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(restored["content"], original["content"])
        self.assertEqual(restored["input"], original["input"])
        self.assertTrue(restored["error"])
        self.finish.set()
        await self.complete(job)

    async def test_delete_survives_restart_and_stale_legacy_index(self):
        result = await self.complete(await self.submit(await self.create()))
        project_bytes = self.manager.projects_path.read_bytes()
        response = await self.client.delete(f"/api/scripts/jobs/{result['id']}")

        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(await response.json(), {"deleted": True, "id": result["id"]})
        self.assertEqual(self.manager.projects_path.read_bytes(), project_bytes)
        self.assertEqual((await self.client.get(f"/api/scripts/jobs/{result['id']}")).status, 404)
        self.assertNotIn(result["id"], self.reload().jobs)
        tombstone = json.loads((self.manager.history_directory / f"{result['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(tombstone["id"], result["id"])
        self.assertTrue(tombstone["deleted"])
        self.assertTrue(tombstone["deleted_at"])
        write_json(self.manager.jobs_path, [result])
        self.assertNotIn(result["id"], self.reload().jobs)
        self.assertEqual((await self.client.delete(f"/api/scripts/jobs/{result['id']}")).status, 404)

    async def test_delete_does_not_change_imported_shortfilm_or_project(self):
        self.reply = json.dumps(STORYBOARD, ensure_ascii=False)
        project = await self.create(duration=5)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result.get("error"))
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 201, await response.text())
        before = {path.relative_to(self.root): path.read_bytes()
                  for folder in (self.root / "shortfilms",)
                  for path in folder.rglob("*") if path.is_file()}
        self.assertTrue(before)
        project_bytes = self.manager.projects_path.read_bytes()

        response = await self.client.delete(f"/api/scripts/jobs/{result['id']}")

        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.manager.projects_path.read_bytes(), project_bytes)
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in (self.root / "shortfilms").rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    async def test_queued_and_running_deletions_are_rejected(self):
        project = await self.create()
        for number, status in ((1, "queued"), (2, "running")):
            job = history_job(number, status=status, project_id=project["id"])
            self.manager.jobs[job["id"]] = job
            self.manager._save_jobs(job["id"])
            with self.subTest(status=status):
                response = await self.client.delete(f"/api/scripts/jobs/{job['id']}")
                self.assertEqual(response.status, 409, await response.text())
                self.assertEqual(fixture_module.stored_record(self.manager.get_job(job["id"])), job)

    async def test_completed_job_cannot_be_deleted_while_model_unload_is_running(self):
        self.mode = "unload_hold"
        job = await self.submit(await self.create())
        await asyncio.wait_for(self.unload_started.wait(), 3)
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "completed")
        self.assertFalse(self.manager.tasks[job["id"]].done())

        response = await self.client.delete(f"/api/scripts/jobs/{job['id']}")

        self.assertEqual(response.status, 409, await response.text())
        self.assertIn(job["id"], self.manager.jobs)
        self.finish.set()
        await self.complete(job)
        response = await self.client.delete(f"/api/scripts/jobs/{job['id']}")
        self.assertEqual(response.status, 200, await response.text())

    async def test_tombstone_write_failure_keeps_history_and_in_memory_result(self):
        result = await self.complete(await self.submit(await self.create()))
        archive = self.manager.history_directory / f"{result['id']}.json"
        original_archive = archive.read_bytes()
        original_index = self.manager.jobs_path.read_bytes()
        atomic = scriptwriting._atomic_json

        def fail_tombstone(path, value):
            if Path(path) == archive and value.get("deleted"):
                raise OSError("test: read-only archive")
            return atomic(path, value)

        with patch("scriptwriting._atomic_json", side_effect=fail_tombstone):
            response = await self.client.delete(f"/api/scripts/jobs/{result['id']}")

        self.assertEqual(response.status, 500, await response.text())
        self.assertEqual(self.manager.get_job(result["id"]), result)
        self.assertEqual(archive.read_bytes(), original_archive)
        self.assertEqual(self.manager.jobs_path.read_bytes(), original_index)
        self.assertEqual(self.reload().get_job(result["id"]), result)

    async def test_committed_delete_survives_index_failure_with_reported_warning(self):
        result = await self.complete(await self.submit(await self.create()))
        atomic = scriptwriting._atomic_json

        def fail_index(path, value):
            if Path(path) == self.manager.jobs_path:
                raise OSError("test: index volume full")
            return atomic(path, value)

        with patch("scriptwriting._atomic_json", side_effect=fail_index):
            response = await self.client.delete(f"/api/scripts/jobs/{result['id']}")
            self.assertEqual(response.status, 200, await response.text())
            listing = await (await self.client.get("/api/scripts/jobs")).json()

        self.assertTrue(listing["history_persistent"])
        self.assertIsInstance(listing["persistence_error"], str)
        self.assertTrue(listing["persistence_error"])
        self.assertEqual(listing["items"], [])
        self.assertNotIn(result["id"], self.reload().jobs)

    async def test_history_pagination_preserves_all_items_and_global_active_jobs(self):
        project = await self.create()
        other_project = await self.create(title="另一部測試短片")
        records = [history_job(number, project_id=project["id"]) for number in range(1, 5)]
        active = history_job(5, status="queued", project_id=other_project["id"])
        for record in [*records, active]:
            self.manager.jobs[record["id"]] = record
        self.manager._save_jobs()

        listing = await (await self.client.get("/api/scripts/jobs")).json()
        self.assertEqual(listing["total"], 5)
        self.assertEqual(len(listing["items"]), 5)
        self.assertFalse(listing["has_more"])
        response = await self.client.get("/api/scripts/jobs", params={"project_id": project["id"], "limit": "2", "offset": "1"})
        self.assertEqual(response.status, 200, await response.text())
        page = await response.json()
        self.assertEqual(page["total"], 4)
        self.assertEqual(page["offset"], 1)
        self.assertEqual(page["limit"], 2)
        self.assertEqual([item["id"] for item in page["items"]], [records[2]["id"], records[1]["id"]])
        self.assertTrue(page["has_more"])
        self.assertEqual([item["id"] for item in page["active_items"]], [active["id"]])
        self.assertTrue(all("input" not in item for item in [*page["items"], *page["active_items"]]))
        last = await (await self.client.get("/api/scripts/jobs", params={"project_id": project["id"], "limit": "2", "offset": "3"})).json()
        self.assertEqual([item["id"] for item in last["items"]], [records[0]["id"]])
        self.assertFalse(last["has_more"])

    async def test_invalid_pagination_does_not_mutate_history(self):
        for query in ({"limit": "0"}, {"limit": "201"}, {"limit": "nope"},
                      {"offset": "-1"}, {"offset": "1.5"}, {"project_id": "../x"}):
            with self.subTest(query=query):
                response = await self.client.get("/api/scripts/jobs", params=query)
                self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.manager.jobs, {})


if __name__ == "__main__":
    unittest.main()
