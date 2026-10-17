"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const library = require("../static/editor_media_library.js");
const core = require("../static/editor.js");

const id = number => number.toString(16).padStart(32, "0");
const media = (number, kind = "video") => ({ id: id(number), kind, name: `素材 ${number}`, duration: 12,
  width: 1280, height: 720, fps: 24, has_audio: kind === "video", created_at: "2026-10-13T10:00:00+08:00",
  url: `/api/editor/media/${id(number)}/file`, source: { job_id: id(number + 100) } });
function fixture() {
  const video = media(1), audio = media(2, "audio"), image = media(3, "image");
  const registry = new Map([video, audio, image].map(item => [item.id, item]));
  const project = { id: id(201), name: "尚未儲存的剪輯", width: 1280, height: 720, fps: 24, updated_at: "r1",
    clips: [{ id: "main", media_id: video.id, in: 0, out: 8, speed: 1, volume: 1 }],
    audio_clips: [{ id: "music", media_id: audio.id, in: 0, out: 8, start: 0, track: 0, speed: 1, volume: 1, fade_in: 0, fade_out: 0 }],
    overlays: [
      { id: "picture", kind: "image", media_id: image.id, start: 0, end: 8, x: .5, y: .5, width: .3, rotation: 0, opacity: 1 },
      { id: "pip", kind: "video", media_id: video.id, in: 0, out: 8, start: 0, end: 8, speed: 1, volume: 0, x: .5, y: .5, width: .3, rotation: 0, opacity: 1 },
    ] };
  core.validateProject(project, registry);
  return { registry, project, session: new core.ProjectSession(project), video, audio, image };
}
const removeResponse = item => ({ ...item, library_removed_at: "2026-10-13T11:00:00+08:00", library_removed: true });
const update = (registry, payload, expectedId, removed) => {
  const item = library.validateVisibilityResponse(payload, expectedId, removed);
  registry.set(item.id, item);
  return item;
};

test("removing library cards retains timeline validation, dirty drafts, undo and playback sources", () => {
  const { registry, project, session, video, audio, image } = fixture();
  session.change(value => { value.name = "繼續編輯"; value.clips[0].volume = .6; });
  const before = { signature: core.signature(session.project), dirty: session.dirty,
    undo: JSON.stringify(session.undoStack), redo: JSON.stringify(session.redoStack), project: session.project };
  const cachedPreview = new Map([[video.id, { url: "/api/editor/previews/example/file", status: "completed" }]]);
  const cachedItem = cachedPreview.get(video.id);
  for (const item of [video, audio, image]) update(registry, { media: removeResponse(item) }, item.id, true);
  assert.equal(library.visibleMedia(registry.values()).length, 0);
  assert.equal(library.removedMedia(registry.values()).length, 3);
  assert.equal(registry.size, 3);
  assert.doesNotThrow(() => core.validateProject(session.project, registry));
  const restored = core.resolveDraft(project, { project: session.project, savedSignature: session.savedSignature }, registry);
  assert.equal(restored.status, "recoverable");
  assert.equal(core.signature(restored.project), before.signature);
  assert.equal(core.signature(session.project), before.signature);
  assert.equal(session.project, before.project);
  assert.equal(session.dirty, before.dirty);
  assert.equal(JSON.stringify(session.undoStack), before.undo);
  assert.equal(JSON.stringify(session.redoStack), before.redo);
  assert.equal(cachedPreview.get(video.id), cachedItem);
  const point = core.locateTime(session.project, 3);
  assert.equal(registry.get(point.clip.media_id).url, video.url);
  assert.equal(library.mediaReferences(session.project, video.id), 2);
  assert.equal(library.mediaReferences(session.project, audio.id), 1);
  assert.equal(library.mediaReferences(session.project, image.id), 1);
  assert.equal(session.travel(true), true);
  assert.doesNotThrow(() => core.validateProject(session.project, registry));
  assert.equal(session.travel(false), true);
  assert.doesNotThrow(() => core.validateProject(session.project, registry));
});

