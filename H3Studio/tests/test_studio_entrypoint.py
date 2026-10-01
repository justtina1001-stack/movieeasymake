import unittest
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
            patch.object(studio, "create_app") as create,
            patch.object(studio.webbrowser, "open") as browser,
        ):
            studio.main()
        select.assert_called_once_with(8787, auto_port=True,
                                       required_editor_capabilities=("position_keyframes", "speed_curves", "overlay_tracks", "text_style", "generated_video_thumbnails"))
        create.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:8789/editor")

    def test_no_browser_also_applies_to_existing_service(self):
        with (
            patch("sys.argv", ["app.py", "--port", "8789", "--no-browser"]),
            patch.object(studio, "plan_startup", return_value=SimpleNamespace(port=8789, existing=True, sock=None)) as select,
            patch.object(studio.webbrowser, "open") as browser,
        ):
            studio.main()
        select.assert_called_once_with(8789, auto_port=False,
                                       required_editor_capabilities=("position_keyframes", "speed_curves", "overlay_tracks", "text_style", "generated_video_thumbnails"))
        browser.assert_not_called()

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
