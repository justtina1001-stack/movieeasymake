"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const UI = require("../static/editor_speed_ui.js");

// Mount the shipped editor. This DOM surface implements the selectors and
// pointer events it uses without adding a browser dependency to the project.
class Element {
  constructor(doc, tag) {
    this.ownerDocument = doc; this.tagName = tag.toLowerCase(); this.children = []; this.parentNode = null;
    this.attributes = new Map(); this.dataset = new Proxy({}, { set: (data, name, value) => {
      data[name] = String(value); this.attributes.set(`data-${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`, String(value)); return true;
    } }); this.events = new Map(); this.disabled = false; this.value = ""; this._text = "";
    const classes = new Set();
    this.classList = { add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name), toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : force; if (on) classes.add(name); else classes.delete(name); return on; },
      set: value => { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(name => classes.add(name)); }, text: () => [...classes].join(" ") };
  }
  set className(value) { this.classList.set(value); }
  get className() { return this.classList.text(); }
  set textContent(value) { this._text = String(value); this.children.forEach(child => { child.parentNode = null; }); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(html) {
    this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = "";
    const stack = [this];
    for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith("</")) { stack.pop(); continue; }
      if (!token.startsWith("<")) { stack.at(-1)._text += token; continue; }
      const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
      const node = this.ownerDocument.createElement(tag);
      for (const attribute of token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
        if (attribute.index === 1) continue;
        node.setAttribute(attribute[1], attribute[2] ?? "");
      }
      stack.at(-1).append(node);
      if (!["input", "br", "hr", "img"].includes(tag) && !token.endsWith("/>")) stack.push(node);
    }
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") this.className = value;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    if (name === "value") this.value = String(value);
  }
  getAttribute(name) { return name === "class" ? this.className : this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); } }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; }
  get isConnected() { return this === this.ownerDocument.body || Boolean(this.parentNode?.isConnected); }
  matches(selector) {
    if (selector.includes(":")) { const [base] = selector.split(":"); return this.matches(base) && !this.disabled; }
    if (selector.startsWith("#")) return this.getAttribute("id") === selector.slice(1);
    if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
    if (selector.startsWith("[")) { const match = selector.match(/^\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]$/); return match && this.getAttribute(match[1]) !== null && (match[2] === undefined || this.getAttribute(match[1]) === match[2]); }
    return this.tagName === selector;
  }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) ?? null; }
  querySelectorAll(selector) {
    const selectors = selector.split(",").map(value => value.trim()), result = [];
    const walk = node => { for (const child of node.children) { if (selectors.some(value => child.matches(value))) result.push(child); walk(child); } }; walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, callback) { if (!this.events.has(name)) this.events.set(name, []); this.events.get(name).push(callback); }
  dispatch(name, values = {}) {
    const event = { target: this, button: 0, pointerId: 1, clientX: 0, clientY: 0, preventDefault() {}, stopPropagation() {}, ...values };
    for (const callback of this.events.get(name) || []) callback(event);
    this[`on${name}`]?.(event);
  }
  click() { if (!this.disabled) this.dispatch("click"); }
  focus() { this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 420, height: 218 }; }
  setPointerCapture(id) { this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture(id) { if (this.capture === id) this.capture = null; this.dispatch("lostpointercapture", { pointerId: id }); }
}
function harness(options = {}) {
  const doc = { createElement(tag) { return new Element(this, tag); }, createElementNS(namespace, tag) { return this.createElement(tag); } };
  doc.body = doc.createElement("body"); doc.activeElement = doc.body;
  const host = doc.createElement("section"); doc.body.append(host);
  const query = selector => host.querySelector(selector);
  let actual = options.points || UI.presetPoints("montage", 0, 10), reject = options.reject || false, renderOnReject = options.renderOnReject || false;
  const calls = [];
  let editor;
  function onChangeCurve(points, meta) {
    calls.push({ points, meta });
    if (reject) {
      if (renderOnReject) editor.render({ points: actual, mode: "curve" });
      throw new Error("同軌後方片段超過專案長度限制");
    }
    if (options.onChangeCurve) return options.onChangeCurve(points, meta, editor);
    actual = points; editor.render({ points: actual, mode: "curve" });
  }
  editor = UI.mountSpeedEditor(host, { onChangeCurve, onGestureCommit: points => onChangeCurve(points, { reason: "drag" }),
    onGestureStart() {}, onGesturePreview() {}, onGestureCancel() {}, onSeekSource() {} });
  editor.render({ clipId: "overlay:clip", sourceIn: 0, sourceOut: 10, points: actual, mode: "curve", normalSpeed: 1, sourcePlayhead: 5,
    supported: true, disabled: false, ...options.state });
  return { doc, host, editor, query, calls, get actual() { return actual; },
    reject(value, render = false) { reject = value; renderOnReject = render; },
    preset(id) { query(`[data-speed-preset="${id}"]`).click(); },
    status: () => query("#speedCurveStatus").textContent,
    active: () => host.querySelectorAll("[data-speed-preset]").find(node => node.getAttribute("aria-pressed") === "true")?.dataset.speedPreset,
    path: () => query(".speed-curve-line").getAttribute("d") };
}
function selectPoint(h, index) { h.query("#speedCurveGraph").dispatch("keydown", { key: "Enter", target: h.query(`[data-speed-point="${index}"]`) }); }
function failure(h, path, active = "montage") {
  assert.match(h.status(), /同軌後方片段/); assert.doesNotMatch(h.status(), /已套用|已重設|已加入|已刪除|已設定|速度點：/);
  assert.equal(h.path(), path); assert.equal(h.active(), active);
}

