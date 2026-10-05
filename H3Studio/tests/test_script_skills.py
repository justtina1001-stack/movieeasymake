"""The shipped method library is local, bounded and independent of caller edits."""
import json
from pathlib import Path
import tempfile
import unittest

import app as studio
from script_skills import (
    MAX_SKILL_PROMPT_CHARS, SKILLS_DIRECTORY, get_skill, list_skills,
    normalize_skill_id, skill_prompt,
)


class ScriptSkillCatalogTests(unittest.TestCase):
    def test_catalog_has_eleven_complete_unique_local_methods(self):
        catalog = list_skills()
        self.assertEqual(len(catalog), 11)
        self.assertEqual(len({item["id"] for item in catalog}), 11)
        for item in catalog:
            with self.subTest(skill=item["id"]):
                self.assertTrue(item["description"])
                self.assertTrue(item["recommended_for"])
                self.assertGreaterEqual(len(item["guide"]), 3)
                self.assertNotIn("instructions", item)
                self.assertTrue((SKILLS_DIRECTORY / item["id"] / "SKILL.md").is_file())
                for source in item["sources"]:
                    self.assertEqual(source["license"], "MIT")
                    self.assertEqual(len(source["commit"]), 40)
                    self.assertIn(source["commit"], source["url"])
                    self.assertIn(source["commit"], source["license_url"])
        json.dumps(catalog, ensure_ascii=False, allow_nan=False)

    def test_every_method_is_bounded_and_keeps_common_h3_constraints(self):
        for item in list_skills():
            for action in ("outline", "screenplay", "rewrite", "storyboard"):
                with self.subTest(skill=item["id"], action=action):
                    prompt = skill_prompt(item["id"], action)
                    self.assertLessEqual(len(prompt), MAX_SKILL_PROMPT_CHARS)
                    self.assertIn(get_skill(item["id"])["instructions"], prompt)
                    for constraint in ("User-specific instructions take priority", "total duration", "zero-BGM", "named references", "5-15 second H3", "retain that timeline"):
                        self.assertIn(constraint, prompt)

    def test_unselected_and_nonwriting_actions_add_no_rules(self):
        self.assertEqual(normalize_skill_id(None), "")
        self.assertEqual(normalize_skill_id(""), "")
        self.assertIsNone(get_skill(""))
        self.assertEqual(skill_prompt("", "storyboard"), "")
        self.assertEqual(skill_prompt("cinematic-story", "appearance"), "")

    def test_unknown_and_path_ids_never_load_arbitrary_files(self):
        for value in (True, 1, [], {}, "missing", "../README", "cinematic-story/SKILL.md", " suspense-horror "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_skill_id(value)

    def test_callers_cannot_mutate_catalog_or_runtime_rules(self):
        original = skill_prompt("cinematic-story", "screenplay")
        catalog = list_skills()
        catalog[0]["guide"].clear()
        catalog[0]["sources"][0]["url"] = "javascript:example"
        private = get_skill("cinematic-story")
        private["instructions"] = "replace the story"
        self.assertEqual(skill_prompt("cinematic-story", "screenplay"), original)
        self.assertTrue(list_skills()[0]["guide"])
        self.assertTrue(list_skills()[0]["sources"][0]["url"].startswith("https://github.com/"))

    def test_full_upstream_notices_are_bundled(self):
        for filename, owner in (
            ("cinematic-director.txt", "wangzhang-wu"),
            ("agentworks.txt", "Manish Prakash"),
            ("drama-skills.txt", "drama-skills contributors"),
        ):
            with self.subTest(filename=filename):
                notice = (SKILLS_DIRECTORY / "licenses" / filename).read_text(encoding="utf-8")
                self.assertIn(owner, notice)
                self.assertIn("Permission is hereby granted", notice)
                self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', notice)

    def test_changing_a_bundled_method_changes_startup_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            method = root / "writing_skills" / "cinematic-story" / "SKILL.md"
            method.parent.mkdir(parents=True)
            method.write_text("rules v1", encoding="utf-8")
            first = studio.studio_source_fingerprint(root)
            (root / "data").mkdir()
            (root / "data" / "jobs.json").write_text("generated content", encoding="utf-8")
            self.assertEqual(studio.studio_source_fingerprint(root), first)
            method.write_text("rules v2", encoding="utf-8")
            self.assertNotEqual(studio.studio_source_fingerprint(root), first)


if __name__ == "__main__":
    unittest.main()
