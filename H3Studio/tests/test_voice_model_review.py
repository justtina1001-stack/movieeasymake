"""Independent model-selection review; no weights, CUDA, or production data."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from voice import VOICE_MODELS, VoiceError, VoiceInstaller, VoiceJobManager, compile_voice_request
import voice_worker


TEXT = "今天終於等到你，我們一起回家吧。"
JOY = "帶著喜悅與期待，音調明亮，節奏輕快。"
LOW = "低沉而克制，聲音沉穩，語速稍慢。"


class VoiceModelPersistenceReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.installer = VoiceInstaller(self.root)
        self.installer.runtime_installed = lambda: True
        self.installer.model_installed = lambda _model: True
        self.manager = VoiceJobManager(self.root, asyncio.Lock(), self.installer, lambda _id: self.root / "ref.wav")
        self.compiled = []

        async def capture(job_id, compiled):
            self.compiled.append(dict(compiled))
            self.manager.update(job_id, status="failed", error="review worker deliberately skipped")

        self.manager._run = capture

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def test_explicit_model_survives_restart_and_resume(self):
        job = self.manager.create({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT, "instruct": JOY, "seed": 42})
        await self.manager.tasks[job["id"]]
        self.assertEqual(job["mode"], "custom")
        self.assertEqual(job["model_id"], "custom_1_7b")
        saved = json.loads((self.manager.job_dir / f"{job['id']}.request.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["model_id"], "custom_1_7b")
        self.assertEqual(saved["instruct"], JOY)
        replacement = VoiceJobManager(self.root, self.manager.gpu_lock, self.installer, lambda _id: self.root / "ref.wav")
        resumed = []

        async def capture(_job_id, compiled):
            resumed.append(compiled)

        replacement._run = capture
        try:
            replacement.resume(job["id"])
            await replacement.tasks[job["id"]]
            self.assertEqual(resumed[0]["model_id"], "custom_1_7b")
            self.assertEqual(resumed[0]["instruct"], JOY)
            self.assertEqual(resumed[0]["seed"], 42)
        finally:
            await replacement.shutdown()

    async def test_legacy_hidden_instruction_does_not_upgrade_old_work(self):
        # This request reproduces the old UI: every mode sent the hidden field.
        job = self.manager.create({"mode": "custom", "text": TEXT, "instruct": LOW, "seed": 7})
        await self.manager.tasks[job["id"]]
        self.assertEqual(self.compiled[0]["model_id"], "custom")
        self.assertEqual(self.compiled[0]["instruct"], "")
        # Simulate a pre-upgrade request that has no model_id on disk.
        path = self.manager.job_dir / f"{job['id']}.request.json"
        path.write_text(json.dumps({"mode": "custom", "text": TEXT, "instruct": LOW, "seed": 7}), encoding="utf-8")
        self.manager.resume(job["id"])
        await self.manager.tasks[job["id"]]
        self.assertEqual(self.compiled[-1]["model_id"], "custom")
        self.assertEqual(self.compiled[-1]["instruct"], "")
        self.assertEqual(self.compiled[-1]["seed"], 7)

    async def test_invalid_model_does_not_persist_or_queue_a_job(self):
        for model_id in ("missing_model", "clone_1_7b", "design"):
            with self.subTest(model_id=model_id), self.assertRaises(VoiceError):
                self.manager.create({"mode": "custom", "model_id": model_id, "text": TEXT})
        self.assertEqual(self.manager.jobs, {})
        self.assertEqual(list(self.manager.job_dir.glob("*.request.json")), [])

    async def test_one_gpu_lock_serializes_different_model_workers(self):
        # Use the production manager runner with a fake subprocess only.
        manager = VoiceJobManager(self.root / "serial", asyncio.Lock(), self.installer, lambda _id: self.root / "ref.wav")
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        requests = []

        async def launch(*args, **_kwargs):
            self.assertTrue(manager.gpu_lock.locked())
            request_path = Path(args[args.index("--request") + 1])
            output = Path(args[args.index("--output") + 1])
            requests.append(json.loads(request_path.read_text(encoding="utf-8")))
            number = len(requests)

            async def communicate():
                if number == 1:
                    first_started.set()
                    await first_release.wait()
                output.write_bytes(b"RIFF" + b"0" * 200)
                return b"", b""

            return SimpleNamespace(returncode=0, communicate=communicate)

        try:
            with patch("voice.asyncio.create_subprocess_exec", AsyncMock(side_effect=launch)):
                first = manager.create({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT, "instruct": JOY})
                await asyncio.wait_for(first_started.wait(), 5)
                second = manager.create({"mode": "custom", "model_id": "custom", "text": TEXT})
                await asyncio.sleep(0)
                self.assertEqual(len(requests), 1)
                self.assertEqual(second["status"], "queued")
                first_release.set()
                await asyncio.wait_for(asyncio.gather(manager.tasks[first["id"]], manager.tasks[second["id"]]), 5)
            self.assertEqual([item["model_id"] for item in requests], ["custom_1_7b", "custom"])
            self.assertTrue(requests[0]["model_dir"].endswith(VOICE_MODELS["custom_1_7b"]["directory"]))
            self.assertTrue(requests[1]["model_dir"].endswith(VOICE_MODELS["custom"]["directory"]))
            self.assertFalse(manager.gpu_lock.locked())
        finally:
            first_release.set()
            await manager.shutdown()

    async def test_cancelled_worker_keeps_lock_until_the_process_finishes(self):
        manager = VoiceJobManager(self.root / "drain", asyncio.Lock(), self.installer, lambda _id: self.root / "ref.wav")
        first_started, terminated, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        launched = []

        async def launch(*args, **_kwargs):
            output = Path(args[args.index("--output") + 1])
            process = SimpleNamespace(returncode=None)
            launched.append(process)
            number = len(launched)

            def terminate():
                terminated.set()

            async def communicate():
                if number == 1:
                    first_started.set()
                    await exited.wait()
                    process.returncode = -15
                else:
                    output.write_bytes(b"RIFF" + b"0" * 200)
                    process.returncode = 0
                return b"", b""

            process.terminate = terminate
            process.kill = terminate
            process.communicate = communicate
            return process

        try:
            with patch("voice.asyncio.create_subprocess_exec", AsyncMock(side_effect=launch)):
                first = manager.create({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT})
                await asyncio.wait_for(first_started.wait(), 5)
                second = manager.create({"mode": "custom", "model_id": "custom", "text": TEXT})
                manager.tasks[first["id"]].cancel()
                await asyncio.wait_for(terminated.wait(), 5)
                self.assertTrue(manager.gpu_lock.locked())
                self.assertEqual(len(launched), 1)
                exited.set()
                await asyncio.wait_for(asyncio.gather(manager.tasks[first["id"]], manager.tasks[second["id"]]), 5)
            self.assertEqual(first["status"], "cancelled")
            self.assertEqual(second["status"], "completed")
            self.assertFalse(manager.gpu_lock.locked())
        finally:
            exited.set()
            await manager.shutdown()


class VoiceWorkerInstructionReview(unittest.TestCase):
    def exercise(self, mode, model_id, instruction):
        sdk = SimpleNamespace(
            generate_custom_voice=Mock(return_value=([[0.0]], 24000)),
            generate_voice_design=Mock(return_value=([[0.0]], 24000)),
            generate_voice_clone=Mock(return_value=([[0.0]], 24000)),
        )
        loader = Mock(return_value=sdk)
        fake_torch = SimpleNamespace(
            bfloat16="mock-bfloat16", manual_seed=Mock(),
            cuda=SimpleNamespace(is_available=lambda: True, manual_seed_all=Mock()),
        )
        fake_sf = SimpleNamespace(write=Mock())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text(json.dumps({
                "mode": mode, "model_id": model_id, "text": TEXT, "language": "Chinese",
                "speaker": "Vivian", "instruct": instruction, "seed": 42,
                "model_dir": str(root / VOICE_MODELS[model_id]["directory"]),
                "reference_audio": str(root / "ref.wav"), "reference_text": "測試聲線",
                "x_vector_only": False,
            }), encoding="utf-8")
            with patch.dict("sys.modules", {
                "torch": fake_torch, "soundfile": fake_sf,
                "qwen_tts": SimpleNamespace(Qwen3TTSModel=SimpleNamespace(from_pretrained=loader)),
            }), patch("builtins.print"):
                voice_worker.generate(request, root / "output.wav")
        return sdk, loader

    def test_custom_1_7b_passes_each_emotion_instruction_to_production_sdk_call(self):
        for instruction in (JOY, LOW):
            with self.subTest(instruction=instruction):
                sdk, loader = self.exercise("custom", "custom_1_7b", instruction)
                self.assertEqual(sdk.generate_custom_voice.call_args.kwargs["instruct"], instruction)
                self.assertEqual(loader.call_args.kwargs["attn_implementation"], "sdpa")
                sdk.generate_voice_design.assert_not_called()
                sdk.generate_voice_clone.assert_not_called()

    def test_custom_0_6b_does_not_forward_an_unsupported_instruction(self):
        sdk, _loader = self.exercise("custom", "custom", JOY)
        self.assertNotIn("instruct", sdk.generate_custom_voice.call_args.kwargs)

    def test_clone_1_7b_keeps_canonical_mode_without_forwarding_instruction(self):
        sdk, _loader = self.exercise("clone", "clone_1_7b", JOY)
        self.assertNotIn("instruct", sdk.generate_voice_clone.call_args.kwargs)
        self.assertEqual(sdk.generate_voice_clone.call_args.kwargs["ref_text"], "測試聲線")
        sdk.generate_custom_voice.assert_not_called()

    def test_design_still_forwards_its_required_description(self):
        sdk, _loader = self.exercise("design", "design", LOW)
        self.assertEqual(sdk.generate_voice_design.call_args.kwargs["instruct"], LOW)


class VoiceRemoteModelReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.submissions = []
        self.capabilities = None
        self.remote_models = {key: {**VOICE_MODELS[key], "installed": True} for key in ("custom", "design", "clone")}

        async def status(_request):
            payload = {"models": self.remote_models, "runtime_installed": True}
            if self.capabilities is not None:
                payload["capabilities"] = self.capabilities
            return web.json_response(payload)

        async def submit(request):
            self.submissions.append(await request.json())
            return web.json_response({"id": "a" * 32, "status": "failed", "error": "mock host stops after submission"})

        app = web.Application()
        app.router.add_get("/api/voice/status", status)
        app.router.add_post("/api/voice/jobs", submit)
        self.server = TestServer(app)
        await self.server.start_server()
        comfy = SimpleNamespace(mode="remote", base_url=str(self.server.make_url("/")).rstrip("/"), remote_access_token="review-token")
        installer = VoiceInstaller(self.root)
        installer.runtime_installed = Mock(side_effect=AssertionError("remote work inspected client runtime"))
        installer.model_installed = Mock(side_effect=AssertionError("remote work inspected client model"))
        self.manager = VoiceJobManager(self.root, asyncio.Lock(), installer, lambda _id: self.root / "ref.wav", comfy=comfy)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        await self.server.close()
        self.temp.cleanup()

    async def submit_and_finish(self, payload):
        job = self.manager.create(payload)
        await asyncio.wait_for(self.manager.tasks[job["id"]], 5)
        return job

    async def test_explicit_new_model_fails_before_submission_to_legacy_host(self):
        job = await self.submit_and_finish({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT, "instruct": JOY})
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.submissions, [])
        self.assertFalse(self.manager.gpu_lock.locked())

    async def test_legacy_model_remains_usable_with_a_legacy_host(self):
        await self.submit_and_finish({"mode": "custom", "model_id": "custom", "text": TEXT, "seed": 42})
        self.assertEqual(len(self.submissions), 1)
        payload = self.submissions[0]
        self.assertEqual(payload["mode"], "custom")
        self.assertEqual(payload["seed"], 42)
        # The old host ignores unknown keys. A retained legacy model_id is
        # compatible as long as it cannot select a different model there.
        self.assertEqual(payload.get("model_id", "custom"), "custom")
        self.assertFalse(payload.get("instruct"))

    async def test_new_model_and_instruction_reach_capable_host(self):
        self.capabilities = {"model_selection": True, "custom_voice_instruct": True}
        self.remote_models = {key: {**value, "installed": True} for key, value in VOICE_MODELS.items()}
        await self.submit_and_finish({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT, "instruct": JOY, "seed": 42})
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(self.submissions[0]["mode"], "custom")
        self.assertEqual(self.submissions[0]["model_id"], "custom_1_7b")
        self.assertEqual(self.submissions[0]["instruct"], JOY)
        self.assertFalse(self.manager.gpu_lock.locked())

    async def test_explicitly_false_capabilities_override_model_metadata(self):
        self.remote_models = {key: {**value, "installed": True} for key, value in VOICE_MODELS.items()}
        for capabilities in (
            {"model_selection": False, "custom_voice_instruct": True},
            {"model_selection": True, "custom_voice_instruct": False},
        ):
            with self.subTest(capabilities=capabilities):
                self.capabilities = capabilities
                job = await self.submit_and_finish({"mode": "custom", "model_id": "custom_1_7b", "text": TEXT, "instruct": JOY})
                self.assertEqual(job["status"], "failed")
                self.assertEqual(self.submissions, [])


if __name__ == "__main__":
    unittest.main()
