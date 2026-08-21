import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine_installer import ALL_MODEL_FILES, InstallerError, installer_preflight, model_state, resolve_install_target
from runtime_env import check_python_executable, inspect_python_candidates


class EngineInstallerTests(unittest.TestCase):
    def test_python_environment_is_executed_instead_of_only_checking_file_presence(self):
        ready, detail = check_python_executable(Path(sys.executable), required_imports=("json",))
        self.assertTrue(ready, detail)
        with tempfile.TemporaryDirectory() as folder:
            copied_python = Path(folder) / "python.exe"
            copied_python.write_bytes(b"not a portable Python executable")
            state = inspect_python_candidates([copied_python])
            self.assertFalse(state["ready"])
            self.assertEqual(state["detail"], "偵測到不可執行的搬移環境")

    def test_install_target_is_resolved_and_drive_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder) / "H3Studio"
            app_dir.mkdir()
            self.assertEqual(resolve_install_target("../ComfyUI", app_dir), app_dir.parent / "ComfyUI")
        with self.assertRaises(InstallerError):
            resolve_install_target(Path.cwd().anchor, Path.cwd())

    def test_missing_models_are_reported_without_creating_large_files(self):
        with tempfile.TemporaryDirectory() as folder:
            state = model_state(Path(folder))
            self.assertEqual(len(state), len(ALL_MODEL_FILES))
            self.assertTrue(all(not item["ready"] for item in state))

    def test_preflight_accepts_empty_folder_with_enough_space_and_nvidia(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "ComfyUI"
            with (
                patch("engine_installer.detect_nvidia_gpu", return_value={"name": "RTX Test", "vram_mb": 16384, "vram_gb": 16.0}),
                patch("engine_installer.system_memory_gb", return_value=64.0),
                patch("engine_installer.shutil.which", return_value="git.exe"),
                patch("engine_installer.shutil.disk_usage", return_value=SimpleNamespace(total=200 * 1024**3, used=0, free=200 * 1024**3)),
            ):
                result = installer_preflight(target)
            self.assertTrue(result["ready_to_install"])
            self.assertFalse(result["installed"])
            self.assertGreater(result["required_gb"], 70)

    def test_preflight_marks_copied_broken_venv_for_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "ComfyUI"
            copied_python = target / ".venv" / "Scripts" / "python.exe"
            copied_python.parent.mkdir(parents=True)
            copied_python.write_bytes(b"broken copied launcher")
            (target / "main.py").write_text("", encoding="utf-8")
            with (
                patch("engine_installer.detect_nvidia_gpu", return_value={"name": "RTX Test", "vram_mb": 16384, "vram_gb": 16.0}),
                patch("engine_installer.system_memory_gb", return_value=64.0),
                patch("engine_installer.shutil.which", return_value="git.exe"),
                patch("engine_installer.shutil.disk_usage", return_value=SimpleNamespace(total=200 * 1024**3, used=0, free=200 * 1024**3)),
            ):
                result = installer_preflight(target)
            self.assertTrue(result["ready_to_install"])
            self.assertTrue(result["environment_repair_required"])
            self.assertFalse(result["environment"]["ready"])
            self.assertTrue(any("只重建本機 .venv" in warning for warning in result["warnings"]))

    def test_worker_dry_run_finishes_without_network_or_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Path(__file__).resolve().parents[1] / "install_engine_worker.py"
            result = subprocess.run(
                [sys.executable, str(worker), "--target", str(Path(folder) / "ComfyUI"), "--dry-run"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"progress": 100', result.stdout)
            self.assertFalse((Path(folder) / "ComfyUI").exists())


if __name__ == "__main__":
    unittest.main()
