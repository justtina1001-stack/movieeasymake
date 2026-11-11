"""Real CPU media checks for effect transparency, timing and fixed geometry."""
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import av
import numpy as np
from PIL import Image

import effects_processing as effects


def movie(path, frames, fps=12, audio=False):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("ffv1", rate=Fraction(fps, 1))
        stream.width, stream.height = frames[0].shape[1], frames[0].shape[0]
        stream.pix_fmt = "bgr0"
        if audio:
            sound = output.add_stream("pcm_s16le", rate=48000)
            sound.layout = "mono"
        for index, image in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
        if audio:
            samples = round(len(frames) / fps * 48000)
            frame = av.AudioFrame.from_ndarray(np.zeros((1, samples), dtype=np.int16), format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 48000, 0, Fraction(1, 48000)
            for packet in sound.encode(frame):
                output.mux(packet)
            for packet in sound.encode():
                output.mux(packet)


def spec(**changes):
    return {"kind": "flame", "playback": "once", "duration": 1, "fps": 12,
            "trim_start": 0, "width": 256, "height": 256, "anchor": "bottom_center",
            "color": "#CC44FF", "black_level": 0, "softness": .08,
            "temporal_smoothing": 0, "loop_blend": 0, **changes}


def fire_frames(count=12):
    result = []
    yy, xx = np.indices((256, 256))
    for index in range(count):
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        strength = np.exp(-((xx - (112 + index)) ** 2 / 500 + (yy - 140) ** 2 / 1500))
        frame[:, :, 0] = np.rint(strength * 64)
        frame[:, :, 2] = np.rint(strength * 192)
        result.append(frame)
    return result


class EffectProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mkv"

    def tearDown(self):
        self.temporary.cleanup()

    def process(self, frames=None, **settings):
        movie(self.source, frames if frames is not None else fire_frames())
        return effects.process_effect_video(self.source, self.root / "output", spec(**settings))

    def test_real_export_preserves_color_over_black_fixed_canvas_pivot_and_original(self):
        originals = fire_frames()
        for index, gain in ((9, .2), (10, .04), (11, 0)):
            originals[index] = np.rint(originals[index].astype(float) * gain).astype(np.uint8)
        movie(self.source, originals)
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        stages = []
        result = effects.process_effect_video(self.source, self.root / "output", spec(),
                                             progress=lambda stage, value: stages.append((stage, value)))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertEqual((result["frame_count"], result["fps"], result["duration"]), (12, 12, 1))
        self.assertEqual(result["alpha_mode"], "straight")
        self.assertEqual(result["blend_mode"], "additive")
        self.assertEqual((result["pivot"]["x"], result["pivot"]["y"]), (128, 256))
        self.assertEqual(result["qa"]["status"], "passed")
        self.assertGreater(result["qa"]["metrics"]["adjacent_rgb_median_difference"], 0)
        self.assertGreater(result["qa"]["metrics"]["seam_to_adjacent_rgb_ratio"], 1)
        self.assertEqual({stage for stage, value in stages}, {"matting", "looping", "checking", "exporting"})
        for stage in {stage for stage, value in stages}:
            values = [value for seen, value in stages if seen == stage]
            self.assertEqual(values[-1], 1)
            self.assertEqual(values, sorted(values))
        with zipfile.ZipFile(self.root / "output/sequence.zip") as archive:
            self.assertEqual(len(archive.namelist()), 26)
            manifest = json.loads(archive.read("sequence.json"))
            self.assertEqual(manifest["loop_frame_range"], [0, 12])
            self.assertEqual(manifest["endpoint_convention"], "half_open")
            self.assertNotIn(str(self.root), archive.read("sequence.json").decode("utf-8"))
            for index in range(12):
                rgba = np.asarray(Image.open(io.BytesIO(archive.read(f"rgba/frame_{index + 1:06d}.png"))))
                black = np.asarray(Image.open(io.BytesIO(archive.read(f"additive/frame_{index + 1:06d}.png"))))
                self.assertEqual(rgba.shape, (256, 256, 4))
                self.assertTrue((rgba[0, :, 3] == 0).all())
                reconstructed = np.rint(rgba[:, :, :3].astype(float) * rgba[:, :, 3:4] / 255)
                self.assertLessEqual(np.abs(reconstructed - black).max(), 1)
                self.assertLessEqual(np.abs(black.astype(float) - originals[index]).max(), 1)
                # The moving effect remains at its original coordinates.
                self.assertEqual(np.unravel_index(black[:, :, 2].argmax(), (256, 256))[1],
                                 np.unravel_index(originals[index][:, :, 2].argmax(), (256, 256))[1])
        for filename in ["preview.mp4", "preview-checker.mp4", "preview-white.mp4"]:
            with av.open(str(self.root / "output" / filename)) as preview:
                self.assertEqual(len(preview.streams.audio), 0)
                self.assertEqual(float(preview.streams.video[0].average_rate), 12)
                self.assertEqual(sum(1 for _ in preview.decode(video=0)), 12)

    def test_trim_and_resampling_follow_the_source_clock_without_retiming(self):
        frames = []
        for index in range(48):
            frame = np.zeros((256, 256, 3), dtype=np.uint8)
            frame[100:140, 100:140] = (index, 0, 0)
            frames.append(frame)
        movie(self.source, frames, fps=24)
        chosen, info = effects._read_frames(self.source, 256, 256, 12, 12, .5, None)
        self.assertEqual([int(image[110, 110, 0]) for image in chosen], list(range(12, 36, 2)))
        self.assertEqual((info["trim_start"], info["trim_end"]), (.5, 1.5))
        self.assertTrue(info["resampled"])
        chosen, info = effects._read_frames(self.source, 256, 256, 30, 30, 1, None)
        self.assertEqual(len(chosen), 30)
        self.assertEqual(int(chosen[-1][110, 110, 0]), 47)
        with self.assertRaisesRegex(effects.EffectProcessingError, "原片長度|原片有效畫面不足"):
            effects.process_effect_video(self.source, self.root / "output", spec(trim_start=1.5, duration=1))
        self.assertFalse((self.root / "output/sequence.zip").exists())

    def test_short_or_empty_sources_cannot_be_extended_to_the_requested_length(self):
        movie(self.source, fire_frames(6))
        with self.assertRaisesRegex(effects.EffectProcessingError, "原片長度|原片有效畫面不足"):
            effects.process_effect_video(self.source, self.root / "output", spec())
        self.source.write_bytes(b"not a movie")
        with self.assertRaises(effects.EffectProcessingError):
            effects.process_effect_video(self.source, self.root / "bad", spec())

    def test_complete_five_second_retime_keeps_late_stages_order_and_last_frame_in_two_seconds(self):
        colors = [(220, 20, 10), (10, 220, 20), (20, 10, 220), (220, 10, 220), (220, 220, 10), (240, 240, 240)]
        frames = []
        for index in range(120):
            image = np.zeros((256, 128, 3), dtype=np.uint8)
            image[100:140, 48:80] = colors[index // 24 if index < 119 else 5]
            x = 8 + index // 2
            image[32:48, x:x + 8] = (80, 120, 200)
            frames.append(image)
        movie(self.source, frames, fps=24, audio=True)
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with av.open(str(self.source)) as original:
            self.assertEqual(len(original.streams.audio), 1)
        retimed = effects.process_effect_video(self.source, self.root / "retimed",
                                              spec(timing_mode="retime", duration=2, fps=24, trim_start=3))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertEqual((retimed["frame_count"], retimed["fps"], retimed["duration"]), (48, 24, 2))
        self.assertEqual(retimed["timing_mode"], "retime")
        info = retimed["source"]
        self.assertAlmostEqual(info["duration"], 5, delta=.002)
        self.assertEqual(info["timing_mode"], "retime")
        self.assertEqual(info["trim_start"], 0)
        self.assertEqual(info["trim_end"], info["duration"])
        self.assertEqual(info["selected_source_duration"], info["duration"])
        self.assertAlmostEqual(info["playback_speed"], 2.5, delta=.001)
        self.assertAlmostEqual(info["sampled_last_time"], 119 / 24, delta=.001)
        self.assertFalse(info["interpolated"])
        self.assertEqual(info["content_rect"], [64, 0, 128, 256])
        stage_ids, positions = [], []
        with zipfile.ZipFile(self.root / "retimed/sequence.zip") as archive:
            manifest = json.loads(archive.read("sequence.json"))
            self.assertEqual(manifest["timing_mode"], "retime")
            self.assertEqual(manifest["settings"]["trim_start"], 0)
            self.assertEqual(manifest["source"], info)
            for index in range(48):
                image = np.asarray(Image.open(io.BytesIO(archive.read(f"additive/frame_{index + 1:06d}.png"))))
                self.assertEqual(image.shape, (256, 256, 3))
                self.assertTrue((image[:, :64] == 0).all())
                self.assertTrue((image[:, 192:] == 0).all())
                stage_ids.append(colors.index(tuple(image[110, 128])))
                positions.append(float(np.flatnonzero(image[40, :, 2] > 0).mean()))
            self.assertEqual(stage_ids, sorted(stage_ids))
            self.assertEqual(set(stage_ids), set(range(6)))
            self.assertEqual((stage_ids[0], stage_ids[-1]), (0, 5))
            self.assertEqual(positions, sorted(positions))
            self.assertEqual((positions[0], positions[-1]), (75.5, 134.5))
        for filename in ("preview.mp4", "preview-checker.mp4", "preview-white.mp4"):
            with av.open(str(self.root / "retimed" / filename)) as preview:
                self.assertEqual(len(preview.streams.audio), 0)
                stream = preview.streams.video[0]
                self.assertEqual((stream.width, stream.height), (256, 256))
                self.assertEqual(float(stream.average_rate), 24)
                self.assertAlmostEqual(float(stream.duration * stream.time_base), 2, places=5)
                self.assertEqual(sum(1 for _ in preview.decode(video=0)), 48)
        trimmed = effects.process_effect_video(self.source, self.root / "trimmed", spec(duration=2, fps=24))
        self.assertEqual(trimmed["timing_mode"], "trim")
        self.assertEqual(trimmed["source"]["playback_speed"], 1)
        self.assertEqual(trimmed["source"]["trim_end"], 2)
        with zipfile.ZipFile(self.root / "trimmed/sequence.zip") as archive:
            ids = [colors.index(tuple(np.asarray(Image.open(io.BytesIO(archive.read(f"additive/frame_{index + 1:06d}.png"))))[110, 128]))
                   for index in range(48)]
        self.assertEqual(set(ids), {0, 1})
        self.assertEqual(ids, sorted(ids))

    def test_retime_can_slow_short_footage_and_an_omitted_mode_keeps_trim(self):
        movie(self.source, fire_frames(6))
        frames, info = effects._read_frames(self.source, 256, 256, 12, 24, 0, None, "retime")
        self.assertEqual(len(frames), 24)
        self.assertEqual(info["playback_speed"], .25)
        self.assertTrue(np.array_equal(frames[0], fire_frames(6)[0]))
        self.assertTrue(np.array_equal(frames[-1], fire_frames(6)[-1]))
        positions = [np.unravel_index(image[:, :, 2].argmax(), (256, 256))[1] for image in frames]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 6)
        with self.assertRaisesRegex(effects.EffectProcessingError, "原片長度|原片有效畫面不足"):
            effects.process_effect_video(self.source, self.root / "default", spec())

    def test_retime_itself_is_a_note_and_does_not_fail_quality_checks(self):
        frames = fire_frames()
        for index, gain in ((9, .2), (10, .04), (11, 0)):
            frames[index] = np.rint(frames[index].astype(float) * gain).astype(np.uint8)
        movie(self.source, frames)
        result = effects.process_effect_video(self.source, self.root / "retimed", spec(timing_mode="retime", duration=.5))
        self.assertEqual(result["qa"]["status"], "passed")
        self.assertEqual(result["qa"]["warnings"], [])
        self.assertTrue(any("沒有插值補畫" in note for note in result["qa"]["notes"]))

    def test_retime_rejects_invalid_modes_and_long_sources_and_checks_cancel_in_both_passes(self):
        movie(self.source, fire_frames())
        for mode in ("stretch", [], False, None, ""):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(effects.EffectProcessingError, "時長處理"):
                    effects.process_effect_video(self.source, self.root / "invalid", spec(timing_mode=mode))
        with patch.object(effects, "MAX_RETIME_SOURCE_DURATION", .5):
            # Matroska has no stream duration here: the limit must also use
            # decoded presentation timestamps, rather than just a header.
            with av.open(str(self.source)) as original:
                self.assertIsNone(original.streams.video[0].duration)
            with self.assertRaisesRegex(effects.EffectProcessingError, "整片變速.*原片"):
                effects.process_effect_video(self.source, self.root / "long", spec(timing_mode="retime"))
        with patch.object(effects, "MAX_DECODED_FRAMES", 5):
            with self.assertRaisesRegex(effects.EffectProcessingError, "影格過多"):
                effects.process_effect_video(self.source, self.root / "many", spec(timing_mode="retime"))
        for cancel_at in (4, 16):
            calls = 0
            def cancelled():
                nonlocal calls
                calls += 1
                return calls >= cancel_at
            with self.subTest(cancel_at=cancel_at):
                output = self.root / f"cancel-{cancel_at}"
                with self.assertRaisesRegex(effects.EffectProcessingError, "取消"):
                    effects.process_effect_video(self.source, output, spec(timing_mode="retime"), cancelled=cancelled)
                self.assertFalse((output / "sequence.zip").exists())

    def test_smoke_uses_soft_straight_alpha_and_has_no_additive_layer(self):
        frames = fire_frames()
        for image in frames:
            image[:, :, 0] = image[:, :, 2]
            image[:, :, 1] = image[:, :, 2]
        result = self.process(frames, kind="smoke", color="#BBBBBB")
        self.assertEqual(result["blend_mode"], "normal")
        with zipfile.ZipFile(self.root / "output/sequence.zip") as archive:
            self.assertFalse(any(name.startswith("additive/") for name in archive.namelist()))
            rgba = np.asarray(Image.open(io.BytesIO(archive.read("rgba/frame_000001.png"))))
            self.assertTrue(((rgba[:, :, 3] > 0) & (rgba[:, :, 3] < 255)).any())
            visible = rgba[:, :, 3] > 0
            self.assertTrue((rgba[:, :, :3][visible] >= 254).all())
        with self.assertRaisesRegex(effects.EffectProcessingError, "純黑煙"):
            effects.process_effect_video(self.source, self.root / "black", spec(kind="smoke", color="#000000"))

    def test_empty_dirty_and_clipped_footage_is_delivered_for_review_not_marked_passed(self):
        for name, frames, warning in [
            ("empty", [np.zeros((256, 256, 3), dtype=np.uint8)] * 12, "內容"),
            ("dirty", [np.full((256, 256, 3), (40, 60, 100), dtype=np.uint8)] * 12, "黑底"),
            ("clipped", [np.pad(np.full((30, 30, 3), 255, dtype=np.uint8), ((0, 226), (0, 226), (0, 0)))] * 12, "邊緣"),
        ]:
            with self.subTest(name=name):
                movie(self.source, frames)
                result = effects.process_effect_video(self.source, self.root / name, spec())
                self.assertEqual(result["qa"]["status"], "needs_adjustment")
                self.assertTrue(any(warning in message for message in result["qa"]["warnings"]))

    def test_dirty_source_is_detected_even_when_letterbox_borders_are_black(self):
        movie(self.source, [np.full((64, 256, 3), 100, dtype=np.uint8)] * 12)
        result = effects.process_effect_video(self.source, self.root / "output", spec())
        self.assertEqual(result["qa"]["status"], "needs_adjustment")
        self.assertGreater(result["qa"]["metrics"]["border_mean"], .3)

    def test_loop_seam_checks_do_not_reverse_or_automatically_retime_effects(self):
        frames = fire_frames()
        frames[-1] = np.zeros_like(frames[-1])
        result = self.process(frames, playback="loop")
        self.assertEqual(result["frame_count"], 12)
        self.assertEqual(result["qa"]["status"], "needs_adjustment")
        self.assertTrue(any("接縫" in item for item in result["qa"]["warnings"]))
        with zipfile.ZipFile(self.root / "output/sequence.zip") as archive:
            last = np.asarray(Image.open(io.BytesIO(archive.read("rgba/frame_000012.png"))))
            self.assertEqual(int(last[:, :, 3].max()), 0)

    def test_mixed_explosion_recommends_normal_alpha_without_claiming_separate_layers(self):
        result = self.process(kind="explosion")
        self.assertEqual(result["blend_mode"], "normal")
        self.assertEqual(result["qa"]["status"], "needs_adjustment")
        with zipfile.ZipFile(self.root / "output/sequence.zip") as archive:
            manifest = json.loads(archive.read("sequence.json"))
            self.assertFalse(manifest["mixed_layers_separated"])
            self.assertEqual(manifest["color_space"], "sRGB")
            self.assertTrue(any(name.startswith("additive/") for name in archive.namelist()))
        self.assertTrue(any("尚未獨立分層" in message for message in result["qa"]["warnings"]))

    def test_one_shot_warns_if_the_selected_interval_ends_before_dissipation(self):
        movie(self.source, fire_frames())
        unfinished = effects.process_effect_video(self.source, self.root / "unfinished", spec())
        self.assertEqual(unfinished["qa"]["status"], "needs_adjustment")
        self.assertGreater(unfinished["qa"]["metrics"]["tail_energy_to_peak_ratio"], .8)
        self.assertTrue(any("尚未完全消散" in message for message in unfinished["qa"]["warnings"]))
        frames = fire_frames()
        # Image-driven one-shots may begin with a fully visible reference effect.
        for index, gain in ((9, .2), (10, .04), (11, 0)):
            frames[index] = np.rint(frames[index].astype(float) * gain).astype(np.uint8)
        movie(self.source, frames)
        finished = effects.process_effect_video(self.source, self.root / "finished", spec())
        self.assertEqual(finished["qa"]["status"], "passed")
        self.assertEqual(finished["qa"]["metrics"]["final_energy_to_peak_ratio"], 0)

    def test_cancellation_size_limits_and_existing_outputs_are_respected(self):
        movie(self.source, fire_frames())
        with self.assertRaisesRegex(effects.EffectProcessingError, "取消"):
            effects.process_effect_video(self.source, self.root / "cancel", spec(), cancelled=lambda: True)
        def cancelled():
            return (self.root / "later/sequence.zip").exists()
        with self.assertRaisesRegex(effects.EffectProcessingError, "取消"):
            effects.process_effect_video(self.source, self.root / "later", spec(), cancelled=cancelled)
        with patch.object(effects, "MAX_PIXEL_FRAMES", 100):
            with self.assertRaisesRegex(effects.EffectProcessingError, "過大"):
                effects.process_effect_video(self.source, self.root / "large", spec())
        (self.root / "existing").mkdir()
        (self.root / "existing/qa.json").write_text("old", encoding="utf-8")
        with self.assertRaisesRegex(effects.EffectProcessingError, "已有成品"):
            effects.process_effect_video(self.source, self.root / "existing", spec())
        self.assertEqual((self.root / "existing/qa.json").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
