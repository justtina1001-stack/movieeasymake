import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

import app as studio
from queue_presentation import build_queue_view
from queue_snapshot import normalize_queue_snapshot, queue_unavailable


def snapshot(running=(), pending=()):
    return normalize_queue_snapshot({
        "queue_running": [[0, prompt_id] for prompt_id in running],
        "queue_pending": [[index, prompt_id] for index, prompt_id in enumerate(pending)],
    })


def job(status="queued", **values):
    return {"status": status, "created_at": "2026-09-14T09:00:00+08:00", **values}


class QueuePresentationTests(unittest.TestCase):
    def test_engine_snapshot_overrides_stale_running_status_and_progress(self):
        video = {
            "active-video": job("running", prompt_id="active-prompt", name="正在生成", progress=37),
            "waiting-video": job("running", prompt_id="waiting-prompt", name="下一部", progress=91),
        }
        view = build_queue_view(snapshot(["active-prompt"], ["colleague-prompt", "waiting-prompt"]), video, {}, {})
        self.assertEqual((view["running_count"], view["pending_count"]), (1, 2))
        self.assertEqual(view["jobs"]["active-video"]["phase"], "engine_running")
        self.assertEqual(view["jobs"]["active-video"]["progress"], 37)
        waiting = view["jobs"]["waiting-video"]
        self.assertEqual(waiting["phase"], "engine_waiting")
        self.assertEqual(waiting["position"], 2)
        self.assertEqual(waiting["ahead_count"], 2)
        self.assertIsNone(waiting["progress"])
        self.assertEqual(view["local_active"], [])

    def test_pending_heap_is_shown_in_actual_engine_priority_order(self):
        engine = normalize_queue_snapshot({
            "queue_running": [],
            "queue_pending": [[4, "later"], [-1, "front"], [2, "mine"]],
        })
        view = build_queue_view(engine, {"video": job("running", prompt_id="mine")}, {}, {})
        self.assertEqual([row["position"] for row in view["pending"]], [1, 2, 3])
        self.assertEqual(view["pending"][1]["job_id"], "video")
        self.assertEqual(view["jobs"]["video"]["ahead_count"], 1)

    def test_local_lock_waiting_has_no_invented_global_rank(self):
        video = {"video": job("queued", name="本機影片")}
        music = {"music": job("queued", name="配樂", created_at="2026-09-14T08:00:00+08:00")}
        voice = {"voice": job("running", name="人聲")}
        view = build_queue_view(snapshot(["someone-else"], ["remote-pending"]), video, music, voice)
        self.assertEqual(view["local_waiting_count"], 2)
        self.assertEqual([row["job_id"] for row in view["local_waiting"]], ["music", "video"])
        self.assertEqual([row["kind"] for row in view["local_waiting"]], ["music", "video"])
        for row in view["local_waiting"]:
            self.assertEqual(row["phase"], "local_waiting")
            self.assertIsNone(row["position"])
            self.assertIsNone(row["ahead_count"])
        self.assertEqual(view["jobs"]["voice"]["phase"], "local_processing")
        self.assertEqual((view["running_count"], view["pending_count"]), (1, 1))

    def test_music_can_be_identified_in_shared_engine_queue(self):
        view = build_queue_view(snapshot([], ["music-prompt"]), {}, {
            "music": job("running", prompt_id="music-prompt", name="片尾配樂"),
        }, {})
        self.assertEqual(view["pending"][0]["kind"], "music")
        self.assertEqual(view["jobs"]["music"]["phase"], "engine_waiting")

    def test_long_replacement_parent_borrows_hidden_child_without_double_count(self):
        video = {
            "parent": job("running", name="長片替換", active_child_id="child", progress=20),
            "child": job("running", name="隱藏片段", prompt_id="child-prompt", parent_job_id="parent",
                         segment_index=2, hidden=True, progress=45),
        }
        view = build_queue_view(snapshot(["child-prompt"]), video, {}, {})
        self.assertEqual(view["running_count"], 1)
        self.assertEqual(len(view["running"]), 1)
        self.assertEqual(view["running"][0]["job_id"], "parent")
        self.assertIn("長片替換", view["running"][0]["title"])
        self.assertIn("第 2 段", view["running"][0]["title"])
        self.assertEqual(view["jobs"]["parent"]["phase"], "engine_running")
        self.assertEqual(view["jobs"]["parent"]["progress"], 45)
        self.assertEqual(view["local_waiting_count"], 0)
        self.assertEqual(view["local_active"], [])

    def test_long_replacement_local_child_is_counted_once(self):
        video = {
            "parent": job("running", active_child_id="child"),
            "child": job("queued", hidden=True, parent_job_id="parent"),
        }
        view = build_queue_view(snapshot(), video, {}, {})
        self.assertEqual(view["local_waiting_count"], 1)
        self.assertEqual(view["local_active"], [])
        self.assertEqual(view["jobs"]["parent"]["phase"], "local_waiting")
        self.assertIsNone(view["jobs"]["parent"]["position"])

    def test_finished_child_does_not_hide_parent_finishing(self):
        video = {
            "parent": job("preparing", active_child_id="child", output=None,
                          current_node="合併替換片段並處理聲音"),
            "child": job("completed", parent_job_id="parent", hidden=True),
        }
        view = build_queue_view(snapshot(), video, {}, {})
        self.assertEqual(len(view["local_active"]), 1)
        self.assertEqual(view["jobs"]["parent"]["phase"], "finishing")
        self.assertNotIn("child", view["jobs"])

    def test_empty_live_snapshot_does_not_promote_stale_running_job(self):
        view = build_queue_view(snapshot(), {"old": job("running", prompt_id="completed-upstream")}, {}, {})
        self.assertTrue(view["available"])
        self.assertEqual((view["running_count"], view["pending_count"]), (0, 0))
        self.assertEqual(view["jobs"]["old"]["phase"], "unknown")
        self.assertIsNone(view["jobs"]["old"]["position"])
        self.assertEqual(view["running"], [])

    def test_unavailable_snapshot_is_unknown_not_idle_or_stale_queue(self):
        unavailable = {**queue_unavailable("暫時離線"), "running_count": 1,
                       "running": [{"prompt_id": "old-prompt", "position": 0}]}
        view = build_queue_view(unavailable, {"old": job("running", prompt_id="old-prompt")}, {}, {})
        self.assertFalse(view["available"])
        self.assertIsNone(view["running_count"])
        self.assertIsNone(view["pending_count"])
        self.assertEqual(view["running"], [])
        self.assertEqual(view["jobs"]["old"]["phase"], "unknown")
        self.assertEqual(view["error"], "暫時離線")

    def test_terminal_jobs_are_omitted_and_inputs_not_mutated(self):
        video = {status: job(status) for status in ("completed", "failed", "cancelled")}
        engine = snapshot()
        before = copy.deepcopy((engine, video))
        view = build_queue_view(engine, video, {}, {})
        self.assertEqual(view["jobs"], {})
        self.assertEqual(view["local_waiting"], [])
        self.assertEqual(view["local_active"], [])
        self.assertEqual((engine, video), before)

    def test_browser_projection_drops_workflows_prompts_ids_and_tokens(self):
        engine = snapshot(["secret-engine-id"], ["secret-colleague-id"])
        engine["running"][0].update(prompt="secret-engine-dialogue", extra_data={"token": "secret-engine-token"})
        video = {"public-local-job-id": job(
            "running", name="我自己的影片", prompt_id="secret-engine-id", prompt="secret-user-dialogue",
            workflow={"inputs": {"text": "secret-workflow-dialogue"}}, remote_access_token="secret-user-token",
        )}
        view = build_queue_view(engine, video, {}, {})
        encoded = json.dumps(view, ensure_ascii=False)
        for secret in ("secret-engine-id", "secret-colleague-id", "secret-engine-dialogue", "secret-engine-token",
                       "secret-user-dialogue", "secret-workflow-dialogue", "secret-user-token"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(view["pending"][0]["owner"], "其他使用者")
        self.assertEqual(view["running"][0]["job_id"], "public-local-job-id")


class QueueAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.enterContext(patch.multiple(
            studio, DATA_DIR=root, ASSET_DIR=root / "assets", JOB_DIR=root / "jobs",
            OUTPUT_DIR=root / "outputs", CONFIG_PATH=root / "config.json",
        ))
        self.app = studio.create_app()
        # Handler tests never start ComfyUI, recover jobs, or bind the gateway.
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.gateway = self.app["shared_gateway"]
        self.gateway.store.config["users"] = [{"id": "colleague", "name": "同事阿華", "token_hash": "private-token-hash"}]
        self.gateway.store.state["prompt_owners"] = {"colleague-prompt": {"user_id": "colleague"}}

    def configure(self, *, role="host", mode="local", base_url="http://127.0.0.1:8188"):
        settings = self.app["settings"].current
        settings.studio_role = role
        settings.mode = mode
        settings.base_url = base_url
        self.app["comfy"].configure(settings)

    async def read_queue(self, engine=None):
        if engine is None:
            engine = snapshot(["colleague-prompt"])
        with patch.object(self.app["comfy"], "queue_status", new=AsyncMock(return_value=engine)) as query:
            response = await self.client.get("/api/queue")
        query.assert_awaited_once()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return await response.json()

    async def test_local_host_can_resolve_colleague_names_on_same_engine(self):
        self.configure()
        view = await self.read_queue()
        self.assertEqual(view["running"][0]["owner"], "同事阿華")
        self.assertEqual(view["running"][0]["title"], "共用引擎工作")
        self.assertIsNone(view["running"][0]["job_id"])
        encoded = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("colleague-prompt", encoded)
        self.assertNotIn("private-token-hash", encoded)

    async def test_client_never_resolves_other_colleagues_names(self):
        self.configure(role="client")
        view = await self.read_queue()
        self.assertEqual(view["running"][0]["owner"], "其他使用者")
        self.assertNotIn("同事阿華", json.dumps(view, ensure_ascii=False))

    async def test_remote_host_does_not_resolve_names_from_local_gateway(self):
        self.configure(mode="remote")
        view = await self.read_queue()
        self.assertEqual(view["running"][0]["owner"], "其他使用者")

    async def test_local_host_does_not_mix_names_from_another_upstream(self):
        self.configure(base_url="http://127.0.0.1:8288")
        view = await self.read_queue()
        self.assertEqual(view["running"][0]["owner"], "其他使用者")

    async def test_api_combines_video_music_and_voice_without_engine_start(self):
        self.configure(role="client", mode="remote")
        self.app["jobs"].jobs["video"] = job("running", prompt_id="video-prompt", name="影片")
        self.app["music_jobs"].jobs["music"] = job("queued", name="音樂")
        self.app["voice_jobs"].jobs["voice"] = job("running", name="語音")
        with patch.object(self.app["comfy"], "ensure_running", new=AsyncMock()) as start:
            view = await self.read_queue(snapshot(["colleague-prompt"], ["video-prompt"]))
        start.assert_not_awaited()
        self.assertEqual(view["jobs"]["video"]["phase"], "engine_waiting")
        self.assertEqual(view["jobs"]["video"]["position"], 1)
        self.assertEqual(view["jobs"]["video"]["ahead_count"], 1)
        self.assertEqual(view["jobs"]["music"]["phase"], "local_waiting")
        self.assertEqual(view["jobs"]["voice"]["phase"], "local_processing")

    async def test_unavailable_api_response_does_not_claim_idle(self):
        view = await self.read_queue(queue_unavailable())
        self.assertFalse(view["available"])
        self.assertIsNone(view["running_count"])
        self.assertIsNone(view["pending_count"])
        self.assertTrue(view["error"])

    def cancellation_job(self, kind, *, status="running", prompt_id="own-pending-prompt"):
        manager_key = "jobs" if kind == "video" else f"{kind}_jobs"
        api_prefix = "/api/jobs" if kind == "video" else f"/api/{kind}/jobs"
        manager = self.app[manager_key]
        job_id = f"cancel-{kind}"
        record = job(status, id=job_id, name="測試取消工作", prompt_id=prompt_id,
                     current_node="等待引擎", progress=0)
        manager.jobs[job_id] = record
        manager.cancel_events[job_id] = asyncio.Event()
        return manager, record, f"{api_prefix}/{job_id}/cancel"

    async def assert_cancel_rejection_keeps_monitoring(self, kind):
        from queue_cancel import QueueCancelError

        manager, record, path = self.cancellation_job(kind)
        before = copy.deepcopy(record)

        async def reject(prompt_id):
            self.assertEqual(prompt_id, "own-pending-prompt")
            self.assertFalse(manager.cancel_events[record["id"]].is_set())
            raise QueueCancelError("無法安全確認引擎佇列，請稍後再試。")

        with patch.object(self.app["comfy"], "interrupt", new=AsyncMock(side_effect=reject)) as interrupt:
            response = await self.client.post(path)
        interrupt.assert_awaited_once_with("own-pending-prompt")
        self.assertEqual(response.status, 409)
        self.assertIn("無法安全確認", (await response.json())["error"])
        self.assertEqual(record, before)
        self.assertFalse(manager.cancel_events[record["id"]].is_set())

    async def test_video_cancel_rejection_keeps_running_state_and_event_unset(self):
        await self.assert_cancel_rejection_keeps_monitoring("video")

    async def test_music_cancel_rejection_keeps_running_state_and_event_unset(self):
        await self.assert_cancel_rejection_keeps_monitoring("music")

    async def assert_pending_cancel_targets_own_prompt(self, kind):
        manager, record, path = self.cancellation_job(kind)
        queue = await self.read_queue(snapshot(["colleague-prompt"], ["own-pending-prompt"]))
        self.assertEqual(queue["jobs"][record["id"]]["phase"], "engine_waiting")

        async def confirm(prompt_id):
            self.assertEqual(prompt_id, "own-pending-prompt")
            self.assertEqual(record["status"], "running")
            self.assertFalse(manager.cancel_events[record["id"]].is_set())

        with patch.object(self.app["comfy"], "interrupt", new=AsyncMock(side_effect=confirm)) as interrupt:
            response = await self.client.post(path)
        interrupt.assert_awaited_once_with("own-pending-prompt")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "cancelled")
        self.assertTrue(manager.cancel_events[record["id"]].is_set())
        queue = await self.read_queue(snapshot(["colleague-prompt"]))
        self.assertNotIn(record["id"], queue["jobs"])
        self.assertEqual(queue["running_count"], 1)

    async def test_video_pending_cancel_records_cancelled_after_exact_prompt_confirmation(self):
        await self.assert_pending_cancel_targets_own_prompt("video")

    async def test_music_pending_cancel_records_cancelled_after_exact_prompt_confirmation(self):
        await self.assert_pending_cancel_targets_own_prompt("music")

    async def assert_local_cancel_never_interrupts_shared_engine(self, kind):
        manager, record, path = self.cancellation_job(kind, status="queued", prompt_id=None)
        with patch.object(self.app["comfy"], "interrupt", new=AsyncMock()) as interrupt:
            response = await self.client.post(path)
        interrupt.assert_not_awaited()
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "cancelled")
        self.assertTrue(manager.cancel_events[record["id"]].is_set())
        queue = await self.read_queue(snapshot(["colleague-prompt"]))
        self.assertEqual(queue["local_waiting_count"], 0)
        self.assertNotIn(record["id"], queue["jobs"])
        self.assertEqual(queue["running_count"], 1)

    async def test_local_queued_video_cancel_never_interrupts_colleague(self):
        await self.assert_local_cancel_never_interrupts_shared_engine("video")

    async def test_local_queued_music_cancel_never_interrupts_colleague(self):
        await self.assert_local_cancel_never_interrupts_shared_engine("music")

    async def test_local_queued_voice_cancel_is_removed_from_waiting_count(self):
        await self.assert_local_cancel_never_interrupts_shared_engine("voice")

    async def test_replacement_child_cancel_rejection_preserves_parent_and_child(self):
        from queue_cancel import QueueCancelError

        manager, child, _ = self.cancellation_job("video")
        parent_id = "cancel-parent"
        parent = job("preparing", id=parent_id, active_child_id=child["id"], batch_type="replace_long")
        child.update(parent_job_id=parent_id, hidden=True, segment_index=1)
        manager.jobs[parent_id] = parent
        manager.cancel_events[parent_id] = asyncio.Event()
        before = copy.deepcopy((parent, child))
        with patch.object(self.app["comfy"], "interrupt", new=AsyncMock(side_effect=QueueCancelError("請稍後再試。"))) as interrupt:
            response = await self.client.post(f"/api/jobs/{parent_id}/cancel")
        interrupt.assert_awaited_once_with("own-pending-prompt")
        self.assertEqual(response.status, 409)
        self.assertEqual((parent, child), before)
        self.assertFalse(manager.cancel_events[parent_id].is_set())
        self.assertFalse(manager.cancel_events[child["id"]].is_set())


if __name__ == "__main__":
    unittest.main()
