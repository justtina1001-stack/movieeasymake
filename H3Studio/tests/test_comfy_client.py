import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import web

from comfy_client import ComfyClient
from settings import ConnectionSettings


class ComfyClientRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.history_calls = 0
        app = web.Application()
        app.router.add_post("/prompt", self.prompt)
        app.router.add_get("/ws", self.websocket)
        app.router.add_get("/history/{prompt_id}", self.history)
        app.router.add_post("/interrupt", self.interrupt)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.temporary = tempfile.TemporaryDirectory()
        settings = ConnectionSettings(
            mode="remote",
            base_url=f"http://127.0.0.1:{port}",
            comfy_dir=self.temporary.name,
            auto_start_local=False,
        )
        self.client = ComfyClient(settings, Path(self.temporary.name))

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temporary.cleanup()

    async def prompt(self, _request):
        return web.json_response({"prompt_id": "prompt-1"})

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()
        return ws

    async def history(self, _request):
        self.history_calls += 1
        if self.history_calls < 3:
            return web.json_response({})
        return web.json_response({
            "prompt-1": {
                "outputs": {"15": {"images": [{"filename": "done.mp4", "subfolder": "H3Studio", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        })

    async def interrupt(self, _request):
        return web.json_response({})

    async def test_closed_websocket_reconnects_and_uses_history_result(self):
        events = []

        async def progress(event):
            events.append(event)

        prompt_id, history = await asyncio.wait_for(
            self.client.run_prompt({}, progress, asyncio.Event()),
            timeout=8,
        )
        self.assertEqual(prompt_id, "prompt-1")
        self.assertEqual(self.client.history_state(history), "success")
        self.assertGreaterEqual(self.history_calls, 3)
        self.assertTrue(any("自動重連" in str(event.get("current_node")) for event in events))

    def test_history_error_extracts_comfy_exception(self):
        history = {
            "status": {
                "status_str": "error",
                "completed": True,
                "messages": [["execution_error", {"exception_message": "GPU out of memory"}]],
            }
        }
        self.assertEqual(self.client.history_state(history), "error")
        self.assertEqual(self.client.history_error(history), "GPU out of memory")

    def test_decodes_comfy_preview_binary_messages(self):
        jpeg = b"\xff\xd8preview"
        decoded = self.client.decode_preview_message((1).to_bytes(4, "big") + (1).to_bytes(4, "big") + jpeg)
        self.assertEqual(decoded, (jpeg, "image/jpeg"))

        metadata = json.dumps({"image_type": "image/png"}).encode("utf-8")
        png = b"\x89PNGpreview"
        decoded = self.client.decode_preview_message(
            (4).to_bytes(4, "big") + len(metadata).to_bytes(4, "big") + metadata + png
        )
        self.assertEqual(decoded, (png, "image/png"))


if __name__ == "__main__":
    unittest.main()
