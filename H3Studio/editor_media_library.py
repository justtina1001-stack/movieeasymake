"""Reversible removal from the editor's shared imported-media library.

Removing a library card changes metadata only. The independent source copy and
its identifier remain available to saved timelines, browser drafts, undo history,
and workers using an earlier project snapshot.
"""
from __future__ import annotations

import copy

from aiohttp import web

from video_editor import EditorError, atomic_json, check_id, now


REMOVED_AT = "library_removed_at"


def _media(store, identifier):
    try:
        check_id(identifier)
    except EditorError as error:
        raise EditorError("找不到剪輯素材。", 404) from error
    if store.closing:
        raise EditorError("Studio 正在關閉，請稍後再試。", 503)
    return store._get(store.media, identifier, "剪輯素材")


def _save(store, item):
    # Publish only after the atomic metadata write succeeds. Existing readers
    # retain their original record, and failed writes leave the registry intact.
    atomic_json(store.media_dir / f"{item['id']}.json", item)
    store.media[item["id"]] = item
    return item


def restore_library_media(store, item):
    """Restore an existing media record while the caller owns import_lock."""
    current = _media(store, item.get("id"))
    if REMOVED_AT not in current:
        return current
    restored = copy.deepcopy(current)
    restored.pop(REMOVED_AT)
    return _save(store, restored)


def register_editor_media_library(app):
    store = app["editor"]

    async def status(request):
        return web.json_response({"available": True, "reversible": True},
                                 headers={"Cache-Control": "no-store"})

    async def mutate(request, restore):
        if request.content_type != "application/json":
            raise EditorError("素材操作需使用空白 JSON 設定。")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError) as error:
            raise EditorError("素材操作需使用空白 JSON 設定。") from error
        if not isinstance(payload, dict) or payload:
            raise EditorError("素材操作僅接受空白設定，不能指定檔案或路徑。")
        async with store.import_lock:
            current = _media(store, request.match_info["media_id"])
            if restore:
                item = restore_library_media(store, current)
            elif isinstance(current.get(REMOVED_AT), str) and current[REMOVED_AT]:
                item = current
            else:
                item = copy.deepcopy(current)
                item[REMOVED_AT] = now()
                item = _save(store, item)
        return web.json_response({"media": item}, headers={"Cache-Control": "no-store"})

    async def remove(request):
        return await mutate(request, False)

    async def restore(request):
        return await mutate(request, True)

    app.router.add_get("/api/editor/media-library/status", status)
    app.router.add_post("/api/editor/media/{media_id}/remove", remove)
    app.router.add_post("/api/editor/media/{media_id}/restore", restore)
