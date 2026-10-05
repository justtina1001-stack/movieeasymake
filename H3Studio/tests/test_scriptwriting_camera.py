"""Lossless model-only field repairs keep strict imports and saved drafts safe."""
import copy
import json
import unittest
from unittest.mock import patch

from domain import compile_request
from shortfilm import CAMERAS, compile_shot_payload, flatten_shots
from scriptwriting import (
    DEFAULT_SETTINGS, ScriptwritingError, build_messages, normalize_project,
    parse_storyboard, validate_storyboard,
)
from tests import test_scriptwriting as scripts
from tests import test_script_video as video


CAMERA_ERROR = "分鏡 camera 必須使用支援的鏡頭運動代號。"


def draft(camera="25mm廣角，固定機位", detail=None):
    raw = copy.deepcopy(scripts.STORYBOARD)
    shot = raw["scenes"][0]["shots"][0]
    shot["camera"] = camera
    if detail is not None:
        shot["camera_detail"] = detail
    return raw


def short_camera_draft():
    raw = draft()
    source = raw["scenes"][0]["shots"][0]
    raw["scenes"][0]["shots"] = [
        {**copy.deepcopy(source), "title": f"切鏡{index + 1}", "duration": duration,
         "action": f"阿青依序走向第{index + 1}個位置。", "camera": f"{25 + index}mm，固定機位，從阿青背後拍攝",
         "camera_detail": f"運鏡細節{index + 1}", "dialogue": "無", "speaker": "無"}
        for index, duration in enumerate((2, 3, 3, 3, 4, 5, 5))
    ]
    return raw


