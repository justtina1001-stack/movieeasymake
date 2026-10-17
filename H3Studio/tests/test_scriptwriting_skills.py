"""Selected local writing guides survive projects, inputs and video snapshots."""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from PIL import Image

import scriptwriting
from script_references import build_appearance_messages
from script_skills import SKILLS_VERSION, list_skills, skill_prompt
from scriptwriting import (
    DEFAULT_SETTINGS, ScriptwritingError, ScriptwritingManager, build_messages,
    fit_context_settings, normalize_project, validate_context,
)
from tests import test_script_references as references
from tests import test_script_video as video
from tests import test_scriptwriting as scripts


ACTIONS = ("outline", "screenplay", "rewrite", "storyboard")


class WritingSkillProjectTests(unittest.TestCase):
    def test_new_projects_are_unselected_and_selected_updates_can_be_cleared(self):
        self.assertEqual(normalize_project({})["writing_skill"], "")
        self.assertEqual(normalize_project({"writing_skill": None})["writing_skill"], "")
        project = normalize_project({"writing_skill": "cinematic-story"})
        updated = normalize_project({"title": "更新標題"}, project)
        self.assertEqual(updated["writing_skill"], "cinematic-story")
        self.assertEqual(normalize_project({"writing_skill": ""}, updated)["writing_skill"], "")
        self.assertEqual(project["writing_skill"], "cinematic-story")

    def test_unknown_or_nonstring_skill_ids_are_rejected(self):
        for value in (True, 1, [], {}, "unknown-guide", "cinematic-story\x00"):
            with self.subTest(value=value), self.assertRaises(ScriptwritingError):
                normalize_project({"writing_skill": value})

    def test_legacy_missing_selection_and_explicit_empty_selection_have_identical_prompts(self):
        project = normalize_project({"idea": "阿青回家。", "content": "既有完整原稿。"})
        legacy = copy.deepcopy(project)
        legacy.pop("writing_skill")
        with patch.object(scriptwriting, "skill_prompt", side_effect=AssertionError("unselected guide must not be used")):
            for action in ACTIONS:
                with self.subTest(action=action):
                    self.assertEqual(build_messages(project, action, "保留原稿"),
                                     build_messages(legacy, action, "保留原稿"))

    def test_selected_skill_preserves_default_six_beats_and_explicit_user_timing(self):
        template = "未指定時間分配且全長15秒時，採0–2、2–4、4–6.5、6.5–9、9–12、12–15s六段；已指定則保留其合法時間分配。"
        timings = (
            "依0–2、2–4、4–6.5、6.5–9、9–12、12–15秒六段編寫。",
            "依0–2、2–4、4–5.5、5.5–7、7–8.5、8.5–10、10–12.5、12.5–15秒八段編寫。",
        )
        for timing in ("", *timings):
            unselected = normalize_project({"instructions": timing, "content": "原稿與指定台詞。"})
            selected = dict(unselected, writing_skill="cinematic-story")
            for action in ACTIONS:
                with self.subTest(timing=timing, action=action):
                    original = build_messages(unselected, action, "保留原分段和台詞，不新增鏡頭")
                    guided = build_messages(selected, action, "保留原分段和台詞，不新增鏡頭")
                    if action in {"screenplay", "rewrite"}:
                        self.assertIn(template, original[0]["content"])
                        self.assertIn(template, guided[0]["content"])
                    else:
                        self.assertNotIn(template, original[0]["content"])
                        self.assertNotIn(template, guided[0]["content"])
                    self.assertEqual(guided[1], original[1])
                    body = json.loads(guided[1]["content"].split("\n", 1)[0])
                    self.assertEqual(body["instructions"], timing)
                    self.assertEqual(body["existing_script"], "原稿與指定台詞。")
                    self.assertTrue(guided[1]["content"].endswith("本次要求：保留原分段和台詞，不新增鏡頭"))
                    self.assertIn("本次總長固定 15 秒", guided[0]["content"])
                    self.assertIn("使用者明確的創作要求", guided[0]["content"])

    def test_every_selected_guide_reaches_all_writing_actions_and_preserves_task_rules(self):
        catalog = list_skills()
        self.assertGreaterEqual(len(catalog), 2)
        for item in catalog:
            project = normalize_project({"writing_skill": item["id"], "instructions": "採黑白風格，保留指定台詞。"})
            for action in ACTIONS:
                with self.subTest(skill=item["id"], action=action):
                    messages = build_messages(project, action, "不要追加旁白")
                    guide = skill_prompt(item["id"], action)
                    if guide:
                        self.assertIn(guide, messages[0]["content"])
                        self.assertIn("使用者明確的創作要求", messages[0]["content"])
                        self.assertLess(messages[0]["content"].index(guide), messages[0]["content"].index("\n任務："))
                    self.assertIn("本次總長固定 15 秒", messages[0]["content"])
                    self.assertIn("零BGM", messages[0]["content"])
                    body = json.loads(messages[1]["content"].split("\n", 1)[0])
                    self.assertEqual(body["instructions"], project["instructions"])
                    self.assertTrue(messages[1]["content"].endswith("本次要求：不要追加旁白"))
                    if action == "storyboard":
                        self.assertIn("輸出單一有效 JSON", messages[0]["content"])
                        self.assertIn("所有shots的duration加總必須恰好15秒", messages[0]["content"])
                    before = copy.deepcopy(messages)
                    fitted = fit_context_settings(messages, DEFAULT_SETTINGS)
                    validate_context(messages, fitted)
                    self.assertEqual(messages, before)
                    self.assertEqual(fitted["num_ctx"], DEFAULT_SETTINGS["num_ctx"])
                    self.assertGreaterEqual(fitted["num_predict"], 1024)

    def test_human_screenplay_layout_stays_distinct_from_machine_storyboard_with_every_skill(self):
        for skill in ("", *(item["id"] for item in list_skills())):
            project = normalize_project({"writing_skill": skill, "idea": "阿青登上列車。"})
            for action in ("screenplay", "rewrite"):
                with self.subTest(skill=skill, action=action):
                    system = build_messages(project, action, "")[0]["content"]
                    self.assertIn("1. 0.0–2.0s｜段名", system)
                    self.assertNotIn("輸出單一有效 JSON", system)
                    self.assertRegex(system, r"(?:禁止|不要|不輸出|不得)[^。\n]{0,80}(?:JSON|字典)")
            system = build_messages(project, "storyboard", "")[0]["content"]
            self.assertIn("輸出單一有效 JSON", system)
            self.assertNotIn("1. 0.0–2.0s｜段名", system)

    def test_long_manuscript_keeps_every_input_and_only_adjusts_output_budget(self):
        manuscript = "詳細動作與場景。" * 150
        for item in list_skills():
            project = normalize_project({"writing_skill": item["id"], "content": manuscript})
            messages = build_messages(project, "storyboard", project["instructions"])
            before = copy.deepcopy(messages)
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            fitted = fit_context_settings(messages, settings)
            with self.subTest(skill=item["id"]):
                validate_context(messages, fitted)
                self.assertEqual(messages, before)
                self.assertEqual(settings, DEFAULT_SETTINGS)
                self.assertEqual(json.loads(messages[1]["content"].split("\n", 1)[0])["existing_script"], manuscript)
                self.assertEqual({key: value for key, value in fitted.items() if key != "num_predict"},
                                 {key: value for key, value in settings.items() if key != "num_predict"})
                self.assertGreaterEqual(fitted["num_predict"], 1024)
                self.assertLess(fitted["num_predict"], settings["num_predict"])


class WritingSkillAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = scripts.ScriptAPITests.asyncSetUp
    asyncTearDown = scripts.ScriptAPITests.asyncTearDown
    fake_tags = scripts.ScriptAPITests.fake_tags
    fake_show = scripts.ScriptAPITests.fake_show
    fake_chat = scripts.ScriptAPITests.fake_chat
    fake_unload = scripts.ScriptAPITests.fake_unload
    redirect_target = scripts.ScriptAPITests.redirect_target
    fake_free = scripts.ScriptAPITests.fake_free
    create = scripts.ScriptAPITests.create
    submit = scripts.ScriptAPITests.submit
    complete = scripts.ScriptAPITests.complete

    async def test_catalog_and_support_status_are_read_only(self):
        projects_before = copy.deepcopy(self.manager.projects)
        response = await self.client.get("/api/scripts/skills")
        self.assertEqual(response.status, 200)
        catalog = await response.json()
        self.assertEqual(catalog, {"items": list_skills(), "version": SKILLS_VERSION})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = await self.client.get("/api/scripts/status")
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["writing_skills_supported"])
        self.assertEqual(self.manager.projects, projects_before)
        self.assertEqual(self.manager.jobs, {})
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])

    async def test_selected_skill_persists_updates_reload_and_json_export(self):
        project = await self.create(writing_skill="cinematic-story", content="原始稿件。")
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 1, "title": "新標題"})
        self.assertEqual(response.status, 200, await response.text())
        current = await response.json()
        self.assertEqual(current["writing_skill"], "cinematic-story")
        self.assertEqual(ScriptwritingManager(self.app, self.root).get_project(project["id"]), current)
        response = await self.client.get(f"/api/scripts/projects/{project['id']}/export?format=json")
        self.assertEqual(await response.json(), current)
        alternative = next(item["id"] for item in list_skills() if item["id"] != "cinematic-story")
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 2, "writing_skill": alternative})
        self.assertEqual((await response.json())["writing_skill"], alternative)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 3, "writing_skill": ""})
        self.assertEqual((await response.json())["writing_skill"], "")
        self.assertEqual(self.chat_requests, [])

    async def test_unknown_selection_rejects_without_changing_saved_project(self):
        response = await self.client.post("/api/scripts/projects", json={"writing_skill": "missing-skill"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.manager.projects, {})
        project = await self.create(writing_skill="cinematic-story")
        before = self.manager.projects_path.read_bytes()
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 1, "writing_skill": "missing-skill"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.manager.get_project(project["id"]), project)
        self.assertEqual(self.manager.projects_path.read_bytes(), before)

    async def test_reading_legacy_project_does_not_backfill_saved_selection(self):
        project = await self.create()
        self.manager.projects[project["id"]].pop("writing_skill")
        self.manager._save_projects()
        before = self.manager.projects_path.read_bytes()
        response = await self.client.get(f"/api/scripts/projects/{project['id']}")
        self.assertNotIn("writing_skill", await response.json())
        response = await self.client.get("/api/scripts/projects")
        self.assertNotIn("writing_skill", (await response.json())["items"][0])
        restored = ScriptwritingManager(self.app, self.root)
        self.assertNotIn("writing_skill", restored.get_project(project["id"]))
        self.assertEqual(self.manager.projects_path.read_bytes(), before)

    async def test_writing_task_freezes_selected_guide_when_saved_project_changes(self):
        self.mode = "hold"
        project = await self.create(writing_skill="cinematic-story", content="送出時的稿件。")
        job = await self.submit(project, action="rewrite")
        await asyncio.wait_for(self.started.wait(), 2)
        alternative = next(item["id"] for item in list_skills() if item["id"] != "cinematic-story")
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 1, "writing_skill": alternative, "content": "修改後的稿件。"})
        self.assertEqual(response.status, 200)
        self.finish.set()
        result = await self.complete(job)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["input"]["project"]["writing_skill"], "cinematic-story")
        self.assertEqual(result["input"]["project"]["content"], "送出時的稿件。")
        self.assertIn(skill_prompt("cinematic-story", "rewrite"), self.chat_requests[0]["messages"][0]["content"])
        self.assertEqual(self.manager.get_project(project["id"])["writing_skill"], alternative)
        self.assertEqual(ScriptwritingManager(self.app, self.root).get_job(job["id"])["input"]["project"],
                         result["input"]["project"])

    async def test_internal_project_override_uses_frozen_guide(self):
        project = await self.create(writing_skill="cinematic-story", content="凍結原稿。")
        frozen = copy.deepcopy(project)
        alternative = next(item["id"] for item in list_skills() if item["id"] != "cinematic-story")
        self.manager.update_project(project["id"], {"revision": 1, "writing_skill": alternative})
        child = self.manager.submit({"project_id": project["id"], "action": "rewrite"}, project_override=frozen)
        result = await self.complete(child)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["input"]["project"]["writing_skill"], "cinematic-story")
        self.assertIn(skill_prompt("cinematic-story", "rewrite"), self.chat_requests[-1]["messages"][0]["content"])
        self.assertEqual(self.manager.get_project(project["id"])["writing_skill"], alternative)

    async def test_selected_writing_guide_never_changes_appearance_analysis(self):
        self.tags.append({"name": DEFAULT_SETTINGS["vision_model"], "size": 1000})
        self.show = {"capabilities": ["completion", "vision"]}
        self.reply = "短黑髮，藍色外套，圓形眼睛。"
        assets = references.Assets(self.root)
        self.app["assets"] = assets
        assets.save_image(Image.new("RGB", (16, 16), "blue"), "reference.png", "script-character-reference")
        card = references.card(name="阿青", image_asset_id=references.IMAGE_ID, appearance="短黑髮，藍色外套。")
        project = await self.create(writing_skill="cinematic-story", character_references=[card])
        with patch.object(scriptwriting, "skill_prompt", side_effect=AssertionError("appearance must not use writing guides")):
            result = await self.complete(await self.submit(project, action="appearance", reference_id=card["id"]))
        self.assertEqual(result["status"], "completed", result)
        payload = self.chat_requests[-1]
        expected = build_appearance_messages(card)
        self.assertEqual(payload["messages"][0], expected[0])
        self.assertEqual(payload["messages"][-1]["content"], expected[-1]["content"])
        self.assertEqual(payload["model"], DEFAULT_SETTINGS["vision_model"])
        self.assertEqual(result["input"]["project"]["writing_skill"], "cinematic-story")


class WritingSkillVideoSnapshotTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = video.ScriptVideoAPITests.asyncSetUp
    asyncTearDown = video.ScriptVideoAPITests.asyncTearDown
    fake_tags = video.ScriptVideoAPITests.fake_tags
    fake_show = video.ScriptVideoAPITests.fake_show
    fake_chat = video.ScriptVideoAPITests.fake_chat
    fake_unload = video.ScriptVideoAPITests.fake_unload
    redirect_target = video.ScriptVideoAPITests.redirect_target
    fake_free = video.ScriptVideoAPITests.fake_free
    create = video.ScriptVideoAPITests.create
    submit = video.ScriptVideoAPITests.submit
    complete = video.ScriptVideoAPITests.complete
    project = video.ScriptVideoAPITests.project
    wait_pipeline = video.ScriptVideoAPITests.wait_pipeline

    async def legacy_pipeline_with_updated_saved_skill(self):
        project = await self.project(content=video.SCREENPLAY)
        self.manager.projects[project["id"]].pop("writing_skill")
        self.manager._save_projects()
        with patch.object(self.pipeline_manager, "_launch"):
            response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
            self.assertEqual(response.status, 202, await response.text())
            pipeline = await response.json()
        self.assertNotIn("writing_skill", pipeline["snapshot"])
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 1, "writing_skill": "cinematic-story", "content": "後來修改的編輯稿。"})
        self.assertEqual(response.status, 200, await response.text())
        return pipeline, await response.json()

    async def assert_legacy_pipeline_keeps_original_method(self, pipeline, current):
        result = await self.wait_pipeline(pipeline)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertNotIn("writing_skill", result["snapshot"])
        self.assertEqual(result["snapshot"]["content"], video.SCREENPLAY)
        self.assertEqual(self.chat_actions, ["storyboard"])
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["input"]["project"]["writing_skill"], "")
        self.assertEqual(child["input"]["project"]["content"], video.SCREENPLAY)
        self.assertNotIn("Selected writing method:", self.chat_requests[-1]["messages"][0]["content"])
        self.assertEqual(self.manager.get_project(current["id"]), current)
        archived = json.loads((self.manager.history_directory / f"{child['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(archived["input"]["project"]["writing_skill"], "")

    async def test_legacy_pipeline_missing_skill_does_not_adopt_later_saved_selection(self):
        pipeline, current = await self.legacy_pipeline_with_updated_saved_skill()
        self.pipeline_manager._launch(pipeline["id"])
        await self.assert_legacy_pipeline_keeps_original_method(pipeline, current)

    async def test_resumed_legacy_pipeline_missing_skill_keeps_original_unselected_method(self):
        pipeline, current = await self.legacy_pipeline_with_updated_saved_skill()
        record = self.pipeline_manager.pipelines[pipeline["id"]]
        self.pipeline_manager._update(record, status="interrupted", error="Synthetic restart")
        persisted = json.loads((self.pipeline_manager.directory / f"{pipeline['id']}.json").read_text(encoding="utf-8"))
        self.assertNotIn("writing_skill", persisted["snapshot"])
        response = await self.client.post(f"/api/scripts/video-pipelines/{pipeline['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        await self.assert_legacy_pipeline_keeps_original_method(await response.json(), current)

    async def test_auto_video_freezes_selected_skill_through_screenplay_and_storyboard(self):
        self.mode = "unload_hold"
        project = await self.project(writing_skill="cinematic-story", content="送出前的稿件。")
        response = await self.client.post("/api/scripts/jobs",
                                          json={"project_id": project["id"], "action": "rewrite", "auto_video": True})
        self.assertEqual(response.status, 202, await response.text())
        source = await response.json()
        await asyncio.wait_for(self.unload_started.wait(), 3)
        alternative = next(item["id"] for item in list_skills() if item["id"] != "cinematic-story")
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": 1, "writing_skill": alternative})
        self.assertEqual(response.status, 200)
        self.mode = "normal"
        self.finish.set()
        result = await self.wait_pipeline({"id": source["video_pipeline_id"]})
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["snapshot"]["writing_skill"], "cinematic-story")
        storyboard = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(storyboard["input"]["project"]["writing_skill"], "cinematic-story")
        self.assertIn(skill_prompt("cinematic-story", "storyboard"), self.chat_requests[-1]["messages"][0]["content"])
        self.assertEqual(self.manager.get_project(project["id"])["writing_skill"], alternative)


if __name__ == "__main__":
    unittest.main()
