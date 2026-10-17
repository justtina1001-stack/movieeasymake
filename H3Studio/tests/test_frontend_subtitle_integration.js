"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), { spawnSync } = require("node:child_process");
const editor = require("../static/editor.js"), subtitles = require("../static/editor_subtitles.js"), effects = require("../static/editor_batch_effects.js");
const project = () => ({ id: "subtitle-project", name: "字幕整合", width: 1280, height: 720, fps: 24, updated_at: "r1", clips: [{ id: "main", media_id: "video", in: 0, out: 8, speed: 1, volume: 1 }], audio_clips: [{ id: "sound", media_id: "audio", in: 0, out: 3, start: 0, track: 0, volume: 0.5, speed: 1, fade_in: 0, fade_out: 0 }], overlays: [] });
const srt = "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n2\n00:00:02,000 --> 00:00:03,000\n第二句\n第二行";
function importResult(value, text = srt, style = subtitles.TEXT_PRESETS.clean) {
  let index = 0;
  return subtitles.createSubtitleLayers(subtitles.parseSRT(text), { fps: value.fps, totalDuration: editor.totalDuration(value), existingCount: value.overlays.length, makeId: () => `new_${++index}`, style });
}
function commitLayers(session, layers) {
  return session.change(next => {
    next.overlays.push(...layers);
    next.overlays = editor.overlayTrackGroups(next).flatMap(track => [...track.clips].sort((left, right) => left.start - right.start));
  });
}
function sessionWithSubtitles(text = srt) {
  const session = new editor.ProjectSession(project()), result = importResult(session.project, text);
  commitLayers(session, result.layers);
  return { session, trackId: result.trackId };
}

test("one SRT import is one editor transaction and undo/redo preserves video and audio timing", () => {
  let changed = 0;
  const original = project(), session = new editor.ProjectSession(original, () => changed++), result = importResult(session.project), before = editor.signature(session.project);
  assert.equal(commitLayers(session, result.layers), true);
  const imported = editor.signature(session.project);
  assert.notEqual(imported, before); assert.equal(changed, 1); assert.equal(session.undoStack.length, 1); assert.equal(session.dirty, true);
  assert.deepEqual(session.project.clips, original.clips); assert.deepEqual(session.project.audio_clips, original.audio_clips); assert.equal(editor.totalDuration(session.project), 8);
  assert.equal(subtitles.subtitleTracks(session.project)[0].count, 2);
  assert.equal(session.travel(true), true); assert.equal(editor.signature(session.project), before); assert.equal(session.dirty, false);
  assert.equal(session.travel(false), true); assert.equal(editor.signature(session.project), imported); assert.equal(changed, 3);
});

test("a malformed later cue and a same-track conflict leave project, undo history and saved signature untouched", () => {
  const { session, trackId } = sessionWithSubtitles(), before = editor.signature(session.project), history = session.undoStack.length, saved = session.savedSignature;
  assert.throws(() => importResult(session.project, srt + "\n\n3\n00:00:04,000 --> 00:00:05,000\n"), /第 3 段字幕/);
  const colliding = { ...session.project.overlays[0], id: "conflict", track_id: trackId, start: 0.5, end: 1.5 };
  assert.throws(() => commitLayers(session, [colliding]), /同一圖層軌.*重疊/);
  assert.equal(editor.signature(session.project), before); assert.equal(session.undoStack.length, history); assert.equal(session.savedSignature, saved);
  assert.equal(subtitles.subtitleTracks(session.project)[0].count, 2);
});

test("subtitle presets use canonical editor payloads and one style change restores with undo", () => {
  const { session, trackId } = sessionWithSubtitles(), before = editor.signature(session.project), cuesBefore = subtitles.stringifySRT(subtitles.subtitleTracks(session.project)[0].layers);
  const styled = subtitles.applySubtitleStyle(session.project, trackId, subtitles.TEXT_PRESETS.yellow);
  session.change(next => { next.overlays = styled.overlays; });
  const canonical = editor.editableContent(session.project), layers = canonical.overlays;
  assert.equal(layers[0].track_id, trackId); assert.equal(layers[0].color, "#ffd966"); assert.equal(layers[0].stroke_width, 0.08);
  assert.equal(layers[0].fill_mode, undefined); assert.equal(layers[0].gradient_start, undefined);
  assert.equal(subtitles.stringifySRT(layers), cuesBefore); assert.equal(session.undoStack.length, 2);
  assert.equal(session.travel(true), true); assert.equal(editor.signature(session.project), before);
});

