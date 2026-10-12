"""Storyboard conversion keeps complete manuscripts within bounded model context."""
from __future__ import annotations

import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web

from scriptwriting import (
    DEFAULT_SETTINGS, ScriptwritingError, ScriptwritingManager, _input_token_estimate,
    _model_context_limit, build_messages, fit_context_settings,
)
from tests import test_scriptwriting as fixture


def chinese_messages(count, suffix=""):
    return [{"role": "user", "content": "中" * count + suffix}]


class StoryboardContextTests(unittest.TestCase):
    def test_7260_token_manuscript_grows_context_and_keeps_full_output(self):
        messages = chinese_messages(4477)
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        source = copy.deepcopy(messages)
        requested = copy.deepcopy(settings)
        self.assertEqual(_input_token_estimate(messages), 7260)

        fitted = fit_context_settings(messages, settings, action="storyboard")

        self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (12288, 4096))
        self.assertEqual(messages, source)
        self.assertEqual(settings, requested)
        self.assertIsNot(fitted, settings)

    def test_context_is_allocated_at_2048_steps_only_when_needed(self):
        cases = [(2500, "", 4096, 8192), (2500, "a", 4097, 10240),
                 (3780, "", 6144, 10240), (3780, "a", 6145, 12288)]
        for count, suffix, estimate, context in cases:
            with self.subTest(estimate=estimate):
                messages = chinese_messages(count, suffix)
                self.assertEqual(_input_token_estimate(messages), estimate)
                fitted = fit_context_settings(messages, DEFAULT_SETTINGS, action="storyboard")
                self.assertEqual(fitted["num_ctx"], context)
                self.assertEqual(fitted["num_predict"], 4096)

    def test_sixteen_k_boundary_preserves_at_least_1024_output_tokens(self):
        messages = chinese_messages(9540)
        self.assertEqual(_input_token_estimate(messages), 15360)
        fitted = fit_context_settings(messages, DEFAULT_SETTINGS, action="storyboard")
        self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (16384, 1024))

        too_long = chinese_messages(9540, "a")
        self.assertEqual(_input_token_estimate(too_long), 15361)
        original = copy.deepcopy(too_long)
        with self.assertRaises(ScriptwritingError):
            fit_context_settings(too_long, DEFAULT_SETTINGS, action="storyboard")
        self.assertEqual(too_long, original)

    def test_small_requested_output_is_preserved_at_the_auto_context_limit(self):
        messages = chinese_messages(9860)
        self.assertEqual(_input_token_estimate(messages), 15872)
        requested = {**DEFAULT_SETTINGS, "num_predict": 512}
        fitted = fit_context_settings(messages, requested, action="storyboard")
        self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (16384, 512))

    def test_explicit_large_context_is_never_lowered_to_the_auto_ceiling(self):
        for capacity in (16384, 32768):
            with self.subTest(capacity=capacity):
                settings = {**DEFAULT_SETTINGS, "num_ctx": capacity}
                fitted = fit_context_settings(chinese_messages(4477), settings, action="storyboard")
                self.assertEqual(fitted, settings)
        settings = {**DEFAULT_SETTINGS, "num_ctx": 32768}
        fitted = fit_context_settings(chinese_messages(12000), settings, action="storyboard")
        self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (32768, 4096))

    def test_non_storyboard_actions_keep_existing_output_only_fitting(self):
        messages = chinese_messages(3690)
        self.assertEqual(_input_token_estimate(messages), 6000)
        for action in (None, "outline", "screenplay", "rewrite", "appearance"):
            with self.subTest(action=action):
                fitted = fit_context_settings(messages, DEFAULT_SETTINGS, action=action)
                self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (8192, 2192))
                with self.assertRaises(ScriptwritingError):
                    fit_context_settings(chinese_messages(4477), DEFAULT_SETTINGS, action=action)

    def test_model_hardcap_fails_or_reduces_output_without_changing_input(self):
        messages = chinese_messages(4477)
        source = copy.deepcopy(messages)
        settings = {**DEFAULT_SETTINGS, "num_ctx": 12288}
        with self.assertRaises(ScriptwritingError):
            fit_context_settings(messages, settings, action="storyboard", context_limit=8192)
        fitted = fit_context_settings(messages, settings, action="storyboard", context_limit=10240)
        self.assertEqual((fitted["num_ctx"], fitted["num_predict"]), (10240, 2980))
        self.assertEqual(messages, source)
        self.assertEqual(settings["num_ctx"], 12288)
        self.assertEqual(settings["num_predict"], 4096)


