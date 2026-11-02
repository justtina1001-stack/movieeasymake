"""Install pinned video adapters without replacing existing models or engine code."""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from model_updates import load_model_manifest
from settings import SettingsStore
from setup_flux3_image import copy_source, engine_version
from setup_qwen_image_turbo import exists, publish_verified, verify


SPACE_MARGIN = 1024 ** 3
MANIFEST_PATH = Path(__file__).with_name("video_expansion_manifest.json")
NODE_CLASS = "MiniMaxH3RefPatchLoader"


def validate_manifest(path):
    manifest = load_model_manifest(Path(path))
    targets = set()
    for item in manifest["files"]:
        if (not re.fullmatch(r"[0-9a-f]{40}", item["revision"])
                or not item.get("sha256")
                or Path(item["target"]).parts[0] not in {"loras", "model_patches"}
                or Path(item["target"]).suffix != ".safetensors"
                or item["target"].casefold() in targets):
            raise RuntimeError("Expansion manifest has an invalid or duplicate pinned artifact.")
        targets.add(item["target"].casefold())
    if len(manifest["custom_nodes"]) != 1:
        raise RuntimeError("Expansion must contain exactly one pinned Ref Patch node.")
    return manifest


def checked_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise RuntimeError("Installation path escaped its directory; existing files were preserved.")
    return path


def require_engine(engine):
    engine = Path(engine)
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install or select the local ComfyUI engine first.")
    version = engine_version(engine)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    if not match or tuple(map(int, match.groups())) < (0, 42, 0):
        raise RuntimeError("This expansion needs ComfyUI 0.42.0 or later; no automatic engine upgrade is performed.")
    return version


def download_official(item, staging, progress=None):
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    # Do not expose temporary signed URLs in exceptions or automatic retry logs.
    logging.getLogger("huggingface_hub").setLevel(logging.CRITICAL)
    from huggingface_hub import hf_hub_download
    try:
        shared = Path(hf_hub_download(item["repo_id"], item["source"], revision=item["revision"],
                                     local_files_only=True, token=False)).resolve()
        cached = checked_path(staging, item["source"])
        return copy_source(shared, cached, item["size"], item["sha256"])
    except Exception:
        pass
    # Some large whole-object CDN requests stall before the first byte. Bounded
    # range requests preserve progress and validate every returned byte range.
    # A completed file still requires the pinned full SHA-256 before publication.
    return download_ranges(item, staging, progress=progress)


