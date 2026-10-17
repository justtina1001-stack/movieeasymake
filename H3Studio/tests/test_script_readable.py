"""Readable views must preserve the script source and the exact engine snapshot."""
import copy
import unittest

from scriptwriting import readable_job


PERFORMANCE = """片名：武打測試
全片風格：精緻 3D CG
聲音規則：零 BGM
參考圖對應：姜子牙＝圖一；妲己＝圖二

1. 0.0–2.0s｜察覺來襲
鏡頭／景別：24mm 廣角中景，低機位。
地點：廢棄遺跡。
角色／位置與空間關係：姜子牙在中央，妲己在右後方三米。
動作／表情：姜子牙停步→眼神轉向右後方→右手扣住符咒；妲己抬起右手。
運鏡：慢速推近至姜子牙的半身。
VFX：妲己指尖先出現紅光，再沿右臂流動。
聲音：腳步突然停止，碎石回音與妖法低鳴。
對白：無。

2. 2.0–15.0s｜交鋒與收勢
鏡頭／景別：50mm 中景，固定機位。
地點：同一遺跡。
角色／位置與空間關係：姜子牙面向右後方，與妲己保持三米距離。
動作／表情：妲己推出右掌→姜子牙抬符咒抵擋→衝擊迫使兩人後移→雙腳站穩。
運鏡：固定至兩人收勢。
VFX：紅光接觸白色護盾，再碎成火花。
聲音：能量撞擊，衣料摩擦，最後只留環境回音。
對白：妲己：「別想走。」
"""


def storyboard_job():
    return {
        "id": "saved-job", "status": "completed", "action": "storyboard",
        "content": '{"title":"engine raw"}',
        "input": {"project": {"duration": 15, "content": PERFORMANCE}},
        "storyboard": {"title": "engine raw", "synopsis": "", "characters": [], "scenes": [
            {"title": "遺跡", "location": "遺跡", "time_of_day": "夜", "shots": [
                {"title": "交鋒", "duration": 15, "action": "Opening action. [Shot 2] At 00:02.000, continue.",
                 "dialogue": "", "speaker": "", "sound": "Stone echoes. Zero BGM.", "camera": "static", "shot_size": "wide"},
            ]},
        ]},
    }


class ReadableJobTests(unittest.TestCase):
    def test_storyboard_uses_full_source_script_without_altering_engine_or_history(self):
        saved = storyboard_job()
        before = copy.deepcopy(saved)
        result = readable_job(saved)
        self.assertEqual(result["readable_content"], PERFORMANCE)
        self.assertEqual(result["readable_content_source"], "screenplay")
        self.assertIn("原表演腳本", result["readable_content_note"])
        self.assertEqual(result["content"], before["content"])
        self.assertEqual(result["storyboard"], before["storyboard"])
        result["input"]["project"]["content"] = "changed in response"
        self.assertEqual(saved, before)

    def test_storyboard_falls_back_to_actual_cut_data_when_source_is_not_timed(self):
        saved = storyboard_job()
        saved["input"]["project"]["content"] = "追逐的故事摘要。"
        result = readable_job(saved)
        self.assertEqual(result["readable_content_source"], "storyboard")
        self.assertIn("Opening action.", result["readable_content"])
        self.assertIn("continue.", result["readable_content"])
        self.assertIn("未提供", result["readable_content"])
        self.assertEqual(result["content"], saved["content"])

    def test_screenplay_is_readable_but_raw_conversion_remains_available(self):
        saved = storyboard_job()
        saved.update(action="screenplay", content=PERFORMANCE, raw_content="model original dictionary")
        result = readable_job(saved)
        self.assertEqual(result["readable_content"], PERFORMANCE)
        self.assertEqual(result["raw_content"], saved["raw_content"])

    def test_incomplete_work_and_malformed_legacy_snapshots_are_not_treated_as_full_source(self):
        for status in ["queued", "running", "failed", "cancelled"]:
            saved = storyboard_job()
            saved["status"] = status
            self.assertEqual(readable_job(saved), saved)
        for job_input in [None, [], {"project": None}, {"project": "bad snapshot"}]:
            saved = storyboard_job()
            saved["input"] = job_input
            result = readable_job(saved)
            self.assertEqual(result["readable_content_source"], "storyboard")
        saved = storyboard_job()
        saved["input"]["project"]["content"] = PERFORMANCE.replace("2.0–15.0s", "2.0–17.0s")
        self.assertEqual(readable_job(saved)["readable_content_source"], "storyboard")


if __name__ == "__main__":
    unittest.main()
