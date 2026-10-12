"""Protected content and all-or-nothing local translation, without model inference."""
import copy
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestServer

from h3_prompt_translation import H3PromptTranslator, _mask, _restore


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = {"scriptwriting": SimpleNamespace(settings={"base_url": "http://127.0.0.1:11434", "model": "local-writer"})}
        self.translator = H3PromptTranslator(self.app)

        async def translate(values, settings):
            return {key: value.replace("向左走", "walks left").replace("穿藍衣", "wears blue clothing")
                    .replace("說：", "says: ") for key, value in values.items()}

        self.translator._request = AsyncMock(side_effect=translate)

    async def test_dialogue_visible_text_aliases_and_numeric_times_are_exact(self):
        prompt = '小明向左走，在 "小明咖啡" 招牌旁停下。小明說：「你好……！」'
        # The fake translator does not invent a rewritten story.
        async def translate(values, settings):
            return {key: value.replace("向左走，在 ", " walks left and stops beside ")
                    .replace(" 招牌旁停下。", " signage. ").replace("說：", " says: ")
                    .replace("穿藍衣", "wears blue clothing")
                    for key, value in values.items()}

        self.translator._request.side_effect = translate
        raw = {"prompt": prompt, "references": [{"alias": "小明", "type": "character", "description": "穿藍衣"}]}
        original = copy.deepcopy(raw)
        translated = await self.translator.prepare(raw)
        self.assertEqual(raw, original)
        self.assertEqual(translated["h3_translation"]["status"], "translated")
        self.assertIn('"小明咖啡"', translated["prompt"])
        self.assertIn("<d>[Chinese] 你好……！</d>", translated["prompt"])
        self.assertIn("小明", translated["prompt"])
        self.assertIn("walks left", translated["prompt"])
        self.assertEqual(translated["references"][0]["description"], "wears blue clothing")
        self.assertEqual(translated["h3_translation"]["source_texts"]["prompt"], prompt)

    async def test_english_directions_and_chinese_dialogue_need_no_model(self):
        raw = {"prompt": '小明 walks left. 小明 says <d>[Chinese] 你好。</d>.',
               "references": [{"alias": "小明", "type": "character", "description": "A blue robot."}]}
        self.assertEqual(await self.translator.prepare(raw), raw)
        self.translator._request.assert_not_awaited()

    async def test_same_fields_cache_across_seed_and_size_changes(self):
        raw = {"prompt": "小明向左走", "references": [{"alias": "小明", "description": "穿藍衣"}], "seed": 1}
        first = await self.translator.prepare(raw)
        second = await self.translator.prepare(raw | {"seed": 99, "duration": 10})
        self.assertEqual(first["prompt"], second["prompt"])
        self.assertEqual(second["seed"], 99)
        self.translator._request.assert_awaited_once()
        # Submitted prepared payloads do not translate a second time.
        third = await self.translator.prepare(second)
        self.assertEqual(third["prompt"], second["prompt"])
        self.translator._request.assert_awaited_once()

    async def test_model_failure_retains_directions_and_reports_unavailable(self):
        self.translator._request.side_effect = TimeoutError()
        raw = {"prompt": "角色向左走", "references": [{"alias": "角色"}]}
        result = await self.translator.prepare(raw)
        self.assertEqual(result["prompt"], raw["prompt"])
        self.assertEqual(result["h3_translation"]["status"], "unavailable")
        self.assertTrue(result["h3_preparation_warnings"])

    async def test_one_bad_field_never_commits_partial_translation(self):
        async def bad(values, settings):
            result = {key: value.replace("向左走", "walks left").replace("穿藍衣", "wears blue clothing") for key, value in values.items()}
            result["0"] = "A different person walks right."
            return result

        self.translator._request.side_effect = bad
        raw = {"prompt": "小明向左走", "references": [{"alias": "小明", "description": "穿藍衣"}]}
        result = await self.translator.prepare(raw)
        self.assertEqual(result["prompt"], raw["prompt"])
        self.assertEqual(result["references"], raw["references"])
        self.assertEqual(result["h3_translation"]["status"], "unavailable")
        self.assertFalse(self.translator._cache)

    async def test_disabled_preserves_input_without_request(self):
        raw = {"prompt": "角色向左走", "translate_directions": False}
        result = await self.translator.prepare(raw)
        self.assertEqual(result["prompt"], raw["prompt"])
        self.assertEqual(result["h3_translation"]["status"], "disabled")
        self.translator._request.assert_not_awaited()

    async def test_writer_and_translation_do_not_use_same_model_concurrently(self):
        lock = asyncio.Lock()
        self.app["scriptwriting"].queue_lock = lock
        request = self.translator._request.side_effect

        async def translate(values, settings):
            self.assertTrue(lock.locked())
            return await request(values, settings)

        self.translator._request.side_effect = translate
        await self.translator.prepare({"prompt": "向左走"})
        self.assertFalse(lock.locked())

    def test_locked_tags_times_and_english_name_boundaries(self):
        source = '[Shot 2] At 00:03.500, Alex向左走. A sign reads "Alex Cafe". (S1) <d>[Chinese] Alex你好。</d>'
        masked, protected = _mask(source, ["Alex", "Al"])
        restored = _restore(masked.replace("向左走", " walks left"), masked, protected)
        self.assertIn('[Shot 2] At 00:03.500, Alex walks left', restored)
        self.assertIn('"Alex Cafe"', restored)
        self.assertIn('<d>[Chinese] Alex你好。</d>', restored)
        untouched, _ = _mask("Alice walks alongside Al.", ["Al"])
        self.assertIn("Alice walks alongside", untouched)

    def test_deletion_duplication_and_reordering_of_locked_content_rejected(self):
        masked, protected = _mask('角色向左走 2 秒，<d>[Chinese] 嗨。</d>', ["角色"])
        keys = list(protected)
        english = masked.replace("向左走 ", " walks left for ").replace(" 秒，", " seconds, ")
        for invalid in (english.replace(keys[0], ""), english + keys[0],
                        english.replace(keys[0], "TEMP").replace(keys[1], keys[0]).replace("TEMP", keys[1])):
            with self.assertRaises(ValueError):
                _restore(invalid, masked, protected)

    def test_adjacent_reference_speaker_and_dialogue_tags_are_preserved(self):
        for source in ('<Subject 1>(S1)向左走', '[Shot 2]<Subject 1>向左走',
                       'Alex<d>[Chinese] 你好。</d>向左走'):
            masked, protected = _mask(source, ["Alex"])
            self.assertEqual(_restore(masked.replace("向左走", " walks left"), masked, protected),
                             source.replace("向左走", " walks left"))
        masked, protected = _mask("Alex向左走", ["Alex"])
        with self.assertRaises(ValueError):
            _restore(masked.replace("向左走", " walks left") + " H3KEEP123456ABCDEF X0000X".replace(" ", ""),
                     masked, protected)

    async def test_canonical_sound_and_reference_direction_fields_are_translated(self):
        result = await self.translator.prepare({"prompt": "The subject walks.",
            "overall_soundscape": "向左走", "non_diegetic_music": "穿藍衣",
            "reference_style": "穿藍衣", "replacement_target": "穿藍衣"})
        self.assertEqual(result["overall_soundscape"], "walks left")
        self.assertEqual(result["non_diegetic_music"], "wears blue clothing")
        self.assertEqual(result["reference_style"], "wears blue clothing")
        self.assertEqual(result["replacement_target"], "wears blue clothing")


class LocalRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_request_uses_cpu_and_releases_model(self):
        calls = []
        app = web.Application()

        async def tags(request):
            return web.json_response({"models": [{"name": "writer:latest"}]})

        async def chat(request):
            body = await request.json()
            calls.append(body)
            values = json.loads(body["messages"][-1]["content"])
            return web.json_response({"done": True, "done_reason": "stop",
                                      "message": {"content": json.dumps(values)}})

        async def unload(request):
            calls.append(await request.json())
            return web.json_response({"done": True})

        app.router.add_get("/api/tags", tags)
        app.router.add_post("/api/chat", chat)
        app.router.add_post("/api/generate", unload)
        server = TestServer(app)
        await server.start_server()
        try:
            translator = H3PromptTranslator({})
            sent = {"0": "H3KEEPABCDEF123456X0000X walks beside H3KEEPABCDEF123456X0001X."}
            result = await translator._request(sent,
                                                {"base_url": str(server.make_url("/")), "model": "writer"})
            self.assertEqual(result, sent)
            self.assertEqual(calls[0]["options"]["num_gpu"], 0)
            self.assertEqual(calls[0]["keep_alive"], 0)
            self.assertEqual(calls[0]["format"]["required"], ["0"])
            self.assertIn("H3KEEPABCDEF123456X0000X", calls[0]["format"]["properties"]["0"]["pattern"])
            self.assertIn("H3KEEPABCDEF123456X0001X", calls[0]["format"]["properties"]["0"]["pattern"])
            self.assertEqual(calls[-1], {"model": "writer", "keep_alive": 0, "stream": False})
        finally:
            await server.close()

    async def test_local_origin_cloud_model_never_receives_prompt(self):
        app = web.Application()
        chat = AsyncMock()

        async def tags(request):
            return web.json_response({"models": [{"name": "writer", "remote_host": "https://cloud.example"}]})

        app.router.add_get("/api/tags", tags)
        app.router.add_post("/api/chat", chat)
        server = TestServer(app)
        await server.start_server()
        try:
            with self.assertRaises(ValueError):
                await H3PromptTranslator({})._request({"0": "source"},
                    {"base_url": str(server.make_url("/")), "model": "writer"})
            chat.assert_not_awaited()
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
