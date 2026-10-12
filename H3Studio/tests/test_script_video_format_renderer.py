"""Frozen video formats, supported editor canvases, and small real CPU exports."""
import asyncio
import copy
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import av
import numpy as np
from aiohttp import web
from PIL import Image

from domain import ASPECT_RATIOS, compile_request, compute_dimensions
from script_video_format import normalize_video_settings, video_dimensions
from script_video_renderer import ScriptVideoRenderer
from scriptwriting import ScriptwritingError
from shortfilm import ShortFilmStore, new_asset, new_project, new_scene, new_shot
from tests import test_script_references as pictures
from tests import test_video_editor_render as sources
from video_editor import EditorError, EditorStore, probe_media


class ScriptVideoFormatRendererTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.assets = pictures.Assets(self.root)
        self.assets.save_image(Image.new("RGB", (32, 32), "blue"), "reference.png", "test")
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
        self.app["prepare_generation_payload"] = AsyncMock(side_effect=lambda raw: copy.deepcopy(raw))
        self.app["script_video"] = SimpleNamespace(pipelines={})
        self.renderer = ScriptVideoRenderer(self.app)

    async def asyncTearDown(self):
        if isinstance(self.app.get("editor"), EditorStore):
            await self.app["editor"].shutdown()
        self.temporary.cleanup()

    def project(self, settings=None):
        settings = normalize_video_settings(settings)
        project, scene, shot, asset = new_project("Video format"), new_scene(), new_shot(), new_asset("character", "Hero")
        asset.update(description="A blue round robot.", image_asset_ids=[pictures.IMAGE_ID])
        shot.update(duration=5, action="Hero waves and settles.", asset_ids=[asset["id"]])
        scene["shots"] = [shot]
        project.update(assets=[asset], scenes=[scene], target_duration=5,
                       aspect_ratio=settings["aspect_ratio"], megapixels=settings["megapixels"])
        return self.app["shortfilms"].create(project)

    async def test_every_generation_format_reaches_the_h3_compiler(self):
        for ratio in ASPECT_RATIOS:
            for megapixels in (0.4, 0.7, 0.9, 0.98):
                with self.subTest(ratio=ratio, megapixels=megapixels):
                    settings = {"aspect_ratio": ratio, "megapixels": megapixels, "output_resolution": "1080p"}
                    project = self.project(settings)
                    pipeline_id = f"{len(self.app['script_video'].pipelines) + 1:032x}"
                    self.app["script_video"].pipelines[pipeline_id] = {"video_settings": settings}
                    plan = await self.renderer.compile_all(project["id"], pipeline_id)
                    compiled = compile_request(plan[0]["payload"])
                    self.assertEqual((compiled.width, compiled.height), compute_dimensions(ratio, megapixels))
                    self.assertEqual(plan[0]["payload"]["aspect_ratio"], ratio)
                    self.assertEqual(plan[0]["payload"]["megapixels"], megapixels)
        self.app["jobs"].create.assert_not_called()

    async def test_invalid_video_settings_stop_before_starting_the_engine(self):
        for settings in ({"aspect_ratio": "3:2"}, {"megapixels": 0.3}, {"output_resolution": "4k"},
                         {"megapixels": True}, {"aspect_ratio": None}):
            with self.subTest(settings=settings):
                with self.assertRaises(ScriptwritingError):
                    await self.renderer.preflight({"duration": 5, "video_settings": settings})
        self.comfy.ensure_running.assert_not_awaited()
        self.app["jobs"].create.assert_not_called()

    async def test_changed_generation_format_cannot_mix_with_a_frozen_render_plan(self):
        settings = {"aspect_ratio": "9:16", "megapixels": 0.7, "output_resolution": "1080p"}
        project = self.project(settings)
        pipeline_id = "f" * 32
        pipeline = {"video_settings": copy.deepcopy(settings)}
        self.app["script_video"].pipelines[pipeline_id] = pipeline
        plan = (await self.renderer.compile_all(project["id"], pipeline_id))[0]
        project["aspect_ratio"] = "16:9"
        self.app["shortfilms"].update(project["id"], project)
        with self.assertRaises(ScriptwritingError) as failure:
            await self.renderer.render_shot(project["id"], plan["scene_id"], plan["shot_id"], pipeline_id,
                                            expected_payload=plan["payload"])
        self.assertEqual(failure.exception.status, 409)
        with self.assertRaises(ScriptwritingError):
            await self.renderer.compile_all(project["id"], pipeline_id)
        self.app["jobs"].create.assert_not_called()
        self.assertEqual(pipeline["video_settings"], settings)

    async def test_editor_accepts_all_native_and_delivery_canvases_without_allowing_arbitrary_large_sizes(self):
        store = EditorStore(self.root / "editor-whitelist", self.assets, self.app["jobs"])
        try:
            for ratio in ASPECT_RATIOS:
                for megapixels in (0.4, 0.7, 0.9, 0.98):
                    for resolution in ("native", "720p", "1080p"):
                        settings = {"aspect_ratio": ratio, "megapixels": megapixels, "output_resolution": resolution}
                        dimensions = video_dimensions(settings)
                        project = store.create_project({"width": dimensions["output_width"], "height": dimensions["output_height"]})
                        self.assertEqual((project["width"], project["height"]),
                                         (dimensions["output_width"], dimensions["output_height"]))
            for dimensions in ((2520, 2520), (2520, 720), (2000, 1080), (1921, 1080), (True, 720)):
                with self.assertRaises(EditorError):
                    store.create_project({"width": dimensions[0], "height": dimensions[1]})
        finally:
            await store.shutdown()

    async def test_real_portrait_native_and_1080p_exports_use_frozen_canvas_and_keep_sound(self):
        source = self.root / "source.mp4"
        sources.make_source(source)
        job_id = "1" * 32
        jobs = SimpleNamespace(jobs={job_id: {"id": job_id, "status": "completed", "name": "Synthetic source"}},
                               local_output_path=lambda job: source)
        self.app["jobs"] = jobs
        editor = EditorStore(self.root / "editor-real", self.assets, jobs)
        self.app["editor"] = editor
        project = self.project()
        pipelines = self.app["script_video"].pipelines
        outputs = []
        for index, resolution in enumerate(("native", "1080p")):
            settings = {"aspect_ratio": "9:16", "megapixels": 0.4, "output_resolution": resolution}
            pipeline_id = f"{index + 2:032x}"
            pipeline = {"id": pipeline_id, "video_settings": copy.deepcopy(settings),
                        "snapshot": {"video_settings": {"aspect_ratio": "21:9", "megapixels": 0.98, "output_resolution": "720p"}},
                        "shortfilm_snapshot": copy.deepcopy(project), "render_plan": [{"payload": {"duration": 0.5}}]}
            pipelines[pipeline_id] = pipeline

            async def progress(changes):
                pipeline.update(changes)
                # Live project edits never change a previously frozen output.
                live = self.app["shortfilms"].get(project["id"])
                live.update(aspect_ratio="21:9", megapixels=0.98)
                self.app["shortfilms"].update(project["id"], live)

            result = await asyncio.wait_for(self.renderer.assemble(project["id"], [job_id], pipeline_id, progress), 30)
            target = editor.export_path(result["export_id"])
            expected = (480, 864) if resolution == "native" else (1080, 1920)
            info = probe_media(target)
            self.assertEqual((info["width"], info["height"]), expected)
            self.assertAlmostEqual(info["duration"], 0.5, delta=1 / 24)
            self.assertEqual(info["fps"], 24)
            self.assertTrue(info["has_audio"])
            with av.open(str(target)) as container:
                first = next(container.decode(video=0)).to_ndarray(format="rgb24")
            self.assertLess(float(first[0].mean()), 3)
            self.assertGreater(first[expected[1] // 2, expected[0] // 2, 0], 230)
            rate, sound = sources.decoded_audio(target)
            self.assertGreater(float(np.sqrt(np.mean(sound[:, :rate // 3] ** 2))), 0.05)
            outputs.append((result, expected))
            self.assertEqual(pipeline["video_settings"], settings)
            self.assertEqual(await self.renderer.assemble(project["id"], [job_id], pipeline_id, progress), result)
        self.assertNotEqual(outputs[0][0]["editor_project_id"], outputs[1][0]["editor_project_id"])
        self.assertEqual(len(editor.exports), 2)

    async def test_snapshot_settings_and_legacy_default_resolve_without_live_project_drift(self):
        settings = {"aspect_ratio": "3:4", "megapixels": 0.9, "output_resolution": "720p"}
        self.assertEqual(self.renderer._video_settings({"snapshot": {"video_settings": settings}}), settings)
        self.assertEqual(video_dimensions(self.renderer._video_settings({}))["output_width"], 864)
        self.assertEqual(video_dimensions(self.renderer._video_settings({}))["output_height"], 480)

    async def test_legacy_half_megapixel_portrait_plan_renders_and_assembles_at_its_original_default(self):
        project = self.project({"aspect_ratio": "9:16", "megapixels": 0.4, "output_resolution": "native"})
        project["megapixels"] = 0.5
        project = self.app["shortfilms"].update(project["id"], project)
        pipeline_id = "e" * 32
        pipeline = {"id": pipeline_id, "shortfilm_id": project["id"],
                    "snapshot": {"title": "Legacy script with no video settings"},
                    "shortfilm_snapshot": copy.deepcopy(project)}
        self.app["script_video"].pipelines[pipeline_id] = pipeline
        plan = await self.renderer.compile_all(project["id"], pipeline_id)
        pipeline["render_plan"] = copy.deepcopy(plan)
        original_plan = copy.deepcopy(plan)
        compiled = compile_request(plan[0]["payload"])
        self.assertEqual((compiled.width, compiled.height), compute_dimensions("9:16", 0.5))
        self.assertEqual(plan[0]["payload"]["megapixels"], 0.5)

        source = self.root / "legacy-source.mp4"
        with av.open(str(source), "w") as output:
            video = output.add_stream("libx264", rate=24)
            video.width, video.height, video.pix_fmt = 64, 96, "yuv420p"
            video.options = {"crf": "18", "preset": "ultrafast", "threads": "1"}
            for index in range(124):
                pixels = np.full((96, 64, 3), (200, 30, 30), dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                frame.pts, frame.time_base = index, Fraction(1, 24)
                for packet in video.encode(frame):
                    output.mux(packet)
            for packet in video.encode():
                output.mux(packet)
        job_id = "2" * 32
        jobs = SimpleNamespace(create=Mock(return_value={"id": job_id, "status": "completed"}), cancel=AsyncMock(),
                               jobs={job_id: {"id": job_id, "status": "completed", "name": "Legacy generated clip"}},
                               local_output_path=lambda job: source)
        self.app["jobs"] = jobs
        shot_plan = plan[0]
        await self.renderer.render_shot(project["id"], shot_plan["scene_id"], shot_plan["shot_id"], pipeline_id,
                                        expected_payload=shot_plan["payload"])
        submitted_compiled, submitted_payload = jobs.create.call_args.args
        self.assertEqual(submitted_payload, shot_plan["payload"])
        self.assertEqual((submitted_compiled.width, submitted_compiled.height), compute_dimensions("9:16", 0.5))
        editor = EditorStore(self.root / "legacy-editor", self.assets, jobs)
        self.app["editor"] = editor

        async def progress(changes):
            pipeline.update(changes)

        result = await asyncio.wait_for(self.renderer.assemble(project["id"], [job_id], pipeline_id, progress), 30)
        info = probe_media(editor.export_path(result["export_id"]))
        self.assertEqual((info["width"], info["height"]), (864, 480))
        self.assertAlmostEqual(info["duration"], 5, delta=1 / 24)
        self.assertEqual(pipeline["render_plan"], original_plan)
        self.assertNotIn("video_settings", pipeline)
        self.assertNotIn("video_settings", pipeline["snapshot"])


if __name__ == "__main__":
    unittest.main()
