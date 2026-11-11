"""Preparation counters must never become whole-video completion."""
import asyncio
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from comfy_client import ComfyClient
from engine_prompt_presence import ComfyJobMissingError
from settings import ConnectionSettings


WORKFLOW = {"1": {"class_type": "FreeVideoMedia", "inputs": {}},
            "2": {"class_type": "FreeVideoGenerate", "inputs": {}},
            "3": {"class_type": "SaveVideo", "inputs": {}}}


class FreeVideoProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.events = []
        self.messages = []
        self.repeat, self.finished, self.closing = False, False, False
        self.complete_when = lambda event: event.get("freevideo_phase") == "saving"
        self.queue_running = [[0, "mine"]]
        self.submissions = 0
        self.enterContext(patch("comfy_client.PROMPT_PRESENCE_INTERVAL", .008))
        app = web.Application()
        app.router.add_post("/prompt", self.submit)
        app.router.add_get("/history/{prompt_id}", self.history)
        app.router.add_get("/queue", self.queue)
        app.router.add_get("/ws", self.socket)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(ConnectionSettings(mode="remote", base_url=str(self.server.make_url("/")),
                                                     auto_start_local=False), Path(self.temporary.name))
        self.client.interrupt = AsyncMock()

    async def asyncTearDown(self):
        self.closing = True
        await self.server.close()
        self.temporary.cleanup()

    async def submit(self, request):
        self.submissions += 1
        return web.json_response({"prompt_id": "mine"})

    async def history(self, request):
        return web.json_response({"mine": {"status": {"completed": True, "status_str": "success"}, "outputs": {}}}
                                 if self.finished else {})

    async def queue(self, request):
        return web.json_response({"queue_running": self.queue_running, "queue_pending": []})

    async def socket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async def send():
            try:
                while not self.closing and not ws.closed:
                    for event in self.messages:
                        await ws.send_json(event)
                    if not self.repeat:
                        return
                    await asyncio.sleep(.002)
            except (ConnectionError, RuntimeError):
                pass
        task = asyncio.create_task(send())
        try:
            async for _ in ws:
                pass
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return ws

    async def run_video(self, workflow=WORKFLOW):
        async def record(event):
            self.events.append(event)
            if not self.repeat and self.complete_when(event):
                self.finished = True
        return await asyncio.wait_for(self.client.run_prompt(workflow, record, asyncio.Event()), 3)

    async def test_preparation_one_hundred_percent_never_marks_whole_video_done(self):
        self.messages = [
            {"type": "executing", "data": {"prompt_id": "mine", "node": "1"}},
            {"type": "progress", "data": {"prompt_id": "mine", "node": "1", "value": 124, "max": 124}},
            {"type": "executing", "data": {"prompt_id": "mine", "node": "2"}},
            {"type": "progress", "data": {"prompt_id": "mine", "node": "2", "value": 50, "max": 50}},
            {"type": "freevideo_progress", "data": {"node": "2", "phase": "encoding", "done": 100, "total": 100, "label": "anonymous"}},
            {"type": "freevideo_progress", "data": {"prompt_id": "other", "node": "2", "phase": "sampling", "label": "foreign"}},
            {"type": "freevideo_progress", "data": {"prompt_id": "mine", "node": "99", "phase": "sampling", "label": "wrong node"}},
            {"type": "freevideo_progress", "data": {"prompt_id": "mine", "node": "2", "phase": "encoding", "label": "Encoding references", "done": 124, "total": 124}},
            {"type": "freevideo_progress", "data": {"prompt_id": "mine", "node": "2", "phase": "sampling", "label": "Sampling", "done": 2, "total": 8}},
            {"type": "freevideo_progress", "data": {"prompt_id": "mine", "node": "2", "phase": "complete", "label": "Saving result", "done": 1, "total": 1, "overall": {"display_fraction": 1}}},
            {"type": "executing", "data": {"prompt_id": "mine", "node": "3"}},
        ]
        prompt_id, history = await self.run_video()
        self.assertEqual(prompt_id, "mine")
        self.assertEqual(self.client.history_state(history), "success")
        self.assertTrue(all(event.get("progress") is None for event in self.events if "progress" in event))
        self.assertTrue(all(event.get("status") != "completed" for event in self.events))
        labels = "\n".join(str(event.get("current_node") or "") for event in self.events)
        self.assertIn("素材編碼、採樣與解碼", labels)
        self.assertIn("儲存 FreeVideo", labels)
        self.assertIn("階段進度 100%", labels)
        self.assertNotIn("anonymous", labels)
        self.assertNotIn("foreign", labels)
        self.assertNotIn("wrong node", labels)
        phases = [event for event in self.events if event.get("freevideo_phase") == "sampling"]
        self.assertEqual(phases[0]["phase_progress"], 25)
        self.assertIs(phases[0]["progress_is_stale"], False)
        self.assertEqual(self.submissions, 1)
        self.client.interrupt.assert_not_awaited()

    async def test_native_h3_retains_its_standard_sampling_percentage(self):
        self.complete_when = lambda event: event.get("progress") == 20
        self.messages = [{"type": "progress", "data": {"prompt_id": "mine", "node": "9", "value": 2, "max": 10}}]
        await self.run_video({"9": {"class_type": "SamplerCustomAdvanced", "inputs": {}}})
        self.assertTrue(any(event.get("progress") == 20 for event in self.events))

    async def test_anonymous_and_foreign_custom_traffic_cannot_reset_lost_prompt_detection(self):
        self.repeat = True
        self.queue_running = []
        self.messages = [{"type": "freevideo_progress", "data": {"node": "2", "phase": "sampling", "done": 1, "total": 1}},
                         {"type": "freevideo_progress", "data": {"prompt_id": "other", "node": "2", "phase": "sampling"}}]
        with patch("engine_prompt_presence.MISSING_GRACE_SECONDS", .035), self.assertRaises(ComfyJobMissingError):
            await self.run_video()
        self.assertFalse(any(event.get("freevideo_phase") == "sampling" for event in self.events))
        self.assertEqual(self.submissions, 1)
        self.client.interrupt.assert_not_awaited()

    def test_invalid_phase_counters_never_produce_invalid_or_overall_progress(self):
        for done, total in ((True, 1), (1, False), (math.nan, 1), (1, math.inf), (1, 0), (-1, 8), (10**1000, 1)):
            with self.subTest(done=str(done)[:20], total=total):
                fields = self.client.freevideo_progress_fields({"prompt_id": "mine", "node": "2", "done": done, "total": total}, "mine", {"2"})
                self.assertIsNone(fields["progress"])
                self.assertIsNone(fields["phase_progress"])


if __name__ == "__main__":
    unittest.main()
