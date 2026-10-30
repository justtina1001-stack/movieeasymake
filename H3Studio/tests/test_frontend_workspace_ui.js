"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/workspace_ui.js"), "utf8");

// Implement browser focus restrictions (hidden, inert, disabled), rather than
// accepting every requested focus and masking a broken focus loop.
function fixture() {
  const observers = [];
  const observed = new Set();
  class Node {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
      this.attributes = new Map(); this.listeners = new Map(); this.disabled = false; this.hidden = false;
      const names = new Set();
      this.classList = { add: (...values) => values.forEach(value => names.add(value)),
        remove: (...values) => values.forEach(value => names.delete(value)), contains: value => names.has(value),
        toggle: (value, force) => { const enabled = force ?? !names.has(value); enabled ? names.add(value) : names.delete(value); return enabled; } };
      this.classNames = names; this.dataset = {};
    }
    get className() { return [...this.classNames].join(" "); }
    set className(value) { this.classNames.clear(); value.split(/\s+/).filter(Boolean).forEach(name => this.classNames.add(name)); }
    get inert() { return this.attributes.has("inert"); }
    set inert(value) { value ? this.attributes.set("inert", "") : this.attributes.delete("inert"); }
    get open() { return this.attributes.has("open"); }
    set open(value) { value ? this.attributes.set("open", "") : this.attributes.delete("open"); }
    get id() { return this.attributes.get("id") || ""; }
    set id(value) { this.attributes.set("id", value); }
    get isConnected() { return this === doc.body || Boolean(this.parentElement?.isConnected); }
    get tabIndex() {
      if (this.attributes.has("tabindex")) return Number(this.attributes.get("tabindex"));
      return ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"].includes(this.tagName) || (this.tagName === "A" && this.attributes.has("href")) ? 0 : -1;
    }
    setAttribute(name, value) { if (name === "class") this.className = String(value); else this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    append(...children) { children.forEach(child => { if (child.parentElement) child.parentElement.children.splice(child.parentElement.children.indexOf(child), 1); child.parentElement = this; this.children.push(child); }); }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    matches(selector) {
      if (selector.includes(",")) return selector.split(",").some(part => this.matches(part.trim()));
      const tag = selector.match(/^[a-z]+/i)?.[0];
      if (tag && this.tagName !== tag.toUpperCase()) return false;
      const id = selector.match(/#([\w-]+)/)?.[1];
      if (id && this.id !== id) return false;
      for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(name)) return false;
      for (const match of selector.matchAll(/\[([\w-]+)(?:(\^?=)['"]?([^\]'"]+)['"]?)?\]/g)) {
        const [, name, operator, wanted] = match, value = this.getAttribute(name);
        if (value === null || (operator === "=" && value !== wanted) || (operator === "^=" && !value.startsWith(wanted))) return false;
      }
      return true;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getClientRects() {
      let child = this;
      for (let node = this; node; child = node, node = node.parentElement) {
        if (node.hidden || node.classList.contains("hidden")) return [];
        if (node.tagName === "DETAILS" && !node.open && child.tagName !== "SUMMARY" && child !== node) return [];
      }
      return this.isConnected ? [{}] : [];
    }
    addEventListener(type, listener) { const listeners = this.listeners.get(type) || []; listeners.push(listener); this.listeners.set(type, listeners); }
    focus() {
      if (!this.getClientRects().length || this.closest("[inert]") || this.disabled) return;
      doc.activeElement = this; doc.dispatch("focusin", { target: this });
    }
    click() {
      if (this.disabled) return;
      const event = { target: this, preventDefault() {}, stopImmediatePropagation() {} };
      for (let node = this; node; node = node.parentElement) for (const listener of node.listeners.get("click") || []) listener(event);
      doc.dispatch("click", event);
    }
  }
  const doc = { activeElement: null, listeners: new Map(),
    createElement: tag => new Node(tag),
    addEventListener(type, listener) { const values = this.listeners.get(type) || []; values.push(listener); this.listeners.set(type, values); },
    dispatch(type, event) { for (const listener of this.listeners.get(type) || []) { listener(event); if (event.stopped) break; } },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    getElementById(id) { return this.querySelector(`#${id}`); },
  };
  doc.body = new Node("body"); doc.activeElement = doc.body;
  const context = vm.createContext({ URLSearchParams, setTimeout,
    MutationObserver: class { constructor(callback) { observers.push(callback); } observe(node) { observed.add(node); } },
  });
  vm.runInContext(source, context);
  function node(tag, attributes = {}, parent = doc.body) { const result = new Node(tag); for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, value); parent?.append(result); return result; }
  const header = node("header"), opener = node("button", {}, header), main = node("main"), preexisting = node("aside");
  preexisting.inert = true;
  function dialog() { return node("div", { class: "modal hidden", role: "dialog" }); }
  function flush() { observers.forEach(observer => observer()); }
  function key(key, shiftKey = false) {
    const event = { key, shiftKey, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
    doc.dispatch("keydown", event); return event;
  }
  return { doc, context, ui: context.H3WorkspaceUI, node, dialog, header, opener, main, preexisting, flush, key, observed };
}

test("opening a modal focuses an eligible control and makes background inert without losing its original state", () => {
  const f = fixture(), modal = f.dialog();
  const disabled = f.node("button", {}, modal); disabled.disabled = true;
  f.node("button", { class: "hidden" }, modal);
  f.node("button", { tabindex: "-1" }, modal);
  const internalInert = f.node("div", { inert: "" }, modal); f.node("button", {}, internalInert);
  const first = f.node("input", {}, modal);
  f.opener.focus(); f.ui.installModalFocus(f.doc);
  modal.classList.remove("hidden"); f.flush();
  assert.strictEqual(f.doc.activeElement, first);
  assert.equal(f.header.inert, true); assert.equal(f.main.inert, true); assert.equal(f.preexisting.inert, true);
  modal.classList.add("hidden"); f.flush();
  assert.strictEqual(f.doc.activeElement, f.opener);
  assert.equal(f.header.inert, false); assert.equal(f.main.inert, false); assert.equal(f.preexisting.inert, true);
});

test("Tab and Shift+Tab wrap at the modal edges and unexpected background focus is returned inside", () => {
  const f = fixture(), modal = f.dialog();
  const first = f.node("button", {}, modal), last = f.node("textarea", {}, modal);
  f.ui.installModalFocus(f.doc); modal.classList.remove("hidden"); f.flush();
  last.focus(); assert.equal(f.key("Tab").prevented, true); assert.strictEqual(f.doc.activeElement, first);
  first.focus(); assert.equal(f.key("Tab", true).prevented, true); assert.strictEqual(f.doc.activeElement, last);
  first.focus(); assert.equal(f.key("Tab").prevented, false);
  f.doc.activeElement = f.opener; f.doc.dispatch("focusin", { target: f.opener });
  assert.strictEqual(f.doc.activeElement, first);
});

test("Escape closes through the dialog's own close action and restores opener and inert state", () => {
  const f = fixture(), modal = f.dialog();
  const close = f.node("button", { "aria-label": "關閉設定" }, modal);
  let clicks = 0; close.addEventListener("click", () => { clicks++; modal.classList.add("hidden"); });
  f.opener.focus(); f.ui.installModalFocus(f.doc); modal.classList.remove("hidden"); f.flush();
  const event = f.key("Escape"); f.flush();
  assert.equal(clicks, 1); assert.equal(event.prevented, true); assert.equal(event.stopped, true);
  assert.strictEqual(f.doc.activeElement, f.opener); assert.equal(f.main.inert, false); assert.equal(f.preexisting.inert, true);
});

test("a modal with no controls remains keyboard focusable and traps Tab on the dialog", () => {
  const f = fixture(), modal = f.dialog();
  f.ui.installModalFocus(f.doc); modal.classList.remove("hidden"); f.flush();
  assert.strictEqual(f.doc.activeElement, modal); assert.equal(modal.getAttribute("tabindex"), "-1");
  assert.equal(f.key("Tab").prevented, true); assert.strictEqual(f.doc.activeElement, modal);
  assert.equal(f.key("Tab", true).prevented, true); assert.strictEqual(f.doc.activeElement, modal);
});

test("stacked dialogs transfer focus and restore the initial page state only after both close", () => {
  const f = fixture(), lower = f.dialog(), upper = f.dialog();
  const lowerButton = f.node("button", {}, lower), upperButton = f.node("button", {}, upper);
  f.opener.focus(); f.ui.installModalFocus(f.doc);
  lower.classList.remove("hidden"); f.flush(); assert.strictEqual(f.doc.activeElement, lowerButton);
  upper.classList.remove("hidden"); f.flush(); assert.strictEqual(f.doc.activeElement, upperButton);
  assert.equal(lower.inert, true); assert.equal(upper.inert, false);
  upper.classList.add("hidden"); f.flush(); assert.strictEqual(f.doc.activeElement, lowerButton);
  assert.equal(f.main.inert, true);
  lower.classList.add("hidden"); f.flush(); assert.strictEqual(f.doc.activeElement, f.opener);
  assert.equal(lower.inert, false); assert.equal(upper.inert, false); assert.equal(f.preexisting.inert, true);
});

test("a modal opened from the collapsed settings menu returns focus to its visible summary", () => {
  const f = fixture(), modal = f.dialog();
  f.node("button", {}, modal);
  f.node("div", { id: "quickWorkspace" });
  const status = f.node("div", { class: "engine-status" }, f.header);
  const trigger = f.node("button", { id: "openConnectionSettings" }, status);
  trigger.addEventListener("click", () => modal.classList.remove("hidden"));
  const start = source.indexOf("function setupHome(");
  vm.runInContext(source.slice(start, source.indexOf("\n  }", start) + 4), f.context);
  f.context.setupHome(f.doc);
  f.ui.installModalFocus(f.doc);
  const menu = status.querySelector("details"), summary = menu.querySelector("summary");
  menu.open = true; trigger.focus(); trigger.click(); f.flush();
  assert.equal(menu.open, false);
  modal.classList.add("hidden"); f.flush();
  assert.strictEqual(f.doc.activeElement, summary);
  assert.equal(summary.getClientRects().length, 1);
});

test("navigation selects the current work area from both dedicated pages and home query parameters", () => {
  const { ui } = fixture();
  for (const [route, query, expected] of [["/editor", "?job=x", "editor"], ["/scripts", "", "scripts"], ["/images", "", "images"],
    ["/", "?workspace=shortfilm", "shortfilm"], ["/", "?tool=voice", "voice"], ["/", "?tool=music", "music"], ["/", "?tool=sfx", "sfx"], ["/", "", "quick"]]) {
    assert.equal(ui.currentSection(route, query), expected);
  }
});

test("a nested native editor picker keeps its own Escape and Tab handling", () => {
  const f = fixture(), modal = f.dialog();
  let parentCloses = 0;
  const close = f.node("button", {class:"modal-close"}, modal); close.addEventListener("click",()=>parentCloses++);
  const picker=f.node("dialog",{open:""},modal), choice=f.node("select",{},picker);
  f.ui.installModalFocus(f.doc); modal.classList.remove("hidden"); f.flush(); choice.focus();
  assert.equal(f.key("Escape").prevented,false);
  assert.equal(f.key("Tab").prevented,false);
  assert.equal(parentCloses,0);
  picker.open=false;
  assert.equal(f.key("Escape").prevented,true);
  assert.equal(parentCloses,1);
});
