from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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


def run(command: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"指令失敗（{completed.returncode}）：{command[0]} {detail}".strip())
    return completed.stdout.strip()


def install_custom_node(comfy_root: Path, item: dict[str, object]) -> None:
    destination = comfy_root / "custom_nodes" / str(item["target"])
    expected_url = str(item["repo_url"])
    revision = str(item["revision"])
    if destination.exists() and not (destination / ".git").is_dir():
        raise RuntimeError(f"自訂節點目標已存在但不是 Git 專案：{destination.name}")
    created = False
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", expected_url, str(destination)])
        created = True
    actual_url = run(["git", "remote", "get-url", "origin"], cwd=destination)
    if actual_url.rstrip("/").removesuffix(".git").casefold() != expected_url.rstrip("/").removesuffix(".git").casefold():
        raise RuntimeError(f"自訂節點來源不符：{destination.name}")
    if not created:
        dirty = run(["git", "status", "--porcelain"], cwd=destination)
        if dirty:
            raise RuntimeError(f"自訂節點有尚未提交的修改，為避免覆蓋已停止更新：{destination.name}")
        current = run(["git", "rev-parse", "HEAD"], cwd=destination)
        if current.lower() == revision.lower():
            return
    run(["git", "fetch", "--depth", "1", "origin", revision], cwd=destination)
    run(["git", "checkout", "--detach", revision], cwd=destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    custom_nodes = manifest["custom_nodes"]
    for index, item in enumerate(custom_nodes, start=1):
        event(3, f"更新加速節點 {index}/{len(custom_nodes)}：{item['target']}")
        install_custom_node(target, item)
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
            try:
                downloaded.resolve().relative_to(model_root.resolve())
                destination.resolve().relative_to(model_root.resolve())
            except ValueError as error:
                raise RuntimeError(f"模型下載位置超出模型資料夾：{destination.name}") from error
            destination.parent.mkdir(parents=True, exist_ok=True)
            downloaded.replace(destination)
        if not destination.is_file() or destination.stat().st_size != item["size"]:
            raise RuntimeError(f"模型檔案驗證失敗：{destination.name}")
    event(98, "驗證所有模型檔案")
    for item in files:
        path = model_root / Path(item["target"])
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"模型檔案未完成：{path.name}")
        if item.get("sha256") and sha256(path) != item["sha256"]:
            raise RuntimeError(f"模型檔案雜湊錯誤：{path.name}")
    event(100, f"模型版本 {manifest['version']} 更新完成")


if __name__ == "__main__":
    main()
