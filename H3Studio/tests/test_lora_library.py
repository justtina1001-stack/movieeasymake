"""Known video adapters remain opt-in and respect their input contracts."""
import copy
import unittest

from custom_loras import normalize_loras, validate_lora_requirements
from domain import RequestError, _with_lora_triggers, build_workflow, compile_request
from lora_catalog import (ANIMATION_FILE, CAMERA_FILE, COMBAT_FILE, ORBIT_FILE,
                          PHYSICS_FILE, REALISM_FILE, SWAP_FILE, WUSHU_FILE,
                          describe_lora, recommended_lora_items)


def name(filename):
    return "h3studio_custom/" + filename


def row(filename, **changes):
    return {"name": name(filename), "strength": 0.5, "family": "both", "enabled": True, **changes}


def ref_payload(*, video=False, **changes):
    reference = dict(alias="Alice", type="character", image_asset_ids=["a" * 32])
    if video:
        reference["video_asset_id"] = "b" * 32
    # This is the narrative field, so speech has an explicit cue rather than a
    # name-only label that could also describe a character's visual direction.
    return dict(mode="r2v", prompt='[Shot 1] Alice moves.\nAlice says: "hello."',
                references=[reference], prompt_profile="shortfilm", **changes)


class RecommendationCatalogTests(unittest.TestCase):
    def test_all_eight_choices_are_visible_without_inventing_installed_weights(self):
        original = [name(WUSHU_FILE).replace("/", "\\"), name(CAMERA_FILE), "other.safetensors"]
        before = copy.deepcopy(original)
        items = recommended_lora_items(original)
        self.assertEqual(original, before)
        self.assertEqual(len(items), 8)
        installed = [item for item in items if item["installed"]]
        self.assertEqual([item["name"] for item in installed], [name(WUSHU_FILE), name(CAMERA_FILE)])
        self.assertTrue(all(item["manual_selectable"] for item in installed))
        self.assertTrue(all(not item["manual_selectable"] for item in items if not item["installed"]))
        animation = next(item for item in items if item["name"] == name(ANIMATION_FILE))
        self.assertEqual(animation["availability"], "gated")
        self.assertIn("登入", animation["usage"])
        self.assertIsNone(animation["family"])
        self.assertEqual(describe_lora("other.safetensors")["known"], False)

    def test_an_authorized_manually_installed_gated_weight_can_be_selected(self):
        items = recommended_lora_items([name(ANIMATION_FILE)])
        animation = next(item for item in items if item["name"] == name(ANIMATION_FILE))
        self.assertTrue(animation["installed"])
        self.assertTrue(animation["manual_selectable"])
        self.assertEqual(animation["availability"], "installed")

    def test_author_settings_modes_and_unknown_families_remain_distinct(self):
        items = {item["name"]: item for item in recommended_lora_items([])}
        for filename in (CAMERA_FILE, PHYSICS_FILE):
            self.assertIsNone(items[name(filename)]["family"])
            self.assertIn("未明確區分", items[name(filename)]["limits"])
        self.assertEqual(items[name(CAMERA_FILE)]["default_strength"], 0.8)
        self.assertEqual(items[name(PHYSICS_FILE)]["default_strength"], 0.3)
        self.assertEqual(items[name(ORBIT_FILE)]["family"], "fl2va")
        self.assertEqual(items[name(SWAP_FILE)]["family"], "ref2va")
        self.assertTrue(items[name(SWAP_FILE)]["requires_source_video"])
        self.assertEqual(items[name(REALISM_FILE)]["family"], "both")

    def test_recommendations_are_detached_from_other_reads(self):
        first = recommended_lora_items([])
        first[0]["usage"] = "changed"
        self.assertNotEqual(recommended_lora_items([])[0]["usage"], "changed")
        self.assertNotEqual(describe_lora(name(WUSHU_FILE))["usage"], "changed")

    def test_public_entries_pin_available_files_not_model_main(self):
        for filename in (CAMERA_FILE, PHYSICS_FILE, ORBIT_FILE, SWAP_FILE, REALISM_FILE):
            with self.subTest(filename=filename):
                item = describe_lora(name(filename))
                self.assertEqual(len(item["revision"]), 40)
                self.assertEqual(len(item["sha256"]), 64)
                self.assertGreater(item["size"], 1_000_000)
                self.assertTrue(item["source_url"].startswith("https://huggingface.co/"))


