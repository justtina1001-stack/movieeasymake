from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = APP_DIR.parent
COMFY_DIR = ROOT_DIR / "ComfyUI"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(COMFY_DIR))
os.chdir(COMFY_DIR)

import execution
import nodes
import av
import numpy as np

from domain import build_workflow, compile_request


ASSET = "a" * 32
VIDEO = "d" * 32
VALIDATION_VIDEO = COMFY_DIR / "input" / "h3studio-validation.mp4"


def create_validation_video() -> None:
    container = av.open(str(VALIDATION_VIDEO), mode="w")
    stream = container.add_stream("libx264", rate=24)
    stream.width = 64
    stream.height = 64
    stream.pix_fmt = "yuv420p"
    for index in range(5):
        pixels = np.full((64, 64, 3), 32 + index * 20, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def payload(mode: str):
    data = {
        "mode": mode,
        "prompt": "A cinematic subject slowly turns toward the camera. Audio: quiet wind.",
        "aspect_ratio": "16:9",
        "megapixels": 0.4,
        "duration": 5,
        "seed": 1,
        "steps": 20,
    }
    if mode == "fl2va":
        data["first_image_asset_id"] = ASSET
    if mode == "r2v":
        data["references"] = [{
            "alias": "Hero",
            "type": "character",
            "image_asset_ids": [ASSET],
            "video_asset_id": VIDEO,
        }]
    return data


async def main():
    create_validation_video()
    try:
        await nodes.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False)
        for mode in ("t2v", "fl2va", "r2v"):
            compiled = compile_request(payload(mode))
            uploaded = {ASSET: "example.png", VIDEO: VALIDATION_VIDEO.name}
            workflow = build_workflow(compiled, uploaded, f"validate-{mode}")
            valid, error, outputs, node_errors = await execution.validate_prompt(f"validate-{mode}", workflow, None)
            if not valid:
                raise RuntimeError(f"{mode} validation failed: {error}; {node_errors}")
            print(f"{mode}: valid ({len(workflow)} nodes, outputs={outputs})")
    finally:
        VALIDATION_VIDEO.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
