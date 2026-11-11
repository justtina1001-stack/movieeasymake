"""Invoke unchanged pinned upstream setup, optionally holding its GPU probes.

This coordination hook lets benchmark downloads run while another generation
owns the GPU. It changes no upstream file or numerical runtime setting.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--probe-gate", type=Path)
    args, remaining = parser.parse_known_args(argv)
    sys.path.insert(0, str(args.source_dir.resolve()))
    from freevideo_engine import bootstrap
    if args.probe_gate is not None:
        original = bootstrap.Installer.check_kernels
        def gated_probe(installer, *probe_args, **probe_kwargs):
            if not args.probe_gate.is_file():
                print("FreeVideo installation is waiting for the current GPU benchmark to finish.", flush=True)
            while not args.probe_gate.is_file():
                if installer.cancel.is_set():
                    raise RuntimeError("Setup stopped while waiting for GPU access.")
                time.sleep(2)
            return original(installer, *probe_args, **probe_kwargs)
        bootstrap.Installer.check_kernels = gated_probe
    return bootstrap.main(remaining[1:] if remaining[:1] == ["--"] else remaining)


if __name__ == "__main__":
    raise SystemExit(main())
