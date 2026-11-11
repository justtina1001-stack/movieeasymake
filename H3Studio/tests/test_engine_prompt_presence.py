"""Reliable missing-prompt detection without generation retries or GPU work."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

import app as studio
from comfy_client import ComfyClient, ComfyJobMissingError
from domain import compile_request
from engine_prompt_presence import EnginePromptPresenceGuard
from settings import ConnectionSettings


def queue(*, running=(), pending=()):
    return {"available": True, "running_count": len(running), "pending_count": len(pending),
            "running": [{"prompt_id": value, "position": 0} for value in running],
            "pending": [{"prompt_id": value, "position": index} for index, value in enumerate(pending, 1)]}


EMPTY_HISTORY = {"available": True, "history": {}}


class PresenceGuardTests(unittest.TestCase):
    def test_absence_requires_both_grace_and_multiple_reliable_checks(self):
        now = [0.0]
        guard = EnginePromptPresenceGuard("mine", clock=lambda: now[0])
        self.assertEqual(guard.observe(EMPTY_HISTORY, queue()), "monitoring")
        now[0] = 100
        self.assertEqual(guard.observe(EMPTY_HISTORY, queue()), "monitoring")
        self.assertEqual(guard.observe(EMPTY_HISTORY, queue()), "engine_missing")
        with self.assertRaises(ComfyJobMissingError):
            guard.raise_if_missing()

    def test_real_queued_or_running_work_has_no_total_runtime_limit(self):
        now = [0.0]
        guard = EnginePromptPresenceGuard("mine", clock=lambda: now[0])
        for now[0] in (0, 100, 1000000):
            for packet in (queue(running=("mine",)), queue(pending=("mine",))):
                self.assertEqual(guard.observe(EMPTY_HISTORY, packet), "present")
                self.assertEqual(guard.missing_checks, 0)

    def test_unknown_queries_and_redacted_rows_reset_absence_evidence(self):
        now = [0.0]
        guard = EnginePromptPresenceGuard("mine", clock=lambda: now[0])
        for unknown_history, packet in (({"available": False, "history": {}}, queue()),
                                        (EMPTY_HISTORY, {"available": False}),
                                        (EMPTY_HISTORY, queue(running=(None,))),
                                        (EMPTY_HISTORY, {**queue(), "pending_count": 2})):
            guard.observe(EMPTY_HISTORY, queue())
            now[0] += 100
            self.assertEqual(guard.observe(unknown_history, packet), "unknown")
            self.assertIsNone(guard.missing_since)
            self.assertEqual(guard.observe(EMPTY_HISTORY, queue()), "monitoring")
            self.assertEqual(guard.missing_checks, 1)

    def test_explicit_own_progress_or_known_history_clears_missing_streak(self):
        guard = EnginePromptPresenceGuard("mine", grace_seconds=0)
        guard.observe(EMPTY_HISTORY, queue())
        self.assertEqual(guard.mark_present(), "present")
        self.assertEqual(guard.missing_checks, 0)
        self.assertEqual(guard.observe({"available": True, "history": {"status": {"completed": False}}}, queue()), "present")


class PresenceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.body, self.status, self.delay = {}, 200, 0
        self.prompt_calls, self.queue_calls, self.history_calls = 0, 0, 0
        self.history_after = None
        self.queue_body = {"queue_running": [], "queue_pending": []}
        self.closing = False
        self.socket_payload = {"type": "status", "data": {"prompt_id": "other"}}
        server_app = web.Application()
        server_app.router.add_get("/history/{prompt}", self.history)
        server_app.router.add_get("/queue", self.queued)
        server_app.router.add_post("/prompt", self.submit)
        server_app.router.add_get("/ws", self.socket)
        self.server = TestServer(server_app)
        await self.server.start_server()
        self.client = ComfyClient(ConnectionSettings(mode="remote", base_url=str(self.server.make_url("/")),
            comfy_dir=self.temp.name, auto_start_local=False), Path(self.temp.name))
        self.client.interrupt = AsyncMock(return_value=True)

    async def asyncTearDown(self):
        self.closing = True
        await self.server.close()

    async def history(self, _request):
        self.history_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        body = (self.history_after[1] if self.history_after and self.history_calls >= self.history_after[0]
                else self.body)
        if self.status == 302:
            return web.Response(status=302, headers={"Location": "/queue"})
        return web.json_response(body, status=self.status)

    async def queued(self, _request):
        self.queue_calls += 1
        return web.json_response(self.queue_body)

    async def submit(self, _request):
        self.prompt_calls += 1
        return web.json_response({"prompt_id": "mine"})

    async def socket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async def send_status():
            try:
                while not self.closing and not ws.closed:
                    # Other jobs can keep a shared websocket busy continuously.
                    await ws.send_json(self.socket_payload)
                    await asyncio.sleep(.003)
            except (ConnectionError, RuntimeError):
                pass
        sender = asyncio.create_task(send_status())
        try:
            # Receive close frames so the client can finish its handshake.
            async for _ in ws:
                pass
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        return ws

    async def test_reliable_empty_and_invalid_http_results_are_distinct(self):
        self.assertEqual(await self.client.get_history_snapshot("mine"), EMPTY_HISTORY)
        for status, body in ((503, {}), (401, {}), (302, {}), (200, []), (200, {"mine": []}),
                             (200, {"mine": {"error": "not a history"}})):
            self.status, self.body = status, body
            with self.subTest(status=status, body=body):
                snapshot = await self.client.get_history_snapshot("mine")
                self.assertFalse(snapshot["available"])
                self.assertEqual(snapshot["history"], {})
        self.assertEqual(self.queue_calls, 0, "redirects must not manufacture reliable history")

    async def test_snapshot_timeout_is_unknown_and_bounded(self):
        self.delay = .15
        with patch("comfy_client.HISTORY_QUERY_TIMEOUT", .015):
            self.assertFalse((await self.client.get_history_snapshot("mine"))["available"])

    async def test_terminal_history_preserves_legacy_dict_contract(self):
        record = {"status": {"status_str": "success", "completed": True}, "outputs": {}}
        self.body = {"mine": record}
        self.assertEqual(await self.client.get_history("mine"), record)
        self.assertEqual(await self.client.get_history_snapshot("mine"), {"available": True, "history": record})

    def fast_checks(self):
        self.enterContext(patch("comfy_client.PROMPT_PRESENCE_INTERVAL", .01))
        self.enterContext(patch("engine_prompt_presence.MISSING_GRACE_SECONDS", .04))

    async def test_foreign_websocket_traffic_cannot_keep_lost_work_watching_forever(self):
        self.fast_checks()
        events = []
        with self.assertRaises(ComfyJobMissingError):
            await asyncio.wait_for(self.client.run_prompt({}, AsyncMock(side_effect=events.append), asyncio.Event()), 2)
        self.assertEqual(self.prompt_calls, 1)
        self.assertGreaterEqual(self.queue_calls, 3)
        self.assertEqual(events[-1]["recovery_state"], "engine_missing")
        self.client.interrupt.assert_not_awaited()

    async def test_queue_present_and_unknown_history_are_not_interrupted(self):
        self.fast_checks()
        for history_status in (200, 503):
            self.status = history_status
            self.queue_body = {"queue_running": [[0, "mine"]], "queue_pending": []}
            cancel = asyncio.Event()
            task = asyncio.create_task(self.client.run_prompt({}, AsyncMock(), cancel))
            await asyncio.sleep(.12)
            self.assertFalse(task.done())
            cancel.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)

    async def test_temporary_absence_can_complete_without_resubmission(self):
        self.fast_checks()
        self.history_after = (3, {"mine": {"status": {"status_str": "success", "completed": True}, "outputs": {}}})
        prompt_id, history = await asyncio.wait_for(self.client.run_prompt({}, AsyncMock(), asyncio.Event()), 2)
        self.assertEqual(prompt_id, "mine")
        self.assertEqual(self.client.history_state(history), "success")
        self.assertEqual(self.prompt_calls, 1)
        self.client.interrupt.assert_not_awaited()

    async def test_only_matching_prompt_progress_clears_stale_progress(self):
        self.fast_checks()
        self.socket_payload = {"type": "progress", "data": {"prompt_id": "mine", "value": 2, "max": 10}}
        self.history_after = (3, {"mine": {"status": {"status_str": "success", "completed": True}, "outputs": {}}})
        events = []
        await asyncio.wait_for(self.client.run_prompt({}, AsyncMock(side_effect=events.append), asyncio.Event()), 2)
        actual_progress = [event for event in events if event.get("progress") == 20]
        self.assertTrue(actual_progress)
        self.assertTrue(all(event.get("progress_is_stale") is False for event in actual_progress))


class RecoveryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.multiple(studio, JOB_DIR=self.root / "jobs", OUTPUT_DIR=self.root / "outputs"))
        self.enterContext(patch("app.RECOVERY_POLL_INTERVAL", .005))
        self.enterContext(patch("engine_prompt_presence.MISSING_GRACE_SECONDS", .025))
        self.engine = SimpleNamespace(get_history_snapshot=AsyncMock(return_value=EMPTY_HISTORY),
            queue_status=AsyncMock(return_value=queue()), history_state=ComfyClient.history_state,
            run_prompt=AsyncMock(), interrupt=AsyncMock())
        self.manager = studio.JobManager(studio.AssetStore(self.root / "assets"), self.engine)
        self.identifier = "a" * 32
        self.manager.jobs[self.identifier] = {"id": self.identifier, "status": "interrupted", "prompt_id": "mine",
            "progress": 45, "generation_started_at": "2026-11-04T11:11:00+08:00"}

    async def test_lost_recovery_stops_with_explicit_interrupted_reason(self):
        request = self.root / "jobs" / f"{self.identifier}.request.json"
        request.write_bytes(b'{"seed":123}')
        self.assertFalse(await asyncio.wait_for(self.manager.reconcile_job(self.identifier, wait=True), 1))
        job = self.manager.jobs[self.identifier]
        self.assertEqual((job["status"], job["error_code"], job["recovery_state"]),
                         ("interrupted", "engine_job_missing", "engine_missing"))
        self.assertIsNone(job["execution_seconds"])
        self.assertEqual(job["progress"], 45)
        self.assertEqual(request.read_bytes(), b'{"seed":123}')
        self.engine.run_prompt.assert_not_awaited()
        self.engine.interrupt.assert_not_awaited()

    async def test_missing_job_is_not_watched_again_on_service_restart(self):
        self.manager.jobs[self.identifier].update(error_code="engine_job_missing")
        batch_id = "b" * 32
        self.manager.jobs[batch_id] = {"id": batch_id, "batch_type": "replace_long", "status": "interrupted",
                                     "error_code": "engine_job_missing"}
        self.manager._recover = AsyncMock()
        self.manager._run_replacement_batch = AsyncMock()
        self.manager.resume_recoverable_jobs()
        self.assertEqual(self.manager.tasks, {})
        self.manager._recover.assert_not_called()
        self.manager._run_replacement_batch.assert_not_called()

    async def test_service_task_stop_is_not_user_cancellation(self):
        self.engine.get_history_snapshot.return_value = {"available": False, "history": {}}
        task = asyncio.create_task(self.manager._recover(self.identifier))
        await asyncio.sleep(.02)
        task.cancel()
        await task
        job = self.manager.jobs[self.identifier]
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["error_code"], "service_interrupted")
        self.assertEqual(job["recovery_state"], "unknown")
        self.engine.interrupt.assert_not_awaited()

    async def test_recovery_of_active_prompt_preserves_but_marks_old_progress_stale(self):
        self.engine.queue_status.return_value = queue(running=("mine",))
        task = asyncio.create_task(self.manager._recover(self.identifier))
        await asyncio.sleep(.04)
        self.assertFalse(task.done())
        job = self.manager.jobs[self.identifier]
        self.assertEqual((job["status"], job["recovery_state"], job["progress"]), ("running", "present", 45))
        self.assertTrue(job["progress_is_stale"])
        self.engine.run_prompt.assert_not_awaited()
        self.engine.interrupt.assert_not_awaited()
        task.cancel()
        await task

    async def test_completed_job_clears_recovery_and_stale_progress(self):
        self.manager.jobs[self.identifier].update(progress_is_stale=True, recovery_state="present")
        self.manager.cache_output = AsyncMock(return_value=self.root / "clip.mp4")
        raw = {"mode": "t2v", "prompt": "A test orb.", "duration": 5, "seed": 123, "megapixels": .25}
        await self.manager.complete_job(self.identifier, compile_request(raw), "mine", {"filename": "clip.mp4"})
        job = self.manager.jobs[self.identifier]
        self.assertFalse(job["progress_is_stale"])
        self.assertIsNone(job["recovery_state"])

    async def test_user_cancelled_job_remains_cancelled_when_watch_task_stops(self):
        self.engine.get_history_snapshot.return_value = {"available": False, "history": {}}
        task = asyncio.create_task(self.manager._recover(self.identifier))
        await asyncio.sleep(.01)
        await self.manager.cancel(self.identifier)
        task.cancel()
        await task
        self.assertEqual(self.manager.jobs[self.identifier]["status"], "cancelled")

    async def test_lost_replacement_child_is_terminal_for_parent_waiter(self):
        self.manager.jobs[self.identifier].update(error_code="engine_job_missing")
        parent_id = "b" * 32
        self.manager.jobs[parent_id] = {"id": parent_id, "segments": [{"child_job_id": self.identifier}]}
        self.manager.cancel_events[parent_id] = asyncio.Event()
        self.manager.reconcile_job = AsyncMock(side_effect=AssertionError("must not rewatch lost child"))
        child = await asyncio.wait_for(self.manager._wait_for_batch_child(parent_id, 0, self.identifier), .1)
        self.assertEqual(child["id"], self.identifier)

    async def test_new_generation_missing_error_is_interrupted_without_retry(self):
        self.engine.ensure_running = AsyncMock()
        self.engine.model_inventory = AsyncMock(return_value={"fl2va": True, "text_encoder": True,
                                                             "video_vae": True, "audio_vae": True})
        async def lost(_workflow, callback, _cancel):
            await callback({"prompt_id": "mine", "status": "running", "progress": 45})
            raise ComfyJobMissingError(3, 90)
        self.engine.run_prompt.side_effect = lost
        raw = {"mode": "t2v", "prompt": "A test orb.", "duration": 5, "seed": 123, "megapixels": .25}
        job = self.manager.create(compile_request(raw), raw)
        await self.manager.tasks[job["id"]]
        self.assertEqual((job["status"], job["error_code"]), ("interrupted", "engine_job_missing"))
        self.assertEqual(self.engine.run_prompt.await_count, 1)
        self.engine.interrupt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
