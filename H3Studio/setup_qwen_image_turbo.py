"""Install only the pinned official Qwen-Image-2.1 Turbo INT8 model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import time

from qwen_image_models import REPO, LICENSE_URL, TURBO_MODEL_FILE, TURBO_REVISION
from settings import SettingsStore


SPACE_MARGIN = 1024**3


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(path: Path, size: int, sha256: str) -> str:
    if not path.is_file() or path.stat().st_size != size:
        raise RuntimeError(f"Size verification failed: {path.name}; file preserved.")
    before = path.stat()
    actual = digest(path)
    after = path.stat()
    if actual != sha256 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"SHA256 verification failed: {path.name}; file preserved.")
    return actual


def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def publish_verified(cached: Path, target: Path, size: int, sha256: str) -> bool:
    """Publish on the same filesystem, preserving even a racing destination."""
    if cached.is_symlink():
        raise RuntimeError("Staged model must be a regular file; symbolic link preserved.")
    verify(cached, size, sha256)
    if exists(target):
        verify(target, size, sha256)
        return False
    if cached.stat().st_dev != target.parent.stat().st_dev:
        raise RuntimeError("Staging and models must be on the same filesystem for atomic publication.")
    try:
        if os.name == "nt":
            # Windows rename fails if the destination appeared; it never overwrites it.
            os.rename(cached, target)
        else:
            # POSIX rename overwrites; an exclusive hard link avoids that race.
            os.link(cached, target)
            cached.unlink()
    except FileExistsError:
        verify(target, size, sha256)
        return False
    return True


def download_official(staging: Path, model: tuple, revision: str, progress=None) -> Path:
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import tqdm

    directory, filename, size, _sha256 = model

    class Progress(tqdm):
        def __init__(self, *args, **kwargs):
            self.tracks_file = not str(kwargs.get("name", "")).endswith(".transfer") and "downloading bytes" not in str(kwargs.get("desc", "")).lower()
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)
            self.completed = 0
            self.last_report = time.monotonic()

        def update(self, amount=1):
            if self.total == size and self.tracks_file:
                self.completed = min(size, self.completed + int(amount))
                now = time.monotonic()
                if progress is not None and now - self.last_report >= 15:
                    progress(self.completed, size)
                    self.last_report = now
            if self.disable:
                self.n += amount
            return super().update(amount)

    try:
        return Path(hf_hub_download(REPO, f"{directory}/{filename}", revision=revision,
            local_dir=staging, token=False, tqdm_class=Progress))
    except Exception as error:
        # Hub errors can contain signed URLs; do not print their payload or traceback.
        raise RuntimeError(f"Official Turbo download failed ({type(error).__name__}); partial files preserved for resume.") from None


def install_turbo(engine: Path, staging: Path, *, model=TURBO_MODEL_FILE,
                  revision=TURBO_REVISION, downloader=download_official, progress=None) -> dict:
    engine, staging = Path(engine), Path(staging)
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install the local ComfyUI engine first.")
    directory, filename, size, sha256 = model
    target = engine / "models" / directory / filename
    started = time.monotonic()
    evidence = {"repo": REPO, "revision": revision, "filename": f"{directory}/{filename}",
                "expected_bytes": size, "expected_sha256": sha256, "target": str(target),
                "other_models_downloaded": False, "core_updated": False}
    if exists(target):
        evidence.update(actual_sha256=verify(target, size, sha256), installed=False,
                        verified_existing=True, actual_bytes=target.stat().st_size)
        return evidence
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != target.parent.stat().st_dev:
        raise RuntimeError("Choose staging on the ComfyUI model drive; no cross-drive model copy is performed.")
    cached = staging / directory / filename
    # Only a completed staged file earns space credit; incomplete Xet files may be preallocated.
    staged_bytes = cached.stat().st_size if cached.is_file() and cached.stat().st_size == size else 0
    required = max(0, size - staged_bytes) + SPACE_MARGIN
    free = shutil.disk_usage(target.parent).free
    evidence.update(disk_free_bytes_before=free, disk_required_bytes=required)
    if free < required:
        raise RuntimeError(f"Not enough free disk space: need {required:,} bytes, have {free:,}.")
    if not cached.is_file():
        print(f"Downloading official Turbo: {filename} ({size / 1e9:.2f} GB); resumable staging.", flush=True)
        cached = Path(downloader(staging, model, revision, progress))
    if not cached.resolve().is_relative_to(staging.resolve()):
        raise RuntimeError("Downloader returned a file outside the selected staging directory.")
    print(f"Verifying size and SHA256: {filename}", flush=True)
    installed = publish_verified(cached, target, size, sha256)
    evidence.update(installed=installed, verified_existing=not installed, actual_bytes=size,
                    actual_sha256=sha256, elapsed_seconds=round(time.monotonic() - started, 2),
                    publication="verified same-filesystem atomic no-overwrite")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-evaluation", action="store_true")
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.research_evaluation:
        parser.error(f"Use --research-evaluation under the existing Qwen research license. {LICENSE_URL}")
    app_dir = Path(__file__).resolve().parent
    store = SettingsStore(app_dir / "config.json", app_dir)
    if store.load_error or store.current.mode != "local":
        raise RuntimeError(store.load_error or "Run this installer on the local GPU host.")
    engine = Path(store.current.comfy_dir)
    staging = args.staging_dir or app_dir / "data/qwen_image_turbo_download"

    def progress(completed, total):
        print(f"Turbo download: {completed / 1e9:.2f}/{total / 1e9:.2f} GB", flush=True)

    result = install_turbo(engine, staging, progress=progress)
    if args.evidence_dir is not None:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        temporary = args.evidence_dir / "download-evidence.json.tmp"
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.evidence_dir / "download-evidence.json")
    print(f"Official Turbo installed and verified: {result['target']}", flush=True)
    print("Existing encoder, VAE, H3 models and ComfyUI core were not changed.", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] {error}", flush=True)
        raise SystemExit(1) from None
