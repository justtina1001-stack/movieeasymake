from __future__ import annotations

import argparse
import json
from pathlib import Path


def probe() -> None:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            f"語音環境目前是 {torch.__version__}，找不到 CUDA；"
            "請使用 H3 Studio 安裝器重新安裝 CUDA 版 PyTorch。"
        )
    from qwen_tts import Qwen3TTSModel  # noqa: F401

    print(json.dumps({
        "cuda": True,
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "cuda_version": torch.version.cuda,
    }, ensure_ascii=False))


def download(repo: str, model_dir: Path) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo, local_dir=str(model_dir))
    print(json.dumps({"repo": repo, "model_dir": str(model_dir)}, ensure_ascii=False))


def generate(request_path: Path, output_path: Path) -> None:
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not torch.cuda.is_available():
        raise RuntimeError("找不到可用的 NVIDIA CUDA GPU。")
    torch.manual_seed(int(request["seed"]))
    torch.cuda.manual_seed_all(int(request["seed"]))
    model = Qwen3TTSModel.from_pretrained(
        request["model_dir"],
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )
    mode = request["mode"]
    common = {"text": request["text"], "language": request["language"]}
    if mode == "custom":
        wavs, sample_rate = model.generate_custom_voice(
            **common,
            speaker=request["speaker"],
        )
    elif mode == "design":
        wavs, sample_rate = model.generate_voice_design(
            **common,
            instruct=request["instruct"],
        )
    elif mode == "clone":
        wavs, sample_rate = model.generate_voice_clone(
            **common,
            ref_audio=request["reference_audio"],
            ref_text=request.get("reference_text") or None,
            x_vector_only_mode=bool(request.get("x_vector_only")),
        )
    else:
        raise ValueError(f"Unknown voice mode: {mode}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), wavs[0], sample_rate, subtype="PCM_16")
    print(json.dumps({"output": str(output_path), "sample_rate": sample_rate}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("probe")
    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--repo", required=True)
    download_parser.add_argument("--model-dir", type=Path, required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--request", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "probe":
        probe()
    elif args.command == "download":
        download(args.repo, args.model_dir)
    else:
        generate(args.request, args.output)


if __name__ == "__main__":
    main()
