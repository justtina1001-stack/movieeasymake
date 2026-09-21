"""Download optional, research-licensed image weights without changing H3 weights."""
from __future__ import annotations
import argparse
import hashlib
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from huggingface_hub import hf_hub_download
from settings import SettingsStore
from qwen_image_models import MODEL_FILES, REPO, REVISION, LICENSE_URL


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-evaluation", action="store_true",
                        help="Install for research/evaluation under the Qwen Research License.")
    args = parser.parse_args()
    if not args.research_evaluation:
        parser.error(f"Use --research-evaluation for non-commercial evaluation. License: {LICENSE_URL}")
    app_dir = Path(__file__).resolve().parent
    store = SettingsStore(app_dir / "config.json", app_dir)
    if store.load_error or store.current.mode != "local":
        raise RuntimeError(store.load_error or "Run this installer on the GPU host in local engine mode.")
    engine = Path(store.current.comfy_dir)
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install the local ComfyUI engine first.")
    root = engine / "models"
    staging = app_dir / "data/qwen_image_download"
    staging.mkdir(parents=True, exist_ok=True)
    missing = [m for m in MODEL_FILES if not (root / m[0] / m[1]).exists()]
    if shutil.disk_usage(root).free < sum(m[2] for m in missing) + 1024**3:
        raise RuntimeError("Not enough free disk space for the selected weights.")

    def download(model):
        directory, filename, size, sha256 = model
        target = root / directory / filename
        if target.exists():
            if target.stat().st_size != size or digest(target) != sha256:
                raise RuntimeError(f"Existing {filename} differs from the pinned weights; preserved.")
            print(f"Verified: {filename}", flush=True)
            return
        print(f"Downloading: {filename} ({size / 1e9:.2f} GB)", flush=True)
        cached = Path(hf_hub_download(REPO, f"{directory}/{filename}", revision=REVISION, local_dir=staging))
        if cached.stat().st_size != size or digest(cached) != sha256:
            raise RuntimeError(f"Checksum failed: {filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Staging is on the same installation drive; replace only after verification.
        if target.exists():
            raise RuntimeError(f"{filename} appeared during download; preserved.")
        cached.rename(target)
        print(f"Installed and verified: {filename}", flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, MODEL_FILES))
    print("Qwen-Image-2.1 weights installed. Requires ComfyUI TextEncodeQwenImage21 support.", flush=True)
    print(f"Research/evaluation only unless separately commercially licensed: {LICENSE_URL}", flush=True)


if __name__ == "__main__":
    main()
