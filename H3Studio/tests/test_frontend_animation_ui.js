"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const UI = require("../static/editor_animation_ui.js");

// Exercise the shipped mount/render/event handlers against a small DOM surface.
// The harness only implements parsing, selectors and event delivery.
class Element {
  constructor(document, tag) {
    this.ownerDocument = document; this.tagName = tag; this.children = []; this.parentNode = null;
    this.attributes = new Map(); this.dataset = {}; this.events = new Map(); this.disabled = false; this.hidden = false; this._text = "";
    const names = new Set();
    this.classList = { contains: name => names.has(name), toggle(name, force) { const add = force === undefined ? !names.has(name) : force; add ? names.add(name) : names.delete(name); return add; },
      set(value) { names.clear(); String(value).split(/\s+/).filter(Boolean).forEach(name => names.add(name)); } };
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") this.classList.set(value);
    if (name === "disabled") this.disabled = true;
    if (name === "hidden") this.hidden = true;
    if (name === "value") this.value = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, character) => character.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  append(node) { node.parentNode = this; this.children.push(node); }
  set innerHTML(html) {
    this.children = []; this._text = ""; const stack = [this];
    for (const token of String(html).match(/<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith("</")) { stack.pop(); continue; }
      if (!token.startsWith("<")) { stack.at(-1)._text += token; continue; }
      const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
      const node = this.ownerDocument.createElement(tag);
      for (const attribute of token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) if (attribute.index !== 1) node.setAttribute(attribute[1], attribute[2] ?? "");
      stack.at(-1).append(node);
      if (!["input", "br", "img", "hr"].includes(tag) && !token.endsWith("/>")) stack.push(node);
    }
  }
  matches(selector) {
    if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
    if (selector.startsWith("[")) { const match = selector.match(/^\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]$/); return Boolean(match && this.attributes.has(match[1]) && (match[2] === undefined || this.getAttribute(match[1]) === match[2])); }
    return this.tagName === selector;
  }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  querySelectorAll(selector) {
    const path = selector.trim().split(/\s+/), result = [];
    const matchesPath = node => {
      if (!node.matches(path.at(-1))) return false;
      let ancestor = node.parentNode;
      for (let index = path.length - 2; index >= 0; index--) { while (ancestor && !ancestor.matches(path[index])) ancestor = ancestor.parentNode; if (!ancestor) return false; ancestor = ancestor.parentNode; }
      return true;
    };
    const walk = node => { for (const child of node.children) { if (matchesPath(child)) result.push(child); walk(child); } }; walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, callback) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(callback); }
  dispatch(name, values = {}) {
    const event = { target: this, stopped: false, preventDefault() {}, stopPropagation() { this.stopped = true; }, ...values };
    for (let current = this; current && !event.stopped; current = current.parentNode) for (const callback of current.events.get(name) || []) callback(event);
  }
  click() { if (!this.disabled) this.dispatch("click"); }
  change(value) { if (!this.disabled) { this.value = String(value); this.dispatch("change"); } }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null; }
}
function fixture(changes = {}) {
  const document = { createElement(tag) { return new Element(this, tag); } }, host = document.createElement("section");
  const calls = [], state = { item: { id: "clip" }, kind: "video", length: 4, supported: true, transitionSupported: true, next: { id: "next", name: "下一段影片", length: 2 }, disabled: false, ...changes };
  const editor = UI.mountAnimationEditor(host, {
    onChoose(type, tab) { calls.push({ action: "choose", type, tab }); },
    onDuration(value, tab) { calls.push({ action: "duration", value, tab }); },
    onPreview(tab) { calls.push({ action: "preview", tab }); }, onRecheck() { calls.push({ action: "recheck" }); },
  });
  editor.render(state);
  return { editor, host, state, calls, query: selector => host.querySelector(selector), presets: () => host.querySelectorAll("[data-animation-preset]"),
    render(changes) { Object.assign(state, changes); editor.render(state); },
    choose(type) { host.querySelector(`[data-animation-preset="${type}"]`).click(); },
    tab(name) { host.querySelector(`[data-animation-tab="${name}"]`).click(); } };
}

