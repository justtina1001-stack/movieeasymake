"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const subtitles = require("../static/editor_subtitles.js");
const cue = (extra = {}) => ({ start: 0, end: 1, text: "繁體中文字幕 🎬", ...extra });
const ids = () => { let index = 0; return () => `id_${++index}`; };
const options = (extra = {}) => ({ fps: 24, totalDuration: 8, existingCount: 0, makeId: ids(), ...extra });
const example = "1\n00:00:00,000 --> 00:00:01,500\n你好世界\n第二行\n\n2\n00:00:02,000 --> 00:00:03,000\n下一段 🎬\n";

test("SRT preserves Unicode and multiline content with BOM, CRLF, CR and optional cue numbers", () => {
  const expected = [{ start: 0, end: 1.5, text: "你好世界\n第二行" }, { start: 2, end: 3, text: "下一段 🎬" }];
  assert.deepEqual(subtitles.parseSRT(example), expected);
  assert.deepEqual(subtitles.parseSRT("\uFEFF" + example.replace(/\n/g, "\r\n")), expected);
  assert.deepEqual(subtitles.parseSRT(example.replace(/\n/g, "\r")), expected);
  assert.deepEqual(subtitles.parseSRT(example.replace(/^1\n/, "").replace(/\n2\n/, "\n").replace(/,/g, ".")), expected);
  assert.deepEqual(subtitles.parseSRT("00:00:00,000 --> 00:00:01,000\n001"), [cue({ text: "001" })]);
});

test("malformed cue prevents parsing the whole file and names its cue", () => {
  for (const invalid of ["abc\n00:00:01,000 --> 00:00:02,000\n文字", "2\n00:00:60,000 --> 00:01:02,000\n文字", "2\n00:60:00,000 --> 01:01:00,000\n文字", "2\n00:00:02,000 --> 00:00:02,000\n文字", "2\n00:00:02,000 --> 00:10:00,001\n文字", "2\n00:00:02,000 --> 00:00:03,000\n   ", "2\n00:00:02,00 --> 00:00:03,000\n文字", "2\n00:00:02,000 --> 00:00:03,000 align:start\n文字"]) {
    assert.throws(() => subtitles.parseSRT(example.split("\n\n")[0] + "\n\n" + invalid), /第 2 段字幕/);
  }
  assert.throws(() => subtitles.parseSRT(example.replace("\n\n2", "\n2")), /第 1 段字幕.*空一行/);
  assert.throws(() => subtitles.parseSRT("00:00:00,000 --> 00:00:01,000\n文字\u0000"), /控制字元/);
  assert.throws(() => subtitles.parseSRT(" \n"), /空的/);
  assert.throws(() => subtitles.parseSRT(null), /SRT/);
});

test("cue text limits count Unicode characters and reject overlong or excessive lines", () => {
  assert.equal(subtitles.parseSRT(`00:00:00,000 --> 00:00:01,000\n${"🎬".repeat(500)}`)[0].text.length, 1000);
  for (const text of ["字".repeat(501), Array(11).fill("字").join("\n")]) assert.throws(() => subtitles.parseSRT(`00:00:00,000 --> 00:00:01,000\n${text}`), /500 字、10 行/);
  assert.throws(() => subtitles.parseSRT("字".repeat(subtitles.MAX_FILE_BYTES)), /16 MiB/);
});

test("export sorts chronologically and stably, rounds to milliseconds and reimports without mutation", () => {
  const cues = [cue({ start: 2.12345, end: 3.98765, text: "後段" }), cue({ start: 0.10049, end: 1.10051, text: "前段\r\n第二行" }), cue({ start: 0.10049, end: 1.5, text: "同時段" })];
  const before = JSON.stringify(cues), output = subtitles.stringifySRT(cues);
  assert.match(output, /^1\n00:00:00,100 --> 00:00:01,101\n前段\n第二行/);
  assert.deepEqual(subtitles.parseSRT(output).map(item => item.text), ["前段\n第二行", "同時段", "後段"]);
  assert.equal(subtitles.stringifySRT(subtitles.parseSRT(output)), output);
  assert.equal(JSON.stringify(cues), before);
  for (const invalid of [[], [cue({ start: 0.0001, end: 0.0002 })], [cue({ text: "" })], [cue({ text: "a\n\nb" })], [cue({ start: NaN })], [cue({ end: Infinity })]]) assert.throws(() => subtitles.stringifySRT(invalid));
});

