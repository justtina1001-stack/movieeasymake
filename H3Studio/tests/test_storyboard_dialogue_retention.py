"""Real missing-speech regression and conservative exact manuscript retention."""
import copy
import json
from pathlib import Path
import unittest

import test_scriptwriting as scripts
from scriptwriting import ScriptwritingError, parse_storyboard, storyboard_dialogue_retention


FIXTURES = Path(__file__).with_name("fixtures")
SOURCE = (FIXTURES / "storyboard_dialogue_retention_source.txt").read_text("utf-8")


def observed(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def retained():
    return observed("gemma4_storyboard_retained_dialogue.json")


class DialogueRetentionTests(unittest.TestCase):
    def test_actual_hermes_format_pass_is_a_content_failure_without_parser_changes(self):
        raw = observed("hermes4_storyboard_omitted_dialogue.json")
        before = copy.deepcopy(raw)
        parsed = parse_storyboard(json.dumps(raw, ensure_ascii=False), target_duration=15)
        report = storyboard_dialogue_retention(parsed, SOURCE)
        self.assertFalse(report["passed"])
        self.assertEqual((report["required_count"], report["matched_count"], report["missing_count"]), (2, 0, 2))
        self.assertEqual(raw, before)

    def test_actual_gemma_preserves_both_exact_lines_without_mutation(self):
        raw = retained()
        before = copy.deepcopy(raw)
        report = storyboard_dialogue_retention(raw, SOURCE)
        self.assertTrue(report["passed"])
        self.assertEqual((report["required_count"], report["matched_count"]), (2, 2))
        self.assertEqual(raw, before)

    def test_punctuation_loss_duplication_and_synopsis_only_speech_fail(self):
        for change in ("punctuation", "duplicate", "synopsis"):
            raw = retained()
            shot = raw["scenes"][0]["shots"][0]
            if change == "punctuation":
                shot["action"] = shot["action"].replace("天亮前，別回頭。", "天亮前別回頭。")
            elif change == "duplicate":
                shot["action"] += " 玄青(S1) says <d>[Chinese] 天亮前，別回頭。</d>"
            else:
                raw["synopsis"] = shot["action"]
                raw["title"] = "天亮前，別回頭。我會把它帶回來。"
                shot["action"] = "The jade slip changes hands."
            with self.subTest(change=change):
                report = storyboard_dialogue_retention(raw, SOURCE)
                self.assertFalse(report["passed"])
                if change == "duplicate":
                    self.assertEqual(report["duplicate_count"], 1)

    def test_repeated_mandated_utterances_keep_exact_occurrence_count(self):
        source = '對白：阿青說：「回家。」\n對白：阿青說：「回家。」'
        raw = copy.deepcopy(scripts.STORYBOARD)
        shot = raw["scenes"][0]["shots"][0]
        shot.update(dialogue="", speaker="")
        for count in (1, 2, 3):
            shot["action"] = ' '.join(['阿青(S1) says <d>[Chinese] 回家。</d>'] * count)
            report = storyboard_dialogue_retention(raw, source)
            self.assertEqual(report["passed"], count == 2)
            self.assertEqual(report["required_count"], 2)

    def test_visible_text_quotes_markup_and_silent_labels_are_not_mandated_speech(self):
        raw = copy.deepcopy(scripts.STORYBOARD)
        raw["scenes"][0]["shots"][0].update(action="阿青 raises a hand.", dialogue="", speaker="")
        source = ('字幕：「天亮前，別回頭。」\n招牌文字：「我會把它帶回來。」\n'
                  '字卡：「<d>[Chinese] 這是畫面標記。</d>」\n'
                  '字幕：<d>[Chinese] 畫面文字。</d>\n'
                  '動作：阿青拿起寫著「離開」的牌子。\n對白：無。\n對白：**無**。')
        report = storyboard_dialogue_retention(raw, source)
        self.assertEqual(report["required_count"], 0)
        self.assertTrue(report["passed"])

    def test_visual_output_markup_cannot_satisfy_actual_required_speech(self):
        raw = retained()
        raw["scenes"][0]["shots"][0].update(
            action='A subtitle reads <d>[Chinese] 天亮前，別回頭。</d> '
                   'A sign reads "<d>[Chinese] 我會把它帶回來。</d>".', dialogue="", speaker="")
        self.assertFalse(storyboard_dialogue_retention(raw, SOURCE)["passed"])

    def test_explicit_tagged_source_and_legacy_dialogue_fields_are_supported(self):
        raw = copy.deepcopy(scripts.STORYBOARD)
        raw["scenes"][0]["shots"][0].update(action="阿青 raises a hand.", dialogue="我們回家吧。", speaker="阿青")
        source = '阿青(S1) says <d>[Chinese] 我們回家吧。</d>'
        self.assertTrue(storyboard_dialogue_retention(raw, source)["passed"])
        raw["scenes"][0]["shots"][0]["dialogue"] = "無。"
        self.assertTrue(storyboard_dialogue_retention(raw, '對白：阿青說：「無。」')["passed"])


class DialogueRetentionAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = scripts.ScriptAPITests.asyncSetUp
    asyncTearDown = scripts.ScriptAPITests.asyncTearDown
    fake_tags = scripts.ScriptAPITests.fake_tags
    fake_show = scripts.ScriptAPITests.fake_show
    fake_chat = scripts.ScriptAPITests.fake_chat
    fake_unload = scripts.ScriptAPITests.fake_unload
    fake_free = scripts.ScriptAPITests.fake_free
    redirect_target = scripts.ScriptAPITests.redirect_target
    create = scripts.ScriptAPITests.create
    submit = scripts.ScriptAPITests.submit
    complete = scripts.ScriptAPITests.complete

    async def test_omitted_speech_fails_preserves_raw_and_cannot_recover_or_import(self):
        raw = observed("hermes4_storyboard_omitted_dialogue.json")
        raw["scenes"][0]["shots"][0]["camera"] = "50mm fixed medium shot"
        self.reply = json.dumps(raw, ensure_ascii=False)
        project = await self.create(duration=15, content=SOURCE)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("指定台詞", result["error"])
        self.assertEqual(result["content"], self.reply)
        self.assertEqual(result["dialogue_retention"]["missing_count"], 2)
        self.assertEqual(len(self.chat_requests), 1)
        self.assertFalse(await self.manager.recover_storyboard(result["id"]))
        stored = self.manager.jobs[result["id"]]
        stored.update(status="completed", storyboard=parse_storyboard(self.reply, target_duration=15))
        with self.assertRaisesRegex(ScriptwritingError, "指定台詞"):
            self.manager.import_shortfilm(result["id"])
        self.assertEqual(self.manager.get_project(project["id"])["content"], SOURCE)
        self.assertEqual(len(self.chat_requests), 1)

    async def test_retained_speech_passes_new_generation_and_import(self):
        self.reply = json.dumps(retained(), ensure_ascii=False)
        result = await self.complete(await self.submit(await self.create(duration=15, content=SOURCE), action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["dialogue_retention"]["passed"])
        self.assertEqual(result["dialogue_retention"]["required_count"], 2)
        self.assertEqual(self.manager.import_shortfilm(result["id"])["project"]["target_duration"], 15)


if __name__ == "__main__":
    unittest.main()
