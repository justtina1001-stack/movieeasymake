"""Single-clip screenplay dialogue retains exact words and valid H3 tags."""
import json
import unittest

from domain import compile_request
from scriptwriting import parse_storyboard
from shortfilm import ShortFilmError, compile_shot_payload, new_asset, new_project, new_scene, new_shot


class ShortFilmDialogueTagTests(unittest.TestCase):
    def compile_line(self, dialogue, language="Chinese"):
        project, scene, shot, actor = new_project("Greeting"), new_scene(), new_shot(), new_asset("character", "Alice")
        actor["image_asset_ids"] = ["1" * 32]
        project["assets"] = [actor]
        shot.update(action="Alice waves.", speaker_alias="Alice", dialogue=dialogue, dialogue_language=language)
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        raw, _ = compile_shot_payload(project, scene["id"], shot["id"])
        return compile_request(raw).prompt

    def test_existing_tagged_dialogue_preserves_language_punctuation_and_one_wrapper(self):
        for line in ("<d>[English] Hello, Alice!</d>", "<d>[Japanese] こんにちは！</d>", "<d>[Chinese] 等等……！</d>"):
            with self.subTest(line=line):
                prompt = self.compile_line(line)
                self.assertEqual(prompt.count(line), 1)
                self.assertEqual(prompt.count("<d>"), 1)
                self.assertEqual(prompt.count("</d>"), 1)

    def test_plain_dialogue_uses_selected_language(self):
        self.assertIn("<d>[Japanese] こんにちは！</d>", self.compile_line("こんにちは！", "Japanese"))

    def test_mixed_unbalanced_and_nested_tags_fail_before_generation(self):
        for line in ("<d>[English] Hello", "Hello</d>", "<d>[English] Hello</d> extra", "<d>[Chinese] <d>Hello</d></d>"):
            with self.subTest(line=line), self.assertRaises(ShortFilmError):
                self.compile_line(line)

    def test_five_second_storyboard_tagged_line_survives_the_same_single_clip_path(self):
        line = "<d>[English] Let's go!</d>"
        data = {"title": "Greeting", "characters": [{"name": "Alice", "type": "character", "description": "A traveler."}],
                "scenes": [{"title": "Station", "location": "Station", "time_of_day": "Day", "shots": [
                    {"title": "Hello", "duration": 5, "action": "Alice waves.", "dialogue": line,
                     "speaker": "Alice", "sound": "Footsteps.", "camera": "static", "shot_size": "medium"}]}]}
        normalized = parse_storyboard(json.dumps(data), target_duration=5)
        self.assertIn(line, self.compile_line(normalized["scenes"][0]["shots"][0]["dialogue"]))


if __name__ == "__main__":
    unittest.main()
