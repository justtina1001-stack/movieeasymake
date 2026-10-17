from __future__ import annotations

import unittest
import json
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from domain import compile_request
from shortfilm import (
    ShortFilmStore,
    ShortFilmError,
    ShortFilmConflictError,
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
    def test_video_vae_defaults_for_new_and_legacy_projects(self):
        self.assertEqual(new_project()["video_vae"], "auto")
        project, scene, shot = self.project_with_shot()
        project.pop("video_vae")
        self.assertEqual(normalize_project(project)["video_vae"], "auto")
        payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
        self.assertEqual(payload["video_vae"], "auto")
        project["video_vae"] = "unknown"
        self.assertEqual(normalize_project(project)["video_vae"], "auto")

    def test_video_vae_survives_store_reload_and_shot_compilation(self):
        for selected in ("auto", "int8", "fp16"):
            with self.subTest(video_vae=selected), TemporaryDirectory() as directory:
                project, scene, shot = self.project_with_shot()
                project["video_vae"] = selected
                store = ShortFilmStore(Path(directory))
                saved = store.create(project)
                restored = ShortFilmStore(Path(directory)).get(saved["id"])
                self.assertEqual(restored["video_vae"], selected)
                payload, _ = compile_shot_payload(restored, scene["id"], shot["id"])
                self.assertEqual(payload["video_vae"], selected)

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
        with TemporaryDirectory() as directory:
            store = ShortFilmStore(Path(directory))
            created = store.create(new_project("測試短片"))
            self.assertEqual(store.get(created["id"])["title"], "測試短片")
            created["title"] = "修改後"
            updated = store.update(created["id"], created)
            self.assertEqual(updated["title"], "修改後")
            self.assertEqual(len(store.list()), 1)
            store.delete(created["id"])
            self.assertEqual(store.list(), [])

    def test_store_detects_stale_project_revision(self):
        with TemporaryDirectory() as directory:
            store = ShortFilmStore(Path(directory))
            first_tab = store.create(new_project("原名"))
            other_tab = store.get(first_tab["id"])
            first_tab["title"] = "第一視窗修改"
            saved = store.update(first_tab["id"], first_tab)
            self.assertEqual(saved["revision"], 2)
            other_tab["title"] = "過期視窗修改"
            with self.assertRaises(ShortFilmConflictError) as raised:
                store.update(other_tab["id"], other_tab)
            self.assertEqual(raised.exception.current_revision, 2)
            self.assertEqual(store.get(saved["id"])["title"], "第一視窗修改")
            legacy_request = dict(saved)
            legacy_request.pop("revision")
            with self.assertRaises(ShortFilmConflictError):
                store.update(saved["id"], legacy_request)

    def test_legacy_project_revision_and_long_text_survive_reload_and_unrelated_edit(self):
        with TemporaryDirectory() as directory:
            project, scene, shot = self.project_with_shot()
            project.pop("revision", None)
            project["title"] = "舊" * 90
            project["updated_at"] = "2025-01-01T00:00:00+00:00"
            shot["action"] = "舊字句" * 2000
            index = Path(directory) / "projects.json"
            index.write_text(json.dumps([project]), encoding="utf-8")
            store = ShortFilmStore(Path(directory))
            restored = store.get(project["id"])
            self.assertEqual(restored["revision"], 0)
            self.assertEqual(restored["updated_at"], project["updated_at"])
            self.assertEqual(restored["title"], project["title"])
            self.assertEqual(restored["scenes"][0]["shots"][0]["action"], shot["action"])
            restored["target_duration"] = 40
            saved = store.update(project["id"], restored)
            self.assertEqual(saved["revision"], 1)
            reloaded = ShortFilmStore(Path(directory)).get(project["id"])
            self.assertEqual(reloaded["scenes"][0]["shots"][0]["action"], shot["action"])
            saved["scenes"][0]["shots"][0]["action"] += "新增"
            with self.assertRaisesRegex(ShortFilmError, "5000 字上限"):
                store.update(project["id"], saved)

    def test_legacy_missing_collections_and_nested_optional_fields_remain_loadable(self):
        for legacy in ({"id": "a" * 32, "title": "早期專案"},
                       {"id": "b" * 32, "title": "早期場次", "assets": [{"alias": "Alice"}], "scenes": [{"title": "場次"}]}):
            with self.subTest(legacy=legacy), TemporaryDirectory() as directory:
                index = Path(directory) / "projects.json"
                index.write_text(json.dumps([legacy]), encoding="utf-8")
                store = ShortFilmStore(Path(directory))
                self.assertTrue(store.storage_status()["available"])
                loaded = store.get(legacy["id"])
                self.assertEqual(loaded["revision"], 0)
                self.assertEqual(loaded["title"], legacy["title"])
                if loaded["scenes"]:
                    self.assertEqual(loaded["scenes"][0]["shots"], [])
                    self.assertEqual(loaded["assets"][0]["image_asset_ids"], [])
                loaded["synopsis"] = "補上大綱"
                saved = store.update(legacy["id"], loaded)
                self.assertEqual(saved["revision"], 1)

    def test_overlong_new_text_is_rejected_instead_of_truncated(self):
        for field, limit in (("title", 80), ("style", 1000), ("synopsis", 5000)):
            with self.subTest(field=field):
                project = new_project()
                project[field] = "字" * (limit + 1)
                with self.assertRaisesRegex(ShortFilmError, f"{limit} 字上限"):
                    normalize_project(project)
                self.assertEqual(len(project[field]), limit + 1)
        project, _, shot = self.project_with_shot()
        shot["dialogue"] = "字" * 1801
        with self.assertRaisesRegex(ShortFilmError, "鏡頭 1.*台詞.*1800 字上限"):
            normalize_project(project)
        shot["dialogue"] = ""
        project["segment_draft"] = {"prompts": ["字" * 5001]}
        with self.assertRaisesRegex(ShortFilmError, "第 1 段分鏡詞"):
            normalize_project(project)

    def test_corrupt_index_without_backup_blocks_every_mutation_and_preserves_bytes(self):
        for damaged in (b"{broken", b"{}", b"[{}]"):
            with self.subTest(damaged=damaged), TemporaryDirectory() as directory:
                index = Path(directory) / "projects.json"
                index.write_bytes(damaged)
                store = ShortFilmStore(Path(directory))
                self.assertFalse(store.storage_status()["available"])
                for action in (store.list, lambda: store.create(new_project()),
                               lambda: store.update("a" * 32, {}), lambda: store.delete("a" * 32)):
                    with self.assertRaisesRegex(ShortFilmError, "儲存已停止"):
                        action()
                self.assertEqual(index.read_bytes(), damaged)

    def test_corrupt_index_restores_valid_backup_and_archives_original_before_write(self):
        with TemporaryDirectory() as directory:
            store = ShortFilmStore(Path(directory))
            project = store.create(new_project("原始備份"))
            project["title"] = "最新版本"
            store.update(project["id"], project)
            damaged = b"[truncated-json"
            store.index_path.write_bytes(damaged)
            recovered = ShortFilmStore(Path(directory))
            self.assertTrue(recovered.storage_status()["recovered"])
            self.assertEqual(recovered.get(project["id"])["title"], "原始備份")
            self.assertEqual(recovered.corrupt_path.read_bytes(), damaged)
            saved = recovered.get(project["id"])
            saved["title"] = "確認後存回"
            recovered.update(saved["id"], saved)
            self.assertEqual(recovered.corrupt_path.read_bytes(), damaged)
            self.assertEqual(ShortFilmStore(Path(directory)).get(saved["id"])["title"], "確認後存回")

    def test_invalid_project_inside_index_is_not_silently_dropped(self):
        with TemporaryDirectory() as directory:
            index = Path(directory) / "projects.json"
            contents = json.dumps([new_project("有效"), {"id": "invalid", "title": "不可丟棄"}])
            index.write_text(contents, encoding="utf-8")
            store = ShortFilmStore(Path(directory))
            with self.assertRaises(ShortFilmError):
                store.create(new_project("不能覆寫"))
            self.assertEqual(index.read_text(encoding="utf-8"), contents)

    def test_malformed_nested_records_cannot_be_silently_removed_on_save(self):
        for changes in ({"assets": "broken"}, {"assets": [None]}, {"scenes": [None]},
                        {"scenes": [{"shots": "broken"}]}, {"scenes": [{"shots": [None]}]}):
            with self.subTest(changes=changes), TemporaryDirectory() as directory:
                index = Path(directory) / "projects.json"
                contents = json.dumps([{**new_project(), **changes}])
                index.write_text(contents, encoding="utf-8")
                store = ShortFilmStore(Path(directory))
                with self.assertRaises(ShortFilmError):
                    store.create(new_project("不能覆寫"))
                self.assertEqual(index.read_text(encoding="utf-8"), contents)

    def test_failed_atomic_write_does_not_change_memory_or_index(self):
        with TemporaryDirectory() as directory:
            store = ShortFilmStore(Path(directory))
            project = store.create(new_project("原始"))
            index_before = store.index_path.read_bytes()
            projects_before = store.list()
            original_replace = Path.replace
            def fail_index_replace(path, target):
                if Path(target) == store.index_path:
                    raise OSError("disk unavailable")
                return original_replace(path, target)
            updated = dict(project, title="不應存進記憶體")
            for mutation in (lambda: store.create(new_project("新增失敗")),
                             lambda: store.update(project["id"], updated),
                             lambda: store.delete(project["id"])):
                with patch.object(Path, "replace", fail_index_replace), self.assertRaises(OSError):
                    mutation()
                self.assertEqual(store.list(), projects_before)
                self.assertEqual(store.index_path.read_bytes(), index_before)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
