"""Selected video formats survive writing, import and resumed automatic pipelines."""
import asyncio
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web

from domain import ASPECT_RATIOS, compute_dimensions
from script_video_format import (
    DEFAULT_VIDEO_SETTINGS, GENERATION_MEGAPIXELS, ScriptVideoFormatError,
    normalize_video_settings, video_dimensions,
)
from scriptwriting import ScriptwritingManager, normalize_project
from shortfilm import ShortFilmStore
from tests import test_scriptwriting as writing
from tests import test_script_video as pipelines


PORTRAIT = {"aspect_ratio": "9:16", "megapixels": 0.9, "output_resolution": "1080p"}


class FormatSettingsTests(unittest.TestCase):
    def test_legacy_default_preserves_existing_render_and_output_size(self):
        self.assertEqual(normalize_video_settings(), DEFAULT_VIDEO_SETTINGS)
        self.assertEqual(video_dimensions(), {"generation_width": 864, "generation_height": 480,
                                             "output_width": 864, "output_height": 480, "fps": 24})

    def test_all_profiles_match_engine_sizes_and_exact_output_targets(self):
        for ratio in ASPECT_RATIOS:
            for megapixels in GENERATION_MEGAPIXELS:
                for resolution in ("native", "720p", "1080p"):
                    with self.subTest(ratio=ratio, megapixels=megapixels, resolution=resolution):
                        settings = {"aspect_ratio": ratio, "megapixels": megapixels, "output_resolution": resolution}
                        size = video_dimensions(settings)
                        self.assertEqual((size["generation_width"], size["generation_height"]),
                                         compute_dimensions(ratio, megapixels))
                        if resolution == "native":
                            self.assertEqual((size["output_width"], size["output_height"]),
                                             (size["generation_width"], size["generation_height"]))
                        elif ratio in {"9:16", "3:4"}:
                            self.assertEqual(size["output_width"], int(resolution[:-1]))
                        else:
                            self.assertEqual(size["output_height"], int(resolution[:-1]))
                        for key in ("output_width", "output_height"):
                            self.assertGreater(size[key], 0)
                            self.assertEqual(size[key] % 2, 0)
                        self.assertEqual(size["fps"], 24)

    def test_selected_portrait_output_does_not_claim_native_1080_detail(self):
        self.assertEqual(video_dimensions(PORTRAIT), {"generation_width": 736, "generation_height": 1280,
                                                     "output_width": 1080, "output_height": 1920, "fps": 24})

    def test_partial_project_edits_keep_format_and_never_mutate_previous(self):
        original = normalize_project({"video_settings": PORTRAIT})
        before = copy.deepcopy(original)
        edited = normalize_project({"title": "另一個片名"}, original)
        self.assertEqual(edited["video_settings"], PORTRAIT)
        partial = normalize_project({"video_settings": {"megapixels": 0.7}}, original)
        self.assertEqual(partial["video_settings"], {**PORTRAIT, "megapixels": 0.7})
        self.assertEqual(original, before)

    def test_invalid_formats_fail_without_clamping_or_silently_choosing_landscape(self):
        invalid = [[], "1080p", {"width": 3840}, {"aspect_ratio": "2:1"}, {"aspect_ratio": []},
                   {"megapixels": True}, {"megapixels": "0.9"}, {"megapixels": 0.8},
                   {"megapixels": math.nan}, {"megapixels": math.inf},
                   {"output_resolution": "4k"}, {"output_resolution": None}]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ScriptVideoFormatError):
                normalize_video_settings(raw)

    def test_manuscript_import_carries_generation_format_from_frozen_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = web.Application()
            app["shortfilms"] = ShortFilmStore(root / "shortfilms")
            manager = ScriptwritingManager(app, root)
            source = manager.create_project({"duration": 5, "video_settings": PORTRAIT})
            job_id = "b" * 32
            manager.jobs[job_id] = {"id": job_id, "status": "completed", "action": "storyboard",
                                    "storyboard": copy.deepcopy(writing.STORYBOARD), "input": {"project": source},
                                    "shortfilm_id": None}
            imported = manager.import_shortfilm(job_id)["project"]
            self.assertEqual(imported["aspect_ratio"], "9:16")
            self.assertEqual(imported["megapixels"], 0.9)
            self.assertEqual(source["video_settings"], PORTRAIT)


class VideoFormatPipelineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = pipelines.ScriptVideoAPITests.asyncSetUp
    asyncTearDown = pipelines.ScriptVideoAPITests.asyncTearDown
    fake_tags = pipelines.ScriptVideoAPITests.fake_tags
    fake_show = pipelines.ScriptVideoAPITests.fake_show
    fake_chat = pipelines.ScriptVideoAPITests.fake_chat
    fake_unload = pipelines.ScriptVideoAPITests.fake_unload
    redirect_target = pipelines.ScriptVideoAPITests.redirect_target
    fake_free = pipelines.ScriptVideoAPITests.fake_free
    create = pipelines.ScriptVideoAPITests.create
    project = pipelines.ScriptVideoAPITests.project
    start_pipeline = pipelines.ScriptVideoAPITests.start_pipeline
    wait_pipeline = pipelines.ScriptVideoAPITests.wait_pipeline

    async def test_legacy_preflight_preserves_missing_format_marker(self):
        project = await self.project()
        legacy = copy.deepcopy(project)
        legacy.pop("video_settings")
        await self.pipeline_manager.preflight(legacy)
        self.assertNotIn("video_settings", legacy)

    async def test_legacy_writing_snapshot_does_not_inherit_later_project_format(self):
        project = await self.project(video_settings=PORTRAIT)
        legacy = copy.deepcopy(project)
        legacy.pop("video_settings")
        with patch.object(self.manager, "_run", new=AsyncMock()):
            job = self.manager.submit({"project_id": project["id"], "action": "storyboard"}, project_override=legacy)
            await self.manager.tasks[job["id"]]
        self.assertEqual(job["input"]["project"]["video_settings"], DEFAULT_VIDEO_SETTINGS)
        self.assertEqual(self.manager.get_project(project["id"])["video_settings"], PORTRAIT)
        self.assertNotIn("video_settings", legacy)

    async def test_automatic_writing_path_freezes_selected_format_before_later_edits(self):
        project = await self.project(video_settings=PORTRAIT)
        self.renderer.hold.clear()
        response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"],
                                                                   "action": "screenplay", "auto_video": True})
        self.assertEqual(response.status, 202, await response.text())
        submitted = await response.json()
        self.assertEqual(submitted["input"]["project"]["video_settings"], PORTRAIT)
        pipeline = submitted["video_pipeline"]
        self.assertEqual(pipeline["video_settings"], PORTRAIT)
        await asyncio.wait_for(self.renderer.started.wait(), 4)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": project["revision"], "video_settings": DEFAULT_VIDEO_SETTINGS})
        self.assertEqual(response.status, 200)
        self.renderer.hold.set()
        completed = await self.wait_pipeline(pipeline)
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["snapshot"]["video_settings"], PORTRAIT)

    async def test_completed_source_uses_selected_project_format_without_rewriting_source_record(self):
        project = await self.project(video_settings=PORTRAIT)
        source_id = "c" * 32
        original = {**copy.deepcopy(project), "video_settings": copy.deepcopy(DEFAULT_VIDEO_SETTINGS)}
        self.manager.jobs[source_id] = {"id": source_id, "project_id": project["id"],
                                       "action": "screenplay", "status": "completed", "content": pipelines.SCREENPLAY,
                                       "input": {"project": original, "settings": copy.deepcopy(self.manager.settings)},
                                       "model_unloaded": True}
        self.manager._save_jobs(source_id)
        completed = await self.wait_pipeline(await self.start_pipeline(project, source_job_id=source_id))
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["video_settings"], PORTRAIT)
        self.assertEqual(self.manager.get_job(source_id)["input"]["project"]["video_settings"], DEFAULT_VIDEO_SETTINGS)

    async def test_format_persists_to_host_snapshot_and_survives_edits_and_resume(self):
        project = await self.project(content="既有劇本。", video_settings=PORTRAIT)
        self.renderer.fail_render_number = 1
        failed = await self.wait_pipeline(await self.start_pipeline(project))
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["video_settings"], PORTRAIT)
        self.assertEqual(failed["snapshot"]["video_settings"], PORTRAIT)
        self.assertEqual(failed["video_format"], {**video_dimensions(PORTRAIT), "container": "mp4"})
        saved = json.loads((self.pipeline_manager.directory / f"{failed['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["video_settings"], PORTRAIT)
        shortfilm = self.app["shortfilms"].get(failed["shortfilm_id"])
        self.assertEqual((shortfilm["aspect_ratio"], shortfilm["megapixels"]), ("9:16", 0.9))
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                        json={"revision": project["revision"], "video_settings": DEFAULT_VIDEO_SETTINGS})
        self.assertEqual(response.status, 200)
        self.renderer.fail_render_number = None
        response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        completed = await self.wait_pipeline(await response.json())
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["video_settings"], PORTRAIT)
        self.assertEqual(completed["snapshot"]["video_settings"], PORTRAIT)

    async def test_rejected_format_does_not_queue_writing_or_video(self):
        response = await self.client.post("/api/scripts/projects", json={"video_settings": {"output_resolution": "4k"}})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.pipeline_manager.pipelines, {})
        self.assertEqual(self.video_jobs.jobs, {})


if __name__ == "__main__":
    unittest.main()
