"""Speed curves preserve source-time anchors through trims and split points."""
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest

import editor_speed as speed
from video_editor import EditorError


def clip(**changes):
    return {"in": 0., "out": 4., "speed": 1., **changes}


def point(time, rate):
    return {"time": time, "speed": rate}


class SpeedMathTests(unittest.TestCase):
    def test_legacy_fixed_speed_and_empty_curve_have_identical_time_mapping(self):
        for rate in (.25, .5, 1, 2, 4):
            fixed = clip(**{"in": 2, "out": 10, "speed": rate})
            empty = {**fixed, "speed_curve": []}
            self.assertEqual(speed.clip_duration(fixed), 8 / rate)
            self.assertEqual(speed.clip_duration(empty), speed.clip_duration(fixed))
            for local in (0, .125, 1, 3):
                self.assertEqual(speed.source_at(fixed, local), speed.source_at(empty, local))
            self.assertEqual(speed.timeline_at(fixed, 6), 4 / rate)

    def test_linear_acceleration_uses_logarithmic_duration_and_exponential_inverse(self):
        value = clip(speed_curve=[point(0, 1), point(4, 2)])
        expected_duration = 4 * math.log(2)
        self.assertAlmostEqual(speed.clip_duration(value), expected_duration, places=12)
        self.assertAlmostEqual(speed.source_at(value, expected_duration / 2), 4 * (math.sqrt(2) - 1), places=12)
        self.assertAlmostEqual(speed.speed_at_source(value, 2), 1.5)

    def test_deceleration_remains_positive_monotonic_and_reversible(self):
        value = clip(speed_curve=[point(0, 4), point(4, .25)])
        self.assertAlmostEqual(speed.clip_duration(value), 4 * math.log(.25 / 4) / (.25 - 4), places=12)
        previous = -1
        for index in range(101):
            source = index / 25
            timeline = speed.timeline_at(value, source)
            self.assertGreater(timeline, previous)
            self.assertAlmostEqual(speed.source_at(value, timeline), source, places=11)
            previous = timeline

    def test_endpoint_speeds_hold_outside_anchor_range(self):
        value = clip(**{"out": 8, "speed_curve": [point(2, .5), point(6, 2)]})
        self.assertEqual(speed.speed_at_source(value, 0), .5)
        self.assertEqual(speed.speed_at_source(value, 20), 2)
        expected = 2 / .5 + 4 * math.log(4) / 1.5 + 2 / 2
        self.assertAlmostEqual(speed.clip_duration(value), expected, places=12)
        for source in (0, 1, 2, 3, 6, 7, 8):
            self.assertAlmostEqual(speed.source_at(value, speed.timeline_at(value, source)), source, places=12)

    def test_nonempty_curve_overrides_fixed_speed(self):
        original = clip(speed_curve=[point(0, .25), point(4, 4)])
        alternate = {**original, "speed": 4}
        self.assertEqual(speed.clip_duration(original), speed.clip_duration(alternate))
        self.assertEqual(speed.source_at(original, 1), speed.source_at(alternate, 1))

    def test_equal_and_nearly_equal_rates_avoid_cancellation(self):
        for delta in (0, 1e-14, 1e-12, 1e-9):
            value = clip(**{"in": 100, "out": 104, "speed_curve": [point(0, 2), point(1000, 2 + delta)]})
            self.assertAlmostEqual(speed.clip_duration(value), 2, places=9)
            self.assertAlmostEqual(speed.source_at(value, 1), 102, places=9)

    def test_trim_and_extension_keep_hidden_original_curve_anchors(self):
        original = clip(**{"out": 10, "speed_curve": [point(0, .25), point(3, 4), point(7, .5), point(10, 2)]})
        snapshot = copy.deepcopy(original)
        trimmed = {**original, "in": 2, "out": 8}
        speed.validate_speed_curve(trimmed, 10)
        offset = speed.timeline_at(original, 2)
        for source in (2, 3, 5, 7, 8):
            local = speed.timeline_at(trimmed, source)
            self.assertAlmostEqual(speed.timeline_at(original, source), offset + local, places=12)
            self.assertAlmostEqual(speed.source_at(trimmed, local), source, places=12)
        self.assertAlmostEqual(speed.source_at_extended(trimmed, -offset, 10), 0, places=12)
        self.assertAlmostEqual(speed.source_at_extended(trimmed, speed.timeline_at(trimmed, 10), 10), 10, places=12)
        self.assertEqual(original, snapshot)
        self.assertEqual(trimmed["speed_curve"], original["speed_curve"])

    def test_split_is_additive_and_reuses_identical_source_motion(self):
        original = clip(**{"in": 1, "out": 9, "speed_curve": [point(0, 2), point(4, .25), point(10, 4)]})
        split_source = speed.source_at(original, speed.clip_duration(original) * .37)
        first = {**original, "out": split_source}
        second = {**original, "in": split_source}
        self.assertAlmostEqual(speed.clip_duration(original), speed.clip_duration(first) + speed.clip_duration(second), places=12)
        for ratio in (0, .2, .5, .8, 1):
            local = speed.clip_duration(second) * ratio
            self.assertAlmostEqual(speed.source_at(second, local), speed.source_at(original, speed.clip_duration(first) + local), places=11)

    def test_inverse_clamps_trim_while_extended_mapping_clamps_only_full_media(self):
        for curve in ([], [point(0, .25), point(4, 4)]):
            value = clip(**{"in": 1, "out": 3, "speed_curve": curve})
            self.assertEqual(speed.source_at(value, -100), 1)
            self.assertEqual(speed.source_at(value, 100), 3)
            self.assertEqual(speed.source_at_extended(value, -100, 4), 0)
            self.assertEqual(speed.source_at_extended(value, 100, 4), 4)
            self.assertGreater(speed.source_at_extended(value, 100), 4)
            self.assertLess(speed.timeline_at(value, 0), 0)

    def test_piecewise_integral_matches_independent_numerical_integration(self):
        value = clip(**{"in": .3, "out": 9.8, "speed_curve": [point(0, .25), point(2, 3), point(3, .75), point(6, 4), point(10, .5)]})
        # Midpoint quadrature serves as an independent reference, rather than
        # duplicating the analytic logarithmic integration implementation.
        intervals = 100_000
        width = (value["out"] - value["in"]) / intervals
        numerical = math.fsum(width / speed.speed_at_source(value, value["in"] + (i + .5) * width) for i in range(intervals))
        self.assertAlmostEqual(speed.clip_duration(value), numerical, delta=1e-7)

    def test_schema_rejects_malformed_boolean_nonfinite_unsorted_and_out_of_source_points(self):
        valid = [point(0, 1), point(4, 2)]
        bad_curves = [None, {}, valid[:1], [None, valid[1]], [True, valid[1]],
                      [point(True, 1), valid[1]], [point(0, True), valid[1]],
                      [point("0", 1), valid[1]], [point(0, "1"), valid[1]],
                      [point(float("nan"), 1), valid[1]], [point(0, float("inf")), valid[1]],
                      [point(-1, 1), valid[1]], [point(0, .249), valid[1]], [point(0, 4.001), valid[1]],
                      [dict(valid[0], easing="linear"), valid[1]], [{"time": 0}, valid[1]],
                      [valid[0], valid[0]], list(reversed(valid)), [point(0, 1), point(1e-10, 2)],
                      [point(i, 1) for i in range(51)], [point(0, 1), point(10**1000, 2)]]
        for points in bad_curves:
            with self.subTest(points=points), self.assertRaises(EditorError):
                speed.validate_speed_curve(clip(speed_curve=points))
        with self.assertRaises(EditorError):
            speed.validate_speed_curve(clip(speed_curve=valid), 3)

    def test_schema_accepts_hidden_points_long_sources_and_maximum_point_count(self):
        value = clip(**{"in": 1200, "out": 1204, "speed_curve": [point(0, .25), point(1300, 4)]})
        self.assertEqual(speed.validate_speed_curve(value, 1300), value["speed_curve"])
        self.assertEqual(len(speed.validate_speed_curve(clip(speed_curve=[point(i, 1) for i in range(50)]))), 50)
        self.assertEqual(speed.validate_speed_curve(clip()), [])
        self.assertEqual(speed.validate_speed_curve(clip(speed_curve=[])), [])

    def test_canonical_validation_copies_points_without_mutating_the_source(self):
        value = clip(speed_curve=[point(0, 1), point(4, 2)])
        before = copy.deepcopy(value)
        canonical = speed.canonical_speed_curve(value)
        validated = speed.validate_speed_curve(value, 4)
        canonical[0]["speed"] = 4
        validated[1]["time"] = 1
        self.assertEqual(value, before)

    @unittest.skipUnless(shutil.which("node"), "Node is required to compare the browser time mapping")
    def test_browser_and_exporter_agree_at_every_sampled_source_and_timeline_time(self):
        values = [clip(speed=rate) for rate in (.25, .5, 1, 2, 4)] + [
            clip(speed_curve=[point(0, .25), point(4, 4)]),
            clip(speed_curve=[point(0, 4), point(4, .25)]),
            clip(**{"in": 1.23, "out": 18.4, "speed_curve": [point(0, .25), point(2, 4), point(6, .5), point(20, 2)]}),
            clip(**{"in": 100, "out": 104, "speed_curve": [point(0, 2), point(1000, 2 + 1e-9)]})]
        script = """const fs=require('node:fs'),m=require(process.argv[1]);
const values=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(values.map(c=>({duration:m.clipDuration(c), samples:Array.from({length:41},(_,i)=>{const t=m.clipDuration(c)*i/40,s=c.in+(c.out-c.in)*i/40;return [m.sourceAt(c,t),m.timelineAt(c,s),m.speedAtSource(c,s)];})}))));"""
        module = Path(__file__).resolve().parents[1] / "static" / "editor_speed.js"
        result = subprocess.run([shutil.which("node"), "-e", script, str(module)], input=json.dumps(values), text=True, capture_output=True, check=True, timeout=10)
        actual = json.loads(result.stdout)
        for value, result in zip(values, actual):
            self.assertAlmostEqual(result["duration"], speed.clip_duration(value), places=10)
            for index, sample in enumerate(result["samples"]):
                timeline = speed.clip_duration(value) * index / 40
                source = value["in"] + (value["out"] - value["in"]) * index / 40
                expected = (speed.source_at(value, timeline), speed.timeline_at(value, source), speed.speed_at_source(value, source))
                for browser, exporter in zip(sample, expected):
                    self.assertAlmostEqual(browser, exporter, places=10)


if __name__ == "__main__":
    unittest.main()
