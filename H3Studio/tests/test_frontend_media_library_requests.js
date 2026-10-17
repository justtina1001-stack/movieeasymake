"use strict";
/* Execute shipped controller and event bindings with small DOM/API adapters. */
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const core = require("../static/editor.js"), mediaLibrary = require("../static/editor_media_library.js");
const source = fs.readFileSync(require.resolve("../static/editor.js"), "utf8");
const lines = source.split(/\r?\n/);
function shippedFunction(name) {
  const declaration = new RegExp(`^  (?:async )?function ${name}\\(`, "m").exec(source);
  assert.ok(declaration, `Missing shipped ${name}`);
  const start = declaration.index;
  for (let end = source.indexOf("\n", start); end !== -1; end = source.indexOf("\n", end + 1)) {
    const candidate = source.slice(start, end);
    try { new vm.Script(candidate); return candidate; }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  assert.fail(`Cannot extract shipped ${name}`);
}
function shippedLine(fragment) {
  const line = lines.find(value => value.includes(fragment));
  assert.ok(line, `Missing shipped binding ${fragment}`);
  return line;
}
const controllerSource = [
  shippedLine("const escape ="), shippedLine("const exporting ="), shippedLine("const locked ="),
  shippedLine("const action ="), shippedLine("const json ="),
  ...["renderDisabled", "overlaySupported", "renderMedia", "checkMediaLibrarySupport", "setMediaRemoved"].map(shippedFunction),
  shippedLine('$("mediaList").onclick ='), shippedLine('$("removedMediaList").onclick ='),
  shippedLine('$("checkMediaLibrary").onclick ='),
].join("\n");
const id = number => number.toString(16).padStart(32, "0");
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const item = (number, kind = "video") => ({ id: id(number), name: `${kind} ${number}`, kind, width: 1280, height: 720,
  duration: 12, fps: 24, has_audio: kind === "video", created_at: "2026-10-13T10:00:00+08:00",
  url: `/api/editor/media/${id(number)}/file`, source: { job_id: id(number + 100) } });
const removed = value => ({ ...value, library_removed: true, library_removed_at: "2026-10-13T11:00:00+08:00" });
const restored = value => ({ ...value, library_removed: false, library_removed_at: null });
const attrKey = name => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
class Element {
  constructor(name, parent = null) {
    this.name = name; this.parent = parent; this.dataset = {}; this.children = [];
    this.disabled = false; this._text = ""; this._html = "";
    const classes = new Set();
    this.classList = { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) };
  }
  set textContent(value) { this._text = String(value); }
  get textContent() { return this._text; }
  set innerHTML(value) {
    this._html = String(value); this.children = [];
    for (const match of this._html.matchAll(/<button\b([^>]*)>/g)) {
      const button = new Element("button", this);
      for (const attribute of match[1].matchAll(/\bdata-([a-z-]+)="([^"]*)"/g)) button.dataset[attrKey(attribute[1])] = attribute[2];
      button.disabled = /\bdisabled(?:\s|=|$)/.test(match[1]);
      this.children.push(button);
    }
  }
  get innerHTML() { return this._html; }
  closest(selector) {
    const match = /^\[data-([a-z-]+)\]$/.exec(selector);
    return match && Object.hasOwn(this.dataset, attrKey(match[1])) ? this : this.parent?.closest(selector) || null;
  }
  setAttribute(name, value) { this[name] = String(value); }
}
function harness(handler = () => ({ available: true, reversible: true })) {
  const elements = new Map(), requests = [], errors = [], notifications = [], added = [];
  const $ = name => {
    if (!elements.has(name)) elements.set(name, new Element(name));
    return elements.get(name);
  };
  const video = item(1), audio = item(2, "audio"), image = item(3, "image");
  const media = new Map([video, audio, image].map(value => [value.id, value]));
  const project = { id: id(201), name: "剪輯中", width: 1280, height: 720, fps: 24, updated_at: "r1",
    clips: [{ id: "main", media_id: video.id, in: 0, out: 8, volume: 1, speed: 1 }],
    audio_clips: [{ id: "sound", media_id: audio.id, in: 0, out: 8, start: 0, track: 0, volume: .6, speed: 1, fade_in: 0, fade_out: 0 }],
    overlays: [{ id: "picture", kind: "image", media_id: image.id, start: 0, end: 8, x: .5, y: .5, width: .2, rotation: 0, opacity: 1 },
      { id: "pip", kind: "video", media_id: video.id, start: 0, end: 8, in: 0, out: 8, speed: 1, volume: 0, x: .5, y: .5, width: .3, rotation: 0, opacity: 1 }] };
  const session = new core.ProjectSession(project);
  session.change(value => { value.name = "未儲存剪輯"; });
  const state = { media, session, busy: false, backendReady: true, mediaRemovalReady: true, mediaLibraryChecking: false,
    mediaLibraryError: "", selected: "main", selectedKind: "video", videoOverlaysReady: true, imageOverlaysReady: true };
  const document = { querySelectorAll(selector) {
    const keys = selector.split(",").map(value => /^\[data-([a-z-]+)\]$/.exec(value)).filter(Boolean).map(match => attrKey(match[1]));
    return [...elements.values()].flatMap(element => element.children).filter(node => keys.some(key => Object.hasOwn(node.dataset, key)));
  } };
  const noop = () => {};
  const sandbox = { state, $, core, mediaLibrary, document, project: () => state.session.project,
    selectedClip: () => state.session.project.clips.find(clip => clip.id === state.selected),
    selectedSpeedVideo: () => state.session.project.clips.find(clip => clip.id === state.selected),
    canSwitch: () => !state.busy && state.backendReady,
    totalDuration: core.totalDuration, formatTime: core.formatTime, mediaKind: core.mediaKind,
    previewViewport: { render: noop }, layoutController: { render: noop },
    renderTextStyleControls: noop, renderTextFontControls: noop, renderSubtitleControls: noop,
    renderPositionControls: noop, renderSpeedControls: noop, renderAnimationControls: noop, renderColorControls: noop,
    api: async (url, options) => { requests.push({ url, options }); return handler(url, options, state); },
    notify: message => notifications.push(message), errorNotice: error => errors.push(error),
    addMedia: mediaId => added.push({ kind: "media", mediaId }), addAudioMedia: mediaId => added.push({ kind: "audio", mediaId }),
    addOverlay: (kind, mediaId) => added.push({ kind, mediaId }) };
  vm.runInNewContext(controllerSource + "\nthis.controller = { renderMedia, renderDisabled, setMediaRemoved, checkMediaLibrarySupport };", sandbox);
  sandbox.controller.renderMedia();
  const button = (key, mediaId) => document.querySelectorAll(`[data-${key}]`).find(node => node.dataset[attrKey(key)] === mediaId);
  const click = async (key, mediaId, nested = false, forced = false) => {
    const node = button(key, mediaId);
    assert.ok(node, `Rendered ${key} button for ${mediaId}`);
    if (node.disabled && !forced) return;
    return node.parent.onclick({ target: nested ? new Element("span", node) : node });
  };
  return { state, $, requests, errors, notifications, added, media, video, audio, image, session, original: project,
    button, click, ...sandbox.controller };
}
function unchanged(h, before) {
  assert.equal(core.signature(h.session.project), before.signature);
  assert.equal(h.session.project, before.object);
  assert.equal(h.session.dirty, before.dirty);
  assert.equal(JSON.stringify(h.session.undoStack), before.undo);
  assert.equal(JSON.stringify(h.session.redoStack), before.redo);
  assert.equal(h.session.savedSignature, before.saved);
  assert.doesNotThrow(() => core.validateProject(h.session.project, h.media));
}
const snapshot = h => ({ signature: core.signature(h.session.project), object: h.session.project,
  dirty: h.session.dirty, undo: JSON.stringify(h.session.undoStack), redo: JSON.stringify(h.session.redoStack), saved: h.session.savedSignature });

