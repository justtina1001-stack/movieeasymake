import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from app import AssetStore, extract_continuation_frame, merge_continuation

TEST_TEMP = Path(__file__).resolve().parents[1] / "data" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def make_video(path: Path, colors: list[tuple[int, int, int]], with_audio: bool = False) -> None:
    with av.open(str(path), mode="w") as output:
        video = output.add_stream("libx264", rate=24)
        video.width = 96
        video.height = 64
        video.pix_fmt = "yuv420p"
        audio = output.add_stream("aac", rate=48000) if with_audio else None
        if audio is not None:
            audio.layout = "stereo"
        for index, color in enumerate(colors):
            pixels = np.zeros((64, 96, 3), dtype=np.uint8)
            pixels[:, :] = color
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, 24)
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        if audio is not None:
            samples = np.zeros((2, len(colors) * 2000), dtype=np.float32)
            frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            frame.sample_rate = 48000
            frame.pts = 0
            frame.time_base = Fraction(1, 48000)
            for packet in audio.encode(frame):
                output.mux(packet)
            for packet in audio.encode():
                output.mux(packet)


class ContinuationTests(unittest.TestCase):
    def test_extracts_last_frame_and_metadata(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "source.mp4"
            make_video(source, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
            result = extract_continuation_frame(source, AssetStore(root / "assets"))
            self.assertEqual((result["width"], result["height"]), (96, 64))
            self.assertEqual(result["frames"], 3)
            self.assertTrue((root / "assets" / f"{result['id']}.png").exists())

    def test_merges_video_without_duplicate_first_continuation_frame(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "source.mp4"
            continuation = root / "continuation.mp4"
            merged = root / "merged.mp4"
            make_video(source, [(255, 0, 0)] * 6, with_audio=True)
            make_video(continuation, [(255, 0, 0)] + [(0, 255, 0)] * 5, with_audio=True)
            duration = merge_continuation(source, continuation, merged, 96, 64, "both")
            with av.open(str(merged)) as container:
                frames = list(container.decode(video=0))
                self.assertTrue(container.streams.audio)
            self.assertEqual(len(frames), 11)
            self.assertAlmostEqual(duration, 11 / 24)


if __name__ == "__main__":
    unittest.main()
