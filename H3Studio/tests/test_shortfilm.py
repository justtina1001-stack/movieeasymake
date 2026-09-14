from __future__ import annotations

import unittest
from pathlib import Path

from domain import compile_request
from shortfilm import (
    ShortFilmStore,
    ShortFilmError,
    append_continuous_scene,
    changed_reference_aliases,
    compile_shot_payload,
    new_asset,
    new_project,
    new_scene,
    new_shot,
    normalize_project,
    project_job_records,
    project_warnings,
    resolve_shot_asset_ids,
)


class ShortFilmTests(unittest.TestCase):
    def test_continuous_minute_creates_twelve_connected_shots(self):
        project = new_project("一分鐘")
        asset = new_asset("character", "主角")
        asset["image_asset_ids"] = ["1" * 32]
        project["assets"] = [asset]
        result = append_continuous_scene(project, {"duration": 5, "prompts": [f"主角向前走，第{i}段" for i in range(12)]})
        scene = result["scenes"][0]
        self.assertEqual(len(scene["shots"]), 12)
        self.assertEqual(result["target_duration"], 60)
        self.assertEqual(project["scenes"], [])  # no mutation of caller
        for index, shot in enumerate(scene["shots"]):
            self.assertEqual(shot["continue_previous"], index > 0)
            payload, _ = compile_shot_payload(result, scene["id"], shot["id"], continuation_asset_id="2" * 32 if index else None)
            self.assertEqual(payload["duration"], 5)
            self.assertEqual(payload["references"][0]["image_asset_ids"], ["1" * 32])
            self.assertEqual(payload["first_image_asset_id"], "2" * 32 if index else None)
            compiled = compile_request(payload)
            self.assertIn("subject_definitions:", compiled.prompt)
            if index:
                self.assertIn("preceding shot's final frame", compiled.prompt)

    def test_continuous_append_preserves_old_jobs_and_does_not_link_first_segment(self):
        project, scene, shot = self.project_with_shot()
        shot["status"] = "completed"
        shot["job_id"] = "a" * 32
        result = append_continuous_scene(project, {"duration": 10, "prompts": ["走路", "停下"]})
        self.assertEqual(result["scenes"][0]["shots"][0]["job_id"], "a" * 32)
        self.assertFalse(result["scenes"][1]["shots"][0]["continue_previous"])
        self.assertEqual(result["target_duration"], 25)

    def test_continuous_shared_materials_plus_tail_frame_limit(self):
        project = new_project()
        for index in range(9):
            asset = new_asset("character", f"actor_{index}")
            asset["image_asset_ids"] = [f"{index + 1:032x}"]
            project["assets"].append(asset)
        plan = {"duration": 5, "prompts": ["walk", "run"], "asset_ids": [asset["id"] for asset in project["assets"]]}
        with self.assertRaisesRegex(ShortFilmError, "10 張.*續接尾幀"):
            append_continuous_scene(project, plan)
        self.assertEqual(project["scenes"], [])
        plan["asset_ids"] = plan["asset_ids"][:8]
        result = append_continuous_scene(project, plan)
        self.assertEqual(len(result["scenes"][0]["shots"][1]["asset_ids"]), 8)
        # Auto-matched aliases count in addition to the manual selection.
        plan["prompts"][1] = "actor_8 walks"
        with self.assertRaisesRegex(ShortFilmError, "10 張"):
            append_continuous_scene(project, plan)

    def test_continuous_limits_count_multiimage_assets_and_audio(self):
        project = new_project()
        asset = new_asset("character", "actor")
        asset["image_asset_ids"] = [f"{index + 1:032x}" for index in range(9)]
        project["assets"] = [asset]
        with self.assertRaisesRegex(ShortFilmError, "10 張"):
            append_continuous_scene(project, {"prompts": ["actor walks", "actor runs"]})
        project["assets"] = []
        for index in range(4):
            asset = new_asset("character", f"voice_{index}")
            asset["audio_asset_id"] = f"{index + 1:032x}"
            project["assets"].append(asset)
        with self.assertRaisesRegex(ShortFilmError, "4 段.*3 段"):
            append_continuous_scene(project, {"prompts": ["walk"], "asset_ids": [a["id"] for a in project["assets"]]})

    def test_continuous_plan_rejects_invalid_input(self):
        for plan in (None, {}, {"duration": 4, "prompts": ["walk"]}, {"prompts": [""]}, {"prompts": ["x" * 5001]}, {"prompts": ["walk"], "asset_ids": ["missing"]}):
            with self.subTest(plan=str(plan)[:80]), self.assertRaises(ShortFilmError):
                append_continuous_scene(new_project(), plan)

    def test_segment_draft_and_continuation_source_survive_store_normalization(self):
        project, scene, shot = self.project_with_shot()
        project["segment_draft"] = {"duration": 5, "target_duration": 60, "prompts": ["draft", ""], "asset_ids": []}
        shot["continuation_job_id"] = "a" * 32
        result = normalize_project(project)
        self.assertEqual(result["segment_draft"]["prompts"], ["draft", ""])
        self.assertEqual(result["scenes"][0]["shots"][0]["continuation_job_id"], "a" * 32)

    def test_project_job_records_keeps_history_and_legacy_link(self):
        project = new_project("測試短片")
        scene = new_scene()
        shot = new_shot()
        shot["job_id"] = "legacy-job"
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        records = [
            {"id": "legacy-job"},
            {"id": "history-job", "shortfilm_project_id": project["id"]},
            {"id": "other-job", "shortfilm_project_id": "another-project"},
        ]
        self.assertEqual(
            [job["id"] for job in project_job_records(project, records)],
            ["legacy-job", "history-job"],
        )

    def test_shot_can_store_a_shorter_retime_preview_duration(self):
        project = new_project("節奏測試")
        project["export_frames"] = True
        project["scenes"] = [{"title": "場次 1", "shots": [{
            "duration": 5,
            "retime_duration": 2,
            "action": "角色快速進場並停在畫面中央。",
        }]}]
        normalized = normalize_project(project)
        shot = normalized["scenes"][0]["shots"][0]
        self.assertEqual(shot["duration"], 5)
        self.assertEqual(shot["retime_duration"], 2)
        payload, _ = compile_shot_payload(
            normalized,
            normalized["scenes"][0]["id"],
            shot["id"],
        )
        self.assertEqual(payload["duration"], 5)
        self.assertEqual(payload["retime_duration"], 2)
        self.assertTrue(payload["export_frames"])

    def project_with_shot(self) -> tuple[dict, dict, dict]:
        project = new_project("雨夜月台")
        project["target_duration"] = 5
        scene = new_scene()
        scene.update({"title": "月台重逢", "location": "雨夜的老車站月台", "time_of_day": "午夜"})
        shot = new_shot()
        shot.update({
            "title": "認出彼此",
            "action": "小雨先低頭握緊車票，聽見腳步後抬頭，向前一步並停在安全距離。",
            "ending": "小雨停下腳步，兩人隔著雨幕對望，構圖穩定。",
        })
        scene["shots"].append(shot)
        project["scenes"].append(scene)
        return project, scene, shot

    def test_text_only_shot_uses_official_base_sections(self) -> None:
        project, scene, shot = self.project_with_shot()
        payload, warnings = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["mode"], "t2v")
        self.assertTrue(warnings)
        compiled = compile_request(payload)
        self.assertIn("integrated_multimodal_description:", compiled.prompt)
        self.assertIn("overall_soundscape:", compiled.prompt)
        self.assertIn("non_diegetic_music:", compiled.prompt)

    def test_reference_shot_uses_six_section_ref_prompt(self) -> None:
        project, scene, shot = self.project_with_shot()
        character = new_asset("character", "小雨")
        character["description"] = "short black hair, blue raincoat, red ticket in the right hand"
        character["image_asset_ids"] = ["1" * 32]
        character["audio_asset_id"] = "2" * 32
        project["assets"].append(character)
        shot.update({
            "asset_ids": [character["id"]],
            "speaker_alias": "小雨",
            "dialogue_language": "Chinese",
            "dialogue": "你真的回來了。",
        })
        payload, warnings = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["mode"], "r2v")
        self.assertFalse(warnings)
        compiled = compile_request(payload)
        headings = [
            "subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:",
        ]
        positions = [compiled.prompt.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("<Subject 1>", compiled.prompt)
        self.assertIn("(S1) says <d>[Chinese] 你真的回來了。</d>", compiled.prompt)
        self.assertIn("fully_preserved", compiled.prompt)

    def test_named_assets_are_automatically_attached_from_story_text(self) -> None:
        project, scene, shot = self.project_with_shot()
        character = new_asset("character", "小雨")
        character["image_asset_ids"] = ["1" * 32]
        background = new_asset("background", "月台")
        background["image_asset_ids"] = ["2" * 32]
        extra = new_asset("object", "紅傘")
        extra["image_asset_ids"] = ["3" * 32]
        project["assets"] = [character, background, extra]

        resolved = resolve_shot_asset_ids(project, scene, shot)
        self.assertEqual(resolved, [character["id"], background["id"]])
        payload, warnings = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["mode"], "r2v")
        self.assertEqual(
            [item["alias"] for item in payload["references"]],
            ["小雨", "月台"],
        )
        self.assertFalse(any("沒有選擇參考素材" in warning for warning in warnings))

    def test_manual_asset_selection_supplements_automatic_matches(self) -> None:
        project, scene, shot = self.project_with_shot()
        character = new_asset("character", "小雨")
        prop = new_asset("object", "紅傘")
        project["assets"] = [character, prop]
        shot["asset_ids"] = [prop["id"]]
        self.assertEqual(
            resolve_shot_asset_ids(project, scene, shot),
            [character["id"], prop["id"]],
        )

    def test_changed_reference_aliases_detects_old_continuation_source(self) -> None:
        expected = [{
            "alias": "妮妮",
            "image_asset_ids": ["1" * 32],
            "audio_asset_id": None,
            "voice_mode": "timbre",
        }, {
            "alias": "神社",
            "image_asset_ids": ["2" * 32],
            "audio_asset_id": None,
            "voice_mode": "timbre",
        }]
        actual = [{
            "alias": "妮妮",
            "image_asset_ids": ["3" * 32],
            "audio_asset_id": None,
            "voice_mode": "timbre",
        }]
        self.assertEqual(changed_reference_aliases(expected, actual), ["妮妮", "神社"])

    def test_project_normalization_removes_unknown_asset_links(self) -> None:
        project, scene, shot = self.project_with_shot()
        shot["asset_ids"] = ["f" * 32]
        shot["camera"] = "unsupported"
        normalized = normalize_project(project)
        self.assertEqual(normalized["scenes"][0]["shots"][0]["asset_ids"], [])
        self.assertEqual(normalized["scenes"][0]["shots"][0]["camera"], "static")

    def test_text_only_named_asset_stays_in_t2v_description(self) -> None:
        project, scene, shot = self.project_with_shot()
        character = new_asset("character", "小雨")
        character["description"] = "short black hair and a blue raincoat"
        project["assets"].append(character)
        shot["asset_ids"] = [character["id"]]
        payload, warnings = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["mode"], "t2v")
        self.assertIn("小雨 (short black hair and a blue raincoat)", payload["prompt"])
        self.assertTrue(any("文字描述" in warning for warning in warnings))

    def test_unchecked_continuity_does_not_reuse_stale_frame(self) -> None:
        project, scene, shot = self.project_with_shot()
        shot["continue_previous"] = False
        shot["continuation_asset_id"] = "3" * 32
        payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["mode"], "t2v")
        self.assertIsNone(payload["first_image_asset_id"])

    def test_project_warnings_cover_timeline_and_missing_action(self) -> None:
        project, _, shot = self.project_with_shot()
        project["target_duration"] = 30
        shot["action"] = ""
        warnings = project_warnings(normalize_project(project))
        self.assertTrue(any("分鏡合計" in warning for warning in warnings))
        self.assertTrue(any("可見動作" in warning for warning in warnings))

    def test_project_warnings_count_all_per_shot_reference_images(self) -> None:
        project, scene, shot = self.project_with_shot()
        first = new_asset("character", "角色")
        first["image_asset_ids"] = [f"{number:032x}" for number in range(1, 7)]
        second = new_asset("background", "背景")
        second["image_asset_ids"] = [f"{number:032x}" for number in range(7, 10)]
        project["assets"] = [first, second]
        shot["asset_ids"] = [first["id"], second["id"]]
        shot["storyboard_asset_id"] = "f" * 32
        normalized = normalize_project(project)
        warnings = project_warnings(normalized)
        self.assertTrue(any("10 張參考圖片" in warning and "9 張上限" in warning for warning in warnings))

    def test_store_crud(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "data" / "test-shortfilm-store"
        directory.mkdir(parents=True, exist_ok=True)
        projects_file = directory / "projects.json"
        projects_file.unlink(missing_ok=True)
        try:
            store = ShortFilmStore(directory)
            created = store.create(new_project("測試短片"))
            self.assertEqual(store.get(created["id"])["title"], "測試短片")
            created["title"] = "修改後"
            updated = store.update(created["id"], created)
            self.assertEqual(updated["title"], "修改後")
            self.assertEqual(len(store.list()), 1)
            store.delete(created["id"])
            self.assertEqual(store.list(), [])
        finally:
            projects_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
