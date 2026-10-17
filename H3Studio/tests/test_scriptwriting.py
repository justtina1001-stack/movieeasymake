import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from scriptwriting import (
    DEFAULT_SETTINGS, ScriptwritingError, ScriptwritingManager, build_messages,
    local_base_url, normalize_settings, parse_storyboard, register_scriptwriting_routes,
    validate_context, validate_storyboard,
)
from shortfilm import ShortFilmStore


STORYBOARD = {
    "title": "回家的路", "synopsis": "阿青在車站找到回家的勇氣。",
    "characters": [{"name": "阿青", "description": "穿藍色外套的年輕旅人。"}],
    "scenes": [{"title": "車站", "location": "月台", "time_of_day": "日",
                "shots": [{"title": "出發", "duration": 5, "action": "阿青拿起行李，走向列車。",
                           "dialogue": "我們回家吧。", "speaker": "阿青", "sound": "列車鳴笛。",
                           "camera": "tracking", "shot_size": "medium"}]}],
}

TIMED_SCREENPLAY = (
    "# 回家的路\n全片風格：自然明亮。\n聲音規則：零 BGM。\n"
    "1. 0.0–5.0s｜抵達月台\n鏡頭／景別：24mm 廣角。\n地點：車站月台。\n"
    "角色／位置：阿青在畫面左側。\n動作／表情：提著行李走近候車線。\n"
    "運鏡：由左向右跟拍。\nVFX：無。\n聲音：腳步聲。\n對白：無。\n"
    "2. 5.0–10.0s｜確認方向\n鏡頭／景別：50mm 中景。\n地點：車門前。\n"
    "角色／位置：阿青面向列車。\n動作／表情：抬頭確認方向，露出安心表情。\n"
    "運鏡：固定。\nVFX：無。\n聲音：列車鳴笛。\n對白：我們回家吧。\n"
    "3. 10.0–15.0s｜出發\n鏡頭／景別：50mm 中景。\n地點：列車窗邊。\n"
    "角色／位置：阿青坐在窗邊。\n動作／表情：向窗外揮手。\n運鏡：緩慢拉遠。\n"
    "VFX：無。\n聲音：列車行駛聲。\n對白：無。\n收尾：15.0 秒切黑。"
)


def dict_screenplay():
    """Synthetic reproduction of the observed Python-dict screenplay shape."""
    return {
        "title": "回家的路", "genre": "劇情", "style": "自然明亮", "voice": "零 BGM，保留腳步聲與列車聲。",
        "character_references": [{"name": "阿青", "type": "character", "reference": "角色參考圖1", "appearance": "藍色外套"}],
        "scene_references": {"車站": "場景參考圖2"},
        "scenes": [
            {"start": "0.0", "end": "5.0", "shot_spec": "24mm 廣角，車站月台，低機位。",
             "characters": [{"name": "阿青", "position": "畫面左側", "action": "提著行李走近候車線", "expression": "期待"}],
             "camera_movement": "由左向右跟拍", "vfx": "無", "sound": "腳步聲", "dialogue": "無"},
            {"start": "5.0", "end": "10.0", "shot_spec": "50mm 中景，列車門前，平視。",
             "characters": [{"name": "阿青", "position": "面向列車", "action": "抬頭確認方向", "expression": "安心"}],
             "camera_movement": "固定", "vfx": "無", "sound": "列車鳴笛", "dialogue": "我們回家吧。"},
            {"start": "10.0", "end": "15.0", "shot_spec": "50mm 中景，列車窗邊，平視。",
             "characters": [{"name": "阿青", "position": "坐在窗邊", "action": "向窗外揮手", "expression": "微笑"}],
             "camera_movement": "緩慢拉遠", "vfx": "無", "sound": "列車行駛聲", "dialogue": "無", "ending": "切黑"},
        ],
    }


def stored_record(view):
    """Detail-only presentation fields are computed, never archive mutations."""
    return {key: value for key, value in view.items()
            if key not in {"readable_content", "readable_content_source", "readable_content_note"}}


