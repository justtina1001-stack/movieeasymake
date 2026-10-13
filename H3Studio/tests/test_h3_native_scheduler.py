"""Native scheduler defaults and source-backed legacy resume, without inference."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from domain import build_workflow, compile_request, required_asset_ids
from script_video_renderer import ScriptVideoRenderer
from scriptwriting import ScriptwritingError
from shortfilm import compile_shot_payload, new_asset, new_project, new_scene, new_shot


MODES = ("t2v", "fl2va", "r2v", "replace", "extend", "symbol_loop", "popup_panel", "mg_animation")
IMAGE_A, IMAGE_B, IMAGE_C, VIDEO_A = (letter * 32 for letter in "abed")


def request_for(mode):
    payload = {"mode": mode, "prompt": "The subject moves gently, then settles.",
               "quality_mode": "native", "duration": 5, "aspect_ratio": "16:9",
               "megapixels": 0.4, "seed": 42, "motion_profile": "none"}
    if mode in {"fl2va", "extend", "symbol_loop"}:
        payload["first_image_asset_id"] = IMAGE_A
    if mode in {"r2v", "replace"}:
        payload["references"] = [{"alias": "Hero", "type": "character", "image_asset_ids": [IMAGE_A]}]
    if mode == "replace":
        payload["references"][0]["video_asset_id"] = VIDEO_A
    if mode in {"popup_panel", "mg_animation"}:
        payload["references"] = [
            {"alias": "背景圖", "type": "background", "image_asset_ids": [IMAGE_A]},
            {"alias": "面板" if mode == "popup_panel" else "轉輪帶", "type": "object",
             "image_asset_ids": [IMAGE_B]},
        ]
        if mode == "mg_animation":
            payload["references"].append({"alias": "角色", "type": "character", "image_asset_ids": [IMAGE_C]})
    return payload


def graph_for(compiled):
    uploaded = {asset: f"h3/{asset}.{'mp4' if asset == VIDEO_A else 'png'}"
                for asset in required_asset_ids(compiled)}
    return build_workflow(compiled, uploaded, "scheduler-regression")


def node(graph, class_type):
    return next(value for value in graph.values() if value["class_type"] == class_type)


def shortfilm_case(with_reference=True):
    character = new_asset("character", "Hero")
    character.update(description="A blue round robot.", image_asset_ids=[IMAGE_A])
    scene = new_scene()
    shot = new_shot()
    shot.update(duration=5, action="Hero waves, then returns to rest.", seed=42,
                asset_ids=[character["id"]] if with_reference else [])
    scene["shots"] = [shot]
    project = new_project("Scheduler test")
    project.update(assets=[character] if with_reference else [], scenes=[scene], target_duration=5)
    return project, scene, shot


class NativeSchedulerTests(unittest.TestCase):
    def test_all_modes_default_to_official_simple_twenty_step_native_graph(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                compiled = compile_request(request_for(mode))
                graph = graph_for(compiled)
                self.assertEqual(compiled.scheduler, "simple")
                self.assertEqual(node(graph, "BasicScheduler")["inputs"],
                                 {"model": node(graph, "BasicGuider")["inputs"]["model"],
                                  "scheduler": "simple", "steps": 20, "denoise": 1.0})
                self.assertEqual(node(graph, "KSamplerSelect")["inputs"]["sampler_name"], "res_multistep")
                self.assertFalse(any(value["class_type"] in {"LoraLoaderModelOnly", "MiniMaxH3SigmaShift"}
                                     for value in graph.values()))
                self.assertEqual(compiled.seed, 42)

    def test_invalid_or_empty_scheduler_falls_back_to_simple_in_both_model_families(self):
        for mode in ("t2v", "r2v"):
            for value in (None, "", "   ", "unknown", "BETA", 123):
                with self.subTest(mode=mode, value=value):
                    payload = request_for(mode)
                    payload["scheduler"] = value
                    graph = graph_for(compile_request(payload))
                    self.assertEqual(node(graph, "BasicScheduler")["inputs"]["scheduler"], "simple")

    def test_explicit_native_advanced_schedulers_and_steps_remain_in_actual_graph(self):
        for mode in MODES:
            for scheduler in ("beta", "normal", "sgm_uniform", "karras"):
                with self.subTest(mode=mode, scheduler=scheduler):
                    payload = request_for(mode)
                    payload.update(scheduler=scheduler, steps=28)
                    compiled = compile_request(payload)
                    graph = graph_for(compiled)
                    self.assertEqual(compiled.scheduler, scheduler)
                    self.assertEqual(node(graph, "BasicScheduler")["inputs"]["scheduler"], scheduler)
                    self.assertEqual(node(graph, "BasicScheduler")["inputs"]["steps"], 28)

    def test_turbo_profiles_keep_their_existing_simple_euler_steps_and_shifts(self):
        for mode, steps in (("t2v", 8), ("r2v", 4)):
            with self.subTest(mode=mode):
                payload = request_for(mode)
                payload.update(quality_mode="turbo", scheduler="beta", steps=28)
                compiled = compile_request(payload)
                graph = graph_for(compiled)
                self.assertEqual(node(graph, "BasicScheduler")["inputs"]["scheduler"], "simple")
                self.assertEqual(node(graph, "BasicScheduler")["inputs"]["steps"], steps)
                self.assertEqual(node(graph, "KSamplerSelect")["inputs"]["sampler_name"], "euler")
                self.assertEqual((compiled.shift_video, compiled.shift_audio), (12.0, 3.0))
                self.assertEqual(node(graph, "LoraLoaderModelOnly")["inputs"]["strength_model"],
                                 1.0 if mode == "r2v" else 0.75)

    def test_new_shortfilm_text_and_reference_requests_use_simple(self):
        for referenced, mode in ((False, "t2v"), (True, "r2v")):
            with self.subTest(mode=mode):
                project, scene, shot = shortfilm_case(referenced)
                payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
                self.assertEqual(payload["mode"], mode)
                self.assertEqual(payload["scheduler"], "simple")
                self.assertEqual(node(graph_for(compile_request(payload)), "BasicScheduler")["inputs"]["scheduler"], "simple")


class FrozenNativeSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reset_case()

    def reset_case(self):
        project, scene, shot = shortfilm_case()
        self.pipeline_id = "f" * 32
        self.project_id, self.scene_id, self.shot_id = project["id"], scene["id"], shot["id"]
        self.current = copy.deepcopy(project)
        self.saved = ScriptVideoRenderer._public_payload(project, scene, shot, self.pipeline_id)
        self.saved["scheduler"] = "beta"
        self.pipeline = {"id": self.pipeline_id, "shortfilm_id": project["id"], "status": "running",
                         "shortfilm_snapshot": copy.deepcopy(project),
                         "render_plan": [{"scene_id": scene["id"], "shot_id": shot["id"],
                                          "payload": copy.deepcopy(self.saved)}]}
        self.jobs = SimpleNamespace(create=Mock(return_value={"id": "9" * 32, "status": "queued"}),
                                    cancel=AsyncMock())
        self.store = SimpleNamespace(get=lambda _id: copy.deepcopy(self.current), update=Mock())
        self.app = {"shortfilms": self.store, "jobs": self.jobs,
                    "assets": SimpleNamespace(path_for=Mock(return_value=Path("virtual-reference.png"))),
                    "script_video": SimpleNamespace(pipelines={self.pipeline_id: self.pipeline})}
        # Explicitly stub preparation so regressions never contact any language model.
        self.app["prepare_generation_payload"] = AsyncMock(side_effect=lambda raw: copy.deepcopy(raw))
        self.renderer = ScriptVideoRenderer(self.app)

    async def render(self, expected=None):
        return await self.renderer.render_shot(self.project_id, self.scene_id, self.shot_id, self.pipeline_id,
                                               expected_payload=copy.deepcopy(self.saved if expected is None else expected))

    async def assert_rejected(self, expected=None):
        with self.assertRaises(ScriptwritingError) as error:
            await self.render(expected)
        self.assertEqual(error.exception.status, 409)
        self.jobs.create.assert_not_called()
        self.app["prepare_generation_payload"].assert_not_called()
        self.store.update.assert_not_called()

    async def test_source_backed_old_beta_plan_is_submitted_unchanged(self):
        before = copy.deepcopy(self.pipeline)
        await self.render()
        compiled, submitted = self.jobs.create.call_args.args
        self.assertEqual(submitted, self.saved)
        self.assertIsNot(submitted, self.pipeline["render_plan"][0]["payload"])
        self.assertEqual(compiled.scheduler, "beta")
        self.assertEqual(node(graph_for(compiled), "BasicScheduler")["inputs"]["scheduler"], "beta")
        self.assertEqual(self.pipeline, before)

    async def test_new_pipeline_default_is_simple(self):
        scene = self.current["scenes"][0]
        fresh = self.renderer._public_payload(self.current, scene, scene["shots"][0], self.pipeline_id)
        self.assertEqual(fresh["scheduler"], "simple")
        await self.render(fresh)
        compiled, submitted = self.jobs.create.call_args.args
        self.assertEqual(submitted, fresh)
        self.assertEqual(node(graph_for(compiled), "BasicScheduler")["inputs"]["scheduler"], "simple")

    async def test_changed_generation_source_still_rejects_old_beta_plan(self):
        for field, value in (("action", "Hero walks away."), ("seed", 99)):
            with self.subTest(field=field):
                self.reset_case()
                self.current["scenes"][0]["shots"][0][field] = value
                await self.assert_rejected()
        self.reset_case()
        self.current["assets"][0]["image_asset_ids"] = [IMAGE_B]
        await self.assert_rejected()
        self.reset_case()
        self.current["quality_mode"] = "turbo"
        await self.assert_rejected()

    async def test_other_frozen_scheduler_and_non_template_drift_are_not_accepted(self):
        for field, value in (("scheduler", "normal"), ("scheduler", "karras"), ("steps", 28),
                             ("seed", 99), ("quality_mode", "turbo"), ("references", [])):
            with self.subTest(field=field, value=value):
                self.reset_case()
                self.saved[field] = value
                self.pipeline["render_plan"][0]["payload"] = copy.deepcopy(self.saved)
                await self.assert_rejected()

    async def test_old_beta_requires_frozen_source_manifest_and_caller_identity(self):
        for missing in ("shortfilm_snapshot", "render_plan"):
            with self.subTest(missing=missing):
                self.reset_case()
                self.pipeline.pop(missing)
                await self.assert_rejected()
        self.reset_case()
        caller = copy.deepcopy(self.saved)
        caller["prompt"] = "A forged request."
        await self.assert_rejected(caller)
        self.reset_case()
        self.saved["script_video_pipeline_id"] = "8" * 32
        self.pipeline["render_plan"][0]["payload"] = copy.deepcopy(self.saved)
        await self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