test("rendered delegated remove and restore buttons change library counts while every source reference survives", async () => {
  const h = harness((url, options, state) => {
    const match = /\/media\/([0-9a-f]{32})\/(remove|restore)$/.exec(url);
    assert.ok(match); assert.equal(options.method, "POST"); assert.equal(options.body, "{}");
    return { media: match[2] === "remove" ? removed(state.media.get(match[1])) : restored(state.media.get(match[1])) };
  });
  const before = snapshot(h), cachedPreview = { url: "/cached/video.mp4", status: "completed" };
  h.state.previews = new Map([[h.video.id, cachedPreview]]);
  assert.equal(h.$("mediaCount").textContent, "3");
  for (const value of [h.video, h.audio, h.image]) {
    await h.click("remove-media", value.id, true);
    assert.equal(h.media.size, 3);
    assert.equal(mediaLibrary.isRemoved(h.media.get(value.id)), true);
    assert.equal(h.button("remove-media", value.id), undefined);
    assert.ok(h.button("restore-media", value.id));
    unchanged(h, before);
  }
  assert.equal(h.$("mediaCount").textContent, "0");
  assert.equal(h.$("removedMediaCount").textContent, "3");
  assert.match(h.$("mediaList").innerHTML, /加入影片、音訊或圖片/);
  assert.equal(h.state.previews.get(h.video.id), cachedPreview);
  const draft = core.resolveDraft(h.original, { project: h.session.project, savedSignature: h.session.savedSignature }, h.media);
  assert.equal(draft.status, "recoverable");
  assert.equal(core.signature(draft.project), before.signature);
  const source = h.media.get(core.locateTime(h.session.project, 2).clip.media_id);
  assert.equal(source.url, h.video.url);
  for (const value of [h.video, h.audio, h.image]) {
    await h.click("restore-media", value.id, true);
    assert.equal(mediaLibrary.isRemoved(h.media.get(value.id)), false);
    assert.ok(h.button("remove-media", value.id));
    assert.equal(h.button("restore-media", value.id), undefined);
    for (const key of ["id", "source", "url", "duration", "width", "height", "fps", "has_audio"]) assert.deepEqual(h.media.get(value.id)[key], value[key]);
    unchanged(h, before);
  }
  assert.equal(h.$("mediaCount").textContent, "3");
  assert.equal(h.$("removedMediaCount").textContent, "0");
  assert.equal(h.errors.length, 0);
  assert.equal(h.requests.length, 6);
});

