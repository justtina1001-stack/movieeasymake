"""Pinned expansion installation preserves files and resumes verified byte ranges."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import setup_video_expansion as setup


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entry(name="test.safetensors", data=b"known weights"):
    return dict(repo_id="example/model", revision="a" * 40, source=name,
                target="loras/" + name, size=len(data), sha256=digest(data))


class Response(io.BytesIO):
    def __init__(self, data, content_range):
        super().__init__(data)
        self.status = 206
        self.headers = {"Content-Range": content_range}


class VideoExpansionInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = self.root / "engine"
        self.engine.mkdir()
        (self.engine / "main.py").write_text("# local engine", "utf-8")
        (self.engine / "comfyui_version.py").write_text('__version__ = "0.42.0"', "utf-8")
        (self.engine / "models").mkdir()
        self.stage = self.engine / "models/.stage"
        self.node = dict(repo_url="https://github.com/example/ref-patch.git",
                         revision="b" * 40, target="RefPatch")

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, items):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(dict(schema_version=1, version="test", files=items,
                                        custom_nodes=[self.node])), "utf-8")
        return path

    def test_real_manifest_pins_all_nine_additions_and_keeps_old_versions_separate(self):
        manifest = setup.validate_manifest(setup.MANIFEST_PATH)
        self.assertEqual(len(manifest["files"]), 9)
        names = {Path(item["target"]).name for item in manifest["files"]}
        self.assertIn("pika_h3_flex_fl2va_v1_bf16.safetensors", names)
        self.assertIn("pika_h3_flex_ref2va_v1_bf16.safetensors", names)
        self.assertIn("h3_character_swap_v1.5.safetensors", names)
        self.assertIn("Motion_Repair_V2.safetensors", names)
        self.assertIn("minimax_h3_ref_patch.safetensors", names)
        self.assertEqual(sum("turbo" in name for name in names), 4)
        self.assertNotIn("h3_character_swap_pro4500_1000.safetensors", names)
        self.assertTrue(all(len(item["sha256"]) == 64 and len(item["revision"]) == 40 for item in manifest["files"]))

    def test_install_and_rerun_verify_without_replacing_older_models(self):
        data = b"known weights"
        item = entry(data=data)
        old = self.engine / "models/old-version.safetensors"
        old.write_bytes(b"old model")
        def download(row, stage, progress):
            path = stage / row["source"]
            path.write_bytes(data)
            return path
        downloader = Mock(side_effect=download)
        node_installer = Mock(return_value={"installed": True})
        path = self.manifest([item])
        result = setup.install_expansion(self.engine, self.stage, manifest=path, downloader=downloader,
                                         node_installer=node_installer, evidence_dir=self.root / "evidence")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["models"][0]["installed"])
        self.assertEqual(old.read_bytes(), b"old model")
        self.assertEqual((self.engine / "models" / item["target"]).read_bytes(), data)
        second = setup.install_expansion(self.engine, self.stage, manifest=path, downloader=downloader,
                                         node_installer=node_installer)
        self.assertTrue(second["models"][0]["verified_existing"])
        downloader.assert_called_once()
        self.assertFalse((self.stage / ".installer-running").exists())
        report = json.loads((self.root / "evidence/video-expansion-install.json").read_text("utf-8"))
        self.assertFalse(report["core_updated"])
        self.assertFalse(report["services_restarted"])

    def test_bad_hash_is_never_published_and_existing_file_is_preserved(self):
        item = entry(data=b"abc")
        def download(row, stage, progress):
            path = stage / row["source"]
            path.write_bytes(b"bad")
            return path
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            setup.install_artifact(self.engine, self.stage, item, downloader=download)
        self.assertFalse((self.engine / "models" / item["target"]).exists())
        partial = self.stage / "example--model" / item["revision"] / item["source"]
        self.assertEqual(partial.read_bytes(), b"bad")
        target = self.engine / "models" / item["target"]
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"old")
        downloader = Mock()
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            setup.install_artifact(self.engine, self.stage, item, downloader=downloader)
        downloader.assert_not_called()
        self.assertEqual(target.read_bytes(), b"old")

    def test_racing_destination_is_not_overwritten(self):
        item = entry(data=b"abc")
        target = self.engine / "models" / item["target"]
        def download(row, stage, progress):
            path = stage / row["source"]
            path.write_bytes(b"abc")
            target.write_bytes(b"new")
            return path
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            setup.install_artifact(self.engine, self.stage, item, downloader=download)
        self.assertEqual(target.read_bytes(), b"new")

    def test_download_path_outside_staging_is_refused(self):
        item = entry(data=b"abc")
        outside = self.root / "outside.safetensors"
        outside.write_bytes(b"abc")
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            setup.install_artifact(self.engine, self.stage, item, downloader=lambda *args: outside)
        self.assertEqual(outside.read_bytes(), b"abc")
        self.assertFalse((self.engine / "models" / item["target"]).exists())

    def test_download_ranges_resumes_exact_prefix_and_validates_each_range(self):
        data = b"abcdefghij"
        item = entry(data=data)
        self.stage.mkdir()
        partial = self.stage / (item["source"] + ".range-partial")
        partial.write_bytes(data[:4])
        requests = []
        def opener(request, timeout):
            value = request.get_header("Range")
            requests.append(value)
            start, end = map(int, value.removeprefix("bytes=").split("-"))
            return Response(data[start:end + 1], f"bytes {start}-{end}/{len(data)}")
        progress = Mock()
        result = setup.download_ranges(item, self.stage, opener=opener, chunk_size=3, progress=progress)
        self.assertEqual(result.read_bytes(), data)
        self.assertEqual(requests, ["bytes=4-6", "bytes=7-9"])
        progress.assert_called_once_with(10, 10)

    def test_shared_hub_cache_is_verified_and_copied_not_moved_outside_staging(self):
        data = b"cached model"
        item = entry(data=data)
        shared = self.root / "shared-cache"
        shared.write_bytes(data)
        self.stage.mkdir()
        with patch("huggingface_hub.hf_hub_download", return_value=str(shared)), patch.object(setup, "download_ranges") as ranges:
            cached = setup.download_official(item, self.stage)
        self.assertTrue(cached.resolve().is_relative_to(self.stage.resolve()))
        self.assertEqual(cached.read_bytes(), data)
        self.assertEqual(shared.read_bytes(), data)
        ranges.assert_not_called()

    def test_wrong_byte_range_never_appends_to_partial(self):
        item = entry(data=b"abcdef")
        self.stage.mkdir()
        partial = self.stage / (item["source"] + ".range-partial")
        partial.write_bytes(b"ab")
        def opener(request, timeout):
            return Response(b"abc", "bytes 0-2/6")
        with patch.object(setup.time, "sleep"), self.assertRaisesRegex(RuntimeError, "retained"):
            setup.download_ranges(item, self.stage, opener=opener, chunk_size=3)
        self.assertEqual(partial.read_bytes(), b"ab")

    def test_manifest_rejects_unpinned_duplicate_and_escaped_destinations(self):
        for changes in ({"revision": "main"}, {"sha256": ""}, {"target": "../outside.safetensors"},
                        {"target": "diffusion_models/replacement.safetensors"}):
            with self.subTest(changes=changes), self.assertRaises((RuntimeError, ValueError)):
                setup.validate_manifest(self.manifest([{**entry(), **changes}]))
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            setup.validate_manifest(self.manifest([entry(), entry()]))

    def test_low_disk_space_refuses_before_downloading_or_installing_node(self):
        downloader, node_installer = Mock(), Mock()
        with patch.object(setup.shutil, "disk_usage", return_value=Mock(free=0)):
            with self.assertRaisesRegex(RuntimeError, "disk space"):
                setup.install_expansion(self.engine, self.stage, manifest=self.manifest([entry()]),
                                         downloader=downloader, node_installer=node_installer)
        downloader.assert_not_called()
        node_installer.assert_not_called()

    def test_running_lock_is_preserved_and_prevents_second_install(self):
        self.stage.mkdir()
        lock = self.stage / ".installer-running"
        lock.mkdir()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            setup.install_expansion(self.engine, self.stage, manifest=self.manifest([entry()]))
        self.assertTrue(lock.is_dir())

    def test_node_failure_records_failure_and_releases_only_own_empty_lock(self):
        evidence = self.root / "evidence"
        node_installer = Mock(side_effect=RuntimeError("Pinned node unavailable"))
        downloader = Mock()
        with self.assertRaisesRegex(RuntimeError, "Pinned node"):
            setup.install_expansion(self.engine, self.stage, manifest=self.manifest([entry()]),
                                     downloader=downloader, node_installer=node_installer, evidence_dir=evidence)
        downloader.assert_not_called()
        self.assertFalse((self.stage / ".installer-running").exists())
        report = json.loads((evidence / "video-expansion-install.json").read_text("utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"][0]["phase"], "setup")

    def test_node_verification_never_replaces_dirty_or_other_revision_checkout(self):
        node_dir = self.root / "node"
        node_dir.mkdir()
        (node_dir / ".git").mkdir()
        (node_dir / "__init__.py").write_text(
            "class MiniMaxH3RefPatchLoader:\n def INPUT_TYPES(self): pass\n def apply_patch(self): pass\n", "utf-8")
        with patch.object(setup, "git", side_effect=["c" * 40]), self.assertRaisesRegex(RuntimeError, "differs"):
            setup.verify_node(node_dir, self.node)
        with patch.object(setup, "git", side_effect=[self.node["revision"], " M __init__.py"]), self.assertRaisesRegex(RuntimeError, "differs"):
            setup.verify_node(node_dir, self.node)
        with patch.object(setup, "git", side_effect=[self.node["revision"], ""]):
            self.assertEqual(setup.verify_node(node_dir, self.node)["node_class"], "MiniMaxH3RefPatchLoader")

    def test_older_engine_is_refused_without_upgrade(self):
        version = self.engine / "comfyui_version.py"
        version.write_text('__version__ = "0.41.0"', "utf-8")
        with self.assertRaisesRegex(RuntimeError, "no automatic"):
            setup.require_engine(self.engine)
        self.assertIn("0.41.0", version.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
