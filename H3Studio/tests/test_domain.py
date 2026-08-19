import unittest

from domain import RequestError, build_workflow, compile_request


IMAGE_A = "a" * 32
IMAGE_B = "b" * 32
AUDIO_A = "c" * 32
VIDEO_A = "d" * 32
IMAGE_C = "e" * 32
IMAGE_D = "f" * 32


class CompileRequestTests(unittest.TestCase):
    def base(self, mode="t2v"):
        return {
            "mode": mode,
            "prompt": "小明走進神殿。",
            "aspect_ratio": "16:9",
            "megapixels": 0.4,
            "duration": 5,
            "seed": 42,
            "steps": 20,
        }

    def test_t2v_uses_official_preview_dimensions_and_frame_grid(self):
        compiled = compile_request(self.base())
        self.assertEqual((compiled.width, compiled.height), (864, 480))
        self.assertEqual(compiled.length, 124)
        self.assertAlmostEqual(compiled.actual_duration, 5.167, places=3)
        workflow = build_workflow(compiled, {}, "2026-08-06_09-10-11_123456")
        save = next(value for value in workflow.values() if value["class_type"] == "SaveVideo")
        self.assertEqual(save["inputs"]["filename_prefix"], "H3Studio/2026-08-06_09-10-11_123456")

    def test_turbo_preview_uses_8_step_fl2v_profile_at_preview_resolution(self):
        payload = self.base()
        payload["quality_mode"] = "turbo"
        payload["steps"] = 30
        payload["scheduler"] = "karras"
        compiled = compile_request(payload)
        self.assertEqual(compiled.steps, 8)
        self.assertEqual(compiled.scheduler, "simple")
        self.assertEqual(compiled.sampler_name, "euler")
        self.assertEqual((compiled.shift_video, compiled.shift_audio), (12.0, 3.0))
        workflow = build_workflow(compiled, {}, "turbo-preview")
        lora = next(value for value in workflow.values() if value["class_type"] == "LoraLoaderModelOnly")
        shift = next(value for value in workflow.values() if value["class_type"] == "MiniMaxH3SigmaShift")
        sampler = next(value for value in workflow.values() if value["class_type"] == "KSamplerSelect")
        scheduler = next(value for value in workflow.values() if value["class_type"] == "BasicScheduler")
        self.assertIn("8step", lora["inputs"]["lora_name"])
        self.assertEqual(lora["inputs"]["strength_model"], 0.75)
        self.assertEqual(shift["inputs"]["shift_video"], 12.0)
        self.assertEqual(sampler["inputs"]["sampler_name"], "euler")
        self.assertEqual(scheduler["inputs"]["steps"], 8)

    def test_turbo_preview_uses_4_step_768p_profile_for_exact_native_landscape(self):
        payload = self.base()
        payload.update({"quality_mode": "turbo", "megapixels": 0.98})
        compiled = compile_request(payload)
        self.assertEqual((compiled.width, compiled.height), (1344, 768))
        self.assertEqual(compiled.steps, 4)
        self.assertEqual(compiled.shift_video, 6.0)
        self.assertIn("768p", compiled.turbo_lora)

    def test_turbo_reference_profile_forces_match_and_4_steps(self):
        payload = self.base("r2v")
        payload.update({
            "quality_mode": "turbo",
            "ref_image_size": "max",
            "references": [{"alias": "小明", "type": "character", "image_asset_ids": [IMAGE_A]}],
        })
        compiled = compile_request(payload)
        self.assertEqual(compiled.steps, 4)
        self.assertEqual(compiled.ref_image_size, "match")
        self.assertIn("ref2v", compiled.turbo_lora)
        workflow = build_workflow(compiled, {IMAGE_A: "h3/a.png"}, "turbo-ref")
        self.assertTrue(any(value["class_type"] == "MiniMaxH3SigmaShift" for value in workflow.values()))

    def test_native_quality_does_not_add_turbo_nodes(self):
        compiled = compile_request(self.base())
        workflow = build_workflow(compiled, {}, "native")
        self.assertFalse(any(value["class_type"] in {"LoraLoaderModelOnly", "MiniMaxH3SigmaShift"} for value in workflow.values()))

    def test_fl2va_requires_a_keyframe(self):
        with self.assertRaisesRegex(RequestError, "至少需要一張"):
            compile_request(self.base("fl2va"))

    def test_extend_uses_last_frame_as_first_frame_and_keeps_source(self):
        payload = self.base("extend")
        payload.update({
            "first_image_asset_id": IMAGE_A,
            "continuation_source_job_id": "job-before",
            "continuation_merge": True,
            "continuation_audio": "both",
        })
        compiled = compile_request(payload)
        self.assertEqual(compiled.first_image, IMAGE_A)
        self.assertEqual(compiled.continuation_source_job, "job-before")
        self.assertTrue(compiled.continuation_merge)
        self.assertIn("上一段影片的最後一幀", compiled.prompt)
        workflow = build_workflow(compiled, {IMAGE_A: "h3/last.png"}, "job")
        node = next(value for value in workflow.values() if value["class_type"] == "MiniMaxH3ImageToVideo")
        self.assertIn("first_frame", node["inputs"])
        save = next(value for value in workflow.values() if value["class_type"] == "SaveVideo")
        self.assertEqual(save["inputs"]["codec"], "auto")

    def test_extend_requires_prepared_last_frame(self):
        with self.assertRaisesRegex(RequestError, "擷取最後畫面"):
            compile_request(self.base("extend"))

    def test_replace_uses_one_new_subject_and_original_video_as_performance(self):
        payload = self.base("replace")
        payload["prompt"] = "妲己完整取代影片左側的狐妖。"
        payload["references"] = [{
            "alias": "妲己",
            "type": "character",
            "description": "黑色長髮與紅色古裝",
            "image_asset_ids": [IMAGE_A],
            "video_asset_id": VIDEO_A,
            "video_use_audio": True,
        }]
        compiled = compile_request(payload)
        self.assertEqual(compiled.mode, "replace")
        self.assertEqual(compiled.reference_images, [IMAGE_A])
        self.assertEqual(compiled.reference_videos, [VIDEO_A])
        self.assertIn("原始表演影片", compiled.prompt)
        self.assertIn("原角色的身份與外觀必須完全消失", compiled.prompt)
        self.assertNotIn("<Subject 2>", compiled.prompt)
        workflow = build_workflow(compiled, {IMAGE_A: "h3/new.png", VIDEO_A: "h3/original.mp4"}, "job")
        self.assertTrue(any(value["class_type"] == "MiniMaxH3ReferenceToVideo" for value in workflow.values()))
        model = next(value for value in workflow.values() if value["class_type"] == "UNETLoader")
        self.assertIn("ref2va", model["inputs"]["unet_name"])

    def test_replace_requires_new_character_image_and_performance_video(self):
        payload = self.base("replace")
        payload["references"] = [{"alias": "新角色", "type": "character", "image_asset_ids": [IMAGE_A]}]
        with self.assertRaisesRegex(RequestError, "原始表演影片"):
            compile_request(payload)
        payload["references"] = [{"alias": "新角色", "type": "character", "video_asset_id": VIDEO_A}]
        with self.assertRaisesRegex(RequestError, "新角色圖片"):
            compile_request(payload)

    def test_replace_batch_segment_adds_identity_and_timeline_continuity_rules(self):
        payload = self.base("replace")
        payload["references"] = [{
            "alias": "新角色",
            "type": "character",
            "image_asset_ids": [IMAGE_A],
            "video_asset_id": VIDEO_A,
        }]
        payload["replacement_batch_segment"] = {
            "index": 2,
            "total": 4,
            "source_start": 9.5,
            "source_end": 20.5,
        }
        compiled = compile_request(payload)
        self.assertIn("replacement segment 2 of 4", compiled.prompt)
        self.assertIn("9.500s–20.500s", compiled.prompt)
        self.assertIn("不得憑空加入 <Subject 1>", compiled.prompt)
        self.assertIn("not a keyframe", compiled.prompt)

    def test_symbol_loop_forces_same_prepared_image_at_both_ends(self):
        payload = self.base("symbol_loop")
        payload["first_image_asset_id"] = IMAGE_A
        payload["last_image_asset_id"] = IMAGE_B
        compiled = compile_request(payload)
        self.assertEqual(compiled.first_image, IMAGE_A)
        self.assertEqual(compiled.last_image, IMAGE_A)
        self.assertIn("精確起始畫面與精確結束畫面", compiled.prompt)
        workflow = build_workflow(compiled, {IMAGE_A: "h3/symbol.png"}, "job")
        node = next(value for value in workflow.values() if value["class_type"] == "MiniMaxH3ImageToVideo")
        self.assertEqual(node["inputs"]["first_frame"], node["inputs"]["last_frame"])

    def test_symbol_loop_requires_prepared_canvas(self):
        with self.assertRaisesRegex(RequestError, "完成自動擴邊"):
            compile_request(self.base("symbol_loop"))

    def test_popup_panel_uses_fixed_background_and_panel_references(self):
        payload = self.base("popup_panel")
        payload["prompt"] = "[0.0秒～0.5秒] 面板從中央彈出。背景圖保持不動。\n[4.5秒～5.0秒] 面板縮小消失。"
        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "面板", "type": "object", "image_asset_ids": [IMAGE_B, IMAGE_C]},
            {"alias": "分數", "type": "object", "image_asset_ids": [IMAGE_D]},
        ]
        compiled = compile_request(payload)
        self.assertEqual(compiled.mode, "popup_panel")
        self.assertEqual(compiled.reference_images, [IMAGE_A, IMAGE_B, IMAGE_C, IMAGE_D])
        self.assertIn("逐像素保持固定", compiled.prompt)
        self.assertIn("其餘已命名參考素材", compiled.prompt)
        self.assertIn("分數", compiled.prompt)
        self.assertIn("鏡頭必須完全鎖定", compiled.prompt)
        self.assertIn("0.0秒～0.5秒", compiled.prompt)
        workflow = build_workflow(compiled, {
            IMAGE_A: "h3/background.png",
            IMAGE_B: "h3/panel-main.png",
            IMAGE_C: "h3/panel-decoration.png",
            IMAGE_D: "h3/score.png",
        }, "popup")
        self.assertTrue(any(value["class_type"] == "MiniMaxH3ReferenceToVideo" for value in workflow.values()))
        model = next(value for value in workflow.values() if value["class_type"] == "UNETLoader")
        self.assertIn("ref2va", model["inputs"]["unet_name"])

    def test_popup_panel_requires_one_background_and_at_least_one_panel_image(self):
        payload = self.base("popup_panel")
        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "面板", "type": "object", "image_asset_ids": []},
        ]
        with self.assertRaisesRegex(RequestError, "面板.*至少一張圖片"):
            compile_request(payload)

        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A, IMAGE_B]},
            {"alias": "面板", "type": "object", "image_asset_ids": [IMAGE_C]},
        ]
        with self.assertRaisesRegex(RequestError, "背景圖.*剛好一張圖片"):
            compile_request(payload)

    def test_reference_aliases_compile_to_picture_and_audio_tags(self):
        payload = self.base("r2v")
        payload.update({
            "references": [{
                "alias": "小明",
                "type": "character",
                "description": "黑色短髮",
                "image_asset_ids": [IMAGE_A],
                "audio_asset_id": AUDIO_A,
                "voice_mode": "timbre",
            }],
            "storyboards": [{
                "duration": 2,
                "description": "小明抬頭看向鏡頭",
                "camera": "緩慢推進",
                "dialogue": "小明說：你好",
                "sound": "風聲",
                "image_asset_id": IMAGE_B,
            }],
        })
        compiled = compile_request(payload)
        self.assertEqual(compiled.reference_images, [IMAGE_A, IMAGE_B])
        self.assertEqual(compiled.reference_audios, [AUDIO_A])
        self.assertIn("<Subject 1>", compiled.prompt)
        self.assertIn("<Picture 1>", compiled.prompt)
        self.assertIn("<Picture 2>", compiled.prompt)
        self.assertIn("<Audio 1>", compiled.prompt)
        self.assertEqual(compiled.mapping[0]["alias"], "小明")
        self.assertIn("motion_direction:", compiled.prompt)

    def test_multimodal_continuation_uses_last_frame_as_picture_one(self):
        payload = self.base("r2v")
        payload.update({
            "first_image_asset_id": IMAGE_A,
            "references": [{
                "alias": "小美",
                "type": "character",
                "image_asset_ids": [IMAGE_B],
            }],
        })
        compiled = compile_request(payload)
        self.assertEqual(compiled.reference_images, [IMAGE_A, IMAGE_B])
        self.assertIn("<Picture 1> 是上一段動畫的最後一幀", compiled.prompt)
        self.assertEqual(compiled.mapping[0]["picture_tags"], ["<Picture 2>"])
        workflow = build_workflow(compiled, {IMAGE_A: "h3/last.png", IMAGE_B: "h3/hero.png"}, "job")
        node = next(value for value in workflow.values() if value["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertIn("ref_images.ref_image_0", node["inputs"])
        self.assertIn("ref_images.ref_image_1", node["inputs"])

    def test_multimodal_continuation_can_use_only_the_previous_last_frame(self):
        payload = self.base("r2v")
        payload["first_image_asset_id"] = IMAGE_A
        compiled = compile_request(payload)
        self.assertEqual(compiled.reference_images, [IMAGE_A])

    def test_reference_video_maps_motion_and_soundtrack_before_voice_audio(self):
        payload = self.base("r2v")
        payload["motion_profile"] = "impact"
        payload["references"] = [{
            "alias": "主角",
            "type": "character",
            "image_asset_ids": [IMAGE_A],
            "video_asset_id": VIDEO_A,
            "video_use_audio": True,
            "audio_asset_id": AUDIO_A,
        }]
        compiled = compile_request(payload)
        self.assertEqual(compiled.reference_videos, [VIDEO_A])
        self.assertEqual(compiled.reference_video_use_audio, [True])
        self.assertIn("<Video 1>", compiled.prompt)
        self.assertIn("<Audio 1> 是 <Video 1>", compiled.prompt)
        self.assertEqual(compiled.mapping[0]["audio_tag"], "<Audio 2>")
        self.assertIn("蓄力", compiled.prompt)

    def test_duplicate_alias_is_rejected(self):
        payload = self.base("r2v")
        payload["references"] = [
            {"alias": "主角", "type": "character", "image_asset_ids": [IMAGE_A]},
            {"alias": "主角", "type": "background", "image_asset_ids": [IMAGE_B]},
        ]
        with self.assertRaisesRegex(RequestError, "重複"):
            compile_request(payload)

    def test_storyboard_cannot_exceed_output_duration(self):
        payload = self.base()
        payload["storyboards"] = [{"duration": 4, "description": "A"}, {"duration": 4, "description": "B"}]
        with self.assertRaisesRegex(RequestError, "超過影片"):
            compile_request(payload)

    def test_r2v_workflow_uses_dynamic_reference_inputs(self):
        payload = self.base("r2v")
        payload["references"] = [{
            "alias": "主角",
            "type": "character",
            "image_asset_ids": [IMAGE_A],
            "video_asset_id": VIDEO_A,
            "video_use_audio": True,
            "audio_asset_id": AUDIO_A,
        }]
        compiled = compile_request(payload)
        workflow = build_workflow(compiled, {IMAGE_A: "h3/a.png", VIDEO_A: "h3/motion.mp4", AUDIO_A: "h3/c.wav"}, "job")
        node = next(value for value in workflow.values() if value["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertIn("ref_images.ref_image_0", node["inputs"])
        self.assertIn("ref_videos.ref_video_0", node["inputs"])
        self.assertIn("ref_video_audios.ref_video_audio_0", node["inputs"])
        self.assertIn("ref_audios.ref_audio_0", node["inputs"])
        self.assertTrue(any(value["class_type"] == "LoadVideo" for value in workflow.values()))
        self.assertTrue(any(value["class_type"] == "GetVideoComponents" for value in workflow.values()))
        self.assertTrue(any(value["class_type"] == "SaveVideo" for value in workflow.values()))

    def test_mg_animation_compiles_separate_character_reel_and_background_direction(self):
        payload = self.base("mg_animation")
        payload["prompt"] = "保持高品質老虎機 MG 畫面，最後停在可讀的中獎結果。"
        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "轉輪帶", "type": "object", "image_asset_ids": [IMAGE_B]},
            {"alias": "角色", "type": "character", "image_asset_ids": [IMAGE_C]},
        ]
        payload["mg_animation"] = {
            "character_position": "upper_left",
            "character_position_detail": "位於轉輪左上方",
            "character_motion": "角色先蓄力，再指向轉輪，最後開心收勢。",
            "reel_motion_model": "continuous",
            "reel_direction": "top_down",
            "reel_stop_order": "left_right",
            "reel_stop_stagger": 0.2,
            "reel_motion": "五軸依序減速並停穩。",
            "symbol_post_stop_motion": "中獎圖騰停穩後才放大與發光。",
            "background_motion_level": "subtle",
            "background_motion": "遠景環境光緩慢流動。",
            "camera_motion": "static",
        }
        compiled = compile_request(payload)
        self.assertEqual(compiled.reference_images, [IMAGE_A, IMAGE_B, IMAGE_C])
        self.assertIn("summary:", compiled.prompt)
        self.assertIn("retention_analysis:", compiled.prompt)
        self.assertIn("overall_soundscape:", compiled.prompt)
        self.assertIn("upper-left area", compiled.prompt)
        self.assertIn("from top to bottom", compiled.prompt)
        self.assertIn("left to right", compiled.prompt)
        self.assertIn("中獎圖騰停穩後才放大與發光", compiled.prompt)
        self.assertIn("never infer or alter mathematical reel-strip order", compiled.prompt)
        workflow = build_workflow(compiled, {
            IMAGE_A: "h3/background.png",
            IMAGE_B: "h3/reels.png",
            IMAGE_C: "h3/character.png",
        }, "mg")
        self.assertTrue(any(value["class_type"] == "MiniMaxH3ReferenceToVideo" for value in workflow.values()))
        model = next(value for value in workflow.values() if value["class_type"] == "UNETLoader")
        self.assertIn("ref2va", model["inputs"]["unet_name"])

    def test_mg_animation_requires_three_named_base_layers(self):
        payload = self.base("mg_animation")
        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "轉輪帶", "type": "object", "image_asset_ids": [IMAGE_B]},
        ]
        with self.assertRaisesRegex(RequestError, "背景圖.*轉輪帶.*角色"):
            compile_request(payload)

        payload["references"].append({"alias": "角色", "type": "character", "image_asset_ids": []})
        with self.assertRaisesRegex(RequestError, "角色.*至少一張"):
            compile_request(payload)


if __name__ == "__main__":
    unittest.main()
