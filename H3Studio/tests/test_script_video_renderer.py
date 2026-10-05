"""H3 reference wiring and real CPU assembly, using only synthetic local media."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import av
import numpy as np
from aiohttp import web
from PIL import Image

from domain import compile_request
from script_video_renderer import ScriptVideoRenderer
from scriptwriting import ScriptwritingError
from shortfilm import ShortFilmStore, new_project, new_scene, new_shot, new_asset
from tests import test_script_references as pictures
from tests import test_video_editor_render as sources
from video_editor import EditorStore, probe_media


class ScriptVideoRendererTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = pictures.Assets(self.root)
        self.assets.save_image(Image.new("RGB", (32, 32), "blue"), "角色.png", "test")
        self.comfy = SimpleNamespace(mode="local", base_url="http://127.0.0.1:8188",
                                     is_ready=AsyncMock(return_value=True), ensure_running=AsyncMock(),
                                     model_inventory=AsyncMock(return_value={"ref2va": True, "text_encoder": True,
                                         "audio_vae": True, "video_vae": True, "video_vae_fp16": True}))
        self.app = web.Application()
        self.app["comfy"], self.app["assets"] = self.comfy, self.assets
        self.app["shortfilms"] = ShortFilmStore(self.root / "shortfilms")
        self.app["editor"] = SimpleNamespace()
        self.app["scriptwriting"] = SimpleNamespace(_validate_reference_assets=lambda refs: None)
        self.app["jobs"] = SimpleNamespace(create=Mock(), jobs={})
        self.renderer = ScriptVideoRenderer(self.app)
        self.pipe_id = "f" * 32

    async def asyncTearDown(self):
        editor = self.app.get("editor")
        if isinstance(editor, EditorStore):
            await editor.shutdown()
        self.temp.cleanup()

    def project(self, *, image=True, extra_character=False):
        project, scene, shot, asset = new_project("測試短片"), new_scene(), new_shot(), new_asset("character", "阿青")
        asset["description"] = "A blue round robot."
        asset["image_asset_ids"] = [pictures.IMAGE_ID] if image else []
        project["assets"] = [asset]
        if extra_character:
            project["assets"].append(new_asset("character", "阿木"))
        shot.update(duration=15, action="阿青 walks toward a stone gate, stops and raises one hand.",
                    dialogue="我們到了。", speaker_alias="阿青", asset_ids=[item["id"] for item in project["assets"]])
        scene["shots"] = [shot]
        project.update(scenes=[scene], target_duration=15)
        return self.app["shortfilms"].create(project)

    async def test_readiness_is_read_only_and_local_model_failure_is_explicit(self):
        self.assertTrue((await self.renderer.readiness())["ready"])
        self.comfy.ensure_running.assert_not_awaited()
        self.app["jobs"].create.assert_not_called()
        self.comfy.model_inventory.return_value["ref2va"] = False
        missing = await self.renderer.readiness()
        self.assertFalse(missing["ready"])
        self.assertIn("Ref2VA", missing["error"])
        self.comfy.mode = "remote"
        self.assertFalse((await self.renderer.readiness())["ready"])

    async def test_compile_preserves_reference_identity_dialogue_and_no_bgm(self):
        project = self.project()
        plan = await self.renderer.compile_all(project["id"], self.pipe_id)
        self.assertEqual(len(plan), 1)
        compiled = compile_request(plan[0]["payload"])
        self.assertEqual(compiled.mode, "r2v")
        self.assertEqual(compiled.reference_images, [pictures.IMAGE_ID])
        self.assertIn("<d>[Chinese] 我們到了。</d>", compiled.prompt)
        self.assertIn("non_diegetic_music:\nN/A", compiled.prompt)
        self.assertIn("subject_definitions:", compiled.prompt)
        self.app["jobs"].create.assert_not_called()

    async def test_missing_images_unbound_characters_and_reference_overflow_never_render(self):
        for project in (self.project(image=False), self.project(extra_character=True)):
            with self.assertRaises(ScriptwritingError):
                await self.renderer.compile_all(project["id"], self.pipe_id)
        project = self.project()
        project["assets"][0]["image_asset_ids"] = [f"{index:032x}" for index in range(1, 11)]
        self.app["shortfilms"].update(project["id"], project)
        with self.assertRaises(ValueError):
            await self.renderer.compile_all(project["id"], self.pipe_id)
        self.app["jobs"].create.assert_not_called()

    async def test_reference_changes_after_plan_reject_before_submission(self):
        project = self.project()
        plan = (await self.renderer.compile_all(project["id"], self.pipe_id))[0]
        project["assets"][0]["description"] = "A different red robot."
        self.app["shortfilms"].update(project["id"], project)
        with self.assertRaises(ScriptwritingError) as error:
            await self.renderer.render_shot(project["id"], plan["scene_id"], plan["shot_id"], self.pipe_id,
                                            expected_payload=plan["payload"])
        self.assertEqual(error.exception.status, 409)
        self.app["jobs"].create.assert_not_called()

    async def test_real_assembly_trims_frame_padding_and_keeps_original_audio_without_music(self):
        paths = [self.root / "first.mp4", self.root / "second.mp4"]
        sources.make_source(paths[0])
        sources.make_source(paths[1], blue=True)
        ids = ["1" * 32, "2" * 32]
        jobs = SimpleNamespace(jobs={job_id: {"id": job_id, "status": "completed", "name": "測試片段"} for job_id in ids},
                               local_output_path=lambda job: paths[ids.index(job["id"])])
        self.app["jobs"] = jobs
        editor = EditorStore(self.root / "editor-data", self.assets, jobs)
        self.app["editor"] = editor
        project = self.project()
        pipeline = {"id": self.pipe_id, "render_plan": [{"payload": {"duration": 1.5}} for _ in ids],
                    "shortfilm_snapshot": copy.deepcopy(project)}
        self.app["script_video"] = SimpleNamespace(pipelines={self.pipe_id: pipeline})
        changes = []
        async def progress(value):
            pipeline.update(value)
            changes.append(copy.deepcopy(value))
        result = await asyncio.wait_for(self.renderer.assemble(project["id"], ids, self.pipe_id, progress), 30)
        path = editor.export_path(result["export_id"])
        info = probe_media(path)
        self.assertAlmostEqual(info["duration"], 3, delta=1 / 24)
        self.assertTrue(info["has_audio"])
        edited = editor.projects[result["editor_project_id"]]
        self.assertEqual(edited["audio_clips"], [])
        self.assertEqual([clip["out"] for clip in edited["clips"]], [1.5, 1.5])
        self.assertTrue(any("export_id" in change for change in changes))
        with av.open(str(path)) as source:
            frames = list(source.decode(video=0))
        self.assertEqual(len(frames), 72)
        self.assertGreater(frames[0].to_ndarray(format="rgb24")[240, 432, 0], 230)
        self.assertGreater(frames[40].to_ndarray(format="rgb24")[240, 432, 2], 230)
        rate, audio = sources.decoded_audio(path)
        self.assertGreater(float(np.sqrt(np.mean(audio[:, :rate] ** 2))), 0.05)
        self.assertEqual(await self.renderer.assemble(project["id"], ids, self.pipe_id, progress), result)
        self.assertEqual(len(editor.exports), 1)


if __name__ == "__main__":
    unittest.main()
