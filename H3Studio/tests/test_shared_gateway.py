import json
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

from shared_gateway import SharedComfyGateway


class SharedGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.gateway = SharedComfyGateway(Path(self.temporary.name))
        self.alice, self.alice_token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.forwarded_prompts = []
        self.upload_subfolders = []
        self.prompt_number = 0

        upstream = web.Application()
        upstream.router.add_get("/system_stats", lambda _: web.json_response({"devices": [{"name": "Test GPU"}]}))
        upstream.router.add_get("/object_info/{tail:.*}", lambda _: web.json_response({"ok": True}))
        upstream.router.add_post("/upload/image", self.fake_upload)
        upstream.router.add_post("/prompt", self.fake_prompt)
        upstream.router.add_get("/history/{prompt_id}", self.fake_history)
        upstream.router.add_get("/view", lambda _: web.Response(body=b"video"))
        upstream.router.add_get("/queue", lambda _: web.json_response({"queue_running": [], "queue_pending": []}))
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

    @staticmethod
    def headers(token, prompt_id=None):
        result = {"Authorization": f"Bearer {token}"}
        if prompt_id:
            result["X-H3-Prompt-ID"] = prompt_id
        return result

    async def fake_upload(self, request):
        reader = await request.multipart()
        subfolder = ""
        async for part in reader:
            if part.name == "subfolder":
                subfolder = await part.text()
            elif part.name == "image":
                await part.read()
        self.upload_subfolders.append(subfolder)
        return web.json_response({"name": "image.png", "subfolder": subfolder, "type": "input"})

    async def fake_prompt(self, request):
        payload = await request.json()
        self.forwarded_prompts.append(payload)
        self.prompt_number += 1
        return web.json_response({"prompt_id": f"prompt-{self.prompt_number}"})

    async def fake_history(self, request):
        prompt_id = request.match_info["prompt_id"]
        return web.json_response({prompt_id: {"outputs": {}}})

    async def submit_alice_prompt(self):
        prefix = f"H3Gateway/{self.alice['id']}/assets"
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": f"{prefix}/image.png"}},
            "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "jobs/my movie"}},
        }
        async with self.session.post(
            f"{self.gateway_url}/prompt",
            headers=self.headers(self.alice_token),
            json={"client_id": "local-client", "prompt": workflow},
        ) as response:
            self.assertEqual(response.status, 200)
            return (await response.json())["prompt_id"]

    async def test_requires_an_active_personal_token(self):
        async with self.session.get(f"{self.gateway_url}/system_stats") as response:
            self.assertEqual(response.status, 401)
        async with self.session.get(
            f"{self.gateway_url}/system_stats", headers=self.headers(self.alice_token)
        ) as response:
            self.assertEqual(response.status, 200)
        self.gateway.store.set_user_enabled(self.alice["id"], False)
        async with self.session.get(
            f"{self.gateway_url}/system_stats", headers=self.headers(self.alice_token)
        ) as response:
            self.assertEqual(response.status, 401)

    async def test_uploads_and_outputs_are_namespaced_per_user(self):
        form = aiohttp.FormData()
        form.add_field("image", b"image", filename="image.png", content_type="image/png")
        form.add_field("type", "input")
        form.add_field("subfolder", "h3studio/job-a")
        async with self.session.post(
            f"{self.gateway_url}/upload/image", headers=self.headers(self.alice_token), data=form
        ) as response:
            self.assertEqual(response.status, 200)
        expected_prefix = f"H3Gateway/{self.alice['id']}/"
        self.assertTrue(self.upload_subfolders[-1].startswith(expected_prefix))

        await self.submit_alice_prompt()
        forwarded = self.forwarded_prompts[-1]
        self.assertTrue(forwarded["client_id"].startswith(f"h3g-{self.alice['id']}-"))
        output_prefix = forwarded["prompt"]["2"]["inputs"]["filename_prefix"]
        self.assertTrue(output_prefix.startswith(expected_prefix))

    async def test_one_user_cannot_read_another_users_history_or_output(self):
        prompt_id = await self.submit_alice_prompt()
        async with self.session.get(
            f"{self.gateway_url}/history/{prompt_id}", headers=self.headers(self.bob_token)
        ) as response:
            self.assertEqual(response.status, 404)
        async with self.session.get(
            f"{self.gateway_url}/history/{prompt_id}", headers=self.headers(self.alice_token)
        ) as response:
            self.assertEqual(response.status, 200)

        query = {"filename": "movie.mp4", "subfolder": f"H3Gateway/{self.alice['id']}", "type": "output"}
        async with self.session.get(
            f"{self.gateway_url}/view", headers=self.headers(self.bob_token), params=query
        ) as response:
            self.assertEqual(response.status, 404)

    async def test_cross_user_material_path_and_cancel_are_rejected(self):
        prompt_id = await self.submit_alice_prompt()
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": f"H3Gateway/{self.alice['id']}/assets/image.png"}},
        }
        async with self.session.post(
            f"{self.gateway_url}/prompt",
            headers=self.headers(self.bob_token),
            json={"client_id": "bob", "prompt": workflow},
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.session.post(
            f"{self.gateway_url}/interrupt", headers=self.headers(self.bob_token, prompt_id)
        ) as response:
            self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
