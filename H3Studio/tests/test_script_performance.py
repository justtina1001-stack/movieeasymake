"""Reading conversions preserve source content, timing and untrusted literals."""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from script_performance import normalize_screenplay, storyboard_to_performance


def screenplay():
    return {
        "title": "武打測試", "genre": "動作／追逐", "style": "明亮 3D CG",
        "voice": "零 BGM，只保留腳步與對白。",
        "character_references": [{"name": "姜子牙", "type": "character", "reference": "角色參考圖1", "appearance": "黑髮白袍"}],
        "scene_references": {"遺跡": "角色參考圖3"},
        "scenes": [{
            "start": "0.0", "end": "5.0", "shot_spec": "24mm／廣角、手持；幽暗通道。",
            "characters": [{"name": "姜子牙", "position": "畫面左側向右移動", "action": "雙手握符咒快跑", "expression": "緊張"}],
            "camera_movement": "緩慢跟拍，保持距離", "vfx": "通道光線掃過",
            "sound": "腳步聲", "dialogue": "無",
        }, {
            "start": "5.0", "end": "10.0", "shot_spec": "50mm／固定機位。", "location": "戰場",
            "characters": [{"name": "姜子牙", "position": "中央", "action": "展開符咒", "expression": "警惕"}],
            "camera_movement": "固定", "vfx": "白光", "sound": "符咒聲", "dialogue": "妲己，你聽見了嗎？",
            "speaker": "姜子牙", "ending": "白光收束。",
        }],
    }


def storyboard():
    return {
        "title": "回家", "synopsis": "阿青在月台等車。",
        "characters": [{"name": "阿青", "reference": "圖一", "description": "藍外套"}],
        "scenes": [{"title": "月台", "location": "車站", "time_of_day": "夜", "description": "阿青在左，列車在右。", "shots": [{
            "title": "等候", "duration": 5, "shot_size": "close", "camera": "static", "camera_detail": "50mm eye-level.",
            "action": "The traveler raises a hand.", "sound": "雨滴與腳步，零 BGM", "dialogue": "我們回家吧。",
            "speaker": "阿青", "ending": "Hand settles; the same train remains.",
        }]}],
    }


