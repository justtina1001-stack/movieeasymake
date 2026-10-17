import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import editor_captions as captions
import video_editor as editor


class CaptionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        self.app = web.Application()
        self.app["assets"] = SimpleNamespace()
        self.app["jobs"] = SimpleNamespace()
        self.store = editor.register_editor_routes(self.app, self.static, self.root)
        self.service = captions.register_editor_captions(self.app)
        self.media_id = "a" * 32
        self.store.media[self.media_id] = {"id": self.media_id, "kind": "video", "duration": 5,
                                           "has_audio": True, "mime": "video/mp4"}
        (self.store.media_dir / f"{self.media_id}.source").write_bytes(b"test media")
        self.project = self.store.create_project({"name": "語音字幕測試", "fps": 24,
            "clips": [{"id": "main-one", "media_id": self.media_id, "in": 0, "out": 3.2}]})
        self.initial = copy.deepcopy(self.project)
        self.runtime_mock = patch.object(self.service, "_ensure_runtime", new=AsyncMock())
        self.runtime_mock.start()
        self.recognize = AsyncMock(return_value={"cues": [{"start": 0, "end": 1, "text": "你好世界。"}],
                                                  "duration": 3.2, "detected_language": "zh"})
        self.recognize_patch = patch.object(self.service, "_recognize", new=self.recognize)
        self.recognize_patch.start()
        self.audio_patch = patch.object(captions, "render_caption_audio", self.audio)
        self.audio_patch.start()
        self.gates = []
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        await self.client.close()
        self.audio_patch.stop()
        self.recognize_patch.stop()
        self.runtime_mock.stop()
        self.temp.cleanup()

    @staticmethod
    def audio(project, paths, output_path, event, callback):
        output_path.write_bytes(b"wave")
        callback(99)
        return {"duration": 3.2083333333, "peak": .2, "frames": 77}

    async def start(self, payload=None):
        return await self.client.post(f"/api/editor/projects/{self.project['id']}/captions",
                                      json={} if payload is None else payload)

    async def wait_job(self, job_id, terminal=True):
        async with asyncio.timeout(5):
            while True:
                response = await self.client.get(f"/api/editor/captions/{job_id}")
                self.assertEqual(response.status, 200)
                job = (await response.json())["job"]
                if not terminal or job["status"] not in editor.ACTIVE:
                    return job
                await asyncio.sleep(.01)

    async def wait_event(self, event):
        async with asyncio.timeout(5):
            while not event.is_set():
                await asyncio.sleep(.01)

    async def test_status_does_not_install_or_load_model(self):
        response = await self.client.get("/api/editor/captions/status")
        self.assertEqual(response.status, 200)
        status = await response.json()
        self.assertEqual(status["engine"], "faster-whisper")
        self.assertEqual(status["model"], "small")
        self.assertFalse(status["runtime_ready"])
        self.assertFalse(status["model_cached"])
        self.assertIsNone(status["active_job"])
        self.service._ensure_runtime.assert_not_called()
        self.recognize.assert_not_called()

    async def test_readiness_requires_all_runtime_and_model_files(self):
        self.service.python.parent.mkdir(parents=True)
        self.service.python.write_bytes(b"isolated python")
        site = self.service.runtime / "Lib/site-packages"
        for module in ("faster_whisper", "ctranslate2", "opencc"):
            (site / module).mkdir(parents=True)
        self.assertFalse(self.service.runtime_ready())
        (site / "av").mkdir()
        self.assertTrue(self.service.runtime_ready())
        model = self.service.models / "small"
        model.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (model / name).write_bytes(b"fixed model")
        self.assertFalse(self.service.model_cached())
        (model / "vocabulary.txt").write_bytes(b"vocabulary")
        self.assertTrue(self.service.model_cached())

    async def test_runtime_install_is_isolated_and_uses_fixed_requirements(self):
        self.runtime_mock.stop()
        job_id = "1" * 32
        self.service.jobs[job_id] = {"id": job_id, "status": "running"}
        commands = []
        async def installer(job_id, event, command, **options):
            commands.append(command)
            if "venv" in command:
                self.service.python.parent.mkdir(parents=True)
                self.service.python.write_bytes(b"isolated python")
            else:
                for module in ("faster_whisper", "ctranslate2", "opencc", "av"):
                    (self.service.runtime / "Lib/site-packages" / module).mkdir(parents=True)
        try:
            with patch.object(self.service, "_subprocess", side_effect=installer):
                await self.service._ensure_runtime(job_id, threading.Event())
            self.assertEqual(commands[0], [sys.executable, "-m", "venv", str(self.service.runtime)])
            self.assertEqual(commands[1][:5], [str(self.service.python), "-m", "pip", "--isolated", "install"])
            self.assertIn("https://pypi.org/simple", commands[1])
            self.assertIn("--only-binary=:all:", commands[1])
            self.assertTrue(Path(commands[1][-1]).is_absolute())
            self.assertEqual(Path(commands[1][-1]).name, "caption_requirements.txt")
        finally:
            self.runtime_mock.start()

    async def test_job_completes_without_mutating_saved_project_or_leaking_paths(self):
        response = await self.start({"language": "zh", "traditional": True})
        self.assertEqual(response.status, 202)
        job = (await response.json())["job"]
        completed = await self.wait_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["cues"][0]["text"], "你好世界。")
        self.assertEqual(self.store.projects[self.project["id"]], self.initial)
        self.assertEqual(completed["source_audio_snapshot"], captions.audio_snapshot(self.initial))
        self.assertNotIn("snapshot", completed)
        self.assertNotIn(str(self.root), json.dumps(completed))
        self.assertFalse((self.service.root / job["id"]).exists())
        self.assertEqual(self.recognize.call_args.args[-1], 3.2)
        persisted = json.loads((self.service.root / f"{job['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["snapshot"], self.initial)

    async def test_saved_snapshot_is_immutable_while_user_edits(self):
        started, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        received = []
        def blocked_audio(project, paths, path, event, callback):
            received.append(copy.deepcopy(project))
            started.set()
            gate.wait(3)
            return self.audio(project, paths, path, event, callback)
        with patch.object(captions, "render_caption_audio", blocked_audio):
            job = (await (await self.start()).json())["job"]
            await self.wait_event(started)
            updated = copy.deepcopy(self.project)
            updated["clips"][0]["volume"] = 0
            self.store.update_project(self.project["id"], updated)
            gate.set()
            result = await self.wait_job(job["id"])
        self.assertEqual(received[0], self.initial)
        self.assertEqual(result["source_audio_snapshot"], captions.audio_snapshot(self.initial))
        self.assertEqual(self.store.projects[self.project["id"]]["clips"][0]["volume"], 0)

    async def test_audio_thread_does_not_block_api_and_second_job_is_refused(self):
        started, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        def blocked_audio(project, paths, path, event, callback):
            started.set()
            gate.wait(3)
            return self.audio(project, paths, path, event, callback)
        with patch.object(captions, "render_caption_audio", blocked_audio):
            response = await self.start()
            job = (await response.json())["job"]
            await self.wait_event(started)
            async with asyncio.timeout(.5):
                response = await self.client.get("/api/editor/captions/status")
                status = await response.json()
            self.assertEqual(status["active_job"]["id"], job["id"])
            duplicate = await self.start()
            self.assertEqual(duplicate.status, 409)
            gate.set()
            self.assertEqual((await self.wait_job(job["id"]))["status"], "completed")

    async def test_cancel_waits_for_audio_owner_before_removing_wave(self):
        started, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        path_holder = []
        def blocked_audio(project, paths, path, event, callback):
            path.write_bytes(b"owned wav")
            path_holder.append(path)
            started.set()
            gate.wait(3)
            self.assertTrue(path.exists())
            raise editor.ExportCancelled()
        with patch.object(captions, "render_caption_audio", blocked_audio):
            job = (await (await self.start()).json())["job"]
            await self.wait_event(started)
            async with asyncio.timeout(.5):
                response = await self.client.post(f"/api/editor/captions/{job['id']}/cancel")
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["job"]["cancel_requested"])
            self.assertTrue(path_holder[0].exists())
            gate.set()
            cancelled = await self.wait_job(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertNotIn("cues", cancelled)
        self.assertFalse(path_holder[0].exists())
        self.recognize.assert_not_called()

    async def test_shutdown_waits_for_audio_worker(self):
        started, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        def blocked_audio(project, paths, path, event, callback):
            started.set()
            gate.wait(3)
            raise editor.ExportCancelled()
        with patch.object(captions, "render_caption_audio", blocked_audio):
            job = (await (await self.start()).json())["job"]
            await self.wait_event(started)
            close = asyncio.create_task(self.service.shutdown())
            await asyncio.sleep(.03)
            self.assertFalse(close.done())
            gate.set()
            await asyncio.wait_for(close, 2)
        self.assertEqual(self.service.jobs[job["id"]]["status"], "cancelled")

    async def test_silence_fails_without_speech_hallucination(self):
        with patch.object(captions, "render_caption_audio", return_value={"peak": 0, "duration": 3.2}):
            job = (await (await self.start()).json())["job"]
            result = await self.wait_job(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("沒有可辨識的音訊", result["error"])
        self.assertNotIn("cues", result)
        self.recognize.assert_not_called()

    async def test_untrusted_requests_reject_paths_languages_types_and_json(self):
        cases = [{"path": "C:/secret.wav"}, {"url": "https://example.com"}, {"model": "large"},
                 {"language": "xx"}, {"language": []}, {"traditional": "true"}, [], None]
        for payload in cases:
            response = await self.client.post(f"/api/editor/projects/{self.project['id']}/captions", json=payload)
            self.assertEqual(response.status, 400, await response.text())
        response = await self.client.post(f"/api/editor/projects/{self.project['id']}/captions",
                                          data="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        for endpoint in ("/api/editor/projects/not-an-id/captions",):
            response = await self.client.post(endpoint, json={})
            self.assertEqual(response.status, 400)
        for value in ("not-an-id", "f" * 32):
            response = await self.client.get(f"/api/editor/captions/{value}")
            self.assertIn(response.status, (400, 404))
        self.service._ensure_runtime.assert_not_called()

    async def test_empty_or_exporting_timeline_is_refused(self):
        empty = self.store.create_project({"name": "空白"})
        response = await self.client.post(f"/api/editor/projects/{empty['id']}/captions", json={})
        self.assertEqual(response.status, 400)
        self.store.exports["b" * 32] = {"status": "running"}
        response = await self.start()
        self.assertEqual(response.status, 409)
        self.service._ensure_runtime.assert_not_called()

    async def test_failure_is_sanitized_and_project_kept(self):
        self.recognize.side_effect = RuntimeError(f"secret internal path {self.root}")
        job = (await (await self.start()).json())["job"]
        result = await self.wait_job(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn(str(self.root), result["error"])
        self.assertEqual(self.store.projects[self.project["id"]], self.initial)
        self.assertFalse((self.service.root / job["id"]).exists())

    async def test_restart_marks_interrupted_and_preserves_completed_cues(self):
        completed = (await (await self.start()).json())["job"]
        await self.wait_job(completed["id"])
        interrupted_id = "c" * 32
        interrupted = {"id": interrupted_id, "project_id": self.project["id"], "status": "running",
                       "stage": "transcribing", "progress": 25, "created_at": editor.now(), "updated_at": editor.now()}
        editor.atomic_json(self.service.root / f"{interrupted_id}.json", interrupted)
        work = self.service.root / interrupted_id
        work.mkdir()
        (work / "audio.wav").write_bytes(b"interrupted")
        reopened = captions.CaptionService(self.store)
        self.assertEqual(reopened.jobs[interrupted_id]["status"], "failed")
        self.assertIn("重新啟動", reopened.jobs[interrupted_id]["error"])
        self.assertFalse(work.exists())
        self.assertEqual(reopened.jobs[completed["id"]]["cues"], self.service.jobs[completed["id"]]["cues"])
        self.assertFalse(reopened.active)

    async def test_owned_subprocess_progress_is_bounded_and_ignores_untrusted_keys(self):
        job_id = "d" * 32
        self.service.jobs[job_id] = {"id": job_id, "status": "running", "stage": "model", "progress": 0}
        code = ('import sys; sys.stdout.write("x" * 70000 + "\\n"); '
                'print(\'{"stage":"transcribing","progress":42,"path":"secret"}\'); '
                'sys.stderr.write("e" * 300000)')
        await self.service._subprocess(job_id, threading.Event(), [sys.executable, "-c", code],
                                       timeout=5, failure="失敗", progress=True)
        self.assertIsNone(self.service.process)
        self.assertEqual(self.service.jobs[job_id]["progress"], 42)
        self.assertNotIn("path", self.service.jobs[job_id])
        self.assertLessEqual((self.service.root / job_id / "worker.log").stat().st_size, captions.MAX_LOG_BYTES)

    async def test_owned_subprocess_cancellation_reaps_process(self):
        job_id = "e" * 32
        event = threading.Event()
        task = asyncio.create_task(self.service._subprocess(
            job_id, event, [sys.executable, "-c", "import time; time.sleep(20)"], timeout=30, failure="失敗"))
        async with asyncio.timeout(3):
            while self.service.process is None:
                await asyncio.sleep(.01)
        child = self.service.process
        event.set()
        with self.assertRaises(editor.ExportCancelled):
            await asyncio.wait_for(task, 3)
        self.assertIsNotNone(child.returncode)
        self.assertIsNone(self.service.process)

    async def test_owned_subprocess_timeout_reaps_process(self):
        job_id = "f" * 32
        with self.assertRaises(captions.CaptionFailure):
            await self.service._subprocess(job_id, threading.Event(),
                [sys.executable, "-c", "import time; time.sleep(20)"], timeout=.05, failure="失敗")
        self.assertIsNone(self.service.process)


class CaptionResultTests(unittest.TestCase):
    def test_valid_result_is_trimmed_and_private_metadata_ignored(self):
        result = captions.validate_result({"cues": [{"start": 0, "end": 1, "text": "  你好。 "}],
                                           "language": "zh", "path": "secret"}, 2)
        self.assertEqual(result["cues"], [{"start": 0.0, "end": 1.0, "text": "你好。"}])
        self.assertNotIn("path", result)

    def test_invalid_results_never_return_partial_cues(self):
        valid = {"start": 0, "end": 1, "text": "有效"}
        cases = [None, {}, {"cues": []}, {"cues": "bad"}, {"cues": [None]},
                 {"cues": [{**valid, "start": True}]}, {"cues": [{**valid, "end": float("nan")}]},
                 {"cues": [{**valid, "start": -1}]}, {"cues": [{**valid, "end": 3}]},
                 {"cues": [valid, {**valid, "start": .5, "end": 1.5}]},
                 {"cues": [{**valid, "text": " "}]}, {"cues": [{**valid, "text": "bad\x00"}]},
                 {"cues": [valid], "language": "C:/secret"}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(captions.CaptionFailure):
                captions.validate_result(value, 2)

    def test_result_respects_editor_text_limits_and_one_frame_minimum(self):
        valid = {"start": 0, "end": 1, "text": "有效"}
        cases = [{**valid, "text": "字" * 501}, {**valid, "text": "\n".join(["字"] * 11)},
                 {**valid, "end": 1 / 24 - .001}]
        for cue in cases:
            with self.subTest(cue=cue), self.assertRaises(captions.CaptionFailure):
                captions.validate_result({"cues": [cue]}, 2, fps=24)
        exact = captions.validate_result({"cues": [{**valid, "end": 1 / 24}]}, 2, fps=24)
        self.assertEqual(len(exact["cues"]), 1)


if __name__ == "__main__":
    unittest.main()
