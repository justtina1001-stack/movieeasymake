"""Waiting for recovery cannot masquerade as engine computation."""
from datetime import datetime, timedelta, timezone
import math
import unittest

from app import JobManager, history_execution_timing


class GenerationTimingProvenanceTests(unittest.TestCase):
    def finish(self, **fields):
        manager = object.__new__(JobManager)
        manager.jobs = {"job": fields}
        return manager.finish_timing("job")

    def test_six_days_of_recovery_are_wall_time_with_unknown_execution(self):
        started = (datetime.now(timezone.utc) - timedelta(hours=147)).isoformat()
        result = self.finish(created_at=started, generation_started_at=started, recovery_state="monitoring")
        self.assertIsNone(result["execution_seconds"])
        self.assertEqual(result["execution_timing_source"], "unknown")
        self.assertGreaterEqual(result["wall_elapsed_seconds"], 147 * 3600)

    def test_old_wall_execution_is_retained_only_as_unverified_diagnostic(self):
        result = self.finish(execution_seconds=531304.985)
        self.assertIsNone(result["execution_seconds"])
        self.assertEqual(result["unverified_execution_seconds"], 531304.985)

    def test_confirmed_engine_duration_survives_a_later_terminal_update(self):
        result = self.finish(execution_timing_source="engine", execution_seconds=83.25)
        self.assertEqual(result["execution_seconds"], 83.25)
        self.assertEqual(result["execution_timing_source"], "engine")

    def test_no_start_or_invalid_dates_do_not_invent_generation_time(self):
        for start in (None, "", "invalid", "2026-11-04T11:11:46"):
            with self.subTest(start=start):
                result = self.finish(generation_started_at=start)
                self.assertIsNone(result["execution_seconds"])

    def test_engine_events_are_the_provenance_for_generation_seconds(self):
        result = history_execution_timing({"status": {"messages": [
            ["execution_start", {"timestamp": 1_000}],
            ["execution_interrupted", {"timestamp": 72_250}],
        ]}})
        self.assertEqual(result["execution_seconds"], 71.25)
        self.assertEqual(result["execution_timing_source"], "engine")

    def test_bad_or_incomplete_engine_timestamps_remain_unknown(self):
        for stamp in (True, math.nan, math.inf, -math.inf, 1e300):
            with self.subTest(stamp=stamp):
                result = history_execution_timing({"status": {"messages": [
                    ["execution_start", {"timestamp": stamp}],
                    ["execution_success", {"timestamp": stamp}],
                ]}})
                self.assertEqual(result, {})
        for value in ({}, {"status": []}, {"status": {"messages": "bad"}}, {"status": {"messages": [
                ["execution_start", {"timestamp": 1000}]]}}):
            self.assertEqual(history_execution_timing(value), {})


if __name__ == "__main__":
    unittest.main()
