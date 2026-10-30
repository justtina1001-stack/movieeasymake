"""Install pinned MOSS SFX in its own environment without allocating GPU memory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv
import zipfile

APP_DIR = Path(__file__).resolve().parent
MODEL_REPO = "OpenMOSS-Team/MOSS-SoundEffect-v2.0"
MODEL_REVISION = "e35df4d82fbe87fcd5d14e5d100e349c0c3c076d"
SOURCE_REVISION = "934d6826b084c46a0d033402174d5f8ac4ed2519"
SOURCE_ARCHIVE_SHA256 = "a0d3c10d24161eb7283dc5d88c10a164ca42c0e8d21e943a126403c18a598f09"
SOURCE_URL = f"https://github.com/OpenMOSS/MOSS-TTS/archive/{SOURCE_REVISION}.zip"
TORCH_VERSION = "2.9.0+cu128"
VERSION = "moss-soundeffect-v2.0-20261030"
MODEL_FILES = {
    "model_index.json": (504, None),
    "scheduler/scheduler_config.json": (134, None),
    "text_encoder/config.json": (726, None),
    "text_encoder/generation_config.json": (239, None),
    "text_encoder/model-00001-of-00002.safetensors": (3441185608, "169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5"),
    "text_encoder/model-00002-of-00002.safetensors": (622329984, "912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9"),
    "text_encoder/model.safetensors.index.json": (25605, None),
    "tokenizer/merges.txt": (1671853, None),
    "tokenizer/tokenizer.json": (11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    "tokenizer/tokenizer_config.json": (9732, None),
    "tokenizer/vocab.json": (2776833, None),
    "transformer/config.json": (311, None),
    "transformer/diffusion_pytorch_model.safetensors": (5664274560, "bda86e4d6730ff47cea47851d219880191e9d21d4d264b21a6ffe7cb2db9dfb0"),
    "vae/config.json": (169, None),
    "vae/vae_128d_48k.pth": (1486465965, "07e6139ff33bd21ba8a7a7f40ed24aab13ac0d100cd686f8a9f3e03dc5251cb1"),
}


def runtime_paths(data_dir: Path | None = None) -> dict[str, Path]:
    root = Path(data_dir) / "sfx_runtime" if data_dir is not None else APP_DIR / "data" / "sfx_runtime"
    return {
        "root": root,
        "python": root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
        "model": root / "models" / "moss-soundeffect-v2.0",
        "worker": APP_DIR / "sound_effects_worker.py",
        "source": root / "source" / f"MOSS-TTS-{SOURCE_REVISION}" / "moss_soundeffect_v2",
        "manifest": root / "installation.json",
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def probe_installation(data_dir: Path | None = None) -> dict:
    paths = runtime_paths(data_dir)
    manifest = _read_json(paths["manifest"])
    pinned = (manifest.get("version") == VERSION and manifest.get("model_revision") == MODEL_REVISION
              and manifest.get("source_revision") == SOURCE_REVISION and manifest.get("verified") is True)
    model_installed = all((paths["model"] / name).is_file()
                          and (paths["model"] / name).stat().st_size == size
                          for name, (size, _) in MODEL_FILES.items())
    runtime_installed = bool(pinned and paths["python"].is_file()
                             and (paths["source"] / "pipeline_moss_soundeffect.py").is_file())
    installed = bool(runtime_installed and model_installed and paths["worker"].is_file())
    return {"installed": installed, "runtime_installed": runtime_installed, "model_installed": model_installed,
            "version": VERSION, "model_repo": MODEL_REPO, "model_revision": MODEL_REVISION,
            "source_revision": SOURCE_REVISION, "paths": {key: str(value) for key, value in paths.items()},
            "error": None if installed else "請在 GPU 主機執行 setup_sound_effects.bat 安裝 MOSS 音效模型。"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(model_dir: Path, *, full: bool = True) -> dict:
    hashes = {}
    for name, (size, expected) in MODEL_FILES.items():
        path = model_dir / name
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeError(f"MOSS 模型檔案不完整：{name}")
        if full:
            digest = sha256(path)
            if expected and digest != expected:
                raise RuntimeError(f"MOSS 模型驗證失敗：{name}")
            hashes[name] = digest
    return hashes


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True,
                   env={**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_XET": "1",
                        "TORCHDYNAMO_DISABLE": "1"})


def install_source(paths: dict[str, Path]) -> None:
    archive = paths["root"] / f"moss-tts-{SOURCE_REVISION[:8]}.zip"
    if not archive.is_file() or sha256(archive) != SOURCE_ARCHIVE_SHA256:
        urllib.request.urlretrieve(SOURCE_URL, archive)
    if sha256(archive) != SOURCE_ARCHIVE_SHA256:
        raise RuntimeError("MOSS 官方程式下載驗證失敗。")
    destination = paths["root"] / "source"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError("MOSS 程式封裝含無效路徑。")
        bundle.extractall(destination)


def finish_installation(paths: dict[str, Path]) -> dict:
    hashes = verify_model(paths["model"])
    # Import only: no CUDA availability probe or model loading during setup.
    _run([str(paths["python"]), "-c", "import torch, soundfile; from moss_soundeffect_v2 import MossSoundEffectPipeline; print('MOSS runtime imports ready')"])
    manifest = {"version": VERSION, "model_repo": MODEL_REPO, "model_revision": MODEL_REVISION,
                "source_revision": SOURCE_REVISION, "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
                "torch": TORCH_VERSION, "verified": True, "model_sha256": hashes,
                "license": "Apache-2.0", "compile_enabled": False}
    paths["manifest"].write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def install(data_dir: Path | None = None, *, finalize_only: bool = False) -> dict:
    paths = runtime_paths(data_dir)
    paths["root"].mkdir(parents=True, exist_ok=True)
    if not finalize_only:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("MOSS 獨立環境需要 Python 3.12，請使用 H3 Studio 的 Python 執行安裝。")
        if not paths["python"].is_file():
            venv.EnvBuilder(with_pip=True).create(paths["root"] / ".venv")
        install_source(paths)
        _run([str(paths["python"]), "-m", "pip", "install", "--upgrade", "pip"])
        _run([str(paths["python"]), "-m", "pip", "install", f"torch=={TORCH_VERSION}",
              "torchaudio==2.9.0+cu128", "torchvision==0.24.0+cu128", "--index-url", "https://download.pytorch.org/whl/cu128"])
        _run([str(paths["python"]), "-m", "pip", "install", "-r", str(APP_DIR / "sound_effects_requirements.txt")])
        _run([str(paths["python"]), "-m", "pip", "install", "--no-deps", "-e", str(paths["source"])])
        download_code = ("from huggingface_hub import snapshot_download; "
                         f"snapshot_download(repo_id={MODEL_REPO!r}, revision={MODEL_REVISION!r}, "
                         f"local_dir={str(paths['model'])!r}, max_workers=3)")
        _run([str(paths["python"]), "-c", download_code])
    manifest = finish_installation(paths)
    return {**probe_installation(data_dir), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="安裝獨立 MOSS 音效環境；不載入 GPU 模型。")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--finalize-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    result = probe_installation(args.data_dir) if args.status else install(args.data_dir, finalize_only=args.finalize_only)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
