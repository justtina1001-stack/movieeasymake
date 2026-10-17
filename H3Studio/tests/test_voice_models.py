import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from remote_voice import RemoteVoiceConnection, RemoteVoiceError
from voice import VOICE_MODELS, VoiceError, VoiceInstaller, VoiceJobManager, compile_voice_request
import voice_worker


class ModelRequestTests(unittest.TestCase):
    def test_legacy_modes_keep_original_models_and_ignore_hidden_design_field(self):
        for mode in ("custom", "design", "clone"):
            payload = {"mode": mode, "text": "測試", "instruct": "清楚溫暖。",
                       "reference_asset_id": "reference", "reference_text": "參考", "voice_authorized": True}
            compiled = compile_voice_request(payload)
            self.assertEqual(compiled["model_id"], mode)
            self.assertEqual(compiled["instruct"], payload["instruct"] if mode == "design" else "")

    def test_explicit_model_must_exist_and_match_canonical_mode(self):
        for model_id in ("", None, "missing", "clone_1_7b", "design"):
            with self.subTest(model_id=model_id), self.assertRaises(VoiceError):
                compile_voice_request({"mode": "custom", "model_id": model_id, "text": "測試"})
        with self.assertRaises(VoiceError):
            compile_voice_request({"mode": "custom_1_7b", "text": "測試"})

    def test_only_large_custom_voice_accepts_performance_instruction(self):
        compiled = compile_voice_request({"mode": "custom", "model_id": "custom_1_7b",
                                          "text": "測試", "instruct": "充滿喜悅。"})
        self.assertEqual(compiled["mode"], "custom")
        self.assertEqual(compiled["instruct"], "充滿喜悅。")
        for model_id in ("custom", "clone", "clone_1_7b"):
            with self.subTest(model_id=model_id), self.assertRaisesRegex(VoiceError, "不支援表演指令"):
                compile_voice_request({"mode": VOICE_MODELS[model_id]["mode"], "model_id": model_id,
                                       "text": "測試", "instruct": "喜悅"})

    def test_large_clone_keeps_reference_transcript_and_authorization_requirements(self):
        payload = {"mode": "clone", "model_id": "clone_1_7b", "text": "新台詞",
                   "reference_asset_id": "reference", "reference_text": "參考", "voice_authorized": True}
        self.assertEqual(compile_voice_request(payload)["model_id"], "clone_1_7b")
        for changes, message in (({"reference_asset_id": ""}, "參考音訊"),
                                 ({"reference_text": ""}, "逐字稿"),
                                 ({"voice_authorized": False}, "有權")):
            with self.subTest(changes=changes), self.assertRaisesRegex(VoiceError, message):
                compile_voice_request({**payload, **changes})


class ModelInstallerTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_model_download_uses_pinned_revision_without_touching_existing_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = VoiceInstaller(Path(temp))
            installer.runtime_installed = lambda: True
            legacy = installer.model_path("custom")
            legacy.mkdir()
            (legacy / "model.safetensors").write_bytes(b"legacy weights")
            commands = []

            async def command(*args, **_kwargs):
                commands.append(args)
                target = Path(args[args.index("--model-dir") + 1])
                (target / "config.json").write_text("{}")
                (target / "model.safetensors").write_bytes(b"new weights")

            installer._command = command
            state = await installer.start("custom_1_7b")
            self.assertEqual(state["requested_mode"], "custom")
            self.assertEqual(state["requested_model_id"], "custom_1_7b")
            await installer.task
            self.assertEqual(len(commands), 1)
            args = commands[0]
            self.assertEqual(args[args.index("--revision") + 1], VOICE_MODELS["custom_1_7b"]["revision"])
            self.assertTrue(installer.model_installed("custom_1_7b"))
            self.assertEqual((legacy / "model.safetensors").read_bytes(), b"legacy weights")
            self.assertFalse(installer.model_installed("clone_1_7b"))
            self.assertEqual(set(installer.public_status()["models"]), set(VOICE_MODELS))
            self.assertTrue(installer.public_status()["capabilities"]["model_selection"])
            await installer.start("clone_1_7b")
            await installer.task
            self.assertEqual(len(commands), 2)
            clone_args = commands[1]
            self.assertEqual(clone_args[clone_args.index("--revision") + 1], VOICE_MODELS["clone_1_7b"]["revision"])
            self.assertNotEqual(args[args.index("--model-dir") + 1], clone_args[clone_args.index("--model-dir") + 1])
            self.assertTrue(installer.model_installed("clone_1_7b"))

    async def test_wrong_revision_does_not_mark_large_model_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = VoiceInstaller(Path(temp))
            model = installer.model_path("clone_1_7b")
            model.mkdir()
            (model / "config.json").write_text("{}")
            (model / "model.safetensors").write_bytes(b"weights")
            marker = model / ".h3studio_complete.json"
            marker.write_text(json.dumps({"repo": VOICE_MODELS["clone_1_7b"]["repo"], "revision": "wrong"}))
            self.assertFalse(installer.model_installed("clone_1_7b"))
            marker.write_text(json.dumps({"repo": VOICE_MODELS["clone_1_7b"]["repo"],
                                          "revision": VOICE_MODELS["clone_1_7b"]["revision"]}))
            self.assertTrue(installer.model_installed("clone_1_7b"))


