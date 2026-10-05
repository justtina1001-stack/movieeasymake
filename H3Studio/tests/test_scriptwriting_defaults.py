"""Detailed screenplay defaults survive the real short-film import boundary."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web

from domain import compile_request
from scriptwriting import (
    DEFAULT_SETTINGS, ScriptwritingError, ScriptwritingManager, build_messages,
    fit_context_settings, normalize_project, validate_context, validate_storyboard,
)
from shortfilm import ShortFilmStore, compile_shot_payload


LINES = ["哇……", "嗚！？", "對、對不起啊——！", "等等等等——！", "這些……都給你唷。", "欸——！？"]
TIMED_ACTION = (
    "From 0.0 to 2.0 seconds, an 18mm low-angle wide shot tracks 金猴爺 from behind through tropical leaves. "
    "He parts the foliage; golden volumetric sunlight illuminates falling droplets. "
    "[Shot 2] At 00:02.000, cut to a 24mm rear three-quarter shot. 金猴爺 stops at the lower left; "
    "古代遺跡 stands fifteen metres ahead in the centre. Tilt up to reveal its blue portal. "
    "金猴爺 (S1) quietly says <d>[Chinese] 哇……</d>. "
    "[Shot 3] At 00:04.000, a 35mm side tracking shot follows 金猴爺 tiptoeing toward the entrance; "
    "blue light reflects on his red and gold clothing. "
    "[Shot 4] At 00:05.500, a 50mm frontal medium close-up shows 金猴爺 freezing as leaves rustle. "
    "神祕女子 stands half a step behind him; only the edge of her feather headdress is visible. "
    "Reduce the jungle ambience briefly for 0.2 seconds. "
    "[Shot 5] At 00:07.000, a 28mm side shot shows both subjects less than half a metre apart. "
    "金猴爺 abruptly turns into 神祕女子 in a brief comic collision, without a chest close-up. "
    "Use slight camera shake, a faint air ring and two tiny stars. 金猴爺 (S1) says <d>[Chinese] 嗚！？</d>. "
    "[Shot 6] At 00:08.500, a 50mm slightly high-angle close-up follows 金猴爺 retreating, blushing and waving both hands. "
    "Two white cartoon steam wisps appear. 金猴爺 (S1) says <d>[Chinese] 對、對不起啊——！</d>. "
    "[Shot 7] At 00:10.000, a 24mm full-body rear-side shot follows 金猴爺 starting to run toward frame right. "
    "神祕女子 gently embraces his upper body from behind and lifts him; his feet kick twice. "
    "Arc fifteen degrees as the feather headdress trails soft blue-violet light. "
    "金猴爺 (S1) says <d>[Chinese] 等等等等——！</d>. "
    "[Shot 8] At 00:12.500, a 65mm two-person medium close-up from thirty degrees in front shows "
    "神祕女子 whispering at 金猴爺's ear while he blushes. "
    "神祕女子 (S2) slowly says <d>[Chinese] 這些……都給你唷。</d>. "
    "The blue portal spins behind them; warm sunset rim light and violet particles remain consistent. "
    "Freeze 金猴爺's astonished expression for the last 0.3 seconds; "
    "金猴爺 (S1) says <d>[Chinese] 欸——！？</d> and cut to black at 15.0 seconds."
)


def detailed_storyboard():
    return {
        "title": "叢林遺跡的相遇", "synopsis": "金猴爺探險時與神祕女子意外相遇。",
        "characters": [
            {"name": "金猴爺", "description": "A small golden monkey with a red and gold costume.",
             "type": "character", "reference": "圖一"},
            {"name": "神祕女子", "description": "A composed woman wearing a feather headdress.",
             "type": "character", "reference": "圖二"},
            {"name": "古代遺跡", "description": "A golden stone entrance with a glowing blue portal.",
             "type": "background", "reference": "圖三"},
        ],
        "scenes": [{
            "title": "叢林遺跡", "location": "Tropical jungle and 古代遺跡", "time_of_day": "Late afternoon",
            "description": "Maintain the entrance fifteen metres ahead and the established warm jungle lighting.",
            "shots": [{
                "title": "0.0–15.0s｜八段敘事分鏡", "duration": 15,
                "action": TIMED_ACTION, "dialogue": "", "speaker": "",
                "sound": "Continuous insects and birds, rustling leaves, footsteps, cloth friction, "
                         "a low portal energy hum and synchronized physical effects.",
                "camera": "custom", "shot_size": "wide",
                "camera_detail": "Follow the focal length, viewpoint and spatial relationships specified in each timed beat.",
                "ending": "Hold the final astonished expression for 0.3 seconds, then cut to black at 15.0 seconds.",
            }],
        }],
    }


def ui_example_project():
    """Use the shipped example instructions, including every prescribed beat."""
    source = (Path(__file__).resolve().parents[1] / "static" / "scripts.js").read_text(encoding="utf-8")
    defaults = re.search(r'^const SCRIPT_DEFAULT_INSTRUCTIONS = ("[^\n]*");', source, re.MULTILINE)
    example = re.search(r'^const SCRIPT_MONKEY_EXAMPLE_INSTRUCTIONS = `([\s\S]*?)`;', source, re.MULTILINE)
    return normalize_project({
        "title": "金猴爺・叢林遺跡奇遇", "genre": "動畫", "duration": 15,
        "idea": "金猴爺深入熱帶叢林，發現古代遺跡，遇到神祕女子。以八段鏡頭呈現探險、驚嚇、意外碰撞與害羞收尾。",
        "characters": "金猴爺＝圖一；神祕女子＝圖二；古代遺跡＝圖三",
        "instructions": json.loads(defaults[1]) + "\n\n" + example[1],
    })


def example_manuscript(project):
    beats = [line for line in project["instructions"].splitlines() if re.match(r"^[1-8]\. ", line)]
    manuscript = "片名：金猴爺・叢林遺跡奇遇\n全片零BGM。固定參考：" + project["characters"] + "\n\n"
    for beat in beats:
        manuscript += beat + "\n- 連續性：維持角色身份、方向、位置、服裝、光源與參考圖對應。\n\n"
    return manuscript


class DetailedScriptDefaultsTests(unittest.TestCase):
    def test_new_project_defaults_and_explicit_values_are_preserved(self):
        project = normalize_project({})
        self.assertEqual(project["duration"], 15)
        self.assertTrue(project["style"])
        self.assertTrue(project["instructions"])
        self.assertIn("BGM", project["instructions"].upper())
        edited = normalize_project({
            "title": "夜間訪客", "duration": 60, "style": "黑白電影",
            "characters": "只有阿青", "instructions": "使用單一固定鏡頭。", "content": "現有手寫稿。",
        })
        updated = normalize_project({"title": "新的片名"}, edited)
        for key in ("duration", "style", "characters", "instructions", "content"):
            self.assertEqual(updated[key], edited[key])
        self.assertEqual(updated["title"], "新的片名")

    def test_default_prompts_fit_current_context_and_do_not_duplicate_requirements(self):
        project = normalize_project({
            "title": "叢林探險", "idea": "金猴爺深入叢林，發現古代遺跡並遇到神祕女子。",
            "characters": "金猴爺＝圖一；神祕女子＝圖二；古代遺跡＝圖三。",
        })
        for action in ("outline", "screenplay", "rewrite", "storyboard"):
            with self.subTest(action=action):
                messages = build_messages(project, action, project["instructions"])
                validate_context(messages, DEFAULT_SETTINGS)
                json_body, remainder = messages[1]["content"].split("\n", 1)
                self.assertEqual(json.loads(json_body)["instructions"], project["instructions"])
                self.assertNotIn(project["instructions"], remainder)
        project["content"] = json.dumps(detailed_storyboard(), ensure_ascii=False)
        messages = build_messages(project, "storyboard", project["instructions"])
        original_messages = copy.deepcopy(messages)
        original_settings = copy.deepcopy(DEFAULT_SETTINGS)
        fitted = fit_context_settings(messages, DEFAULT_SETTINGS)
        validate_context(messages, fitted)
        self.assertEqual(messages, original_messages)
        self.assertEqual(DEFAULT_SETTINGS, original_settings)
        self.assertEqual({key: value for key, value in fitted.items() if key != "num_predict"},
                         {key: value for key, value in original_settings.items() if key != "num_predict"})
        self.assertGreaterEqual(fitted["num_predict"], 1024)
        self.assertLess(fitted["num_predict"], original_settings["num_predict"])
        # The output ceiling uses all remaining room; the complete input is kept.
        with self.assertRaises(ScriptwritingError):
            validate_context(messages, {**fitted, "num_predict": fitted["num_predict"] + 1})
        body = json.loads(messages[1]["content"].split("\n", 1)[0])
        self.assertEqual(body["existing_script"], project["content"])
        self.assertEqual(json.loads(body["existing_script"]), detailed_storyboard())
        for key in ("title", "idea", "genre", "duration", "characters", "style", "instructions"):
            self.assertEqual(body[key], project[key])
        project["content"] = "我自己的原稿：女子低語「這些……都給你唷。」"
        self.assertIn(project["content"], build_messages(project, "rewrite", "保留原台詞")[1]["content"])

    def test_optional_metadata_and_timeline_survive_repeated_validation(self):
        original = detailed_storyboard()
        parsed = validate_storyboard(original)
        self.assertEqual(parsed, original)
        self.assertEqual(validate_storyboard(parsed), parsed)
        self.assertEqual(original, detailed_storyboard())

    def test_timeline_cuts_must_be_sequential_and_inside_the_clip(self):
        invalid_actions = [
            TIMED_ACTION.replace("[Shot 3]", "[Shot 4]", 1),
            TIMED_ACTION.replace("00:04.000", "00:02.000", 1),
            TIMED_ACTION.replace("00:04.000", "00:01.000", 1),
            TIMED_ACTION.replace("00:12.500", "00:15.000", 1),
            TIMED_ACTION.replace("00:12.500", "00:16.000", 1),
        ]
        for action in invalid_actions:
            storyboard = detailed_storyboard()
            storyboard["scenes"][0]["shots"][0]["action"] = action
            with self.subTest(action=action[-120:]), self.assertRaises(ScriptwritingError):
                validate_storyboard(storyboard)

    def test_background_cannot_become_the_dialogue_speaker(self):
        storyboard = detailed_storyboard()
        storyboard["scenes"][0]["shots"][0].update(
            action="古代遺跡 glows while 金猴爺 watches.", speaker="古代遺跡", dialogue="這不是角色。",
        )
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(storyboard)
        storyboard["characters"][0]["type"] = "creature"
        storyboard["scenes"][0]["shots"][0].update(speaker="金猴爺", dialogue="哇……")
        self.assertEqual(validate_storyboard(storyboard)["characters"][0]["type"], "creature")

    def test_reference_roles_and_metadata_cannot_accept_unknown_types_or_truncate(self):
        for key, value in (("type", "scenery"), ("reference", ["圖一"] )):
            storyboard = detailed_storyboard()
            storyboard["characters"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ScriptwritingError):
                validate_storyboard(storyboard)
        for key, value in (("camera_detail", "x" * 801), ("ending", "x" * 1201)):
            storyboard = detailed_storyboard()
            storyboard["scenes"][0]["shots"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ScriptwritingError):
                validate_storyboard(storyboard)
        storyboard = detailed_storyboard()
        storyboard["scenes"][0]["description"] = "x" * 1801
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(storyboard)

    def test_inline_dialogue_cannot_be_duplicated_in_aggregate_fields(self):
        for aggregate in ({"dialogue": "哇……", "speaker": "金猴爺"},
                          {"dialogue": "", "speaker": "金猴爺"}):
            storyboard = detailed_storyboard()
            storyboard["scenes"][0]["shots"][0].update(aggregate)
            with self.subTest(aggregate=aggregate), self.assertRaises(ScriptwritingError):
                validate_storyboard(storyboard)


class ContextBudgetTests(unittest.TestCase):
    def test_fitting_short_prompt_copies_settings_without_reducing_user_limit(self):
        settings = dict(DEFAULT_SETTINGS)
        messages = [{"role": "user", "content": "短篇故事。"}]
        fitted = fit_context_settings(messages, settings)
        self.assertEqual(fitted, settings)
        self.assertIsNot(fitted, settings)

    def test_example_and_existing_full_script_keep_all_input_with_bounded_output(self):
        project = ui_example_project()
        prescribed_beats = [line for line in project["instructions"].splitlines()
                            if re.match(r"^[1-8]\. ", line)]
        self.assertEqual(len(prescribed_beats), 8)
        manuscript = example_manuscript(project)
        settings = dict(DEFAULT_SETTINGS)
        initial_settings = copy.deepcopy(settings)
        for content in ("", manuscript):
            project["content"] = content
            for action in ("screenplay", "rewrite", "storyboard"):
                with self.subTest(has_manuscript=bool(content), action=action):
                    messages = build_messages(project, action, project["instructions"])
                    before = copy.deepcopy(messages)
                    fitted = fit_context_settings(messages, settings)
                    self.assertEqual(messages, before)
                    self.assertEqual(settings, initial_settings)
                    self.assertEqual(fitted["num_ctx"], 8192)
                    self.assertGreaterEqual(fitted["num_predict"], 1024)
                    self.assertLessEqual(fitted["num_predict"], 4096)
                    validate_context(messages, fitted)
                    body = json.loads(messages[1]["content"].split("\n", 1)[0])
                    self.assertEqual(body["instructions"], project["instructions"])
                    if content:
                        self.assertEqual(body["existing_script"], manuscript)
                        self.assertLess(fitted["num_predict"], 4096)
                    for key in ("base_url", "model", "temperature"):
                        self.assertEqual(fitted[key], settings[key])

    def test_insufficient_output_room_and_overlong_input_still_fail(self):
        for content in ("中" * 4500, "中" * 8000):
            settings = dict(DEFAULT_SETTINGS)
            with self.subTest(length=len(content)), self.assertRaises(ScriptwritingError):
                fit_context_settings([{"role": "user", "content": content}], settings)
            self.assertEqual(settings, DEFAULT_SETTINGS)
        settings = {**DEFAULT_SETTINGS, "num_predict": 512}
        with self.assertRaises(ScriptwritingError):
            fit_context_settings([{"role": "user", "content": "中" * 4800}], settings)
        self.assertEqual(settings["num_predict"], 512)


class ContextBudgetSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_persists_effective_budget_without_changing_settings_or_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ScriptwritingManager(web.Application(), root)
            original_settings = copy.deepcopy(manager.settings)
            raw = ui_example_project()
            raw["content"] = example_manuscript(raw)
            project = manager.create_project(raw)
            with patch.object(manager, "_run", new_callable=AsyncMock) as run:
                job = manager.submit({
                    "project_id": project["id"], "action": "storyboard", "instructions": project["instructions"],
                })
                await manager.tasks[job["id"]]
                run.assert_awaited_once()
                messages = run.await_args.args[1]
                effective = job["input"]["settings"]
                self.assertTrue(job["output_budget_adjusted"])
                self.assertEqual(effective["num_ctx"], 8192)
                self.assertGreaterEqual(effective["num_predict"], 1024)
                self.assertLess(effective["num_predict"], 4096)
                self.assertEqual(manager.settings, original_settings)
                self.assertEqual(job["input"]["project"], project)
                self.assertEqual(job["input"]["instructions"], project["instructions"])
                body = json.loads(messages[1]["content"].split("\n", 1)[0])
                self.assertEqual(body["existing_script"], project["content"])
                self.assertEqual(body["instructions"], project["instructions"])
                validate_context(messages, effective)
                restored = ScriptwritingManager(web.Application(), root).get_job(job["id"])
                self.assertTrue(restored["output_budget_adjusted"])
                self.assertEqual(restored["input"]["settings"], effective)
                self.assertEqual(restored["input"]["project"], project)
            await manager.close()


class DetailedScriptImportTests(unittest.TestCase):
    def import_fixture(self, root: Path, storyboard=None):
        app = web.Application()
        store = ShortFilmStore(root / "shortfilms")
        app["shortfilms"] = store
        manager = ScriptwritingManager(app, root)
        project = manager.create_project({"title": "叢林探險", "idea": "叢林探險短片。"})
        job_id = "a" * 32
        manager.jobs[job_id] = {
            "id": job_id, "status": "completed", "action": "storyboard",
            "storyboard": copy.deepcopy(storyboard or detailed_storyboard()),
            "input": {"project": project}, "shortfilm_id": None,
        }
        imported = manager.import_shortfilm(job_id)
        return manager, store, job_id, imported["project"]

    def test_existing_saved_manuscript_keeps_its_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _, _, _ = self.import_fixture(root)
            saved = manager.create_project({
                "title": "既有專案", "duration": 60, "style": "", "instructions": "", "content": "自己的原稿。",
            })
            reloaded = ScriptwritingManager(web.Application(), root).get_project(saved["id"])
            self.assertEqual(reloaded["duration"], 60)
            self.assertEqual(reloaded["style"], "")
            self.assertEqual(reloaded["instructions"], "")
            self.assertEqual(reloaded["content"], "自己的原稿。")

    def test_import_store_and_both_compilers_preserve_eight_beats_and_zero_bgm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, store, job_id, project = self.import_fixture(root)
            self.assertEqual(project["target_duration"], 15)
            self.assertEqual(len(project["scenes"]), 1)
            scene = project["scenes"][0]
            self.assertEqual(len(scene["shots"]), 1)
            shot = scene["shots"][0]
            self.assertEqual(shot["duration"], 15)
            self.assertEqual(shot["action"], TIMED_ACTION)
            self.assertEqual(shot["camera_detail"], detailed_storyboard()["scenes"][0]["shots"][0]["camera_detail"])
            self.assertEqual(shot["ending"], detailed_storyboard()["scenes"][0]["shots"][0]["ending"])
            self.assertEqual(scene["description"], detailed_storyboard()["scenes"][0]["description"])
            self.assertEqual(shot["music"], "N/A")
            for asset, expected in zip(project["assets"], detailed_storyboard()["characters"]):
                self.assertEqual(asset["type"], expected["type"])
                self.assertIn(expected["reference"], asset["description"])
                self.assertEqual(asset["image_asset_ids"], [])

            again = manager.import_shortfilm(job_id)
            self.assertTrue(again["existing"])
            self.assertEqual(again["project"], project)
            self.assertEqual(len(store.list()), 1)
            restored = ShortFilmStore(root / "shortfilms").get(project["id"])
            self.assertEqual(restored["scenes"][0]["shots"][0]["action"], TIMED_ACTION)

            for with_images in (False, True):
                candidate = copy.deepcopy(restored)
                if with_images:
                    for index, asset in enumerate(candidate["assets"], 1):
                        asset["image_asset_ids"] = [f"{index:032x}"]
                payload, _ = compile_shot_payload(candidate, scene["id"], shot["id"])
                compiled = compile_request(payload)
                with self.subTest(mode=payload["mode"]):
                    self.assertEqual(payload["mode"], "r2v" if with_images else "t2v")
                    self.assertEqual(payload["duration"], 15)
                    self.assertEqual(payload["shortfilm_music"], "N/A")
                    self.assertEqual(re.findall(r"<d>\[Chinese\] (.*?)</d>", compiled.prompt), LINES)
                    self.assertEqual(compiled.prompt.count("[Shot 1]"), 1)
                    for marker in ("[Shot 2] At 00:02.000", "[Shot 4] At 00:05.500", "[Shot 8] At 00:12.500"):
                        self.assertIn(marker, compiled.prompt)
                    for focal_length in ("18mm", "24mm", "35mm", "50mm", "28mm", "65mm"):
                        self.assertIn(focal_length, compiled.prompt)
                    self.assertIn("fifteen metres", compiled.prompt)
                    self.assertIn("half a metre", compiled.prompt)
                    self.assertIn("two tiny stars", compiled.prompt)
                    self.assertIn("last 0.3 seconds", compiled.prompt)
                    self.assertRegex(compiled.prompt, r"non_diegetic_music:\s*N/A\s*$")
                    self.assertIn("Continuous insects and birds", compiled.prompt)
                    self.assertIn("(S1)", compiled.prompt)
                    self.assertIn("(S2)", compiled.prompt)
                    if with_images:
                        self.assertIn("<Subject 1>（金猴爺） (S1)", compiled.prompt)
                        self.assertIn("<Subject 2>（神祕女子） (S2)", compiled.prompt)
                        self.assertIn("<Picture 3>（古代遺跡）", compiled.prompt)
                        self.assertEqual(len(payload["references"]), 3)

    def test_import_removes_duplicate_first_shot_marker(self):
        storyboard = detailed_storyboard()
        storyboard["scenes"][0]["shots"][0]["action"] = "[Shot 1] " + TIMED_ACTION
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, project = self.import_fixture(Path(directory), storyboard)
            scene = project["scenes"][0]
            shot = scene["shots"][0]
            payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
            self.assertEqual(payload["prompt"].count("[Shot 1]"), 1)

    def test_supported_metadata_lengths_survive_store_without_truncation(self):
        storyboard = detailed_storyboard()
        raw_scene = storyboard["scenes"][0]
        raw_shot = raw_scene["shots"][0]
        raw_scene["description"] = "s" * 1790 + "SCENE-END!"
        raw_shot["camera_detail"] = "c" * 789 + "CAMERA-END!"
        raw_shot["ending"] = "e" * 1189 + "ENDING-END!"
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, project = self.import_fixture(Path(directory), storyboard)
            scene = project["scenes"][0]
            shot = scene["shots"][0]
            self.assertEqual(scene["description"], raw_scene["description"])
            self.assertEqual(shot["camera_detail"], raw_shot["camera_detail"])
            self.assertEqual(shot["ending"], raw_shot["ending"])


if __name__ == "__main__":
    unittest.main()