test("mounted preset controls forward the chosen entrance, exit and transition category", () => {
  const f = fixture(); f.choose("slide_up"); f.tab("out"); f.choose("zoom_out"); f.tab("transition"); f.choose("wipe_left");
  assert.deepEqual(f.calls, [{ action: "choose", type: "slide_up", tab: "in" }, { action: "choose", type: "zoom_out", tab: "out" }, { action: "choose", type: "wipe_left", tab: "transition" }]);
});

test("selected presets expose active state and editable numeric and range duration controls", () => {
  const f = fixture({ item: { id: "clip", animation_in: { type: "fade", duration: 0.8 } } });
  assert.equal(f.query('[data-animation-preset="fade"]').getAttribute("aria-pressed"), "true");
  assert.equal(f.query('[data-animation-preset="none"]').getAttribute("aria-pressed"), "false");
  for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) { assert.equal(f.query(selector).disabled, false); assert.equal(Number(f.query(selector).value), 0.8); }
  f.query("[data-animation-duration]").change(0.7); f.query("[data-animation-range]").change(0.9);
  assert.deepEqual(f.calls, [{ action: "duration", value: 0.7, tab: "in" }, { action: "duration", value: 0.9, tab: "in" }]);
});

for (const selector of ["[data-animation-duration]", "[data-animation-range]"]) test(`a focused ${selector} edit survives status rerenders and sends the entered value on change`, () => {
  const f = fixture({ item: { id: "clip", animation_in: { type: "fade", duration: 0.6 } } });
  const input = f.query(selector); input.focus(); input.value = "0.8";
  f.render({ checking: true }); f.render({ checking: false });
  assert.equal(Number(input.value), 0.8); assert.deepEqual(f.calls, []);
  input.dispatch("change"); assert.deepEqual(f.calls, [{ action: "duration", value: 0.8, tab: "in" }]);
  f.render({ item: { id: "clip", animation_in: { type: "fade", duration: 0.8 } } });
  input.blur(); f.render({ checking: false });
  assert.equal(Number(f.query("[data-animation-duration]").value), 0.8);
  assert.equal(Number(f.query("[data-animation-range]").value), 0.8);
});

test("unfocused duration controls still update to authoritative clip settings", () => {
  const f = fixture({ item: { id: "clip", animation_in: { type: "fade", duration: 0.6 } } });
  f.render({ item: { id: "clip", animation_in: { type: "fade", duration: 1.2 } } });
  assert.equal(Number(f.query("[data-animation-duration]").value), 1.2);
  assert.equal(Number(f.query("[data-animation-range]").value), 1.2);
});

test("no preset disables duration and preview until a real effect is selected", () => {
  const f = fixture();
  assert.equal(f.query("[data-animation-duration]").disabled, true); assert.equal(f.query("[data-animation-preview]").disabled, true);
  f.query("[data-animation-duration]").change(0.8); f.query("[data-animation-preview]").click(); assert.deepEqual(f.calls, []);
  f.render({ item: { id: "clip", animation_in: { type: "fade", duration: 0.5 } } });
  assert.equal(f.query("[data-animation-preview]").disabled, false); f.query("[data-animation-preview]").click();
  assert.deepEqual(f.calls, [{ action: "preview", tab: "in" }]);
});

test("exit presets use departure-specific zoom labels and forward the correct preview tab", () => {
  const f = fixture({ item: { id: "clip", animation_out: { type: "zoom_in", duration: 0.5 } } }); f.tab("out");
  assert.equal(f.query('[data-animation-preset="zoom_in"]').getAttribute("aria-label"), "退場：縮小離開");
  assert.equal(f.query('[data-animation-preset="zoom_out"]').getAttribute("aria-label"), "退場：放大離開");
  f.query("[data-animation-preview]").click(); assert.deepEqual(f.calls, [{ action: "preview", tab: "out" }]);
  assert.match(f.query(".animation-note").textContent, /結尾/);
});

test("entrance and exit duration limits reserve the other edge animation's time", () => {
  const f = fixture({ length: 2, item: { id: "clip", animation_in: { type: "fade", duration: 0.5 }, animation_out: { type: "fade", duration: 1.2 } } });
  assert.equal(f.query("[data-animation-duration]").max, 0.8);
  f.tab("out"); assert.equal(f.query("[data-animation-duration]").max, 1.5);
});

