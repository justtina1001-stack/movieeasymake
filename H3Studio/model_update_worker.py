from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from huggingface_hub import hf_hub_download

from model_updates import load_model_manifest


def event(progress: int, step: str, **extra: object) -> None:
    print(
        "H3_MODEL_UPDATE_EVENT "
        + json.dumps({"progress": progress, "step": step, **extra}, ensure_ascii=False),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    target = Path(args.target).resolve()
    manifest = load_model_manifest(Path(args.manifest).resolve())
    if not (target / "main.py").is_file():
        raise RuntimeError("指定資料夾不是有效的 ComfyUI。")
    model_root = target / "models"
    files = manifest["files"]
    event(2, f"檢查模型版本 {manifest['version']}")
    for index, item in enumerate(files, start=1):
        destination = model_root / Path(item["target"])
        progress = 4 + round((index - 1) / len(files) * 92)
        event(progress, f"更新模型 {index}/{len(files)}：{destination.name}", current_file=destination.name)
        if destination.is_file() and destination.stat().st_size == item["size"]:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded = Path(hf_hub_download(
            repo_id=item["repo_id"],
            filename=item["source"],
            revision=item["revision"],
            local_dir=model_root,
            force_download=destination.exists(),
        ))
        if downloaded.resolve() != destination.resolve():
            raise RuntimeError(f"模型下載位置不符：{destination.name}")
        if not destination.is_file() or destination.stat().st_size != item["size"]:
            raise RuntimeError(f"模型檔案驗證失敗：{destination.name}")
    event(98, "驗證所有模型檔案")
    for item in files:
        path = model_root / Path(item["target"])
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"模型檔案未完成：{path.name}")
    event(100, f"模型版本 {manifest['version']} 更新完成")


if __name__ == "__main__":
    main()
