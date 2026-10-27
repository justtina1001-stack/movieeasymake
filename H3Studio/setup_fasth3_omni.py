"""Install the pinned FastH3 Omni NVFP4 checkpoint on a local Blackwell GPU host."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import time

from settings import SettingsStore
from setup_flux3_image import copy_source, engine_version
from setup_qwen_image_turbo import exists, publish_verified, verify
from video_models import (FASTH3_REPO, FASTH3_REVISION, FASTH3_FILENAME, FASTH3_SIZE,
                          FASTH3_SHA256, FASTH3_LICENSE_URL, FASTH3_MIN_COMFY_VERSION)


SPACE_MARGIN = 1024**3
SHARED_COMPONENTS = (
    ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15687142551,
     "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6"),
    ("vae", "minimax_h3_audio_vae_fp32.safetensors", 605254808,
     "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48"),
)
VIDEO_VAES = (
    ("vae", "minimax_h3_video_vae_int8_convrot.safetensors", 2811065184,
     "52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e"),
    ("vae", "minimax_h3_video_vae_fp16.safetensors", 5207808496,
     "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522"),
)


def require_engine(engine: Path) -> str:
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install the local ComfyUI engine first.")
    version = engine_version(engine)
    try:
        actual = tuple(int(part) for part in version.split("+", 1)[0].split("-", 1)[0].split("."))
    except ValueError:
        actual = ()
    if len(actual) != 3 or actual < tuple(map(int, FASTH3_MIN_COMFY_VERSION.split("."))):
        raise RuntimeError(f"FastH3 requires ComfyUI {FASTH3_MIN_COMFY_VERSION} or later with full PDD checkpoint support; found {version}.")
    try:
        tree = ast.parse((engine / "comfy/ldm/minimax/model.py").read_text("utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        for name in ("FinalLayer", "MiniMaxH3Model"):
            init = next(node for node in classes[name].body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
            if "pdd_steps" not in {arg.arg for arg in init.args.posonlyargs + init.args.args + init.args.kwonlyargs}:
                raise ValueError("missing PDD constructor")
    except (OSError, SyntaxError, KeyError, StopIteration, ValueError):
        raise RuntimeError("This ComfyUI build lacks full PDD checkpoint support. Update the core and its pinned dependencies, then restart before installing FastH3.") from None
    return version


def verify_shared(engine: Path) -> list[dict]:
    selected_vae = next((model for model in VIDEO_VAES if exists(engine / "models" / model[0] / model[1])), None)
    if selected_vae is None:
        raise RuntimeError("Install the existing H3 text encoder, audio VAE and at least one video VAE first.")
    result = []
    for directory, filename, size, checksum in (*SHARED_COMPONENTS, selected_vae):
        path = engine / "models" / directory / filename
        verify(path, size, checksum)
        result.append({"filename": f"{directory}/{filename}", "size": size, "sha256": checksum})
        print(f"Verified shared H3 component: {filename}", flush=True)
    return result


def download_official(staging: Path) -> Path:
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    from huggingface_hub import hf_hub_download

    try:
        return Path(hf_hub_download(FASTH3_REPO, FASTH3_FILENAME, revision=FASTH3_REVISION, local_dir=staging))
    except Exception as error:
        # Hub errors can include temporary signed URLs. Keep them out of logs.
        raise RuntimeError(f"Official FastH3 download failed ({type(error).__name__}). Partial downloads were preserved. Check access on the official model page and retry.") from None


def install_model(engine: Path, staging: Path, *, source_file: Path | None = None,
                  downloader=download_official, model=None) -> dict:
    engine, staging = Path(engine).resolve(), Path(staging).resolve()
    version = require_engine(engine)
    shared = verify_shared(engine)
    filename, size, checksum = model or (FASTH3_FILENAME, FASTH3_SIZE, FASTH3_SHA256)
    if (Path(filename).name != filename or not filename.endswith(".safetensors")
            or not isinstance(size, int) or size <= 0
            or not isinstance(checksum, str) or len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum)):
        raise RuntimeError("Invalid pinned FastH3 artifact.")
    root = engine / "models"
    target = root / "diffusion_models" / filename
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("FastH3 destination escaped the model directory.")
    started = time.monotonic()
    result = dict(repo=FASTH3_REPO, revision=FASTH3_REVISION, filename=filename, size=size, sha256=checksum,
                  target=str(target), comfyui_version=version, shared_components=shared,
                  core_updated=False, license_url=FASTH3_LICENSE_URL)
    if exists(target):
        verify(target, size, checksum)
        return {**result, "installed": False, "verified_existing": True}
    staging.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != root.stat().st_dev:
        raise RuntimeError("Choose staging on the ComfyUI model drive for atomic publication.")
    cached = staging / filename
    if exists(cached):
        if cached.is_symlink():
            raise RuntimeError("Staged FastH3 model must be a regular file; preserved.")
        verify(cached, size, checksum)
    else:
        if shutil.disk_usage(root).free < size + SPACE_MARGIN:
            raise RuntimeError(f"Not enough free disk space; need at least {size + SPACE_MARGIN:,} bytes.")
        if source_file is not None:
            source = Path(source_file).resolve()
            if Path(source_file).is_symlink():
                raise RuntimeError("Offline FastH3 source must be a regular file; preserved.")
            print(f"Copying verified offline source: {filename}", flush=True)
            cached = copy_source(source, cached, size, checksum)
        else:
            print(f"Downloading official FastH3 weights ({size / 1e9:.2f} GB)", flush=True)
            cached = Path(downloader(staging)).resolve()
            if not cached.is_relative_to(staging):
                raise RuntimeError("Downloader returned a file outside the selected staging directory.")
    installed = publish_verified(cached, target, size, checksum)
    return {**result, "installed": installed, "verified_existing": not installed,
            "elapsed_seconds": round(time.monotonic() - started, 2)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path)
    parser.add_argument("--source-file", type=Path, help="Reuse an offline copy of the pinned checkpoint without downloading.")
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    app_dir = Path(__file__).resolve().parent
    if args.engine_dir is None:
        settings = SettingsStore(app_dir / "config.json", app_dir)
        if settings.load_error or settings.current.mode != "local":
            raise RuntimeError(settings.load_error or "Run this installer on the GPU host in local engine mode.")
        engine = Path(settings.current.comfy_dir)
    else:
        engine = args.engine_dir
    result = install_model(engine, args.staging_dir or engine / "models/.fasth3-download", source_file=args.source_file)
    if args.evidence_dir is not None:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        temporary = args.evidence_dir / "fasth3-model-install.json.tmp"
        temporary.write_text(json.dumps(result, indent=2) + "\n", "utf-8")
        temporary.replace(args.evidence_dir / "fasth3-model-install.json")
    print("FastH3 Omni NVFP4 checkpoint verified. Restart ComfyUI to discover newly installed files.", flush=True)
    print(f"Model license: {FASTH3_LICENSE_URL}", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] {error}", flush=True)
        raise SystemExit(1) from None