test("a deferred remove disables cards and prevents rapid or queued clicks and competing checks", async () => {
  const pending = deferred(), h = harness(() => pending.promise);
  const removing = h.click("remove-media", h.video.id);
  assert.equal(h.state.busy, true);
  assert.equal(h.button("remove-media", h.video.id).disabled, true);
  assert.equal(h.button("remove-media", h.audio.id).disabled, true);
  assert.equal(h.$("checkMediaLibrary").disabled, true);
  assert.equal(h.$("saveProject").disabled, true);
  await h.click("remove-media", h.video.id, false, true);
  await h.click("remove-media", h.audio.id, true, true);
  await h.$("checkMediaLibrary").onclick();
  assert.equal(h.requests.length, 1);
  assert.equal(h.$("mediaCount").textContent, "3");
  pending.resolve(removed(h.video));
  await removing;
  assert.equal(h.state.busy, false);
  assert.equal(h.$("mediaCount").textContent, "2");
  assert.equal(h.button("restore-media", h.video.id).disabled, false);
  assert.equal(h.$("saveProject").disabled, false);
});

test("partial visibility replies preserve source metadata and a restore reply clears the prior removal timestamp", async () => {
  const h = harness(url => ({ id: id(1), name: "video 1",
    ...(url.endsWith("/remove") ? { library_removed: true, library_removed_at: "2026-10-13T11:00:00+08:00" } : {}) }));
  const before = snapshot(h);
  await h.click("remove-media", h.video.id);
  assert.equal(mediaLibrary.isRemoved(h.media.get(h.video.id)), true);
  for (const key of ["kind", "url", "source", "duration", "width", "height", "fps", "has_audio", "created_at"]) assert.deepEqual(h.media.get(h.video.id)[key], h.video[key]);
  assert.equal(h.$("mediaCount").textContent, "2");
  unchanged(h, before);
  await h.click("restore-media", h.video.id);
  assert.equal(Object.hasOwn(h.media.get(h.video.id), "library_removed_at"), false);
  assert.equal(h.media.get(h.video.id).library_removed, false);
  for (const key of ["kind", "url", "source", "duration", "width", "height", "fps", "has_audio", "created_at"]) assert.deepEqual(h.media.get(h.video.id)[key], h.video[key]);
  assert.equal(h.$("mediaCount").textContent, "3");
  assert.equal(h.$("removedMediaCount").textContent, "0");
  assert.equal(h.errors.length, 0);
  unchanged(h, before);
});

test("malformed and failed remove replies retain cards, registry entries and drafts and release busy state", async () => {
  for (const outcome of [new Error("網路暫時中斷"), { ...removed(item(1)), id: id(999) },
    { ...removed(item(1)), library_removed_at: "" }, { ...removed(item(1)), url: "https://outside.example/video.mp4" }]) {
    const h = harness(() => outcome instanceof Error ? Promise.reject(outcome) : outcome);
    const before = snapshot(h), mediaBefore = h.media.get(h.video.id), markup = h.$("mediaList").innerHTML;
    await h.click("remove-media", h.video.id);
    assert.equal(h.errors.length, 1);
    assert.equal(h.state.busy, false);
    assert.equal(h.media.get(h.video.id), mediaBefore);
    assert.equal(h.$("mediaList").innerHTML, markup);
    assert.equal(h.$("mediaCount").textContent, "3");
    assert.equal(h.$("removedMediaCount").textContent, "0");
    assert.equal(h.button("remove-media", h.video.id).disabled, false);
    assert.equal(h.notifications.length, 0);
    unchanged(h, before);
  }
});

