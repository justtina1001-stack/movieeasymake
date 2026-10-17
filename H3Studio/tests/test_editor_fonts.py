"""Font selection survives save/import and shares the preview/export raster."""
from io import BytesIO
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import numpy as np
from PIL import Image

import editor_fonts as fonts
import editor_overlays as overlays
import video_editor as editor
from test_editor_overlays import frame, image_layer, text_layer


def clear_fonts():
    catalog = getattr(fonts, "_cached_catalog", None)
    if catalog is not None:
        catalog.cache_clear()
    fonts.load_font.cache_clear()
    fonts.has_glyph.cache_clear()
    overlays._font.cache_clear()
    overlays._has_glyph.cache_clear()


def clear_discovery():
    fonts._installed_families.cache_clear()
    clear_fonts()


def distinct_family():
    default_name = fonts.load_font(24, False).getname()[0]
    return next((item["id"] for item in fonts.font_catalog()
                 if item["id"] != "default" and item["family"] != default_name), None)


class FontSelectionTests(unittest.TestCase):
    def setUp(self):
        clear_fonts()
        self.addCleanup(clear_fonts)

    def test_legacy_and_explicit_default_keep_same_snapshot_and_pixels(self):
        original = overlays.validate_layer(text_layer(bold=True, stroke_width=.1))
        explicit = overlays.validate_layer(text_layer(bold=True, stroke_width=.1, font_family="default"))
        self.assertEqual(original, explicit)
        self.assertNotIn("font_family", explicit)
        self.assertEqual(overlays.text_png(original, 360, 360), overlays.text_png(explicit, 360, 360))

    def test_font_ids_are_whitelisted_and_only_apply_to_text(self):
        for value in (None, True, 1, [], {}, "", "DEFAULT", "../arial.ttf", "C:/Windows/Fonts/arial.ttf",
                      "installed_" + "A" * 24, "installed_" + "a" * 23, "installed_" + "a" * 25,
                      "installed_" + "g" * 24, "installed_" + "a" * 24 + "/font.ttf"):
            with self.subTest(value=value), self.assertRaises(editor.EditorError) as raised:
                overlays.validate_layer(text_layer(font_family=value))
            self.assertEqual(raised.exception.status, 422)
        with self.assertRaisesRegex(editor.EditorError, "只能使用文字"):
            overlays.validate_layer(image_layer(font_family="default"))

    def test_catalog_contains_only_loadable_choices_and_no_paths(self):
        info = overlays.font_information()
        self.assertTrue(info["available"])
        self.assertEqual(info["items"][0]["id"], "default")
        self.assertEqual(info["family"], info["items"][0]["family"])
        self.assertEqual(len({item["id"] for item in info["items"]}), len(info["items"]))
        for item in info["items"]:
            with self.subTest(identifier=item["id"]):
                self.assertTrue(item["available"])
                self.assertIs(type(item["bold_available"]), bool)
                self.assertIs(type(item["cjk"]), bool)
                self.assertEqual(fonts.load_font(24, False, item["id"]).getname()[0], item["family"])
                self.assertEqual(fonts.load_font(24, True, item["id"]).getname()[0], item["bold_family"])
                self.assertNotIn("path", item)
                self.assertFalse(any("/" in value or "\\" in value
                                     for value in item.values() if isinstance(value, str)))

    def test_missing_known_font_is_preserved_but_never_substituted(self):
        with patch.object(fonts, "font_candidates", return_value=[("/not-an-installed-editor-font.ttf", 0)]):
            clean = overlays.validate_layer(text_layer(text="A", font_family="georgia"))
            self.assertEqual(clean["font_family"], "georgia")
            with self.assertRaisesRegex(editor.EditorError, "無法載入") as raised:
                overlays.text_png(clean, 360, 360)
            self.assertEqual(raised.exception.status, 422)
            self.assertEqual(fonts.font_catalog(), [])

    def test_missing_bold_face_keeps_selected_family_and_reports_limit(self):
        regular_candidates = fonts.font_candidates(False)
        def candidates(bold, family="default"):
            return [] if bold else regular_candidates
        with patch.object(fonts, "font_candidates", side_effect=candidates):
            regular, bold = fonts.load_font(24, False, "kai"), fonts.load_font(24, True, "kai")
            self.assertEqual(regular.getname(), bold.getname())
            item = next(item for item in fonts.font_catalog() if item["id"] == "kai")
            self.assertFalse(item["bold_available"])

    def test_selection_changes_preview_and_cache_with_unchanged_layer_id(self):
        selected = distinct_family()
        if selected is None:
            self.skipTest("A second installed typeface is required for the real raster comparison.")
        layer = text_layer(text="AABg 123", font_size=.2, width=.9)
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360)
        self.addCleanup(compositor.close)
        with patch.object(overlays, "raster_text", wraps=overlays.raster_text) as raster:
            first = compositor.apply(frame(360, 360), .25).to_ndarray(format="rgb24")
            compositor.apply(frame(360, 360), .5)
            self.assertEqual(raster.call_count, 1)
            layer["font_family"] = selected
            second = compositor.apply(frame(360, 360), .5).to_ndarray(format="rgb24")
            self.assertEqual(raster.call_count, 2)
            self.assertFalse(np.array_equal(first, second))
            self.assertLessEqual(compositor.used_bytes, compositor.cache_bytes)
            self.assertNotEqual(overlays.text_png({**layer, "font_family": "default"}, 360, 360),
                                overlays.text_png(layer, 360, 360))

    def test_source_only_animation_cache_also_includes_font(self):
        selected = distinct_family()
        if selected is None:
            self.skipTest("A second installed typeface is required for the real raster comparison.")
        layer = text_layer(text="AABg 123", font_size=.2, width=.9,
                           position_keyframes=[{"time": 0, "x": .3, "y": .5}, {"time": 1, "x": .7, "y": .5}])
        compositor = overlays.OverlayCompositor([layer], {}, 360, 360)
        self.addCleanup(compositor.close)
        with patch.object(overlays, "raster_text", wraps=overlays.raster_text) as raster:
            compositor.apply(frame(360, 360), .25)
            compositor.apply(frame(360, 360), .5)
            self.assertEqual(raster.call_count, 1)
            layer["font_family"] = selected
            compositor.apply(frame(360, 360), .75)
            self.assertEqual(raster.call_count, 2)

    def test_glyph_checks_use_selected_family_and_cannot_reuse_default(self):
        latin = next((item["id"] for item in fonts.font_catalog() if not item["cjk"]), None)
        if latin is None or not overlays.font_information()["cjk"]:
            self.skipTest("Installed CJK and Latin-only fonts are required for the glyph comparison.")
        self.assertTrue(overlays._has_glyph("中", False))
        self.assertFalse(overlays._has_glyph("中", False, latin))
        with self.assertRaisesRegex(editor.EditorError, "所選字型缺少"):
            overlays.text_png(text_layer(text="中", font_family=latin), 360, 360)

    def test_saved_font_retains_text_styles_on_reload(self):
        selected = distinct_family()
        if selected is None:
            self.skipTest("A second installed typeface is required for the save/reload check.")
        with tempfile.TemporaryDirectory(prefix="editor-font-save-") as directory:
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            layer = text_layer(text="AABg 123", font_family=selected, bold=True,
                               stroke_width=.1, stroke_color="#12abef", fill_mode="linear_gradient")
            project = store.create_project({"overlays": [layer]})
            reloaded = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(reloaded.projects[project["id"]]["overlays"], project["overlays"])
            self.assertEqual(project["overlays"][0]["font_family"], selected)
            self.assertEqual(project["overlays"][0]["stroke_color"], "#12ABEF")
            self.assertEqual(project["overlays"][0]["fill_mode"], "linear_gradient")
            self.assertTrue(project["overlays"][0]["bold"])

    def test_discovered_font_changes_real_raster_and_survives_save_reload(self):
        default_family = fonts.load_font(24, False).getname()[0]
        selected = next((item["id"] for item in fonts.font_catalog()
                         if item["id"].startswith("installed_") and item["family"] != default_family
                         and all(fonts.has_glyph(character, False, item["id"]) for character in "AABg123")), None)
        if selected is None:
            self.skipTest("A discovered installed family with Latin glyphs is required for this raster check.")
        layer = text_layer(text="AABg 123", font_size=.2, width=.9, font_family=selected,
                           stroke_width=.02, fill_mode="linear_gradient")
        source = overlays.text_png(layer, 360, 360)
        self.assertNotEqual(source, overlays.text_png({**layer, "font_family": "default"}, 360, 360))
        with tempfile.TemporaryDirectory(prefix="editor-discovered-font-save-") as directory:
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            project = store.create_project({"overlays": [layer]})
            reloaded = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            saved_layer = reloaded.projects[project["id"]]["overlays"][0]
            self.assertEqual(saved_layer["font_family"], selected)
            self.assertEqual(overlays.text_png(saved_layer, 360, 360), source)


