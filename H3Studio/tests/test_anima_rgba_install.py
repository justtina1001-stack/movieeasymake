"""Anima installation verifies pinned artifacts and preserves existing models."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import setup_anima_rgba as setup


def entry(name="test.safetensors", data=b"known weights", directory="diffusion_models"):
    return dict(repo_id="example/model", revision="a" * 40, source=name,
                target=f"{directory}/{name}", size=len(data), sha256=hashlib.sha256(data).hexdigest())


class AnimaRGBAInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = self.root / "engine"
        self.engine.mkdir()
        (self.engine / "main.py").write_text("# engine", "utf-8")
        (self.engine / "comfyui_version.py").write_text('__version__ = "0.42.0"', "utf-8")
        (self.engine / "models").mkdir()
        self.stage = self.engine / "models/.anima-stage"

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, items, nodes=None):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(dict(schema_version=1, version="test", files=items,
                                        custom_nodes=nodes or [])), "utf-8")
        return path

    def test_shipped_manifest_has_exact_four_native_pinned_files(self):
        manifest = setup.validate_manifest(setup.MANIFEST_PATH)
        self.assertEqual({row["target"] for row in manifest["files"]}, {
            "diffusion_models/anima-base-v1.0.safetensors",
            "text_encoders/qwen_3_06b_base.safetensors",
            "vae/anima_rgba_vae.safetensors",
            "loras/anima_rgba/anima_rgba_lora.safetensors",
        })
        self.assertEqual(sum(row["size"] for row in manifest["files"]), 6065570480)
        self.assertFalse(manifest["custom_nodes"])
        self.assertTrue(all(len(row["revision"]) == 40 and len(row["sha256"]) == 64 for row in manifest["files"]))

    def test_manifest_rejects_unpinned_duplicate_unsafe_or_non_model_files(self):
        for changes in ({"revision": "main"}, {"sha256": ""}, {"target": "../outside.safetensors"},
                        {"target": "custom_nodes/execute.safetensors"}, {"target": "vae/weights.py"},
                        {"source": "unsafe.py"}, {"repo_id": "../source"}):
            with self.subTest(changes=changes), self.assertRaises((RuntimeError, ValueError)):
                setup.validate_manifest(self.manifest([{**entry(), **changes}]))
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            setup.validate_manifest(self.manifest([entry(), entry()]))

    def test_manifest_refuses_custom_nodes(self):
        node = dict(repo_url="https://github.com/example/node.git", revision="a" * 40, target="Node")
        with self.assertRaisesRegex(RuntimeError, "native"):
            setup.validate_manifest(self.manifest([entry()], nodes=[node]))

    def test_install_and_verify_only_preserve_other_models_without_second_download(self):
        item = entry()
        old = self.engine / "models/old.safetensors"
        old.write_bytes(b"old model")
        def download(row, stage, progress):
            path = stage / row["source"]
            path.write_bytes(b"known weights")
            return path
        downloader = Mock(side_effect=download)
        path = self.manifest([item])
        first = setup.install_anima_rgba(self.engine, self.stage, manifest=path, downloader=downloader)
        self.assertTrue(first["models"][0]["installed"])
        evidence = self.root / "evidence"
        second = setup.install_anima_rgba(self.engine, self.stage, manifest=path, downloader=downloader,
                                         evidence_dir=evidence, verify_only=True)
        self.assertTrue(second["models"][0]["verified_existing"])
        self.assertFalse(second["models"][0]["installed"])
        downloader.assert_called_once()
        self.assertEqual(old.read_bytes(), b"old model")
        self.assertFalse((self.stage / ".installer-running").exists())
        saved = json.loads((evidence / "anima-rgba-install.json").read_text("utf-8"))
        self.assertTrue(saved["verify_only"])
        self.assertFalse(saved["core_updated"])
        self.assertFalse(saved["services_restarted"])
        self.assertFalse(saved["custom_nodes_installed"])

    def test_verify_only_missing_weights_never_downloads_or_locks(self):
        downloader = Mock()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([entry()]),
                                     downloader=downloader, verify_only=True)
        downloader.assert_not_called()
        self.assertFalse(self.stage.exists())

    def test_verify_only_cannot_download_even_if_file_disappears_after_preflight(self):
        item = entry()
        target = self.engine / "models" / item["target"]
        target.parent.mkdir()
        target.write_bytes(b"known weights")
        original_verify = setup.verify
        def disappearing(*args, **kwargs):
            target.unlink()
            return original_verify(*args, **kwargs)
        downloader = Mock()
        with patch.object(setup, "verify", side_effect=disappearing), patch.object(setup, "install_artifact") as install:
            with self.assertRaisesRegex(RuntimeError, "files failed"):
                setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([item]),
                                         downloader=downloader, verify_only=True)
        install.assert_not_called()
        downloader.assert_not_called()

    def test_hash_mismatch_keeps_existing_file_and_records_failure(self):
        item = entry(data=b"abc")
        target = self.engine / "models" / item["target"]
        target.parent.mkdir()
        target.write_bytes(b"old")
        downloader = Mock()
        evidence = self.root / "evidence"
        with self.assertRaisesRegex(RuntimeError, "files failed"):
            setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([item]),
                                     downloader=downloader, evidence_dir=evidence)
        downloader.assert_not_called()
        self.assertEqual(target.read_bytes(), b"old")
        saved = json.loads((evidence / "anima-rgba-install.json").read_text("utf-8"))
        self.assertEqual(saved["status"], "failed")
        self.assertIn("SHA256", saved["errors"][0]["error"])
        self.assertFalse((self.stage / ".installer-running").exists())

    def test_bad_download_is_not_published(self):
        item = entry(data=b"abc")
        def download(row, stage, progress):
            path = stage / row["source"]
            path.write_bytes(b"bad")
            return path
        with self.assertRaisesRegex(RuntimeError, "files failed"):
            setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([item]), downloader=download)
        self.assertFalse((self.engine / "models" / item["target"]).exists())

    def test_insufficient_disk_refuses_before_download_or_lock(self):
        downloader = Mock()
        with patch.object(setup.shutil, "disk_usage", return_value=Mock(free=0)):
            with self.assertRaisesRegex(RuntimeError, "disk space"):
                setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([entry()]), downloader=downloader)
        downloader.assert_not_called()
        self.assertFalse(self.stage.exists())

    def test_installer_lock_is_preserved_and_blocks_second_install(self):
        self.stage.mkdir()
        lock = self.stage / ".installer-running"
        lock.mkdir()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([entry()]))
        self.assertTrue(lock.is_dir())

    def test_older_engine_is_preserved_and_refused(self):
        version = self.engine / "comfyui_version.py"
        version.write_text('__version__ = "0.41.0"', "utf-8")
        downloader = Mock()
        with self.assertRaisesRegex(RuntimeError, "no automatic"):
            setup.install_anima_rgba(self.engine, self.stage, manifest=self.manifest([entry()]), downloader=downloader)
        downloader.assert_not_called()
        self.assertIn("0.41.0", version.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