test("same-track batch animation is one transaction, clamps short cues and keeps subtitle content and opposite effects", () => {
  const text = "1\n00:00:00,000 --> 00:00:01,000\n長字幕\n\n2\n00:00:01,000 --> 00:00:01,125\n短字幕\n\n3\n00:00:02,000 --> 00:00:03,000\n最後字幕";
  const { session, trackId } = sessionWithSubtitles(text);
  session.change(next => {
    next.overlays[0].animation_in = { type: "fade", duration: 0.4 };
    next.overlays[0].animation_out = { type: "slide_left", duration: 0.2 };
    next.overlays[1].animation_out = { type: "fade", duration: 0.1 };
  });
  const before = editor.signature(session.project), beforeProject = editor.clone(session.project), beforeCues = subtitles.stringifySRT(subtitles.subtitleTracks(session.project)[0].layers), history = session.undoStack.length;
  const result = effects.applyTrackEffect(session.project, { kind: "overlay", itemId: session.project.overlays[0].id, tab: "in" });
  assert.equal(result.count, 2); assert.equal(result.clamped, 1);
  session.change(next => { next.overlays = result.project.overlays; next.clips = result.project.clips; });
  assert.equal(session.undoStack.length, history + 1); assert.equal(subtitles.stringifySRT(subtitles.subtitleTracks(session.project)[0].layers), beforeCues);
  assert.deepEqual(session.project.clips, beforeProject.clips); assert.deepEqual(session.project.audio_clips, beforeProject.audio_clips);
  assert.deepEqual(session.project.overlays[1].animation_out, beforeProject.overlays[1].animation_out);
  assert.ok(Math.abs(session.project.overlays[1].animation_in.duration - 0.025) < 1e-12);
  assert.equal(session.project.overlays[2].track_id, trackId);
  session.travel(true); assert.equal(editor.signature(session.project), before);
  session.travel(false); assert.equal(session.project.overlays[2].animation_in.duration, 0.4);
});

test("mixed subtitle track exports and styles text only while editor retains an inserted image", () => {
  const { session, trackId } = sessionWithSubtitles(), originalCues = subtitles.stringifySRT(subtitles.subtitleTracks(session.project)[0].layers);
  const image = { id: "logo", kind: "image", track_id: trackId, media_id: "logo-media", start: 1, end: 2, x: 0.5, y: 0.5, width: 0.2, rotation: 0, opacity: 1 };
  commitLayers(session, [image]);
  const track = subtitles.subtitleTracks(session.project)[0];
  assert.equal(track.count, 2); assert.equal(subtitles.stringifySRT(track.layers), originalCues);
  const styled = subtitles.applySubtitleStyle(session.project, trackId, subtitles.TEXT_PRESETS.banner);
  session.change(next => { next.overlays = styled.overlays; });
  assert.deepEqual(editor.canonicalOverlay(session.project.overlays.find(layer => layer.id === "logo")), image);
  assert.equal(session.project.overlays.filter(layer => layer.kind === "text").every(layer => layer.background === "#101010"), true);
  assert.doesNotThrow(() => editor.validateProject(session.project));
});

const studio = path.resolve(__dirname, ".."), python = path.join(studio, ".venv", "Scripts", "python.exe");
test("existing backend layer validator preserves subtitle track, Unicode, fonts, styles and animations through editor save", { skip: !fs.existsSync(python) }, async () => {
  const { session, trackId } = sessionWithSubtitles();
  session.change(next => {
    const layer = next.overlays[0];
    layer.text = "繁體中文 🎬\n第二行";
    layer.font_family = "installed_0123456789abcdef01234567";
    layer.animation_in = { type: "fade", duration: 0.1 };
    layer.animation_idle = { preset: "float", speed: 1.5, amount: 0.5 };
    layer.position_keyframes = [{ time: 0, x: 0.5, y: 0.86, easing: "ease_in_out" }, { time: 0.8, x: 0.6, y: 0.86, easing: "linear" }];
  });
  const expected = editor.signature(session.project);
  await session.save(async snapshot => {
    const script = "import json,sys\nfrom editor_overlays import validate_layer\np=json.load(sys.stdin)\np['overlays']=[validate_layer(layer,p['fps']) for layer in p['overlays']]\np['updated_at']='r2'\nprint(json.dumps(p,ensure_ascii=False))\n";
    const result = spawnSync(python, ["-c", script], { cwd: studio, input: JSON.stringify(snapshot), encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" }, timeout: 20000 });
    assert.equal(result.status, 0, result.stderr || String(result.error));
    return { project: JSON.parse(result.stdout) };
  });
  assert.equal(session.project.updated_at, "r2"); assert.equal(session.dirty, false); assert.equal(editor.signature(session.project), expected);
  const cue = subtitles.subtitleTracks(session.project)[0].layers[0];
  assert.equal(cue.track_id, trackId); assert.equal(cue.text, "繁體中文 🎬\n第二行"); assert.equal(cue.font_family, "installed_0123456789abcdef01234567");
  assert.deepEqual(cue.animation_idle, { preset: "float", speed: 1.5, amount: 0.5 });
  assert.equal(cue.position_keyframes.length, 2);
});

test("save detects a backend dropping subtitle grouping and leaves its editable draft dirty", async () => {
  const { session } = sessionWithSubtitles(), expected = editor.signature(session.project);
  await assert.rejects(session.save(async snapshot => {
    const lost = editor.clone(snapshot); lost.updated_at = "r2";
    for (const layer of lost.overlays) delete layer.track_id;
    return { project: lost };
  }), /未完整保存/);
  assert.equal(editor.signature(session.project), expected); assert.equal(session.dirty, true); assert.equal(subtitles.subtitleTracks(session.project)[0].count, 2);
});
