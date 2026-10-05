"""Resume compiler-template updates with synthetic frozen inputs and no GPU or files."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from script_video_renderer import ScriptVideoRenderer
from scriptwriting import ScriptwritingError
from shortfilm import new_asset, new_project, new_scene, new_shot


class FrozenCompilerResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reset_case()

    def reset_case(self):
        self.pipeline_id = "f" * 32
        character = new_asset("character", "角色A")
        character.update(description="A blue round robot.", image_asset_ids=["a" * 32])
        unused = new_asset("object", "盒子")
        unused.update(description="A small wooden box.", image_asset_ids=["b" * 32])
        scene = new_scene()
        shots = [new_shot(1), new_shot(2)]
        for index, shot in enumerate(shots):
            shot.update(duration=5, action=f"角色A takes step {index + 1} and stops.",
                        asset_ids=[character["id"]])
        scene["shots"] = shots
        project = new_project("Neutral frozen pipeline")
        project.update(assets=[character, unused], scenes=[scene], target_duration=10)
        self.current = copy.deepcopy(project)
        self.project_id, self.scene_id, self.shot_id = project["id"], scene["id"], shots[1]["id"]
        self.saved = ScriptVideoRenderer._public_payload(project, scene, shots[1], self.pipeline_id)
        # An earlier compiler used a different prose wrapper for the exact same source.
        self.saved["prompt"] = "Legacy framing wrapper. " + self.saved["prompt"]
        self.saved["shortfilm_summary"] = "A legacy summary for this same segment."
        self.pipeline = {"id": self.pipeline_id, "shortfilm_id": self.project_id,
                         "status": "running", "shortfilm_snapshot": copy.deepcopy(project),
                         "render_plan": [{"scene_id": self.scene_id, "shot_id": self.shot_id,
                                          "payload": copy.deepcopy(self.saved)}]}
        self.jobs = SimpleNamespace(create=Mock(return_value={"id": "9" * 32, "status": "queued"}),
                                    cancel=AsyncMock())
        self.store = SimpleNamespace(get=lambda _id: copy.deepcopy(self.current),
                                     update=Mock(side_effect=self.save_runtime))
        self.assets = SimpleNamespace(path_for=Mock(return_value=Path("virtual-reference.png")))
        self.app = {"shortfilms": self.store, "assets": self.assets, "jobs": self.jobs,
                    "script_video": SimpleNamespace(pipelines={self.pipeline_id: self.pipeline})}
        self.renderer = ScriptVideoRenderer(self.app)

    def save_runtime(self, project_id, project):
        self.assertEqual(project_id, self.project_id)
        self.current = copy.deepcopy(project)

    async def render(self, expected=None):
        return await self.renderer.render_shot(self.project_id, self.scene_id, self.shot_id,
                                               self.pipeline_id,
                                               expected_payload=copy.deepcopy(self.saved if expected is None else expected))

    async def assert_rejected(self, expected=None):
        with self.assertRaises(ScriptwritingError) as error:
            await self.render(expected)
        self.assertEqual(error.exception.status, 409)
        self.jobs.create.assert_not_called()
        self.store.update.assert_not_called()

    async def test_compiler_only_drift_uses_original_request_without_rewriting_frozen_data(self):
        before = copy.deepcopy(self.pipeline)
        result = await self.render()
        compiled, submitted = self.jobs.create.call_args.args
        self.assertEqual(result["status"], "queued")
        self.assertEqual(submitted, self.saved)
        self.assertIsNot(submitted, self.pipeline["render_plan"][0]["payload"])
        self.assertIn("Legacy framing wrapper.", compiled.prompt)
        self.assertIn("one and the same physical individual", compiled.prompt)
        self.assertEqual(self.pipeline, before)
        self.assertEqual(self.assets.path_for.call_args.args, ("a" * 32,))

    async def test_exact_current_payload_keeps_original_strict_path_without_manager_snapshot(self):
        self.app.pop("script_video")
        scene, shot = self.current["scenes"][0], self.current["scenes"][0]["shots"][1]
        fresh = self.renderer._public_payload(self.current, scene, shot, self.pipeline_id)
        await self.render(fresh)
        self.assertEqual(self.jobs.create.call_args.args[1], fresh)

    async def test_runtime_only_changes_on_any_shot_and_segment_draft_can_resume(self):
        self.current.update(created_at="runtime creation", updated_at="runtime update",
                            segment_draft={"prompts": ["Unsubmitted editor scratchpad."], "duration": 15})
        self.current["scenes"][0]["shots"][0].update(job_id="1" * 32, status="completed")
        self.current["scenes"][0]["shots"][1].update(job_id="2" * 32, status="failed")
        await self.render()
        self.assertEqual(self.jobs.create.call_args.args[1], self.saved)

    async def test_source_settings_story_and_assets_edits_reject_even_if_old_plan_is_trusted(self):
        edits = [
            (("title",), "A changed title"), (("style",), "Changed lighting"),
            (("quality_mode",), "turbo"), (("aspect_ratio",), "9:16"),
            (("megapixels",), 0.5), (("video_vae",), "fp16"),
            (("memory_optimization",), True), (("export_frames",), True),
            (("custom_loras",), [{"name": "changed.safetensors", "strength": 0.5}]),
            (("assets", 0, "image_asset_ids"), ["c" * 32]),
            (("assets", 0, "alias"), "別的角色"), (("assets", 0, "type"), "creature"),
            (("assets", 0, "description"), "A red robot."),
            (("assets", 1, "description"), "An edited, unselected prop."),
            (("scenes", 0, "description"), "A changed doorway."),
            (("scenes", 0, "location"), "Another room"),
            (("scenes", 0, "shots", 1, "action"), "角色A turns around instead."),
            (("scenes", 0, "shots", 1, "camera"), "pan_right"),
            (("scenes", 0, "shots", 1, "camera_detail"), "35mm low angle"),
            (("scenes", 0, "shots", 1, "shot_size"), "close"),
            (("scenes", 0, "shots", 1, "duration"), 10),
            (("scenes", 0, "shots", 1, "seed"), 42),
            (("scenes", 0, "shots", 1, "ending"), "An edited final state."),
            (("scenes", 0, "shots", 1, "sound"), "Changed rain."),
            (("scenes", 0, "shots", 1, "music"), "A changed score."),
            (("scenes", 0, "shots", 0, "action"), "A different already-completed opening."),
        ]
        for path, value in edits:
            with self.subTest(path=path):
                self.reset_case()
                target = self.current
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                await self.assert_rejected()

    async def test_unknown_fields_are_not_silently_dropped_from_source_comparison(self):
        for level in ("project", "scene", "shot", "asset"):
            with self.subTest(level=level):
                self.reset_case()
                target = {"project": self.current, "scene": self.current["scenes"][0],
                          "shot": self.current["scenes"][0]["shots"][1], "asset": self.current["assets"][0]}[level]
                target["future_generation_option"] = "changed"
                await self.assert_rejected()
        self.reset_case()
        self.current["assets"][0]["status"] = "An unknown asset field, not shot runtime."
        await self.assert_rejected()

    async def test_missing_snapshot_plan_or_manager_never_accepts_template_drift(self):
        for missing in ("manager", "pipeline", "shortfilm_snapshot", "render_plan"):
            with self.subTest(missing=missing):
                self.reset_case()
                if missing == "manager":
                    self.app.pop("script_video")
                elif missing == "pipeline":
                    self.app["script_video"].pipelines.clear()
                else:
                    self.pipeline.pop(missing)
                await self.assert_rejected()

    async def test_wrong_lineage_and_ambiguous_manifest_reject_before_creation(self):
        for key in ("shortfilm_project_id", "shortfilm_scene_id", "shortfilm_shot_id", "script_video_pipeline_id"):
            with self.subTest(lineage=key):
                self.reset_case()
                self.saved[key] = "8" * 32
                self.pipeline["render_plan"][0]["payload"] = copy.deepcopy(self.saved)
                await self.assert_rejected()
        for key in ("id", "shortfilm_id"):
            with self.subTest(pipeline_key=key):
                self.reset_case()
                self.pipeline[key] = "8" * 32
                await self.assert_rejected()
        self.reset_case()
        self.pipeline["render_plan"].append(copy.deepcopy(self.pipeline["render_plan"][0]))
        await self.assert_rejected()

    async def test_expected_must_match_saved_manifest_and_non_template_fields_must_match_current(self):
        caller = copy.deepcopy(self.saved)
        caller["prompt"] = "Untrusted caller changes the script."
        await self.assert_rejected(caller)
        for key, value in (("seed", 99), ("duration", 10), ("quality_mode", "turbo"),
                           ("references", []), ("unknown_field", None)):
            with self.subTest(field=key):
                self.reset_case()
                self.saved[key] = value
                self.pipeline["render_plan"][0]["payload"] = copy.deepcopy(self.saved)
                await self.assert_rejected()

    async def test_invalid_changed_source_with_expected_plan_reports_conflict_without_submission(self):
        self.current["assets"][0]["image_asset_ids"] = []
        await self.assert_rejected()
        self.reset_case()
        self.current["scenes"][0]["shots"][1]["continue_previous"] = True
        await self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
