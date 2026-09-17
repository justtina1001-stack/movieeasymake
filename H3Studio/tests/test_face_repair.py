import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from aiohttp.test_utils import TestClient, TestServer
import app as studio
from domain import build_workflow, compile_request, required_asset_ids, RequestError
from face_repair import repair_options
from tests.test_continuation import make_video

IMAGE = "a" * 32
SOURCE = "b" * 32


class FaceRepairGraphTests(unittest.TestCase):
    def test_face_pipeline_patches_sampler_and_preserves_source_audio(self):
        base = compile_request({"mode": "r2v", "prompt": "Restore the same character.",
            "references": [{"alias": "person", "type": "character", "image_asset_ids": [IMAGE]}]})
        options = {**repair_options({}, 15.08334), "source_asset_id": SOURCE}
        compiled = replace(base, face_repair=options)
        self.assertEqual(required_asset_ids(compiled), [IMAGE, SOURCE])
        graph = build_workflow(compiled, {IMAGE: "reference.png", SOURCE: "source.mp4"}, "repair")
        def find(kind):
            return next((key, node["inputs"]) for key, node in graph.items() if node["class_type"] == kind)
        tracker_id, tracker = find("H3FaceTrackCrop")
        denoise_id, denoise = find("H3PerFrameDenoise")
        _, schedule = find("BasicScheduler")
        _, guider = find("BasicGuider")
        _, sampler = find("SamplerCustomAdvanced")
        self.assertEqual(schedule["model"], [denoise_id, 2])
        self.assertEqual(guider["model"], [denoise_id, 2])
        self.assertEqual(sampler["latent_image"], [denoise_id, 0])
        self.assertEqual(schedule["denoise"], .3)
        self.assertFalse(tracker["identity_track"])
        self.assertEqual(denoise["transform"], [tracker_id, 1])
        stitch_id, stitch = find("H3FaceStitch")
        component_id, _ = find("GetVideoComponents")
        _, video = find("CreateVideo")
        self.assertEqual(video["images"], [stitch_id, 0])
        self.assertEqual(video["audio"], [component_id, 1])
        self.assertEqual(stitch["undetected_frames"], "skip")
        self.assertEqual(stitch["base_images"], [component_id, 0])
        self.assertFalse(any(n["class_type"] == "VAEDecodeAudio" for n in graph.values()))
        _, condition = find("MiniMaxH3ReferenceToVideo")
        self.assertEqual((condition["width"], condition["height"]), (512, 512))
        # Every edge points to an existing node; no output decodes whole-frame replacement.
        for node in graph.values():
            for value in node["inputs"].values():
                if isinstance(value, list):
                    self.assertIn(value[0], graph)

    def test_options_align_to_source_and_reject_invalid_requests(self):
        self.assertEqual(repair_options({}, 15.08334)["frames"], 124)
        ending = repair_options({"start": 10, "duration": 15}, 15.08334)
        self.assertEqual(ending["frames"] % 17, 5)
        self.assertLessEqual(ending["frames"] + ending["start_frame"], 362)
        for values in ({"start": -1}, {"duration": 16}, {"start": 14}, {"strength": .9},
                       {"canvas": 1024}, {"start": float("nan")}, {"seed": -1}, {"selection": []}):
            with self.subTest(values=values), self.assertRaises(RequestError):
                repair_options(values, 15)


class FaceRepairAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.multiple(studio, DATA_DIR=self.root, ASSET_DIR=self.root / "assets",
            JOB_DIR=self.root / "jobs", OUTPUT_DIR=self.root / "outputs", CONFIG_PATH=self.root / "config.json"))
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.assets = self.app["assets"]
        self.image = self.assets.save_image(Image.new("RGB", (64, 64)), "face.png", "face-repair-reference")
        self.source = self.root / "outputs/source.mp4"
        make_video(self.source, [(120, 70, 30)] * 144, with_audio=True)
        self.original = self.source.read_bytes()
        self.app["jobs"].jobs[SOURCE] = {"id": SOURCE, "name": "source", "status": "completed", "local_output": self.source.name}

    async def test_repair_creates_separate_job_and_preserves_source(self):
        with patch("app.repair_capabilities", new=AsyncMock(return_value={"ready": True})), \
             patch.object(self.app["jobs"], "_run", new=AsyncMock()):
            response = await self.client.post(f"/api/jobs/{SOURCE}/face-repair", json={"reference_image_asset_id": self.image["id"]})
            self.assertEqual(response.status, 202, await response.text())
            job = await response.json()
            await self.app["jobs"].tasks[job["id"]]
        self.assertNotEqual(job["id"], SOURCE)
        self.assertEqual(job["face_repair"]["source_job_id"], SOURCE)
        self.assertEqual(job["status"], "queued")
        self.assertEqual((job["width"], job["height"]), (96, 64))
        self.assertEqual(self.source.read_bytes(), self.original)
        clip = self.assets.path_for(job["face_repair"]["source_asset_id"])
        info = studio.probe_video(clip)
        self.assertTrue(info["has_audio"])
        self.assertAlmostEqual(info["duration"], job["duration"], places=1)
        raw = json.loads((self.root / "jobs" / f"{job['id']}.request.json").read_text(encoding="utf8"))
        self.assertIn(job["face_repair"]["source_asset_id"], studio.request_asset_ids(raw))

    async def test_missing_nodes_never_queues_a_normal_generation(self):
        with patch("app.repair_capabilities", new=AsyncMock(return_value={"ready": False, "error": "restart required"})), \
             patch.object(self.app["jobs"], "create") as create:
            response = await self.client.post(f"/api/jobs/{SOURCE}/face-repair", json={"reference_image_asset_id": self.image["id"]})
        self.assertEqual(response.status, 400)
        create.assert_not_called()

    async def test_unfinished_source_and_invalid_ranges_are_rejected(self):
        self.app["jobs"].jobs[SOURCE]["status"] = "running"
        response = await self.client.post(f"/api/jobs/{SOURCE}/face-repair", json={})
        self.assertEqual(response.status, 400)
        self.app["jobs"].jobs[SOURCE]["status"] = "completed"
        response = await self.client.post(f"/api/jobs/{SOURCE}/face-repair", json={"reference_image_asset_id": self.image["id"], "start": 100})
        self.assertEqual(response.status, 400)
