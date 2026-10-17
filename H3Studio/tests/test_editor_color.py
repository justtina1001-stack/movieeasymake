"""Timeline grading validation, persistence, archives and real CPU MP4 frames."""
import asyncio
import copy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import av
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import editor_color as color
import editor_project_files as files
import editor_media_io
import video_editor as editor


def grade(**values):
    return {"id": "grade", "start": .25, "end": .75, **values}


def source_video(path):
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = 96, 96, "yuv420p"
        stream.codec_context.thread_count = 1
        stream.options = {"crf": "12", "preset": "ultrafast", "threads": "1"}
        pixels = np.zeros((96, 96, 3), np.uint8)
        pixels[:] = [110, 95, 75]
        for index in range(24):
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 24)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


class ColorValidationTests(unittest.TestCase):
    def test_defaults_are_full_canonical_records_and_empty_is_compatible(self):
        self.assertEqual(color.validate_color_adjustments([], 24), [])
        item = color.validate_color_adjustments([grade(name="  暖色  ")], 24)[0]
        self.assertEqual(item["name"], "暖色")
        self.assertIs(item["enabled"], True)
        self.assertEqual({field: item[field] for field in color.COLOR_DEFAULTS}, color.COLOR_DEFAULTS)
        self.assertEqual(color.validate_color_adjustments([grade()], 24)[0]["name"], "調色片段")

    def test_all_numeric_fields_reject_nonfinite_bool_string_null_and_out_of_range(self):
        for field in ("start", "end", *color.COLOR_DEFAULTS):
            for value in (True, False, None, "0", float("nan"), float("inf"), -float("inf"), 10**1000):
                with self.subTest(field=field, value=str(value)[:30]), self.assertRaises(editor.EditorError):
                    color.validate_color_adjustments([grade(**{field: value})], 24)
        for field in color.COLOR_DEFAULTS:
            minimum, maximum = ((0, 100) if field == "strength" else (-2, 2) if field == "exposure" else (-100, 100))
            for value in (minimum - .001, maximum + .001):
                with self.subTest(field=field, value=value), self.assertRaises(editor.EditorError):
                    color.validate_color_adjustments([grade(**{field: value})], 24)
            color.validate_color_adjustments([grade(**{field: minimum})], 24)
            color.validate_color_adjustments([grade(**{field: maximum})], 24)

    def test_ids_names_enabled_and_unknown_fields_are_strict(self):
        for changes in ({"id": ""}, {"id": "../x"}, {"id": "a" * 65}, {"id": None},
                        {"name": ""}, {"name": " "}, {"name": "a" * 81}, {"name": 1},
                        {"name": "a\x00b"}, {"name": "a\x7fb"}, {"enabled": 1}, {"enabled": None},
                        {"enabled": "true"}, {"unknown": 1}):
            with self.subTest(changes=changes), self.assertRaises(editor.EditorError):
                color.validate_color_adjustments([grade(**changes)], 24)
        for invalid in (None, {}, "", [None], [1]):
            with self.subTest(invalid=invalid), self.assertRaises(editor.EditorError):
                color.validate_color_adjustments(invalid, 24)
        color.validate_color_adjustments([grade(name="😀" * 80, enabled=False)], 24)

    def test_global_ids_are_unique_and_array_order_is_preserved(self):
        with self.assertRaises(editor.EditorError):
            color.validate_color_adjustments([grade()], 24, {"grade"})
        with self.assertRaises(editor.EditorError):
            color.validate_color_adjustments([grade(), grade()], 24)
        items = [grade(id="z"), grade(id="a")]
        self.assertEqual([item["id"] for item in color.validate_color_adjustments(items, 24)], ["z", "a"])

    def test_time_bounds_one_frame_and_half_open_overlap_limits(self):
        for start, end in ((-1, 1), (0, 601), (1, 1), (1, 0), (0, 1 / 24 - 2e-9)):
            with self.subTest(start=start, end=end), self.assertRaises(editor.EditorError):
                color.validate_color_adjustments([grade(start=start, end=end)], 24)
        color.validate_color_adjustments([grade(start=600 - 1 / 24, end=600)], 24)
        first = [grade(id=f"a{i}", start=0, end=1, enabled=False) for i in range(4)]
        second = [grade(id=f"b{i}", start=1, end=2) for i in range(4)]
        self.assertEqual(len(color.validate_color_adjustments(first + second, 24)), 8)
        with self.assertRaises(editor.EditorError):
            color.validate_color_adjustments(first + [grade(id="fifth", start=0, end=1)], 24)
        twenty = [grade(id=f"c{i}", start=i, end=i + .5) for i in range(20)]
        color.validate_color_adjustments(twenty, 24)
        with self.assertRaises(editor.EditorError):
            color.validate_color_adjustments(twenty + [grade(id="extra", start=30, end=31)], 24)


