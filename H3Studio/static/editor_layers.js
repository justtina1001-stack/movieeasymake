/* Shared fade math and bounded, streaming video-layer preview. No media decoding in JS. */
(function (root) {
  "use strict";
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const speedOf = item => item.speed === undefined ? 1 : Number(item.speed);
  const speedMath = typeof module !== "undefined" && module.exports ? require("./editor_speed.js") : root.H3EditorSpeed;
  function fadeEnvelope(item, localTime, length) {
    if (localTime < 0 || localTime >= length) return 0;
    const fadeIn = item.fade_in ?? 0, fadeOut = item.fade_out ?? 0;
    return Math.max(0, Math.min(1, fadeIn > 0 ? localTime / fadeIn : 1, fadeOut > 0 ? (length - localTime) / fadeOut : 1));
  }
  function clampFades(item, length) {
    const fadeIn = item.fade_in ?? 0, fadeOut = item.fade_out ?? 0, sum = fadeIn + fadeOut;
    if (sum > length && sum > 0) { item.fade_in = fadeIn * length / sum; item.fade_out = fadeOut * length / sum; }
    return item;
  }
  class VideoLayersController {
    constructor(makeElement, options = {}) {
      this.makeElement = makeElement; this.resolveUrl = options.resolveUrl || (layer => layer.url);
      this.onError = options.onError || (() => {}); this.context = options.context || null;
      this.slots = new Map(); this.audioGraphs = new WeakMap();
    }
    setContext(context) { this.context = context; }
    audio(slot) {
      if (!this.context || slot.gain) return;
      let graph = this.audioGraphs.get(slot.element);
      if (!graph) { graph = { source: this.context.createMediaElementSource(slot.element), gain: this.context.createGain() }; this.audioGraphs.set(slot.element, graph); }
      graph.source.connect(graph.gain); graph.gain.connect(this.context.destination); slot.source = graph.source; slot.gain = graph.gain;
    }
    sync(layers, time, playing) {
      const active = layers.filter(layer => layer.kind === "video" && layer.start <= time && time < layer.end);
      if (active.length > 3) { this.pause(); this.onError(new Error("同一時間最多預覽 3 個影片圖層。")); return false; }
      const keep = new Set(active.map(layer => layer.id));
      for (const id of [...this.slots.keys()]) if (!keep.has(id)) this.release(id);
      let ready = true;
      for (const layer of active) {
        const element = this.makeElement(layer); if (!element) { ready = false; continue; }
        let slot = this.slots.get(layer.id);
        if (slot && slot.element !== element) { this.release(layer.id); slot = null; }
        if (!slot) {
          slot = { element, key: null, url: null, target: 0, assigned: false, waitingSeek: false, stalled: false, serial: 0, failed: false, gain: null, source: null };
          element.preload = "auto"; element.playsInline = true; element.preservesPitch = true;
          element.addEventListener("seeked", () => { if (this.slots.get(layer.id) === slot && Math.abs(element.currentTime - slot.target) < 0.005) slot.waitingSeek = false; });
          element.addEventListener("waiting", () => { if (this.slots.get(layer.id) === slot) slot.stalled = true; });
          for (const event of ["canplay", "playing", "seeked"]) element.addEventListener(event, () => { if (this.slots.get(layer.id) === slot && element.readyState >= 3) slot.stalled = false; });
          element.addEventListener("error", () => { if (this.slots.get(layer.id) !== slot || slot.failed) return; slot.failed = true; this.onError(new Error("影片圖層無法預覽，請檢查素材或重試。"), layer, slot.url); });
          this.slots.set(layer.id, slot);
        }
        const wantedUrl = this.resolveUrl(layer), key = [layer.media_id, layer.in, layer.out, layer.start, speedOf(layer), JSON.stringify(speedMath.canonicalSpeedCurve(layer))].join("|");
        // Newly completed proxies are picked up when paused, not halfway through playback.
        if (slot.url !== wantedUrl && (!playing || !slot.url || slot.failed)) {
          slot.serial++; element.pause(); slot.url = wantedUrl; slot.assigned = false; slot.waitingSeek = false; slot.failed = false; slot.stalled = false;
          element.src = wantedUrl; element.load();
        }
        if (key !== slot.key) { slot.key = key; slot.assigned = false; slot.waitingSeek = false; }
        const target = speedMath.sourceAt(layer, Math.max(0, time - layer.start));
        this.audio(slot); element.playbackRate = speedMath.speedAtSource(layer, target); element.preservesPitch = true;
        const gain = layer.volume * fadeEnvelope(layer, time - layer.start, layer.end - layer.start);
        if (slot.gain) { slot.gain.gain.value = gain; element.volume = 1; } else element.volume = Math.min(1, gain);
        if (slot.failed) { ready = false; continue; }
        if (element.readyState >= 1 && !slot.waitingSeek) {
          const tolerance = slot.assigned && playing ? 0.10 : 0.003;
          if (!slot.assigned || Math.abs(element.currentTime - target) > tolerance) {
            slot.assigned = true; slot.target = clamp(target, 0, Number.isFinite(element.duration) ? Math.max(0, element.duration - 0.00001) : target);
            if (Math.abs(element.currentTime - slot.target) > 0.003) { slot.serial++; element.pause(); slot.waitingSeek = true; element.currentTime = slot.target; }
          }
        }
        if (element.readyState < 2 || !slot.assigned || slot.waitingSeek || element.seeking || slot.stalled) ready = false;
      }
      if (!ready || !playing) { this.pause(); return ready; }
      for (const layer of active) {
        const slot = this.slots.get(layer.id); if (!slot || !slot.element.paused) continue;
        const serial = ++slot.serial;
        Promise.resolve(slot.element.play()).catch(error => { if (slot.serial === serial && this.slots.get(layer.id) === slot) this.onError(error, layer, slot.url); });
      }
      return true;
    }
    pause() { for (const slot of this.slots.values()) { slot.serial++; slot.element.pause(); } }
    seek() { this.pause(); for (const slot of this.slots.values()) { slot.assigned = false; slot.waitingSeek = false; slot.stalled = false; if (slot.failed) { slot.failed = false; slot.element.load(); } } }
    release(id) {
      const slot = this.slots.get(id); if (!slot) return;
      this.slots.delete(id); slot.serial++; slot.element.pause(); slot.element.removeAttribute("src"); slot.element.load(); slot.source?.disconnect(); slot.gain?.disconnect();
    }
    clear() { for (const id of [...this.slots.keys()]) this.release(id); }
  }
  const core = { fadeEnvelope, clampFades, VideoLayersController };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorLayers = core;
})(typeof window !== "undefined" ? window : globalThis);
