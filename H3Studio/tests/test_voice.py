import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from voice import (
    CUSTOM_SPEAKERS,
    VoiceError,
    VoiceInstaller,
    VoiceJobManager,
    VOICE_RUNTIME_SCHEMA,
    VOICE_TORCH_INDEX_URL,
    compile_voice_request,
)


class VoiceRequestTests(unittest.TestCase):
    def test_custom_voice_compiles_with_instruction(self):
        compiled = compile_voice_request({
            "mode": "custom",
            "text": "恭喜進入免費遊戲！",
            "language": "Chinese",
            "speaker": "Vivian",
            "instruct": "充滿喜悅，咬字清楚。",
            "seed": 42,
        })
        self.assertEqual(compiled["speaker"], "Vivian")
        self.assertEqual(compiled["seed"], 42)
        self.assertIn("Vivian", CUSTOM_SPEAKERS)

    def test_voice_design_requires_description(self):
        with self.assertRaisesRegex(VoiceError, "聲線設計"):
            compile_voice_request({"mode": "design", "text": "測試", "instruct": ""})

    def test_clone_requires_reference_and_authorization(self):
        with self.assertRaisesRegex(VoiceError, "參考音訊"):
            compile_voice_request({"mode": "clone", "text": "新的台詞", "voice_authorized": True})
        with self.assertRaisesRegex(VoiceError, "有權"):
            compile_voice_request({
                "mode": "clone", "text": "新的台詞", "reference_asset_id": "a" * 32,
                "reference_text": "原本的台詞", "voice_authorized": False,
            })

    def test_x_vector_only_allows_missing_transcript(self):
        compiled = compile_voice_request({
            "mode": "clone", "text": "新的台詞", "reference_asset_id": "a" * 32,
            "x_vector_only": True, "voice_authorized": True,
        })
        self.assertTrue(compiled["x_vector_only"])


class VoiceInstallerTests(unittest.TestCase):
    def test_runtime_marker_is_bound_to_current_computer(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = VoiceInstaller(Path(temp) / "data")
            installer.python_path.parent.mkdir(parents=True, exist_ok=True)
            installer.python_path.write_bytes(b"python")
            installer.marker_path.write_text(json.dumps({
                "schema": VOICE_RUNTIME_SCHEMA,
                "python": str(installer.python_path.resolve()),
                "machine": os.environ.get("COMPUTERNAME") or "",
                "torch_index_url": VOICE_TORCH_INDEX_URL,
            }), encoding="utf-8")
            self.assertTrue(installer.runtime_installed())
            installer.marker_path.write_text(json.dumps({
                "schema": VOICE_RUNTIME_SCHEMA,
                "python": str(installer.python_path.resolve()),
                "machine": "another-computer",
                "torch_index_url": VOICE_TORCH_INDEX_URL,
            }), encoding="utf-8")
            self.assertFalse(installer.runtime_installed())

    def test_old_cpu_runtime_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = VoiceInstaller(Path(temp) / "data")
            installer.python_path.parent.mkdir(parents=True, exist_ok=True)
            installer.python_path.write_bytes(b"python")
            installer.marker_path.write_text(json.dumps({
                "python": str(installer.python_path.resolve()),
                "machine": os.environ.get("COMPUTERNAME") or "",
            }), encoding="utf-8")
            self.assertFalse(installer.runtime_installed())

    def test_model_needs_completion_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = VoiceInstaller(Path(temp) / "data")
            model = installer.model_path("custom")
            model.mkdir(parents=True, exist_ok=True)
            (model / "config.json").write_text("{}", encoding="utf-8")
            (model / "model.safetensors").write_bytes(b"weights")
            self.assertFalse(installer.model_installed("custom"))
            (model / ".h3studio_complete.json").write_text("{}", encoding="utf-8")
            self.assertTrue(installer.model_installed("custom"))


class VoiceJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_job_persists_and_completes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installer = VoiceInstaller(root / "data")
            installer.runtime_installed = lambda: True
            installer.model_installed = lambda _mode: True

            def asset_path(_asset_id: str) -> Path:
                return root / "reference.wav"

            manager = VoiceJobManager(root / "data", asyncio.Lock(), installer, asset_path)

            async def fake_run(job_id, _compiled):
                output = manager.output_dir / f"{job_id}.wav"
                output.write_bytes(b"RIFF" + b"0" * 200)
                manager.update(job_id, status="completed", progress=100, local_output=output.name)

            manager._run = fake_run
            job = manager.create({
                "mode": "custom", "text": "恭喜發財", "speaker": "Vivian",
                "job_name": "財神台詞", "seed": 7,
            })
            await manager.tasks[job["id"]]
            completed = manager.jobs[job["id"]]
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["name"], "財神台詞")
            self.assertTrue(manager.local_output_path(completed).exists())
            persisted = json.loads((manager.job_dir / f"{job['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["text"], "恭喜發財")

    async def test_clone_rejects_video_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "reference.mp4"
            video.write_bytes(b"video")
            installer = VoiceInstaller(root / "data")
            installer.runtime_installed = lambda: True
            installer.model_installed = lambda _mode: True
            manager = VoiceJobManager(root / "data", asyncio.Lock(), installer, lambda _asset_id: video)
            with self.assertRaisesRegex(VoiceError, "音訊檔"):
                manager.create({
                    "mode": "clone", "text": "新台詞", "reference_asset_id": "b" * 32,
                    "reference_text": "參考台詞", "voice_authorized": True,
                })


if __name__ == "__main__":
    unittest.main()
