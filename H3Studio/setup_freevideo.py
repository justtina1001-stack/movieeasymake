"""Install the pinned FreeVideo node and its private Windows CUDA runtime.

The official bootstrap owns resumable downloads and verifies model checksums.
It receives a separate root and never installs packages into Studio or ComfyUI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

from freevideo_runtime import read_manifest, file_sha256, freevideo_runtime_status
from settings import SettingsStore


def run(command, *, cwd=None, env=None):
    return subprocess.run([str(item) for item in command], cwd=cwd, env=env, check=True)


def git_output(node: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(node), *arguments], text=True, encoding="utf-8").strip()


def validate_layout(engine: Path, root: Path) -> tuple[Path, Path]:
    engine, root = Path(engine).resolve(), Path(root).resolve()
    if not (engine / "main.py").is_file():
        raise RuntimeError("Install the local ComfyUI engine first.")
    if root == root.parent or root == engine or engine.is_relative_to(root) or root.is_relative_to(engine):
        raise RuntimeError("FreeVideo needs a separate runtime directory outside the existing ComfyUI installation.")
    if root == Path(sys.prefix).resolve() or Path(sys.prefix).resolve().is_relative_to(root):
        raise RuntimeError("The FreeVideo runtime must not contain the active Python environment.")
    return engine, root


def ensure_source(engine: Path, manifest: dict, *, source_dir: Path | None = None) -> Path:
    node = engine / "custom_nodes" / manifest["node_directory"]
    if node.exists():
        if not (node / ".git").is_dir():
            raise RuntimeError("An existing FreeVideo node is not a verified Git checkout; it was preserved.")
        if git_output(node, "rev-parse", "HEAD") != manifest["source_revision"]:
            raise RuntimeError("An existing FreeVideo checkout uses another revision; it was preserved.")
        if git_output(node, "status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("The existing FreeVideo source has local changes; it was preserved.")
    else:
        node.parent.mkdir(parents=True, exist_ok=True)
        source = manifest["source_url"]
        if source_dir is not None:
            source_dir = Path(source_dir).resolve()
            if git_output(source_dir, "rev-parse", "HEAD") != manifest["source_revision"]:
                raise RuntimeError("Offline FreeVideo source does not match the pinned revision.")
            source = str(source_dir)
        run(["git", "clone", "--no-checkout", source, node])
        run(["git", "-C", node, "checkout", "--detach", manifest["source_revision"]])
    # These pin all model file hashes and transitive runtime/source versions.
    for name, checksum in manifest["source_files"].items():
        path = node / name
        if not path.resolve().is_relative_to(node.resolve()) or file_sha256(path) != checksum:
            raise RuntimeError("Pinned FreeVideo dependency metadata changed; source was preserved.")
    return node


def bind_installation(node: Path, root: Path):
    path = node / "comfyui.json"
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(current, dict) or Path(current.get("installation", "")).resolve() != root:
            raise RuntimeError("FreeVideo already points to a different installation; its binding was preserved.")
        return
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"installation": str(root)}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def verified_download(url, target: Path, size: int, checksum: str, *, opener=urllib.request.urlopen):
    """Resume a pinned official HTTPS wheel without relying on Schannel curl.

    Normal Python TLS certificate verification remains enabled. A complete
    artifact is published only after both the expected size and SHA-256 match.
    """
    if not url.startswith("https://download.pytorch.org/"):
        raise ValueError("Unexpected GPU runtime download host.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size != size or file_sha256(target) != checksum:
            raise RuntimeError("A different cached runtime wheel was preserved.")
        return target
    partial = target.with_name(target.name + ".h3studio.partial")
    current = partial.stat().st_size if partial.exists() else 0
    if current > size:
        raise RuntimeError("An oversized partial runtime wheel was preserved.")
    if current < size:
        headers = {"User-Agent": "H3Studio-FreeVideo-Installer/1"}
        if current:
            headers["Range"] = f"bytes={current}-"
        request = urllib.request.Request(url, headers=headers)
        with opener(request, timeout=60) as response:
            if current and response.status != 206:
                raise RuntimeError("Server did not honor a partial wheel resume; existing bytes were preserved.")
            if current and not response.headers.get("Content-Range", "").startswith(f"bytes {current}-"):
                raise RuntimeError("Unexpected wheel resume response.")
            mode = "ab" if current else "wb"
            print(f"Downloading pinned GPU runtime: {target.name} ({size / 1e9:.2f} GB)", flush=True)
            last = time.monotonic()
            with partial.open(mode) as output:
                while block := response.read(4 * 1024 * 1024):
                    output.write(block)
                    current += len(block)
                    if current > size:
                        raise RuntimeError("The runtime download exceeded the expected size.")
                    if time.monotonic() - last >= 10:
                        print(f"Runtime download {current / size:.0%}: {target.name}", flush=True)
                        last = time.monotonic()
    if partial.stat().st_size != size or file_sha256(partial) != checksum:
        raise RuntimeError("Runtime wheel verification failed; partial download was retained.")
    partial.replace(target)
    return target


def prefetch_gpu_wheels(node: Path, root: Path):
    catalog = json.loads((node / "freevideo_engine/torch_downloads.json").read_text(encoding="utf-8"))
    wanted = {"torch==2.13.0+cu130", "torchvision==0.28.0+cu130", "torchaudio==2.11.0+cu130"}
    rows = [row for row in catalog["windows_cp312"] if row["package"] + "==" + row["version"] in wanted]
    if len(rows) != 3:
        raise RuntimeError("Pinned Windows runtime catalog is incomplete.")
    for row in rows:
        verified_download(row["sources"]["official"], root / "downloads/torch-wheels" / row["filename"],
                          row["bytes"], row["sha256"])


def bootstrap_command(python: Path, node: Path, engine: Path, root: Path, *, plan=False, probe_gate=None) -> list[str]:
    command = [str(python), "-X", "utf8", "-u", str(Path(__file__).with_name("freevideo_bootstrap.py")),
               "--source-dir", str(node)]
    if probe_gate is not None:
        command += ["--probe-gate", str(probe_gate)]
    command += ["--", "--root", str(root),
               "--environment", "unified", "--prepared-format", "int8", "--model-source", "prepared",
               "--reuse-models", str(engine / "models"), "--no-launch", "--network", "official"]
    if plan:
        command += ["--plan", "--json"]
    else:
        command += ["--plain", "--no-color", "--yes", "--accept-model-license"]
    return command


def install(engine: Path, root: Path, *, source_dir=None, plan=False, probe_gate=None, runner=run) -> dict:
    if platform.system() != "Windows":
        raise RuntimeError("This validated installer currently supports the local Windows NVIDIA GPU host.")
    engine, root = validate_layout(engine, root)
    manifest = read_manifest()
    node = ensure_source(engine, manifest, source_dir=source_dir)
    root.mkdir(parents=True, exist_ok=True)
    bind_installation(node, root)
    # Upstream controls only its own isolated interpreter/encoder library. Its
    # --no-launch mode cannot open or upgrade our existing ComfyUI server.
    env = dict(os.environ, FREEVIDEO_HOME=str(root), PYTHONPATH=str(node),
               PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_COLOR="1")
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME"):
        env.pop(name, None)
    if not plan:
        prefetch_gpu_wheels(node, root)
    runner(bootstrap_command(Path(sys.executable), node, engine, root, plan=plan, probe_gate=probe_gate), cwd=node, env=env)
    if plan:
        return {"planned": True, "root": str(root), "node": str(node), "core_updated": False}
    machine = json.loads((root / "machine.json").read_text(encoding="utf-8"))
    if machine.get("ready") is not True:
        raise RuntimeError("FreeVideo setup did not finish; retained files can be resumed.")
    cache_manifest = Path(machine["cache"]) / "manifest.json"
    if file_sha256(cache_manifest) != manifest["prepared_manifest_sha256"]:
        raise RuntimeError("The prepared VDN model does not match the pinned pack.")
    receipt = {"status": "verified", "installed_at": datetime.now(timezone.utc).isoformat(),
               "source_revision": manifest["source_revision"], "model_revision": manifest["model_revision"],
               "prepared_revision": manifest["prepared_revision"],
               "prepared_manifest_sha256": manifest["prepared_manifest_sha256"],
               "core_updated": False, "existing_models_replaced": False}
    path = root / "h3studio-install.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temp.replace(path)
    status = freevideo_runtime_status(engine)
    if not status["ready"]:
        raise RuntimeError(status["error"])
    return {**receipt, **status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--source-dir", type=Path, help="Reuse the exact pinned upstream Git checkout.")
    parser.add_argument("--plan", action="store_true", help="Only prepare the source and print the official install preflight.")
    parser.add_argument("--probe-gate", type=Path, help="Wait for this coordination file before the final GPU kernel probes.")
    args = parser.parse_args(argv)
    app = Path(__file__).resolve().parent
    if args.engine_dir is None:
        settings = SettingsStore(app / "config.json", app)
        if settings.load_error or settings.current.mode != "local":
            raise RuntimeError("Run setup_freevideo.bat on the local GPU host.")
        engine = Path(settings.current.comfy_dir)
    else:
        engine = args.engine_dir
    result = install(engine, args.runtime_dir or app / "data/freevideo_runtime", source_dir=args.source_dir,
                     plan=args.plan, probe_gate=args.probe_gate)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not args.plan:
        print("FreeVideo runtime verified. Restart ComfyUI and Studio when the generation queue is empty.", flush=True)
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Upstream keeps detailed local setup logs. Avoid signed download URLs
        # or credentials from an unexpected downloader exception in UI output.
        print(f"[ERROR] FreeVideo installation did not finish ({type(error).__name__}). Existing files and partial downloads were preserved.", flush=True)
        raise SystemExit(1) from None
