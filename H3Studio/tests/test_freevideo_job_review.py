"""FreeVideo's approved quality survives job creation and final review binding."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import JobManager
from domain import compile_request
from generation_review import GenerationReviewStore, GenerationReviewError, prompt_review_item
from tests.test_dialogue_compile import request_payload


class FreeVideoJobReviewTests(unittest.IsolatedAsyncioTestCase):
    def payload(self, quality="medium"):
        return request_payload("A purple flame rises against a completely black background.",
                               video_model="freevideo_vdn_h3", freevideo_quality=quality)

    async def test_record_and_final_review_keep_chosen_quality(self):
        payload = self.payload("high")
        compiled = compile_request(payload)
        self.assertEqual(prompt_review_item(compiled)["freevideo_quality"], "high")
        with tempfile.TemporaryDirectory() as temporary, patch("app.JOB_DIR", Path(temporary)):
            manager = JobManager(object(), object())
            manager._run = AsyncMock()
            job = manager.create(compiled, payload)
            await manager.tasks[job["id"]]
            self.assertEqual(job["freevideo_quality"], "high")
            self.assertEqual(job["video_model"], "freevideo_vdn_h3")

    async def test_changing_quality_requires_another_final_review(self):
        payload = self.payload()
        store = GenerationReviewStore()
        receipt = store.issue(payload, payload, compile_request(payload))["review_receipt"]
        with self.assertRaises(GenerationReviewError):
            store.claim({**payload, "freevideo_quality": "light", "review_receipt": receipt})
        _, approved = store.claim({**payload, "review_receipt": receipt})
        self.assertEqual(approved["compiled"].freevideo_quality, "medium")


if __name__ == "__main__":
    unittest.main()
