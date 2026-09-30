"""Bounded, self-contained still image sources for editor overlays."""
from pathlib import Path
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 25 * 1024**2
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 8192
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def is_image_file(path):
    with Path(path).open("rb") as source:
        header = source.read(16)
    return (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff")
            or header[:4] == b"RIFF" and header[8:12] == b"WEBP")


def probe_image(path):
    # Imported lazily so video_editor.probe_media can use this module too.
    from video_editor import EditorError
    if Path(path).stat().st_size > MAX_IMAGE_BYTES:
        raise EditorError("圖片最多 25 MB。", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=list(IMAGE_MIMES)) as source:
                mime = IMAGE_MIMES[source.format]
                width, height = source.size
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_SIDE:
                    raise EditorError("圖片最多 1600 萬像素，任一邊最多 8192 像素。", 413)
                if getattr(source, "n_frames", 1) != 1:
                    raise EditorError("目前圖片圖層支援靜態圖片；請將動態圖片轉為 PNG 或影片。")
                source.verify()
            with Image.open(path, formats=list(IMAGE_MIMES)) as source:
                source.load()
                # Match the browser's display orientation and the renderer.
                with ImageOps.exif_transpose(source) as oriented:
                    width, height = oriented.size
        return {"kind": "image", "duration": 0, "width": width, "height": height,
                "fps": 0, "has_audio": False, "mime": mime}
    except EditorError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise EditorError("圖片尺寸超過安全解碼範圍。", 413) from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, KeyError) as error:
        raise EditorError("無法讀取圖片，請使用完整的 PNG、JPEG 或靜態 WebP。") from error


def write_thumbnail(source_path, target_path):
    probe_image(source_path)
    with Image.open(source_path, formats=list(IMAGE_MIMES)) as source, ImageOps.exif_transpose(source) as oriented:
        oriented.thumbnail((320, 320), Image.Resampling.LANCZOS)
        with oriented.convert("RGBA") as thumbnail:
            thumbnail.save(target_path, format="PNG")
