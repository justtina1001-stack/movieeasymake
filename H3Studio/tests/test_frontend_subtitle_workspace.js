"use strict";

const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs");
const workspace = require("../static/editor_subtitle_workspace.js");
const html = fs.readFileSync(require.resolve("../static/editor.html"), "utf8");
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);

// Keep node identity, ancestor fieldset behavior, and mutation counts observable.
class Node {
  constructor(doc, tagName, comment = false) {
    this.ownerDocument = doc; this.tagName = tagName; this.comment = comment;
    this.parentNode = null; this.childNodes = []; this.attributes = new Map();
    this.dataset = {}; this.disabled = false; this.tabIndex = 0; this.textContent = "";
    this.value = ""; this.selectionStart = 0; this.selectionEnd = 0;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      contains: name => classes.has(name),
      toggle: (name, force) => {
        const wanted = force === undefined ? !classes.has(name) : Boolean(force);
        wanted ? classes.add(name) : classes.delete(name); return wanted;
      },
    };
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name === "class") this.classList.add(...String(value).split(/\s+/).filter(Boolean));
    if (name === "disabled") this.disabled = true;
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null;
  }
  contains(node) { return this === node || this.childNodes.some(child => child.contains(node)); }
  appendChild(node) { return this.insertBefore(node, null); }
  insertBefore(node, before) {
    assert.ok(before === null || before.parentNode === this, "Insertion anchor belongs to destination");
    if (node.parentNode) {
      // Real DOM reparenting can blur an input; repeated render must avoid it.
      if (node.contains(this.ownerDocument.activeElement)) this.ownerDocument.activeElement = null;
      node.parentNode.childNodes.splice(node.parentNode.childNodes.indexOf(node), 1);
    }
    const index = before === null ? this.childNodes.length : this.childNodes.indexOf(before);
    this.childNodes.splice(index, 0, node); node.parentNode = this;
    this.ownerDocument.mutations++; return node;
  }
  focus() { this.ownerDocument.activeElement = this; }
}

function documentFromMarkup() {
  const doc = { ids: new Map(), duplicates: [], mutations: 0, activeElement: null,
    getElementById(id) { return this.ids.get(id) || null; },
    createComment(text) { const node = new Node(this, "#comment", true); node.textContent = text; return node; },
  };
  doc.root = new Node(doc, "#document");
  const stack = [doc.root];
  for (const match of html.matchAll(/<(\/?)([a-z][\w:-]*)([^>]*)>/gi)) {
    const tag = match[2].toLowerCase();
    if (match[1]) {
      const index = stack.findLastIndex(node => node.tagName === tag);
      assert.ok(index > 0, `Balanced closing tag ${tag}`); stack.length = index; continue;
    }
    const node = new Node(doc, tag);
    for (const attribute of match[3].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) {
      node.setAttribute(attribute[1], attribute[2] ?? attribute[3] ?? attribute[4] ?? "");
    }
    if (node.id) {
      if (doc.ids.has(node.id)) doc.duplicates.push(node.id);
      doc.ids.set(node.id, node);
    }
    stack.at(-1).appendChild(node);
    if (!VOID.has(tag) && !/\/\s*$/.test(match[3])) stack.push(node);
  }
  doc.mutations = 0;
  return doc;
}
const visible = node => !node.classList.contains("hidden");
const effectivelyDisabled = node => {
  for (let parent = node; parent; parent = parent.parentNode) {
    if (parent.disabled && (parent === node || parent.tagName === "fieldset")) return true;
  }
  return false;
};
const elements = node => node.childNodes.filter(child => !child.comment);
const view = overrides => ({ open: true, tab: "content", selected: true, index: 1, count: 3, blocked: false, ready: true, ...overrides });
function harness() {
  const doc = documentFromMarkup(), $ = id => {
    const node = doc.getElementById(id); assert.ok(node, `Shipped markup contains ${id}`); return node;
  };
  const text = $("overlayTextEditor"), timing = $("overlayTimingFields");
  const homes = [text, timing].map(node => ({ node, parent: node.parentNode, siblings: elements(node.parentNode) }));
  return { doc, $, text, timing, homes, ui: workspace.mount(doc) };
}