for (const preset of UI.PRESETS) test(`mounted ${preset.id} button applies its real source curve and selection`, () => {
  const h = harness({ points: [], state: { mode: "normal" } }); h.preset(preset.id);
  assert.deepEqual(h.calls[0].points, UI.presetPoints(preset.id, 0, 10)); assert.equal(h.active(), preset.id);
  assert.match(h.status(), new RegExp(`已套用「${preset.label}」`));
});

for (const render of [false, true]) test(`rejected preset keeps the prior curve and selection ${render ? "with" : "without"} authoritative controller render`, () => {
  const h = harness({ reject: true, renderOnReject: render }), before = h.path(); h.preset("hero"); failure(h, before);
  assert.deepEqual(h.actual, UI.presetPoints("montage", 0, 10));
  h.reject(false); h.preset("hero"); assert.equal(h.active(), "hero"); assert.match(h.status(), /已套用「英雄時刻」/);
});

test("preset selection compares visible anchors while retaining hidden source anchors", () => {
  const hidden = [{ time: 0, speed: 2 }, { time: 12, speed: 3 }];
  const h = harness({ points: [hidden[0], ...UI.presetPoints("montage", 2, 8), hidden[1]], state: { sourceIn: 2, sourceOut: 8 } });
  assert.equal(h.active(), "montage"); h.preset("bullet"); assert.equal(h.active(), "bullet");
  assert.deepEqual(h.actual[0], hidden[0]); assert.deepEqual(h.actual.at(-1), hidden[1]);
});

test("custom selects the existing curve without replacing its controls", () => {
  const h = harness(), before = h.path(); h.preset("custom"); assert.equal(h.active(), "custom"); assert.equal(h.path(), before); assert.equal(h.calls.length, 0);
  h.reject(true); h.preset("hero"); failure(h, before, "custom");
});

test("authoritative undo restores preset selection after a custom control edit", () => {
  const h = harness({ points: UI.presetPoints("hero", 0, 10) }), heroPath = h.path(); selectPoint(h, 1);
  const input = h.query("#speedPointValue"); input.value = "1.7"; input.dispatch("change");
  assert.equal(h.active(), "custom"); assert.notEqual(h.path(), heroPath);
  h.editor.render({ points: UI.presetPoints("hero", 0, 10), mode: "curve" });
  assert.equal(h.active(), "hero"); assert.equal(h.path(), heroPath);
});

test("explicit Custom selection survives cursor and unchanged authoritative renders", () => {
  const h = harness({ points: UI.presetPoints("hero", 0, 10) }); h.preset("custom");
  h.editor.render({ sourcePlayhead: 6 }); assert.equal(h.active(), "custom");
  h.editor.render({ points: UI.presetPoints("hero", 0, 10), mode: "curve" }); assert.equal(h.active(), "custom");
});

