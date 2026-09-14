"""Opt-in, small-tensor H3 acceleration runtime smoke test (not unittest discovery).

Run with ComfyUI's interpreter, for example:
    ComfyUI/.venv/Scripts/python.exe H3Studio/tests/smoke_acceleration_runtime.py
    ComfyUI/.venv/Scripts/python.exe H3Studio/tests/smoke_acceleration_runtime.py --device cuda

No checkpoint, encoder, VAE, video generation, package installation, or server is
used. Real MiniMaxH3Model modules and ModelPatcher objects exercise the installed
forward contracts. Random tiny weights do NOT validate trained-model quality,
production VRAM consumption, quantized checkpoints, or performance.

External H3MemoryOptimization and core BlockSparseAttention are intentionally
NEVER stacked: their block-forward ownership is not currently compatible.
The GPU sparse cases test kernel dispatch/finite output, not SLA/VSA quality.
"""

from __future__ import annotations

import argparse
import copy
import importlib.metadata
import inspect
import json
import logging
from pathlib import Path
import sys
import traceback
from unittest.mock import patch


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--comfy-root", type=Path,
        default=Path(__file__).resolve().parents[2] / "ComfyUI",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def run(args):
    comfy_root = args.comfy_root.resolve()
    optimizations_root = comfy_root / "custom_nodes" / "H3-Optimizations"
    for root in (comfy_root, optimizations_root):
        if not root.is_dir():
            raise RuntimeError(f"Required installed source directory missing: {root}")
        sys.path.insert(0, str(root))

    # Comfy parses argv during import. Keep the probe process isolated from the
    # server/compiler; no global environment variables or saved settings change.
    original_argv = sys.argv
    sys.argv = [sys.argv[0], "--disable-comfy-compiler", "--disable-dynamic-vram",
                "--use-pytorch-cross-attention"]
    if args.device == "cpu":
        sys.argv.append("--cpu")
    import comfy.options
    comfy.options.enable_args_parsing()
    import torch
    import comfy.ops
    import comfy.quant_ops
    from comfy.ldm.minimax.model import MiniMaxH3Model, DiTBlock, FinalLayer
    from comfy.model_patcher import ModelPatcher
    from comfy.model_sampling import ModelSamplingAV
    from comfy_extras.nodes_sparse_attention import BlockSparseAttention
    import comfy_extras.nodes_sparse_attention as sparse_module
    from h3_optimizations.memory_migration_node import H3MemoryOptimization
    from h3_optimizations.memory import embedding, final_layer
    from h3_optimizations.plan import STATUS_KEY
    sys.argv = original_argv

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    device = torch.device(args.device)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was explicitly requested but is unavailable")
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    torch.set_grad_enabled(False)
    report = {
        "device": str(device), "torch": torch.__version__,
        "dtype": str(dtype), "comfy_root": str(comfy_root),
        "scope": "tiny random-weight runtime smoke; not a generation benchmark",
        "composition": "standalone memory OR standalone core sparse; never both",
        "versions": {},
        "signatures": {
            "model_forward": str(inspect.signature(MiniMaxH3Model.forward)),
            "model_internal_forward": str(inspect.signature(MiniMaxH3Model._forward)),
            "block_forward": str(inspect.signature(DiTBlock.forward)),
            "final_layer": str(inspect.signature(FinalLayer.forward)),
        },
        "checks": [],
    }
    for name in ("comfy-kitchen", "comfy-aimdo", "transformers"):
        try:
            report["versions"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["versions"][name] = "not installed"
    if device.type == "cuda":
        report["gpu"] = torch.cuda.get_device_name(device)
        report["capability"] = list(torch.cuda.get_device_capability(device))
        torch.cuda.reset_peak_memory_stats(device)

    def check(name, operation):
        try:
            details = operation()
            record = {"name": name, "status": "passed", **(details or {})}
        except Exception as exc:
            record = {"name": name, "status": "failed",
                      "error": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc()}
        report["checks"].append(record)
        print(json.dumps({key: record[key] for key in
                          ("name", "status", "error", "sparse_kernel_calls",
                           "embedding_fallback", "video_max_abs_error", "audio_max_abs_error")
                          if key in record}, ensure_ascii=False, default=str), flush=True)

    def make_model(*, pruned=False, vsa=False):
        # Two 128-dimensional heads retain the real sparse kernel head dimension
        # while the entire synthetic transformer remains only a few MB.
        torch.manual_seed(410)
        model = MiniMaxH3Model(
            hidden_size=256, num_layers=2, token_refiner_num_layers=0,
            num_attention_heads=2, attention_head_dim=128, ffn_hidden_size=512,
            text_dim=64, timestep_input_dim=16, time_embed_hidden_size=32,
            time_embed_dim=8 if pruned else 16, rope_inv_freq_len=16,
            adaln_curve_grid=8 if pruned else None, gate_compress=vsa,
            dtype=dtype, device=device, operations=comfy.ops.disable_weight_init,
        ).eval().requires_grad_(False)
        for name, parameter in model.named_parameters():
            parameter.copy_(torch.randn_like(parameter) * 0.02)
            if "norm" in name and name.endswith("weight"):
                parameter.fill_(1.0)
        model.rope.inv_freq.copy_(torch.linspace(0.001, 0.1, 16, device=device))
        if pruned:
            model.adaln_t_table.copy_(torch.randn_like(model.adaln_t_table) * 0.02)
        return model

    def make_patcher(model):
        # This shell replaces only the heavyweight model_base construction; the
        # transformer, sampler state object and patcher are their real classes.
        shell = torch.nn.Module()
        shell.diffusion_model = model
        shell.model_sampling = ModelSamplingAV()
        shell.current_patcher = None
        return ModelPatcher(shell, device, device)

    def make_inputs(*, referenced=False, masked=False):
        torch.manual_seed(411)
        video = torch.randn(1, 24, 4, 16, 16, device=device, dtype=dtype) * 0.1
        audio = torch.randn(1, 32, 2, 8, device=device, dtype=dtype) * 0.1
        context = torch.randn(1, 4, 256, device=device, dtype=dtype) * 0.1
        payload = {"audio_scale": 4.0}
        if referenced:
            payload.update({
                "visual_cond_noise_aug": 1.0,
                "refs": [{"kind": "video", "latent_t": 1,
                          "latent_h": 4, "latent_w": 4, "ref_audio_t": 0}],
                "cond_video_latents": [
                    torch.randn(1, 24, 1, 4, 4, device=device, dtype=dtype) * 0.1
                ],
            })
        options = {
            "sample_sigmas": torch.tensor([1.0, 0.75, 0.5, 0.25, 0.0], device=device),
            "minimax_h3_sigma_shift_video": 12.0,
            "minimax_h3_sigma_shift_audio": 3.0,
        }
        kwargs = {"minimax_payload": payload}
        if masked:
            video_mask = torch.ones(1, 1, 4, 16, 16, device=device, dtype=dtype)
            video_mask[:, :, 0] = 0.5
            audio_mask = torch.ones(1, 1, 2, 8, device=device, dtype=dtype)
            audio_mask[..., 0:4] = 0.5
            kwargs.update(denoise_mask=video_mask, audio_denoise_mask=audio_mask)
        return [video, audio], torch.tensor([750.0], device=device), context, options, kwargs

    @torch.inference_mode()
    def forward(model, data, options=None):
        x, timestep, context, native_options, kwargs = data
        merged = copy.deepcopy(native_options if options is None else options)
        merged.update(native_options)
        output = model([part.clone() for part in x], timestep, context.clone(),
                       transformer_options=merged, **kwargs)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        if len(output) != len(x):
            raise AssertionError("H3 forward did not return both video and audio streams")
        for actual, source in zip(output, x):
            if actual.shape != source.shape or not torch.isfinite(actual).all():
                raise AssertionError("H3 forward returned wrong shape or non-finite values")
        return [value.detach().clone() for value in output]

    def difference(actual, expected, *, assert_parity):
        errors = [float((a.float() - b.float()).abs().max())
                  for a, b in zip(actual, expected)]
        if assert_parity:
            for actual_stream, expected_stream in zip(actual, expected):
                torch.testing.assert_close(actual_stream, expected_stream,
                                           rtol=0.01 if dtype == torch.bfloat16 else 1e-4,
                                           atol=0.01 if dtype == torch.bfloat16 else 1e-5)
        return {"video_max_abs_error": errors[0], "audio_max_abs_error": errors[1]}

    def memory_case(pruned, masked, *, studio_defaults=False):
        model = make_model(pruned=pruned)
        data = make_inputs(referenced=True, masked=masked)
        expected = forward(model, data)
        patcher = make_patcher(model)
        if studio_defaults:
            # Exercise the shipped UI's Auto precision/QKV/embedding/MLP policy,
            # including runtime quantization if selected. Numerical equivalence
            # is deliberately not asserted for that approximation-capable path.
            optimized = H3MemoryOptimization.execute(
                patcher, precision_mode="Auto", qkv_streaming_mode="Auto",
                embedding_memory_mode="Auto", mlp_memory="auto", chunk_rows=4096,
            )[0]
        else:
            optimized = H3MemoryOptimization.execute(
                patcher, chunk_rows=64, precision_mode="Preserve native",
                qkv_streaming_mode="Off",
            )[0]
        keys = sorted(optimized.object_patches)
        if final_layer.FINAL_LAYER_KEY not in keys:
            raise AssertionError("Standalone memory did not install its final-layer hook")
        block_hooks = any(key.startswith("diffusion_model.blocks.") for key in keys)
        if not block_hooks:
            # FP32 is intentionally outside the extension's BF16/FP16 chunked
            # MLP inventory. CPU probes still exercise the actual FinalLayer
            # hook and verify the explicitly reported upstream MLP fallback.
            mlp_status = optimized.model_options["transformer_options"].get(
                STATUS_KEY, {}).get("mlp", {})
            if (device.type != "cpu"
                    or mlp_status.get("provider") != "preserve_upstream_mlp"
                    or mlp_status.get("activation_mode") != "off"):
                raise AssertionError(
                    "Standalone memory did not install block hooks or an expected CPU fallback")
        if optimized.model_options.get("transformer_options", {}).get("patches_replace"):
            raise AssertionError("Core sparse must not be stacked with external memory")
        native_final = model.final_layer.forward
        final_calls = []
        observer = model.final_layer.register_forward_pre_hook(
            lambda _module, values: final_calls.append(len(values)))
        try:
            optimized.patch_model(load_weights=False)
            optimized.pre_run()
            actual = forward(model, data, optimized.model_options["transformer_options"])
            if not final_calls or not all(count == 7 for count in final_calls):
                raise AssertionError(f"Current seven-argument FinalLayer was not called: {final_calls}")
            metrics = difference(actual, expected, assert_parity=not studio_defaults)
        finally:
            observer.remove()
            optimized.cleanup()
            optimized.unpatch_model(unpatch_weights=False)
        if model.final_layer.forward != native_final:
            raise AssertionError("Unpatch did not restore the native FinalLayer")
        restored = forward(model, data)
        difference(restored, expected, assert_parity=True)
        options = optimized.model_options["transformer_options"]
        status = options.get(STATUS_KEY, {})
        return {**metrics, "object_patches": keys, "block_hooks_installed": block_hooks,
                "final_layer_argument_counts": final_calls,
                "profile": "Studio Auto defaults" if studio_defaults else "Preserve native / QKV Off",
                "quality_parity_asserted": not studio_defaults,
                "embedding_installed": embedding.FORWARD_KEY in keys,
                "embedding_fallback": options.get(embedding.FALLBACK_REASON_KEY),
                "selected_mlp": status.get("mlp"),
                "selected_qkv": status.get("fused_qkv"), "restored": True}

    def sparse_case(mode):
        model = make_model(pruned=True, vsa=mode == "vsa")
        data = make_inputs()
        expected = forward(model, data)
        patcher = make_patcher(model)
        selection = {"selection": mode, "keep_percent": 15.0 if mode == "sla" else 10.0,
                     "tau": 1.3}
        sparse = BlockSparseAttention.execute(
            patcher, selection=selection, start_percent=0.0, end_percent=1.0,
            min_tokens=0, dense_blocks="", extra_tokens=0,
            sink_conditioning="exact_kv_and_rows", verbose=True,
        )[0]
        if sparse.object_patches:
            raise AssertionError("Standalone core sparse unexpectedly includes external memory hooks")
        native_kernel = sparse_module.ck.sol_attn_chunked
        with patch.object(sparse_module.ck, "sol_attn_chunked", wraps=native_kernel) as observed:
            try:
                sparse.pre_run()
                sparse.prepare_state(data[1], sparse.model_options)
                actual = forward(model, data, sparse.model_options["transformer_options"])
                count = observed.call_count
            finally:
                sparse.cleanup()
        if device.type == "cuda" and count == 0:
            raise AssertionError("CUDA sparse case silently fell back to dense; kernel was not exercised")
        metrics = difference(actual, expected, assert_parity=device.type == "cpu")
        return {**metrics, "sparse_kernel_calls": count,
                "expected_path": "CUDA sparse" if device.type == "cuda" else "CPU dense fallback",
                "quality_parity_asserted": device.type == "cpu"}

    def availability():
        if device.type != "cuda":
            # This kitchen query accepts CUDA devices only. Dense CPU fallback
            # is verified through all three real core sparse wrappers below.
            return {"sol_attn_available": False, "expected_path": "CPU dense fallback"}
        available = bool(comfy.quant_ops.ck.sol_attn_is_available(device))
        if device.type == "cuda" and not available:
            raise AssertionError("Installed comfy-kitchen has no usable sparse CUDA kernel for this GPU")
        return {"sol_attn_available": available}

    check("kitchen_sparse_availability", availability)
    for pruned in (False, True):
        for masked in (False, True):
            check(f"standalone_memory_{'curve' if pruned else 'full'}_masked_{masked}",
                  lambda p=pruned, m=masked: memory_case(p, m))
        check(f"studio_default_memory_{'curve' if pruned else 'full'}",
              lambda p=pruned: memory_case(p, True, studio_defaults=True))
    for mode in ("sol-attn", "sla", "vsa"):
        check(f"standalone_core_sparse_{mode}", lambda mode=mode: sparse_case(mode))
    if device.type == "cuda":
        report["peak_allocated_bytes"] = torch.cuda.max_memory_allocated(device)
    report["passed"] = all(item["status"] == "passed" for item in report["checks"])
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0 if report["passed"] else 1


def main():
    args = parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(json.dumps({"passed": False, "phase": "bootstrap",
                          "error": f"{type(exc).__name__}: {exc}",
                          "traceback": traceback.format_exc()}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
