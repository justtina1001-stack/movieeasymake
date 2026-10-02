"""Real client/Gateway/host flow, with only the GPU subprocess replaced."""
import asyncio
import io
import json
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from comfy_client import ComfyClient
from remote_voice import RemoteVoiceConnection, RemoteVoiceError
from settings import ConnectionSettings
from shared_gateway import SharedComfyGateway
from voice import VoiceInstaller, VoiceJobManager, compile_voice_request


def sample_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 3200)
    return output.getvalue()


class FakeVoiceProcess:
    def __init__(self, output, finish, content):
        self.output = output
        self.finish = finish
        self.content = content
        self.stopped = asyncio.Event()
        self.returncode = None

    async def communicate(self):
        done = asyncio.create_task(self.finish.wait())
        stopped = asyncio.create_task(self.stopped.wait())
        try:
            await asyncio.wait((done, stopped), return_when=asyncio.FIRST_COMPLETED)
            if self.stopped.is_set():
                self.returncode = -15
            else:
                self.output.write_bytes(self.content)
                self.returncode = 0
            return b"", b""
        finally:
            done.cancel()
            stopped.cancel()
            await asyncio.gather(done, stopped, return_exceptions=True)

    def terminate(self):
        self.stopped.set()


class GatewayVoiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.host_root = self.root / "gpu-host"
        self.host_assets = self.host_root / "assets"
        self.host_assets.mkdir(parents=True)
        engine = web.Application()

        async def queue(_):
            return web.json_response({"queue_running": [], "queue_pending": []})

        async def free(_):
            return web.json_response({})

        engine.router.add_get("/queue", queue)
        engine.router.add_post("/free", free)
        self.engine = TestServer(engine)
        await self.engine.start_server()
        self.host_comfy = ComfyClient(ConnectionSettings(mode="local", base_url=str(self.engine.make_url("/")),
                                                       comfy_dir=str(self.host_root / "ComfyUI"), auto_start_local=False),
                                     self.host_root)
        self.host_installer = VoiceInstaller(self.host_root)
        self.host_installer.runtime_installed = Mock(return_value=True)
        self.host_installer.model_installed = Mock(return_value=True)
        self.host = VoiceJobManager(self.host_root, asyncio.Lock(), self.host_installer,
                                    lambda asset_id: self.asset_path(self.host_assets, asset_id), comfy=self.host_comfy)
        assets = SimpleNamespace(directory=self.host_assets,
                                 path_for=lambda asset_id: self.asset_path(self.host_assets, asset_id))
        self.gateway = SharedComfyGateway(self.host_root, voice_installer=self.host_installer,
                                         voice_jobs=self.host, voice_assets=assets)
        self.gateway.store.config["upstream_url"] = str(self.engine.make_url("/")).rstrip("/")
        self.alice, self.alice_token = self.gateway.store.create_user("Alice")
        self.bob, self.bob_token = self.gateway.store.create_user("Bob")
        self.http = TestClient(TestServer(self.gateway.create_app()))
        await self.http.start_server()
        self.gateway_url = str(self.http.make_url("/")).rstrip("/")
        self.managers = []
        self.finish = asyncio.Event()
        self.worker_requests = []
        self.wav = sample_wav()
        self.process_patch = patch("voice.asyncio.create_subprocess_exec", new=AsyncMock(side_effect=self.host_worker))
        self.process_patch.start()
        self.client, self.client_installer, self.client_assets, self.client_comfy = self.make_client("alice", self.alice_token)

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.shutdown()
        await self.host.shutdown()
        self.process_patch.stop()
        await self.http.close()
        await self.engine.close()
        self.temporary.cleanup()

    @staticmethod
    def asset_path(directory, asset_id):
        matches = [path for path in directory.glob(f"{asset_id}.*") if path.suffix != ".json"]
        if not matches:
            raise ValueError("missing audio")
        return matches[0]

    def make_client(self, name, token):
        root = self.root / name
        assets = root / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        installer = VoiceInstaller(root)
        installer.runtime_installed = Mock(return_value=False)
        installer.model_installed = Mock(return_value=False)
        comfy = ComfyClient(ConnectionSettings(mode="remote", base_url=self.gateway_url,
                                              remote_access_token=token, comfy_dir=str(root / "missing-comfy"),
                                              auto_start_local=False), root)
        manager = VoiceJobManager(root, asyncio.Lock(), installer,
                                  lambda asset_id: self.asset_path(assets, asset_id), comfy=comfy)
        manager.remote_poll_interval = .01
        self.managers.append(manager)
        return manager, installer, assets, comfy

    async def host_worker(self, *args, **_kwargs):
        self.assertEqual(args[0], str(self.host_installer.python_path), "colleague must never launch a local model")
        request = Path(args[args.index("--request") + 1])
        output = Path(args[args.index("--output") + 1])
        self.assertEqual(request.parent, self.host.job_dir)
        self.assertEqual(output.parent, self.host.output_dir)
        compiled = json.loads(request.read_text(encoding="utf-8"))
        self.assertTrue(Path(compiled["model_dir"]).is_relative_to(self.host_root))
        self.worker_requests.append(compiled)
        return FakeVoiceProcess(output, self.finish, self.wav)

    @staticmethod
    async def wait_until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.01)

    def payload(self, **changes):
        return {"mode": "custom", "text": "使用 GPU 主機產生語音", "job_name": "遠端旁白", "seed": 42, **changes}

    async def complete(self, manager, job):
        self.finish.set()
        await asyncio.wait_for(manager.tasks[job["id"]], timeout=5)
        result = manager.jobs[job["id"]]
        self.assertEqual(result["status"], "completed", result.get("error"))
        return result

    async def test_model_is_only_required_on_host_and_wav_is_saved_on_colleague_computer(self):
        status = await self.client.public_status()
        self.assertTrue(status["models"]["custom"]["installed"])
        self.assertEqual(status["connection_mode"], "remote")
        self.assertFalse(status["can_install"])
        job = self.client.create(self.payload())
        completed = await self.complete(self.client, job)
        self.assertEqual(completed["engine_mode"], "remote")
        self.assertEqual(len(self.host.jobs), 1)
        self.assertEqual(len(self.worker_requests), 1)
        self.assertNotEqual(completed["id"], completed["remote_job_id"])
        path = self.client.local_output_path(completed)
        self.assertEqual(path.parent, self.client.output_dir)
        self.assertEqual(path.read_bytes(), self.wav)
        with wave.open(str(path)) as audio:
            self.assertEqual(audio.getnframes(), 3200)
        self.client_installer.runtime_installed.assert_not_called()
        self.client_installer.model_installed.assert_not_called()
        self.assertIsNone(self.client_installer.task)
        persisted = (self.client.job_dir / f"{job['id']}.json").read_text(encoding="utf-8")
        self.assertNotIn(self.alice_token, persisted)
        self.assertEqual(list(self.client_installer.model_root.glob("**/*.safetensors")), [])

    async def test_clone_uploads_reference_to_host_and_enforces_personal_ownership(self):
        local_reference = uuid.uuid4().hex
        (self.client_assets / f"{local_reference}.wav").write_bytes(self.wav)
        payload = self.payload(mode="clone", reference_asset_id=local_reference,
                               reference_text="參考台詞", voice_authorized=True)
        completed = await self.complete(self.client, self.client.create(payload))
        remote_reference = completed["remote_reference_id"]
        self.assertNotEqual(remote_reference, local_reference)
        remote_path = self.asset_path(self.host_assets, remote_reference)
        self.assertEqual(remote_path.read_bytes(), self.wav)
        self.assertEqual(self.worker_requests[0]["reference_audio"], str(remote_path))
        self.assertEqual(self.gateway.store.state["voice_asset_owners"][remote_reference]["user_id"], self.alice["id"])
        bob = RemoteVoiceConnection(self.gateway_url, self.bob_token)
        with self.assertRaises(RemoteVoiceError):
            await bob.get(completed["remote_job_id"])
        with self.assertRaises(RemoteVoiceError):
            await bob.create(compile_voice_request(payload), uuid.uuid4().hex, remote_reference)
        self.assertEqual(len(self.host.jobs), 1)

    async def test_client_restart_reconnects_same_host_job_without_duplicate_generation(self):
        job = self.client.create(self.payload())
        await self.wait_until(lambda: job.get("remote_job_id") and self.worker_requests)
        host_id = job["remote_job_id"]
        await self.client.shutdown()
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(self.host.jobs[host_id]["status"], "running")
        reloaded, installer, _, _ = self.make_client("alice", self.alice_token)
        recovered = reloaded.resume(job["id"])
        completed = await self.complete(reloaded, recovered)
        self.assertEqual(completed["remote_job_id"], host_id)
        self.assertEqual(len(self.host.jobs), 1)
        self.assertEqual(len(self.worker_requests), 1)
        self.assertEqual(reloaded.local_output_path(completed).read_bytes(), self.wav)
        installer.runtime_installed.assert_not_called()
        installer.model_installed.assert_not_called()

    async def test_cancelling_one_colleague_voice_keeps_another_colleague_job(self):
        alice_job = self.client.create(self.payload())
        await self.wait_until(lambda: alice_job.get("remote_job_id") and self.worker_requests)
        bob, _, _, _ = self.make_client("bob", self.bob_token)
        bob_job = bob.create(self.payload(text="另一位同事的台詞"))
        await self.wait_until(lambda: bob_job.get("remote_job_id"))
        await self.client.cancel(alice_job["id"])
        await asyncio.wait_for(self.client.tasks[alice_job["id"]], timeout=5)
        completed = await self.complete(bob, bob_job)
        self.assertEqual(alice_job["status"], "cancelled")
        self.assertEqual(self.host.jobs[alice_job["remote_job_id"]]["status"], "cancelled")
        self.assertEqual(self.host.jobs[completed["remote_job_id"]]["status"], "completed")
        self.assertEqual(len(self.host.jobs), 2)
        self.assertEqual(bob.local_output_path(completed).read_bytes(), self.wav)


if __name__ == "__main__":
    unittest.main()
