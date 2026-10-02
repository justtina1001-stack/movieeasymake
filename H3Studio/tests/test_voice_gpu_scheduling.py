import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from comfy_client import ComfyClient
from settings import ConnectionSettings
from shared_gateway import GatewayError, SharedComfyGateway
from voice import VoiceInstaller, VoiceJobManager


def queue_snapshot(running=0, pending=0, available=True):
    return {"available": available, "running_count": running, "pending_count": pending,
            "running": [], "pending": [], "error": None}


class GatewayVoiceGPUSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.gpu_lock = asyncio.Lock()
        self.gateway = SharedComfyGateway(self.root, voice_jobs=SimpleNamespace(gpu_lock=self.gpu_lock))
        self.user, self.token = self.gateway.store.create_user("Test colleague")
        self.forwarded = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

        async def prompt(request):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            payload = await request.json()
            self.forwarded.append(payload)
            number = len(self.forwarded)
            self.entered.set()
            try:
                await self.release.wait()
                return web.json_response({"prompt_id": f"accepted-{number}"})
            finally:
                self.in_flight -= 1

        upstream = web.Application()
        upstream.router.add_post("/prompt", prompt)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.gateway.store.config["upstream_url"] = str(self.upstream.make_url("/")).rstrip("/")
        self.http = TestClient(TestServer(self.gateway.create_app()))
        await self.http.start_server()

    async def asyncTearDown(self):
        self.release.set()
        await self.http.close()
        await self.upstream.close()
        self.temporary.cleanup()

    async def submit(self):
        return await self.http.post("/prompt", json={"prompt": {}, "client_id": "same-client"},
                                    headers={"Authorization": f"Bearer {self.token}"})

    async def test_busy_host_does_not_accept_a_prompt_and_unlock_allows_submission(self):
        await self.gpu_lock.acquire()
        try:
            response = await self.submit()
            self.assertEqual(response.status, 409)
            result = await response.json()
            self.assertEqual(result["code"], "host_gpu_busy")
            self.assertTrue(result["retryable"])
            self.assertEqual(self.forwarded, [])
            self.assertEqual(self.gateway.store.state["prompt_owners"], {})
        finally:
            self.gpu_lock.release()
        response = await self.submit()
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(len(self.forwarded), 1)
        self.assertTrue(self.gateway.store.owns_prompt(result["prompt_id"], self.user["id"]))

    async def test_gateway_serializes_submissions_until_each_acceptance_finishes(self):
        self.release.clear()
        first = asyncio.create_task(self.submit())
        await asyncio.wait_for(self.entered.wait(), 5)
        second = asyncio.create_task(self.submit())
        try:
            await asyncio.sleep(.02)
            self.assertTrue(self.gateway.submit_lock.locked())
            self.assertEqual(len(self.forwarded), 1)
        finally:
            self.release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 5)
        self.assertEqual([response.status for response in results], [200, 200])
        self.assertEqual(len(self.forwarded), 2)
        self.assertEqual(self.max_in_flight, 1)

    async def test_voice_waits_for_existing_running_or_pending_comfy_jobs(self):
        for counts in ((1, 0), (0, 2)):
            with self.subTest(counts=counts):
                self.gateway.store.config["enabled"] = True
                snapshots = AsyncMock(side_effect=[queue_snapshot(*counts), queue_snapshot()])
                with patch("shared_gateway.fetch_queue_snapshot", snapshots):
                    await asyncio.wait_for(self.gateway.wait_for_engine_idle(asyncio.Event()), 5)
                self.assertEqual(snapshots.await_count, 2)

    async def test_running_gateway_checks_queue_even_if_config_was_disabled(self):
        self.gateway.runner = object()
        try:
            fetch = AsyncMock(return_value=queue_snapshot())
            with patch("shared_gateway.fetch_queue_snapshot", fetch):
                await self.gateway.wait_for_engine_idle(asyncio.Event())
            fetch.assert_awaited_once()
        finally:
            self.gateway.runner = None

    async def test_voice_waits_for_in_flight_prompt_acceptance_before_checking_idle(self):
        self.gateway.store.config["enabled"] = True
        await self.gateway.submit_lock.acquire()
        fetch = AsyncMock(return_value=queue_snapshot())
        with patch("shared_gateway.fetch_queue_snapshot", fetch):
            waiting = asyncio.create_task(self.gateway.wait_for_engine_idle(asyncio.Event()))
            try:
                await asyncio.sleep(0)
                fetch.assert_not_awaited()
                self.assertFalse(waiting.done())
            finally:
                self.gateway.submit_lock.release()
            await asyncio.wait_for(waiting, 5)
        fetch.assert_awaited_once()

    async def test_unavailable_queue_fails_closed(self):
        self.gateway.store.config["enabled"] = True
        fetch = AsyncMock(return_value=queue_snapshot(available=False))
        with patch("shared_gateway.fetch_queue_snapshot", fetch):
            with self.assertRaisesRegex(GatewayError, "無法確認"):
                await self.gateway.wait_for_engine_idle(asyncio.Event())
        fetch.assert_awaited_once()

    async def test_cancel_while_queue_is_busy_stops_waiting(self):
        self.gateway.store.config["enabled"] = True
        observed = asyncio.Event()

        async def busy(*_args, **_kwargs):
            observed.set()
            return queue_snapshot(1)

        cancel_event = asyncio.Event()
        fetch = AsyncMock(side_effect=busy)
        with patch("shared_gateway.fetch_queue_snapshot", fetch):
            waiting = asyncio.create_task(self.gateway.wait_for_engine_idle(cancel_event))
            await asyncio.wait_for(observed.wait(), 5)
            cancel_event.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(waiting, 5)
        self.assertEqual(fetch.await_count, 1)

    async def test_already_cancelled_does_not_fetch_queue(self):
        self.gateway.store.config["enabled"] = True
        cancel_event = asyncio.Event()
        cancel_event.set()
        fetch = AsyncMock(return_value=queue_snapshot())
        with patch("shared_gateway.fetch_queue_snapshot", fetch):
            with self.assertRaises(asyncio.CancelledError):
                await self.gateway.wait_for_engine_idle(cancel_event)
        fetch.assert_not_awaited()

    async def test_no_shared_gateway_does_not_require_comfy_for_local_voice(self):
        fetch = AsyncMock(side_effect=AssertionError("local voice does not require Comfy"))
        with patch("shared_gateway.fetch_queue_snapshot", fetch):
            await self.gateway.wait_for_engine_idle(asyncio.Event())
        fetch.assert_not_awaited()


class LocalVoicePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        installer = VoiceInstaller(self.root)
        installer.runtime_installed = lambda: True
        installer.model_installed = lambda _mode: True
        self.manager = VoiceJobManager(self.root, asyncio.Lock(), installer, lambda _id: self.root / "reference.wav")

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temporary.cleanup()

    async def test_preparation_callback_runs_under_gpu_lock_before_worker(self):
        order = []

        async def prepare(_cancel):
            self.assertTrue(self.manager.gpu_lock.locked())
            worker.assert_not_awaited()
            order.append("prepare")

        async def launch(*args, **_kwargs):
            order.append("worker")
            output = Path(args[args.index("--output") + 1])

            async def communicate():
                output.write_bytes(b"RIFF" + b"0" * 200)
                return b"", b""

            return SimpleNamespace(returncode=0, communicate=communicate)

        self.manager.before_local_generate = prepare
        worker = AsyncMock(side_effect=launch)
        with patch("voice.asyncio.create_subprocess_exec", worker):
            job = self.manager.create({"text": "主機旁白"})
            await asyncio.wait_for(self.manager.tasks[job["id"]], 5)
        self.assertEqual(order, ["prepare", "worker"])
        self.assertEqual(job["status"], "completed")
        self.assertFalse(self.manager.gpu_lock.locked())

    async def test_cancellation_during_preparation_does_not_start_worker(self):
        async def prepare(cancel_event):
            self.assertTrue(self.manager.gpu_lock.locked())
            cancel_event.set()

        self.manager.before_local_generate = prepare
        worker = AsyncMock(side_effect=AssertionError("cancelled jobs must not start a model"))
        with patch("voice.asyncio.create_subprocess_exec", worker):
            job = self.manager.create({"text": "取消的旁白"})
            await asyncio.wait_for(self.manager.tasks[job["id"]], 5)
        worker.assert_not_awaited()
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(list(self.manager.output_dir.iterdir()), [])
        self.assertFalse(self.manager.gpu_lock.locked())

    async def test_unconfirmed_engine_queue_never_starts_worker(self):
        self.manager.before_local_generate = AsyncMock(side_effect=GatewayError("無法確認主機佇列"))
        worker = AsyncMock(side_effect=AssertionError("unavailable queue must fail closed"))
        with patch("voice.asyncio.create_subprocess_exec", worker):
            job = self.manager.create({"text": "暫停的旁白"})
            await asyncio.wait_for(self.manager.tasks[job["id"]], 5)
        worker.assert_not_awaited()
        self.assertEqual(job["status"], "failed")
        self.assertIn("無法確認", job["error"])
        self.assertFalse(self.manager.gpu_lock.locked())


class ComfyHostBusyRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.requests = []
        self.response_status = 409
        self.response_body = {"code": "host_gpu_busy", "retryable": True, "error": "Host is generating voice"}
        self.busy_responses = 1

        async def prompt(request):
            self.requests.append(await request.json())
            if len(self.requests) <= self.busy_responses:
                return web.json_response(self.response_body, status=self.response_status)
            return web.json_response({"prompt_id": "accepted-prompt"})

        async def history(_):
            return web.json_response({"accepted-prompt": {
                "outputs": {}, "status": {"completed": True, "status_str": "success", "messages": []},
            }})

        app = web.Application()
        app.router.add_post("/prompt", prompt)
        app.router.add_get("/history/{id}", history)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(ConnectionSettings(mode="remote", base_url=str(self.server.make_url("/")),
                                                   remote_access_token="test-key", comfy_dir=self.temporary.name,
                                                   auto_start_local=False), Path(self.temporary.name))
        self.client.interrupt = AsyncMock()

    async def asyncTearDown(self):
        await self.server.close()
        self.temporary.cleanup()

    async def test_host_busy_not_accepted_response_reports_queued_and_retries_same_request(self):
        events = []

        async def progress(event):
            events.append(event)

        workflow = {"1": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "test"}}}
        prompt_id, history = await asyncio.wait_for(self.client.run_prompt(workflow, progress, asyncio.Event()), 5)
        self.assertEqual(prompt_id, "accepted-prompt")
        self.assertEqual(self.client.history_state(history), "success")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(events[0]["status"], "queued")
        self.assertNotIn("prompt_id", events[0])
        self.assertEqual(events[1]["prompt_id"], "accepted-prompt")
        self.client.interrupt.assert_not_awaited()

    async def test_cancel_during_busy_wait_does_not_interrupt_or_resubmit_any_prompt(self):
        cancel_event = asyncio.Event()

        async def progress(event):
            self.assertEqual(event["status"], "queued")
            cancel_event.set()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.client.run_prompt({}, progress, cancel_event), 5)
        self.assertEqual(len(self.requests), 1)
        self.client.interrupt.assert_not_awaited()

    async def test_already_cancelled_job_neither_submits_nor_interrupts(self):
        cancel_event = asyncio.Event()
        cancel_event.set()
        callback = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await self.client.run_prompt({}, callback, cancel_event)
        self.assertEqual(self.requests, [])
        callback.assert_not_awaited()
        self.client.interrupt.assert_not_awaited()

    async def test_an_ordinary_409_is_not_retried(self):
        self.response_body = {"error": "Model configuration conflict"}
        callback = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "Model configuration conflict"):
            await asyncio.wait_for(self.client.run_prompt({}, callback, asyncio.Event()), 5)
        self.assertEqual(len(self.requests), 1)
        callback.assert_not_awaited()
        self.client.interrupt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
