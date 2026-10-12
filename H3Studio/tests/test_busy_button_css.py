"""Browser-level regressions for disabled loading buttons in shot cards."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def chromium_browser():
    candidates = [os.environ.get("H3_CSS_TEST_BROWSER"), shutil.which("chromium"),
                  shutil.which("chromium-browser"), shutil.which("google-chrome"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    return next((value for value in candidates if value and Path(value).is_file()), None)


class BusyButtonCSSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        browser = chromium_browser()
        if browser is None:
            raise unittest.SkipTest("A local Chromium browser is required for computed CSS checks.")
        source = Path(os.environ.get("H3_BUSY_BUTTON_CSS", Path(__file__).resolve().parents[1] / "static/styles.css"))
        css = source.read_text(encoding="utf-8")
        fixture = """<!doctype html><meta charset="utf-8"><style>CSS_SOURCE</style>
<div class="panel shortfilm-scene-card" style="position:relative;width:1000px">
  <div class="shortfilm-shot-card">
    <div class="shortfilm-shot-actions">
      <button id="busyDisabled" class="button primary small" disabled aria-busy="true">生成此鏡頭</button>
      <button id="busyEnabled" class="button ghost small" aria-busy="true">預覽提示詞</button>
      <a id="busyLink" class="button secondary small" aria-busy="true">正在整理</a>
      <button id="idleReady" class="button primary small">生成此鏡頭</button>
      <button id="idleDisabled" class="button primary small" disabled>等待生成</button>
    </div>
    <p id="feedback" class="render-note shortfilm-shot-feedback" role="status" aria-live="polite"></p>
    <p id="failure" class="render-note shortfilm-shot-feedback error">請確認素材後重試。</p>
    <p id="hidden" class="render-note shortfilm-shot-feedback hidden">已結束的提示</p>
  </div>
</div>
<script>
document.getElementById('feedback').textContent = '正在準備參考素材。' + 'LongAssetIdentifier'.repeat(100);
const facts = {};
for (const id of ['busyDisabled','busyEnabled','busyLink','idleReady','idleDisabled']) {
  const element = document.getElementById(id), style = getComputedStyle(element);
  const sheen = getComputedStyle(element, '::before'), spinner = getComputedStyle(element, '::after');
  facts[id] = {position:style.position,overflowX:style.overflowX,overflowY:style.overflowY,isolation:style.isolation,
    width:element.getBoundingClientRect().width,sheenWidth:parseFloat(sheen.width),sheenPosition:sheen.position,
    beforeContent:sheen.content,afterContent:spinner.content,spinnerWidth:spinner.width,animation:spinner.animationName,
    cursor:style.cursor,opacity:style.opacity};
}
for (const id of ['feedback','failure','hidden']) {
  const element=document.getElementById(id),style=getComputedStyle(element);
  facts[id]={width:element.getBoundingClientRect().width,parentWidth:element.parentElement.getBoundingClientRect().width,
    scrollWidth:element.scrollWidth,clientWidth:element.clientWidth,overflowWrap:style.overflowWrap,
    whiteSpace:style.whiteSpace,color:style.color,display:style.display};
}
const result=document.createElement('pre'); result.id='css-facts'; result.textContent=JSON.stringify(facts); document.body.append(result);
</script>""".replace("CSS_SOURCE", css)
        with tempfile.TemporaryDirectory(prefix="h3-button-css-") as temporary:
            directory = Path(temporary)
            page = directory / "fixture.html"
            page.write_text(fixture, encoding="utf-8")
            process = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions",
                                      "--user-data-dir=" + str(directory / "profile"),
                                      "--dump-dom", page.as_uri()], capture_output=True, timeout=30)
            rendered = process.stdout.decode("utf-8", errors="replace")
            found = re.search(r'<pre id="css-facts">(.*?)</pre>', rendered, re.S)
            if found is None:
                raise AssertionError("Headless browser did not return computed CSS facts: "
                                     + process.stderr.decode("utf-8", errors="replace")[-1000:])
            cls.facts = json.loads(html.unescape(found.group(1)))

    def test_busy_buttons_keep_their_own_containing_block_even_while_disabled(self):
        for id in ("busyDisabled", "busyEnabled", "busyLink"):
            with self.subTest(button=id):
                actual = self.facts[id]
                self.assertEqual(actual["position"], "relative")
                self.assertEqual(actual["overflowX"], "hidden")
                self.assertEqual(actual["overflowY"], "hidden")
                self.assertEqual(actual["isolation"], "isolate")
                self.assertEqual(actual["sheenPosition"], "absolute")
                self.assertLessEqual(abs(actual["sheenWidth"] - actual["width"]), 3)
                self.assertLess(actual["sheenWidth"], 350)

    def test_spinner_stays_small_and_both_decorations_disappear_without_busy_state(self):
        self.assertEqual(self.facts["busyDisabled"]["spinnerWidth"], "10px")
        self.assertEqual(self.facts["busyDisabled"]["animation"], "busy-spin")
        self.assertEqual(self.facts["busyDisabled"]["cursor"], "progress")
        self.assertEqual(self.facts["busyDisabled"]["opacity"], "0.82")
        for id in ("idleReady", "idleDisabled"):
            self.assertEqual(self.facts[id]["beforeContent"], "none")
            self.assertEqual(self.facts[id]["afterContent"], "none")

    def test_shot_feedback_wraps_inside_the_card_and_hidden_feedback_occupies_no_space(self):
        actual = self.facts["feedback"]
        self.assertEqual(actual["overflowWrap"], "anywhere")
        self.assertEqual(actual["whiteSpace"], "normal")
        self.assertGreater(actual["width"], actual["parentWidth"] * 0.85)
        self.assertLessEqual(actual["scrollWidth"], actual["clientWidth"])
        self.assertEqual(self.facts["failure"]["color"], "rgb(255, 176, 187)")
        self.assertEqual(self.facts["hidden"]["display"], "none")
        self.assertEqual(self.facts["hidden"]["width"], 0)


if __name__ == "__main__":
    unittest.main()
