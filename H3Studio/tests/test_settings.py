import json
import tempfile
import unittest
from pathlib import Path

from settings import ConnectionSettings, SettingsError, SettingsStore


class ConnectionSettingsTests(unittest.TestCase):
    def test_new_install_defaults_to_client_role(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = ConnectionSettings().normalized(Path(folder))
            self.assertEqual(settings.studio_role, "client")
            self.assertEqual(settings.public_dict()["studio_role"], "client")

    def test_role_cannot_be_changed_through_connection_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            path = app_dir / "config.json"
            store = SettingsStore(path, app_dir)
            store.set_studio_role("host")
            updated = store.update({"studio_role": "client", "mode": "local"})
            self.assertEqual(updated.studio_role, "host")
            with self.assertRaises(SettingsError):
                store.set_studio_role("owner")

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

    def test_remote_token_is_persisted_but_never_returned_publicly(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            path = app_dir / "config.json"
            store = SettingsStore(path, app_dir)
            updated = store.update({
                "mode": "remote",
                "base_url": "http://10.0.0.8:8190",
                "remote_access_token": "h3g_secret",
            })
            self.assertEqual(updated.remote_access_token, "h3g_secret")
            self.assertNotIn("remote_access_token", updated.public_dict())
            self.assertTrue(updated.public_dict()["has_remote_access_token"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["remote_access_token"], "h3g_secret")

            preserved = store.update({"mode": "remote", "base_url": "http://10.0.0.8:8190"})
            self.assertEqual(preserved.remote_access_token, "h3g_secret")

            cleared = store.update({
                "mode": "remote",
                "base_url": "http://10.0.0.8:8190",
                "clear_remote_access_token": True,
            })
            self.assertEqual(cleared.remote_access_token, "")

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
