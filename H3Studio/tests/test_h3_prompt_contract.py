"""User manuscripts and complete official prompts must retain their constraints."""
import unittest

from dialogue import format_dialogue
from domain import RequestError, compile_request
from h3_prompt_format import normalize_prompt_source, normalize_shots, parse_prompt_sections


def payload(prompt, **changes):
    return {"mode": "t2v", "duration": 10, "prompt": prompt, "motion_profile": "none", **changes}


def reference(alias="Alex", image="portrait"):
    return {"alias": alias, "type": "character", "image_asset_ids": [image]}


def full_reference(**changes):
    fields = {
        "subject_definitions": "<Subject 1> is the robot from <Picture 1>; use its red paint as a loose style reference.",
        "summary": "[reference generation] <Subject 1> becomes a small red bird.",
        "retention_analysis": "<Subject 1>: weak_reference; keep only the red paint, allowing an entirely different silhouette.",
        "detailed_description": "[Shot 1] <Subject 1> gradually becomes a small bird and settles on the branch.",
        "overall_soundscape": "Soft wind and a single chirp.",
        "non_diegetic_music": "Sparse piano notes at slow speed.",
    }
    fields.update(changes)
    return "\n\n".join(f"{key}:\n{value}" for key, value in fields.items())


class ManuscriptNormalizationTests(unittest.TestCase):
    def test_hidden_mg_defaults_do_not_warn_for_an_english_base_or_reference_prompt(self):
        for mode in ("t2v", "r2v"):
            extra = {"references": [reference()]} if mode == "r2v" else {}
            c = compile_request(payload("[Shot 1] Alex smiles.", mode=mode,
                                        mg_animation={"character_motion": "角色開心揮手"}, replacement_target="白髮少女", **extra))
            self.assertEqual(c.warnings, [])
        c = compile_request(payload("[Shot 1] Alex smiles.",
                                    references=[{**reference(), "description": "白髮少女"}], reference_style="日式動畫"))
        self.assertEqual(c.warnings, [])

    def test_numbered_manuscript_and_storyboard_titles_become_official_cuts(self):
        for first, second in (
            ("### 分鏡 1：開場（0–3秒）", "### 分鏡 2：收尾（3–10秒）"),
            ("1. 0.0–3.0s｜開場", "2. 3.0–10.0s｜收尾"),
            ("0–3 秒：開場", "3–10 秒：收尾"),
            ("時間碼：0–3 秒", "時間碼：3–10 秒"),
            ("### **分镜 1：开场（0–3秒）**", "### **分镜 2：收尾（3–10秒）**"),
        ):
            with self.subTest(first=first):
                result = compile_request(payload(first + "\r\nAlex raises a hand.\r\n" + second + "\r\nAlex settles."))
                self.assertEqual(result.prompt.count("[Shot 1]"), 1)
                self.assertIn("[Shot 2] At 00:03.000,", result.prompt)
                self.assertNotIn("0.0–3.0s", result.prompt)

    def test_reference_and_shortfilm_profiles_share_the_manuscript_parser(self):
        text = "1. 0–3s｜Opening\nAlex turns.\n2. 3–10s｜Ending\nAlex settles."
        for profile in ("", "shortfilm"):
            c = compile_request(payload(text, mode="r2v", prompt_profile=profile, references=[reference()]))
            self.assertIn("[Shot 2] At 00:03.000, Ending", c.prompt)
            self.assertIn("<Subject 1>（Alex） settles", c.prompt)

    def test_incomplete_or_ambiguous_manuscript_is_never_silently_one_shot(self):
        texts = (
            "1. 0–3s｜Opening\n2. 4–10s｜Gap", "1. 0–4s｜Opening\n2. 3–10s｜Overlap",
            "1. 0–3s｜Opening\n3. 3–10s｜Skipped", "1. 0–3s｜Only half",
            "1. 0–3s｜Opening\n2. 3–11s｜Past end", "1. 1–3s｜Late opening\n2. 3–10s｜End",
            "### 分鏡 1：Opening（0–3秒）\n### 分鏡 2：沒有時間",
            "### 分鏡一：Opening（0–10秒）", "1. 00:00–00:10｜Unsupported clock",
            "1. 0–3s｜Opening\n[Shot 2] At 00:03.000, Ending.",
            "[Shot 1] A. [Shot 1] B.", "[Shot 1] At 00:bad, A.",
            "[Shot 1] A. [Shot 2] At 00:60.000, B.", "[Shot 1] A. [Shot two] B.",
            "[Shot 1, 0s-3s] A. [Shot 2, 4s-10s] B.",
        )
        for text in texts:
            with self.subTest(text=text), self.assertRaises(RequestError):
                compile_request(payload(text))

    def test_quotes_dialogue_and_visible_signs_cannot_create_cuts(self):
        text = ('[Shot 1] A sign reads "\n1. 0–3s｜公告\n2. 3–10s｜結尾\n". '
                '<d>[Chinese] 請讀：\n### 分鏡 9：文字（2–3秒）\n</d>')
        self.assertEqual(normalize_shots(text, 10), (text, 1))

    def test_pretranslation_normalizer_is_idempotent_and_preserves_all_other_sections(self):
        original = full_reference(detailed_description='1. 0–3s｜開場\nAlex輕聲細語：「等一下！」\n2. 3–10s｜收尾\nAlex settles.')
        prepared = normalize_prompt_source(original, 10.125, speakers=("Alex",), timeline_duration=10)
        before, after = parse_prompt_sections(original), parse_prompt_sections(prepared)
        for key in before:
            if key != "detailed_description":
                self.assertEqual(after[key], before[key])
        self.assertIn("[Shot 2] At 00:03.000,", prepared)
        self.assertIn("<d>[Chinese] 等一下！</d>", prepared)
        self.assertEqual(normalize_prompt_source(prepared, 10.125, speakers=("Alex",), timeline_duration=10), prepared)
        self.assertEqual(normalize_prompt_source("Alex moves.", 10), "Alex moves.")

    def test_known_delivery_cues_and_mixed_sound_fields_wrap_only_exact_spoken_words(self):
        for cue in ("輕聲細語", "害羞道", "轻声细语"):
            text = f'台詞／音效：Alex{cue}：「請、等一下！」；衣料摩擦「沙沙」。'
            formatted = format_dialogue(text, speakers=("Alex",))
            self.assertEqual(formatted, f'台詞／音效：Alex{cue}：<d>[Chinese] 請、等一下！</d>；衣料摩擦「沙沙」。')
            self.assertEqual(format_dialogue(formatted, speakers=("Alex",)), formatted)
        self.assertEqual(format_dialogue("台詞：你好。音效：風聲。"), "台詞：<d>[Chinese] 你好。</d>音效：風聲。")
        for text in ('台詞／音效：「呼——」', '字幕顯示害羞道：「等一下。」', 'Alex不要輕聲細語：「等一下。」'):
            self.assertEqual(format_dialogue(text, speakers=("Alex",)), text)

    def test_first_actual_speaker_is_numbered_once_and_explicit_ids_are_preserved(self):
        for explicit in ("", " (S4)"):
            text = f'Alex stands silently. Bea{explicit}害羞道：「等一下！」。 Bea輕聲細語：「我來了。」'
            c = compile_request(payload(text, mode="r2v", references=[reference(), reference("Bea", "bea")]))
            number = "S4" if explicit else "S1"
            self.assertEqual(c.prompt.count(f"({number})"), 2)
            self.assertNotIn("Alex） (S", c.prompt)

    def test_text_only_named_speaker_keeps_one_id_across_cuts(self):
        text = ('[Shot 1] A small red robot named Alex stands by a window. Alex whispers: 「你好！」 '
                '[Shot 2] At 00:03.000, Alex replies: "Welcome home!"')
        c = compile_request(payload(text))
        self.assertEqual(c.prompt.count("Alex (S1)"), 2)
        self.assertIn("<d>[Chinese] 你好！</d>", c.prompt)
        self.assertIn("<d>[English] Welcome home!</d>", c.prompt)
        self.assertEqual(c.warnings, [])

    def test_text_only_speakers_follow_first_actual_voice_not_visual_introduction(self):
        text = ('[Shot 1] Alex stands beside Bea. Bea whispers: "Hello." '
                'Alex replies: "Hi." Bea says: "Goodbye."')
        c = compile_request(payload(text))
        self.assertEqual(c.prompt.count("Bea (S1)"), 2)
        self.assertIn("Alex (S2) replies", c.prompt)

    def test_text_only_explicit_ids_and_narrator_labels_are_preserved(self):
        text = '[Shot 1] Alex (S4) whispers: "Hello." Alex replies: "Hi."'
        c = compile_request(payload(text))
        self.assertEqual(c.prompt.count("Alex (S4)"), 2)
        self.assertNotIn("(S1)", c.prompt)
        narrator = compile_request(payload("[Shot 1] 旁白：你好。"))
        self.assertIn("旁白 (S1)：<d>[Chinese] 你好。</d>", narrator.prompt)

    def test_text_only_signage_does_not_create_or_reserve_a_voice(self):
        text = ('[Shot 1] A sign reads "Alex (S1) says: Hello." '
                'Bea whispers: "Welcome." A screen displays "<d>[English] Alex says hello.</d>".')
        c = compile_request(payload(text))
        self.assertIn("Bea (S1) whispers", c.prompt)
        self.assertEqual(c.prompt.count("Bea (S1)"), 1)
        self.assertNotIn("Alex (S2)", c.prompt)
        self.assertEqual(c.warnings, [])

    def test_ambiguous_text_only_speech_is_retained_with_an_actionable_warning(self):
        for text in ('[Shot 1] He says: "Hello."', '[Shot 1] 她說：「Hello.」', '[Shot 1] Alex and Bea say <d>[English] Hello.</d>',
                     '[Shot 1] <d>[English] Hello.</d>'):
            with self.subTest(text=text):
                c = compile_request(payload(text))
                self.assertIn("<d>[English] Hello.</d>", c.prompt)
                self.assertNotIn("(S1)", c.prompt)
                self.assertTrue(any("無法確定發話來源" in warning for warning in c.warnings))

    def test_official_compound_speaker_ids_are_preserved_without_guessing_members(self):
        group = 'Alex and Bea (S1,S2) shout together <d>[English] Wait for us!</d>'
        c = compile_request(payload(group, mode="r2v", references=[reference(), reference("Bea", "bea")]))
        self.assertIn("(S1,S2) shout together <d>[English] Wait for us!</d>", c.prompt)
        self.assertNotIn("(S1) (S1,S2)", c.prompt)
        self.assertNotIn("(S2) (S1,S2)", c.prompt)
        with self.assertRaisesRegex(RequestError, "不會自動猜測聲源"):
            compile_request(payload(group + ' Alex says "I am here."', mode="r2v", references=[reference(), reference("Bea", "bea")]))
        explicit = group + ' Alex (S1) says "I am here." Bea (S2) says "Me too."'
        self.assertIn("(S1,S2) shout together", compile_request(payload(explicit, mode="r2v", references=[reference(), reference("Bea", "bea")])).prompt)


