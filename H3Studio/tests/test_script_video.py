"""Local script-to-video orchestration using fake Ollama and fake video jobs."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import script_video
from domain import compile_request
from shortfilm import ShortFilmStore, compile_shot_payload, flatten_shots
from scriptwriting import DEFAULT_SETTINGS, ScriptwritingError, register_scriptwriting_routes
from tests import test_scriptwriting as scripts
from tests import test_script_references as references


SCREENPLAY = "0–5 秒：阿青拿起行李。\n5–10 秒：阿青登上列車。"
STORYBOARD = copy.deepcopy(scripts.STORYBOARD)
STORYBOARD["scenes"][0]["shots"].append({
    **copy.deepcopy(STORYBOARD["scenes"][0]["shots"][0]),
    "title": "登車", "action": "阿青登上列車，向車站揮手。", "dialogue": "", "speaker": "",
})
SHORT_STORYBOARD = copy.deepcopy(STORYBOARD)
SHORT_STORYBOARD["scenes"][0]["shots"] = [
    {**copy.deepcopy(STORYBOARD["scenes"][0]["shots"][0]),
     "title": f"切鏡 {index}", "duration": duration,
     "action": f"阿青走動，第 {index} 次回頭。", "dialogue": "", "speaker": ""}
    for index, duration in enumerate((2, 2, 2.5, 2.5, 3, 3), 1)
]
DURATION_ERROR = "H3 分鏡的每個鏡頭必須介於 5～15 秒。"


class FakeVideoJobs:
    def __init__(self, lock):
        self.gpu_lock = lock
        self.jobs = {}
        self.tasks = {}
        self.cancelled = []

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        job = self.jobs[job_id]
        job.update(status="cancelled", error="test cancellation")
        task = self.tasks.get(job_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


class FakeVideoRenderer:
    def __init__(self, app):
        self.app = app
        self.ready = True
        self.error = None
        self.readiness_calls = 0
        self.preflight_calls = []
        self.plans = {}
        self.render_calls = []
        self.assemble_calls = []
        self.cancel_export_calls = []
        self.cancel_export_records = []
        self.started = asyncio.Event()
        self.hold = asyncio.Event()
        self.hold.set()
        self.assembly_started = asyncio.Event()
        self.assembly_hold = asyncio.Event()
        self.assembly_hold.set()
        self.assembly_progress = 100
        self.fail_render_number = None

    async def readiness(self):
        self.readiness_calls += 1
        return {"ready": self.ready, "error": self.error}

    async def preflight(self, project):
        self.preflight_calls.append(copy.deepcopy(project))
        if not self.ready:
            raise ScriptwritingError(self.error or "本機影片引擎尚未就緒。", 503)
        duration = project.get("duration", project.get("target_duration", 0))
        if not 0 < duration <= 600:
            raise ScriptwritingError("自動組片最多 600 秒。")
        if "character_references" in project:
            cards = project["character_references"]
            if not cards or any(not card.get("image_asset_id") or not card.get("appearance") for card in cards):
                raise ScriptwritingError("自動 Ref2VA 生成需要圖片與外觀描述。")
            for card in cards:
                self.app["assets"].path_for(card["image_asset_id"])
        if "scenes" in project and len(flatten_shots(project)) > 50:
            raise ScriptwritingError("自動組片最多 50 鏡。")

    async def compile_all(self, shortfilm_id, pipeline_id):
        project = self.app["shortfilms"].get(shortfilm_id)
        await self.preflight(project)
        plans = []
        for scene, shot in flatten_shots(project):
            payload, _ = compile_shot_payload(project, scene["id"], shot["id"])
            # The fake executes the real compiler, but never submits its workflow.
            compiled = compile_request(payload)
            if compiled.mode != "r2v":
                raise ScriptwritingError("自動影片必須使用 Ref2VA，不能降級為純文字。")
            if shot["music"] != "N/A":
                raise ScriptwritingError("自動影片禁止背景配樂。")
            plans.append({"scene_id": scene["id"], "shot_id": shot["id"], "payload": payload})
        self.plans[shortfilm_id] = copy.deepcopy(plans)
        return plans

    async def render_shot(self, *args, **kwargs):
        call = {"args": copy.deepcopy(args), "kwargs": copy.deepcopy(kwargs)}
        self.render_calls.append(call)
        number = len(self.render_calls)
        job_id = f"{number + 1000:032x}"
        job = {"id": job_id, "status": "queued", "progress": 0, "error": None}
        self.app["jobs"].jobs[job_id] = job

        async def generate():
            async with self.app["jobs"].gpu_lock:
                job.update(status="running", progress=50)
                self.started.set()
                await self.hold.wait()
                await asyncio.sleep(0.005)
                if self.fail_render_number == number:
                    job.update(status="failed", error="test video failure")
                else:
                    job.update(status="completed", progress=100,
                               output={"filename": f"{job_id}.mp4"}, video_url=f"/api/jobs/{job_id}/video")

        self.app["jobs"].tasks[job_id] = asyncio.create_task(generate())
        return copy.deepcopy(job)

    async def assemble(self, shortfilm_id, video_job_ids, pipeline_id, progress_callback):
        self.assemble_calls.append({"shortfilm_id": shortfilm_id, "video_job_ids": list(video_job_ids), "pipeline_id": pipeline_id})
        for job_id in video_job_ids:
            if self.app["jobs"].jobs[job_id]["status"] != "completed":
                raise AssertionError("assembly must wait until all video jobs complete")
        if progress_callback is not None:
            await progress_callback(copy.deepcopy(self.assembly_progress))
        self.assembly_started.set()
        await self.assembly_hold.wait()
        return {"video_url": f"/api/editor/exports/{pipeline_id}/file",
                "editor_project_id": "e" * 32, "export_id": "f" * 32}

    async def cancel_export(self, pipeline):
        self.cancel_export_calls.append(pipeline["id"])
        self.cancel_export_records.append(copy.deepcopy(pipeline))


class ScriptVideoAPITests(unittest.IsolatedAsyncioTestCase):
    fake_tags = scripts.ScriptAPITests.fake_tags
    fake_show = scripts.ScriptAPITests.fake_show
    fake_unload = scripts.ScriptAPITests.fake_unload
    redirect_target = scripts.ScriptAPITests.redirect_target
    fake_free = scripts.ScriptAPITests.fake_free
    create = scripts.ScriptAPITests.create
    submit = scripts.ScriptAPITests.submit
    complete = scripts.ScriptAPITests.complete

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        (self.static / "scripts.html").write_text("test", encoding="utf-8")
        self.lock = asyncio.Lock()
        self.mode = "normal"
        self.tags = [{"name": DEFAULT_SETTINGS["model"], "size": 18_000_000_000}]
        self.show = {"capabilities": ["completion", "thinking"]}
        self.reply = SCREENPLAY
        self.screenplay_reply = SCREENPLAY
        self.storyboard_reply = json.dumps(STORYBOARD, ensure_ascii=False)
        self.chat_requests = []
        self.chat_actions = []
        self.unloads = []
        self.started = asyncio.Event()
        self.unload_started = asyncio.Event()
        self.finish = asyncio.Event()
        self.free_calls = []
        self.redirect_hits = 0
        fake = web.Application()
        fake.router.add_get("/api/tags", self.fake_tags)
        fake.router.add_post("/api/show", self.fake_show)
        fake.router.add_post("/api/chat", self.fake_chat)
        fake.router.add_post("/api/generate", self.fake_unload)
        fake.router.add_get("/redirect-target", self.redirect_target)
        fake.router.add_post("/free", self.fake_free)
        self.ollama = TestServer(fake)
        await self.ollama.start_server()
        self.app = web.Application()
        self.video_jobs = FakeVideoJobs(self.lock)
        self.app["jobs"] = self.video_jobs
        self.app["shortfilms"] = ShortFilmStore(self.root / "shortfilms")
        self.assets = references.Assets(self.root / "assets")
        self.assets.directory.mkdir()
        self.assets.save_image(Image.new("RGB", (32, 32), "red"), "portrait.png", "script-character-reference")
        self.app["assets"] = self.assets
        self.reference = references.card(name="阿青", image_asset_id=references.IMAGE_ID,
                                         image_name="portrait.png", appearance="短黑髮，穿藍色外套，拿著黑色行李。")
        self.renderer = FakeVideoRenderer(self.app)
        self.app["script_video_renderer"] = self.renderer
        self.manager = register_scriptwriting_routes(self.app, self.static, self.root)
        self.manager.settings["base_url"] = str(self.ollama.make_url("/")).rstrip("/")
        self.pipeline_manager = self.app["script_video"]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        self.finish.set()
        self.renderer.hold.set()
        self.renderer.assembly_hold.set()
        await self.client.close()
        await self.video_jobs.close()
        await self.ollama.close()
        self.temp.cleanup()

    async def fake_chat(self, request):
        payload = await request.json()
        action = "storyboard" if payload.get("format") == "json" else "screenplay"
        self.chat_actions.append(action)
        self.reply = self.storyboard_reply if action == "storyboard" else self.screenplay_reply
        return await scripts.ScriptAPITests.fake_chat(self, request)

    async def project(self, **fields):
        return await self.create(**{"duration": 10, "character_references": [self.reference], **fields})

    async def start_pipeline(self, project, **fields):
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"], **fields})
        self.assertEqual(response.status, 202, await response.text())
        return await response.json()

    async def wait_pipeline(self, pipeline, statuses=("completed", "failed", "cancelled", "interrupted")):
        async with asyncio.timeout(6):
            while True:
                response = await self.client.get(f"/api/scripts/video-pipelines/{pipeline['id']}")
                self.assertEqual(response.status, 200, await response.text())
                value = await response.json()
                if value["status"] in statuses:
                    return value
                await asyncio.sleep(0.01)

    async def failed_short_storyboard(self):
        """Simulate an old validator rejecting an otherwise complete draft."""
        project = await self.project(duration=15)
        self.screenplay_reply = "0–15 秒：阿青走向列車，幾次回頭望向車站。"
        self.storyboard_reply = json.dumps(SHORT_STORYBOARD, ensure_ascii=False)
        with patch("scriptwriting.parse_storyboard", side_effect=ScriptwritingError(DURATION_ERROR)):
            result = await self.wait_pipeline(await self.start_pipeline(project))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "storyboard")
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["done_reason"], "stop")
        self.assertFalse(child.get("truncated"))
        self.assertTrue(child["model_unloaded"])
        return project, result, child

    async def test_resume_recovers_saved_short_cuts_without_rewriting_or_model_generation(self):
        project, failed, child = await self.failed_short_storyboard()
        screenplay = self.manager.get_job(failed["screenplay_job_id"])
        original_jobs = set(self.manager.jobs)
        original_actions = list(self.chat_actions)

        response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        result = await self.wait_pipeline(await response.json())

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["storyboard_job_id"], child["id"])
        self.assertEqual(self.chat_actions, original_actions)
        self.assertEqual(set(self.manager.jobs), original_jobs)
        self.assertEqual(self.manager.get_job(screenplay["id"]), screenplay)
        self.assertEqual(self.manager.get_project(project["id"]), project)
        recovered = self.manager.get_job(child["id"])
        self.assertEqual(recovered["content"], child["content"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["recovered_from_error"], DURATION_ERROR)
        self.assertTrue(recovered["recovered_at"])
        metadata = recovered["storyboard"]["storyboard_normalization"]
        self.assertEqual(metadata["original_shot_count"], 6)
        self.assertEqual(metadata["generation_clip_count"], 1)
        self.assertEqual(len(self.renderer.render_calls), 1)
        archive = json.loads((self.manager.history_directory / f"{child['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(archive, scripts.stored_record(recovered))

    async def test_recovery_rejects_incomplete_nonstop_and_unrelated_valid_drafts(self):
        _, _, child = await self.failed_short_storyboard()
        valid_clip = copy.deepcopy(SHORT_STORYBOARD)
        valid_clip["scenes"][0]["shots"] = [
            {**copy.deepcopy(STORYBOARD["scenes"][0]["shots"][0]), "duration": 15}
        ]
        # Raw model metadata must not manufacture evidence of normalization.
        valid_clip["storyboard_normalization"] = {"original_shot_count": 6, "generation_clip_count": 1}
        cases = ({"truncated": True}, {"done_reason": "length"}, {"done_reason": None},
                 {"status": "cancelled"}, {"status": "completed"},
                 {"action": "screenplay"}, {"content": ""},
                 {"content": '{"title":"incomplete"'},
                 {"content": json.dumps(valid_clip, ensure_ascii=False)})
        for changes in cases:
            with self.subTest(changes=changes):
                record = {**copy.deepcopy(child), **changes}
                self.manager.jobs[child["id"]] = record
                self.assertFalse(await self.manager.recover_storyboard(child["id"]))
                self.assertEqual(scripts.stored_record(self.manager.get_job(child["id"])), record)
        self.assertFalse(await self.manager.recover_storyboard("0" * 32))
        self.assertEqual(self.renderer.render_calls, [])

    async def test_recovery_waits_for_existing_task_and_releases_model_under_gpu_lock(self):
        _, _, child = await self.failed_short_storyboard()
        record = self.manager.jobs[child["id"]]
        record["model_unloaded"] = False
        pending = asyncio.create_task(asyncio.sleep(60))
        self.manager.tasks[child["id"]] = pending
        try:
            self.assertFalse(await self.manager.recover_storyboard(child["id"]))
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            self.manager.tasks.pop(child["id"], None)

        async def unload(settings):
            self.assertTrue(self.lock.locked())
            self.assertEqual(settings, child["input"]["settings"])
            return True
        with patch.object(self.manager, "_unload", new=AsyncMock(side_effect=unload)) as released:
            self.assertTrue(await self.manager.recover_storyboard(child["id"]))
        released.assert_awaited_once()
        self.assertFalse(self.lock.locked())
        self.assertTrue(self.manager.get_job(child["id"])["model_unloaded"])

    async def test_recovery_failed_unload_keeps_failed_draft(self):
        _, _, child = await self.failed_short_storyboard()
        self.manager.jobs[child["id"]]["model_unloaded"] = False
        previous = self.manager.get_job(child["id"])
        with patch.object(self.manager, "_unload", new=AsyncMock(return_value=False)):
            self.assertFalse(await self.manager.recover_storyboard(child["id"]))
        self.assertEqual(self.manager.get_job(child["id"]), previous)
        self.assertFalse(self.lock.locked())

    async def test_recovery_disk_failure_rolls_back_and_resume_does_not_regenerate(self):
        _, failed, child = await self.failed_short_storyboard()
        before = self.manager.get_job(child["id"])
        actions = list(self.chat_actions)
        with patch.object(self.manager, "_save_jobs", side_effect=OSError("test recovery disk full")):
            response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
            self.assertEqual(response.status, 202, await response.text())
            result = await self.wait_pipeline(await response.json())
        self.assertEqual(result["status"], "failed")
        self.assertIn("test recovery disk full", result["error"])
        self.assertEqual(self.manager.get_job(child["id"]), before)
        self.assertEqual(self.chat_actions, actions)
        self.assertEqual(self.renderer.render_calls, [])
        archive = json.loads((self.manager.history_directory / f"{child['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(archive, before)

    async def test_idea_to_completed_movie_preserves_script_and_ref2va_plan(self):
        project = await self.project()
        result = await self.wait_pipeline(await self.start_pipeline(project))

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(self.chat_actions, ["screenplay", "storyboard"])
        self.assertTrue(result["screenplay_job_id"])
        self.assertTrue(result["storyboard_job_id"])
        self.assertEqual(len(result["video_job_ids"]), 2)
        self.assertEqual(len(self.renderer.render_calls), 2)
        self.assertEqual(len(self.renderer.assemble_calls), 1)
        self.assertEqual(self.renderer.assemble_calls[0]["video_job_ids"], result["video_job_ids"])
        self.assertTrue(result["video_url"].endswith("/file"))
        self.assertEqual(result["editor_project_id"], "e" * 32)
        self.assertEqual(result["export_id"], "f" * 32)
        self.assertEqual(self.manager.get_project(project["id"]), project)
        imported = self.app["shortfilms"].get(result["shortfilm_id"])
        self.assertEqual(imported["target_duration"], 10)
        self.assertTrue(all(shot["music"] == "N/A" for _, shot in flatten_shots(imported)))
        plans = self.renderer.plans[result["shortfilm_id"]]
        self.assertTrue(all(plan["payload"]["mode"] == "r2v" for plan in plans))
        self.assertTrue(all(plan["payload"]["references"][0]["image_asset_ids"] == [references.IMAGE_ID] for plan in plans))
        self.assertTrue(all(call["kwargs"].get("expected_payload") for call in self.renderer.render_calls))

    async def test_existing_manuscript_goes_directly_to_storyboard(self):
        project = await self.project(content="使用者編寫的完整劇本。")
        result = await self.wait_pipeline(await self.start_pipeline(project))

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(self.chat_actions, ["storyboard"])
        self.assertFalse(result.get("screenplay_job_id"))
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["input"]["project"]["content"], project["content"])
        self.assertEqual(self.manager.get_project(project["id"]), project)

    async def test_completed_source_uses_its_content_with_current_saved_cards(self):
        project = await self.project(content="原始編輯稿。")
        source = await self.complete(await self.submit(project))
        changed_reference = {**self.reference, "appearance": "短黑髮，改穿金色外套。"}
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": project["revision"], "content": "最新編輯稿。", "character_references": [changed_reference]})
        self.assertEqual(response.status, 200, await response.text())
        current = await response.json()
        result = await self.wait_pipeline(await self.start_pipeline(current, source_job_id=source["id"]))

        self.assertEqual(result["status"], "completed", result.get("error"))
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["input"]["project"]["content"], source["content"])
        self.assertEqual(child["input"]["project"]["character_references"], [changed_reference])
        self.assertEqual(self.manager.get_project(project["id"]), current)
        self.assertEqual(self.chat_actions, ["screenplay", "storyboard"])

    async def test_missing_picture_prerequisite_prevents_any_model_call(self):
        project = await self.project(character_references=[])
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.renderer.render_calls, [])

    async def test_unavailable_renderer_prevents_any_model_call(self):
        self.renderer.ready = False
        self.renderer.error = "test engine offline"
        project = await self.project()
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 503, await response.text())
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.renderer.render_calls, [])

    async def test_invalid_storyboard_preserves_failed_draft_without_video_submission(self):
        self.storyboard_reply = '{"title":"unfinished"'
        result = await self.wait_pipeline(await self.start_pipeline(await self.project(content="現有完整劇本。")))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["content"], self.storyboard_reply)
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.renderer.assemble_calls, [])

    async def test_unbound_storyboard_character_cannot_fall_back_to_text_only_video(self):
        unbound = copy.deepcopy(STORYBOARD)
        unbound["characters"][0]["name"] = "陌生角色"
        for shot in unbound["scenes"][0]["shots"]:
            shot["action"] = shot["action"].replace("阿青", "陌生角色")
            if shot["speaker"]:
                shot["speaker"] = "陌生角色"
        self.storyboard_reply = json.dumps(unbound, ensure_ascii=False)

        result = await self.wait_pipeline(await self.start_pipeline(await self.project(content="已有完整劇本。")))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.renderer.assemble_calls, [])

    async def test_bad_screenplay_timeline_does_not_start_storyboard_or_video(self):
        self.screenplay_reply = "0–15 秒：超出十秒預算。"
        result = await self.wait_pipeline(await self.start_pipeline(await self.project()))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.chat_actions, ["screenplay"])
        self.assertEqual(self.renderer.render_calls, [])

    async def test_failed_video_keeps_completed_shot_and_resume_reuses_it(self):
        self.renderer.fail_render_number = 2
        project = await self.project()
        failed = await self.wait_pipeline(await self.start_pipeline(project))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(self.renderer.render_calls), 2)
        completed_id = failed["video_job_ids"][0]
        self.assertEqual(self.video_jobs.jobs[completed_id]["status"], "completed")
        self.assertEqual(self.renderer.assemble_calls, [])
        changed_reference = {**self.reference, "appearance": "使用者之後修改的紅衣造型。"}
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "character_references": [changed_reference]})
        self.assertEqual(response.status, 200)
        self.renderer.fail_render_number = None

        response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        resumed = await self.wait_pipeline(await response.json())

        self.assertEqual(resumed["status"], "completed", resumed.get("error"))
        self.assertEqual(len(self.renderer.render_calls), 3)
        self.assertEqual(resumed["video_job_ids"][0], completed_id)
        self.assertEqual(resumed["snapshot"]["character_references"], [self.reference])
        self.assertEqual(self.chat_actions, ["screenplay", "storyboard"])
        self.assertEqual(len(self.renderer.assemble_calls), 1)

    async def test_failed_automatic_source_resumes_frozen_action_with_replacement_child(self):
        self.mode = "stream_error"
        project = await self.project(content="送出時的原始編輯稿。")
        response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": "rewrite", "auto_video": True})
        self.assertEqual(response.status, 202, await response.text())
        source = await response.json()
        failed = await self.wait_pipeline(source["video_pipeline"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["source_job_id"], source["id"])
        original = self.manager.get_job(source["id"])
        self.assertEqual(original["status"], "failed")
        self.assertTrue(original["content"])
        self.assertEqual(self.renderer.render_calls, [])
        changed_reference = {**self.reference, "appearance": "稍後修改的紅衣造型。"}
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "content": "稍後修改的劇本。", "character_references": [changed_reference]})
        self.assertEqual(response.status, 200, await response.text())
        current = await response.json()
        self.mode = "normal"

        response = await self.client.post(f"/api/scripts/video-pipelines/{failed['id']}/resume")
        self.assertEqual(response.status, 202, await response.text())
        completed = await self.wait_pipeline(await response.json())

        self.assertEqual(completed["status"], "completed", completed.get("error"))
        self.assertEqual(completed["source_job_id"], source["id"])
        self.assertNotEqual(completed["screenplay_job_id"], source["id"])
        replacement = self.manager.get_job(completed["screenplay_job_id"])
        self.assertEqual(replacement["action"], "rewrite")
        self.assertEqual(replacement["input"]["project"]["content"], project["content"])
        self.assertEqual(replacement["input"]["project"]["character_references"], [self.reference])
        self.assertEqual(self.manager.get_job(source["id"]), original)
        self.assertEqual(self.manager.get_project(project["id"]), current)
        self.assertEqual(self.chat_actions, ["screenplay", "screenplay", "storyboard"])

    async def test_active_pipeline_prevents_deleting_completed_source_and_storyboard(self):
        self.renderer.hold.clear()
        project = await self.project()
        source = await self.complete(await self.submit(project))
        pipeline = await self.start_pipeline(project, source_job_id=source["id"])
        await asyncio.wait_for(self.renderer.started.wait(), 4)
        active = self.pipeline_manager.get_pipeline(pipeline["id"])
        for job_id in (source["id"], active["storyboard_job_id"]):
            with self.subTest(job_id=job_id):
                self.assertEqual(self.manager.jobs[job_id]["status"], "completed")
                self.assertNotIn(job_id, self.manager.tasks)
                response = await self.client.delete(f"/api/scripts/jobs/{job_id}")
                self.assertEqual(response.status, 409, await response.text())
                self.assertIn(job_id, self.manager.jobs)

    async def test_async_assembly_progress_persists_export_ids_before_cancel(self):
        self.renderer.assembly_progress = {"progress": 95, "editor_project_id": "e" * 32, "export_id": "f" * 32}
        self.renderer.assembly_hold.clear()
        pipeline = await self.start_pipeline(await self.project())
        await asyncio.wait_for(self.renderer.assembly_started.wait(), 4)
        tracked = self.pipeline_manager.get_pipeline(pipeline["id"])
        persisted = json.loads((self.pipeline_manager.directory / f"{pipeline['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(tracked["stage"], "assemble")
        for record in (tracked, persisted):
            self.assertEqual(record["export_id"], "f" * 32)
            self.assertEqual(record["editor_project_id"], "e" * 32)
            self.assertEqual(record["progress"], 95)

        response = await self.client.post(f"/api/scripts/video-pipelines/{pipeline['id']}/cancel")

        self.assertEqual(response.status, 200, await response.text())
        cancelled = await self.wait_pipeline(await response.json())
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.renderer.cancel_export_records[-1]["export_id"], "f" * 32)
        self.assertEqual(self.renderer.cancel_export_records[-1]["editor_project_id"], "e" * 32)
        self.assertTrue(all(job["status"] == "completed" for job in self.video_jobs.jobs.values()))
        self.assertIsNone(cancelled["video_url"])

    async def test_export_tracking_write_failure_cancels_known_export(self):
        self.renderer.assembly_progress = {"progress": 94, "editor_project_id": "e" * 32, "export_id": "f" * 32}
        original_save = self.pipeline_manager._save
        failed_once = False

        def fail_tracking(record):
            nonlocal failed_once
            if record["stage"] == "assemble" and record.get("export_id") and not failed_once:
                failed_once = True
                raise OSError("test: cannot persist export tracking")
            return original_save(record)

        with patch.object(self.pipeline_manager, "_save", side_effect=fail_tracking):
            result = await self.wait_pipeline(await self.start_pipeline(await self.project()))

        self.assertTrue(failed_once)
        self.assertEqual(result["status"], "failed")
        self.assertIn("persist export tracking", result["error"])
        self.assertEqual(self.renderer.cancel_export_records[-1]["export_id"], "f" * 32)
        self.assertEqual(self.renderer.cancel_export_records[-1]["editor_project_id"], "e" * 32)
        self.assertIsNone(result["video_url"])

    async def test_video_tracking_write_failure_cancels_new_child(self):
        self.renderer.hold.clear()
        original_save = self.pipeline_manager._save
        failed_once = False

        def fail_tracking(record):
            nonlocal failed_once
            if record["stage"] == "render" and record["video_job_ids"] and not failed_once:
                failed_once = True
                raise OSError("test: cannot persist video tracking")
            return original_save(record)

        with patch.object(self.pipeline_manager, "_save", side_effect=fail_tracking):
            result = await self.wait_pipeline(await self.start_pipeline(await self.project()))

        self.assertTrue(failed_once)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.renderer.render_calls), 1)
        child_id = result["video_job_ids"][0]
        self.assertEqual(self.video_jobs.cancelled, [child_id])
        self.assertEqual(self.video_jobs.jobs[child_id]["status"], "cancelled")
        self.assertTrue(self.video_jobs.tasks[child_id].done())
        self.assertEqual(self.renderer.assemble_calls, [])

    async def test_close_cancels_only_its_children_and_preserves_restart_record(self):
        self.renderer.hold.clear()
        pipeline = await self.start_pipeline(await self.project())
        await asyncio.wait_for(self.renderer.started.wait(), 4)
        active = self.pipeline_manager.get_pipeline(pipeline["id"])
        unrelated_id = "d" * 32
        self.video_jobs.jobs[unrelated_id] = {"id": unrelated_id, "status": "running"}

        await self.pipeline_manager.close()

        result = self.pipeline_manager.get_pipeline(pipeline["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.video_jobs.cancelled, active["video_job_ids"])
        self.assertEqual(self.video_jobs.jobs[unrelated_id]["status"], "running")
        self.assertTrue(all(self.video_jobs.tasks[job_id].done() for job_id in active["video_job_ids"]))
        persisted = json.loads((self.pipeline_manager.directory / f"{pipeline['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["render_plan"], active["render_plan"])
        self.assertEqual(persisted["video_job_ids"], active["video_job_ids"])
        self.assertEqual(self.manager.get_job(result["storyboard_job_id"])["status"], "completed")
        self.assertEqual(self.renderer.assemble_calls, [])

    async def test_cancel_active_video_child_stops_next_shot_and_assembly(self):
        self.renderer.hold.clear()
        pipeline = await self.start_pipeline(await self.project())
        await asyncio.wait_for(self.renderer.started.wait(), 4)
        response = await self.client.post(f"/api/scripts/video-pipelines/{pipeline['id']}/cancel")
        self.assertEqual(response.status, 200, await response.text())
        result = await self.wait_pipeline(await response.json())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.video_jobs.cancelled), 1)
        self.assertEqual(len(self.renderer.render_calls), 1)
        self.assertEqual(self.renderer.assemble_calls, [])
        self.assertIn(pipeline["id"], self.renderer.cancel_export_calls)

    async def test_cancel_active_script_child_keeps_partial_output(self):
        self.mode = "hold"
        pipeline = await self.start_pipeline(await self.project())
        await asyncio.wait_for(self.started.wait(), 3)
        for _ in range(100):
            if any(job["content"] for job in self.manager.jobs.values()):
                break
            await asyncio.sleep(0.005)
        response = await self.client.post(f"/api/scripts/video-pipelines/{pipeline['id']}/cancel")
        self.assertEqual(response.status, 200, await response.text())
        result = await self.wait_pipeline(await response.json())
        self.assertEqual(result["status"], "cancelled")
        child = self.manager.get_job(result["screenplay_job_id"])
        self.assertEqual(child["status"], "cancelled")
        self.assertTrue(child["content"])
        self.assertEqual(self.renderer.render_calls, [])
        self.assertFalse(self.lock.locked())

    async def test_duplicate_active_project_returns_conflict_without_extra_model_job(self):
        self.mode = "hold"
        project = await self.project()
        await self.start_pipeline(project)
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 409, await response.text())
        self.assertLessEqual(len(self.manager.jobs), 1)

    async def test_restart_marks_running_pipeline_interrupted_without_requeue(self):
        self.renderer.hold.clear()
        pipeline = await self.start_pipeline(await self.project())
        await asyncio.wait_for(self.renderer.started.wait(), 4)
        before_calls = len(self.renderer.render_calls)

        restarted = script_video.ScriptVideoManager(self.app, self.manager, self.root)
        restored = restarted.get_pipeline(pipeline["id"])

        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(len(self.renderer.render_calls), before_calls)
        self.assertEqual(restored["snapshot"]["character_references"], [self.reference])
        self.assertFalse(any(not task.done() for task in restarted.tasks.values()))
        await restarted.close()

    async def test_cross_project_source_is_rejected(self):
        first = await self.project()
        source = await self.complete(await self.submit(first))
        second = await self.project(title="另一份劇本")
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": second["id"], "source_job_id": source["id"]})
        self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.chat_actions, ["screenplay"])

    async def test_truncated_completed_source_is_rejected(self):
        project = await self.project()
        source = await self.complete(await self.submit(project))
        self.manager.jobs[source["id"]]["truncated"] = True
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"], "source_job_id": source["id"]})
        self.assertEqual(response.status, 409, await response.text())
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.chat_actions, ["screenplay"])

    async def test_auto_video_bool_and_action_validation_happens_before_generation(self):
        project = await self.project(content="已有劇本。")
        for action, auto_video in (("screenplay", "true"), ("rewrite", 1), ("storyboard", True), ("outline", True), ("appearance", True)):
            with self.subTest(action=action, auto_video=auto_video):
                response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": action, "auto_video": auto_video})
                self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.manager.jobs, {})

    async def test_auto_video_waits_for_source_unload_and_uses_its_snapshot(self):
        self.mode = "unload_hold"
        project = await self.project(content="送出時的編輯稿。")
        response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": "rewrite", "auto_video": True})
        self.assertEqual(response.status, 202, await response.text())
        source = await response.json()
        await asyncio.wait_for(self.unload_started.wait(), 3)
        self.assertEqual(self.manager.jobs[source["id"]]["status"], "completed")
        self.assertFalse(self.manager.tasks[source["id"]].done())
        self.assertEqual(self.chat_actions, ["screenplay"])
        self.assertEqual(self.renderer.render_calls, [])
        changed_reference = {**self.reference, "appearance": "之後更改的紅色外套。"}
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "character_references": [changed_reference], "content": "使用者稍後修改的稿件。"})
        self.assertEqual(response.status, 200)
        current = await response.json()
        pipelines = self.pipeline_manager.list_pipelines()
        self.assertEqual(len(pipelines), 1)
        self.mode = "normal"
        self.finish.set()
        result = await self.wait_pipeline(pipelines[0])

        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["snapshot"]["character_references"], [self.reference])
        child = self.manager.get_job(result["storyboard_job_id"])
        self.assertEqual(child["input"]["project"]["character_references"], [self.reference])
        self.assertEqual(child["input"]["project"]["content"], SCREENPLAY)
        self.assertEqual(self.manager.get_project(project["id"]), current)
        self.assertEqual(self.chat_actions, ["screenplay", "storyboard"])

    async def test_status_is_read_only_and_advertises_renderer_readiness(self):
        self.renderer.ready = False
        self.renderer.error = "test engine offline"
        response = await self.client.get("/api/scripts/status")
        self.assertEqual(response.status, 200)
        status = await response.json()
        self.assertTrue(status["video_pipelines_supported"])
        self.assertFalse(status["video_ready"])
        self.assertEqual(status["video_error"], "test engine offline")
        self.assertEqual(self.renderer.preflight_calls, [])
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.renderer.assemble_calls, [])
        self.assertEqual(self.chat_requests, [])
        listing = await (await self.client.get("/api/scripts/video-pipelines")).json()
        self.assertEqual(listing["items"], [])

    async def test_duration_over_600_is_rejected_before_model_generation(self):
        project = await self.project(duration=601)
        response = await self.client.post("/api/scripts/video-pipelines", json={"project_id": project["id"]})
        self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.renderer.render_calls, [])

    async def test_storyboard_over_50_clips_stops_before_first_video_submission(self):
        too_many = copy.deepcopy(STORYBOARD)
        too_many["scenes"][0]["shots"] = [copy.deepcopy(STORYBOARD["scenes"][0]["shots"][0]) for _ in range(51)]
        self.storyboard_reply = json.dumps(too_many, ensure_ascii=False)

        result = await self.wait_pipeline(await self.start_pipeline(await self.project(duration=255, content="已有完整劇本。")))

        self.assertEqual(result["status"], "failed")
        self.assertIn("50", result["error"])
        self.assertEqual(self.renderer.render_calls, [])
        self.assertEqual(self.renderer.assemble_calls, [])


if __name__ == "__main__":
    unittest.main()
