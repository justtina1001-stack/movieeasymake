"""Effects review, source preservation, isolated cancellation and retry contracts."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

import av
import numpy as np
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as studio
import effects
from effects import EffectJobManager, register_effect_routes
from effects_spec import normalize_effect_spec


def make_video(path, seconds=2, fps=12):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=fps)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "10", "preset": "ultrafast"}
        for index in range(round(seconds * fps)):
            pixels = np.zeros((64, 64, 3), np.uint8)
            pixels[20:44, 20:44, 0] = 200
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def processor(source, output, spec, **kwargs):
    kwargs["progress"]("matting", .3)
    kwargs["progress"]("exporting", 1)
    for name in ["sequence.zip", "preview.mp4", "qa.json"]:
        (output / name).write_bytes(b"output")
    return {"frame_count": spec["frame_count"], "fps": spec["fps"], "width": spec["width"], "height": spec["height"],
            "duration": spec["duration"], "qa": {"status": "passed", "warnings": []},
            "files": [{"name": "sequence.zip", "label": "透明序列圖", "kind": "archive"},
                      {"name": "preview.mp4", "label": "黑底預覽", "kind": "preview"},
                      {"name": "qa.json", "label": "檢查紀錄", "kind": "metadata"}]}


class Generator:
    def __init__(self, source):
        self.jobs, self.creates, self.cancels = {}, [], []
        self.source, self.complete_immediately = source, False

    def create(self, compiled, prepared, **kwargs):
        self.creates.append((compiled, copy.deepcopy(prepared), kwargs))
        identifier = f"{len(self.creates):032x}"
        child = {"id": identifier, "status": "completed" if self.complete_immediately else "queued", "progress": 0,
                 "parent_job_id": kwargs.get("parent_job_id")}
        self.jobs[identifier] = child
        return child

    async def job_output_path(self, child):
        return self.source

    async def cancel(self, identifier):
        self.cancels.append(identifier)
        self.jobs[identifier]["status"] = "cancelled"


class EffectsAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.asset_id = "a" * 32
        self.assets = studio.AssetStore(self.root / "assets")
        self.source = self.assets.directory / f"{self.asset_id}.mp4"
        make_video(self.source)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.generator = Generator(self.source)
        self.comfy = type("Comfy", (), {"system_stats": AsyncMock(return_value=None), "model_inventory": AsyncMock(return_value={})})()
        self.app = web.Application()
        self.app["assets"], self.app["jobs"], self.app["comfy"] = self.assets, self.generator, self.comfy
        self.app["prepare_generation_payload"] = AsyncMock(side_effect=lambda payload: copy.deepcopy(payload))
        self.manager = register_effect_routes(self.app, Path(studio.STATIC_DIR), self.root)
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.enterContext(patch("effects.process_video", side_effect=processor))

    def payload(self, **changes):
        return {"source_mode": "video", "source_asset_id": self.asset_id, "duration": 1, "fps": 12,
                "width": 256, "height": 256, "playback": "once", **changes}

    async def preview(self, payload=None):
        response = await self.client.post("/api/effects/preview", json=payload or self.payload())
        result = await response.json()
        self.assertEqual(response.status, 200, result)
        return result

    async def create(self, packet, **flags):
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "review_receipt": packet["review_receipt"], **flags})
        result = await response.json()
        self.assertEqual(response.status, 202, result)
        return result

    async def finish(self, identifier):
        await asyncio.wait_for(self.manager.tasks[identifier], 5)
        return self.manager.public(self.manager.jobs[identifier])

    async def test_import_preview_never_prepares_or_submits_generation(self):
        packet = await self.preview()
        self.assertIsNone(packet["generation"])
        self.assertIn("不送出", packet["prompt"])
        self.app["prepare_generation_payload"].assert_not_awaited()
        self.assertEqual(self.generator.creates, [])
        self.assertEqual(self.manager.jobs, {})
        self.assertAlmostEqual(packet["source_info"]["duration"], 2, places=2)

    async def test_confirmed_import_preserves_source_publishes_revision_files_and_no_gpu(self):
        job = await self.create(await self.preview())
        complete = await self.finish(job["id"])
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["result"]["frame_count"], 12)
        self.assertEqual(complete["source_sha256"], self.source_hash)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        self.assertEqual(self.generator.creates, [])
        self.assertTrue(any(item["name"] == "r0001_sequence.zip" for item in complete["files"]))
        self.assertNotIn("prepared", complete)
        self.assertTrue(all("relative_path" not in entry for entry in complete["files"]))
        response = await self.client.get(next(item["url"] for item in complete["files"] if item["kind"] == "archive"))
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"output")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_mutation_of_reviewed_spec_and_receipt_replay_are_rejected(self):
        packet = await self.preview()
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "duration": .5, "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.manager.jobs, {})
        job = await self.create(packet)
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        await self.finish(job["id"])

    async def test_review_receipt_binds_timing_mode(self):
        packet = await self.preview(self.payload(timing_mode="retime"))
        self.assertEqual(packet["spec"]["timing_mode"], "retime")
        response = await self.client.post("/api/effects/jobs", json={
            **packet["spec"], "timing_mode": "trim", "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.manager.jobs, {})
        self.assertEqual(self.generator.creates, [])
        job = await self.create(packet)
        self.assertEqual((await self.finish(job["id"]))["spec"]["timing_mode"], "retime")

    async def test_reviewed_spec_survives_browser_integer_number_roundtrip(self):
        packet = await self.preview()
        self.assertIsInstance(packet["spec"]["duration"], int)
        self.assertIsInstance(packet["spec"]["source_duration"], int)
        self.assertIsInstance(packet["spec"]["trim_start"], int)
        # JavaScript JSON.stringify emits 1 rather than Python's 1.0. The
        # public receipt payload must already use the browser representation.
        submitted = copy.deepcopy(packet["spec"])
        for key, value in submitted.items():
            if isinstance(value, float) and value.is_integer():
                submitted[key] = int(value)
        response = await self.client.post("/api/effects/jobs", json={**submitted, "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 202, await response.text())
        await self.finish((await response.json())["id"])

    async def test_source_range_and_wrong_media_are_rejected_before_job_creation(self):
        for payload in [self.payload(trim_start=1.5), self.payload(source_asset_id="../private"), self.payload(source_asset_id="b" * 32)]:
            response = await self.client.post("/api/effects/preview", json=payload)
            self.assertEqual(response.status, 400, await response.text())
        image = self.assets.save_image(Image.new("RGB", (64, 64)), "wrong.png", "effects-source")
        response = await self.client.post("/api/effects/preview", json=self.payload(source_asset_id=image["id"]))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.manager.jobs, {})

    async def test_retime_accepts_longer_target_but_trim_and_offset_remain_rejected(self):
        for payload in [self.payload(timing_mode="trim", duration=3),
                        self.payload(timing_mode="retime", trim_start=.25),
                        self.payload(timing_mode="unknown")]:
            response = await self.client.post("/api/effects/preview", json=payload)
            self.assertEqual(response.status, 400, await response.text())
        packet = await self.preview(self.payload(timing_mode="retime", duration=10))
        self.assertEqual(packet["spec"]["duration"], 10)
        self.assertEqual(packet["source_info"]["duration"], 2)
        with patch("effects.process_video", side_effect=processor) as process:
            job = await self.create(packet)
            result = await self.finish(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(process.call_args.args[2]["timing_mode"], "retime")
        self.assertEqual(process.call_args.args[2]["duration"], 10)
        self.assertEqual(self.generator.creates, [])

    def test_retime_still_requires_valid_actual_source_duration(self):
        spec = {"timing_mode": "retime", "trim_start": 0, "duration": 2, "fps": 24}
        for invalid in [None, 0, -1, float("nan"), float("inf"), "unknown", 120.001]:
            with self.subTest(source_duration=invalid), self.assertRaises(ValueError):
                effects.check_source_range(spec, {"duration": invalid})
        effects.check_source_range(spec, {"duration": 1})
        effects.check_source_range(spec, {"duration": 120})
        effects.check_source_range({**spec, "timing_mode": "trim"}, {"duration": 121})

    async def test_retime_source_limit_is_checked_before_review_while_trim_stays_available(self):
        info = {"width": 64, "height": 64, "fps": 12, "duration": 121, "has_audio": False}
        with patch("effects.inspect_video", return_value=info):
            response = await self.client.post("/api/effects/preview", json=self.payload(timing_mode="retime"))
            self.assertEqual(response.status, 400, await response.text())
            self.assertIn("120", (await response.json())["error"])
            packet = await self.preview(self.payload(timing_mode="trim"))
        self.assertEqual(packet["source_info"]["duration"], 121)
        self.assertEqual(self.manager.jobs, {})
        self.assertEqual(self.generator.creates, [])

    async def test_changed_source_cannot_use_an_existing_receipt(self):
        packet = await self.preview()
        with self.source.open("ab") as handle:
            handle.write(b"changed")
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.manager.jobs, {})

    async def test_generated_effects_reuse_queue_and_frozen_server_compilation(self):
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None, duration=1))
        self.assertIn("overall_soundscape: N/A", packet["prompt"])
        self.assertEqual(self.generator.creates, [])
        job = await self.create(packet)
        await asyncio.sleep(0)
        self.assertEqual(len(self.generator.creates), 1)
        compiled, prepared, options = self.generator.creates[0]
        self.assertEqual(compiled.prompt, packet["prompt"])
        self.assertEqual(options, {"hidden": True, "parent_job_id": job["id"]})
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "generating")
        unrelated = "f" * 32
        self.generator.jobs[unrelated] = {"id": unrelated, "status": "running", "parent_job_id": None}
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/cancel")
        self.assertEqual(response.status, 200)
        self.assertEqual(self.generator.cancels, [self.manager.jobs[job["id"]]["child_job_id"]])
        self.assertEqual(self.generator.jobs[unrelated]["status"], "running")

    async def test_freevideo_quality_is_reviewed_and_the_exact_approved_recipe_reaches_queue(self):
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None, duration=1,
                                                video_model="freevideo_vdn_h3", freevideo_quality="high"))
        self.assertEqual(packet["generation"]["video_model"], "freevideo_vdn_h3")
        self.assertEqual(packet["generation"]["freevideo_quality"], "high")
        self.assertEqual(packet["generation"]["steps"], 16)
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "freevideo_quality": "light",
                                                                   "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        self.assertFalse(self.generator.creates)
        job = await self.create(packet)
        await asyncio.sleep(0)
        compiled, prepared, options = self.generator.creates[0]
        self.assertEqual(compiled.prompt, packet["prompt"])
        self.assertEqual(compiled.freevideo_quality, "high")
        self.assertEqual(prepared["freevideo_quality"], "high")
        self.assertEqual(self.manager.jobs[job["id"]]["spec"]["freevideo_quality"], "high")
        await self.client.post(f"/api/effects/jobs/{job['id']}/cancel")

    async def test_freevideo_import_is_cpu_only_even_with_invalid_inactive_model_settings(self):
        packet = await self.preview(self.payload(video_model="freevideo_vdn_h3", freevideo_quality={"not": "a quality"}))
        self.assertIsNone(packet["generation"])
        job = await self.create(packet)
        self.assertEqual((await self.finish(job["id"]))["status"], "completed")
        self.app["prepare_generation_payload"].assert_not_awaited()
        self.assertFalse(self.generator.creates)

    async def test_freevideo_status_does_not_depend_on_original_h3_weights(self):
        self.comfy.system_stats.return_value = {"devices": [{"name": "GPU"}]}
        self.comfy.model_inventory.return_value = {"freevideo_nodes_ready": True, "freevideo_runtime_ready": True}
        response = await self.client.get("/api/effects/status")
        result = await response.json()
        self.assertTrue(result["ready"])
        profiles = {row["id"]: row for row in result["models"]}
        self.assertTrue(profiles["freevideo_vdn_h3"]["ready"])
        self.assertFalse(profiles["h3"]["ready"])
        self.comfy.model_inventory.return_value["freevideo_runtime_ready"] = False
        response = await self.client.get("/api/effects/status")
        result = await response.json()
        self.assertFalse(result["ready"])
        self.assertTrue(result["import_ready"])

    async def test_generated_retime_preserves_full_five_second_source_for_cpu_processor(self):
        make_video(self.source, seconds=5)
        source_bytes = self.source.read_bytes()
        self.generator.complete_immediately = True
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None,
                                                source_duration=5, duration=2, timing_mode="retime"))
        self.assertGreaterEqual(packet["generation"]["duration"], 5)
        with patch("effects.process_video", side_effect=processor) as process:
            job = await self.create(packet)
            result = await self.finish(job["id"])
        self.assertEqual(result["status"], "completed")
        source, _, spec = process.call_args.args
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(spec, self.manager.jobs[job["id"]]["spec"])
        self.assertEqual((spec["timing_mode"], spec["trim_start"], spec["duration"], spec["frame_count"]),
                         ("retime", 0, 2, 24))
        self.assertEqual(result["source_info"]["duration"], 5)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(len(self.generator.creates), 1)
        compiled, prepared, options = self.generator.creates[0]
        self.assertGreaterEqual(compiled.actual_duration, 5)
        self.assertIsNone(prepared.get("retime_duration"))
        self.assertEqual(options["parent_job_id"], job["id"])

    async def test_generated_retime_slow_target_does_not_extend_source_generation(self):
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None,
                                                source_duration=5, duration=8, timing_mode="retime"))
        self.assertEqual(packet["spec"]["duration"], 8)
        self.assertGreaterEqual(packet["generation"]["duration"], 5)
        self.assertLess(packet["generation"]["duration"], 8)
        prepared = self.app["prepare_generation_payload"].call_args.args[0]
        self.assertEqual(prepared["duration"], 5)
        self.assertIsNone(prepared.get("retime_duration"))
        self.assertEqual(self.generator.creates, [])

    async def test_transparent_reference_is_black_composited_without_changing_original(self):
        image = Image.new("RGBA", (48, 96), (255, 0, 0, 0))
        image.paste((255, 64, 0, 128), (18, 30, 30, 70))
        asset = self.assets.save_image(image, "source.png", "effects-source")
        original = self.assets.path_for(asset["id"]).read_bytes()
        packet = await self.preview(self.payload(source_mode="image", source_asset_id=asset["id"]))
        prepared = self.app["prepare_generation_payload"].call_args.args[0]
        self.assertNotEqual(prepared["first_image_asset_id"], asset["id"])
        self.assertEqual(packet["spec"]["source_asset_id"], asset["id"])
        with Image.open(self.assets.path_for(prepared["first_image_asset_id"])) as fixed:
            self.assertEqual(fixed.mode, "RGB")
            self.assertEqual(fixed.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(fixed.size, (packet["generation"]["width"], packet["generation"]["height"]))
        self.assertEqual(self.assets.path_for(asset["id"]).read_bytes(), original)

    async def test_unavailable_translation_requires_explicit_original_confirmation(self):
        async def unavailable(payload):
            return {**payload, "h3_translation": {"status": "unavailable", "reason_code": "timeout"}}
        self.app["prepare_generation_payload"].side_effect = unavailable
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None))
        self.assertEqual(packet["generation"]["h3_translation"]["status"], "unavailable")
        response = await self.client.post("/api/effects/jobs", json={**packet["spec"], "review_receipt": packet["review_receipt"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.generator.creates, [])
        job = await self.create(packet, original_prompt_confirmed=True)
        await self.manager.cancel(job["id"])

    async def test_failed_processing_can_retry_from_source_without_resubmission(self):
        self.generator.complete_immediately = True
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None))
        with patch("effects.process_video", side_effect=ValueError("test processing failure")):
            job = await self.create(packet)
            failed = await self.finish(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["source_file"])
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry")
        self.assertEqual(response.status, 202)
        complete = await self.finish(job["id"])
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(len(self.generator.creates), 1)
        self.assertEqual(complete["revision"], 2)
        self.assertTrue(any(item["name"] == "r0002_sequence.zip" for item in complete["files"]))

    async def test_missing_generation_source_does_not_retry_gpu(self):
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None))
        job = await self.create(packet)
        await asyncio.sleep(0)
        await self.manager.cancel(job["id"])
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry")
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.generator.creates), 1)

    async def test_cancelling_postprocessing_waits_for_worker_and_old_progress_cannot_change_retry(self):
        started, stopped = threading.Event(), threading.Event()
        def blocked(source, output, spec, **kwargs):
            started.set()
            try:
                for _ in range(500):
                    if kwargs["cancelled"]():
                        raise ValueError("cancelled")
                    time.sleep(.01)
                raise ValueError("test worker was not cancelled")
            finally:
                stopped.set()
        with patch("effects.process_video", side_effect=blocked):
            job = await self.create(await self.preview())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            response = await self.client.post(f"/api/effects/jobs/{job['id']}/cancel")
            self.assertEqual(response.status, 200)
            self.assertTrue(stopped.is_set())
            self.assertTrue(self.manager.workers[job["id"]].done())
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry")
        self.assertEqual(response.status, 202)
        complete = await self.finish(job["id"])
        self.assertEqual(complete["revision"], 2)
        self.manager._progress(job["id"], "matting", .1, 1)
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "completed")

    async def test_quality_warning_is_visible_and_does_not_claim_completed_acceptance(self):
        def needs_adjustment(*args, **kwargs):
            result = processor(*args, **kwargs)
            result["qa"] = {"status": "needs_adjustment", "warnings": ["循環接縫亮度跳變"]}
            return result
        with patch("effects.process_video", side_effect=needs_adjustment):
            job = await self.create(await self.preview())
            result = await self.finish(job["id"])
        self.assertEqual(result["status"], "needs_adjustment")
        self.assertEqual(result["qa"]["warnings"], ["循環接縫亮度跳變"])
        self.assertTrue(result["files"])

    async def test_postprocess_adjustments_publish_new_revision_keep_original_and_never_generate(self):
        job = await self.create(await self.preview())
        first = await self.finish(job["id"])
        old_file = next(entry["url"] for entry in first["files"] if entry["kind"] == "archive")
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {
            "trim_start": .25, "duration": .5, "black_level": .02, "playback": "loop", "anchor": "center"}})
        self.assertEqual(response.status, 202, await response.text())
        second = await self.finish(job["id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["result"]["frame_count"], 6)
        self.assertEqual(second["spec"]["duration"], .5)
        self.assertEqual(second["original_spec"]["duration"], 1)
        self.assertEqual([attempt["spec"]["duration"] for attempt in second["attempts"]], [1, .5])
        self.assertEqual((await self.client.get(old_file)).status, 200)
        self.assertTrue(any(file["name"] == "r0001_sequence.zip" for file in second["files"]))
        self.assertTrue(any(file["name"] == "r0002_sequence.zip" for file in second["files"]))
        self.assertEqual(self.generator.creates, [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)

    async def test_retry_switches_timing_modes_keeps_source_and_revision_history(self):
        job = await self.create(await self.preview())
        first = await self.finish(job["id"])
        old_archive = next(file["url"] for file in first["files"] if file["kind"] == "archive")
        source = self.manager.output_dir / job["id"] / first["source_file"]
        source_bytes = source.read_bytes()
        for revision, changes in enumerate([
                {"timing_mode": "retime", "trim_start": 0, "duration": 5},
                {"timing_mode": "trim", "trim_start": .5, "duration": 1.5}], start=2):
            with patch("effects.process_video", side_effect=processor) as process:
                response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": changes})
                self.assertEqual(response.status, 202, await response.text())
                result = await self.finish(job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["revision"], revision)
            for field, expected in changes.items():
                self.assertEqual(process.call_args.args[2][field], expected)
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual((await self.client.get(old_archive)).status, 200)
        self.assertEqual([attempt["spec"]["timing_mode"] for attempt in result["attempts"]], ["trim", "retime", "trim"])
        self.assertEqual(result["original_spec"]["timing_mode"], "trim")
        self.assertEqual(result["source_sha256"], self.source_hash)
        self.assertEqual(self.generator.creates, [])

    async def test_legacy_job_without_timing_mode_retries_as_trim(self):
        job = await self.create(await self.preview())
        await self.finish(job["id"])
        legacy = copy.deepcopy(self.manager.jobs[job["id"]]["spec"])
        legacy.pop("timing_mode")
        self.manager.update(job["id"], spec=legacy)
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={})
        self.assertEqual(response.status, 202, await response.text())
        result = await self.finish(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["spec"]["timing_mode"], "trim")
        self.assertEqual(result["spec"]["duration"], 1)
        self.assertEqual(self.generator.creates, [])

    async def test_retry_retime_recovers_completed_child_without_copy_or_gpu_restart(self):
        self.generator.complete_immediately = True
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None,
                                                source_duration=5, duration=3, timing_mode="trim"))
        job = await self.create(packet)
        failed = await self.finish(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["source_file"])
        self.assertEqual(self.generator.jobs[failed["child_job_id"]]["status"], "completed")
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {
            "timing_mode": "retime", "trim_start": 0, "duration": 2}})
        self.assertEqual(response.status, 202, await response.text())
        result = await self.finish(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["spec"]["timing_mode"], "retime")
        self.assertEqual(result["source_sha256"], self.source_hash)
        self.assertEqual(len(self.generator.creates), 1)

    async def test_invalid_postprocess_changes_do_not_modify_job_or_outputs(self):
        job = await self.create(await self.preview())
        await self.finish(job["id"])
        record = self.manager.job_dir / f"{job['id']}.json"
        before = record.read_bytes()
        files_before = {str(p.relative_to(self.manager.output_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in self.manager.output_dir.rglob("*") if p.is_file()}
        for payload in [{"seed": 5}, {"postprocess": {"video_model": "h3"}}, {"postprocess": {"source_asset_id": "f" * 32}},
                        {"postprocess": {"fps": 29}}, {"postprocess": {"trim_start": 2}}, {"postprocess": []},
                        {"postprocess": {"timing_mode": "unknown"}},
                        {"postprocess": {"timing_mode": "retime", "trim_start": .25}},
                        {"postprocess": {"timing_mode": "trim", "duration": 3}},
                        {"postprocess": {"timing_mode": "retime", "duration": 11}}]:
            response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json=payload)
            self.assertEqual(response.status, 400, await response.text())
            self.assertEqual(record.read_bytes(), before)
        files_after = {str(p.relative_to(self.manager.output_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in self.manager.output_dir.rglob("*") if p.is_file()}
        self.assertEqual(files_after, files_before)
        self.assertEqual(self.generator.creates, [])

    async def test_generated_postprocess_range_uses_actual_source_length_beyond_nominal_request(self):
        make_video(self.source, seconds=6)
        self.generator.complete_immediately = True
        job = await self.create(await self.preview(self.payload(source_mode="text", source_asset_id=None, source_duration=5)))
        await self.finish(job["id"])
        response = await self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {"trim_start": 5.25, "duration": .5}})
        self.assertEqual(response.status, 202, await response.text())
        result = await self.finish(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["spec"]["trim_start"], 5.25)
        self.assertEqual(result["spec"]["source_duration"], 5)
        self.assertEqual(len(self.generator.creates), 1)

    async def test_concurrent_retry_requests_commit_only_one_new_revision(self):
        job = await self.create(await self.preview())
        await self.finish(job["id"])
        started, release = threading.Event(), threading.Event()
        snapshot = effects.source_snapshot
        def delayed(path):
            if path.name == "source.mp4":
                started.set()
                if not release.wait(3):
                    raise ValueError("test preflight deadline")
            return snapshot(path)
        with patch("effects.source_snapshot", side_effect=delayed):
            first = asyncio.create_task(self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {"duration": .5}}))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            second = asyncio.create_task(self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {"duration": .75}}))
            await asyncio.sleep(.03)
            release.set()
            responses = await asyncio.gather(first, second)
            self.assertEqual([response.status for response in responses], [202, 409])
            result = await self.finish(job["id"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["spec"]["duration"], .5)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(self.generator.creates, [])

    async def test_cancel_serializes_with_retry_commit(self):
        job = await self.create(await self.preview())
        await self.finish(job["id"])
        started, release = threading.Event(), threading.Event()
        snapshot = effects.source_snapshot
        def delayed(path):
            if path.name == "source.mp4":
                started.set()
                release.wait(3)
            return snapshot(path)
        with patch("effects.source_snapshot", side_effect=delayed):
            retrying = asyncio.create_task(self.client.post(f"/api/effects/jobs/{job['id']}/retry", json={"postprocess": {"duration": .5}}))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            cancelling = asyncio.create_task(self.client.post(f"/api/effects/jobs/{job['id']}/cancel"))
            await asyncio.sleep(.03)
            release.set()
            retried, cancelled = await asyncio.gather(retrying, cancelling)
            self.assertEqual(retried.status, 202)
            self.assertEqual(cancelled.status, 200)
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "cancelled")
        self.assertTrue(self.manager.tasks[job["id"]].done())
        self.assertEqual(self.generator.creates, [])

    async def test_restarted_jobs_are_interrupted_and_do_not_autogenerate(self):
        packet = await self.preview(self.payload(source_mode="text", source_asset_id=None))
        job = await self.create(packet)
        await asyncio.sleep(0)
        restarted = EffectJobManager(self.app, self.root)
        self.assertEqual(restarted.jobs[job["id"]]["status"], "interrupted")
        self.assertEqual(restarted.tasks, {})
        self.assertEqual(len(self.generator.creates), 1)
        await self.manager.cancel(job["id"])

    async def test_unknown_files_and_path_escape_are_not_exposed(self):
        job = await self.create(await self.preview())
        complete = await self.finish(job["id"])
        for name in ["other.zip", "private.json", "..%2Fprivate.json"]:
            response = await self.client.get(f"/api/effects/jobs/{job['id']}/files/{name}")
            self.assertEqual(response.status, 404)
        internal = self.manager.jobs[job["id"]]
        internal["files"].append({"name": "escape.json", "relative_path": "../../private.json"})
        (self.root / "private.json").write_text("secret")
        response = await self.client.get(f"/api/effects/jobs/{job['id']}/files/escape.json")
        self.assertEqual(response.status, 404)

    async def test_import_is_available_when_engine_is_offline(self):
        response = await self.client.get("/api/effects/status")
        data = await response.json()
        self.assertEqual(response.status, 200)
        self.assertFalse(data["ready"])
        self.assertTrue(data["import_ready"])
        self.comfy.model_inventory.assert_not_awaited()
        packet = await self.preview()
        job = await self.create(packet)
        self.assertEqual((await self.finish(job["id"]))["status"], "completed")

    async def test_reference_only_inventory_does_not_advertise_text_effect_generation(self):
        self.comfy.system_stats.return_value = {"devices": [{"name": "GPU"}]}
        self.comfy.model_inventory.return_value = {"text_encoder": True, "audio_vae": True, "video_vae": True, "ref2va": True}
        response = await self.client.get("/api/effects/status")
        result = await response.json()
        self.assertFalse(result["ready"])
        self.assertTrue(result["import_ready"])
        self.assertFalse(next(item for item in result["models"] if item["id"] == "h3")["ready"])


class EffectsUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_effects_source_upload_keeps_original_rgba(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        image = root / "input.png"
        Image.new("RGBA", (32, 32), (220, 40, 10, 70)).save(image)
        assets = studio.AssetStore(root / "assets")
        app = web.Application()
        async def upload(request):
            return web.json_response(await assets.save_upload(request))
        app.router.add_post("/api/assets", upload)
        async with TestClient(TestServer(app)) as client:
            with image.open("rb") as handle:
                body = FormData()
                body.add_field("kind", "effects-source")
                body.add_field("file", handle, filename="input.png", content_type="image/png")
                response = await client.post("/api/assets", data=body)
                result = await response.json()
            self.assertEqual(response.status, 200)
            self.assertFalse(result["transparency_filled"])
            with Image.open(assets.path_for(result["id"])) as uploaded:
                self.assertEqual(uploaded.mode, "RGBA")
                self.assertEqual(uploaded.getpixel((0, 0))[3], 70)


if __name__ == "__main__":
    unittest.main()
