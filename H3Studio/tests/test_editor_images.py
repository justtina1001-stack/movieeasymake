"""Image uploads preserve source bytes and reject unbounded or animated content."""
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import editor_images
import editor_media_io
from video_editor import EditorError, register_editor_routes


def image_bytes(format="PNG", **kwargs):
    content = io.BytesIO()
    with Image.new("RGBA" if format != "JPEG" else "RGB", (12, 8), (255, 50, 0, 128) if format != "JPEG" else (255, 50, 0)) as source:
        source.save(content, format=format, **kwargs)
    return content.getvalue()


class ImageProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "opaque.source"

    def tearDown(self):
        self.temp.cleanup()

    def test_supported_formats_are_recognized_without_filename_and_keep_alpha(self):
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(format=fmt):
                content = image_bytes(fmt)
                self.path.write_bytes(content)
                self.assertTrue(editor_images.is_image_file(self.path))
                item = editor_images.probe_image(self.path)
                self.assertEqual((item["kind"], item["width"], item["height"], item["mime"]), ("image", 12, 8, mime))
                self.assertFalse(item["has_audio"])
                self.assertEqual(item["duration"], 0)
                self.assertEqual(self.path.read_bytes(), content)

    def test_exif_orientation_dimensions_match_display_without_rewriting_original(self):
        exif = Image.Exif(); exif[274] = 6
        content = image_bytes("JPEG", exif=exif)
        self.path.write_bytes(content)
        item = editor_images.probe_image(self.path)
        self.assertEqual((item["width"], item["height"]), (8, 12))
        self.assertEqual(self.path.read_bytes(), content)

    def test_truncated_unsupported_and_animation_are_rejected(self):
        for content in (b"<svg></svg>", b"not an image", image_bytes()[:40]):
            self.path.write_bytes(content)
            with self.assertRaises(EditorError): editor_images.probe_image(self.path)
        data = io.BytesIO()
        with Image.new("RGBA", (12, 8), "red") as first, Image.new("RGBA", (12, 8), "blue") as second:
            first.save(data, format="PNG", save_all=True, append_images=[second], duration=100)
        self.path.write_bytes(data.getvalue())
        with self.assertRaisesRegex(EditorError, "靜態"): editor_images.probe_image(self.path)

    def test_size_and_pixel_bounds_reject_before_large_decoding(self):
        self.path.write_bytes(image_bytes())
        for constant, maximum in (("MAX_IMAGE_BYTES", 32), ("MAX_IMAGE_PIXELS", 95), ("MAX_IMAGE_SIDE", 11)):
            with self.subTest(limit=constant), patch.object(editor_images, constant, maximum):
                with self.assertRaises(EditorError) as error: editor_images.probe_image(self.path)
                self.assertEqual(error.exception.status, 413)


class ImageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        app = web.Application()
        app["assets"] = SimpleNamespace(path_for=Mock(side_effect=AssertionError("independent image upload")))
        app["jobs"] = SimpleNamespace(jobs={})
        self.store = register_editor_routes(app, self.root, self.root)
        editor_media_io.register_editor_media_io(app)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def upload(self, content, name):
        form = FormData(); form.add_field("file", content, filename=name)
        return await self.client.post("/api/editor/upload", data=form)

    async def test_transparent_image_can_be_reopened_as_media_but_cannot_start_video_proxy(self):
        content = image_bytes()
        response = await self.upload(content, "浮水印.png")
        self.assertEqual(response.status, 201, await response.text())
        media = await response.json()
        self.assertEqual(media["kind"], "image")
        response = await self.client.get(media["url"])
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(await response.read(), content)
        response = await self.client.get(f"/api/editor/media/{media['id']}/thumbnail")
        self.assertEqual(response.status, 200)
        with Image.open(io.BytesIO(await response.read())) as thumb:
            self.assertEqual(thumb.mode, "RGBA")
            self.assertEqual(thumb.getpixel((6, 4))[3], 128)
            self.assertLessEqual(max(thumb.size), 320)
        self.assertEqual(self.store.media_path(media["id"]).read_bytes(), content)
        response = await self.client.post(f"/api/editor/media/{media['id']}/preview")
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.store.media), 1)

    async def test_bad_or_over_limit_image_upload_never_leaves_partial_sources(self):
        for content, name in ((b"bad", "a.png"), (b"<svg></svg>", "a.svg"), (image_bytes()[:40], "bad.webp")):
            response = await self.upload(content, name)
            self.assertEqual(response.status, 400, await response.text())
        with patch.object(editor_media_io, "MAX_IMAGE_BYTES", 32):
            response = await self.upload(image_bytes(), "large.png")
            self.assertEqual(response.status, 413)
        self.assertEqual(self.store.media, {})
        self.assertEqual(list(self.store.media_dir.iterdir()), [])