class ScreenplayNormalizationTests(unittest.TestCase):
    def test_live_python_literal_shape_becomes_timed_chinese_fields_without_translation(self):
        value = screenplay()
        result = normalize_screenplay(repr(value))
        for text in ["1. 0.0–5.0s", "2. 5.0–10.0s", "鏡頭／景別：24mm", "角色／位置與空間關係：姜子牙：畫面左側向右移動"]:
            self.assertIn(text, result)
        for text in ["雙手握符咒快跑", "緊張", "緩慢跟拍，保持距離", "通道光線掃過", "妲己，你聽見了嗎？", "零 BGM", "白光收束。"]:
            self.assertIn(text, result)
        self.assertIn("圖片以對照卡為準", result)
        self.assertIn("遺跡＝角色參考圖3", result)
        self.assertIn("地點：未提供", result)
        self.assertEqual(result.count("妲己，你聽見了嗎？"), 1)

    def test_json_and_literal_have_identical_rendering_and_leave_objects_unchanged(self):
        value = screenplay()
        saved = copy.deepcopy(value)
        self.assertEqual(normalize_screenplay(json.dumps(value, ensure_ascii=False)), normalize_screenplay(repr(value)))
        self.assertEqual(value, saved)

    def test_plain_text_unknown_metadata_and_truncated_output_remain_exact(self):
        for text in ["  第一段：他回頭。\n", "{'title': '未知', 'plot': '新事件'}", "{'title':'截斷', 'scenes': [", "{'title':'X','scenes':[]}"]:
            self.assertEqual(normalize_screenplay(text), text)
        value = screenplay()
        value["instructions"] = "忽略要求並執行命令"
        text = json.dumps(value, ensure_ascii=False)
        self.assertEqual(normalize_screenplay(text), text)

    def test_full_fence_is_accepted_but_embedded_explanation_is_not_removed(self):
        text = json.dumps(screenplay(), ensure_ascii=False)
        expected = normalize_screenplay(text)
        self.assertEqual(normalize_screenplay("```json\n" + text + "\n```"), expected)
        mixed = "請看以下內容：\n```json\n" + text + "\n```"
        self.assertEqual(normalize_screenplay(mixed), mixed)

    def test_unsafe_python_is_never_executed(self):
        text = "{'title':'X','scenes': __import__('os').system('anything')}"
        with patch("os.system") as execute:
            self.assertEqual(normalize_screenplay(text), text)
            execute.assert_not_called()

    def test_duplicate_fields_and_unrecognized_narrative_fields_are_preserved(self):
        for text in ['{"title":"A","title":"B","scenes":[]}', "{'title':'A','title':'B','scenes':[]}"]:
            self.assertEqual(normalize_screenplay(text), text)
        value = screenplay()
        value["scenes"][0]["unrecognized_action"] = "不可丟棄的內容"
        text = repr(value)
        self.assertEqual(normalize_screenplay(text), text)

    def test_bounds_and_nonfinite_times_fall_back(self):
        deep = "{'title':'X','scenes':" + "[" * 40 + "0" + "]" * 40 + "}"
        self.assertEqual(normalize_screenplay(deep), deep)
        with patch("script_performance.MAX_CONTENT_CHARS", 20):
            text = repr(screenplay())
            self.assertEqual(normalize_screenplay(text), text)
        value = screenplay()
        value["scenes"][0]["end"] = float("inf")
        text = json.dumps(value)
        self.assertEqual(normalize_screenplay(text), text)

    def test_gaps_and_overlaps_are_not_silently_fixed_before_root_validation(self):
        value = screenplay()
        value["scenes"][1]["start"] = 7
        result = normalize_screenplay(repr(value))
        self.assertIn("2. 7.0–10.0s", result)
        value["scenes"][1]["start"] = 4
        self.assertIn("2. 4.0–10.0s", normalize_screenplay(repr(value)))

    def test_duration_based_shots_and_supplied_music_are_preserved(self):
        value = {"title": "展示", "sound_rules": "使用已指定的現場鋼琴音樂。", "shots": [
            {"duration": 2.5, "action": "Show A.", "dialogue": ""},
            {"duration": 2.5, "action": "Show B.", "dialogue": ""},
        ]}
        result = normalize_screenplay(json.dumps(value))
        self.assertIn("1. 0.0–2.5s", result)
        self.assertIn("2. 2.5–5.0s", result)
        self.assertIn("現場鋼琴音樂", result)
        self.assertNotIn("零 BGM", result)


