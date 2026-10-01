"""Portable, self-contained editor projects with bounded streaming ZIP I/O."""
from __future__ import annotations

import asyncio
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile
import threading
from urllib.parse import quote
import uuid
import zipfile
import zlib

from aiohttp import web

from video_editor import EditorError, check_id, now, probe_media


MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_UNPACKED_TOTAL = 8 * 1024**3
MAX_MEDIA_BYTES = 2 * 1024**3
MAX_MEDIA = 150
MAX_ENTRIES = 152
MAX_MANIFEST_BYTES = 1024**2
MAX_DIRECTORY_BYTES = 256 * 1024
MAX_COMPRESSION_RATIO = 200
CHUNK_BYTES = 1024**2
PROJECT_FIELDS = {"name", "clips", "audio_clips", "overlays", "width", "height", "fps"}
MEDIA_FIELDS = {"kind", "duration", "width", "height", "fps", "has_audio", "mime"}
FORMAT = "h3-studio-project"


def _check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError("專案檔案作業已取消。")


async def _finish_worker(function, *args, cancel=None):
    """Never remove a worker's files while its thread is still using them."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if cancel is not None:
            cancel.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done() and not task.cancelled():
            task.exception()  # Consume worker failures after caller cancellation.
        raise


def _write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    with path.open("xb") as handle:
        handle.write(data)
    return data


def _editable(project):
    return {key: copy.deepcopy(project[key]) for key in PROJECT_FIELDS
            if key in project and (key != "overlays" or project[key])}


def _source_clips(project):
    return (project["clips"] + project.get("audio_clips", [])
            + [layer for layer in project.get("overlays", []) if layer.get("kind") in ("image", "video")])


def _archive_version(project):
    # Older readers silently discard unknown clip fields. Require a new format
    # for visual fades, even when there are no extra layers to trigger v2.
    layers = project.get("overlays", [])
    visuals = project.get("clips", []) + layers
    if any(any(isinstance(item.get(field), dict) and item[field].get("type", "none") != "none"
               for field in ("animation_in", "animation_out", "transition_out")) for item in visuals):
        return 8
    text_defaults = {"stroke_width": 0, "stroke_color": "#000000", "fill_mode": "solid",
                     "gradient_start": "#ffffff", "gradient_end": "#ff8a3d", "gradient_angle": 90}
    if any(any((str(layer.get(key, default)).lower() if isinstance(default, str) else layer.get(key, default)) != default
               for key, default in text_defaults.items()) for layer in layers if layer.get("kind") == "text"):
        return 7
    if any("track_id" in layer for layer in layers):
        return 6
    if any(item.get("speed_curve") for item in visuals + project.get("audio_clips", [])):
        return 5
    if any(layer.get("position_keyframes") for layer in layers):
        return 4
    if any(layer.get("kind") == "video" for layer in layers) or any(
            item.get("fade_in", 0) or item.get("fade_out", 0) for item in visuals):
        return 3
    return 2 if layers else 1


def _build_archive(project, items, paths, destination, cancel):
    manifest = {"format": FORMAT, "version": _archive_version(project),
                "project": _editable(project), "media": []}
    total = 0
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item in items:
            _check_cancel(cancel)
            path = paths[item["id"]]
            expected = path.stat().st_size
            if expected > MAX_MEDIA_BYTES:
                raise EditorError("專案中的單一素材超過 2 GB，無法建立完整專案檔。", 413)
            name = f"media/{item['id']}.source"
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as output:
                while block := source.read(CHUNK_BYTES):
                    _check_cancel(cancel)
                    size += len(block)
                    total += len(block)
                    if size > MAX_MEDIA_BYTES or total > MAX_UNPACKED_TOTAL:
                        raise EditorError("專案素材總量超過 8 GB 或單一素材超過 2 GB。", 413)
                    output.write(block)
                    digest.update(block)
            if size != expected:
                raise EditorError("來源素材在儲存期間已改變，請重新儲存專案檔。")
            manifest["media"].append({"id": item["id"], "name": item["name"], "file": name,
                                      "metadata": {key: item[key] for key in MEDIA_FIELDS if key in item},
                                      "size": size, "sha256": digest.hexdigest()})
        _check_cancel(cancel)
        data = json.dumps(manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_MANIFEST_BYTES or total + len(data) > MAX_UNPACKED_TOTAL:
            raise EditorError("專案資訊或素材總量超過專案檔限制。", 413)
        archive.writestr("project.json", data)
    if destination.stat().st_size > MAX_ARCHIVE_BYTES:
        raise EditorError("完整專案檔超過 8 GB。", 413)
    _check_cancel(cancel)


def _check_zip_directory(path):
    """Bound the central directory before ZipFile allocates its entry objects."""
    size = path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise EditorError("專案檔最多 8 GB。", 413)
    with path.open("rb") as source:
        if size < 22:
            raise EditorError("專案 ZIP 檔案不完整。")
        source.seek(-22, 2)
        signature, disk, directory_disk, disk_count, count, directory_size, offset, comment = struct.unpack("<4s4H2LH", source.read(22))
        if signature != b"PK\x05\x06" or comment or disk or directory_disk:
            raise EditorError("專案 ZIP 格式不支援；請使用 Studio 儲存的 .h3edit.zip 檔。")
        end = size - 22
        locator_data = b""
        if size >= 42:
            source.seek(size - 42)
            locator_data = source.read(20)
        # ZipFile honors a ZIP64 locator even if the ordinary footer does not
        # contain sentinel values. Inspect that same effective directory first.
        if locator_data.startswith(b"PK\x06\x07") or count == 0xFFFF or directory_size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
            if size < 98:
                raise EditorError("ZIP64 專案檔案不完整。")
            locator, locator_disk, zip64_offset, disks = struct.unpack("<4sLQL", locator_data)
            if locator != b"PK\x06\x07" or locator_disk or disks != 1 or zip64_offset != size - 98:
                raise EditorError("ZIP64 專案目錄格式錯誤。")
            source.seek(zip64_offset)
            header = struct.unpack("<4sQHHLLQQQQ", source.read(56))
            if header[0] != b"PK\x06\x06" or header[1] != 44 or header[4] or header[5]:
                raise EditorError("ZIP64 專案目錄格式錯誤。")
            disk_count, count, directory_size, offset = header[6:10]
            end = zip64_offset
        if disk_count != count or count > MAX_ENTRIES or directory_size > MAX_DIRECTORY_BYTES:
            raise EditorError("專案檔案數量或目錄大小超過限制。", 413)
        if offset + directory_size > end:
            raise EditorError("專案 ZIP 目錄位置錯誤。")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EditorError("專案資訊含重複欄位。")
        result[key] = value
    return result


def _read_entry(archive, info, destination, maximum, total, cancel):
    digest, size = hashlib.sha256(), 0
    with archive.open(info) as source, destination.open("xb") as output:
        while block := source.read(min(CHUNK_BYTES, maximum + 1)):
            _check_cancel(cancel)
            size += len(block)
            total[0] += len(block)
            if size > maximum or total[0] > MAX_UNPACKED_TOTAL:
                raise EditorError("解開的專案資料超過大小限制。", 413)
            output.write(block)
            digest.update(block)
    return size, digest.hexdigest()


def _prepare_import(store, archive_path, staging, cancel):
    try:
        _check_zip_directory(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            infos, entries, folded = archive.infolist(), {}, set()
            if len(infos) > MAX_ENTRIES:
                raise EditorError("專案檔案數量超過限制。", 413)
            declared = 0
            for info in infos:
                name = info.filename
                mode = stat.S_IFMT(info.external_attr >> 16)
                if name != info.orig_filename or name.casefold() in folded or info.is_dir() or mode not in (0, stat.S_IFREG) or info.external_attr & 0x10:
                    raise EditorError("專案 ZIP 含重複、連結或不安全的檔案項目。")
                if name != "project.json" and not re.fullmatch(r"media/[a-f0-9]{32}\.source", name):
                    raise EditorError("專案 ZIP 含未允許的路徑或檔案。")
                if info.flag_bits & 0x41 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise EditorError("專案 ZIP 不能加密或使用不支援的壓縮方式。")
                maximum = MAX_MANIFEST_BYTES if name == "project.json" else MAX_MEDIA_BYTES
                declared += info.file_size
                if info.file_size > maximum or declared > MAX_UNPACKED_TOTAL or info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                    raise EditorError("專案 ZIP 解壓大小或壓縮比例超過限制。", 413)
                folded.add(name.casefold())
                entries[name] = info
            if "project.json" not in entries:
                raise EditorError("專案 ZIP 缺少 project.json。")
            total = [0]
            manifest_path = staging / "manifest.json"
            _read_entry(archive, entries["project.json"], manifest_path, MAX_MANIFEST_BYTES, total, cancel)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(EditorError("專案資訊含無效數值。")))
            if not isinstance(manifest, dict) or set(manifest) != {"format", "version", "project", "media"} or manifest.get("format") != FORMAT or type(manifest.get("version")) is not int or manifest["version"] not in (1, 2, 3, 4, 5, 6, 7, 8):
                raise EditorError("不支援這個專案格式或版本。")
            project, records = manifest["project"], manifest["media"]
            if not isinstance(project, dict) or set(project) - PROJECT_FIELDS or not {"name", "clips", "width", "height", "fps"} <= set(project):
                raise EditorError("專案設定不完整或含不支援的欄位。")
            if manifest["version"] == 1 and "overlays" in project:
                raise EditorError("文字與圖片圖層需要第 2 版專案備份格式。")
            if not isinstance(records, list) or len(records) > MAX_MEDIA:
                raise EditorError("專案最多包含 150 個素材。", 413)
            if not isinstance(project.get("clips"), list) or not isinstance(project.get("audio_clips", []), list) or not isinstance(project.get("overlays", []), list):
                raise EditorError("專案時間軸格式錯誤。")
            if any(not isinstance(layer, dict) for layer in project.get("overlays", [])):
                raise EditorError("專案圖層格式錯誤。")
            if any(not isinstance(clip, dict) for clip in project["clips"] + project.get("audio_clips", [])):
                raise EditorError("專案片段格式錯誤。")
            if _archive_version(project) > manifest["version"]:
                raise EditorError("入場／退場動畫與影片轉場需要第 8 版備份；文字描邊與漸層需要第 7 版，共用圖層軌道需要第 6 版，曲線變速需要第 5 版，位置動畫需要第 4 版，影片疊層與淡入淡出需要第 3 版。")
            clips = _source_clips(project)
            if any(not isinstance(clip, dict) for clip in clips):
                raise EditorError("專案片段格式錯誤。")
            references = {check_id(clip.get("media_id")) for clip in clips}
            if any(not isinstance(record, dict) for record in records):
                raise EditorError("素材資訊格式錯誤。")
            declared_ids = [check_id(record.get("id")) for record in records]
            expected_files = {"project.json", *(f"media/{identifier}.source" for identifier in declared_ids)}
            if len(set(declared_ids)) != len(declared_ids) or set(declared_ids) != references or set(entries) != expected_files:
                raise EditorError("專案引用的素材與封存檔內容不一致。")
            mapping, media, expected_entries = {}, [], {"project.json"}
            for record in records:
                if not isinstance(record, dict) or set(record) != {"id", "name", "file", "metadata", "size", "sha256"}:
                    raise EditorError("素材資訊格式錯誤。")
                old_id = check_id(record["id"])
                filename = f"media/{old_id}.source"
                if old_id in mapping or record["file"] != filename or filename not in entries:
                    raise EditorError("專案素材重複、缺漏或路徑錯誤。")
                if not isinstance(record["name"], str) or not record["name"].strip() or len(record["name"]) > 200 or not isinstance(record["metadata"], dict) or set(record["metadata"]) - MEDIA_FIELDS:
                    raise EditorError("素材名稱或資訊格式錯誤。")
                if type(record["size"]) is not int or record["size"] < 1 or record["size"] != entries[filename].file_size or not isinstance(record["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", record["sha256"]):
                    raise EditorError("素材大小或校驗資訊錯誤。")
                new_id = uuid.uuid4().hex
                while new_id in store.media or new_id in mapping.values():
                    new_id = uuid.uuid4().hex
                mapping[old_id] = new_id
                expected_entries.add(filename)
                path = staging / f"{new_id}.source"
                size, digest = _read_entry(archive, entries[filename], path, MAX_MEDIA_BYTES, total, cancel)
                if size != record["size"] or digest != record["sha256"]:
                    raise EditorError("專案素材校驗失敗，檔案可能損壞或不完整。")
                _check_cancel(cancel)
                info = probe_media(path)  # Never trust archived duration/codec metadata.
                _check_cancel(cancel)
                item = {"id": new_id, "name": record["name"], **info, "created_at": now(),
                        "url": f"/api/editor/media/{new_id}/file"}
                media.append(item)
                _write_json(staging / f"{new_id}.json", item)
            if set(mapping) != references or set(entries) != expected_entries:
                raise EditorError("專案引用的素材與封存檔內容不一致。")
            for clip in clips:
                clip["media_id"] = mapping[clip["media_id"]]
            project_id = uuid.uuid4().hex
            while project_id in store.projects:
                project_id = uuid.uuid4().hex
            validator = copy.copy(store)
            validator.media_dir = staging
            validator.media = {item["id"]: item for item in media}
            restored = validator._project(project, {"id": project_id})
            _write_json(staging / "restored-project.json", restored)
            _check_cancel(cancel)
            return restored, media
    except EditorError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeError, ValueError, KeyError, TypeError, RecursionError, NotImplementedError, struct.error, EOFError, zlib.error) as error:
        raise EditorError("無法讀取專案檔；檔案格式錯誤、損壞或資料不完整。") from error


def _publish_file(source, destination, published, cancel):
    _check_cancel(cancel)
    try:
        # Atomic, no-overwrite publication, on the same filesystem as staging.
        os.link(source, destination)
        published.append(destination)
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV} and getattr(error, "winerror", None) not in {1, 50}:
            raise
        # Filesystems without hardlinks still get exclusive no-overwrite writes.
        with destination.open("xb") as output:
            published.append(destination)
            with source.open("rb") as incoming:
                while block := incoming.read(CHUNK_BYTES):
                    _check_cancel(cancel)
                    output.write(block)


def _publish_import(store, staging, project, media, published, cancel):
    for item in media:
        for suffix in (".source", ".json"):
            _publish_file(staging / f"{item['id']}{suffix}", store.media_dir / f"{item['id']}{suffix}", published, cancel)
    # The project record is last, after all sources and metadata are durable.
    _publish_file(staging / "restored-project.json", store.project_dir / f"{project['id']}.json", published, cancel)
    _check_cancel(cancel)


def _cleanup(directory, root, published):
    for path in reversed(published):
        path.unlink(missing_ok=True)
    if directory.resolve().parent != root.resolve():
        raise RuntimeError("Unexpected project archive temporary directory")
    shutil.rmtree(directory)


def _download_name(project):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(project["name"])).strip(" .")[:100] or "H3 專案"
    return f"attachment; filename=\"project-{project['id']}.h3edit.zip\"; filename*=UTF-8''{quote(name + '.h3edit.zip', safe='')}"


def register_editor_project_files(app):
    store = app["editor"]
    temporary_root = store.root / "project_files"
    temporary_root.mkdir(exist_ok=True)
    busy = {"archive": False, "import": False}

    async def download(request):
        if busy["archive"]:
            raise EditorError("已有完整專案檔正在儲存，請稍候再試。", 409)
        if store.closing:
            raise EditorError("Studio 正在關閉。", 503)
        project = copy.deepcopy(store._get(store.projects, request.match_info["project_id"], "剪輯專案"))
        project = store._project(project, project)
        ids = list(dict.fromkeys(clip["media_id"] for clip in _source_clips(project)))
        items = [copy.deepcopy(store.media[identifier]) for identifier in ids]
        paths = {identifier: store.media_path(identifier) for identifier in ids}
        directory = Path(tempfile.mkdtemp(prefix="save-", dir=temporary_root))
        busy["archive"] = True
        target, cancel = directory / "project.zip", threading.Event()
        try:
            await _finish_worker(_build_archive, project, items, paths, target, cancel, cancel=cancel)
            response = web.StreamResponse(headers={"Content-Type": "application/zip", "Content-Length": str(target.stat().st_size),
                                                    "Content-Disposition": _download_name(project), "Cache-Control": "no-store"})
            await response.prepare(request)
            if request.method != "HEAD":
                with target.open("rb") as handle:
                    while block := await _finish_worker(handle.read, CHUNK_BYTES):
                        await response.write(block)
            await response.write_eof()
            return response
        finally:
            try:
                await _finish_worker(_cleanup, directory, temporary_root, [])
            finally:
                busy["archive"] = False

    async def restore(request):
        if busy["import"]:
            raise EditorError("已有專案檔正在讀取，請稍候再試。", 409)
        if store.closing:
            raise EditorError("Studio 正在關閉。", 503)
        if not request.content_type.startswith("multipart/"):
            raise EditorError("請上傳 .h3edit.zip 完整專案檔。")
        if request.content_length is not None and request.content_length > MAX_ARCHIVE_BYTES + MAX_MANIFEST_BYTES:
            raise EditorError("專案檔最多 8 GB。", 413)
        directory = Path(tempfile.mkdtemp(prefix="open-", dir=temporary_root))
        busy["import"] = True
        target, cancel, published, committed = directory / "archive.zip", threading.Event(), [], False
        try:
            reader = await request.multipart()
            part = await reader.next()
            filename = getattr(part, "filename", None)
            if part is None or getattr(part, "name", None) != "file" or not isinstance(filename, str) or not filename.lower().endswith(".h3edit.zip"):
                raise EditorError("請選擇一個 .h3edit.zip 完整專案檔。")
            size = 0
            with target.open("xb") as handle:
                while block := await part.read_chunk(CHUNK_BYTES):
                    size += len(block)
                    if size > MAX_ARCHIVE_BYTES:
                        raise EditorError("專案檔最多 8 GB。", 413)
                    await _finish_worker(handle.write, block)
            if not size or await reader.next() is not None:
                raise EditorError("請一次上傳一個非空白的完整專案檔。")
            project, media = await _finish_worker(_prepare_import, store, target, directory, cancel, cancel=cancel)
            async with store.import_lock:
                if store.closing:
                    raise EditorError("Studio 正在關閉。", 503)
                await _finish_worker(_publish_import, store, directory, project, media, published, cancel, cancel=cancel)
                store.media.update({item["id"]: item for item in media})
                store.projects[project["id"]] = project
                committed = True
            return web.json_response({"project": project, "media": media}, status=201)
        finally:
            try:
                await _finish_worker(_cleanup, directory, temporary_root, [] if committed else published)
            finally:
                busy["import"] = False

    app.router.add_get("/api/editor/projects/{project_id}/archive", download)
    app.router.add_post("/api/editor/projects/import", restore)
    return busy
