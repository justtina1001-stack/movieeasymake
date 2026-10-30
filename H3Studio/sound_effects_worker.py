"""One-shot, offline MOSS SFX inference. The Studio owns the shared GPU lease."""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path
import sys
import time

def configure_environment() -> None:
    # Set before importing the official package (which has torch.compile
    # decorators), only inside the isolated worker, never at library import.
    os.environ.update({"TORCHDYNAMO_DISABLE": "1", "HF_HUB_OFFLINE": "1",
                       "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def validate_request(request: dict) -> dict:
    if not isinstance(request, dict):
        raise ValueError("音效設定格式錯誤。")
    prompt = str(request.get("prompt") or "").strip()
    if not prompt or len(prompt) > 4000:
        raise ValueError("音效敘述需有 1 至 4,000 個字元。")
    seconds = float(request.get("seconds", 5))
    if not math.isfinite(seconds) or not 1 <= seconds <= 30:
        raise ValueError("音效長度請設定為 1 至 30 秒。")
    # The official wrapper rounds duration to tenths; make that contract explicit.
    seconds = round(seconds, 1)
    seed = int(request.get("seed", 0))
    if not 0 <= seed <= 2**64 - 1:
        raise ValueError("音效 Seed 超出可用範圍。")
    steps = int(request.get("steps", 100))
    if not 20 <= steps <= 100:
        raise ValueError("音效採樣步數需為 20 至 100。")
    model_dir = Path(str(request.get("model_dir") or ""))
    if not (model_dir / "model_index.json").is_file():
        raise ValueError("音效模型尚未安裝完成。")
    return {"prompt": prompt, "seconds": seconds, "seed": seed, "steps": steps, "model_dir": model_dir}


def waveform_quality(audio) -> dict:
    """Objective diagnostics; this is not an assessment of semantic quality."""
    import numpy as np

    values = np.asarray(audio, dtype=np.float32)
    if not values.size:
        raise ValueError("音效模型回傳空白音訊。")
    finite = bool(np.isfinite(values).all())
    if not finite:
        raise ValueError("音效模型回傳無效音訊，未儲存檔案。")
    rms = float(np.sqrt(np.mean(values.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(values)))
    return {"finite": finite, "rms": rms, "peak_abs": peak,
            "clipped_fraction": float(np.mean(np.abs(values) >= 1.0)), "near_silent": rms < 1e-5}


def sampler_progress(timesteps):
    total = len(timesteps)
    for index, timestep in enumerate(timesteps):
        emit({"progress": round(10 + 80 * index / max(1, total), 1), "stage": "sampling",
              "step": index, "steps": total})
        yield timestep
    emit({"progress": 92, "stage": "decoding", "step": total, "steps": total})


def generate(request_path: Path, output_path: Path, metrics_path: Path) -> dict:
    started = time.perf_counter()
    request = validate_request(json.loads(request_path.read_text(encoding="utf-8")))
    configure_environment()
    emit({"progress": 1, "stage": "loading", "message": "正在載入 MOSS 音效模型"})
    import numpy as np
    import soundfile as sf
    import torch
    from moss_soundeffect_v2 import MossSoundEffectPipeline

    if not torch.cuda.is_available():
        raise RuntimeError("MOSS 音效目前需要可用的 NVIDIA CUDA 顯示卡；未切換成較慢的 CPU 生成。")
    torch.cuda.set_device(0)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    pipe = None
    try:
        load_started = time.perf_counter()
        # Load on CPU and use the upstream pipeline's staged offload support.
        # Avoid a transient upload of all three models to a 16 GB device.
        pipe = MossSoundEffectPipeline.from_pretrained(str(request["model_dir"]),
                                                       torch_dtype=torch.bfloat16, device="cpu")
        pipe.engine.enable_cpu_offload()
        pipe.engine.device = torch.device("cuda")
        torch.cuda.synchronize()
        load_seconds = time.perf_counter() - load_started
        emit({"progress": 9, "stage": "encoding", "message": "正在整理音效敘述"})
        inference_started = time.perf_counter()
        with torch.inference_mode():
            audio = pipe(prompt=request["prompt"], seconds=request["seconds"],
                         num_inference_steps=request["steps"], cfg_scale=4.0, sigma_shift=5.0,
                         seed=request["seed"], progress_bar_cmd=sampler_progress)
        torch.cuda.synchronize()
        inference_seconds = time.perf_counter() - inference_started
        if audio.ndim != 3 or audio.shape[0] != 1 or audio.shape[1] != 1:
            raise RuntimeError("MOSS 音效回傳的音軌格式不正確。")
        waveform = audio[0].detach().float().cpu().numpy().T
        # Match upstream's int(sr * seconds) crop, including a possible
        # single-sample float rounding difference for durations such as 2.3s.
        if waveform.shape[0] != int(request["seconds"] * 48000):
            raise RuntimeError("MOSS 音效回傳長度與設定不一致。")
        quality = waveform_quality(waveform)
        if quality["near_silent"]:
            raise RuntimeError("MOSS 產生的音效接近無聲，請調整敘述後重試。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Float WAV retains the generated waveform without hidden normalization
        # or PCM clipping. The editor and browser decode ordinary IEEE-float WAV.
        sf.write(str(output_path), waveform.astype(np.float32), 48000, subtype="FLOAT")
        metrics = {"elapsed_seconds": round(time.perf_counter() - started, 3),
                   "load_seconds": round(load_seconds, 3), "inference_seconds": round(inference_seconds, 3),
                   "peak_vram_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                   "peak_vram_reserved_bytes": int(torch.cuda.max_memory_reserved()),
                   "sample_rate": 48000, "duration_seconds": waveform.shape[0] / 48000,
                   "channels": 1, "frames": int(waveform.shape[0]), "quality": quality,
                   "device": torch.cuda.get_device_name(0), "torch_version": torch.__version__,
                   "compile_enabled": False, "cpu_offload": True,
                   "steps": request["steps"], "seed": request["seed"],
                   "latent_duration_seconds": 30, "format": "wav", "subtype": "FLOAT"}
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        emit({"progress": 100, "stage": "completed", "output": str(output_path), "performance": metrics})
        return metrics
    finally:
        del pipe
        gc.collect()
        torch.cuda.empty_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="MOSS-SoundEffect v2.0 offline worker")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("generate")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--metrics", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.request, args.output, args.metrics)
    except Exception as error:
        emit({"stage": "failed", "error": str(error), "exception_type": type(error).__name__})
        raise


if __name__ == "__main__":
    main()