class KnownWorkflowRequirementsTests(unittest.TestCase):
    def test_orbit_and_swap_correct_legacy_both_and_reject_wrong_explicit_family(self):
        for filename, expected, wrong in ((ORBIT_FILE, "fl2va", "ref2va"),
                                           (SWAP_FILE, "ref2va", "fl2va")):
            self.assertEqual(normalize_loras([row(filename)])[0]["family"], expected)
            value = row(filename)
            value["family"] = wrong
            with self.assertRaisesRegex(ValueError, "只適用"):
                normalize_loras([value])

    def test_orbit_is_rejected_in_automatic_reference_workflow(self):
        with self.assertRaisesRegex(RequestError, "不適用 Ref2VA"):
            compile_request(ref_payload(custom_loras=[row(ORBIT_FILE)]))

    def test_orbit_reaches_fl_sampler_without_autochanging_user_prompt(self):
        raw = dict(mode="fl2va", first_image_asset_id="a" * 32, last_image_asset_id="a" * 32,
                   prompt="One frozen instant. Only the camera moves in a continuous 360 orbit.",
                   custom_loras=[row(ORBIT_FILE)])
        before = copy.deepcopy(raw)
        compiled = compile_request(raw)
        workflow = build_workflow(compiled, {"a" * 32: "a.png"}, "test")
        self.assertEqual(raw, before)
        self.assertEqual([node["inputs"]["lora_name"] for node in workflow.values()
                          if node["class_type"] == "LoraLoaderModelOnly"], [name(ORBIT_FILE)])

    def test_swap_requires_a_source_video_and_preserves_inactive_choices(self):
        with self.assertRaisesRegex(RequestError, "需要來源影片"):
            compile_request(ref_payload(custom_loras=[row(SWAP_FILE)]))
        for change in ({"enabled": False}, {"strength": 0}):
            value = row(SWAP_FILE)
            value.update(change)
            compiled = compile_request(ref_payload(custom_loras=[value]))
            self.assertEqual(compiled.custom_loras[0]["name"], name(SWAP_FILE))

    def test_swap_with_source_video_reaches_ref_workflow(self):
        compiled = compile_request(ref_payload(video=True, custom_loras=[row(SWAP_FILE)]))
        self.assertEqual(compiled.reference_videos, ["b" * 32])
        workflow = build_workflow(compiled, {"a" * 32: "a.png", "b" * 32: "b.mp4"}, "test")
        self.assertIn(name(SWAP_FILE), [node["inputs"]["lora_name"] for node in workflow.values()
                                       if node["class_type"] == "LoraLoaderModelOnly"])

    def test_swap_rejects_video_only_or_background_only_reference(self):
        for reference in (dict(alias="Actor", type="character", video_asset_id="b" * 32),
                          dict(alias="Scene", type="background", image_asset_ids=["a" * 32], video_asset_id="b" * 32)):
            with self.subTest(reference=reference), self.assertRaisesRegex(RequestError, "需要新角色參考圖片"):
                compile_request(dict(mode="r2v", prompt="Replace the actor.", references=[reference],
                                     custom_loras=[row(SWAP_FILE)]))

    def test_unknown_legacy_cross_family_filtering_is_preserved(self):
        self.assertEqual(validate_lora_requirements([row(COMBAT_FILE)], "ref2va")[0]["family"], "both")
        unknown = dict(name="other.safetensors", family="fl2va", strength=0.5)
        self.assertEqual(validate_lora_requirements([unknown], "ref2va")[0]["name"], unknown["name"])


class NewVisualTriggerTests(unittest.TestCase):
    def test_prefix_cues_stay_in_visual_direction_and_leave_raw_dialogue_unchanged(self):
        choices = [row(WUSHU_FILE), row(CAMERA_FILE), row(REALISM_FILE)]
        raw = ref_payload(custom_loras=choices)
        before = copy.deepcopy(raw)
        compiled = compile_request(raw)
        self.assertEqual(raw, before)
        self.assertTrue(compiled.prompt.startswith("subject_definitions:"))
        description = compiled.prompt.split("detailed_description:\n", 1)[1]
        first_shot = next(line for line in description.splitlines() if line.startswith("[Shot "))
        self.assertTrue(first_shot.startswith("[Shot 1] camera motion, r34l1sm, wushu_action,"))
        self.assertIn("<d>[English] hello.</d>", compiled.prompt)
        self.assertNotIn("prfight2", compiled.prompt)

    def test_cues_inside_nested_quotes_do_not_satisfy_visual_prefix(self):
        for prompt in ('[Shot 1] Alice says: "camera motion".',
                       '[Shot 1] A sign reads 「outer「inner」r34l1sm」.'):
            choices = [row(CAMERA_FILE), row(REALISM_FILE)]
            result = _with_lora_triggers(prompt, choices, "ref2va")
            self.assertTrue(result.startswith("[Shot 1] camera motion, r34l1sm, "))
            self.assertTrue(result.endswith(prompt.removeprefix("[Shot 1] ")))

    def test_prefix_insertion_is_idempotent_and_respects_existing_prefix_order(self):
        choices = [row(CAMERA_FILE), row(REALISM_FILE)]
        for prompt in ("[Shot 1] Alice walks.", "[Shot 1] camera motion, Alice walks.",
                       "r34l1sm, camera motion, Alice walks."):
            result = _with_lora_triggers(prompt, choices, "ref2va")
            self.assertEqual(_with_lora_triggers(result, choices, "ref2va"), result)
            self.assertEqual(result.count("camera motion"), 1)
            self.assertEqual(result.count("r34l1sm"), 1)

    def test_disabled_zero_and_wrong_explicit_family_do_not_add_a_cue(self):
        for changes in ({"enabled": False}, {"strength": 0}, {"family": "fl2va"}):
            choices = [row(CAMERA_FILE)]
            choices[0].update(changes)
            self.assertEqual(_with_lora_triggers("[Shot 1] Alice walks.", choices, "ref2va"), "[Shot 1] Alice walks.")

    def test_mg_visual_timeline_gets_prefix_before_first_action(self):
        references = [dict(alias=alias, type=kind, image_asset_ids=[character * 32])
                      for alias, kind, character in (("背景圖", "background", "a"),
                                                     ("轉輪帶", "object", "b"), ("角色", "character", "c"))]
        raw = dict(mode="mg_animation", prompt="角色觀察轉輪。", references=references,
                   custom_loras=[row(CAMERA_FILE), row(REALISM_FILE)])
        before = copy.deepcopy(raw)
        compiled = compile_request(raw)
        self.assertEqual(raw, before)
        description = compiled.prompt.split("detailed_description:\n", 1)[1]
        first_shot = next(line for line in description.splitlines() if line.startswith("[Shot "))
        self.assertTrue(first_shot.startswith("[Shot 1] camera motion, r34l1sm, "))
        self.assertEqual(compiled.prompt.count("camera motion"), 1)
        self.assertEqual(compiled.prompt.count("r34l1sm"), 1)


if __name__ == "__main__":
    unittest.main()