test("feature checks disable only removal while editing and existing add actions remain available", async () => {
  const pending = deferred(), h = harness(() => pending.promise);
  h.state.mediaRemovalReady = false; h.renderDisabled();
  const before = snapshot(h), checking = h.$("checkMediaLibrary").onclick();
  assert.equal(h.state.mediaLibraryChecking, true);
  assert.equal(h.$("checkMediaLibrary").disabled, true);
  assert.equal(h.button("remove-media", h.video.id).disabled, true);
  assert.equal(h.button("add-media", h.video.id).disabled, false);
  assert.equal(h.$("saveProject").disabled, false);
  await h.click("remove-media", h.video.id, true, true);
  await h.$("checkMediaLibrary").onclick();
  await h.click("add-media", h.video.id);
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.added, [{ kind: "media", mediaId: h.video.id }]);
  pending.resolve({ available: true, reversible: false });
  await checking;
  assert.equal(h.state.mediaLibraryChecking, false);
  assert.equal(h.state.mediaRemovalReady, false);
  assert.equal(h.button("remove-media", h.video.id).disabled, true);
  assert.equal(h.$("checkMediaLibrary").disabled, false);
  assert.equal(h.$("saveProject").disabled, false);
  await h.click("remove-media", h.video.id, false, true);
  assert.equal(h.errors.length, 1);
  assert.equal(h.requests.length, 1);
  unchanged(h, before);
  h.session.change(value => { value.clips[0].volume = .5; });
  assert.equal(h.session.project.clips[0].volume, .5);
  assert.doesNotThrow(() => core.validateProject(h.session.project, h.media));
  h.state.backendReady = false; h.renderDisabled();
  assert.equal(h.button("remove-media", h.video.id).disabled, true);
  assert.equal(h.$("checkMediaLibrary").disabled, true);
  assert.equal(h.$("playPause").disabled, false, "existing media playback is available even if the host is offline");
  await h.click("remove-media", h.video.id, false, true);
  assert.equal(h.requests.length, 1);
});

test("unsupported hosts and transient check failures recover through the actual recheck button", async () => {
  let response = Object.assign(new Error("舊主機"), { status: 404 });
  const h = harness(() => response instanceof Error ? Promise.reject(response) : response);
  await h.$("checkMediaLibrary").onclick();
  assert.equal(h.state.mediaRemovalReady, false);
  assert.equal(h.state.mediaLibraryChecking, false);
  assert.equal(h.state.mediaLibraryError, "");
  assert.match(h.$("mediaLibraryStatus").textContent, /尚未載入/);
  assert.equal(h.button("remove-media", h.video.id).disabled, true);
  assert.equal(h.$("saveProject").disabled, false);
  response = new Error("網路暫時中斷");
  await h.$("checkMediaLibrary").onclick();
  assert.match(h.$("mediaLibraryStatus").textContent, /網路暫時中斷/);
  assert.equal(h.$("checkMediaLibrary").disabled, false);
  response = { available: true, reversible: true };
  await h.$("checkMediaLibrary").onclick();
  assert.equal(h.state.mediaRemovalReady, true);
  assert.equal(h.state.mediaLibraryError, "");
  assert.equal(h.state.mediaLibraryChecking, false);
  assert.equal(h.button("remove-media", h.video.id).disabled, false);
  assert.equal(h.$("checkMediaLibrary").classList.contains("hidden"), true);
  assert.equal(h.media.size, 3);
  assert.equal(h.$("mediaCount").textContent, "3");
  assert.equal(h.requests.length, 3);
});

test("non-action clicks, unrelated add buttons and literal asset names cannot trigger removal", async () => {
  const h = harness();
  const named = { ...h.video, name: '<img src=x onerror="remove()">' };
  h.media.set(named.id, named); h.renderMedia();
  assert.match(h.$("mediaList").innerHTML, /&lt;img src=x onerror=&quot;remove\(\)&quot;&gt;/);
  assert.ok(!h.$("mediaList").innerHTML.includes('<img src=x onerror="remove()">'));
  await h.$("mediaList").onclick({ target: new Element("article") });
  await h.$("removedMediaList").onclick({ target: new Element("article") });
  await h.click("add-audio", h.video.id, true);
  await h.click("add-video-layer", h.video.id, true);
  assert.deepEqual(h.added, [{ kind: "audio", mediaId: h.video.id }, { kind: "video", mediaId: h.video.id }]);
  assert.equal(h.requests.length, 0);
  assert.equal(h.state.busy, false);
  assert.equal(h.media.size, 3);
});
