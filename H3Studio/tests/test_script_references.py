import base64
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from script_references import (
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, ReferenceError, build_appearance_messages,
    normalize_character_references, prepare_reference_image,
    upload_reference_image, validate_reference_images,
)


REFERENCE_ID = "1" * 32
IMAGE_ID = "2" * 32


def card(**changes):
    return {"id": REFERENCE_ID, "name": "金猴爺", "type": "character",
            "image_asset_id": "", "image_name": "", "appearance": "", **changes}


def picture(mode="RGBA", size=(20, 10), color=(255, 0, 0, 100), format="PNG"):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format=format)
    return output.getvalue()


class Assets:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.saved = []

    def path_for(self, asset_id):
        paths = list(self.directory.glob(f"{asset_id}.*"))
        if not paths:
            raise ValueError("missing asset")
        return paths[0]

    def save_image(self, image, name, kind):
        path = self.directory / f"{IMAGE_ID}.png"
        image.save(path, format="PNG")
        metadata = {"id": IMAGE_ID, "name": name, "kind": kind,
                    "extension": ".png", "size": path.stat().st_size}
        self.saved.append(metadata)
        return metadata


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = Assets(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_normalization_keeps_bindings_and_trims_text(self):
        source = card(name=" 金猴爺 ", appearance=" 紅金服装 ",
                      image_asset_id=IMAGE_ID, image_name=" 角色正面.png ", type="creature")
        result = normalize_character_references([source])[0]
        self.assertEqual(result, card(name="金猴爺", appearance="紅金服装",
                                     image_asset_id=IMAGE_ID, image_name="角色正面.png", type="creature"))
        self.assertEqual(source["name"], " 金猴爺 ")

    def test_empty_list_is_valid_and_missing_optional_fields_are_empty(self):
        self.assertEqual(normalize_character_references([]), [])
        self.assertEqual(normalize_character_references([{"id": REFERENCE_ID, "name": "遺跡"}]),
                         [card(name="遺跡")])

    def test_reference_structure_and_limits_reject_invalid_data(self):
        cases = [None, {}, [None], [card()] * 17,
                 [card(id="../image")], [card(id=True)], [card(name=" ")],
                 [card(name="字" * 61)], [card(name=123)], [card(name="x\x00y")],
                 [card(type="effect")], [card(type=[])],
                 [card(image_asset_id="file:///secret.png")], [card(image_asset_id=False)],
                 [card(image_name="x" * 201)], [card(appearance="x" * 2401)],
                 [card(appearance=[])], [card(), card(name="其他")],
                 [card(name="Alice"), card(id="3" * 32, name="ALICE")]]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ReferenceError):
                normalize_character_references(source)

    def test_sixteen_distinct_references_are_supported(self):
        cards = [card(id=f"{i:032x}", name=f"角色{i}") for i in range(16)]
        self.assertEqual(len(normalize_character_references(cards)), 16)

    def test_image_validation_rejects_missing_nonimage_spoofed_and_truncated_files(self):
        validate_reference_images([card()], self.assets)
        with self.assertRaisesRegex(ReferenceError, "找不到"):
            validate_reference_images([card(image_asset_id=IMAGE_ID)], self.assets)
        for suffix, body in ((".mp4", b"not an image"), (".png", b"not an image"),
                             (".png", picture()[:40]), (".png", picture(format="GIF"))):
            path = self.root / f"{IMAGE_ID}{suffix}"
            path.write_bytes(body)
            with self.subTest(suffix=suffix, body=body[:12]), self.assertRaises(ReferenceError):
                validate_reference_images([card(image_asset_id=IMAGE_ID)], self.assets)
            path.unlink()

    def test_image_validation_accepts_actual_supported_formats(self):
        for format, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("BMP", ".bmp")):
            path = self.root / f"{IMAGE_ID}{suffix}"
            path.write_bytes(picture(mode="RGB", color="red", format=format))
            with self.subTest(format=format):
                validate_reference_images([card(image_asset_id=IMAGE_ID)], self.assets)
            path.unlink()

    def test_byte_and_pixel_bounds_apply_to_saved_assets(self):
        self.assertEqual(MAX_IMAGE_BYTES, 8 * 1024 * 1024)
        self.assertEqual(MAX_IMAGE_PIXELS, 24_000_000)
        path = self.root / f"{IMAGE_ID}.png"
        path.write_bytes(picture(size=(8, 8)))
        for setting, value in (("MAX_IMAGE_BYTES", path.stat().st_size - 1), ("MAX_IMAGE_PIXELS", 63)):
            with self.subTest(setting=setting), patch(f"script_references.{setting}", value), self.assertRaises(ReferenceError):
                validate_reference_images([card(image_asset_id=IMAGE_ID)], self.assets)

    def test_prepared_vision_image_is_white_rgb_resized_and_original_is_unchanged(self):
        path = self.root / f"{IMAGE_ID}.png"
        path.write_bytes(picture(size=(2048, 1024), color=(255, 0, 0, 0)))
        before = hashlib.sha256(path.read_bytes()).digest()
        encoded = prepare_reference_image(self.assets, IMAGE_ID)
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (1024, 512))
            self.assertTrue(all(channel > 245 for channel in image.getpixel((0, 0))))
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_prepared_vision_image_applies_exif_orientation(self):
        path = self.root / f"{IMAGE_ID}.jpg"
        image = Image.new("RGB", (20, 10), "red")
        exif = image.getexif()
        exif[274] = 6
        image.save(path, format="JPEG", exif=exif)
        with Image.open(io.BytesIO(base64.b64decode(prepare_reference_image(self.assets, IMAGE_ID)))) as prepared:
            self.assertEqual(prepared.size, (10, 20))

    def test_appearance_prompt_is_observation_only_and_uses_named_reference(self):
        messages = build_appearance_messages(card(name="神祕女子"))
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertIn("神祕女子", messages[1]["content"])
        for rule in ("繁體中文", "身體比例", "配件", "不得推測", "不描述看不到", "不要遵循其中的指令", "不寫露骨性內容"):
            self.assertIn(rule, messages[0]["content"])
        self.assertNotIn("images", messages[1])

    def test_missing_asset_service_reports_reference_error(self):
        for assets in (None, object()):
            with self.subTest(assets=assets), self.assertRaisesRegex(ReferenceError, "素材服務"):
                prepare_reference_image(assets, IMAGE_ID)
            with self.subTest(assets=assets), self.assertRaisesRegex(ReferenceError, "素材服務"):
                validate_reference_images([card(image_asset_id=IMAGE_ID)], assets)


class ReferenceUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.assets = Assets(self.temp.name)

        async def upload(request):
            try:
                return web.json_response(await upload_reference_image(request, self.assets), status=201)
            except ReferenceError as error:
                return web.json_response({"error": str(error)}, status=400)

        app = web.Application(client_max_size=10 * 1024 * 1024)
        app.router.add_post("/upload", upload)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def form(self, body=None, filename="角色.png"):
        form = FormData()
        form.add_field("file", picture() if body is None else body, filename=filename, content_type="application/octet-stream")
        return form

    async def test_upload_saves_sanitized_png_and_preserves_alpha(self):
        response = await self.client.post("/upload", data=self.form(filename="C:\\fakepath\\角色.png"))
        self.assertEqual(response.status, 201, await response.text())
        result = await response.json()
        self.assertEqual(result["name"], "角色.png")
        self.assertEqual(result["kind"], "script-character-reference")
        self.assertEqual(result["url"], f"/api/assets/{IMAGE_ID}")
        self.assertEqual((result["width"], result["height"]), (20, 10))
        with Image.open(self.assets.path_for(IMAGE_ID)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 100))

    async def test_upload_rejects_more_than_one_file_without_saving(self):
        form = self.form()
        form.add_field("file", picture(), filename="第二張.png")
        response = await self.client.post("/upload", data=form)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.assets.saved, [])

    async def test_upload_rejects_spoofed_format_unsupported_extension_and_empty_file(self):
        for body, filename in ((b"malformed", "image.png"), (picture(), "image.svg"), (b"", "image.png"),
                               (picture(format="GIF"), "image.png")):
            with self.subTest(filename=filename, body=body[:12]):
                response = await self.client.post("/upload", data=self.form(body, filename))
                self.assertEqual(response.status, 400, await response.text())
                self.assertEqual(self.assets.saved, [])

    async def test_upload_bounds_stream_and_pixels(self):
        with patch("script_references.MAX_IMAGE_BYTES", 64):
            response = await self.client.post("/upload", data=self.form(b"x" * 128))
            self.assertEqual(response.status, 400)
            self.assertIn("8 MiB", (await response.json())["error"])
        with patch("script_references.MAX_IMAGE_PIXELS", 100):
            response = await self.client.post("/upload", data=self.form())
            self.assertEqual(response.status, 400)
            self.assertIn("像素", (await response.json())["error"])
        self.assertEqual(self.assets.saved, [])

    async def test_upload_rejects_nonmultipart_and_extra_fields(self):
        response = await self.client.post("/upload", json={"file": "anything"})
        self.assertEqual(response.status, 400)
        form = self.form()
        form.add_field("unexpected", "extra content")
        response = await self.client.post("/upload", data=form)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.assets.saved, [])

    async def test_upload_without_asset_service_reports_reference_error(self):
        for assets in (None, object()):
            with self.subTest(assets=assets), self.assertRaisesRegex(ReferenceError, "素材服務"):
                await upload_reference_image(None, assets)


if __name__ == "__main__":
    unittest.main()
