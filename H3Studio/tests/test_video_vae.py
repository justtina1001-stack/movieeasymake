import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from domain import (
    VIDEO_VAE_FILENAMES, RequestError, build_workflow, compile_request,
    resolve_video_vae, validate_runtime_inventory,
)


def inventory(*, int8=True, fp16=True, supported=True):
    return dict(fl2va=True, ref2va=True, text_encoder=True, audio_vae=True,
                video_vae=fp16 or (int8 and supported), video_vae_int8=int8,
                video_vae_fp16=fp16, video_vae_int8_supported=supported)


class VideoVAETests(unittest.TestCase):
    def compile(self, **changes):
        return compile_request(dict(prompt="A character walks.", motion_profile="none", **changes))

    def test_missing_choice_defaults_to_auto_in_both_prompt_paths(self):
        for changes in ({}, {"prompt_profile": "shortfilm"}):
            with self.subTest(changes=changes):
                self.assertEqual(asdict(self.compile(**changes))["video_vae"], "auto")
                self.assertEqual(self.compile(video_vae="fp16", **changes).video_vae, "fp16")

    def test_invalid_selection_is_rejected(self):
        for value in ("unknown", "../external.safetensors", True, ["int8"]):
            with self.subTest(value=value), self.assertRaises(RequestError):
                self.compile(video_vae=value)

    def test_auto_prefers_supported_int8_and_falls_back_to_available_fp16(self):
        compiled = self.compile()
        for state, expected in (
            (inventory(), "int8"),
            (inventory(fp16=False), "int8"),
            (inventory(int8=False), "fp16"),
            (inventory(supported=False), "fp16"),
            ({"video_vae": True}, "fp16"),
        ):
            with self.subTest(state=state):
                self.assertEqual(resolve_video_vae(compiled, state), VIDEO_VAE_FILENAMES[expected])

    def test_explicit_selection_never_silently_changes_models(self):
        self.assertEqual(resolve_video_vae(self.compile(video_vae="fp16"), inventory()), VIDEO_VAE_FILENAMES["fp16"])
        for choice, state, message in (
            ("int8", inventory(supported=False), "0.37.0"),
            ("int8", inventory(int8=False), "INT8"),
            ("fp16", inventory(fp16=False), "影片 VAE"),
            ("auto", inventory(fp16=False, supported=False), "0.37.0"),
            ("auto", inventory(fp16=False, int8=False), "影片 VAE"),
        ):
            with self.subTest(choice=choice, state=state), self.assertRaisesRegex(RequestError, message):
                validate_runtime_inventory(self.compile(video_vae=choice), state)

    def test_both_families_wire_only_the_selected_video_vae(self):
        for reference in (False, True):
            payload = {"mode": "r2v", "references": [{"alias": "Hero", "type": "character", "image_asset_ids": ["a" * 32]}]} if reference else {}
            uploaded = {"a" * 32: "example.png"}
            fp16 = build_workflow(self.compile(video_vae="fp16", **payload), uploaded, "test")
            int8 = build_workflow(self.compile(**payload), uploaded, "test")
            different = [key for key in fp16 if fp16[key] != int8[key]]
            self.assertEqual(len(different), 1)
            key = different[0]
            self.assertEqual(fp16[key]["class_type"], "VAELoader")
            self.assertEqual(fp16[key]["inputs"]["vae_name"], VIDEO_VAE_FILENAMES["fp16"])
            self.assertEqual(int8[key]["inputs"]["vae_name"], VIDEO_VAE_FILENAMES["int8"])
            fallback = build_workflow(self.compile(**payload), uploaded, "test", video_vae_name=VIDEO_VAE_FILENAMES["fp16"])
            self.assertEqual(fallback, fp16)
        with self.assertRaises(RequestError):
            build_workflow(self.compile(video_vae="fp16"), {}, "test", video_vae_name=VIDEO_VAE_FILENAMES["int8"])


class VideoVAEJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_records_and_submits_resolved_vae(self):
        from app import JobManager
        for state, choice in ((inventory(), "int8"), (inventory(int8=False), "fp16")):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fake = SimpleNamespace(
                    ensure_running=AsyncMock(), model_inventory=AsyncMock(return_value=state),
                    run_prompt=AsyncMock(return_value=("test-prompt", {"outputs": {"save": {"images": [{"filename": "test.mp4", "type": "output", "subfolder": ""}]}}})),
                )
                with patch("app.JOB_DIR", root), patch("app.OUTPUT_DIR", root):
                    manager = JobManager(object(), fake)
                    manager.complete_job = AsyncMock()
                    payload = {"prompt": "A character walks."}
                    job = manager.create(compile_request(payload), payload)
                    await manager.tasks[job["id"]]
                    self.assertIsNone(job["error"])
                    self.assertEqual(job["video_vae"], "auto")
                    self.assertEqual(job["video_vae_name"], VIDEO_VAE_FILENAMES[choice])
                    workflow = fake.run_prompt.await_args.args[0]
                    self.assertTrue(any(node["inputs"].get("vae_name") == VIDEO_VAE_FILENAMES[choice] for node in workflow.values()))
                    manager.complete_job.assert_awaited_once()

    async def test_unsupported_explicit_int8_fails_before_upload(self):
        from app import JobManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = SimpleNamespace(ensure_running=AsyncMock(), model_inventory=AsyncMock(return_value=inventory(supported=False)),
                                   upload_asset=AsyncMock(), run_prompt=AsyncMock())
            with patch("app.JOB_DIR", root), patch("app.OUTPUT_DIR", root):
                manager = JobManager(object(), fake)
                payload = {"mode": "fl2va", "prompt": "A character walks.", "first_image_asset_id": "a" * 32, "video_vae": "int8"}
                job = manager.create(compile_request(payload), payload)
                await manager.tasks[job["id"]]
                self.assertEqual(job["status"], "failed")
                self.assertIn("0.37.0", job["error"])
                fake.upload_asset.assert_not_called()
                fake.run_prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
