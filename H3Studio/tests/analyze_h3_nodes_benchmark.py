"""CPU-only comparison of complete H3 custom-node benchmark PNG sequences.

    python analyze_h3_nodes_benchmark.py --directory h3_nodes_20260929
    python analyze_h3_nodes_benchmark.py --self-test

A directory name resolves beneath H3Studio/data/diagnostics; an existing path
can also be supplied. Both baseline/results/benchmark.json and candidate's
matching completion record are required before any analysis is written.
Inputs are baseline/output/quality/<case>/frame*.png and candidate equivalents.
This module imports no torch, model code, CUDA runtime, or GPU video encoder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from analyze_vae_benchmark import (
    Preview, get_ssim, luminance, percentile_histogram, psnr, rgb8,
)


DEFAULT_CASES = (
    "r2v_trial0_seed290901", "r2v_trial1_seed290902",
    "r2v_trial2_seed290903", "fl2va_trial0_seed290901",
)
LIMITATIONS = (
    "Relative comparison of baseline and candidate exported 8-bit RGB PNGs; "
    "these are not ground-truth or absolute perceptual quality scores. PNG "
    "quantization hides differences below 1/255. Finite checks apply only to "
    "decoded uint8 PNG values and CANNOT establish whether pre-export float "
    "tensors contained NaN/Inf. Pixel bitwise equality is distinct from PNG "
    "file-byte equality (metadata/compression can differ). MP4 is a lossy CPU "
    "viewing aid; all metrics use original-size PNGs. No audio comparison."
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def numpy_ssim(a: np.ndarray, b: np.ndarray) -> float | None:
    """Same valid Gaussian-window luma SSIM as the optional SciPy implementation."""
    if min(a.shape) < 11:
        return None
    if np.array_equal(a, b):
        return 1.0
    positions = np.arange(-5, 6, dtype=np.float64)
    kernel = np.exp(-(positions * positions) / (2 * 1.5 ** 2))
    kernel /= kernel.sum()

    def blur(value):
        result = value
        for axis in (0, 1):
            padding = [(0, 0), (0, 0)]
            padding[axis] = (5, 5)
            padded = np.pad(result, padding, mode="symmetric")
            filtered = np.zeros_like(result)
            for offset, weight in enumerate(kernel):
                window = [slice(None), slice(None)]
                window[axis] = slice(offset, offset + result.shape[axis])
                filtered += padded[tuple(window)] * weight
            result = filtered
        return result

    aa, bb = a.astype(np.float64), b.astype(np.float64)
    ma, mb = blur(aa), blur(bb)
    va = np.maximum(blur(aa * aa) - ma * ma, 0.0)
    vb = np.maximum(blur(bb * bb) - mb * mb, 0.0)
    covariance = blur(aa * bb) - ma * mb
    score = ((2 * ma * mb + 0.01 ** 2) * (2 * covariance + 0.03 ** 2)
             / ((ma * ma + mb * mb + 0.01 ** 2) * (va + vb + 0.03 ** 2)))
    return float(score[5:-5, 5:-5].mean())


def frame_number(path: Path) -> int:
    numbers = re.findall(r"\d+", path.stem)
    if len(numbers) != 1:
        raise ValueError(f"Expected one numeric frame index in {path.name}")
    return int(numbers[0])


def font(size=16):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def validated_inputs(directory, cases, expected_frames, expected_width, expected_height):
    """Preflight all selected cases; never silently compare partial generation."""
    prepared, completion = {}, {}
    if not cases or len(set(cases)) != len(cases):
        raise ValueError("Cases must be nonempty and unique")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", case) for case in cases):
        raise ValueError("Case names must contain only letters, digits, underscore or hyphen")
    for side in ("baseline", "candidate"):
        marker = directory / side / "results" / "benchmark.json"
        state = json.loads(marker.read_text(encoding="utf-8"))
        if not state.get("completed_at"):
            raise ValueError(f"{side}: benchmark is not marked completed")
        runs = state.get("runs", [])
        completion[side] = {"path": str(marker), "sha256": digest(marker),
                            "completed_at": state["completed_at"]}
        for case in cases:
            matches = [run for run in runs if run.get("label") == case]
            if (len(matches) != 1 or matches[0].get("result_event") != "execution_success"
                    or matches[0].get("timing_valid") is not True
                    or matches[0].get("precise_ws_timing") is False):
                raise ValueError(f"{side}/{case}: need exactly one successful, timing-valid run")
            folder = directory / side / "output" / "quality" / case
            files = sorted(folder.glob("frame*.png"), key=frame_number)
            if len(files) != expected_frames:
                raise ValueError(f"{side}/{case}: expected {expected_frames} frames, found {len(files)}")
            numbers = [frame_number(path) for path in files]
            if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
                raise ValueError(f"{side}/{case}: frame indices have gaps or duplicates")
            for path in files:
                with Image.open(path) as image:
                    if (image.format != "PNG" or image.mode not in ("RGB", "RGBA")
                            or image.size != (expected_width, expected_height)):
                        raise ValueError(f"{side}/{case}/{path.name}: expected {expected_width}x{expected_height} RGB/RGBA PNG")
                    has_alpha = image.mode == "RGBA"
                    image.verify()
                if has_alpha:
                    with Image.open(path) as image:
                        if image.getchannel("A").getextrema() != (255, 255):
                            raise ValueError(f"{side}/{case}/{path.name}: nonopaque alpha cannot be ignored")
            prepared.setdefault(case, {})[side] = files
    for case, files in prepared.items():
        if [frame_number(p) for p in files["baseline"]] != [frame_number(p) for p in files["candidate"]]:
            raise ValueError(f"{case}: baseline/candidate numeric frame indices differ")
    return prepared, completion


def contact_sheet(case, pairs, records, path, gain, fps, labels):
    worst = max(range(len(records)), key=lambda i: records[i]["mae_normalized"])
    selected = [(0, "First"), ((len(records) - 1) // 2, "Middle"),
                (len(records) - 1, "Last"), (worst, "Largest RGB MAE")]
    example = rgb8(pairs[0][0])
    width = 400
    height = max(1, round(example.shape[0] * width / example.shape[1]))
    margin, heading, caption = 16, 76, 30
    sheet = Image.new("RGB", (width * 3 + margin * 4,
                             heading + (height + caption + margin) * 4 + 44), "#141a24")
    draw = ImageDraw.Draw(sheet)
    small = font(14)
    draw.text((margin, 12), f"H3 node comparison | {case} | exported 8-bit PNGs", fill="white", font=font())
    for column, title in enumerate((labels[0], labels[1], f"Absolute RGB difference x{gain:g} (clipped)")):
        draw.text((margin + column * (width + margin), 44), title, fill="#b9cadf", font=small)
    for row, (index, title) in enumerate(selected):
        a, b = (rgb8(path) for path in pairs[index])
        difference = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * gain, 0, 255).astype(np.uint8)
        y = heading + row * (height + caption + margin)
        record = records[index]
        value = "identical" if record["psnr_db"] is None else f"{record['psnr_db']:.2f} dB"
        draw.text((margin, y), f"{title}: frame {index + 1}/{len(records)} ({index / fps:.2f}s) | PSNR {value} | MAE {record['mae_normalized']:.6f}", fill="white", font=small)
        for column, pixels in enumerate((a, b, difference)):
            thumb = Image.fromarray(pixels).resize((width, height), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (margin + column * (width + margin), y + caption))
    draw.text((margin, sheet.height - 35), "Relative differences, not ground-truth quality. Metrics use full-size PNGs; rows may repeat frames.", fill="#b9cadf", font=small)
    sheet.save(path)
    return {"selected_frame_indices_0_based": [index for index, _ in selected],
            "largest_mae_frame_index_0_based": worst, "difference_gain": gain}


def analyze_case(case, inputs, output, completion, fps, difference_gain, labels):
    output.mkdir(parents=True, exist_ok=True)
    pairs = list(zip(inputs["baseline"], inputs["candidate"]))
    ssim = get_ssim() or numpy_ssim
    records, histogram = [], np.zeros(256, dtype=np.int64)
    levels = np.arange(256, dtype=np.float64)
    preview = None
    temporary_preview = output / "comparison_side_by_side.partial.mp4"
    preview_path = output / "comparison_side_by_side.mp4"
    try:
        for index, (path_a, path_b) in enumerate(pairs):
            a, b = rgb8(path_a), rgb8(path_b)
            if a.shape != b.shape:
                raise ValueError(f"{case}: frame sizes changed after preflight")
            finite_a, finite_b = bool(np.isfinite(a).all()), bool(np.isfinite(b).all())
            difference = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)
            frame_histogram = np.bincount(difference.ravel(), minlength=256)
            histogram += frame_histogram
            mae = float(np.dot(frame_histogram, levels)) / difference.size / 255
            mse = float(np.dot(frame_histogram, levels ** 2)) / difference.size / 255 ** 2
            pixels_identical = bool(np.array_equal(a, b))
            score = (1.0 if pixels_identical and min(a.shape[:2]) >= 11
                     else ssim(luminance(a), luminance(b)))
            if not all(math.isfinite(value) for value in (mae, mse)) or (score is not None and not math.isfinite(score)):
                raise ValueError(f"{case}: nonfinite metric at frame {index}")
            records.append({
                "frame_index_0_based": index, "source_frame_number": frame_number(path_a),
                "time_seconds": index / fps, "baseline_filename": path_a.name,
                "candidate_filename": path_b.name, "width": a.shape[1], "height": a.shape[0],
                "baseline_exported_uint8_finite": finite_a, "candidate_exported_uint8_finite": finite_b,
                "bitwise_equal_rgb_pixels": pixels_identical,
                "byte_equal_png_files": digest(path_a) == digest(path_b),
                "mae_normalized": mae, "mse_normalized": mse, "psnr_db": psnr(mse),
                "ssim_luma_gaussian11_sigma1_5": score,
                "max_absdiff_normalized": float(difference.max()) / 255,
                "changed_channel_fraction": 1 - int(frame_histogram[0]) / difference.size,
            })
            if preview is None:
                preview = Preview(temporary_preview, a.shape[1] * 2, a.shape[0] + 32, fps)
                header = Image.new("RGB", (a.shape[1] * 2, 32), "#141a24")
                draw = ImageDraw.Draw(header)
                draw.text((8, 7), labels[0], font=font(), fill="white")
                draw.text((a.shape[1] + 8, 7), labels[1], font=font(), fill="white")
                header_pixels = np.asarray(header)
            preview.append(np.concatenate((header_pixels, np.concatenate((a, b), axis=1)), axis=0))
    finally:
        if preview is not None:
            preview.close()
    temporary_preview.replace(preview_path)
    total = int(histogram.sum())
    mse = float(np.dot(histogram, levels ** 2)) / total / 255 ** 2
    scores = [record["ssim_luma_gaussian11_sigma1_5"] for record in records
              if record["ssim_luma_gaussian11_sigma1_5"] is not None]
    summary = {
        "global_mae_normalized": float(np.dot(histogram, levels)) / total / 255,
        "global_mse_normalized": mse, "global_psnr_db": psnr(mse),
        "mean_frame_ssim_luma_gaussian11_sigma1_5": float(np.mean(scores)) if scores else None,
        "global_p99_absdiff_normalized_nearest_rank": percentile_histogram(histogram, 0.99),
        "global_changed_channel_fraction": 1 - int(histogram[0]) / total,
        "max_frame_mae_normalized": max(record["mae_normalized"] for record in records),
        "bitwise_equal_rgb_pixels_all_frames": all(record["bitwise_equal_rgb_pixels"] for record in records),
        "byte_equal_png_files_all_frames": all(record["byte_equal_png_files"] for record in records),
        "baseline_exported_uint8_finite_all_frames": all(record["baseline_exported_uint8_finite"] for record in records),
        "candidate_exported_uint8_finite_all_frames": all(record["candidate_exported_uint8_finite"] for record in records),
    }
    sheet = contact_sheet(case, pairs, records, output / "comparison_contact_sheet.png", difference_gain, fps, labels)
    result = {
        "schema_version": 1, "case": case, "status": "complete", "limitations": LIMITATIONS,
        "completion_records": completion,
        "baseline_label": labels[0], "candidate_label": labels[1],
        "baseline_dir": str(inputs["baseline"][0].parent), "candidate_dir": str(inputs["candidate"][0].parent),
        "baseline_frame_count": len(inputs["baseline"]), "candidate_frame_count": len(inputs["candidate"]),
        "frame_count": len(records), "frame_counts_equal": True, "frame_dimensions_equal": True,
        "width": records[0]["width"], "height": records[0]["height"], "fps": fps,
        "pairing": "Equal numeric frame indices, contiguous within each input directory",
        "ssim_method": "Rec.709 nonlinear RGB luma proxy; Gaussian 11x11 sigma1.5; K1=.01 K2=.03; population covariance; valid centers",
        "ssim_backend": "NumPy CPU" if ssim is numpy_ssim else "SciPy CPU",
        "psnr_null_means": "Identical exported RGB values / zero MSE (infinite PSNR)",
        "preview": {"path": preview_path.name, "left": labels[0], "right": labels[1],
                    "duration_seconds": len(records) / fps,
                    "codec": "CPU libx264 CRF18 yuv420p; no audio; odd dimensions edge-padded"},
        "summary": summary, "contact_sheet": sheet, "frames": records,
    }
    temporary_metrics = output / "metrics.json.partial"
    temporary_metrics.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary_metrics.replace(output / "metrics.json")
    return result


def analyze(directory: Path, cases=DEFAULT_CASES, expected_frames=124, expected_width=864,
            expected_height=480, fps=24, difference_gain=8.0,
            labels=("Baseline H3 nodes 0.2.42", "Candidate H3 nodes 0.2.45")):
    directory = directory.resolve()
    if min(expected_frames, expected_width, expected_height, fps) <= 0 or not math.isfinite(difference_gain) or difference_gain <= 0:
        raise ValueError("Frame count, dimensions, fps and finite difference gain must be positive")
    prepared, completion = validated_inputs(directory, cases, expected_frames, expected_width, expected_height)
    results = []
    for case in cases:
        print(f"CPU analysis: {case}", flush=True)
        result = analyze_case(case, prepared[case], directory / "analysis" / case,
                              completion, fps, difference_gain, labels)
        results.append(result)
        print(json.dumps({"case": case, "summary": result["summary"]}), flush=True)
    return results


def self_test():
    """Synthetic pairs validate metrics, safeguards, and CPU MP4 decoding."""
    import av
    from PIL.PngImagePlugin import PngInfo

    with tempfile.TemporaryDirectory(prefix="h3-node-analysis-") as folder:
        root, case = Path(folder), "synthetic"
        run = {"label": case, "result_event": "execution_success", "timing_valid": True}
        for side in ("baseline", "candidate"):
            (root / side / "results").mkdir(parents=True)
            (root / side / "results" / "benchmark.json").write_text(
                json.dumps({"completed_at": "synthetic", "runs": [run]}), encoding="utf-8")
            (root / side / "output" / "quality" / case).mkdir(parents=True)
        for index in range(3):
            a = np.full((16, 24, 3), 60 + index * 5, dtype=np.uint8)
            b = a.copy()
            b[..., 0] += index
            metadata = PngInfo()
            metadata.add_text("synthetic", "metadata is not pixel data")
            Image.fromarray(a).save(root / "baseline" / "output" / "quality" / case / f"frame_{index + 1}.png")
            candidate_image = Image.fromarray(b)
            if index == 1:
                candidate_image = candidate_image.convert("RGBA")
            candidate_image.save(root / "candidate" / "output" / "quality" / case / f"frame_{index + 1}.png", pnginfo=metadata)
        result = analyze(root, (case,), 3, 24, 16)[0]
        assert abs(result["summary"]["global_mae_normalized"] - 1 / (3 * 255)) < 1e-12
        assert abs(result["summary"]["global_mse_normalized"] - 5 / (9 * 255 ** 2)) < 1e-12
        assert result["frames"][0]["psnr_db"] is None
        assert result["frames"][0]["bitwise_equal_rgb_pixels"]
        assert not result["frames"][0]["byte_equal_png_files"]
        assert result["frames"][0]["ssim_luma_gaussian11_sigma1_5"] == 1.0
        assert result["frames"][2]["ssim_luma_gaussian11_sigma1_5"] < 1.0
        assert result["contact_sheet"]["selected_frame_indices_0_based"] == [0, 1, 2, 2]
        assert result["summary"]["candidate_exported_uint8_finite_all_frames"]
        output = root / "analysis" / case
        assert json.loads((output / "metrics.json").read_text())["status"] == "complete"
        assert (output / "comparison_contact_sheet.png").stat().st_size > 0
        with av.open(str(output / "comparison_side_by_side.mp4")) as video:
            frames = list(video.decode(video=0))
            assert len(frames) == 3 and frames[0].width == 48 and frames[0].height == 48
        uniform = np.full((16, 24), 0.3, dtype=np.float64)
        other = np.full_like(uniform, 0.6)
        assert numpy_ssim(uniform, uniform) == 1.0
        expected_ssim = (2 * 0.3 * 0.6 + 0.01 ** 2) / (0.3 ** 2 + 0.6 ** 2 + 0.01 ** 2)
        assert abs(numpy_ssim(uniform, other) - expected_ssim) < 1e-10

        def rejected():
            try:
                validated_inputs(root, (case,), 3, 24, 16)
            except (ValueError, OSError):
                return
            raise AssertionError("Invalid/incomplete input was accepted")

        marker = root / "candidate" / "results" / "benchmark.json"
        good_marker = marker.read_text()
        marker.write_text(json.dumps({"runs": [run]}))
        rejected()
        marker.write_text(json.dumps({"completed_at": "synthetic", "runs": [dict(run, timing_valid=False)]}))
        rejected()
        marker.write_text(good_marker)
        last = root / "candidate" / "output" / "quality" / case / "frame_3.png"
        last.rename(last.with_name("frame_4.png"))
        rejected()
        last.with_name("frame_4.png").rename(last)
        Image.new("RGB", (12, 16)).save(last)
        rejected()
        last.unlink()
        rejected()
        assert "torch" not in __import__("sys").modules
    print("H3 node analysis self-test passed (CPU only).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--case", dest="cases", action="append", help="Repeat to select completed cases; defaults to all four")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--expected-frames", type=int, default=124)
    parser.add_argument("--expected-width", type=int, default=864)
    parser.add_argument("--expected-height", type=int, default=480)
    parser.add_argument("--difference-gain", type=float, default=8.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.directory is None:
        parser.error("--directory is required unless --self-test is used")
    directory = args.directory
    if not directory.exists() and not directory.is_absolute() and directory.parent == Path("."):
        directory = Path(__file__).resolve().parents[1] / "data" / "diagnostics" / directory
    try:
        analyze(directory, args.cases or DEFAULT_CASES, args.expected_frames,
                args.expected_width, args.expected_height, args.fps, args.difference_gain)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Analysis stopped: {error}\n")


if __name__ == "__main__":
    main()