class CameraParsingTests(unittest.TestCase):
    def test_supported_enums_remain_unchanged_and_public_validation_stays_strict(self):
        for camera in CAMERAS:
            with self.subTest(camera=camera):
                raw = draft(camera, "50mm側面，攝影機緩慢推近。")
                self.assertEqual(parse_storyboard(json.dumps(raw)), raw)
        with self.assertRaisesRegex(ScriptwritingError, "支援的鏡頭運動代號"):
            validate_storyboard(draft())

    def test_natural_chinese_and_english_camera_preserve_all_original_details(self):
        for camera in ("25mm廣角，固定機位", "50mm medium lens, locked camera behind the traveler"):
            with self.subTest(camera=camera):
                raw = draft(camera, "First frame: hands visible.\nFinal framing: medium close-up.")
                original = copy.deepcopy(raw)
                content = json.dumps(raw, ensure_ascii=False)
                parsed = parse_storyboard(content, target_duration=5)
                shot = parsed["scenes"][0]["shots"][0]
                detail = camera + "\n" + original["scenes"][0]["shots"][0]["camera_detail"]
                self.assertEqual(shot["camera"], "custom")
                self.assertEqual(shot["camera_detail"], detail)
                for field in ("action", "dialogue", "speaker", "sound", "duration", "shot_size"):
                    self.assertEqual(shot[field], original["scenes"][0]["shots"][0][field])
                metadata = parsed["storyboard_camera_normalization"]
                self.assertEqual(metadata["correction_count"], 1)
                self.assertEqual(metadata["corrections"], [{"scene_index": 0, "shot_index": 0,
                    "original_camera": camera, "original_camera_detail": original["scenes"][0]["shots"][0]["camera_detail"],
                    "camera": "custom", "camera_detail": detail}])
                self.assertEqual(raw, original)
                self.assertEqual(content, json.dumps(original, ensure_ascii=False))

    def test_edge_whitespace_is_preserved_and_combined_limit_never_truncates(self):
        camera, detail = " 25mm wide lens \n", " original viewpoint  "
        parsed = parse_storyboard(json.dumps(draft(camera, detail)))
        combined = parsed["scenes"][0]["shots"][0]["camera_detail"]
        self.assertIn(camera, combined)
        self.assertIn(detail, combined)
        exact = parse_storyboard(json.dumps(draft("鏡" * 800)))
        self.assertEqual(exact["scenes"][0]["shots"][0]["camera_detail"], "鏡" * 800)
        for raw in (draft("鏡" * 801), draft("鏡" * 799, "細"), draft(" 25mm ", "細" * 800)):
            with self.subTest(raw=raw), self.assertRaisesRegex(ScriptwritingError, "800"):
                parse_storyboard(json.dumps(raw))

    def test_invalid_camera_detail_and_schema_are_not_repaired(self):
        for camera in (None, True, 12, [], {}, "", " \n ", "\x00", "25mm\x00"):
            with self.subTest(camera=camera), self.assertRaises(ScriptwritingError):
                parse_storyboard(json.dumps(draft(camera)))
        for detail in (None, True, [], {}, "\x00"):
            raw = draft()
            raw["scenes"][0]["shots"][0]["camera_detail"] = detail
            with self.subTest(detail=detail), self.assertRaises(ScriptwritingError):
                parse_storyboard(json.dumps(raw))
        for raw in ([], {"scenes": None}, {"scenes": [[]]}, {"scenes": [{"shots": [None]}]}):
            with self.subTest(raw=raw), self.assertRaises(ScriptwritingError):
                parse_storyboard(json.dumps(raw))

    def test_silent_placeholder_pairs_are_precise_and_real_words_remain(self):
        for dialogue, speaker in (("無", "無"), ("", "無"), ("無", ""), ("N/A", "none")):
            raw = draft("static")
            raw["scenes"][0]["shots"][0].update(dialogue=dialogue, speaker=speaker)
            parsed = parse_storyboard(json.dumps(raw))
            self.assertEqual(parsed["scenes"][0]["shots"][0]["dialogue"], "")
            self.assertEqual(parsed["scenes"][0]["shots"][0]["speaker"], "")
            self.assertEqual(parsed["storyboard_dialogue_normalization"]["correction_count"], 1)
        for dialogue in ("無", "「無」", '"none"'):
            raw = draft("static")
            raw["scenes"][0]["shots"][0]["dialogue"] = dialogue
            self.assertEqual(parse_storyboard(json.dumps(raw)), raw)
        named = draft("static")
        named["characters"][0]["name"] = "無"
        named["scenes"][0]["shots"][0].update(speaker="無", dialogue="無")
        self.assertEqual(parse_storyboard(json.dumps(named)), named)
        blank = draft("static")
        blank["scenes"][0]["shots"][0].update(dialogue=" \n ", speaker=" ")
        self.assertNotIn("storyboard_dialogue_normalization", parse_storyboard(json.dumps(blank)))
        for dialogue, speaker in (("實際台詞", "無"), ("「無」", "無"), ("無", "未知角色"),
                                  (True, "無"), ("無", []), ("無" + " " * 1800, "無"), ("無", "無" + " " * 60)):
            raw = draft("static")
            raw["scenes"][0]["shots"][0].update(dialogue=dialogue, speaker=speaker)
            with self.subTest(dialogue=dialogue, speaker=speaker), self.assertRaises(ScriptwritingError):
                parse_storyboard(json.dumps(raw))

    def test_raw_fake_metadata_is_ignored_and_short_cuts_keep_all_camera_details(self):
        original = short_camera_draft()
        parsed = parse_storyboard(json.dumps(original), target_duration=25)
        self.assertEqual(parsed["storyboard_normalization"]["clip_durations"], [15, 10])
        self.assertEqual(parsed["storyboard_camera_normalization"]["correction_count"], 7)
        self.assertEqual(parsed["storyboard_dialogue_normalization"]["correction_count"], 7)
        clips = parsed["scenes"][0]["shots"]
        for cut in original["scenes"][0]["shots"]:
            self.assertTrue(any(cut["camera"] in clip["action"] and cut["camera_detail"] in clip["action"] for clip in clips))
        fake = draft("static")
        fake["storyboard_camera_normalization"] = {"correction_count": 1, "corrections": [{}]}
        fake["storyboard_dialogue_normalization"] = {"correction_count": 1, "corrections": [{}]}
        self.assertNotIn("storyboard_camera_normalization", parse_storyboard(json.dumps(fake)))
        self.assertNotIn("storyboard_dialogue_normalization", parse_storyboard(json.dumps(fake)))

    def test_storyboard_prompt_assigns_descriptions_to_camera_detail(self):
        prompt = build_messages(normalize_project({}), "storyboard", "")[0]["content"]
        self.assertIn("camera 只填支援代號", prompt)
        self.assertIn("焦段、機位和運鏡描述放 camera_detail，不放 camera", prompt)
        self.assertIn("dialogue與speaker都填空字串，不把「無」當作台詞或角色", prompt)
        self.assertIn("實際指定台詞仍逐字保留", prompt)


