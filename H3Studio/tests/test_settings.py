import json
import tempfile
import unittest
from pathlib import Path

from settings import ConnectionSettings, SettingsError, SettingsStore


class ConnectionSettingsTests(unittest.TestCase):
    def test_relative_comfy_path_is_resolved_from_app_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder) / "H3Studio"
            app_dir.mkdir()
            settings = ConnectionSettings(comfy_dir="../ComfyUI").normalized(app_dir)
            self.assertEqual(Path(settings.comfy_dir), (app_dir.parent / "ComfyUI").resolve())

    def test_remote_url_is_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            settings = ConnectionSettings(mode="remote", base_url="https://h3.example.test:8188/").normalized(app_dir)
            self.assertEqual(settings.base_url, "https://h3.example.test:8188")
            with self.assertRaises(SettingsError):
                ConnectionSettings(mode="remote", base_url="ftp://example.test").normalized(app_dir)

    def test_settings_are_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            path = app_dir / "config.json"
            store = SettingsStore(path, app_dir)
            store.update({"mode": "remote", "base_url": "http://10.0.0.8:8188"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "remote")
            self.assertEqual(payload["base_url"], "http://10.0.0.8:8188")

    def test_invalid_existing_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            path = app_dir / "config.json"
            path.write_text("not-json", encoding="utf-8")
            store = SettingsStore(path, app_dir)
            self.assertEqual(store.current.mode, "local")
            self.assertIsNotNone(store.load_error)


if __name__ == "__main__":
    unittest.main()
