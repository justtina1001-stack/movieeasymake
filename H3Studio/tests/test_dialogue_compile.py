"""Dialogue formatting at the same compiler boundary used by preview and render."""

import copy
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

import app as studio
from domain import compile_request


IMAGE_A = "a" * 32


def request_payload(prompt="小明走進車站。", **values):
    payload = {
        "mode": "t2v",
        "prompt": prompt,
        "duration": 5,
        "aspect_ratio": "16:9",
        "megapixels": 0.4,
        "seed": 42,
        "steps": 20,
    }
    payload.update(values)
    return payload


def spoken_blocks(prompt):
    return re.findall(r"<d>(.*?)</d>", prompt, flags=re.DOTALL)


class DialogueCompileTests(unittest.TestCase):
    def test_inline_chinese_speech_is_tagged_without_changing_the_words(self):
        compiled = compile_request(request_payload("小明走進車站。小明說：「你好，歡迎回來……！」"))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 你好，歡迎回來……！"])
        self.assertIn("小明走進車站。", compiled.prompt)

    def test_markdown_storyboard_dialogue_and_narration_are_tagged(self):
        compiled = compile_request(request_payload(
            "### 分鏡 1\n小明推開門。\n**小明台詞：**\n「我回來了！」\n\n"
            "### 分鏡 2\n鏡頭推近。\n**內心旁白：**\n「終於……到家了。」"
        ))
        self.assertEqual(spoken_blocks(compiled.prompt), [
            "[Chinese] 我回來了！", "[Chinese] 終於……到家了。",
        ])

    def test_multiple_speakers_remain_separate(self):
        compiled = compile_request(request_payload("小明說：「你好。」\n小美回答：「好久不見！」"))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 你好。", "[Chinese] 好久不見！"])

    def test_quoted_signage_and_nonspoken_notes_are_not_dialogue(self):
        prompt = "招牌寫著「歡迎光臨」。\n字幕：『明天見』\n注意：「保持鏡頭穩定」。"
        compiled = compile_request(request_payload(prompt))
        self.assertEqual(spoken_blocks(compiled.prompt), [])
        self.assertIn(prompt, compiled.prompt)

    def test_signage_before_dialogue_does_not_get_wrapped_with_speech(self):
        compiled = compile_request(request_payload("招牌寫著「歡迎光臨」。小明說：「你好！」"))
        self.assertIn("招牌寫著「歡迎光臨」。", compiled.prompt)
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 你好！"])

    def test_explicit_no_dialogue_heading_does_not_make_stage_direction_spoken(self):
        for prompt in (
            "無對白：角色只張嘴微笑，保持安靜。",
            "沒有台詞：只有車站環境音。",
            "No dialogue: The actor smiles silently.",
        ):
            with self.subTest(prompt=prompt):
                compiled = compile_request(request_payload(prompt))
                self.assertEqual(spoken_blocks(compiled.prompt), [])
                self.assertIn(prompt, compiled.prompt)

    def test_caption_described_as_dialogue_is_not_automatically_spoken(self):
        prompt = "字幕顯示台詞：「歡迎光臨」；人物不發聲。"
        compiled = compile_request(request_payload(prompt))
        self.assertEqual(spoken_blocks(compiled.prompt), [])
        self.assertIn(prompt, compiled.prompt)

    def test_clear_english_speech_gets_english_language(self):
        compiled = compile_request(request_payload('Alex says: "Welcome home!"'))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[English] Welcome home!"])

    def test_language_hint_comes_from_speech_clause_not_previous_visual_description(self):
        compiled = compile_request(request_payload("鏡頭拍攝中文招牌。小明用英文說：「Welcome home!」"))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[English] Welcome home!"])
        self.assertIn("鏡頭拍攝中文招牌。", compiled.prompt)

    def test_existing_language_and_speaker_tags_are_preserved(self):
        prompt = "小明 (S1) says <d>[Chinese] 小美，我回來了。</d>。"
        compiled = compile_request(request_payload(prompt))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 小美，我回來了。"])
        self.assertIn("(S1)", compiled.prompt)
        self.assertIn(prompt, compiled.prompt)

    def test_explicit_language_is_not_reinferred_from_latin_words(self):
        tagged = "<d>[French] Bonjour, mon ami !</d>"
        compiled = compile_request(request_payload("旁白：" + tagged))
        self.assertIn(tagged, compiled.prompt)
        self.assertEqual(len(spoken_blocks(compiled.prompt)), 1)

    def test_compiling_tagged_prompt_again_is_idempotent(self):
        payload = request_payload("小明說：「你好。」")
        first = compile_request(payload)
        second = compile_request({**payload, "prompt": first.prompt})
        # The compiler adds presentation sections each time, but must never nest
        # or duplicate dialogue markup when a compiled prompt is pasted back in.
        self.assertEqual(spoken_blocks(first.prompt), ["[Chinese] 你好。"])
        self.assertEqual(spoken_blocks(second.prompt), spoken_blocks(first.prompt))

    def test_storyboard_description_and_bare_dialogue_field_are_both_covered(self):
        payload = request_payload(storyboards=[{
            "duration": 5,
            "description": "小明回頭，低聲說：「等一下。」",
            "dialogue": "你好，請問有人在嗎？",
            "sound": "門鈴輕響。",
        }])
        compiled = compile_request(payload)
        self.assertEqual(spoken_blocks(compiled.prompt), [
            "[Chinese] 等一下。", "[Chinese] 你好，請問有人在嗎？",
        ])
        self.assertIn("門鈴輕響。", compiled.prompt)

    def test_storyboard_dialogue_with_speaker_does_not_speak_stage_direction(self):
        compiled = compile_request(request_payload(storyboards=[{
            "duration": 5,
            "description": "小明站在門前。",
            "dialogue": "小明說：「請開門！」",
        }]))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 請開門！"])

    def test_aliases_in_spoken_text_are_not_replaced_by_reference_tags(self):
        for profile in ("", "shortfilm"):
            with self.subTest(prompt_profile=profile):
                compiled = compile_request(request_payload(
                    "小明揮手。小明說：「小明來了，請小明進來！」",
                    mode="r2v",
                    prompt_profile=profile,
                    references=[{"alias": "小明", "type": "character", "image_asset_ids": [IMAGE_A]}],
                ))
                self.assertIn("<Subject 1>", compiled.prompt)
                self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 小明來了，請小明進來！"])

    def test_background_alias_is_not_treated_as_a_storyboard_speaker(self):
        compiled = compile_request(request_payload(
            "車站入口特寫。",
            mode="r2v",
            references=[{"alias": "招牌", "type": "background", "image_asset_ids": [IMAGE_A]}],
            storyboards=[{"duration": 5, "description": "招牌：「歡迎光臨」"}],
        ))
        self.assertEqual(spoken_blocks(compiled.prompt), [])
        self.assertIn("「歡迎光臨」", compiled.prompt)

    def test_existing_tagged_dialogue_is_protected_from_alias_rewriting(self):
        compiled = compile_request(request_payload(
            "小明 (S1) says <d>[Chinese] 小明，往這裡走！</d>。",
            mode="r2v",
            prompt_profile="shortfilm",
            references=[{"alias": "小明", "type": "character", "image_asset_ids": [IMAGE_A]}],
        ))
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 小明，往這裡走！"])
        self.assertIn("(S1)", compiled.prompt)

    def test_text_only_shortfilm_early_return_also_formats_dialogue(self):
        compiled = compile_request(request_payload("旁白：「火車即將進站。」", prompt_profile="shortfilm"))
        self.assertIn("integrated_multimodal_description:", compiled.prompt)
        self.assertEqual(spoken_blocks(compiled.prompt), ["[Chinese] 火車即將進站。"])

    def test_compilation_does_not_mutate_editable_user_payload(self):
        payload = request_payload(
            "小明說：「你好。」",
            storyboards=[{"duration": 5, "description": "小明說：「再見。」", "dialogue": "明天見！"}],
        )
        original = copy.deepcopy(payload)
        compile_request(payload)
        self.assertEqual(payload, original)


class DialogueSubmissionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(
            studio,
            DATA_DIR=self.root,
            ASSET_DIR=self.root / "assets",
            JOB_DIR=self.root / "jobs",
            OUTPUT_DIR=self.root / "outputs",
            CONFIG_PATH=self.root / "config.json",
        )
        self.paths.start()
        self.app = studio.create_app()
        # Exercise real HTTP handlers without engine startup, job recovery, or GPU work.
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    async def test_preview_and_submission_compile_identical_unicode_dialogue(self):
        payload = request_payload(
            "**小明台詞：**\n「你好，歡迎來到未來的世界。」",
            storyboards=[{"duration": 5, "description": "小明揮手。", "dialogue": "明天見！"}],
        )
        response = await self.client.post("/api/compile", json=payload)
        preview = await response.json()
        self.assertEqual(response.status, 200, preview)
        with patch.object(self.app["jobs"], "create", return_value={"id": "test-job", "status": "queued"}) as create:
            response = await self.client.post("/api/render", json=payload)
            result = await response.json()
            self.assertEqual(response.status, 202, result)
            create.assert_called_once()
            submitted, original_request = create.call_args.args
        self.assertEqual(submitted.prompt, preview["prompt"])
        self.assertEqual(spoken_blocks(submitted.prompt), [
            "[Chinese] 你好，歡迎來到未來的世界。", "[Chinese] 明天見！",
        ])
        self.assertEqual(original_request, payload)
        self.assertNotIn("????", submitted.prompt)


if __name__ == "__main__":
    unittest.main()
