from __future__ import annotations

import unittest
from pathlib import Path

from domain import compile_request
from shortfilm import (
    ShortFilmStore,
    compile_shot_payload,
    new_asset,
    new_project,
    new_scene,
    new_shot,
    normalize_project,
    project_warnings,
)


class ShortFilmTests(unittest.TestCase):
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