test("import creates sorted editable text on one safe shared track, preserves source and applies defaults", () => {
  const cues = [cue({ start: 2, end: 3 }), cue({ start: 0, end: 1 })], before = JSON.stringify(cues);
  const { trackId, layers } = subtitles.createSubtitleLayers(cues, options());
  assert.equal(trackId, "subtitle_id_1"); assert.equal(subtitles.isSubtitleTrack(trackId), true);
  assert.deepEqual(layers.map(layer => layer.start), [0, 2]);
  assert.ok(layers.every(layer => layer.kind === "text" && layer.track_id === trackId && layer.stroke_width === 0.08 && layer.y === 0.86));
  assert.equal(JSON.stringify(cues), before);
  assert.notEqual(layers[0].id, layers[1].id);
  assert.deepEqual(subtitles.subtitleTracks({ overlays: [...layers, { id: "text", kind: "text", track_id: "other" }] }), [{ id: trackId, count: 2, layers }]);
  assert.deepEqual(subtitles.subtitleTracks({ overlays: [...layers].reverse() })[0].layers.map(layer => layer.start), [0, 2]);
  assert.deepEqual(subtitles.subtitleTracks({ overlays: [{ id: "subtitle_ordinary", kind: "text" }] }), []);
  assert.equal(subtitles.subtitleTracks({ overlays: [...layers, { id: "image", kind: "image", track_id: trackId }] })[0].count, 2);
});

test("whole import refuses capacity, overlap, short cues or missing timeline before allocating identifiers", () => {
  let calls = 0; const makeId = () => { calls++; return "id"; };
  for (const [cues, extra, error] of [
    [[cue(), cue({ start: 2, end: 3 })], { existingCount: 4999 }, /可再匯入 1 句/],
    [[cue(), cue({ start: 0.5, end: 2 })], {}, /重疊/],
    [[cue({ end: 0.04 })], {}, /一個專案影格/],
    [[cue()], { totalDuration: 0 }, /先加入影片/],
    [[cue({ start: 7.5, end: 8.002 })], {}, /超過影片/],
    [[cue()], { fps: 0 }, /影格率/],
    [[cue()], { existingCount: 1.5 }, /字幕句數/],
  ]) assert.throws(() => subtitles.createSubtitleLayers(cues, options({ ...extra, makeId })), error);
  assert.equal(calls, 0);
  assert.equal(subtitles.createSubtitleLayers([cue()], options({ existingCount: 49 })).layers.length, 1);
  assert.throws(() => subtitles.createSubtitleLayers([cue()], options({ makeId: () => "../bad" })), /編號/);
  assert.throws(() => subtitles.createSubtitleLayers([cue(), cue({ start: 2, end: 3 })], options({ makeId: () => "same" })), /重複/);
});

test("timeline accepts only millisecond endpoint tolerance and clamps without hiding zero-frame captions", () => {
  const imported = subtitles.createSubtitleLayers([cue({ start: 7, end: 8.001 })], options());
  assert.equal(imported.layers[0].end, 8);
  assert.equal(subtitles.createSubtitleLayers([cue({ start: -0.001 })], options()).layers[0].start, 0);
  assert.throws(() => subtitles.createSubtitleLayers([cue({ start: -0.0011 })], options()), /起訖時間/);
  assert.throws(() => subtitles.createSubtitleLayers([cue({ start: 7.99, end: 8.001 })], options()), /一個專案影格/);
  assert.throws(() => subtitles.createSubtitleLayers([cue({ start: 8, end: 8.001 })], options()), /超過影片/);
  assert.doesNotThrow(() => subtitles.createSubtitleLayers([cue({ end: 1 / 24 }), cue({ start: 1 / 24, end: 1 })], options()));
});

