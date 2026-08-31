from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from huggingface_hub import hf_hub_download

from engine_installer import (
    ALL_MODEL_FILES,
    COMFY_REPO,
    COMFY_REVISION,
    MODEL_MANIFEST_CUSTOM_NODES,
    MODEL_MANIFEST_FILES,
)
from model_update_worker import install_custom_node, sha256
from runtime_env import check_python_executable


OFFICIAL_REPO = "MiniMaxAI/MiniMax-H3"
OFFICIAL_REVISION = "b8b09e34f8d2b9d1b7a51982ccb26ae2b8b9ef08"


def event(progress: int, step: str, **extra: object) -> None:
    print("H3_INSTALL_EVENT " + json.dumps({"progress": progress, "step": step, **extra}, ensure_ascii=False), flush=True)


def run(command: list[str], cwd: Path | None = None) -> None:
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"指令失敗（{completed.returncode}）：{command[0]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target = Path(args.target).resolve()
    if args.dry_run:
        event(5, "乾跑：ComfyUI 程式")
        event(20, "乾跑：CUDA 13.0 PyTorch 與相依套件")
        for item in MODEL_MANIFEST_CUSTOM_NODES:
            event(25, f"乾跑：加速節點 {item['target']}")
        for index, (relative, _) in enumerate(ALL_MODEL_FILES, start=1):
            event(20 + round(index / len(ALL_MODEL_FILES) * 70), f"乾跑：模型 {index}/{len(ALL_MODEL_FILES)}", current_file=Path(relative).name)
        event(100, "乾跑完成")
        return
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_comfy = (target / "main.py").is_file()
    event(3, "檢查 ComfyUI 程式")
    if not existing_comfy:
        if target.exists() and any(target.iterdir()):
            raise RuntimeError("目標資料夾不是空的。")
        run(["git", "clone", COMFY_REPO, str(target)])
        if (target / ".git").is_dir():
            run(["git", "fetch", "origin", COMFY_REVISION, "--depth", "1"], cwd=target)
            run(["git", "checkout", "--detach", COMFY_REVISION], cwd=target)
    else:
        event(7, "保留已複製的 ComfyUI 程式與版本")

    event(10, "建立 ComfyUI Python 環境")
    python = target / ".venv" / "Scripts" / "python.exe"
    environment_ready, _ = check_python_executable(python, required_imports=("torch",))
    if not environment_ready:
        event(11, "修復從其他電腦搬移的 Python 環境")
        command = [sys.executable, "-m", "venv"]
        if (target / ".venv").exists():
            command.append("--clear")
        command.append(str(target / ".venv"))
        run(command)

    event(14, "更新安裝工具")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    event(18, "安裝 NVIDIA CUDA 13.0 PyTorch")
    run([
        str(python), "-m", "pip", "install", "torch", "torchvision", "torchaudio",
        "--extra-index-url", "https://download.pytorch.org/whl/cu130",
    ])
    event(28, "安裝 ComfyUI 相依套件")
    run([str(python), "-m", "pip", "install", "-r", str(target / "requirements.txt")])

    for index, item in enumerate(MODEL_MANIFEST_CUSTOM_NODES, start=1):
        event(31, f"安裝加速節點 {index}/{len(MODEL_MANIFEST_CUSTOM_NODES)}：{item['target']}")
        install_custom_node(target, item)

    model_root = target / "models"
    start_progress = 35
    progress_span = 58
    for index, item in enumerate(MODEL_MANIFEST_FILES):
        destination = model_root / Path(item["target"])
        progress = start_progress + round(index / len(MODEL_MANIFEST_FILES) * progress_span)
        event(progress, f"下載模型 {index + 1}/{len(MODEL_MANIFEST_FILES)}：{destination.name}", current_file=destination.name)
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
            downloaded = destination
        if downloaded.stat().st_size != item["size"]:
            raise RuntimeError(f"模型檔案大小不符：{destination.name}")

    event(94, "下載 MiniMax H3 授權文件")
    docs_dir = model_root / "MiniMax-H3-docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    hf_hub_download(
        repo_id=OFFICIAL_REPO,
        filename="LICENSE",
        revision=OFFICIAL_REVISION,
        local_dir=docs_dir,
    )
    (docs_dir / "NOTICE").write_text(
        "MiniMax H3 is licensed under the MiniMax H3 Community License Agreement, "
        "Copyright © 2026 MiniMax. All Rights Reserved.\n",
        encoding="utf-8",
    )

    event(97, "驗證本機引擎")
    run([
        str(python), "-c",
        "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__, torch.cuda.get_device_name(0))",
    ], cwd=target)
    for relative, expected_size in ALL_MODEL_FILES:
        path = model_root / Path(relative)
        if not path.is_file() or path.stat().st_size != expected_size:
            raise RuntimeError(f"缺少模型：{path.name}")
    for item in MODEL_MANIFEST_FILES:
        if item.get("sha256"):
            path = model_root / Path(item["target"])
            if sha256(path) != item["sha256"]:
                raise RuntimeError(f"模型檔案雜湊錯誤：{path.name}")
    event(100, "本機引擎安裝完成")


if __name__ == "__main__":
    main()
