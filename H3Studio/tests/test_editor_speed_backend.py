"""Real curve exports and portable restores with isolated synthetic media."""
import json
import math
from fractions import Fraction
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import zipfile

import av
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import editor_project_files as files
from editor_media_io import register_editor_media_io
from editor_overlays import validate_layer
from editor_speed import clip_duration, timeline_at
import video_editor as editor
from test_video_editor_render import decoded_audio, make_source


def ramp(left=.25, right=4):
    return [{"time": 0., "speed": left}, {"time": 2., "speed": right}]


def clip(**values):
    return {"id": "main", "media_id": "a" * 32, "in": 0., "out": 2.,
            "speed": 1., "volume": 1., "fade_in": 0., "fade_out": 0., **values}


class SpeedBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-speed-backend-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = editor.EditorStore(self.root / "original", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.identifier = "a" * 32
        self.source = self.root / "tone.mp4"
        make_source(self.source)
        self.source.replace(self.store.media_dir / f"{self.identifier}.source")
        self.source = self.store.media_dir / f"{self.identifier}.source"
        self.store.media[self.identifier] = {"id": self.identifier, "name": "synthetic.mp4",
                                             **editor.probe_media(self.source), "created_at": editor.now()}
        self.paths = {self.identifier: self.source}

    def project(self, points=None, **values):
        main = clip(**({"speed_curve": points} if points is not None else {}))
        return self.store.create_project({"name": "curve", "width": 360, "height": 360,
                                          "fps": 24, "clips": [main], **values})

    def test_save_reload_omits_empty_curve_and_retains_hidden_source_anchors(self):
        project = self.project([])
        self.assertNotIn("speed_curve", project["clips"][0])
        project["clips"][0].update({"in": .5, "out": 1.5, "speed_curve": ramp()})
        saved = self.store.update_project(project["id"], project)
        reloaded = editor.EditorStore(self.root / "original", SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(reloaded.projects[saved["id"]]["clips"][0]["speed_curve"], ramp())
        self.assertAlmostEqual(clip_duration(saved["clips"][0]), math.log(3.0625 / 1.1875) / 1.875)

    def test_rejects_source_anchors_beyond_media_and_duration_uses_curve(self):
        with self.assertRaises(editor.EditorError):
            self.project([{"time": 0, "speed": 1}, {"time": 2.1, "speed": 1}])
        with self.assertRaises(editor.EditorError):
            self.project(ramp(), clips=[clip(speed_curve=ramp(), fade_in=1., fade_out=1.)])
        self.store.media[self.identifier]["duration"] = 12.1
        slow = [{"time": 0, "speed": .25}, {"time": 12.1, "speed": .25}]
        with self.assertRaises(editor.EditorError):
            self.project(slow, clips=[clip(id=f"main{i}", out=12.1, speed=4, speed_curve=slow) for i in range(50)])

    def test_detached_audio_curves_overlap_validation_uses_presentation_duration(self):
        detached = {**clip(id="audio", speed_curve=ramp()), "start": 0, "track": 0}
        project = self.project(ramp(), audio_clips=[detached])
        self.assertEqual(project["audio_clips"][0]["speed_curve"], ramp())
        with self.assertRaises(editor.EditorError):
            self.project(ramp(), audio_clips=[detached, {**detached, "id": "a2", "start": 1}])
        project = self.project(ramp(), audio_clips=[detached, {**detached, "id": "a2", "start": 1.5}])
        self.assertEqual(len(project["audio_clips"]), 2)

    def test_real_ramp_exports_video_event_pitch_audio_event_and_exact_endpoint(self):
        for points in (ramp(), ramp(4, .25), [{"time": 0, "speed": .5}, {"time": 1, "speed": 4}, {"time": 2, "speed": .5}]):
            with self.subTest(points=points):
                project = self.project(points)
                main = project["clips"][0]
                expected = clip_duration(main)
                target = self.root / f"ramp-{len(points)}-{points[0]['speed']}.mp4"
                result = editor.render_project(project, self.paths, target, threading.Event(), lambda _: None)
                self.assertEqual(result["frame_count"], math.floor(expected * 24 + .5 + 1e-7))
                self.assertEqual(result["audio_samples"], result["frame_count"] * 2000)
                with av.open(str(target)) as source:
                    frames = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]
                transition = next(i for i, pixels in enumerate(frames) if pixels[180, 180, 1] > pixels[180, 180, 0]) / 24
                self.assertAlmostEqual(transition, timeline_at(main, 1), delta=1 / 24 + .005)
                rate, audio = decoded_audio(target)
                wave = audio[0]
                event = timeline_at(main, 1)
                for start, end in ((.05, event - .05), (event + .05, expected - .05)):
                    if end - start < .1:
                        continue
                    values = wave[round(start * rate):round(end * rate)]
                    spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values))))
                    frequency = np.fft.rfftfreq(len(values), 1 / rate)[np.argmax(spectrum)]
                    self.assertAlmostEqual(frequency, 440, delta=7)
                # The known source amplitude switches at source 1 s. Verify
                # the audio follows the same curved clock as the video.
                window = round(.025 * rate)
                envelope = np.sqrt(np.mean(wave[:len(wave) // window * window].reshape(-1, window) ** 2, axis=1))
                indexes = np.flatnonzero(envelope > .26)
                self.assertTrue(len(indexes))
                self.assertAlmostEqual(indexes[0] * window / rate, event, delta=.08)
                tail = wave[round((expected - .1) * rate):round((expected - .025) * rate)]
                self.assertGreater(np.sqrt(np.mean(tail ** 2)), .12)

    def test_curve_audio_reader_exact_sample_count_silent_source_and_cancellation(self):
        for points in (ramp(), ramp(4, .25), ramp(.25, .25), ramp(4, 4)):
            main = clip(speed_curve=points)
            reader = editor.TempoAudioReader(self.source, main, threading.Event())
            try:
                blocks = list(reader.blocks)
                self.assertEqual(sum(block.shape[1] for block in blocks), round(clip_duration(main) * 48000))
                self.assertLessEqual(max(block.shape[1] for block in blocks), 1024)
                self.assertTrue(all(np.isfinite(block).all() for block in blocks))
            finally:
                reader.close()
        silent = self.root / "silent.mp4"
        make_source(silent, sound=False)
        reader = editor.TempoAudioReader(silent, clip(speed_curve=ramp()), threading.Event())
        try:
            self.assertFalse(reader.read(48000).any())
        finally:
            reader.close()
        event = threading.Event()
        reader = editor.TempoAudioReader(self.source, clip(speed_curve=ramp()), event)
        event.set()
        try:
            with self.assertRaises(editor.ExportCancelled):
                reader.read(480)
        finally:
            reader.close()

    def test_deep_bass_and_opposite_stereo_phase_survive_flat_and_changing_curves(self):
        for frequency in (20, 40, 80):
            source = self.root / f"bass-{frequency}.wav"
            with av.open(str(source), "w") as output:
                stream = output.add_stream("pcm_s16le", rate=48000)
                stream.layout = "stereo"
                for position in range(0, 96000, 4096):
                    count = min(4096, 96000 - position)
                    wave = (.2 * np.sin(2 * np.pi * frequency * np.arange(position, position + count) / 48000)).astype(np.float32)
                    frame = av.AudioFrame.from_ndarray(np.stack((wave, -wave)), format="fltp", layout="stereo")
                    frame.pts, frame.sample_rate, frame.time_base = position, 48000, Fraction(1, 48000)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            for points in (ramp(.25, .25), ramp(4, 4), ramp(), ramp(4, .25)):
                with self.subTest(frequency=frequency, points=points):
                    main = clip(speed_curve=points)
                    reader = editor.TempoAudioReader(source, main, threading.Event())
                    try:
                        sound = np.concatenate(list(reader.blocks), axis=1)
                    finally:
                        reader.close()
                    self.assertEqual(sound.shape[1], round(clip_duration(main) * 48000))
                    sound = sound[:, 3840:-3840]
                    self.assertLess(float(np.max(np.abs(sound[0] + sound[1]))), .0001)
                    spectrum = np.abs(np.fft.rfft(sound[0] * np.hanning(sound.shape[1])))
                    dominant = np.fft.rfftfreq(sound.shape[1], 1 / 48000)[np.argmax(spectrum)]
                    self.assertAlmostEqual(dominant, frequency, delta=2.5)
                    self.assertGreater(float(np.sqrt(np.mean(sound[0] ** 2))), .115)
    def test_upper_video_curve_clock_export_and_layer_validation(self):
        points = ramp()
        duration = clip_duration(clip(speed_curve=points))
        layer = {**clip(id="top", speed_curve=points), "kind": "video", "start": .25,
                 "end": .25 + duration, "x": .5, "y": .5, "width": 1,
                 "rotation": 0, "opacity": 1}
        project = self.project(clips=[], overlays=[layer])
        self.assertEqual(project["overlays"][0]["speed_curve"], points)
        with self.assertRaises(editor.EditorError):
            validate_layer({**layer, "end": 2.25})
        target = self.root / "upper.mp4"
        result = editor.render_project(project, self.paths, target, threading.Event(), lambda _: None)
        with av.open(str(target)) as source:
            frames = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]
        self.assertLess(frames[0].max(), 12)
        transition = next(i for i, pixels in enumerate(frames) if pixels[180, 180, 1] > pixels[180, 180, 0]) / 24
        self.assertAlmostEqual(transition, .25 + timeline_at(layer, 1), delta=1 / 24 + .005)
        self.assertEqual(result["audio_samples"], result["frame_count"] * 2000)

    def test_v5_archive_roundtrip_and_downgrade_rejection(self):
        project = self.project(ramp(), audio_clips=[{**clip(id="a", speed_curve=ramp()), "track": 0, "start": 0}])
        destination = self.root / "curve.h3edit.zip"
        files._build_archive(project, list(self.store.media.values()), self.paths, destination, threading.Event())
        with zipfile.ZipFile(destination) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(entries["project.json"])
        self.assertEqual(manifest["version"], 5)
        other = editor.EditorStore(self.root / "restored", SimpleNamespace(), SimpleNamespace(jobs={}))
        stage = self.root / "stage"
        stage.mkdir()
        restored, _ = files._prepare_import(other, destination, stage, threading.Event())
        self.assertEqual(restored["clips"][0]["speed_curve"], ramp())
        self.assertEqual(restored["audio_clips"][0]["speed_curve"], ramp())
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                manifest["version"] = version
                bad = self.root / f"v{version}.zip"
                with zipfile.ZipFile(bad, "w") as archive:
                    for name, data in entries.items():
                        archive.writestr(name, json.dumps(manifest).encode() if name == "project.json" else data)
                failed = self.root / f"failed{version}"
                failed.mkdir()
                with self.assertRaisesRegex(editor.EditorError, "第 5 版"):
                    files._prepare_import(other, bad, failed, threading.Event())
                self.assertFalse(other.media or other.projects)
        manifest.update(version=5)
        manifest["project"]["audio_clips"] = [None]
        invalid = self.root / "invalid-audio.zip"
        with zipfile.ZipFile(invalid, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, json.dumps(manifest).encode() if name == "project.json" else data)
        failed = self.root / "invalid-audio"
        failed.mkdir()
        with self.assertRaisesRegex(editor.EditorError, "片段格式"):
            files._prepare_import(other, invalid, failed, threading.Event())


class SpeedCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_response_advertises_curve_support(self):
        with tempfile.TemporaryDirectory(prefix="h3-speed-api-") as temporary:
            root = Path(temporary)
            static = root / "static"
            static.mkdir()
            (static / "editor.html").write_text("editor", encoding="utf-8")
            app = web.Application()
            app["assets"], app["jobs"] = SimpleNamespace(), SimpleNamespace(jobs={})
            editor.register_editor_routes(app, static, root / "data")
            register_editor_media_io(app)
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/api/editor/capabilities")
                self.assertEqual(response.status, 200)
                body = await response.json()
                self.assertTrue(body["speed_curves"])
                self.assertEqual(body["max_speed_curve_points"], 50)
                self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
