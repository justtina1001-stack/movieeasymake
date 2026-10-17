import contextlib
import io
import json
from pathlib import Path
import re
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

import editor_caption_worker as worker


def segment(text, start=0, end=3, words=None):
    return SimpleNamespace(text=text, start=start, end=end, words=words)


def word(text, start, end):
    return SimpleNamespace(word=text, start=start, end=end)


def compact(text):
    return re.sub(r"\s+", "", text)


class CaptionCueTests(unittest.TestCase):
    def assert_valid(self, cues, duration=10, fps=24):
        previous = 0
        for cue in cues:
            self.assertGreaterEqual(cue["start"], previous)
            self.assertLessEqual(cue["end"], min(duration, 600))
            self.assertGreaterEqual(cue["end"] - cue["start"] + 1e-9, 1 / fps)
            self.assertLessEqual(len(cue["text"]), 500)
            self.assertLessEqual(len(cue["text"].splitlines()), 2)
            previous = cue["end"]

    def test_chinese_wrap_preserves_every_character_and_limits_line_width(self):
        text = "歡迎來到影片剪輯室，今天我們將一起製作自動語音字幕，並且調整文字的外觀與時間。" * 4
        chunks = worker.split_caption_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(compact("".join(chunks)), compact(text))
        self.assertTrue(all(len(line) <= 22 for chunk in chunks for line in chunk.splitlines()))

    def test_latin_wrap_preserves_long_token_and_limits_line_width(self):
        text = "Here is a useful subtitle with a " + "x" * 200 + " long token."
        chunks = worker.split_caption_text(text)
        self.assertEqual(compact("".join(chunks)), compact(text))
        self.assertTrue(all(len(line) <= 42 for chunk in chunks for line in chunk.splitlines()))

    def test_short_segment_uses_real_word_times(self):
        cues, approximate = worker.build_cues([
            segment(" Hello world!", 0, 4, [word(" Hello", .5, 1), word(" world!", 2, 3)])
        ], duration=4, fps=24)
        self.assertEqual(cues, [{"start": .5, "end": 3, "text": "Hello world!"}])
        self.assertFalse(approximate)

    def test_long_segment_splits_at_real_word_boundaries(self):
        words = [word(" " + "a" * 20, i, i + .5) for i in range(8)]
        text = "".join(w.word for w in words)
        cues, approximate = worker.build_cues([segment(text, 0, 8, words)], duration=8, fps=24)
        self.assertEqual(compact("".join(c["text"] for c in cues)), compact(text))
        self.assertEqual(len(cues), 2)
        self.assertEqual([(c["start"], c["end"]) for c in cues], [(0, 3.5), (4, 7.5)])
        self.assertFalse(approximate)
        self.assert_valid(cues, 8)

    def test_split_within_long_word_is_reported_as_approximate(self):
        text = "字" * 100
        cues, approximate = worker.build_cues([segment(text, 0, 5, [word(text, 0, 5)])],
                                               duration=5, fps=24)
        self.assertEqual(compact("".join(c["text"] for c in cues)), text)
        self.assertTrue(approximate)
        self.assert_valid(cues, 5)

    def test_missing_or_mismatching_words_fall_back_without_dropping_text(self):
        text = "A long caption that needs to retain every recognized word. " * 5
        for words in (None, [word("different text", 0, 5)], [word(text, float("nan"), 5)]):
            with self.subTest(words=words):
                cues, approximate = worker.build_cues([segment(text, 0, 5, words)], duration=5, fps=24)
                self.assertTrue(approximate)
                self.assertEqual(compact("".join(c["text"] for c in cues)), compact(text))
                self.assert_valid(cues, 5)

    def test_negative_overlapping_and_late_timestamps_are_bounded(self):
        cues, approximate = worker.build_cues([
            segment("第一句", -2, 1), segment("第二句", .8, 2), segment("第三句", 9.99, 11)
        ], duration=10, fps=24)
        self.assertEqual(compact("".join(c["text"] for c in cues)), "第一句第二句第三句")
        self.assertTrue(approximate)
        self.assert_valid(cues)

    def test_subframe_punctuation_merges_into_previous_cue(self):
        cues, _ = worker.build_cues([segment("你好", 0, 1), segment("。", 1, 1.001)],
                                     duration=2, fps=24)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "你好。")
        self.assert_valid(cues, 2)

    def test_merged_latin_sentences_remain_readable(self):
        cues, _ = worker.build_cues([segment("Hello.", 0, 1), segment("Okay!", 1, 1.001)],
                                     duration=2, fps=24)
        self.assertEqual(cues[0]["text"], "Hello. Okay!")

    def test_tiny_terminal_cue_can_shorten_previous_and_preserve_text(self):
        text = "字" * 44
        cues, _ = worker.build_cues([segment(text, 0, 1), segment("尾", 1, 1)],
                                     duration=1, fps=24)
        self.assertEqual(compact("".join(c["text"] for c in cues)), text + "尾")
        self.assert_valid(cues, 1)

    def test_impossible_dense_text_errors_instead_of_silent_truncation(self):
        with self.assertRaisesRegex(worker.CaptionError, "太密集"):
            worker.build_cues([segment("字" * 100, 0, .05)], duration=.05, fps=24)

    def test_frame_short_input_errors_instead_of_creating_invalid_cue(self):
        with self.assertRaises(worker.CaptionError):
            worker.build_cues([segment("你好", 0, .01)], duration=.01, fps=24)

    def test_invalid_duration_and_fps_are_rejected(self):
        for duration, fps in ((0, 24), (-1, 24), (float("nan"), 24), (10, 0), (10, float("inf")), (10, 241)):
            with self.subTest(duration=duration, fps=fps), self.assertRaises(worker.CaptionError):
                worker.build_cues([], duration=duration, fps=fps)

    def test_pure_helpers_can_convert_traditional_without_model_imports(self):
        convert = lambda text: text.replace("汉", "漢").replace("语", "語")
        cues, approximate = worker.build_cues([segment("汉语", 0, 2, [word("汉语", 0, 2)])],
                                               duration=2, fps=24, converter=convert)
        self.assertEqual(cues[0]["text"], "漢語")
        self.assertFalse(approximate)

    def test_duration_limit_is_ten_minutes(self):
        cues, _ = worker.build_cues([segment("結束", 599, 602)], duration=700, fps=24)
        self.assertEqual(cues[0]["end"], 600)
        self.assert_valid(cues, 700)

    def test_empty_or_whitespace_only_segments_produce_no_cues(self):
        cues, approximate = worker.build_cues([segment(" \n\t")], duration=3, fps=24)
        self.assertEqual(cues, [])
        self.assertFalse(approximate)


class CaptionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "speech.wav"
        self.output = self.root / "result.json"
        with wave.open(str(self.input), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000 * 3)

    def test_lazy_download_uses_fixed_repository_and_cpu_local_model(self):
        model_dir = self.root / "small"
        model_dir.mkdir()
        def download(**kwargs):
            for name in worker.MODEL_FILES:
                (model_dir / name).write_bytes(b"test")
        snapshot = Mock(side_effect=download)
        constructor = Mock(return_value=object())
        with patch.dict("sys.modules", {
            "faster_whisper": SimpleNamespace(WhisperModel=constructor),
            "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        }):
            worker._load_model(model_dir)
        kwargs = snapshot.call_args.kwargs
        self.assertEqual(kwargs["repo_id"], "Systran/faster-whisper-small")
        self.assertFalse(kwargs["token"])
        self.assertNotIn("*.py", kwargs["allow_patterns"])
        constructor.assert_called_once_with(str(model_dir), device="cpu", compute_type="int8",
                                             cpu_threads=4, num_workers=1, local_files_only=True)

    def test_cached_model_does_not_call_download(self):
        for name in worker.MODEL_FILES:
            (self.root / name).write_bytes(b"test")
        constructor = Mock(return_value=object())
        snapshot = Mock(side_effect=AssertionError("must not download"))
        with patch.dict("sys.modules", {
            "faster_whisper": SimpleNamespace(WhisperModel=constructor),
            "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        }):
            worker._load_model(self.root)
        snapshot.assert_not_called()

    def test_incomplete_download_raises_explicit_error(self):
        with patch.dict("sys.modules", {
            "faster_whisper": SimpleNamespace(WhisperModel=Mock()),
            "huggingface_hub": SimpleNamespace(snapshot_download=Mock()),
        }), self.assertRaisesRegex(worker.CaptionError, "未完整下載"):
            worker._load_model(self.root)

    def test_transcribe_is_not_translation_and_outputs_json_only(self):
        def recognized():
            print("library log")
            yield segment("字幕測試", 0, 2, [word("字幕測試", 0, 2)])
        model = SimpleNamespace(transcribe=Mock(return_value=(recognized(), SimpleNamespace(
            language="zh", language_probability=.98))))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(worker, "_load_model", return_value=model) as load, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = worker.transcribe_audio(self.input, self.output, model_dir=self.root / "models",
                                             language="auto", traditional=False, fps=24, duration=3)
        self.assertEqual(load.call_args.args[0], self.root / "models" / "small")
        options = model.transcribe.call_args.kwargs
        audio_samples = model.transcribe.call_args.args[0]
        self.assertIsInstance(audio_samples, np.ndarray)
        self.assertEqual(audio_samples.dtype, np.float32)
        self.assertEqual(audio_samples.shape, (48000,))
        self.assertEqual(options["task"], "transcribe")
        self.assertIsNone(options["language"])
        self.assertEqual(options["beam_size"], 5)
        self.assertIs(options["vad_filter"], True)
        self.assertIs(options["word_timestamps"], True)
        self.assertIs(options["condition_on_previous_text"], False)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), result)
        self.assertEqual(result["model"], "small")
        self.assertEqual(result["language"], "zh")
        self.assertFalse(result["timing_approximate"])
        self.assertFalse(self.output.with_name("result.json.tmp").exists())
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(events[-1], {"stage": "transcribing", "progress": 100})
        self.assertIn("library log", stderr.getvalue())

    def test_explicit_language_and_official_opencc_api(self):
        model = SimpleNamespace(transcribe=Mock(return_value=(iter([segment("汉语国家", 0, 2)]),
                                                              SimpleNamespace(language="zh", language_probability=1))))
        opencc = Mock(return_value=SimpleNamespace(convert=lambda text: text.translate(str.maketrans("汉语国", "漢語國"))))
        with patch.object(worker, "_load_model", return_value=model), \
                patch.dict("sys.modules", {"opencc": SimpleNamespace(OpenCC=opencc)}), \
                contextlib.redirect_stdout(io.StringIO()):
            result = worker.transcribe_audio(self.input, self.output, model_dir=self.root,
                                             language="zh", traditional=True, fps=24, duration=3)
        opencc.assert_called_once_with("s2t.json")
        self.assertEqual(model.transcribe.call_args.kwargs["language"], "zh")
        self.assertEqual(result["cues"][0]["text"], "漢語國家")

    def test_traditional_setting_does_not_change_detected_japanese_kanji(self):
        for language in ("auto", "ja"):
            with self.subTest(language=language):
                model = SimpleNamespace(transcribe=Mock(return_value=(iter([segment("この国へようこそ。", 0, 2)]),
                                                                      SimpleNamespace(language="ja", language_probability=1))))
                opencc = Mock(side_effect=AssertionError("Japanese must not use Chinese conversion"))
                with patch.object(worker, "_load_model", return_value=model), \
                        patch.dict("sys.modules", {"opencc": SimpleNamespace(OpenCC=opencc)}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = worker.transcribe_audio(self.input, self.output, model_dir=self.root,
                                                     language=language, traditional=True, fps=24, duration=3)
                self.assertEqual(result["cues"][0]["text"], "この国へようこそ。")
                opencc.assert_not_called()

    def test_automatically_detected_chinese_uses_traditional_conversion(self):
        model = SimpleNamespace(transcribe=Mock(return_value=(iter([segment("国家", 0, 2)]),
                                                              SimpleNamespace(language="zh", language_probability=1))))
        opencc = Mock(return_value=SimpleNamespace(convert=lambda text: text.replace("国", "國")))
        with patch.object(worker, "_load_model", return_value=model), \
                patch.dict("sys.modules", {"opencc": SimpleNamespace(OpenCC=opencc)}), \
                contextlib.redirect_stdout(io.StringIO()):
            result = worker.transcribe_audio(self.input, self.output, model_dir=self.root,
                                             language="auto", traditional=True, fps=24, duration=3)
        self.assertEqual(result["cues"][0]["text"], "國家")
        opencc.assert_called_once_with("s2t.json")

    def test_real_48khz_float_pcm_wav_is_accepted(self):
        samples = b"\x00\x00\x00\x00" * 48000 * 3
        format_chunk = struct.pack("<HHIIHH", 3, 1, 48000, 48000 * 4, 4, 32)
        wav = (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt "
               + struct.pack("<I", len(format_chunk)) + format_chunk + b"data"
               + struct.pack("<I", len(samples)) + samples)
        self.input.write_bytes(wav)
        self.assertAlmostEqual(worker._audio_duration(self.input, maximum=600), 3, places=5)
        model = SimpleNamespace(transcribe=Mock(return_value=(iter([segment("浮點音軌", 0, 2)]),
                                                              SimpleNamespace(language="zh", language_probability=1))))
        with patch.object(worker, "_load_model", return_value=model), contextlib.redirect_stdout(io.StringIO()):
            result = worker.transcribe_audio(self.input, self.output, model_dir=self.root,
                                             language="auto", traditional=False, fps=24, duration=3)
        self.assertEqual(result["cues"][0]["text"], "浮點音軌")
        self.assertEqual(model.transcribe.call_args.args[0].shape, (48000,))

    def test_real_s16_audio_decodes_to_normalized_float32(self):
        with wave.open(str(self.input), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<h", 8192) * 16000)
        samples = worker.decode_audio(self.input, duration=1)
        self.assertEqual(samples.shape, (16000,))
        self.assertEqual(samples.dtype, np.float32)
        np.testing.assert_allclose(samples, .25, atol=1e-6)

    def test_real_float_pcm_audio_is_resampled_flushed_and_cropped(self):
        sample_values = np.full(48000 * 2, .25, dtype="<f4")
        data = sample_values.tobytes()
        fmt = struct.pack("<HHIIHH", 3, 1, 48000, 48000 * 4, 4, 32)
        self.input.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
                               + struct.pack("<I", len(fmt)) + fmt + b"data"
                               + struct.pack("<I", len(data)) + data)
        samples = worker.decode_audio(self.input, duration=1.25)
        self.assertEqual(samples.shape, (20000,))
        np.testing.assert_allclose(samples[100:-100], .25, atol=1e-6)
        full_samples = worker.decode_audio(self.input, duration=2)
        self.assertEqual(full_samples.shape, (32000,))
        self.assertGreater(full_samples[-1], .2, "resampler tail must be flushed")

    def test_short_audio_is_padded_to_requested_range(self):
        with wave.open(str(self.input), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<h", 8192) * 8000)
        samples = worker.decode_audio(self.input, duration=1)
        self.assertEqual(samples.shape, (16000,))
        np.testing.assert_allclose(samples[:8000], .25, atol=1e-6)
        np.testing.assert_array_equal(samples[8000:], np.zeros(8000, dtype=np.float32))

    def test_stereo_audio_is_mixed_to_one_mono_channel(self):
        with wave.open(str(self.input), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<hh", 8192, 8192) * 16000)
        samples = worker.decode_audio(self.input, duration=1)
        self.assertEqual(samples.shape, (16000,))
        self.assertTrue(np.isfinite(samples).all())
        self.assertGreater(samples.mean(), .2)

    def test_decoder_rejects_unbounded_or_invalid_duration(self):
        for duration in (0, -1, 601, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(worker.CaptionError):
                worker.decode_audio(self.input, duration=duration)

    def test_missing_audio_duration_metadata_falls_back_to_decoding(self):
        audio_stream = SimpleNamespace(duration=None, time_base=None,
                                       codec_context=SimpleNamespace(sample_rate=48000))
        container = SimpleNamespace(streams=SimpleNamespace(audio=[audio_stream]), duration=None,
                                    decode=Mock(return_value=iter([
                                        SimpleNamespace(samples=48000, sample_rate=48000),
                                        SimpleNamespace(samples=24000, sample_rate=48000),
                                    ])))
        opened = Mock()
        opened.__enter__ = Mock(return_value=container)
        opened.__exit__ = Mock(return_value=False)
        with patch.dict("sys.modules", {"av": SimpleNamespace(open=Mock(return_value=opened), time_base=1000000)}):
            duration = worker._audio_duration(self.input, maximum=600)
        self.assertEqual(duration, 1.5)
        container.decode.assert_called_once_with(audio_stream)

    def test_container_without_audio_is_rejected(self):
        opened = Mock()
        opened.__enter__ = Mock(return_value=SimpleNamespace(streams=SimpleNamespace(audio=[])))
        opened.__exit__ = Mock(return_value=False)
        with patch.dict("sys.modules", {"av": SimpleNamespace(open=Mock(return_value=opened))}), \
                self.assertRaisesRegex(worker.CaptionError, "沒有可辨識"):
            worker._audio_duration(self.input, maximum=600)

    def test_invalid_audio_or_excessive_duration_fails_before_loading_model(self):
        for input_path, duration in ((self.root / "missing.wav", 3), (self.input, 601)):
            with self.subTest(duration=duration), patch.object(worker, "_load_model") as load, \
                    self.assertRaises(worker.CaptionError):
                worker.transcribe_audio(input_path, self.output, model_dir=self.root,
                                         language="auto", traditional=False, fps=24, duration=duration)
            load.assert_not_called()

    def test_cli_exception_does_not_echo_download_tokens(self):
        stderr = io.StringIO()
        with patch.object(worker, "transcribe_audio", side_effect=RuntimeError("https://x/?token=SECRET")), \
                contextlib.redirect_stderr(stderr):
            code = worker.main(["--input", str(self.input), "--output", str(self.output),
                                "--model-dir", str(self.root), "--fps", "24", "--duration", "3"])
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", stderr.getvalue())
        self.assertEqual(json.loads(stderr.getvalue())["type"], "RuntimeError")

    def test_cli_traditional_flag_is_boolean(self):
        with patch.object(worker, "transcribe_audio") as transcribe:
            code = worker.main(["--input", str(self.input), "--output", str(self.output),
                                "--model-dir", str(self.root), "--fps", "24", "--duration", "3",
                                "--language", "ja", "--traditional"])
        self.assertEqual(code, 0)
        self.assertIs(transcribe.call_args.kwargs["traditional"], True)
        self.assertEqual(transcribe.call_args.kwargs["language"], "ja")


if __name__ == "__main__":
    unittest.main()
