"""Install the pinned FLUX 3 Image Klein generation and editing weights on a local GPU host."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from flux3_image_models import LICENSE_URL, MIN_COMFY_VERSION, MODEL_FILES, REPO, REVISION
from settings import SettingsStore
from setup_qwen_image_turbo import exists, publish_verified, verify


SPACE_MARGIN = 1024**3


def engine_version(engine: Path) -> str:
    try:
        tree = ast.parse((engine / "comfyui_version.py").read_text(encoding="utf-8"))
        for statement in tree.body:
            if (isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__"
                                                        for target in statement.targets)
                    and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)):
                return statement.value.value
    except (OSError, SyntaxError):
        pass
    raise RuntimeError("Cannot read the local ComfyUI version; install a supported engine first.")


def require_engine(engine: Path) -> str:
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install the local ComfyUI engine first.")
    version = engine_version(engine)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    required = tuple(int(part) for part in MIN_COMFY_VERSION.split("."))
    if not match or tuple(map(int, match.groups())) < required:
        raise RuntimeError(f"FLUX 3 requires ComfyUI {MIN_COMFY_VERSION} or later; found {version}. Update the engine before installing these weights.")
    return version


def download_official(staging: Path, model: tuple) -> Path:
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    from huggingface_hub import hf_hub_download

    directory, filename, _size, _sha256 = model
    try:
        return Path(hf_hub_download(REPO, f"{directory}/{filename}", revision=REVISION, local_dir=staging))
    except Exception as error:
        # Hub exception strings may contain signed URLs. Keep credentials out of logs.
        raise RuntimeError(
            f"Official FLUX 3 download failed ({type(error).__name__}). Sign in to the official model page, accept its access terms yourself, then run hf auth login on this computer. Partial downloads are preserved."
        ) from None


def checked_target(root: Path, model: tuple) -> Path:
    directory, filename, size, sha256 = model
    if (directory not in {"diffusion_models", "text_encoders", "vae"}
            or Path(filename).name != filename or not filename.endswith(".safetensors")
            or not isinstance(size, int) or size <= 0 or not re.fullmatch(r"[a-f0-9]{64}", sha256)):
        raise RuntimeError("Invalid pinned model entry.")
    target = root / directory / filename
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("A model destination escaped the selected model directory.")
    return target


def copy_source(source: Path, cached: Path, size: int, sha256: str) -> Path:
    if source.is_symlink():
        raise RuntimeError(f"Cached source must be a regular file: {source.name}; preserved.")
    verify(source, size, sha256)
    cached.parent.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_name(cached.name + f".copying-{uuid.uuid4().hex}")
    shutil.copyfile(source, temporary)
    verify(temporary, size, sha256)
    if exists(cached):
        verify(cached, size, sha256)
        temporary.unlink()
        return cached
    if os.name == "nt":
        os.rename(temporary, cached)
    else:
        os.link(temporary, cached)
        temporary.unlink()
    return cached


def install_models(engine: Path, staging: Path, *, source_dir: Path | None = None,
                   model_files=MODEL_FILES, downloader=download_official) -> dict:
    engine, staging = Path(engine).resolve(), Path(staging).resolve()
    version = require_engine(engine)
    root = engine / "models"
    root.mkdir(exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    source_dir = Path(source_dir).resolve() if source_dir is not None else None
    if staging.stat().st_dev != root.stat().st_dev:
        raise RuntimeError("Choose staging on the ComfyUI model drive for atomic publication.")
    started = time.monotonic()
    result = {"repo": REPO, "revision": REVISION, "license_url": LICENSE_URL,
              "comfyui_version": version, "core_updated": False, "files": []}
    pending = []
    required_bytes = SPACE_MARGIN
    for model in model_files:
        target = checked_target(root, model)
        directory, filename, size, sha256 = model
        if exists(target):
            actual = verify(target, size, sha256)
            result["files"].append({"filename": f"{directory}/{filename}", "target": str(target),
                                    "size": size, "sha256": actual, "installed": False, "verified_existing": True})
            print(f"Verified existing: {filename}", flush=True)
            continue
        cached = staging / directory / filename
        if exists(cached):
            if cached.is_symlink():
                raise RuntimeError(f"Staged model must be a regular file: {filename}; preserved.")
            verify(cached, size, sha256)
        else:
            required_bytes += size
        if source_dir is not None and not exists(cached):
            source = source_dir / directory / filename
            if not source.resolve().is_relative_to(source_dir):
                raise RuntimeError("A cached source escaped the selected source directory.")
            # Fail before publishing any file if the offline bundle is incomplete.
            if not source.is_file() or source.stat().st_size != size:
                raise RuntimeError(f"Cached source is missing or incomplete: {filename}; no network fallback was attempted.")
        pending.append((model, target, cached))
    if pending and shutil.disk_usage(root).free < required_bytes:
        raise RuntimeError(f"Not enough free disk space; need at least {required_bytes:,} bytes.")
    for model, target, cached in pending:
        directory, filename, size, sha256 = model
        target.parent.mkdir(parents=True, exist_ok=True)
        if not exists(cached):
            if source_dir is not None:
                print(f"Copying verified local source: {filename}", flush=True)
                cached = copy_source(source_dir / directory / filename, cached, size, sha256)
            else:
                print(f"Downloading official weights: {filename} ({size / 1e9:.2f} GB)", flush=True)
                cached = Path(downloader(staging, model)).resolve()
                if not cached.is_relative_to(staging):
                    raise RuntimeError("Downloader returned a file outside the selected staging directory.")
        installed = publish_verified(cached, target, size, sha256)
        result["files"].append({"filename": f"{directory}/{filename}", "target": str(target), "size": size,
                                "sha256": sha256, "installed": installed, "verified_existing": not installed})
        print(f"Installed and verified: {filename}", flush=True)
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path, help="Explicit local ComfyUI directory; otherwise use Studio settings.")
    parser.add_argument("--source-dir", type=Path, help="Reuse an offline bundle containing diffusion_models, text_encoders and vae.")
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    app_dir = Path(__file__).resolve().parent
    if args.engine_dir is None:
        store = SettingsStore(app_dir / "config.json", app_dir)
        if store.load_error or store.current.mode != "local":
            raise RuntimeError(store.load_error or "Run this installer on the GPU host in local engine mode.")
        engine = Path(store.current.comfy_dir)
    else:
        engine = args.engine_dir
    staging = args.staging_dir or engine / "models" / ".flux3-download"
    result = install_models(engine, staging, source_dir=args.source_dir)
    if args.evidence_dir is not None:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        temporary = args.evidence_dir / "flux3-model-install.json.tmp"
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.evidence_dir / "flux3-model-install.json")
    print("FLUX 3 generation and editing weights verified. Restart ComfyUI to discover newly installed files.", flush=True)
    print(f"Model license: {LICENSE_URL}", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] {error}", flush=True)
        raise SystemExit(1) from None
