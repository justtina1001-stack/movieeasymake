"""Regressions for model-format and creative-intent losses found in the H3 audit."""

import copy
import re
import unittest

from domain import RequestError, compile_request


BASE_FIELDS = ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
REF_FIELDS = ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]


def request(mode="t2v", **extra):
    return {"mode": mode, "prompt": "[Shot 1] Alex opens the door and settles beside it.", "duration": 5, "motion_profile": "none", **extra}


def character(alias="Alex", **extra):
    return {"alias": alias, "type": "character", "image_asset_ids": ["appearance"], **extra}


class OfficialPromptTests(unittest.TestCase):
    def test_all_base_profiles_have_three_sections_and_correct_frame_alignment(self):
        cases = [("t2v", {}, "T2VA"), ("fl2va", {"first_image_asset_id": "first"}, "I2VA"),
                 ("fl2va", {"last_image_asset_id": "last"}, "L2VA"),
                 ("fl2va", {"first_image_asset_id": "first", "last_image_asset_id": "last"}, "FL2VA"),
                 ("extend", {"first_image_asset_id": "first"}, "I2VA"),
                 ("symbol_loop", {"first_image_asset_id": "first"}, "FL2VA")]
        for profile in ("", "shortfilm"):
            for mode, extra, official in cases:
                with self.subTest(profile=profile, mode=mode, official=official):
                    c = compile_request(request(mode, prompt_profile=profile, **extra))
                    self.assertEqual(re.findall(r"^([a-z_]+):", c.prompt, re.M), BASE_FIELDS)
                    self.assertEqual(c.official_mode, official)
                    if official == "I2VA":
                        self.assertTrue(c.prompt.startswith("For the target video, at 0.00 seconds"))
                    if official in {"FL2VA", "L2VA"}:
                        self.assertIn("5.17-second mark", c.prompt)
                    if official != "T2VA":
                        self.assertIn("\n\nintegrated_multimodal_description:", c.prompt)

    def test_reference_modes_emit_exact_six_section_order(self):
        cases = [request("r2v", references=[character()]),
                 request("replace", references=[character(video_asset_id="video")]),
                 request("popup_panel", references=[{"alias": "背景圖", "type": "background", "image_asset_ids": ["bg"]}, character("面板", type="object")]),
                 request("mg_animation", references=[{"alias": "背景圖", "type": "background", "image_asset_ids": ["bg"]}, character("轉輪帶", type="object"), character("角色")])]
        for payload in cases:
            with self.subTest(mode=payload["mode"]):
                c = compile_request(payload)
                self.assertEqual(re.findall(r"^([a-z_]+):", c.prompt, re.M), REF_FIELDS)
                self.assertEqual(c.official_mode, "Ref2VA")
                self.assertRegex(c.prompt, r"summary:\n\[[a-z +]+\] ")
                detail = c.prompt.split("detailed_description:\n", 1)[1]
                self.assertFalse(detail.startswith("[Shot 1]"))
                self.assertIn("[Shot 1]", detail)

    def test_shortfilm_retains_background_style_motion_effect_descriptions(self):
        for role in ("background", "style", "motion", "effect"):
            c = compile_request(request("r2v", prompt_profile="shortfilm", references=[
                {"alias": "Setting", "type": role, "image_asset_ids": ["image"], "description": "UNIQUE_SIX_RED_COLUMNS"}]))
            self.assertIn("UNIQUE_SIX_RED_COLUMNS", c.prompt)
            self.assertIn('<Subject 1> is the ', c.prompt)
            self.assertIn('<Subject 1>: fully_preserved', c.prompt)
            self.assertNotIn("subject_definitions:\nN/A", c.prompt)

    def test_shortfilm_preserves_audio_reuse_and_video_soundtrack(self):
        c = compile_request(request("r2v", prompt_profile="shortfilm", references=[character(
            video_asset_id="video", video_use_audio=True, audio_asset_id="voice", voice_mode="reuse")]))
        self.assertIn("audio reuse + audio reference]", c.prompt)
        self.assertIn("<Audio 1>: reference", c.prompt)
        self.assertIn("<Audio 2>: partially_copy", c.prompt)
        self.assertIn("preserving spoken words and language", c.prompt)
        self.assertIn("<Audio 2> is reused", c.prompt)
        self.assertNotIn("<Audio 2>: reference", c.prompt)

    def test_mixed_video_soundtrack_flags_do_not_renumber_the_wrong_video(self):
        c = compile_request(request("r2v", references=[character(video_asset_ids=["silent", "audible"], video_use_audio=True, video_audio_flags=[False, True], audio_asset_id="voice")]))
        self.assertEqual(c.reference_video_use_audio, [False, True])
        self.assertIn("<Audio 1> is the enabled synchronized audio track from <Video 2>", c.prompt)
        self.assertEqual(c.mapping[0]["audio_tag"], "<Audio 2>")

    def test_edit_summary_and_source_identification_remain_distinct(self):
        c = compile_request(request("replace", references=[character(video_asset_id="original")], replacement_source_image_asset_ids=["old-face"]))
        self.assertIn("[video editing + reference generation] The target video is an edited version of <Video 1>.", c.prompt)
        self.assertIn("<Picture 2>: weak_reference only to identify", c.prompt)
        self.assertNotIn("<Picture 2>: fully_preserved", c.prompt)

    def test_overlapping_names_sign_text_speech_and_existing_tags_are_preserved(self):
        prompt = '[Shot 1] Alex walks alongside Al. Alex says: "Alex and Al!" A sign reads "Alex". <Subject 9> stays visible.'
        c = compile_request(request("r2v", prompt=prompt, references=[character("Alex"), character("Al")]))
        self.assertIn("<Subject 1>（Alex） walks alongside <Subject 2>（Al）", c.prompt)
        self.assertIn("<d>[English] Alex and Al!</d>", c.prompt)
        self.assertIn('A sign reads "Alex"', c.prompt)
        self.assertIn("<Subject 9> stays visible", c.prompt)
        self.assertNotIn("（<Subject", c.prompt)

    def test_named_speakers_keep_ids_across_main_text_and_storyboards(self):
        c = compile_request(request("r2v", prompt='Alex says: "Hello." Al says: "Hi."', references=[character("Alex"), character("Al")], storyboards=[{"duration": 5, "dialogue": 'Alex says: "Goodbye."'}]))
        self.assertIn("<Subject 1>（Alex） (S1) says", c.prompt)
        self.assertIn("<Subject 2>（Al） (S2) says", c.prompt)
        self.assertEqual(c.prompt.count("(S1) says"), 2)

    def test_user_speaker_id_is_respected_even_if_it_first_occurs_later(self):
        c = compile_request(request("r2v", prompt='Alex says: "Hello." Alex (S4) says: "Goodbye."', references=[character("Alex")]))
        self.assertEqual(c.prompt.count("(S4) says"), 2)
        self.assertNotIn("(S1)", c.prompt)
        with self.assertRaises(RequestError):
            compile_request(request("r2v", prompt='Alex (S1) says: "Hello." Alex (S2) says: "Goodbye."', references=[character("Alex")]))

    def test_frame_alignment_uses_actual_last_shot(self):
        c = compile_request(request("fl2va", first_image_asset_id="first", last_image_asset_id="last", prompt="[Shot 1] Opening. [Shot 2] At 00:02.000, The door closes."))
        self.assertIn("Picture 2 (from Shot 2) aligns with the 5.17-second mark", c.prompt)

    def test_storyboards_use_official_cut_times_including_shortfilm_base(self):
        for profile in ("", "shortfilm"):
            c = compile_request(request(prompt_profile=profile, storyboards=[{"duration": 2, "description": "Door opens."}, {"duration": 3, "description": "Alex exits."}]))
            self.assertIn("[Shot 1]", c.prompt)
            self.assertIn("[Shot 2] At 00:02.000,", c.prompt)
            self.assertIn("Alex exits.", c.prompt)
            self.assertNotIn("[Shot 1,", c.prompt)

    def test_legacy_storyboard_marker_normalizes_without_touching_speech(self):
        c = compile_request(request(prompt='[Shot 1, 0.0s-2.0s] Door opens. [Shot 2, 2.0s-5.0s] Alex says <d>[English] Read [Shot 8] aloud.</d>'))
        self.assertIn("[Shot 2] At 00:02.000,", c.prompt)
        self.assertIn("<d>[English] Read [Shot 8] aloud.</d>", c.prompt)

    def test_invalid_cuts_are_rejected_before_render(self):
        for prompt in ("[Shot 1] A. [Shot 2] At 00:09.000, B.",
                       "[Shot 1] A. [Shot 2] At 00:04.000, B. [Shot 3] At 00:03.000, C.",
                       "[Shot 1] A. [Shot 2] At 00:02.000, B. [Shot 2] At 00:03.000, C.",
                       "[Shot 1] A. [Shot 3] At 00:02.000, B.", "[Shot 1] A. [Shot 2] B."):
            with self.subTest(prompt=prompt), self.assertRaises(RequestError):
                compile_request(request(prompt=prompt))

    def test_loop_rejects_cuts_and_does_not_inject_tracking_camera(self):
        with self.assertRaises(RequestError):
            compile_request(request("symbol_loop", first_image_asset_id="symbol", prompt="[Shot 1] A. [Shot 2] At 00:02.000, B."))
        c = compile_request(request("symbol_loop", first_image_asset_id="symbol", motion_profile="natural", camera_response="follow"))
        self.assertIn("The camera is completely locked", c.prompt)
        self.assertNotIn("camera follows", c.prompt)

    def test_chinese_body_warns_once_but_named_aliases_and_speech_do_not(self):
        c = compile_request(request(prompt="角色走進神殿。"))
        self.assertTrue(c.warnings)
        c = compile_request(request("r2v", prompt='少年 says: "你好。"', references=[character("少年")]))
        self.assertFalse(c.warnings)
        c = compile_request(request(prompt="中文方向。", h3_preparation_warnings=["Translation unavailable"]))
        self.assertEqual(c.warnings, ["Translation unavailable"])

    def test_payload_is_unchanged_and_recompiling_does_not_nest_base_schema(self):
        payload = request(prompt='[Shot 1] Alex says: "Hello."')
        original = copy.deepcopy(payload)
        first = compile_request(payload)
        second = compile_request({**payload, "prompt": first.prompt})
        self.assertEqual(payload, original)
        self.assertEqual(second.prompt.count("integrated_multimodal_description:"), 1)
        self.assertEqual(second.prompt.count("<d>"), 1)

    def test_no_music_and_explicit_complete_silence_use_exact_na(self):
        c = compile_request(request(prompt="[Shot 1] Complete silence throughout the video. The door opens."))
        self.assertIn("overall_soundscape: N/A", c.prompt)
        self.assertTrue(c.prompt.endswith("non_diegetic_music: N/A"))

    def test_explicit_narrative_background_music_is_not_overridden_by_default_na(self):
        c = compile_request(request(prompt="[Shot 1] Alex opens a door. Background music uses slow solo piano with soft cello."))
        self.assertIn("non_diegetic_music: Background music uses slow solo piano with soft cello.", c.prompt)
        c = compile_request(request(prompt='[Shot 1] Alex says: "Background music uses piano." A radio plays music.'))
        self.assertTrue(c.prompt.endswith("non_diegetic_music: N/A"))
        c = compile_request(request(prompt="[Shot 1] Alex walks. Background music uses piano with no vocals or swell."))
        self.assertIn("non_diegetic_music: Background music uses piano with no vocals or swell.", c.prompt)

    def test_no_bgm_remains_na_and_explicit_music_field_takes_precedence(self):
        c = compile_request(request(prompt="[Shot 1] Alex walks. No background music, only footsteps."))
        self.assertTrue(c.prompt.endswith("non_diegetic_music: N/A"))
        c = compile_request(request(prompt="[Shot 1] Background music uses piano.", non_diegetic_music="N/A"))
        self.assertTrue(c.prompt.endswith("non_diegetic_music: N/A"))


if __name__ == "__main__":
    unittest.main()
