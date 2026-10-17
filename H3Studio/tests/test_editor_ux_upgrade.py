"""Production regressions for large captions, portable backgrounds and export history."""
import asyncio
import copy
import json
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
import editor_project_files as archives
import editor_captions as captions
import video_editor as editor
from editor_caption_audio import render_caption_audio
from test_video_editor_render import make_audio


def text_layer(**values):
    return {"id": "text", "kind": "text", "start": 0., "end": .1, "x": .5, "y": .5,
            "width": .8, "rotation": 0., "opacity": 1., "text": "字幕", "font_size": .08,
            "color": "#FFFFFF", "background": "transparent", "bold": False, "align": "center", **values}


def large_project():
    return {"name": "大量字幕與背景", "width": 360, "height": 360, "fps": 24,
            "canvas_duration": 500, "background": "#123456", "clips": [],
            "overlays": [text_layer(id=f"cue_{index}", track_id="subtitle_track", start=index / 10,
                                    end=(index + 1) / 10, text="繁體字幕" * 60,
                                    animation_idle={"preset": "float", "amount": 1, "speed": 1})
                         for index in range(5000)]}


class EditorUXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-editor-ux-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))

    def test_large_subtitles_save_reload_and_full_backup_keep_every_cue_and_animation(self):
        project = self.store.create_project(large_project())
        reloaded = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(reloaded.projects[project["id"]], project)
        path = self.root / "large.h3edit.zip"
        archives._build_archive(project, [], {}, path, threading.Event())
        self.assertGreater(path.stat().st_size, 1024**2)
        self.assertEqual(archives._archive_version(project), 13)
        staging = self.root / "staging"
        staging.mkdir()
        restored, media = archives._prepare_import(self.store, path, staging, threading.Event())
        self.assertEqual(media, [])
        self.assertEqual(restored["overlays"], project["overlays"])
        self.assertEqual(restored["canvas_duration"], 500)
        self.assertEqual(restored["background"], "#123456")

    def test_caption_capacity_does_not_consume_the_fifty_general_layers_and_keeps_active_bound(self):
        payload = large_project()
        titles = [text_layer(id=f"title_{index}", start=index, end=index + .5) for index in range(50)]
        payload["overlays"] = titles + payload["overlays"]
        saved = self.store.create_project(payload)
        self.assertEqual(len(saved["overlays"]), 5050)
        for extra in (text_layer(id="extra_title"), text_layer(id="extra_cue", track_id="subtitle_extra")):
            with self.assertRaises(editor.EditorError):
                self.store.create_project({**payload, "overlays": [*payload["overlays"], extra]})
        with self.assertRaisesRegex(editor.EditorError, "12"):
            self.store.create_project({"overlays": [text_layer(id=f"overlap_{index}", track_id=f"subtitle_{index}") for index in range(13)]})

    def test_large_caption_renderer_is_lazy_bounded_and_supports_backwards_seek(self):
        layers = [text_layer(id=f"cue_{index}", track_id="subtitle_track", start=index / 10, end=(index + 1) / 10)
                  for index in range(5000)]
        canvas = av.VideoFrame.from_ndarray(np.zeros((16, 16, 3), np.uint8), format="rgb24")
        with patch.object(overlays, "raster_text", side_effect=lambda *_: Image.new("RGBA", (8, 8), "white")) as raster:
            compositor = overlays.OverlayCompositor(layers, {}, 16, 16, cache_bytes=512)
            try:
                self.assertEqual(raster.call_count, 0)
                for time in (0, .1, .2, .3, 100, 499.9, .1):
                    compositor.apply(canvas, time)
                    self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)
                    self.assertEqual(len(compositor.active_indices), 1)
                self.assertLessEqual(raster.call_count, 7)
            finally:
                compositor.close()
            self.assertEqual(compositor.used_bytes, 0)

    def test_pure_background_renders_frame_aligned_video_and_caption_audio(self):
        project = self.store.create_project({"name": "純圖文", "width": 360, "height": 360,
                                             "canvas_duration": .25, "background": "#204060"})
        output, audio = self.root / "background.mp4", self.root / "caption.wav"
        result = editor.render_project(project, {}, output, threading.Event(), lambda _: None)
        caption_result = render_caption_audio(project, {}, audio, threading.Event(), lambda _: None)
        self.assertEqual(result["frame_count"], 6)
        self.assertEqual(caption_result["frames"], 6)
        self.assertEqual(caption_result["samples"], result["audio_samples"])
        self.assertEqual(captions.timeline_duration(project), .25)
        self.assertEqual(captions.audio_snapshot(project)["canvas_duration"], .25)
        with av.open(str(output)) as source:
            first = next(source.decode(video=0)).to_ndarray(format="rgb24")
        np.testing.assert_allclose(first[180, 180], [32, 64, 96], atol=4)

    def test_image_text_and_narration_export_without_a_main_video(self):
        image_id, sound_id = "a" * 32, "b" * 32
        image_path = self.store.media_dir / f"{image_id}.source"
        with Image.new("RGB", (16, 16), "red") as picture:
            picture.save(image_path, format="PNG")
        sound_path = self.root / "narration.wav"
        make_audio(sound_path)
        sound_path.replace(self.store.media_dir / f"{sound_id}.source")
        paths = {identifier: self.store.media_dir / f"{identifier}.source" for identifier in (image_id, sound_id)}
        for identifier, path in paths.items():
            self.store.media[identifier] = {"id": identifier, **editor.probe_media(path)}
        project = self.store.create_project({"width": 360, "height": 360, "canvas_duration": .25, "background": "#204060",
            "audio_clips": [{"id": "voice", "media_id": sound_id, "in": 0, "out": .25, "start": 0, "track": 0}],
            "overlays": [{"id": "picture", "kind": "image", "media_id": image_id, "start": 0, "end": .25, "width": .3},
                         text_layer(id="title", text="H3", start=0, end=.25, y=.85)]})
        output, wav = self.root / "image-narration.mp4", self.root / "image-narration.wav"
        result = editor.render_project(project, paths, output, threading.Event(), lambda _: None)
        audio_result = render_caption_audio(project, paths, wav, threading.Event(), lambda _: None)
        self.assertEqual(result["frame_count"], 6)
        self.assertGreater(audio_result["peak"], .05)
        with av.open(str(output)) as source:
            pixels = next(source.decode(video=0)).to_ndarray(format="rgb24")
        self.assertGreater(pixels[180, 180, 0], 240)
        np.testing.assert_allclose(pixels[5, 5], [32, 64, 96], atol=4)


class EditorUXHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-editor-ux-api-")
        self.root = Path(self.temp.name)
        self.app = web.Application(client_max_size=1024)
        self.app["assets"] = SimpleNamespace()
        self.app["jobs"] = SimpleNamespace(jobs={})
        self.store = editor.register_editor_routes(self.app, self.root, self.root)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_five_thousand_cues_can_be_created_and_updated_over_bounded_http(self):
        response = await self.client.post("/api/editor/projects", json=large_project())
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        project = await response.json()
        project["overlays"][-1]["text"] = "最後一句已修改"
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=project)
        self.assertEqual(response.status, 200)
        saved = await response.json()
        self.assertEqual(len(saved["overlays"]), 5000)
        self.assertEqual(saved["overlays"][-1]["text"], "最後一句已修改")

    async def test_export_history_survives_store_reload_and_reports_snapshot_without_its_content(self):
        project = self.store.create_project({"name": "第一版", "canvas_duration": 1})
        other = self.store.create_project({"name": "另一專案"})
        for number, timestamp in ((1, "2026-10-17T01:00:00Z"), (2, "2026-10-17T02:00:00Z")):
            snapshot = copy.deepcopy(project)
            snapshot["name"] = f"成品版本 {number}"
            identifier = f"{number:032x}"
            editor.atomic_json(self.store.export_dir / f"{identifier}.json", {"id": identifier, "project_id": project["id"],
                "status": "completed", "created_at": timestamp, "snapshot": snapshot})
        reloaded = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
        self.store.exports.update(reloaded.exports)
        response = await self.client.get(f"/api/editor/projects/{project['id']}/exports")
        self.assertEqual(response.status, 200)
        jobs = await response.json()
        self.assertEqual([job["source"]["name"] for job in jobs], ["成品版本 2", "成品版本 1"])
        self.assertEqual(jobs[0]["source"]["duration"], 1)
        self.assertTrue(all("snapshot" not in job for job in jobs))
        response = await self.client.get(f"/api/editor/projects/{other['id']}/exports")
        self.assertEqual(await response.json(), [])
        response = await self.client.get(f"/api/editor/projects/{'f' * 32}/exports")
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
