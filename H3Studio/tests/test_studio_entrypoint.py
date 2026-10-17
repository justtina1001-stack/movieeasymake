import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

import app as studio


class StudioEntrypointTests(unittest.TestCase):
    def test_reuses_healthy_alternative_without_creating_engines(self):
        plan = SimpleNamespace(port=8789, existing=True, sock=None)
        with (
            patch("sys.argv", ["app.py", "--auto-port", "--open-editor"]),
            patch.object(studio, "plan_startup", return_value=plan) as select,
            patch.object(studio, "_read_local_http", return_value=json.dumps(studio.STUDIO_RUNTIME_VERSION).encode()) as version,
            patch.object(studio, "create_app") as create,
            patch.object(studio.webbrowser, "open") as browser,
        ):
            studio.main()
        select.assert_called_once_with(8787, auto_port=True,
                                       required_editor_capabilities=("position_keyframes", "speed_curves", "overlay_tracks", "text_style", "generated_video_thumbnails", "clip_animations", "clip_transitions", "text_fonts", "text_idle_animations", "transition_effects", "reference_transitions", "color_adjustments"))
        create.assert_not_called()
        version.assert_called_once_with(8789, "/api/studio/version", 1.5,
                                        max_bytes=4097, accept="application/json")
        browser.assert_called_once_with("http://127.0.0.1:8789/editor")

    def test_no_browser_also_applies_to_existing_service(self):
        with (
            patch("sys.argv", ["app.py", "--port", "8789", "--no-browser"]),
            patch.object(studio, "plan_startup", return_value=SimpleNamespace(port=8789, existing=True, sock=None)) as select,
            patch.object(studio, "_read_local_http", return_value=json.dumps(studio.STUDIO_RUNTIME_VERSION).encode()),
            patch.object(studio.webbrowser, "open") as browser,
        ):
            studio.main()
        select.assert_called_once_with(8789, auto_port=False,
                                       required_editor_capabilities=("position_keyframes", "speed_curves", "overlay_tracks", "text_style", "generated_video_thumbnails", "clip_animations", "clip_transitions", "text_fonts", "text_idle_animations", "transition_effects", "reference_transitions", "color_adjustments"))
        browser.assert_not_called()

    def test_old_or_unreadable_version_never_opens_browser_or_creates_second_studio(self):
        version = studio.STUDIO_RUNTIME_VERSION
        invalid = (None, b"not json", b"[]", b"\xff", b" " * 4097,
                   json.dumps({**version, "source_fingerprint": "old"}).encode(),
                   json.dumps({**version, "workspace_fingerprint": "other"}).encode(),
                   json.dumps({**version, "studio": "other-app"}).encode())
        for body in invalid:
            with (
                self.subTest(body=body),
                patch("sys.argv", ["app.py", "--auto-port"]),
                patch.object(studio, "plan_startup", return_value=SimpleNamespace(port=8787, existing=True, sock=None)),
                patch.object(studio, "_read_local_http", return_value=body),
                patch.object(studio, "create_app") as create,
                patch.object(studio.webbrowser, "open") as browser,
                self.assertRaises(SystemExit) as error,
            ):
                studio.main()
            self.assertEqual(error.exception.code, 1)
            create.assert_not_called()
            browser.assert_not_called()

    def test_version_diagnostic_requires_saving_all_workspaces_before_restart(self):
        with patch.object(studio, "_read_local_http", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "儲存各工作區.*等待.*工作完成.*確認服務已停止"):
                studio.verify_running_studio(8787)

    def test_occupied_unresponsive_port_is_failure_without_startup_or_browser(self):
        with (
            patch("sys.argv", ["app.py", "--port", "8787"]),
            patch.object(studio, "plan_startup", side_effect=RuntimeError("Port is occupied and not responding")),
            patch.object(studio, "create_app") as create,
            patch.object(studio.webbrowser, "open") as browser,
            self.assertRaises(SystemExit) as error,
        ):
            studio.main()
        self.assertEqual(error.exception.code, 1)
        create.assert_not_called()
        browser.assert_not_called()

    def test_reserved_socket_is_passed_to_aiohttp_and_closed(self):
        sock = Mock()
        lock = Mock()
        application = web.Application()
        with (
            patch("sys.argv", ["app.py", "--auto-port", "--no-browser"]),
            patch.object(studio, "plan_startup", return_value=SimpleNamespace(port=8790, existing=False, sock=sock, lock=lock)),
            patch.object(studio, "create_app", return_value=application),
            patch.object(studio.web, "run_app") as run,
        ):
            studio.main()
        self.assertIs(run.call_args.args[0], application)
        self.assertIs(run.call_args.kwargs["sock"], sock)
        self.assertNotIn("port", run.call_args.kwargs)
        self.assertNotIn("host", run.call_args.kwargs)
        sock.close.assert_called_once()
        lock.close.assert_called_once()

    def test_creation_failure_releases_reserved_socket(self):
        sock = Mock()
        lock = Mock()
        with (
            patch("sys.argv", ["app.py", "--no-browser"]),
            patch.object(studio, "plan_startup", return_value=SimpleNamespace(port=8787, existing=False, sock=sock, lock=lock)),
            patch.object(studio, "create_app", side_effect=RuntimeError("bad settings")),
            self.assertRaisesRegex(RuntimeError, "bad settings"),
        ):
            studio.main()
        sock.close.assert_called_once()
        lock.close.assert_called_once()


class StudioFingerprintTests(unittest.TestCase):
    def test_source_fingerprint_tracks_code_but_ignores_user_data_and_runtime_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("original code", encoding="utf-8")
            (root / "static").mkdir()
            script = root / "static" / "scripts.js"
            script.write_text("original UI", encoding="utf-8")
            original = studio.studio_source_fingerprint(root)
            for name in ("config.json", "data/jobs.json", "tests/test_fake.py",
                         ".venv/runtime.py", "models/weights.py", "static/photo.png"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("mutable file", encoding="utf-8")
            self.assertEqual(studio.studio_source_fingerprint(root), original)
            script.write_text("new UI", encoding="utf-8")
            updated = studio.studio_source_fingerprint(root)
            self.assertNotEqual(updated, original)
            (root / "app.py").write_text("new backend", encoding="utf-8")
            self.assertNotEqual(studio.studio_source_fingerprint(root), updated)


class BrowserReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_waits_until_http_is_ready(self):
        with (
            patch.object(studio, "probe_studio", side_effect=[False, True]) as probe,
            patch.object(studio.asyncio, "sleep", new=AsyncMock()),
            patch.object(studio.webbrowser, "open") as browser,
        ):
            await studio.open_browser(8790, "/editor")
        self.assertEqual(probe.call_count, 2)
        browser.assert_called_once_with("http://127.0.0.1:8790/editor")

    async def test_timeout_does_not_open_a_broken_page(self):
        with (
            patch.object(studio, "probe_studio", return_value=False),
            patch.object(studio.asyncio, "sleep", new=AsyncMock()),
            patch.object(studio.webbrowser, "open") as browser,
        ):
            await studio.open_browser(8790)
        browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