class ModelWorkerTests(unittest.TestCase):
    def generate(self, mode, model_id, instruct=""):
        model = Mock()
        for method in ("generate_custom_voice", "generate_voice_design", "generate_voice_clone"):
            getattr(model, method).return_value = ([[0.0]], 24000)
        loader = Mock(return_value=model)
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, manual_seed_all=Mock()),
                                manual_seed=Mock(), bfloat16="bfloat16")
        modules = {"torch": torch, "qwen_tts": SimpleNamespace(Qwen3TTSModel=SimpleNamespace(from_pretrained=loader)),
                   "soundfile": SimpleNamespace(write=Mock())}
        with tempfile.TemporaryDirectory() as temp, patch.dict("sys.modules", modules), patch("builtins.print"):
            root = Path(temp)
            request = {"mode": mode, "model_id": model_id, "model_dir": "selected-model", "seed": 7,
                       "text": "測試", "language": "Chinese", "speaker": "Vivian", "instruct": instruct,
                       "reference_audio": "reference.wav", "reference_text": "參考台詞"}
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            voice_worker.generate(request_path, root / "output.wav")
        self.assertEqual(loader.call_args.kwargs, {"device_map": "cuda:0", "dtype": "bfloat16", "attn_implementation": "sdpa"})
        return model

    def test_large_custom_instruction_is_forwarded(self):
        model = self.generate("custom", "custom_1_7b", "充滿喜悅。")
        self.assertEqual(model.generate_custom_voice.call_args.kwargs["instruct"], "充滿喜悅。")
        model.generate_voice_design.assert_not_called()

    def test_empty_and_small_custom_instructions_are_not_forwarded(self):
        for model_id, instruction in (("custom", "ignored legacy"), ("custom_1_7b", "")):
            with self.subTest(model_id=model_id):
                model = self.generate("custom", model_id, instruction)
                self.assertNotIn("instruct", model.generate_custom_voice.call_args.kwargs)

    def test_large_clone_uses_reference_without_design_instruction(self):
        model = self.generate("clone", "clone_1_7b")
        self.assertEqual(model.generate_voice_clone.call_args.kwargs["ref_audio"], "reference.wav")
        self.assertNotIn("instruct", model.generate_voice_clone.call_args.kwargs)

    def test_download_revision_is_forwarded(self):
        download = Mock()
        with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(snapshot_download=download)}), patch("builtins.print"):
            voice_worker.download("repo", Path("model"), "pinned")
        download.assert_called_once_with(repo_id="repo", local_dir="model", revision="pinned")