def download_ranges(item, staging, *, progress=None, opener=urlopen, chunk_size=16 * 1024 ** 2):
    partial = checked_path(staging, item["source"] + ".range-partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    size = item["size"]
    done = partial.stat().st_size if exists(partial) else 0
    if done > size:
        raise RuntimeError("Partial download is larger than the pinned file; preserved for inspection.")
    base = (f"https://huggingface.co/{quote(item['repo_id'], safe='/')}/resolve/"
            f"{item['revision']}/{quote(item['source'], safe='/')}")
    last_report = time.monotonic()
    while done < size:
        end = min(size - 1, done + chunk_size - 1)
        # The explicit query also separates cache entries for each range.
        request = Request(base + f"?download=true&h3studio_range={done}-{end}",
                          headers={"Range": f"bytes={done}-{end}", "User-Agent": "H3Studio-PinnedInstaller/1"})
        for attempt in range(1, 4):
            try:
                with opener(request, timeout=40) as response:
                    expected = f"bytes {done}-{end}/{size}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise RuntimeError("Server returned a mismatched byte range; partial file preserved.")
                    data = response.read(end - done + 1)
                    if len(data) != end - done + 1 or response.read(1):
                        raise RuntimeError("Server returned an incomplete or oversized range; partial file preserved.")
                break
            except Exception as error:
                if attempt == 3:
                    raise RuntimeError(f"Download failed ({type(error).__name__}); verified ranges retained for retry.") from None
                time.sleep(2 * attempt)
        with partial.open("ab") as stream:
            if stream.tell() != done:
                raise RuntimeError("Partial download changed during transfer; no bytes appended.")
            stream.write(data)
        done += len(data)
        now = time.monotonic()
        if progress is not None and (done == size or now - last_report >= 15):
            progress(done, size)
            last_report = now
    return partial


def install_artifact(engine, staging, item, *, downloader=download_official, progress=None):
    root = Path(engine).resolve() / "models"
    target = checked_path(root, item["target"])
    started = time.monotonic()
    if exists(target):
        actual = verify(target, item["size"], item["sha256"])
        return {**item, "installed": False, "verified_existing": True,
                "actual_sha256": actual, "target": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(staging).resolve()
    item_stage = stage_root / item["repo_id"].replace("/", "--") / item["revision"]
    item_stage.mkdir(parents=True, exist_ok=True)
    if item_stage.stat().st_dev != target.parent.stat().st_dev:
        raise RuntimeError("Staging must be on the model drive for atomic publication.")
    cached = checked_path(item_stage, item["source"])
    if not exists(cached):
        print(f"Downloading {Path(item['target']).name} ({item['size'] / 1e9:.3f} GB)", flush=True)
        candidate = Path(downloader(item, item_stage, progress))
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(item_stage):
            raise RuntimeError("Downloader returned an unsafe staged path; file was not published.")
        cached = candidate.resolve()
    installed = publish_verified(cached, target, item["size"], item["sha256"])
    print(f"Verified {Path(item['target']).name}", flush=True)
    return {**item, "installed": installed, "verified_existing": not installed,
            "actual_sha256": item["sha256"], "target": str(target),
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def git(arguments, *, cwd=None):
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(["git", *arguments], cwd=cwd, env=environment,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Pinned Ref Patch checkout failed; existing node and staging were preserved.")
    return result.stdout.strip()


def verify_node(target, node):
    if target.is_symlink() or not (target / ".git").is_dir():
        raise RuntimeError("Existing Ref Patch node is not a regular pinned git checkout; preserved.")
    revision = git(["rev-parse", "HEAD"], cwd=target)
    if revision != node["revision"] or git(["status", "--porcelain"], cwd=target):
        raise RuntimeError("Existing Ref Patch node differs from the pinned version; no replacement was performed.")
    tree = ast.parse((target / "__init__.py").read_text("utf-8"))
    entry = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == NODE_CLASS), None)
    if entry is None or not {"INPUT_TYPES", "apply_patch"} <= {n.name for n in entry.body if isinstance(n, ast.FunctionDef)}:
        raise RuntimeError("Pinned Ref Patch node contract is missing.")
    return {**node, "actual_revision": revision, "path": str(target), "node_class": NODE_CLASS}


def install_ref_node(engine, staging, node):
    parent = Path(engine).resolve() / "custom_nodes"
    parent.mkdir(parents=True, exist_ok=True)
    target = checked_path(parent, node["target"])
    if exists(target):
        return {**verify_node(target, node), "installed": False, "verified_existing": True}
    stage_root = Path(staging).resolve() / "custom_nodes"
    stage = checked_path(stage_root, node["target"])
    stage.parent.mkdir(parents=True, exist_ok=True)
    if not exists(stage):
        git(["clone", "--no-checkout", node["repo_url"], str(stage)])
    if stage.is_symlink() or not (stage / ".git").is_dir():
        raise RuntimeError("Ref Patch staging is not a git checkout; preserved.")
    git(["checkout", "--detach", node["revision"]], cwd=stage)
    verify_node(stage, node)
    if stage.stat().st_dev != parent.stat().st_dev:
        raise RuntimeError("Node staging must be on the engine drive.")
    if exists(target):
        return {**verify_node(target, node), "installed": False, "verified_existing": True}
    try:
        os.rename(stage, target)
    except FileExistsError:
        return {**verify_node(target, node), "installed": False, "verified_existing": True}
    return {**verify_node(target, node), "installed": True, "verified_existing": False}


def save_report(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(path)


def install_expansion(engine, staging, *, manifest=None, workers=2, downloader=download_official,
                      node_installer=install_ref_node, evidence_dir=None, node_only=False):
    engine, staging = Path(engine).resolve(), Path(staging).resolve()
    version = require_engine(engine)
    manifest = validate_manifest(manifest or MANIFEST_PATH)
    items = [] if node_only else manifest["files"]
    model_root = engine / "models"
    model_root.mkdir(exist_ok=True)
    required = sum(item["size"] for item in items if not exists(checked_path(model_root, item["target"]))) + SPACE_MARGIN
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
        raise RuntimeError("Expansion installer is already running, or an interrupted lock needs inspection.") from None
    report = dict(version=manifest["version"], engine=str(engine), comfyui_version=version,
                  disk_free_bytes=free, disk_required_bytes=required, status="running", models=[], errors=[],
                  core_updated=False, services_restarted=False, existing_versions_preserved=True, node_only=node_only)
    destination = Path(evidence_dir) / "video-expansion-install.json" if evidence_dir is not None else None
    def persist():
        if destination is not None:
            save_report(destination, report)
    def run(item):
        def progress(done, size):
            print(f"Progress {Path(item['target']).name}: {done}/{size} bytes", flush=True)
        return install_artifact(engine, staging, item, downloader=downloader, progress=progress)
    try:
        report["custom_node"] = node_installer(engine, staging, manifest["custom_nodes"][0])
        persist()
        with ThreadPoolExecutor(max_workers=max(1, min(2, int(workers)))) as executor:
            futures = {executor.submit(run, item): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    report["models"].append(future.result())
                except Exception as error:
                    report["errors"].append({"target": item["target"], "error": str(error)})
                persist()
        report["status"] = "failed" if report["errors"] else "completed"
        persist()
        if report["errors"]:
            raise RuntimeError("Some expansion files failed; verified files and partial downloads were preserved. See the installation report.")
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
    parser.add_argument("--node-only", action="store_true", help="Install/verify only the pinned Ref Patch node.")
    args = parser.parse_args(argv)
    app_dir = Path(__file__).resolve().parent
    if args.engine_dir is None:
        settings = SettingsStore(app_dir / "config.json", app_dir)
        if settings.load_error or settings.current.mode != "local":
            raise RuntimeError(settings.load_error or "Run this installer on the local GPU host.")
        engine = Path(settings.current.comfy_dir)
    else:
        engine = args.engine_dir
    result = install_expansion(engine, args.staging_dir or engine / "models/.video-expansion-download",
                               workers=args.workers, evidence_dir=args.evidence_dir or app_dir / "data/video_expansion_install",
                               node_only=args.node_only)
    print(f"Verified {len(result['models'])} expansion weights and the pinned Ref Patch node.", flush=True)
    print("Existing models were preserved. Restart ComfyUI and Studio when generation is idle to discover additions.", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] {error}", flush=True)
        raise SystemExit(1) from None
