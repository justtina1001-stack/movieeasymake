/* Bounded muted transition sources. Audio stays on the editor's normal transport. */
(function (root) {
  "use strict";
  const speedMath = typeof module !== "undefined" && module.exports ? require("./editor_speed.js") : root.H3EditorSpeed;
  const animationMath = typeof module !== "undefined" && module.exports ? require("./editor_animations.js") : root.H3EditorAnimations;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  class TransitionPreviewController {
    constructor(makeElement, options = {}) {
      this.makeElement = makeElement; this.resolveUrl = options.resolveUrl || (clip => clip.url);
      this.onError = options.onError || (() => {}); this.preloadSeconds = options.preloadSeconds ?? 0.25;
      this.maxPairs = 4; this.slots = new Map(); this.pairs = new Map();
    }
    get(id) {
      const from = this.slots.get(`${id}:from`), to = this.slots.get(`${id}:to`);
      return from && to ? { from: from.element, to: to.element } : null;
    }
    createSlot(pair, role) {
      const element = this.makeElement(pair, role); if (!element) return null;
      const key = `${pair.id}:${role}`, slot = { element, key: null, url: null, target: 0, assigned: false, waitingSeek: false, stalled: false, failed: false, serial: 0, pair, role };
      element.muted = true; element.volume = 0; element.preload = "auto"; element.playsInline = true; element.preservesPitch = true;
      const current = () => this.slots.get(key) === slot;
      element.addEventListener("seeked", () => { if (current() && Math.abs(element.currentTime - slot.target) < 0.005) slot.waitingSeek = false; });
      element.addEventListener("waiting", () => { if (current()) slot.stalled = true; });
      for (const event of ["canplay", "playing", "seeked"]) element.addEventListener(event, () => { if (current() && element.readyState >= 3) slot.stalled = false; });
      element.addEventListener("error", () => {
        if (!current() || slot.failed) return; slot.failed = true;
        this.onError(new Error("轉場影片無法預覽，請檢查素材或重試。"), slot.pair[slot.role], slot.url);
      });
      this.slots.set(key, slot); return slot;
    }
    sync(pairs, time, playing, fps = 24) {
      const active = pairs.filter(pair => pair.start <= time && time < pair.end), activeIds = new Set(active.map(pair => pair.id));
      if (active.length > this.maxPairs) { this.pause(); this.onError(new Error("同一時間最多預覽 4 組影片轉場。")); return false; }
      const nearby = pairs.filter(pair => !activeIds.has(pair.id) && pair.start > time && pair.start - time <= this.preloadSeconds).sort((a, b) => a.start - b.start);
      const selected = [...active, ...nearby.slice(0, this.maxPairs - active.length)], keep = new Set(selected.map(pair => pair.id));
      for (const key of [...this.slots.keys()]) if (!keep.has(this.slots.get(key).pair.id)) this.releaseSlot(key);
      this.pairs = new Map(selected.map(pair => [pair.id, pair]));
      let ready = true; const wantsPlay = [];
      for (const pair of selected) {
        const isActive = activeIds.has(pair.id), visual = animationMath.transitionStateAt(pair, isActive ? time : pair.start, Number.isFinite(fps) && fps > 0 ? fps : 24);
        for (const role of ["from", "to"]) {
          const clip = pair[role], key = `${pair.id}:${role}`;
          let slot = this.slots.get(key); if (!slot) slot = this.createSlot(pair, role);
          if (!slot) { if (isActive) ready = false; continue; }
          slot.pair = pair;
          const element = slot.element, wantedUrl = this.resolveUrl(clip), clipKey = [clip.media_id, clip.in, clip.out, clip.speed ?? 1, JSON.stringify(speedMath.canonicalSpeedCurve(clip)), role === "from" ? pair.fromStart : pair.toStart].join("|");
          // Keep a currently playing source stable when its proxy completes.
          if (slot.url !== wantedUrl && (!playing || !isActive || !slot.url || slot.failed)) {
            slot.serial++; element.pause(); slot.url = wantedUrl; slot.assigned = false; slot.waitingSeek = false; slot.failed = false; slot.stalled = false;
            element.src = wantedUrl; element.load();
          }
          if (slot.key !== clipKey) { slot.key = clipKey; slot.assigned = false; slot.waitingSeek = false; }
          const local = role === "from" ? visual.fromLocal : visual.toLocal, held = role === "from" ? visual.fromHeld : visual.toHeld;
          const source = speedMath.sourceAt(clip, local);
          element.playbackRate = speedMath.speedAtSource(clip, source); element.muted = true; element.volume = 0;
          if (slot.failed) { if (isActive) ready = false; continue; }
          if (element.readyState >= 1 && !slot.waitingSeek) {
            const target = clamp(source, 0, Number.isFinite(element.duration) ? Math.max(0, element.duration - 0.00001) : source);
            const tolerance = slot.assigned && playing && isActive && !held ? 0.05 : 0.004;
            if (!slot.assigned || Math.abs(element.currentTime - target) > tolerance) {
              slot.assigned = true; slot.target = target;
              if (Math.abs(element.currentTime - target) > 0.004) { slot.serial++; element.pause(); slot.waitingSeek = true; element.currentTime = target; }
            }
          }
          const available = element.readyState >= 2 && slot.assigned && !slot.waitingSeek && !element.seeking && !slot.stalled;
          if (isActive && !available) ready = false;
          wantsPlay.push({ slot, shouldPlay: isActive && playing && !held });
        }
      }
      if (!ready || !playing) { this.pause(); return ready; }
      for (const { slot, shouldPlay } of wantsPlay) {
        if (!shouldPlay) { slot.serial++; slot.element.pause(); continue; }
        if (!slot.element.paused) continue;
        const serial = ++slot.serial, key = `${slot.pair.id}:${slot.role}`;
        Promise.resolve(slot.element.play()).catch(error => { if (slot.serial === serial && this.slots.get(key) === slot) this.onError(error, slot.pair[slot.role], slot.url); });
      }
      return true;
    }
    pause() { for (const slot of this.slots.values()) { slot.serial++; slot.element.pause(); } }
    seek() {
      this.pause();
      for (const slot of this.slots.values()) { slot.assigned = false; slot.waitingSeek = false; slot.stalled = false; if (slot.failed) { slot.failed = false; slot.element.load(); } }
    }
    releaseSlot(key) {
      const slot = this.slots.get(key); if (!slot) return;
      this.slots.delete(key); slot.serial++; slot.element.pause(); slot.element.removeAttribute("src"); slot.element.load(); slot.element.remove?.();
    }
    clear() { for (const key of [...this.slots.keys()]) this.releaseSlot(key); this.pairs.clear(); }
  }
  const core = { TransitionPreviewController };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorTransitionPreview = core;
})(typeof window !== "undefined" ? window : globalThis);