class InstalledFontDiscoveryTests(unittest.TestCase):
    def setUp(self):
        clear_discovery()
        self.addCleanup(clear_discovery)

    def test_windows_directories_include_system_and_per_user_installations(self):
        with patch.object(fonts.sys, "platform", "win32"), \
                patch.dict(fonts.os.environ, {"WINDIR": "C:/Custom Windows", "LOCALAPPDATA": "C:/User Local"}):
            self.assertEqual(fonts._font_directories(),
                             [Path("C:/Custom Windows/Fonts"), Path("C:/User Local/Microsoft/Windows/Fonts")])

    def test_windows_registry_reads_both_scopes_and_absolute_font_locations(self):
        with tempfile.TemporaryDirectory(prefix="editor-registry-fonts-") as directory:
            root = Path(directory)
            system, user = root / "System Fonts", root / "User Fonts"
            external = root / "Custom Location" / "Outside.otf"
            entries = {
                "user": [("Personal Family (TrueType)", "Personal.ttf", 1),
                         ("External Family (OpenType)", str(external), 1)],
                "system": [("Shared Family (TrueType)", "Shared.ttf", 1),
                           ("Non-string registration", 123, 3)],
            }
            opened = []
            class RegistryKey:
                def __init__(self, hive):
                    self.hive = hive
                def __enter__(self):
                    opened.append(self.hive)
                    return self
                def __exit__(self, *args):
                    return False
            registry = SimpleNamespace(HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="system",
                                       REG_SZ=1, REG_EXPAND_SZ=2, OpenKey=lambda hive, key: RegistryKey(hive),
                                       QueryInfoKey=lambda key: (0, len(entries[key.hive]), 0),
                                       EnumValue=lambda key, index: entries[key.hive][index])
            with patch.object(fonts.sys, "platform", "win32"), \
                    patch.dict("sys.modules", {"winreg": registry}), \
                    patch.object(fonts, "_font_directories", return_value=[system, user]):
                found = dict(fonts._windows_font_entries())
            self.assertEqual(set(opened), {"user", "system"})
            self.assertEqual(found, {user / "Personal.ttf": "Personal Family",
                                     external: "External Family", system / "Shared.ttf": "Shared Family"})

    def test_installed_font_files_cover_nested_directories_and_registered_locations(self):
        with tempfile.TemporaryDirectory(prefix="editor-installed-fonts-") as directory:
            root = Path(directory)
            user_fonts = root / "User Fonts"
            nested = user_fonts / "Family"
            nested.mkdir(parents=True)
            regular, collection = nested / "Regular.TTF", user_fonts / "Collection.otc"
            registered = root / "Other Location" / "Registered.otf"
            registered.parent.mkdir()
            for path in (regular, collection, registered):
                path.write_bytes(b"font fixture")
            (user_fonts / "not-a-font.txt").write_text("ignore", encoding="utf-8")
            with patch.object(fonts, "_font_directories", return_value=[user_fonts]), \
                    patch.object(fonts, "_windows_font_entries",
                                 return_value=[(registered, "Registered Family"), (regular, "User Family")]):
                entries = fonts._installed_font_files()
            found = {Path(path).resolve(): label for path, label in entries}
            self.assertEqual(set(found), {regular.resolve(), collection.resolve(), registered.resolve()})
            self.assertEqual(len(entries), len(found))
            self.assertEqual(found[registered.resolve()], "Registered Family")

    def test_discovery_uses_font_metadata_and_ids_survive_file_relocation(self):
        original = fonts.load_font(24, False)
        source = Path(original.path)
        with tempfile.TemporaryDirectory(prefix="editor-font-relocate-") as directory:
            root = Path(directory)
            first, relocated = root / ("misleading-name" + source.suffix), root / ("renamed" + source.suffix)
            shutil.copyfile(source, first)
            broken = root / "broken.ttf"
            broken.write_bytes(b"This file is not a font.")
            with patch.object(fonts, "_installed_font_files", return_value=[(first, "Untrusted Filename Label"), (broken, "Broken")]):
                discovered = fonts._installed_families()
                self.assertTrue(discovered)
                self.assertTrue(all(identifier.startswith("installed_") for identifier in discovered))
                self.assertTrue(all(len(identifier) == len("installed_") + 24 for identifier in discovered))
                selected = next(identifier for identifier in discovered
                                if fonts.load_font(24, False, identifier).getname()[0] == original.getname()[0])
                self.assertEqual(fonts.load_font(24, False, selected).getname()[0], original.getname()[0])
            shutil.copyfile(first, relocated)
            first.unlink()
            clear_discovery()
            with patch.object(fonts, "_installed_font_files", return_value=[(relocated, "Changed Registry Label")]):
                second = fonts._installed_families()
                self.assertEqual(set(discovered), set(second))
                self.assertEqual(fonts.load_font(24, False, selected).getname()[0], original.getname()[0])

    def test_font_collection_discovers_every_face(self):
        collection = next((Path(path) for path, _, *rest in fonts.font_candidates(False)
                           if Path(path).is_file() and Path(path).suffix.lower() in (".ttc", ".otc")), None)
        if collection is None:
            self.skipTest("An installed TrueType or OpenType collection is required for the face check.")
        with collection.open("rb") as stream:
            header = stream.read(12)
        face_count = struct.unpack(">I", header[8:12])[0]
        self.assertEqual(fonts._font_face_count(collection), face_count)
        names = {fonts.ImageFont.truetype(str(collection), 24, index=index).getname()[0]
                 for index in range(face_count)}
        with patch.object(fonts, "_installed_font_files", return_value=[(collection, "Collection")]):
            families = fonts._installed_families()
            actual = {fonts.load_font(24, False, identifier).getname()[0] for identifier in families}
        self.assertEqual(actual, names)

    def test_regular_and_bold_faces_share_family_while_italic_remains_selectable(self):
        with tempfile.TemporaryDirectory(prefix="editor-font-styles-") as directory:
            root = Path(directory)
            paths = [root / filename for filename in ("regular.ttf", "bold.ttf", "italic.ttf")]
            for path in paths:
                path.write_bytes(b"style fixture")
            styles = {str(path): style for path, style in zip(paths, ("Regular", "Bold", "Italic"))}
            def fake_font(path, size, index=0):
                def no_variations():
                    raise OSError("not a variable font")
                return SimpleNamespace(getname=lambda: ("Installed Example Family", styles[path]),
                                       get_variation_axes=no_variations)
            with patch.object(fonts, "_installed_font_files", return_value=[(path, "") for path in paths]), \
                    patch.object(fonts.ImageFont, "truetype", side_effect=fake_font):
                families = fonts._installed_families()
                self.assertEqual(len(families), 2)
                upright = next(identifier for identifier in families
                               if fonts.font_candidates(True, identifier))
                italic = next(identifier for identifier in families if identifier != upright)
                self.assertEqual(fonts.font_candidates(False, upright)[0], (str(paths[0]), 0))
                self.assertEqual(fonts.font_candidates(True, upright), [(str(paths[1]), 0)])
                self.assertEqual(fonts.font_candidates(False, italic), [(str(paths[2]), 0)])
                self.assertEqual(fonts.font_candidates(True, italic), [])

    def test_variable_weight_faces_use_regular_and_bold_weights_with_axis_limits(self):
        with tempfile.TemporaryDirectory(prefix="editor-variable-fonts-") as directory:
            root = Path(directory)
            paths = [root / filename for filename in ("full.ttf", "limited.ttf", "width-only.ttf")]
            for path in paths:
                path.write_bytes(b"variable font fixture")
            metadata = {
                str(paths[0]): ("Full Weight Family", [{"name": b"Weight", "minimum": 100, "maximum": 900, "default": 400}]),
                str(paths[1]): ("Limited Weight Family", [{"name": b"Weight", "minimum": 500, "maximum": 650, "default": 500}]),
                str(paths[2]): ("Width Only Family", [{"name": b"Width", "minimum": 50, "maximum": 200, "default": 100}]),
            }
            def fake_font(path, size, index=0):
                family, axes = metadata[path]
                return SimpleNamespace(getname=lambda: (family, "Regular"), get_variation_axes=lambda: axes,
                                       set_variation_by_axes=lambda values: self.fail("A font without a weight axis must remain unchanged."))
            with patch.object(fonts, "_installed_font_files", return_value=[(path, "") for path in paths]), \
                    patch.object(fonts.ImageFont, "truetype", side_effect=fake_font):
                by_label = {spec["label"]: identifier for identifier, spec in fonts._installed_families().items()}
                self.assertEqual(fonts.font_candidates(False, by_label["Full Weight Family"]), [(str(paths[0]), 0, 400)])
                self.assertEqual(fonts.font_candidates(True, by_label["Full Weight Family"]), [(str(paths[0]), 0, 700)])
                self.assertEqual(fonts.font_candidates(False, by_label["Limited Weight Family"]), [(str(paths[1]), 0, 500)])
                self.assertEqual(fonts.font_candidates(True, by_label["Limited Weight Family"]), [(str(paths[1]), 0, 650)])
                self.assertEqual(fonts.font_candidates(False, by_label["Width Only Family"]), [(str(paths[2]), 0)])
                self.assertEqual(fonts.font_candidates(True, by_label["Width Only Family"]), [])
                self.assertEqual(fonts.load_font(24, False, by_label["Width Only Family"]).getname()[0], "Width Only Family")

    def test_bold_catalog_metadata_requires_distinct_face_or_variable_weight(self):
        with tempfile.TemporaryDirectory(prefix="editor-bold-catalog-") as directory:
            root = Path(directory)
            bold_only, variable = root / "bold-only.ttf", root / "variable.ttf"
            for path in (bold_only, variable):
                path.write_bytes(b"catalog fixture")
            def fake_font(path, size, index=0):
                is_variable = path == str(variable)
                axes = [{"name": b"Weight", "minimum": 100, "maximum": 900, "default": 400}] if is_variable else []
                return SimpleNamespace(path=path, index=index,
                                       getname=lambda: ("Variable Family" if is_variable else "Bold Only Family",
                                                        "Regular" if is_variable else "Bold"),
                                       get_variation_axes=lambda: axes, set_variation_by_axes=lambda values: None)
            with patch.object(fonts, "FONT_FAMILIES", {}), \
                    patch.object(fonts, "_installed_font_files", return_value=[(bold_only, ""), (variable, "")]), \
                    patch.object(fonts.ImageFont, "truetype", side_effect=fake_font), \
                    patch.object(fonts, "_font_has_glyph", return_value=True):
                catalog = {item["family"]: item for item in fonts.font_catalog(refresh=True)}
            self.assertFalse(catalog["Bold Only Family"]["bold_available"])
            self.assertTrue(catalog["Variable Family"]["bold_available"])

    def test_missing_imported_installed_font_is_retained_and_rendering_fails_clearly(self):
        identifier = "installed_" + "a" * 24
        with patch.object(fonts, "_installed_font_files", return_value=[]), \
                tempfile.TemporaryDirectory(prefix="editor-font-missing-") as directory:
            layer = overlays.validate_layer(text_layer(text="A", font_family=identifier))
            self.assertEqual(layer["font_family"], identifier)
            store = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            project = store.create_project({"overlays": [layer]})
            reloaded = editor.EditorStore(directory, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(reloaded.projects[project["id"]]["overlays"][0]["font_family"], identifier)
            with self.assertRaisesRegex(editor.EditorError, "無法載入") as raised:
                overlays.text_png(layer, 360, 360)
            self.assertEqual(raised.exception.status, 422)

    def test_discovered_default_family_is_not_listed_twice(self):
        original = fonts.load_font(24, False)
        with patch.object(fonts, "_installed_font_files", return_value=[(Path(original.path), "Existing Default")]):
            catalog = fonts.font_catalog(refresh=True)
        duplicates = [item for item in catalog
                      if item["id"].startswith("installed_") and item["family"] == original.getname()[0]]
        self.assertEqual(duplicates, [])


class FontAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_fonts()
        self.temp = tempfile.TemporaryDirectory(prefix="editor-font-api-")
        self.root = Path(self.temp.name)
        self.app = web.Application()
        self.app["assets"], self.app["jobs"] = SimpleNamespace(), SimpleNamespace(jobs={})
        editor.register_editor_routes(self.app, self.root, self.root)
        overlays.register_editor_overlays(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()
        clear_fonts()

    async def test_selected_preview_matches_export_source_and_font_api(self):
        selected = distinct_family()
        if selected is None:
            self.skipTest("A second installed typeface is required for the preview check.")
        response = await self.client.get("/api/editor/fonts")
        catalog = await response.json()
        self.assertIn(selected, [item["id"] for item in catalog["items"]])
        layer = text_layer(text="AABg 123", font_family=selected, bold=True,
                           stroke_width=.1, fill_mode="linear_gradient")
        response = await self.client.post("/api/editor/text-preview", json={"layer": layer, "width": 360, "height": 360})
        self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
        body = await response.read()
        self.assertEqual(body, overlays.text_png(overlays.validate_layer(layer, fps=60), 360, 360))
        with Image.open(BytesIO(body)) as result:
            self.assertEqual(result.mode, "RGBA")
            self.assertIsNotNone(result.getbbox())

    async def test_bad_font_returns_clear_error_without_saving_partial_changes(self):
        response = await self.client.post("/api/editor/projects", json={"overlays": [text_layer()]})
        project = await response.json()
        changed = {**project, "overlays": [{**project["overlays"][0], "font_family": "../arbitrary.ttf"}]}
        response = await self.client.put(f"/api/editor/projects/{project['id']}", json=changed)
        self.assertEqual(response.status, 422)
        self.assertIn("字型", (await response.json())["error"])
        response = await self.client.get(f"/api/editor/projects/{project['id']}")
        self.assertEqual(await response.json(), project)

    async def test_font_refresh_rescans_new_installation_and_preview_uses_discovered_face(self):
        selected = next((item for item in fonts.font_catalog() if item["id"].startswith("installed_")), None)
        if selected is None:
            self.skipTest("An installed family beyond the legacy preset list is required for the refresh check.")
        source = Path(fonts.font_candidates(False, selected["id"])[0][0])
        installed = self.root / ("new-installation" + source.suffix)
        shutil.copyfile(source, installed)
        clear_discovery()
        self.addCleanup(clear_discovery)
        entries = []
        with patch.object(fonts, "_installed_font_files", side_effect=lambda: entries):
            response = await self.client.get("/api/editor/fonts")
            before = await response.json()
            self.assertNotIn(selected["id"], [item["id"] for item in before["items"]])
            entries.append((installed, ""))
            response = await self.client.get("/api/editor/fonts")
            cached = await response.json()
            self.assertNotIn(selected["id"], [item["id"] for item in cached["items"]])
            response = await self.client.get("/api/editor/fonts?refresh=1")
            self.assertEqual(response.status, 200)
            refreshed = await response.json()
            self.assertIn(selected["id"], [item["id"] for item in refreshed["items"]])
            self.assertFalse(refreshed["fixed"])
            layer = text_layer(text="AABg 123", font_family=selected["id"])
            response = await self.client.post("/api/editor/text-preview", json={"layer": layer, "width": 360, "height": 360})
            self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
            self.assertEqual(await response.read(), overlays.text_png(overlays.validate_layer(layer), 360, 360))
            response = await self.client.post("/api/editor/projects", json={"overlays": [layer]})
            self.assertEqual(response.status, 201)
            project = await response.json()
            response = await self.client.get(f"/api/editor/projects/{project['id']}")
            saved = await response.json()
            self.assertEqual(saved["overlays"][0]["font_family"], selected["id"])
            reloaded = editor.EditorStore(self.root, SimpleNamespace(), SimpleNamespace(jobs={}))
            self.assertEqual(reloaded.projects[project["id"]]["overlays"][0]["font_family"], selected["id"])


if __name__ == "__main__":
    unittest.main()