class ScriptValidationTests(unittest.TestCase):
    def test_local_origin_rejects_remote_credentials_and_paths(self):
        for value in ("http://127.0.0.1:11434/", "https://localhost:11434", "http://[::1]:11434"):
            self.assertEqual(local_base_url(value), value.rstrip("/"))
        for value in ("https://ollama.com", "http://127.0.0.2", "http://127.0.0.1.evil.test",
                      "http://user:secret@localhost", "http://localhost/api", "http://localhost?token=x",
                      "http://localhost#x", "ftp://localhost", "http://localhost:99999", "http://localhost:0"):
            with self.subTest(value=value), self.assertRaises(ScriptwritingError):
                local_base_url(value)

    def test_settings_validate_numeric_bounds_and_cloud_names(self):
        for raw in ({"model": "a:cloud"}, {"temperature": float("nan")}, {"temperature": True},
                    {"num_ctx": "8192"}, {"num_predict": 8192}, {"num_predict": -1},
                    {"base_url": "https://example.com"}, {"api_key": "secret"}, []):
            with self.subTest(raw=raw), self.assertRaises(ScriptwritingError):
                normalize_settings(raw)
        self.assertEqual(normalize_settings({})["model"], "h3-script-hermes4:14b-q5_k_m")
        self.assertEqual(normalize_settings({"model": "qwen3.5:9b"})["model"], "qwen3.5:9b")

    def test_storyboard_preserves_content_and_rejects_unsafe_structure(self):
        self.assertEqual(validate_storyboard(STORYBOARD), STORYBOARD)
        self.assertEqual(parse_storyboard("```json\n" + json.dumps(STORYBOARD) + "\n```"), STORYBOARD)
        for key, value in (("duration", 16), ("duration", True), ("action", ""),
                           ("camera", "zoom_anything"), ("shot_size", "cinematic"),
                           ("speaker", "不存在"), ("speaker", "")):
            invalid = copy.deepcopy(STORYBOARD)
            invalid["scenes"][0]["shots"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ScriptwritingError):
                validate_storyboard(invalid)
        duplicate = copy.deepcopy(STORYBOARD)
        duplicate["characters"].append(copy.deepcopy(duplicate["characters"][0]))
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(duplicate)
        with self.assertRaises(ScriptwritingError):
            parse_storyboard('{"title":"unfinished"')
        invalid = copy.deepcopy(STORYBOARD)
        invalid["scenes"] *= 101
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(invalid)
        invalid = copy.deepcopy(STORYBOARD)
        invalid["scenes"][0]["shots"] *= 501
        with self.assertRaises(ScriptwritingError):
            validate_storyboard(invalid)
        invalid = copy.deepcopy(STORYBOARD)
        invalid["scenes"][0]["shots"][0]["duration"] = 15
        invalid["scenes"][0]["shots"] *= 241
        with self.assertRaisesRegex(ScriptwritingError, "3600"):
            validate_storyboard(invalid)

    def test_aggregate_context_guard_does_not_silently_truncate(self):
        settings = dict(DEFAULT_SETTINGS)
        with self.assertRaises(ScriptwritingError):
            validate_context([{"content": "中" * 8000}], settings)
        validate_context([{"content": "短篇故事"}], settings)

    def test_outline_reads_existing_manuscript(self):
        project = {key: "" for key in ("title", "idea", "genre", "characters", "style", "instructions")}
        project.update(duration=60, content="阿青獨自登上列車。")
        messages = build_messages(project, "outline", "")
        self.assertIn('"existing_script": "阿青獨自登上列車。"', messages[1]["content"])
        self.assertIn("150 字作為參考上限", messages[0]["content"])
        self.assertIn("同一段夜景維持夜間照明", messages[0]["content"])
        self.assertIn("臺灣慣用", messages[0]["content"])


class ScriptAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        (self.static / "scripts.html").write_text("腳本編寫", encoding="utf-8")
        self.lock = asyncio.Lock()
        self.mode = "normal"
        self.tags = [{"name": DEFAULT_SETTINGS["model"], "size": 18_000_000_000}]
        self.show = {"capabilities": ["completion", "thinking"],
                     "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 40_960}}
        self.reply = TIMED_SCREENPLAY
        self.chat_requests = []
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
        self.app["jobs"] = SimpleNamespace(gpu_lock=self.lock)
        self.app["shortfilms"] = ShortFilmStore(self.root / "shortfilms")
        self.manager = register_scriptwriting_routes(self.app, self.static, self.root)
        self.manager.settings["base_url"] = str(self.ollama.make_url("/")).rstrip("/")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        self.finish.set()
        await self.client.close()
        await self.ollama.close()
        self.temp.cleanup()

    async def fake_tags(self, request):
        if self.mode == "redirect":
            raise web.HTTPFound("/redirect-target")
        if self.mode == "bad_tags":
            return web.json_response({"models": "not a list"})
        body = json.dumps({"models": self.tags}, ensure_ascii=False).encode()
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(body[:7])
        await asyncio.sleep(0.005)
        await response.write(body[7:])
        return response

    async def fake_show(self, request):
        return web.json_response(self.show)

    async def fake_chat(self, request):
        payload = await request.json()
        self.chat_requests.append(payload)
        self.assertTrue(self.lock.locked(), "generation must hold the shared GPU lock")
        self.started.set()
        if self.mode == "http_error":
            return web.json_response({"error": "out of memory"}, status=500)
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        try:
            first = json.dumps({"message": {"content": self.reply[:9], "thinking": "DO NOT SHOW"}, "done": False}, ensure_ascii=False).encode() + b"\n"
            # Split in the middle of a UTF-8 character as well as across JSON lines.
            await response.write(first[:34])
            await asyncio.sleep(0.005)
            await response.write(first[34:])
            if self.mode == "hold":
                await self.finish.wait()
            if self.mode == "abrupt":
                return response
            if self.mode == "malformed":
                await response.write(b"{not valid json}\n")
                return response
            if self.mode == "stream_error":
                await response.write(b'{"error":"out of memory"}\n')
                return response
            final = {"message": {"content": self.reply[9:]}, "done": True,
                     "done_reason": "length" if self.mode == "length" else "stop"}
            # A valid last record need not end in a newline.
            await response.write(json.dumps(final, ensure_ascii=False).encode())
        except (ConnectionResetError, RuntimeError):
            pass
        return response

    async def fake_unload(self, request):
        self.unloads.append({"payload": await request.json(), "lock_held": self.lock.locked()})
        self.unload_started.set()
        if self.mode == "unload_hold":
            await self.finish.wait()
        return web.json_response({"done": True})

    async def redirect_target(self, request):
        self.redirect_hits += 1
        return web.json_response({"models": self.tags})

    async def fake_free(self, request):
        self.free_calls.append(await request.json())
        self.assertTrue(self.lock.locked())
        return web.json_response({})

    async def create(self, **fields):
        response = await self.client.post("/api/scripts/projects", json={"title": "回家的路", "idea": "阿青回家。", **fields})
        self.assertEqual(response.status, 201, await response.text())
        return await response.json()

    async def submit(self, project, action="screenplay", **fields):
        response = await self.client.post("/api/scripts/jobs", json={"project_id": project["id"], "action": action, **fields})
        self.assertEqual(response.status, 202, await response.text())
        return await response.json()

    async def complete(self, job):
        task = self.manager.tasks.get(job["id"])
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 4)
        response = await self.client.get(f"/api/scripts/jobs/{job['id']}")
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_projects_revision_export_and_persistence(self):
        project = await self.create(content="阿青：走吧。")
        updated = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "title": "新片名"})
        self.assertEqual(updated.status, 200)
        value = await updated.json()
        self.assertEqual(value["revision"], 2)
        self.assertEqual(value["content"], "阿青：走吧。")
        conflict = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "content": "old tab"})
        self.assertEqual(conflict.status, 409)
        self.assertEqual(self.manager.projects[project["id"]]["content"], "阿青：走吧。")
        for format_name in ("txt", "md", "json"):
            response = await self.client.get(f"/api/scripts/projects/{project['id']}/export?format={format_name}")
            self.assertEqual(response.status, 200)
            self.assertIn("阿青：走吧。", await response.text())
            self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        response = await self.client.get(f"/api/scripts/projects/{project['id']}/export?format=html")
        self.assertEqual(response.status, 400)
        loaded = ScriptwritingManager(web.Application(), self.root)
        self.assertEqual(loaded.get_project(project["id"]), value)
        self.assertFalse(list(self.manager.directory.glob("*.tmp")))

    async def test_untimed_handwritten_manuscript_remains_saveable_and_exportable(self):
        manuscript = "第 1 場 外景 車站 日\n阿青：我們回家吧。"
        project = await self.create(content=manuscript)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                         json={"revision": project["revision"], "content": manuscript + "\n列車鳴笛。"})
        self.assertEqual(response.status, 200)
        saved = await response.json()
        self.assertEqual(saved["content"], manuscript + "\n列車鳴笛。")
        export = await self.client.get(f"/api/scripts/projects/{project['id']}/export?format=txt")
        self.assertEqual(export.status, 200)
        self.assertIn(saved["content"], await export.text())
        self.assertEqual(self.chat_requests, [])
        persisted = ScriptwritingManager(web.Application(), self.root)
        self.assertEqual(persisted.get_project(project["id"])["content"], saved["content"])

    async def test_page_settings_status_and_fragmented_inventory(self):
        self.assertEqual((await self.client.get("/scripts")).status, 200)
        response = await self.client.get("/api/scripts/status")
        status = await response.json()
        self.assertTrue(status["ready"], status)
        self.assertEqual(status["models"][0]["name"], DEFAULT_SETTINGS["model"])
        response = await self.client.post("/api/scripts/settings", json={"temperature": 0.5})
        self.assertEqual(response.status, 200)
        persisted = json.loads(self.manager.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["temperature"], 0.5)
        before = copy.deepcopy(self.manager.settings)
        for payload in ({"base_url": "https://example.com"}, {"model": "x:cloud"}, {"temperature": True}):
            response = await self.client.post("/api/scripts/settings", json=payload)
            self.assertEqual(response.status, 400)
            self.assertEqual(self.manager.settings, before)

    async def test_generation_keeps_input_snapshot_and_never_overwrites_edits(self):
        await self.lock.acquire()
        project = await self.create()
        job = await self.submit(project, instructions="結尾保留希望。")
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "idea": "修改後故事", "content": "使用者新稿"})
        self.assertEqual(response.status, 200)
        self.lock.release()
        result = await self.complete(job)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["content"], self.reply)
        self.assertEqual(result["input"]["project"]["idea"], "阿青回家。")
        self.assertEqual(self.manager.projects[project["id"]]["content"], "使用者新稿")
        self.assertFalse(self.chat_requests[0]["think"])
        self.assertIn("繁體中文", self.chat_requests[0]["messages"][0]["content"])
        self.assertNotIn("DO NOT SHOW", result["content"])
        self.assertEqual(len(self.unloads), 1)
        self.assertTrue(result["model_unloaded"])
        self.assertEqual(self.unloads[0]["payload"]["keep_alive"], 0)
        self.assertTrue(self.unloads[0]["lock_held"])
        jobs = await (await self.client.get("/api/scripts/jobs")).json()
        self.assertEqual(jobs["items"][0]["content"], self.reply)
        self.assertNotIn("input", jobs["items"][0])

    async def test_rewrite_requires_content_and_input_validation_creates_no_job(self):
        project = await self.create()
        for payload in ({"project_id": project["id"], "action": "rewrite"},
                        {"project_id": project["id"], "action": "unknown"},
                        {"project_id": [], "action": "screenplay"}, []):
            response = await self.client.post("/api/scripts/jobs", json=payload)
            self.assertIn(response.status, (400, 404))
        self.assertEqual(self.manager.jobs, {})
        response = await self.client.post("/api/scripts/projects", json={"characters": ["wrong type"]})
        self.assertEqual(response.status, 400)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"content": "x"})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/api/scripts/settings", data="{bad", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)

    async def test_stream_failures_preserve_partial_content_and_unload(self):
        project = await self.create()
        for mode in ("abrupt", "malformed", "stream_error", "http_error"):
            self.mode = mode
            job = await self.submit(project)
            result = await self.complete(job)
            self.assertEqual(result["status"], "failed", (mode, result))
            self.assertTrue(result["error"])
            if mode != "http_error":
                self.assertEqual(result["content"], self.reply[:9])
        self.assertEqual(len(self.unloads), 4)
        self.assertFalse(self.lock.locked())

    async def test_active_cancel_closes_stream_unloads_and_retains_partial(self):
        self.mode = "hold"
        project = await self.create()
        job = await self.submit(project)
        await asyncio.wait_for(self.started.wait(), 2)
        for _ in range(100):
            if self.manager.jobs[job["id"]]["content"]:
                break
            await asyncio.sleep(0.01)
        response = await self.client.post(f"/api/scripts/jobs/{job['id']}/cancel")
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["content"], self.reply[:9])
        self.assertEqual(len(self.unloads), 1)
        self.assertFalse(self.lock.locked())
        self.assertNotIn(job["id"], self.manager.tasks)

    async def test_queued_cancel_does_not_evict_active_model(self):
        await self.lock.acquire()
        job = await self.submit(await self.create())
        response = await self.client.post(f"/api/scripts/jobs/{job['id']}/cancel")
        self.assertEqual((await response.json())["status"], "cancelled")
        self.assertEqual(self.chat_requests, [])
        self.assertEqual(self.unloads, [])
        self.lock.release()

    async def test_cloud_inventory_and_show_metadata_are_blocked(self):
        self.tags += [{"name": "large:cloud", "size": 123},
                      {"name": "disguised:latest", "size": 123, "remote_host": "https://ollama.com"}]
        status = await (await self.client.get("/api/scripts/status")).json()
        self.assertTrue(status["ready"])
        self.assertEqual(status["excluded_cloud_models"], 2)
        self.assertEqual(len(status["models"]), 1)
        self.show["remote_model"] = "qwen-cloud"
        status = await (await self.client.get("/api/scripts/status")).json()
        self.assertFalse(status["ready"])
        job = await self.submit(await self.create())
        result = await self.complete(job)
        self.assertEqual(result["status"], "failed")
        self.assertIn("雲端", result["error"])
        self.assertEqual(self.chat_requests, [])

    async def test_redirect_missing_model_and_malformed_inventory_do_not_generate(self):
        project = await self.create()
        for mode in ("redirect", "bad_tags"):
            self.mode = mode
            status = await (await self.client.get("/api/scripts/status")).json()
            self.assertFalse(status["ready"])
            result = await self.complete(await self.submit(project))
            self.assertEqual(result["status"], "failed")
        self.mode = "normal"
        self.tags = []
        result = await self.complete(await self.submit(project))
        self.assertEqual(result["status"], "failed")
        self.assertIn("尚未安裝", result["error"])
        self.assertEqual(self.redirect_hits, 0)
        self.assertEqual(self.chat_requests, [])

    async def test_non_thinking_model_omits_think_option_and_output_limit_is_visible(self):
        self.show = {"capabilities": ["completion"]}
        self.mode = "length"
        result = await self.complete(await self.submit(await self.create()))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["content"], self.reply)
        self.assertTrue(result["error"])
        self.assertNotIn("think", self.chat_requests[0])

    async def test_overlong_repeated_tail_fails_without_overwriting_manuscript(self):
        self.reply = (
            "# 回家的路\n"
            "### 0.0s–5.0s｜抵達月台\n阿青提著行李走向候車線。\n"
            "### 5.0s–10.0s｜找到列車\n阿青抬頭確認列車方向，向車門移動。\n"
            "### 10.0s–15.0s｜出發\n阿青登車並向窗外揮手，15 秒切黑。\n"
            "### 15.0s–15.5s｜出發\n阿青向窗外揮手。\n"
            "### 15.5s–30.0s｜出發\n阿青向窗外揮手。"
        )
        project = await self.create(duration=15, content="原始稿：阿青等待列車。")
        result = await self.complete(await self.submit(project))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["content"], self.reply)
        self.assertIn("15", result["error"])
        self.assertEqual(self.manager.get_project(project["id"])["content"], project["content"])
        persisted = ScriptwritingManager(web.Application(), self.root)
        self.assertEqual(persisted.get_job(result["id"])["content"], self.reply)
        self.assertEqual(persisted.get_job(result["id"])["status"], "failed")

    async def test_legal_fifteen_second_screenplay_and_rewrite_complete(self):
        self.reply = (
            "# 回家的路\n"
            "1. **0.0–5.0s｜抵達月台**\n阿青走近候車線。\n"
            "2. **5.0–10.0s｜找到列車**\n阿青確認方向並提起行李。\n"
            "3. **10.0–15.0s｜出發**\n阿青登車，向窗外揮手後切黑。"
        )
        project = await self.create(duration=15, content="原始稿：阿青等待列車。")
        for action in ("screenplay", "rewrite"):
            with self.subTest(action=action):
                result = await self.complete(await self.submit(project, action=action))
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["content"], self.reply)

    async def test_known_python_dict_and_json_screenplays_are_readable_and_preserve_raw_output(self):
        original = "手寫原稿：阿青在月台等待。"
        project = await self.create(duration=15, content=original)
        for action, reply in (("screenplay", repr(dict_screenplay())),
                              ("rewrite", json.dumps(dict_screenplay(), ensure_ascii=False))):
            with self.subTest(action=action):
                self.reply = reply
                result = await self.complete(await self.submit(project, action=action))
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["raw_content"], reply)
                self.assertEqual(result["content_format"], "performance-script-v1")
                self.assertFalse(result["content"].lstrip().startswith("{"))
                self.assertIn("0.0–5.0s", result["content"])
                self.assertIn("5.0–10.0s", result["content"])
                self.assertIn("10.0–15.0s", result["content"])
                for value in ("阿青", "藍色外套", "角色參考圖1", "場景參考圖2", "我們回家吧。", "列車鳴笛", "向窗外揮手"):
                    self.assertIn(value, result["content"])
                self.assertEqual(self.manager.get_project(project["id"])["content"], original)
                persisted = ScriptwritingManager(web.Application(), self.root).get_job(result["id"])
                self.assertEqual(persisted["raw_content"], reply)
                self.assertEqual(persisted["content"], result["content"])

    async def test_generated_unknown_json_and_untimed_summary_are_retained_but_not_completed(self):
        project = await self.create(duration=15, content="保持這段手寫原稿。")
        replies = (json.dumps({"title": "回家的路", "summary": "阿青搭列車回家。"}, ensure_ascii=False),
                   "一句話梗概：阿青搭列車回家。",
                   '{"summary":"未知資料格式"}\n0.0–15.0s｜這行時間碼不能使結構資料變成完整劇本')
        for action in ("screenplay", "rewrite"):
            for reply in replies:
                with self.subTest(action=action, reply=reply):
                    self.reply = reply
                    result = await self.complete(await self.submit(project, action=action))
                    self.assertEqual(result["status"], "failed", result)
                    self.assertIn("時間碼", result["error"])
                    self.assertEqual(result["content"], reply)
                    self.assertFalse(result["truncated"])
                    self.assertNotIn("content_format", result)
                    self.assertEqual(self.manager.get_project(project["id"])["content"], project["content"])

    async def test_known_dict_with_overrun_still_fails_after_readable_conversion(self):
        draft = dict_screenplay()
        draft["scenes"][-1]["end"] = "16.0"
        self.reply = repr(draft)
        project = await self.create(duration=15, content="保持這段手寫原稿。")
        result = await self.complete(await self.submit(project))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["raw_content"], self.reply)
        self.assertIn("16.0s", result["content"])
        self.assertIn("15", result["error"])
        self.assertEqual(self.manager.get_project(project["id"])["content"], project["content"])

    async def test_output_limit_does_not_publish_even_parseable_storyboard_or_outline(self):
        self.mode = "length"
        project = await self.create(duration=5)
        for action, reply in (("storyboard", json.dumps(STORYBOARD, ensure_ascii=False)),
                              ("outline", "一句話梗概：阿青搭列車回家。")):
            with self.subTest(action=action):
                self.reply = reply
                result = await self.complete(await self.submit(project, action=action))
                self.assertEqual(result["status"], "failed", result)
                self.assertTrue(result["truncated"])
                self.assertEqual(result["content"], reply)
                if action == "storyboard":
                    response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
                    self.assertEqual(response.status, 409)
        self.assertEqual(self.app["shortfilms"].list(), [])

    async def test_outline_is_not_treated_as_a_complete_timed_screenplay(self):
        self.reply = "0.0–5.0s｜開端：抵達月台。\n10.0–15.0s｜結尾：搭車回家。"
        result = await self.complete(await self.submit(await self.create(duration=15), action="outline"))
        self.assertEqual(result["status"], "completed", result)

    async def test_storyboard_with_two_fifteen_second_clips_cannot_import_for_fifteen_second_target(self):
        storyboard = copy.deepcopy(STORYBOARD)
        storyboard["scenes"][0]["shots"][0]["duration"] = 15
        storyboard["scenes"][0]["shots"] *= 2
        self.reply = json.dumps(storyboard, ensure_ascii=False)
        result = await self.complete(await self.submit(await self.create(duration=15), action="storyboard"))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["content"], self.reply)
        self.assertNotIn("storyboard", result)
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 409)
        self.assertEqual(self.app["shortfilms"].list(), [])

    async def test_import_revalidates_duration_for_legacy_completed_job(self):
        self.reply = json.dumps(STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(await self.create(duration=5), action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        # A persisted job accepted by an older version must meet its original request.
        self.manager.jobs[result["id"]]["input"]["project"]["duration"] = 15
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app["shortfilms"].list(), [])
        self.assertIsNone(self.manager.jobs[result["id"]]["shortfilm_id"])

    async def test_legacy_completed_but_truncated_storyboard_cannot_be_imported(self):
        self.reply = json.dumps(STORYBOARD, ensure_ascii=False)
        result = await self.complete(await self.submit(await self.create(duration=5), action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        # Earlier versions marked a length stop complete, even if JSON happened to parse.
        self.manager.jobs[result["id"]]["truncated"] = True
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 409)
        self.assertEqual(self.app["shortfilms"].list(), [])
        self.assertIsNone(self.manager.jobs[result["id"]]["shortfilm_id"])

    async def test_storyboard_duration_uses_submission_snapshot_after_project_edit(self):
        self.reply = json.dumps(STORYBOARD, ensure_ascii=False)
        self.mode = "hold"
        project = await self.create(duration=5)
        job = await self.submit(project, action="storyboard")
        await asyncio.wait_for(self.started.wait(), 2)
        response = await self.client.put(f"/api/scripts/projects/{project['id']}",
                                         json={"revision": project["revision"], "duration": 15})
        self.assertEqual(response.status, 200)
        self.finish.set()
        result = await self.complete(job)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["input"]["project"]["duration"], 5)
        self.assertEqual(self.manager.get_project(project["id"])["duration"], 15)
        imported = self.manager.import_shortfilm(result["id"])["project"]
        self.assertEqual(imported["target_duration"], 5)

    async def test_storyboard_import_maps_characters_and_is_idempotent_without_jobs(self):
        self.reply = json.dumps(STORYBOARD, ensure_ascii=False)
        project = await self.create(duration=5)
        result = await self.complete(await self.submit(project, action="storyboard"))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(self.chat_requests[0]["format"], "json")
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 201)
        imported = await response.json()
        self.assertEqual(imported["project"]["synopsis"], STORYBOARD["synopsis"])
        asset = imported["project"]["assets"][0]
        shot = imported["project"]["scenes"][0]["shots"][0]
        self.assertEqual(shot["speaker_alias"], "阿青")
        self.assertEqual(shot["asset_ids"], [asset["id"]])
        self.assertEqual(shot["status"], "draft")
        self.assertIsNone(shot["job_id"])
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["shortfilm_id"], imported["shortfilm_id"])
        self.assertEqual(len(self.app["shortfilms"].list()), 1)
        loaded = ScriptwritingManager(self.app, self.root)
        self.assertEqual(loaded.import_shortfilm(result["id"])["shortfilm_id"], imported["shortfilm_id"])

    async def test_malformed_storyboard_cannot_be_imported(self):
        self.reply = "不是 JSON 的分鏡"
        result = await self.complete(await self.submit(await self.create(), action="storyboard"))
        self.assertEqual(result["status"], "failed")
        response = await self.client.post(f"/api/scripts/jobs/{result['id']}/shortfilm")
        self.assertEqual(response.status, 409)
        self.assertEqual(self.app["shortfilms"].list(), [])

    async def test_character_asset_aliases_match_whole_names(self):
        storyboard = copy.deepcopy(STORYBOARD)
        storyboard["characters"] = [{"name": "Ann", "description": "sister"}, {"name": "Hannah", "description": "traveller"}]
        storyboard["scenes"][0]["shots"][0].update(action="Hannah walks to the train.", speaker="Hannah")
        self.reply = json.dumps(storyboard)
        result = await self.complete(await self.submit(await self.create(duration=5), action="storyboard"))
        imported = self.manager.import_shortfilm(result["id"])["project"]
        shot = imported["scenes"][0]["shots"][0]
        self.assertEqual(shot["asset_ids"], [imported["assets"][1]["id"]])

    async def test_comfy_queue_wait_then_release_only_idle_local_weights(self):
        self.app["comfy"] = SimpleNamespace(
            mode="local", base_url=str(self.ollama.make_url("/")).rstrip("/"),
            auth_headers=lambda: {},
            queue_status=AsyncMock(side_effect=[
                {"available": True, "running_count": 1, "pending_count": 2},
                {"available": True, "running_count": 0, "pending_count": 0},
            ]),
        )
        result = await self.complete(await self.submit(await self.create()))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.free_calls, [{"unload_models": True, "free_memory": True}])
        self.assertEqual(self.app["comfy"].queue_status.await_count, 2)

    async def test_comfy_busy_wait_cancellation_never_unloads_video(self):
        self.app["comfy"] = SimpleNamespace(
            mode="local", base_url=str(self.ollama.make_url("/")).rstrip("/"), auth_headers=lambda: {},
            queue_status=AsyncMock(return_value={"available": True, "running_count": 1, "pending_count": 0}),
        )
        job = await self.submit(await self.create())
        for _ in range(100):
            if self.manager.jobs[job["id"]].get("waiting_reason"):
                break
            await asyncio.sleep(0.01)
        self.assertIn("waiting_reason", self.manager.jobs[job["id"]])
        result = await (await self.client.post(f"/api/scripts/jobs/{job['id']}/cancel")).json()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.free_calls, [])
        self.assertEqual(self.chat_requests, [])

    async def test_remote_and_unavailable_comfy_never_receive_free(self):
        for mode, snapshot in (("remote", {"available": True, "running_count": 0, "pending_count": 0}),
                               ("local", {"available": False})):
            self.app["comfy"] = SimpleNamespace(
                mode=mode, base_url=str(self.ollama.make_url("/")).rstrip("/"), auth_headers=lambda: {},
                queue_status=AsyncMock(return_value=snapshot),
            )
            result = await self.complete(await self.submit(await self.create()))
            self.assertEqual(result["status"], "completed")
        self.assertEqual(self.free_calls, [])

    async def test_shutdown_marks_queued_interrupted_and_restart_never_resubmits(self):
        await self.lock.acquire()
        job = await self.submit(await self.create())
        await self.manager.close()
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "interrupted")
        loaded = ScriptwritingManager(web.Application(), self.root)
        self.assertEqual(loaded.jobs[job["id"]]["status"], "interrupted")
        self.assertEqual(loaded.tasks, {})
        self.lock.release()

    async def test_shutdown_during_final_unload_preserves_completed_result(self):
        self.mode = "unload_hold"
        job = await self.submit(await self.create())
        await asyncio.wait_for(self.unload_started.wait(), 2)
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "completed")
        closing = asyncio.create_task(self.manager.close())
        await asyncio.sleep(0.01)
        self.finish.set()
        await asyncio.wait_for(closing, 2)
        self.assertEqual(self.manager.jobs[job["id"]]["status"], "completed")
        self.assertTrue(self.manager.jobs[job["id"]]["model_unloaded"])

    async def test_restart_marks_saved_unfinished_interrupted(self):
        path = self.root / "recovered"
        directory = path / "scriptwriting"
        directory.mkdir(parents=True)
        old = {"id": "a" * 32, "status": "running", "content": "保留初稿"}
        (directory / "jobs.json").write_text(json.dumps([old]), encoding="utf-8")
        loaded = ScriptwritingManager(web.Application(), path)
        self.assertEqual(loaded.jobs[old["id"]]["status"], "interrupted")
        self.assertEqual(loaded.jobs[old["id"]]["content"], "保留初稿")
        self.assertEqual(loaded.tasks, {})

    async def test_atomic_save_failure_preserves_previous_revision_and_corrupt_file(self):
        project = await self.create()
        original = self.manager.projects_path.read_bytes()
        with patch("scriptwriting._atomic_json", side_effect=OSError("disk full")):
            response = await self.client.put(f"/api/scripts/projects/{project['id']}", json={"revision": 1, "content": "new"})
            self.assertEqual(response.status, 500)
        self.assertEqual(self.manager.get_project(project["id"]), project)
        self.assertEqual(self.manager.projects_path.read_bytes(), original)
        self.manager.projects_path.write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(ScriptwritingError):
            ScriptwritingManager(web.Application(), self.root)
        self.assertEqual(self.manager.projects_path.read_text(encoding="utf-8"), "{corrupt")

    async def test_corrupt_manuscript_isolated_from_other_studio_routes(self):
        broken_root = self.root / "broken-feature"
        directory = broken_root / "scriptwriting"
        directory.mkdir(parents=True)
        corrupt_path = directory / "projects.json"
        original = b'{"unfinished manuscript'
        corrupt_path.write_bytes(original)
        app = web.Application()
        register_scriptwriting_routes(app, self.static, broken_root)

        async def unrelated_tool(request):
            return web.json_response({"video_editor": "available"})
        app.router.add_get("/api/editor-health", unrelated_tool)
        client = TestClient(TestServer(app))
        try:
            await client.start_server()
            response = await client.get("/api/editor-health")
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["video_editor"], "available")
            response = await client.get("/scripts")
            self.assertEqual(response.status, 200)
            response = await client.get("/api/scripts/status")
            self.assertEqual(response.status, 200)
            status = await response.json()
            self.assertFalse(status["ready"])
            self.assertTrue(status["initialization_error"])
            self.assertIn("projects.json", status["error"])
            self.assertIn("其他工具仍可使用", status["error"])
            response = await client.get("/api/scripts/settings")
            self.assertEqual(response.status, 200)
            settings = await response.json()
            self.assertFalse(settings["available"])
            self.assertTrue(settings["error"])
            for path, payload in (("/api/scripts/projects", {"idea": "new"}),
                                  ("/api/scripts/jobs", {"project_id": "a" * 32, "action": "screenplay"}),
                                  ("/api/scripts/settings", {"temperature": 0.5})):
                response = await client.post(path, json=payload)
                self.assertEqual(response.status, 503)
                self.assertTrue((await response.json())["error"])
            self.assertEqual(corrupt_path.read_bytes(), original)
        finally:
            await client.close()
        self.assertEqual(corrupt_path.read_bytes(), original)
        self.assertFalse((directory / "settings.json").exists())
        self.assertFalse((directory / "jobs.json").exists())


if __name__ == "__main__":
    unittest.main()
