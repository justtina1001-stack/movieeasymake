import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web

from comfy_client import ComfyClient
from queue_snapshot import fetch_queue_snapshot, normalize_queue_snapshot
from settings import ConnectionSettings
from shared_gateway import SharedComfyGateway


def queued(priority, prompt_id):
    return [priority, prompt_id, {"secret_workflow": "private dialogue"}, {"client_id": "private user"}, ["output"]]


class QueueNormalizationTests(unittest.TestCase):
    def test_pending_heap_is_sorted_and_workflows_are_removed(self):
        snapshot = normalize_queue_snapshot({
            "queue_running": [queued(0, "running")],
            "queue_pending": [queued(1, "first"), queued(9, "last"), queued(3, "second")],
            "private_metadata": "not public",
        })
        self.assertEqual(snapshot, {
            "available": True, "running_count": 1, "pending_count": 3,
            "running": [{"prompt_id": "running", "position": 0}],
            "pending": [
                {"prompt_id": "first", "position": 1},
                {"prompt_id": "second", "position": 2},
                {"prompt_id": "last", "position": 3},
            ],
        })

    def test_gateway_summary_keeps_anonymous_positions_but_no_extra_fields(self):
        snapshot = normalize_queue_snapshot({
            "available": True, "running_count": 1, "pending_count": 2,
            "running": [{"prompt_id": None, "position": 0, "name": "Bob"}],
            "pending": [{"prompt_id": "mine", "position": 2}, {"prompt_id": None, "position": 1}],
            "private_metadata": "not public",
        })
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["pending"], [{"prompt_id": None, "position": 1}, {"prompt_id": "mine", "position": 2}])
        self.assertEqual(snapshot["running"], [{"prompt_id": None, "position": 0}])
        self.assertNotIn("private_metadata", snapshot)

    def test_invalid_or_partial_snapshot_is_unknown_instead_of_idle(self):
        invalid = [
            None, [], {}, {"queue_running": []},
            {"queue_running": [], "queue_pending": [None]},
            {"queue_running": [], "queue_pending": [queued("1", "a")]},
            {"queue_running": [], "queue_pending": [queued(float("nan"), "a")]},
            {"queue_running": [queued(1, "same")], "queue_pending": [queued(2, "same")]},
            {"available": True, "running_count": 0, "pending_count": 3, "running": [], "pending": []},
            {"available": True, "running_count": 0, "pending_count": 1, "running": [],
             "pending": [{"prompt_id": "mine", "position": 2}]},
            {"available": False, "running_count": 0, "pending_count": 0, "error": "private details"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = normalize_queue_snapshot(payload)
                self.assertIs(result["available"], False)
                self.assertIsNone(result["running_count"])
                self.assertIsNone(result["pending_count"])
                self.assertEqual(result["running"], [])
                self.assertEqual(result["pending"], [])
                self.assertNotIn("private details", json.dumps(result))

    def test_valid_empty_queue_means_idle(self):
        result = normalize_queue_snapshot({"queue_running": [], "queue_pending": []})
        self.assertTrue(result["available"])
        self.assertEqual((result["running_count"], result["pending_count"]), (0, 0))


class QueueTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.gateway = SharedComfyGateway(Path(self.temporary.name))
        self.alice, self.alice_token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.gateway.store.set_prompt_owner("alice-next", self.alice["id"])
        self.gateway.store.set_prompt_owner("alice-later", self.alice["id"])
        self.gateway.store.set_prompt_owner("bob-running", self.bob["id"])
        self.gateway.store.set_prompt_owner("bob-next", self.bob["id"])
        self.payload = {
            "queue_running": [queued(0, "bob-running")],
            "queue_pending": [queued(1, "alice-next"), queued(8, "alice-later"),
                              queued(3, "host-next"), queued(5, "bob-next")],
        }
        self.status = 200
        self.body = None
        self.seen_authorization = []
        upstream = web.Application()
        upstream.router.add_get("/queue", self.fake_queue)
        self.upstream_runner, self.upstream_url = await self.start_app(upstream)
        self.gateway.store.config["upstream_url"] = self.upstream_url
        self.gateway_runner, self.gateway_url = await self.start_app(self.gateway.create_app())
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.gateway_runner.cleanup()
        await self.upstream_runner.cleanup()
        self.temporary.cleanup()

    @staticmethod
    async def start_app(app):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def fake_queue(self, request):
        self.seen_authorization.append(request.headers.get("Authorization"))
        if self.body is not None:
            return web.Response(text=self.body, status=self.status, content_type="application/json")
        return web.json_response(self.payload, status=self.status)

    def client(self, url, token=""):
        return ComfyClient(ConnectionSettings(
            mode="remote", base_url=url, comfy_dir=self.temporary.name,
            auto_start_local=False, remote_access_token=token,
        ), Path(self.temporary.name))

    async def test_direct_client_sends_auth_and_normalizes_queue(self):
        result = await self.client(self.upstream_url, "personal-token").queue_status()
        self.assertEqual(self.seen_authorization, ["Bearer personal-token"])
        self.assertTrue(result["available"])
        self.assertEqual([row["prompt_id"] for row in result["pending"]],
                         ["alice-next", "host-next", "bob-next", "alice-later"])
        self.assertNotIn("private", json.dumps(result))

    async def test_gateway_shows_global_order_and_only_own_prompt_ids(self):
        async with self.session.get(f"{self.gateway_url}/queue", headers={"Authorization": f"Bearer {self.alice_token}"}) as response:
            self.assertEqual(response.status, 200)
            alice_result = await response.json()
        self.assertTrue(alice_result["available"])
        self.assertEqual((alice_result["running_count"], alice_result["pending_count"]), (1, 4))
        self.assertEqual(alice_result["running"], [{"prompt_id": None, "position": 0}])
        self.assertEqual(alice_result["pending"], [
            {"prompt_id": "alice-next", "position": 1}, {"prompt_id": None, "position": 2},
            {"prompt_id": None, "position": 3}, {"prompt_id": "alice-later", "position": 4},
        ])
        serialized = json.dumps(alice_result)
        for secret in ("host-next", "bob-next", "bob-running", "Bob", "Alice", "secret_workflow",
                       "private dialogue", self.alice_token, self.bob_token, self.bob["id"]):
            self.assertNotIn(secret, serialized)
        bob_result = await self.client(self.gateway_url, self.bob_token).queue_status()
        self.assertEqual(bob_result["running"], [{"prompt_id": "bob-running", "position": 0}])
        self.assertEqual(bob_result["pending"][2], {"prompt_id": "bob-next", "position": 3})
        self.assertNotIn("alice-", json.dumps(bob_result))
        # Personal gateway credentials must never be forwarded to ComfyUI.
        self.assertEqual(self.seen_authorization, [None, None])

    async def test_gateway_requires_active_token_and_does_not_query_upstream_on_failure(self):
        for token in ("", "invalid"):
            async with self.session.get(f"{self.gateway_url}/queue", headers={"Authorization": f"Bearer {token}"}) as response:
                self.assertEqual(response.status, 401)
        self.gateway.store.set_user_enabled(self.alice["id"], False)
        result = await self.client(self.gateway_url, self.alice_token).queue_status()
        self.assertFalse(result["available"])
        self.assertIsNone(result["running_count"])
        self.assertEqual(self.seen_authorization, [])

    async def test_old_gateway_auth_failure_and_bad_json_do_not_report_idle(self):
        client = self.client(self.upstream_url)
        for status, body in ((404, "old gateway"), (401, "token secret"), (503, "backend secret"),
                             (200, "broken json"), (200, '{"queue_running": []}')):
            self.status, self.body = status, body
            with self.subTest(status=status, body=body):
                result = await client.queue_status()
                self.assertFalse(result["available"])
                self.assertIsNone(result["pending_count"])
                self.assertNotIn(body, json.dumps(result))

    async def test_gateway_upstream_failure_is_a_sanitized_unknown_snapshot(self):
        self.status, self.body = 500, "secret workflow failure"
        async with self.session.get(f"{self.gateway_url}/queue", headers={"Authorization": f"Bearer {self.alice_token}"}) as response:
            self.assertEqual(response.status, 200)
            result = await response.json()
        self.assertFalse(result["available"])
        self.assertIsNone(result["running_count"])
        self.assertIsNone(result["pending_count"])
        self.assertNotIn("secret", json.dumps(result))

    async def test_timeout_and_connection_failure_are_unknown_and_timeout_is_bounded(self):
        for error in (asyncio.TimeoutError(), aiohttp.ClientConnectionError("private address")):
            with patch("queue_snapshot.aiohttp.ClientSession", side_effect=error) as constructor:
                result = await fetch_queue_snapshot(self.upstream_url, timeout=60)
            self.assertFalse(result["available"])
            self.assertIsNone(result["running_count"])
            self.assertLessEqual(constructor.call_args.kwargs["timeout"].total, 3)
            self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
