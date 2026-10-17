import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import editor_media_library as library
from video_editor import EditorStore, atomic_json, now, register_editor_routes


class EditorMediaLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = web.Application()
        self.app["assets"] = SimpleNamespace()
        self.app["jobs"] = SimpleNamespace()
        self.store = register_editor_routes(self.app, self.root, self.root)
        self.app["editor_captions"] = SimpleNamespace(jobs={})
        library.register_editor_media_library(self.app)
        self.video = self.add_media("a" * 32, "video")
        self.audio = self.add_media("b" * 32, "audio")
        self.image = self.add_media("c" * 32, "image")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def add_media(self, identifier, kind):
        item = {"id": identifier, "name": f"素材 {kind}", "kind": kind,
                "created_at": now(), "width": 640, "height": 360,
                "duration": 4, "fps": 24, "has_audio": kind != "image",
                "mime": {"video": "video/mp4", "audio": "audio/wav", "image": "image/png"}[kind],
                "source": {"asset_id": "d" * 32},
                "url": f"/api/editor/media/{identifier}/file"}
        self.store.media[identifier] = item
        (self.store.media_dir / f"{identifier}.source").write_bytes(bytes(range(256)) * 2)
        atomic_json(self.metadata_path(identifier), item)
        return item

    def metadata_path(self, identifier):
        return self.store.media_dir / f"{identifier}.json"

    async def mutate(self, identifier, operation="remove", payload=None):
        response = await self.client.post(f"/api/editor/media/{identifier}/{operation}",
                                          json={} if payload is None else payload)
        return response, await response.json()

    def reload(self):
        return EditorStore(self.root, self.app["assets"], self.app["jobs"])

    async def test_status_and_remove_restore_survive_reload_without_changing_sources(self):
        response = await self.client.get("/api/editor/media-library/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"available": True, "reversible": True})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        before = copy.deepcopy(self.video)
        original = self.store.media_path(self.video["id"]).read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        response, value = await self.mutate(self.video["id"])
        self.assertEqual(response.status, 200, value)
        removed = value["media"]
        self.assertIsInstance(removed[library.REMOVED_AT], str)
        self.assertTrue(removed[library.REMOVED_AT])
        self.assertEqual({key: value for key, value in removed.items() if key != library.REMOVED_AT}, before)
        self.assertEqual(self.reload().media[self.video["id"]], removed)
        # Hidden records remain discoverable for timelines and draft validation.
        response = await self.client.get("/api/editor/media")
        records = {item["id"]: item for item in await response.json()}
        self.assertEqual(records[self.video["id"]], removed)
        response = await self.client.get(removed["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), original)
        response, value = await self.mutate(self.video["id"], "restore")
        self.assertEqual(response.status, 200, value)
        self.assertEqual(value["media"], before)
        self.assertEqual(self.reload().media[self.video["id"]], before)
        self.assertEqual(hashlib.sha256(self.store.media_path(self.video["id"]).read_bytes()).hexdigest(), digest)
        self.assertEqual(len(list(self.store.media_dir.glob("*.source"))), 3)

    async def test_idempotence_keeps_timestamp_and_does_not_rewrite_metadata(self):
        _, first = await self.mutate(self.video["id"])
        content = self.metadata_path(self.video["id"]).read_bytes()
        with patch.object(library, "atomic_json", side_effect=AssertionError("no repeated write")):
            response, second = await self.mutate(self.video["id"])
        self.assertEqual(response.status, 200, second)
        self.assertEqual(second, first)
        self.assertEqual(self.metadata_path(self.video["id"]).read_bytes(), content)
        _, restored = await self.mutate(self.video["id"], "restore")
        with patch.object(library, "atomic_json", side_effect=AssertionError("already restored")):
            response, repeated = await self.mutate(self.video["id"], "restore")
        self.assertEqual(response.status, 200, repeated)
        self.assertEqual(repeated, restored)

    async def test_saved_projects_export_and_caption_snapshots_keep_all_references(self):
        project = self.store.create_project({"name": "保護剪輯", "fps": 24,
            "clips": [{"id": "main", "media_id": self.video["id"], "in": 0, "out": 3}],
            "audio_clips": [{"id": "audio", "media_id": self.audio["id"], "in": 0, "out": 3, "start": 0, "track": 0}],
            "overlays": [{"id": "image", "kind": "image", "media_id": self.image["id"], "start": 0, "end": 3},
                         {"id": "layer", "kind": "video", "media_id": self.video["id"], "in": 0, "out": 3, "start": 0, "end": 3}]})
        persisted = (self.store.project_dir / f"{project['id']}.json").read_bytes()
        export = {"id": "e" * 32, "status": "running", "snapshot": copy.deepcopy(project)}
        caption = {"id": "f" * 32, "status": "queued", "snapshot": copy.deepcopy(project)}
        self.store.exports[export["id"]] = export
        self.app["editor_captions"].jobs[caption["id"]] = caption
        snapshots = copy.deepcopy([export, caption])
        sources = {item["id"]: self.store.media_path(item["id"]).read_bytes() for item in (self.video, self.audio, self.image)}
        for item in (self.video, self.audio, self.image):
            response, value = await self.mutate(item["id"])
            self.assertEqual(response.status, 200, value)
        self.assertEqual([export, caption], snapshots)
        self.assertEqual(self.store.projects[project["id"]], project)
        self.assertEqual((self.store.project_dir / f"{project['id']}.json").read_bytes(), persisted)
        # Both the original timeline and earlier worker snapshots remain valid.
        for snapshot in (project, export["snapshot"], caption["snapshot"]):
            validated = self.store._project(snapshot, snapshot)
            for field in ("clips", "audio_clips", "overlays"):
                self.assertEqual(validated[field], project[field])
        updated = self.store.update_project(project["id"], project)
        self.assertEqual(updated["clips"], project["clips"])
        for identifier, content in sources.items():
            self.assertEqual(self.store.media_path(identifier).read_bytes(), content)

    async def test_existing_preview_and_thumbnail_files_are_preserved(self):
        related = {}
        for directory, suffix in (("previews", ".mp4"), ("previews", ".json"), ("image_thumbnails", ".png")):
            folder = self.store.root / directory
            folder.mkdir(exist_ok=True)
            path = folder / f"{self.video['id']}{suffix}"
            path.write_bytes(b"existing reusable preview")
            related[path] = path.read_bytes()
        for operation in ("remove", "restore"):
            response, value = await self.mutate(self.video["id"], operation)
            self.assertEqual(response.status, 200, value)
            self.assertEqual({path: path.read_bytes() for path in related}, related)

    async def test_rejects_unknown_payloads_non_json_and_invalid_or_missing_ids(self):
        before = copy.deepcopy(self.store.media)
        for payload in ({"path": "C:/secret.mp4"}, {"media_id": self.video["id"]}, {"force": True}, [], "", 2, True):
            response, value = await self.mutate(self.video["id"], payload=payload)
            self.assertEqual(response.status, 400, value)
        for operation in ("remove", "restore"):
            for identifier in ("not-a-media-id", "0" * 32, "A" * 32):
                response, value = await self.mutate(identifier, operation)
                self.assertEqual(response.status, 404, value)
            response = await self.client.post(f"/api/editor/media/{self.video['id']}/{operation}", data="{}")
            self.assertEqual(response.status, 400)
            response = await self.client.post(f"/api/editor/media/{self.video['id']}/{operation}",
                                              data="{", headers={"Content-Type": "application/json"})
            self.assertEqual(response.status, 400)
            response = await self.client.post(f"/api/editor/media/{self.video['id']}/{operation}",
                                              data="null", headers={"Content-Type": "application/json"})
            self.assertEqual(response.status, 400)
        self.assertEqual(self.store.media, before)

    async def test_closing_rejects_mutation_without_changes(self):
        before = self.metadata_path(self.video["id"]).read_bytes()
        self.store.closing = True
        for operation in ("remove", "restore"):
            response, value = await self.mutate(self.video["id"], operation)
            self.assertEqual(response.status, 503, value)
        self.assertEqual(self.metadata_path(self.video["id"]).read_bytes(), before)

    async def test_failed_write_preserves_registry_identity_and_existing_metadata(self):
        original = self.store.media[self.video["id"]]
        content = self.metadata_path(self.video["id"]).read_bytes()
        with patch.object(library, "atomic_json", side_effect=OSError("disk unavailable")):
            response, value = await self.mutate(self.video["id"])
        self.assertEqual(response.status, 500, value)
        self.assertIs(self.store.media[self.video["id"]], original)
        self.assertEqual(self.metadata_path(self.video["id"]).read_bytes(), content)
        _, removed = await self.mutate(self.video["id"])
        original = self.store.media[self.video["id"]]
        content = self.metadata_path(self.video["id"]).read_bytes()
        with patch.object(library, "atomic_json", side_effect=OSError("disk unavailable")):
            response, value = await self.mutate(self.video["id"], "restore")
        self.assertEqual(response.status, 500, value)
        self.assertIs(self.store.media[self.video["id"]], original)
        self.assertEqual(self.store.media[self.video["id"]], removed["media"])
        self.assertEqual(self.metadata_path(self.video["id"]).read_bytes(), content)

    async def test_failed_atomic_replacement_keeps_old_metadata_and_cleans_temp(self):
        original = self.store.media[self.video["id"]]
        content = self.metadata_path(self.video["id"]).read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("replacement denied")):
            response, value = await self.mutate(self.video["id"])
        self.assertEqual(response.status, 500, value)
        self.assertIs(self.store.media[self.video["id"]], original)
        self.assertEqual(self.metadata_path(self.video["id"]).read_bytes(), content)
        self.assertEqual(list(self.store.media_dir.glob("*.tmp")), [])

    async def test_mutation_waits_for_import_lock(self):
        original = copy.deepcopy(self.video)
        await self.store.import_lock.acquire()
        task = asyncio.create_task(self.mutate(self.video["id"]))
        try:
            await asyncio.sleep(.03)
            self.assertFalse(task.done())
            self.assertEqual(self.store.media[self.video["id"]], original)
        finally:
            self.store.import_lock.release()
        response, value = await task
        self.assertEqual(response.status, 200, value)

    async def test_restore_helper_uses_current_record_and_changes_only_library_flag(self):
        _, removed = await self.mutate(self.video["id"])
        current = self.store.media[self.video["id"]]
        current["name"] = "最新素材名稱"
        atomic_json(self.metadata_path(current["id"]), current)
        async with self.store.import_lock:
            restored = library.restore_library_media(self.store, removed["media"])
        self.assertEqual(restored["name"], "最新素材名稱")
        self.assertNotIn(library.REMOVED_AT, restored)
        self.assertEqual(json.loads(self.metadata_path(current["id"]).read_text(encoding="utf-8")), restored)

    async def test_restore_cleans_malformed_internal_flag_and_remove_replaces_it(self):
        for bad_flag in (None, False, "", 123):
            current = self.store.media[self.video["id"]]
            current[library.REMOVED_AT] = bad_flag
            atomic_json(self.metadata_path(current["id"]), current)
            response, value = await self.mutate(current["id"])
            self.assertEqual(response.status, 200, value)
            self.assertIsInstance(value["media"][library.REMOVED_AT], str)
            self.assertTrue(value["media"][library.REMOVED_AT])
            response, value = await self.mutate(current["id"], "restore")
            self.assertEqual(response.status, 200, value)
            self.assertNotIn(library.REMOVED_AT, value["media"])
            current = self.store.media[self.video["id"]]
            current[library.REMOVED_AT] = bad_flag
            atomic_json(self.metadata_path(current["id"]), current)
            response, value = await self.mutate(current["id"], "restore")
            self.assertEqual(response.status, 200, value)
            self.assertNotIn(library.REMOVED_AT, value["media"])

    async def test_reimport_cached_asset_and_job_restores_same_id_without_copy_or_probe(self):
        identifier = self.video["id"]
        original = self.store.media_path(identifier).read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        source_mtime = self.store.media_path(identifier).stat().st_mtime_ns
        for key in ("asset_id", "job_id"):
            with self.subTest(source=key):
                item = self.store.media[identifier]
                item["source"] = {key: "d" * 32}
                atomic_json(self.metadata_path(identifier), item)
                _, removed = await self.mutate(identifier)
                old_timestamp = removed["media"][library.REMOVED_AT]
                with (patch.object(self.store, "_import_source", new=AsyncMock(side_effect=AssertionError("cached source must be reused"))) as source_import,
                      patch("video_editor.probe_media", side_effect=AssertionError("no decode")) as probe,
                      patch("video_editor.shutil.copyfile", side_effect=AssertionError("no copy")) as copying):
                    restored = await self.store.import_media({key: "d" * 32})
                source_import.assert_not_awaited()
                probe.assert_not_called()
                copying.assert_not_called()
                self.assertEqual(restored["id"], identifier)
                self.assertNotIn(library.REMOVED_AT, restored)
                self.assertTrue(old_timestamp)
                self.assertEqual(self.reload().media[identifier], restored)
                self.assertEqual(self.store.media_path(identifier).stat().st_mtime_ns, source_mtime)
                self.assertEqual(hashlib.sha256(self.store.media_path(identifier).read_bytes()).hexdigest(), digest)
                self.assertEqual(len(self.store.media), 3)
                self.assertEqual(len(list(self.store.media_dir.glob("*.source"))), 3)

    async def test_reimport_restore_write_failure_keeps_removed_record_and_source(self):
        identifier = self.video["id"]
        for key in ("asset_id", "job_id"):
            with self.subTest(source=key):
                item = self.store.media[identifier]
                item["source"] = {key: "d" * 32}
                atomic_json(self.metadata_path(identifier), item)
                await self.mutate(identifier)
                removed = self.store.media[identifier]
                metadata = self.metadata_path(identifier).read_bytes()
                source = self.store.media_path(identifier).read_bytes()
                with (patch.object(library, "atomic_json", side_effect=OSError("restore denied")),
                      patch.object(self.store, "_import_source", new=AsyncMock(side_effect=AssertionError("must not create duplicate"))) as source_import):
                    with self.assertRaises(OSError):
                        await self.store.import_media({key: "d" * 32})
                source_import.assert_not_awaited()
                self.assertIs(self.store.media[identifier], removed)
                self.assertTrue(removed[library.REMOVED_AT])
                self.assertEqual(self.metadata_path(identifier).read_bytes(), metadata)
                self.assertEqual(self.store.media_path(identifier).read_bytes(), source)
                self.assertEqual(len(self.store.media), 3)


if __name__ == "__main__":
    unittest.main()
