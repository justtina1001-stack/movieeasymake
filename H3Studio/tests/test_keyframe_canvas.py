import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import AssetStore, RequestError, flatten_transparent_image, prepare_keyframe_canvas


TEST_TEMP = Path(__file__).resolve().parents[1] / "data" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class KeyframeCanvasTests(unittest.TestCase):
    def prepare(self, fit_mode: str):
        temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        root = Path(temporary.name)
        source = root / "portrait.png"
        Image.new("RGB", (300, 600), (30, 60, 90)).save(source)
        result = prepare_keyframe_canvas(source, AssetStore(root / "assets"), 640, 640, fit_mode)
        return temporary, root, result

    def test_contain_preserves_ratio_and_adds_side_canvas(self):
        temporary, root, result = self.prepare("contain")
        with temporary:
            self.assertEqual((result["content_width"], result["content_height"]), (320, 640))
            self.assertEqual(result["padding"], {"left": 160, "top": 0, "right": 160, "bottom": 0})
            prepared = root / "assets" / f"{result['prepared_asset']['id']}.png"
            with Image.open(prepared) as image:
                self.assertEqual(image.size, (640, 640))
                self.assertEqual(image.getpixel((0, 0))[:3], (30, 60, 90))

    def test_cover_preserves_ratio_and_center_crops(self):
        temporary, root, result = self.prepare("cover")
        with temporary:
            self.assertEqual((result["content_width"], result["content_height"]), (640, 1280))
            prepared = root / "assets" / f"{result['prepared_asset']['id']}.png"
            with Image.open(prepared) as image:
                self.assertEqual(image.size, (640, 640))

    def test_stretch_matches_canvas_exactly(self):
        temporary, root, result = self.prepare("stretch")
        with temporary:
            self.assertEqual((result["content_width"], result["content_height"]), (640, 640))
            prepared = root / "assets" / f"{result['prepared_asset']['id']}.png"
            with Image.open(prepared) as image:
                self.assertEqual(image.size, (640, 640))

    def test_unknown_fit_mode_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "portrait.png"
            Image.new("RGB", (300, 600), "white").save(source)
            with self.assertRaises(RequestError):
                prepare_keyframe_canvas(source, AssetStore(root / "assets"), 640, 640, "unknown")

    def test_transparent_upload_is_composited_over_chroma_green(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "transparent.png"
            image = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
            image.putpixel((10, 10), (255, 255, 255, 255))
            image.save(source)
            self.assertTrue(flatten_transparent_image(source))
            with Image.open(source) as flattened:
                self.assertEqual(flattened.mode, "RGB")
                self.assertEqual(flattened.getpixel((0, 0)), (0, 255, 0))
                self.assertEqual(flattened.getpixel((10, 10)), (255, 255, 255))

    def test_legacy_transparent_keyframe_is_filled_before_resize(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "legacy.png"
            Image.new("RGBA", (300, 600), (255, 255, 255, 0)).save(source)
            result = prepare_keyframe_canvas(source, AssetStore(root / "assets"), 640, 640, "contain")
            self.assertTrue(result["transparency_filled"])
            prepared = root / "assets" / f"{result['prepared_asset']['id']}.png"
            with Image.open(prepared) as image:
                self.assertEqual(image.getpixel((320, 320))[:3], (0, 255, 0))


if __name__ == "__main__":
    unittest.main()
