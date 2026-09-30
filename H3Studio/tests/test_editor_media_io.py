import asyncio
import hashlib
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import av
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import editor_media_io as media_io
from video_editor import ExportCancelled, register_editor_routes
from test_video_editor_render import make_source


class EditorMediaIOTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        make_source(self.source)
        self.app = web.Application()
        self.app["assets"] = SimpleNamespace(path_for=Mock(side_effect=AssertionError("must bypass AssetStore")))
        self.app["jobs"] = SimpleNamespace(jobs={})
        self.store = register_editor_routes(self.app, self.root, self.root)
        self.previews = media_io.register_editor_media_io(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def upload(self, data=None, filename="clip.mp4"):
        form = FormData(quote_fields=False)
        form.add_field("file", self.source.read_bytes() if data is None else data, filename=filename)
        response = await self.client.post("/api/editor/upload", data=form)
        return response, await response.json()

    async def wait_status(self, identifier, states):
        async with asyncio.timeout(10):
            while True:
                response = await self.client.get(f"/api/editor/media/{identifier}/preview")
                value = await response.json()
                if value["status"] in states:
                    return value
                await asyncio.sleep(.01)

    async def test_direct_upload_saves_one_independent_copy_without_generic_assets(self):
        capabilities = await (await self.client.get("/api/editor/capabilities")).json()
        self.assertEqual(capabilities["schema_version"], 6)
        self.assertTrue(capabilities["overlay_tracks"])
        self.assertEqual(capabilities["audio_tracks"], 4)
        self.assertTrue(capabilities["visual_fades"])
        self.assertTrue(capabilities["video_overlays"])
        self.assertEqual(capabilities["max_video_overlays"], 3)
        content = self.source.read_bytes()
        response, media = await self.upload(filename="../原始.mp4")
        self.assertEqual(response.status, 201, media)
        self.assertEqual(media["name"], "原始.mp4")
        self.assertEqual(media["kind"], "video")
        self.assertEqual(self.store.media_path(media["id"]).read_bytes(), content)
        self.assertEqual(len(list(self.store.media_dir.glob("*.source"))), 1)
        self.assertEqual(list(self.store.media_dir.glob("*.uploading")), [])
        self.app["assets"].path_for.assert_not_called()

    async def test_audio_upload_is_accepted_but_video_proxy_is_not_needed(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(48000)
            sound.writeframes(b"\x00\x00" * 48000)
        response, media = await self.upload(data.getvalue(), "music.wav")
        self.assertEqual(response.status, 201, media)
        self.assertEqual(media["kind"], "audio")
        response = await self.client.post(f"/api/editor/media/{media['id']}/preview")
        self.assertEqual(response.status, 400)

    async def test_invalid_empty_oversized_and_multi_file_uploads_leave_no_partial_media(self):
        for data, name in [(b"", "a.mp4"), (b"not a video", "a.mp4"), (b"bad", "a.html")]:
            response, _ = await self.upload(data, name)
            self.assertEqual(response.status, 400)
        with patch.object(media_io, "MAX_UPLOAD_BYTES", 32):
            response, _ = await self.upload()
            self.assertEqual(response.status, 413)
        form = FormData()
        form.add_field("file", self.source.read_bytes(), filename="one.mp4")
        form.add_field("file", self.source.read_bytes(), filename="two.mp4")
        response = await self.client.post("/api/editor/upload", data=form)
        self.assertEqual(response.status, 400)
        self.assertEqual(list(self.store.media_dir.iterdir()), [])

    async def test_preview_short_gop_faststart_audio_range_and_reuse(self):
        _, media = await self.upload()
        original_hash = hashlib.sha256(self.store.media_path(media["id"]).read_bytes()).hexdigest()
        endpoint = f"/api/editor/media/{media['id']}/preview"
        self.assertEqual((await (await self.client.get(endpoint)).json())["status"], "missing")
        response = await self.client.post(endpoint)
        self.assertEqual(response.status, 202)
        self.assertEqual((await self.client.post(endpoint)).status, 202)
        result = await self.wait_status(media["id"], {"completed", "failed"})
        self.assertEqual(result["status"], "completed", result)
        target = self.previews.directory / f"{media['id']}.mp4"
        data = target.read_bytes()
        self.assertLess(data.find(b"moov"), data.find(b"mdat"))
        with av.open(str(target)) as decoded:
            frames = list(decoded.decode(video=0))
            self.assertEqual(len(frames), 48)
            keys = [index for index, frame in enumerate(frames) if frame.key_frame]
            self.assertEqual(keys[0], 0)
            self.assertTrue(all(b - a <= 12 for a, b in zip(keys, keys[1:])))
            self.assertGreaterEqual(len(keys), 4)
        with av.open(str(target)) as decoded:
            self.assertEqual(decoded.streams.audio[0].sample_rate, 48000)
            self.assertGreater(sum(frame.samples for frame in decoded.decode(audio=0)), 90000)
        response = await self.client.get(result["url"], headers={"Range": "bytes=10-29"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), data[10:30])
        self.assertIn("immutable", response.headers["Cache-Control"])
        before = target.stat().st_mtime_ns
        self.assertEqual((await self.client.post(endpoint)).status, 200)
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertEqual(hashlib.sha256(self.store.media_path(media["id"]).read_bytes()).hexdigest(), original_hash)
        reopened = media_io.PreviewCache(self.store)
        self.assertEqual(reopened.get(media["id"])["status"], "completed")
        await reopened.shutdown()

    async def test_cancel_running_and_queued_previews_and_allow_retry(self):
        _, first = await self.upload()
        _, second = await self.upload()
        started = threading.Event()
        def slow(source, media, target, cancel, progress):
            target.write_bytes(b"partial")
            started.set()
            cancel.wait(5)
            raise ExportCancelled()
        with patch.object(media_io, "render_preview", side_effect=slow):
            self.previews.start(first["id"])
            async with asyncio.timeout(5):
                while not started.is_set():
                    await asyncio.sleep(.01)
            self.previews.start(second["id"])
            self.assertEqual((await self.previews.cancel(second["id"]))["status"], "cancelled")
            self.assertEqual((await self.previews.cancel(first["id"]))["status"], "cancelled")
        self.assertEqual(list(self.previews.directory.glob("*.partial.mp4")), [])
        self.previews.start(first["id"])
        result = await self.wait_status(first["id"], {"completed", "failed"})
        self.assertEqual(result["status"], "completed", result)

    async def test_preview_missing_invalid_ids_and_source_limits(self):
        for identifier in ("unknown", "a" * 32):
            response = await self.client.post(f"/api/editor/media/{identifier}/preview")
            self.assertIn(response.status, (400, 404))
        _, media = await self.upload()
        response = await self.client.get(f"/api/editor/media/{media['id']}/preview/file")
        self.assertEqual(response.status, 404)
        self.store.media[media["id"]]["duration"] = 601
        response = await self.client.post(f"/api/editor/media/{media['id']}/preview")
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
