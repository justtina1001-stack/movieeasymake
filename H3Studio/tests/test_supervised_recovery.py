"""A watchdog recovery observes accepted work without restarting GPU engines."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import app as studio


class SupervisedRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_only_monitors_existing_prompt_ids(self):
        manager = object.__new__(studio.JobManager)
        manager.jobs = {
            "accepted": {"status": "interrupted", "prompt_id": "already-submitted"},
            "batch": {"status": "interrupted", "batch_type": "replace_long"},
            "queued": {"status": "interrupted"},
        }
        manager.tasks, manager.cancel_events = {}, {}
        manager._recover = AsyncMock()
        manager._run_replacement_batch = AsyncMock()
        manager.resume_recoverable_jobs(existing_prompts_only=True)
        await asyncio.gather(*manager.tasks.values())
        manager._recover.assert_awaited_once_with("accepted")
        manager._run_replacement_batch.assert_not_awaited()
        self.assertEqual(set(manager.tasks), {"accepted"})
        self.assertEqual(manager.jobs["batch"]["status"], "interrupted")

    async def check_startup(self, recovery):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.multiple(studio, DATA_DIR=root, ASSET_DIR=root / "assets", JOB_DIR=root / "jobs",
                               OUTPUT_DIR=root / "outputs", CONFIG_PATH=root / "config.json"),
                patch.dict(os.environ, {"H3_STUDIO_SUPERVISED_RECOVERY": "1" if recovery else "0"}),
            ):
                application = studio.create_app()
                engine, jobs = application["comfy"], application["jobs"]
                callback = next(item for item in application.on_startup if item.__name__ == "auto_start_engine")
                with (
                    patch.object(type(engine), "can_start", new_callable=PropertyMock, return_value=True),
                    patch.object(engine, "ensure_running", new=AsyncMock()) as ensure,
                    patch.object(jobs, "resume_recoverable_jobs", new=Mock()) as resume,
                    patch.object(jobs, "backfill_completed_timings", new=AsyncMock()),
                ):
                    await callback(application)
                    await application["comfy_autostart_task"]
                    if recovery:
                        ensure.assert_not_awaited()
                        resume.assert_called_once_with(existing_prompts_only=True)
                    else:
                        ensure.assert_awaited_once()
                        resume.assert_called_once_with()

    async def test_supervised_restart_does_not_start_engine_or_dispatch_batches(self):
        await self.check_startup(True)

    async def test_normal_launch_retains_existing_engine_startup(self):
        await self.check_startup(False)


if __name__ == "__main__":
    unittest.main()
