/* Speed curves are edited in source time. One pointer gesture is one undo step. */
(function (root) {
  "use strict";
  const MIN_SPEED = 0.25, MAX_SPEED = 4, MAX_POINTS = 50;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const GRAPH = { left: 38, right: 406, top: 15, bottom: 185, width: 420, height: 218 };
  const PRESETS = [
    { id: "custom", label: "自訂", english: "Custom", points: [[0,1],[.25,1],[.5,1],[.75,1],[1,1]] },
    { id: "montage", label: "蒙太奇", english: "Montage", points: [[0,1],[.2,2.5],[.4,.5],[.6,2.5],[.8,.5],[1,1]] },
    { id: "hero", label: "英雄時刻", english: "Hero", points: [[0,2],[.3,1],[.45,.35],[.65,.35],[.8,1],[1,2]] },
    { id: "bullet", label: "子彈時間", english: "Bullet", points: [[0,1],[.25,2.5],[.4,.25],[.6,.25],[.75,2.5],[1,1]] },
    { id: "jump_cut", label: "跳切", english: "Jump Cut", points: [[0,.5],[.2,.5],[.35,3],[.65,3],[.8,.5],[1,.5]] },
    { id: "flash_in", label: "閃入", english: "Flash In", points: [[0,4],[.2,2],[.4,.5],[.7,.5],[1,1]] },
    { id: "flash_out", label: "閃出", english: "Flash Out", points: [[0,1],[.3,.5],[.6,.5],[.8,2],[1,4]] }
  ];
  const clonePoints = points => (points || []).map(point => ({ time: point.time, speed: point.speed }));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const nearly = (a, b) => Math.abs(a - b) < 1e-7;
  const fmt = value => Number(value).toFixed(2);
  function speedAt(points, time, normalSpeed = 1) {
    if (!points.length) return normalSpeed;
    let left = points[0];
    if (time <= left.time) return left.speed;
    for (let index = 1; index < points.length; index++) {
      const right = points[index];
      if (time <= right.time) return left.speed + (right.speed - left.speed) * (time - left.time) / (right.time - left.time);
      left = right;
    }
    return left.speed;
  }
  function durationFor(points, sourceIn, sourceOut, normalSpeed = 1) {
    if (!(sourceOut > sourceIn)) return 0;
    const times = [sourceIn, ...points.filter(point => point.time > sourceIn && point.time < sourceOut).map(point => point.time), sourceOut];
    let result = 0;
    for (let index = 1; index < times.length; index++) {
      const a = speedAt(points, times[index - 1], normalSpeed), b = speedAt(points, times[index], normalSpeed), span = times[index] - times[index - 1];
      result += Math.abs(b - a) <= Math.abs(a) * 1e-12 ? span / a : span * Math.log1p((b - a) / a) / (b - a);
    }
    return result;
  }
  function presetPoints(id, sourceIn, sourceOut, normalSpeed = 1) {
    const preset = PRESETS.find(item => item.id === id);
    if (!preset || !(sourceOut > sourceIn)) return [];
    return preset.points.map(([ratio, speed]) => ({ time: sourceIn + ratio * (sourceOut - sourceIn), speed: id === "custom" ? clamp(normalSpeed, MIN_SPEED, MAX_SPEED) : speed }));
  }
  function replaceVisiblePoints(points, replacement, sourceIn, sourceOut) {
    return [...clonePoints(points).filter(point => point.time < sourceIn - 1e-7 || point.time > sourceOut + 1e-7), ...clonePoints(replacement)].sort((a, b) => a.time - b.time);
  }
  function constrainPoint(points, index, time, speed, sourceIn, sourceOut) {
    const original = points[index];
    if (!original || !Number.isFinite(time) || !Number.isFinite(speed)) return null;
    const gap = Math.min(0.001, (sourceOut - sourceIn) / 10000);
    const fixedTime = index === 0 || index === points.length - 1;
    const low = Math.max(sourceIn, index ? points[index - 1].time + gap : sourceIn);
    const high = Math.min(sourceOut, index + 1 < points.length ? points[index + 1].time - gap : sourceOut);
    return { time: fixedTime ? original.time : clamp(time, low, high), speed: clamp(speed, MIN_SPEED, MAX_SPEED) };
  }
  function graphX(time, sourceIn, sourceOut) { return GRAPH.left + clamp((time - sourceIn) / (sourceOut - sourceIn), 0, 1) * (GRAPH.right - GRAPH.left); }
  function graphY(speed) { return GRAPH.bottom - (Math.log2(clamp(speed, MIN_SPEED, MAX_SPEED)) + 2) / 4 * (GRAPH.bottom - GRAPH.top); }
  function graphPoint(x, y, sourceIn, sourceOut) {
    return { time: sourceIn + clamp((x - GRAPH.left) / (GRAPH.right - GRAPH.left), 0, 1) * (sourceOut - sourceIn), speed: clamp(2 ** ((GRAPH.bottom - y) / (GRAPH.bottom - GRAPH.top) * 4 - 2), MIN_SPEED, MAX_SPEED) };
  }
  function graphPath(points, sourceIn, sourceOut, normalSpeed = 1, dimensions = GRAPH) {
    if (!(sourceOut > sourceIn)) return "";
    const segments = [];
    for (let index = 0; index <= 120; index++) {
      const time = sourceIn + (sourceOut - sourceIn) * index / 120;
      const x = dimensions.left + index / 120 * (dimensions.right - dimensions.left);
      const y = dimensions.bottom - (Math.log2(clamp(speedAt(points, time, normalSpeed), MIN_SPEED, MAX_SPEED)) + 2) / 4 * (dimensions.bottom - dimensions.top);
      segments.push(`${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`);
    }
    return segments.join(" ");
  }
  function mountSpeedEditor(element, options = {}) {
    if (!element) throw new Error("缺少影片速度編輯區。");
    const doc = element.ownerDocument;
    let state = { clipId: null, sourceIn: 0, sourceOut: 1, normalSpeed: 1, points: [], mode: "normal", sourcePlayhead: 0, disabled: true, supported: false };
    let selected = 0, gesture = null, expanded = false, destroyed = false, customEditing = false, renderRevision = 0, editRevision = 0;
    const backdrop = doc.createElement("div"); backdrop.className = "speed-editor-backdrop hidden"; backdrop.setAttribute("aria-hidden", "true"); doc.body.append(backdrop);
    const query = selector => element.querySelector(selector);
    element.innerHTML = `<div class="speed-editor-heading"><h3>速度</h3><button type="button" class="button ghost small speed-expand" aria-label="展開速度曲線編輯">展開編輯 ↗</button></div>
      <div class="speed-tabs" role="tablist" aria-label="影片變速模式"><button type="button" id="speedNormalTab" role="tab" aria-controls="speedNormalSummary">一般</button><button type="button" id="speedCurveTab" role="tab" aria-controls="speedCurvePanel">曲線</button></div>
      <p id="speedNormalSummary" class="subtle">整段影片以固定速度播放。切換「曲線」可分段加速與慢放。</p>
      <div id="speedCurvePanel" role="tabpanel" aria-labelledby="speedCurveTab"><div class="speed-curve-presets" aria-label="速度曲線預設"></div>
      <div class="speed-curve-chart"><svg id="speedCurveGraph" viewBox="0 0 420 218" role="group" aria-label="速度曲線，向上加速、向下慢放，左右調整來源時間" tabindex="0"><g class="speed-curve-grid"></g><path class="speed-curve-fill"></path><path class="speed-curve-line"></path><line class="speed-source-cursor"></line><g class="speed-curve-nodes"></g></svg></div>
      <div class="speed-curve-actions"><button type="button" id="addSpeedPoint" class="button secondary small">＋ 加入點</button><button type="button" id="deleteSpeedPoint" class="button ghost small">刪除點</button><button type="button" id="resetSpeedCurve" class="button ghost small">重設</button></div>
      <div class="trim-fields speed-point-fields"><label class="field">來源時間（秒）<input id="speedPointTime" type="number" step="0.01" aria-label="速度點來源時間"></label><label class="field">速度（倍）<input id="speedPointValue" type="number" min="0.25" max="4" step="0.05" aria-label="速度點倍率"></label></div>
      <div class="speed-duration-summary"><span>原長 <strong id="speedSourceDuration"></strong></span><span>變速後 <strong id="speedCurveDuration"></strong></span></div>
      <p class="subtle speed-curve-help">上下拖曳控制速度；左右拖曳調整位置。點曲線定位，雙擊新增點。選取點後可用方向鍵微調。同軌後方片段會隨長度變化移動並保留原有間隔；其他軌道維持原時間。</p></div>
      <p id="speedCurveStatus" class="speed-curve-status subtle" role="status" aria-live="polite"></p>`;
    const graph = query("#speedCurveGraph"), nodes = query(".speed-curve-nodes"), grid = query(".speed-curve-grid");
    function svgNode(tag, attributes, text) {
      const node = doc.createElementNS(SVG_NS, tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
      if (text !== undefined) node.textContent = text;
      return node;
    }
    for (const speed of [4, 2, 1, .5, .25]) {
      const y = graphY(speed);
      grid.append(svgNode("line", { x1: GRAPH.left, y1: y, x2: GRAPH.right, y2: y, class: speed === 1 ? "speed-baseline" : "" }));
      grid.append(svgNode("text", { x: GRAPH.left - 7, y: y + 4, "text-anchor": "end" }, `${speed}×`));
    }
    for (const ratio of [0, .25, .5, .75, 1]) grid.append(svgNode("line", { x1: GRAPH.left + ratio * (GRAPH.right - GRAPH.left), y1: GRAPH.top, x2: GRAPH.left + ratio * (GRAPH.right - GRAPH.left), y2: GRAPH.bottom }));
    const startLabel = svgNode("text", { x: GRAPH.left, y: 207 }), endLabel = svgNode("text", { x: GRAPH.right, y: 207, "text-anchor": "end" });
    grid.append(startLabel, endLabel);
    for (const preset of PRESETS) {
      const button = doc.createElement("button"); button.type = "button"; button.dataset.speedPreset = preset.id;
      button.title = `${preset.label} · ${preset.english}`; button.setAttribute("aria-label", `${preset.label}速度曲線`);
      const icon = svgNode("svg", { viewBox: "0 0 60 28", "aria-hidden": "true" });
      icon.append(svgNode("line", { x1: 2, y1: 14, x2: 58, y2: 14, class: "preset-baseline" }));
      icon.append(svgNode("path", { d: graphPath(presetPoints(preset.id, 0, 1), 0, 1, 1, { left: 2, right: 58, top: 2, bottom: 26 }), fill: "none" }));
      const label = doc.createElement("span"); label.textContent = preset.label;
      button.append(icon, label); button.onclick = () => {
        if (!editable()) return;
        if (preset.id === "custom" && state.points.length >= 2) {
          customEditing = true; paint(); graph.focus({ preventScroll: true }); status("自訂曲線：拖曳現有控制點，或在播放游標位置加入點。"); return;
        }
        const points = replaceVisiblePoints(state.points, presetPoints(preset.id, state.sourceIn, state.sourceOut, preset.id === "custom" ? state.normalSpeed : 1), state.sourceIn, state.sourceOut);
        if (points.length > MAX_POINTS) { status("保留裁切範圍外的控制點後會超過 50 點，請先刪除一些控制點。"); return; }
        accepted(change(points, { reason: "preset", preset: preset.id, selected: points.findIndex(point => nearly(point.time, state.sourceIn)) }),
          `已套用「${preset.label}」。可拖曳白色控制點調整。`);
      };
      query(".speed-curve-presets").append(button);
    }
    function editable() { return !state.disabled && state.supported && state.clipId !== null && state.sourceOut > state.sourceIn; }
    function status(message) { query("#speedCurveStatus").textContent = message; }
    function call(name, ...args) {
      if (!options[name]) return { ok: true };
      const revision = editRevision, clipId = state.clipId;
      try {
        const result = options[name](...args);
        if (result && typeof result.then === "function") {
          const settled = Promise.resolve(result).then(value => ({ ok: true, result: value }), error => {
            if (!destroyed && revision === editRevision && state.clipId === clipId) status(error.message || "速度調整未完成，請再試一次。");
            return { ok: false, error };
          });
          return { ok: true, result, pending: true, settled };
        }
        return { ok: true, result };
      } catch (error) {
        if (!destroyed && revision === editRevision && state.clipId === clipId) status(error.message || "速度調整未完成，請再試一次。");
        return { ok: false, error };
      }
    }
    function editSnapshot() { return { points: clonePoints(state.points), mode: state.mode, clipId: state.clipId, selected, customEditing }; }
    function deliverEdit(name, args, before, rendered) {
      const revision = ++editRevision, current = () => !destroyed && revision === editRevision && state.clipId === before.clipId;
      function restore() {
        if (!current()) return;
        // The controller may have rendered its authoritative project while rejecting
        // the edit. Preserve that render; restore locally when no render occurred.
        if (renderRevision === rendered) { state.points = clonePoints(before.points); state.mode = before.mode; }
        customEditing = before.customEditing; selected = Math.max(0, Math.min(before.selected, state.points.length - 1)); paint();
      }
      const outcome = call(name, ...args); outcome.current = current;
      if (outcome.pending) outcome.settled = outcome.settled.then(result => { if (!result.ok) restore(); return result; });
      else if (!outcome.ok) restore();
      return outcome;
    }
    function accepted(outcome, message, after) {
      function complete(result) { if (!result.ok || outcome.current && !outcome.current()) return; after?.(); status(message); }
      if (outcome.pending) { if (!outcome.current || outcome.current()) status("正在套用速度調整…"); outcome.settled.then(complete); }
      else complete(outcome);
      return outcome;
    }
    function change(points, meta = {}) {
      const before = editSnapshot(), rendered = renderRevision;
      customEditing = meta.reason === "preset" ? meta.preset === "custom" : true;
      if (Number.isInteger(meta.selected)) selected = meta.selected;
      state.points = clonePoints(points); state.mode = "curve";
      paint(); return deliverEdit("onChangeCurve", [clonePoints(points), meta], before, rendered);
    }
    function visibleIndices() { return state.points.map((point, index) => ({ point, index })).filter(({ point }) => point.time >= state.sourceIn - 1e-7 && point.time <= state.sourceOut + 1e-7); }
    function focusPoint() { nodes.querySelector(`[data-speed-point="${selected}"]`)?.focus({ preventScroll: true }); }
    function selectPoint(index, seek = true) {
      selected = index; paintNodes(); paintFields();
      if (seek && state.points[index]) call("onSeekSource", state.points[index].time);
    }
    function paintNodes() {
      for (const boundary of nodes.querySelectorAll(".speed-point-boundary")) boundary.remove();
      const focused = doc.activeElement?.getAttribute?.("data-speed-point"), oldNodes = new Map([...nodes.children].map(node => [Number(node.dataset.speedPoint), node]));
      const visible = visibleIndices();
      for (const { point, index } of visible) {
        let group = oldNodes.get(index);
        if (!group) {
          group = svgNode("g", { "data-speed-point": index, role: "slider", tabindex: "0", "aria-valuemin": MIN_SPEED, "aria-valuemax": MAX_SPEED });
          group.append(svgNode("circle", { r: 14, class: "speed-point-hit" }), svgNode("circle", { r: 5.5, class: "speed-point-dot" }));
          nodes.append(group);
        }
        group.classList.toggle("selected", selected === index);
        group.setAttribute("transform", `translate(${graphX(point.time, state.sourceIn, state.sourceOut)},${graphY(point.speed)})`);
        group.setAttribute("aria-label", `速度點 ${index + 1}，來源 ${fmt(point.time)} 秒，${fmt(point.speed)} 倍`);
        group.setAttribute("aria-valuenow", point.speed); group.setAttribute("aria-valuetext", `${fmt(point.speed)} 倍，${fmt(point.time)} 秒`);
        group.setAttribute("aria-disabled", String(!editable()));
        oldNodes.delete(index);
      }
      for (const old of oldNodes.values()) old.remove();
      for (const [time, side] of [[state.sourceIn, "開始"], [state.sourceOut, "結束"]]) {
        if (visible.some(({ point }) => nearly(point.time, time))) continue;
        const circle = svgNode("circle", { cx: graphX(time, state.sourceIn, state.sourceOut), cy: graphY(speedAt(state.points, time, state.normalSpeed)), r: 5, class: "speed-point-boundary", "aria-hidden": "true" });
        const title = svgNode("title", {}, `${side}邊界：雙擊可加入速度點`); circle.append(title); nodes.append(circle);
      }
      if (focused !== null && focused !== undefined && !doc.activeElement?.isConnected) focusPoint();
    }
    function paintFields() {
      const point = state.points[selected], can = editable(), visible = point && point.time >= state.sourceIn - 1e-7 && point.time <= state.sourceOut + 1e-7;
      const time = query("#speedPointTime"), value = query("#speedPointValue");
      if (doc.activeElement !== time) time.value = visible ? String(Number(point.time.toFixed(6))) : "";
      if (doc.activeElement !== value) value.value = visible ? fmt(point.speed) : "";
      time.min = state.sourceIn; time.max = state.sourceOut;
      time.disabled = !can || !visible || selected === 0 || selected === state.points.length - 1;
      value.disabled = !can || !visible;
      query("#deleteSpeedPoint").disabled = !can || !visible || selected === 0 || selected === state.points.length - 1 || state.points.length <= 2;
      query("#addSpeedPoint").disabled = !can || state.points.length >= MAX_POINTS || !Number.isFinite(state.sourcePlayhead) || state.sourcePlayhead < state.sourceIn - 1e-7 || state.sourcePlayhead > state.sourceOut + 1e-7;
      query("#resetSpeedCurve").disabled = !can;
    }
    function paintCursor() {
      const cursor = query(".speed-source-cursor"), inRange = Number.isFinite(state.sourcePlayhead) && state.sourcePlayhead >= state.sourceIn && state.sourcePlayhead <= state.sourceOut;
      const x = inRange ? graphX(state.sourcePlayhead, state.sourceIn, state.sourceOut) : GRAPH.left;
      cursor.setAttribute("x1", x); cursor.setAttribute("x2", x); cursor.setAttribute("y1", GRAPH.top); cursor.setAttribute("y2", GRAPH.bottom); cursor.classList.toggle("hidden", !inRange);
      query("#addSpeedPoint").disabled = !editable() || state.points.length >= MAX_POINTS || !inRange;
    }
    function paint() {
      const curve = state.mode === "curve";
      query("#speedNormalTab").setAttribute("aria-selected", String(!curve)); query("#speedCurveTab").setAttribute("aria-selected", String(curve));
      query("#speedNormalTab").tabIndex = curve ? -1 : 0; query("#speedCurveTab").tabIndex = curve ? 0 : -1;
      query("#speedNormalTab").disabled = state.disabled; query("#speedCurveTab").disabled = state.disabled || !state.supported;
      query("#speedNormalSummary").classList.toggle("hidden", curve); query("#speedCurvePanel").classList.toggle("hidden", !curve);
      query(".speed-expand").classList.toggle("hidden", !curve); query(".speed-expand").disabled = state.disabled;
      startLabel.textContent = `${fmt(state.sourceIn)} 秒`; endLabel.textContent = `${fmt(state.sourceOut)} 秒`;
      const path = graphPath(state.points, state.sourceIn, state.sourceOut, state.normalSpeed);
      query(".speed-curve-line").setAttribute("d", path);
      query(".speed-curve-fill").setAttribute("d", path ? `${path} L${GRAPH.right},${GRAPH.bottom} L${GRAPH.left},${GRAPH.bottom} Z` : "");
      paintCursor();
      const visiblePoints = visibleIndices().map(item => item.point);
      const match = PRESETS.find(preset => {
        const presetCurve = presetPoints(preset.id, state.sourceIn, state.sourceOut, preset.id === "custom" ? state.normalSpeed : 1);
        return presetCurve.length === visiblePoints.length && presetCurve.every((point, index) => nearly(point.time, visiblePoints[index].time) && nearly(point.speed, visiblePoints[index].speed));
      });
      for (const button of element.querySelectorAll("[data-speed-preset]")) {
        const active = button.dataset.speedPreset === (customEditing ? "custom" : (match?.id || "custom"));
        button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); button.disabled = !editable();
      }
      query("#speedSourceDuration").textContent = `${fmt(state.sourceOut - state.sourceIn)}s`;
      query("#speedCurveDuration").textContent = `${fmt(durationFor(state.points, state.sourceIn, state.sourceOut, state.normalSpeed))}s`;
      paintNodes(); paintFields();
    }
    function pointFromEvent(event) {
      const bounds = graph.getBoundingClientRect();
      return graphPoint((event.clientX - bounds.left) / bounds.width * GRAPH.width, (event.clientY - bounds.top) / bounds.height * GRAPH.height, state.sourceIn, state.sourceOut);
    }
    function addPoint(time, speed = speedAt(state.points, time, state.normalSpeed)) {
      if (!editable()) return;
      if (state.points.length >= MAX_POINTS) { status("每段影片最多 50 個速度點。"); return; }
      const index = state.points.findIndex(point => nearly(point.time, time));
      if (index >= 0) { selectPoint(index); status("這個時間已有速度點。上下拖曳即可修改速度。"); return; }
      const points = [...clonePoints(state.points), { time: clamp(time, state.sourceIn, state.sourceOut), speed: clamp(speed, MIN_SPEED, MAX_SPEED) }].sort((a, b) => a.time - b.time);
      if (points.length < 2) return;
      accepted(change(points, { reason: "add", selected: points.findIndex(point => nearly(point.time, time)) }), `已加入 ${fmt(time)} 秒的速度點。`, focusPoint);
    }
    function deletePoint() {
      if (!editable() || selected <= 0 || selected >= state.points.length - 1 || state.points.length <= 2) return;
      const points = clonePoints(state.points); points.splice(selected, 1);
      accepted(change(points, { reason: "delete", selected: Math.max(0, selected - 1) }), "已刪除速度點。", focusPoint);
    }
    function cancelGesture() {
      if (!gesture) return;
      const previous = gesture; gesture = null; state.points = clonePoints(previous.before); customEditing = previous.customEditing; paint();
      if (previous.frame !== null) root.cancelAnimationFrame(previous.frame);
      if (graph.hasPointerCapture?.(previous.pointerId)) graph.releasePointerCapture(previous.pointerId);
      call("onGestureCancel"); status("已取消這次曲線調整。");
    }
    graph.addEventListener("pointerdown", event => {
      const target = event.target.closest?.("[data-speed-point]");
      if (!target || !editable() || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      selected = Number(target.dataset.speedPoint); selectPoint(selected, false); target.focus({ preventScroll: true });
      gesture = { before: clonePoints(state.points), pointerId: event.pointerId, index: selected, customEditing, frame: null, lastPoint: null,
        startPoint: pointFromEvent(event), startX: event.clientX, startY: event.clientY, moved: false };
      graph.setPointerCapture?.(event.pointerId);
      if (!call("onGestureStart", clonePoints(state.points)).ok) {
        gesture = null;
        if (graph.hasPointerCapture?.(event.pointerId)) graph.releasePointerCapture(event.pointerId);
        paint();
      }
    });
    function flushGesture() {
      if (!gesture) return;
      gesture.frame = null;
      if (!gesture.lastPoint) return;
      const initial = gesture.before[gesture.index], point = gesture.lastPoint;
      const bounded = constrainPoint(gesture.before, gesture.index, initial.time + point.time - gesture.startPoint.time,
        initial.speed * point.speed / gesture.startPoint.speed, state.sourceIn, state.sourceOut);
      if (!bounded) return;
      state.points = clonePoints(gesture.before); state.points[gesture.index] = bounded; customEditing = true; paint();
      call("onGesturePreview", clonePoints(state.points));
    }
    graph.addEventListener("pointermove", event => {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      event.preventDefault();
      if (!gesture.moved && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) < 2) return;
      gesture.moved = true; gesture.lastPoint = pointFromEvent(event);
      if (gesture.frame === null) gesture.frame = root.requestAnimationFrame(flushGesture);
    });
    graph.addEventListener("pointerup", event => {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      if (gesture.frame !== null) root.cancelAnimationFrame(gesture.frame);
      if (gesture.moved) { gesture.lastPoint = pointFromEvent(event); flushGesture(); }
      const previous = gesture; gesture = null;
      if (graph.hasPointerCapture?.(event.pointerId)) graph.releasePointerCapture(event.pointerId);
      const changed = JSON.stringify(previous.before) !== JSON.stringify(state.points);
      if (changed) {
        const before = { ...editSnapshot(), points: clonePoints(previous.before), customEditing: previous.customEditing }, rendered = renderRevision;
        const message = `速度點：${fmt(state.points[selected].time)} 秒，${fmt(state.points[selected].speed)} 倍。`;
        const outcome = options.onGestureCommit ? deliverEdit("onGestureCommit", [clonePoints(state.points)], before, rendered)
          : deliverEdit("onChangeCurve", [clonePoints(state.points), { reason: "drag" }], before, rendered);
        accepted(outcome, message);
      } else { customEditing = previous.customEditing; paint(); call("onGestureCancel"); call("onSeekSource", state.points[selected].time); }
    });
    graph.addEventListener("pointercancel", cancelGesture);
    graph.addEventListener("lostpointercapture", () => { if (gesture) cancelGesture(); });
    graph.addEventListener("click", event => {
      if (event.target.closest?.("[data-speed-point]") || gesture || !state.clipId) return;
      call("onSeekSource", pointFromEvent(event).time);
    });
    graph.addEventListener("dblclick", event => {
      if (event.target.closest?.("[data-speed-point]")) return;
      event.preventDefault(); const point = pointFromEvent(event); addPoint(point.time, point.speed);
    });
    graph.addEventListener("keydown", event => {
      if (event.key === "Escape" && gesture) { event.preventDefault(); event.stopPropagation(); cancelGesture(); return; }
      const target = event.target.closest?.("[data-speed-point]");
      if (!target || !editable()) return;
      selected = Number(target.dataset.speedPoint);
      if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); event.stopPropagation(); deletePoint(); return; }
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); selectPoint(selected); return; }
      if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const point = state.points[selected], direction = ["ArrowUp", "ArrowRight"].includes(event.key) ? 1 : -1;
      const time = point.time + (["ArrowLeft", "ArrowRight"].includes(event.key) ? direction * (event.shiftKey ? .1 : .01) : 0);
      const speed = point.speed + (["ArrowUp", "ArrowDown"].includes(event.key) ? direction * (event.shiftKey ? .25 : .05) : 0);
      const bounded = constrainPoint(state.points, selected, time, speed, state.sourceIn, state.sourceOut);
      const points = clonePoints(state.points); points[selected] = bounded; change(points, { reason: "keyboard" }); focusPoint();
    });
    query("#speedNormalTab").onclick = () => { if (state.disabled) return; cancelGesture(); call("onChangeMode", "normal"); };
    query("#speedCurveTab").onclick = () => { if (!editable()) return; call("onChangeMode", "curve"); };
    query(".speed-tabs").addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const next = event.key === "ArrowLeft" || event.key === "Home" ? query("#speedNormalTab") : query("#speedCurveTab");
      if (!next.disabled) { next.focus(); next.click(); }
    });
    query("#addSpeedPoint").onclick = () => addPoint(state.sourcePlayhead);
    query("#deleteSpeedPoint").onclick = deletePoint;
    query("#resetSpeedCurve").onclick = () => {
      if (!editable()) return;
      const points = replaceVisiblePoints(state.points, presetPoints("custom", state.sourceIn, state.sourceOut, 1), state.sourceIn, state.sourceOut);
      if (points.length > MAX_POINTS) { status("保留裁切範圍外的控制點後會超過 50 點，請先刪除一些控制點。"); return; }
      accepted(change(points, { reason: "reset", selected: points.findIndex(point => nearly(point.time, state.sourceIn)) }), "已重設為 1 倍速曲線。");
    };
    function changeInput(event) {
      if (!editable() || !state.points[selected]) return;
      const original = state.points[selected], changedTime = event?.target === query("#speedPointTime");
      const timeText = changedTime ? query("#speedPointTime").value : String(original.time), speedText = changedTime ? String(original.speed) : query("#speedPointValue").value;
      const time = Number(timeText), speed = Number(speedText);
      if (!timeText.trim() || !speedText.trim() || !Number.isFinite(time) || !Number.isFinite(speed) || speed < MIN_SPEED || speed > MAX_SPEED || time < state.sourceIn || time > state.sourceOut) { status("請輸入來源範圍內的時間，速度可設為 0.25–4 倍。"); paintFields(); return; }
      const bounded = constrainPoint(state.points, selected, time, speed, state.sourceIn, state.sourceOut);
      if (!bounded) return;
      const points = clonePoints(state.points); points[selected] = bounded;
      accepted(change(points, { reason: "number" }), `已設定 ${fmt(bounded.time)} 秒，${fmt(bounded.speed)} 倍。`);
    }
    query("#speedPointTime").onchange = changeInput; query("#speedPointValue").onchange = changeInput;
    query("#speedPointTime").onblur = paintFields; query("#speedPointValue").onblur = paintFields;
    function setExpanded(value) {
      expanded = value; element.classList.toggle("speed-editor-expanded", expanded);
      backdrop.classList.toggle("hidden", !expanded);
      if (expanded) { element.setAttribute("role", "dialog"); element.setAttribute("aria-modal", "true"); }
      else { element.removeAttribute("role"); element.removeAttribute("aria-modal"); }
      query(".speed-expand").textContent = expanded ? "完成編輯 ✓" : "展開編輯 ↗";
      query(".speed-expand").setAttribute("aria-label", expanded ? "收合速度曲線編輯" : "展開速度曲線編輯");
      element.setAttribute("aria-label", expanded ? "速度曲線展開編輯" : "影片速度");
      if (expanded) graph.focus({ preventScroll: true });
    }
    query(".speed-expand").onclick = () => setExpanded(!expanded);
    backdrop.onclick = () => { cancelGesture(); setExpanded(false); query(".speed-expand").focus(); };
    element.addEventListener("keydown", event => {
      if (event.key === "Escape" && expanded && !gesture) { event.preventDefault(); event.stopPropagation(); setExpanded(false); query(".speed-expand").focus(); }
      if (event.key === "Tab" && expanded) {
        const focusable = [...element.querySelectorAll("button:not(:disabled),input:not(:disabled),[tabindex='0']")].filter(node => !node.closest(".hidden"));
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    });
    function render(nextState) {
      if (destroyed) return;
      if (Object.prototype.hasOwnProperty.call(nextState, "points")) renderRevision++;
      const changedClip = Object.prototype.hasOwnProperty.call(nextState, "clipId") && state.clipId !== nextState.clipId;
      if (changedClip && gesture) cancelGesture();
      const selectedTime = state.points[selected]?.time;
      const points = gesture ? state.points : clonePoints(nextState.points ?? state.points);
      const pointsChanged = points.length !== state.points.length || points.some((point, index) => point.time !== state.points[index].time || point.speed !== state.points[index].speed);
      const structureChanged = changedClip || ["sourceIn", "sourceOut", "normalSpeed", "mode", "disabled", "supported"].some(key => Object.prototype.hasOwnProperty.call(nextState, key) && nextState[key] !== state[key])
        || pointsChanged;
      state = { ...state, ...nextState, points };
      if (changedClip) { selected = Math.max(0, state.points.findIndex(point => point.time >= state.sourceIn)); customEditing = false; setExpanded(false); }
      else if (!gesture && selectedTime !== undefined) { const same = state.points.findIndex(point => nearly(point.time, selectedTime)); selected = same >= 0 ? same : Math.min(selected, state.points.length - 1); }
      if (!gesture && pointsChanged) customEditing = false;
      if (expanded && !gesture && (state.mode !== "curve" || state.disabled)) setExpanded(false);
      if (!state.supported) status("目前 Studio 尚未載入曲線變速。儲存後重啟 Studio，再重新檢查功能。");
      else if (changedClip || query("#speedCurveStatus").textContent.includes("尚未載入")) status(state.mode === "curve" ? "拖曳控制點，設計影片的加速與慢放節奏。" : "選擇「曲線」，讓一段影片逐漸加速或慢放。");
      if (structureChanged) paint(); else paintCursor();
    }
    paint(); render(state);
    return { render, cancelGesture, focusCurve() { if (state.disabled) return; element.scrollIntoView({ block: "nearest", behavior: "smooth" }); query("#speedCurveTab").focus({ preventScroll: true }); }, destroy() { cancelGesture(); destroyed = true; backdrop.remove(); element.innerHTML = ""; element.classList.remove("speed-editor-expanded"); } };
  }
  const api = { MIN_SPEED, MAX_SPEED, MAX_POINTS, PRESETS, GRAPH, speedAt, durationFor, presetPoints, replaceVisiblePoints, constrainPoint, graphX, graphY, graphPoint, graphPath, mountSpeedEditor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.H3EditorSpeedUI = api;
})(typeof window !== "undefined" ? window : globalThis);
