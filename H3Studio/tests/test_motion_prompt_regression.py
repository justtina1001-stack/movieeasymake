"""Reference motion and explicit screenplay cuts must survive compilation."""

import unittest

from domain import RequestError, compile_request
from h3_prompt_format import normalize_prompt_source, normalize_shots


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

    def test_five_shot_combat_manuscript_retains_all_cuts_through_preparation_and_compile(self):
        # This is the heading layout from the real 15-second FastH3 failure.
        # It previously passed through as prose inside a single official Shot 1.
        text = (
            "SHOT 01｜0.0–3.0 秒\n月夜對峙・妖氣與道光升起\n"
            "鏡位：18mm 超廣角，低機位。姜子牙與妲己同時入鏡。\n"
            "SHOT 02｜3.0–6.0 秒\n法術先發・金光符鏈對狐火妖波\n"
            "姜子牙左手向前一按，妲己側滑旋身擋下符鏈。\n"
            "SHOT 03｜6.0–9.0 秒\n近身切入・妖狐突進與道者格擋\n"
            "妲己前衝爪擊，姜子牙舉起法器橫擋。\n"
            "SHOT 04｜9.0–12.0 秒\n拉開距離・尾擊纏身與雷訣反震\n"
            "妲己向後翻退，姜子牙將法器頓地。\n"
            "SHOT 05｜12.0–15.0 秒\n最後強碰・雙術交轟定格收尾\n"
            "兩股力量在森林邊界猛烈對撞，最後停在雙方身影。"
        )
        references = [
            {"alias": "妲己", "type": "character", "image_asset_ids": ["fox"]},
            {"alias": "森林邊界", "type": "background", "image_asset_ids": ["forest"]},
            {"alias": "姜子牙", "type": "character", "image_asset_ids": ["taoist"]},
        ]
        prepared = normalize_prompt_source(text, 15.083333, timeline_duration=15)
        self.assertEqual(normalize_prompt_source(prepared, 15.083333, timeline_duration=15), prepared)
        for profile in ("", "shortfilm"):
            result = compile_request(reference_request(
                prompt=prepared, prompt_profile=profile, references=references,
                video_model="fasth3_omni_nvfp4", storyboards=[{"duration": 2}],
            )).prompt
            self.assertEqual(result.count("[Shot 1]"), 1)
            for index, start in [(2, 3), (3, 6), (4, 9), (5, 12)]:
                self.assertEqual(result.count(f"[Shot {index}] At 00:{start:02d}.000,"), 1)
            self.assertNotIn("SHOT 0", result)
            self.assertNotIn("0.0–3.0 秒", result)
            for title in ("月夜對峙", "法術先發", "近身切入", "拉開距離", "最後強碰"):
                self.assertIn(title, result)

    def test_english_shot_heading_variants_keep_titles_and_original_sign_text(self):
        for first, second in (
            ("### **SHOT 01｜0.0–3.0 秒**", "### **SHOT 02｜3.0–5.0 秒**"),
            ("Shot 1 | 0–3s | Opening", "shot 2 | 3–5s | End"),
            ("SHOT 1 0–3秒", "SHOT 2 3–5秒"),
            ('Shot 1: Sign "SHOT 99" (0–3s)', "Shot 2: Ending (3–5s)"),
        ):
            with self.subTest(first=first):
                text = first + "\r\nA sign reads \"SHOT 09｜0–5 秒\".\r\n" + second + "\r\nSettle."
                result, count = normalize_shots(text, 5.17)
                self.assertEqual(count, 2)
                self.assertIn("[Shot 2] At 00:03.000,", result)
                self.assertIn('A sign reads "SHOT 09｜0–5 秒".', result)
                if "Opening" in first:
                    self.assertIn("[Shot 1] Opening", result)
                if "Sign" in first:
                    self.assertIn('[Shot 1] Sign "SHOT 99"', result)

    def test_malformed_english_headings_cannot_silently_collapse_to_one_shot(self):
        for text in (
            "SHOT 01｜0–3秒\nSHOT 02｜4–15秒",
            "SHOT 01｜0–4秒\nSHOT 02｜3–15秒",
            "SHOT 01｜0–3秒\nSHOT 03｜3–15秒",
            "SHOT 01｜0–3秒\nSHOT 02｜3–16秒",
            "SHOT 01｜0–3秒\nSHOT 02｜3–15",
            "SHOT 01｜0–3秒\nSHOT 02｜沒有時間",
            "SHOT 01｜0–3秒\nSHOT 02",
            "SHOT 01｜0–3秒\n[Shot 2] At 00:03.000, Ending.",
            "SHOT 1 - 0.0–3.0 秒\n畫面A\nSHOT 2 - 3.0–15.0 秒\n畫面B",
            "SHOT 1 [0–3秒]\n畫面A\nSHOT 2 [3–15秒]\n畫面B",
            "SHOT 1 開場 0–3秒\n畫面A\nSHOT 2 收尾 3–15秒\n畫面B",
            "SHOT 1: 0–3s | Title (0–3s)\nSHOT 2: 3–15s | End (3–15s)",
        ):
            with self.subTest(text=text), self.assertRaises(RequestError):
                compile_request(reference_request(prompt=text))

    def test_heading_parser_does_not_change_quoted_text_or_dialogue(self):
        prompt = ('[Shot 1] A sign reads "\n#### **鏡頭 9：Sign（7–8秒）**\n". '
                  '<d>[Chinese] Read this:\n#### **鏡頭 8：Spoken（5–7秒）**\n</d>'
                  ' A sign reads "\nSHOT 01｜0–3秒\nSHOT 02｜3–15秒\n".'
                  '<d>[English] Read this:\nSHOT 01｜0–3秒\nSHOT 02｜3–15秒\n</d>')
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

    def test_incomplete_heading_is_rejected_while_inline_prose_does_not_invent_cuts(self):
        with self.assertRaisesRegex(ValueError, "時間碼"):
            normalize_shots("#### **鏡頭 2：沒有時間**\nDragon moves.", 15)
        prompt = "The sign describes 鏡頭 2：Other（3–7秒） in prose."
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
