"""Real audio-only caption inputs agree with the editor's MP4 timeline."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import av
import numpy as np

from editor_caption_audio import render_caption_audio
from editor_speed import clip_duration
import video_editor as editor
from test_video_editor_render import decoded_audio, make_audio, make_source


def clip(**values):
    return {"id": "main", "media_id": "video", "in": 0., "out": 2.,
            "speed": 1., "volume": 1., "fade_in": 0., "fade_out": 0., **values}


def pcm_audio(path):
    with av.open(str(path)) as source:
        stream = source.streams.audio[0]
        rate = stream.sample_rate
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        blocks = [converted.to_ndarray() for frame in source.decode(audio=0)
                  for converted in resampler.resample(frame)]
        blocks.extend(frame.to_ndarray() for frame in resampler.resample(None))
    return rate, np.concatenate(blocks, axis=1)


class CaptionAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-caption-audio-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {key: self.root / name for key, name in
                      (("video", "video.mp4"), ("silent", "silent.mp4"),
                       ("music", "music.wav"), ("opposite", "opposite.wav"))}
        make_source(self.paths["video"], audio_delay=.25)
        make_source(self.paths["silent"], sound=False)
        make_audio(self.paths["music"], amplitude=.35)
        make_audio(self.paths["opposite"], amplitude=-.35)

    def project(self, **values):
        return {"width": 96, "height": 96, "fps": 24, "clips": [clip()],
                "audio_clips": [], "overlays": [], **values}

    def assert_export_agrees(self, project):
        wav, mp4 = self.root / "caption.wav", self.root / "export.mp4"
        actual = render_caption_audio(project, self.paths, wav, threading.Event(), lambda _: None)
        expected = editor.render_project(project, self.paths, mp4, threading.Event(), lambda _: None)
        self.assertEqual(actual["frames"], expected["frame_count"])
        self.assertEqual(actual["samples"], expected["audio_samples"])
        self.assertEqual(actual["duration"], expected["duration"])
        rate, clean = pcm_audio(wav)
        encoded_rate, encoded = decoded_audio(mp4)
        self.assertEqual(rate, encoded_rate)
        self.assertEqual(clean.shape, (2, actual["samples"]))
        self.assertAlmostEqual(float(np.max(np.abs(clean))), actual["peak"], places=7)
        encoded = encoded[:, :clean.shape[1]]
        # AAC is lossy, but events, mix boundaries and timing must still match.
        self.assertLess(float(np.sqrt(np.mean((clean - encoded) ** 2))), .025)
        self.assertGreater(np.corrcoef(clean.ravel(), encoded.ravel())[0, 1], .985)
        return actual, clean

    def test_trim_speed_fades_delays_tracks_and_video_layer_tail_match_export(self):
        main = clip(**{"in": .2, "out": 1.3, "speed": 1.7,
                       "volume": .6, "fade_in": .1, "fade_out": .15})
        layer = {**clip(id="layer", **{"in": .15, "out": 1.65, "speed": 1.5,
                                      "volume": .4, "fade_in": .2, "fade_out": .2}),
                 "kind": "video", "start": .75, "end": 1.75,
                 "x": .5, "y": .5, "width": .5, "rotation": 0, "opacity": 0}
        music = [{**clip(id=f"music{track}", media_id="music", **{"in": .1, "out": 1.6,
                        "volume": .3, "fade_in": .15, "fade_out": .2}),
                  "start": .2 + track * .05, "track": track} for track in range(4)]
        result, _ = self.assert_export_agrees(self.project(clips=[main], overlays=[layer], audio_clips=music))
        self.assertEqual(result["duration"], 1.75)

    def test_curved_speed_uses_same_pitch_preserving_audio_and_exact_endpoint(self):
        points = [{"time": 0., "speed": .5}, {"time": 1., "speed": 2.}, {"time": 2., "speed": .75}]
        main = clip(**{"in": .2, "out": 1.8, "speed_curve": points, "fade_in": .15, "fade_out": .1})
        layer = {**clip(id="layer", volume=.2, speed_curve=points), "kind": "video", "start": .3,
                 "end": .3 + clip_duration(clip(speed_curve=points)),
                 "x": .5, "y": .5, "width": .5, "rotation": 0, "opacity": 0}
        self.assert_export_agrees(self.project(clips=[main], overlays=[layer]))

    def test_many_short_cuts_use_cumulative_rounding(self):
        clips = [clip(id=f"cut{index}", **{"in": .5, "out": .5 + 1.49 / 24, "volume": .5})
                 for index in range(50)]
        result, _ = self.assert_export_agrees(self.project(clips=clips))
        self.assertEqual(result["frames"], 75)
        self.assertEqual(result["samples"], 150000)

    def test_no_video_decode_mute_silent_sources_and_audio_tails_do_not_extend(self):
        project = self.project(clips=[clip(volume=0, out=.5), clip(id="silent", media_id="silent", out=.5)],
                               audio_clips=[{**clip(media_id="music", volume=0), "start": 100, "track": 0}],
                               overlays=[{"kind": "text", "start": 0, "end": 500}])
        with patch.object(editor, "VideoReader", side_effect=AssertionError("video decode forbidden")):
            info = render_caption_audio(project, self.paths, self.root / "silence.wav", threading.Event(), lambda _: None)
        _, values = pcm_audio(self.root / "silence.wav")
        self.assertEqual(info["duration"], 1)
        self.assertEqual(info["peak"], 0)
        self.assertFalse(values.any())

    def test_clipping_occurs_after_all_sources_have_been_mixed(self):
        # Equal opposite tracks cancel after an intermediate sum exceeds 1.
        project = self.project(clips=[clip(media_id="silent", volume=0, out=.5)], audio_clips=[
            {**clip(media_id="music", volume=2), "start": 0, "track": 0},
            {**clip(media_id="music", volume=2), "start": 0, "track": 1},
            {**clip(media_id="opposite", volume=2), "start": 0, "track": 2},
            {**clip(media_id="opposite", volume=2), "start": 0, "track": 3},
        ])
        info = render_caption_audio(project, self.paths, self.root / "cancelled-mix.wav", threading.Event(), lambda _: None)
        self.assertLess(info["peak"], .0002)

    def test_cancel_deletes_partial_wav_and_releases_all_readers(self):
        event, target = threading.Event(), self.root / "partial.wav"
        progress = []
        def update(value):
            progress.append(value)
            if value >= 20:
                event.set()
        with self.assertRaises(editor.ExportCancelled):
            render_caption_audio(self.project(), self.paths, target, event, update)
        self.assertTrue(progress)
        self.assertFalse(target.exists())
        # A new pass can open the same sources and output immediately.
        render_caption_audio(self.project(), self.paths, target, threading.Event(), lambda _: None)
        self.assertTrue(target.is_file())

    def test_empty_timeline_rejects_audio_only_project_without_partial_file(self):
        target = self.root / "empty.wav"
        with self.assertRaisesRegex(editor.EditorError, "影片"):
            render_caption_audio(self.project(clips=[], audio_clips=[{**clip(media_id="music"), "start": 0, "track": 0}]),
                                 self.paths, target, threading.Event(), lambda _: None)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
