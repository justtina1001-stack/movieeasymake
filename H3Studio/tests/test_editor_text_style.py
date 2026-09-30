"""Text style schema, actual Pillow pixels, bounded caching and CPU MP4 output."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw

import editor_overlays as overlays
import video_editor as editor
from test_editor_overlays import frame, image_layer, text_layer
from test_video_editor_render import make_source


def styled(**values):
    return text_layer(**{"text": "HH", "font_size": .2, "width": .8, "fill_mode": "linear_gradient",
                         "gradient_start": "#FF0000", "gradient_end": "#0000FF", "gradient_angle": 0, **values})


def pixels(layer, width=360, height=360):
    with overlays.raster_text(layer, width, height) as source:
        return np.asarray(source).copy()


def old_solid_raster(layer, width=360, height=360):
    """The pre-style layout, for pixel-exact compatibility of old projects."""
    box_width = round(layer["width"] * width)
    size = round(layer["font_size"] * min(width, height))
    font = overlays._font(size, layer["bold"])
    padding = max(1, round(size * .18))
    available = box_width - 2 * padding
    lines = []
    for explicit in layer["text"].replace("\t", "    ").split("\n"):
        line = ""
        for character in explicit:
            if line and font.getlength(line + character) > available:
                lines.append(line)
                line = character
            else:
                line += character
        lines.append(line)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + max(1, round(size * .12))
    background = (0, 0, 0, 0) if layer["background"] == "transparent" else layer["background"]
    with Image.new("RGBA", (box_width, len(lines) * line_height + 2 * padding), background) as image:
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            length = font.getlength(line)
            x = padding if layer["align"] == "left" else box_width - padding - length if layer["align"] == "right" else (box_width - length) / 2
            draw.text((x, padding + index * line_height + ascent), line, font=font, fill=layer["color"], anchor="ls")
        return np.asarray(image).copy()


class TextStyleSchemaTests(unittest.TestCase):
    def test_default_and_explicit_default_fields_are_omitted(self):
        original = overlays.validate_layer(text_layer())
        explicit = overlays.validate_layer(text_layer(**overlays.TEXT_STYLE_DEFAULTS))
        self.assertEqual(explicit, original)
        self.assertFalse(set(overlays.TEXT_STYLE_DEFAULTS) & original.keys())

    def test_inactive_choices_survive_color_canonicalization_and_reload(self):
        layer = text_layer(stroke_color="#12abef", gradient_start="#efab12", gradient_end="#123abc", gradient_angle=360)
        clean = overlays.validate_layer(layer)
        self.assertNotIn("stroke_width", clean)
        self.assertNotIn("fill_mode", clean)
        self.assertEqual(clean["stroke_color"], "#12ABEF")
        self.assertEqual(clean["gradient_start"], "#EFAB12")
        self.assertEqual(clean["gradient_end"], "#123ABC")
        self.assertEqual(clean["gradient_angle"], 360)
        with tempfile.TemporaryDirectory() as directory:
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            project = store.create_project({"overlays": [layer]})
            loaded = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(loaded.projects[project["id"]]["overlays"][0], clean)

    def test_styles_reject_bools_nonfinite_out_of_range_and_non_numeric(self):
        cases = {"stroke_width": [True, False, None, "0.1", float("nan"), float("inf"), -.001, .250001, 10 ** 1000],
                 "gradient_angle": [True, False, None, "90", float("nan"), -float("inf"), -.001, 360.0001, 10 ** 1000]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(editor.EditorError):
                    overlays.validate_layer(text_layer(**{key: value}))
        for key, value in (("stroke_width", .25), ("gradient_angle", 0), ("gradient_angle", 360)):
            self.assertEqual(overlays.validate_layer(text_layer(**{key: value}))[key], value)

    def test_styles_reject_invalid_colors_modes_and_non_text_layers(self):
        for key in ("stroke_color", "gradient_start", "gradient_end"):
            for value in (None, True, 123, "red", "#123", "#12345678", "#12345G", "#123456\n", []):
                with self.subTest(key=key, value=value), self.assertRaises(editor.EditorError):
                    overlays.validate_layer(text_layer(**{key: value}))
        for value in (None, True, [], {}, "gradient", "SOLID"):
            with self.subTest(mode=value), self.assertRaises(editor.EditorError):
                overlays.validate_layer(text_layer(fill_mode=value))
        for kind in ("image", "video"):
            for key, value in overlays.TEXT_STYLE_DEFAULTS.items():
                with self.subTest(kind=kind, key=key), self.assertRaisesRegex(editor.EditorError, "只能使用文字"):
                    overlays.validate_layer(image_layer(kind=kind, **{key: value}))


class TextStyleRasterTests(unittest.TestCase):
    def test_solid_zero_styles_are_pixel_identical_for_old_layouts(self):
        for align in ("left", "center", "right"):
            for background in ("transparent", "#123456"):
                for bold in (False, True):
                    layer = text_layer(text="中文 A g\n第二行\tB", color="#12ABEF", align=align, background=background, bold=bold)
                    with self.subTest(align=align, background=background, bold=bold):
                        np.testing.assert_array_equal(pixels(layer), old_solid_raster(layer))
                        np.testing.assert_array_equal(pixels({**layer, **overlays.TEXT_STYLE_DEFAULTS}), old_solid_raster(layer))

    def test_gradient_keeps_glyph_alpha_and_leaves_padding_transparent(self):
        gradient = pixels(styled())
        solid = pixels({**styled(), "fill_mode": "solid"})
        np.testing.assert_array_equal(gradient[..., 3], solid[..., 3])
        self.assertEqual(int(gradient[0, 0, 3]), 0)
        self.assertEqual(int(gradient[-1, -1, 3]), 0)
        opaque = gradient[..., 3] == 255
        ys, xs = np.where(opaque)
        left, right = xs.min(), xs.max()
        self.assertGreater(gradient[ys[xs == left], left, 0].mean(), 245)
        self.assertGreater(gradient[ys[xs == right], right, 2].mean(), 245)

    def test_gradient_angles_axis_reverse_and_diagonal_match_projection(self):
        for angle in (0, 37, 90, 180, 270, 360):
            layer = {**styled(), "gradient_angle": angle}
            source = pixels(layer)
            ys, xs = np.where(source[..., 3] == 255)
            mask_ys, mask_xs = np.where(source[..., 3] > 0)
            radians = np.deg2rad(angle)
            cosine, sine = np.cos(radians), np.sin(radians)
            corners = [x * cosine + y * sine for x in (mask_xs.min(), mask_xs.max()) for y in (mask_ys.min(), mask_ys.max())]
            ramp = np.clip((xs * cosine + ys * sine - min(corners)) / max(max(corners) - min(corners), 1), 0, 1)
            with self.subTest(angle=angle):
                np.testing.assert_allclose(source[ys, xs, 0], np.rint(255 * (1 - ramp)), atol=1)
                np.testing.assert_allclose(source[ys, xs, 2], np.rint(255 * ramp), atol=1)
                self.assertTrue(np.all(source[ys, xs, 1] == 0))
        np.testing.assert_array_equal(pixels({**styled(), "gradient_angle": 0}), pixels({**styled(), "gradient_angle": 360}))

    def test_uniform_outline_expands_alpha_without_clipping_or_gradient_leak(self):
        for align in ("left", "center", "right"):
            layer = {**styled(text="中文gj"), "stroke_width": .25, "stroke_color": "#00FF00", "align": align}
            source = pixels(layer)
            no_outline = pixels({**layer, "stroke_width": 0})
            # Full radius is reserved outside every glyph and between lines.
            alpha = source[..., 3]
            ys, xs = np.where(alpha > 0)
            with self.subTest(align=align):
                self.assertGreater(xs.min(), 0)
                self.assertLess(xs.max(), source.shape[1] - 1)
                self.assertGreater(ys.min(), 0)
                self.assertLess(ys.max(), source.shape[0] - 1)
                self.assertGreater(np.count_nonzero(alpha), np.count_nonzero(no_outline[..., 3]))
                green = (source[..., 1] == 255) & (source[..., 0] == 0) & (source[..., 2] == 0) & (alpha == 255)
                self.assertGreater(np.count_nonzero(green), 100)
                self.assertTrue(np.any((source[..., 0] > 200) & (alpha == 255)))
                self.assertTrue(np.any((source[..., 2] > 200) & (alpha == 255)))

    def test_solid_stroke_color_background_blank_and_wrap_alignment(self):
        layer = text_layer(text="gj中文", font_size=.2, width=.9, color="#FF0000", stroke_width=.2, stroke_color="#00FF00")
        source = pixels(layer)
        self.assertTrue(np.any(np.all(source == (0, 255, 0, 255), axis=2)))
        self.assertTrue(np.any(np.all(source == (255, 0, 0, 255), axis=2)))
        self.assertGreater(source.shape[0], pixels({**layer, "stroke_width": 0}).shape[0])
        opaque = pixels({**styled(), "background": "#123456"})
        self.assertTrue(np.all(opaque[..., 3] == 255))
        np.testing.assert_array_equal(opaque[0, 0], (18, 52, 86, 255))
        blank = pixels({**styled(), "text": "", "stroke_width": .25})
        self.assertFalse(np.any(blank[..., 3]))
        positions = []
        for align in ("left", "center", "right"):
            wrapped = pixels({**styled(), "text": "H" * 8, "width": .35, "font_size": .08, "stroke_width": .2, "align": align})
            ys, xs = np.where(wrapped[..., 3] > 0)
            top_line = xs[ys < ys.min() + 30]
            positions.append(top_line.mean())
            self.assertGreater(wrapped.shape[0], 50)
            self.assertGreater(xs.min(), 0)
            self.assertLess(xs.max(), wrapped.shape[1] - 1)
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])

    def test_too_narrow_or_large_styled_text_remains_rejected(self):
        for layer in ({**styled(), "width": .02, "stroke_width": .25},
                      {**styled(), "text": "中" * 500, "width": .2, "stroke_width": .25},
                      {**styled(), "text": "中\n" * 9 + "中", "width": 2, "font_size": .3, "stroke_width": .25}):
            with self.subTest(layer=layer), self.assertRaises(editor.EditorError):
                overlays.raster_text(layer, 1920, 1920)

    def test_gradient_temporary_paint_allocations_use_at_most_32_rows(self):
        allocations = []
        original = np.empty
        def empty(shape, *args, **kwargs):
            allocations.append(shape)
            return original(shape, *args, **kwargs)
        with patch.object(overlays.np, "empty", side_effect=empty):
            source = pixels({**styled(), "text": "中\n文\n測\n試", "font_size": .1, "stroke_width": .25}, 1920, 1920)
        self.assertGreater(source.shape[0], 500)
        self.assertGreater(len(allocations), 5)
        self.assertTrue(all(shape[0] <= 32 and shape[-1] == 4 for shape in allocations))

    def test_preview_source_exact_transform_and_clockwise_rotation(self):
        layer = {**styled(), "stroke_width": .1, "stroke_color": "#00FF00"}
        source = pixels(layer)
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360)
        try:
            prepared, _, _ = compositor._prepare(layer)
            try:
                if prepared.size == (source.shape[1], source.shape[0]):
                    np.testing.assert_array_equal(np.asarray(prepared), source)
            finally:
                prepared.close()
            original = compositor.apply(frame(360, 360), .5).to_ndarray(format="rgb24")
        finally:
            compositor.close()
        rotated = overlays.OverlayCompositor([{**layer, "rotation": 90}], {}, 360, 360)
        try:
            rotated_pixels = rotated.apply(frame(360, 360), .5).to_ndarray(format="rgb24")
        finally:
            rotated.close()
        red = (original[..., 0] > 180) & (original[..., 2] < 80)
        blue = (original[..., 2] > 180) & (original[..., 0] < 80)
        self.assertLess(np.where(red)[1].mean(), np.where(blue)[1].mean())
        red = (rotated_pixels[..., 0] > 180) & (rotated_pixels[..., 2] < 80)
        blue = (rotated_pixels[..., 2] > 180) & (rotated_pixels[..., 0] < 80)
        self.assertLess(np.where(red)[0].mean(), np.where(blue)[0].mean())

    def test_opacity_fade_motion_and_cache_reuse_apply_alpha_once(self):
        layer = {**styled(), "font_size": .1, "stroke_width": .1, "opacity": .5, "fade_in": .5,
                 "position_keyframes": [{"time": 0, "x": .3, "y": .5}, {"time": 1, "x": .7, "y": .5}]}
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360)
        try:
            with patch.object(overlays, "raster_text", wraps=overlays.raster_text) as raster:
                at_zero = compositor.apply(frame(360, 360), 0).to_ndarray(format="rgb24")
                self.assertFalse(np.any(at_zero))
                early = compositor.apply(frame(360, 360), .25).to_ndarray(format="rgb24")
                full = compositor.apply(frame(360, 360), .5).to_ndarray(format="rgb24")
                later = compositor.apply(frame(360, 360), .75).to_ndarray(format="rgb24")
                self.assertEqual(raster.call_count, 1)
                self.assertEqual(early.max(), 64)
                self.assertEqual(full.max(), 128)
                self.assertEqual(later.max(), 128)
                self.assertAlmostEqual(early.sum() / full.sum(), .5, delta=.02)
                self.assertAlmostEqual(np.where(later.max(axis=2) > 50)[1].mean() - np.where(full.max(axis=2) > 50)[1].mean(), 36, delta=1)
                self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)
                self.assertEqual(len(compositor.cache), 1)
        finally:
            compositor.close()

    def test_style_cache_fingerprint_changes_pixels_and_budget_stays_bounded(self):
        layer = styled()
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360, cache_bytes=288 * 160 * 4)
        try:
            with patch.object(overlays, "raster_text", wraps=overlays.raster_text) as raster:
                first = compositor.apply(frame(360, 360), .25).to_ndarray(format="rgb24")
                compositor.apply(frame(360, 360), .5)
                self.assertEqual(raster.call_count, 1)
                layer["gradient_angle"] = 180
                second = compositor.apply(frame(360, 360), .5).to_ndarray(format="rgb24")
                self.assertEqual(raster.call_count, 2)
                self.assertFalse(np.array_equal(first, second))
                layer["stroke_width"] = .2
                compositor.apply(frame(360, 360), .5)
                self.assertEqual(raster.call_count, 3)
                self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)
        finally:
            compositor.close()
        self.assertEqual(compositor.used_bytes, 0)

    def test_font_scaling_retains_gradient_and_outline(self):
        counts = []
        for size in (.08, .16):
            source = pixels({**styled(), "font_size": size, "stroke_width": .2, "stroke_color": "#00FF00"})
            counts.append(np.count_nonzero(source[..., 3]))
            self.assertTrue(np.any(source[..., 1] == 255))
            self.assertTrue(np.any((source[..., 0] > 220) & (source[..., 3] == 255)))
            self.assertTrue(np.any((source[..., 2] > 220) & (source[..., 3] == 255)))
        self.assertGreater(counts[1] / counts[0], 3.5)
        self.assertLess(counts[1] / counts[0], 4.8)

    def test_real_cpu_mp4_contains_gradient_and_uniform_outline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.mp4", root / "styled.mp4"
            make_source(source, sound=False)
            layer = {**styled(), "gradient_start": "#00FF00", "stroke_width": .1,
                     "stroke_color": "#FFFFFF", "background": "#000000"}
            project = {"fps": 24, "width": 360, "height": 360,
                       "clips": [{"media_id": "video", "in": 0, "out": 1, "speed": 1, "volume": 0}], "overlays": [layer]}
            result = editor.render_project(project, {"video": source}, output, threading.Event(), lambda _: None)
            self.assertEqual(result["frame_count"], 24)
            with av.open(str(output)) as decoded:
                frames = [item.to_ndarray(format="rgb24") for item in decoded.decode(video=0)]
            for item in (frames[0], frames[-1]):
                self.assertGreater(np.count_nonzero((item[..., 1] > 220) & (item[..., 2] < 40)), 20)
                self.assertGreater(np.count_nonzero((item[..., 2] > 220) & (item[..., 1] < 40)), 20)
                self.assertGreater(np.count_nonzero(np.all(item > 220, axis=2)), 100)


class TextStyleAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editor-text-style-api-")
        self.root = Path(self.temp.name)
        self.app = web.Application()
        self.app["assets"], self.app["jobs"] = SimpleNamespace(), SimpleNamespace(jobs={})
        self.store = editor.register_editor_routes(self.app, self.root, self.root)
        overlays.register_editor_overlays(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_style_preview_is_shared_png_and_changes_on_angle_color_stroke(self):
        layer = {**styled(), "stroke_width": .2, "stroke_color": "#00FF00"}
        bodies = []
        for values in ({}, {"gradient_angle": 90}, {"gradient_start": "#00FFFF"}, {"stroke_color": "#FFFFFF"}):
            changed = {**layer, **values}
            response = await self.client.post("/api/editor/text-preview", json={"layer": changed, "width": 360, "height": 360})
            self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            body = await response.read()
            self.assertEqual(body, overlays.text_png(overlays.validate_layer(changed, fps=60), 360, 360))
            with Image.open(BytesIO(body)) as source:
                self.assertEqual(source.mode, "RGBA")
            bodies.append(body)
        self.assertEqual(len(set(bodies)), 4)

    async def test_project_save_readback_edit_inactive_choices_and_reload(self):
        layer = {**styled(), "stroke_width": .2, "stroke_color": "#12abef", "gradient_angle": 37}
        created = await self.client.post("/api/editor/projects", json={"overlays": [layer], "width": 360, "height": 360})
        self.assertEqual(created.status, 201, await created.text())
        project = await created.json()
        changed = {**project, "overlays": [{**project["overlays"][0], "fill_mode": "solid", "stroke_width": 0}]}
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=changed)
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        restored = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual(await restored.json(), saved)
        clean = saved["overlays"][0]
        self.assertNotIn("fill_mode", clean)
        self.assertNotIn("stroke_width", clean)
        self.assertEqual(clean["stroke_color"], "#12ABEF")
        self.assertEqual(clean["gradient_start"], "#FF0000")
        self.assertEqual(clean["gradient_end"], "#0000FF")
        self.assertEqual(clean["gradient_angle"], 37)
        reloaded = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
        self.assertEqual(reloaded.projects[project["id"]], saved)

    async def test_invalid_style_returns_clear_error_without_partial_save(self):
        created = await self.client.post("/api/editor/projects", json={"overlays": [styled()]})
        project = await created.json()
        changed = {**project, "overlays": [{**project["overlays"][0], "stroke_width": True}]}
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=changed)
        self.assertEqual(response.status, 400)
        self.assertIn("描邊", (await response.json())["error"])
        restored = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual(await restored.json(), project)
        response = await self.client.post("/api/editor/text-preview", json={"layer": {**styled(), "gradient_end": "red"}, "width": 360, "height": 360})
        self.assertEqual(response.status, 400)
        self.assertIn("漸層", (await response.json())["error"])


if __name__ == "__main__":
    unittest.main()