class ColorMathTests(unittest.TestCase):
    def test_cross_runtime_fixed_vectors(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/editor_color_parity.json").read_text("utf-8"))
        self.assertEqual(fixtures["format"], "h3-editor-color-srgb-v1")
        for case in fixtures["cases"]:
            with self.subTest(case=case["name"]):
                pixels = np.array(case["pixels"], np.uint8).reshape((1, -1, case["channels"]))
                for item in case["items"]:
                    pixels = color.apply_color_pixels(pixels, item)
                self.assertEqual(pixels.reshape(-1).tolist(), case["expected"])

    def test_neutral_disabled_and_strength_zero_are_bit_identity(self):
        pixels = np.arange(256, dtype=np.uint8).reshape((4, 16, 4))
        for item in (grade(), grade(enabled=False, brightness=100), grade(strength=0, exposure=2),
                     grade(strength=40, contrast=0)):
            self.assertIs(color.apply_color_pixels(pixels, item), pixels)
        frame = av.VideoFrame.from_ndarray(pixels[:, :, :3], format="rgb24")
        self.assertIs(color.ColorCompositor([grade()]).apply(frame, .4), frame)

    def test_known_linear_exposure_brightness_and_desaturation(self):
        pixels = np.array([[[100, 50, 20]]], np.uint8)
        self.assertEqual(color.apply_color_pixels(pixels, grade(exposure=1))[0, 0].tolist(), [200, 100, 40])
        self.assertEqual(color.apply_color_pixels(pixels, grade(brightness=100))[0, 0].tolist(), [164, 114, 84])
        expected = int(np.floor((100 * .2126 + 50 * .7152 + 20 * .0722) + .5))
        self.assertEqual(color.apply_color_pixels(pixels, grade(saturation=-100))[0, 0].tolist(), [expected] * 3)

    def test_temperature_tint_strength_and_alpha(self):
        pixels = np.array([[[100, 100, 100, 13]]], np.uint8)
        self.assertEqual(color.apply_color_pixels(pixels, grade(temperature=100))[0, 0].tolist(), [125, 100, 75, 13])
        self.assertEqual(color.apply_color_pixels(pixels, grade(tint=100))[0, 0].tolist(), [110, 80, 110, 13])
        self.assertEqual(color.apply_color_pixels(pixels, grade(brightness=100, strength=50))[0, 0].tolist(), [132, 132, 132, 13])

    def test_highlight_shadow_curve_preserves_endpoints_and_finite_uint8(self):
        pixels = np.array([[[0, 32, 255], [128, 192, 255]]], np.uint8)
        result = color.apply_color_pixels(pixels, grade(shadows=100, highlights=-100))
        self.assertEqual(result[0, 0, 0], 0)
        self.assertEqual(result[0, 0, 2], 255)
        self.assertGreater(result[0, 0, 1], 32)
        self.assertLess(result[0, 1, 1], 192)
        extremes = grade(exposure=2, brightness=100, contrast=100, saturation=100,
                         temperature=100, tint=-100, highlights=100, shadows=-100)
        self.assertEqual(color.apply_color_pixels(pixels, extremes).dtype, np.uint8)

    def test_compositor_range_boundary_order_and_source_immutability(self):
        pixels = np.array([[[100, 50, 25]]], np.uint8)
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        add = grade(id="add", brightness=100)
        expose = grade(id="expose", exposure=1)
        compositor = color.ColorCompositor([add, expose])
        self.assertIs(compositor.apply(frame, .25 - 1e-7), frame)
        self.assertIs(compositor.apply(frame, .75), frame)
        expected = color.apply_color_pixels(color.apply_color_pixels(pixels, add), expose)
        np.testing.assert_array_equal(compositor.apply(frame, .25).to_ndarray(format="rgb24"), expected)
        alternate = color.ColorCompositor([expose, add]).apply(frame, .25).to_ndarray(format="rgb24")
        self.assertFalse(np.array_equal(expected, alternate))
        np.testing.assert_array_equal(frame.to_ndarray(format="rgb24"), pixels)


class ColorStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="editor-color-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.media_id = "a" * 32
        self.path = self.store.media_dir / f"{self.media_id}.source"
        source_video(self.path)
        self.media = {"id": self.media_id, "name": "synthetic.mp4", **editor.probe_media(self.path),
                      "created_at": editor.now(), "url": f"/api/editor/media/{self.media_id}/file"}
        self.store.media[self.media_id] = self.media
        editor.atomic_json(self.store.media_dir / f"{self.media_id}.json", self.media)

    def project(self, **values):
        return self.store.create_project({"width": 360, "height": 360, "fps": 24,
            "clips": [{"id": "main", "media_id": self.media_id, "in": 0, "out": 1, "volume": 0}], **values})

    def test_saved_reload_partial_update_clear_and_legacy_omit(self):
        legacy = self.project(color_adjustments=[])
        self.assertNotIn("color_adjustments", legacy)
        self.assertNotIn("color_adjustments", files._editable(legacy))
        project = self.project(color_adjustments=[grade(contrast=35)])
        expected = copy.deepcopy(project["color_adjustments"])
        partial = self.store.update_project(project["id"], {"name": "改名"})
        self.assertEqual(partial["color_adjustments"], expected)
        reloaded = editor.EditorStore(self.root / "data", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(reloaded.projects[project["id"]]["color_adjustments"], expected)
        cleared = self.store.update_project(project["id"], {"color_adjustments": []})
        self.assertNotIn("color_adjustments", cleared)
        self.assertNotIn("color_adjustments", json.loads((self.store.project_dir / f"{project['id']}.json").read_text("utf-8")))

    def test_id_collisions_with_main_audio_and_overlay_and_atomic_bad_save(self):
        project = self.project()
        before = (self.store.project_dir / f"{project['id']}.json").read_bytes()
        from test_video_editor_render import make_audio
        audio_id = "c" * 32
        audio_path = self.store.media_dir / f"{audio_id}.source"
        temporary_audio = self.root / "audio.wav"
        make_audio(temporary_audio)
        temporary_audio.replace(audio_path)
        self.store.media[audio_id] = {"id": audio_id, **editor.probe_media(audio_path)}
        for changes in ({"color_adjustments": [grade(id="main")]},
                        {"audio_clips": [{"id": "music", "media_id": audio_id, "in": 0, "out": 1}],
                         "color_adjustments": [grade(id="music")]},
                        {"overlays": [{"id": "text", "kind": "text", "start": 0, "end": 1, "text": "test"}],
                         "color_adjustments": [grade(id="text")]},
                        {"color_adjustments": [grade(contrast=None)]}):
            with self.subTest(changes=changes), self.assertRaises(editor.EditorError):
                self.store.update_project(project["id"], changes)
            self.assertEqual((self.store.project_dir / f"{project['id']}.json").read_bytes(), before)
            self.assertEqual(self.store.projects[project["id"]], project)

    def test_version12_archive_roundtrip_and_lower_version_rejected(self):
        project = self.project(color_adjustments=[grade(temperature=60, enabled=False)])
        archive_path = self.root / "project.zip"
        files._build_archive(project, [self.media], {self.media_id: self.path}, archive_path, threading.Event())
        self.assertEqual(files._archive_version(project), 12)
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("project.json"))
            entries = {name: archive.read(name) for name in archive.namelist()}
        self.assertEqual(manifest["version"], 12)
        self.assertEqual(manifest["project"]["color_adjustments"], project["color_adjustments"])
        restored_store = editor.EditorStore(self.root / "other", SimpleNamespace(), SimpleNamespace(jobs={}))
        staging = self.root / "staging"
        staging.mkdir()
        restored, media = files._prepare_import(restored_store, archive_path, staging, threading.Event())
        self.assertEqual(restored["color_adjustments"], project["color_adjustments"])
        self.assertNotEqual(restored["clips"][0]["media_id"], self.media_id)
        manifest["version"] = 11
        downgraded = self.root / "downgraded.zip"
        with zipfile.ZipFile(downgraded, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, json.dumps(manifest).encode("utf-8") if name == "project.json" else data)
        other_staging = self.root / "other-staging"
        other_staging.mkdir()
        with self.assertRaisesRegex(editor.EditorError, "12"):
            files._prepare_import(restored_store, downgraded, other_staging, threading.Event())
        self.assertEqual(restored_store.projects, {})
        self.assertEqual(restored_store.media, {})

    def test_real_mp4_range_affects_final_overlay_without_extending_or_changing_audio(self):
        project = self.project(color_adjustments=[grade(saturation=-100), grade(id="beyond", start=2, end=3, exposure=2)],
            overlays=[{"id": "title", "kind": "text", "text": "", "background": "#FF0000", "color": "#FFFFFF",
                       "start": 0, "end": 1, "x": .5, "y": .5, "width": .5, "font_size": .2}])
        # Tiny synthetic dimensions deliberately bypass UI size choices for render
        # correctness while retaining a normally validated timeline snapshot.
        project.update(width=96, height=96)
        target = self.root / "graded.mp4"
        info = editor.render_project(project, {self.media_id: self.path}, target, threading.Event(), lambda _: None)
        self.assertEqual(info["frame_count"], 24)
        self.assertEqual(info["duration"], 1)
        self.assertEqual(info["audio_samples"], 48000)
        with av.open(str(target)) as output:
            frames = [frame.to_ndarray(format="rgb24") for frame in output.decode(video=0)]
        self.assertGreater(frames[0][48, 48, 0], 220)
        self.assertLess(frames[0][48, 48, 1], 20)
        for index in range(6, 18):
            self.assertLess(int(frames[index][48, 48].max()) - int(frames[index][48, 48].min()), 5)
        self.assertGreater(frames[18][48, 48, 0], 220)
        self.assertLess(frames[18][48, 48, 1], 20)
        self.assertGreater(int(frames[4][10, 10].max()) - int(frames[4][10, 10].min()), 20)
        self.assertLess(int(frames[8][10, 10].max()) - int(frames[8][10, 10].min()), 5)

    def test_neutral_export_is_byte_identical_to_legacy_export(self):
        project = self.project()
        project.update(width=96, height=96)
        base, neutral = self.root / "base.mp4", self.root / "neutral.mp4"
        paths = {self.media_id: self.path}
        editor.render_project(project, paths, base, threading.Event(), lambda _: None)
        editor.render_project({**project, "color_adjustments": color.validate_color_adjustments([grade()], 24)},
                              paths, neutral, threading.Event(), lambda _: None)
        self.assertEqual(base.read_bytes(), neutral.read_bytes())


class ColorHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-color-api-test-")
        self.root = Path(self.temp.name)
        self.app = web.Application()
        self.app["assets"], self.app["jobs"] = SimpleNamespace(), SimpleNamespace(jobs={})
        self.store = editor.register_editor_routes(self.app, self.root, self.root)
        editor_media_io.register_editor_media_io(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.media_id = "b" * 32
        self.store.media[self.media_id] = {"id": self.media_id, "duration": 1, "kind": "video"}
        (self.store.media_dir / f"{self.media_id}.source").write_bytes(b"test source")

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_capability_save_validation_and_isolated_export_snapshot(self):
        response = await self.client.get("/api/editor/capabilities")
        capabilities = await response.json()
        self.assertEqual(capabilities["schema_version"], 13)
        self.assertIs(capabilities["color_adjustments"], True)
        self.assertEqual(capabilities["max_color_adjustments"], 20)
        self.assertEqual(capabilities["max_active_color_adjustments"], 4)
        response = await self.client.post("/api/editor/projects", json={
            "clips": [{"id": "main", "media_id": self.media_id, "in": 0, "out": 1, "volume": 0}],
            "color_adjustments": [grade(brightness=20)]})
        self.assertEqual(response.status, 201, await response.text())
        project = await response.json()
        captured = {}
        gate = threading.Event()

        def render(snapshot, _paths, output, cancel, _progress):
            captured["snapshot"] = copy.deepcopy(snapshot)
            if not gate.wait(3):
                raise AssertionError("test render gate timed out")
            if cancel.is_set():
                raise editor.ExportCancelled()
            output.write_bytes(b"test mp4")
            return {"duration": 1}

        try:
            with patch.object(editor, "render_project", side_effect=render):
                response = await self.client.post(f"/api/editor/projects/{project['id']}/exports")
                self.assertEqual(response.status, 202, await response.text())
                response = await self.client.put(f"/api/editor/projects/{project['id']}", json={"color_adjustments": [grade(brightness=-50)]})
                self.assertEqual(response.status, 200, await response.text())
                gate.set()
                await self.store.export_task
        finally:
            gate.set()
        self.assertEqual(captured["snapshot"]["color_adjustments"], project["color_adjustments"])
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json={"color_adjustments": [grade(enabled=1)]})
        self.assertEqual(response.status, 400)
        saved = await (await self.client.get(f"/api/editor/projects/{project['id']}")).json()
        self.assertEqual(saved["color_adjustments"][0]["brightness"], -50)


if __name__ == "__main__":
    unittest.main()