test("text and images expose entrance and exit while hiding the video-only transition tab", () => {
  for (const kind of ["text", "image"]) {
    const f = fixture({ kind: "overlay", item: { id: "layer", kind } });
    assert.equal(f.query('[data-animation-tab="transition"]').hidden, true);
    f.editor.selectTab("transition"); assert.equal(f.editor.currentTab(), "in");
    assert.match(f.query(".animation-note").textContent, /位置關鍵幀/); f.choose("slide_right");
    assert.deepEqual(f.calls, [{ action: "choose", type: "slide_right", tab: "in" }]);
  }
});

test("adjacent video transitions display their target and limit duration to the shorter clip", () => {
  const f = fixture({ length: 7, next: { id: "next", name: "測試第二段", length: 1.25 }, item: { id: "clip", transition_out: { type: "crossfade", duration: 1, next_id: "next" } } });
  f.tab("transition"); assert.equal(f.presets().length, 4);
  assert.equal(f.query("[data-animation-duration]").max, 1.25);
  assert.match(f.query(".animation-note").textContent, /測試第二段/);
  assert.match(f.query(".animation-note").textContent, /剪輯長度與音訊不變/);
  f.query("[data-animation-duration]").change(0.75); f.query("[data-animation-preview]").click();
  assert.deepEqual(f.calls, [{ action: "duration", value: 0.75, tab: "transition" }, { action: "preview", tab: "transition" }]);
});

test("a video without an adjacent target disables transitions and explains how to connect clips", () => {
  const f = fixture({ next: null }); f.tab("transition");
  assert.ok(f.presets().every(node => node.disabled)); assert.equal(f.query("[data-animation-duration]").disabled, true);
  assert.match(f.query(".animation-note").textContent, /前後相接/); f.choose("crossfade"); assert.deepEqual(f.calls, []);
});

test("old backend capabilities disable effects while retaining the selected setting and allowing a recheck", () => {
  const f = fixture({ supported: false, transitionSupported: false, item: { id: "clip", animation_in: { type: "fade", duration: 0.5 } } });
  assert.ok(f.presets().every(node => node.disabled)); assert.equal(f.query('[data-animation-preset="fade"]').getAttribute("aria-pressed"), "true");
  assert.equal(f.query(".animation-support").classList.contains("hidden"), false);
  f.choose("slide_up"); f.query("[data-animation-preview]").click(); f.query("[data-animation-recheck]").click();
  assert.deepEqual(f.calls, [{ action: "recheck" }]);
  f.render({ supported: true }); assert.equal(f.query(".animation-support").classList.contains("hidden"), true); assert.equal(f.query("[data-animation-preview]").disabled, false);
});

test("export and gesture locks disable presets, durations, preview and recheck without resetting the tab", () => {
  const f = fixture({ item: { id: "clip", animation_out: { type: "fade", duration: 0.5 } } }); f.tab("out"); f.render({ disabled: true });
  assert.equal(f.editor.currentTab(), "out"); assert.ok(f.presets().every(node => node.disabled));
  for (const selector of ["[data-animation-duration]", "[data-animation-range]", "[data-animation-preview]", "[data-animation-recheck]"]) assert.equal(f.query(selector).disabled, true);
  f.choose("fade"); f.query("[data-animation-preview]").click(); f.query("[data-animation-recheck]").click(); assert.deepEqual(f.calls, []);
});

test("changing the selected clip resets to entrance while rerendering the same clip keeps its current tab", () => {
  const f = fixture(); f.tab("out"); f.render({ length: 3 }); assert.equal(f.editor.currentTab(), "out");
  f.render({ item: { id: "different" } }); assert.equal(f.editor.currentTab(), "in");
  f.render({ item: null }); assert.equal(f.host.classList.contains("hidden"), true);
});

test("selecting none forwards removal instead of retaining the former animation", () => {
  const f = fixture({ item: { id: "clip", animation_in: { type: "slide_left", duration: 0.5 } } }); f.choose("none");
  assert.deepEqual(f.calls, [{ action: "choose", type: "none", tab: "in" }]);
});

test("short clips retain a usable duration input below the ordinary 50ms preset step", () => {
  const f = fixture({ length: 1 / 24, item: { id: "clip", animation_in: { type: "fade", duration: 1 / 24 } } });
  assert.equal(f.query("[data-animation-duration]").min, 1 / 24); assert.equal(f.query("[data-animation-duration]").max, 1 / 24);
  assert.equal(f.query("[data-animation-duration]").disabled, false);
});
