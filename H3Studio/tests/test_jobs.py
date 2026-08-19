import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import av
from PIL import Image

from app import (
    AssetStore,
    JobManager,
    RequestError,
    clean_job_name,
    history_execution_timing,
    output_filename_stem,
    output_timestamp,
    paginate_job_records,
    request_asset_ids,
    replacement_segment_plan,
    sort_job_records,
)
from domain import compile_request
from tests.test_continuation import make_video


class JobListingTests(unittest.TestCase):
    def test_output_timestamp_is_filesystem_safe_and_precise(self):
        moment = datetime(2026, 8, 6, 9, 10, 11, 123456, tzinfo=timezone.utc)
        self.assertEqual(output_timestamp(moment), "2026-08-06_09-10-11_123456")
        self.assertEqual(output_filename_stem("", moment), "2026-08-06_09-10-11_123456")

    def test_job_name_takes_priority_for_output_filename(self):
        self.assertEqual(output_filename_stem("金色圖騰：測試/01"), "金色圖騰：測試_01")
        self.assertEqual(output_filename_stem("CON"), "_CON")

    def test_job_name_is_normalized_and_limited(self):
        self.assertEqual(clean_job_name("  妲己   替換測試 01  "), "妲己 替換測試 01")
        with self.assertRaisesRegex(RequestError, "最多 80"):
            clean_job_name("名" * 81)

    def test_jobs_are_paginated_twenty_per_page(self):
        records = [{"id": f"job-{index:02}", "name": f"任務 {index}", "mode": "r2v"} for index in range(45)]
        first = paginate_job_records(records, page=1, page_size=20)
        third = paginate_job_records(records, page=3, page_size=20)
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(third["items"]), 5)
        self.assertEqual(third["total_pages"], 3)
        self.assertEqual(third["total"], 45)

    def test_jobs_can_be_found_by_name_or_id(self):
        records = [
            {"id": "abc123", "name": "妲己替換", "mode": "replace"},
            {"id": "def456", "name": "神殿動畫", "mode": "t2v"},
        ]
        self.assertEqual(paginate_job_records(records, 1, 20, "妲己")["items"][0]["id"], "abc123")
        self.assertEqual(paginate_job_records(records, 1, 20, "def456")["items"][0]["name"], "神殿動畫")

    def test_favorite_jobs_are_sorted_before_newer_regular_jobs(self):
        records = [
            {"id": "new", "created_at": "2026-08-15T10:00:00+00:00", "favorite": False},
            {"id": "favorite", "created_at": "2026-08-14T10:00:00+00:00", "favorite": True},
            {"id": "old", "created_at": "2026-08-13T10:00:00+00:00", "favorite": False},
        ]
        self.assertEqual([job["id"] for job in sort_job_records(records)], ["favorite", "new", "old"])

    def test_history_execution_time_uses_comfy_timestamps(self):
        timing = history_execution_timing({"status": {"messages": [
            ["execution_start", {"timestamp": 1_000}],
            ["execution_success", {"timestamp": 72_250}],
        ]}})
        self.assertEqual(timing["execution_seconds"], 71.25)
        self.assertTrue(timing["generation_started_at"].endswith("+00:00"))

    def test_request_asset_ids_finds_reusable_nested_assets(self):
        payload = {
            "first_image_asset_id": "a" * 32,
            "references": [{"image_asset_ids": ["b" * 32], "video_asset_id": "c" * 32}],
            "continuation_source_job_id": "d" * 32,
        }
        self.assertEqual(request_asset_ids(payload), {"a" * 32, "b" * 32, "c" * 32})


