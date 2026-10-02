import asyncio
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from voice import VOICE_MODELS, VoiceError, VoiceInstaller, VoiceJobManager


REMOTE_JOB_ID = "0" * 31 + "1"
REMOTE_REFERENCE_ID = "b" * 32


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 2400)
    return output.getvalue()


class RemoteVoiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise remote voice through HTTP without models, CUDA, or user data."""

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reference = self.root / "reference.wav"
        self.reference.write_bytes(wav_bytes())
        self.submissions = []
        self.references = []
        self.cancellations = []
        self.resumptions = []
        self.remote_jobs = {}
        self.auth_requests = []
        self.status_code = 200
        self.status_error = ""
        self.missing_models = set()
        self.audio_code = 200
        self.audio_body = wav_bytes()
        self.audio_started = asyncio.Event()
        self.audio_release = None
        self.remote_state = "completed"
        self.post_started = asyncio.Event()
        self.post_release = None
        self.fail_first_submission = False
        self.managers = []

        @web.middleware
        async def personal_token(request, handler):
            self.auth_requests.append((request.path, request.headers.get("Authorization")))
            if request.headers.get("Authorization") != "Bearer test-personal-token":
                return web.json_response({"error": "invalid access token"}, status=401)
            return await handler(request)

        app = web.Application(middlewares=[personal_token])
        app.router.add_get("/api/voice/status", self.voice_status)
        app.router.add_post("/api/voice/references", self.upload_reference)
        app.router.add_post("/api/voice/jobs", self.create_remote_job)
        app.router.add_get("/api/voice/jobs/{job_id}", self.get_remote_job)
        app.router.add_post("/api/voice/jobs/{job_id}/cancel", self.cancel_remote_job)
        app.router.add_post("/api/voice/jobs/{job_id}/resume", self.resume_remote_job)
        app.router.add_get("/api/voice/jobs/{job_id}/audio", self.remote_audio)
        self.server = TestServer(app)
        await self.server.start_server()
        self.comfy = SimpleNamespace(
            mode="remote",
            base_url=str(self.server.make_url("/")).rstrip("/"),
            remote_access_token="test-personal-token",
            auth_headers=lambda *_args: {"Authorization": "Bearer test-personal-token"},
        )
        self.gpu_lock = asyncio.Lock()
        self.installer = VoiceInstaller(self.root / "client-data")
        # Remote mode must not inspect a nonexistent client runtime or weights.
        self.installer.runtime_installed = Mock(side_effect=AssertionError("client runtime inspected"))
        self.installer.model_installed = Mock(side_effect=AssertionError("client model inspected"))
        self.manager = self.make_manager()

    async def asyncTearDown(self):
        if self.post_release:
            self.post_release.set()
        if self.audio_release:
            self.audio_release.set()
        for manager in self.managers:
            await manager.shutdown()
        await self.server.close()
        self.temporary.cleanup()

    def make_manager(self):
        manager = VoiceJobManager(
            self.root / "client-data", self.gpu_lock, self.installer,
            lambda _asset_id: self.reference, comfy=self.comfy,
        )
        manager.remote_poll_interval = 0.01
        self.managers.append(manager)
        return manager

    @staticmethod
    def request(mode="custom", **changes):
        payload = {
            "mode": mode, "text": "恭喜進入免費遊戲！", "language": "Chinese",
            "speaker": "Vivian", "job_name": "主管報告旁白", "seed": 42,
        }
        if mode == "design":
            payload["instruct"] = "溫暖成熟，語速自然，清楚咬字。"
        if mode == "clone":
            payload.update(
                reference_asset_id="local-reference", reference_text="參考聲音台詞",
                voice_authorized=True,
            )
        return {**payload, **changes}

    async def finished(self, job):
        await asyncio.wait_for(asyncio.shield(self.manager.tasks[job["id"]]), timeout=10)
        return self.manager.jobs[job["id"]]

    async def wait_remote_id(self, job):
        async def wait():
            while not job.get("remote_job_id"):
                task = self.manager.tasks[job["id"]]
                if task.done():
                    self.fail(f"Remote submission stopped early: {job}")
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), timeout=5)
        return job["remote_job_id"]

    async def voice_status(self, _request):
        if self.status_code != 200:
            return web.json_response({"error": self.status_error or "voice unavailable"}, status=self.status_code)
        return web.json_response({
            "state": "complete", "active": False, "runtime_installed": True,
            "installed": True, "error": "", "current": "", "requested_mode": "custom",
            "models": {
                mode: {**definition, "installed": mode not in self.missing_models}
                for mode, definition in VOICE_MODELS.items()
            },
        })

    async def upload_reference(self, request):
        parts = []
        reader = await request.multipart()
        async for part in reader:
            parts.append({"name": part.name, "filename": part.filename, "body": bytes(await part.read())})
        self.references.append(parts)
        return web.json_response({"id": REMOTE_REFERENCE_ID}, status=201)

    async def create_remote_job(self, request):
        payload = await request.json()
        self.submissions.append(payload)
        # The host deduplicates uncertain submissions by client job ID.
        client_job_id = str(payload.get("client_job_id") or "")
        job = next((item for item in self.remote_jobs.values() if item["client_job_id"] == client_job_id), None)
        if job is None:
            remote_id = f"{len(self.remote_jobs) + 1:032x}"
            job = {"id": remote_id, "client_job_id": client_job_id, "status": "queued", "progress": 0}
            self.remote_jobs[remote_id] = job
        self.post_started.set()
        if self.post_release is not None:
            await self.post_release.wait()
        if self.fail_first_submission and len(self.submissions) == 1:
            return web.json_response({"error": "temporarily unavailable"}, status=503)
        return web.json_response(job, status=202)

    async def get_remote_job(self, request):
        job = self.remote_jobs.get(request.match_info["job_id"])
        if job is None:
            raise web.HTTPNotFound()
        if job["status"] not in {"cancelled", "failed"}:
            job["status"] = self.remote_state
        return web.json_response({**job, "progress": 100 if job["status"] == "completed" else 35})

    async def cancel_remote_job(self, request):
        job_id = request.match_info["job_id"]
        self.cancellations.append(job_id)
        self.remote_jobs[job_id]["status"] = "cancelled"
        return web.json_response(self.remote_jobs[job_id])

    async def resume_remote_job(self, request):
        job_id = request.match_info["job_id"]
        self.resumptions.append(job_id)
        self.remote_jobs[job_id]["status"] = "queued"
        return web.json_response(self.remote_jobs[job_id], status=202)

    async def remote_audio(self, request):
        if request.match_info["job_id"] not in self.remote_jobs:
            raise web.HTTPNotFound()
        if self.audio_release is not None:
            response = web.StreamResponse(status=self.audio_code, headers={"Content-Type": "audio/wav"})
            await response.prepare(request)
            await response.write(self.audio_body[:44])
            self.audio_started.set()
            await self.audio_release.wait()
            try:
                await response.write_eof(self.audio_body[44:])
            except (ConnectionResetError, RuntimeError):
                # A cancelled client intentionally disconnects this stalled stream.
                pass
            return response
        return web.Response(body=self.audio_body, status=self.audio_code, content_type="audio/wav")

    async def test_custom_voice_completes_without_client_models_or_gpu_lock(self):
        await self.gpu_lock.acquire()
        try:
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as subprocess:
                job = self.manager.create(self.request())
                result = await self.finished(job)
                subprocess.assert_not_called()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["engine_mode"], "remote")
            self.assertEqual(result["remote_base_url"], self.comfy.base_url)
            self.assertEqual(result["remote_job_id"], REMOTE_JOB_ID)
            self.assertTrue(self.gpu_lock.locked())
            self.assertEqual(self.manager.local_output_path(result).read_bytes(), wav_bytes())
            self.assertFalse((self.root / "client-data" / "voice_runtime").exists())
            self.assertEqual(self.submissions[0]["job_name"], "主管報告旁白")
            self.assertEqual(self.submissions[0]["seed"], 42)
            self.assertEqual(self.submissions[0]["client_job_id"], job["id"])
            self.assertTrue(all(header == "Bearer test-personal-token" for _, header in self.auth_requests))
        finally:
            self.gpu_lock.release()

    async def test_design_voice_forwards_description(self):
        payload = self.request("design")
        result = await self.finished(self.manager.create(payload))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.submissions[0]["mode"], "design")
        self.assertEqual(self.submissions[0]["instruct"], payload["instruct"])
        self.assertEqual(self.references, [])

    async def test_clone_uploads_reference_and_uses_host_asset_id(self):
        result = await self.finished(self.manager.create(self.request("clone")))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.references), 1)
        self.assertEqual(len(self.references[0]), 1)
        part = self.references[0][0]
        self.assertEqual(part["name"], "file")
        self.assertEqual(part["filename"], "reference.wav")
        self.assertEqual(part["body"], self.reference.read_bytes())
        self.assertEqual(self.submissions[0]["reference_asset_id"], REMOTE_REFERENCE_ID)
        self.assertEqual(self.submissions[0]["reference_text"], "參考聲音台詞")
        self.assertTrue(self.submissions[0]["voice_authorized"])
        persisted_request = json.loads((self.manager.job_dir / f"{result['id']}.request.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted_request["reference_asset_id"], "local-reference")

    async def test_remote_clone_still_requires_permission_and_valid_reference(self):
        with self.assertRaisesRegex(VoiceError, "有權"):
            self.manager.create(self.request("clone", voice_authorized=False))
        self.reference = self.root / "reference.mp4"
        self.reference.write_bytes(b"video")
        with self.assertRaisesRegex(VoiceError, "音訊檔"):
            self.manager.create(self.request("clone"))
        self.assertEqual(self.submissions, [])

    async def test_status_reports_host_models_without_client_checks(self):
        status = await self.manager.public_status()
        self.assertTrue(status["runtime_installed"])
        self.assertTrue(all(status["models"][mode]["installed"] for mode in VOICE_MODELS))
        self.assertEqual(status["connection_mode"], "remote")
        self.assertFalse(status["can_install"])
        self.installer.runtime_installed.assert_not_called()
        self.installer.model_installed.assert_not_called()

    async def test_rejected_host_auth_does_not_start_a_job(self):
        self.status_code = 401
        self.status_error = "Invalid token"
        result = await self.finished(self.manager.create(self.request()))
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], "Token|token|驗證|授權|金鑰")
        self.assertEqual(self.submissions, [])
        self.assertIsNone(self.manager.local_output_path(result))

    async def test_status_capability_errors_disable_remote_generation_and_client_install(self):
        for code in (401, 404):
            with self.subTest(code=code):
                self.status_code = code
                status = await self.manager.public_status()
                self.assertFalse(status["available"])
                self.assertFalse(status["can_install"])
                self.assertFalse(status["runtime_installed"])
                self.assertEqual(status["connection_mode"], "remote")
                self.assertTrue(status["error"])
        self.installer.runtime_installed.assert_not_called()
        self.installer.model_installed.assert_not_called()

    async def test_old_gateway_voice_capability_is_reported_clearly(self):
        self.status_code = 404
        result = await self.finished(self.manager.create(self.request()))
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], "更新|語音|Gateway")
        self.assertEqual(self.submissions, [])

    async def test_missing_model_on_host_does_not_inspect_or_install_client_model(self):
        self.missing_models.add("design")
        result = await self.finished(self.manager.create(self.request("design")))
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], "主機|遠端")
        self.assertEqual(self.submissions, [])
        self.installer.model_installed.assert_not_called()

    async def test_audio_http_failure_never_marks_local_job_completed(self):
        self.audio_code = 503
        self.audio_body = b"unavailable"
        result = await self.finished(self.manager.create(self.request()))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(self.manager.local_output_path(result))

    async def test_invalid_audio_payload_never_marks_local_job_completed(self):
        self.audio_body = b"This is an HTML error masquerading as audio" * 10
        result = await self.finished(self.manager.create(self.request()))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(self.manager.local_output_path(result))

    async def assert_truncated_audio_can_be_retrieved_again(self, truncated):
        self.audio_body = truncated
        job = self.manager.create(self.request())
        result = await self.finished(job)
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], "不完整|WAV")
        self.assertIsNone(self.manager.local_output_path(result))
        self.assertFalse((self.manager.output_dir / f"{job['id']}.wav").exists())
        self.assertFalse((self.manager.output_dir / f"{job['id']}.wav.part").exists())
        self.assertEqual(job["remote_job_id"], REMOTE_JOB_ID)
        self.audio_body = wav_bytes()
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["remote_job_id"], REMOTE_JOB_ID)
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(self.resumptions, [])
        self.assertEqual(self.manager.local_output_path(result).read_bytes(), wav_bytes())
        self.assertFalse((self.manager.output_dir / f"{job['id']}.wav.part").exists())

    async def test_wav_header_without_declared_pcm_frames_never_completes_and_can_resume(self):
        complete = wav_bytes()
        header_only = complete[:44]
        with wave.open(io.BytesIO(header_only), "rb") as audio:
            self.assertEqual(audio.getnframes(), 2400)
            self.assertEqual(audio.readframes(audio.getnframes()), b"")
        await self.assert_truncated_audio_can_be_retrieved_again(header_only)

    async def test_wav_with_half_declared_pcm_frames_never_completes_and_can_resume(self):
        complete = wav_bytes()
        half_pcm = complete[:44 + (len(complete) - 44) // 2]
        with wave.open(io.BytesIO(half_pcm), "rb") as audio:
            self.assertEqual(audio.getnframes(), 2400)
            self.assertEqual(len(audio.readframes(audio.getnframes())), 2400)
        await self.assert_truncated_audio_can_be_retrieved_again(half_pcm)

    async def test_cancel_aborts_stalled_audio_download_without_waiting_for_stream(self):
        self.audio_release = asyncio.Event()
        job = self.manager.create(self.request())
        await asyncio.wait_for(self.audio_started.wait(), timeout=5)
        partial_path = self.manager.output_dir / f"{job['id']}.wav.part"

        async def wait_partial_file():
            while not partial_path.exists():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait_partial_file(), timeout=5)
        await asyncio.wait_for(self.manager.cancel(job["id"]), timeout=2)
        await asyncio.wait_for(asyncio.shield(self.manager.tasks[job["id"]]), timeout=2)
        self.assertFalse(self.audio_release.is_set())
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(self.cancellations, [REMOTE_JOB_ID])
        self.assertIsNone(self.manager.local_output_path(job))
        self.assertFalse(partial_path.exists())
        self.assertFalse((self.manager.output_dir / f"{job['id']}.wav").exists())

    async def test_cancel_during_submission_cancels_job_once_host_id_arrives(self):
        self.remote_state = "running"
        self.post_release = asyncio.Event()
        job = self.manager.create(self.request())
        await asyncio.wait_for(self.post_started.wait(), timeout=5)
        cancelling = asyncio.create_task(self.manager.cancel(job["id"]))
        await asyncio.sleep(0)
        self.post_release.set()
        await asyncio.wait_for(cancelling, timeout=5)
        result = await self.finished(job)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.cancellations, [REMOTE_JOB_ID])
        self.assertEqual(self.remote_jobs[REMOTE_JOB_ID]["status"], "cancelled")

    async def test_cancel_before_submission_never_creates_host_job(self):
        job = self.manager.create(self.request())
        await self.manager.cancel(job["id"])
        result = await self.finished(job)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.submissions, [])
        self.assertEqual(self.cancellations, [])

    async def test_resume_after_download_failure_reuses_existing_host_job(self):
        self.audio_code = 503
        job = self.manager.create(self.request())
        self.assertEqual((await self.finished(job))["status"], "failed")
        self.audio_code = 200
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["remote_job_id"], REMOTE_JOB_ID)
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(len(self.remote_jobs), 1)
        self.assertEqual(self.resumptions, [])

    async def test_resume_failed_host_job_restarts_same_host_id(self):
        self.remote_state = "failed"
        job = self.manager.create(self.request())
        self.assertEqual((await self.finished(job))["status"], "failed")
        self.remote_state = "completed"
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.resumptions, [REMOTE_JOB_ID])
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(len(self.remote_jobs), 1)

    async def test_uncertain_submission_retries_with_same_idempotency_key(self):
        self.fail_first_submission = True
        job = self.manager.create(self.request())
        result = await self.finished(job)
        if result["status"] == "failed":
            result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.remote_jobs), 1)
        self.assertGreaterEqual(len(self.submissions), 2)
        self.assertEqual({payload["client_job_id"] for payload in self.submissions}, {job["id"]})

    async def test_automatically_chosen_seed_stays_fixed_when_uncertain_submission_is_retried(self):
        self.fail_first_submission = True
        payload = self.request()
        payload.pop("seed")
        job = self.manager.create(payload)
        original_seed = job["seed"]
        await self.finished(job)
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual({payload["seed"] for payload in self.submissions}, {original_seed})
        persisted = json.loads((self.manager.job_dir / f"{job['id']}.request.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["seed"], original_seed)

    async def test_shutdown_only_interrupts_local_monitor_and_reload_resumes_same_job(self):
        self.remote_state = "running"
        job = self.manager.create(self.request())
        await self.wait_remote_id(job)
        await self.manager.shutdown()
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(self.cancellations, [])
        self.assertNotEqual(self.remote_jobs[REMOTE_JOB_ID]["status"], "cancelled")
        self.manager = self.make_manager()
        self.remote_state = "completed"
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(result["remote_job_id"], REMOTE_JOB_ID)

    async def test_shutdown_during_submission_can_recover_accepted_job_without_duplicates(self):
        self.post_release = asyncio.Event()
        job = self.manager.create(self.request())
        await asyncio.wait_for(self.post_started.wait(), timeout=5)
        await self.manager.shutdown()
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(self.cancellations, [])
        self.post_release.set()
        self.manager = self.make_manager()
        result = await self.finished(self.manager.resume(job["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["remote_job_id"], REMOTE_JOB_ID)
        self.assertEqual(len(self.remote_jobs), 1)
        self.assertEqual({payload["client_job_id"] for payload in self.submissions}, {job["id"]})

    async def test_resume_refuses_changed_remote_endpoint(self):
        self.audio_code = 503
        job = self.manager.create(self.request())
        await self.finished(job)
        self.comfy.base_url = "http://127.0.0.1:9"
        with self.assertRaisesRegex(VoiceError, "主機|連線|遠端|位址"):
            self.manager.resume(job["id"])
        self.assertEqual(len(self.submissions), 1)


if __name__ == "__main__":
    unittest.main()
