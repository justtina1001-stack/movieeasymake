"""Install the optional face repair engine components on this GPU workstation."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from settings import SettingsStore

REVISION = "d8521d14fe0d721d80cd9417fff5a559cbc21aba"
MODEL_SHA256 = "717923c19b3f4bbf5250b728f1fa6b2cb72a33aed1d236ea9caf0e21ad943e5f"


def main():
    app_dir = Path(__file__).resolve().parent
    settings = SettingsStore(app_dir / "config.json", app_dir)
    if settings.load_error:
        raise RuntimeError(settings.load_error)
    if settings.current.mode != "local":
        raise RuntimeError("Run this installer on the GPU host in local engine mode.")
    engine = Path(settings.current.comfy_dir)
    python = engine / ".venv/Scripts/python.exe"
    if not python.exists():
        raise RuntimeError("ComfyUI Python environment was not found. Install the local engine first.")
    target = engine / "custom_nodes/ComfyUI-H3-FaceRefine"
    if not target.exists():
        subprocess.run(["git", "clone", "https://github.com/Carasibana/ComfyUI-H3-FaceRefine.git", str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", "--detach", REVISION], check=True)
    else:
        revision = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        if revision != REVISION:
            raise RuntimeError("An existing FaceRefine version differs from the tested version. It was preserved; review compatibility before changing it.")
    # Do not replace a workstation's torch/CUDA or numerical runtime during an optional install.
    query = "import importlib.metadata as m,json;print(json.dumps({p:m.version(p) for p in ['torch','torchvision','numpy','scipy']}))"
    versions = json.loads(subprocess.check_output([str(python), "-c", query], text=True))
    with tempfile.TemporaryDirectory(prefix="h3-face-install-") as temporary:
        constraints = Path(temporary) / "constraints.txt"
        constraints.write_text("\n".join(f"{name}=={version}" for name, version in versions.items()), encoding="utf-8")
        subprocess.run([str(python), "-m", "pip", "install", "-c", str(constraints),
                        "ultralytics==8.4.154", "scenedetect==0.7.1"], check=True)
        model = engine / "models/ultralytics/bbox/face_yolov8m.pt"
        if not model.exists():
            download = Path(temporary) / "face_yolov8m.pt"
            with urllib.request.urlopen("https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt", timeout=120) as response:
                download.write_bytes(response.read())
            if hashlib.sha256(download.read_bytes()).hexdigest() != MODEL_SHA256:
                raise RuntimeError("Face detector checksum differs. Download was not installed.")
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(download.read_bytes())
        if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
            raise RuntimeError("Existing face detector differs from the tested model; the file was preserved.")
    print("Face repair installed. Wait for active jobs to finish, then restart ComfyUI and Studio.")


if __name__ == "__main__":
    main()