test("shipped markup has unique IDs, one text/timing editor, and workspace script precedes editor initialization", () => {
  const doc = documentFromMarkup();
  assert.deepEqual(doc.duplicates, []);
  assert.equal([...html.matchAll(/\bid="overlayText"/g)].length, 1);
  assert.equal([...html.matchAll(/\bid="overlayStart"/g)].length, 1);
  assert.equal([...html.matchAll(/\bid="overlayEnd"/g)].length, 1);
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g)].map(match => match[1]);
  const position = name => scripts.findIndex(src => src.split("?")[0] === `/static/${name}`);
  assert.ok(position("editor_subtitle_workspace.js") >= 0);
  assert.ok(position("editor_subtitle_workspace.js") < position("editor.js"));
  assert.ok(position("editor_subtitles.js") < position("editor.js"));
  const $ = id => doc.getElementById(id);
  assert.ok($("inspectorPanel").contains($("subtitlePanel")));
  assert.equal($("libraryPanel").contains($("subtitleList")), false);
  assert.ok($("libraryPanel").contains($("openSubtitleWorkspace")));
  assert.ok($("overlayFields").contains($("overlayTimingFields")));
  assert.ok($("overlayTextFields").contains($("overlayTextEditor")));
  assert.ok($("subtitleCueFields").contains($("subtitleTextSlot")));
  assert.ok($("subtitleCueFields").contains($("subtitleTimingSlot")));
});

test("opening and closing move original controls and restore original sibling order without losing content or handlers", () => {
  const h = harness(), textInput = h.$("overlayText"), start = h.$("overlayStart"), end = h.$("overlayEnd");
  const handler = () => {}; textInput.oninput = handler; start.onchange = handler; end.onchange = handler;
  textInput.value = "已校對的字幕"; start.value = "3.12"; end.value = "4.92";
  h.ui.render(view());
  assert.equal(h.text.parentNode, h.$("subtitleTextSlot"));
  assert.equal(h.timing.parentNode, h.$("subtitleTimingSlot"));
  assert.equal(h.$("overlayFields").contains(textInput), false);
  assert.equal(h.$("overlayFields").contains(start), false);
  assert.ok(h.$("subtitleCueFields").contains(textInput));
  h.ui.render(view({ open: false }));
  for (const home of h.homes) {
    assert.equal(home.node.parentNode, home.parent);
    assert.deepEqual(elements(home.parent), home.siblings);
  }
  assert.equal(h.$("overlayText"), textInput);
  assert.equal(textInput.value, "已校對的字幕");
  assert.equal(start.value, "3.12"); assert.equal(end.value, "4.92");
  assert.equal(textInput.oninput, handler); assert.equal(start.onchange, handler); assert.equal(end.onchange, handler);
});

test("repeated renders and tab changes preserve focused text input, cursor, and DOM identity", () => {
  const h = harness(); h.ui.render(view());
  const input = h.$("overlayText"); input.value = "校對進行中"; input.selectionStart = 2; input.selectionEnd = 3; input.focus();
  const mutations = h.doc.mutations;
  for (const tab of ["content", "content", "style", "create", "content"]) h.ui.render(view({ tab }));
  assert.equal(h.doc.mutations, mutations);
  assert.equal(h.doc.activeElement, input);
  assert.equal(h.$("overlayText"), input);
  assert.equal(input.value, "校對進行中");
  assert.equal(input.selectionStart, 2); assert.equal(input.selectionEnd, 3);
  h.ui.render(view({ open: false }));
  const closedMutations = h.doc.mutations;
  h.ui.render(view({ open: false }));
  assert.equal(h.doc.mutations, closedMutations);
});

