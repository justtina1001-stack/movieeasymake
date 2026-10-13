"""Reference motion and explicit screenplay cuts must survive compilation."""

import unittest

from domain import RequestError, compile_request
from h3_prompt_format import normalize_shots


def reference_request(**extra):
    return {
        "mode": "r2v", "duration": 15, "motion_profile": "none",
        "prompt": "Dragon walks in and its belly grows as it eats.",
        "references": [
            {"alias": "Dragon", "type": "character", "image_asset_ids": ["dragon"]},
            {"alias": "Pose", "type": "motion", "image_asset_ids": ["pose"]},
        ],
        **extra,
    }


class MotionPromptRegressionTests(unittest.TestCase):
    def test_motion_image_transfers_pose_without_overriding_character_appearance(self):
        result = compile_request(reference_request()).prompt
        self.assertIn("<Subject 2> is the action and pose", result)
        self.assertIn("<Picture 2> supply <Subject 2>'s action and pose reference", result)
        self.assertNotIn("<Picture 2> supply only <Subject 2>'s defined appearance", result)
        self.assertIn("rather than freezing the reference pose", result)
        self.assertIn("do not transfer the reference person's identity", result)
        self.assertIn("<Picture 1> supply only <Subject 1>'s defined appearance", result)

    def test_explicit_transformation_overrides_proportion_lock_without_changing_identity(self):
        for kind in ("character", "creature"):
            with self.subTest(kind=kind):
                result = compile_request(reference_request(references=[
                    {"alias": "Dragon", "type": kind, "image_asset_ids": ["dragon"]},
                ])).prompt
                self.assertIn("its belly grows as it eats", result)
                self.assertIn("proportion change, or state change takes precedence", result)
                self.assertIn("without creating a different individual", result)

    def test_timed_chinese_markdown_headings_become_actual_cuts(self):
        prompt = "## Story\n\n" + "\n\n".join(
            f"#### **鏡頭 {index}：Beat {index}（{start}–{end}秒）**\nDragon moves."
            for index, start, end in [(1, 0, 3), (2, 3, 7), (3, 7, 10), (4, 10, 13), (5, 13, 15)]
        )
        result = compile_request(reference_request(prompt=prompt, storyboards=[{"duration": 2}])).prompt
        self.assertEqual(result.count("[Shot 1]"), 1)
        for index, timestamp in [(2, "00:03.000"), (3, "00:07.000"), (4, "00:10.000"), (5, "00:13.000")]:
            self.assertIn(f"[Shot {index}] At {timestamp}, Beat {index}", result)
        self.assertNotIn("#### **鏡頭", result)

    def test_heading_parser_does_not_change_quoted_text_or_dialogue(self):
        prompt = ('[Shot 1] A sign reads "\n#### **鏡頭 9：Sign（7–8秒）**\n". '
                  '<d>[Chinese] Read this:\n#### **鏡頭 8：Spoken（5–7秒）**\n</d>')
        result, count = normalize_shots(prompt, 15)
        self.assertEqual(result, prompt)
        self.assertEqual(count, 1)

    def test_heading_title_preserves_visible_quotes(self):
        result, count = normalize_shots('#### **鏡頭 1：招牌 "鏡頭 9"（0–5秒）**\nA sign stays visible.', 5.17)
        self.assertIn('[Shot 1] 招牌 "鏡頭 9"', result)
        self.assertEqual(count, 1)

    def test_windows_line_endings_and_decimal_ranges_are_supported(self):
        result, count = normalize_shots("#### **鏡頭 1：Start（0–1.5秒）**\r\nMove.\r\n#### **鏡頭 2：End（1.5–5秒）**\r\nSettle.", 5.17)
        self.assertIn("[Shot 2] At 00:01.500, End", result)
        self.assertEqual(count, 2)

    def test_untimed_or_inline_prose_does_not_invent_cuts(self):
        for prompt in ("#### **鏡頭 2：沒有時間**\nDragon moves.",
                       "The sign describes 鏡頭 2：Other（3–7秒） in prose."):
            result, count = normalize_shots(prompt, 15)
            self.assertEqual(result, "[Shot 1] " + prompt)
            self.assertEqual(count, 1)

    def test_invalid_explicit_heading_timeline_is_rejected(self):
        for second in ("#### **鏡頭 3：Skipped（3–7秒）**",
                       "#### **鏡頭 2：Past end（3–17秒）**",
                       "#### **鏡頭 2：Backward（0–2秒）**",
                       "#### **鏡頭 2：Reverse（7–3秒）**"):
            with self.subTest(second=second), self.assertRaises(RequestError):
                compile_request(reference_request(prompt="#### **鏡頭 1：Opening（0–3秒）**\nMove.\n" + second))


if __name__ == "__main__":
    unittest.main()
