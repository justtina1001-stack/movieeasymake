"""Realistic manuscript normalization before protected English preparation."""
import copy
import hashlib
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from h3_official_policy import (OFFICIAL_PROMPT_DIRECTORY, OFFICIAL_PROMPT_REVISION,
                                official_prompt_sources, official_storyboard_rules)
from h3_prompt_translation import H3PromptTranslator, _mask, _restore, original_h3_payload
from scriptwriting import build_messages, normalize_project


MANUSCRIPT = (
    "### 分鏡 1：Opening（0–2秒）\n阿青向左走。阿青輕聲細語：「你好……！」\n"
    "### 分鏡 2：Ending（2–5秒）\n阿青向右走。"
)


class OfficialPreparationTests(unittest.IsolatedAsyncioTestCase):
    def translator(self):
        app = {"scriptwriting": SimpleNamespace(settings={"base_url": "http://127.0.0.1:11434", "model": "writer"})}
        translator = H3PromptTranslator(app)
        translator._request = AsyncMock()
        return translator

    async def test_disabled_translation_still_formats_shots_and_speech_and_recovers_original(self):
        translator = self.translator()
        raw = {"prompt": MANUSCRIPT, "duration": 5, "translate_directions": False,
               "references": [{"alias": "阿青", "type": "character"}]}
        before = copy.deepcopy(raw)
        prepared = await translator.prepare(raw)
        self.assertEqual(raw, before)
        self.assertIn("[Shot 1]", prepared["prompt"])
        self.assertIn("[Shot 2] At 00:02.000,", prepared["prompt"])
        self.assertIn("<d>[Chinese] 你好……！</d>", prepared["prompt"])
        self.assertEqual(original_h3_payload(prepared)["prompt"], MANUSCRIPT)
        self.assertEqual(prepared["h3_translation"]["status"], "disabled")
        translator._request.assert_not_awaited()

    async def test_writer_sees_normalized_protected_shots_and_preserves_exact_words(self):
        translator = self.translator()

        async def translate(values, settings):
            self.assertTrue(all("分鏡" not in text for text in values.values()))
            return {key: text.replace("向左走。", " walks left.")
                    .replace("向右走。", " walks right.").replace("輕聲細語：", " whispers: ")
                    for key, text in values.items()}

        translator._request.side_effect = translate
        raw = {"prompt": MANUSCRIPT, "duration": 5,
               "references": [{"alias": "阿青", "type": "character"}]}
        prepared = await translator.prepare(raw)
        self.assertEqual(prepared["h3_translation"]["status"], "translated")
        self.assertIn("[Shot 2] At 00:02.000,", prepared["prompt"])
        self.assertIn("<d>[Chinese] 你好……！</d>", prepared["prompt"])
        self.assertEqual(original_h3_payload(prepared)["prompt"], MANUSCRIPT)
        translator._request.assert_awaited_once()

    async def test_missing_writer_still_normalizes_without_running_a_model(self):
        prepared = await H3PromptTranslator({}).prepare({"prompt": MANUSCRIPT, "duration": 5})
        self.assertIn("[Shot 2] At 00:02.000,", prepared["prompt"])
        self.assertEqual(original_h3_payload(prepared)["prompt"], MANUSCRIPT)

    async def test_invalid_timeline_fails_before_loading_a_writer(self):
        translator = self.translator()
        raw = {"duration": 5, "prompt": "1. 0–3s｜Start\nWalk.\n2. 2–5s｜End\nStop."}
        with self.assertRaises(ValueError):
            await translator.prepare(raw)
        translator._request.assert_not_awaited()

    async def test_hidden_mg_defaults_do_not_start_translation_in_other_modes(self):
        translator = self.translator()
        raw = {"mode": "t2v", "prompt": "A red robot waves.",
               "reference_style": "隱藏參考風格", "replacement_target": "隱藏替換目標",
               "references": [{"alias": "隱藏角色", "description": "紅色外衣"}],
               "mg_animation": {"character_motion": "角色揮手", "background_motion": "背景固定"}}
        prepared = await translator.prepare(raw)
        self.assertEqual(prepared, raw)
        translator._request.assert_not_awaited()

    async def test_visible_mg_directions_are_still_translated(self):
        translator = self.translator()
        translator._request.return_value = {"0": "The robot waves."}
        raw = {"mode": "mg_animation", "prompt": "The foreground robot moves.",
               "mg_animation": {"character_motion": "角色揮手"}}
        prepared = await translator.prepare(raw)
        self.assertEqual(prepared["mg_animation"]["character_motion"], "The robot waves.")
        self.assertEqual(raw["mg_animation"]["character_motion"], "角色揮手")
        translator._request.assert_awaited_once()

    def test_section_names_and_complete_cut_notation_are_protected_as_units(self):
        raw = "detailed_description:\n[Shot 2] At 00:02.500, Alex向左走。"
        masked, protected = _mask(raw, ["Alex"])
        self.assertIn("detailed_description:", protected.values())
        self.assertIn("[Shot 2] At 00:02.500,", protected.values())
        restored = _restore(masked.replace("向左走。", " walks left."), masked, protected)
        self.assertEqual(restored, "detailed_description:\n[Shot 2] At 00:02.500, Alex walks left.")


class OfficialPolicyTests(unittest.TestCase):
    def test_git_checkout_preserves_vendored_guide_checksums(self):
        if not shutil.which("git"):
            self.skipTest("Git is needed to verify checkout line-ending policy")
        root = OFFICIAL_PROMPT_DIRECTORY.parents[2]
        paths = ["H3Studio/writing_skills/official_h3_prompt/references/base-en.txt",
                 "H3Studio/writing_skills/official_h3_prompt/references/ref-en.txt"]
        check = subprocess.run(["git", "check-attr", "eol", "--", *paths], cwd=root,
                               capture_output=True, text=True)
        if check.returncode:
            self.skipTest("A Git checkout is needed to verify line-ending policy")
        self.assertEqual(len(check.stdout.splitlines()), 2)
        self.assertTrue(all(line.endswith(": lf") for line in check.stdout.splitlines()))

    def test_pinned_official_skill_and_both_complete_guides_match_provenance(self):
        manifest = official_prompt_sources()
        self.assertEqual(manifest["revision"], OFFICIAL_PROMPT_REVISION)
        for name in ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"):
            data = (OFFICIAL_PROMPT_DIRECTORY / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["files"][name]["sha256"])
            self.assertIn(OFFICIAL_PROMPT_REVISION, manifest["files"][name]["source"])
        self.assertIn("## 7. Complete Example", (OFFICIAL_PROMPT_DIRECTORY / "references/ref-en.txt").read_text("utf-8"))

    def test_storyboard_contract_is_required_even_without_optional_creative_skill(self):
        for creative in ("", "cinematic-story", "h3-official-storyboard"):
            project = normalize_project({"idea": "阿青招呼。", "writing_skill": creative})
            system = build_messages(project, "storyboard", "")[0]["content"]
            self.assertIn(official_storyboard_rules(), system)
            self.assertIn("輸出單一有效 JSON", system)
            self.assertIn("silent subjects receive no ID", system)
            self.assertNotIn("依characters順序固定", system)
        writing = build_messages(normalize_project({"idea": "招呼"}), "screenplay", "")[0]["content"]
        self.assertNotIn(official_storyboard_rules(), writing)


if __name__ == "__main__":
    unittest.main()
