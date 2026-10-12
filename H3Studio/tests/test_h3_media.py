from concurrent.futures import ThreadPoolExecutor
import asyncio
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import av
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from app import AssetStore, JobManager, create_app, normalize_h3_reference_video, probe_video
from comfy_client import ComfyClient
from domain import compile_request
from h3_media import normalized_reference_video, prepare_media_payload
from shortfilm import new_project, new_scene, new_shot
from settings import ConnectionSettings


def source_video(path, fps=24, seconds=5, audio=False, tone=False, timestamp_offset=0):
    count = round(fps * seconds)
    with av.open(str(path), "w") as output:
        video = output.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = 96, 64, "yuv420p"
        sound = output.add_stream("aac", rate=48000) if audio else None
        if sound is not None:
            sound.layout = "stereo"
        for index in range(count):
            pixels = np.full((64, 96, 3), round(200 * index / max(1, count - 1)), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index + round(timestamp_offset * fps), Fraction(1, fps)
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        if sound is not None:
            samples = round(seconds * 48000)
            values = np.sin(np.arange(samples) * (2 * np.pi * 440 / 48000)) * 0.25 if tone else np.zeros(samples)
            frame = av.AudioFrame.from_ndarray(np.stack([values, values]).astype(np.float32), format="fltp", layout="stereo")
            frame.sample_rate, frame.pts, frame.time_base = 48000, 0, Fraction(1, 48000)
            for packet in sound.encode(frame):
                output.mux(packet)
            for packet in sound.encode():
                output.mux(packet)


def register_video(assets, asset_id, **options):
    source_video(assets.directory / f"{asset_id}.mp4", **options)
    assets.register_derived_video(asset_id, "original.mp4", "reference")


class H3MediaTests(unittest.TestCase):
    def test_nonzero_source_timestamps_are_normalized_to_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            asset_id = "d" * 32
            register_video(assets, asset_id, fps=30, timestamp_offset=10)
            result = normalized_reference_video(assets, asset_id, 124, probe_video, normalize_h3_reference_video)
            with av.open(result["path"]) as container:
                frames = list(container.decode(video=0))
            self.assertEqual(len(frames), 124)
            self.assertEqual(float(frames[0].time), 0)
            self.assertAlmostEqual(float(frames[60].to_ndarray(format="rgb24").mean()), 100, delta=4)
            self.assertAlmostEqual(float(frames[-1].to_ndarray(format="rgb24").mean()), 200, delta=3)

    def test_two_second_reference_pads_only_to_its_own_h3_grid(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            asset_id = "d" * 32
            register_video(assets, asset_id, seconds=2, audio=True)
            result = normalized_reference_video(assets, asset_id, 124, probe_video, normalize_h3_reference_video)
            self.assertEqual(result["frames"], 56)
            self.assertAlmostEqual(probe_video(Path(result["path"]))["duration"], 56 / 24, places=2)

    def test_24_30_60_fps_preserve_timing_and_pad_last_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            for fps in (24, 30, 60):
                with self.subTest(fps=fps):
                    asset_id = f"{fps:032x}"
                    register_video(assets, asset_id, fps=fps, audio=True)
                    result = normalized_reference_video(assets, asset_id, 124, probe_video, normalize_h3_reference_video)
                    with av.open(result["path"]) as container:
                        self.assertEqual(container.streams.video[0].average_rate, 24)
                        self.assertEqual(container.streams.video[0].codec_context.name, "h264")
                        self.assertEqual(container.streams.audio[0].codec_context.name, "pcm_s16le")
                        audio_duration = float(container.streams.audio[0].duration * container.streams.audio[0].time_base)
                        frames = list(container.decode(video=0))
                    self.assertEqual(len(frames), 124)
                    self.assertAlmostEqual(audio_duration, 124 / 24, delta=0.03)
                    with av.open(result["path"]) as container:
                        self.assertEqual(sum(frame.samples for frame in container.decode(audio=0)), 124 * 2000)
                    self.assertAlmostEqual(float(frames[60].to_ndarray(format="rgb24").mean()), 100, delta=4)
                    self.assertAlmostEqual(float(frames[-1].to_ndarray(format="rgb24").mean()), 200, delta=3)

    def test_normalization_cache_reuses_video_and_invalidates_changed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            asset_id = "d" * 32
            register_video(assets, asset_id, fps=30)
            normalize = Mock(wraps=normalize_h3_reference_video)
            first = normalized_reference_video(assets, asset_id, 124, probe_video, normalize)
            second = normalized_reference_video(assets, asset_id, 124, probe_video, normalize)
            self.assertEqual(first["asset_id"], second["asset_id"])
            self.assertEqual(normalize.call_count, 1)
            register_video(assets, asset_id, fps=60)
            changed = normalized_reference_video(assets, asset_id, 124, probe_video, normalize)
            self.assertNotEqual(first["asset_id"], changed["asset_id"])
            self.assertEqual(normalize.call_count, 2)

    def test_parallel_previews_share_one_complete_cached_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            asset_id = "d" * 32
            register_video(assets, asset_id, fps=60)
            normalize = Mock(wraps=normalize_h3_reference_video)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: normalized_reference_video(
                    assets, asset_id, 124, probe_video, normalize), range(2)))
            self.assertEqual(normalize.call_count, 1)
            self.assertEqual(results[0]["asset_id"], results[1]["asset_id"])
            self.assertEqual(assets.metadata(results[0]["asset_id"])["frame_count"], 124)

    def test_mixed_silent_and_audible_videos_keep_audio_labels_in_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            silent_id, audible_id, voice_id = "d" * 32, "e" * 32, "c" * 32
            register_video(assets, silent_id)
            register_video(assets, audible_id, audio=True)
            register_video(assets, voice_id, audio=True)
            payload = {"mode": "r2v", "prompt": "Hero speaks.", "duration": 5, "references": [
                {"alias": "Hero", "type": "character", "video_asset_ids": [silent_id, audible_id],
                 "video_use_audio": True, "audio_asset_id": voice_id}]}
            prepared = prepare_media_payload(payload, assets, probe_video, normalize_h3_reference_video)
            compiled = compile_request(prepared)
            self.assertEqual(compiled.reference_video_use_audio, [False, True])
            self.assertEqual(compiled.mapping[0]["video_audio_tags"], ["<Audio 1>"])
            self.assertEqual(compiled.mapping[0]["audio_tag"], "<Audio 2>")
            definition = next(line for line in compiled.prompt.splitlines() if line.startswith("<Audio 1> is"))
            self.assertIn("<Video 2>", definition)
            self.assertNotIn("<Video 1>", definition)

    def test_silent_video_does_not_reserve_audio_label_before_voice(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            video_id, voice_id = "d" * 32, "c" * 32
            register_video(assets, video_id, fps=30)
            register_video(assets, voice_id, audio=True)
            payload = {"mode": "r2v", "prompt": "Hero speaks.", "duration": 5, "references": [
                {"alias": "Hero", "type": "character", "video_asset_id": video_id,
                 "video_use_audio": True, "audio_asset_id": voice_id}]}
            prepared = prepare_media_payload(payload, assets, probe_video, normalize_h3_reference_video)
            self.assertFalse(prepared["references"][0]["video_use_audio"])
            self.assertEqual(prepared["references"][0]["video_asset_id"], video_id)
            compiled = compile_request(prepared)
            self.assertEqual(compiled.mapping[0]["video_audio_tags"], [])
            self.assertEqual(compiled.mapping[0]["audio_tag"], "<Audio 1>")
            self.assertTrue(prepared["h3_preparation_warnings"])

    def test_reference_clip_limit_trims_video_and_audio_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            asset_id = "d" * 32
            register_video(assets, asset_id, fps=30, seconds=8, audio=True)
            payload = {"mode": "r2v", "prompt": "Follow the motion.", "duration": 5,
                       "references": [{"alias": "Motion", "type": "motion", "video_asset_id": asset_id}]}
            prepared = prepare_media_payload(payload, assets, probe_video, normalize_h3_reference_video)
            result = prepared["media_preflight"]["normalized_videos"][asset_id]
            self.assertEqual(result["frames"], 124)
            info = probe_video(assets.path_for(result["asset_id"]))
            self.assertAlmostEqual(info["duration"], 124 / 24, places=2)
            self.assertTrue(any("裁切" in message for message in prepared["h3_preparation_warnings"]))

    def test_complete_short_replacement_uses_source_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = AssetStore(Path(temporary))
            source_id, image_id = "d" * 32, "a" * 32
            register_video(assets, source_id, fps=30, seconds=8)
            Image.new("RGB", (32, 32)).save(assets.directory / f"{image_id}.png")
            payload = {"mode": "replace", "prompt": "Replace Hero.", "duration": 5, "references": [
                {"alias": "Hero", "type": "character", "image_asset_ids": [image_id], "video_asset_id": source_id}]}
            prepared = prepare_media_payload(payload, assets, probe_video, normalize_h3_reference_video)
            self.assertEqual(prepared["duration"], 8)
            self.assertEqual(compile_request(prepared).length, 192)


class H3MediaJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_and_remote_clients_upload_mov_with_video_mime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = AssetStore(root / "assets")
            asset_id = "d" * 32
            register_video(assets, asset_id, audio=True)
            prepared = normalized_reference_video(assets, asset_id, 124, probe_video, normalize_h3_reference_video)
            path = assets.path_for(prepared["asset_id"])
            self.assertEqual(path.suffix, ".mov")
            self.assertEqual(assets.metadata(prepared["asset_id"])["extension"], ".mov")
            uploads = []

            async def upload(request):
                reader = await request.multipart()
                values = {}
                async for part in reader:
                    if part.name == "image":
                        values.update(filename=part.filename, mime=part.headers.get("Content-Type"), data=await part.read())
                    else:
                        values[part.name] = await part.text()
                uploads.append(values)
                return web.json_response({"name": values["filename"], "subfolder": values["subfolder"]})

            server_app = web.Application()
            server_app.router.add_post("/upload/image", upload)
            server = TestServer(server_app)
            await server.start_server()
            try:
                for mode in ("local", "remote"):
                    client = ComfyClient(ConnectionSettings(mode=mode, base_url=str(server.make_url("/")),
                                                           comfy_dir=str(root), auto_start_local=False), root)
                    uploaded = await client.upload_asset(path, "h3studio/audit")
                    self.assertTrue(uploaded.endswith(".mov"))
                    self.assertEqual(uploads[-1]["mime"], "video/quicktime")
                    self.assertEqual(uploads[-1]["data"], path.read_bytes())
            finally:
                await server.close()

    async def test_server_shortfilm_batch_routes_keep_running_job_on_cancel_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("app.DATA_DIR", root), patch("app.ASSET_DIR", root / "assets"), \
                 patch("app.JOB_DIR", root / "jobs"), patch("app.OUTPUT_DIR", root / "outputs"):
                app = create_app()
                app.on_startup.clear()
                app["h3_prompt_translator"].prepare = AsyncMock(side_effect=lambda payload: payload)
                project, scene, shot = new_project("Route regression"), new_scene("Scene"), new_shot()
                shot["action"], shot["continue_previous"] = "A subject walks.", False
                scene["shots"], project["scenes"] = [shot], [scene]
                project = app["shortfilms"].create(project)
                job = {"id": "f" * 32, "status": "running", "output": None}
                app["jobs"].jobs[job["id"]] = job
                manager = app["shortfilm_batches"]
                manager.compile_submit = AsyncMock(return_value=job)
                manager.poll_interval = 0.01
                client = TestClient(TestServer(app))
                await client.start_server()
                try:
                    started = await client.post(f"/api/shortfilms/{project['id']}/batch", json={})
                    self.assertEqual(started.status, 202, await started.text())
                    batch = await started.json()
                    await asyncio.sleep(0.03)
                    listed = await client.get(f"/api/shortfilm-batches?project_id={project['id']}")
                    self.assertEqual((await listed.json())[0]["id"], batch["id"])
                    cancelled = await client.post(f"/api/shortfilm-batches/{batch['id']}/cancel", json={})
                    self.assertEqual((await cancelled.json())["status"], "cancelled")
                    self.assertEqual(job["status"], "running")
                    resumed = await client.post(f"/api/shortfilm-batches/{batch['id']}/resume", json={})
                    self.assertEqual(resumed.status, 202, await resumed.text())
                    job.update(status="completed", output={"filename": "mock.mp4"})
                    await asyncio.wait_for(manager.tasks[batch["id"]], timeout=2)
                    self.assertEqual(manager.get(batch["id"])["status"], "completed")
                    manager.compile_submit.assert_awaited_once()
                finally:
                    await client.close()

    async def test_short_replacement_original_generated_and_mute_are_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = AssetStore(root / "assets")
            source_id, image_id = "d" * 32, "a" * 32
            register_video(assets, source_id, fps=30, audio=True, tone=True)
            Image.new("RGB", (32, 32)).save(assets.directory / f"{image_id}.png")
            generated = root / "generated.mp4"
            source_video(generated, seconds=124 / 24, audio=True)
            fake = Mock()
            fake.fetch_output = AsyncMock(return_value=(generated.read_bytes(), "video/mp4"))
            with patch("app.JOB_DIR", root / "jobs"), patch("app.OUTPUT_DIR", root / "outputs"):
                manager = JobManager(assets, fake)
                for audio_mode in ("original", "generated", "mute"):
                    payload = {"mode": "replace", "prompt": "Replace Hero.", "duration": 5, "references": [
                        {"alias": "Hero", "type": "character", "image_asset_ids": [image_id], "video_asset_id": source_id}]}
                    compiled = compile_request(payload)
                    job_id = {"original": "1", "generated": "2", "mute": "3"}[audio_mode] * 32
                    manager.jobs[job_id] = {"id": job_id, "name": "replacement", "replacement_source_asset_id": source_id,
                                            "replacement_source_duration": 5, "replacement_audio_mode": audio_mode}
                    await manager.complete_job(job_id, compiled, "mock-prompt", {"filename": "generated.mp4"})
                    job = manager.jobs[job_id]
                    self.assertEqual(job["status"], "completed")
                    self.assertEqual(job["duration"], 5)
                    output_path = root / "outputs" / job["local_output"]
                    with av.open(str(output_path)) as container:
                        self.assertEqual(bool(container.streams.audio), audio_mode != "mute")
                        if audio_mode != "mute":
                            samples = next(container.decode(audio=0)).to_ndarray()
                            self.assertEqual(float(np.abs(samples).mean()) > 0.05, audio_mode == "original")
                    with av.open(str(output_path)) as container:
                        self.assertEqual(len(list(container.decode(video=0))), 120)

    async def test_compile_and_submit_share_real_stream_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("app.DATA_DIR", root), patch("app.ASSET_DIR", root / "assets"), \
                 patch("app.JOB_DIR", root / "jobs"), patch("app.OUTPUT_DIR", root / "outputs"):
                app = create_app()
                app.on_startup.clear()
                app["h3_prompt_translator"].prepare = AsyncMock(side_effect=lambda payload: payload)
                app["jobs"].create = Mock(side_effect=lambda compiled, payload: {"duration": compiled.actual_duration, "payload": payload})
                assets = app["assets"]
                video_id, voice_id = "d" * 32, "c" * 32
                register_video(assets, video_id, fps=60)
                register_video(assets, voice_id, audio=True)
                payload = {"mode": "r2v", "prompt": "Hero speaks.", "duration": 5, "references": [
                    {"alias": "Hero", "type": "character", "video_asset_id": video_id,
                     "video_use_audio": True, "audio_asset_id": voice_id}]}
                client = TestClient(TestServer(app))
                await client.start_server()
                try:
                    preview = await client.post("/api/compile", json=payload)
                    self.assertEqual(preview.status, 200, await preview.text())
                    data = await preview.json()
                    self.assertEqual(data["mapping"][0]["audio_tag"], "<Audio 1>")
                    submitted = await client.post("/api/render", json=data["prepared_payload"])
                    self.assertEqual(submitted.status, 202, await submitted.text())
                    job = await submitted.json()
                    self.assertEqual(job["payload"]["media_preflight"]["normalized_videos"],
                                     data["prepared_payload"]["media_preflight"]["normalized_videos"])
                    self.assertEqual(job["payload"]["references"], data["prepared_payload"]["references"])
                finally:
                    await client.close()
