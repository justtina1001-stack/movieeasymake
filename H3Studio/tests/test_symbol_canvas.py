import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import AssetStore, choose_symbol_canvas, prepare_symbol_canvas


TEST_TEMP = Path(__file__).resolve().parents[1] / "data" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class SymbolCanvasTests(unittest.TestCase):
    def test_small_square_keeps_pixels_and_uses_smallest_square_canvas(self):
        choice = choose_symbol_canvas(500, 500)
        self.assertEqual((choice["width"], choice["height"]), (640, 640))
        self.assertEqual(choice["aspect_ratio"], "1:1")
        self.assertEqual(choice["scale"], 1.0)

    def test_large_square_is_uniformly_reduced_to_largest_square_canvas(self):
        choice = choose_symbol_canvas(2000, 2000)
        self.assertEqual((choice["width"], choice["height"]), (992, 992))
        self.assertAlmostEqual(choice["scale"], 992 / 2000)

    def test_prepared_canvas_expands_without_resizing_when_source_fits(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            source = root / "symbol.png"
            Image.new("RGBA", (500, 500), (20, 40, 60, 255)).save(source)
            result = prepare_symbol_canvas(source, AssetStore(root / "assets"))
            self.assertTrue(result["pixel_size_preserved"])
            self.assertEqual((result["content_width"], result["content_height"]), (500, 500))
            self.assertEqual((result["canvas_width"], result["canvas_height"]), (640, 640))
            self.assertEqual(result["padding"], {"left": 70, "top": 70, "right": 70, "bottom": 70})
            prepared = root / "assets" / f"{result['prepared_asset']['id']}.png"
            with Image.open(prepared) as image:
                self.assertEqual(image.size, (640, 640))


if __name__ == "__main__":
    unittest.main()
