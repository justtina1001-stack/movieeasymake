from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


def check_python_executable(
    executable: Path,
    required_imports: Iterable[str] = (),
    timeout: int = 60,
) -> tuple[bool, str]:
    """Return whether a Python executable works on this computer.

    Windows virtual environments are not portable: their ``pyvenv.cfg`` keeps
    the base interpreter's absolute path.  Merely checking that python.exe was
    copied is therefore insufficient.
    """

    executable = Path(executable)
    if not executable.is_file():
        return False, "找不到 python.exe"
    imports = "; ".join(f"import {name}" for name in required_imports)
    code = f"import sys; {imports + '; ' if imports else ''}print(sys.executable)"
    try:
        completed = subprocess.run(
            [str(executable), "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if completed.returncode == 0:
        return True, completed.stdout.strip()
    detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
    return False, detail


def inspect_python_candidates(
    candidates: Iterable[Path],
    required_imports: Iterable[str] = (),
) -> dict[str, object]:
    checked: list[dict[str, object]] = []
    for candidate in candidates:
        path = Path(candidate)
        exists = path.is_file()
        ready, detail = check_python_executable(path, required_imports) if exists else (False, "找不到檔案")
        item = {"path": str(path), "exists": exists, "ready": ready, "detail": detail}
        checked.append(item)
        if ready:
            return {"ready": True, "executable": str(path), "detail": detail, "candidates": checked}
    copied_but_invalid = any(bool(item["exists"]) for item in checked)
    return {
        "ready": False,
        "executable": None,
        "detail": "偵測到不可執行的搬移環境" if copied_but_invalid else "尚未建立 Python 環境",
        "candidates": checked,
    }