class CompletePromptPreservationTests(unittest.TestCase):
    def test_full_six_section_rules_survive_compilation_and_recompilation(self):
        original = full_reference()
        raw = payload(original, mode="r2v", references=[reference()])
        first = compile_request(raw)
        sections = parse_prompt_sections(first.prompt)
        for key, value in parse_prompt_sections(original).items():
            self.assertEqual(sections[key], value)
        self.assertNotIn("fully_preserved whenever", first.prompt)
        self.assertEqual(compile_request({**raw, "prompt": first.prompt}).prompt, first.prompt)
        self.assertEqual(raw["prompt"], original)

    def test_full_base_music_and_sound_are_not_replaced_by_form_defaults(self):
        original = "integrated_multimodal_description: [Shot 1] Alex sits.\n\noverall_soundscape: Rain taps on glass.\n\nnon_diegetic_music: Slow cello notes."
        c = compile_request(payload(original, overall_soundscape="N/A", non_diegetic_music="N/A"))
        self.assertIn("overall_soundscape: Rain taps on glass.", c.prompt)
        self.assertTrue(c.prompt.endswith("non_diegetic_music: Slow cello notes."))

    def test_full_prompt_rejects_incomplete_duplicate_and_mixed_sections(self):
        for original in (
            full_reference().replace("retention_analysis:", "retention_analyses:"),
            full_reference() + "\n\nsummary:\n[reference generation] Another summary.",
            full_reference(overall_soundscape=""),
            "detailed_description: [Shot 1] A.\n\noverall_soundscape: Wind.",
        ):
            with self.subTest(original=original), self.assertRaises(RequestError):
                compile_request(payload(original, mode="r2v", references=[reference()]))

    def test_missing_nonexistent_duplicate_and_rebound_labels_are_rejected(self):
        for changes in (
            {"detailed_description": "[Shot 1] <Subject 9> appears."},
            {"subject_definitions": "<Subject 1> is from <Picture 2>."},
            {"subject_definitions": "<Subject 1> is the red robot, from an unspecified image."},
            {"subject_definitions": "<Subject 1> is from <Picture 1>.\n<Subject 1> is another person."},
            {"detailed_description": "[Shot 1] <Subject 1> sees <Picture 2>."},
            {"retention_analysis": "N/A"}, {"detailed_description": "[Shot 1] <Subject 01> moves."},
            {"summary": "[reference generation + reference generation] Duplicate roles."},
        ):
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                compile_request(payload(full_reference(**changes), mode="r2v", references=[reference(), reference("Bea", "bea")]))

    def test_labels_spoken_or_printed_as_text_do_not_bind_to_uploads(self):
        original = full_reference(detailed_description='[Shot 1] <Subject 1> says <d>[English] Read <Subject 99>.</d> A sign reads "<Picture 99>".')
        c = compile_request(payload(original, mode="r2v", references=[reference()]))
        self.assertIn("Read <Subject 99>.", c.prompt)
        self.assertIn('"<Picture 99>"', c.prompt)
        self.assertIn("<Subject 1> (S1) says", c.prompt)

    def test_complete_prompt_preserves_speaker_id_from_definition_and_later_speech(self):
        original = full_reference(
            subject_definitions="<Subject 1> (S4) is the robot from <Picture 1>, with a quiet voice.",
            detailed_description='[Shot 1] <Subject 1> says <d>[English] Hello.</d> <Subject 1> says <d>[English] Goodbye.</d>')
        c = compile_request(payload(original, mode="r2v", references=[reference()]))
        self.assertEqual(c.prompt.count("<Subject 1> (S4)"), 3)
        self.assertNotIn("(S1)", c.prompt)

    def test_mode_mismatch_and_unknown_narrative_tags_fail_before_any_workflow(self):
        for raw in (
            payload(full_reference()),
            payload("integrated_multimodal_description: [Shot 1] A.\n\noverall_soundscape: Wind.\n\nnon_diegetic_music: N/A", mode="r2v", references=[reference()]),
            payload("[Shot 1] <Picture 1> is shown."),
            payload("[Shot 1] <Subject 9> walks.", mode="r2v", references=[reference()]),
        ):
            with self.subTest(raw=raw), self.assertRaises(RequestError):
                compile_request(raw)


if __name__ == "__main__":
    unittest.main()