test("track styling clones project and changes only appearance, keeping content, timing and all animations", () => {
  const { trackId, layers } = subtitles.createSubtitleLayers([cue(), cue({ start: 2, end: 3 })], options());
  Object.assign(layers[0], { animation_in: { type: "fade", duration: 0.2 }, animation_idle: { preset: "float", amount: 0.5 }, position_keyframes: [{ time: 0, x: 0.5, y: 0.8 }] });
  const unrelated = { ...layers[1], id: "other", track_id: "other", text: "標題" }, project = { clips: [], overlays: [...layers, unrelated] }, before = JSON.stringify(project);
  const changed = subtitles.applySubtitleStyle(project, trackId, { ...subtitles.TEXT_PRESETS.yellow, id: "spoof", text: "overwrite", start: 10, end: 20, track_id: "other", animation_in: undefined, font_family: "kai" });
  assert.equal(JSON.stringify(project), before);
  assert.equal(changed.overlays[0].color, "#ffd966"); assert.equal(changed.overlays[0].font_family, "kai");
  for (const key of ["id", "text", "start", "end", "track_id", "animation_in", "animation_idle", "position_keyframes"]) assert.deepEqual(changed.overlays[0][key], layers[0][key]);
  assert.deepEqual(changed.overlays[2], unrelated);
  const image = { id: "image", kind: "image", track_id: trackId, start: 3, end: 4, x: 0.5, y: 0.5 };
  const mixed = subtitles.applySubtitleStyle({ ...project, overlays: [...project.overlays, image] }, trackId, subtitles.TEXT_PRESETS.banner);
  assert.deepEqual(mixed.overlays.at(-1), image);
  assert.notEqual(changed.overlays[0].animation_in, layers[0].animation_in);
  assert.ok(Object.isFrozen(subtitles.TEXT_PRESETS) && Object.isFrozen(subtitles.TEXT_PRESETS.yellow));
  for (const style of [{ font_size: NaN }, { stroke_width: 0.3 }, { color: "red" }, { font_family: "../font" }, { bold: 1 }, { opacity: -1 }]) assert.throws(() => subtitles.applySubtitleStyle(project, trackId, style));
  assert.throws(() => subtitles.applySubtitleStyle(project, "subtitle_missing", {}), /找不到/);
  assert.throws(() => subtitles.applySubtitleStyle({ overlays: [{ track_id: trackId, kind: "image" }] }, trackId, {}), /文字/);
});

test("decoder recognizes UTF-8 and UTF-16 BOMs and explicit Big5, rejecting malformed data", () => {
  const encoder = new TextEncoder(), text = "00:00:00,000 --> 00:00:01,000\n中文";
  assert.equal(subtitles.decodeSubtitleBuffer(encoder.encode(text)), text);
  assert.equal(subtitles.decodeSubtitleBuffer(Uint8Array.from([0xef, 0xbb, 0xbf, ...encoder.encode(text)]).buffer), text);
  for (const bigEndian of [false, true]) {
    const bytes = new Uint8Array(2 + text.length * 2); bytes.set(bigEndian ? [0xfe, 0xff] : [0xff, 0xfe]);
    const view = new DataView(bytes.buffer);
    for (let index = 0; index < text.length; index++) view.setUint16(2 + index * 2, text.charCodeAt(index), !bigEndian);
    assert.equal(subtitles.decodeSubtitleBuffer(bytes), text);
  }
  assert.equal(subtitles.decodeSubtitleBuffer(Uint8Array.from([0xa4, 0xa4, 0xa4, 0xe5]), "big5"), "中文");
  assert.throws(() => subtitles.decodeSubtitleBuffer(Uint8Array.from([0xa4, 0xa4])), /請選 Big5/);
  assert.throws(() => subtitles.decodeSubtitleBuffer(Uint8Array.from([0xff, 0xfe, 0x61])), /UTF-16/);
  assert.throws(() => subtitles.decodeSubtitleBuffer(new Uint8Array(subtitles.MAX_FILE_BYTES + 1)), /16 MiB/);
  assert.throws(() => subtitles.decodeSubtitleBuffer(encoder.encode(text), "latin1"), /UTF-8 或 Big5/);
});
