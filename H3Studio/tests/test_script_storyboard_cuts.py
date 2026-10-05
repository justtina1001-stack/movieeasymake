"""Narrative cut packing preserves time, speech, references and compiler behaviour."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from PIL import Image

from domain import compile_request
from script_storyboard_cuts import StoryboardPackingError, pack_storyboard_cuts
from scriptwriting import ScriptwritingError, ScriptwritingManager, normalize_project, parse_storyboard, validate_storyboard
from shortfilm import ShortFilmStore, compile_shot_payload


def storyboard(durations=(2, 2, 2.5, 2.5, 3, 3)):
    return {
        "title": "回家的路", "synopsis": "阿青與小白在月台會合，一起搭車回家。",
        "characters": [
            {"name": "阿青", "type": "character", "reference": "圖一", "description": "藍色外套的成年旅人。"},
            {"name": "月台", "type": "background", "reference": "圖二", "description": "有雨棚的車站月台。"},
            {"name": "小白", "type": "creature", "reference": "圖三", "description": "白色成年狗。"},
        ],
        "scenes": [{
            "title": "月台會合", "location": "月台", "time_of_day": "日",
            "description": "阿青在左側，小白在右側；列車停靠於後方。",
            "shots": [{
                "title": f"敘事切鏡 {index}", "duration": duration,
                "action": f"阿青與小白完成第 {index} 個可見動作。",
                "dialogue": "", "speaker": "", "sound": f"第 {index} 次腳步聲。",
                "camera": "tracking" if index % 2 else "static",
                "camera_detail": f"{24 + index}mm，攝影機從第 {index} 個位置跟拍。",
                "shot_size": "wide" if index % 2 else "close",
                "ending": f"第 {index} 個動作收勢並維持相對位置。",
            } for index, duration in enumerate(durations, 1)],
        }],
    }


class StoryboardCutPackingTests(unittest.TestCase):
    def pack(self, value, target=None):
        return pack_storyboard_cuts(value, target_duration=target, validator=validate_storyboard)

    def import_fixture(self, root, original, reference_types):
        app = web.Application()
        store = ShortFilmStore(root / "shortfilms")
        app["shortfilms"] = store
        paths, references = {}, []
        for index, (name, asset_type) in enumerate(reference_types.items(), 1):
            image_id = f"{100 + index:032x}"
            path = root / f"reference-{index}.png"
            image = Image.new("RGB", (2, 2), (index * 40, 80, 120))
            image.save(path)
            image.close()
            paths[image_id] = path
            references.append({
                "id": f"{index:032x}", "name": name, "type": asset_type,
                "image_asset_id": image_id, "image_name": path.name, "appearance": f"固定外觀：{name}。",
            })
        app["assets"] = SimpleNamespace(path_for=lambda image_id: paths[image_id])
        manager = ScriptwritingManager(app, root)
        content = json.dumps(original, ensure_ascii=False)
        duration = sum(shot["duration"] for scene in original["scenes"] for shot in scene["shots"])
        project = normalize_project({"title": "回家的路", "duration": duration, "idea": "搭车回家。",
                                     "character_references": references})
        manager.jobs["fixture"] = {
            "id": "fixture", "status": "completed", "action": "storyboard", "content": content,
            "storyboard": parse_storyboard(content, target_duration=duration),
            "input": {"project": project}, "shortfilm_id": None,
        }
        return manager, store, paths

    def test_six_narrative_cuts_become_one_fifteen_second_clip_without_content_loss(self):
        original = storyboard()
        snapshot = copy.deepcopy(original)
        packed = self.pack(original, 15)
        self.assertEqual(original, snapshot)
        self.assertEqual(packed["characters"], original["characters"])
        self.assertEqual({key: value for key, value in packed["scenes"][0].items() if key != "shots"},
                         {key: value for key, value in original["scenes"][0].items() if key != "shots"})
        self.assertEqual(len(packed["scenes"][0]["shots"]), 1)
        clip = packed["scenes"][0]["shots"][0]
        self.assertEqual(clip["duration"], 15)
        self.assertEqual(re.findall(r"\[Shot (\d+)\] At (\d+:\d+\.\d+)", clip["action"]), [
            ("2", "00:02.000"), ("3", "00:04.000"), ("4", "00:06.500"),
            ("5", "00:09.000"), ("6", "00:12.000"),
        ])
        for cut in original["scenes"][0]["shots"]:
            for field in ("title", "action", "camera", "shot_size", "camera_detail", "sound"):
                with self.subTest(cut=cut["title"], field=field):
                    self.assertIn(cut[field], clip["action"])
            self.assertIn(cut["ending"], clip["action"] + clip.get("ending", ""))
            self.assertIn(cut["camera_detail"], clip["camera_detail"])
            self.assertIn(cut["sound"], clip["sound"])
        metadata = packed["storyboard_normalization"]
        self.assertEqual(metadata["original_shot_count"], 6)
        self.assertEqual(metadata["generation_clip_count"], 1)
        self.assertEqual(metadata["clip_durations"], [15])
        self.assertEqual(metadata["source_cuts"][0]["cuts"], original["scenes"][0]["shots"])
        metadata["source_cuts"][0]["cuts"][0]["action"] = "僅修改來源副本。"
        self.assertEqual(original, snapshot)

    def test_two_speakers_and_existing_inline_dialogue_keep_correct_ids_and_each_line_once(self):
        original = storyboard((2, 2, 2, 3, 3, 3))
        shots = original["scenes"][0]["shots"]
        shots[0].update(dialogue="我們回家吧。", speaker="阿青")
        shots[1].update(dialogue="汪！", speaker="小白")
        inline = '阿青 (S1) says <d>[Chinese] 列車到了。</d>.'
        shots[2]["action"] += " " + inline
        packed = self.pack(original, 15)
        clip = packed["scenes"][0]["shots"][0]
        self.assertEqual((clip["dialogue"], clip["speaker"]), ("", ""))
        self.assertIn("阿青 (S1) says <d>[Chinese] 我們回家吧。</d>", clip["action"])
        self.assertIn("小白 (S2) says <d>[Chinese] 汪！</d>", clip["action"])
        self.assertIn(inline, clip["action"])
        for line in ("我們回家吧。", "汪！", "列車到了。"):
            self.assertEqual(clip["action"].count(line), 1)

    def test_existing_tagged_dialogue_is_preserved_and_ambiguous_tags_are_rejected(self):
        original = storyboard((2, 3))
        line = "<d>[English] Let's go home.</d>"
        original["scenes"][0]["shots"][0].update(dialogue=line, speaker="阿青")
        self.assertIn(line, self.pack(original, 5)["scenes"][0]["shots"][0]["action"])
        original["scenes"][0]["shots"][0]["dialogue"] = line + " 尚未標記的台詞"
        with self.assertRaises(StoryboardPackingError):
            self.pack(original, 5)

    def test_speaker_without_dialogue_remains_identifiable(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][0]["speaker"] = "阿青"
        self.assertIn("Speaker: 阿青.", self.pack(original, 5)["scenes"][0]["shots"][0]["action"])

    def test_legal_clips_are_unchanged_and_have_no_normalization_metadata(self):
        original = storyboard((5, 10, 15))
        original["scenes"][0]["shots"][0]["action"] += " [Shot 2] At 00:02.000, 阿青停下腳步。"
        packed = self.pack(original, 30)
        self.assertEqual(packed, validate_storyboard(original, target_duration=30))
        self.assertNotIn("storyboard_normalization", packed)

    def test_partition_avoids_short_tail_and_does_not_touch_other_scenes(self):
        original = storyboard((7, 7, 2))
        other_scene = copy.deepcopy(storyboard((5, 10))["scenes"][0])
        other_scene["title"] = "候車室"
        original["scenes"].append(other_scene)
        packed = self.pack(original, 31)
        self.assertEqual([shot["duration"] for shot in packed["scenes"][0]["shots"]], [7, 9])
        self.assertEqual(packed["scenes"][0]["shots"][0], original["scenes"][0]["shots"][0])
        self.assertEqual(packed["scenes"][1], other_scene)
        self.assertIn("[Shot 2] At 00:07.000", packed["scenes"][0]["shots"][1]["action"])

    def test_legal_nested_clip_is_preserved_while_adjacent_short_cuts_pack(self):
        original = storyboard((7, 3, 2))
        nested_clip = original["scenes"][0]["shots"][0]
        nested_clip["action"] += " [Shot 2] At 00:03.000, 阿青轉頭看見小白。"
        packed = self.pack(original, 12)
        self.assertEqual([shot["duration"] for shot in packed["scenes"][0]["shots"]], [7, 5])
        self.assertEqual(packed["scenes"][0]["shots"][0], nested_clip)
        self.assertIn("[Shot 2] At 00:03.000", packed["scenes"][0]["shots"][1]["action"])

    def test_unrepresentable_boundaries_and_short_scenes_are_rejected_without_padding(self):
        for durations in ((14, 2), (1, 1, 1), (4,)):
            with self.subTest(durations=durations), self.assertRaises(StoryboardPackingError):
                self.pack(storyboard(durations), sum(durations))
        original = storyboard((2, 2))
        original["scenes"].append(copy.deepcopy(storyboard((6,))["scenes"][0]))
        with self.assertRaises(StoryboardPackingError):
            self.pack(original, 10)

    def test_source_schema_and_target_errors_are_not_hidden_by_packing(self):
        invalids = []
        for key, value in (("duration", 0), ("duration", True), ("duration", 16),
                           ("duration", float("nan")), ("camera", "invalid"),
                           ("shot_size", "invalid"), ("action", ""), ("speaker", "不存在")):
            original = storyboard((2, 3))
            original["scenes"][0]["shots"][0][key] = value
            invalids.append(original)
        for original in invalids:
            with self.subTest(shot=original["scenes"][0]["shots"][0]), self.assertRaises(ScriptwritingError):
                self.pack(original, 5)
        with self.assertRaises(ScriptwritingError):
            self.pack(storyboard(), 30)

    def test_nested_cuts_needing_merge_and_submillisecond_boundaries_are_rejected(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][0]["action"] += " [Shot 2] At 00:01.000, 阿青轉身。"
        with self.assertRaises(StoryboardPackingError):
            self.pack(original, 5)
        original["scenes"][0]["shots"][0]["action"] = "[Shot 2] At 00:03.000, 阿青轉身。"
        with self.assertRaises(ScriptwritingError):
            self.pack(original, 5)
        for durations in ((0.0001, 0.0001, 5), (0.5, 0.0001, 5), (5, 0.0001)):
            with self.subTest(durations=durations), self.assertRaises(StoryboardPackingError):
                self.pack(storyboard(durations), sum(durations))

    def test_packing_rejects_overflow_instead_of_truncating_source_text(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][0]["action"] = "甲" * 3000
        original["scenes"][0]["shots"][1]["action"] = "乙" * 3000
        with self.assertRaises(ScriptwritingError):
            self.pack(original, 5)
        original = storyboard((2, 3))
        for shot in original["scenes"][0]["shots"]:
            shot["camera_detail"] = "鏡" * 450
        with self.assertRaises(ScriptwritingError):
            self.pack(original, 5)

    def test_reference_character_creature_swaps_keep_inline_speaker_ids_and_images(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][0].update(dialogue="我們回家吧。", speaker="阿青")
        original["scenes"][0]["shots"][1].update(dialogue="汪！", speaker="小白")
        with tempfile.TemporaryDirectory() as directory:
            manager, _, paths = self.import_fixture(Path(directory), original, {"阿青": "creature", "小白": "character"})
            job_snapshot = copy.deepcopy(manager.jobs["fixture"])
            image_bytes = {key: path.read_bytes() for key, path in paths.items()}
            imported = manager.import_shortfilm("fixture")["project"]
            assets = {item["alias"]: item for item in imported["assets"]}
            self.assertEqual(assets["阿青"]["type"], "creature")
            self.assertEqual(assets["小白"]["type"], "character")
            self.assertEqual(assets["阿青"]["image_asset_ids"], [f"{101:032x}"])
            self.assertEqual(assets["小白"]["image_asset_ids"], [f"{102:032x}"])
            action = imported["scenes"][0]["shots"][0]["action"]
            self.assertIn("阿青 (S1) says <d>[Chinese] 我們回家吧。</d>", action)
            self.assertIn("小白 (S2) says <d>[Chinese] 汪！</d>", action)
            self.assertEqual(manager.jobs["fixture"]["content"], job_snapshot["content"])
            self.assertEqual(manager.jobs["fixture"]["input"], job_snapshot["input"])
            self.assertEqual({key: path.read_bytes() for key, path in paths.items()}, image_bytes)

    def test_reference_cannot_turn_packed_speaker_into_background(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][0].update(dialogue="我們回家吧。", speaker="阿青")
        with tempfile.TemporaryDirectory() as directory:
            manager, store, _ = self.import_fixture(Path(directory), original, {"阿青": "background"})
            snapshot = copy.deepcopy(manager.jobs["fixture"])
            with self.assertRaisesRegex(ScriptwritingError, "說話角色.*阿青.*場景或物件"):
                manager.import_shortfilm("fixture")
            self.assertEqual(store.list(), [])
            self.assertEqual(manager.jobs["fixture"], snapshot)

    def test_promoting_background_before_an_inline_speaker_cannot_shift_speaker_number(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][1].update(dialogue="汪！", speaker="小白")
        with tempfile.TemporaryDirectory() as directory:
            manager, store, _ = self.import_fixture(Path(directory), original, {"月台": "character"})
            snapshot = copy.deepcopy(manager.jobs["fixture"])
            with self.assertRaisesRegex(ScriptwritingError, "對白編號 S2.*小白.*月台"):
                manager.import_shortfilm("fixture")
            self.assertEqual(store.list(), [])
            self.assertEqual(manager.jobs["fixture"], snapshot)

    def test_existing_inline_dialogue_is_also_checked_when_reference_types_shift_ids(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][1]["action"] += " 小白 (S2) says <d>[Chinese] 汪！</d>."
        with tempfile.TemporaryDirectory() as directory:
            manager, store, _ = self.import_fixture(Path(directory), original, {"月台": "creature"})
            with self.assertRaisesRegex(ScriptwritingError, "對白編號 S2"):
                manager.import_shortfilm("fixture")
            self.assertEqual(store.list(), [])

    def test_reference_background_to_object_change_does_not_shift_inline_speaker_ids(self):
        original = storyboard((2, 3))
        original["scenes"][0]["shots"][1].update(dialogue="汪！", speaker="小白")
        with tempfile.TemporaryDirectory() as directory:
            manager, _, _ = self.import_fixture(Path(directory), original, {"月台": "object"})
            imported = manager.import_shortfilm("fixture")["project"]
            self.assertEqual(next(item for item in imported["assets"] if item["alias"] == "月台")["type"], "object")
            self.assertIn("小白 (S2) says <d>[Chinese] 汪！</d>", imported["scenes"][0]["shots"][0]["action"])

    def test_spoken_text_that_looks_like_a_speaker_id_is_not_reinterpreted(self):
        original = storyboard((2, 3))
        line = "請看車廂標示 (S2)。"
        original["scenes"][0]["shots"][0].update(dialogue=line, speaker="阿青")
        with tempfile.TemporaryDirectory() as directory:
            manager, _, _ = self.import_fixture(Path(directory), original, {"月台": "character"})
            imported = manager.import_shortfilm("fixture")["project"]
            self.assertIn(f"阿青 (S1) says <d>[Chinese] {line}</d>", imported["scenes"][0]["shots"][0]["action"])

    def test_legal_unpacked_dialogue_fields_keep_existing_reference_binding_behaviour(self):
        original = storyboard((5, 10))
        original["scenes"][0]["shots"][1].update(dialogue="汪！", speaker="小白")
        with tempfile.TemporaryDirectory() as directory:
            manager, _, _ = self.import_fixture(Path(directory), original, {"月台": "character"})
            self.assertNotIn("storyboard_normalization", manager.jobs["fixture"]["storyboard"])
            imported = manager.import_shortfilm("fixture")["project"]
            scene, clip = imported["scenes"][0], imported["scenes"][0]["shots"][1]
            payload, _ = compile_shot_payload(imported, scene["id"], clip["id"])
            self.assertIn("小白 (S3) says <d>[Chinese] 汪！</d>", payload["prompt"])

    def test_parse_and_import_compile_fifteen_seconds_with_all_references_and_speech(self):
        original = storyboard()
        original["scenes"][0]["shots"][0].update(dialogue="我們回家吧。", speaker="阿青")
        original["scenes"][0]["shots"][1].update(dialogue="汪！", speaker="小白")
        content = json.dumps(original, ensure_ascii=False)
        packed = parse_storyboard(content, target_duration=15)
        self.assertEqual(json.loads(content), original)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = web.Application()
            store = ShortFilmStore(root / "shortfilms")
            app["shortfilms"] = store
            manager = ScriptwritingManager(app, root)
            project = normalize_project({"title": "回家的路", "duration": 15, "idea": "搭車回家。"})
            manager.jobs["fixture"] = {
                "id": "fixture", "status": "completed", "action": "storyboard", "content": content,
                "storyboard": packed, "input": {"project": project}, "shortfilm_id": None,
            }
            imported = manager.import_shortfilm("fixture")["project"]
            self.assertEqual(imported["target_duration"], 15)
            self.assertEqual(len(imported["scenes"][0]["shots"]), 1)
            self.assertEqual(manager.jobs["fixture"]["content"], content)
            for asset, reference in zip(imported["assets"], original["characters"]):
                self.assertEqual(asset["alias"], reference["name"])
                self.assertIn(reference["reference"], asset["description"])
            scene, clip = imported["scenes"][0], imported["scenes"][0]["shots"][0]
            for with_images in (False, True):
                candidate = copy.deepcopy(imported)
                if with_images:
                    for index, asset in enumerate(candidate["assets"], 1):
                        asset["image_asset_ids"] = [f"{index:032x}"]
                with self.subTest(with_images=with_images):
                    payload, _ = compile_shot_payload(candidate, scene["id"], clip["id"])
                    self.assertEqual(payload["mode"], "r2v" if with_images else "t2v")
                    self.assertEqual(payload["duration"], 15)
                    self.assertEqual(payload["shortfilm_music"], "N/A")
                    compiled = compile_request(payload)
                    self.assertEqual(compiled.requested_duration, 15)
                    for line in ("我們回家吧。", "汪！"):
                        self.assertEqual(compiled.prompt.count(line), 1)
                    self.assertEqual(compiled.prompt.count("[Shot 1]"), 1)
                    self.assertIn("(S1)", compiled.prompt)
                    self.assertIn("(S2)", compiled.prompt)
                    self.assertRegex(compiled.prompt, r"non_diegetic_music:\s*N/A\s*$")
                    for reference in original["characters"]:
                        self.assertIn(reference["name"], payload["prompt"])
                    if with_images:
                        self.assertEqual(len(payload["references"]), 3)
                        self.assertEqual([item["image_asset_ids"] for item in payload["references"]],
                                         [[f"{index:032x}"] for index in range(1, 4)])
