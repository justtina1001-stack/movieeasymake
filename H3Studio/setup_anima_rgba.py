"""Install pinned Anima RGBA weights without changing existing models or engine code."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import shutil

from model_updates import load_model_manifest
from settings import SettingsStore
from setup_qwen_image_turbo import exists, verify
from setup_video_expansion import (
    SPACE_MARGIN, checked_path, download_official, install_artifact, require_engine,
    save_report,
)


MANIFEST_PATH = Path(__file__).with_name("anima_rgba_manifest.json")
ALLOWED_MODEL_DIRECTORIES = {"diffusion_models", "text_encoders", "loras", "vae"}


def validate_manifest(path):
    manifest = load_model_manifest(Path(path))
    targets = set()
    for item in manifest["files"]:
        target = Path(item["target"])
        if (not re.fullmatch(r"[0-9a-f]{40}", item["revision"])
                or not item.get("sha256")
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", item["repo_id"])
                or len(target.parts) < 2
                or target.parts[0] not in ALLOWED_MODEL_DIRECTORIES
                or target.suffix != ".safetensors"
                or Path(item["source"]).suffix != ".safetensors"
                or item["target"].casefold() in targets):
            raise RuntimeError("Anima RGBA manifest has an invalid or duplicate pinned artifact.")
        targets.add(item["target"].casefold())
    if manifest["custom_nodes"]:
        raise RuntimeError("Anima RGBA uses native ComfyUI nodes; custom node installation is not supported.")
    return manifest


def install_anima_rgba(engine, staging, *, manifest=None, workers=2,
                       downloader=download_official, evidence_dir=None, verify_only=False):
    engine, staging = Path(engine).resolve(), Path(staging).resolve()
    version = require_engine(engine)
    manifest = validate_manifest(manifest or MANIFEST_PATH)
    model_root = engine / "models"
    model_root.mkdir(exist_ok=True)
    missing = [item for item in manifest["files"] if not exists(checked_path(model_root, item["target"]))]
    if verify_only and missing:
        raise RuntimeError("Anima RGBA verification found missing weights; no download was started.")
    required = sum(item["size"] for item in missing) + (SPACE_MARGIN if missing else 0)
    free = shutil.disk_usage(model_root).free
    if free < required:
        raise RuntimeError(f"Not enough free disk space; need {required:,} bytes and have {free:,}.")
    staging.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != model_root.stat().st_dev:
        raise RuntimeError("Choose staging on the ComfyUI model drive.")
    lock = staging / ".installer-running"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError("Anima RGBA installer is already running, or an interrupted lock needs inspection.") from None
    report = dict(version=manifest["version"], engine=str(engine), comfyui_version=version,
                  disk_free_bytes=free, disk_required_bytes=required, status="running", models=[], errors=[],
                  core_updated=False, custom_nodes_installed=False, services_restarted=False,
                  existing_versions_preserved=True, verify_only=verify_only)
    destination = Path(evidence_dir) / "anima-rgba-install.json" if evidence_dir is not None else None

    def persist():
        if destination is not None:
            save_report(destination, report)

    def run(item):
        def progress(done, size):
            print(f"Progress {Path(item['target']).name}: {done}/{size} bytes", flush=True)
        if verify_only:
            target = checked_path(model_root, item["target"])
            actual = verify(target, item["size"], item["sha256"])
            return {**item, "target": str(target), "actual_sha256": actual,
                    "installed": False, "verified_existing": True}
        # The underlying primitive checks the existing file's complete SHA-256
        # before returning, and never replaces a differing or racing file.
        return install_artifact(engine, staging, item, downloader=downloader, progress=progress)

    try:
        persist()
        with ThreadPoolExecutor(max_workers=max(1, min(2, int(workers)))) as executor:
            futures = {executor.submit(run, item): item for item in manifest["files"]}
            for future in as_completed(futures):
                try:
                    report["models"].append(future.result())
                except Exception as error:
                    report["errors"].append({"target": futures[future]["target"], "error": str(error)})
                persist()
        report["status"] = "failed" if report["errors"] else "completed"
        persist()
        if report["errors"]:
            raise RuntimeError("Some Anima RGBA files failed; existing files and resumable downloads were preserved. See the installation report.")
        return report
    except Exception as error:
        if report["status"] == "running":
            report["status"] = "failed"
            report["errors"].append({"phase": "setup", "error": str(error)})
            persist()
        raise
    finally:
        lock.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path)
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--verify-only", action="store_true", help="Verify existing weights without downloading missing files.")
    args = parser.parse_args(argv)
    app_dir = Path(__file__).resolve().parent
    if args.engine_dir is None:
        settings = SettingsStore(app_dir / "config.json", app_dir)
        if settings.load_error or settings.current.mode != "local":
            raise RuntimeError(settings.load_error or "Run this installer on the local GPU host.")
        engine = Path(settings.current.comfy_dir)
    else:
        engine = args.engine_dir
    result = install_anima_rgba(engine, args.staging_dir or engine / "models/.anima-rgba-download",
                                workers=args.workers,
                                evidence_dir=args.evidence_dir or app_dir / "data/anima_rgba_install",
                                verify_only=args.verify_only)
    print(f"Verified {len(result['models'])} pinned Anima RGBA weights.", flush=True)
    print("Existing models were preserved. No engine update, custom node installation or restart was performed.", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] {error}", flush=True)
        raise SystemExit(1) from None