test("restore and source reimport expose the same source ID without replacing timeline references", () => {
  const { registry, session, video } = fixture();
  update(registry, removeResponse(video), video.id, true);
  const signature = core.signature(session.project);
  const reimported = { ...registry.get(video.id), library_removed_at: null, library_removed: false };
  const result = update(registry, reimported, video.id, false);
  assert.equal(library.visibleMedia(registry.values()).length, 3);
  assert.equal(library.removedMedia(registry.values()).length, 0);
  assert.equal(registry.size, 3);
  assert.equal(result.id, session.project.clips[0].media_id);
  for (const key of ["url", "source", "duration", "width", "height", "fps", "has_audio", "created_at"]) assert.deepEqual(result[key], video[key]);
  assert.equal(core.signature(session.project), signature);
  assert.equal(session.dirty, false);
  assert.equal(session.undoStack.length, 0);
  assert.doesNotThrow(() => core.validateProject(session.project, registry));
});

test("malformed, mismatched and wrong-state server replies never mutate the source registry", () => {
  const { registry, session, video } = fixture();
  const before = JSON.stringify([...registry]), signature = core.signature(session.project);
  for (const payload of [null, [], { media: null }, { ...removeResponse(video), id: id(999) },
    { ...removeResponse(video), name: " " }, { ...removeResponse(video), kind: "text" },
    { ...removeResponse(video), url: "https://outside.example/file.mp4" },
    { ...removeResponse(video), url: `/api/editor/media/${id(2)}/file` },
    { ...removeResponse(video), library_removed_at: true }, { ...removeResponse(video), library_removed_at: " " },
    { ...video, library_removed: true }, { ...removeResponse(video), library_removed: "true" },
    { ...removeResponse(video), library_removed: false }]) {
    assert.throws(() => update(registry, payload, video.id, true), /素材狀態/);
    assert.equal(JSON.stringify([...registry]), before);
    assert.equal(core.signature(session.project), signature);
    assert.equal(session.dirty, false);
  }
  assert.throws(() => update(registry, removeResponse(video), "../file", true), /素材狀態/);
  assert.throws(() => update(registry, removeResponse(video), video.id, "true"), /素材狀態/);
  assert.throws(() => update(registry, removeResponse(video), video.id, false), /素材狀態/);
  assert.throws(() => update(registry, { ...video, library_removed_at: false }, video.id, false), /素材狀態/);
});

test("visible counts and removed ordering preserve full registry records and stable timestamp ties", () => {
  const visible = media(1), oldest = { ...media(2), library_removed_at: "2026-10-13T10:00:00+08:00" },
    firstTie = { ...media(3), library_removed_at: "2026-10-13T03:00:00Z" },
    secondTie = { ...media(4), library_removed_at: "2026-10-13T11:00:00+08:00" },
    opaque = { ...media(5), library_removed_at: "legacy-timestamp" };
  const registry = new Map([visible, oldest, firstTie, secondTie, opaque].map(item => [item.id, item]));
  const before = [...registry], result = library.partitionMedia(registry.values());
  assert.equal(result.visible.length, 1);
  assert.equal(result.visible[0], visible);
  assert.deepEqual(result.removed.map(item => item.id), [firstTie.id, secondTie.id, oldest.id, opaque.id]);
  assert.equal(result.removed[0], firstTie);
  assert.deepEqual([...registry], before);
  const legacy = { ...media(6) }; delete legacy.kind; delete legacy.url;
  assert.equal(library.validateVisibilityResponse(legacy, legacy.id, false).id, legacy.id);
  for (const library_removed_at of [null, undefined, "", "  ", false, true, 123, {}]) assert.equal(library.isRemoved({ library_removed_at }), false);
  assert.deepEqual(library.visibleMedia(null), []);
  assert.deepEqual(library.removedMedia("bad list"), []);
});

test("browser module exposes the same safe filtering contract without touching editor or DOM state", () => {
  const { registry, session, video } = fixture();
  const sandbox = { window: { media: registry, session }, console };
  const before = JSON.stringify([...registry]);
  vm.runInNewContext(fs.readFileSync(require.resolve("../static/editor_media_library.js"), "utf8"), sandbox);
  const browser = sandbox.window.H3EditorMediaLibrary;
  assert.equal(typeof browser.validateVisibilityResponse, "function");
  assert.equal(browser.visibleMedia(registry.values()).length, 3);
  assert.equal(browser.mediaReferences(session.project, video.id), 2);
  assert.equal(JSON.stringify([...registry]), before);
  assert.equal(session.dirty, false);
  assert.equal(session.undoStack.length, 0);
});
