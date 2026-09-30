"""Real bounded Pillow/PyAV overlays, persistence and local HTTP contracts."""
import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import editor_overlays as overlays
from test_video_editor_render import make_source
import video_editor as editor


def text_layer(**values):
    return {"id": "text", "kind": "text", "start": 0, "end": 1, "x": .5, "y": .5,
            "width": .8, "rotation": 0, "opacity": 1, "text": "中文測試\n第二行", "font_size": .08,
            "color": "#FFFFFF", "background": "transparent", "bold": False, "align": "center", **values}


def image_layer(**values):
    return {"id": "image", "kind": "image", "media_id": "a" * 32, "start": 0, "end": 1,
            "x": .5, "y": .5, "width": .5, "rotation": 0, "opacity": 1, **values}


def frame(width=100, height=100):
    return av.VideoFrame.from_ndarray(np.zeros((height, width, 3), np.uint8), format="rgb24")


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-overlay-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.image_id = "a" * 32
        self.path = self.store.media_dir / f"{self.image_id}.source"
        with Image.new("RGBA", (20, 10), (255, 0, 0, 128)) as source:
            source.save(self.path, format="PNG")
        media = {"id": self.image_id, **editor.probe_media(self.path)}
        self.store.media[self.image_id] = media
        editor.atomic_json(self.store.media_dir / f"{self.image_id}.json", media)

    def compositor(self, layers, **values):
        compositor = overlays.OverlayCompositor(layers, {self.image_id: self.path}, 100, 100, **values)
        self.addCleanup(compositor.close)
        return compositor

    def test_strict_schema_persistence_legacy_and_image_reference(self):
        project = self.store.create_project({"overlays": [text_layer(), image_layer()]})
        self.assertEqual(len(project["overlays"]), 2)
        restored = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(restored.projects[project["id"]]["overlays"], project["overlays"])
        old = {**project}
        old.pop("overlays")
        editor.atomic_json(self.store.project_dir / f"{project['id']}.json", old)
        reloaded = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(reloaded.projects[project["id"]]["overlays"], [])
        for key, value in [("x", True), ("y", float("nan")), ("width", .019), ("rotation", 181),
                           ("opacity", 1.1), ("start", -1), ("end", 601), ("end", .001),
                           ("font_size", .31), ("bold", 1), ("color", "red"), ("background", "#ffffff00"),
                           ("align", "justify"), ("text", "x" * 501), ("text", "\n" * 10)]:
            with self.subTest(key=key, value=value), self.assertRaises(editor.EditorError):
                self.store.create_project({"overlays": [text_layer(**{key: value})]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [text_layer(), image_layer(id="text")]})
        self.store.media[self.image_id]["kind"] = "video"
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [image_layer()]})

    def test_maximum_overlap_adjacency_empty_text_and_crlf(self):
        layers = [text_layer(id=f"a{i}") for i in range(12)]
        self.store.create_project({"overlays": layers + [text_layer(id="next", start=1, end=2)]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": layers + [text_layer(id="extra")]})
        with self.assertRaises(editor.EditorError):
            self.store.create_project({"overlays": [text_layer(id=f"x{i}", start=i, end=i + 1) for i in range(51)]})
        normalized = overlays.validate_layer(text_layer(text="A\r\nB\rC"))
        self.assertEqual(normalized["text"], "A\nB\nC")
        with overlays.raster_text(overlays.validate_layer(text_layer(text="")), 100, 100) as blank:
            self.assertIsNone(blank.getbbox())

    def test_shared_track_mixed_kinds_persist_and_legacy_layers_stay_independent(self):
        layers = [text_layer(id="caption", track_id="upper", end=1),
                  image_layer(id="still", track_id="upper", start=1, end=2),
                  text_layer(id="front", start=0, end=2)]
        project = self.store.create_project({"overlays": layers})
        self.assertEqual([item.get("track_id") for item in project["overlays"]], ["upper", "upper", None])
        restored = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(restored.projects[project["id"]]["overlays"], project["overlays"])
        legacy = self.store.create_project({"overlays": [text_layer(id="a"), text_layer(id="b")]})
        self.assertTrue(all("track_id" not in item for item in legacy["overlays"]))

    def test_shared_track_ids_collisions_grouping_and_adjacent_epsilon(self):
        for value in (None, True, 1, "", "../track", "x" * 65, "track name"):
            with self.subTest(value=value), self.assertRaisesRegex(editor.EditorError, "軌道識別碼"):
                self.store.create_project({"overlays": [text_layer(track_id=value)]})
        for second_start in (0, .5, 1 - 2e-6):
            with self.subTest(start=second_start), self.assertRaisesRegex(editor.EditorError, "不能重疊"):
                self.store.create_project({"overlays": [text_layer(id="a", track_id="shared"),
                    image_layer(id="b", track_id="shared", start=second_start, end=2)]})
        for second_start in (1, 1 + .2, 1 - .5e-6):
            with self.subTest(start=second_start):
                self.store.create_project({"overlays": [text_layer(id="a", track_id="shared"),
                    image_layer(id="b", track_id="shared", start=second_start, end=2)]})
        # Non-adjacent members would change stacking order at different times.
        with self.assertRaisesRegex(editor.EditorError, "連續排列"):
            self.store.create_project({"overlays": [text_layer(id="a", track_id="shared"),
                text_layer(id="middle"), image_layer(id="b", track_id="shared", start=1, end=2)]})
        # A legacy clip's ID is also its effective track identifier.
        joined = self.store.create_project({"overlays": [text_layer(id="a"),
            image_layer(id="b", track_id="a", start=1, end=2)]})
        self.assertNotIn("track_id", joined["overlays"][0])
        self.assertEqual(joined["overlays"][1]["track_id"], "a")
        with self.assertRaisesRegex(editor.EditorError, "不能重疊"):
            self.store.create_project({"overlays": [text_layer(id="a"), image_layer(id="b", track_id="a")]})

    def test_alpha_once_timing_noop_and_cache_bounded(self):
        compositor = self.compositor([image_layer(opacity=.5, start=.25, end=.75)])
        original = frame()
        self.assertIs(compositor.apply(original, 0), original)
        self.assertIs(compositor.apply(original, .75), original)
        with patch.object(compositor, "_prepare", wraps=compositor._prepare) as prepare:
            first = compositor.apply(original, .25).to_ndarray(format="rgb24")
            compositor.apply(original, .5)
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(int(first[50, 50, 0]), 64)
        self.assertEqual(int(first[50, 50, 1]), 0)
        self.assertEqual(int(first[10, 10, 0]), 0)
        self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)
        uncached = self.compositor([image_layer()], cache_bytes=16)
        uncached.apply(original, .5)
        self.assertEqual(uncached.used_bytes, 0)
        compositor.close()
        self.assertEqual(compositor.used_bytes, 0)

    def test_positive_rotation_clockwise_stack_and_canvas_crop(self):
        source = np.zeros((10, 20, 4), np.uint8)
        source[:, :10] = (255, 0, 0, 255)
        source[:, 10:] = (0, 0, 255, 255)
        Image.fromarray(source).save(self.path, format="PNG")
        pixels = self.compositor([image_layer(rotation=90)]).apply(frame(), .5).to_ndarray(format="rgb24")
        self.assertGreater(pixels[33, 50, 0], 240)  # source left rotates to top
        self.assertGreater(pixels[67, 50, 2], 240)
        layers = [image_layer(), image_layer(id="top", rotation=180, width=.2)]
        pixels = self.compositor(layers).apply(frame(), .5).to_ndarray(format="rgb24")
        self.assertGreater(pixels[50, 45, 2], 240)
        crop = self.compositor([image_layer(x=0, width=2, rotation=45)])
        crop.apply(frame(), .5)
        self.assertLessEqual(crop.used_bytes, 100 * 100 * 4)

    def test_extreme_aspect_never_allocates_scaled_source(self):
        with Image.new("RGBA", (1, 8192), "red") as source:
            source.save(self.path, format="PNG")
        compositor = self.compositor([image_layer(width=2, rotation=45)])
        pixels = compositor.apply(frame(), .5).to_ndarray(format="rgb24")
        self.assertEqual(pixels.shape, (100, 100, 3))
        self.assertGreater(pixels[50, 50, 0], 240)
        self.assertLessEqual(compositor.used_bytes, 40000)

    def test_exif_orientation_and_cache_eviction(self):
        source = np.zeros((10, 20, 3), np.uint8)
        source[:, :10] = (255, 0, 0)
        source[:, 10:] = (0, 0, 255)
        with Image.fromarray(source) as image:
            exif = image.getexif()
            exif[274] = 6
            image.save(self.path, format="JPEG", exif=exif, quality=100, subsampling=0)
        metadata = editor.probe_media(self.path)
        self.assertEqual((metadata["width"], metadata["height"]), (10, 20))
        layers = [image_layer(width=.2, end=1), image_layer(id="next", start=1, end=2, width=.2)]
        compositor = self.compositor(layers, cache_bytes=20 * 40 * 4)
        pixels = compositor.apply(frame(), .5).to_ndarray(format="rgb24")
        self.assertGreater(pixels[38, 50, 0], 230)
        self.assertGreater(pixels[62, 50, 2], 230)
        compositor.apply(frame(), 1)
        self.assertEqual(list(compositor.cache), ["next"])
        self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)

    def test_text_cjk_color_background_align_and_shared_preview_raster(self):
        self.assertTrue(overlays.font_information()["cjk"])
        layer = text_layer(text="中文\n測試", font_size=.12, color="#12EF45", bold=True)
        encoded = overlays.text_png(layer, 100, 100)
        with Image.open(BytesIO(encoded)) as png:
            self.assertEqual(png.width, 80)
            self.assertEqual(png.mode, "RGBA")
            alpha = np.asarray(png.getchannel("A"))
            self.assertGreater(np.count_nonzero(alpha), 30)
            self.assertEqual(int(alpha[0, 0]), 0)
            source = np.asarray(png).copy()
        prepared, left, top = self.compositor([layer])._prepare(layer)
        try:
            # Integral unrotated geometry maps preview and export exactly.
            if prepared.height == source.shape[0] and prepared.width == source.shape[1]:
                np.testing.assert_array_equal(np.asarray(prepared), source)
        finally:
            prepared.close()
        with overlays.raster_text(text_layer(text="中文", background="#102030"), 100, 100) as solid:
            self.assertEqual(solid.getpixel((0, 0)), (16, 32, 48, 255))
        positions = []
        for align in ("left", "center", "right"):
            with overlays.raster_text(text_layer(text="A", align=align), 100, 100) as raster:
                positions.append(raster.getbbox()[0])
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])

    def test_text_missing_font_glyph_and_layout_limits(self):
        with patch.object(overlays, "_font", side_effect=editor.EditorError("缺少字型", 422)):
            with self.assertRaises(editor.EditorError):
                overlays.raster_text(text_layer(), 100, 100)
            self.assertFalse(overlays.font_information()["available"])
        with patch.object(overlays, "_has_glyph", return_value=False):
            with self.assertRaises(editor.EditorError) as raised:
                overlays.raster_text(text_layer(), 100, 100)
            self.assertEqual(raised.exception.status, 422)
        for layer in (text_layer(width=.02, font_size=.3), text_layer(text="中" * 500, width=.2),
                      text_layer(text="中\n" * 9 + "中", width=2, font_size=.3)):
            with self.subTest(layer=layer), self.assertRaises(editor.EditorError) as raised:
                overlays.raster_text(layer, 1920, 1920)
            self.assertEqual(raised.exception.status, 422)

    def test_real_mp4_global_timing_across_speed_cut_and_text(self):
        source, output = self.root / "input.mp4", self.root / "overlay.mp4"
        make_source(source, sound=False)
        with Image.new("RGBA", (20, 10), (0, 0, 255, 255)) as image:
            image.save(self.path, format="PNG")
        project = {"fps": 24, "width": 96, "height": 96,
                   "clips": [{"media_id": "video", "in": 0, "out": 1, "speed": 2, "volume": 0},
                             {"media_id": "video", "in": 0, "out": .5, "speed": .5, "volume": 0}],
                   "overlays": [image_layer(start=.25, end=.75),
                                text_layer(start=1, end=1.5, text="中文", background="#FFFFFF", color="#000000", y=.12)]}
        result = editor.render_project(project, {"video": source, self.image_id: self.path}, output, threading.Event(), lambda _: None)
        self.assertEqual(result["frame_count"], 36)
        with av.open(str(output)) as decoded:
            frames = [f.to_ndarray(format="rgb24") for f in decoded.decode(video=0)]
        for index in (0, 5, 18, 23):
            self.assertLess(frames[index][48, 48, 2], 30)
        for index in (6, 11, 12, 17):
            self.assertGreater(frames[index][48, 48, 2], 230)
        self.assertLess(frames[23][8, 15].max(), 30)
        self.assertGreater(frames[24][8, 15].min(), 220)


class OverlayAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-overlay-api-")
        self.root = Path(self.temp.name)
        self.app = web.Application()
        self.app["assets"], self.app["jobs"] = SimpleNamespace(), SimpleNamespace(jobs={})
        self.store = editor.register_editor_routes(self.app, self.root, self.root)
        overlays.register_editor_overlays(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_png_matches_shared_raster_sixtieth_frame_and_fonts(self):
        layer = text_layer(end=1 / 60, opacity=.1, rotation=90)
        response = await self.client.post("/api/editor/text-preview", json={"layer": layer, "width": 360, "height": 360})
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        self.assertEqual(response.content_type, "image/png")
        self.assertEqual(await response.read(), overlays.text_png(layer, 360, 360))
        fonts = await self.client.get("/api/editor/fonts")
        self.assertTrue((await fonts.json())["cjk"])

    async def test_bad_preview_status_and_body_limit(self):
        cases = [({"layer": image_layer(), "width": 360, "height": 360}, 400),
                 ({"layer": text_layer(), "width": True, "height": 360}, 400),
                 ({"layer": text_layer(width=.02, font_size=.3), "width": 360, "height": 360}, 422)]
        for payload, status in cases:
            response = await self.client.post("/api/editor/text-preview", json=payload)
            self.assertEqual(response.status, status, await response.text())
        response = await self.client.post("/api/editor/text-preview", data=b" " * 16385)
        self.assertEqual(response.status, 413)
        self.assertIn("error", await response.json())

    async def test_export_snapshot_keeps_image_paths(self):
        image_id, video_id = "a" * 32, "b" * 32
        for identifier, kind in ((image_id, "image"), (video_id, "video")):
            (self.store.media_dir / f"{identifier}.source").write_bytes(b"fixture")
            self.store.media[identifier] = {"id": identifier, "kind": kind, "duration": 2}
        project = self.store.create_project({"overlays": [image_layer(), text_layer()],
                    "clips": [{"id": "v", "media_id": video_id, "in": 0, "out": 1, "volume": 0}]})
        seen = []
        def render(snapshot, paths, output, event, progress):
            seen.append((snapshot, paths))
            output.write_bytes(b"export")
            return {}
        with patch.object(editor, "render_project", side_effect=render):
            job = self.store.start_export(project["id"])
            await self.store.export_task
        self.assertEqual(self.store.exports[job["id"]]["status"], "completed")
        self.assertEqual(set(seen[0][1]), {image_id, video_id})
        self.assertEqual(seen[0][0]["overlays"], project["overlays"])

    async def test_concurrent_preview_limit_and_release(self):
        entered, release = threading.Event(), threading.Event()
        original = overlays.text_png
        def delayed(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        requests = []
        try:
            with patch.object(overlays, "text_png", side_effect=delayed):
                for _ in range(2):
                    requests.append(asyncio.create_task(self.client.post("/api/editor/text-preview", json={
                        "layer": text_layer(), "width": 100, "height": 100})))
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                await asyncio.sleep(.02)
                busy = await self.client.post("/api/editor/text-preview", json={"layer": text_layer(), "width": 100, "height": 100})
                self.assertEqual(busy.status, 409)
                release.set()
                completed = await asyncio.gather(*requests)
                self.assertEqual([response.status for response in completed], [200, 200])
        finally:
            release.set()
            await asyncio.gather(*requests, return_exceptions=True)
