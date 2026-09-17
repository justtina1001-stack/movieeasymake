"""H3 face-only video refinement using the pinned community FaceRefine nodes."""
from __future__ import annotations

import math
from typing import Any

import aiohttp

from domain import RequestError, aligned_frame_count

FACE_NODES = ("H3FaceTrackCrop", "H3InjectVideoLatent", "H3PerFrameDenoise", "H3FaceStitch")
SELECTIONS = {"largest_face", "centre_most", "left_most", "right_most"}


def repair_options(payload: dict[str, Any], source_duration: float) -> dict[str, Any]:
    try:
        start = float(payload.get("start", 0))
        duration = float(payload.get("duration", 5))
        strength = float(payload.get("strength", 0.3))
        canvas = int(payload.get("canvas", 512))
        seed = int(payload.get("seed", 42))
    except (TypeError, ValueError, OverflowError):
        raise RequestError("臉部修復的時間、強度與 Seed 必須是有效數值。")
    if not all(math.isfinite(v) for v in (start, duration, strength, source_duration)):
        raise RequestError("臉部修復參數不是有限數值。")
    if start < 0 or not 5 <= duration <= 15 or start >= source_duration:
        raise RequestError("請選擇有效的開始時間，修復長度為 5～15 秒；長片可分段修復。")
    if not 0.1 <= strength <= 0.5 or canvas not in (512, 768) or not 0 <= seed <= 2**53 - 1:
        raise RequestError("修復強度須為 0.1～0.5，畫布為 512 或 768，Seed 須為有效非負整數。")
    selection = payload.get("selection", "largest_face")
    if not isinstance(selection, str) or selection not in SELECTIONS:
        raise RequestError("不支援的人物選擇方式。")
    start_frame = round(start * 24)
    available = max(0, math.floor(source_duration * 24 + 0.01) - start_frame)
    frames = min(aligned_frame_count(duration), available)
    frames -= (frames - 5) % 17
    if frames < 107:
        raise RequestError("所選區間剩餘影片太短，請將開始時間往前移，至少保留約 5 秒。")
    return dict(start_frame=start_frame, frames=frames, strength=strength, canvas=canvas,
                selection=selection, seed=seed, detector="face_yolov8m.pt")


async def repair_capabilities(comfy) -> dict[str, Any]:
    missing = []
    timeout = aiohttp.ClientTimeout(total=12)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name in FACE_NODES:
                async with session.get(f"{comfy.base_url}/object_info/{name}", headers=comfy.auth_headers()) as response:
                    if response.status != 200:
                        missing.append(name)
                        continue
                    data = await response.json()
                    if name not in data:
                        missing.append(name)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return {"ready": False, "error": "引擎未回應，可能正在忙碌；請確認狀態後再檢查。", "missing_nodes": []}
    detector_exists = None
    if comfy.mode == "local":
        detector_exists = (comfy.comfy_dir / "models/ultralytics/bbox/face_yolov8m.pt").is_file()
    ready = not missing and detector_exists is not False
    return {"ready": ready, "missing_nodes": missing, "detector_exists": detector_exists,
            "error": None if ready else "請在運算主機執行 setup_h3_face_repair.bat，等工作完成後重啟 ComfyUI 與 Studio。"}


def wire_face_repair(workflow: dict, compiled, uploaded: dict[str, str]) -> dict:
    """Replace full-frame generation output with tracked, cropped refinement."""
    options = compiled.face_repair
    def find(kind):
        return next((key, value) for key, value in workflow.items() if value["class_type"] == kind)
    def add(kind, inputs, title):
        key = str(max(map(int, workflow)) + 1)
        workflow[key] = {"class_type": kind, "inputs": inputs, "_meta": {"title": title}}
        return key
    condition_id, condition = find("MiniMaxH3ReferenceToVideo")
    _, scheduler = find("BasicScheduler")
    _, guider = find("BasicGuider")
    _, sampler = find("SamplerCustomAdvanced")
    decoded_id, _ = find("VAEDecode")
    _, output = find("CreateVideo")
    audio_decode_id, _ = find("VAEDecodeAudio")
    del workflow[audio_decode_id]  # Output always uses source audio, never regenerated speech.
    source = add("LoadVideo", {"file": uploaded[options["source_asset_id"]]}, "載入修復來源片段")
    components = add("GetVideoComponents", {"video": [source, 0]}, "原始畫面與原聲")
    tracker = add("H3FaceTrackCrop", {
        "images": [components, 0], "detector": options["detector"], "confidence": 0.35,
        "crop_factor": 2.5, "canvas_width": options["canvas"], "canvas_height": options["canvas"],
        "canvas_mode": "manual", "smooth_window": 21, "size_smooth_window": 51,
        "smooth_method": "gaussian", "size_mode": "per_frame", "identity_track": False,
        "select": options["selection"], "cut_detection": "auto (pyscenedetect)", "fallback_detector": "none",
    }, "追蹤人物並放大臉部")
    condition["inputs"].update(width=options["canvas"], height=options["canvas"], length=compiled.length)
    injected = add("H3InjectVideoLatent", {
        "av_latent": [condition_id, 1], "images": [tracker, 0], "vae": condition["inputs"]["vae"],
    }, "沿用原臉部動作")
    denoise = add("H3PerFrameDenoise", {
        "model": guider["inputs"]["model"], "av_latent": [injected, 0], "transform": [tracker, 1],
        "denoise_multiplier_small_face": 1.0, "denoise_multiplier_large_face": 0.35,
        "scale_mode": "absolute_px", "face_px_small": 30.0, "face_px_large": 120.0,
        "gamma": 1.0, "smooth_frames": 9,
    }, "依臉部大小調整修復強度")
    scheduler["inputs"].update(model=[denoise, 2], denoise=options["strength"])
    guider["inputs"]["model"] = [denoise, 2]
    sampler["inputs"]["latent_image"] = [denoise, 0]
    stitched = add("H3FaceStitch", {
        "base_images": [components, 0], "refined_crops": [decoded_id, 0], "transform": [tracker, 1],
        "paste_region": "face_only", "mask_dilation": 16, "feather": 6, "colour_match": 1.0,
        "blend": 1.0, "undetected_frames": "skip",
    }, "融合修復臉部，保留其他畫面")
    output["inputs"].update(images=[stitched, 0], audio=[components, 1], fps=24)
    return workflow
