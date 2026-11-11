"""Remote FreeVideo capability and file inputs keep Gateway user boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

import aiohttp
from aiohttp import web

from shared_gateway import SharedComfyGateway


class FreeVideoGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.gateway = SharedComfyGateway(Path(self.temporary.name))
        self.alice, self.alice_token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.setup_calls, self.private_calls, self.forwarded = [], [], []
        self.setup_state = {"ready": True, "root": "E:/private/runtime", "token": "do-not-leak",
                            "downloads": {"credentials": "also-private"}}
        self.setup_behavior = "normal"
        self.websocket_events = []

        async def setup(request):
            self.setup_calls.append(dict(request.query))
            if self.setup_behavior == "redirect":
                raise web.HTTPFound("/private")
            if self.setup_behavior == "invalid":
                return web.Response(text="private traceback")
            if self.setup_behavior == "error":
                return web.json_response(self.setup_state, status=500)
            return web.json_response(self.setup_state)

        async def private(request):
            self.private_calls.append(request.path)
            return web.json_response(self.setup_state)

        async def prompt(request):
            self.forwarded.append(await request.json())
            return web.json_response({"prompt_id": f"prompt-{len(self.forwarded)}"})

        async def object_info(request):
            from tests.test_freevideo_video import freevideo_schemas
            name = request.match_info["node"]
            schemas = freevideo_schemas()
            return web.json_response({name: schemas[name]} if name in schemas else {})

        async def stats(request):
            return web.json_response({"system": {"comfyui_version": "0.42.0"}, "devices": []})

        async def socket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            for event in self.websocket_events:
                await ws.send_json(event)
            await ws.close()
            return ws

        upstream = web.Application()
        upstream.router.add_get("/freevideo/setup", setup)
        upstream.router.add_get("/private", private)
        upstream.router.add_post("/prompt", prompt)
        upstream.router.add_get("/object_info/{node}", object_info)
        upstream.router.add_get("/system_stats", stats)
        upstream.router.add_get("/ws", socket)
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
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return runner, f"http://127.0.0.1:{port}"

    def headers(self, token=None):
        return {"Authorization": f"Bearer {token or self.alice_token}"}

    def workflow(self, files=None, **generate):
        prefix = f"H3Gateway/{self.alice['id']}/assets/"
        rows = files if files is not None else [{"file": prefix + "角色.png", "role": "reference"},
                                                {"file": prefix + "motion.mp4", "role": "reference", "sound": False},
                                                {"file": prefix + "voice.wav", "role": "reference", "use": "reference"}]
        return {"1": {"class_type": "FreeVideoMedia", "inputs": {"assets": json.dumps(rows, ensure_ascii=False)}},
                "2": {"class_type": "FreeVideoGenerate", "inputs": {"text": "Reviewed prompt", "media": ["1", 0], **generate}},
                "3": {"class_type": "SaveVideo", "inputs": {"video": ["2", 0], "filename_prefix": "H3Studio/比較影片"}}}

    async def submit(self, workflow, token=None):
        return await self.session.post(f"{self.gateway_url}/prompt", headers=self.headers(token),
                                       json={"prompt": workflow, "client_id": "test"})

    async def test_readiness_authenticates_before_contacting_private_setup(self):
        async with self.session.get(f"{self.gateway_url}/freevideo/setup") as response:
            self.assertEqual(response.status, 401)
        self.assertFalse(self.setup_calls)
        self.gateway.store.set_user_enabled(self.alice["id"], False)
        async with self.session.get(f"{self.gateway_url}/freevideo/setup", headers=self.headers()) as response:
            self.assertEqual(response.status, 401)
        self.assertFalse(self.setup_calls)

    async def test_only_strict_readiness_boolean_is_public_and_queries_are_not_forwarded(self):
        async with self.session.get(f"{self.gateway_url}/freevideo/setup", headers=self.headers(),
                                    params={"root": "C:/private", "download_root": "other"}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"ready": True})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.setup_calls, [{}])
        for state in ({"ready": "true", "token": "private"}, {"ready": 1}, [True], None):
            self.setup_state = state
            async with self.session.get(f"{self.gateway_url}/freevideo/setup", headers=self.headers()) as response:
                self.assertEqual(await response.json(), {"ready": False})

    async def test_unavailable_malformed_and_redirected_setup_never_discloses_or_claims_ready(self):
        for behavior in ("redirect", "invalid", "error"):
            self.setup_behavior = behavior
            with self.subTest(behavior=behavior):
                async with self.session.get(f"{self.gateway_url}/freevideo/setup", headers=self.headers()) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.json(), {"ready": False})
        self.assertFalse(self.private_calls)
        for path in ("/freevideo/setup", "/freevideo/setup/install", "/freevideo/setup/plan"):
            async with self.session.post(self.gateway_url + path, headers=self.headers(), json={"accept_licenses": True}) as response:
                self.assertIn(response.status, {404, 405})
        self.assertEqual(len(self.setup_calls), 3)

    async def test_owned_multimodal_files_are_forwarded_in_order_and_save_output_is_namespaced(self):
        workflow = self.workflow()
        async with await self.submit(workflow) as response:
            self.assertEqual(response.status, 200)
            prompt_id = (await response.json())["prompt_id"]
        forwarded = self.forwarded[-1]["prompt"]
        self.assertEqual(forwarded["1"], workflow["1"])
        self.assertEqual(forwarded["2"], workflow["2"])
        self.assertEqual(forwarded["3"]["inputs"]["filename_prefix"], f"H3Gateway/{self.alice['id']}/比較影片")
        self.assertTrue(self.gateway.store.owns_prompt(prompt_id, self.alice["id"]))
        self.assertFalse(self.gateway.store.owns_prompt(prompt_id, self.bob["id"]))

    async def test_remote_studio_inventory_recognizes_ready_freevideo_through_authenticated_gateway(self):
        from comfy_client import ComfyClient
        from settings import ConnectionSettings
        from video_models import VIDEO_MODEL_FREEVIDEO, video_model_profiles
        settings = ConnectionSettings(mode="remote", base_url=self.gateway_url, remote_access_token=self.alice_token)
        client = ComfyClient(settings, Path(self.temporary.name))
        inventory = await client.model_inventory(refresh=True)
        self.assertTrue(inventory["freevideo_nodes_ready"])
        self.assertTrue(inventory["freevideo_runtime_ready"])
        self.assertNotIn("do-not-leak", json.dumps(inventory))
        profile = next(row for row in video_model_profiles(inventory) if row["id"] == VIDEO_MODEL_FREEVIDEO)
        self.assertTrue(profile["ready"])
        self.assertEqual(self.setup_calls, [{}])

    async def test_foreign_absolute_traversal_and_nonliteral_media_are_rejected_before_forwarding(self):
        prefix = f"H3Gateway/{self.alice['id']}/"
        bad_names = [f"H3Gateway/{self.bob['id']}/assets/actor.png", "/" + prefix + "actor.png",
                     "C:/" + prefix + "actor.png", prefix + "../other/actor.png", prefix + "assets/../../actor.png",
                     prefix + "assets/./actor.png", prefix + "/actor.png", prefix + "assets\\actor.png",
                     prefix + ".. /other/actor.png", prefix + "assets/.../actor.png", prefix + "assets /actor.png",
                     prefix + "actor.png:stream", prefix + "actor\x00.png", "file://" + prefix + "actor.png", prefix]
        for name in bad_names:
            with self.subTest(name=name):
                async with await self.submit(self.workflow([{"file": name, "role": "reference", "enabled": False}])) as response:
                    self.assertEqual(response.status, 400)
        for invalid in (["4", 0], "not json", json.dumps({"file": prefix + "x.png"}), "[null]", "[{}]",
                        json.dumps([{"file": prefix + "x.png"}] * 33), " " * (128 * 1024 + 1)):
            workflow = self.workflow()
            workflow["1"]["inputs"]["assets"] = invalid
            async with await self.submit(workflow) as response:
                self.assertEqual(response.status, 400)
        self.assertFalse(self.forwarded)

    async def test_remote_saved_output_continuation_is_refused_instead_of_accessing_unowned_files(self):
        for field in ("refine_from", "continue_from"):
            for value in ("FreeVideo/private.mp4", f"H3Gateway/{self.alice['id']}/old.mp4", ["9", 0], False, 12):
                with self.subTest(field=field, value=value):
                    async with await self.submit(self.workflow(**{field: value})) as response:
                        self.assertEqual(response.status, 400)
        self.assertFalse(self.forwarded)
        async with await self.submit(self.workflow(refine_from="", continue_from=None)) as response:
            self.assertEqual(response.status, 200)

    async def test_broadcast_freevideo_progress_is_filtered_to_authenticated_prompt_owner(self):
        self.gateway.store.set_prompt_owner("mine", self.alice["id"])
        self.gateway.store.set_prompt_owner("other", self.bob["id"])
        self.websocket_events = [
            {"type": "freevideo_progress", "data": {"node": "2", "label": "anonymous"}},
            {"type": "freevideo_progress", "data": {"prompt_id": "other", "node": "2", "label": "foreign"}},
            {"type": "freevideo_progress", "data": {"prompt_id": "mine", "node": "2", "label": "owned"}},
            {"type": "status", "data": {"exec_info": {"queue_remaining": 0}}},
        ]
        received = []
        async with self.session.ws_connect(self.gateway_url + "/ws?clientId=test", headers=self.headers()) as ws:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    received.append(json.loads(message.data))
        self.assertEqual([event["type"] for event in received], ["freevideo_progress", "status"])
        self.assertEqual(received[0]["data"]["label"], "owned")


if __name__ == "__main__":
    unittest.main()