class VoiceRecoveryFileTests(unittest.TestCase):
    def test_crash_worker_request_and_malformed_files_do_not_prevent_loading_real_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "voice_jobs"
            jobs.mkdir()
            job_id = "a" * 32
            real_job = {"id": job_id, "type": "qwen3_tts", "mode": "custom", "status": "running",
                        "text": "原本工作", "seed": 7}
            (jobs / f"{job_id}.json").write_text(json.dumps(real_job), encoding="utf-8")
            # An interrupted process leaves both its worker input and saved recipe.
            worker = {"mode": "custom", "model_dir": "local-model", "text": "worker input"}
            (jobs / f"{job_id}.worker.json").write_text(json.dumps(worker), encoding="utf-8")
            (jobs / f"{job_id}.request.json").write_text(json.dumps({"mode": "custom", "text": "saved recipe"}), encoding="utf-8")
            (jobs / "bad.json").write_text("{broken", encoding="utf-8")
            (jobs / "invalid-encoding.json").write_bytes(b"\xff")
            (jobs / "array.json").write_text("[]", encoding="utf-8")
            (jobs / f"{'c' * 32}.json").write_text(json.dumps({"type": "qwen3_tts", "status": "running", "mode": "custom"}),
                                                       encoding="utf-8")
            installer = VoiceInstaller(root)
            manager = VoiceJobManager(root, asyncio.Lock(), installer, lambda _id: root / "reference.wav")
            self.assertEqual(set(manager.jobs), {job_id})
            self.assertEqual(manager.jobs[job_id]["status"], "interrupted")
            self.assertEqual(manager.jobs[job_id]["model_id"], "custom")
            self.assertEqual(manager.jobs[job_id]["text"], "原本工作")
            self.assertTrue((jobs / f"{job_id}.worker.json").exists())

    def test_only_official_record_with_matching_id_is_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "voice_jobs"
            jobs.mkdir()
            valid_id = "b" * 32
            official = {"id": valid_id, "type": "qwen3_tts", "mode": "clone", "model_id": "clone_1_7b",
                        "status": "completed", "text": "已完成", "local_output": f"{valid_id}.wav"}
            (jobs / f"{valid_id}.json").write_text(json.dumps(official), encoding="utf-8")
            bad_changes = ({"id": None}, {"id": ["id"]}, {"id": "not-an-id"}, {"id": "f" * 32},
                           {"type": "image"}, {"type": None}, {"status": ["running"]}, {"status": "nonsense"},
                           {"mode": {}}, {"model_id": []})
            for index, changes in enumerate(bad_changes):
                file_id = f"{index:032x}"
                record = {**official, "id": file_id, **changes}
                (jobs / f"{file_id}.json").write_text(json.dumps(record), encoding="utf-8")
            installer = VoiceInstaller(root)
            manager = VoiceJobManager(root, asyncio.Lock(), installer, lambda _id: root / "reference.wav")
            self.assertEqual(set(manager.jobs), {valid_id})
            self.assertEqual(manager.jobs[valid_id]["status"], "completed")
            self.assertEqual(manager.jobs[valid_id]["model_id"], "clone_1_7b")
            self.assertEqual(manager.jobs[valid_id]["local_output"], official["local_output"])


class RemoteModelCapabilityTests(unittest.TestCase):
    def test_legacy_hosts_still_accept_original_model_selection(self):
        RemoteVoiceConnection.require_model_support({"models": {"custom": {"installed": True}}},
                                                    compile_voice_request({"text": "測試"}))

    def test_missing_false_or_truthy_capability_blocks_large_model(self):
        compiled = compile_voice_request({"text": "測試", "model_id": "custom_1_7b"})
        for value in (None, False, 1, "true"):
            state = {"models": {"custom_1_7b": dict(VOICE_MODELS["custom_1_7b"])},
                     "capabilities": {"model_selection": value}}
            with self.subTest(value=value), self.assertRaisesRegex(RemoteVoiceError, "更新"):
                RemoteVoiceConnection.require_model_support(state, compiled)

    def test_large_custom_instruction_requires_both_capability_and_metadata(self):
        compiled = compile_voice_request({"text": "測試", "model_id": "custom_1_7b", "instruct": "喜悅"})
        state = {"models": {"custom_1_7b": dict(VOICE_MODELS["custom_1_7b"])},
                 "capabilities": {"model_selection": True}}
        with self.assertRaisesRegex(RemoteVoiceError, "表演指令"):
            RemoteVoiceConnection.require_model_support(state, compiled)
        state["capabilities"]["custom_voice_instruct"] = True
        RemoteVoiceConnection.require_model_support(state, compiled)
        state["models"]["custom_1_7b"]["supports_instruct"] = False
        with self.assertRaisesRegex(RemoteVoiceError, "表演指令"):
            RemoteVoiceConnection.require_model_support(state, compiled)


class VoiceCacheAndCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.installer = VoiceInstaller(self.root)
        self.installer.runtime_installed = lambda: True
        self.installer.model_installed = lambda _id: True
        self.manager = VoiceJobManager(self.root, asyncio.Lock(), self.installer, lambda _id: self.root / "reference.wav")

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def test_idle_cache_is_freed_under_lock_before_worker(self):
        events = []

        async def free(request):
            self.assertTrue(self.manager.gpu_lock.locked())
            self.assertEqual(await request.json(), {"unload_models": True, "free_memory": True})
            events.append("free")
            return web.json_response({})

        app = web.Application()
        app.router.add_post("/free", free)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        comfy = SimpleNamespace(mode="local", base_url=str(server.make_url("/")), auth_headers=lambda: {},
                                queue_status=AsyncMock(return_value={"available": True, "running_count": 0, "pending_count": 0}))
        self.manager.comfy = comfy

        async def launch(*args, **_kwargs):
            events.append("worker")
            output = Path(args[args.index("--output") + 1])

            async def communicate():
                output.write_bytes(b"RIFF" + b"0" * 200)
                return b"", b""

            return SimpleNamespace(returncode=0, communicate=communicate)

        with patch("voice.asyncio.create_subprocess_exec", AsyncMock(side_effect=launch)):
            job = self.manager.create({"text": "測試", "model_id": "custom_1_7b"})
            await self.manager.tasks[job["id"]]
        self.assertEqual(events, ["free", "worker"])
        self.assertEqual(job["status"], "completed")

    async def test_unavailable_engine_never_receives_free_request(self):
        self.manager.comfy = SimpleNamespace(mode="local", base_url="http://127.0.0.1:9", auth_headers=lambda: {},
                                            queue_status=AsyncMock(return_value={"available": False}))
        with patch("voice.aiohttp.ClientSession", side_effect=AssertionError("unavailable engine must not be unloaded")):
            await self.manager._release_idle_comfy("unused", asyncio.Event())

    async def test_busy_engine_is_not_unloaded_and_wait_can_be_cancelled(self):
        cancel = asyncio.Event()

        async def busy():
            cancel.set()
            return {"available": True, "running_count": 1, "pending_count": 0}

        self.manager.comfy = SimpleNamespace(mode="local", base_url="http://127.0.0.1:9", auth_headers=lambda: {}, queue_status=busy)
        self.manager.jobs["job"] = {"id": "job"}
        with patch("voice.aiohttp.ClientSession", side_effect=AssertionError("busy engine must not be unloaded")):
            with self.assertRaises(asyncio.CancelledError):
                await self.manager._release_idle_comfy("job", cancel)

    async def test_shutdown_keeps_gpu_lock_until_worker_has_exited(self):
        started, stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Process:
            returncode = None

            async def communicate(process):
                started.set()
                await release.wait()
                process.returncode = -15
                return b"", b""

            def terminate(process):
                stopped.set()

        with patch("voice.asyncio.create_subprocess_exec", AsyncMock(return_value=Process())):
            job = self.manager.create({"text": "測試"})
            await started.wait()
            shutdown = asyncio.create_task(self.manager.shutdown())
            await stopped.wait()
            await asyncio.sleep(0)
            self.assertTrue(self.manager.gpu_lock.locked())
            self.assertFalse(shutdown.done())
            release.set()
            await shutdown
        self.assertFalse(self.manager.gpu_lock.locked())
        self.assertEqual(job["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
