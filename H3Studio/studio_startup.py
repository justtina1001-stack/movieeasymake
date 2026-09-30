"""Find a healthy local Studio or reserve its listening socket before startup.

This module deliberately uses only the standard library. A returned new socket
is already listening and nonblocking: pass it directly to ``web.run_app(sock=...)``
without host/port arguments. The caller owns it until aiohttp takes ownership and
must close it if a later startup step fails.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import errno
import http.client
import json
import math
import os
from pathlib import Path
import socket
import threading
import time


AUTO_PORTS = (8787, 8789, 8790, 8791, 8792)
PROBE_TIMEOUT = 1.5
CONNECT_TIMEOUT = 0.15
LOCK_WAIT_TIMEOUT = 5.0
LOCK_RETRY_INTERVAL = 0.1
STUDIO_TITLE = b"<title>MiniMax H3 Studio</title>"


class StartupLock:
    """Workspace-wide process lock; close when the owning Studio exits."""

    def __init__(self, handle):
        self.handle = handle

    def close(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


@dataclass(frozen=True)
class StartupPlan:
    port: int
    existing: bool
    sock: socket.socket | None = None
    lock: StartupLock | None = None


def _try_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return StartupLock(handle)
    except BaseException as error:
        handle.close()
        if isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise


def _valid_port(port):
    return type(port) is int and 1 <= port <= 65535


def _interrupt(sock):
    # shutdown also wakes HTTPResponse's buffered file object. Merely close()
    # does not interrupt reads while that file holds a reference to the socket.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def _read_local_http(port, path, timeout, *, max_bytes=8192, accept="text/html"):
    """Read a small loopback response with a total deadline and no redirects."""
    if not _valid_port(port) or isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return None
    if not math.isfinite(timeout) or timeout <= 0:
        return None
    deadline = time.monotonic() + timeout
    sock = None
    response = None
    timer = None
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sock.settimeout(remaining)
        # Socket timeouts alone are per-read: a slow peer could otherwise keep
        # sending one byte at a time forever. This deadline bounds header/body
        # parsing as well as a completely stalled response.
        timer = threading.Timer(remaining, _interrupt, args=(sock,))
        timer.daemon = True
        timer.start()
        request = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   f"Connection: close\r\nAccept: {accept}\r\n\r\n").encode("ascii")
        sock.sendall(request)
        response = http.client.HTTPResponse(sock, method="GET")
        response.begin()
        if response.status != 200:
            return None
        body = response.read(max_bytes)
        return body if time.monotonic() <= deadline else None
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        if sock is not None:
            sock.close()


def probe_studio(port: int, timeout: float = 1.5) -> bool:
    """Recognize Studio's root page within a total wall-clock deadline.

    Direct loopback sockets bypass environment/system proxies. HTTPResponse is
    a parser only, so redirects cannot be followed. At most the first 8192 body
    bytes are inspected; /api/status and the generation engine are never queried.
    """
    body = _read_local_http(port, "/", timeout)
    return body is not None and STUDIO_TITLE in body


def _existing_plan(port, required_editor_capabilities):
    if required_editor_capabilities:
        # Static files are read from disk by an already running Python process,
        # while registered API handlers keep their previous code. A healthy root
        # page alone therefore cannot prove new editor features are loaded.
        body = _read_local_http(port, "/api/editor/capabilities", PROBE_TIMEOUT,
                                max_bytes=8193, accept="application/json")
        capabilities = None
        if body is not None and len(body) <= 8192:
            try:
                capabilities = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                pass
        if not isinstance(capabilities, dict) or any(
                capabilities.get(name) is not True for name in required_editor_capabilities):
            labels = {"position_keyframes": "位置動畫", "speed_curves": "曲線變速"}
            features = "、".join(labels.get(name, name)
                               for name in required_editor_capabilities)
            raise RuntimeError(
                f"Studio 仍在 http://127.0.0.1:{port} 執行，但未載入目前版本的{features}。"
                "再次執行啟動檔只會開啟原有服務，不會重新啟動它。"
                "請先在剪輯器儲存專案，再關閉原本的 Studio 啟動視窗，確認服務已停止後重新啟動。"
                "本次未開啟舊服務，也未另開第二個工作佇列。")
    return StartupPlan(port=port, existing=True)


def _has_listener(port, timeout=CONNECT_TIMEOUT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _healthy_listener(port, timeout=PROBE_TIMEOUT):
    deadline = time.monotonic() + timeout
    return (_has_listener(port, timeout=min(CONNECT_TIMEOUT, timeout))
            and probe_studio(port, timeout=max(0, deadline - time.monotonic())))


def _find_existing(ports, timeout):
    if len(ports) == 1:
        healthy = [_healthy_listener(ports[0], timeout=timeout)]
    else:
        with ThreadPoolExecutor(max_workers=len(ports), thread_name_prefix="studio-preflight") as pool:
            healthy = list(pool.map(lambda port: _healthy_listener(port, timeout=timeout), ports))
    return next((port for port, existing in zip(ports, healthy) if existing), None)


def _reserve_port(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            # SO_REUSEADDR allows competing binds on Windows and must never be
            # used here. Exclusive ownership is acquired before bind().
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(socket.SOMAXCONN)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def plan_startup(preferred_port: int = 8787, auto_port: bool = False, *, lock_file=None,
                 required_editor_capabilities=()) -> StartupPlan:
    """Prefer any already healthy Studio over starting a second local gateway.

    Auto mode checks preferred, 8787, 8789, 8790, 8791, 8792, in that priority.
    Health checks run concurrently before any free port is reserved, so a Studio
    on an alternative port is reused even when the preferred port is available.
    No process is stopped or modified when a port is occupied by another service.
    Optional required editor flags must be exactly true on a reused Studio; an
    outdated or unresponsive capability endpoint fails without another startup.
    """
    if not _valid_port(preferred_port):
        raise RuntimeError("Studio 埠號必須是 1–65535 的整數。")
    if not isinstance(required_editor_capabilities, (tuple, list)) or any(
            not isinstance(name, str) or not name for name in required_editor_capabilities):
        raise ValueError("required_editor_capabilities must contain nonempty capability names")
    required_editor_capabilities = tuple(required_editor_capabilities)
    ports = tuple(dict.fromkeys((preferred_port, *AUTO_PORTS))) if auto_port else (preferred_port,)
    existing = _find_existing(ports, PROBE_TIMEOUT)
    if existing is not None:
        return _existing_plan(existing, required_editor_capabilities)
    path = Path(lock_file) if lock_file is not None else Path(__file__).resolve().parent / "data" / ".studio-startup.lock"
    try:
        lock = _try_lock(path)
        deadline = time.monotonic() + LOCK_WAIT_TIMEOUT
        while lock is None and time.monotonic() < deadline:
            existing = _find_existing(ports, min(PROBE_TIMEOUT, deadline - time.monotonic()))
            if existing is not None:
                return _existing_plan(existing, required_editor_capabilities)
            lock = _try_lock(path)
            if lock is None:
                time.sleep(min(LOCK_RETRY_INTERVAL, max(0, deadline - time.monotonic())))
    except OSError as error:
        raise RuntimeError("無法建立 Studio 啟動鎖，請確認 data 目錄的存取權限。") from error
    if lock is None:
        raise RuntimeError("另一個 Studio 正在啟動或暫時無法回應，請稍候再試；為避免重複啟動工作佇列，本次未另開服務。")
    try:
        # Another launcher may have become ready just before this lock was
        # acquired. Check again while holding it, before reserving any socket.
        existing = _find_existing(ports, PROBE_TIMEOUT)
        if existing is not None:
            lock.close()
            return _existing_plan(existing, required_editor_capabilities)
        failure = None
        for port in ports:
            try:
                return StartupPlan(port=port, existing=False, sock=_reserve_port(port), lock=lock)
            except OSError as error:
                failure = error
        tried = "、".join(str(port) for port in ports)
        hint = "請釋放其中一個埠，或指定其他埠。" if auto_port else "請指定其他埠，或使用 --auto-port 自動尋找。"
        raise RuntimeError(f"Studio 無法啟動：本機埠 {tried} 已被占用或無法綁定，且沒有可重用的正常 Studio。{hint}") from failure
    except BaseException:
        lock.close()
        raise
