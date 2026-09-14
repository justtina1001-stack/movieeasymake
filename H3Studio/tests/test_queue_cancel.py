import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from comfy_client import ComfyClient
from queue_cancel import QueueCancelError
from settings import ConnectionSettings
from shared_gateway import SharedComfyGateway


class QueueCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.running = [[1, "colleague", {}, {}, []]]
        self.pending = [[2, "mine", {}, {}, []]]
        self.atomic_status = 200
        self.move_pending_on_delete = False
        self.requests = []
        upstream = web.Application()
        upstream.router.add_get("/queue", self.queue)
        upstream.router.add_post("/queue", self.delete_pending)
        upstream.router.add_post("/interrupt", self.global_interrupt)
        upstream.router.add_post("/api/jobs/{prompt_id}/cancel", self.atomic_cancel)
        upstream.router.add_post("/prompt", self.submit_prompt)
        upstream.router.add_get("/history/{prompt_id}", self.history)
        self.runner, self.base_url = await self.start_app(upstream)
        self.gateway = SharedComfyGateway(Path(self.temporary.name) / "gateway")
        self.alice, self.token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.gateway.store.set_prompt_owner("mine", self.alice["id"])
        self.gateway.store.set_prompt_owner("colleague", self.bob["id"])
        self.gateway.store.config["upstream_url"] = self.base_url
        self.gateway_runner, self.gateway_url = await self.start_app(self.gateway.create_app())
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.gateway_runner.cleanup()
        await self.runner.cleanup()
        self.temporary.cleanup()

    @staticmethod
    async def start_app(app):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    def client(self, url=None, token=""):
        return ComfyClient(ConnectionSettings(
            mode="remote" if token else "local", base_url=url or self.base_url,
            remote_access_token=token, comfy_dir=self.temporary.name, auto_start_local=False,
        ), Path(self.temporary.name))

    async def queue(self, _request):
        return web.json_response({"queue_running": self.running, "queue_pending": self.pending})

    async def atomic_cancel(self, request):
        prompt_id = request.match_info["prompt_id"]
        self.requests.append(("atomic", prompt_id))
        if self.atomic_status != 200:
            return web.Response(status=self.atomic_status)
        found = any(item[1] == prompt_id for item in self.running + self.pending)
        self.running[:] = [item for item in self.running if item[1] != prompt_id]
        self.pending[:] = [item for item in self.pending if item[1] != prompt_id]
        return web.json_response({"cancelled": found})

    async def delete_pending(self, request):
        payload = await request.json()
        self.requests.append(("delete", payload))
        self.assertEqual(payload, {"delete": ["mine"]})
        if self.move_pending_on_delete:
            self.running[:] = self.pending
            self.pending[:] = []
        else:
            self.pending[:] = [item for item in self.pending if item[1] not in payload["delete"]]
        return web.Response(status=200)

    async def global_interrupt(self, _request):
        self.requests.append(("global", None))
        return web.Response(status=200)

    async def submit_prompt(self, _request):
        return web.json_response({"prompt_id": "mine"})

    async def history(self, _request):
        return web.json_response({})

    async def test_cancel_own_pending_does_not_interrupt_colleague(self):
        self.assertTrue(await self.client().interrupt("mine"))
        self.assertEqual([item[1] for item in self.running], ["colleague"])
        self.assertEqual(self.pending, [])
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_cancel_own_running_uses_exact_endpoint(self):
        self.running, self.pending = self.pending, self.running
        self.assertTrue(await self.client().interrupt("mine"))
        self.assertEqual(self.running, [])
        self.assertEqual([item[1] for item in self.pending], ["colleague"])
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_missing_id_never_sends_an_interrupt(self):
        self.assertFalse(await self.client().interrupt())
        self.assertFalse(await self.client().interrupt(""))
        self.assertEqual(self.requests, [])

    async def test_old_engine_removes_only_own_pending_item(self):
        self.atomic_status = 404
        self.assertTrue(await self.client().interrupt("mine"))
        self.assertEqual(self.requests, [("atomic", "mine"), ("delete", {"delete": ["mine"]})])
        self.assertEqual([item[1] for item in self.running], ["colleague"])

    async def test_old_engine_refuses_running_cancel_with_update_message(self):
        self.atomic_status = 404
        self.running, self.pending = self.pending, self.running
        with self.assertRaisesRegex(QueueCancelError, "更新"):
            await self.client().interrupt("mine")
        self.assertEqual([item[1] for item in self.running], ["mine"])
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_pending_to_running_race_is_not_reported_as_cancelled(self):
        self.atomic_status = 404
        self.move_pending_on_delete = True
        with self.assertRaisesRegex(QueueCancelError, "剛開始生成"):
            await self.client().interrupt("mine")
        self.assertEqual([item[1] for item in self.running], ["mine"])
        self.assertNotIn(("global", None), self.requests)

    async def test_finished_job_is_idempotent(self):
        self.assertFalse(await self.client().interrupt("finished"))
        self.assertEqual([item[1] for item in self.running], ["colleague"])

    async def test_gateway_authenticates_and_only_cancels_owner(self):
        self.assertTrue(await self.client(self.gateway_url, self.token).interrupt("mine"))
        self.assertEqual(self.requests, [("atomic", "mine")])
        for path in ("/interrupt", "/api/jobs/colleague/cancel"):
            async with self.session.post(
                self.gateway_url + path,
                headers={"Authorization": f"Bearer {self.token}", "X-H3-Prompt-ID": "colleague"},
            ) as response:
                self.assertEqual(response.status, 404)
        async with self.session.post(self.gateway_url + "/api/jobs/colleague/cancel") as response:
            self.assertEqual(response.status, 401)
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_legacy_gateway_interrupt_route_also_uses_safe_upstream_cancel(self):
        async with self.session.post(
            self.gateway_url + "/interrupt",
            headers={"Authorization": f"Bearer {self.token}", "X-H3-Prompt-ID": "mine"},
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"cancelled": True})
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_gateway_propagates_unsupported_running_cancel(self):
        self.atomic_status = 404
        self.running, self.pending = self.pending, self.running
        with self.assertRaises(QueueCancelError) as error:
            await self.client(self.gateway_url, self.token).interrupt("mine")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.requests, [("atomic", "mine")])

    async def test_run_prompt_cancel_exit_uses_targeted_interrupt(self):
        client = self.client()
        cancel = asyncio.Event()

        async def progress(_event):
            cancel.set()

        with patch.object(client, "interrupt", new_callable=AsyncMock, return_value=True) as interrupt:
            with self.assertRaises(asyncio.CancelledError):
                await client.run_prompt({}, progress, cancel)
        interrupt.assert_awaited_once_with("mine")
        self.assertEqual(self.requests, [])

    async def test_run_prompt_does_not_hide_cancel_failure(self):
        client = self.client()
        cancel = asyncio.Event()

        async def progress(_event):
            cancel.set()

        with patch.object(client, "interrupt", new_callable=AsyncMock, side_effect=QueueCancelError("cannot cancel")):
            with self.assertRaisesRegex(QueueCancelError, "cannot cancel"):
                await client.run_prompt({}, progress, cancel)


if __name__ == "__main__":
    unittest.main()
