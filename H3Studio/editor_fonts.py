"""Discover locally installed fonts for shared text previews and exports.

Project files store stable family IDs, never arbitrary filesystem paths.  The
default candidates deliberately keep the previous editor's font order so an
older project continues to render with the same font on the same machine.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import threading
import unicodedata

from PIL import ImageFont

from video_editor import EditorError


def _default_candidates(bold):
    filename = "msjhbd.ttc" if bold else "msjh.ttc"
    weight = "Bold" if bold else "Regular"
    return [(f"C:/Windows/Fonts/{filename}", 0),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc", 3),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc", 0),
            (f"/usr/share/fonts/opentype/noto/NotoSansCJKtc-{weight}.otf", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", 0),
            (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf", 0)]


# Keep the original IDs and candidates for saved projects. Additional fonts
# come only from local installation directories and the OS font registry.
FONT_FAMILIES = {
    "default": {"label": "預設（系統中文字型）"},
    "microsoft_jhenghei": {
        "label": "微軟正黑體",
        "regular": [("C:/Windows/Fonts/msjh.ttc", 0)],
        "bold": [("C:/Windows/Fonts/msjhbd.ttc", 0)],
    },
    "microsoft_yahei": {
        "label": "微軟雅黑",
        "regular": [("C:/Windows/Fonts/msyh.ttc", 0)],
        "bold": [("C:/Windows/Fonts/msyhbd.ttc", 0)],
    },
    "kai": {
        "label": "標楷體",
        "regular": [("C:/Windows/Fonts/kaiu.ttf", 0),
                    ("/System/Library/Fonts/STKaiti.ttf", 0),
                    ("/System/Library/Fonts/Supplemental/Kaiti.ttc", 0)],
        "bold": [],
    },
    "mingliu": {
        "label": "細明體（MingLiU）",
        "regular": [("C:/Windows/Fonts/mingliu.ttc", 0)],
        "bold": [],
    },
    "arial": {
        "label": "Arial（西文字型）",
        "regular": [("C:/Windows/Fonts/arial.ttf", 0),
                    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
                    ("/Library/Fonts/Arial.ttf", 0),
                    ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", 0),
                    ("/usr/share/fonts/truetype/msttcorefonts/arial.ttf", 0)],
        "bold": [("C:/Windows/Fonts/arialbd.ttf", 0),
                 ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
                 ("/Library/Fonts/Arial Bold.ttf", 0),
                 ("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf", 0),
                 ("/usr/share/fonts/truetype/msttcorefonts/arialbd.ttf", 0)],
    },
    "georgia": {
        "label": "Georgia（西文襯線）",
        "regular": [("C:/Windows/Fonts/georgia.ttf", 0),
                    ("/System/Library/Fonts/Supplemental/Georgia.ttf", 0),
                    ("/Library/Fonts/Georgia.ttf", 0),
                    ("/usr/share/fonts/truetype/msttcorefonts/Georgia.ttf", 0),
                    ("/usr/share/fonts/truetype/msttcorefonts/georgia.ttf", 0)],
        "bold": [("C:/Windows/Fonts/georgiab.ttf", 0),
                 ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 0),
                 ("/Library/Fonts/Georgia Bold.ttf", 0),
                 ("/usr/share/fonts/truetype/msttcorefonts/Georgia_Bold.ttf", 0),
                 ("/usr/share/fonts/truetype/msttcorefonts/georgiab.ttf", 0)],
    },
    "noto_sans_cjk": {
        "label": "Noto Sans CJK（思源黑體）",
        "regular": [("C:/Windows/Fonts/NotoSansTC-VF.ttf", 0, 400),
                    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 3),
                    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
                    ("/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf", 0),
                    ("/Library/Fonts/NotoSansCJKtc-Regular.otf", 0)],
        "bold": [("C:/Windows/Fonts/NotoSansTC-VF.ttf", 0, 700),
                 ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 3),
                 ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
                 ("/usr/share/fonts/opentype/noto/NotoSansCJKtc-Bold.otf", 0),
                 ("/Library/Fonts/NotoSansCJKtc-Bold.otf", 0)],
    },
    "noto_serif_cjk": {
        "label": "Noto Serif CJK（思源宋體）",
        "regular": [("C:/Windows/Fonts/NotoSerifTC-VF.ttf", 0, 400),
                    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 3),
                    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
                    ("/usr/share/fonts/opentype/noto/NotoSerifCJKtc-Regular.otf", 0),
                    ("/Library/Fonts/NotoSerifCJKtc-Regular.otf", 0)],
        "bold": [("C:/Windows/Fonts/NotoSerifTC-VF.ttf", 0, 700),
                 ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 3),
                 ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 0),
                 ("/usr/share/fonts/opentype/noto/NotoSerifCJKtc-Bold.otf", 0),
                 ("/Library/Fonts/NotoSerifCJKtc-Bold.otf", 0)],
    },
    "pingfang": {
        "label": "蘋方（PingFang）",
        "regular": [("/System/Library/Fonts/PingFang.ttc", 0)],
        "bold": [],
    },
    "dejavu_sans": {
        "label": "DejaVu Sans（西文字型）",
        "regular": [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)],
        "bold": [("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)],
    },
    "dejavu_serif": {
        "label": "DejaVu Serif（西文襯線）",
        "regular": [("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 0)],
        "bold": [("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0)],
    },
}


INSTALLED_ID = re.compile(r"installed_[0-9a-f]{24}\Z")
FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc"}
_catalog_lock = threading.RLock()


def _font_directories():
    home = Path.home()
    if sys.platform == "win32":
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
        return roots
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts",
            Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "fonts"]


def _windows_font_entries():
    """Include per-user registrations and fonts installed outside Fonts dirs."""
    if sys.platform != "win32":
        return []
    import winreg
    entries = []
    roots = _font_directories()
    key_name = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive, root in ((winreg.HKEY_CURRENT_USER, roots[-1]), (winreg.HKEY_LOCAL_MACHINE, roots[0])):
        try:
            with winreg.OpenKey(hive, key_name) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    label, filename, kind = winreg.EnumValue(key, index)
                    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not filename:
                        continue
                    path = Path(os.path.expandvars(filename))
                    if not path.is_absolute():
                        path = root / path
                    label = re.sub(r"\s*\((?:TrueType|OpenType)\)\s*$", "", label, flags=re.I)
                    entries.append((path, label))
        except OSError:
            continue
    return entries


def _installed_font_files():
    # Registered names are useful for Chinese fonts whose embedded family name
    # is English. A path appears once even when registered in both scopes.
    files = {}
    for path, label in _windows_font_entries():
        if path.suffix.lower() in FONT_EXTENSIONS and path.is_file():
            files.setdefault(path.resolve(), label)
    for root in _font_directories():
        try:
            for path in root.rglob("*"):
                if path.suffix.lower() in FONT_EXTENSIONS and path.is_file():
                    files.setdefault(path.resolve(), "")
        except OSError:
            continue
    return sorted(files.items(), key=lambda item: str(item[0]).casefold())


def _font_face_count(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[:4] == b"ttcf":
            return min(struct.unpack(">I", header[8:12])[0], 256)
        return 1
    except (OSError, struct.error):
        return 0


def _family_key(family, italic=False):
    return unicodedata.normalize("NFKC", family).strip().casefold() + ("|italic" if italic else "|upright")


def _installed_id(family, italic=False):
    # No filenames/paths in projects; moving the same family between computers
    # or installing it per-user instead of globally does not change its ID.
    return "installed_" + hashlib.sha256(_family_key(family, italic).encode("utf-8")).hexdigest()[:24]


def _style_weight(style):
    style = re.sub(r"[\s_-]", "", style.casefold())
    for word, weight in (("thin", 100), ("extralight", 200), ("ultralight", 200), ("light", 300),
                         ("semibold", 600), ("demibold", 600), ("extrabold", 800), ("ultrabold", 800),
                         ("black", 900), ("heavy", 900), ("bold", 700), ("medium", 500)):
        if word in style:
            return weight
    match = re.search(r"w([1-9])", style)
    return int(match[1]) * 100 if match else 400


@lru_cache(maxsize=1)
def _installed_families():
    families = {}
    for path, registered_label in _installed_font_files():
        count = _font_face_count(path)
        labels = registered_label.split(" & ") if registered_label else []
        for index in range(count):
            try:
                font = ImageFont.truetype(str(path), 24, index=index)
                family, style = font.getname()
                if not family or family.startswith("@"):
                    continue
                style = style or "Regular"
                italic = bool(re.search(r"italic|oblique|斜體", style, re.I))
                identifier = _installed_id(family, italic)
                label = labels[index] if len(labels) == count else family
                if italic and not re.search(r"italic|oblique|斜體", label, re.I):
                    label += "（斜體）"
                spec = families.setdefault(identifier, {"label": label, "regular": [], "bold": [],
                                                         "family_key": _family_key(family, italic)})
                candidate = (str(path), index)
                weight = _style_weight(style)
                try:
                    axes = font.get_variation_axes()
                except (OSError, ValueError):
                    axes = []
                weight_axis = next((axis for axis in axes if axis["name"].lower() == b"weight"), None)
                if weight_axis is not None:
                    regular_weight = max(weight_axis["minimum"], min(400, weight_axis["maximum"]))
                    spec["regular"].append((abs(regular_weight - 400), (*candidate, regular_weight)))
                    if weight_axis["maximum"] >= 600:
                        bold_weight = max(weight_axis["minimum"], min(700, weight_axis["maximum"]))
                        spec["bold"].append((abs(bold_weight - 700), (*candidate, bold_weight)))
                else:
                    spec["regular"].append((abs(weight - 400), candidate))
                    if weight >= 600:
                        spec["bold"].append((abs(weight - 700), candidate))
                if abs(weight - 400) < spec.get("label_weight", 1000):
                    spec["label"], spec["label_weight"] = label, abs(weight - 400)
            except (OSError, ValueError):
                continue
    for spec in families.values():
        for style in ("regular", "bold"):
            spec[style] = [candidate for _, candidate in sorted(spec[style], key=lambda item: item[0])]
    return families


def _font_spec(font_family):
    if font_family in FONT_FAMILIES:
        return FONT_FAMILIES[font_family]
    with _catalog_lock:
        return _installed_families().get(font_family)


def validate_font_family(value):
    # A missing installed font must still survive import/save. Loading it gives
    # a clear error; only OS-discovered candidates can resolve a dynamic ID.
    if not isinstance(value, str) or value not in FONT_FAMILIES and not INSTALLED_ID.fullmatch(value):
        raise EditorError("文字字型需使用字型選單中的有效選項。", 422)
    return value


def font_candidates(bold, font_family="default"):
    validate_font_family(font_family)
    if font_family == "default":
        return _default_candidates(bold)
    spec = _font_spec(font_family)
    return spec["bold" if bold else "regular"] if spec else []


def _load_candidates(candidates, size):
    for candidate in candidates:
        path, index = candidate[:2]
        if not Path(path).is_file():
            continue
        try:
            font = ImageFont.truetype(path, size, index=index)
            if len(candidate) == 3:
                axes = font.get_variation_axes()
                weights = [candidate[2] if axis["name"].lower() == b"weight" else axis["default"]
                           for axis in axes]
                if not any(axis["name"].lower() == b"weight" for axis in axes):
                    continue
                font.set_variation_by_axes(weights)
                font._editor_weight = candidate[2]
            return font
        except (OSError, ValueError):
            continue
    return None


@lru_cache(maxsize=256)
def _cached_load_font(size, bold, font_family="default"):
    validate_font_family(font_family)
    font = _load_candidates(font_candidates(bold, font_family), size)
    # Several traditional typefaces have no separate bold face. Retain their
    # own design instead of substituting a different family; the catalog makes
    # that limitation visible to the editor.
    if font is None and bold and font_family != "default":
        font = _load_candidates(font_candidates(False, font_family), size)
    if font is not None:
        return font
    if font_family == "default":
        raise EditorError("找不到可用的文字字型。請安裝微軟正黑體或 Noto Sans CJK 後重新啟動 Studio。", 422)
    spec = _font_spec(font_family)
    label = spec["label"] if spec else font_family
    raise EditorError(f"這台電腦尚未安裝或無法載入「{label}」。請安裝後重新讀取本機字型，或改選其他字型。", 422)


def load_font(size, bold, font_family="default"):
    # Lock outside the memoized call so an in-flight load cannot repopulate the
    # cache with an old face after a completed refresh clears it.
    with _catalog_lock:
        return _cached_load_font(size, bold, font_family)


load_font.cache_clear = _cached_load_font.cache_clear


@lru_cache(maxsize=4096)
def _cached_has_glyph(character, bold, font_family="default"):
    return _font_has_glyph(load_font(24, bold, font_family), character)


def has_glyph(character, bold, font_family="default"):
    with _catalog_lock:
        return _cached_has_glyph(character, bold, font_family)


has_glyph.cache_clear = _cached_has_glyph.cache_clear


@lru_cache(maxsize=1)
def _cached_catalog():
    """Return installed choices only; do not expose server filesystem paths."""
    items = []
    represented = set()
    specs = [*FONT_FAMILIES.items(), *sorted(_installed_families().items(), key=lambda item: item[1]["label"].casefold())]
    for identifier, spec in specs:
        regular = _load_candidates(font_candidates(False, identifier), 24)
        native = _load_candidates(font_candidates(True, identifier), 24)
        bold = native or (regular if identifier != "default" else None)
        if regular is None or bold is None:
            continue
        if identifier.startswith("installed_") and spec["family_key"] in represented:
            continue
        if identifier != "default":
            represented.add(_family_key(regular.getname()[0], bool(re.search(r"italic|oblique|斜體", regular.getname()[1] or "", re.I))))
        # A bold-only installation may also be the family's regular fallback;
        # it has no separate bold design. Variable weights on one face do.
        native_bold = native is not None and (
            native.path, native.index, getattr(native, "_editor_weight", None)) != (
            regular.path, regular.index, getattr(regular, "_editor_weight", None))
        items.append({"id": identifier, "label": spec["label"], "available": True,
                      "family": regular.getname()[0], "bold_family": bold.getname()[0],
                      "bold_available": native_bold,
                      "cjk": all(_font_has_glyph(regular, char) and _font_has_glyph(bold, char)
                                 for char in "中文測試")})
    return items


def _font_has_glyph(font, character):
    missing, glyph = font.getmask("\U0010ffff"), font.getmask(character)
    return glyph.size != missing.size or bytes(glyph) != bytes(missing)


def font_catalog(refresh=False):
    """Return only usable installed choices, without exposing font file paths."""
    with _catalog_lock:
        if refresh:
            _installed_families.cache_clear()
            _cached_catalog.cache_clear()
            load_font.cache_clear()
            has_glyph.cache_clear()
        return [dict(item) for item in _cached_catalog()]
