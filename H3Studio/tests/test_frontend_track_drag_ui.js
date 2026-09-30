"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const UI = require("../static/editor_track_drag_ui.js");
const rect = (left, top, right, bottom) => ({ left, top, right, bottom });
const project = { clips: [{ id: "a" }, { id: "b" }, { id: "c" }], overlays: [{ id: "lower", kind: "video" }, { id: "middle", kind: "text" }, { id: "upper", kind: "video" }] };
const geometry = {
  canvas: rect(100, 80, 1000, 650), viewport: rect(100, 80, 500, 390), newRow: rect(100, 111, 1000, 143),
  overlayRows: [{ id: "upper", rect: rect(100, 143, 1000, 181) }, { id: "middle", rect: rect(100, 181, 1000, 219) }, { id: "lower", rect: rect(100, 219, 1000, 257) }],
  mainRow: rect(100, 263, 1000, 337), mainClips: [{ id: "a", rect: rect(100, 268, 220, 332) }, { id: "b", rect: rect(220, 268, 340, 332) }, { id: "c", rect: rect(340, 268, 460, 332) }],
};
const resolve = (x, y, kind = "video", id = "b", value = project, layout = geometry) => UI.resolveGeometry(layout, { clientX: x, clientY: y }, { kind, id }, value);
test("the empty top row promotes a main clip at the topmost insertion boundary", () => {
  const before = structuredClone(project), feedback = resolve(260, 125);
  assert.equal(feedback.valid, true); assert.deepEqual(feedback.target, { kind: "overlay", index: 3, rowId: "new" });
  assert.deepEqual(feedback.line, { orientation: "horizontal", x: 100, y: 143, width: 900, height: 0 });
  assert.deepEqual(project, before);
});
test("an existing layer dragged to the new top row accounts for its removal", () => {
  assert.equal(resolve(250, 125, "overlay", "lower").target.index, 2);
  assert.equal(resolve(250, 125, "overlay", "upper").target.index, 2);
});
test("narrow upper and lower strips insert separate tracks", () => {
  for (const [visualIndex, id] of ["upper", "middle", "lower"].entries()) {
    const row = geometry.overlayRows[visualIndex], index = project.overlays.findIndex(layer => layer.id === id);
    const above = resolve(250, row.rect.top + 5), below = resolve(250, row.rect.bottom - 5);
    assert.equal(above.target.index, index + 1); assert.equal(below.target.index, index);
    assert.equal(above.line.y, row.rect.top); assert.equal(below.line.y, row.rect.bottom);
  }
});
test("moving a layer up and down preserves array order after removing the source", () => {
  assert.equal(resolve(250, 148, "overlay", "middle").target.index, 2);
  assert.equal(resolve(250, 176, "overlay", "middle").target.index, 1);
  assert.equal(resolve(250, 224, "overlay", "middle").target.index, 1);
  assert.equal(resolve(250, 252, "overlay", "middle").target.index, 0);
});
test("row centers join an existing track, including the source track", () => {
  for (const row of geometry.overlayRows) {
    for (const y of [row.rect.top + 8, (row.rect.top + row.rect.bottom) / 2, row.rect.bottom - 8]) {
      const feedback = resolve(490, y, "overlay", row.id);
      assert.deepEqual(feedback.target, { kind: "overlay", mode: "join", trackId: row.id, rowId: row.id });
      assert.equal(feedback.label, "放入此軌"); assert.equal(feedback.line, null);
      assert.deepEqual(feedback.region, { x: 100, y: row.rect.top, width: 900, height: 38 });
    }
  }
});
test("a main clip dropped in an existing row center shares that track", () => {
  const feedback = resolve(250, 162);
  assert.equal(feedback.valid, true);
  assert.deepEqual(feedback.target, { kind: "overlay", mode: "join", trackId: "upper", rowId: "upper" });
});
test("multi-clip rows resolve their shared identity and count groups rather than clips", () => {
  const grouped = { clips: project.clips, overlays: [
    { id: "lower-a", track_id: "base", kind: "video" }, { id: "lower-b", track_id: "base", kind: "video" },
    { id: "upper-a", track_id: "top", kind: "text" }, { id: "upper-b", track_id: "top", kind: "image" },
  ] };
  const layout = { ...geometry, overlayRows: [
    { id: "top", trackId: "top", layerId: "upper-a", rect: rect(100, 143, 1000, 181) },
    { id: "base", trackId: "base", layerId: "lower-a", rect: rect(100, 181, 1000, 219) },
  ] };
  const join = resolve(250, 162, "overlay", "lower-b", grouped, layout);
  assert.deepEqual(join.target, { kind: "overlay", mode: "join", trackId: "top", rowId: "top" });
  assert.equal(resolve(250, 125, "overlay", "lower-b", grouped, layout).target.index, 2);
  assert.equal(resolve(250, 148, "overlay", "lower-b", grouped, layout).target.index, 2);
  assert.equal(resolve(250, 176, "overlay", "lower-b", grouped, layout).target.index, 1);
  assert.equal(resolve(250, 186, "overlay", "lower-b", grouped, layout).target.index, 1);
  assert.equal(resolve(250, 214, "overlay", "lower-b", grouped, layout).target.index, 0);
});
test("a singleton source track disappears from new-track boundary counts", () => {
  const grouped = { clips: project.clips, overlays: [
    { id: "lower", kind: "video" }, { id: "upper-a", track_id: "top", kind: "video" }, { id: "upper-b", track_id: "top", kind: "video" },
  ] };
  const layout = { ...geometry, overlayRows: [
    { id: "top", trackId: "top", rect: rect(100, 143, 1000, 181) },
    { id: "lower", rect: rect(100, 181, 1000, 219) },
  ] };
  assert.equal(resolve(250, 125, "overlay", "lower", grouped, layout).target.index, 1);
  assert.equal(resolve(250, 148, "overlay", "lower", grouped, layout).target.index, 1);
  assert.equal(resolve(250, 176, "overlay", "lower", grouped, layout).target.index, 0);
  assert.equal(resolve(250, 186, "overlay", "lower", grouped, layout).target.index, 0);
  assert.equal(resolve(250, 214, "overlay", "lower", grouped, layout).target.index, 0);
});
test("legacy row representatives resolve to the track of their member", () => {
  const grouped = { clips: project.clips, overlays: [
    { id: "upper", track_id: "shared", kind: "video" }, { id: "other", track_id: "shared", kind: "video" },
  ] };
  assert.equal(resolve(250, 162, "overlay", "other", grouped).target.trackId, "shared");
});
test("DOM geometry reads a shared-track row once even when it has multiple clips", () => {
  const top = { dataset: { trackId: "top", layerId: "upper-a" }, getBoundingClientRect: () => rect(100, 143, 1000, 181),
    querySelector: () => ({ dataset: { overlayId: "upper-a" } }) };
  const old = { dataset: {}, getBoundingClientRect: () => rect(100, 181, 1000, 219),
    querySelector: () => ({ dataset: { overlayId: "lower" } }) };
  const added = { dataset: { trackDrop: "new" }, getBoundingClientRect: () => geometry.newRow };
  const main = { getBoundingClientRect: () => geometry.mainRow, querySelectorAll: () => geometry.mainClips.map(item => ({ dataset: { clipId: item.id }, getBoundingClientRect: () => item.rect })) };
  const canvas = { getBoundingClientRect: () => geometry.canvas, closest: () => null,
    querySelector: selector => selector === "#clipTrack" ? main : added, querySelectorAll: () => [added, top, old] };
  const measured = UI.geometryOf(canvas);
  assert.equal(measured.overlayRows.length, 2);
  assert.deepEqual(measured.overlayRows[0], { id: "top", trackId: "top", layerId: "upper-a", rect: rect(100, 143, 1000, 181) });
  assert.equal(measured.overlayRows[1].id, "lower");
  assert.deepEqual(measured.mainClips, geometry.mainClips);
});
test("joining highlights the entire target row and clears that highlight on insertion or cancellation", () => {
  const makeElement = () => {
    const classes = new Set();
    const element = { style: {}, setAttribute() {}, append() {}, remove() {},
      classList: { add: (...items) => items.forEach(item => classes.add(item)), remove: (...items) => items.forEach(item => classes.delete(item)),
        contains: item => classes.has(item), toggle: (item, enabled) => enabled ? classes.add(item) : classes.delete(item) } };
    Object.defineProperty(element, "className", { get: () => [...classes].join(" "), set: value => { classes.clear(); value.split(/\s+/).forEach(item => classes.add(item)); } });
    return element;
  };
  const created = [];
  const canvas = { ...makeElement(), ownerDocument: { createElement: () => { const element = makeElement(); created.push(element); return element; } },
    closest: () => null, getBoundingClientRect: () => geometry.canvas };
  const view = UI.mountTrackDrag(canvas), region = created.find(node => node.classList.contains("track-drop-region")),
    line = created.find(node => node.classList.contains("track-drop-indicator")), ghost = created.find(node => node.classList.contains("track-drag-ghost")),
    label = created.find(node => node.classList.contains("track-drop-label"));
  const join = resolve(250, 162);
  view.show(join, { pointer: { clientX: 250, clientY: 162 }, start: 2, duration: 1, zoom: 60 });
  assert.equal(region.classList.contains("hidden"), false); assert.equal(region.classList.contains("valid"), true);
  assert.deepEqual(region.style, { left: "0px", top: "63px", width: "900px", height: "38px" });
  assert.equal(line.classList.contains("hidden"), true); assert.equal(ghost.style.top, "66px");
  assert.equal(ghost.style.height, "32px"); assert.equal(label.textContent, "放入此軌");
  view.show({ ...join, valid: false, reason: "此軌已有片段，請移到空白時間" });
  assert.equal(region.classList.contains("invalid"), true); assert.match(label.textContent, /空白時間/);
  view.show(resolve(250, 148)); assert.equal(region.classList.contains("hidden"), true);
  assert.equal(line.classList.contains("hidden"), false); assert.equal(ghost.style.height, "44px");
  view.show(join); view.hide();
  for (const node of [region, line, ghost, label]) assert.equal(node.classList.contains("hidden"), true);
  view.destroy();
});
test("a video layer can move to a main-track splice boundary", () => {
  const feedback = resolve(240, 300, "overlay", "upper");
  assert.deepEqual(feedback.target, { kind: "video", index: 1 });
  assert.deepEqual(feedback.line, { orientation: "vertical", x: 220, y: 267, width: 0, height: 66 });
  assert.match(feedback.label, /自動拼接/);
});
test("text and image layers stay out of the sequential video track", () => {
  assert.equal(resolve(240, 300, "overlay", "middle").valid, false);
  const withImage = structuredClone(project); withImage.overlays[1].kind = "image";
  assert.equal(resolve(240, 300, "overlay", "middle", withImage).valid, false);
  assert.equal(resolve(240, 224, "overlay", "middle", withImage).valid, true);
});
test("main-track reorder indices refer to the remaining clips", () => {
  assert.equal(resolve(110, 300, "video", "c").target.index, 0);
  assert.equal(resolve(240, 300, "video", "c").target.index, 1);
  assert.equal(resolve(430, 300, "video", "a").target.index, 2);
  assert.equal(resolve(245, 300, "video", "b").target.index, 1);
  assert.equal(resolve(495, 300, "video", "b").target.index, 2);
});
test("dropping an upper video into an empty main track resolves index zero", () => {
  const empty = { clips: [], overlays: [{ id: "upper", kind: "video" }] };
  const layout = { ...geometry, mainClips: [] };
  const feedback = resolve(280, 300, "overlay", "upper", empty, layout);
  assert.deepEqual(feedback.target, { kind: "video", index: 0 }); assert.equal(feedback.line.x, 100);
});
test("audio regions, ruler, outside viewport and missing clips cannot mutate tracks", () => {
  for (const [x, y] of [[250, 370], [250, 90], [99, 200], [501, 200], [250, 391], [250, 79]]) assert.equal(resolve(x, y).valid, false, `${x},${y}`);
  assert.equal(resolve(250, 125, "audio", "b").valid, false);
  assert.equal(resolve(250, 125, "video", "missing").valid, false);
  assert.equal(resolve(NaN, 125).valid, false); assert.equal(resolve(250, Infinity).valid, false);
});
test("autoscroll pressure ramps only inside the viewport and is neutral at its center", () => {
  assert.equal(UI.scrollDelta(100, 100, 500), -16);
  assert.equal(UI.scrollDelta(115, 100, 500), -8);
  assert.equal(UI.scrollDelta(130, 100, 500), 0);
  assert.equal(UI.scrollDelta(300, 100, 500), 0);
  assert.equal(UI.scrollDelta(485, 100, 500), 8);
  assert.equal(UI.scrollDelta(500, 100, 500), 16);
  assert.equal(UI.scrollDelta(99, 100, 500), 0); assert.equal(UI.scrollDelta(501, 100, 500), 0);
});
test("vertical-only layer placement never changes the horizontal scroll position", () => {
  const classList = { add() {}, remove() {}, toggle() {} };
  const createElement = () => ({ classList, style: {}, setAttribute() {}, append() {}, remove() {} });
  const scroll = { scrollLeft: 120, scrollTop: 50, clientWidth: 400, clientHeight: 310,
    getBoundingClientRect: () => rect(100, 80, 500, 390) };
  const canvas = { ownerDocument: { createElement }, classList, append() {}, closest: () => scroll };
  const view = UI.mountTrackDrag(canvas), point = { clientX: 485, clientY: 375 };
  assert.equal(view.autoScroll(point, { horizontal: false }), true);
  assert.equal(scroll.scrollLeft, 120); assert.equal(scroll.scrollTop, 58);
  assert.equal(view.autoScroll(point, { vertical: false }), true);
  assert.equal(scroll.scrollLeft, 128); assert.equal(scroll.scrollTop, 58);
  assert.equal(view.autoScroll(point, { horizontal: false, vertical: false }), false);
  assert.equal(scroll.scrollLeft, 128); assert.equal(scroll.scrollTop, 58);
  assert.equal(view.autoScroll({ clientX: 501, clientY: 375 }), false);
  assert.equal(scroll.scrollLeft, 128); assert.equal(scroll.scrollTop, 58);
  assert.equal(view.autoScroll({ clientX: 115, clientY: 95 }), true);
  assert.equal(scroll.scrollLeft, 120); assert.equal(scroll.scrollTop, 50);
  view.destroy(); assert.equal(view.autoScroll(point), false);
});
