"""Adapter choices persist across writing and stay frozen for automatic rendering."""
import copy
import json
import unittest
from unittest.mock import AsyncMock

from custom_loras import normalize_loras
from domain import compile_request, build_workflow, RequestError
from scriptwriting import ScriptwritingError, ScriptwritingManager, normalize_project
from tests import test_scriptwriting as scripts
from tests import test_script_video as videos
from tests import test_script_video_renderer as renderers


WUSHU = "h3studio_custom/wushu_h3_r2v_v7_2000.safetensors"
COMBAT = "h3studio_custom/H3_Combat_V2.safetensors"


def choices():
    return normalize_loras([dict(name=WUSHU, family="ref2va", strength=0.5),
                            dict(name=COMBAT, family="both", strength=0.3)])


class PromptTriggerTests(unittest.TestCase):
    def payload(self, **changes):
        return dict(mode="r2v", prompt="[Shot 1] Alice moves her hands.\nAlice: hello.",
                    references=[dict(alias="Alice", type="character", image_asset_ids=["a" * 32])],
                    prompt_profile="shortfilm", custom_loras=choices(), **changes)

    def test_required_visual_trigger_and_model_stack_preserve_raw_dialogue(self):
        raw = self.payload()
        original = copy.deepcopy(raw)
        compiled = compile_request(raw)
        self.assertEqual(raw, original)
        self.assertTrue(compiled.prompt.startswith("subject_definitions:"))
        self.assertIn("[Shot 1] wushu_action,", compiled.prompt.split("detailed_description:\n", 1)[1])
        self.assertIn("<d>[English] hello.</d>", compiled.prompt)
        self.assertNotIn("prfight2", compiled.prompt)
        workflow = build_workflow(compiled, {"a" * 32: "a.png"}, "test")
        loaded = [n["inputs"] for n in workflow.values() if n["class_type"] == "LoraLoaderModelOnly"]
        self.assertEqual([(n["lora_name"], n["strength_model"]) for n in loaded], [(WUSHU, 0.5), (COMBAT, 0.3)])

    def test_cue_in_speech_or_sign_does_not_replace_required_visual_cue(self):
        for prompt in ('[Shot 1] Alice says: "wushu_action".', '[Shot 1] A sign reads 「wushu_action」.',
                       '[Shot 1] A sign reads 「Outer「inner」wushu_action」.',
                       '[Shot 1] A sign reads “Outer “inner” wushu_action”.'):
            raw = self.payload()
            raw["prompt"] = prompt
            result = compile_request(raw).prompt
            self.assertIn("[Shot 1] wushu_action,", result)
            self.assertEqual(result.count("wushu_action"), 2)

    def test_existing_cue_not_duplicated_inactive_has_no_cue_and_wrong_mode_rejected(self):
        raw = self.payload()
        raw["prompt"] = "[Shot 1] wushu_action, Alice moves."
        self.assertEqual(compile_request(raw).prompt.count("wushu_action"), 1)
        for change in ({"enabled": False}, {"strength": 0}):
            raw = self.payload()
            raw["custom_loras"][0].update(change)
            self.assertNotIn("wushu_action", compile_request(raw).prompt)
        with self.assertRaisesRegex(RequestError, "不適用 FL2VA"):
            compile_request(dict(mode="t2v", prompt="A person moves.", custom_loras=choices()))


class ScriptLoraPersistenceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_save_reload_export_and_input_snapshot_keep_choices(self):
        project = await self.create(custom_loras=choices())
        job = await self.submit(project)
        result = await self.complete(job)
        self.assertEqual(result["input"]["project"]["custom_loras"], choices())
        request = json.dumps(self.chat_requests[0]["messages"])
        self.assertNotIn("safetensors", request)
        self.assertNotIn("wushu_action", request)
        exported = await (await self.client.get(f"/api/scripts/projects/{project['id']}/export?format=json")).json()
        self.assertEqual(exported["custom_loras"], choices())
        loaded = ScriptwritingManager({}, self.root)
        self.assertEqual(loaded.get_project(project["id"])["custom_loras"], choices())
        status = await (await self.client.get("/api/scripts/status")).json()
        self.assertTrue(status["custom_loras_supported"])

    async def test_old_frozen_snapshot_never_inherits_current_choice(self):
        project = await self.create(custom_loras=choices())
        frozen = copy.deepcopy(project)
        frozen.pop("custom_loras")
        job = self.manager.submit(dict(project_id=project["id"], action="screenplay"), project_override=frozen)
        result = await self.complete(job)
        self.assertEqual(result["input"]["project"]["custom_loras"], [])
        self.assertEqual(self.manager.get_project(project["id"])["custom_loras"], choices())

    async def test_import_uses_job_snapshot_and_rejects_active_wrong_family_before_writes(self):
        project = await self.create(duration=5, custom_loras=choices())
        job = {"id": "9" * 32, "status": "completed", "action": "storyboard", "storyboard": copy.deepcopy(scripts.STORYBOARD),
               "input": {"project": copy.deepcopy(project)}, "truncated": False}
        self.manager.jobs[job["id"]] = job
        self.manager.projects[project["id"]]["custom_loras"] = []
        imported = self.manager.import_shortfilm(job["id"])
        self.assertEqual(imported["project"]["custom_loras"], choices())
        bad = copy.deepcopy(job)
        bad["id"] = "8" * 32
        bad.pop("shortfilm_id")
        bad["input"]["project"]["custom_loras"] = [dict(name="fl.safetensors", family="fl2va", strength=0.5)]
        self.manager.jobs[bad["id"]] = bad
        before = copy.deepcopy(self.app["shortfilms"].projects)
        with self.assertRaises(ScriptwritingError):
            self.manager.import_shortfilm(bad["id"])
        self.assertNotIn("shortfilm_id", bad)
        self.assertEqual(self.app["shortfilms"].projects, before)
        bad["input"]["project"]["custom_loras"][0]["enabled"] = False
        self.assertFalse(self.manager.import_shortfilm(bad["id"])["project"]["custom_loras"][0]["enabled"])


class AutomaticLoraTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = videos.ScriptVideoAPITests.asyncSetUp
    asyncTearDown = videos.ScriptVideoAPITests.asyncTearDown
    fake_tags = scripts.ScriptAPITests.fake_tags
    fake_show = scripts.ScriptAPITests.fake_show
    fake_chat = videos.ScriptVideoAPITests.fake_chat
    fake_unload = scripts.ScriptAPITests.fake_unload
    fake_free = scripts.ScriptAPITests.fake_free
    redirect_target = scripts.ScriptAPITests.redirect_target
    create = scripts.ScriptAPITests.create
    project = videos.ScriptVideoAPITests.project
    start_pipeline = videos.ScriptVideoAPITests.start_pipeline
    wait_pipeline = videos.ScriptVideoAPITests.wait_pipeline

    async def test_pipeline_freezes_choices_and_each_render_plan_receives_them(self):
        project = await self.project(custom_loras=choices())
        pipeline = await self.start_pipeline(project)
        self.manager.projects[project["id"]]["custom_loras"] = []
        result = await self.wait_pipeline(pipeline)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["snapshot"]["custom_loras"], choices())
        self.assertEqual(result["shortfilm_snapshot"]["custom_loras"], choices())
        for part in result["render_plan"]:
            self.assertEqual(part["payload"]["custom_loras"], choices())
            self.assertIn("wushu_action", compile_request(part["payload"]).prompt)

    async def test_invalid_family_rejected_before_pipeline_or_writer_queue(self):
        project = await self.project(custom_loras=[dict(name="fl.safetensors", family="fl2va", strength=0.4)])
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 400, await response.text())
        self.assertFalse(self.pipeline_manager.pipelines)
        self.assertFalse(self.manager.jobs)

    async def test_swap_requires_source_video_before_pipeline_or_writer_queue(self):
        project = await self.project(custom_loras=[dict(name="h3studio_custom/h3_character_swap_pro4500_1000.safetensors",
                                                       family="ref2va", strength=1.0)])
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 400, await response.text())
        self.assertIn("來源影片", (await response.json())["error"])
        self.assertFalse(self.pipeline_manager.pipelines)
        self.assertFalse(self.manager.jobs)


class RendererLoraPreflightTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = renderers.ScriptVideoRendererTests.asyncSetUp
    asyncTearDown = renderers.ScriptVideoRendererTests.asyncTearDown

    async def test_missing_enabled_file_fails_but_disabled_and_zero_do_not_probe_catalog(self):
        project = dict(duration=5, character_references=[dict(name="阿青", type="character", image_asset_id="a" * 32, appearance="blue")], custom_loras=choices())
        self.comfy.list_loras = AsyncMock(return_value=[WUSHU])
        with self.assertRaisesRegex(ScriptwritingError, "H3_Combat_V2"):
            await self.renderer.preflight(project)
        self.app["jobs"].create.assert_not_called()
        self.comfy.list_loras.return_value = [name.replace("/", "\\") for name in (WUSHU, COMBAT)]
        await self.renderer.preflight(project)
        self.comfy.list_loras.reset_mock()
        project["custom_loras"][0]["enabled"] = False
        project["custom_loras"][1]["strength"] = 0
        await self.renderer.preflight(project)
        self.comfy.list_loras.assert_not_awaited()


class LegacyScriptLoraTests(unittest.TestCase):
    def test_old_project_defaults_empty_and_partial_edit_preserves_choices(self):
        self.assertEqual(normalize_project({})["custom_loras"], [])
        project = normalize_project(dict(custom_loras=choices()))
        self.assertEqual(normalize_project(dict(title="新標題"), project)["custom_loras"], choices())
        with self.assertRaises(ScriptwritingError):
            normalize_project(dict(custom_loras=[dict(name="../bad.safetensors")]))
