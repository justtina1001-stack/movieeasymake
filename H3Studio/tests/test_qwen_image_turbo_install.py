import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import setup_qwen_image_turbo as installer


class TurboInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = self.root / "engine"
        self.engine.mkdir()
        (self.engine / "main.py").write_text("# fixture", encoding="utf-8")
        self.staging = self.root / "staging"
        self.body = b"verified turbo fixture"
        self.model = ("diffusion_models", "turbo.safetensors", len(self.body),
                      hashlib.sha256(self.body).hexdigest())
        self.target = self.engine / "models" / self.model[0] / self.model[1]

    def stage(self, body=None):
        path = self.staging / self.model[0] / self.model[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.body if body is None else body)
        return path

    def install(self, downloader=None):
        return installer.install_turbo(self.engine, self.staging, model=self.model,
            revision="fixed-revision", downloader=downloader or Mock(side_effect=AssertionError("unexpected download")))

    def test_production_pin_is_exact_single_full_model(self):
        self.assertEqual(installer.TURBO_REVISION, "72a49122613a4f9afbcc443426d6c765c0c383cf")
        self.assertEqual(installer.TURBO_MODEL_FILE, (
            "diffusion_models", "qwen_image_2.1_turbo_int8_convrot.safetensors", 7256787829,
            "8144a0182964aa8403ad6dbc011db837304167d235161b9aabaa8532e967314c"))

    def test_valid_existing_target_is_verified_without_download_or_space_demand(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.body)
        with patch.object(installer.shutil, "disk_usage", side_effect=AssertionError("space check not needed")):
            result = self.install()
        self.assertFalse(result["installed"])
        self.assertTrue(result["verified_existing"])
        self.assertEqual(self.target.read_bytes(), self.body)

    def test_different_existing_file_is_never_overwritten(self):
        self.target.parent.mkdir(parents=True)
        for body in (b"different size", b"x" * len(self.body)):
            with self.subTest(body=body):
                self.target.write_bytes(body)
                with self.assertRaises(RuntimeError):
                    self.install()
                self.assertEqual(self.target.read_bytes(), body)

    def test_valid_staging_is_published_without_redownload(self):
        cached = self.stage()
        result = self.install()
        self.assertTrue(result["installed"])
        self.assertFalse(cached.exists())
        self.assertEqual(self.target.read_bytes(), self.body)
        self.assertEqual(result["actual_sha256"], self.model[3])
        self.assertFalse(result["other_models_downloaded"])

    def test_size_and_hash_failure_preserve_staging_and_never_publish(self):
        for body in (b"short", b"x" * len(self.body)):
            with self.subTest(body=body):
                cached = self.stage(body)
                with self.assertRaises(RuntimeError):
                    self.install()
                self.assertFalse(self.target.exists())
                self.assertEqual(cached.read_bytes(), body)

    def test_disk_capacity_checked_before_network(self):
        downloader = Mock()
        with patch.object(installer.shutil, "disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(RuntimeError, "Not enough free disk"):
                self.install(downloader)
        downloader.assert_not_called()

    def test_download_only_requested_file_then_verifies_and_publishes(self):
        def download(staging, model, revision, progress):
            self.assertEqual(model, self.model)
            self.assertEqual(revision, "fixed-revision")
            return self.stage()
        downloader = Mock(side_effect=download)
        self.assertTrue(self.install(downloader)["installed"])
        self.assertEqual(downloader.call_count, 1)

    def test_download_exception_keeps_incomplete_for_next_run(self):
        partial = self.staging / ".cache" / "partial.incomplete"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        with self.assertRaises(RuntimeError):
            self.install(Mock(side_effect=RuntimeError("interrupted")))
        self.assertEqual(partial.read_bytes(), b"partial")
        self.assertFalse(self.target.exists())

    def test_downloader_cannot_publish_a_file_outside_staging(self):
        outside = self.root / "outside.safetensors"
        outside.write_bytes(self.body)
        with self.assertRaisesRegex(RuntimeError, "outside"):
            self.install(Mock(return_value=outside))
        self.assertFalse(self.target.exists())
        self.assertTrue(outside.exists())

    def test_destination_created_during_publication_is_preserved(self):
        cached = self.stage()
        self.target.parent.mkdir(parents=True)
        def race(source, target):
            Path(target).write_bytes(b"race-created different file")
            raise FileExistsError("destination appeared")
        operation = "rename" if os.name == "nt" else "link"
        with patch.object(installer.os, operation, side_effect=race):
            with self.assertRaises(RuntimeError):
                installer.publish_verified(cached, self.target, self.model[2], self.model[3])
        self.assertEqual(self.target.read_bytes(), b"race-created different file")
        self.assertEqual(cached.read_bytes(), self.body)

    def test_staged_symbolic_link_is_not_moved_into_models(self):
        cached = self.stage()
        original = Path.is_symlink
        self.target.parent.mkdir(parents=True)
        with patch.object(Path, "is_symlink", lambda path: path == cached or original(path)):
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                installer.publish_verified(cached, self.target, self.model[2], self.model[3])
        self.assertFalse(self.target.exists())
        self.assertEqual(cached.read_bytes(), self.body)

    def test_hub_call_uses_one_pin_anonymous_and_no_signed_error_disclosure(self):
        with patch("huggingface_hub.hf_hub_download", return_value=str(self.stage())) as hub:
            installer.download_official(self.staging, self.model, "revision")
        self.assertEqual(hub.call_args.args, (installer.REPO, "diffusion_models/turbo.safetensors"))
        self.assertEqual(hub.call_args.kwargs["revision"], "revision")
        self.assertIs(hub.call_args.kwargs["token"], False)
        with patch("huggingface_hub.hf_hub_download", side_effect=RuntimeError("https://example/?token=SECRET")):
            with self.assertRaises(RuntimeError) as raised:
                installer.download_official(self.staging, self.model, "revision")
        self.assertNotIn("SECRET", str(raised.exception))

    def test_xet_progress_ignores_transfer_bar_and_keeps_file_counter(self):
        callback = Mock()
        with patch("huggingface_hub.hf_hub_download", return_value=str(self.stage())) as hub:
            installer.download_official(self.staging, self.model, "revision", callback)
        cls = hub.call_args.kwargs["tqdm_class"]
        transfer = cls(total=self.model[2], name="huggingface_hub.xet_get.transfer",
                       desc="file: downloading bytes")
        reconstruction = cls(total=self.model[2], name="huggingface_hub.xet_get",
                             desc="file: reconstructing file")
        transfer.last_report = reconstruction.last_report = -100
        transfer.update(10)
        callback.assert_not_called()
        reconstruction.update(10)
        callback.assert_called_once_with(10, self.model[2])
        self.assertEqual(reconstruction.n, 10)
        transfer.close()
        reconstruction.close()


if __name__ == "__main__":
    unittest.main()
