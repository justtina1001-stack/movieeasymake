import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from model_updates import ModelUpdateError, ModelUpdateManager, load_model_manifest


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "data" / "test-model-updates"


@contextmanager
def workspace():
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class ModelUpdateTests(unittest.TestCase):
    def make_manager(self, root: Path, *, mode: str = "local", installed: bool = False):
        app_dir = root / "H3Studio"
        data_dir = app_dir / "data"
        comfy_dir = root / "ComfyUI"
        app_dir.mkdir()
        (comfy_dir / "models" / "diffusion_models").mkdir(parents=True)
        (comfy_dir / "main.py").write_text("", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "version": "test-2",
            "published_at": "2026-08-21",
            "channel": "stable",
            "title": "Test model",
            "summary": "Test update",
            "changes": ["Faster"],
            "files": [{
                "repo_id": "test/models",
                "revision": "abc123",
                "source": "diffusion_models/new.safetensors",
                "target": "diffusion_models/new.safetensors",
                "size": 4,
            }],
        }
        (app_dir / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if installed:
            (comfy_dir / "models" / "diffusion_models" / "new.safetensors").write_bytes(b"test")
        settings = SimpleNamespace(mode=mode, comfy_dir=str(comfy_dir))
        return ModelUpdateManager(app_dir, data_dir, lambda: settings), data_dir

    def test_complete_current_version_does_not_prompt(self):
        with workspace() as folder:
            manager, _ = self.make_manager(folder, installed=True)
            result = manager.inspect()
            self.assertFalse(result["update_available"])
            self.assertFalse(result["should_prompt"])
            self.assertEqual(result["ready_files"], 1)

    def test_missing_model_prompts_until_user_skips_or_defers(self):
        with workspace() as folder:
            manager, data_dir = self.make_manager(folder)
            self.assertTrue(manager.inspect()["should_prompt"])
            skipped = manager.skip_current()
            self.assertTrue(skipped["skipped"])
            self.assertFalse(skipped["should_prompt"])
            preferences = json.loads((data_dir / "model_update_preferences.json").read_text(encoding="utf-8"))
            self.assertEqual(preferences["skipped_version"], "test-2")
            restored = manager.restore_prompt()
            self.assertTrue(restored["should_prompt"])
            deferred = manager.remind_later(24)
            self.assertTrue(deferred["deferred"])
            self.assertFalse(deferred["should_prompt"])

    def test_remote_engine_is_informational_only(self):
        with workspace() as folder:
            manager, _ = self.make_manager(folder, mode="remote")
            result = manager.inspect()
            self.assertFalse(result["supported"])
            self.assertFalse(result["update_available"])
            self.assertIn("管理者", result["unavailable_reason"])

    def test_manifest_rejects_parent_directory_targets(self):
        with workspace() as folder:
            path = folder / "model_manifest.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "version": "bad",
                "files": [{
                    "repo_id": "test/models", "revision": "abc", "source": "model.bin",
                    "target": "../outside.bin", "size": 1,
                }],
            }), encoding="utf-8")
            with self.assertRaises(ModelUpdateError):
                load_model_manifest(path)


if __name__ == "__main__":
    unittest.main()