class FakeRecoveryComfy:
    async def get_history(self, _prompt_id):
        return {
            "outputs": {"15": {"images": [{"filename": "recovered.mp4", "subfolder": "H3Studio", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []},
        }

    @staticmethod
    def history_state(_history):
        return "success"

    @staticmethod
    def history_error(_history):
        return "unexpected"

    async def fetch_output(self, _output):
        return b"recovered-video", "video/mp4"


class FakeBatchComfy:
    def __init__(self, video_bytes):
        self.video_bytes = video_bytes
        self.counter = 0

    async def ensure_running(self):
        return None

    async def upload_asset(self, path, _subfolder):
        return path.name

    async def run_prompt(self, _workflow, progress, _cancel_event):
        self.counter += 1
        await progress({"status": "running", "progress": 50, "current_node": None})
        output = {"filename": f"fake-{self.counter}.mp4", "subfolder": "H3Studio", "type": "output"}
        history = {"outputs": {"15": {"video": output}}, "status": {"messages": []}}
        return f"prompt-{self.counter}", history

    async def fetch_output(self, _output):
        return self.video_bytes, "video/mp4"


class JobRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_progress_connection_is_reconciled_from_comfy_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job_dir = root / "jobs"
            output_dir = root / "outputs"
            job_dir.mkdir()
            output_dir.mkdir()
            job_id = "a" * 32
            job = {
                "id": job_id,
                "name": "恢復測試",
                "mode": "t2v",
                "status": "failed",
                "progress": 5,
                "current_node": None,
                "error": "與 ComfyUI 的進度連線中斷。",
                "prompt_id": "prompt-1",
                "output": None,
                "width": 864,
                "height": 480,
                "duration": 5.167,
                "created_at": "2026-08-15T00:00:00+00:00",
                "updated_at": "2026-08-15T00:00:01+00:00",
            }
            request = {
                "mode": "t2v",
                "prompt": "A simple test video.",
                "aspect_ratio": "16:9",
                "megapixels": 0.4,
                "duration": 5,
                "seed": 1,
                "steps": 20,
            }
            (job_dir / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            (job_dir / f"{job_id}.request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

            with patch("app.JOB_DIR", job_dir), patch("app.OUTPUT_DIR", output_dir):
                manager = JobManager(object(), FakeRecoveryComfy())
                recovered = await manager.reconcile_job(job_id)

            self.assertTrue(recovered)
            self.assertEqual(manager.jobs[job_id]["status"], "completed")
            self.assertEqual(manager.jobs[job_id]["progress"], 100)
            self.assertIsNone(manager.jobs[job_id]["error"])
            self.assertEqual((output_dir / f"{job_id}.mp4").read_bytes(), b"recovered-video")

    async def test_long_replacement_batch_runs_hidden_children_and_merges_original_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "assets"
            job_dir = root / "jobs"
            output_dir = root / "outputs"
            asset_dir.mkdir()
            job_dir.mkdir()
            output_dir.mkdir()
            source_id = "d" * 32
            source_path = asset_dir / f"{source_id}.mp4"
            make_video(source_path, [(20, 30, 40)] * (16 * 24), with_audio=True)
            assets = AssetStore(asset_dir)
            assets.register_derived_video(source_id, "source.mp4", "replacement-performance-video")
            image = assets.save_image(Image.new("RGB", (64, 64), (200, 30, 40)), "new.png", "replacement-character")
            generated = root / "generated.mp4"
            make_video(generated, [(60, 120, 180)] * (10 * 24), with_audio=True)
            payload = {
                "mode": "replace",
                "prompt": "新角色完整取代主要角色。",
                "aspect_ratio": "16:9",
                "megapixels": 0.4,
                "duration": 5,
                "seed": 42,
                "steps": 4,
                "replacement_auto_split": True,
                "replacement_continuity": True,
                "replacement_audio_mode": "original",
                "references": [{
                    "alias": "新角色",
                    "type": "character",
                    "image_asset_ids": [image["id"]],
                    "video_asset_id": source_id,
                    "video_use_audio": True,
                }],
            }
            compiled = replace(compile_request(payload), width=96, height=64)
            source_info = {"width": 96, "height": 64, "fps": 24.0, "duration": 16.0, "has_audio": True}
            plan = replacement_segment_plan(16.0, smart=False)
            fake = FakeBatchComfy(generated.read_bytes())

            with patch("app.JOB_DIR", job_dir), patch("app.OUTPUT_DIR", output_dir):
                manager = JobManager(assets, fake)
                parent = manager.create_replacement_batch(compiled, payload, source_info, plan)
                await manager.tasks[parent["id"]]

            completed = manager.jobs[parent["id"]]
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(completed["segments"]), 2)
            self.assertTrue(all(segment["status"] == "completed" for segment in completed["segments"]))
            children = [job for job in manager.jobs.values() if job.get("parent_job_id") == parent["id"]]
            self.assertEqual(len(children), 2)
            self.assertTrue(all(job["hidden"] for job in children))
            final_path = output_dir / completed["local_output"]
            with av.open(str(final_path)) as container:
                frames = list(container.decode(video=0))
                self.assertTrue(container.streams.audio)
            self.assertEqual(len(frames), 16 * 24)


if __name__ == "__main__":
    unittest.main()
