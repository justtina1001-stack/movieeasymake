"""Behavior checks for the two inspected transition rendering graphs."""
import math
import unittest

import numpy as np
from PIL import Image

from editor_animations import (REFERENCE_TRANSITION_TYPES, _horizontal_blur_parameters,
                               transition_surface)


class ReferenceTransitionTests(unittest.TestCase):
    def test_radial_zoom_keeps_exponential_dissolve_instead_of_linear_crossfade(self):
        with Image.new("RGBA", (121, 61), (255, 0, 0, 128)) as left, Image.new("RGBA", (121, 61), (0, 0, 255, 128)) as right:
            for progress, expected in ((.25, (251, 0, 4, 128)), (.5, (128, 0, 128, 128)),
                                       (.75, (4, 0, 251, 128))):
                with self.subTest(progress=progress), transition_surface(left, right, "radial_zoom", progress) as result:
                    np.testing.assert_allclose(result.getpixel((60, 30)), expected, atol=1)
                    np.testing.assert_allclose(result.getpixel((0, 0)), expected, atol=1)

    def test_horizontal_timeline_keeps_early_dissolve_peak_and_directional_sample_graph(self):
        peak = _horizontal_blur_parameters(.466666, 1920, 1080)
        self.assertAlmostEqual(peak["intensity"], 120, delta=.001)
        self.assertAlmostEqual(peak["exposure"], 2, delta=.001)
        self.assertAlmostEqual(peak["correction"], 4, delta=.001)
        self.assertEqual(peak["downscale"], .5)
        self.assertEqual(math.floor(peak["sample"]), 46)
        self.assertLess(peak["step_x"], 0)
        self.assertAlmostEqual(peak["step_y"], 0, delta=1e-10)
        middle = _horizontal_blur_parameters(.5, 1920, 1080)
        self.assertAlmostEqual(middle["mix"], .625, delta=.001)
        self.assertGreater(middle["intensity"], 118)
        late = _horizontal_blur_parameters(.8, 1920, 1080)
        self.assertEqual(late["mix"], 1)
        self.assertGreater(late["exposure"], 0)
        early = _horizontal_blur_parameters(.001, 1920, 1080)
        self.assertEqual(early["downscale"], .8)
        self.assertEqual(early["sample"], 0)

    def test_horizontal_gamma_exposure_preserves_alpha_and_border_renormalization(self):
        with Image.new("RGBA", (121, 61), (128, 128, 128, 96)) as left, Image.new("RGBA", (121, 61), (128, 128, 128, 96)) as right:
            with transition_surface(left, right, "horizontal_blur", .466666) as result:
                center = result.getpixel((60, 30))
                self.assertTrue(250 <= center[0] <= 255)
                self.assertEqual(center[0], center[1])
                self.assertEqual(center[1], center[2])
                self.assertEqual(center[3], 96)
                for position in ((0, 0), (120, 0), (0, 60), (120, 60)):
                    self.assertEqual(result.getpixel(position), center)

    def test_reference_material_alpha_hidden_colors_and_single_pixel_canvases(self):
        for kind in REFERENCE_TRANSITION_TYPES:
            with self.subTest(kind=kind):
                with Image.new("RGBA", (1, 1), (255, 0, 0, 0)) as left, Image.new("RGBA", (1, 1), (0, 0, 200, 128)) as right:
                    with transition_surface(left, right, kind, .5) as result:
                        red, green, blue, alpha = result.getpixel((0, 0))
                        self.assertEqual(red, 0)
                        self.assertEqual(green, 0)
                        self.assertGreater(blue, 190)
                        self.assertTrue(60 <= alpha <= 90)
                        self.assertEqual(result.size, (1, 1))


if __name__ == "__main__":
    unittest.main()
