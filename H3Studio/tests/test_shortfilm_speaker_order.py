"""Speaker IDs follow actual project-wide speech rather than the asset list."""
import unittest

from domain import compile_request
from shortfilm import ShortFilmError, _speaker_ids, compile_shot_payload, new_asset, new_project, new_scene, new_shot


def project_with_speakers():
    project = new_project("Speech order")
    project["style"] = "Live action"
    project["assets"] = [new_asset("character", name) for name in ("Alex", "Bea", "Silent")]
    for index, asset in enumerate(project["assets"], 1):
        asset["image_asset_ids"] = [str(index) * 32]
    scene = new_scene(1)
    project["scenes"] = [scene]
    for index in range(1, 4):
        shot = new_shot(index)
        shot.update(action="Alex and Bea stand beside Silent.", asset_ids=[asset["id"] for asset in project["assets"]])
        scene["shots"].append(shot)
    return project, scene, scene["shots"]


class ShortfilmSpeakerOrderTests(unittest.TestCase):
    def test_dedicated_and_inline_dialogue_keep_project_order_across_cuts(self):
        project, scene, shots = project_with_speakers()
        shots[0].update(dialogue="Hello!", speaker_alias="Bea", dialogue_language="English")
        shots[1].update(dialogue="Welcome.", speaker_alias="Alex", dialogue_language="English")
        shots[2]["action"] = 'Bea輕聲細語：「我回來了。」'
        self.assertEqual(_speaker_ids(project), {"Bea": "S1", "Alex": "S2"})
        for shot, expected in ((shots[0], "Bea (S1) says"), (shots[1], "Alex (S2) says"), (shots[2], "Bea (S1)輕聲細語")):
            raw, _ = compile_shot_payload(project, scene["id"], shot["id"])
            self.assertIn(expected, raw["prompt"])
            compiled = compile_request(raw)
            self.assertNotIn("Silent） (S", compiled.prompt)
            self.assertNotIn("(S3)", compiled.prompt)

    def test_explicit_future_id_is_reserved_without_renumbering_it(self):
        project, scene, shots = project_with_speakers()
        shots[0]["action"] = 'Alex says "Hello!"'
        shots[1]["action"] = 'Bea (S7) says "Hi."'
        shots[2].update(dialogue="Goodbye.", speaker_alias="Bea", dialogue_language="English")
        self.assertEqual(_speaker_ids(project), {"Bea": "S7", "Alex": "S1"})
        raw, _ = compile_shot_payload(project, scene["id"], shots[2]["id"])
        self.assertIn("Bea (S7) says", raw["prompt"])

    def test_spoken_and_visible_id_text_do_not_reserve_a_speaker(self):
        project, _, shots = project_with_speakers()
        shots[0]["action"] = 'A sign reads "Silent (S1)". Bea says <d>[English] Read (S1), please.</d>'
        self.assertEqual(_speaker_ids(project), {"Bea": "S1"})

    def test_silent_explicit_introduction_is_used_only_if_that_character_later_speaks(self):
        project, _, shots = project_with_speakers()
        shots[0]["action"] = "Alex (S4) stands still. Silent (S8) looks out of the window."
        self.assertEqual(_speaker_ids(project), {})
        shots[1]["action"] = 'Alex says "Hello."'
        self.assertEqual(_speaker_ids(project), {"Alex": "S4"})

    def test_conflicting_explicit_ids_stop_compilation_instead_of_switching_voices(self):
        for first, second in (("Alex (S3)", "Alex (S4)"), ("Alex (S3)", "Bea (S3)")):
            project, scene, shots = project_with_speakers()
            shots[0]["action"] = first + ' says "Hello."'
            shots[1]["action"] = second + ' says "Hi."'
            with self.subTest(first=first, second=second), self.assertRaises(ShortFilmError):
                compile_shot_payload(project, scene["id"], shots[0]["id"])


if __name__ == "__main__":
    unittest.main()