class CameraRecoveryTests(unittest.IsolatedAsyncioTestCase):
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
    start_pipeline = video.ScriptVideoAPITests.start_pipeline
    wait_pipeline = video.ScriptVideoAPITests.wait_pipeline

    async def failed_job(self, raw=None, duration=5):
        project = await self.project(duration=duration)
        content = json.dumps(raw if raw is not None else draft(), ensure_ascii=False)
        job = {"id": "1" * 32, "project_id": project["id"], "project_title": project["title"],
               "action": "storyboard", "status": "failed", "content": content, "error": CAMERA_ERROR,
               "done_reason": "stop", "truncated": False, "model_unloaded": True,
               "created_at": project["created_at"], "updated_at": project["updated_at"],
               "input": {"project": project, "settings": copy.deepcopy(self.manager.settings), "instructions": ""}}
        self.manager.jobs[job["id"]] = copy.deepcopy(job)
        self.manager._save_jobs(job["id"])
        return job

    async def test_complete_failed_camera_draft_recovers_without_model_or_content_change(self):
        failed = await self.failed_job()
        self.assertTrue(await self.manager.recover_storyboard(failed["id"]))
        recovered = self.manager.get_job(failed["id"])
        self.assertEqual(recovered["content"], failed["content"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["recovered_from_error"], CAMERA_ERROR)
        self.assertEqual(recovered["storyboard"]["storyboard_camera_normalization"]["correction_count"], 1)
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])
        archived = json.loads((self.manager.history_directory / f"{failed['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(archived, recovered)

    async def test_only_silent_pair_correction_can_recover_a_complete_failed_draft(self):
        raw = draft("static")
        raw["scenes"][0]["shots"][0].update(dialogue="無", speaker="無")
        failed = await self.failed_job(raw)
        self.assertTrue(await self.manager.recover_storyboard(failed["id"]))
        recovered = self.manager.get_job(failed["id"])
        self.assertNotIn("storyboard_camera_normalization", recovered["storyboard"])
        self.assertEqual(recovered["storyboard"]["storyboard_dialogue_normalization"]["correction_count"], 1)
        self.assertEqual(recovered["content"], failed["content"])

    async def test_incomplete_truncated_invalid_or_fake_repairs_never_recover(self):
        failed = await self.failed_job()
        fake = draft("static")
        fake["storyboard_camera_normalization"] = {"correction_count": 9, "corrections": [{}] * 9}
        invalid = draft()
        invalid["scenes"][0]["shots"][0]["shot_size"] = "invented"
        blank = draft("static")
        blank["scenes"][0]["shots"][0].update(dialogue=" \n ", speaker=" ")
        for changes in ({"truncated": True}, {"done_reason": "length"}, {"done_reason": None},
                        {"content": '{"title":"unfinished"'}, {"content": json.dumps(invalid)},
                        {"content": json.dumps(fake)}, {"content": json.dumps(blank)}, {"status": "cancelled"}):
            record = {**copy.deepcopy(failed), **changes}
            self.manager.jobs[failed["id"]] = record
            with self.subTest(changes=changes):
                self.assertFalse(await self.manager.recover_storyboard(failed["id"]))
                self.assertEqual(self.manager.get_job(failed["id"]), record)
        self.assertEqual(self.chat_requests, [])

    async def test_camera_recovery_save_failure_rolls_back_completion(self):
        failed = await self.failed_job()
        with patch.object(self.manager, "_save_jobs", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                await self.manager.recover_storyboard(failed["id"])
        self.assertEqual(self.manager.get_job(failed["id"]), failed)

    async def test_complete_25_second_draft_imports_and_compiles_without_inference(self):
        failed = await self.failed_job(short_camera_draft(), duration=25)
        self.assertTrue(await self.manager.recover_storyboard(failed["id"]))
        imported = self.manager.import_shortfilm(failed["id"])
        project = imported["project"]
        self.assertEqual([shot["duration"] for _, shot in flatten_shots(project)], [15, 10])
        for scene, shot in flatten_shots(project):
            payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
            compiled = compile_request(payload)
            self.assertEqual(compiled.mode, "r2v")
            self.assertIn("25mm" if shot["duration"] == 15 else "30mm", compiled.prompt)
            self.assertEqual(payload["shortfilm_music"], "N/A")
            self.assertNotIn("<d>[Chinese] 無</d>", compiled.prompt)
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.video_jobs.jobs, {})
        self.assertEqual(self.manager.get_job(failed["id"])["content"], failed["content"])

    async def test_resume_recovers_camera_failure_and_does_not_resubmit_llm(self):
        self.storyboard_reply = json.dumps(short_camera_draft(), ensure_ascii=False)
        self.screenplay_reply = "0–25 秒：阿青從月台走向列車。"
        project = await self.project(duration=25)
        with patch("scriptwriting.parse_storyboard", side_effect=ScriptwritingError(CAMERA_ERROR)):
            failed = await self.wait_pipeline(await self.start_pipeline(project))
        self.assertEqual(failed["status"], "failed")
        old_job = self.manager.get_job(failed["storyboard_job_id"])
        actions = list(self.chat_actions)
        response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        result = await self.wait_pipeline(await response.json())
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(self.chat_actions, actions)
        self.assertEqual(result["storyboard_job_id"], old_job["id"])
        self.assertEqual(self.manager.get_job(old_job["id"])["content"], old_job["content"])


if __name__ == "__main__":
    unittest.main()