class ModelContextMetadataTests(unittest.TestCase):
    def test_architecture_specific_limit_takes_priority_over_other_metadata(self):
        shown = {"model_info": {"unrelated.context_length": 8192,
                                "general.architecture": "llama", "llama.context_length": 32768}}
        self.assertEqual(_model_context_limit(shown), 32768)

    def test_suffix_fallback_ignores_non_integer_boolean_and_invalid_limits(self):
        shown = {"model_info": {"general.architecture": "missing", "a.context_length": True,
                                "b.context_length": "8192", "c.context_length": -1,
                                "d.context_length": 0, "gemma.context_length": 10240}}
        self.assertEqual(_model_context_limit(shown), 10240)
        for model_info in (None, [], {}, {"context_length": 8192}, {"a.context_length": False}):
            with self.subTest(model_info=model_info):
                self.assertIsNone(_model_context_limit({"model_info": model_info}))


class StoryboardContextAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ScriptAPITests.asyncSetUp(self)
        self.show["model_info"] = {"general.architecture": "qwen3", "qwen3.context_length": 40960}

    asyncTearDown = fixture.ScriptAPITests.asyncTearDown
    fake_tags = fixture.ScriptAPITests.fake_tags
    fake_show = fixture.ScriptAPITests.fake_show
    fake_chat = fixture.ScriptAPITests.fake_chat
    fake_unload = fixture.ScriptAPITests.fake_unload
    redirect_target = fixture.ScriptAPITests.redirect_target
    fake_free = fixture.ScriptAPITests.fake_free
    create = fixture.ScriptAPITests.create
    submit = fixture.ScriptAPITests.submit
    complete = fixture.ScriptAPITests.complete

    async def long_project(self, target=7260):
        """Exercise real message construction using a synthetic Chinese manuscript."""
        project = await self.create(duration=5, content="原稿")
        initial = _input_token_estimate(build_messages(project, "storyboard", ""))
        self.assertLess(initial, target)
        project["content"] += "中" * int((target - initial) / 1.6)
        while _input_token_estimate(build_messages(project, "storyboard", "")) < target:
            project["content"] += "a"
        self.assertEqual(_input_token_estimate(build_messages(project, "storyboard", "")), target)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": project["revision"], "content": project["content"]})
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    def assert_budget(self, job, *, context=12288, output=4096, output_adjusted=False):
        self.assertTrue(job["context_budget_adjusted"])
        self.assertEqual(job["output_budget_adjusted"], output_adjusted)
        expected = {"estimated_input_tokens": 7260, "requested_num_ctx": 8192,
                    "requested_num_predict": 4096}
        self.assertEqual({key: job["context_budget"][key] for key in expected}, expected)
        self.assertEqual(job["input"]["settings"]["num_ctx"], context)
        self.assertEqual(job["input"]["settings"]["num_predict"], output)

    async def test_submit_archives_adjustment_and_restart_preserves_source_and_budget(self):
        project = await self.long_project()
        original = copy.deepcopy(project)
        settings = copy.deepcopy(self.manager.settings)
        with patch.object(self.manager, "_run", new=AsyncMock()) as run:
            job = self.manager.submit({"project_id": project["id"], "action": "storyboard"})
            await asyncio.sleep(0)
        self.assert_budget(job)
        run.assert_awaited_once()
        self.assertEqual(run.await_args.args[1], build_messages(original, "storyboard", ""))
        self.assertEqual(job["input"]["project"], original)
        self.assertEqual(self.manager.settings, settings)
        self.assertEqual(self.manager.get_project(project["id"]), original)
        archived = json.loads((self.manager.history_directory / f"{job['id']}.json").read_text(encoding="utf-8"))
        self.assert_budget(archived)
        restarted = ScriptwritingManager(web.Application(), self.root)
        recovered = restarted.get_job(job["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assert_budget(recovered)
        self.assertEqual(recovered["input"]["project"], original)
        self.assertEqual(restarted.get_project(project["id"]), original)

    async def test_api_chat_uses_auto_context_and_complete_manuscript(self):
        project = await self.long_project()
        original = copy.deepcopy(project)
        self.reply = json.dumps(fixture.STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assert_budget(result)
        self.assertEqual(result["context_budget"]["model_context_limit"], 40960)
        self.assertEqual(len(self.chat_requests), 1)
        chat = self.chat_requests[0]
        self.assertEqual(chat["options"], {"num_ctx": 12288, "num_predict": 4096,
                                          "temperature": DEFAULT_SETTINGS["temperature"]})
        self.assertEqual(chat["messages"], build_messages(original, "storyboard", ""))
        self.assertEqual(result["input"]["project"], original)
        self.assertEqual(self.manager.get_project(project["id"]), original)
        self.assertEqual(self.manager.settings["num_ctx"], 8192)
        self.assertEqual(self.manager.settings["num_predict"], 4096)
        self.assert_budget(ScriptwritingManager(web.Application(), self.root).get_job(result["id"]))

    async def test_model_8192_hardcap_fails_before_chat_and_preserves_manuscript(self):
        project = await self.long_project()
        self.show["model_info"] = {"general.architecture": "llama", "llama.context_length": 8192}
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("8,192", result["error"])
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])
        self.assertEqual(result["input"]["project"], project)
        self.assertEqual(self.manager.get_project(project["id"]), project)
        self.assert_budget(ScriptwritingManager(web.Application(), self.root).get_job(result["id"]))

    async def test_model_10240_hardcap_reduces_output_and_persists_actual_chat_options(self):
        project = await self.long_project()
        self.show["model_info"] = {"general.architecture": "llama", "llama.context_length": 10240}
        self.reply = json.dumps(fixture.STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assert_budget(result, context=10240, output=2980, output_adjusted=True)
        self.assertEqual(self.chat_requests[0]["options"]["num_ctx"], 10240)
        self.assertEqual(self.chat_requests[0]["options"]["num_predict"], 2980)
        self.assertEqual(self.chat_requests[0]["messages"], build_messages(project, "storyboard", ""))
        archived = json.loads((self.manager.history_directory / f"{result['id']}.json").read_text(encoding="utf-8"))
        self.assert_budget(archived, context=10240, output=2980, output_adjusted=True)
        self.assertEqual(self.manager.settings["num_ctx"], 8192)
        self.assertEqual(self.manager.settings["num_predict"], 4096)

    async def test_non_step_model_12000_hardcap_clamps_context_without_cutting_output(self):
        project = await self.long_project()
        self.show["model_info"] = {"general.architecture": "llama", "llama.context_length": 12000}
        self.reply = json.dumps(fixture.STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assert_budget(result, context=12000)
        self.assertEqual(result["context_budget"]["model_context_limit"], 12000)
        self.assertEqual(self.chat_requests[0]["options"]["num_ctx"], 12000)
        self.assertEqual(self.chat_requests[0]["options"]["num_predict"], 4096)
        self.assertEqual(self.chat_requests[0]["messages"], build_messages(project, "storyboard", ""))

    async def test_unknown_or_malformed_model_capacity_fails_before_freeing_or_chat(self):
        project = await self.long_project()
        for model_info in (None, [], {}, {"qwen3.context_length": True},
                           {"qwen3.context_length": "40960"}, {"qwen3.context_length": 0}):
            with self.subTest(model_info=model_info):
                self.show["model_info"] = model_info
                with patch.object(self.manager, "_release_idle_comfy", new=AsyncMock()) as release:
                    result = await self.complete(await self.submit(project, action="storyboard"))
                self.assertEqual(result["status"], "failed", result)
                self.assertIn("未提供有效容量資訊", result["error"])
                release.assert_not_awaited()
                self.assertIsNone(result["context_budget"]["model_context_limit"])
                self.assertEqual(result["input"]["project"], project)
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])
        self.assertEqual(self.manager.get_project(project["id"]), project)

    async def test_unknown_model_capacity_falls_back_to_requested_context_when_input_fits(self):
        project = await self.long_project(target=6500)
        self.show.pop("model_info", None)
        self.reply = json.dumps(fixture.STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["context_budget_adjusted"])
        self.assertTrue(result["output_budget_adjusted"])
        self.assertIsNone(result["context_budget"]["model_context_limit"])
        self.assertEqual(result["context_budget"]["estimated_input_tokens"], 6500)
        self.assertEqual(result["input"]["settings"]["num_ctx"], 8192)
        self.assertEqual(result["input"]["settings"]["num_predict"], 1692)
        self.assertEqual(self.chat_requests[0]["options"]["num_ctx"], 8192)
        self.assertEqual(self.chat_requests[0]["options"]["num_predict"], 1692)
        self.assertEqual(self.chat_requests[0]["messages"], build_messages(project, "storyboard", ""))
        self.assertEqual(self.manager.get_project(project["id"]), project)
        self.assertEqual(self.manager.settings["num_ctx"], 8192)
        self.assertEqual(self.manager.settings["num_predict"], 4096)

    async def test_api_excessive_context_is_rejected_without_scheduling_or_truncation(self):
        project = await self.long_project(target=15361)
        with patch.object(self.manager, "_run", new=AsyncMock()) as run:
            response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": "storyboard"})
        self.assertEqual(response.status, 400, await response.text())
        run.assert_not_called()
        self.assertEqual(self.manager.jobs, {})
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.manager.get_project(project["id"]), project)


if __name__ == "__main__":
    unittest.main()
