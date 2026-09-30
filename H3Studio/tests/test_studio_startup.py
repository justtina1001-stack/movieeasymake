"""Real loopback startup checks; never touch the user's Studio ports or data."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import studio_startup as startup


class StudioStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="studio-preflight-test-")
        self.addCleanup(temporary.cleanup)
        self.lock_file = Path(temporary.name) / "startup.lock"
        for name, value in (("PROBE_TIMEOUT", .15), ("CONNECT_TIMEOUT", .04),
                            ("LOCK_WAIT_TIMEOUT", .3), ("LOCK_RETRY_INTERVAL", .015)):
            override = patch.object(startup, name, value)
            override.start()
            self.addCleanup(override.stop)

    def listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(socket.SOMAXCONN)
        self.addCleanup(sock.close)
        return sock

    def free_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def server(self, body=startup.STUDIO_TITLE, status=200, *, redirect=None, gate=None, dribble=False,
               capabilities=None, capability_status=200, capability_redirect=None, capability_gate=None):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                requests.append(self.path)
                response_body, response_status, response_redirect, response_gate = body, status, redirect, gate
                if self.path == "/api/editor/capabilities":
                    response_body = body if capabilities is None else capabilities
                    response_status, response_redirect, response_gate = capability_status, capability_redirect, capability_gate
                try:
                    if response_gate is not None:
                        response_gate.wait(.8)
                    if dribble:
                        self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        for _ in range(50):
                            self.connection.sendall(b"x")
                            time.sleep(.02)
                        return
                    self.send_response(response_status)
                    self.send_header("Content-Length", str(len(response_body)))
                    if response_redirect is not None:
                        self.send_header("Location", response_redirect)
                    self.end_headers()
                    self.wfile.write(response_body)
                except OSError:
                    pass  # The bounded probe is expected to close stalled peers.

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        worker.start()

        def stop():
            if gate is not None:
                gate.set()
            if capability_gate is not None:
                capability_gate.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)

        self.addCleanup(stop)
        return server.server_port, requests

    def plan(self, port, auto=False, **kwargs):
        result = startup.plan_startup(port, auto, lock_file=self.lock_file, **kwargs)
        if result.sock is not None:
            self.addCleanup(result.sock.close)
        if result.lock is not None:
            self.addCleanup(result.lock.close)
        return result

    def test_probe_only_root_page_and_bypasses_environment_proxies(self):
        port, requests = self.server()
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                                     "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertTrue(startup.probe_studio(port, timeout=.3))
        self.assertEqual(requests, ["/"])

    def test_unrelated_http_error_and_redirect_are_never_considered_studio(self):
        healthy, target_requests = self.server()
        for body, status, redirect in ((b"Other app", 200, None), (startup.STUDIO_TITLE, 503, None),
                                       (startup.STUDIO_TITLE, 302, f"http://127.0.0.1:{healthy}/")):
            with self.subTest(status=status):
                port, requests = self.server(body, status, redirect=redirect)
                self.assertFalse(startup.probe_studio(port, timeout=.3))
                self.assertEqual(requests, ["/"])
        self.assertEqual(target_requests, [])

    def test_title_must_be_exact_and_in_first_8192_body_bytes(self):
        for body, expected in ((b"<title>minimax H3 Studio</title>", False),
                               (b"x" * 8192 + startup.STUDIO_TITLE, False),
                               (b"x" * (8192 - len(startup.STUDIO_TITLE)) + startup.STUDIO_TITLE, True)):
            port, _ = self.server(body)
            self.assertEqual(startup.probe_studio(port, timeout=.3), expected)

    def test_stalled_listener_and_slow_dripping_headers_have_total_deadlines(self):
        stalled = self.listener().getsockname()[1]
        dribbling, _ = self.server(dribble=True)
        for port in (stalled, dribbling):
            with self.subTest(port=port):
                started = time.monotonic()
                self.assertFalse(startup.probe_studio(port, timeout=.12))
                self.assertLess(time.monotonic() - started, .5)

    def test_healthy_alternative_is_reused_even_when_preferred_port_is_free(self):
        preferred, (alternative, requests) = self.free_port(), self.server()
        with patch.object(startup, "AUTO_PORTS", (alternative,)):
            result = self.plan(preferred, auto=True)
        self.assertEqual((result.port, result.existing, result.sock, result.lock), (alternative, True, None, None))
        self.assertEqual(requests, ["/"])
        with socket.socket() as check:
            check.bind(("127.0.0.1", preferred))

    def test_required_editor_capability_reuses_supported_instance_without_lock_or_bind(self):
        port, requests = self.server(capabilities=b'{"position_keyframes":true}')
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}), \
                patch.object(startup, "_try_lock", side_effect=AssertionError("reuse must not lock")), \
                patch.object(startup, "_reserve_port", side_effect=AssertionError("reuse must not bind")):
            result = self.plan(port, required_editor_capabilities=("position_keyframes",))
        self.assertEqual((result.port, result.existing, result.sock, result.lock), (port, True, None, None))
        self.assertEqual(requests, ["/", "/api/editor/capabilities"])

    def test_both_position_and_speed_curve_capabilities_reuse_the_same_ready_studio(self):
        port, requests = self.server(capabilities=b'{"position_keyframes":true,"speed_curves":true}')
        with patch.object(startup, "_try_lock", side_effect=AssertionError("supported reuse must not lock")), \
                patch.object(startup, "_reserve_port", side_effect=AssertionError("supported reuse must not bind")):
            result = self.plan(port, required_editor_capabilities=("position_keyframes", "speed_curves"))
        self.assertEqual((result.port, result.existing, result.sock, result.lock), (port, True, None, None))
        self.assertEqual(requests, ["/", "/api/editor/capabilities"])

    def test_text_style_capability_is_required_before_reusing_a_loaded_editor(self):
        required = ("position_keyframes", "speed_curves", "overlay_tracks", "text_style")
        for flag in (None, False, 1, "true"):
            with self.subTest(text_style=flag):
                flags = {name: True for name in required[:-1]}
                if flag is not None:
                    flags["text_style"] = flag
                port, requests = self.server(capabilities=json.dumps(flags).encode())
                with patch.object(startup, "_try_lock", side_effect=AssertionError("stale service must not lock")), \
                        patch.object(startup, "_reserve_port", side_effect=AssertionError("must not create a second Studio")):
                    with self.assertRaisesRegex(RuntimeError, "文字描邊與漸層"):
                        self.plan(port, required_editor_capabilities=required)
                self.assertEqual(requests, ["/", "/api/editor/capabilities"])

    def test_text_style_editor_reuses_the_same_ready_instance(self):
        required = ("position_keyframes", "speed_curves", "overlay_tracks", "text_style")
        port, requests = self.server(capabilities=json.dumps({name: True for name in required}).encode())
        with patch.object(startup, "_try_lock", side_effect=AssertionError("reuse must not lock")), \
                patch.object(startup, "_reserve_port", side_effect=AssertionError("reuse must not bind")):
            result = self.plan(port, required_editor_capabilities=required)
        self.assertEqual((result.port, result.existing, result.sock, result.lock), (port, True, None, None))
        self.assertEqual(requests, ["/", "/api/editor/capabilities"])

    def test_loaded_position_animation_cannot_hide_missing_or_false_speed_curve_capability(self):
        # A restarted process from the previous feature release is healthy and
        # supports position animation, but still cannot save/render speed ramps.
        for capability in (None, False, 1, "true"):
            with self.subTest(speed_curves=capability):
                flags = {"position_keyframes": True}
                if capability is not None:
                    flags["speed_curves"] = capability
                old, requests = self.server(capabilities=json.dumps(flags).encode())
                alternative = self.free_port()
                with patch.object(startup, "AUTO_PORTS", (alternative,)), \
                        patch.object(startup, "_try_lock", side_effect=AssertionError("stale service must fail before lock")), \
                        patch.object(startup, "_reserve_port", side_effect=AssertionError("must not launch a second gateway")):
                    with self.assertRaisesRegex(RuntimeError, f"{old}.*未載入.*曲線變速") as error:
                        self.plan(old, auto=True, required_editor_capabilities=("position_keyframes", "speed_curves"))
                self.assertIn("儲存專案", str(error.exception))
                self.assertIn("確認服務已停止後重新啟動", str(error.exception))
                self.assertIn("再次執行啟動檔只會開啟原有服務", str(error.exception))
                self.assertEqual(requests, ["/", "/api/editor/capabilities"])
                self.assertTrue(startup._has_listener(old))

    def test_missing_or_invalid_editor_flags_never_reuse_or_launch_another_gateway(self):
        invalid = ({}, {"position_keyframes": False}, {"position_keyframes": 1},
                   {"position_keyframes": "true"}, {"position_keyframes": None}, [], None)
        bodies = [json.dumps(value).encode() for value in invalid]
        bodies += [b"not json", b'{"position_keyframes":true}' + b" " * 8192]
        for body in bodies:
            with self.subTest(body=body[:80]):
                old, requests = self.server(capabilities=body)
                alternative = self.free_port()
                with patch.object(startup, "AUTO_PORTS", (alternative,)), \
                        patch.object(startup, "_try_lock", side_effect=AssertionError("stale reuse must fail before lock")), \
                        patch.object(startup, "_reserve_port", side_effect=AssertionError("must not start a second gateway")):
                    with self.assertRaisesRegex(RuntimeError, f"{old}.*未載入.*位置動畫"):
                        self.plan(old, auto=True, required_editor_capabilities=("position_keyframes",))
                self.assertEqual(requests, ["/", "/api/editor/capabilities"])
                self.assertTrue(startup._has_listener(old))

    def test_capability_error_and_redirect_are_not_followed(self):
        target, target_requests = self.server(capabilities=b'{"position_keyframes":true}')
        for status in (404, 302):
            with self.subTest(status=status):
                port, requests = self.server(capabilities=b'{"position_keyframes":true}', capability_status=status,
                                             capability_redirect=f"http://127.0.0.1:{target}/api/editor/capabilities")
                with self.assertRaisesRegex(RuntimeError, "未載入"):
                    self.plan(port, required_editor_capabilities=("position_keyframes",))
                self.assertEqual(requests, ["/", "/api/editor/capabilities"])
        self.assertEqual(target_requests, [])

    def test_unresponsive_capability_endpoint_has_a_total_deadline(self):
        port, requests = self.server(capabilities=b'{"position_keyframes":true}', capability_gate=threading.Event())
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "未載入"):
            self.plan(port, required_editor_capabilities=("position_keyframes",))
        self.assertLess(time.monotonic() - started, .6)
        self.assertEqual(requests, ["/", "/api/editor/capabilities"])

    def test_waiting_launcher_checks_capabilities_before_reusing_late_owner(self):
        old, requests = self.server(capabilities=b"{}")
        held = startup._try_lock(self.lock_file)
        self.addCleanup(held.close)
        with patch.object(startup, "_find_existing", side_effect=[None, old]), \
                patch.object(startup, "_reserve_port", side_effect=AssertionError("must not bypass stale owner")):
            with self.assertRaisesRegex(RuntimeError, "未載入"):
                self.plan(old, required_editor_capabilities=("position_keyframes",))
        self.assertEqual(requests, ["/api/editor/capabilities"])
        self.assertIsNone(startup._try_lock(self.lock_file))

    def test_post_lock_stale_reuse_releases_newly_acquired_startup_lock(self):
        old, requests = self.server(capabilities=b"{}")
        with patch.object(startup, "_find_existing", side_effect=[None, old]), \
                patch.object(startup, "_reserve_port", side_effect=AssertionError("must not start another gateway")):
            with self.assertRaisesRegex(RuntimeError, "未載入"):
                self.plan(old, required_editor_capabilities=("position_keyframes",))
        self.assertEqual(requests, ["/api/editor/capabilities"])
        released = startup._try_lock(self.lock_file)
        self.assertIsNotNone(released)
        released.close()

    def test_healthy_priority_and_duplicate_candidate_ports(self):
        preferred, preferred_requests = self.server()
        alternative, alternative_requests = self.server()
        with patch.object(startup, "AUTO_PORTS", (alternative, preferred, alternative)):
            result = self.plan(preferred, auto=True)
        self.assertEqual((result.port, result.existing), (preferred, True))
        self.assertEqual(preferred_requests, ["/"])
        self.assertEqual(alternative_requests, ["/"])

    def test_explicit_occupied_port_does_not_reuse_or_open_another_port(self):
        unrelated, _ = self.server(b"Some other server")
        alternative, requests = self.server()
        with patch.object(startup, "AUTO_PORTS", (alternative,)):
            with self.assertRaisesRegex(RuntimeError, str(unrelated)):
                self.plan(unrelated)
        self.assertEqual(requests, [])
        # Failed startup releases the workspace lock.
        released = startup._try_lock(self.lock_file)
        self.assertIsNotNone(released)
        released.close()

    def test_auto_skips_unrelated_service_and_reserves_first_available_candidate(self):
        occupied, _ = self.server(b"Unrelated")
        available, other = self.free_port(), self.free_port()
        with patch.object(startup, "AUTO_PORTS", (available, other)):
            result = self.plan(occupied, auto=True)
        self.assertFalse(result.existing)
        self.assertEqual(result.port, available)
        self.assertEqual(result.sock.getsockname(), ("127.0.0.1", available))

    def test_all_occupied_ports_fail_without_stopping_their_listeners(self):
        first, _ = self.server(b"Other")
        second_sock = self.listener()
        second = second_sock.getsockname()[1]
        with patch.object(startup, "AUTO_PORTS", (second,)):
            with self.assertRaisesRegex(RuntimeError, f"{first}.*{second}"):
                self.plan(first, auto=True)
        self.assertGreaterEqual(second_sock.fileno(), 0)
        self.assertTrue(startup._has_listener(second))

    def test_candidate_health_checks_run_concurrently_only_for_live_listeners(self):
        sockets = [self.listener() for _ in range(4)]
        ports = [sock.getsockname()[1] for sock in sockets]
        barrier = threading.Barrier(4)
        seen = []

        def probe(port, timeout):
            seen.append(port)
            barrier.wait(timeout=1)
            return False

        with patch.object(startup, "AUTO_PORTS", tuple(ports[1:])), patch.object(startup, "probe_studio", side_effect=probe):
            with self.assertRaisesRegex(RuntimeError, "沒有可重用"):
                self.plan(ports[0], auto=True)
        self.assertEqual(sorted(seen), sorted(ports * 2))  # Initial and post-lock checks.
        with patch.object(startup, "probe_studio", side_effect=AssertionError("free ports need no HTTP probe")):
            result = self.plan(self.free_port())
        self.assertFalse(result.existing)

    def test_reserved_socket_is_listening_nonblocking_and_blocks_competing_binds(self):
        result = self.plan(self.free_port())
        self.assertEqual(result.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
        self.assertFalse(result.sock.getblocking())
        if os.name == "nt":
            self.assertEqual(result.sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
            self.assertEqual(result.sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
        with socket.socket() as competitor:
            competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                competitor.bind(("127.0.0.1", result.port))

    def test_unready_owner_prevents_second_bind_even_on_a_free_alternative(self):
        first = self.plan(self.free_port())
        alternative = self.free_port()
        with patch.object(startup, "AUTO_PORTS", (alternative,)), patch.object(startup, "_reserve_port", side_effect=AssertionError("must not start another gateway")):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "另一個 Studio"):
                self.plan(first.port, auto=True)
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertGreaterEqual(first.sock.fileno(), 0)

    def test_healthy_instance_is_reusable_while_another_owner_holds_the_lock(self):
        first = self.plan(self.free_port())
        healthy, _ = self.server()
        with patch.object(startup, "AUTO_PORTS", (healthy,)), patch.object(startup, "_try_lock", side_effect=AssertionError("healthy reuse needs no lock")):
            result = self.plan(first.port, auto=True)
        self.assertEqual((result.port, result.existing, result.lock), (healthy, True, None))
        self.assertIsNotNone(first.lock.handle)

    def test_waiting_launcher_reuses_owner_when_it_becomes_ready(self):
        held = startup._try_lock(self.lock_file)
        self.addCleanup(held.close)
        gate = threading.Event()
        port, _ = self.server(gate=gate)
        timer = threading.Timer(.23, gate.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with patch.object(startup, "_reserve_port", side_effect=AssertionError("must wait for existing startup")):
            result = self.plan(port)
        self.assertTrue(result.existing)
        self.assertIsNone(result.lock)

    def test_process_exit_lock_release_allows_subsequent_startup(self):
        first = self.plan(self.free_port())
        script = ("import pathlib,sys; from studio_startup import _try_lock; "
                  "lock=_try_lock(pathlib.Path(sys.argv[1])); "
                  "print('busy' if lock is None else 'acquired'); lock.close() if lock else None")

        def child():
            completed = subprocess.run([sys.executable, "-c", script, str(self.lock_file)],
                                       cwd=Path(startup.__file__).parent, text=True, capture_output=True,
                                       timeout=3, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.strip()

        self.assertEqual(child(), "busy")
        first.sock.close()
        first.lock.close()
        first.lock.close()  # Cleanup is idempotent.
        self.assertEqual(child(), "acquired")
        replacement = self.plan(first.port)
        self.assertFalse(replacement.existing)

    def test_racing_launchers_create_at_most_one_reserved_gateway(self):
        preferred, alternative = self.free_port(), self.free_port()
        with patch.object(startup, "AUTO_PORTS", (alternative,)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(startup.plan_startup, preferred, True, lock_file=self.lock_file) for _ in range(2)]
                outcomes = []
                for future in futures:
                    try:
                        result = future.result(timeout=3)
                        self.addCleanup(result.sock.close)
                        self.addCleanup(result.lock.close)
                        outcomes.append(result)
                    except RuntimeError as error:
                        self.assertIn("另一個 Studio", str(error))
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].port, preferred)


if __name__ == "__main__":
    unittest.main()
