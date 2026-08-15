import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import JobManager, RequestError, clean_job_name, output_filename_stem, output_timestamp, paginate_job_records


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


if __name__ == "__main__":
    unittest.main()
