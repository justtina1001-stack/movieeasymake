"""Host-owned queue lifecycle uses synthetic jobs only; never runs inference."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from shortfilm import ShortFilmStore, flatten_shots, new_project, new_scene, new_shot
from shortfilm_batch import ShortFilmBatchError, ShortFilmBatchManager


class ShortFilmBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ShortFilmStore(self.root / "projects")
        project, scene = new_project("Synthetic film"), new_scene()
        scene["shots"] = [new_shot(index) for index in range(1, 4)]
        for shot in scene["shots"]:
            shot["action"] = "The actor waves."
        project["scenes"] = [scene]
        self.project = self.store.create(project)
        self.ids = [shot["id"] for _, shot in flatten_shots(self.project)]
        self.jobs = SimpleNamespace(jobs={}, tasks={}, cancel_calls=[])
        self.calls = []
        self.automatic = True

        async def submit(project_id, shot_id, snapshot):
            self.calls.append(shot_id)
            job = {"id": f"{len(self.calls):032x}", "status": "completed" if self.automatic else "running"}
            self.jobs.jobs[job["id"]] = job
            return copy.deepcopy(job)
        self.submit = submit
        self.app = {"shortfilms": self.store, "jobs": self.jobs}
        self.manager = ShortFilmBatchManager(self.app, self.root, self.submit, poll_interval=0.005)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.005)

    async def test_browser_independent_batch_completes_in_order_and_persists(self):
        batch = await self.manager.start(self.project["id"])
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, self.ids)
        completed = self.manager.get(batch["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["completed_shot_ids"], self.ids)
        loaded = ShortFilmBatchManager(self.app, self.root, self.submit)
        self.assertEqual(loaded.get(batch["id"])["status"], "completed")
        self.assertTrue(all(shot["status"] == "completed" for _, shot in flatten_shots(self.store.get(self.project["id"]))))

    async def test_cancel_retains_current_job_and_resume_does_not_duplicate_it(self):
        self.automatic = False
        batch = await self.manager.start(self.project["id"])
        await self.until(lambda: len(self.calls) == 1)
        await self.manager.cancel(batch["id"])
        self.assertEqual(self.manager.get(batch["id"])["status"], "cancelled")
        self.assertEqual(self.calls, self.ids[:1])
        self.assertEqual(self.jobs.jobs[f"{1:032x}"]["status"], "running")
        self.jobs.jobs[f"{1:032x}"]["status"] = "completed"
        self.automatic = True
        await self.manager.resume(batch["id"])
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, self.ids)

    async def test_shutdown_and_restart_pause_until_explicit_resume(self):
        self.automatic = False
        batch = await self.manager.start(self.project["id"])
        await self.until(lambda: len(self.calls) == 1)
        await self.manager.shutdown()
        self.manager = ShortFilmBatchManager(self.app, self.root, self.submit, poll_interval=0.005)
        self.assertEqual(self.manager.get(batch["id"])["status"], "interrupted")
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.calls), 1)
        self.jobs.jobs[f"{1:032x}"]["status"] = "completed"
        self.automatic = True
        await self.manager.resume(batch["id"])
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, self.ids)

    async def test_edit_drift_stops_submissions_and_preserves_new_project_content(self):
        self.automatic = False
        batch = await self.manager.start(self.project["id"])
        await self.until(lambda: len(self.calls) == 1)
        edited = self.store.get(self.project["id"])
        edited["scenes"][0]["shots"][1]["action"] = "A new user direction."
        self.store.update(edited["id"], edited)
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.manager.get(batch["id"])["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.get(edited["id"])["scenes"][0]["shots"][1]["action"], "A new user direction.")
        with self.assertRaises(ShortFilmBatchError):
            await self.manager.resume(batch["id"])

    async def test_failed_shot_retry_skips_completed_shots_and_duplicate_start_is_rejected(self):
        self.automatic = False
        batch = await self.manager.start(self.project["id"])
        await self.until(lambda: len(self.calls) == 1)
        with self.assertRaises(ShortFilmBatchError):
            await self.manager.start(self.project["id"])
        self.jobs.jobs[f"{1:032x}"]["status"] = "completed"
        await self.until(lambda: len(self.calls) == 2)
        self.jobs.jobs[f"{2:032x}"].update(status="failed", error="Synthetic failure")
        await self.manager.tasks[batch["id"]]
        self.automatic = True
        await self.manager.resume(batch["id"])
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, [self.ids[0], self.ids[1], self.ids[1], self.ids[2]])

    async def test_existing_active_job_is_waited_instead_of_submitted_again(self):
        project = self.store.get(self.project["id"])
        job_id = "e" * 32
        project["scenes"][0]["shots"][0].update(job_id=job_id, status="running")
        self.jobs.jobs[job_id] = {"id": job_id, "status": "running"}
        self.store.update(project["id"], project)
        batch = await self.manager.start(project["id"])
        await self.until(lambda: self.manager.get(batch["id"])["current_shot_id"] == self.ids[0])
        self.assertEqual(self.calls, [])
        self.jobs.jobs[job_id]["status"] = "completed"
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, self.ids[1:])

    async def test_new_earlier_render_invalidates_completed_continuation_descendants(self):
        project = self.store.get(self.project["id"])
        for _, shot in flatten_shots(project)[1:]:
            old_id = f"{100 + self.ids.index(shot['id']):032x}"
            self.jobs.jobs[old_id] = {"id": old_id, "status": "completed"}
            shot.update(continue_previous=True, job_id=old_id, status="completed", continuation_asset_id="e" * 32)
        self.store.update(project["id"], project)
        batch = await self.manager.start(project["id"])
        await self.manager.tasks[batch["id"]]
        self.assertEqual(self.calls, self.ids)


if __name__ == "__main__":
    unittest.main()
