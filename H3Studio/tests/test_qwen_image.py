import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import app as studio
from domain import RequestError
from qwen_image import compile_image_request, build_image_workflow, ImageJobManager, is_image_lora, image_capabilities
from queue_presentation import build_queue_view
from qwen_image_models import MODEL_FILES

IMAGE_ID = "a" * 32


class ImageCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_engine_lists_only_dedicated_adapters_and_optional_loader_failure_is_nonfatal(self):
        missing_loader = False
        async def info(request):
            name = request.match_info["name"]
            if name == "LoraLoaderModelOnly":
                if missing_loader:
                    raise web.HTTPNotFound()
                field, files = "lora_name", ["h3.safetensors", r"qwen_image_2_1\style.safetensors", "qwen_image_2_1/../old.safetensors"]
            elif name == "TextEncodeQwenImage21":
                return web.json_response({name:{"input":{}}})
            else:
                index, field = {"UNETLoader":(0,"unet_name"),"CLIPLoader":(1,"clip_name"),"VAELoader":(2,"vae_name")}[name]
                files = [MODEL_FILES[index][1]]
            return web.json_response({name:{"input":{"required":{field:[files]}}}})
        engine = web.Application()
        engine.router.add_get('/object_info/{name}',info)
        server = TestServer(engine)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        comfy = SimpleNamespace(base_url=str(server.make_url('/')).rstrip('/'),mode='remote',auth_headers=lambda:{})
        result = await image_capabilities(comfy)
        self.assertTrue(result['ready'])
        self.assertTrue(result['lora_supported'])
        self.assertEqual(result['loras'],[r"qwen_image_2_1\style.safetensors"])
        missing_loader = True
        result = await image_capabilities(comfy)
        self.assertTrue(result['ready'])
        self.assertFalse(result['lora_supported'])


