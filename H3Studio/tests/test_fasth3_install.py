import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import setup_fasth3_omni as installer


class FastH3InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.engine = self.root / "engine"
        self.engine.mkdir()
        (self.engine / "main.py").write_text("", "utf-8")
        (self.engine / "comfyui_version.py").write_text('__version__ = "0.42.0"\n', "utf-8")
        core = self.engine / "comfy/ldm/minimax/model.py"
        core.parent.mkdir(parents=True)
        core.write_text("class FinalLayer:\n    def __init__(self, pdd_steps=1): pass\n"
                        "class MiniMaxH3Model:\n    def __init__(self, pdd_steps=1): pass\n", "utf-8")
        self.data = b"verified FastH3 checkpoint"
        self.model = ("fast-test.safetensors", len(self.data), hashlib.sha256(self.data).hexdigest())
        self.staging = self.engine / "models/.fasth3-download"
        self.source = self.root / "offline.safetensors"
        self.source.write_bytes(self.data)
        self.shared = ("text_encoders", "shared.safetensors", 6, hashlib.sha256(b"shared").hexdigest())
        self.vae = ("vae", "video.safetensors", 3, hashlib.sha256(b"vae").hexdigest())
        for model, data in ((self.shared, b"shared"), (self.vae, b"vae")):
            path = self.engine / "models" / model[0] / model[1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        patch = mock.patch.multiple(installer, SHARED_COMPONENTS=(self.shared,), VIDEO_VAES=(self.vae,))
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, **kwargs):
        return installer.install_model(self.engine, self.staging, model=self.model, **kwargs)

    def no_network(self, *_args):
        self.fail("Offline installation attempted a download")

    @property
    def target(self):
        return self.engine / "models/diffusion_models" / self.model[0]

    def test_verified_offline_checkpoint_is_copied_and_original_h3_preserved(self):
        old = self.engine / "models/diffusion_models/original-h3.safetensors"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old H3")
        result = self.install(source_file=self.source, downloader=self.no_network)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(old.read_bytes(), b"old H3")
        self.assertTrue(result["installed"])
        self.assertFalse(result["core_updated"])

    def test_existing_verified_checkpoint_does_not_download_or_need_space(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.data)
        with mock.patch.object(installer.shutil, "disk_usage", side_effect=AssertionError("space check")):
            result = self.install(downloader=self.no_network)
        self.assertFalse(result["installed"])
        self.assertTrue(result["verified_existing"])

    def test_existing_corrupt_checkpoint_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(downloader=self.no_network)
        self.assertEqual(self.target.read_bytes(), b"x" * len(self.data))

    def test_offline_checksum_mismatch_never_publishes(self):
        self.source.write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(source_file=self.source, downloader=self.no_network)
        self.assertFalse(self.target.exists())

    def test_missing_shared_component_blocks_before_download(self):
        (self.engine / "models" / self.shared[0] / self.shared[1]).unlink()
        with self.assertRaisesRegex(RuntimeError, "Size verification"):
            self.install(downloader=self.no_network)
        self.assertFalse(self.target.exists())

    def test_video_vae_is_required(self):
        (self.engine / "models" / self.vae[0] / self.vae[1]).unlink()
        with self.assertRaisesRegex(RuntimeError, "at least one video VAE"):
            self.install(downloader=self.no_network)

    def test_old_core_rejected_without_modifying_engine_or_source(self):
        (self.engine / "comfyui_version.py").write_text('__version__ = "0.41.3"\n', "utf-8")
        with self.assertRaisesRegex(RuntimeError, "requires ComfyUI 0.42.0"):
            self.install(source_file=self.source, downloader=self.no_network)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_same_version_without_full_pdd_constructor_is_rejected(self):
        (self.engine / "comfy/ldm/minimax/model.py").write_text(
            "class FinalLayer:\n    def __init__(self): pass\n"
            "class MiniMaxH3Model:\n    def __init__(self): pass\n", "utf-8")
        with self.assertRaisesRegex(RuntimeError, "lacks full PDD"):
            self.install(source_file=self.source, downloader=self.no_network)

    def test_version_file_is_not_executed(self):
        marker = self.root / "unexpected"
        (self.engine / "comfyui_version.py").write_text(
            f'__version__ = "0.42.0"\nopen({str(marker)!r}, "w").write("bad")\n', "utf-8")
        self.assertEqual(installer.require_engine(self.engine), "0.42.0")
        self.assertFalse(marker.exists())

    def test_downloader_cannot_return_unrelated_path(self):
        with self.assertRaisesRegex(RuntimeError, "outside the selected staging"):
            self.install(downloader=lambda _staging: self.source)
        self.assertTrue(self.source.exists())

    def test_wrong_download_hash_never_publishes(self):
        def download(staging):
            file = staging / self.model[0]
            file.write_bytes(b"x" * len(self.data))
            return file
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install(downloader=download)
        self.assertFalse(self.target.exists())

    def test_manifest_path_escape_is_rejected(self):
        malformed = ("../escape.safetensors", self.model[1], self.model[2])
        with self.assertRaisesRegex(RuntimeError, "Invalid pinned"):
            installer.install_model(self.engine, self.staging, model=malformed, downloader=self.no_network)


if __name__ == "__main__":
    unittest.main()
