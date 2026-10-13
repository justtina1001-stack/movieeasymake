"""Safe translation failure reasons without local inference or real HTTP calls."""
import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from h3_prompt_translation import H3PromptTranslator, _WARNING, _failure_diagnostics


PRIVATE_ERROR = "http://user:password@localhost/private?token=secret Model output: INPUT_PRIVATE"
DIAGNOSTIC_FIELDS = ("reason_code", "error_message", "elapsed_seconds")


class FakeResponse:
    def __init__(self, data=None, *, status=200, json_error=None):
        self.status = status
        self.data = data
        self.json_error = json_error
        self.content_length = None
        self.content = SimpleNamespace(iter_chunked=self.chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.data

    async def chunks(self, size):
        yield json.dumps(self.data).encode()

    async def read(self):
        return b"{}"


class FakeSession:
    def __init__(self, *, tags=None, chat=None):
        self.tags = tags or FakeResponse({"models": [{"name": "writer"}]})
        self.chat = chat or FakeResponse({"done": True, "message": {"content": '{"0":"walks left"}'}})
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **options):
        self.calls.append(("tags", options))
        return self.tags

    def post(self, url, **options):
        kind = "chat" if url.endswith("/api/chat") else "unload"
        self.calls.append((kind, options))
        return self.chat if kind == "chat" else FakeResponse({"done": True})


class FailureDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {"base_url": "http://127.0.0.1:11434", "model": "writer"}
        self.app = {"scriptwriting": SimpleNamespace(settings=self.settings, queue_lock=asyncio.Lock())}
        self.translator = H3PromptTranslator(self.app)
        self.raw = {"prompt": "角色向左走", "references": [{"alias": "角色", "description": "穿藍衣"}]}

    def assert_diagnostics(self, result, reason):
        metadata = result["h3_translation"]
        self.assertEqual(metadata["status"], "unavailable")
        self.assertEqual(metadata["reason_code"], reason)
        self.assertIsInstance(metadata["error_message"], str)
        self.assertTrue(metadata["error_message"])
        self.assertIsInstance(metadata["elapsed_seconds"], (float, int))
        self.assertGreaterEqual(metadata["elapsed_seconds"], 0)
        diagnostics = json.dumps({key: metadata[key] for key in DIAGNOSTIC_FIELDS})
        for private in (PRIVATE_ERROR, "password", "token=", "INPUT_PRIVATE", "localhost/private"):
            self.assertNotIn(private, diagnostics)
        self.assertEqual(result["h3_preparation_warnings"], [_WARNING])
        self.assertEqual(result["prompt"], self.raw["prompt"])
        self.assertEqual(result["references"], self.raw["references"])
        return metadata

    async def failure(self, error):
        self.translator._request = AsyncMock(side_effect=error)
        original = copy.deepcopy(self.raw)
        result = await self.translator.prepare(self.raw)
        self.assertEqual(self.raw, original)
        return result

    async def test_timeout_has_read_limit_and_measured_elapsed_without_exception_text(self):
        result = await self.failure(TimeoutError(PRIVATE_ERROR))
        metadata = self.assert_diagnostics(result, "timeout")
        self.assertIn("CPU", metadata["error_message"])
        self.assertIn("175", metadata["error_message"])
        self.assertFalse(self.app["scriptwriting"].queue_lock.locked())

    def test_elapsed_seconds_uses_monotonic_time_and_is_nonnegative(self):
        with patch("h3_prompt_translation.time", SimpleNamespace(monotonic=lambda: 275.25)):
            result = _failure_diagnostics(TimeoutError(PRIVATE_ERROR), 100)
            self.assertEqual(result["elapsed_seconds"], 175.25)
            self.assertEqual(_failure_diagnostics(TimeoutError(), 300)["elapsed_seconds"], 0)

    async def test_connection_failure_is_model_unavailable(self):
        result = await self.failure(aiohttp.ClientConnectionError(PRIVATE_ERROR))
        self.assert_diagnostics(result, "model_unavailable")

    async def test_http_error_classification_does_not_serialize_url_or_error_body(self):
        for status, reason in ((503, "model_unavailable"), (404, "model_unavailable"), (500, "local_error")):
            with self.subTest(status=status):
                error = aiohttp.ClientResponseError(SimpleNamespace(real_url=PRIVATE_ERROR), (),
                                                    status=status, message=PRIVATE_ERROR)
                self.assert_diagnostics(await self.failure(error), reason)

    async def test_malformed_response_content_type_is_invalid_response(self):
        error = aiohttp.ContentTypeError(SimpleNamespace(real_url=PRIVATE_ERROR), (), message=PRIVATE_ERROR)
        self.assert_diagnostics(await self.failure(error), "invalid_response")

    async def test_truncated_response_transport_is_invalid_response(self):
        self.assert_diagnostics(await self.failure(aiohttp.ClientPayloadError(PRIVATE_ERROR)), "invalid_response")

    async def test_local_os_failure_has_safe_local_error(self):
        self.assert_diagnostics(await self.failure(OSError(PRIVATE_ERROR)), "local_error")

    async def test_invalid_local_origin_is_a_safe_configuration_error(self):
        self.settings["base_url"] = PRIVATE_ERROR
        with patch("h3_prompt_translation.aiohttp.ClientSession") as session:
            result = await self.translator.prepare(self.raw)
        self.assert_diagnostics(result, "local_error")
        session.assert_not_called()

    async def test_aiohttp_invalid_url_is_configuration_failure_not_model_response(self):
        self.assert_diagnostics(await self.failure(aiohttp.InvalidURL(PRIVATE_ERROR)), "local_error")

    async def test_invalid_placeholder_rejects_all_fields_and_keeps_cache_empty(self):
        async def invalid(values, settings):
            translated = {key: value.replace("向左走", "walks left").replace("穿藍衣", "wears blue clothing")
                          for key, value in values.items()}
            translated["0"] = PRIVATE_ERROR
            return translated

        self.translator._request = AsyncMock(side_effect=invalid)
        result = await self.translator.prepare(self.raw)
        self.assert_diagnostics(result, "invalid_response")
        self.assertFalse(self.translator._cache)

    async def test_non_object_translation_uses_safe_fallback(self):
        self.translator._request = AsyncMock(return_value=[PRIVATE_ERROR])
        self.assert_diagnostics(await self.translator.prepare(self.raw), "invalid_response")

    async def test_handoff_restores_server_diagnostics_after_client_tampering(self):
        fallback = await self.failure(TimeoutError(PRIVATE_ERROR))
        original = copy.deepcopy(fallback)
        submitted = copy.deepcopy(fallback)
        submitted["h3_translation"].update(reason_code="local_error", error_message=PRIVATE_ERROR,
                                           elapsed_seconds=9999, source_texts={"prompt": PRIVATE_ERROR})
        reused = await self.translator.prepare(submitted)
        self.assertEqual(reused["h3_translation"], original["h3_translation"])
        self.assertEqual(reused["h3_preparation_warnings"], original["h3_preparation_warnings"])
        self.translator._request.assert_awaited_once()

    async def test_disabled_and_successful_translation_have_no_failure_diagnostics(self):
        disabled = await self.translator.prepare(self.raw | {"translate_directions": False})
        self.assertEqual(disabled["h3_translation"], {"status": "disabled"})

        async def translated(values, settings):
            return {key: value.replace("向左走", "walks left").replace("穿藍衣", "wears blue clothing")
                    for key, value in values.items()}

        self.translator._request = AsyncMock(side_effect=translated)
        result = await self.translator.prepare(self.raw)
        self.assertEqual(result["h3_translation"]["status"], "translated")
        for key in DIAGNOSTIC_FIELDS:
            self.assertNotIn(key, result["h3_translation"])

    async def test_cancellation_still_propagates_without_fallback_or_locked_writer(self):
        self.translator._request = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.translator.prepare(self.raw)
        self.assertFalse(self.translator._fallback_handoffs)
        self.assertFalse(self.translator._cache)
        self.assertFalse(self.app["scriptwriting"].queue_lock.locked())


class RequestPhaseDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    setUp = FailureDiagnosticsTests.setUp
    assert_diagnostics = FailureDiagnosticsTests.assert_diagnostics

    async def request_failure(self, session):
        with patch("h3_prompt_translation.aiohttp.ClientSession", return_value=session) as constructor:
            result = await self.translator.prepare(self.raw)
        self.assertEqual(constructor.call_args.kwargs["timeout"].total, 180)
        self.assertEqual(constructor.call_args.kwargs["timeout"].sock_read, 175)
        self.assertEqual(constructor.call_args.kwargs["timeout"].sock_connect, 3)
        return result

    async def test_missing_installed_model_has_availability_reason_without_chat(self):
        session = FakeSession(tags=FakeResponse({"models": []}))
        result = await self.request_failure(session)
        self.assert_diagnostics(result, "model_unavailable")
        self.assertEqual([kind for kind, _ in session.calls], ["tags"])

    async def test_unavailable_inventory_endpoint_has_availability_reason(self):
        result = await self.request_failure(FakeSession(tags=FakeResponse(status=503)))
        self.assert_diagnostics(result, "model_unavailable")

    async def test_invalid_inventory_has_response_reason(self):
        result = await self.request_failure(FakeSession(tags=FakeResponse({"models": PRIVATE_ERROR})))
        self.assert_diagnostics(result, "invalid_response")

    async def test_chat_http_failure_is_sanitized_and_still_releases_model(self):
        for status, reason in ((503, "model_unavailable"), (500, "local_error")):
            with self.subTest(status=status):
                session = FakeSession(chat=FakeResponse({"error": PRIVATE_ERROR}, status=status))
                result = await self.request_failure(session)
                self.assert_diagnostics(result, reason)
                self.assertEqual([kind for kind, _ in session.calls], ["tags", "chat", "unload"])
                self.assertEqual(session.calls[1][1]["json"]["options"]["num_gpu"], 0)
                self.assertEqual(session.calls[-1][1]["json"]["keep_alive"], 0)

    async def test_invalid_chat_protocol_uses_fallback_and_never_records_model_body(self):
        for data in ([PRIVATE_ERROR], {"done": True, "message": [PRIVATE_ERROR]},
                     {"done": True, "message": {"content": PRIVATE_ERROR}}):
            with self.subTest(data=data):
                session = FakeSession(chat=FakeResponse(data))
                result = await self.request_failure(session)
                self.assert_diagnostics(result, "invalid_response")
                self.assertEqual(session.calls[-1][0], "unload")


if __name__ == "__main__":
    unittest.main()
