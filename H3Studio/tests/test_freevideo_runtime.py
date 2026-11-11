"""Readiness cannot advertise a partial/foreign isolated video installation."""
import json
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import freevideo_runtime as runtime
import setup_freevideo as setup


class FreeVideoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.engine = self.root / "ComfyUI"
        self.node = self.engine / "custom_nodes/FreeVideo"
        self.node.mkdir(parents=True)
        (self.engine / "main.py").touch()
        self.install = self.root / "private-runtime"
        self.install.mkdir()
        self.pack = self.install / "cache"
        self.pack.mkdir()
        (self.pack / "weights.bin").write_bytes(b"verified")
        self.write(self.pack / "manifest.json", {"precision": "int8", "groups": [
            {"file": "weights.bin", "bytes": 8}]})
        self.manifest = {"node_directory": "FreeVideo", "source_revision": "a" * 40,
                         "model_revision": "b" * 40, "prepared_format": "int8_convrot",
                         "prepared_manifest_sha256": runtime.file_sha256(self.pack / "manifest.json"), "source_files": {}}
        self.machine = {"ready": True, "root": str(self.install), "cache": str(self.pack),
                        "encoder": "encoder.safetensors", "gpu_uuid": "GPU-test",
                        "model_revision": "b" * 40, "prepared_format": "int8_convrot"}
        for name in ("python", "comfy_python", "model_paths"):
            path = self.install / name
            path.touch()
            self.machine[name] = str(path)
        for name in ("base", "checkpoint", "comfy_root", "vdn_root", "model_root", "encoder_model_root"):
            path = self.install / name
            path.mkdir()
            self.machine[name] = str(path)
        (self.node / "freevideo_engine").mkdir()
        self.write(self.node / "freevideo_engine/model_files.json", [
            {"file": "decoder.safetensors", "bytes": 3}])
        (Path(self.machine["model_root"]) / "decoder.safetensors").write_bytes(b"vae")
        self.write(self.install / "machine.json", self.machine)
        self.write(self.node / "comfyui.json", {"installation": str(self.install)})
        self.receipt = {"status": "verified", "source_revision": "a" * 40,
                        "prepared_manifest_sha256": self.manifest["prepared_manifest_sha256"]}
        self.write(self.install / "h3studio-install.json", self.receipt)
        self.manifest_patch = patch.object(runtime, "read_manifest", return_value=self.manifest)
        self.manifest_patch.start()
        self.addCleanup(self.manifest_patch.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def status(self):
        return runtime.freevideo_runtime_status(self.engine)

    def test_verified_complete_pack_is_ready_without_importing_runtime(self):
        with patch("subprocess.run", side_effect=AssertionError("Polling cannot spawn a process")):
            status = self.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["groups"], 1)

    def test_ready_flag_alone_cannot_make_incomplete_setup_selectable(self):
        (self.install / "h3studio-install.json").unlink()
        self.assertFalse(self.status()["ready"])

    def test_partial_or_deleted_tensor_group_is_unavailable(self):
        path = self.pack / "weights.bin"
        path.write_bytes(b"partial")
        self.assertFalse(self.status()["ready"])
        path.unlink()
        self.assertFalse(self.status()["ready"])

    def test_changed_pack_manifest_is_not_silently_accepted(self):
        with (self.pack / "manifest.json").open("a") as stream:
            stream.write(" ")
        self.assertFalse(self.status()["ready"])

    def test_machine_model_and_prepared_revision_are_enforced(self):
        for field, value in (("model_revision", "c" * 40), ("prepared_format", "fp8"), ("video_model", False)):
            with self.subTest(field=field):
                self.write(self.install / "machine.json", dict(self.machine, **{field: value}))
                self.assertFalse(self.status()["ready"])

    def test_missing_private_python_is_unavailable(self):
        Path(self.machine["python"]).unlink()
        self.assertFalse(self.status()["ready"])

    def test_missing_decoder_is_unavailable_even_with_complete_transformer(self):
        (Path(self.machine["model_root"]) / "decoder.safetensors").unlink()
        self.assertFalse(self.status()["ready"])

    def test_latent_upscaler_uses_the_official_target_directory(self):
        self.write(self.node / "freevideo_engine/model_files.json", [
            {"file": "original-upscaler-folder/upscaler.safetensors", "bytes": 3, "role": "latent_upscaler"}])
        folder = Path(self.machine["model_root"]) / "latent_upscaler"
        folder.mkdir()
        (folder / "upscaler.safetensors").write_bytes(b"vae")
        self.assertTrue(self.status()["ready"])

    def test_foreign_root_and_missing_gpu_identity_are_unavailable(self):
        for field, value in (("root", str(self.root)), ("gpu_uuid", "")):
            with self.subTest(field=field):
                self.write(self.install / "machine.json", dict(self.machine, **{field: value}))
                self.assertFalse(self.status()["ready"])

    def test_uninstalled_node_gives_actionable_unavailable_result(self):
        (self.node / "comfyui.json").unlink()
        status = self.status()
        self.assertFalse(status["ready"])
        self.assertIn("setup_freevideo.bat", status["error"])

    def test_installer_refuses_runtime_inside_existing_engine(self):
        for path in (self.engine, self.engine / "models", self.root):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                setup.validate_layout(self.engine, path)

    def test_installer_preserves_a_binding_to_another_runtime(self):
        before = (self.node / "comfyui.json").read_bytes()
        with self.assertRaises(RuntimeError):
            setup.bind_installation(self.node, self.root / "other")
        self.assertEqual((self.node / "comfyui.json").read_bytes(), before)

    def test_bootstrap_uses_isolated_root_and_never_starts_frontend(self):
        command = setup.bootstrap_command(Path(self.machine["python"]), self.node, self.engine, self.install)
        self.assertEqual(command[command.index("--root") + 1], str(self.install))
        self.assertIn("--no-launch", command)
        self.assertIn("--network", command)
        self.assertNotIn("--frontend-root", command)
        self.assertNotIn("--frontend-download", command)
        self.assertNotIn("--image-model", command)
        self.assertEqual(command[command.index("--prepared-format") + 1], "int8")

    def test_gpu_probe_gate_is_explicit_and_does_not_change_numerical_flags(self):
        gate = self.install / "probe-allowed"
        command = setup.bootstrap_command(Path(self.machine["python"]), self.node, self.engine, self.install, probe_gate=gate)
        self.assertEqual(command[command.index("--probe-gate") + 1], str(gate))
        self.assertIn("--no-launch", command)


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class VerifiedRuntimeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "wheel.whl"
        self.url = "https://download.pytorch.org/whl/test.whl"
        self.data = b"complete verified runtime"
        self.sha = hashlib.sha256(self.data).hexdigest()

    def fetch(self, response):
        return setup.verified_download(self.url, self.path, len(self.data), self.sha,
                                       opener=lambda *a, **k: response)

    def test_only_verified_complete_artifact_is_published(self):
        self.fetch(Response(self.data))
        self.assertEqual(self.path.read_bytes(), self.data)
        self.assertFalse(self.path.with_name("wheel.whl.h3studio.partial").exists())

    def test_hash_mismatch_retains_partial_and_never_publishes(self):
        with self.assertRaises(RuntimeError):
            self.fetch(Response(b"x" * len(self.data)))
        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_name("wheel.whl.h3studio.partial").exists())

    def test_short_download_retains_partial(self):
        with self.assertRaises(RuntimeError):
            self.fetch(Response(self.data[:5]))
        self.assertFalse(self.path.exists())

    def test_resume_requires_matching_http_range_and_retains_existing_bytes(self):
        partial = self.path.with_name("wheel.whl.h3studio.partial")
        partial.write_bytes(self.data[:5])
        seen = []
        def opener(request, **kwargs):
            seen.append(request.get_header("Range"))
            return Response(self.data[5:], 206, {"Content-Range": f"bytes 5-{len(self.data)-1}/{len(self.data)}"})
        setup.verified_download(self.url, self.path, len(self.data), self.sha, opener=opener)
        self.assertEqual(seen, ["bytes=5-"])
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_ignored_range_does_not_overwrite_partial(self):
        partial = self.path.with_name("wheel.whl.h3studio.partial")
        partial.write_bytes(self.data[:5])
        with self.assertRaises(RuntimeError):
            self.fetch(Response(self.data))
        self.assertEqual(partial.read_bytes(), self.data[:5])

    def test_existing_verified_wheel_is_reused_without_network(self):
        self.path.write_bytes(self.data)
        setup.verified_download(self.url, self.path, len(self.data), self.sha,
                                opener=lambda *a, **k: self.fail("Unnecessary network call"))

    def test_other_host_is_rejected(self):
        with self.assertRaises(ValueError):
            setup.verified_download("https://example.invalid/wheel.whl", self.path, len(self.data), self.sha)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