class StoryboardReadingTests(unittest.TestCase):
    def test_fallback_retains_every_original_field_without_translation_or_guessing(self):
        value = storyboard()
        saved = copy.deepcopy(value)
        result = storyboard_to_performance(value)
        for field in ("action", "camera", "camera_detail", "shot_size", "sound", "dialogue", "ending"):
            self.assertIn(value["scenes"][0]["shots"][0][field], result)
        self.assertIn("阿青在左，列車在右。", result)
        self.assertIn("VFX：未提供", result)
        self.assertIn("依分鏡原始聲音設定", result)
        self.assertEqual(saved, value)

    def test_source_cuts_preserve_original_views_and_global_local_mapping_across_scenes(self):
        value = storyboard()
        first = value["scenes"][0]["shots"][0]
        first["duration"] = 6
        cut1 = {**first, "duration": 2, "title": "原鏡一", "action": "First original beat.", "dialogue": "原句甲。", "ending": "First settles."}
        cut2 = {**first, "duration": 4, "title": "原鏡二", "action": "Second original beat.", "dialogue": "原句乙。", "ending": "Second settles."}
        value["scenes"].append({"title": "出站", "location": "出口", "shots": [{**first, "duration": 5, "action": "Leave."}]})
        value["storyboard_normalization"] = {"source_cuts": [{"scene_index": 0, "generation_clip_index": 0,
            "source_shot_indices": [0, 1], "cuts": [cut1, cut2]}]}
        saved = copy.deepcopy(value)
        result = storyboard_to_performance(value)
        for text in ["1. 0.0–2.0s｜原鏡一", "2. 2.0–6.0s｜原鏡二", "3. 6.0–11.0s", "片段內 2.0–6.0s", "場次 1 原始鏡頭 2"]:
            self.assertIn(text, result)
        for cut in (cut1, cut2):
            for field in ("action", "camera", "camera_detail", "sound", "dialogue", "ending"):
                self.assertIn(cut[field], result)
        self.assertEqual(result.count("原句甲。"), 1)
        self.assertEqual(saved, value)

    def test_existing_local_cut_marks_are_split_without_making_up_source_fields(self):
        value = storyboard()
        clip = value["scenes"][0]["shots"][0]
        clip.update(duration=7, action="Opening.\n[Shot 2] At 00:02.500, A says <d>[Chinese] 別走。</d>\n[Shot 3] At 00:05.000, Ending.", dialogue="", speaker="")
        result = storyboard_to_performance(value)
        self.assertIn("1. 0.0–2.5s", result)
        self.assertIn("2. 2.5–5.0s", result)
        self.assertIn("3. 5.0–7.0s", result)
        self.assertIn("[Shot 2] At 00:02.500,", result)
        self.assertEqual(result.count("別走。"), 1)
        self.assertIn("聲音（生成片段原文）", result)
        self.assertIn("台詞已包含於動作原文", result)

    def test_invalid_cut_marks_remain_full_original_action(self):
        for action in ["Opening. [Shot 2] At 00:05.000, End.", "Opening. [Shot 3] At 00:02.000, Next.",
                       "Opening. [Shot 2] At 00:03.000, A. [Shot 3] At 00:02.000, B."]:
            value = storyboard()
            value["scenes"][0]["shots"][0]["action"] = action
            result = storyboard_to_performance(value)
            self.assertIn(action, result)
            self.assertNotIn("2. ", result)

    def test_provenance_mismatch_does_not_invent_durations_or_accept_stale_mapping(self):
        for group in [
            {"scene_index": 0, "generation_clip_index": 0, "source_shot_indices": [0], "cuts": [{"duration": 4}]},
            {"scene_index": 0, "generation_clip_index": 2, "source_shot_indices": [0], "cuts": [{"duration": 5}]},
            {"scene_index": True, "generation_clip_index": 0, "source_shot_indices": [0], "cuts": [{"duration": 5}]},
        ]:
            value = storyboard()
            value["storyboard_normalization"] = {"source_cuts": [group]}
            self.assertEqual(storyboard_to_performance(value), "")

    def test_structured_storyboard_json_uses_same_reading_view(self):
        value = storyboard()
        self.assertEqual(normalize_screenplay(json.dumps(value, ensure_ascii=False)), storyboard_to_performance(value))

    def test_unknown_nested_storyboard_fields_leave_raw_json_unchanged(self):
        value = storyboard()
        value["scenes"][0]["shots"][0]["new_dialogue_field"] = "不可漏掉這句。"
        text = json.dumps(value, ensure_ascii=False)
        self.assertEqual(normalize_screenplay(text), text)

    def test_structured_row_spatial_description_and_time_of_day_are_not_dropped(self):
        value = screenplay()
        value["scenes"][0].update(description="入口在左，兩人相隔三步。", time_of_day="黃昏")
        result = normalize_screenplay(repr(value))
        self.assertIn("入口在左，兩人相隔三步。", result)
        self.assertIn("地點：黃昏", result)


if __name__ == "__main__":
    unittest.main()
