"""Canonical generation requests and observed ambiguous outputs, without inference."""
import copy
import json
from pathlib import Path
import unittest

import test_scriptwriting as scripts
from domain import compile_request
from scriptwriting import ScriptwritingError, parse_storyboard, storyboard_response_schema
from shortfilm import compile_shot_payload


FIXTURES = Path(__file__).with_name("fixtures")


def observed(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def canonical():
    raw = observed("gemma4_storyboard_untimed_dialogue.json")
    shot = raw["scenes"][0]["shots"][0]
    shot.update(dialogue="", speaker="", action=(
        '玄青 stands on the left, 小羽 on the right. 玄青 extends the single jade slip. '
        '玄青 (S1) says <d>[Chinese] 天亮前，別回頭。</d> '
        '[Shot 2] At 00:05.000, 小羽 receives the jade slip with both hands; '
        '玄青 releases it and withdraws. 小羽 (S2) says <d>[Chinese] 我會把它帶回來。</d> '
        '[Shot 3] At 00:10.000, 小羽 holds the jade slip at her chest and exits right; '
        '玄青 remains at the stone platform. No BGM.'
    ))
    return raw


class CanonicalSchemaTests(unittest.TestCase):
    def test_observed_ambiguous_outputs_remain_rejected_without_mutation(self):
        for name in ("gemma4_storyboard_untimed_dialogue.json", "hermes4_storyboard_duplicate_dialogue.json"):
            raw = observed(name)
            before = copy.deepcopy(raw)
            with self.subTest(name=name), self.assertRaises(ScriptwritingError):
                parse_storyboard(json.dumps(raw, ensure_ascii=False), target_duration=15)
            self.assertEqual(raw, before)

    def test_short_request_schema_requires_empty_legacy_fields_and_one_whole_clip(self):
        schema = storyboard_response_schema(15)
        scenes = schema["properties"]["scenes"]
        shots = scenes["items"]["properties"]["shots"]
        fields = shots["items"]["properties"]
        self.assertEqual((scenes["minItems"], scenes["maxItems"], shots["minItems"], shots["maxItems"]), (1, 1, 1, 1))
        self.assertEqual(fields["duration"]["enum"], [15])
        self.assertEqual(fields["dialogue"]["enum"], [""])
        self.assertEqual(fields["speaker"]["enum"], [""])
        self.assertFalse(shots["items"]["additionalProperties"])
        self.assertIn("static", fields["camera"]["enum"])
        self.assertIn("custom", fields["camera"]["enum"])
        self.assertIn("medium", fields["shot_size"]["enum"])
        # This is a new-request constraint, not a change to valid legacy data.
        legacy = copy.deepcopy(scripts.STORYBOARD)
        self.assertEqual(parse_storyboard(json.dumps(legacy), target_duration=5), legacy)

    def test_long_request_schema_keeps_multiple_supported_clips_and_is_detached(self):
        schema = storyboard_response_schema(30)
        scenes = schema["properties"]["scenes"]
        fields = scenes["items"]["properties"]["shots"]["items"]["properties"]
        self.assertGreater(scenes["maxItems"], 1)
        self.assertNotIn("enum", fields["duration"])
        self.assertEqual((fields["duration"]["minimum"], fields["duration"]["maximum"]), (5, 15))
        fields["camera"]["enum"].clear()
        self.assertIn("static", storyboard_response_schema(30)["properties"]["scenes"]["items"]["properties"]["shots"]["items"]["properties"]["camera"]["enum"])


class CanonicalRequestTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_canonical_wire_schema_and_timed_speech_survive_import_and_compile(self):
        raw = canonical()
        self.reply = json.dumps(raw, ensure_ascii=False)
        result = await self.complete(await self.submit(await self.create(duration=15), action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["content"], self.reply)
        self.assertEqual(self.chat_requests[0]["format"], result["input"]["response_format"])
        project = self.manager.import_shortfilm(result["id"])["project"]
        scene = project["scenes"][0]
        shot = scene["shots"][0]
        payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
        prompt = compile_request(payload).prompt
        for text in ("天亮前，別回頭。", "我會把它帶回來。"):
            self.assertEqual(prompt.count(text), 1)
        self.assertLess(prompt.index("天亮前，別回頭。"), prompt.index("At 00:05.000"))
        self.assertLess(prompt.index("At 00:05.000"), prompt.index("我會把它帶回來。"))
        self.assertLess(prompt.index("我會把它帶回來。"), prompt.index("At 00:10.000"))

    async def test_engine_ignoring_schema_fails_preserves_raw_and_never_retries(self):
        project = await self.create(duration=15, content="使用者原稿，不可覆寫。")
        for name in ("gemma4_storyboard_untimed_dialogue.json", "hermes4_storyboard_duplicate_dialogue.json"):
            self.reply = json.dumps(observed(name), ensure_ascii=False)
            count = len(self.chat_requests)
            result = await self.complete(await self.submit(project, action="storyboard"))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["content"], self.reply)
            self.assertEqual(len(self.chat_requests), count + 1)
            self.assertEqual(self.manager.get_project(project["id"])["content"], project["content"])


if __name__ == "__main__":
    unittest.main()