test("moved controls use the subtitle fieldset lock and navigation disables during blocked or unsupported editing", () => {
  const h = harness(); h.$("overlayFields").disabled = true;
  h.ui.render(view());
  assert.equal(effectivelyDisabled(h.$("overlayText")), false);
  assert.equal(effectivelyDisabled(h.$("overlayStart")), false);
  assert.equal(h.$("previousSubtitle").disabled, false); assert.equal(h.$("nextSubtitle").disabled, false);
  h.ui.render(view({ blocked: true }));
  assert.equal(effectivelyDisabled(h.$("overlayText")), true);
  assert.equal(effectivelyDisabled(h.$("overlayEnd")), true);
  assert.equal(h.$("previousSubtitle").disabled, true); assert.equal(h.$("nextSubtitle").disabled, true);
  h.ui.render(view({ ready: false }));
  assert.equal(effectivelyDisabled(h.$("overlayText")), true);
  h.ui.render(view({ open: false, blocked: false, ready: true }));
  assert.equal(effectivelyDisabled(h.$("overlayText")), true, "Closed controls return to normal overlay fieldset");
});

test("first, last, and sole cue navigation boundaries match the selected sentence", () => {
  const h = harness();
  h.ui.render(view({ index: 0, count: 3 }));
  assert.equal(h.$("previousSubtitle").disabled, true); assert.equal(h.$("nextSubtitle").disabled, false);
  assert.equal(h.$("selectedSubtitleLabel").textContent, "第 1 / 3 句");
  h.ui.render(view({ index: 2, count: 3 }));
  assert.equal(h.$("previousSubtitle").disabled, false); assert.equal(h.$("nextSubtitle").disabled, true);
  assert.equal(h.$("selectedSubtitleLabel").textContent, "第 3 / 3 句");
  h.ui.render(view({ index: 0, count: 1 }));
  assert.equal(h.$("previousSubtitle").disabled, true); assert.equal(h.$("nextSubtitle").disabled, true);
});

test("no selected subtitle shows guidance and cannot edit or navigate a stale layer", () => {
  const h = harness(); h.ui.render(view({ selected: false, index: -1, count: 0 }));
  assert.equal(visible(h.$("subtitleCueEditor")), false);
  assert.equal(visible(h.$("subtitleCueEmpty")), true);
  assert.equal(visible(h.$("subtitleStyleEmpty")), true);
  assert.equal(h.$("subtitleCueFields").disabled, true);
  assert.equal(h.$("previousSubtitle").disabled, true); assert.equal(h.$("nextSubtitle").disabled, true);
  assert.equal(h.$("selectedSubtitleLabel").textContent, "選取字幕");
  h.ui.render(view());
  assert.equal(visible(h.$("subtitleCueEditor")), true);
  assert.equal(visible(h.$("subtitleCueEmpty")), false);
  assert.equal(visible(h.$("subtitleStyleEmpty")), false);
});

test("workspace tabs expose one selected pane and restore normal inspector semantics on close", () => {
  const h = harness();
  for (const tab of ["content", "style", "create"]) {
    h.ui.render(view({ tab }));
    for (const name of ["content", "style", "create"]) {
      assert.equal(h.$(`subtitleTab-${name}`).getAttribute("aria-selected"), String(name === tab));
      assert.equal(h.$(`subtitleTab-${name}`).tabIndex, name === tab ? 0 : -1);
      assert.equal(visible(h.$(`subtitlePane-${name}`)), name === tab);
    }
    assert.equal(visible(h.$("showPositionAnimation")), tab === "style");
    assert.equal(h.$("inspectorPanel").getAttribute("aria-label"), "字幕編輯");
    assert.equal(h.$("openSubtitleWorkspace").getAttribute("aria-expanded"), "true");
    assert.equal(h.$("editorShell").classList.contains("subtitle-workspace-open"), true);
  }
  h.ui.render(view({ tab: "unexpected" }));
  assert.equal(h.$("inspectorPanel").dataset.subtitleTab, "content");
  h.ui.render(view({ open: false }));
  assert.equal(h.$("inspectorPanel").getAttribute("aria-label"), "片段設定");
  assert.equal(h.$("inspectorTitle").textContent, "片段設定");
  assert.equal(h.$("openSubtitleWorkspace").getAttribute("aria-expanded"), "false");
  assert.equal(visible(h.$("subtitlePanel")), false);
  assert.equal(visible(h.$("closeSubtitleWorkspace")), false);
  assert.equal(h.$("editorShell").classList.contains("subtitle-workspace-open"), false);
});
