/* Pixel transitions share premultiplied RGBA math with the exported video frames. */
(function (root) {
  "use strict";
  const REFERENCE_TRANSITION_TYPES = Object.freeze(["radial_zoom", "horizontal_blur"]);
  const EFFECT_TYPES = Object.freeze(["white_flash", "blur_dissolve", "wave_distort", "rgb_glitch", ...REFERENCE_TRANSITION_TYPES]);
  const isEffect = type => EFFECT_TYPES.includes(type);
  const clampIndex = (value, length) => Math.max(0, Math.min(length - 1, value));
  const roundByte = value => Math.max(0, Math.min(255, Math.floor(value + 0.5)));
  // Reuse the two exact-size work buffers during playback; inputs and results remain independent.
  let blended = new Float32Array(0), horizontal = new Float32Array(0), downsampled = new Float32Array(0);
  let jitter = new Float64Array(0), jitterWidth = 0, jitterHeight = 0;
  let columnKernels = new Float64Array(0), columnWeights = new Float64Array(0), sampledRow = new Float64Array(0);
  let columnStarts = new Int32Array(0), columnLengths = new Int32Array(0), columnTotals = new Float64Array(0);
  function validate(outgoing, incoming, width, height, type, progress) {
    if (!isEffect(type)) throw new Error("不支援的轉場特效。");
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 || !Number.isSafeInteger(width * height * 4)) throw new Error("轉場畫面尺寸無效。");
    const size = width * height * 4;
    const bytes = value => value instanceof Uint8Array || value instanceof Uint8ClampedArray;
    if (!bytes(outgoing) || !bytes(incoming) || outgoing.length !== size || incoming.length !== size || !Number.isFinite(progress)) throw new Error("轉場 RGBA 像素或進度無效。");
    return size;
  }
  function writePixel(result, offset, red, green, blue, alpha) {
    const factor = alpha > 0 ? 255 / alpha : 0;
    result[offset] = roundByte(red * factor); result[offset + 1] = roundByte(green * factor);
    result[offset + 2] = roundByte(blue * factor); result[offset + 3] = roundByte(alpha);
  }
  function boxBlur(result, width, height, radius) {
    if (horizontal.length !== blended.length) horizontal = new Float32Array(blended.length);
    const count = radius * 2 + 1;
    // Sliding sums make the blur cost independent of its radius. Clamp the image edges.
    for (let y = 0; y < height; y++) for (let channel = 0; channel < 4; channel++) {
      const row = y * width * 4;
      let sum = 0;
      for (let dx = -radius; dx <= radius; dx++) sum += blended[row + clampIndex(dx, width) * 4 + channel];
      for (let x = 0; x < width; x++) {
        horizontal[row + x * 4 + channel] = sum / count;
        sum += blended[row + clampIndex(x + radius + 1, width) * 4 + channel] - blended[row + clampIndex(x - radius, width) * 4 + channel];
      }
    }
    for (let x = 0; x < width; x++) {
      let red = 0, green = 0, blue = 0, alpha = 0;
      for (let dy = -radius; dy <= radius; dy++) {
        const offset = (clampIndex(dy, height) * width + x) * 4;
        red += horizontal[offset]; green += horizontal[offset + 1]; blue += horizontal[offset + 2]; alpha += horizontal[offset + 3];
      }
      for (let y = 0; y < height; y++) {
        writePixel(result, (y * width + x) * 4, red / count, green / count, blue / count, alpha / count);
        const add = (clampIndex(y + radius + 1, height) * width + x) * 4, remove = (clampIndex(y - radius, height) * width + x) * 4;
        red += horizontal[add] - horizontal[remove]; green += horizontal[add + 1] - horizontal[remove + 1];
        blue += horizontal[add + 2] - horizontal[remove + 2]; alpha += horizontal[add + 3] - horizontal[remove + 3];
      }
    }
  }
  function sampleLinear(source, width, height, x, y, sample) {
    x = Math.max(0, Math.min(width - 1, x)); y = Math.max(0, Math.min(height - 1, y));
    const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.min(width - 1, x0 + 1), y1 = Math.min(height - 1, y0 + 1), fx = x - x0, fy = y - y0;
    const a = (y0 * width + x0) * 4, b = (y0 * width + x1) * 4, c = (y1 * width + x0) * 4, d = (y1 * width + x1) * 4;
    for (let channel = 0; channel < 4; channel++) sample[channel] = (source[a + channel] * (1 - fx) + source[b + channel] * fx) * (1 - fy) + (source[c + channel] * (1 - fx) + source[d + channel] * fx) * fy;
  }
  function radialZoom(result, width, height, progress) {
    if (jitterWidth !== width || jitterHeight !== height) {
      jitter = new Float64Array(width * height); jitterWidth = width; jitterHeight = height;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        // Canonical double-precision GLSL hash, with bottom-up fragment coordinates.
        const random = Math.sin((x + 0.5) * 12.9898 + (height - y - 0.5) * 78.233 + 0.5 * 151.7182) * 43758.5453;
        jitter[y * width + x] = random - Math.floor(random);
      }
    }
    const strength = 0.2 * (1 - Math.cos(2 * Math.PI * progress)), centerX = (0.25 + 0.5 * progress) * width - 0.5, centerY = height / 2 - 0.5;
    const sample = new Float64Array(4);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      let red = 0, green = 0, blue = 0, alpha = 0, total = 0;
      const random = jitter[y * width + x];
      for (let index = 0; index <= 40; index++) {
        const percent = (index + random) / 40, weight = 4 * (percent - percent * percent), amount = percent * strength;
        sampleLinear(blended, width, height, x + (centerX - x) * amount, y + (centerY - y) * amount, sample);
        red += sample[0] * weight; green += sample[1] * weight; blue += sample[2] * weight; alpha += sample[3] * weight; total += weight;
      }
      writePixel(result, (y * width + x) * 4, Math.max(0, Math.min(255, Math.fround(red / total))), Math.max(0, Math.min(255, Math.fround(green / total))), Math.max(0, Math.min(255, Math.fround(blue / total))), Math.max(0, Math.min(255, Math.fround(alpha / total))));
    }
  }
  function aeCurve(progress, x1, y1, x2, y2) {
    // AETools in the reference package stops after the interval is below 0.001.
    let low = 0, high = 1;
    for (let iteration = 0; iteration < 10; iteration++) {
      const mid = (low + high) / 2, inverse = 1 - mid;
      const x = 3 * x1 * inverse * inverse * mid + 3 * x2 * inverse * mid * mid + mid * mid * mid;
      if (x > progress) high = mid; else low = mid;
    }
    const t = (low + high) / 2, inverse = 1 - t;
    return 3 * y1 * inverse * inverse * t + 3 * y2 * inverse * t * t + t * t * t;
  }
  function horizontalParameters(progress) {
    const time = progress * 0.5, peak = 0.233333, rising = time < peak;
    const local = rising ? time / peak : (time - peak) / (0.5 - peak);
    const eased = aeCurve(local, 0.33333333, 0, 0, rising ? 1.000000013 : 0.99999969);
    const linear = aeCurve(local, 0.166666667, 0.166666667, 0.833333333, 0.833333333), intensity = 120 * (rising ? eased : 1 - eased), exposure = 2 * (rising ? linear : 1 - linear), correction = 1 + 3 * (rising ? linear : 1 - linear);
    const dissolve = time >= 0.4 ? 1 : aeCurve(time / 0.4, 0.166666667, 0.166666667, 0.833333333, 0.833333333);
    let samples, scale;
    if (intensity <= 10) { samples = 0.8 * intensity; scale = 0.8; }
    else if (intensity <= 50) { samples = (0.7 * intensity + 2) * 0.78; scale = 0.7; }
    else { samples = (0.5 * intensity + 10) * 0.66; scale = 0.5; }
    if (samples < 2) samples = Math.floor(samples + 0.5);
    return { dissolve, intensity, exposure, correction, samples, scale };
  }
  function horizontalBlur(result, width, height, parameters) {
    const { intensity, exposure, correction, samples, scale } = parameters, smallWidth = Math.max(1, Math.floor(width * scale)), smallHeight = Math.max(1, Math.floor(height * scale));
    const smallSize = smallWidth * smallHeight * 4, sample = new Float64Array(4);
    if (downsampled.length !== smallSize) downsampled = new Float32Array(smallSize);
    for (let y = 0; y < smallHeight; y++) for (let x = 0; x < smallWidth; x++) {
      sampleLinear(blended, width, height, (x + 0.5) * width / smallWidth - 0.5, (y + 0.5) * height / smallHeight - 0.5, sample);
      downsampled.set(sample, (y * smallWidth + x) * 4);
    }
    const radius = intensity / 1000, sigma = radius / 3, baseSize = Math.max(Math.min(width, height), Math.max(width, height) / 2);
    const step = radius / Math.max(samples, 1e-5), dx = -step * baseSize / width, dy = Math.sin(-3.141592653) * step * baseSize / height;
    const count = Math.floor(samples), weights = new Float64Array(count + 1), offsets = new Float64Array(count + 1);
    for (let index = 1; index <= count; index++) { offsets[index] = index * dx; weights[index] = Math.exp(-0.5 * (index * index * (dx * dx + dy * dy)) / (sigma * sigma)); }
    // Bilinear taps along one axis can be merged into source-column weights.
    // The reference angle's truncated PI leaves a ~1e-12 UV y displacement;
    // retain its Gaussian length and use the exact horizontal axis for sampling.
    const stride = Math.min(smallWidth, 2 * Math.ceil(count * Math.abs(dx) * smallWidth) + 4);
    if (columnKernels.length !== width * stride) columnKernels = new Float64Array(width * stride);
    if (columnWeights.length !== smallWidth) columnWeights = new Float64Array(smallWidth);
    if (sampledRow.length !== smallWidth * 4) sampledRow = new Float64Array(smallWidth * 4);
    if (columnStarts.length !== width) { columnStarts = new Int32Array(width); columnLengths = new Int32Array(width); columnTotals = new Float64Array(width); }
    for (let x = 0; x < width; x++) {
      const u = (x + 0.5) / width; columnWeights.fill(0);
      let first = smallWidth - 1, last = 0, total = 0;
      const add = (sampleU, weight) => {
        if (sampleU < 0 || sampleU > 1) return;
        const coordinate = Math.max(0, Math.min(smallWidth - 1, sampleU * smallWidth - 0.5)), left = Math.floor(coordinate), right = Math.min(smallWidth - 1, left + 1), fraction = coordinate - left;
        columnWeights[left] += weight * (1 - fraction); columnWeights[right] += weight * fraction;
        first = Math.min(first, left); last = Math.max(last, right); total += weight;
      };
      add(u, 1);
      for (let index = 1; index <= count; index++) { add(u - offsets[index], weights[index]); add(u + offsets[index], weights[index]); }
      columnStarts[x] = first; columnLengths[x] = last - first + 1; columnTotals[x] = total;
      for (let column = first; column <= last; column++) columnKernels[x * stride + column - first] = columnWeights[column];
    }
    const exposureGain = 2 ** exposure;
    for (let y = 0; y < height; y++) {
      const coordinate = Math.max(0, Math.min(smallHeight - 1, (y + 0.5) * smallHeight / height - 0.5)), top = Math.floor(coordinate), bottom = Math.min(smallHeight - 1, top + 1), fraction = coordinate - top;
      for (let index = 0; index < smallWidth * 4; index++) sampledRow[index] = downsampled[top * smallWidth * 4 + index] * (1 - fraction) + downsampled[bottom * smallWidth * 4 + index] * fraction;
      for (let x = 0; x < width; x++) {
        let red = 0, green = 0, blue = 0, alpha = 0;
        const first = columnStarts[x], count = columnLengths[x], total = columnTotals[x];
        for (let column = 0; column < count; column++) {
          const offset = (first + column) * 4, weight = columnKernels[x * stride + column];
          red += sampledRow[offset] * weight; green += sampledRow[offset + 1] * weight; blue += sampledRow[offset + 2] * weight; alpha += sampledRow[offset + 3] * weight;
        }
        alpha = Math.max(0, Math.min(255, Math.fround(alpha / total)));
        const expose = color => {
          const straight = alpha > 0 ? Math.max(0, Math.min(1, Math.fround(color / total) / alpha)) : 0;
          return Math.fround(Math.min(1, ((straight ** 2.2 * exposureGain) ** (1 / correction)) ** (1 / 2.2)) * alpha);
        };
        writePixel(result, (y * width + x) * 4, expose(red), expose(green), expose(blue), alpha);
      }
    }
  }
  function applyPixels(outgoing, incoming, width, height, type, progress) {
    const size = validate(outgoing, incoming, width, height, type, progress);
    if (progress <= 0) return new Uint8ClampedArray(outgoing);
    if (progress >= 1) return new Uint8ClampedArray(incoming);
    if (blended.length !== size) blended = new Float32Array(size);
    const parameters = type === "horizontal_blur" ? horizontalParameters(progress) : null;
    const dissolve = type === "radial_zoom" ? progress < 0.5 ? 0.5 * 2 ** (20 * progress - 10) : 1 - 0.5 * 2 ** (-20 * progress + 10) : parameters?.dissolve ?? progress;
    const result = new Uint8ClampedArray(size), from = 1 - dissolve, envelope = Math.sin(Math.PI * progress) ** 2;
    for (let offset = 0; offset < size; offset += 4) {
      const left = outgoing[offset + 3] / 255 * from, right = incoming[offset + 3] / 255 * dissolve;
      blended[offset] = outgoing[offset] * left + incoming[offset] * right;
      blended[offset + 1] = outgoing[offset + 1] * left + incoming[offset + 1] * right;
      blended[offset + 2] = outgoing[offset + 2] * left + incoming[offset + 2] * right;
      blended[offset + 3] = outgoing[offset + 3] * from + incoming[offset + 3] * dissolve;
    }
    if (type === "radial_zoom") { radialZoom(result, width, height, progress); return result; }
    if (type === "horizontal_blur") { horizontalBlur(result, width, height, parameters); return result; }
    if (type === "blur_dissolve") {
      const radius = Math.floor(Math.min(width, height) * 0.018 * envelope + 0.5);
      if (radius > 0) { boxBlur(result, width, height, radius); return result; }
    }
    if (type === "wave_distort" || type === "rgb_glitch") {
      const split = Math.floor(width * 0.012 * envelope + 0.5), bandHeight = Math.max(1, Math.floor(height / 12));
      for (let y = 0; y < height; y++) {
        const shift = type === "wave_distort"
          ? Math.floor(Math.sin(6 * Math.PI * y / height + 4 * Math.PI * progress) * width * 0.035 * envelope + 0.5)
          : Math.floor(Math.sin(Math.floor(y / bandHeight) * 12.9898 + Math.floor(progress * 12) * 78.233) * width * 0.03 * envelope + 0.5);
        for (let x = 0; x < width; x++) {
          const offset = (y * width + x) * 4, center = (y * width + clampIndex(x + shift, width)) * 4;
          if (type === "wave_distort") writePixel(result, offset, blended[center], blended[center + 1], blended[center + 2], blended[center + 3]);
          else {
            const red = (y * width + clampIndex(x + shift + split, width)) * 4, blue = (y * width + clampIndex(x + shift - split, width)) * 4;
            writePixel(result, offset, blended[red], blended[center + 1], blended[blue + 2], Math.max(blended[red + 3], blended[center + 3], blended[blue + 3]));
          }
        }
      }
      return result;
    }
    const flash = type === "white_flash" ? envelope ** 2 : 0;
    for (let offset = 0; offset < size; offset += 4) {
      const alpha = blended[offset + 3], white = alpha * flash;
      writePixel(result, offset, blended[offset] * (1 - flash) + white, blended[offset + 1] * (1 - flash) + white, blended[offset + 2] * (1 - flash) + white, alpha);
    }
    return result;
  }
  function render(canvas, fromCanvas, toCanvas, type, progress) {
    if (!isEffect(type)) return false;
    if (!canvas || !fromCanvas || !toCanvas || !Number.isInteger(canvas.width) || !Number.isInteger(canvas.height) || canvas.width <= 0 || canvas.height <= 0 || [fromCanvas, toCanvas].some(source => source.width !== canvas.width || source.height !== canvas.height)) throw new Error("轉場畫布尺寸必須相同。");
    const target = canvas.getContext("2d"), from = fromCanvas.getContext("2d", { willReadFrequently: true }), to = toCanvas.getContext("2d", { willReadFrequently: true });
    if (!target || !from || !to) throw new Error("無法讀取轉場畫布。");
    const width = canvas.width, height = canvas.height;
    const pixels = applyPixels(from.getImageData(0, 0, width, height).data, to.getImageData(0, 0, width, height).data, width, height, type, progress);
    const output = target.createImageData(width, height); output.data.set(pixels); target.putImageData(output, 0, 0);
    return true;
  }
  const core = { EFFECT_TYPES, REFERENCE_TRANSITION_TYPES, isEffect, applyPixels, render };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.H3EditorTransitionEffects = core;
})(typeof window !== "undefined" ? window : globalThis);
