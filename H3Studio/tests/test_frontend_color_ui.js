"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const color = require("../static/editor_color.js"), ui = require("../static/editor_color_ui.js");
const clone = value => JSON.parse(JSON.stringify(value));
const project = items => ({ id: "project", updated_at: "v1", width: 1280, height: 720, fps: 24, clips: [{ id: "v", media_id: "source", in: 0, out: 10, speed: 1, volume: 1 }], audio_clips: [], overlays: [], color_adjustments: items || [{ id: "grade", start: 1, end: 4, name: "暖色段", temperature: 30 }] });

function matches(node, selector) {
  const parts = selector.trim().split(/\s+/), last = parts.pop();
  const simple = (element, token) => token.startsWith(".") ? element.classes.has(token.slice(1)) : token.startsWith("[") ? (() => { const m = token.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/); return Boolean(m && element.attributes.has(m[1]) && (m[2] === undefined || element.attributes.get(m[1]) === m[2])); })() : element.tagName === token;
  if (!simple(node, last)) return false;
  let parent = node.parentNode;
  while (parts.length) {
    const wanted = parts.pop();
    while (parent && !simple(parent, wanted)) parent = parent.parentNode;
    if (!parent) return false;
    parent = parent.parentNode;
  }
  return true;
}
class Node {
  constructor(doc, tagName) {
    this.ownerDocument = doc; this.tagName = tagName; this.childNodes = []; this.parentNode = null; this.listeners = new Map(); this.attributes = new Map(); this.classes = new Set(); this.dataset = {}; this.value = ""; this.disabled = false; this.checked = false; this.textContent = ""; this.rebuilds = 0;
    this.classList = { toggle: (name, force) => { const state = force ?? !this.classes.has(name); state ? this.classes.add(name) : this.classes.delete(name); return state; }, contains: name => this.classes.has(name) };
  }
  setAttribute(key, value) {
    this.attributes.set(key, String(value));
    if (key === "class") this.classes = new Set(String(value).split(/\s+/));
    if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  appendChild(child) { this.childNodes.push(child); child.parentNode = this; return child; }
  set innerHTML(value) {
    this.rebuilds++; this.childNodes = [];
    const stack = [this], voids = new Set(["input", "br", "hr"]);
    for (const m of String(value).matchAll(/<(\/?)([a-z][\w:-]*)([^>]*)>/gi)) {
      const tag = m[2].toLowerCase();
      if (m[1]) { const index = stack.findLastIndex(n => n.tagName === tag); if (index > 0) stack.length = index; continue; }
      const node = new Node(this.ownerDocument, tag);
      for (const a of m[3].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) node.setAttribute(a[1], a[2] ?? a[3] ?? a[4] ?? "");
      stack.at(-1).appendChild(node);
      if (!voids.has(tag)) stack.push(node);
    }
  }
  querySelectorAll(selector) {
    const selectors = selector.split(","), nodes = [];
    const walk = node => { for (const child of node.childNodes) { if (selectors.some(s => matches(child, s))) nodes.push(child); walk(child); } };
    walk(this); return nodes;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let node = this; node; node = node.parentNode) if (matches(node, selector)) return node; return null; }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  emit(type, fields = {}) { const event = { target: this, type, ...fields }; for (let node = this; node; node = node.parentNode) for (const fn of node.listeners.get(type) || []) fn(event); }
  focus() { this.ownerDocument.activeElement = this; }
}
function harness() {
  const doc = { activeElement: null }, host = new Node(doc, "section"), calls = [];
  const callbacks = Object.fromEntries(["Controls", "Preset", "Reset", "Range", "All", "Add", "Check"].map(name => [`on${name}`, (...args) => calls.push([name, ...args])]));
  const controller = ui.mountInspector(host, callbacks), selected = project().color_adjustments[0];
  const base = { open: true, selected, blocked: false, ready: true, totalDuration: 10, canUseClip: true, checking: false, error: "" };
  controller.render(base);
  const $ = selector => { const node = host.querySelector(selector); assert.ok(node, selector); return node; };
  return { doc, host, controller, base, calls, $ };
}
test("inspector mounts once and preserves focused timing and parameter drafts across renders", () => {
  const h = harness(), start = h.$("[data-color-start]"), number = h.$('[data-color-number="brightness"]');
  start.focus(); start.value = "2.7";
  for (let i = 0; i < 4; i++) h.controller.render(h.base);
  assert.equal(start.value, "2.7"); assert.equal(h.doc.activeElement, start);
  number.focus(); number.value = "-"; h.controller.render({ ...h.base, selected: { ...h.base.selected, brightness: 44 } });
  assert.equal(number.value, "-"); assert.equal(h.host.rebuilds, 1);
  assert.equal(h.$('[data-color-range="brightness"]').value, 44);
});
test("linked slider updates emit one gesture group and distinct gestures get separate history groups", () => {
  const h = harness(), slider = h.$('[data-color-range="contrast"]'), number = h.$('[data-color-number="contrast"]');
  slider.focus(); slider.emit("pointerdown"); slider.value = "10"; slider.emit("input"); slider.value = "20"; slider.emit("input");
  assert.equal(number.value, 20); assert.deepEqual(h.calls.slice(0, 2).map(call => call[1]), [{ contrast: 10 }, { contrast: 20 }]);
  assert.equal(h.calls[0][2], h.calls[1][2]);
  slider.emit("change"); slider.emit("pointerdown"); slider.value = "30"; slider.emit("input");
  assert.notEqual(h.calls[1][2], h.calls[2][2]);
  number.focus(); number.value = "40"; number.emit("change"); assert.deepEqual(h.calls.at(-1), ["Controls", { contrast: 40 }, null]);
});
test("timing change commits the typed value while autosave render has not replaced the focused input", () => {
  const h = harness(), end = h.$("[data-color-end]"); end.focus(); end.value = "6.25";
  h.controller.render(h.base); end.emit("change"); assert.deepEqual(h.calls, [["Range", { end: 6.25 }]]);
  end.value = ""; end.emit("change"); end.value = "601"; end.emit("change"); assert.equal(h.calls.length, 1);
});
test("presets, enable, per-control and whole reset route separate actions", () => {
  const h = harness(); h.$('[data-color-preset="mono"]').emit("click");
  const enabled = h.$("[data-color-enabled]"); enabled.checked = false; enabled.emit("change");
  h.$('[data-color-reset-field="saturation"]').emit("click");
  h.$('[data-color-range="strength"]').emit("dblclick"); h.$("[data-color-reset]").emit("click"); h.$("[data-color-all]").emit("click");
  assert.deepEqual(h.calls, [["Preset", "mono"], ["Controls", { enabled: false }, null], ["Controls", { saturation: 0 }, null], ["Controls", { strength: 100 }, null], ["Reset"], ["All"]]);
});
test("empty editor offers all, selected clip and range; unsupported backend permits only recheck", () => {
  const h = harness(); h.controller.render({ ...h.base, selected: null, canUseClip: false });
  assert.equal(h.$('[data-color-add="clip"]').disabled, true);
  h.$('[data-color-add="clip"]').emit("click"); h.$('[data-color-add="all"]').emit("click"); h.$('[data-color-add="range"]').emit("click");
  assert.deepEqual(h.calls, [["Add", "all"], ["Add", "range"]]);
  h.calls.length = 0; h.controller.render({ ...h.base, ready: false });
  h.$('[data-color-preset="warm"]').emit("click"); h.$('[data-color-range="contrast"]').value = "20"; h.$('[data-color-range="contrast"]').emit("input"); h.$("[data-color-check]").emit("click");
  assert.deepEqual(h.calls, [["Check"]]);
  h.controller.render({ ...h.base, ready: false, checking: true }); h.$("[data-color-check]").emit("click"); assert.equal(h.calls.length, 1);
});
test("blocked controls cannot mutate even through programmatic event dispatch and timing overflow is explained", () => {
  const h = harness(); h.controller.render({ ...h.base, blocked: true, selected: { ...h.base.selected, end: 12 } });
  for (const selector of ['[data-color-preset="warm"]', "[data-color-reset]", "[data-color-all]", '[data-color-add="all"]', "[data-color-check]"]) h.$(selector).emit("click");
  assert.deepEqual(h.calls, []); assert.match(h.$(".color-range-note").textContent, /超出片尾/);
  assert.equal(h.$('[data-color-number="brightness"]').disabled, true);
});
test("range moves preserve length, frame-quantize and snap either boundary without changing the movie", () => {
  const p = project(), original = clone(p);
  const moved = ui.transformRange(p, "grade", "move", 2.08, { maxEnd: 10, snap: [6.1], snapTolerance: 0.06 });
  assert.ok(Math.abs(moved.item.end - 6.1) < 1e-9); assert.ok(Math.abs(moved.item.start - 3.1) < 1e-9);
  assert.equal(moved.item.end - moved.item.start, 3); assert.deepEqual(p, original); assert.deepEqual(moved.project.clips, p.clips);
  const clamped = ui.transformRange(p, "grade", "move", 99, { maxEnd: 10 }); assert.equal(clamped.item.start, 7); assert.equal(clamped.item.end, 10); assert.equal(clamped.clamped, true);
  const oneFrame = ui.transformRange(p, "grade", "left", 20, { maxEnd: 10 }); assert.ok(Math.abs(oneFrame.item.end - oneFrame.item.start - 1 / 24) < 1e-9);
});
test("existing ranges beyond movie remain movable and right extension preserves one output frame", () => {
  const p = project([{ id: "grade", start: 12, end: 15 }]);
  assert.equal(ui.transformRange(p, "grade", "move", 100, { maxEnd: 10 }).item.end, 15);
  const shrunk = ui.transformRange(p, "grade", "right", -99, { maxEnd: 10 }); assert.ok(Math.abs(shrunk.item.end - shrunk.item.start - 1 / 24) < 1e-9);
  assert.throws(() => ui.transformRange(p, "grade", "middle", 1), /無效/); assert.throws(() => ui.transformRange(p, "missing", "left", 1), /找不到/);
});
test("clicking or cancelling an off-frame interval preserves its exact original timing", () => {
  const p = project([{ id: "grade", start: 0.12, end: 4.92 }]);
  for (const mode of ["move", "left", "right"]) {
    const result = ui.transformRange(p, "grade", mode, 0, { snap: [0.125, 4.9] });
    assert.equal(result.changed, false); assert.equal(result.item.start, 0.12); assert.equal(result.item.end, 4.92);
  }
});
test("gesture previews never mutate live session and commit exactly one change with color content", () => {
  const p = project(), edits = [], session = { project: p, change(fn) { const next = clone(this.project); fn(next); edits.push(next); this.project = next; return true; } };
  const transaction = new ui.AdjustmentTransaction(session, "grade", "move", { maxEnd: 10 });
  transaction.update(1); transaction.update(2); assert.equal(session.project.color_adjustments[0].start, 1); assert.equal(edits.length, 0);
  session.project.updated_at = "v2"; assert.equal(transaction.commit(), true); assert.equal(transaction.commit(), false);
  assert.equal(edits.length, 1); assert.equal(session.project.color_adjustments[0].start, 3); assert.equal(session.project.updated_at, "v2");
  assert.deepEqual(session.project.clips, p.clips);
});
test("invalid overlap clears an earlier valid preview so pointer release cannot commit stale work", () => {
  const items = [{ id: "moving", start: 6, end: 8 }, ...Array.from({ length: 4 }, (_, i) => ({ id: `fixed-${i}`, start: 1, end: 4 }))], p = project(items);
  let changes = 0; const session = { project: p, change() { changes++; } }, transaction = new ui.AdjustmentTransaction(session, "moving", "move");
  transaction.update(1); assert.throws(() => transaction.update(-4), /重疊 4/); assert.deepEqual(transaction.preview, p);
  assert.equal(transaction.commit(), false); assert.equal(changes, 0);
});
test("cancellation and concurrent content changes cannot overwrite another edit", () => {
  const p = project(), before = clone(p), session = { project: p, change() { throw new Error("must not write"); } };
  const cancelled = new ui.AdjustmentTransaction(session, "grade", "left"); cancelled.update(1); assert.equal(cancelled.cancel(), false); assert.equal(cancelled.commit(), false); assert.deepEqual(p, before);
  const stale = new ui.AdjustmentTransaction(session, "grade", "right"); stale.update(1); session.project.clips[0].out = 8; assert.throws(() => stale.commit(), /內容已變更/);
});
test("timeline has separate overlapping lanes, safe labels, focusable handles and stable ID attributes", () => {
  const p = project([{ id: "one", start: 0, end: 2, name: '<script>"坏&' }, { id: "two", start: 1, end: 3, enabled: false }, { id: "three", start: 2, end: 4 }]);
  const markup = ui.timelineMarkup(p, { selectedId: "two", zoom: 10 });
  assert.equal(markup.height, 68); assert.match(markup.html, /data-color-id="two"/); assert.match(markup.html, /selected inactive/);
  assert.match(markup.html, /&lt;script&gt;&quot;坏&amp;/); assert.doesNotMatch(markup.html, /<script>/);
  assert.equal([...markup.html.matchAll(/role="slider"/g)].length, 6); assert.match(markup.html, /left:10px;width:20px/);
  const locked = ui.timelineMarkup(p, { locked: true }); assert.equal([...locked.html.matchAll(/tabindex="-1"/g)].length, 6);
  assert.deepEqual(ui.timelineMarkup(project([])), { html: "", height: 0 });
});
