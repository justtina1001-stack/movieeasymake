"""Bounded Studio supervision and raw diagnostics; never submits GPU work."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def is_control_exit(code):
    return code in {-2, 130} or (isinstance(code, int) and code & 0xFFFFFFFF == 0xC000013A)


def probe_arguments(arguments):
    """Read only routing options; app.py retains all argument validation."""
    port, automatic = 8787, False
    for index, argument in enumerate(arguments):
        if argument == "--auto-port":
            automatic = True
        candidate = (arguments[index + 1] if argument == "--port" and index + 1 < len(arguments)
                     else argument.partition("=")[2] if argument.startswith("--port=") else None)
        if candidate is not None:
            try:
                selected = int(candidate)
                if 1 <= selected <= 65535:
                    port = selected
            except (TypeError, ValueError):
                pass
    return port, automatic


@dataclass(frozen=True)
class LauncherPolicy:
    # Tests inject short intervals. The CLI always uses these production limits.
    poll_interval: float = 2.0
    probe_timeout: float = .75
    handover_timeout: float = 2.0
    stable_seconds: float = 30.0
    max_restarts: int = 2
    stop_grace_seconds: float = 10.0
    drain_timeout: float = 1.0


class StudioLauncher:
    def __init__(self, root, app_args=(), *, policy=None, python_executable=None,
                 probe=None, console_out=None, console_err=None, stop_event=None):
        self.root = Path(root).resolve()
        self.app_args = list(app_args)
        self.policy = policy or LauncherPolicy()
        if not 0 <= self.policy.max_restarts <= 2:
            raise ValueError("At most two automatic Studio restarts are allowed.")
        if any(value <= 0 for value in (self.policy.poll_interval, self.policy.probe_timeout,
                                       self.policy.handover_timeout, self.policy.stable_seconds,
                                       self.policy.stop_grace_seconds, self.policy.drain_timeout)):
            raise ValueError("Supervisor intervals must be positive.")
        self.python = str(python_executable or sys.executable)
        self.preferred_port, self.auto_port = probe_arguments(self.app_args)
        if probe is None:
            from studio_startup import find_matching_running_studio
            probe = find_matching_running_studio
        self.probe = probe
        self.console_out = sys.stdout if console_out is None else console_out
        self.console_err = sys.stderr if console_err is None else console_err
        self.console_lock = threading.Lock()
        self.stop_event = stop_event or threading.Event()
        self.stop_signal = None
        self.child = None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.log_dir = self.root / "data" / "logs" / "studio" / f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.receipt = {"schema_version": 1, "launcher_pid": os.getpid(), "started_at": utc_now(),
                        "finished_at": None, "status": "starting", "wrapper_exit_code": None,
                        "restart_count": 0, "max_restarts": self.policy.max_restarts, "attempts": []}

    def _save(self):
        atomic_json(self.log_dir / "launcher.json", self.receipt)

    def _say(self, message):
        try:
            with self.console_lock:
                self.console_out.write(message + "\n")
                self.console_out.flush()
        except (OSError, ValueError, UnicodeError):
            pass  # A closed console must not stop supervision or raw logging.

    def _tail(self, source, aggregate, console, stopped):
        # The service writes a regular file directly. Its output never depends
        # on this reader staying alive after a venv-wrapper handover.
        with source.open("rb", buffering=0) as reader, aggregate.open("ab", buffering=0) as aggregate_log:
            final_size = None
            while True:
                if stopped.is_set() and final_size is None:
                    final_size = os.fstat(reader.fileno()).st_size
                remaining = None if final_size is None else final_size - reader.tell()
                if remaining is not None and remaining <= 0:
                    break
                chunk = reader.read(65536 if remaining is None else min(65536, remaining))
                if not chunk:
                    stopped.wait(.025)
                    continue
                aggregate_log.write(chunk)
                if console is not None:
                    try:
                        with self.console_lock:
                            binary = getattr(console, "buffer", None)
                            if binary is not None:
                                binary.write(chunk)
                                binary.flush()
                            else:
                                console.write(chunk.decode("utf-8", errors="replace"))
                                console.flush()
                    except (OSError, ValueError, UnicodeError):
                        console = None

    def _matching(self, timeout):
        try:
            match = self.probe(self.root, preferred_port=self.preferred_port,
                               auto_port=self.auto_port, timeout=timeout)
        except (OSError, ValueError):
            return None
        if (not isinstance(match, dict) or type(match.get("pid")) is not int or match["pid"] <= 0
                or type(match.get("port")) is not int or not 1 <= match["port"] <= 65535):
            return None
        return match

    def _ready_fields(self, match):
        identity = match.get("identity") or {}
        return {"ready_server_pid": match["pid"], "ready_port": match["port"],
                "ready_identity": {key: identity[key] for key in
                    ("studio", "source_fingerprint", "workspace_fingerprint", "started_at", "supervised_recovery")
                    if key in identity}}

    def _start_attempt(self, attempt):
        handles = []
        try:
            for channel in ("stdout", "stderr"):
                handles.append((self.log_dir / f"attempt-{attempt['number']}.{channel}.log").open("wb", buffering=0))
                (self.log_dir / f"{channel}.log").touch(exist_ok=True)
            environment = os.environ.copy()
            environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
            if attempt["recovery_start"]:
                environment["H3_STUDIO_SUPERVISED_RECOVERY"] = "1"
            else:
                environment.pop("H3_STUDIO_SUPERVISED_RECOVERY", None)
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            self.child = subprocess.Popen(
                [self.python, "-X", "utf8", "-X", "faulthandler", "-u", str(self.root / "app.py"), *self.app_args],
                cwd=self.root, env=environment, stdout=handles[0], stderr=handles[1], **options)
        finally:
            for handle in handles:
                handle.close()
        attempt["child_pid"] = self.child.pid
        if attempt["recovery_start"]:
            self.receipt["restart_count"] += 1
        self._save()
        threads, stopped = [], threading.Event()
        for channel, console in (("stdout", self.console_out), ("stderr", self.console_err)):
            thread = threading.Thread(target=self._tail, args=(self.log_dir / attempt[f"{channel}_log"],
                                      self.log_dir / f"{channel}.log", console, stopped), daemon=True)
            thread.start()
            threads.append(thread)
        return threads, stopped

    def _stop_child(self):
        child = self.child
        if child is None:
            return
        # Only this launcher's new child group receives the signal. ComfyUI
        # starts in its own group and is never signalled, terminated or queried.
        try:
            if os.name == "nt":
                os.kill(child.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(child.pid, signal.SIGINT)
        except (ProcessLookupError, OSError):
            pass
        try:
            child.wait(timeout=self.policy.stop_grace_seconds)
        except subprocess.TimeoutExpired:
            # Do not use tree termination: descendants can include an
            # independent generation engine. This fallback owns only Popen.
            child.terminate()
            try:
                child.wait(timeout=self.policy.stop_grace_seconds)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=self.policy.stop_grace_seconds)

    def _observe_attempt(self, attempt):
        streak_start, maximum_stable = None, 0.0
        while True:
            if self.stop_event.is_set():
                self._stop_child()
                return self.child.poll()
            code = self.child.poll()
            if code is not None:
                return code
            match = self._matching(self.policy.probe_timeout)
            now = time.monotonic()
            if match:
                changed = attempt.get("ready_server_pid") != match["pid"] or attempt.get("ready_port") != match["port"]
                if streak_start is None or changed:
                    streak_start = now
                maximum_stable = max(maximum_stable, now - streak_start)
                was_stable = attempt["stable_ready"]
                attempt.update(self._ready_fields(match), first_ready_at=attempt.get("first_ready_at") or utc_now(),
                               last_ready_at=utc_now(), ready_stable_seconds=round(maximum_stable, 3),
                               stable_ready=maximum_stable >= self.policy.stable_seconds)
                if changed or attempt["stable_ready"] != was_stable:
                    self.receipt["status"] = "ready"
                    self._save()
            else:
                # A transient timeout never kills a live service. It only
                # breaks the continuous-health interval used for recovery.
                streak_start = None
            self.stop_event.wait(self.policy.poll_interval)

    def _wait_for_successor(self):
        deadline = time.monotonic() + self.policy.handover_timeout
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            match = self._matching(min(self.policy.probe_timeout, remaining))
            if match:
                return match
            self.stop_event.wait(min(self.policy.poll_interval, max(0.0, deadline - time.monotonic())))
        return None

    def _finish(self, status, code):
        self.receipt.update(status=status, wrapper_exit_code=code, finished_at=utc_now(),
                            intentional_stop=self.stop_event.is_set(), stop_signal=self.stop_signal)
        self._save()
        return code

    def _after_exit(self, attempt, code):
        if self.stop_event.is_set() or is_control_exit(code):
            attempt["decision"] = "intentional_stop" if self.stop_event.is_set() else "control_exit"
            return self._finish("stopped", 0)
        if code == 0:
            attempt["decision"] = "normal_exit"
            return self._finish("completed", 0)
        self._save()
        match = self._wait_for_successor()
        if self.stop_event.is_set():
            self._stop_child()
            attempt["decision"] = "intentional_stop"
            return self._finish("stopped", 0)
        if match:
            attempt["successor"] = self._ready_fields(match)
            attempt["decision"] = "matching_service_ready"
            self._say(f"Studio service is still ready on port {match['port']} (server PID {match['pid']}); "
                      f"the launcher child exited with original code {code}.")
            return self._finish("handed_over", 0)
        if attempt["stable_ready"] and self.receipt["restart_count"] < self.policy.max_restarts:
            attempt["decision"] = "restart_scheduled"
            self._save()
            self._say(f"Studio exited with code {code}; the cause is unknown. "
                      f"Restarting Studio only ({self.receipt['restart_count'] + 1}/{self.policy.max_restarts}).")
            return None
        attempt["decision"] = ("recovery_limit_reached" if attempt["stable_ready"] else "not_stable_for_recovery")
        self._say(f"[ERROR] Studio exited with original code {code}; the cause is unknown. "
                  f"Diagnostic logs: {self.log_dir}")
        return self._finish("failed", code if isinstance(code, int) else 1)

    def _run(self):
        self.log_dir.mkdir(parents=True, exist_ok=False)
        self._save()
        self._say(f"Studio diagnostic logs: {self.log_dir}")
        while True:
            if self.stop_event.is_set():
                return self._finish("stopped", 0)
            attempt = {"number": len(self.receipt["attempts"]) + 1, "started_at": utc_now(), "finished_at": None,
                       "child_pid": None, "ready_server_pid": None, "ready_port": None,
                       "first_ready_at": None, "ready_stable_seconds": 0.0, "stable_ready": False,
                       "original_exit_code": None, "decision": "starting",
                       "recovery_start": bool(self.receipt["attempts"])}
            attempt.update(stdout_log=f"attempt-{attempt['number']}.stdout.log",
                           stderr_log=f"attempt-{attempt['number']}.stderr.log", log_capture="direct_regular_file")
            self.receipt["attempts"].append(attempt)
            self.receipt["status"] = "starting"
            self._save()
            if self.stop_event.is_set():
                attempt.update(finished_at=utc_now(), decision="intentional_stop")
                return self._finish("stopped", 0)
            try:
                threads, tail_stopped = self._start_attempt(attempt)
            except OSError as error:
                attempt.update(finished_at=utc_now(), decision="launch_failed", launch_error_type=type(error).__name__,
                               launch_error_errno=error.errno, launch_error_winerror=getattr(error, "winerror", None),
                               launch_error_message=str(error))
                self._say(f"[ERROR] Studio could not be launched. Diagnostic logs: {self.log_dir}")
                return self._finish("failed", 1)
            started = time.monotonic()
            try:
                try:
                    code = self._observe_attempt(attempt)
                except KeyboardInterrupt:
                    self.stop_event.set()
                    self.stop_signal = "KeyboardInterrupt"
                    self._stop_child()
                    code = self.child.poll()
                attempt.update(finished_at=utc_now(), original_exit_code=code,
                               elapsed_seconds=round(time.monotonic() - started, 3))
                outcome = self._after_exit(attempt, code)
            finally:
                # Stop mirroring at a finite file offset. Surviving services
                # keep writing the authoritative attempt files independently.
                tail_stopped.set()
                deadline = time.monotonic() + self.policy.drain_timeout
                for thread in threads:
                    thread.join(max(0, deadline - time.monotonic()))
            if outcome is not None:
                return outcome

    def run(self):
        previous = {}
        if threading.current_thread() is threading.main_thread():
            def request_stop(signum, _frame):
                self.stop_signal = signal.Signals(signum).name
                self.stop_event.set()
            for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                signum = getattr(signal, name, None)
                if signum is not None:
                    previous[signum] = signal.signal(signum, request_stop)
        try:
            return self._run()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main(arguments=None):
    return StudioLauncher(Path(__file__).resolve().parent,
                          sys.argv[1:] if arguments is None else arguments).run()


if __name__ == "__main__":
    raise SystemExit(main())
