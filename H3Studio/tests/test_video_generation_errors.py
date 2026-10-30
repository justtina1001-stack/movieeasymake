import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import JobManager
from comfy_client import ComfyClient, ComfyExecutionError
from domain import compile_request
from video_generation_errors import video_generation_error_fields


ALLOCATION_ERROR = (
    "Allocation on device 0 would exceed allowed memory. (out of memory) "
    "Currently allocated : 12.40 GiB Requested : 3.14 GiB Device limit : 15.93 GiB "
    "Free (according to CUDA): 0 bytes\nTIPS: If the workflow worked before you might "
    "have accidentally set the batch_size to a large number."
)


class VideoGenerationErrorTests(unittest.TestCase):
    def test_oom_has_actionable_chinese_explanation_and_exact_technical_detail(self):
        result = video_generation_error_fields(ALLOCATION_ERROR)
        self.assertEqual(result["error_code"], "gpu_out_of_memory")
        self.assertEqual(result["error_detail"], ALLOCATION_ERROR)
        self.assertIn("降低解析度", result["error"])
        self.assertIn("確認稿件後重試", result["error"])
        self.assertIn("不會自動重送", result["error"])
        self.assertNotIn("batch_size", result["error"])

    def test_oom_identification_supports_allocator_and_exception_type(self):
        for error in (
            "CUDA out of memory. Tried to allocate 3 GiB",
            "Allocation on device 0 would exceed allowed memory.",
            ComfyExecutionError("Allocation failed", {"exception_type": "torch.OutOfMemoryError"}),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(video_generation_error_fields(error)["error_code"], "gpu_out_of_memory")

    def test_cpu_allocation_and_unrelated_errors_keep_original_message(self):
        for detail in ("DefaultCPUAllocator: out of memory", "CPUAllocator: not enough memory",
                       "CPU out of memory", "out of memory", "CUDA allocation failed: invalid shape",
                       "素材上傳失敗", "model matrix shape mismatch", ""):
            with self.subTest(detail=detail):
                result = video_generation_error_fields(RuntimeError(detail))
                self.assertEqual(result["error"], detail)
                self.assertIsNone(result["error_code"])
                self.assertIsNone(result["error_detail"])

    def test_engine_node_identity_and_workflow_title_are_retained(self):
        error = ComfyExecutionError(ALLOCATION_ERROR, {"node_id": "16", "node_type": "SamplerCustomAdvanced"})
        workflow = {"16": {"class_type": "SamplerCustomAdvanced", "_meta": {"title": "Generate"}}}
        original = copy.deepcopy(workflow)
        result = video_generation_error_fields(error, workflow=workflow, current_node="stale title")
        self.assertEqual(result["error_node"], {"id": "16", "type": "SamplerCustomAdvanced", "title": "Generate"})
        self.assertEqual(workflow, original)

    def test_history_error_without_workflow_can_still_explain_failing_node(self):
        error = ComfyExecutionError(ALLOCATION_ERROR, {"node_id": "16", "node_type": "SamplerCustomAdvanced"})
        self.assertEqual(video_generation_error_fields(error)["error_node"],
                         {"id": "16", "type": "SamplerCustomAdvanced", "title": "SamplerCustomAdvanced"})


class VideoOOMJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_parent_keeps_failed_child_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent_id, child_id = "b" * 32, "c" * 32
            with patch("app.JOB_DIR", root), patch("app.OUTPUT_DIR", root):
                manager = JobManager(SimpleNamespace(path_for=lambda _asset_id: root / "source.mp4"), object())
                manager.jobs[parent_id] = {"id": parent_id, "status": "running", "active_child_id": child_id,
                                           "segments": [{"child_job_id": child_id, "source_asset_id": "split-video"}]}
                manager.jobs[child_id] = {"id": child_id, "status": "running", "error": "顯示卡記憶體不足",
                                          "error_code": "gpu_out_of_memory", "error_detail": ALLOCATION_ERROR,
                                          "error_node": {"id": "16", "type": "SamplerCustomAdvanced", "title": "Generate"}}

                async def failed_child(*_args):
                    manager.jobs[child_id]["status"] = "failed"
                    return manager.jobs[child_id]

                manager._wait_for_batch_child = AsyncMock(side_effect=failed_child)
                await manager._run_replacement_batch(parent_id, {"references": [{"video_asset_id": "d" * 32}]})
            parent = manager.jobs[parent_id]
            self.assertEqual(parent["status"], "failed")
            self.assertIn("第 1/1 段角色替換失敗", parent["error"])
            for key in ("error_code", "error_detail", "error_node"):
                self.assertEqual(parent[key], manager.jobs[child_id][key])

    async def test_failed_job_preserves_reviewed_request_workflow_and_seed_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {"mode": "t2v", "prompt": "A robot waves.", "duration": 15,
                       "megapixels": 0.98, "seed": 123456}
            original = copy.deepcopy(payload)

            async def fail(workflow, progress, _cancel):
                node_id = next(key for key, node in workflow.items() if node["class_type"] == "SamplerCustomAdvanced")
                await progress({"prompt_id": "failed-prompt", "current_node": node_id, "progress": 0})
                raise ComfyExecutionError(ALLOCATION_ERROR, {"node_id": node_id, "node_type": "SamplerCustomAdvanced",
                                                            "exception_type": "torch.OutOfMemoryError"})

            fake = SimpleNamespace(
                ensure_running=AsyncMock(), model_inventory=AsyncMock(return_value={
                    "fl2va": True, "text_encoder": True, "video_vae": True, "audio_vae": True,
                }), run_prompt=AsyncMock(side_effect=fail),
            )
            with patch("app.JOB_DIR", root), patch("app.OUTPUT_DIR", root):
                manager = JobManager(object(), fake)
                job = manager.create(compile_request(payload), payload)
                await manager.tasks[job["id"]]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error_code"], "gpu_out_of_memory")
            self.assertEqual(job["error_detail"], ALLOCATION_ERROR)
            self.assertEqual(job["error_node"]["title"], "Generate")
            self.assertEqual(job["error_node"]["type"], "SamplerCustomAdvanced")
            self.assertEqual(fake.run_prompt.await_count, 1)
            self.assertEqual(payload, original)
            self.assertEqual(json.loads((root / f"{job['id']}.request.json").read_text(encoding="utf-8")), original)
            workflow = json.loads((root / f"{job['id']}.workflow.json").read_text(encoding="utf-8"))
            condition = next(node["inputs"] for node in workflow.values() if node["class_type"] == "MiniMaxH3ImageToVideo")
            noise = next(node["inputs"] for node in workflow.values() if node["class_type"] == "RandomNoise")
            self.assertEqual((condition["width"], condition["height"], condition["length"]), (1344, 768, 362))
            self.assertEqual(noise["noise_seed"], original["seed"])

    async def test_reconciliation_stops_on_incomplete_error_history_without_resubmission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = {"status": {"completed": False, "status_str": "error", "messages": [["execution_error", {
                "node_id": "16", "node_type": "SamplerCustomAdvanced", "exception_message": ALLOCATION_ERROR,
                "exception_type": "torch.OutOfMemoryError",
            }]]}}
            fake = SimpleNamespace(get_history=AsyncMock(return_value=history),
                                   history_state=ComfyClient.history_state, run_prompt=AsyncMock())
            with patch("app.JOB_DIR", root), patch("app.OUTPUT_DIR", root):
                manager = JobManager(object(), fake)
                job_id = "a" * 32
                manager.jobs[job_id] = {"id": job_id, "status": "running", "prompt_id": "failed-prompt",
                                        "current_node": "Generate"}
                self.assertFalse(await asyncio.wait_for(manager.reconcile_job(job_id, wait=True), timeout=1))
            job = manager.jobs[job_id]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error_code"], "gpu_out_of_memory")
            self.assertEqual(job["error_node"], {"id": "16", "type": "SamplerCustomAdvanced", "title": "Generate"})
            self.assertEqual(fake.get_history.await_count, 1)
            fake.run_prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
