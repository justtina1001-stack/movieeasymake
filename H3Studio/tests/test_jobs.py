import unittest
from datetime import datetime, timezone

from app import RequestError, clean_job_name, output_filename_stem, output_timestamp, paginate_job_records


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


if __name__ == "__main__":
    unittest.main()
