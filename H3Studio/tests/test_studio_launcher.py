"""Isolated fake apps and temporary loopback ports; no Studio/GPU interaction."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import tempfile
import threading
import time
import unittest

from studio_launcher import LauncherPolicy, StudioLauncher, is_control_exit
from studio_runtime_identity import current_studio_identity


FAKE_APP = r'''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, sys, threading, time
from pathlib import Path
from studio_runtime_identity import current_studio_identity
root = Path(__file__).resolve().parent
config = json.loads((root / "fixture.json").read_text())
count_path = root / "launches.json"
launches = json.loads(count_path.read_text()) if count_path.exists() else []
launches.append({"pid": os.getpid(), "recovery": os.environ.get("H3_STUDIO_SUPERVISED_RECOVERY") == "1"})
count_path.write_text(json.dumps(launches))
number = len(launches)
sys.stdout.buffer.write(bytes.fromhex(config.get("stdout_hex", "")))
sys.stdout.buffer.flush()
sys.stderr.buffer.write(bytes.fromhex(config.get("stderr_hex", "")))
sys.stderr.buffer.flush()
server = None
if config.get("ready"):
    identity = {**current_studio_identity(root), "pid": os.getpid()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            if self.path != "/api/studio/version":
                self.send_error(404); return
            body = json.dumps(identity).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    (root / ("ready-" + str(number))).write_text(str(os.getpid()))
lifetimes = config.get("lifetimes", [config.get("lifetime", 0)])
time.sleep(lifetimes[min(number - 1, len(lifetimes) - 1)])
if server:
    server.shutdown()
    server.server_close()
codes = config.get("codes", [0])
sys.exit(codes[min(number - 1, len(codes) - 1)])
'''


class BrokenConsole:
    def write(self, _value):
        raise BrokenPipeError("fixture closed console")

    def flush(self):
        raise OSError("fixture closed console")


class StudioLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="studio supervisor isolated ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "app.py").write_text(FAKE_APP, encoding="utf-8")
        source = Path(__file__).resolve().parents[1]
        shutil.copyfile(source / "studio_runtime_identity.py", self.root / "studio_runtime_identity.py")
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            self.port = free.getsockname()[1]
        self.policy = LauncherPolicy(poll_interval=.015, probe_timeout=.06, handover_timeout=.12,
                                     stable_seconds=.075, stop_grace_seconds=1, drain_timeout=1)
        self.output, self.errors = io.StringIO(), io.StringIO()

    def config(self, **values):
        (self.root / "fixture.json").write_text(json.dumps({"port": self.port, **values}), encoding="utf-8")

    def launcher(self, **values):
        return StudioLauncher(self.root, ["--port", str(self.port), "--no-browser"], policy=self.policy,
                              console_out=self.output, console_err=self.errors, **values)

    def receipt(self, launcher):
        return json.loads((launcher.log_dir / "launcher.json").read_text(encoding="utf-8"))

    def launches(self):
        return json.loads((self.root / "launches.json").read_text())

    def background(self, launcher):
        result = []
        def run():
            try:
                result.append(launcher.run())
            except BaseException as error:
                result.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        def stop():
            launcher.stop_event.set()
            worker.join(4)
        self.addCleanup(stop)
        return worker, result

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.015)
        self.fail("isolated fixture did not reach expected state")

    def handover_server(self, *, delay=0, identity=None, pid=987654321):
        # Serve a distinct, fully isolated API process identity. The production
        # matcher deliberately does not compare this PID with Popen's venv PID.
        payload = {**(identity or current_studio_identity(self.root)), "pid": pid}
        body = json.dumps(payload).encode()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        def serve():
            time.sleep(delay)
            server.serve_forever(poll_interval=.01)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return payload

    def test_normal_exit_preserves_raw_streams_even_when_console_is_closed(self):
        stdout = b"out\x00\xff" + "測試輸出".encode() + b"\n" + b"x" * 100000
        stderr = b"err\xfe\x00\r\n" + "例外原文".encode() + b"y" * 100000
        self.config(stdout_hex=stdout.hex(), stderr_hex=stderr.hex(), codes=[0])
        launcher = self.launcher()
        launcher.console_out = launcher.console_err = BrokenConsole()
        self.assertEqual(launcher.run(), 0)
        for channel, expected in (("stdout", stdout), ("stderr", stderr)):
            self.assertEqual((launcher.log_dir / f"{channel}.log").read_bytes(), expected)
            self.assertEqual((launcher.log_dir / f"attempt-1.{channel}.log").read_bytes(), expected)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["wrapper_exit_code"], 0)
        self.assertEqual(receipt["restart_count"], 0)
        self.assertEqual(receipt["attempts"][0]["original_exit_code"], 0)
        self.assertEqual(len(self.launches()), 1)

    def test_immediate_startup_error_keeps_original_code_without_retry(self):
        self.config(stderr_hex=b"fixture startup error\n".hex(), codes=[23])
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 23)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["wrapper_exit_code"], 23)
        self.assertEqual(receipt["attempts"][0]["original_exit_code"], 23)
        self.assertEqual(receipt["restart_count"], 0)
        self.assertFalse(receipt["attempts"][0]["stable_ready"])
        self.assertEqual(len(self.launches()), 1)
        self.assertIn("cause is unknown", self.output.getvalue())

    def test_preexisting_stop_does_not_launch_any_child(self):
        self.config(codes=[0])
        stop = threading.Event()
        stop.set()
        launcher = self.launcher(stop_event=stop)
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["attempts"], [])
        self.assertEqual(receipt["restart_count"], 0)
        self.assertTrue(receipt["intentional_stop"])
        self.assertFalse((self.root / "launches.json").exists())

    def test_stop_between_attempts_prevents_recovery_popen(self):
        self.config(ready=True, lifetime=.3, codes=[23])
        stop = threading.Event()
        class StopOnRecovery(io.StringIO):
            def write(self, value):
                if "Restarting Studio only" in value:
                    stop.set()
                return super().write(value)
        launcher = self.launcher(stop_event=stop)
        launcher.console_out = StopOnRecovery()
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        self.assertEqual(len(receipt["attempts"]), 1)
        self.assertEqual(receipt["restart_count"], 0)
        self.assertEqual(len(self.launches()), 1)
        self.assertTrue(receipt["intentional_stop"])

    def test_stop_just_before_start_attempt_does_not_popen(self):
        self.config(codes=[0])
        launcher = self.launcher()
        save = launcher._save
        def save_then_stop():
            save()
            if launcher.receipt["attempts"]:
                launcher.stop_event.set()
        launcher._save = save_then_stop
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["attempts"][0]["decision"], "intentional_stop")
        self.assertIsNone(receipt["attempts"][0]["child_pid"])
        self.assertFalse((self.root / "launches.json").exists())

    def test_process_launch_failure_records_os_error_without_arguments_or_environment(self):
        self.config(codes=[0])
        launcher = self.launcher(python_executable=self.root / "missing-python.exe")
        self.assertEqual(launcher.run(), 1)
        receipt = self.receipt(launcher)
        attempt = receipt["attempts"][0]
        self.assertEqual(attempt["decision"], "launch_failed")
        self.assertEqual(attempt["launch_error_type"], "FileNotFoundError")
        self.assertIsInstance(attempt["launch_error_errno"], int)
        self.assertTrue(attempt["launch_error_message"])
        self.assertIsNone(attempt["original_exit_code"])
        self.assertEqual(receipt["restart_count"], 0)
        self.assertFalse((self.root / "launches.json").exists())
        self.assertNotIn("environment", receipt)
        self.assertNotIn("app_args", receipt)

    def test_delayed_matching_handover_records_original_error_and_server_pid(self):
        self.config(codes=[27], lifetime=.02)
        self.handover_server()
        from studio_startup import find_matching_running_studio
        exited_at = []
        def delayed_probe(*args, **kwargs):
            if launcher.child.poll() is None:
                return None
            if not exited_at:
                exited_at.append(time.monotonic())
            if time.monotonic() - exited_at[0] < .05:
                return None
            return find_matching_running_studio(*args, **kwargs)
        launcher = self.launcher(probe=delayed_probe)
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        attempt = receipt["attempts"][0]
        self.assertEqual(attempt["original_exit_code"], 27)
        self.assertEqual(attempt["decision"], "matching_service_ready")
        self.assertEqual(attempt["successor"]["ready_server_pid"], 987654321)
        self.assertNotEqual(attempt["child_pid"], 987654321)
        self.assertEqual(receipt["restart_count"], 0)
        self.assertIn("still ready", self.output.getvalue())
        self.assertGreaterEqual(time.monotonic() - exited_at[0], .05)

    def test_old_version_is_not_accepted_as_a_handover(self):
        self.config(codes=[29])
        identity = current_studio_identity(self.root)
        identity["source_fingerprint"] = "old-disk-version"
        self.handover_server(identity=identity)
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 29)
        self.assertEqual(self.receipt(launcher)["restart_count"], 0)

    def test_surviving_api_keeps_raw_output_after_wrapper_handover_returns(self):
        (self.root / "app.py").write_text(r'''
import subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parent
survivor=subprocess.Popen([sys.executable,"-u",str(root/"survivor.py"),sys.argv[sys.argv.index("--port")+1]],
                          stdout=sys.stdout,stderr=sys.stderr)
(root/"survivor-launcher-pid").write_text(str(survivor.pid))
deadline=time.monotonic()+3
while not (root/"survivor-ready").exists() and time.monotonic()<deadline:
    time.sleep(.01)
sys.exit(27)
''', encoding="utf-8")
        (self.root / "survivor.py").write_text(r'''
from http.server import BaseHTTPRequestHandler,HTTPServer
import json,os,sys
from pathlib import Path
from studio_runtime_identity import current_studio_identity
root=Path(__file__).resolve().parent
identity={**current_studio_identity(root),"pid":os.getpid()}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        body=json.dumps(identity).encode()
        self.send_response(200)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
server=HTTPServer(("127.0.0.1",int(sys.argv[1])),Handler)
server.timeout=.025
(root/"survivor-ready").write_text(str(os.getpid()))
wrote=False
try:
    while not (root/"survivor-stop").exists():
        server.handle_request()
        if not wrote and (root/"write-late").exists():
            sys.stderr.buffer.write(b"late stderr\xff\x00\n")
            sys.stderr.buffer.flush()
            sys.stdout.buffer.write(b"late stdout\xfe\n")
            sys.stdout.buffer.flush()
            (root/"survivor-wrote").touch()
            wrote=True
finally:
    server.server_close()
    (root/"survivor-stopped").touch()
''', encoding="utf-8")
        def stop_survivor():
            (self.root / "survivor-stop").touch()
            if (self.root / "survivor-ready").exists():
                self.wait_for(lambda: (self.root / "survivor-stopped").exists())
                if os.name == "nt":
                    # The final fixture marker precedes Python/venv shutdown.
                    # Wait for both owned fake processes to release raw files.
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                    kernel.OpenProcess.restype = wintypes.HANDLE
                    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
                    for marker in ("survivor-ready", "survivor-launcher-pid"):
                        handle = kernel.OpenProcess(0x00100000, False, int((self.root / marker).read_text()))
                        if handle:
                            try:
                                self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0)
                            finally:
                                kernel.CloseHandle(handle)
        self.addCleanup(stop_survivor)
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["status"], "handed_over")
        self.assertEqual(receipt["attempts"][0]["original_exit_code"], 27)
        self.assertEqual(receipt["attempts"][0]["log_capture"], "direct_regular_file")
        from studio_startup import find_matching_running_studio
        match = find_matching_running_studio(self.root, preferred_port=self.port, timeout=.2)
        self.assertIsNotNone(match)
        self.assertNotEqual(receipt["attempts"][0]["child_pid"], match["pid"])
        # The supervisor's readers have stopped before this surviving process
        # writes. Regular stdout/stderr handles must still remain usable.
        (self.root / "write-late").touch()
        self.wait_for(lambda: (self.root / "survivor-wrote").exists())
        self.assertEqual((launcher.log_dir / "attempt-1.stderr.log").read_bytes(), b"late stderr\xff\x00\n")
        self.assertEqual((launcher.log_dir / "attempt-1.stdout.log").read_bytes(), b"late stdout\xfe\n")
        self.assertIsNotNone(find_matching_running_studio(self.root, preferred_port=self.port, timeout=.2))

    def test_stable_ready_external_exit_recovers_once_without_normal_start_environment(self):
        self.config(ready=True, lifetimes=[20, .3], codes=[31, 0])
        launcher = self.launcher()
        worker, result = self.background(launcher)
        self.wait_for(lambda: bool(launcher.receipt["attempts"] and launcher.receipt["attempts"][0]["stable_ready"]))
        fake_api_pid = launcher.receipt["attempts"][0]["ready_server_pid"]
        os.kill(fake_api_pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [0])
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["restart_count"], 1)
        self.assertNotEqual(receipt["attempts"][0]["original_exit_code"], 0)
        self.assertEqual(receipt["attempts"][1]["original_exit_code"], 0)
        self.assertTrue(receipt["attempts"][0]["stable_ready"])
        self.assertGreaterEqual(receipt["attempts"][0]["ready_stable_seconds"], .075)
        self.assertEqual([launch["recovery"] for launch in self.launches()], [False, True])
        self.assertTrue(all(a["ready_server_pid"] > 0 for a in receipt["attempts"]))

    def test_repeated_ready_exits_stop_after_two_recoveries(self):
        self.config(ready=True, lifetime=.3, codes=[41, 42, 43, 0])
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 43)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["restart_count"], 2)
        self.assertEqual([a["original_exit_code"] for a in receipt["attempts"]], [41, 42, 43])
        self.assertEqual(receipt["attempts"][-1]["decision"], "recovery_limit_reached")
        self.assertEqual(len(self.launches()), 3)

    def test_ready_for_too_short_a_time_does_not_recover(self):
        self.config(ready=True, lifetime=.04, codes=[51])
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 51)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(self.receipt(launcher)["restart_count"], 0)

    def test_intentional_stop_forwards_to_owned_group_without_recovery(self):
        self.config(ready=True, lifetime=20, codes=[61])
        launcher = self.launcher()
        worker, result = self.background(launcher)
        self.wait_for(lambda: bool(launcher.receipt["attempts"] and launcher.receipt["attempts"][0]["stable_ready"]))
        launcher.stop_event.set()
        worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [0])
        receipt = self.receipt(launcher)
        self.assertTrue(receipt["intentional_stop"])
        self.assertEqual(receipt["attempts"][0]["decision"], "intentional_stop")
        self.assertEqual(receipt["restart_count"], 0)
        self.assertEqual(len(self.launches()), 1)

    def test_ctrl_c_handler_marks_user_stop_and_does_not_retry(self):
        self.config(ready=True, lifetime=20, codes=[61])
        launcher = self.launcher()
        sent = threading.Event()
        def interrupt():
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if launcher.receipt["attempts"] and launcher.receipt["attempts"][0]["stable_ready"]:
                    signal.raise_signal(signal.SIGINT)
                    sent.set()
                    return
                time.sleep(.015)
            launcher.stop_event.set()
        controller = threading.Thread(target=interrupt, daemon=True)
        controller.start()
        try:
            self.assertEqual(launcher.run(), 0)
        finally:
            controller.join(2)
        self.assertTrue(sent.is_set())
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["stop_signal"], "SIGINT")
        self.assertTrue(receipt["intentional_stop"])
        self.assertEqual(receipt["restart_count"], 0)
        self.assertEqual(len(self.launches()), 1)

    def test_control_exit_codes_are_not_treated_as_crashes(self):
        for code in (130, -2, 0xC000013A, -1073741510):
            with self.subTest(code=code):
                self.assertTrue(is_control_exit(code))
        self.config(codes=[130])
        launcher = self.launcher()
        self.assertEqual(launcher.run(), 0)
        receipt = self.receipt(launcher)
        self.assertEqual(receipt["attempts"][0]["original_exit_code"], 130)
        self.assertEqual(receipt["attempts"][0]["decision"], "control_exit")
        self.assertEqual(len(self.launches()), 1)

    def test_idle_healthy_child_is_not_restarted_and_probe_outage_does_not_kill_it(self):
        self.config(ready=True, lifetime=20)
        from studio_startup import find_matching_running_studio
        outage = threading.Event()
        def probe(*args, **kwargs):
            return None if outage.is_set() else find_matching_running_studio(*args, **kwargs)
        launcher = self.launcher(probe=probe)
        worker, result = self.background(launcher)
        self.wait_for(lambda: bool(launcher.receipt["attempts"] and launcher.receipt["attempts"][0]["stable_ready"]))
        pid = launcher.child.pid
        time.sleep(.15)
        outage.set()
        time.sleep(.18)
        self.assertIsNone(launcher.child.poll())
        self.assertEqual(launcher.child.pid, pid)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(launcher.receipt["restart_count"], 0)
        launcher.stop_event.set()
        worker.join(4)
        self.assertEqual(result, [0])


if __name__ == "__main__":
    unittest.main()
