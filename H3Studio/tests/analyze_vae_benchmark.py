"""CPU-only comparison of paired FP16/INT8 VAE outputs exported as 8-bit PNGs.

Example (use an interpreter with numpy, Pillow and PyAV):
    python analyze_vae_benchmark.py --fp16-dir fp16 --int8-dir int8 --out-dir analysis

Pairs are formed by lexicographically sorted filenames in each directory; names
may have different prefixes. These metrics compare already quantized 8-bit PNGs,
not the original float VAE tensors. In particular, sub-1/255 differences cannot
be assessed reliably. The MP4 previews are lossy viewing aids, not metric inputs.
This script does not import torch, load a model, or use GPU codecs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont


LIMITATION = (
    "Metrics use exported 8-bit RGB PNG values normalized to [0,1], not original "
    "float VAE output. PNG export quantization obscures sub-1/255 differences. "
    "Differences are relative to FP16, not a reference-ground-truth quality score. "
    "MP4s are lossy CPU-encoded previews and are excluded from all metrics."
)


def rgb8(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode not in ("RGB", "RGBA"):
            raise ValueError(f"Expected 8-bit RGB/RGBA PNG: {path} ({image.mode})")
        if image.mode == "RGBA" and image.getchannel("A").getextrema() != (255, 255):
            raise ValueError(f"Nonopaque PNG cannot be compared as RGB: {path}")
        return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()


def luminance(rgb: np.ndarray) -> np.ndarray:
    # Rec.709 coefficients applied to exported nonlinear RGB code values; this
    # is a luma proxy, not a linear-light photometric measurement.
    return (rgb[..., 0].astype(np.float32) * (0.2126 / 255.0)
            + rgb[..., 1].astype(np.float32) * (0.7152 / 255.0)
            + rgb[..., 2].astype(np.float32) * (0.0722 / 255.0))


def psnr(mse: float) -> float | None:
    # JSON has no standards-compliant infinity. None means identical pixels.
    return -10.0 * math.log10(mse) if mse > 0 else None


def percentile_histogram(histogram: np.ndarray, quantile: float) -> float:
    # Nearest-rank percentile, explicitly not interpolated numpy.percentile.
    rank = max(1, math.ceil(int(histogram.sum()) * quantile))
    return float(np.searchsorted(np.cumsum(histogram), rank)) / 255.0


def get_ssim():
    try:
        from scipy.ndimage import gaussian_filter
    except ImportError:
        return None

    def ssim_luma(a: np.ndarray, b: np.ndarray) -> float | None:
        if min(a.shape) < 11:
            return None
        # Standard Gaussian-window SSIM on luma with population covariances,
        # data_range=1, 11x11 support, sigma=1.5; average over valid window centers.
        def blur(x):
            return gaussian_filter(x, sigma=1.5, truncate=3.5, mode="reflect")

        aa, bb = a.astype(np.float64), b.astype(np.float64)
        ma, mb = blur(aa), blur(bb)
        va = np.maximum(blur(aa * aa) - ma * ma, 0.0)
        vb = np.maximum(blur(bb * bb) - mb * mb, 0.0)
        cov = blur(aa * bb) - ma * mb
        score = ((2 * ma * mb + 0.01 ** 2) * (2 * cov + 0.03 ** 2)
                 / ((ma * ma + mb * mb + 0.01 ** 2) * (va + vb + 0.03 ** 2)))
        return float(score[5:-5, 5:-5].mean())

    return ssim_luma


class Preview:
    def __init__(self, path: Path, width: int, height: int, fps: int):
        import av
        self.av = av
        self.container = av.open(str(path), mode="w")
        self.stream = self.container.add_stream("libx264", rate=fps)
        self.stream.width = width + width % 2
        self.stream.height = height + height % 2
        self.stream.pix_fmt = "yuv420p"
        self.stream.options = {"crf": "18", "preset": "medium", "threads": "2"}

    def append(self, pixels: np.ndarray):
        if pixels.shape[0] % 2 or pixels.shape[1] % 2:
            pixels = np.pad(pixels, ((0, pixels.shape[0] % 2),
                                    (0, pixels.shape[1] % 2), (0, 0)), mode="edge")
        frame = self.av.VideoFrame.from_ndarray(pixels, format="rgb24")
        for packet in self.stream.encode(frame):
            self.container.mux(packet)

    def close(self):
        try:
            for packet in self.stream.encode():
                self.container.mux(packet)
        finally:
            self.container.close()


def contact_sheet(pairs, metrics, path: Path, gain: float, fps: int):
    count = len(pairs)
    worst = max(range(count), key=lambda i: metrics[i]["mae_normalized"])
    representatives = [0, (count - 1) // 2, count - 1]
    selected = [(i, label) for i, label in zip(representatives, ("First", "Middle", "Last"))]
    selected.append((worst, "Largest RGB MAE"))
    thumb_width = 440
    example = rgb8(pairs[0][0])
    thumb_height = max(1, round(example.shape[0] * thumb_width / example.shape[1]))
    margin, heading, footer, row_label = 18, 74, 56, 34
    sheet = Image.new("RGB", (thumb_width * 3 + margin * 4,
                             heading + (thumb_height + row_label + margin) * 4 + footer), "#141a24")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 17)
        small = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default(size=17)
        small = ImageFont.load_default(size=14)
    draw.text((margin, 12), "H3 VAE comparison | 8-bit exported PNGs | same latent", fill="white", font=font)
    for column, title in enumerate(("FP16 baseline", "INT8 ConvRot", f"Absolute RGB difference x{gain:g} (clipped)")):
        draw.text((margin + column * (thumb_width + margin), 43), title, fill="#b9cadf", font=small)
    for row, (index, label) in enumerate(selected):
        a, b = (rgb8(p) for p in pairs[index])
        diff = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * gain, 0, 255).astype(np.uint8)
        y = heading + row * (thumb_height + row_label + margin)
        record = metrics[index]
        value = "identical" if record["psnr_db"] is None else f"{record['psnr_db']:.2f} dB"
        draw.text((margin, y), f"{label}: frame {index + 1}/{count} ({index / fps:.2f}s) | PSNR {value} | MAE {record['mae_normalized']:.6f}", fill="white", font=small)
        for column, pixels in enumerate((a, b, diff)):
            thumb = Image.fromarray(pixels).resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (margin + column * (thumb_width + margin), y + row_label))
    draw.text((margin, sheet.height - 45), "Differences below one 8-bit step cannot be reliably assessed. Difference image is amplified for visibility.", fill="#b9cadf", font=small)
    draw.text((margin, sheet.height - 24), "Representative rows may repeat the largest-difference frame. PSNR/MAE use original PNG size, not thumbnails.", fill="#b9cadf", font=small)
    sheet.save(path)
    return {"representative_frame_indices_0_based": representatives,
            "largest_mae_frame_index_0_based": worst, "difference_gain": gain}


def analyze(fp16_dir: Path, int8_dir: Path, out_dir: Path, fps=24, difference_gain=8.0):
    fp16_dir, int8_dir, out_dir = (p.resolve() for p in (fp16_dir, int8_dir, out_dir))
    if out_dir in (fp16_dir, int8_dir) or fp16_dir == int8_dir:
        raise ValueError("Input directories must differ and output must not equal either input")
    files_a, files_b = (sorted(p.glob("*.png"), key=lambda q: q.name) for p in (fp16_dir, int8_dir))
    if not files_a or len(files_a) != len(files_b):
        raise ValueError(f"Need equal nonzero PNG counts, got {len(files_a)} and {len(files_b)}")
    if fps <= 0 or difference_gain <= 0:
        raise ValueError("fps and difference gain must be positive")
    pairs = list(zip(files_a, files_b))
    out_dir.mkdir(parents=True, exist_ok=True)
    ssim = get_ssim()
    records, histogram = [], np.zeros(256, dtype=np.int64)
    previous_a = previous_b = None
    previews = []
    shape = None
    sum_abs, sum_squared, total_values = 0, 0.0, 0
    try:
        for index, (path_a, path_b) in enumerate(pairs):
            a, b = rgb8(path_a), rgb8(path_b)
            if a.shape != b.shape or (shape is not None and a.shape != shape):
                raise ValueError(f"Frame dimensions differ at pair {index}: {a.shape}, {b.shape}; expected {shape}")
            if shape is None:
                shape = a.shape
                for name in ("fp16_preview.mp4", "int8_preview.mp4"):
                    previews.append(Preview(out_dir / name, shape[1], shape[0], fps))
            difference = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)
            frame_hist = np.bincount(difference.ravel(), minlength=256)
            histogram += frame_hist
            # Small histograms give exact aggregate 8-bit MAE/MSE without full
            # float RGB copies or retaining the full video in memory.
            levels = np.arange(256, dtype=np.float64)
            absolute = int(np.dot(frame_hist, levels))
            squared = float(np.dot(frame_hist, levels * levels))
            mse = squared / difference.size / 255.0 ** 2
            ya, yb = luminance(a), luminance(b)
            record = {
                "frame_index_0_based": index, "time_seconds": index / fps,
                "fp16_filename": path_a.name, "int8_filename": path_b.name,
                "mae_normalized": absolute / difference.size / 255.0,
                "mse_normalized": mse, "psnr_db": psnr(mse),
                "identical_rgb_pixels": bool(frame_hist[0] == difference.size),
                "p99_absdiff_normalized_nearest_rank": percentile_histogram(frame_hist, 0.99),
                "max_absdiff_normalized": float(difference.max()) / 255.0,
                "changed_channel_fraction": 1.0 - int(frame_hist[0]) / difference.size,
                "fp16_mean_luma": float(ya.mean()), "int8_mean_luma": float(yb.mean()),
                "int8_minus_fp16_mean_luma": float((yb - ya).mean()),
            }
            if ssim is not None:
                record["ssim_luma_gaussian11_sigma1_5"] = ssim(ya, yb)
            if previous_a is not None:
                delta_a, delta_b = ya - previous_a, yb - previous_b
                record["temporal"] = {
                    "fp16_adjacent_luma_mae": float(np.abs(delta_a).mean()),
                    "int8_adjacent_luma_mae": float(np.abs(delta_b).mean()),
                    "difference_of_adjacent_luma_changes_mae": float(np.abs(delta_b - delta_a).mean()),
                    "fp16_mean_luma_change": float(delta_a.mean()),
                    "int8_mean_luma_change": float(delta_b.mean()),
                    "mean_luma_error_change": float((delta_b - delta_a).mean()),
                }
            previous_a, previous_b = ya, yb
            records.append(record)
            sum_abs += absolute
            sum_squared += squared
            total_values += difference.size
            previews[0].append(a)
            previews[1].append(b)
    finally:
        # Flush both previews even if one encoder fails to close.
        errors = []
        for preview in previews:
            try:
                preview.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
    mse = sum_squared / total_values / 255.0 ** 2
    summary = {
        "global_mae_normalized": sum_abs / total_values / 255.0,
        "global_mse_normalized": mse, "global_psnr_db": psnr(mse),
        "global_p99_absdiff_normalized_nearest_rank": percentile_histogram(histogram, 0.99),
        "global_changed_channel_fraction": 1.0 - int(histogram[0]) / total_values,
        "max_frame_mae_normalized": max(r["mae_normalized"] for r in records),
    }
    if ssim is not None:
        scores = [r["ssim_luma_gaussian11_sigma1_5"] for r in records if r["ssim_luma_gaussian11_sigma1_5"] is not None]
        summary["mean_frame_ssim_luma_gaussian11_sigma1_5"] = float(np.mean(scores)) if scores else None
    temporal = [r["temporal"] for r in records if "temporal" in r]
    if temporal:
        summary["mean_temporal"] = {key: float(np.mean([r[key] for r in temporal])) for key in temporal[0]}
    sheet_info = contact_sheet(pairs, records, out_dir / "comparison_contact_sheet.png", difference_gain, fps)
    result = {
        "schema_version": 1, "comparison_domain": "8-bit RGB PNG normalized to [0,1]",
        "limitations": LIMITATION,
        "pairing": "lexicographic filename order independently in each directory; inspect per-frame names",
        "psnr_null_means": "zero MSE / identical exported RGB values (infinite PSNR)",
        "ssim_method": ("luma proxy, Gaussian 11x11 sigma1.5, K1=.01 K2=.03, population covariance, valid centers"
                        if ssim is not None else "not computed; SciPy is unavailable"),
        "temporal_method": "Adjacent-frame Rec.709-weighted nonlinear RGB luma differences; no optical-flow compensation; these are change proxies, not perceptual flicker scores",
        "fp16_dir": str(fp16_dir), "int8_dir": str(int8_dir),
        "frame_count": len(records), "width": shape[1], "height": shape[0],
        "fps": fps, "preview_duration_seconds": len(records) / fps,
        "preview_codec": "CPU libx264 CRF18 yuv420p; odd dimensions edge-padded to even",
        "summary": summary, "contact_sheet": sheet_info, "frames": records,
    }
    (out_dir / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return result


def self_test():
    with tempfile.TemporaryDirectory(prefix="h3-vae-analysis-") as folder:
        root = Path(folder)
        a_dir, b_dir = root / "a", root / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        for index in range(3):
            a = np.full((16, 24, 3), 60 + index * 5, dtype=np.uint8)
            b = a.copy()
            b[..., 0] += index
            Image.fromarray(a).save(a_dir / f"fp16_{index:03}.png")
            Image.fromarray(b).save(b_dir / f"int8_{index:03}.png")
        result = analyze(a_dir, b_dir, root / "out", fps=24)
        assert result["frame_count"] == 3
        assert result["frames"][0]["psnr_db"] is None
        assert abs(result["summary"]["global_mae_normalized"] - 1 / (3 * 255)) < 1e-12
        assert abs(result["summary"]["global_mse_normalized"] - 5 / (9 * 255 ** 2)) < 1e-12
        assert abs(result["frames"][2]["p99_absdiff_normalized_nearest_rank"] - 2 / 255) < 1e-12
        assert result["contact_sheet"]["largest_mae_frame_index_0_based"] == 2
        if "mean_frame_ssim_luma_gaussian11_sigma1_5" in result["summary"]:
            assert result["frames"][0]["ssim_luma_gaussian11_sigma1_5"] == 1.0
        for name in ("metrics.json", "comparison_contact_sheet.png", "fp16_preview.mp4", "int8_preview.mp4"):
            assert (root / "out" / name).stat().st_size > 0
        import av
        with av.open(str(root / "out" / "int8_preview.mp4")) as video:
            assert sum(1 for _ in video.decode(video=0)) == 3
        print(json.dumps({"self_test": "passed", "summary": result["summary"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fp16-dir", type=Path)
    parser.add_argument("--int8-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--difference-gain", type=float, default=8.0)
    parser.add_argument("--self-test", action="store_true", help="Tiny synthetic CPU-only test in a temporary directory")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if None in (args.fp16_dir, args.int8_dir, args.out_dir):
        parser.error("--fp16-dir, --int8-dir and --out-dir are required unless --self-test is used")
    result = analyze(args.fp16_dir, args.int8_dir, args.out_dir, args.fps, args.difference_gain)
    print(json.dumps({"output_dir": str(args.out_dir.resolve()), "summary": result["summary"]}, indent=2))


if __name__ == "__main__":
    main()
