import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import setup_flux3_image as installer


class Flux3InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = self.root / "engine"
        self.engine.mkdir()
        (self.engine / "main.py").write_text("", encoding="utf-8")
        (self.engine / "comfyui_version.py").write_text('__version__ = "0.41.3"\n', encoding="utf-8")
        self.staging = self.engine / "models/.flux3-download"
        self.source = self.root / "source"
        self.data = b"verified FLUX test model"
        self.model = ("diffusion_models", "flux-test.safetensors", len(self.data), hashlib.sha256(self.data).hexdigest())

    def tearDown(self):
        self.temporary.cleanup()

    def source_file(self, content=None):
        path = self.source / self.model[0] / self.model[1]
        path.parent.mkdir(parents=True)
        path.write_bytes(self.data if content is None else content)
        return path

    def install(self, **kwargs):
        return installer.install_models(self.engine, self.staging, model_files=[self.model], **kwargs)

    def no_network(self, *_args):
        self.fail("Offline installation attempted a network download")

    def test_offline_bundle_is_copied_verified_and_source_preserved(self):
        source = self.source_file()
        result = self.install(source_dir=self.source, downloader=self.no_network)
        target = self.engine / "models" / self.model[0] / self.model[1]
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(source.read_bytes(), self.data)
        self.assertTrue(result["files"][0]["installed"])
        self.assertFalse(result["core_updated"])
        source.write_bytes(b"source later changed")
        self.assertEqual(target.read_bytes(), self.data)

    def test_existing_verified_file_needs_neither_network_nor_free_space(self):
        target = self.engine / "models" / self.model[0] / self.model[1]
        target.parent.mkdir(parents=True)
        target.write_bytes(self.data)
        with mock.patch.object(installer.shutil, "disk_usage", side_effect=AssertionError("unnecessary space check")):
            result = self.install(downloader=self.no_network)
        self.assertTrue(result["files"][0]["verified_existing"])
        self.assertFalse(result["files"][0]["installed"])

    def test_existing_wrong_file_is_preserved(self):
        target = self.engine / "models" / self.model[0] / self.model[1]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(downloader=self.no_network)
        self.assertEqual(target.read_bytes(), b"x" * len(self.data))

    def test_corrupt_same_size_cached_source_cannot_be_published(self):
        source = self.source_file(b"x" * len(self.data))
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(source_dir=self.source, downloader=self.no_network)
        self.assertEqual(source.read_bytes(), b"x" * len(self.data))
        self.assertFalse((self.engine / "models" / self.model[0] / self.model[1]).exists())

    def test_missing_offline_source_has_no_network_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "no network fallback"):
            self.install(source_dir=self.source, downloader=self.no_network)

    def test_downloader_cannot_publish_from_outside_staging(self):
        source = self.source_file()
        with self.assertRaisesRegex(RuntimeError, "outside the selected staging"):
            self.install(downloader=lambda *_args: source)
        self.assertTrue(source.exists())

    def test_downloaded_checksum_mismatch_leaves_target_absent(self):
        def download(staging, model):
            file = staging / model[0] / model[1]
            file.parent.mkdir(parents=True)
            file.write_bytes(b"x" * len(self.data))
            return file
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(downloader=download)
        self.assertFalse((self.engine / "models" / self.model[0] / self.model[1]).exists())

    def test_older_engine_is_rejected_without_changing_source_or_engine(self):
        source = self.source_file()
        version = self.engine / "comfyui_version.py"
        version.write_text('__version__ = "0.37.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "requires ComfyUI"):
            self.install(source_dir=self.source, downloader=self.no_network)
        self.assertFalse((self.engine / "models").exists())
        self.assertEqual(source.read_bytes(), self.data)
        self.assertIn("0.37.0", version.read_text(encoding="utf-8"))

    def test_engine_version_is_parsed_without_executing_python(self):
        marker = self.root / "unexpected"
        (self.engine / "comfyui_version.py").write_text(
            f'__version__ = "0.41.3"\nopen({str(marker)!r}, "w").write("unexpected")\n', encoding="utf-8")
        self.assertEqual(installer.require_engine(self.engine), "0.41.3")
        self.assertFalse(marker.exists())

    def test_manifest_path_escape_is_rejected(self):
        malformed = ("diffusion_models", "../escape.safetensors", len(self.data), self.model[3])
        with self.assertRaisesRegex(RuntimeError, "Invalid pinned"):
            installer.install_models(self.engine, self.staging, model_files=[malformed], downloader=self.no_network)


if __name__ == "__main__":
    unittest.main()
