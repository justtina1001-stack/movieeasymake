/* Preview camera: viewing state is independent of editing and media playback. */
(function (root) {
  "use strict";
  const ZOOM_LEVELS = Object.freeze([0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4]);
  const MIN_SCALE = 0.05, MAX_SCALE = 4;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  function fitScale(width, height, viewportWidth, viewportHeight) {
    if (![width, height].every(value => Number.isFinite(value) && value > 0) || ![viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value >= 0)) return 0;
    return Math.min(viewportWidth / width, viewportHeight / height);
  }
  function clampPan(pan, width, height, scale, viewportWidth, viewportHeight) {
    const limitX = Math.max(0, (width * scale - viewportWidth) / 2), limitY = Math.max(0, (height * scale - viewportHeight) / 2);
    return { x: clamp(Number.isFinite(pan.x) ? pan.x : 0, -limitX, limitX), y: clamp(Number.isFinite(pan.y) ? pan.y : 0, -limitY, limitY) };
  }
  function zoomAt(view, newScale, anchor, width, height, viewportWidth, viewportHeight) {
    if (!Number.isFinite(newScale) || newScale <= 0) throw new Error("預覽比例需大於零。");
    const point = anchor || { x: viewportWidth / 2, y: viewportHeight / 2 }, previousScale = view.scale > 0 ? view.scale : newScale;
    const ratio = newScale / previousScale;
    const pan = { x: point.x - viewportWidth / 2 - (point.x - viewportWidth / 2 - view.x) * ratio,
      y: point.y - viewportHeight / 2 - (point.y - viewportHeight / 2 - view.y) * ratio };
    return { scale: newScale, ...clampPan(pan, width, height, newScale, viewportWidth, viewportHeight) };
  }
  class PreviewViewportModel {
    constructor(options = {}) {
      this.width = options.width || 1280; this.height = options.height || 720;
      this.viewportWidth = options.viewportWidth || 0; this.viewportHeight = options.viewportHeight || 0;
      this.key = options.key; this.mode = "fit"; this.x = 0; this.y = 0; this.hand = false;
      this.scale = fitScale(this.width, this.height, this.viewportWidth, this.viewportHeight);
    }
    snapshot() {
      return { mode: this.mode, scale: this.scale, x: this.x, y: this.y, hand: this.hand,
        width: this.width, height: this.height, viewportWidth: this.viewportWidth, viewportHeight: this.viewportHeight,
        canPan: this.width * this.scale > this.viewportWidth + 1e-6 || this.height * this.scale > this.viewportHeight + 1e-6 };
    }
    setContent(width, height, key = this.key) {
      if (![width, height].every(value => Number.isFinite(value) && value > 0)) throw new Error("預覽輸出尺寸無效。");
      if (width !== this.width || height !== this.height || key !== this.key) {
        this.width = width; this.height = height; this.key = key; this.hand = false; this.fit();
      }
      return this.snapshot();
    }
    resize(width, height) {
      if (![width, height].every(value => Number.isFinite(value) && value >= 0)) throw new Error("預覽視窗尺寸無效。");
      this.viewportWidth = width; this.viewportHeight = height;
      if (this.mode === "fit") return this.fit();
      Object.assign(this, clampPan(this, this.width, this.height, this.scale, width, height));
      return this.snapshot();
    }
    fit() {
      this.mode = "fit"; this.scale = fitScale(this.width, this.height, this.viewportWidth, this.viewportHeight); this.x = 0; this.y = 0;
      return this.snapshot();
    }
    setScale(scale, anchor) {
      if (!Number.isFinite(scale) || scale <= 0) throw new Error("預覽比例需大於零。");
      Object.assign(this, zoomAt(this, clamp(scale, MIN_SCALE, MAX_SCALE), anchor, this.width, this.height, this.viewportWidth, this.viewportHeight));
      this.mode = "fixed"; return this.snapshot();
    }
    zoomStep(direction) {
      if (direction > 0 && this.scale >= MAX_SCALE || direction < 0 && this.scale <= MIN_SCALE) return this.snapshot();
      const next = direction > 0 ? ZOOM_LEVELS.find(value => value > this.scale + 1e-6) : [...ZOOM_LEVELS].reverse().find(value => value < this.scale - 1e-6);
      return this.setScale(next ?? (direction > 0 ? MAX_SCALE : MIN_SCALE));
    }
    zoomBy(factor, anchor) {
      if (!Number.isFinite(factor) || factor <= 0) throw new Error("預覽縮放倍率無效。");
      if (factor > 1 && this.scale >= MAX_SCALE || factor < 1 && this.scale <= MIN_SCALE) return this.snapshot();
      return this.setScale((this.scale || fitScale(this.width, this.height, this.viewportWidth, this.viewportHeight) || MIN_SCALE) * factor, anchor);
    }
    panBy(dx, dy) {
      if (![dx, dy].every(Number.isFinite)) throw new Error("預覽平移距離無效。");
      Object.assign(this, clampPan({ x: this.x + dx, y: this.y + dy }, this.width, this.height, this.scale, this.viewportWidth, this.viewportHeight));
      return this.snapshot();
    }
    setHand(enabled) { this.hand = Boolean(enabled); return this.snapshot(); }
  }
  function mountPreviewViewport(elements, options = {}) {
    const { stage, canvas, zoomSelect, zoomIn, zoomOut, fitButton, handButton } = elements;
    const win = options.window || root, doc = options.document || stage.ownerDocument || win.document;
    const getStyle = options.getStyle || (node => win.getComputedStyle(node));
    const model = new PreviewViewportModel(), bindings = []; let pan = null, padding = { left: 0, top: 0 };
    const blocked = () => Boolean(options.isBlocked?.());
    function bind(node, name, callback, config) { if (!node?.addEventListener) return; node.addEventListener(name, callback, config); bindings.push([node, name, callback, config]); }
    function viewport() {
      const style = getStyle(stage), number = name => parseFloat(style[name]) || 0;
      padding = { left: number("paddingLeft"), top: number("paddingTop") };
      const rect = stage.getBoundingClientRect();
      return { left: rect.left + padding.left, top: rect.top + padding.top,
        width: Math.max(0, stage.clientWidth - padding.left - number("paddingRight")),
        height: Math.max(0, stage.clientHeight - padding.top - number("paddingBottom")) };
    }
    function render() {
      const view = model.snapshot();
      canvas.style.width = `${view.width * view.scale}px`; canvas.style.height = `${view.height * view.scale}px`;
      canvas.style.left = `${padding.left + view.viewportWidth / 2}px`; canvas.style.top = `${padding.top + view.viewportHeight / 2}px`;
      canvas.style.transform = `translate(-50%, -50%) translate(${view.x}px, ${view.y}px)`;
      stage.classList.toggle("preview-hand-mode", view.hand); stage.classList.toggle("preview-panning", Boolean(pan));
      stage.setAttribute("aria-label", `剪輯預覽，${view.mode === "fit" ? "適合視窗" : `${Math.round(view.scale * 100)}%`}${view.hand ? "，手形平移模式" : ""}`);
      const unavailable = blocked();
      if (zoomSelect) {
        const preset = ZOOM_LEVELS.find(scale => Math.abs(scale - view.scale) < 1e-6), custom = zoomSelect.querySelector?.("[data-preview-custom]");
        if (custom) { custom.textContent = `${Math.round(view.scale * 100)}%`; custom.hidden = view.mode === "fit" || Boolean(preset); }
        zoomSelect.value = view.mode === "fit" ? "fit" : preset ? String(preset) : "custom"; zoomSelect.disabled = unavailable;
      }
      if (zoomIn) zoomIn.disabled = unavailable || view.scale >= MAX_SCALE - 1e-6;
      if (zoomOut) zoomOut.disabled = unavailable || view.scale <= MIN_SCALE + 1e-6;
      if (fitButton) fitButton.disabled = unavailable;
      if (handButton) { handButton.disabled = unavailable; handButton.setAttribute("aria-pressed", String(view.hand)); }
      return view;
    }
    function cancelPan(restore = true) {
      if (!pan) return false;
      const ending = pan; pan = null;
      if (restore) Object.assign(model, { x: ending.x, y: ending.y });
      if (stage.hasPointerCapture?.(ending.pointerId)) stage.releasePointerCapture(ending.pointerId);
      render(); return true;
    }
    function resize() { cancelPan(); const rect = viewport(); model.resize(rect.width, rect.height); return render(); }
    function update(value) {
      if (value.width !== model.width || value.height !== model.height || value.key !== undefined && value.key !== model.key) cancelPan();
      model.setContent(value.width, value.height, value.key); const rect = viewport(); model.resize(rect.width, rect.height); return render();
    }
    function setZoom(scale, anchor) { if (blocked()) return model.snapshot(); cancelPan(false); model.setScale(scale, anchor); return render(); }
    function fit() { if (blocked()) return model.snapshot(); cancelPan(false); model.fit(); return render(); }
    function setHand(enabled) { if (blocked()) return model.snapshot(); cancelPan(false); model.setHand(enabled); return render(); }
    function beginPan(event) {
      if (pan || blocked() || event.isPrimary === false || !(event.button === 1 || event.button === 0 && model.hand)) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const next = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, x: model.x, y: model.y };
      try { stage.setPointerCapture(event.pointerId); } catch { return; }
      pan = next; stage.focus?.({ preventScroll: true }); render();
    }
    function movePan(event) {
      if (!pan || event.pointerId !== pan.pointerId) return;
      event.preventDefault(); event.stopPropagation();
      if (event.buttons === 0) { cancelPan(); return; }
      Object.assign(model, clampPan({ x: pan.x + event.clientX - pan.startX, y: pan.y + event.clientY - pan.startY }, model.width, model.height, model.scale, model.viewportWidth, model.viewportHeight));
      render();
    }
    bind(stage, "pointerdown", beginPan, true);
    bind(stage, "pointermove", movePan);
    bind(stage, "pointerup", event => {
      if (!pan || event.pointerId !== pan.pointerId) return;
      event.preventDefault(); event.stopPropagation();
      Object.assign(model, clampPan({ x: pan.x + event.clientX - pan.startX, y: pan.y + event.clientY - pan.startY }, model.width, model.height, model.scale, model.viewportWidth, model.viewportHeight));
      cancelPan(false);
    });
    for (const name of ["pointercancel", "lostpointercapture"]) bind(stage, name, event => { if (pan?.pointerId === event.pointerId) cancelPan(); });
    bind(stage, "wheel", event => {
      if (!(event.ctrlKey || event.metaKey) || blocked()) return;
      event.preventDefault(); event.stopPropagation(); cancelPan(false);
      const rect = viewport(), units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
      model.zoomBy(Math.exp(-clamp(event.deltaY * units, -1000, 1000) * 0.002), { x: event.clientX - rect.left, y: event.clientY - rect.top }); render();
    }, { passive: false });
    bind(zoomSelect, "change", () => { if (zoomSelect.value === "fit") fit(); else if (zoomSelect.value !== "custom") setZoom(Number(zoomSelect.value)); });
    for (const [node, direction] of [[zoomIn, 1], [zoomOut, -1]]) bind(node, "click", () => { if (blocked()) return; cancelPan(false); model.zoomStep(direction); render(); });
    bind(fitButton, "click", fit); bind(handButton, "click", () => setHand(!model.hand));
    bind(stage, "keydown", event => {
      if (event.target !== stage || blocked() || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "Home" || event.key === "0") { event.preventDefault(); event.stopPropagation(); fit(); return; }
      if (!model.hand || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation(); cancelPan(false);
      const step = event.shiftKey ? 80 : 20;
      model.panBy(event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0,
        event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0); render();
    });
    bind(doc, "keydown", event => {
      if (event.key !== "Escape" || !(pan || model.hand && (event.target === stage || stage.contains?.(event.target)))) return;
      event.preventDefault(); event.stopImmediatePropagation(); cancelPan(); model.setHand(false); render();
    }, true);
    bind(win, "blur", () => cancelPan());
    bind(doc, "visibilitychange", () => { if (doc.hidden) cancelPan(); });
    resize();
    return { model, render, update, resize, setZoom, fit, setHand, cancelPan,
      snapshot: () => model.snapshot(), get isPanning() { return Boolean(pan); }, get isHandMode() { return model.hand; },
      destroy() { cancelPan(); for (const [node, name, callback, config] of bindings) node.removeEventListener(name, callback, config); bindings.length = 0; } };
  }
  const api = { ZOOM_LEVELS, MIN_SCALE, MAX_SCALE, fitScale, clampPan, zoomAt, PreviewViewportModel, mountPreviewViewport };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.H3EditorPreview = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
