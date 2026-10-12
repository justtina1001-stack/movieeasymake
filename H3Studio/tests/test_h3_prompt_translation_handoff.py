"""Preview-to-submit fallback reuse is server-verified, temporary, and request-specific."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from h3_prompt_translation import H3PromptTranslator, payload_digest


class FallbackHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {"base_url": "http://127.0.0.1:11434", "model": "local-writer",
                         "temperature": 0.8, "num_ctx": 8192, "num_predict": 4096}
        self.app = {"scriptwriting": SimpleNamespace(settings=self.settings)}
        self.translator = H3PromptTranslator(self.app)
        self.translator._request = AsyncMock(side_effect=TimeoutError("mock translation timeout"))
        self.raw = {"prompt": "小明向左走。小明說：「您好。」", "seed": 1, "duration": 5,
                    "references": [{"alias": "小明", "type": "character", "image_asset_ids": ["a" * 32]}],
                    "media_preflight": {"warnings": ["derived stream warning"],
                                        "normalized_videos": {"v": {"asset_id": "b" * 32, "frames": 124}}}}

    async def test_same_prepared_fallback_does_not_retry_and_retains_server_metadata(self):
        original = copy.deepcopy(self.raw)
        fallback = await self.translator.prepare(self.raw)
        self.assertEqual(self.raw, original)
        self.assertEqual(fallback["h3_translation"]["status"], "unavailable")
        self.assertTrue(fallback["h3_translation"]["fallback_handoff"])
        submitted = copy.deepcopy(fallback)
        submitted["h3_translation"].update(status="translated", source_texts={"prompt": "client replacement"})
        submitted["h3_preparation_warnings"] = []
        reused = await self.translator.prepare(submitted)
        self.assertEqual(reused["prompt"], fallback["prompt"])
        self.assertIn("<d>[Chinese] 您好。</d>", reused["prompt"])
        self.assertEqual(reused["h3_translation"], fallback["h3_translation"])
        self.assertEqual(reused["h3_preparation_warnings"], fallback["h3_preparation_warnings"])
        self.translator._request.assert_awaited_once()

    async def test_new_preview_missing_forged_and_other_translator_tokens_retry_normally(self):
        fallback = await self.translator.prepare(self.raw)
        await self.translator.prepare(self.raw)
        without_token = copy.deepcopy(fallback)
        without_token["h3_translation"].pop("fallback_handoff")
        await self.translator.prepare(without_token)
        forged = copy.deepcopy(fallback)
        forged["h3_translation"]["fallback_handoff"] = "not-issued-by-this-translator"
        await self.translator.prepare(forged)
        self.assertEqual(self.translator._request.await_count, 4)
        other = H3PromptTranslator(self.app)
        other._request = AsyncMock(side_effect=TimeoutError())
        await other.prepare(fallback)
        other._request.assert_awaited_once()

    async def test_content_seed_reference_or_media_fact_changes_do_not_reuse(self):
        changes = [lambda value: value.update(prompt="小明向右走"),
                   lambda value: value.update(seed=2),
                   lambda value: value.update(duration=10),
                   lambda value: value["references"][0].update(image_asset_ids=["c" * 32]),
                   lambda value: value["media_preflight"]["normalized_videos"]["v"].update(frames=141)]
        fallback = await self.translator.prepare(self.raw)
        for change in changes:
            edited = copy.deepcopy(fallback)
            change(edited)
            await self.translator.prepare(edited)
        self.assertEqual(self.translator._request.await_count, len(changes) + 1)

    async def test_model_origin_and_other_writer_settings_changes_do_not_reuse(self):
        fallback = await self.translator.prepare(self.raw)
        for key, value in (("model", "another-writer"), ("base_url", "http://localhost:11434"),
                           ("num_ctx", 16384), ("temperature", 0.5)):
            self.app["scriptwriting"].settings = self.settings | {key: value}
            await self.translator.prepare(fallback)
        self.assertEqual(self.translator._request.await_count, 5)

    async def test_handoff_expiry_does_not_extend_when_reused(self):
        with patch("h3_prompt_translation.time.monotonic", return_value=100):
            fallback = await self.translator.prepare(self.raw)
        with patch("h3_prompt_translation.time.monotonic", return_value=219):
            await self.translator.prepare(fallback)
        self.translator._request.assert_awaited_once()
        with patch("h3_prompt_translation.time.monotonic", return_value=220):
            renewed = await self.translator.prepare(fallback)
        self.assertEqual(self.translator._request.await_count, 2)
        self.assertNotEqual(renewed["h3_translation"]["fallback_handoff"], fallback["h3_translation"]["fallback_handoff"])

    async def test_handoff_storage_is_bounded_and_oldest_tokens_cannot_suppress_retry(self):
        with patch("h3_prompt_translation._FALLBACK_HANDOFF_LIMIT", 2):
            first = await self.translator.prepare(self.raw)
            await self.translator.prepare(self.raw | {"seed": 2})
            await self.translator.prepare(self.raw | {"seed": 3})
            self.assertEqual(len(self.translator._fallback_handoffs), 2)
            await self.translator.prepare(first)
            self.assertEqual(self.translator._request.await_count, 4)
            self.assertEqual(len(self.translator._fallback_handoffs), 2)

    async def test_only_derived_media_warning_changes_are_ignored_for_handoff(self):
        fallback = await self.translator.prepare(self.raw)
        inspected = copy.deepcopy(fallback)
        inspected["media_preflight"]["warnings"] = []
        # The global frozen-source digest remains strict.
        self.assertNotEqual(payload_digest(inspected), payload_digest(fallback))
        reused = await self.translator.prepare(inspected)
        self.assertEqual(reused["media_preflight"]["warnings"], [])
        self.assertEqual(reused["h3_preparation_warnings"], fallback["h3_preparation_warnings"])
        self.translator._request.assert_awaited_once()

    async def test_a_new_preview_can_translate_after_an_unavailable_result(self):
        fallback = await self.translator.prepare({"prompt": "向左走", "seed": 1})
        self.translator._request.side_effect = None
        self.translator._request.return_value = {"0": "Walk left."}
        translated = await self.translator.prepare({"prompt": "向左走", "seed": 1})
        self.assertEqual(translated["h3_translation"]["status"], "translated")
        self.assertEqual(translated["prompt"], "Walk left.")
        self.assertEqual(self.translator._request.await_count, 2)
        self.assertNotIn("fallback_handoff", translated["h3_translation"])
        self.assertEqual(fallback["h3_translation"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
