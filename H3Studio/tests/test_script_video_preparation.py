"""Prepared H3 recipes reuse English prose without weakening frozen-source checks."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_script_video_compiler_resume as frozen
from scriptwriting import ScriptwritingError


class PreparedPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = frozen.FrozenCompilerResumeTests()
        self.fixture.setUp()
        self.fixture.app["comfy"] = SimpleNamespace(mode="local", base_url="http://127.0.0.1:8188",
            model_inventory=AsyncMock(return_value={"ref2va": True, "text_encoder": True,
                "audio_vae": True, "video_vae_fp16": True}))

        async def prepare(raw):
            result = copy.deepcopy(raw)
            result["prompt"] = "Prepared English direction. " + result["prompt"]
            result["h3_preparation_warnings"] = ["Synthetic preparation"]
            return result

        self.prepare = AsyncMock(side_effect=prepare)
        self.fixture.app["prepare_generation_payload"] = self.prepare

    async def plan(self):
        fixture = self.fixture
        result = await fixture.renderer.compile_all(fixture.project_id, fixture.pipeline_id)
        fixture.pipeline["render_plan"] = result
        return result[1]

    async def test_preflight_prepared_payload_is_reused_after_strict_source_match(self):
        fixture = self.fixture
        manifest = await self.plan()
        await fixture.renderer.render_shot(fixture.project_id, fixture.scene_id, fixture.shot_id,
            fixture.pipeline_id, expected_payload=manifest["payload"])
        self.assertEqual(self.prepare.await_count, 2)
        _, submitted = fixture.jobs.create.call_args.args
        self.assertEqual(submitted, manifest["prepared_payload"])
        self.assertIsNot(submitted, manifest["prepared_payload"])
        self.assertTrue(manifest["prepared_source_digest"])

    async def test_modified_source_is_rejected_before_preparation_or_submission(self):
        fixture = self.fixture
        manifest = await self.plan()
        fixture.current["scenes"][0]["shots"][1]["action"] = "The subject runs instead of walking."
        with self.assertRaises(ScriptwritingError):
            await fixture.renderer.render_shot(fixture.project_id, fixture.scene_id, fixture.shot_id,
                fixture.pipeline_id, expected_payload=manifest["payload"])
        self.assertEqual(self.prepare.await_count, 2)
        fixture.jobs.create.assert_not_called()

    async def test_missing_prepared_digest_reprepares_the_frozen_source(self):
        fixture = self.fixture
        manifest = await self.plan()
        manifest["prepared_source_digest"] = "invalid"
        await fixture.renderer.render_shot(fixture.project_id, fixture.scene_id, fixture.shot_id,
            fixture.pipeline_id, expected_payload=manifest["payload"])
        self.assertEqual(self.prepare.await_count, 3)


if __name__ == "__main__":
    unittest.main()
