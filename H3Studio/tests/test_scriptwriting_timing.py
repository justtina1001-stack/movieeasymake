"""Generation must honour the requested timeline without altering saved text."""
import copy
import json
import unittest

from scriptwriting import (
    ScriptwritingError, build_messages, parse_storyboard, validate_script_timeline, validate_storyboard,
)
from tests.test_scriptwriting import STORYBOARD


class ScriptTimelineTests(unittest.TestCase):
    def test_contiguous_markdown_timeline_ends_at_target(self):
        content = (
            "# 回家的路\n"
            "1. **0.0–2.0s｜抵達月台**\n阿青從畫面左側走近。\n"
            "2. **2.0–5.5s｜確認方向**\n阿青抬頭查看指示牌。\n"
            "3. **5.5–15.0s｜登上列車**\n阿青提起行李，登車後向窗外揮手。"
        )
        validate_script_timeline(content, 15)
        validate_script_timeline(content, 15, require_timeline=True)

    def test_generated_timed_layout_requires_at_least_one_timecode_header(self):
        for content in ("一句話梗概：阿青搭列車回家。", "第 1 場 外景 車站 日\n阿青：走吧。",
                        '{"title":"回家的路","scenes":[]}', "{'title': '回家的路', 'scenes': []}",
                        '{"opaque":true}\n0.0–15.0s｜結構資料內的偽裝標題',
                        '```python\n0.0–15.0s｜程式碼內的偽裝標題\n```'):
            with self.subTest(content=content):
                validate_script_timeline(content, 15)
                with self.assertRaisesRegex(ScriptwritingError, "時間碼"):
                    validate_script_timeline(content, 15, require_timeline=True)

    def test_simplified_timecode_headers_are_recognised(self):
        content = "**时间码：0.0s-5.0s**\n阿青到達月台。\n**时间码：5.0s-15.0s**\n阿青登車。"
        validate_script_timeline(content, 15)
        with self.assertRaises(ScriptwritingError):
            validate_script_timeline(content + "\n**时间码：15.0s-30.0s**\n阿青繼續揮手。", 15)

    def test_millisecond_ranges_and_rounding_tolerance(self):
        validate_script_timeline("0.000–7.125s｜候車\n7.125–15.000s｜出發", 15)
        validate_script_timeline("0.000–7.125s｜候車\n7.1255–15.0005s｜出發", 15)
        with self.assertRaises(ScriptwritingError):
            validate_script_timeline("0.000–7.125s｜候車\n7.127–15.000s｜出發", 15)

    def test_gap_overlap_invalid_start_and_tail_are_rejected(self):
        cases = {
            "gap": "0.0–5.0s｜候車\n6.0–15.0s｜出發",
            "overlap": "0.0–6.0s｜候車\n5.0–15.0s｜出發",
            "late_start": "1.0–15.0s｜候車與出發",
            "zero_length": "0.0–5.0s｜候車\n5.0–5.0s｜等待\n5.0–15.0s｜出發",
            "reversed": "0.0–5.0s｜候車\n5.0–4.0s｜等待\n4.0–15.0s｜出發",
            "short": "0.0–14.0s｜候車與出發",
            "long": "0.0–16.0s｜候車與出發",
            "repeated_tail": "0.0–15.0s｜完整故事\n15.0–15.5s｜揮手\n15.5–30.0s｜揮手",
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ScriptwritingError):
                validate_script_timeline(content, 15)

    def test_lens_ranges_and_legacy_custom_text_are_not_timeline_headers(self):
        for content in (
            "鏡頭使用 18–35mm 變焦，阿青走向列車。",
            "### 鏡頭：18–35mm\n低機位跟拍角色登車。",
            "第 1 場 外景 車站 日\n阿青：我們回家吧。",
            "台詞約 10–15 字，列車鳴笛後切黑。",
        ):
            with self.subTest(content=content):
                validate_script_timeline(content, 15)


class StoryboardDurationTests(unittest.TestCase):
    def test_fifteen_second_json_task_uses_matching_duration_without_prose_heading_rule(self):
        project = {key: "" for key in ("title", "idea", "genre", "characters", "style", "instructions", "content")}
        project.update(title="回家的路", idea="阿青搭列車回家。", duration=15)
        system = build_messages(project, "storyboard", "")[0]["content"]
        self.assertIn('"duration":15,', system)
        self.assertNotIn('"duration":5,', system)
        self.assertIn("輸出單一有效 JSON", system)
        self.assertIn("所有shots的duration加總必須恰好15秒", system)
        self.assertNotIn("時間碼段落標題用", system)
        self.assertNotIn("1. 0.0–2.0s｜段名", system)
        self.assertNotIn("12–15s六段", system)

    def test_exact_sum_across_scenes_matches_target(self):
        storyboard = copy.deepcopy(STORYBOARD)
        storyboard["scenes"] *= 3
        self.assertEqual(validate_storyboard(storyboard, target_duration=15), storyboard)
        self.assertEqual(parse_storyboard(json.dumps(storyboard), target_duration=15), storyboard)

    def test_total_duration_rejects_overrun_and_underrun(self):
        storyboard = copy.deepcopy(STORYBOARD)
        storyboard["scenes"][0]["shots"][0]["duration"] = 15
        storyboard["scenes"][0]["shots"] *= 2
        for value, target in ((storyboard, 15), (STORYBOARD, 15)):
            with self.subTest(target=target, count=len(value["scenes"][0]["shots"])):
                with self.assertRaises(ScriptwritingError):
                    validate_storyboard(value, target_duration=target)
                with self.assertRaises(ScriptwritingError):
                    parse_storyboard(json.dumps(value), target_duration=target)

    def test_optional_target_preserves_standalone_schema_validation(self):
        self.assertEqual(validate_storyboard(STORYBOARD), STORYBOARD)
        self.assertEqual(parse_storyboard(json.dumps(STORYBOARD)), STORYBOARD)

    def test_decimal_clip_durations_are_summed_with_millisecond_tolerance(self):
        storyboard = copy.deepcopy(STORYBOARD)
        storyboard["scenes"][0]["shots"][0]["duration"] = 7.125
        storyboard["scenes"][0]["shots"].append(copy.deepcopy(storyboard["scenes"][0]["shots"][0]))
        storyboard["scenes"][0]["shots"][1]["duration"] = 7.875
        validate_storyboard(storyboard, target_duration=15)
        storyboard["scenes"][0]["shots"][1]["duration"] = 7.8755
        validate_storyboard(storyboard, target_duration=15)
        storyboard["scenes"][0]["shots"][1]["duration"] = 7.877
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(storyboard, target_duration=15)
