import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from shared_gateway import GatewayStore, SharedComfyGateway
from voice import VoiceInstaller, VoiceJobManager


class GatewayVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.asset_dir = self.root / "assets"
        self.asset_dir.mkdir()
        self.installer = VoiceInstaller(self.root)
        self.installer.runtime_installed = lambda: True
        self.installer.model_installed = lambda _mode: True
        self.assets = SimpleNamespace(directory=self.asset_dir, path_for=self.asset_path)
        self.manager = VoiceJobManager(self.root, asyncio.Lock(), self.installer, self.asset_path)
        self.finish = asyncio.Event()
        self.generated = []

        async def fake_run(job_id, compiled):
            self.generated.append((job_id, compiled))
            await self.finish.wait()
            output = self.manager.output_dir / f"{job_id}.wav"
            output.write_bytes(b"RIFF" + b"0" * 200)
            self.manager.update(job_id, status="completed", progress=100, local_output=output.name)

        self.manager._run = fake_run
        self.gateway = SharedComfyGateway(self.root, voice_installer=self.installer,
                                         voice_jobs=self.manager, voice_assets=self.assets)
        self.alice, self.alice_token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.client = TestClient(TestServer(self.gateway.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        await self.client.close()
        self.temporary.cleanup()

    def asset_path(self, asset_id):
        paths = [path for path in self.asset_dir.glob(f"{asset_id}.*") if path.suffix != ".json"]
        if not paths:
            raise ValueError("missing asset")
        return paths[0]

    def headers(self, token=None):
        return {"Authorization": f"Bearer {token or self.alice_token}"}

    def payload(self, **changes):
        return {"client_job_id": uuid.uuid4().hex, "mode": "custom", "text": "遠端旁白測試",
                "speaker": "Vivian", "job_name": "旁白", "seed": 7, **changes}

    async def create(self, payload=None, token=None):
        response = await self.client.post("/api/voice/jobs", json=payload or self.payload(),
                                          headers=self.headers(token))
        self.assertEqual(response.status, 202, await response.text())
        return await response.json()

    async def upload(self, filename="reference.wav", content=b"RIFF" + b"0" * 200, token=None):
        form = aiohttp.FormData()
        form.add_field("file", content, filename=filename, content_type="audio/wav")
        return await self.client.post("/api/voice/references", data=form, headers=self.headers(token))

    async def test_all_voice_routes_require_an_active_personal_key(self):
        job_id = "a" * 32
        requests = [("GET", "/api/voice/status"), ("POST", "/api/voice/references"),
                    ("POST", "/api/voice/jobs"), ("GET", f"/api/voice/jobs/{job_id}"),
                    ("POST", f"/api/voice/jobs/{job_id}/cancel"),
                    ("POST", f"/api/voice/jobs/{job_id}/resume"),
                    ("GET", f"/api/voice/jobs/{job_id}/audio")]
        for method, url in requests:
            response = await self.client.request(method, url)
            self.assertEqual(response.status, 401)
        self.gateway.store.set_user_enabled(self.alice["id"], False)
        response = await self.client.get("/api/voice/status", headers=self.headers())
        self.assertEqual(response.status, 401)
        self.assertEqual(self.manager.jobs, {})

    async def test_missing_service_is_explicit_and_no_install_api_is_exposed(self):
        empty_gateway = SharedComfyGateway(self.root)
        other = TestClient(TestServer(empty_gateway.create_app()))
        await other.start_server()
        try:
            response = await other.get("/api/voice/status", headers=self.headers())
            self.assertEqual(response.status, 503)
            self.assertEqual((await response.json())["error_code"], "remote_voice_unavailable")
            response = await other.post("/api/voice/install", json={"mode": "custom"}, headers=self.headers())
            self.assertEqual(response.status, 404)
        finally:
            await other.close()

    async def test_status_reports_host_models_without_host_paths_or_install_permissions(self):
        self.installer.error = "private-token C:/private/model"
        response = await self.client.get("/api/voice/status", headers=self.headers())
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertTrue(state["models"]["custom"]["installed"])
        self.assertEqual(state["connection_mode"], "remote")
        self.assertFalse(state["can_install"])
        self.assertTrue(state["remote_voice"])
        rendered = json.dumps(state)
        self.assertNotIn("directory", rendered)
        self.assertNotIn("private-token", rendered)
        self.assertNotIn("C:/private", rendered)

    async def test_gateway_rejects_a_host_that_is_configured_as_a_remote_client(self):
        self.manager.comfy = SimpleNamespace(mode="remote")
        response = await self.client.get("/api/voice/status", headers=self.headers())
        self.assertEqual(response.status, 503)
        self.assertEqual((await response.json())["error_code"], "remote_voice_host_not_local")
        response = await self.client.post("/api/voice/jobs", json=self.payload(), headers=self.headers())
        self.assertEqual(response.status, 503)
        self.assertEqual(self.manager.jobs, {})

    async def test_create_is_idempotent_per_user_even_for_concurrent_requests(self):
        payload = self.payload()
        first, second = await asyncio.gather(self.create(payload), self.create(payload))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.manager.jobs), 1)
        bob = await self.create(payload, self.bob_token)
        self.assertNotEqual(first["id"], bob["id"])
        self.assertEqual(len(self.manager.jobs), 2)
        self.assertNotIn("client_job_id", first)
        self.assertNotIn("reference_audio", first)

    async def test_client_job_id_and_payload_are_validated(self):
        for invalid in (None, "../outside", "a" * 31, "G" * 32, 42):
            response = await self.client.post("/api/voice/jobs", json=self.payload(client_job_id=invalid),
                                              headers=self.headers())
            self.assertEqual(response.status, 400)
        for payload in ([], {}, self.payload(text="")):
            response = await self.client.post("/api/voice/jobs", json=payload, headers=self.headers())
            self.assertEqual(response.status, 400)
        self.assertEqual(self.manager.jobs, {})

    async def test_missing_host_model_does_not_install_or_start_a_job(self):
        self.installer.model_installed = lambda _mode: False
        response = await self.client.post("/api/voice/jobs", json=self.payload(), headers=self.headers())
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["error_code"], "remote_voice_model_missing")
        self.assertEqual(self.manager.jobs, {})
        self.assertIsNone(self.installer.task)

    async def test_other_user_cannot_read_cancel_resume_or_download_a_job(self):
        job = await self.create()
        cancel = Mock(wraps=self.manager.cancel)
        resume = Mock(wraps=self.manager.resume)
        self.manager.cancel, self.manager.resume = cancel, resume
        for method, suffix in (("GET", ""), ("POST", "/cancel"), ("POST", "/resume"), ("GET", "/audio")):
            response = await self.client.request(method, f"/api/voice/jobs/{job['id']}{suffix}",
                                                 headers=self.headers(self.bob_token))
            self.assertEqual(response.status, 404)
        cancel.assert_not_called()
        resume.assert_not_called()

    async def test_owner_and_idempotency_maps_survive_reload_and_prompt_persistence(self):
        upload = await self.upload()
        asset = await upload.json()
        payload = self.payload()
        job = await self.create(payload)
        self.gateway.store.set_prompt_owner("some-prompt", self.alice["id"])
        reloaded = GatewayStore(self.root)
        self.assertTrue(reloaded.owns_prompt("some-prompt", self.alice["id"]))
        self.assertEqual(reloaded.state["voice_asset_owners"][asset["id"]]["user_id"], self.alice["id"])
        self.assertEqual(reloaded.state["voice_job_owners"][job["id"]]["user_id"], self.alice["id"])
        self.assertEqual(reloaded.state["voice_client_jobs"][f"{self.alice['id']}:{payload['client_job_id']}"]["job_id"], job["id"])
        self.gateway.store = reloaded
        # Handler services hold the store they were registered with; reopen the app.
        reopened_gateway = SharedComfyGateway(self.root, voice_installer=self.installer,
                                              voice_jobs=self.manager, voice_assets=self.assets)
        reopened = TestClient(TestServer(reopened_gateway.create_app()))
        await reopened.start_server()
        try:
            response = await reopened.post("/api/voice/jobs", json=payload, headers=self.headers())
            self.assertEqual(response.status, 202)
            self.assertEqual((await response.json())["id"], job["id"])
            self.assertEqual(len(self.manager.jobs), 1)
        finally:
            await reopened.close()

    async def test_clone_accepts_owned_uploaded_audio_and_rejects_other_users_reference(self):
        response = await self.upload(filename="../../reference.wav")
        self.assertEqual(response.status, 201)
        reference = await response.json()
        self.assertEqual(reference["name"], "reference.wav")
        self.assertEqual(self.asset_path(reference["id"]).parent, self.asset_dir)
        payload = self.payload(mode="clone", reference_asset_id=reference["id"],
                               reference_text="參考台詞", voice_authorized=True)
        own = await self.create(payload)
        self.assertEqual(own["mode"], "clone")
        payload["client_job_id"] = uuid.uuid4().hex
        response = await self.client.post("/api/voice/jobs", json=payload, headers=self.headers(self.bob_token))
        self.assertEqual(response.status, 404)
        self.assertEqual(len(self.manager.jobs), 1)

    async def test_clone_rejects_host_local_assets_and_paths_outside_asset_root(self):
        local_id = uuid.uuid4().hex
        (self.asset_dir / f"{local_id}.wav").write_bytes(b"host private")
        response = await self.client.post("/api/voice/jobs", json=self.payload(
            mode="clone", reference_asset_id=local_id, x_vector_only=True, voice_authorized=True,
        ), headers=self.headers())
        self.assertEqual(response.status, 404)
        upload = await self.upload()
        asset = await upload.json()
        outside = self.root / "outside.wav"
        outside.write_bytes(b"private")
        self.assets.path_for = lambda _asset_id: outside
        response = await self.client.post("/api/voice/jobs", json=self.payload(
            mode="clone", reference_asset_id=asset["id"], x_vector_only=True, voice_authorized=True,
        ), headers=self.headers())
        self.assertEqual(response.status, 404)
        self.assertEqual(self.manager.jobs, {})

    async def test_invalid_empty_and_oversized_references_leave_no_files_or_owners(self):
        response = await self.upload(filename="../../program.exe")
        self.assertEqual(response.status, 400)
        response = await self.upload(content=b"")
        self.assertEqual(response.status, 400)
        with patch("gateway_voice.MAX_REFERENCE_BYTES", 16):
            response = await self.upload(content=b"0" * 17)
        self.assertEqual(response.status, 413)
        self.assertEqual(list(self.asset_dir.iterdir()), [])
        self.assertEqual(self.gateway.store.state["voice_asset_owners"], {})

    async def test_completed_audio_download_is_owned_and_does_not_expose_paths(self):
        job = await self.create()
        unfinished = await self.client.get(f"/api/voice/jobs/{job['id']}/audio", headers=self.headers())
        self.assertEqual(unfinished.status, 404)
        self.finish.set()
        await self.manager.tasks[job["id"]]
        response = await self.client.get(f"/api/voice/jobs/{job['id']}/audio?download=1", headers=self.headers())
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"RIFF" + b"0" * 200)
        self.assertIn("filename*=UTF-8", response.headers["Content-Disposition"])
        self.manager.update(job["id"], error="private-token C:/private/model", worker_path="C:/private/worker")
        response = await self.client.get(f"/api/voice/jobs/{job['id']}", headers=self.headers())
        public = await response.json()
        self.assertEqual(public["local_output"], f"{job['id']}.wav")
        self.assertNotIn("private", json.dumps(public))
        self.assertNotIn("worker_path", public)

    async def test_audio_rejects_output_paths_outside_voice_directory(self):
        job = await self.create()
        outside = self.root / "outside.wav"
        outside.write_bytes(b"private")
        self.manager.update(job["id"], status="completed")
        self.manager.local_output_path = lambda _job: outside
        response = await self.client.get(f"/api/voice/jobs/{job['id']}/audio", headers=self.headers())
        self.assertEqual(response.status, 404)

    async def test_resume_only_restarts_failed_cancelled_or_interrupted_jobs(self):
        job = await self.create()
        resume = Mock(side_effect=lambda job_id: self.manager.jobs[job_id])
        self.manager.resume = resume
        for status in ("queued", "preparing", "running", "completed"):
            self.manager.update(job["id"], status=status)
            response = await self.client.post(f"/api/voice/jobs/{job['id']}/resume", headers=self.headers())
            self.assertEqual(response.status, 202)
            self.assertEqual((await response.json())["status"], status)
        resume.assert_not_called()
        for status in ("failed", "cancelled", "interrupted"):
            self.manager.update(job["id"], status=status)
            response = await self.client.post(f"/api/voice/jobs/{job['id']}/resume", headers=self.headers())
            self.assertEqual(response.status, 202)
        self.assertEqual(resume.call_count, 3)


if __name__ == "__main__":
    unittest.main()
