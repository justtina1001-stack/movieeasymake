"""Pixel and transparency invariants for seekable built-in transition effects."""
import math
import unittest

import numpy as np
from PIL import Image

from editor_animations import EFFECT_TRANSITION_TYPES, transition_surface


def patterned_surfaces(width=120, height=60):
    y, x = np.indices((height, width))
    left = np.stack(((x * 17 + y * 3) % 256, (x * 7 + y * 19) % 256,
                     (x * 31 + y * 11) % 256, (x * 5 + y * 13) % 256), axis=-1).astype(np.uint8)
    right = np.stack(((x * 23 + y * 5 + 41) % 256, (x * 11 + y * 29 + 71) % 256,
                      (x * 3 + y * 7 + 113) % 256, (x * 13 + y * 17 + 37) % 256), axis=-1).astype(np.uint8)
    left[:8, :12, 3] = right[:8, :12, 3] = 0
    return Image.fromarray(left), Image.fromarray(right)


class TransitionEffectTests(unittest.TestCase):
    def test_exact_endpoints_new_surfaces_and_inputs_unchanged_after_random_seek(self):
        left, right = patterned_surfaces()
        with left, right:
            original_left, original_right = left.tobytes(), right.tobytes()
            for kind in EFFECT_TRANSITION_TYPES:
                with self.subTest(kind=kind):
                    for ratio, source in ((-1, left), (0, left), (1, right), (2, right)):
                        with transition_surface(left, right, kind, ratio) as result:
                            self.assertIsNot(result, source)
                            self.assertEqual(result.mode, "RGBA")
                            self.assertEqual(result.size, source.size)
                            self.assertEqual(result.tobytes(), source.tobytes())
                    with transition_surface(left, right, kind, .37) as first:
                        expected = first.tobytes()
                        self.assertEqual(first.size, left.size)
                        self.assertEqual(first.mode, "RGBA")
                    for ratio in (.79, .12, .37):
                        with transition_surface(left, right, kind, ratio) as result:
                            if ratio == .37:
                                self.assertEqual(result.tobytes(), expected)
                    self.assertEqual(left.tobytes(), original_left)
                    self.assertEqual(right.tobytes(), original_right)

    def test_rgb_inputs_produce_rgba_and_empty_inputs_do_not_cover_lower_layers(self):
        for kind in EFFECT_TRANSITION_TYPES:
            with self.subTest(kind=kind):
                with Image.new("RGB", (120, 60), (17, 83, 129)) as left, Image.new("RGB", (120, 60), (39, 101, 233)) as right:
                    with transition_surface(left, right, kind, 0) as first:
                        self.assertEqual(first.getpixel((0, 0)), (17, 83, 129, 255))
                    with transition_surface(left, right, kind, .5) as middle:
                        self.assertEqual(middle.mode, "RGBA")
                        self.assertEqual(middle.getextrema()[3], (255, 255))
                with Image.new("RGBA", (120, 60), (255, 0, 0, 0)) as left, Image.new("RGBA", (120, 60), (0, 0, 255, 0)) as right:
                    with transition_surface(left, right, kind, .5) as middle:
                        self.assertIsNone(middle.getbbox())
                        self.assertEqual(middle.getextrema()[3], (0, 0))

    def test_flash_whitens_color_without_changing_translucent_opacity(self):
        with Image.new("RGBA", (9, 7), (200, 20, 40, 64)) as left, Image.new("RGBA", (9, 7), (20, 100, 220, 192)) as right:
            with transition_surface(left, right, "white_flash", .5) as middle:
                self.assertEqual(middle.getpixel((4, 3)), (255, 255, 255, 128))
            ratio = .25
            alpha = 64 * (1 - ratio) + 192 * ratio
            color = (np.array((200, 20, 40)) * 64 * (1 - ratio)
                     + np.array((20, 100, 220)) * 192 * ratio) / alpha
            flash = math.sin(math.pi * ratio) ** 4
            expected = np.floor(np.r_[color * (1 - flash) + 255 * flash, alpha] + .5)
            with transition_surface(left, right, "white_flash", ratio) as quarter:
                np.testing.assert_array_equal(quarter.getpixel((4, 3)), expected)

    def test_blur_edge_clamps_and_premultiplies_to_prevent_hidden_color_halos(self):
        pixels = np.zeros((60, 120, 4), np.uint8)
        pixels[..., 0] = 255  # Hidden red must never tint the visible blue edge.
        pixels[:, 60:] = (0, 0, 255, 128)
        with Image.fromarray(pixels) as left, Image.fromarray(pixels) as right:
            with transition_surface(left, right, "blur_dissolve", .5) as blurred:
                self.assertEqual(blurred.getpixel((58, 30)), (0, 0, 0, 0))
                self.assertEqual(blurred.getpixel((59, 30)), (0, 0, 255, 43))
                self.assertEqual(blurred.getpixel((60, 30)), (0, 0, 255, 85))
                self.assertEqual(blurred.getpixel((61, 30)), (0, 0, 255, 128))
                self.assertEqual(blurred.getpixel((119, 0)), (0, 0, 255, 128))
                self.assertEqual(blurred.getpixel((119, 59)), (0, 0, 255, 128))

    def test_wave_nearest_sampling_uses_both_shift_directions_and_canvas_clamp(self):
        pixels = np.zeros((60, 120, 4), np.uint8)
        pixels[..., 0] = np.arange(120)
        pixels[..., 1] = np.arange(60)[:, None]
        pixels[..., 3] = 255
        with Image.fromarray(pixels) as left, Image.fromarray(pixels) as right:
            with transition_surface(left, right, "wave_distort", .5) as wave:
                self.assertEqual(wave.getpixel((50, 5)), (54, 5, 0, 255))
                self.assertEqual(wave.getpixel((50, 15)), (46, 15, 0, 255))
                self.assertEqual(wave.getpixel((119, 5)), (119, 5, 0, 255))
                self.assertEqual(wave.getpixel((0, 15)), (0, 15, 0, 255))
                self.assertEqual(wave.getpixel((50, 0)), (50, 0, 0, 255))

    def test_glitch_samples_rgb_from_distinct_positions_with_shared_maximum_alpha(self):
        pixels = np.zeros((60, 120, 4), np.uint8)
        pixels[..., :3] = (90, 150, 210)
        pixels[..., 3] = np.arange(120) * 2
        ratio = .375
        envelope = math.sin(math.pi * ratio) ** 2
        split = math.floor(120 * .012 * envelope + .5)
        self.assertEqual(split, 1)
        with Image.fromarray(pixels) as left, Image.fromarray(pixels) as right:
            with transition_surface(left, right, "rgb_glitch", ratio) as glitch:
                for y in (0, 5, 10, 25, 55):
                    band = y // 5
                    jump = math.floor(math.sin(band * 12.9898 + math.floor(ratio * 12) * 78.233)
                                      * 120 * .03 * envelope + .5)
                    for x in (0, 50, 119):
                        positions = np.clip([x + jump + split, x + jump, x + jump - split], 0, 119)
                        alpha = int(pixels[y, positions, 3].max())
                        color = [pixels[y, position, channel] * float(pixels[y, position, 3]) / alpha
                                 if alpha else 0 for channel, position in enumerate(positions)]
                        expected = np.floor(np.r_[color, alpha] + .5)
                        np.testing.assert_allclose(glitch.getpixel((x, y)), expected, atol=1)


if __name__ == "__main__":
    unittest.main()
