"""Long replacement segments wait for their actual, continuity-aware final prompts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import app as studio
from domain import compile_request
from generation_review import GenerationReviewError
from tests.test_continuation import make_video
from tests.test_jobs import FakeBatchComfy


class ReplacementReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job_dir, self.output_dir = self.root / "jobs", self.root / "outputs"
        self.job_dir.mkdir(); self.output_dir.mkdir()
        self.paths = patch.multiple(studio, JOB_DIR=self.job_dir, OUTPUT_DIR=self.output_dir)
        self.paths.start()
        asset_dir = self.root / "assets"; asset_dir.mkdir()
        self.source_id = "d" * 32
        make_video(asset_dir / f"{self.source_id}.mp4", [(20, 30, 40)] * (16 * 24), with_audio=False)
        self.assets = studio.AssetStore(asset_dir)
        self.assets.register_derived_video(self.source_id, "source.mp4", "replacement-performance-video")
        image = self.assets.save_image(Image.new("RGB", (64, 64), "red"), "new.png", "replacement-character")
        original = self.assets.save_image(Image.new("RGB", (64, 64), "blue"), "original.png", "replacement-source-character")
        generated = self.root / "generated.mp4"
        make_video(generated, [(60, 120, 180)] * (10 * 24), with_audio=False)
        self.fake = FakeBatchComfy(generated.read_bytes())
        self.payload = {"mode": "replace", "prompt": "The new actor replaces the original actor and follows the original performance.",
                        "aspect_ratio": "16:9", "megapixels": 0.4, "duration": 5, "seed": 42, "steps": 4,
                        "replacement_auto_split": True, "replacement_continuity": True, "replacement_audio_mode": "original",
                        "replacement_source_image_asset_ids": [original["id"]],
                        "references": [{"alias": "New actor", "type": "character", "image_asset_ids": [image["id"]],
                                        "video_asset_id": self.source_id, "video_use_audio": False}]}
        self.manager = studio.JobManager(self.assets, self.fake)

    async def asyncTearDown(self):
        for task in self.manager.tasks.values():
            if not task.done():
                task.cancel()
        import asyncio
        await asyncio.gather(*self.manager.tasks.values(), return_exceptions=True)
        self.paths.stop()
        self.temp.cleanup()

    async def waiting(self):
        parent = self.manager.create_replacement_batch(compile_request(self.payload), self.payload,
            {"width": 96, "height": 64, "fps": 24.0, "duration": 16.0, "has_audio": False},
            studio.replacement_segment_plan(16, smart=False), require_prompt_review=True)
        await self.manager.tasks[parent["id"]]
        self.assertEqual(parent["status"], "awaiting_review", parent.get("error"))
        self.assertEqual(self.fake.counter, 0)
        return parent

    async def test_each_segment_uses_reviewed_compilation_and_new_continuity_review(self):
        parent = await self.waiting()
        first = copy.deepcopy(parent["review"])
        first_compiled = copy.deepcopy(parent["pending_review"]["compiled"])
        persisted = json.loads((self.job_dir / f"{parent['id']}.json").read_text(encoding="utf8"))
        self.assertEqual(persisted["review"], first)
        await self.manager.approve_replacement_review(parent["id"], {"review_id": first["id"]})
        await self.manager.tasks[parent["id"]]
        self.assertEqual(parent["status"], "awaiting_review", parent.get("error"))
        self.assertEqual(self.fake.counter, 1)
        first_child = parent["segments"][0]["child_job_id"]
        self.assertEqual((self.job_dir / f"{first_child}.prompt.txt").read_text(encoding="utf8"), first["prompts"][0]["prompt"])
        second = copy.deepcopy(parent["review"])
        self.assertNotEqual(second["id"], first["id"])
        self.assertTrue(parent["segments"][0]["continuity_asset_id"])
        self.assertGreater(len(parent["pending_review"]["compiled"]["reference_images"]), len(first_compiled["reference_images"]))
        with patch.object(studio, "compile_request", side_effect=AssertionError("approved segment must not recompile")):
            await self.manager.approve_replacement_review(parent["id"], {"review_id": second["id"]})
            await self.manager.tasks[parent["id"]]
        self.assertEqual(parent["status"], "completed", parent.get("error"))
        self.assertEqual(self.fake.counter, 2)
        second_child = parent["segments"][1]["child_job_id"]
        self.assertEqual((self.job_dir / f"{second_child}.prompt.txt").read_text(encoding="utf8"), second["prompts"][0]["prompt"])

    async def test_wrong_id_original_setting_or_saved_packet_change_never_submits(self):
        parent = await self.waiting()
        with self.assertRaises(GenerationReviewError):
            await self.manager.approve_replacement_review(parent["id"], {"review_id": "wrong"})
        path = self.job_dir / f"{parent['id']}.request.json"
        changed = {**self.payload, "seed": 43}
        path.write_text(json.dumps(changed), encoding="utf8")
        with self.assertRaises(GenerationReviewError):
            await self.manager.approve_replacement_review(parent["id"], {"review_id": parent["review"]["id"]})
        path.write_text(json.dumps(self.payload), encoding="utf8")
        parent["pending_review"]["compiled"]["prompt"] = "Altered final prompt."
        with self.assertRaises(GenerationReviewError):
            await self.manager.approve_replacement_review(parent["id"], {"review_id": parent["review"]["id"]})
        self.assertEqual(self.fake.counter, 0)

    async def test_restart_and_resume_cannot_reuse_prior_approval(self):
        parent = await self.waiting()
        parent.update(status="queued", approved_review_id=parent["review"]["id"])
        self.manager._persist(parent)
        self.manager = studio.JobManager(self.assets, self.fake)
        restored = self.manager.jobs[parent["id"]]
        self.assertEqual(restored["status"], "interrupted")
        self.assertIsNone(restored["approved_review_id"])
        self.manager.resume_recoverable_jobs()
        await self.manager.tasks[parent["id"]]
        self.assertEqual(restored["status"], "awaiting_review", restored.get("error"))
        await self.manager.cancel(parent["id"])
        self.assertEqual(restored["status"], "cancelled")
        await self.manager.resume_batch(parent["id"], {"require_prompt_review": True})
        await self.manager.tasks[parent["id"]]
        self.assertEqual(restored["status"], "awaiting_review")
        self.assertEqual(self.fake.counter, 0)

    async def test_legacy_resume_upgrades_to_review_before_first_submission(self):
        with patch.object(self.manager, "_run_replacement_batch", new=AsyncMock()):
            parent = self.manager.create_replacement_batch(compile_request(self.payload), self.payload,
                {"width": 96, "height": 64, "fps": 24.0, "duration": 16.0, "has_audio": False},
                studio.replacement_segment_plan(16, smart=False))
            await self.manager.tasks[parent["id"]]
        self.manager.update(parent["id"], status="interrupted")
        await self.manager.resume_batch(parent["id"], {"require_prompt_review": True})
        await self.manager.tasks[parent["id"]]
        self.assertEqual(parent["status"], "awaiting_review", parent.get("error"))
        self.assertEqual(self.fake.counter, 0)
