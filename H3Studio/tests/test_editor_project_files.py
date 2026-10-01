"""Portable project archives against fresh stores and small real media."""
import asyncio
import copy
import errno
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile

import av
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import editor_project_files as files
import editor_overlays as overlays
from video_editor import atomic_json, now, probe_media, register_editor_routes, render_project
from test_video_editor_render import make_audio, make_source


class ProjectFileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-project-files-test-")
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        (self.static / "editor.html").write_text("editor", encoding="utf-8")
        self.clients, self.gates = [], []
        self.client, self.store, self.busy, self.app = await self.make_store("original")
        self.other, self.restored, self.other_busy, self.other_app = await self.make_store("restored")
        self.video_id, self.audio_id, self.unused_id = "1" * 32, "2" * 32, "3" * 32
        for identifier, audio in ((self.video_id, False), (self.audio_id, True), (self.unused_id, False)):
            path = self.store.media_dir / f"{identifier}.source"
            temporary = self.root / (identifier + (".wav" if audio else ".mp4"))
            (make_audio if audio else make_source)(temporary)
            temporary.replace(path)
            item = {"id": identifier, "name": "配樂.wav" if audio else "生成影片.mp4", **probe_media(path),
                    "created_at": now(), "url": f"/api/editor/media/{identifier}/file"}
            self.store.media[identifier] = item
            atomic_json(self.store.media_dir / f"{identifier}.json", item)
        base = {"id": "a1", "media_id": self.audio_id, "in": .25, "out": 1.5, "speed": 1,
                "start": .2, "track": 0, "volume": .3, "fade_in": .1, "fade_out": .2}
        self.project = self.store.create_project({"name": "森林 / 完整備份", "width": 360, "height": 360, "fps": 24,
            "clips": [{"id": "v1", "media_id": self.video_id, "in": .25, "out": 1.25, "speed": 2, "volume": .25},
                      {"id": "v2", "media_id": self.video_id, "in": .5, "out": 1.5, "speed": .5, "volume": 0}],
            "audio_clips": [base, {**base, "id": "a2", "track": 1, "start": 0, "in": 0, "out": 1, "speed": 2, "fade_in": 0},
                            {**base, "id": "a3", "track": 2, "media_id": self.video_id, "start": .3, "in": .2, "out": 1.2, "speed": .5},
                            {**base, "id": "a4", "track": 3, "start": .7, "in": 0, "out": .5, "volume": 0}]})

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        for client in self.clients:
            await client.close()
        self.temp.cleanup()

    async def make_store(self, name):
        # A tiny ordinary request-body limit demonstrates streamed multipart
        # archives do not need a global multi-gigabyte JSON-body allowance.
        app = web.Application(client_max_size=1024)
        app["assets"] = SimpleNamespace()
        app["jobs"] = SimpleNamespace(jobs={})
        store = register_editor_routes(app, self.static, self.root / name)
        busy = files.register_editor_project_files(app)
        client = TestClient(TestServer(app))
        self.clients.append(client)
        await client.start_server()
        return client, store, busy, app

    async def archive(self):
        response = await self.client.get(f"/api/editor/projects/{self.project['id']}/archive")
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        self.assertIn(".h3edit.zip", response.headers["Content-Disposition"])
        data = await response.read()
        self.assertEqual(len(data), int(response.headers["Content-Length"]))
        return data

    async def upload(self, data, client=None, filename="備份.h3edit.zip", second=False):
        form = FormData()
        form.add_field("file", data, filename=filename, content_type="application/zip")
        if second:
            form.add_field("unexpected", "extra")
        return await (client or self.other).post("/api/editor/projects/import", data=form)

    def unpack(self, data):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
        return json.loads(dict(entries)["project.json"]), entries

    async def test_v4_position_animation_roundtrip_preserves_hidden_points_and_rejects_downgrade(self):
        from test_editor_overlays import text_layer
        points = [{"time": -1, "x": .1, "y": .2, "easing": "ease_in_out"},
                  {"time": 3, "x": .9, "y": .8, "easing": "linear"}]
        self.project = self.store.update_project(self.project["id"], {**self.project,
            "overlays": [text_layer(start=1, end=2, position_keyframes=points)]})
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 4)
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        restored = (await response.json())["project"]
        self.assertEqual(restored["overlays"][0]["position_keyframes"], points)
        for version in (1, 2, 3):
            await self.assert_rejected_atomically(self.repack(data, lambda value: value.update(version=version)))

    async def test_v6_shared_track_roundtrip_preserves_group_ids_and_rejects_downgrade(self):
        from test_editor_overlays import text_layer
        from test_editor_video_layers import video_layer
        self.project = self.store.update_project(self.project["id"], {**self.project, "overlays": [
            video_layer(id="first", media_id=self.video_id, track_id="shared", start=0, end=.5, out=.5),
            text_layer(id="caption", track_id="shared", start=.5, end=1),
            video_layer(id="last", media_id=self.video_id, track_id="shared", start=1, end=1.5, out=.5),
            text_layer(id="front", start=0, end=2)]})
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 6)
        self.assertEqual([item.get("track_id") for item in manifest["project"]["overlays"]],
                         ["shared", "shared", "shared", None])
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        restored = (await response.json())["project"]
        self.assertEqual([item.get("track_id") for item in restored["overlays"]],
                         ["shared", "shared", "shared", None])
        for old, new in zip(self.project["overlays"], restored["overlays"]):
            self.assertEqual({k: v for k, v in old.items() if k != "media_id"},
                             {k: v for k, v in new.items() if k != "media_id"})
        for version in (1, 2, 3, 4, 5):
            await self.assert_rejected_atomically(self.repack(data, lambda value: value.update(version=version)))
        await self.assert_rejected_atomically(self.repack(data, lambda value:
            value["project"]["overlays"][1].update(start=.25)))
        await self.assert_rejected_atomically(self.repack(data, lambda value:
            value["project"]["overlays"].insert(1, value["project"]["overlays"].pop())))

    async def test_text_style_api_save_reload_and_inactive_preferences_require_version_seven(self):
        from test_editor_overlays import text_layer
        styled = text_layer(text="描邊與漸層", stroke_width=.08, stroke_color="#123abc",
                            fill_mode="linear_gradient", gradient_start="#ffe100",
                            gradient_end="#09ddee", gradient_angle=35)
        response = await self.client.put(f"/api/editor/projects/{self.project['id']}",
            json={"updated_at": self.project["updated_at"], "overlays": [styled]})
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        self.project = await response.json()
        active = self.project["overlays"][0]
        self.assertEqual(active["stroke_color"], "#123ABC")
        self.assertEqual(active["gradient_start"], "#FFE100")
        self.assertEqual(active["gradient_end"], "#09DDEE")
        self.assertEqual(active["stroke_width"], .08)
        self.assertEqual(active["fill_mode"], "linear_gradient")
        # Switching the effects off must retain the colors and direction for
        # a later toggle, without adding explicit default fields to snapshots.
        inactive = {**active, "stroke_width": 0, "fill_mode": "solid"}
        response = await self.client.put(f"/api/editor/projects/{self.project['id']}",
            json={"updated_at": self.project["updated_at"], "overlays": [inactive]})
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        self.project = await response.json()
        expected = self.project["overlays"][0]
        self.assertNotIn("stroke_width", expected)
        self.assertNotIn("fill_mode", expected)
        for field in ("stroke_color", "gradient_start", "gradient_end", "gradient_angle"):
            self.assertEqual(expected[field], active[field])
        restarted, restarted_store, _, _ = await self.make_store("original")
        response = await restarted.get(f"/api/editor/projects/{self.project['id']}")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["overlays"], [expected])
        self.assertEqual(restarted_store.projects[self.project["id"]]["overlays"], [expected])
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 7)
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        self.assertEqual((await response.json())["project"]["overlays"], [expected])
        await self.assert_rejected_atomically(self.repack(data, lambda value: value.update(version=6)))

    async def test_v7_text_style_archive_preserves_motion_tracks_fades_and_real_export(self):
        from test_editor_overlays import text_layer
        motion = [{"time": -1, "x": .35, "y": .4, "easing": "ease_in_out"},
                  {"time": 2, "x": .65, "y": .6, "easing": "linear"}]
        active = text_layer(id="gradient", track_id="captions", start=0, end=1,
            text="描邊漸層\nEDIT", font_size=.12, bold=True, align="left", color="#73A142",
            stroke_width=.08, stroke_color="#FA13CD", fill_mode="linear_gradient",
            gradient_start="#FFEE00", gradient_end="#00EEFF", gradient_angle=0,
            rotation=12, opacity=.85, fade_in=.1, fade_out=.2, position_keyframes=motion)
        inactive = text_layer(id="solid", track_id="captions", start=1, end=2,
            text="保留設定", color="#19AF39", background="#102030", rotation=-7,
            opacity=.8, fade_in=.2, fade_out=.1, stroke_width=0, stroke_color="#13CDF0",
            fill_mode="solid", gradient_start="#C0338A", gradient_end="#78B329", gradient_angle=235,
            position_keyframes=motion)
        self.project = self.store.update_project(self.project["id"], {**self.project,
            "clips": [{"id": "v1", "media_id": self.video_id, "in": 0, "out": 2,
                       "speed": 1, "volume": 0}], "audio_clips": [], "overlays": [active, inactive]})
        expected = self.project["overlays"]
        rasters = [overlays.text_png(layer, 360, 360) for layer in expected]
        legacy = {key: value for key, value in expected[0].items()
                  if key not in ("stroke_width", "stroke_color", "fill_mode", "gradient_start", "gradient_end", "gradient_angle")}
        self.assertNotEqual(rasters[0], overlays.text_png(legacy, 360, 360))
        original_output = self.root / "original-text-style.mp4"
        original_info = await asyncio.to_thread(render_project, self.project,
            {self.video_id: self.store.media_path(self.video_id)}, original_output,
            threading.Event(), lambda _: None)
        data = await self.archive()
        manifest, entries = self.unpack(data)
        self.assertEqual(manifest["version"], 7)
        self.assertEqual(manifest["project"]["overlays"], expected)
        self.assertEqual(len(manifest["media"]), 1)
        self.assertEqual(sum(name == f"media/{self.video_id}.source" for name, _ in entries), 1)
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        imported = await response.json()
        restored = imported["project"]
        self.assertEqual(restored["overlays"], expected)
        self.assertEqual([overlays.text_png(layer, 360, 360) for layer in restored["overlays"]], rasters)
        restored_id = restored["clips"][0]["media_id"]
        self.assertNotEqual(restored_id, self.video_id)
        self.assertEqual(self.restored.media_path(restored_id).read_bytes(), self.store.media_path(self.video_id).read_bytes())
        self.store.media_path(self.video_id).unlink()
        restored_output = self.root / "restored-text-style.mp4"
        restored_info = await asyncio.to_thread(render_project, restored,
            {restored_id: self.restored.media_path(restored_id)}, restored_output,
            threading.Event(), lambda _: None)
        self.assertEqual(original_info["frame_count"], 48)
        self.assertEqual(restored_info["frame_count"], 48)
        self.assertEqual(restored_info["duration"], 2)
        decoded = []
        for path in (original_output, restored_output):
            with av.open(str(path)) as video:
                decoded.append([frame.to_ndarray(format="rgb24").tobytes() for frame in video.decode(video=0)])
        self.assertEqual(len(decoded[0]), 48)
        self.assertEqual(decoded[1], decoded[0])
        for version in (1, 2, 3, 4, 5, 6):
            with self.subTest(version=version):
                await self.assert_rejected_atomically(self.repack(data, lambda value: value.update(version=version)))
        await self.assert_clean()

    async def test_v8_animation_and_bound_transition_roundtrip_preserves_editable_settings_and_export(self):
        from test_editor_overlays import text_layer
        entry, exit = {"type": "slide_left", "duration": .2}, {"type": "zoom_out", "duration": .2}
        main = [{"id": "left", "media_id": self.video_id, "in": 0, "out": .75, "speed": 1, "volume": .25,
                 "animation_in": entry, "animation_out": exit,
                 "transition_out": {"type": "crossfade", "duration": .5, "next_id": "right"}},
                {"id": "right", "media_id": self.video_id, "in": 1, "out": 1.75, "speed": 1, "volume": 0}]
        video = {"kind": "video", "media_id": self.video_id, "in": 0, "out": .75, "speed": 1, "volume": 0,
                 "track_id": "shared", "x": .7, "y": .5, "width": .4, "rotation": 0, "opacity": .7}
        layers = [{**video, "id": "layer-left", "start": 0, "end": .75,
                   "animation_in": {"type": "fade", "duration": .2},
                   "transition_out": {"type": "wipe_left", "duration": .5, "next_id": "layer-right"}},
                  {**video, "id": "layer-right", "start": .75, "end": 1.5, "in": 1, "out": 1.75,
                   "animation_out": {"type": "slide_down", "duration": .2}},
                  text_layer(id="title", end=1.5, text="Scene", font_size=.08,
                             animation_in={"type": "zoom_in", "duration": .25},
                             animation_out={"type": "fade", "duration": .25})]
        self.project = self.store.update_project(self.project["id"], {**self.project, "clips": main,
            "audio_clips": [], "overlays": layers})
        original = self.root / "original-animations.mp4"
        expected_info = await asyncio.to_thread(render_project, self.project,
            {self.video_id: self.store.media_path(self.video_id)}, original, threading.Event(), lambda _: None)
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 8)
        self.assertEqual(manifest["project"]["clips"], self.project["clips"])
        self.assertEqual(manifest["project"]["overlays"], self.project["overlays"])
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
        restored = (await response.json())["project"]
        for collection in ("clips", "overlays"):
            for previous, following in zip(self.project[collection], restored[collection]):
                self.assertEqual({key: value for key, value in previous.items() if key != "media_id"},
                                 {key: value for key, value in following.items() if key != "media_id"})
        self.assertEqual(restored["clips"][0]["transition_out"]["next_id"], restored["clips"][1]["id"])
        self.assertEqual(restored["overlays"][0]["transition_out"]["next_id"], restored["overlays"][1]["id"])
        new_id = restored["clips"][0]["media_id"]
        self.assertNotEqual(new_id, self.video_id)
        self.assertEqual({layer["media_id"] for layer in restored["overlays"] if layer["kind"] == "video"}, {new_id})
        self.store.media_path(self.video_id).unlink()
        output = self.root / "restored-animations.mp4"
        actual_info = await asyncio.to_thread(render_project, restored,
            {new_id: self.restored.media_path(new_id)}, output, threading.Event(), lambda _: None)
        self.assertEqual(actual_info, expected_info)
        self.assertEqual(actual_info["frame_count"], 36)
        decoded = []
        for path in (original, output):
            with av.open(str(path)) as source:
                decoded.append([frame.to_ndarray(format="rgb24").tobytes() for frame in source.decode(video=0)])
        self.assertEqual(decoded[0], decoded[1])
        await self.assert_rejected_atomically(self.repack(data, lambda value: value.update(version=7)))
        await self.assert_clean()

    async def test_explicit_default_text_styles_keep_v2_archives_and_legacy_versions_readable(self):
        from test_editor_overlays import text_layer
        defaults = {"stroke_width": 0, "stroke_color": "#000000", "fill_mode": "solid",
                    "gradient_start": "#ffffff", "gradient_end": "#ff8a3d", "gradient_angle": 90}
        self.project = self.store.update_project(self.project["id"], {**self.project,
            "overlays": [text_layer(**defaults)]})
        expected = self.project["overlays"][0]
        for field in defaults:
            self.assertNotIn(field, expected)
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 2)
        for version in (2, 3, 4, 5, 6):
            with self.subTest(version=version):
                response = await self.upload(self.repack(data, lambda value: value.update(version=version)))
                self.assertEqual(response.status, 201, await response.text() if response.status != 201 else "")
                self.assertEqual((await response.json())["project"]["overlays"], [expected])

    def repack(self, data, mutate=None, entries_change=None, compression=zipfile.ZIP_STORED):
        manifest, entries = self.unpack(data)
        if mutate:
            mutate(manifest)
        entries = [(name, json.dumps(manifest, ensure_ascii=False).encode() if name == "project.json" else content)
                   for name, content in entries]
        if entries_change:
            entries = entries_change(entries)
        output = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(output, "w", compression=compression) as archive:
            warnings.simplefilter("ignore", UserWarning)
            for name, content in entries:
                archive.writestr(name, content)
        return output.getvalue()

    async def assert_clean(self, store=None):
        store = store or self.restored
        async with asyncio.timeout(2):
            while list((store.root / "project_files").iterdir()):
                await asyncio.sleep(.005)
        self.assertEqual(list((store.root / "project_files").iterdir()), [])

    async def assert_rejected_atomically(self, data, status=400):
        before_media, before_projects = copy.deepcopy(self.restored.media), copy.deepcopy(self.restored.projects)
        before_files = {str(path.relative_to(self.restored.root)) for path in self.restored.root.rglob("*") if path.is_file()}
        response = await self.upload(data)
        self.assertEqual(response.status, status, await response.text())
        self.assertIn("error", await response.json())
        self.assertEqual(self.restored.media, before_media)
        self.assertEqual(self.restored.projects, before_projects)
        after_files = {str(path.relative_to(self.restored.root)) for path in self.restored.root.rglob("*") if path.is_file()}
        self.assertEqual(after_files, before_files)
        await self.assert_clean()

    async def test_real_roundtrip_restores_new_ids_shared_sources_four_audio_tracks_and_independent_export(self):
        data = await self.archive()
        manifest, entries = self.unpack(data)
        self.assertEqual(manifest["format"], files.FORMAT)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(len(entries), 3)
        self.assertEqual({item["id"] for item in manifest["media"]}, {self.video_id, self.audio_id})
        self.assertNotIn("updated_at", manifest["project"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text())
        value = await response.json()
        self.assertNotEqual(value["project"]["id"], self.project["id"])
        self.assertEqual(len(value["media"]), 2)
        new_ids = {item["id"] for item in value["media"]}
        self.assertFalse(new_ids & set(self.store.media))
        for kind in ("clips", "audio_clips"):
            for old, new in zip(self.project[kind], value["project"][kind]):
                self.assertEqual({k: v for k, v in old.items() if k != "media_id"}, {k: v for k, v in new.items() if k != "media_id"})
                self.assertIn(new["media_id"], new_ids)
        self.assertEqual(value["project"]["clips"][0]["media_id"], value["project"]["clips"][1]["media_id"])
        for identifier in self.store.media:
            self.store.media_path(identifier).unlink()
        for item in value["media"]:
            restored_file = await self.other.get(item["url"], headers={"Range": "bytes=0-31"})
            self.assertEqual(restored_file.status, 206)
            self.assertEqual(len(await restored_file.read()), 32)
        output = self.root / "restored-export.mp4"
        result = await asyncio.to_thread(render_project, value["project"],
            {identifier: self.restored.media_path(identifier) for identifier in new_ids}, output, threading.Event(), lambda _: None)
        self.assertEqual(result["frame_count"], 60)
        self.assertEqual(result["duration"], 2.5)
        with av.open(str(output)) as rendered:
            self.assertEqual(rendered.streams.audio[0].codec_context.name, "aac")
            self.assertEqual(sum(1 for _ in rendered.decode(video=0)), 60)
        await self.assert_clean(self.store)
        await self.assert_clean()

    async def test_overlay_archive_preserves_editable_text_and_independent_transparent_image_sources(self):
        image_id = "4" * 32
        path = self.store.media_dir / f"{image_id}.source"
        with Image.new("RGBA", (96, 64), (255, 0, 0, 128)) as image:
            image.save(path, format="PNG")
        item = {"id": image_id, "name": "透明圖層.png", **probe_media(path), "created_at": now(),
                "url": f"/api/editor/media/{image_id}/file"}
        self.store.media[image_id] = item
        atomic_json(self.store.media_dir / f"{image_id}.json", item)
        shared = {"start": 0, "end": 2.5, "x": .5, "y": .5, "width": .5, "rotation": 15, "opacity": .75}
        overlays = [{**shared, "id": "pic", "kind": "image", "media_id": image_id},
                    {**shared, "id": "caption", "kind": "text", "text": "完整備份\n中文圖層", "font_size": .05,
                     "color": "#ffffff", "background": "#123456", "bold": True, "align": "center"},
                    {**shared, "id": "second-pic", "kind": "image", "media_id": image_id, "x": .8, "width": .1}]
        self.project = self.store.update_project(self.project["id"], {**self.project, "overlays": overlays})
        data = await self.archive()
        manifest, entries = self.unpack(data)
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["project"]["overlays"], self.project["overlays"])
        self.assertEqual(len(manifest["media"]), 3)
        self.assertEqual(sum(name == f"media/{image_id}.source" for name, _ in entries), 1)
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text())
        imported = await response.json()
        restored = imported["project"]
        self.assertEqual(restored["overlays"][1], self.project["overlays"][1])
        first, second = restored["overlays"][0], restored["overlays"][2]
        self.assertNotEqual(first["media_id"], image_id)
        self.assertEqual(first["media_id"], second["media_id"])
        self.assertEqual(self.restored.media_path(first["media_id"]).read_bytes(), path.read_bytes())
        path.unlink()  # The restored project must not rely on the source store.
        for field in ("x", "y", "width", "rotation", "opacity", "start", "end"):
            self.assertEqual(first[field], overlays[0][field])
        output = self.root / "overlay-restored.mp4"
        info = await asyncio.to_thread(render_project, restored,
            {entry["id"]: self.restored.media_path(entry["id"]) for entry in imported["media"]}, output,
            threading.Event(), lambda _: None)
        self.assertEqual(info["frame_count"], 60)
        await self.assert_rejected_atomically(self.repack(data, lambda m: m.update(version=1)))
        await self.assert_clean()

    async def test_layered_video_and_visual_fades_roundtrip_in_version_three(self):
        payload = copy.deepcopy(self.project)
        payload["clips"][0].update(fade_in=.2, fade_out=.1)
        shared = {"kind": "video", "media_id": self.unused_id, "start": .2,
                  "in": 0, "out": 1.4, "speed": .5, "end": 3,
                  "volume": .2, "fade_in": .3, "fade_out": .4,
                  "x": .7, "y": .3, "width": .4, "rotation": 12, "opacity": .8}
        payload["overlays"] = [
            {**shared, "id": "upper-video"},
            {"id": "title", "kind": "text", "start": 0, "end": 3,
             "text": "淡入淡出", "font_size": .08, "color": "#FFFFFF",
             "background": "transparent", "bold": True, "align": "center",
             "x": .5, "y": .8, "width": .7, "rotation": 0, "opacity": 1,
             "fade_in": .5, "fade_out": .5},
            {**shared, "id": "front-video", "start": .25, "in": .5,
             "out": 1.5, "speed": 1, "end": 1.25, "fade_in": .1, "fade_out": .1}]
        self.project = self.store.update_project(self.project["id"], payload)
        data = await self.archive()
        manifest, entries = self.unpack(data)
        self.assertEqual(manifest["version"], 3)
        self.assertEqual(manifest["project"]["clips"][0]["fade_in"], .2)
        self.assertEqual(len(manifest["media"]), 3)
        self.assertEqual(sum(name == f"media/{self.unused_id}.source" for name, _ in entries), 1)
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text())
        imported = await response.json()
        restored = imported["project"]
        self.assertEqual(restored["overlays"][1], self.project["overlays"][1])
        for field in ("clips", "overlays"):
            for old, new in zip(self.project[field], restored[field]):
                self.assertEqual({k: v for k, v in old.items() if k != "media_id"},
                                 {k: v for k, v in new.items() if k != "media_id"})
        original_path = self.store.media_path(self.unused_id)
        restored_id = restored["overlays"][0]["media_id"]
        self.assertNotEqual(restored_id, self.unused_id)
        self.assertEqual(restored_id, restored["overlays"][2]["media_id"])
        self.assertEqual(original_path.read_bytes(), self.restored.media_path(restored_id).read_bytes())
        original_path.unlink()
        result = await asyncio.to_thread(render_project, restored,
            {item["id"]: self.restored.media_path(item["id"]) for item in imported["media"]},
            self.root / "layered-video-restored.mp4", threading.Event(), lambda _: None)
        self.assertEqual(result["frame_count"], 72)
        self.assertEqual(result["duration"], 3)
        for version in (1, 2):
            await self.assert_rejected_atomically(self.repack(data, lambda m: m.update(version=version)))
        await self.assert_clean()

    async def test_visual_fades_without_overlays_also_require_version_three(self):
        payload = copy.deepcopy(self.project)
        payload["clips"][0].update(fade_in=.2, fade_out=.1)
        self.project = self.store.update_project(self.project["id"], payload)
        data = await self.archive()
        manifest, _ = self.unpack(data)
        self.assertEqual(manifest["version"], 3)
        self.assertNotIn("overlays", manifest["project"])
        restored = await self.upload(data)
        self.assertEqual(restored.status, 201, await restored.text())
        self.assertEqual((await restored.json())["project"]["clips"][0]["fade_out"], .1)
        for version in (1, 2):
            await self.assert_rejected_atomically(self.repack(data, lambda m: m.update(version=version)))

    async def test_importing_same_package_twice_never_overwrites_existing_projects_or_media(self):
        data = await self.archive()
        first = await (await self.upload(data)).json()
        saved = copy.deepcopy(self.restored.projects[first["project"]["id"]])
        second_response = await self.upload(data)
        self.assertEqual(second_response.status, 201)
        second = await second_response.json()
        self.assertNotEqual(first["project"]["id"], second["project"]["id"])
        self.assertEqual(self.restored.projects[first["project"]["id"]], saved)
        self.assertEqual(len(self.restored.projects), 2)
        self.assertEqual(len(self.restored.media), 4)

    async def test_zip64_roundtrip_and_directory_limits_are_checked_even_without_footer_sentinels(self):
        with patch.object(zipfile, "ZIP64_LIMIT", 32):
            data = await self.archive()
        self.assertIn(b"PK\x06\x07", data[-42:])
        response = await self.upload(data)
        self.assertEqual(response.status, 201, await response.text())
        tampered = bytearray(data)
        zip64_offset = struct.unpack_from("<Q", tampered, len(tampered) - 34)[0]
        struct.pack_into("<Q", tampered, zip64_offset + 40, files.MAX_DIRECTORY_BYTES + 1)
        await self.assert_rejected_atomically(bytes(tampered), 413)

    async def test_metadata_is_reprobed_and_forged_source_duration_cannot_make_invalid_trims_valid(self):
        data = await self.archive()
        forged = self.repack(data, lambda manifest: [item["metadata"].update(duration=999, width=999, kind="audio") for item in manifest["media"]])
        response = await self.upload(forged)
        self.assertEqual(response.status, 201, await response.text())
        result = await response.json()
        self.assertTrue(all(item["duration"] < 3 for item in result["media"]))
        self.assertTrue(any(item["kind"] == "video" and item["width"] == 96 for item in result["media"]))
        forged_trim = self.repack(forged, lambda manifest: manifest["project"]["clips"][0].update(out=10))
        await self.assert_rejected_atomically(forged_trim)

    async def test_missing_tampered_invalid_versions_and_external_paths_roll_back_atomically(self):
        data = await self.archive()
        bad = [b"not a zip", data[:-10],
               self.repack(data, lambda manifest: manifest.update(version=99)),
               self.repack(data, lambda manifest: manifest["media"][0].update(file="https://example.com/video.mp4")),
               self.repack(data, lambda manifest: manifest["media"][0].update(sha256="0" * 64)),
               self.repack(data, entries_change=lambda entries: entries[1:]),
               self.repack(data, entries_change=lambda entries: entries + [("../outside.mp4", b"escape")]),
               self.repack(data, entries_change=lambda entries: entries + [("media/CON.source", b"device")]),
               self.repack(data, entries_change=lambda entries: entries + [("C:/outside.mp4", b"absolute")])]
        for index, invalid in enumerate(bad):
            with self.subTest(index=index):
                await self.assert_rejected_atomically(invalid)

    async def test_zip_duplicate_symlink_encryption_crc_and_compression_bomb_are_rejected(self):
        data = await self.archive()
        duplicate = self.repack(data, entries_change=lambda entries: entries + [entries[0]])
        await self.assert_rejected_atomically(duplicate)
        symlink = zipfile.ZipInfo(f"media/{self.video_id}.source")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        unsafe_link = self.repack(data, entries_change=lambda entries: [(symlink if name == symlink.filename else name, content) for name, content in entries])
        await self.assert_rejected_atomically(unsafe_link)
        encrypted = bytearray(data)
        for magic, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            index = encrypted.find(magic)
            flags = struct.unpack_from("<H", encrypted, index + flag_offset)[0]
            struct.pack_into("<H", encrypted, index + flag_offset, flags | 1)
        await self.assert_rejected_atomically(bytes(encrypted))
        corrupt = bytearray(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(f"media/{self.video_id}.source")
            offset = info.header_offset
        name_size, extra_size = struct.unpack_from("<HH", corrupt, offset + 26)
        corrupt[offset + 30 + name_size + extra_size + 30] ^= 1
        await self.assert_rejected_atomically(bytes(corrupt))
        bomb = self.repack(data, entries_change=lambda entries: [(name, b"\0" * (1024**2) if name.startswith("media/") else content) for name, content in entries], compression=zipfile.ZIP_DEFLATED)
        await self.assert_rejected_atomically(bomb, 413)

    async def test_streamed_archive_manifest_unpacked_and_entry_limits(self):
        data = await self.archive()
        for limit, value in (("MAX_ARCHIVE_BYTES", len(data) - 1), ("MAX_MANIFEST_BYTES", 32),
                             ("MAX_UNPACKED_TOTAL", 100), ("MAX_MEDIA_BYTES", 100), ("MAX_ENTRIES", 2)):
            with self.subTest(limit=limit), patch.object(files, limit, value):
                await self.assert_rejected_atomically(data, 413)
        with patch.object(files, "MAX_MEDIA_BYTES", 100):
            response = await self.client.get(f"/api/editor/projects/{self.project['id']}/archive")
            self.assertEqual(response.status, 413, await response.text())
        await self.assert_clean(self.store)
        for filename, extra in (("project.zip", False), ("project.h3edit.zip", True)):
            response = await self.upload(data, filename=filename, second=extra)
            self.assertEqual(response.status, 400)
        self.assertEqual((await self.other.post("/api/editor/projects/import", json={})).status, 400)

    async def test_publish_failure_rolls_back_linked_media_and_project_without_changing_existing_records(self):
        data = await self.archive()
        await self.upload(data)
        original_publish = files._publish_file
        calls = []
        def fail_second(source, destination, published, cancel):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("simulated disk failure")
            return original_publish(source, destination, published, cancel)
        with patch.object(files, "_publish_file", side_effect=fail_second), self.assertLogs("video_editor", level="ERROR"):
            await self.assert_rejected_atomically(data, 500)

    async def test_download_and_import_each_have_separate_409_guards_and_release_after_completion(self):
        data = await self.archive()
        for operation, name in (("archive", "_build_archive"), ("import", "_prepare_import")):
            started, release = threading.Event(), threading.Event()
            self.gates.append(release)
            original = getattr(files, name)
            def wait_then_run(*args):
                started.set()
                release.wait(3)
                return original(*args)
            with patch.object(files, name, side_effect=wait_then_run):
                first = asyncio.create_task(self.archive() if operation == "archive" else self.upload(data))
                async with asyncio.timeout(2):
                    while not started.is_set():
                        await asyncio.sleep(.005)
                response = (await self.client.get(f"/api/editor/projects/{self.project['id']}/archive")
                            if operation == "archive" else await self.upload(data))
                self.assertEqual(response.status, 409, await response.text())
                if operation == "archive":
                    self.store.update_project(self.project["id"], {"name": "下載期間另存的名稱"})
                release.set()
                completed = await first
                if operation == "import":
                    self.assertEqual(completed.status, 201)
                else:
                    manifest, _ = self.unpack(completed)
                    self.assertEqual(manifest["project"]["name"], self.project["name"])
            async with asyncio.timeout(2):
                while self.busy["archive"] if operation == "archive" else self.other_busy["import"]:
                    await asyncio.sleep(.005)
            self.assertFalse(self.busy["archive"] if operation == "archive" else self.other_busy["import"])

    async def test_windows_filesystems_without_hardlinks_use_exclusive_copy_without_overwriting(self):
        data = await self.archive()
        for winerror in (1, 50):
            error = OSError(errno.EINVAL, "hard links not supported")
            error.winerror = winerror
            with self.subTest(winerror=winerror), patch.object(files.os, "link", side_effect=error):
                response = await self.upload(data)
                self.assertEqual(response.status, 201, await response.text())
        source, target = self.root / "source", self.root / "existing"
        source.write_bytes(b"new")
        target.write_bytes(b"original")
        published = []
        with patch.object(files.os, "link", side_effect=error):
            with self.assertRaises(FileExistsError):
                files._publish_file(source, target, published, threading.Event())
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(published, [])

    async def test_cancelled_import_waits_for_worker_before_deleting_staging_and_never_publishes(self):
        data = await self.archive()
        started, cancelled, release = threading.Event(), threading.Event(), threading.Event()
        self.gates.append(release)
        stages = []
        def blocked_prepare(store, target, staging, event):
            stages.append(staging)
            started.set()
            event.wait(2)
            cancelled.set()
            release.wait(2)
            self.assertTrue(staging.exists())
            (staging / "worker-finished").write_text("finished")
            raise InterruptedError("cancelled")
        class Part:
            name, filename = "file", "test.h3edit.zip"
            def __init__(self): self.blocks = iter((data, b""))
            async def read_chunk(self, _size): return next(self.blocks)
        class Reader:
            def __init__(self): self.parts = iter((Part(), None))
            async def next(self): return next(self.parts)
        class Request:
            content_type, content_length = "multipart/form-data", len(data)
            async def multipart(self): return Reader()
        handler = next(route.handler for route in self.other_app.router.routes()
                       if route.method == "POST" and route.resource.canonical == "/api/editor/projects/import")
        with patch.object(files, "_prepare_import", side_effect=blocked_prepare):
            task = asyncio.create_task(handler(Request()))
            async with asyncio.timeout(2):
                while not started.is_set(): await asyncio.sleep(.005)
            task.cancel()
            async with asyncio.timeout(2):
                while not cancelled.is_set(): await asyncio.sleep(.005)
            self.assertFalse(task.done())
            self.assertTrue(stages[0].exists())
            release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.restored.projects, {})
        self.assertEqual(self.restored.media, {})
        self.assertFalse(self.other_busy["import"])
        await self.assert_clean()


if __name__ == "__main__":
    unittest.main()
