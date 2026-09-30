import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from domain import RequestError
import video_editor as editor


class VideoEditorAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise the editor HTTP contract without an engine or video encoding."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.static = self.root / "static"
        self.static.mkdir()
        (self.static / "editor.html").write_text("<!doctype html><title>Editor</title>", encoding="utf-8")
        self.source = self.root / "original.mp4"
        self.content = bytes(range(256)) * 8
        self.source.write_bytes(self.content)
        self.asset_id = "a" * 32
        self.job_id = "b" * 32
        self.assets = SimpleNamespace(path_for=self.asset_path, metadata=self.asset_metadata)
        self.jobs = SimpleNamespace(
            jobs={self.job_id: {"id": self.job_id, "status": "completed", "name": "生成片段",
                               "output": {"filename": "original.mp4"}}},
            local_output_path=Mock(return_value=self.source),
        )
        # Decoding/encoding behavior belongs to test_video_editor_render.py.
        self.probe_patch = patch.object(editor, "probe_media", return_value={
            "duration": 4.0, "width": 640, "height": 360, "fps": 24.0, "has_audio": True,
            "mime": "video/mp4",
        })
        self.probe_patch.start()
        self.gates = []
        await self.open_client()

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        await self.client.close()
        self.probe_patch.stop()
        self.temp.cleanup()

    def asset_path(self, asset_id):
        if asset_id != self.asset_id:
            raise RequestError("找不到素材。")
        return self.source

    def asset_metadata(self, asset_id):
        self.asset_path(asset_id)
        return {"id": asset_id, "name": "上傳影片.mp4", "extension": ".mp4"}

    async def open_client(self):
        self.app = web.Application()
        self.app["assets"] = self.assets
        self.app["jobs"] = self.jobs
        self.store = editor.register_editor_routes(self.app, self.static, self.data)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def import_media(self, source="asset"):
        payload = {"asset_id": self.asset_id} if source == "asset" else {"job_id": self.job_id}
        response = await self.client.post("/api/editor/media", json=payload)
        body = await response.json()
        self.assertIn(response.status, (200, 201), body)
        return body

    async def create_project(self, media=None):
        response = await self.client.post("/api/editor/projects", json={"name": "測試剪輯"})
        project = await response.json()
        self.assertEqual(response.status, 201, project)
        if media is not None:
            project["clips"] = [{"id": "c" * 32, "media_id": media["id"], "in": 0.25,
                                 "out": 2.75, "volume": 0.8}]
            response = await self.client.put(f"/api/editor/projects/{project['id']}", json=project)
            project = await response.json()
            self.assertEqual(response.status, 200, project)
        return project

    async def wait_export(self, export_id, expected):
        async with asyncio.timeout(4):
            while True:
                response = await self.client.get(f"/api/editor/exports/{export_id}")
                self.assertEqual(response.status, 200)
                result = await response.json()
                if result["status"] in expected:
                    return result
                await asyncio.sleep(0.01)

    async def wait_started(self, event):
        async with asyncio.timeout(4):
            while not event.is_set():
                await asyncio.sleep(0.01)

    async def assert_json_error(self, response, statuses=(400, 404)):
        self.assertIn(response.status, statuses, await response.text())
        self.assertIn("error", await response.json())

    async def test_import_copies_assets_and_completed_jobs_independently_of_the_source(self):
        asset = await self.import_media("asset")
        job = await self.import_media("job")
        self.assertNotEqual(asset["id"], job["id"])
        self.jobs.local_output_path.assert_called_once_with(self.jobs.jobs[self.job_id])
        self.source.unlink()
        self.jobs.jobs.clear()
        for media in (asset, job):
            with self.subTest(media_id=media["id"]):
                self.assertEqual(media["duration"], 4.0)
                self.assertEqual(media["width"], 640)
                response = await self.client.get(media["url"])
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), self.content)
        response = await self.client.get("/api/editor/media")
        self.assertEqual({item["id"] for item in await response.json()}, {asset["id"], job["id"]})

    async def test_import_rejects_arbitrary_paths_unknown_ids_and_unfinished_jobs(self):
        cases = [
            {"path": str(self.source)}, {"url": "file:///C:/Windows/win.ini"},
            {"asset_id": "../original.mp4"}, {"job_id": "../original.mp4"},
            {"asset_id": "f" * 32}, {"job_id": "f" * 32}, {},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                await self.assert_json_error(await self.client.post("/api/editor/media", json=payload))
        self.jobs.jobs[self.job_id]["status"] = "running"
        await self.assert_json_error(
            await self.client.post("/api/editor/media", json={"job_id": self.job_id}),
            statuses=(400, 404, 409),
        )
        response = await self.client.get("/api/editor/media")
        self.assertEqual(await response.json(), [])
        self.jobs.local_output_path.assert_not_called()

    async def test_invalid_media_project_and_export_ids_never_serve_files(self):
        for value in ("not-an-id", "a" * 31, "a" * 33, "../original.mp4", "f" * 32):
            encoded = quote(value, safe="")
            for endpoint in (f"/api/editor/media/{encoded}/file",
                             f"/api/editor/projects/{encoded}",
                             f"/api/editor/exports/{encoded}",
                             f"/api/editor/exports/{encoded}/file"):
                with self.subTest(endpoint=endpoint):
                    response = await self.client.get(endpoint)
                    self.assertIn(response.status, (400, 404))
                    self.assertNotEqual(await response.read(), self.content)

    async def test_mutation_endpoints_reject_non_object_and_malformed_json(self):
        project = await self.create_project()
        endpoints = (("post", "/api/editor/media"), ("post", "/api/editor/projects"),
                     ("put", f"/api/editor/projects/{project['id']}"))
        for method, url in endpoints:
            for body in ("[]", "null", '"text"', "{"):
                with self.subTest(method=method, url=url, body=body):
                    response = await self.client.request(
                        method, url, data=body, headers={"Content-Type": "application/json"},
                    )
                    await self.assert_json_error(response, (400,))

    async def test_saved_project_and_media_survive_reopening_the_store(self):
        media = await self.import_media()
        project = await self.create_project(media)
        await self.client.close()
        self.source.unlink()
        await self.open_client()
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        restored = await response.json()
        self.assertEqual(response.status, 200)
        for key in ("id", "name", "clips", "width", "height", "fps", "updated_at"):
            self.assertEqual(restored[key], project[key])

    async def test_shared_overlay_track_save_read_reload_and_collision_rejection(self):
        from test_editor_video_layers import video_layer
        media = await self.import_media()
        project = await self.create_project()
        project["overlays"] = [video_layer(id="first", track_id="overlay-one", media_id=media["id"]),
            video_layer(id="second", track_id="overlay-one", media_id=media["id"], start=1, end=2)]
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=project)
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        saved = await response.json()
        self.assertEqual([item["track_id"] for item in saved["overlays"]], ["overlay-one", "overlay-one"])
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual((await response.json())["overlays"], saved["overlays"])
        invalid = copy.deepcopy(saved)
        invalid["overlays"][1].update(start=.5, end=1.5)
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=invalid)
        self.assertEqual(response.status, 400)
        self.assertIn("不能重疊", (await response.json())["error"])
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual((await response.json())["overlays"], saved["overlays"])
        await self.client.close()
        await self.open_client()
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual((await response.json())["overlays"], saved["overlays"])
        response = await self.client.get(media["url"])
        self.assertEqual(await response.read(), self.content)
        response = await self.client.get("/api/editor/projects")
        self.assertIn(project["id"], [item["id"] for item in await response.json()])

    async def test_invalid_trims_and_volume_are_atomic_and_do_not_replace_saved_project(self):
        media = await self.import_media()
        original = await self.create_project(media)
        url = f"/api/editor/projects/{original['id']}"
        for edits in ({"in": -0.1}, {"in": 3, "out": 2}, {"in": 1, "out": 1},
                      {"in": 0, "out": 0.01},
                      {"out": 4.1}, {"in": float("nan")}, {"out": float("inf")},
                      {"volume": -0.1}, {"volume": 2.1}, {"volume": float("nan")},
                      {"speed": 0.2}, {"speed": 4.1}, {"speed": True}, {"speed": float("inf")},
                      {"in": 0, "out": 0.05, "speed": 4},
                      {"media_id": "f" * 32}):
            candidate = copy.deepcopy(original)
            candidate["name"] = "不可寫入"
            candidate["clips"][0].update(edits)
            with self.subTest(edits=edits):
                statuses = (404,) if "media_id" in edits else (400,)
                await self.assert_json_error(await self.client.put(url, json=candidate), statuses)
                response = await self.client.get(url)
                self.assertEqual(await response.json(), original)

    async def test_stale_project_update_returns_conflict_without_losing_new_changes(self):
        original = await self.create_project(await self.import_media())
        url = f"/api/editor/projects/{original['id']}"
        updated = {**original, "name": "最新名稱"}
        response = await self.client.put(url, json=updated)
        self.assertEqual(response.status, 200)
        latest = await response.json()
        response = await self.client.put(url, json={**original, "name": "舊頁覆寫"})
        self.assertEqual(response.status, 409)
        conflict = await response.json()
        self.assertEqual(conflict["project"], latest)
        response = await self.client.get(url)
        self.assertEqual(await response.json(), latest)

    async def test_clip_count_and_total_duration_limits_reject_oversized_projects(self):
        media = await self.import_media()
        project = await self.create_project(media)
        url = f"/api/editor/projects/{project['id']}"
        candidate = copy.deepcopy(project)
        candidate["clips"] = [
            {**project["clips"][0], "id": f"{index:032x}"} for index in range(51)
        ]
        await self.assert_json_error(await self.client.put(url, json=candidate), (400,))
        with patch.object(editor, "probe_media", return_value={
            "duration": 1000.0, "width": 640, "height": 360, "fps": 24.0, "has_audio": True,
            "mime": "video/mp4",
        }):
            long_media = await self.import_media("job")
        candidate = copy.deepcopy(project)
        candidate["clips"][0].update(media_id=long_media["id"], **{"in": 0, "out": 601})
        await self.assert_json_error(await self.client.put(url, json=candidate), (400,))
        response = await self.client.get(url)
        self.assertEqual(await response.json(), project)

    async def test_media_serves_byte_ranges_head_and_unsatisfiable_ranges(self):
        media = await self.import_media()
        response = await self.client.get(media["url"], headers={"Range": "bytes=17-48"})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes 17-48/{len(self.content)}")
        self.assertEqual(await response.read(), self.content[17:49])
        response = await self.client.get(media["url"], headers={"Range": "bytes=-19"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), self.content[-19:])
        response = await self.client.head(media["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, max-age=31536000, immutable")
        self.assertEqual(int(response.headers["Content-Length"]), len(self.content))
        self.assertEqual(await response.read(), b"")
        response = await self.client.get(media["url"], headers={"Range": "bytes=99999-"})
        self.assertEqual(response.status, 416)

    async def test_single_active_export_can_cancel_and_release_the_slot(self):
        project = await self.create_project(await self.import_media())
        started, release = threading.Event(), threading.Event()
        self.gates.append(release)

        def blocked_render(_project, _media, output, cancel, progress):
            output.write_bytes(b"partial")
            started.set()
            while not release.wait(0.01):
                if cancel.is_set():
                    raise editor.ExportCancelled()
            if cancel.is_set():
                raise editor.ExportCancelled()

        url = f"/api/editor/projects/{project['id']}/exports"
        with patch.object(editor, "render_project", side_effect=blocked_render):
            responses = await asyncio.gather(self.client.post(url), self.client.post(url))
            self.assertEqual(sorted(response.status for response in responses), [202, 409])
            accepted = next(response for response in responses if response.status == 202)
            export = await accepted.json()
            await self.wait_started(started)
            response = await self.client.get(f"/api/editor/exports/{export['id']}/file")
            self.assertIn(response.status, (404, 409))
            response = await self.client.post(f"/api/editor/exports/{export['id']}/cancel")
            self.assertIn(response.status, (200, 202))
            cancelled = await self.wait_export(export["id"], {"cancelled"})
            self.assertEqual(cancelled["status"], "cancelled")
        export_root = self.data / "editor" / "exports"
        self.assertFalse((export_root / f"{export['id']}.partial.mp4").exists())
        self.assertFalse((export_root / f"{export['id']}.mp4").exists())

        def successful_render(_project, _media, output, _cancel, progress):
            output.write_bytes(b"finished")
            progress(1.0)
            return {"duration": 2.5, "width": 1280, "height": 720, "fps": 24}

        with patch.object(editor, "render_project", side_effect=successful_render):
            response = await self.client.post(url)
            self.assertEqual(response.status, 202)
            retry = await response.json()
            result = await self.wait_export(retry["id"], {"completed", "failed"})
        self.assertEqual(result["status"], "completed", result)
        response = await self.client.get(f"/api/editor/exports/{retry['id']}/file?download=1")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"finished")
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))

    async def test_export_failure_cleans_partial_and_does_not_block_retry(self):
        project = await self.create_project(await self.import_media())
        url = f"/api/editor/projects/{project['id']}/exports"

        def failed_render(_project, _media, output, _cancel, _progress):
            output.write_bytes(b"incomplete")
            raise RuntimeError("模擬編碼失敗")

        with self.assertLogs(editor.LOGGER, level="ERROR"), patch.object(editor, "render_project", side_effect=failed_render):
            response = await self.client.post(url)
            self.assertEqual(response.status, 202)
            first = await response.json()
            failure = await self.wait_export(first["id"], {"failed"})
        self.assertIn("模擬編碼失敗", failure["error"])
        self.assertFalse((self.data / "editor/exports" / f"{first['id']}.partial.mp4").exists())
        response = await self.client.get(f"/api/editor/exports/{first['id']}/file")
        self.assertIn(response.status, (404, 409))
        with self.assertLogs(editor.LOGGER, level="ERROR"), patch.object(editor, "render_project", side_effect=failed_render):
            response = await self.client.post(url)
            self.assertEqual(response.status, 202)
            second = await response.json()
            self.assertNotEqual(first["id"], second["id"])
            await self.wait_export(second["id"], {"failed"})

    async def test_restart_marks_interrupted_export_failed_and_removes_partial(self):
        project = await self.create_project(await self.import_media())
        with self.assertLogs(editor.LOGGER, level="ERROR"), patch.object(editor, "render_project", side_effect=RuntimeError("stopped")):
            response = await self.client.post(f"/api/editor/projects/{project['id']}/exports")
            exported = await response.json()
            await self.wait_export(exported["id"], {"failed"})
        await self.client.close()
        export_root = self.data / "editor" / "exports"
        record = export_root / f"{exported['id']}.json"
        state = json.loads(record.read_text(encoding="utf-8"))
        state.update(status="running", error=None)
        record.write_text(json.dumps(state), encoding="utf-8")
        partial = export_root / f"{exported['id']}.partial.mp4"
        partial.write_bytes(b"interrupted")
        await self.open_client()
        response = await self.client.get(f"/api/editor/exports/{exported['id']}")
        recovered = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(recovered["status"], "failed")
        self.assertTrue(recovered["error"])
        self.assertFalse(partial.exists())

    async def test_reimport_reuses_independent_copy_without_probe_even_after_source_removal(self):
        media = await self.import_media()
        self.source.unlink()
        with patch.object(editor, "probe_media", side_effect=AssertionError("must not decode again")):
            repeated = await self.import_media()
        self.assertEqual(repeated, media)
        response = await self.client.get("/api/editor/media")
        self.assertEqual(len(await response.json()), 1)

    async def test_speed_audio_tracks_save_restore_and_export_snapshot(self):
        video = await self.import_media()
        with patch.object(editor, "probe_media", return_value={
            "kind": "audio", "duration": 4, "width": 0, "height": 0, "fps": 0,
            "has_audio": True, "mime": "audio/mpeg",
        }):
            music = await self.import_media("job")
        project = await self.create_project(video)
        self.assertEqual(project["clips"][0]["speed"], 1)
        self.assertEqual(project["audio_clips"], [])
        project["clips"][0]["speed"] = 2
        project["audio_clips"] = [{"id": "music", "media_id": music["id"], "in": 0.5, "out": 3.5,
                                  "start": 0.25, "volume": 0.7, "speed": 0.5, "fade_in": 0.3,
                                  "fade_out": 0.4, "track": 3}]
        url = f"/api/editor/projects/{project['id']}"
        response = await self.client.put(url, json=project)
        self.assertEqual(response.status, 200, await response.text())
        updated = await response.json()
        self.assertEqual(updated["clips"][0]["speed"], 2)
        self.assertEqual(updated["audio_clips"], project["audio_clips"])
        captured = {}
        def render(snapshot, paths, output, _cancel, _progress):
            captured.update(project=snapshot, media=set(paths))
            output.write_bytes(b"encoded")
        with patch.object(editor, "render_project", side_effect=render):
            response = await self.client.post(url + "/exports")
            export = await response.json()
            result = await self.wait_export(export["id"], {"completed", "failed"})
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(captured["project"], updated)
        self.assertEqual(captured["media"], {video["id"], music["id"]})
        await self.client.close()
        await self.open_client()
        response = await self.client.get(url)
        self.assertEqual(await response.json(), updated)

    async def test_invalid_audio_tracks_are_rejected_without_altering_saved_project(self):
        media = await self.import_media()
        project = await self.create_project(media)
        url = f"/api/editor/projects/{project['id']}"
        original = {"id": "music", "media_id": media["id"], "in": 0, "out": 2, "start": 0,
                    "volume": 1, "speed": 1, "fade_in": 0.5, "fade_out": 0.5, "track": 0}
        edits = ({"in": -1}, {"out": 4.1}, {"start": -1}, {"start": 601}, {"volume": 3},
                 {"speed": 0}, {"speed": float("nan")}, {"track": 4}, {"track": True},
                 {"fade_in": -1}, {"fade_out": 2}, {"fade_in": float("inf")},
                 {"out": 0.05, "speed": 4}, {"id": project["clips"][0]["id"]})
        for edit in edits:
            with self.subTest(edit=edit):
                candidate = {**project, "audio_clips": [{**original, **edit}]}
                await self.assert_json_error(await self.client.put(url, json=candidate), (400,))
        candidate = {**project, "audio_clips": [{**original, "id": f"sound{i}"} for i in range(51)]}
        await self.assert_json_error(await self.client.put(url, json=candidate), (400,))
        self.store.media[media["id"]]["has_audio"] = False
        await self.assert_json_error(await self.client.put(url, json={**project, "audio_clips": [original]}), (400,))
        self.store.media[media["id"]]["has_audio"] = True
        self.store.media[media["id"]]["kind"] = "audio"
        await self.assert_json_error(await self.client.put(url, json=project), (400,))
        response = await self.client.get(url)
        self.assertEqual(await response.json(), project)

    async def test_legacy_projects_and_media_get_compatible_defaults_after_restart(self):
        media = await self.import_media()
        project = await self.create_project(media)
        await self.client.close()
        project.pop("audio_clips")
        project["clips"][0].pop("speed")
        media.pop("kind")
        (self.data / "editor/projects" / f"{project['id']}.json").write_text(json.dumps(project), encoding="utf-8")
        (self.data / "editor/media" / f"{media['id']}.json").write_text(json.dumps(media), encoding="utf-8")
        await self.open_client()
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        restored = await response.json()
        self.assertEqual(restored["clips"][0]["speed"], 1)
        self.assertEqual(restored["audio_clips"], [])
        response = await self.client.get("/api/editor/media")
        self.assertEqual((await response.json())[0]["kind"], "video")

    async def test_music_tracks_reject_same_track_overlap_but_allow_four_tracks_and_adjacent_clips(self):
        media = await self.import_media()
        project = await self.create_project(media)
        url = f"/api/editor/projects/{project['id']}"
        clip = {"id": "a0", "media_id": media["id"], "in": 0, "out": 2, "speed": 2,
                "start": 0, "volume": 1, "track": 0}
        candidate = {**project, "audio_clips": [clip, {**clip, "id": "overlap", "start": 0.5}]}
        response = await self.client.put(url, json=candidate)
        self.assertEqual(response.status, 400)
        self.assertIn("不能重疊", (await response.json())["error"])
        candidate["audio_clips"] = [{**clip, "id": f"a{i}", "track": i} for i in range(4)]
        candidate["audio_clips"].append({**clip, "id": "adjacent", "start": 1})
        response = await self.client.put(url, json=candidate)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(len((await response.json())["audio_clips"]), 5)


if __name__ == "__main__":
    unittest.main()
