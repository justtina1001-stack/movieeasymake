"""Read-only readiness of the isolated, pinned FreeVideo runtime.

No Torch, CUDA, upstream imports, downloads, subprocesses or setup are performed
while Studio polls model availability.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("freevideo_manifest.json")
REQUIRED_MACHINE_KEYS = (
    "root", "python", "cache", "base", "checkpoint", "comfy_python",
    "comfy_root", "vdn_root", "model_paths", "model_root", "encoder_model_root", "encoder", "gpu_uuid",
)


def read_manifest() -> dict:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported FreeVideo install manifest.")
    return value


def file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _required_path(value, *, directory=False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing runtime path.")
    path = Path(value).expanduser()
    if not path.is_absolute() or not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing runtime component.")
    return path.resolve()


def freevideo_runtime_status(comfy_dir: Path) -> dict:
    """Return availability only for an installation verified by our installer.

    The installer hashes every downloaded file. Polls check the immutable pack
    manifest and each packed group size so a missing/partial model cannot become
    selectable merely because a machine.json says ready.
    """
    result = {"ready": False, "error": "FreeVideo／VDN-H3 尚未安裝，請在 GPU 主機執行 setup_freevideo.bat。"}
    try:
        manifest = read_manifest()
        node = Path(comfy_dir).resolve() / "custom_nodes" / manifest["node_directory"]
        binding = _json(node / "comfyui.json")
        root = _required_path(binding.get("installation"), directory=True)
        result.update(root=str(root), source_revision=manifest["source_revision"])
        receipt = _json(root / "h3studio-install.json")
        if (receipt.get("source_revision") != manifest["source_revision"]
                or receipt.get("status") != "verified"
                or receipt.get("prepared_manifest_sha256") != manifest["prepared_manifest_sha256"]):
            raise ValueError("Unverified installation.")
        for name, checksum in manifest["source_files"].items():
            source_file = (node / name).resolve()
            if not source_file.is_relative_to(node.resolve()) or file_sha256(source_file) != checksum:
                raise ValueError("Pinned dependency metadata changed.")
        machine = _json(root / "machine.json")
        if machine.get("ready") is not True or any(not machine.get(key) for key in REQUIRED_MACHINE_KEYS):
            raise ValueError("Incomplete machine configuration.")
        if Path(machine["root"]).resolve() != root or machine.get("video_model") is False:
            raise ValueError("The configured installation does not contain the video model.")
        if machine.get("model_revision") != manifest["model_revision"]:
            raise ValueError("Unexpected VDN model revision.")
        if machine.get("prepared_format") != manifest["prepared_format"]:
            raise ValueError("Unexpected prepared model format.")
        for name in ("python", "comfy_python", "model_paths"):
            _required_path(machine[name])
        for name in ("base", "checkpoint", "comfy_root", "vdn_root", "model_root", "encoder_model_root"):
            _required_path(machine[name], directory=True)
        model_rows = json.loads((node / "freevideo_engine/model_files.json").read_text(encoding="utf-8"))
        for row in model_rows:
            name = row["file"]
            # Prepared packs replace the full transformer and conversion-only
            # adapters. The original decoders/configs and encoder remain needed.
            if (name.endswith(".safetensors") and name.startswith(("h3-base/transformer/", "stage-dmd-step-250/"))
                    or not name.endswith((".json", ".safetensors"))):
                continue
            library = Path(machine["encoder_model_root"] if name.startswith("text_encoders/") else machine["model_root"]).resolve()
            relative = Path("latent_upscaler") / Path(name).name if row.get("role") == "latent_upscaler" else Path(name)
            component = (library / relative).resolve()
            if not component.is_relative_to(library) or not component.is_file() or component.stat().st_size != row["bytes"]:
                raise ValueError("Missing or partial decoder/encoder component.")
        cache = _required_path(machine["cache"], directory=True)
        pack_path = cache / "manifest.json"
        if file_sha256(pack_path) != manifest["prepared_manifest_sha256"]:
            raise ValueError("Prepared model manifest changed.")
        pack = _json(pack_path)
        groups = pack.get("groups")
        if pack.get("precision") != "int8" or not isinstance(groups, list) or not groups:
            raise ValueError("Invalid prepared model pack.")
        for group in groups:
            name, size = group.get("file"), group.get("bytes")
            if not isinstance(name, str) or type(size) is not int or size <= 0:
                raise ValueError("Invalid packed model group.")
            path = (cache / name).resolve()
            if not path.is_relative_to(cache) or not path.is_file() or path.stat().st_size != size:
                raise ValueError("Missing or partial model group.")
        result.update(ready=True, error=None, prepared_format=machine["prepared_format"],
                      model_revision=machine["model_revision"], groups=len(groups))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if result.get("root"):
            result["error"] = "FreeVideo／VDN-H3 安裝尚未完成或檔案已變更，請在 GPU 主機重新執行 setup_freevideo.bat；既有模型不受影響。"
    return result
