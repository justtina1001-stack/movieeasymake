"""Production instructions must preserve the brief and fit the local writer budget."""
from __future__ import annotations

import copy
import json
import unittest

from scriptwriting import DEFAULT_SETTINGS, build_messages, fit_context_settings, normalize_project, validate_context
from script_skills import skill_prompt


class ScriptContinuityInstructionsTests(unittest.TestCase):
    def project(self, **overrides):
        return normalize_project({
            "title": "床邊印痕", "duration": 15,
            "idea": "小美獨自醒來，床邊固定一雙印痕。鏡頭先看印痕，再看小美反應。",
            "characters": "小美是同一位主角；床邊另有人影。",
            "content": "0–5s 看左、右各一個印痕；5–15s 小美看同一雙印痕。台詞：『欸——！？』",
            "character_references": [{"id": "a" * 32, "name": "小美", "type": "character", "image_asset_id": "1" * 32,
                                      "appearance": "正面、側面、背面為同一人的三視圖。"}],
            **overrides,
        })

    def test_grounding_reaches_every_writing_task_without_rewriting_source(self):
        project = self.project()
        before = copy.deepcopy(project)
        for action in ("outline", "screenplay", "rewrite", "storyboard"):
            with self.subTest(action=action):
                messages = build_messages(project, action, "保留另有人影及原台詞。")
                system = messages[0]["content"]
                self.assertIn("same individual across cuts/POV", system)
                self.assertIn("Turnaround views define one identity", system)
                self.assertIn("not extra figures/panels/labels", system)
                self.assertIn("exactly two impressions (left+right)", system)
                self.assertIn("explicit quantities override examples", system)
                body = json.loads(messages[1]["content"].split("\n", 1)[0])
                self.assertEqual(body["idea"], project["idea"])
                self.assertEqual(body["characters"], project["characters"])
                self.assertEqual(body["existing_script"], project["content"])
                self.assertEqual(body["character_references"][0]["name"], "小美")
                self.assertEqual(body["character_references"][0]["appearance"], project["character_references"][0]["appearance"])
                self.assertEqual(project, before)

    def test_explicit_copies_quantity_changes_and_dialogue_remain_authoritative(self):
        manuscript = "雙胞胎同時入鏡。先一雙、行走後變成兩雙腳印。台詞：『不要再多一雙！』"
        project = self.project(content=manuscript, characters="小美與雙胞胎姐妹",
                               instructions="保留第二個實體、床邊人影及新增印痕事件。")
        messages = build_messages(project, "storyboard", project["instructions"])
        body = json.loads(messages[1]["content"].split("\n", 1)[0])
        self.assertEqual(body["existing_script"], manuscript)
        self.assertEqual(body["instructions"], project["instructions"])
        self.assertIn("Respect explicit crowds/clones/mirrors/shadows", messages[0]["content"])
        self.assertIn("unless action adds marks", messages[0]["content"])

    def test_storyboard_prompt_checks_cut_state_and_keeps_names_and_given_cut_times(self):
        project = self.project()
        messages = build_messages(project, "storyboard", "")
        self.assertIn("Keep original asset names in English action", messages[0]["content"])
        self.assertIn("visible cast/count/positions/start/end state per cut", messages[0]["content"])
        self.assertIn("few cuts unless prescribed", messages[0]["content"])
        self.assertIn("preserve events/times/dialogue without replay", messages[0]["content"])
        body = json.loads(messages[1]["content"].split("\n", 1)[0])
        self.assertEqual(body["existing_script"], project["content"])

    def test_empty_object_story_does_not_gain_footprints_in_its_brief(self):
        project = self.project(idea="產品旋轉展示", content="0–15s 固定鏡頭展示商品。", characters="",
                               character_references=[])
        messages = build_messages(project, "storyboard", "")
        body = json.loads(messages[1]["content"].split("\n", 1)[0])
        self.assertNotIn("腳印", json.dumps(body, ensure_ascii=False))
        self.assertIn("This adds no footprints", messages[0]["content"])

    def test_conversion_budget_fits_official_rules_without_losing_original_eight_cut_example(self):
        from test_scriptwriting_defaults import example_manuscript, ui_example_project
        project = ui_example_project()
        project["content"] = example_manuscript(project)
        original = copy.deepcopy(project)
        messages = build_messages(project, "storyboard", project["instructions"])
        settings = fit_context_settings(messages, DEFAULT_SETTINGS, action="storyboard")
        validate_context(messages, settings)
        self.assertGreaterEqual(settings["num_predict"], 1024)
        self.assertGreaterEqual(settings["num_ctx"], 8192)
        self.assertLessEqual(settings["num_ctx"], 16384)
        self.assertEqual(project, original)

    def test_genre_methods_do_not_invent_an_unrequested_pursuer(self):
        horror = skill_prompt("suspense-horror", "storyboard")
        chase = skill_prompt("action-chase", "storyboard")
        self.assertIn("only when the brief specifies a pursuer", horror)
        self.assertIn("do not automatically introduce a new character", horror)
        self.assertIn("single-character action does not need an invented pursuer", chase)


if __name__ == "__main__":
    unittest.main()
