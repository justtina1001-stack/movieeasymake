"""Check the portable effects against independent original-shader pixel captures."""
import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from editor_animations import transition_surface


class TransitionShaderReferenceTests(unittest.TestCase):
    def test_reference_effects_match_captured_original_glsl_on_opaque_geometry(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/transition_shader_reference.json").read_text(encoding="utf-8"))
        width, height = fixture["width"], fixture["height"]
        y, x = np.indices((height, width))
        left = np.stack((x*3+12, y*5+15, (x*2+y*2+5)%256, np.full_like(x, 255)), axis=-1).astype(np.uint8)
        right = np.stack((240-x*3, 210-y*4, (x+y*3+70)%256, np.full_like(x, 255)), axis=-1).astype(np.uint8)
        left[8:25, 18:36, :3] = (20, 210, 90)
        right[12:30, 30:47, :3] = (225, 30, 175)
        with Image.fromarray(left) as outgoing, Image.fromarray(right) as incoming:
            for case in fixture["cases"]:
                with self.subTest(effect=case["type"], progress=case["progress"]):
                    with transition_surface(outgoing, incoming, case["type"], case["progress"]) as surface:
                        actual = np.array([surface.getpixel(tuple(point)) for point in fixture["points"]], dtype=np.int16)
                    expected = np.asarray(case["rgba"], dtype=np.int16)
                    difference = np.abs(actual-expected)
                    # Highp GPU sin/fract jitter differs from deterministic CPU
                    # doubles. The original 41-tap kernel bounds this variation.
                    self.assertLessEqual(int(difference.max()), 3)
                    self.assertLess(float(difference.mean()), .35)
                    np.testing.assert_array_equal(actual[:, 3], 255)


if __name__ == "__main__":
    unittest.main()
