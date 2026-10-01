import asyncio
import copy
from fractions import Fraction
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

import av
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as studio
import job_thumbnails as thumbnails
from tests.test_continuation import make_video


SOURCE = "a" * 32


def make_sized_video(path, width, height, color):
    with av.open(str(path), "w") as output:
        video = output.add_stream("libx264", rate=24)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        with Image.new("RGB", (width, height), color) as image:
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = 0, Fraction(1, 24)
            for packet in video.encode(frame):
                output.mux(packet)
            for packet in video.encode():
                output.mux(packet)


class JobThumbnailAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.outputs = self.root / "outputs"
        self.enterContext(patch.multiple(studio, JOB_DIR=self.root / "jobs", OUTPUT_DIR=self.outputs))
        self.remote = AsyncMock(side_effect=AssertionError("thumbnails must never fetch remote outputs"))
        self.jobs = studio.JobManager(object(), type("Remote", (), {"fetch_output": self.remote})())
        self.source = self.outputs / "神社 完成作品.mp4"
        make_video(self.source, [(130, 65, 20)] * 3)
        self.jobs.jobs[SOURCE] = {"id": SOURCE, "status": "completed", "name": "神社",
                                  "local_output": self.source.name,
                                  "output": {"filename": "remote.mp4"}}
        self.app = web.Application()
        self.app["jobs"] = self.jobs
        self.cache = thumbnails.register_job_thumbnails(self.app, self.outputs)
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    def url(self, identifier=SOURCE):
        return f"/api/jobs/{identifier}/thumbnail"

    async def image(self, identifier=SOURCE):
        response = await self.client.get(self.url(identifier))
        content = await response.read()
        self.assertEqual(response.status, 200, content)
        return response, content

    async def test_chinese_local_video_produces_small_jpeg_without_modifying_job_or_source(self):
        original = self.source.read_bytes()
        metadata = copy.deepcopy(self.jobs.jobs[SOURCE])
        response, content = await self.image()
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("ETag", response.headers)
        self.assertIn("Last-Modified", response.headers)
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (96, 64))
            pixel = image.getpixel((48, 32))
            self.assertLess(sum(abs(a - b) for a, b in zip(pixel, (130, 65, 20))), 15)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.jobs.jobs[SOURCE], metadata)
        self.remote.assert_not_called()

    async def test_wide_and_portrait_thumbnails_preserve_whole_picture_aspect_ratio(self):
        for width, height, expected in ((640, 480, (240, 180)), (360, 640, (101, 180)),
                                        (1280, 720, (320, 180))):
            with self.subTest(size=(width, height)):
                make_sized_video(self.source, width, height, (40, 110, 180))
                _, content = await self.image()
                with Image.open(io.BytesIO(content)) as image:
                    self.assertEqual(image.size, expected)
                    self.assertLessEqual(image.width, 320)
                    self.assertLessEqual(image.height, 180)

    async def test_cache_hit_and_conditional_get_do_not_decode_again(self):
        with patch.object(thumbnails, "write_video_thumbnail", wraps=thumbnails.write_video_thumbnail) as decode:
            first, content = await self.image()
            _, second = await self.image()
            conditional = await self.client.get(self.url(), headers={"If-None-Match": first.headers["ETag"]})
            self.assertEqual(conditional.status, 304)
            self.assertEqual(content, second)
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(len(list(self.outputs.glob(f"{SOURCE}.thumbnail.*.jpg"))), 1)

    async def test_replacing_source_invalidates_cache_and_only_old_thumbnail_is_pruned(self):
        _, old = await self.image()
        old_cache = next(self.outputs.glob(f"{SOURCE}.thumbnail.*.jpg"))
        keep = self.outputs / f"{SOURCE}.thumbnail.keep.jpg"
        keep.write_bytes(b"unrelated user file")
        old_stamp = self.source.stat().st_mtime_ns
        make_video(self.source, [(20, 120, 210)] * 3)
        os.utime(self.source, ns=(old_stamp + 1_000_000, old_stamp + 1_000_000))
        _, updated = await self.image()
        self.assertNotEqual(old, updated)
        self.assertFalse(old_cache.exists())
        self.assertTrue(keep.exists())
        self.assertTrue(self.source.exists())
        self.assertEqual(len([path for path in self.outputs.glob(f"{SOURCE}.thumbnail.*.jpg")
                             if path != keep]), 1)

    async def test_same_size_and_mtime_but_new_source_path_invalidates_cache(self):
        _, old = await self.image()
        stamp = self.source.stat()
        other = self.outputs / "另一个來源.mp4"
        other.write_bytes(self.source.read_bytes())
        os.utime(other, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.jobs.jobs[SOURCE]["local_output"] = other.name
        with patch.object(thumbnails, "write_video_thumbnail", wraps=thumbnails.write_video_thumbnail) as decode:
            _, content = await self.image()
            self.assertEqual(content, old)
            self.assertEqual(decode.call_count, 1)

    async def test_missing_invalid_noncompleted_and_outside_sources_never_use_remote_downloads(self):
        for identifier in ("missing", "b" * 32, "..%2F..%2Foutside"):
            with self.subTest(identifier=identifier):
                response = await self.client.get(self.url(identifier))
                self.assertEqual(response.status, 404)
        job = self.jobs.jobs[SOURCE]
        for status in ("queued", "running", "interrupted", "failed", "cancelled"):
            with self.subTest(status=status):
                job["status"] = status
                response = await self.client.get(self.url())
                self.assertEqual(response.status, 404)
        job["status"] = "completed"
        outside = self.root / "outside.mp4"
        outside.write_bytes(self.source.read_bytes())
        for local in ("missing.mp4", "../outside.mp4", ".", None):
            with self.subTest(local=local):
                job["local_output"] = local
                response = await self.client.get(self.url())
                self.assertEqual(response.status, 404)
                self.assertIn("本機影片", (await response.json())["error"])
        self.remote.assert_not_called()
        self.assertEqual(list(self.outputs.glob("*.jpg")), [])

    async def test_deleting_local_source_does_not_return_preexisting_thumbnail(self):
        await self.image()
        self.source.unlink()
        response = await self.client.get(self.url())
        self.assertEqual(response.status, 404)
        self.remote.assert_not_called()

    async def test_corrupt_video_returns_clear_422_and_no_partial_cache(self):
        self.source.write_bytes(b"not a video")
        response = await self.client.get(self.url())
        self.assertEqual(response.status, 422)
        self.assertIn("無法讀取", (await response.json())["error"])
        self.assertEqual(list(self.outputs.glob(f"{SOURCE}.thumbnail.*")), [])

    async def test_concurrent_requests_share_decode_and_use_at_most_two_cpu_workers(self):
        other_ids = ["b" * 32, "c" * 32]
        for identifier in other_ids:
            self.jobs.jobs[identifier] = {**self.jobs.jobs[SOURCE], "id": identifier}
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        counter_lock = threading.Lock()
        active = peak = calls = 0
        original = thumbnails.write_video_thumbnail

        def slow_write(source, destination):
            nonlocal active, peak, calls
            with counter_lock:
                active += 1
                calls += 1
                peak = max(peak, active)
                if active == 2:
                    started.set()
            try:
                if not release.wait(5):
                    raise AssertionError("thumbnail concurrency fixture timed out")
                return original(source, destination)
            finally:
                with counter_lock:
                    active -= 1

        with patch.object(thumbnails, "write_video_thumbnail", side_effect=slow_write):
            requests = [asyncio.create_task(self.image(identifier)) for identifier in
                        (SOURCE, SOURCE, *other_ids, SOURCE)]
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            self.assertEqual(peak, 2)
            release.set()
            await asyncio.gather(*requests)
        self.assertEqual(calls, 3)
        self.assertEqual(peak, 2)

    async def test_deleted_job_during_decode_never_publishes_orphan_thumbnail(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = thumbnails.write_video_thumbnail

        def slow_write(source, destination):
            original(source, destination)
            started.set()
            if not release.wait(5):
                raise AssertionError("thumbnail deletion fixture timed out")

        with patch.object(thumbnails, "write_video_thumbnail", side_effect=slow_write):
            request = asyncio.create_task(self.client.get(self.url()))
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            self.jobs.jobs.pop(SOURCE)
            release.set()
            response = await request
        self.assertEqual(response.status, 404)
        self.assertEqual(list(self.outputs.glob(f"{SOURCE}.thumbnail.*")), [])

    async def test_existing_job_deletion_cleans_cache_and_preserves_other_outputs(self):
        await self.image()
        unrelated = self.outputs / "unrelated.mp4"
        unrelated.write_bytes(b"keep")
        self.jobs.delete_job(SOURCE)
        self.assertEqual(list(self.outputs.glob(f"{SOURCE}*")), [])
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.source.exists())
        response = await self.client.get(self.url())
        self.assertEqual(response.status, 404)

    async def test_cancelled_request_keeps_worker_alive_and_next_request_reuses_it(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = thumbnails.write_video_thumbnail

        def slow_write(source, destination):
            started.set()
            if not release.wait(5):
                raise AssertionError("thumbnail cancellation fixture timed out")
            original(source, destination)

        with patch.object(thumbnails, "write_video_thumbnail", side_effect=slow_write) as decode:
            cancelled = asyncio.create_task(self.cache.get(SOURCE))
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            next_request = asyncio.create_task(self.image())
            release.set()
            await next_request
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(list(self.outputs.glob("*.partial")), [])

    async def test_source_updated_during_decode_retries_and_never_commits_old_image(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = thumbnails.write_video_thumbnail
        calls = 0

        def slow_write(source, destination):
            nonlocal calls
            calls += 1
            original(source, destination)
            if calls == 1:
                started.set()
                if not release.wait(5):
                    raise AssertionError("thumbnail source update fixture timed out")

        with patch.object(thumbnails, "write_video_thumbnail", side_effect=slow_write):
            request = asyncio.create_task(self.image())
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            old_stamp = self.source.stat().st_mtime_ns
            make_video(self.source, [(20, 120, 210)] * 3)
            os.utime(self.source, ns=(old_stamp + 1_000_000, old_stamp + 1_000_000))
            release.set()
            _, content = await request
        with Image.open(io.BytesIO(content)) as image:
            self.assertLess(sum(abs(a - b) for a, b in zip(image.getpixel((48, 32)), (20, 120, 210))), 15)
        self.assertEqual(calls, 2)
        self.assertEqual(len(list(self.outputs.glob(f"{SOURCE}.thumbnail.*.jpg"))), 1)
        self.assertEqual(list(self.outputs.glob("*.partial")), [])

    async def test_app_factory_registers_endpoint(self):
        with patch.multiple(studio, DATA_DIR=self.root / "factory", ASSET_DIR=self.root / "factory/assets",
                            JOB_DIR=self.root / "factory/jobs", OUTPUT_DIR=self.root / "factory/outputs",
                            CONFIG_PATH=self.root / "factory/config.json"):
            app = studio.create_app()
        routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
        self.assertIn(("GET", "/api/jobs/{job_id}/thumbnail"), routes)


if __name__ == "__main__":
    unittest.main()
