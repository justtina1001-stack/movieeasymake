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
  const calls = [], state = { item: { id: "clip" }, kind: "video", length: 4, supported: true, transitionSupported: true, effectsSupported: true, referenceSupported: true, next: { id: "next", name: "下一段影片", length: 2 }, disabled: false, ...changes };
  const editor = UI.mountAnimationEditor(host, {
    onChoose(type, tab) { calls.push({ action: "choose", type, tab }); },
    onDuration(value, tab) { calls.push({ action: "duration", value, tab }); },
    onPreview(tab) { calls.push({ action: "preview", tab }); }, onRecheck() { calls.push({ action: "recheck" }); },
    onIdleChange(field, value) { calls.push({ action: "idle", field, value }); },
    onApplyTrack(tab) { calls.push({ action: "applyTrack", tab }); },
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
  f.tab("transition"); assert.equal(f.presets().length, 10);
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

test("only text layers expose all five idle choices and their distinct speed and intensity controls", () => {
  const f = fixture({ kind: "overlay", item: { id: "title", kind: "text", animation_idle: { preset: "float", speed: 1.5, amount: 0.4 } }, idleSupported: true });
  assert.equal(f.query('[data-animation-tab="idle"]').hidden, false); assert.equal(f.query(".animation-heading h3").textContent, "文字動畫"); f.tab("idle");
  assert.equal(f.presets().length, 5); assert.equal(f.query('[data-animation-preset="float"]').getAttribute("aria-pressed"), "true");
  assert.equal(f.query(".animation-duration").classList.contains("hidden"), true); assert.equal(f.query(".animation-idle-controls").classList.contains("hidden"), false);
  assert.equal(Number(f.query("[data-animation-idle-speed]").value), 1.5); assert.equal(Number(f.query("[data-animation-idle-amount]").value), 40);
  f.query("[data-animation-idle-speed]").change(2); f.query("[data-animation-idle-amount]").change(75); f.choose("none");
  assert.deepEqual(f.calls, [{ action: "idle", field: "speed", value: 2 }, { action: "idle", field: "amount", value: 0.75 }, { action: "choose", type: "none", tab: "idle" }]);
  assert.match(f.query(".animation-note").textContent, /入場結束到退場開始/);
});

test("idle selections remain visible with unsupported capabilities and gesture locks without being overwritten", () => {
  const f = fixture({ kind: "overlay", item: { id: "title", kind: "text", animation_idle: { preset: "pulse", speed: 2, amount: 0.8 } }, idleSupported: false }); f.tab("idle");
  assert.equal(f.query('[data-animation-preset="pulse"]').getAttribute("aria-pressed"), "true"); assert.ok(f.presets().every(node => node.disabled)); assert.equal(f.query(".animation-support").classList.contains("hidden"), false);
  f.choose("float"); f.query("[data-animation-idle-speed]").change(1); assert.deepEqual(f.calls, []);
  f.render({ idleSupported: true, disabled: true }); assert.equal(f.editor.currentTab(), "idle"); assert.equal(f.query("[data-animation-idle-amount]").disabled, true);
  f.render({ disabled: false }); assert.equal(f.query("[data-animation-idle-speed]").disabled, false); assert.equal(f.query(".animation-support").classList.contains("hidden"), true);
});

test("video and image selection hides idle while retained text selections preserve the active tab and focused edits", () => {
  const f = fixture({ kind: "overlay", item: { id: "title", kind: "text", animation_idle: { preset: "shake", speed: 1, amount: 1 } }, idleSupported: true }); f.tab("idle");
  const input = f.query("[data-animation-idle-speed]"); input.focus(); input.value = "1.75"; f.render({ checking: true }); assert.equal(input.value, "1.75"); assert.equal(f.editor.currentTab(), "idle"); input.dispatch("change"); assert.equal(f.calls[0].value, 1.75);
  for (const kind of ["image", "video"]) { f.render({ item: { id: kind, kind } }); assert.equal(f.query('[data-animation-tab="idle"]').hidden, true); f.editor.selectTab("idle"); assert.equal(f.editor.currentTab(), "in"); }
});

test("idle preview disables for none zero intensity or no middle interval and does not borrow edge duration inputs", () => {
  const f = fixture({ kind: "overlay", item: { id: "title", kind: "text" }, idleSupported: true }); f.tab("idle");
  assert.equal(f.query("[data-animation-preview]").disabled, true); assert.equal(f.query("[data-animation-idle-speed]").disabled, true); f.choose("blink"); assert.deepEqual(f.calls, [{ action: "choose", type: "blink", tab: "idle" }]);
  f.render({ item: { id: "title", kind: "text", animation_idle: { preset: "blink", amount: 0 } } }); assert.equal(f.query("[data-animation-preview]").disabled, true);
  f.render({ item: { id: "title", kind: "text", animation_idle: { preset: "blink" }, animation_in: { duration: 2 }, animation_out: { duration: 2 } } }); assert.equal(f.query("[data-animation-preview]").disabled, true);
  f.render({ item: { id: "title", kind: "text", animation_idle: { preset: "blink" }, animation_in: { duration: 0.5 }, animation_out: { duration: 0.5 } } }); assert.equal(f.query("[data-animation-preview]").disabled, false); f.query("[data-animation-duration]").change(2); f.query("[data-animation-preview]").click(); assert.deepEqual(f.calls.at(-1), { action: "preview", tab: "idle" }); assert.equal(f.calls.filter(call => call.action === "duration").length, 0);
});

test("all four advanced transition choices expose labels, durations and preview callbacks", () => {
  for (const [type, label] of [["white_flash", "閃白"], ["blur_dissolve", "模糊溶接"], ["wave_distort", "波浪扭曲"], ["rgb_glitch", "RGB 故障"]]) {
    const f = fixture({ item: { id: "clip", transition_out: { type, duration: 0.75, next_id: "next" } } }); f.tab("transition");
    const selected = f.query(`[data-animation-preset="${type}"]`);
    assert.equal(selected.getAttribute("aria-pressed"), "true"); assert.equal(selected.getAttribute("aria-label"), `轉場：${label}`);
    f.choose(type); f.query("[data-animation-duration]").change(1); f.query("[data-animation-preview]").click();
    assert.deepEqual(f.calls, [{ action: "choose", type, tab: "transition" }, { action: "duration", value: 1, tab: "transition" }, { action: "preview", tab: "transition" }]);
  }
});

test("older servers disable only advanced presets while retaining working basic transitions", () => {
  for (const effectsSupported of [false, undefined, "true"]) {
    const f = fixture({ effectsSupported, referenceSupported: false, item: { id: "clip", transition_out: { type: "crossfade", duration: 0.75, next_id: "next" } } }); f.tab("transition");
    for (const type of UI.TRANSITION_EFFECT_TYPES) { assert.equal(f.query(`[data-animation-preset="${type}"]`).disabled, true); f.choose(type); }
    for (const type of ["none", "crossfade", "wipe_left", "wipe_right"]) assert.equal(f.query(`[data-animation-preset="${type}"]`).disabled, false);
    assert.equal(f.query("[data-animation-preview]").disabled, false); assert.equal(f.query("[data-animation-duration]").disabled, false);
    assert.match(f.query(".animation-support span").textContent, /轉場特效更新/);
    f.choose("wipe_right"); assert.deepEqual(f.calls, [{ action: "choose", type: "wipe_right", tab: "transition" }]);
  }
});

test("unsupported saved FX settings remain selected and cannot change duration or preview until rechecked", () => {
  const setting = { type: "rgb_glitch", duration: 1.25, next_id: "next" };
  const f = fixture({ effectsSupported: false, item: { id: "clip", transition_out: setting } }); f.tab("transition");
  assert.equal(f.query('[data-animation-preset="rgb_glitch"]').getAttribute("aria-pressed"), "true");
  for (const selector of ["[data-animation-duration]", "[data-animation-range]", "[data-animation-preview]"]) assert.equal(f.query(selector).disabled, true);
  assert.equal(Number(f.query("[data-animation-duration]").value), 1.25);
  // Dispatch directly as well: capability checks protect the event handlers, not just the DOM.
  f.query("[data-animation-duration]").dispatch("change"); f.query('[data-animation-preset="rgb_glitch"]').dispatch("click"); f.query("[data-animation-preview]").dispatch("click");
  assert.deepEqual(f.calls, []); assert.deepEqual(setting, { type: "rgb_glitch", duration: 1.25, next_id: "next" });
  f.query("[data-animation-recheck]").click(); assert.deepEqual(f.calls, [{ action: "recheck" }]);
  f.render({ effectsSupported: true }); assert.equal(f.editor.currentTab(), "transition");
  assert.equal(f.query('[data-animation-preset="rgb_glitch"]').disabled, false); assert.equal(f.query("[data-animation-preview]").disabled, false);
  assert.equal(f.query(".animation-support").classList.contains("hidden"), true);
  f.query("[data-animation-duration]").change(1); assert.deepEqual(f.calls.at(-1), { action: "duration", value: 1, tab: "transition" });
});

test("v10 effect support enables the original four but requires a separate strict capability for the two reference presets", () => {
  for (const referenceSupported of [false, undefined, "true"]) {
    const f = fixture({ referenceSupported }); f.tab("transition");
    for (const type of UI.TRANSITION_EFFECT_TYPES.filter(type => !UI.REFERENCE_TRANSITION_TYPES.includes(type))) assert.equal(f.query(`[data-animation-preset="${type}"]`).disabled, false);
    for (const type of UI.REFERENCE_TRANSITION_TYPES) { const button = f.query(`[data-animation-preset="${type}"]`); assert.equal(button.disabled, true); button.dispatch("click"); }
    assert.deepEqual(f.calls, []); assert.match(f.query(".animation-support span").textContent, /放射／橫向模糊/);
  }
});

test("saved reference presets retain their labels and timing while unsupported previews and duration events remain blocked", () => {
  for (const [type, label] of [["radial_zoom", "放射"], ["horizontal_blur", "橫向模糊"]]) {
    const setting = { type, duration: 1.25, next_id: "next" }, f = fixture({ referenceSupported: false, item: { id: "clip", transition_out: setting } }); f.tab("transition");
    const button = f.query(`[data-animation-preset="${type}"]`); assert.equal(button.getAttribute("aria-label"), `轉場：${label}`); assert.equal(button.getAttribute("aria-pressed"), "true");
    for (const selector of ["[data-animation-duration]", "[data-animation-range]", "[data-animation-preview]"]) { assert.equal(f.query(selector).disabled, true); f.query(selector).dispatch(selector.includes("preview") ? "click" : "change"); }
    assert.equal(Number(f.query("[data-animation-duration]").value), 1.25); assert.deepEqual(f.calls, []); assert.deepEqual(setting, { type, duration: 1.25, next_id: "next" });
    f.choose("none"); assert.deepEqual(f.calls, [{ action: "choose", type: "none", tab: "transition" }]);
    f.render({ referenceSupported: true }); assert.equal(f.editor.currentTab(), "transition"); assert.equal(f.query("[data-animation-preview]").disabled, false);
    f.query("[data-animation-duration]").change(0.75); f.query("[data-animation-preview]").click();
    assert.deepEqual(f.calls.slice(1), [{ action: "duration", value: 0.75, tab: "transition" }, { action: "preview", tab: "transition" }]);
  }
});

test("reference support and original effect support are evaluated independently", () => {
  const f = fixture({ effectsSupported: false, referenceSupported: true }); f.tab("transition");
  for (const type of UI.REFERENCE_TRANSITION_TYPES) { assert.equal(f.query(`[data-animation-preset="${type}"]`).disabled, false); f.choose(type); }
  assert.deepEqual(f.calls, [{ action: "choose", type: "radial_zoom", tab: "transition" }, { action: "choose", type: "horizontal_blur", tab: "transition" }]);
});

test("batch action labels expose the selected scope and pass the active tab", () => {
  const main = fixture();
  assert.equal(main.query("[data-animation-apply-track]").textContent, "套用主軌全部影片");
  main.query("[data-animation-apply-track]").click(); main.tab("out"); main.query("[data-animation-apply-track]").click(); main.tab("transition");
  assert.equal(main.query("[data-animation-apply-track]").textContent, "套用主軌全部交界"); main.query("[data-animation-apply-track]").click();
  assert.deepEqual(main.calls, [{ action: "applyTrack", tab: "in" }, { action: "applyTrack", tab: "out" }, { action: "applyTrack", tab: "transition" }]);
  assert.match(main.query(".animation-batch-note").textContent, /短片段會縮短轉場/);
  const text = fixture({ kind: "overlay", item: { id: "title", kind: "text" }, idleSupported: true });
  assert.equal(text.query("[data-animation-apply-track]").textContent, "套用同軌同類圖層"); assert.match(text.query(".animation-batch-note").textContent, /另一端動畫保留/);
  text.tab("idle"); text.query("[data-animation-apply-track]").click(); assert.deepEqual(text.calls, [{ action: "applyTrack", tab: "idle" }]);
  assert.match(text.query(".animation-batch-note").textContent, /保留速度與強度/);
  const upper = fixture({ kind: "overlay", item: { id: "upper", kind: "video" } }); upper.tab("transition"); assert.equal(upper.query("[data-animation-apply-track]").textContent, "套用同軌全部交界");
});

test("none remains a usable batch-removal source after its last local effect was deleted", () => {
  const f = fixture({ item: { id: "clip", transition_out: { type: "crossfade", duration: 0.5, next_id: "next" } } }); f.tab("transition");
  f.choose("none"); f.render({ item: { id: "clip" }, next: null });
  assert.equal(f.query("[data-animation-preview]").disabled, true);
  assert.equal(f.query("[data-animation-apply-track]").disabled, false); f.query("[data-animation-apply-track]").click();
  assert.deepEqual(f.calls, [{ action: "choose", type: "none", tab: "transition" }, { action: "applyTrack", tab: "transition" }]);
  assert.match(f.query(".animation-batch-note").textContent, /「無」可移除同軌全部轉場/);
});

test("active batch transitions require an adjacent target and strict matching backend capabilities", () => {
  for (const changes of [{ next: null }, { transitionSupported: false }, { transitionSupported: "true" }, { effectsSupported: false }, { effectsSupported: "true" }]) {
    const f = fixture({ item: { id: "clip", transition_out: { type: "rgb_glitch", duration: 0.5, next_id: "next" } }, ...changes }); f.tab("transition");
    assert.equal(f.query("[data-animation-apply-track]").disabled, true); f.query("[data-animation-apply-track]").dispatch("click"); assert.deepEqual(f.calls, []);
  }
  const f = fixture({ referenceSupported: false, item: { id: "clip", transition_out: { type: "radial_zoom", duration: 0.5, next_id: "next" } } }); f.tab("transition");
  assert.equal(f.query("[data-animation-apply-track]").disabled, true); f.render({ referenceSupported: true });
  assert.equal(f.query("[data-animation-apply-track]").disabled, false);
});

test("unsupported backends and export locks also block clearing track effects", () => {
  for (const changes of [{ supported: false }, { supported: "true" }, { disabled: true }]) {
    const f = fixture(changes); assert.equal(f.query("[data-animation-apply-track]").disabled, true); f.query("[data-animation-apply-track]").dispatch("click"); assert.deepEqual(f.calls, []);
  }
  const f = fixture({ next: null, transitionSupported: false }); f.tab("transition");
  assert.equal(f.query("[data-animation-apply-track]").disabled, true); f.query("[data-animation-apply-track]").dispatch("click"); assert.deepEqual(f.calls, []);
  f.render({ transitionSupported: true }); assert.equal(f.query("[data-animation-apply-track]").disabled, false);
  f.render({ disabled: true }); assert.equal(f.query("[data-animation-apply-track]").disabled, true);
  const text = fixture({ kind: "overlay", item: { id: "title", kind: "text" }, idleSupported: false }); text.tab("idle");
  assert.equal(text.query("[data-animation-apply-track]").disabled, true); text.query("[data-animation-apply-track]").dispatch("click"); assert.deepEqual(text.calls, []);
});

test("transition categories show precise counts and filter only the existing effects", () => {
  const f = fixture(); assert.equal(f.query(".animation-categories").classList.contains("hidden"), true); f.tab("transition");
  assert.equal(f.query(".animation-categories").classList.contains("hidden"), false);
  assert.equal(f.query('[data-animation-category="all"]').textContent, "全部 9");
  assert.equal(f.query('[data-animation-category="basic"]').textContent, "基礎 3");
  assert.equal(f.query('[data-animation-category="motion"]').textContent, "動感 6");
  f.query('[data-animation-category="basic"]').click(); assert.deepEqual(f.presets().map(node => node.dataset.animationPreset), ["none", "crossfade", "wipe_left", "wipe_right"]);
  assert.equal(f.query('[data-animation-category="basic"]').getAttribute("aria-pressed"), "true"); f.choose("wipe_left");
  f.query('[data-animation-category="motion"]').click(); assert.deepEqual(f.presets().map(node => node.dataset.animationPreset), ["none", ...UI.TRANSITION_EFFECT_TYPES]);
  f.choose("horizontal_blur"); assert.deepEqual(f.calls, [{ action: "choose", type: "wipe_left", tab: "transition" }, { action: "choose", type: "horizontal_blur", tab: "transition" }]);
  f.render({ checking: true }); assert.equal(f.query('[data-animation-category="motion"]').getAttribute("aria-pressed"), "true");
  f.query('[data-animation-category="all"]').click(); assert.equal(f.presets().length, 10);
  f.tab("in"); assert.equal(f.query(".animation-categories").classList.contains("hidden"), true);
});

test("category filters preserve saved settings and all capability guards", () => {
  const setting = { type: "rgb_glitch", duration: 1.25, next_id: "next" };
  const f = fixture({ effectsSupported: false, item: { id: "clip", transition_out: setting } }); f.tab("transition"); f.query('[data-animation-category="basic"]').click();
  assert.equal(f.query("[data-animation-duration]").disabled, true); assert.equal(f.query("[data-animation-apply-track]").disabled, true);
  f.query('[data-animation-category="motion"]').click(); assert.equal(f.query('[data-animation-preset="rgb_glitch"]').getAttribute("aria-pressed"), "true");
  f.query('[data-animation-preset="rgb_glitch"]').dispatch("click"); f.query("[data-animation-apply-track]").dispatch("click"); assert.deepEqual(f.calls, []); assert.deepEqual(f.state.item.transition_out, setting);
  f.render({ item: { id: "new", transition_out: setting } }); f.tab("transition"); assert.equal(f.presets().length, 10); assert.equal(f.query('[data-animation-category="all"]').getAttribute("aria-pressed"), "true");
});
