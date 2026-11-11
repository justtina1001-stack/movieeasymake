"""Standard-library identities shared by Studio and its local supervisor."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def studio_source_fingerprint(root: Path) -> str:
    """Hash shipped code, never settings, tests, models or generated work."""
    root = Path(root)
    files = list(root.glob("*.py"))
    static = root / "static"
    if static.is_dir():
        files.extend(path for path in static.rglob("*")
                     if path.is_file() and path.suffix.lower() in {".html", ".js", ".css"})
    writing_skills = root / "writing_skills"
    if writing_skills.is_dir():
        files.extend(path for path in writing_skills.rglob("*")
                     if path.is_file() and path.suffix.lower() in {".md", ".json", ".txt"})
    fingerprint = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        content = path.read_bytes()
        fingerprint.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        fingerprint.update(len(content).to_bytes(8, "big"))
        fingerprint.update(content)
    return fingerprint.hexdigest()


def studio_workspace_fingerprint(root: Path) -> str:
    """Identify the resolved workspace, matching Studio's absolute APP_DIR."""
    root = Path(root).resolve()
    return hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()


def current_studio_identity(root: Path) -> dict[str, str]:
    """Recompute the expected identity from the files currently on disk."""
    root = Path(root).resolve()
    return {"studio": "minimax-h3-studio", "source_fingerprint": studio_source_fingerprint(root),
            "workspace_fingerprint": studio_workspace_fingerprint(root)}
