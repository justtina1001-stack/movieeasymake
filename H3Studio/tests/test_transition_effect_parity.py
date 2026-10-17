"""Compare the shipped browser pixel effects with CPU export on real RGBA data."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import numpy as np
from PIL import Image

from editor_animations import EFFECT_TRANSITION_TYPES, transition_surface


@unittest.skipUnless(shutil.which("node"), "Node is required to compare the browser and CPU renderers.")
class TransitionEffectParityTests(unittest.TestCase):
    def test_browser_pixels_match_export_for_translucent_gradient_edges_and_seek_positions(self):
        width, height = 127, 79
        rng = np.random.default_rng(71)
        left = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        right = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        left[:, :12, 3] = 0
        right[:9, :, 3] = 0
        progress = [0, .17, .33, .5, .81, 1]
        payload = {"width": width, "height": height, "effects": list(EFFECT_TRANSITION_TYPES),
                   "progress": progress, "left": base64.b64encode(left.tobytes()).decode(),
                   "right": base64.b64encode(right.tobytes()).decode()}
        module = Path(__file__).resolve().parents[1] / "static/editor_transition_effects.js"
        script = """
const fs = require('node:fs'), fx = require(process.argv[1]);
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
const left = new Uint8ClampedArray(Buffer.from(value.left, 'base64'));
const right = new Uint8ClampedArray(Buffer.from(value.right, 'base64'));
process.stdout.write(JSON.stringify(value.effects.flatMap(type => value.progress.map(progress =>
  Buffer.from(fx.applyPixels(left, right, value.width, value.height, type, progress)).toString('base64')))));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(module)], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=20, check=True)
        browser = iter(json.loads(result.stdout))
        with Image.fromarray(left) as outgoing, Image.fromarray(right) as incoming:
            for effect in EFFECT_TRANSITION_TYPES:
                for ratio in progress:
                    with self.subTest(effect=effect, progress=ratio), transition_surface(outgoing, incoming, effect, ratio) as frame:
                        actual = np.frombuffer(base64.b64decode(next(browser)), dtype=np.uint8).reshape(height, width, 4)
                        expected = np.asarray(frame)
                        difference = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
                        self.assertLessEqual(int(difference.max()), 1)


if __name__ == "__main__":
    unittest.main()