class ImageCompileTests(unittest.TestCase):
    def test_lora_routes_only_model_and_zero_strength_bypasses(self):
        payload = {"prompt":"保持人物", "lora_name":"qwen_image_2_1/style.safetensors", "lora_strength":0.65}
        compiled = compile_image_request(payload)
        graph = build_image_workflow(compiled, {}, "test")
        self.assertEqual(graph["9"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(graph["9"]["inputs"]["strength_model"], 0.65)
        self.assertEqual(graph["6"]["inputs"]["model"], ["9", 0])
        self.assertEqual(graph["4"]["inputs"]["clip"], ["2", 0])
        compiled["lora_strength"] = 0
        graph = build_image_workflow(compiled, {}, "test")
        self.assertNotIn("9", graph)
        self.assertEqual(graph["6"]["inputs"]["model"], ["1", 0])

    def test_lora_rejects_other_families_traversal_and_nonfinite_strengths(self):
        self.assertTrue(is_image_lora(r"qwen_image_2_1\角色.safetensors"))
        for name in ("h3studio_custom/a.safetensors", "qwen_image_2_1/../a.safetensors",
                     "qwen_image_2_1/a.pt", "qwen_image_2_1/C:/a.safetensors", None, ["x"]):
            with self.subTest(name=name), self.assertRaises(RequestError):
                compile_image_request({"prompt":"x", "lora_name":name})
        for strength in (True, "1", float("nan"), float("inf"), -0.1, 1.6):
            with self.subTest(strength=strength), self.assertRaises(RequestError):
                compile_image_request({"prompt":"x", "lora_strength":strength})

    def test_edit_preserves_explicit_reference_order(self):
        first, second = "b"*32, "a"*32
        job = compile_image_request({"prompt":"改圖 1", "mode":"edit", "image_asset_ids":[first,second]})
        graph = build_image_workflow(job, {first:"main.png",second:"style.png"}, "test")
        self.assertEqual(graph["12"]["inputs"]["image"], "main.png")
        self.assertEqual(graph["14"]["inputs"]["image"], "style.png")

    def test_generation_matches_official_sampling_and_transparent_png_path(self):
        compiled = compile_image_request({"prompt": "紅色小龍", "transparent": True, "seed_auto": False, "seed": 42})
        graph = build_image_workflow(compiled, {}, "test")
        self.assertIn("alpha channel", graph["4"]["inputs"]["prompt"])
        self.assertIn("紅色小龍", graph["4"]["inputs"]["prompt"])
        self.assertEqual(graph["6"]["inputs"]["cfg"], 1)
        self.assertEqual(graph["6"]["inputs"]["latent_image"], ["5", 0])
        self.assertEqual(graph["8"]["class_type"], "SaveImage")
        self.assertNotIn("lora", json.dumps(graph).lower())

    def test_edit_uses_reference_latent_and_restores_alpha(self):
        compiled = compile_image_request({"prompt": "換成藍色", "mode": "edit", "image_asset_ids": [IMAGE_ID]})
        graph = build_image_workflow(compiled, {IMAGE_ID: "H3Gateway/user/reference.png"}, "test")
        self.assertEqual(graph["6"]["inputs"]["latent_image"], ["4", 2])
        alpha_id = graph["4"]["inputs"]["images.image_1"][0]
        self.assertEqual(graph[alpha_id]["class_type"], "JoinImageWithAlpha")
        load_id = graph[alpha_id]["inputs"]["image"][0]
        self.assertEqual(graph[load_id]["inputs"]["image"], "H3Gateway/user/reference.png")

    def test_auto_seed_and_validation(self):
        with patch("qwen_image.secrets.randbelow", side_effect=[20, 30]):
            self.assertEqual(compile_image_request({"prompt": "one"})["seed"], 20)
            self.assertEqual(compile_image_request({"prompt": "two"})["seed"], 30)
        for payload in ([], {}, {"prompt": " "}, {"prompt":"x", "width":513},
                        {"prompt":"x", "width":True}, {"prompt":"x", "seed_auto":"false"},
                        {"prompt":"x", "seed_auto":False, "seed":-1}, {"prompt":"x", "steps":51},
                        {"prompt":"x", "mode":"edit"}, {"prompt":"x", "image_asset_ids":[IMAGE_ID]},
                        {"prompt":"x", "mode":"edit", "image_asset_ids":[IMAGE_ID]*11}):
            with self.subTest(payload=payload), self.assertRaises(RequestError):
                compile_image_request(payload)

    def test_image_jobs_are_visible_in_shared_queue(self):
        snapshot = {"available":True, "running_count":1, "pending_count":1,
                    "running":[{"prompt_id":"other", "position":1}],
                    "pending":[{"prompt_id":"mine", "position":1}]}
        queue = build_queue_view(snapshot, {}, {}, {}, image_jobs={"job":{"prompt_id":"mine", "status":"running", "name":"test"}})
        self.assertEqual(queue["jobs"]["job"]["kind"], "image")
        self.assertEqual(queue["jobs"]["job"]["ahead_count"], 1)


class ImageAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.multiple(studio, DATA_DIR=self.root, ASSET_DIR=self.root/"assets",
            JOB_DIR=self.root/"jobs", OUTPUT_DIR=self.root/"outputs", CONFIG_PATH=self.root/"config.json"))
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.manager = self.app["image_jobs"]

    async def test_upload_keeps_qwen_alpha_and_preserves_existing_video_behavior(self):
        content = io.BytesIO()
        Image.new("RGBA", (32,32), (255,0,0,0)).save(content, format="PNG")
        for kind, filled in (("qwen-image-reference",False), ("reference",True)):
            data = FormData()
            data.add_field("kind", kind)
            data.add_field("file",content.getvalue(), filename="alpha.png", content_type="image/png")
            response = await self.client.post("/api/assets", data=data)
            self.assertEqual(response.status,200)
            asset = await response.json()
            self.assertEqual(asset["transparency_filled"],filled)
            if not filled:
                with Image.open(self.app["assets"].path_for(asset["id"])) as image:
                    self.assertEqual(image.getpixel((0,0))[3],0)

    async def test_job_waits_for_shared_gpu_and_exact_cancellation(self):
        await self.manager.gpu_lock.acquire()
        response = await self.client.post("/api/images/jobs",json={"prompt":"test"})
        self.assertEqual(response.status,202)
        job = await response.json()
        await asyncio.sleep(0)
        self.assertEqual(self.manager.jobs[job["id"]]["status"],"queued")
        with patch.object(self.manager.comfy,"interrupt",new=AsyncMock()) as interrupt:
            response = await self.client.post(f"/api/images/jobs/{job['id']}/cancel")
            self.assertEqual(response.status,200)
            interrupt.assert_not_called()
        self.manager.gpu_lock.release()
        self.assertEqual(self.manager.jobs[job["id"]]["status"],"cancelled")

    async def test_real_png_completion_and_reuse_preserves_alpha(self):
        content = io.BytesIO()
        Image.new("RGBA", (32,32), (0,100,250,90)).save(content,format="PNG")
        async def run(workflow, callback, event):
            await callback({"prompt_id":"owned", "status":"running", "preview_bytes":b"not json"})
            return "owned", {"outputs":{"8":{"images":[{"filename":"final.png","type":"output","subfolder":""}]}}}
        with patch("qwen_image.image_capabilities",new=AsyncMock(return_value={"ready":True})), \
             patch.object(self.manager.comfy,"ensure_running",new=AsyncMock()), \
             patch.object(self.manager.comfy,"run_prompt",side_effect=run), \
             patch.object(self.manager.comfy,"fetch_output",new=AsyncMock(return_value=(content.getvalue(),"image/png"))):
            response = await self.client.post("/api/images/jobs",json={"prompt":"透明的小龍"})
            job = await response.json()
            await self.manager.tasks[job["id"]]
        final = self.manager.jobs[job["id"]]
        self.assertEqual(final["status"],"completed")
        self.assertTrue(final["rgba"])
        response = await self.client.get(f"/api/images/jobs/{job['id']}/image?download=1")
        self.assertEqual(response.status,200)
        self.assertEqual(await response.read(),content.getvalue())
        response = await self.client.post(f"/api/images/jobs/{job['id']}/reference")
        asset = await response.json()
        with Image.open(self.app["assets"].path_for(asset["id"])) as image:
            self.assertEqual(image.getpixel((0,0))[3],90)

    async def test_missing_model_fails_without_submission(self):
        with patch("qwen_image.image_capabilities",new=AsyncMock(return_value={"ready":False,"error":"missing"})), \
             patch.object(self.manager.comfy,"ensure_running",new=AsyncMock()), \
             patch.object(self.manager.comfy,"run_prompt",new=AsyncMock()) as run:
            job = self.manager.create({"prompt":"test"})
            await self.manager.tasks[job["id"]]
            run.assert_not_called()
            self.assertEqual(job["status"],"failed")

    async def test_missing_lora_fails_without_sending_or_uploading(self):
        with patch("qwen_image.image_capabilities",new=AsyncMock(return_value={"ready":True,"lora_supported":True,"loras":[]})), \
             patch.object(self.manager.comfy,"ensure_running",new=AsyncMock()), \
             patch.object(self.manager.comfy,"upload_asset",new=AsyncMock()) as upload, \
             patch.object(self.manager.comfy,"run_prompt",new=AsyncMock()) as run:
            job = self.manager.create({"prompt":"test", "lora_name":"qwen_image_2_1/missing.safetensors"})
            await self.manager.tasks[job["id"]]
            run.assert_not_called()
            upload.assert_not_called()
            self.assertEqual(job["status"],"failed")
            self.assertIn("LoRA",job["error"])

    async def test_recovery_reads_existing_history_without_resubmission(self):
        job_id = "b" * 32
        self.manager.jobs[job_id] = {"id":job_id,"status":"running","prompt_id":"existing"}
        history = {"status":{"completed":True,"status_str":"success"}}
        with patch.object(self.manager.comfy,"get_history",new=AsyncMock(return_value=history)), \
             patch.object(self.manager.comfy,"run_prompt",new=AsyncMock()) as run, \
             patch.object(self.manager,"_complete",new=AsyncMock()) as complete:
            await self.manager.recover(job_id)
            complete.assert_awaited_once_with(job_id,history)
            run.assert_not_called()

    async def test_bad_payload_and_missing_job_return_json_errors(self):
        response = await self.client.post("/api/images/jobs",json=[])
        self.assertEqual(response.status,400)
        self.assertIn("error",await response.json())
        response = await self.client.post("/api/images/jobs/not-a-job/cancel")
        self.assertEqual(response.status,404)

    def completed_image(self, job_id=IMAGE_ID):
        self.manager.jobs[job_id] = {"id":job_id,"status":"completed","created_at":"2026-09-21","name":"測試圖片"}
        self.manager.update(job_id)
        path = self.manager.output_dir / f"{job_id}.png"
        Image.new('RGBA',(32,32),(50,100,200,100)).save(path)
        (self.manager.job_dir / f"{job_id}.workflow.json").write_text('{}')
        return path

    async def test_delete_removes_local_files_but_keeps_reference_and_other_job(self):
        path = self.completed_image()
        other = self.completed_image('b'*32)
        response = await self.client.post(f'/api/images/jobs/{IMAGE_ID}/reference')
        asset = await response.json()
        reference = self.app['assets'].path_for(asset['id'])
        reference_bytes = reference.read_bytes()
        response = await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')
        self.assertEqual(response.status,200)
        self.assertTrue((await response.json())['comfy_output_preserved'])
        self.assertFalse(path.exists())
        self.assertFalse((self.manager.job_dir / f'{IMAGE_ID}.json').exists())
        self.assertFalse((self.manager.job_dir / f'{IMAGE_ID}.workflow.json').exists())
        self.assertEqual(reference.read_bytes(),reference_bytes)
        self.assertTrue(other.exists())
        response = await self.client.get('/api/images/jobs')
        self.assertEqual([j['id'] for j in (await response.json())['items']],['b'*32])
        reloaded = ImageJobManager(self.manager.comfy,self.root,self.manager.gpu_lock,self.app['assets'])
        self.assertNotIn(IMAGE_ID,reloaded.jobs)
        self.assertEqual((await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')).status,404)
        self.assertNotEqual((await self.client.get(f'/api/images/jobs/{IMAGE_ID}/image')).status,200)

    async def test_delete_rejects_active_status_and_unfinished_task(self):
        path = self.completed_image()
        for status in ('queued','preparing','running'):
            self.manager.jobs[IMAGE_ID]['status']=status
            response = await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')
            self.assertEqual(response.status,400)
            self.assertTrue(path.exists())
        self.manager.jobs[IMAGE_ID]['status']='completed'
        task = asyncio.create_task(asyncio.sleep(60))
        self.manager.tasks[IMAGE_ID]=task
        try:
            self.assertEqual((await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')).status,400)
            self.assertTrue(path.exists())
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_delete_waits_for_reference_copy_lock_and_ignores_metadata_paths(self):
        path = self.completed_image()
        protected = self.root / 'protected.png'
        protected.write_bytes(b'preserve')
        self.manager.jobs[IMAGE_ID]['local_output']=str(protected)
        self.manager.jobs[IMAGE_ID]['output']={'filename':str(protected)}
        await self.manager.file_lock.acquire()
        task = asyncio.create_task(self.manager.delete(IMAGE_ID))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(path.exists())
        self.manager.file_lock.release()
        await task
        self.assertEqual(protected.read_bytes(),b'preserve')

    async def test_file_error_keeps_job_record_for_retry(self):
        path = self.completed_image()
        with patch.object(Path,'unlink',side_effect=PermissionError('file in use')):
            response = await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')
        self.assertEqual(response.status,400)
        self.assertIn(IMAGE_ID,self.manager.jobs)
        self.assertTrue(path.exists())
        self.assertTrue((self.manager.job_dir / f'{IMAGE_ID}.json').exists())
        self.assertEqual((await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')).status,200)
