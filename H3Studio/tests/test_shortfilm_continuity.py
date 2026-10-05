from __future__ import annotations

import unittest
from copy import deepcopy

from domain import compile_request, required_asset_ids
from shortfilm import compile_shot_payload, new_asset, new_project, new_scene, new_shot


IMAGE_A = "a" * 32
IMAGE_B = "b" * 32
IMAGE_C = "c" * 32
AUDIO_A = "d" * 32
VIDEO_A = "e" * 32


class ShortFilmContinuityTests(unittest.TestCase):
    def project_with_shot(self, action="角色A轉身，看向門口。", duration=15):
        project = new_project("連續性測試")
        scene = new_scene("場景")
        shot = new_shot()
        shot["title"] = "片段"
        shot["duration"] = duration
        shot["action"] = action
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        return project, scene, shot

    def add_asset(self, project, shot, kind="character", alias="角色A", description="紅外套與短黑髮", images=None, audio=None):
        asset = new_asset(kind, alias)
        asset["description"] = description
        asset["image_asset_ids"] = list(images or [])
        asset["audio_asset_id"] = audio
        project["assets"].append(asset)
        shot["asset_ids"].append(asset["id"])
        return asset

    def compiled(self, project, scene, shot):
        payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
        return payload, compile_request(payload)

    def test_turnaround_sheet_and_multiple_images_define_one_physical_individual(self):
        for kind in ("character", "creature"):
            with self.subTest(kind=kind):
                project, scene, shot = self.project_with_shot()
                self.add_asset(project, shot, kind, images=[IMAGE_A, IMAGE_B])
                original = deepcopy(project)
                payload, compiled = self.compiled(project, scene, shot)
                definitions, remainder = compiled.prompt.split("\n\nsummary:", 1)
                retention = remainder.split("retention_analysis:\n", 1)[1].split("\n\ndetailed_description:", 1)[0]
                self.assertIn("one and the same physical individual", definitions)
                self.assertIn("turnaround/reference sheet", definitions)
                self.assertIn("sheet layout, panels, labels, or multiple figures", definitions)
                self.assertIn("same physical individual across views and cuts", retention)
                self.assertIn("not extra people, bodies, panels, or labels", retention)
                self.assertEqual(retention.count("<Subject 1>:"), 1)
                self.assertEqual(len(compiled.mapping), 1)
                self.assertEqual(compiled.mapping[0]["picture_tags"], ["<Picture 1>", "<Picture 2>"])
                self.assertNotIn("<Subject 2>", compiled.prompt)
                self.assertEqual(required_asset_ids(compiled), [IMAGE_A, IMAGE_B])
                self.assertEqual(payload["references"][0]["image_asset_ids"], [IMAGE_A, IMAGE_B])
                self.assertEqual(project, original)

    def test_two_characters_remain_distinct_subjects_and_story_exceptions_are_allowed(self):
        action = "角色A看向鏡中的倒影；角色B是她的雙胞胎，從門口走進。"
        project, scene, shot = self.project_with_shot(action)
        self.add_asset(project, shot, alias="角色A", images=[IMAGE_A])
        self.add_asset(project, shot, alias="角色B", images=[IMAGE_B])
        _, compiled = self.compiled(project, scene, shot)
        self.assertEqual([item["subject_tag"] for item in compiled.mapping], ["<Subject 1>", "<Subject 2>"])
        self.assertIn("<Subject 1>（角色A）看向鏡中的倒影", compiled.prompt)
        self.assertIn("<Subject 2>（角色B）是她的雙胞胎", compiled.prompt)
        self.assertIn("Explicit story directions for twins, duplication, shadows, or reflections take precedence", compiled.prompt)
        self.assertIn("story explicitly defines this alias as a group or distinct additional individuals", compiled.prompt)
        self.assertNotIn("only one person in the scene", compiled.prompt)

    def test_named_group_direction_is_preserved_instead_of_forcing_one_member(self):
        project, scene, shot = self.project_with_shot("村民是一群三人，三名村民站在門旁。")
        self.add_asset(project, shot, alias="村民", description="三名成年村民穿著各自不同的雨衣", images=[IMAGE_A])
        _, compiled = self.compiled(project, scene, shot)
        self.assertIn("三名成年村民", compiled.prompt)
        self.assertIn("三名<Subject 1>（村民）站在門旁", compiled.prompt)
        self.assertIn("Unless the story explicitly defines this alias as a group", compiled.prompt)

    def test_object_quantity_is_not_a_character_count_or_assumed_single_object(self):
        project, scene, shot = self.project_with_shot("兩個杯子放在桌上，角色A將右邊杯子拿起。")
        self.add_asset(project, shot, kind="object", alias="杯子", description="兩個白色瓷杯", images=[IMAGE_A])
        _, compiled = self.compiled(project, scene, shot)
        definitions = compiled.prompt.split("\n\nsummary:", 1)[0]
        retention = compiled.prompt.split("retention_analysis:\n", 1)[1].split("\n\ndetailed_description:", 1)[0]
        self.assertIn("兩個白色瓷杯", definitions)
        self.assertNotIn("physical individual", definitions)
        self.assertIn("explicitly stated quantity", retention)
        self.assertIn("until an explicit action changes them", retention)
        self.assertNotIn("face, body proportions", retention)
        self.assertNotIn("one and the same physical individual", retention)
        self.assertIn("將右邊<Subject 1>（杯子）拿起", compiled.prompt)

    def test_pair_counts_and_explicit_state_changes_are_retained_without_inventing_props(self):
        action = "地面只有一對左右腳印。 [Shot 2] At 00:04.000, 角色A再踩一步，只新增一個右腳印。"
        project, scene, shot = self.project_with_shot(action)
        self.add_asset(project, shot, images=[IMAGE_A])
        payload, compiled = self.compiled(project, scene, shot)
        self.assertIn(action, payload["prompt"])
        self.assertIn("Preserve explicitly stated object quantities, left/right pairing, positions, and states", compiled.prompt)
        self.assertIn("until an explicit action changes them", compiled.prompt)
        self.assertIn("一對左右腳印", compiled.prompt)
        self.assertIn("只新增一個右腳印", compiled.prompt)
        self.assertNotIn("all objects must be single", compiled.prompt)
        plain_project, plain_scene, plain_shot = self.project_with_shot("角色A坐在椅子上。")
        plain_payload, _ = self.compiled(plain_project, plain_scene, plain_shot)
        self.assertNotIn("footprint", plain_payload["prompt"].lower())
        self.assertNotIn("腳印", plain_payload["prompt"])

    def test_background_and_style_do_not_receive_character_individual_rules(self):
        project, scene, shot = self.project_with_shot("空房間的光線逐渐變暗。")
        self.add_asset(project, shot, kind="background", alias="空房間", description="空間中央的桌面朝北", images=[IMAGE_A])
        self.add_asset(project, shot, kind="style", alias="風格", description="柔和的暖色水彩筆觸", images=[IMAGE_B])
        _, compiled = self.compiled(project, scene, shot)
        self.assertNotIn("one and the same physical individual", compiled.prompt)
        self.assertNotIn("face, body proportions", compiled.prompt)
        self.assertIn("空間中央的桌面朝北", compiled.prompt)
        self.assertIn("柔和的暖色水彩筆觸", compiled.prompt)

    def test_referenced_subject_appearance_is_defined_once_and_text_only_is_retained(self):
        project, scene, shot = self.project_with_shot("角色A和角色B查看盒子。")
        self.add_asset(project, shot, alias="角色A", description="特有的銀色髮夾和深紅雨衣", images=[IMAGE_A])
        self.add_asset(project, shot, kind="object", alias="盒子", description="刻有六角紋路的木盒", images=[IMAGE_B])
        self.add_asset(project, shot, alias="角色B", description="捲髮與明黃色襯衫")
        payload, compiled = self.compiled(project, scene, shot)
        self.assertNotIn("特有的銀色髮夾和深紅雨衣", payload["prompt"])
        self.assertNotIn("刻有六角紋路的木盒", payload["prompt"])
        self.assertEqual(compiled.prompt.count("特有的銀色髮夾和深紅雨衣"), 1)
        self.assertEqual(compiled.prompt.count("刻有六角紋路的木盒"), 1)
        self.assertIn("角色B (捲髮與明黃色襯衫)", payload["prompt"])
        self.assertIn("For 角色B, preserve the same physical individual", compiled.prompt)

    def test_audio_only_character_definition_preserves_appearance_once(self):
        project, scene, shot = self.project_with_shot()
        self.add_asset(project, shot, description="橙色髮束與灰色眼睛", audio=AUDIO_A)
        payload, compiled = self.compiled(project, scene, shot)
        self.assertNotIn("橙色髮束與灰色眼睛", payload["prompt"])
        self.assertEqual(compiled.prompt.count("橙色髮束與灰色眼睛"), 1)
        self.assertIn("文字描述", compiled.prompt)
        self.assertEqual(compiled.reference_audios, [AUDIO_A])

    def test_audio_only_non_subject_description_is_not_lost(self):
        project, scene, shot = self.project_with_shot("風聲穿過空房間。")
        self.add_asset(project, shot, kind="background", alias="空房間", description="牆上有一道裂縫", audio=AUDIO_A)
        payload, compiled = self.compiled(project, scene, shot)
        self.assertIn("空房間 (牆上有一道裂縫)", payload["prompt"])
        self.assertIn("牆上有一道裂縫", compiled.prompt)

    def test_pure_text_subject_keeps_full_appearance_and_individual_direction(self):
        project, scene, shot = self.project_with_shot()
        self.add_asset(project, shot, description="short black hair and a blue raincoat")
        payload, compiled = self.compiled(project, scene, shot)
        self.assertEqual(payload["mode"], "t2v")
        self.assertIn("角色A (short black hair and a blue raincoat)", payload["prompt"])
        self.assertIn("same physical individual across views and cuts", compiled.prompt)
        self.assertNotIn("subject_definitions:", compiled.prompt)

    def test_object_closeup_and_late_character_entry_are_not_forced_into_opening_cast(self):
        action = "地面的一對腳印特寫，角色A完全不在畫面內。 [Shot 2] At 00:06.000, 角色A才從門口走進畫面。"
        project, scene, shot = self.project_with_shot(action)
        self.add_asset(project, shot, images=[IMAGE_A])
        payload, compiled = self.compiled(project, scene, shot)
        self.assertIn(action, payload["prompt"])
        self.assertIn("Their visibility and entry timing follow the action", compiled.prompt)
        self.assertIn("do not insert them into offscreen or object-only views", compiled.prompt)
        self.assertNotIn("featuring 角色A", payload["prompt"])
        self.assertNotIn("From the very first visible frame", payload["prompt"])

    def test_nested_cuts_keep_camera_text_times_and_dialogue_without_default_overrides(self):
        action = "25mm廣角大全景，固定機位，角色A坐下。 [Shot 2] At 00:02.000, 50mm中焦，攝影機繞到角色A背後。 角色A說：<d>[Chinese] 別回頭。</d>"
        project, scene, shot = self.project_with_shot(action)
        self.add_asset(project, shot, images=[IMAGE_A])
        shot["shot_size"] = "medium"
        shot["camera"] = "static"
        shot["camera_detail"] = "Keep the camera on the doorway through the last cut."
        payload, compiled = self.compiled(project, scene, shot)
        self.assertIn(action, payload["prompt"])
        self.assertIn(shot["camera_detail"], payload["prompt"])
        self.assertNotIn("The camera remains in a stable Static Shot", compiled.prompt)
        self.assertNotIn("Opening framing: medium shot", compiled.prompt)
        self.assertIn("each cut's own framing and camera direction", compiled.prompt)
        self.assertIn("[Shot 2] At 00:02.000", compiled.prompt)
        self.assertIn("<d>[Chinese] 別回頭。</d>", compiled.prompt)
        self.assertIn("video segment", payload["shortfilm_summary"])

    def test_packed_cut_camera_lines_are_deduplicated_and_unique_supplement_is_retained(self):
        first = "Camera: custom; shot size: wide. 25mm廣角，固定機位\n靜止，没有明显移動"
        second = "Camera: custom; shot size: medium. 50mm中焦，固定機位，從角色A背後拍攝\n靜止，没有明显移動"
        action = f"Cut title: 開場. {first} 角色A坐下。 [Shot 2] At 00:02.000, Cut title: 轉頭. {second} 角色A轉頭。"
        project, scene, shot = self.project_with_shot(action, duration=5)
        shot["camera"] = "custom"
        shot["camera_detail"] = f"0–2s: {first}\n2–5s: {second}\nKeep the doorway visible in the last cut."
        original = deepcopy(project)
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn(action, payload["prompt"])
        self.assertEqual(payload["prompt"].count("25mm廣角"), 1)
        self.assertEqual(payload["prompt"].count("50mm中焦"), 1)
        self.assertEqual(payload["prompt"].count("靜止，没有明显移動"), 2)
        self.assertIn("Keep the doorway visible in the last cut.", payload["prompt"])
        self.assertEqual(project, original)

    def test_timed_camera_detail_is_not_removed_when_its_words_belong_to_another_cut(self):
        action = "Camera: static. 角色A停下。 [Shot 2] At 00:02.000, Camera: pan right."
        for detail in ("0–2s: Camera: pan right.", "0–5s: Camera: static."):
            with self.subTest(detail=detail):
                project, scene, shot = self.project_with_shot(action, duration=5)
                shot["camera"] = "custom"
                shot["camera_detail"] = detail
                payload, _ = self.compiled(project, scene, shot)
                self.assertIn(detail, payload["prompt"])
                self.assertIn(action, payload["prompt"])

    def test_nested_cuts_without_opening_camera_keep_opening_only_user_selection(self):
        action = "角色A打開門。 [Shot 2] At 00:05.000, 切到窗戶，Camera: pan right; shot size: wide."
        project, scene, shot = self.project_with_shot(action)
        shot["camera"] = "push_in"
        shot["shot_size"] = "close"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn("Opening cut only, unless its action specifies otherwise: The camera pushes in", payload["prompt"])
        self.assertIn("Do not carry this movement into later cuts", payload["prompt"])
        self.assertIn("Opening cut only, unless its action specifies framing: close-up", payload["prompt"])
        self.assertIn("each cut's own framing and camera direction", payload["prompt"])
        self.assertIn(action, payload["prompt"])

    def test_packed_opening_camera_and_framing_override_outer_selections(self):
        action = "Camera: custom; shot size: wide. 25mm廣角，固定機位。 [Shot 2] At 00:05.000, Camera: pan right; shot size: medium."
        project, scene, shot = self.project_with_shot(action)
        shot["camera"] = "push_in"
        shot["shot_size"] = "close"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn(action, payload["prompt"])
        self.assertNotIn("The camera pushes in", payload["prompt"])
        self.assertNotIn("Opening cut only, unless its action specifies framing", payload["prompt"])

    def test_wide_smile_and_pushing_chair_are_actions_not_camera_directions(self):
        action = "角色A gives a wide smile and pushes in a chair. [Shot 2] At 00:05.000, she walks away."
        project, scene, shot = self.project_with_shot(action)
        shot["camera"] = "pan_right"
        shot["shot_size"] = "close"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn("Opening cut only, unless its action specifies otherwise: The camera pans right", payload["prompt"])
        self.assertIn("Opening cut only, unless its action specifies framing: close-up", payload["prompt"])
        self.assertIn(action, payload["prompt"])

    def test_chinese_actor_movement_does_not_replace_opening_camera_selection(self):
        action = "角色A把椅子推入房間，再把桌子推近門口，環繞桌子走了一圈。 [Shot 2] At 00:05.000, 角色A停下。"
        project, scene, shot = self.project_with_shot(action)
        shot["camera"] = "pan_right"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn("Opening cut only, unless its action specifies otherwise: The camera pans right", payload["prompt"])
        self.assertIn(action, payload["prompt"])

    def test_chinese_explicit_camera_movement_still_overrides_opening_fallback(self):
        for direction in ("攝影機推近角色A臉部", "運鏡：環繞角色A", "固定機位，跟拍角色A"):
            with self.subTest(direction=direction):
                action = f"{direction}，角色A坐下。 [Shot 2] At 00:05.000, 角色A站起。"
                project, scene, shot = self.project_with_shot(action)
                shot["camera"] = "pan_right"
                payload, _ = self.compiled(project, scene, shot)
                self.assertNotIn("The camera pans right", payload["prompt"])
                self.assertIn(action, payload["prompt"])

    def test_camera_words_inside_dialogue_do_not_remove_independent_camera_direction(self):
        action = "角色A說：<d>[English] Keep the doorway visible.</d> [Shot 2] At 00:03.000, 角色A站起。"
        project, scene, shot = self.project_with_shot(action)
        shot["camera_detail"] = "Keep the doorway visible."
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn(shot["camera_detail"], payload["prompt"])
        self.assertIn("<d>[English] Keep the doorway visible.</d>", payload["prompt"])
        self.assertEqual(payload["prompt"].count("Keep the doorway visible."), 2)

    def test_shot_marker_in_dialogue_does_not_disable_single_cut_defaults(self):
        project, scene, shot = self.project_with_shot("角色A說：<d>[English] Read [Shot 2] aloud.</d>")
        self.add_asset(project, shot)
        shot["shot_size"] = "close"
        shot["camera"] = "push_in"
        payload, compiled = self.compiled(project, scene, shot)
        self.assertIn("Opening framing: close-up", payload["prompt"])
        self.assertIn("The camera pushes in", compiled.prompt)
        self.assertIn("<d>[English] Read [Shot 2] aloud.</d>", compiled.prompt)
        self.assertIn("single shot", payload["shortfilm_summary"])

    def test_visible_sign_text_is_not_used_to_deduplicate_camera_direction(self):
        project, scene, shot = self.project_with_shot('A doorway sign reads "Pan left." while the actor walks toward it.')
        shot["camera"] = "custom"
        shot["camera_detail"] = "Pan left."
        payload, _ = self.compiled(project, scene, shot)
        self.assertEqual(payload["prompt"].count("Pan left."), 2)
        self.assertIn('sign reads "Pan left."', payload["prompt"])

    def test_camera_direction_word_does_not_match_inside_another_word(self):
        project, scene, shot = self.project_with_shot("A companion waits at the door.")
        shot["camera"] = "custom"
        shot["camera_detail"] = "Pan"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn(". Pan ", payload["prompt"])

    def test_shot_marker_in_visible_caption_does_not_disable_single_cut_defaults(self):
        project, scene, shot = self.project_with_shot('The doorway sign reads "[Shot 2]"; the actor stops beside it.')
        shot["shot_size"] = "close"
        shot["camera"] = "push_in"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn("Opening framing: close-up", payload["prompt"])
        self.assertIn("The camera pushes in", payload["prompt"])
        self.assertIn('sign reads "[Shot 2]"', payload["prompt"])

    def test_single_cut_keeps_selected_camera_and_unique_original_detail(self):
        project, scene, shot = self.project_with_shot()
        shot["shot_size"] = "wide"
        shot["camera"] = "tracking"
        shot["camera_detail"] = "35mm低機位，沿門框向右跟拍。"
        payload, _ = self.compiled(project, scene, shot)
        self.assertIn("Opening framing: wide establishing shot", payload["prompt"])
        self.assertIn("smooth Tracking Shot", payload["prompt"])
        self.assertIn(shot["camera_detail"], payload["prompt"])

    def test_quantity_and_turnaround_rules_do_not_change_quick_replace_or_mg_modes(self):
        base = {"mode": "r2v", "prompt": "角色走到門口。", "duration": 5, "references": [
            {"alias": "角色", "type": "character", "image_asset_ids": [IMAGE_A]},
        ]}
        cases = [base, {**base, "mode": "replace", "prompt_profile": "shortfilm", "references": [
            {"alias": "角色", "type": "character", "image_asset_ids": [IMAGE_A], "video_asset_id": VIDEO_A},
        ]}, {**base, "mode": "mg_animation", "references": [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "轉輪帶", "type": "object", "image_asset_ids": [IMAGE_B]},
            {"alias": "角色", "type": "character", "image_asset_ids": [IMAGE_C]},
        ]}]
        for payload in cases:
            with self.subTest(mode=payload["mode"]):
                compiled = compile_request(payload)
                self.assertNotIn("turnaround/reference sheet", compiled.prompt)
                self.assertNotIn("same physical individual across views and cuts", compiled.prompt)
                self.assertNotIn("explicitly stated quantity", compiled.prompt)
                if payload["mode"] == "replace":
                    self.assertIn("禁止保留或混入原角色的臉", compiled.prompt)
                if payload["mode"] == "mg_animation":
                    self.assertIn("visible reel window", compiled.prompt)


if __name__ == "__main__":
    unittest.main()