for (const action of ["reset", "add", "delete", "number", "keyboard"]) test(`rejected ${action} keeps the curve and the validation warning`, () => {
  const h = harness({ reject: true }), before = h.path();
  if (["delete", "number", "keyboard"].includes(action)) selectPoint(h, 1);
  if (action === "reset") h.query("#resetSpeedCurve").click();
  if (action === "add") h.query("#addSpeedPoint").click();
  if (action === "delete") h.query("#deleteSpeedPoint").click();
  if (action === "number") { const input = h.query("#speedPointValue"); input.value = "1.7"; input.dispatch("change"); }
  if (action === "keyboard") h.query("#speedCurveGraph").dispatch("keydown", { key: "ArrowUp", target: h.query('[data-speed-point="1"]') });
  assert.equal(h.calls.length, 1); failure(h, before);
});

test("rejected pointer commit restores its starting controls and retains the warning", () => {
  const previousRAF = globalThis.requestAnimationFrame, previousCancel = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = () => 1; globalThis.cancelAnimationFrame = () => {};
  try {
    const h = harness({ reject: true }), before = h.path(), graph = h.query("#speedCurveGraph"), point = h.query('[data-speed-point="1"]');
    const x = UI.graphX(2, 0, 10), y = UI.graphY(2.5);
    graph.dispatch("pointerdown", { target: point, clientX: x, clientY: y });
    graph.dispatch("pointermove", { target: point, clientX: x, clientY: y + 20 });
    graph.dispatch("pointerup", { target: point, clientX: x, clientY: y + 20 });
    assert.equal(h.calls[0].meta.reason, "drag"); failure(h, before); assert.equal(graph.capture, null);
  } finally { globalThis.requestAnimationFrame = previousRAF; globalThis.cancelAnimationFrame = previousCancel; }
});

test("async rejected preset reports pending first and restores the prior curve without false success", async () => {
  let reject;
  const h = harness({ onChangeCurve: () => new Promise((resolve, fail) => { reject = fail; }) }), before = h.path();
  h.preset("hero"); assert.match(h.status(), /正在套用/); assert.doesNotMatch(h.status(), /已套用/);
  h.editor.render({ sourcePlayhead: 6 }); // A cursor paint is not a project acceptance.
  reject(new Error("同軌後方片段超過專案長度限制"));
  for (let index = 0; index < 4; index++) await Promise.resolve(); failure(h, before);
});

test("async accepted preset announces success after acceptance", async () => {
  let resolve;
  const h = harness({ onChangeCurve: () => new Promise(accept => { resolve = accept; }) }); h.preset("flash_in");
  assert.match(h.status(), /正在套用/); resolve(); for (let index = 0; index < 4; index++) await Promise.resolve();
  assert.match(h.status(), /已套用「閃入」/); assert.equal(h.active(), "flash_in");
});

test("an obsolete async rejection cannot overwrite a later accepted preset", async () => {
  let reject, first = true;
  const h = harness({ onChangeCurve: (points, meta, editor) => {
    if (first) { first = false; return new Promise((resolve, fail) => { reject = fail; }); }
    editor.render({ points, mode: "curve" });
  } });
  h.preset("hero"); h.preset("bullet"); const acceptedPath = h.path(); reject(new Error("old failed request"));
  for (let index = 0; index < 4; index++) await Promise.resolve();
  assert.equal(h.active(), "bullet"); assert.equal(h.path(), acceptedPath); assert.match(h.status(), /已套用「子彈時間」/);
});

test("pending preset rejection cannot leak into another selected clip", async () => {
  let reject;
  const h = harness({ onChangeCurve: () => new Promise((resolve, fail) => { reject = fail; }) }); h.preset("hero");
  h.editor.render({ clipId: "overlay:another", points: UI.presetPoints("flash_out", 0, 10), mode: "curve" });
  const status = h.status(), path = h.path(); assert.equal(h.active(), "flash_out");
  reject(new Error("old clip rejected")); for (let index = 0; index < 4; index++) await Promise.resolve();
  assert.equal(h.status(), status); assert.equal(h.path(), path); assert.equal(h.active(), "flash_out");
});
