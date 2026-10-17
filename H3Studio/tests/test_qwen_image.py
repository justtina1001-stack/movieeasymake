import asyncio
import ast
import io
import json
import math
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
from qwen_image_models import MODEL_FILES, TURBO_MODEL_FILE, TURBO_REVISION

IMAGE_ID = "a" * 32


def variant_schemas(standard=True, turbo=True):
    unets = ([MODEL_FILES[0][1]] if standard else []) + ([TURBO_MODEL_FILE[1]] if turbo else [])
    return {
        "TextEncodeQwenImage21": {"input": {}, "output": ["CONDITIONING", "CONDITIONING", "LATENT"]},
        "UNETLoader": {"input": {"required": {"unet_name": [unets]}}, "output": ["MODEL"]},
        "CLIPLoader": {"input": {"required": {"clip_name": [[MODEL_FILES[1][1]]]}}, "output": ["CLIP"]},
        "VAELoader": {"input": {"required": {"vae_name": [[MODEL_FILES[2][1]]]}}, "output": ["VAE"]},
        "ManualSigmas": {"input": {"required": {"sigmas": ["STRING"]}}, "output": ["SIGMAS"]},
        "KSamplerSelect": {"input": {"required": {"sampler_name": [["euler"]]}}, "output": ["SAMPLER"]},
        "SamplerCustom": {"input": {"required": {name: [kind] for name, kind in {
            "model": "MODEL", "add_noise": "BOOLEAN", "noise_seed": "INT", "cfg": "FLOAT",
            "positive": "CONDITIONING", "negative": "CONDITIONING", "sampler": "SAMPLER",
            "sigmas": "SIGMAS", "latent_image": "LATENT"}.items()}}, "output": ["LATENT", "LATENT"]},
        "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[]]}}},
    }


class ImageCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def engine(self, schemas):
        async def info(request):
            name = request.match_info["name"]
            if name not in schemas:
                raise web.HTTPNotFound()
            return web.json_response({name: schemas[name]})
        engine = web.Application()
        engine.router.add_get("/object_info/{name}", info)
        server = TestServer(engine)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return SimpleNamespace(base_url=str(server.make_url("/")).rstrip("/"), mode="remote", auth_headers=lambda: {})

    async def test_turbo_only_engine_is_ready_without_changing_legacy_model_inventory(self):
        result = await image_capabilities(await self.engine(variant_schemas(standard=False)))
        self.assertTrue(result["ready"])
        self.assertTrue(result["model_variant_supported"])
        self.assertEqual([item["filename"] for item in result["models"]], [model[1] for model in MODEL_FILES])
        self.assertFalse(result["models"][0]["installed"])
        self.assertFalse(result["variants"][0]["ready"])
        self.assertTrue(result["variants"][1]["ready"])
        self.assertEqual(result["variants"][1]["steps"], 8)
        self.assertIsNone(result["error"])

    async def test_turbo_checks_sigma_sampler_and_edit_latent_schema_contracts(self):
        schemas = variant_schemas()
        comfy = await self.engine(schemas)
        self.assertTrue((await image_capabilities(comfy))["variants"][1]["ready"])
        for name, replacement in (
            ("ManualSigmas", {"input": {"required": {"sigmas": ["STRING"]}}, "output": ["FLOAT"]}),
            ("ManualSigmas", {"input": {"required": {"sigmas": ["STRING"]}}, "output": None}),
            ("SamplerCustom", {"input": {"required": {}}, "output": ["LATENT"]}),
            ("KSamplerSelect", {"input": {"required": {"sampler_name": [["heun"]]}}, "output": ["SAMPLER"]}),
            ("TextEncodeQwenImage21", {"input": {}, "output": ["CONDITIONING", "CONDITIONING", "IMAGE"]}),
        ):
            with self.subTest(node=name):
                original = schemas[name]
                schemas[name] = replacement
                result = await image_capabilities(comfy)
                self.assertTrue(result["variants"][0]["ready"])
                self.assertFalse(result["variants"][1]["ready"])
                self.assertIn("節點", result["variants"][1]["error"])
                schemas[name] = original
        del schemas["ManualSigmas"]
        result = await image_capabilities(comfy)
        self.assertTrue(result["ready"])
        self.assertFalse(result["variants"][1]["ready"])

    async def test_native_v3_combo_sampler_and_model_choices_are_supported(self):
        schemas = variant_schemas()
        for name, field in (("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"),
                            ("VAELoader", "vae_name"), ("KSamplerSelect", "sampler_name")):
            options = schemas[name]["input"]["required"][field][0]
            schemas[name]["input"]["required"][field] = ["COMBO", {"options": options, "multiselect": False}]
        comfy = await self.engine(schemas)
        result = await image_capabilities(comfy)
        self.assertTrue(result["variants"][0]["ready"])
        self.assertTrue(result["variants"][1]["ready"])
        self.assertTrue(all(model["installed"] for model in result["models"]))
        schemas["KSamplerSelect"]["input"]["required"]["sampler_name"] = ["COMBO", {"options": ["heun"], "multiselect": False}]
        result = await image_capabilities(comfy)
        self.assertTrue(result["variants"][0]["ready"])
        self.assertFalse(result["variants"][1]["ready"])

    async def test_malformed_v3_sampler_combo_does_not_enable_turbo(self):
        schemas = variant_schemas()
        comfy = await self.engine(schemas)
        for spec in (["COMBO"], ["COMBO", None], ["COMBO", {"options": "euler"}],
                     ["COMBO", {"options": None}], ["COMBO", {"options": ["euler", {}]}],
                     ["COMBO", {"options": ["euler"], "multiselect": True}]):
            with self.subTest(spec=spec):
                schemas["KSamplerSelect"]["input"]["required"]["sampler_name"] = spec
                result = await image_capabilities(comfy)
                self.assertTrue(result["variants"][0]["ready"])
                self.assertFalse(result["variants"][1]["ready"])
                self.assertIn("節點", result["variants"][1]["error"])

    async def test_partial_local_turbo_file_does_not_enable_turbo(self):
        comfy = await self.engine(variant_schemas())
        comfy.mode, comfy.comfy_dir = "local", Path("test-model-host")
        sizes = {model[1]: model[2] for model in MODEL_FILES}
        sizes[TURBO_MODEL_FILE[1]] = TURBO_MODEL_FILE[2] - 1
        original_stat, original_is_file = Path.stat, Path.is_file
        def file_stat(path, *args, **kwargs):
            return SimpleNamespace(st_size=sizes[path.name]) if path.name in sizes else original_stat(path, *args, **kwargs)
        def is_file(path):
            return True if path.name in sizes else original_is_file(path)
        with patch.object(Path, "stat", file_stat), patch.object(Path, "is_file", is_file):
            result = await image_capabilities(comfy)
        self.assertTrue(result["variants"][0]["ready"])
        self.assertFalse(result["variants"][1]["ready"])
        self.assertIn("尚未齊全", result["variants"][1]["error"])

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
    def test_standard_default_and_legacy_workflow_keep_existing_sampling(self):
        compiled = compile_image_request({"prompt": "原有圖片", "steps": 25})
        self.assertEqual(compiled["model_variant"], "standard")
        self.assertEqual(compiled["steps"], 25)
        compiled.pop("model_variant")
        graph = build_image_workflow(compiled, {}, "legacy")
        self.assertEqual(graph["1"]["inputs"]["unet_name"], MODEL_FILES[0][1])
        self.assertEqual(graph["6"]["class_type"], "KSampler")
        self.assertEqual(graph["6"]["inputs"]["scheduler"], "simple")

    def test_turbo_normalizes_older_step_setting_and_uses_final_sigmas(self):
        compiled = compile_image_request({"prompt": "官方加速", "model_variant": "turbo", "steps": 25,
                                          "seed_auto": False, "seed": 42})
        self.assertEqual(compiled["steps"], 8)
        self.assertEqual(TURBO_REVISION, "72a49122613a4f9afbcc443426d6c765c0c383cf")
        graph = build_image_workflow(compiled, {}, "turbo")
        self.assertEqual(graph["1"]["inputs"]["unet_name"], TURBO_MODEL_FILE[1])
        self.assertEqual(graph["2"]["inputs"]["clip_name"], MODEL_FILES[1][1])
        self.assertEqual(graph["3"]["inputs"]["vae_name"], MODEL_FILES[2][1])
        self.assertEqual(graph["6"]["class_type"], "SamplerCustom")
        self.assertEqual(graph["6"]["inputs"]["latent_image"], ["5", 0])
        self.assertEqual(graph["6"]["inputs"]["noise_seed"], 42)
        self.assertTrue(graph["6"]["inputs"]["add_noise"])
        self.assertEqual(graph["6"]["inputs"]["cfg"], 1)
        sigmas_id = graph["6"]["inputs"]["sigmas"][0]
        sigmas = [float(value.strip()) for value in graph[sigmas_id]["inputs"]["sigmas"].split(",")]
        self.assertEqual(sigmas, [1, .978453, .95418, .926626, .89508, .845148, .704534, .414568, 0])
        self.assertEqual(len(sigmas) - 1, compiled["steps"])
        self.assertTrue(all(left > right for left, right in zip(sigmas, sigmas[1:])))
        sampler_id = graph["6"]["inputs"]["sampler"][0]
        self.assertEqual(graph[sampler_id]["inputs"]["sampler_name"], "euler")
        self.assertNotIn("scheduler", graph["6"]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["samples"], ["6", 0])
        self.assertEqual(graph["8"]["class_type"], "SaveImage")

    def test_turbo_edit_reuses_encoder_latent_without_reference_node_collisions(self):
        refs = [f"{index:032x}" for index in range(1, 11)]
        compiled = compile_image_request({"prompt": "保持透明", "mode": "edit", "model_variant": "turbo",
                                          "transparent": True, "image_asset_ids": refs})
        graph = build_image_workflow(compiled, {asset: f"reference-{index}.png" for index, asset in enumerate(refs)}, "edit")
        self.assertEqual(graph["6"]["inputs"]["latent_image"], ["4", 2])
        self.assertEqual(graph["40"]["class_type"], "ManualSigmas")
        self.assertEqual(graph["41"]["class_type"], "KSamplerSelect")
        self.assertIn("alpha channel", graph["4"]["inputs"]["prompt"])
        self.assertEqual(graph["4"]["inputs"]["images.image_10"], ["31", 0])
        self.assertEqual(graph["31"]["class_type"], "JoinImageWithAlpha")

    def test_native_cpu_empty_latent_correction_matches_qwen_grid_and_keeps_edit_grid(self):
        # Evaluate only the installed core's small latent-shape helpers, without
        # importing its server/model management or executing a sampling graph.
        core = Path(__file__).resolve().parents[2] / "ComfyUI" / "comfy"
        if not all((core / name).is_file() for name in ("sample.py", "utils.py", "latent_formats.py")):
            self.skipTest("Installed ComfyUI source is needed for the native CPU contract check")
        try:
            import torch
        except ImportError:
            self.skipTest("CPU PyTorch is needed for the native latent contract check")
        namespace = {"torch": torch, "math": math}
        def core_definition(filename, name):
            tree = ast.parse((core / filename).read_text(encoding="utf-8"))
            definition = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name)
            module = ast.Module(body=[definition], type_ignores=[])
            exec(compile(module, str(core / filename), "exec"), namespace)
            return namespace[name]
        repeat = core_definition("utils.py", "repeat_to_batch_size")
        resize = core_definition("utils.py", "common_upscale")
        namespace["comfy"] = SimpleNamespace(utils=SimpleNamespace(repeat_to_batch_size=repeat, common_upscale=resize))
        fix_latent = core_definition("sample.py", "fix_empty_latent_channels")
        core_definition("latent_formats.py", "LatentFormat")
        qwen_format = core_definition("latent_formats.py", "QwenImage21")
        with patch.object(torch.cuda, "_lazy_init", side_effect=AssertionError("CPU contract check must not use CUDA")):
            with torch.device("cpu"):
                latent_format = qwen_format()
            model = SimpleNamespace(get_model_object=lambda _: latent_format)
            generated = fix_latent(model, torch.zeros((1, 4, 96, 168), device="cpu"), downscale_ratio_spacial=8)
            self.assertEqual(tuple(generated.shape), (1, 64, 48, 84))
            self.assertEqual(str(generated.device), "cpu")
            reference = torch.zeros((1, 64, 48, 84), device="cpu")
            self.assertIs(fix_latent(model, reference), reference)

    def test_unknown_variant_and_active_turbo_lora_are_rejected_explicitly(self):
        for variant in (None, True, "fast", [], {}):
            with self.subTest(variant=variant), self.assertRaises(RequestError):
                compile_image_request({"prompt": "x", "model_variant": variant})
        payload = {"prompt": "x", "model_variant": "turbo", "lora_name": "qwen_image_2_1/mfd.safetensors"}
        with self.assertRaisesRegex(RequestError, "Turbo.*LoRA"):
            compile_image_request(payload)
        compiled = compile_image_request({**payload, "lora_strength": 0})
        graph = build_image_workflow(compiled, {}, "disabled-lora")
        self.assertNotIn("9", graph)
        compiled["lora_strength"] = 1
        with self.assertRaisesRegex(RequestError, "Turbo.*LoRA"):
            build_image_workflow(compiled, {}, "reloaded-lora")

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

    async def test_turbo_job_saves_actual_variant_and_steps_before_shared_queue_runs(self):
        await self.manager.gpu_lock.acquire()
        try:
            response = await self.client.post("/api/images/jobs", json={"prompt": "官方加速", "model_variant": "turbo", "steps": 25})
            self.assertEqual(response.status, 202)
            job = await response.json()
            saved = json.loads((self.manager.job_dir / f"{job['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual((saved["model_variant"], saved["steps"]), ("turbo", 8))
            self.assertEqual(saved["status"], "queued")
            await self.client.post(f"/api/images/jobs/{job['id']}/cancel")
        finally:
            self.manager.gpu_lock.release()

    async def test_reloaded_legacy_job_defaults_to_standard_without_rewriting_saved_file(self):
        job_id = "c" * 32
        legacy = {"id": job_id, "mode": "generate", "status": "completed", "steps": 25}
        path = self.manager.job_dir / f"{job_id}.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        reloaded = ImageJobManager(self.manager.comfy, self.root, self.manager.gpu_lock, self.app["assets"])
        self.assertEqual(reloaded.jobs[job_id]["model_variant"], "standard")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)

    async def test_selected_turbo_missing_fails_even_when_standard_is_ready(self):
        status = {"ready": True, "variants": [
            {"id": "standard", "ready": True}, {"id": "turbo", "ready": False, "error": "Turbo missing"}]}
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value=status)), \
             patch.object(self.manager.comfy, "ensure_running", new=AsyncMock()), \
             patch.object(self.manager.comfy, "upload_asset", new=AsyncMock()) as upload, \
             patch.object(self.manager.comfy, "run_prompt", new=AsyncMock()) as run:
            job = self.manager.create({"prompt": "x", "model_variant": "turbo"})
            await self.manager.tasks[job["id"]]
        self.assertEqual((job["status"], job["error"]), ("failed", "Turbo missing"))
        upload.assert_not_called()
        run.assert_not_called()

    async def test_legacy_ready_flag_cannot_authorize_turbo_submission(self):
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value={"ready": True})), \
             patch.object(self.manager.comfy, "ensure_running", new=AsyncMock()), \
             patch.object(self.manager.comfy, "run_prompt", new=AsyncMock()) as run:
            job = self.manager.create({"prompt": "x", "model_variant": "turbo"})
            await self.manager.tasks[job["id"]]
        self.assertEqual(job["status"], "failed")
        run.assert_not_called()

    async def test_selected_standard_missing_fails_even_when_turbo_is_ready(self):
        status = {"ready": True, "variants": [
            {"id": "standard", "ready": False, "error": "Standard missing"}, {"id": "turbo", "ready": True}]}
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value=status)), \
             patch.object(self.manager.comfy, "ensure_running", new=AsyncMock()), \
             patch.object(self.manager.comfy, "run_prompt", new=AsyncMock()) as run:
            job = self.manager.create({"prompt": "x"})
            await self.manager.tasks[job["id"]]
        self.assertEqual((job["status"], job["error"]), ("failed", "Standard missing"))
        run.assert_not_called()

    async def test_turbo_lora_request_returns_clear_error_without_creating_job(self):
        response = await self.client.post("/api/images/jobs", json={"prompt": "x", "model_variant": "turbo",
                                           "lora_name": "qwen_image_2_1/mfd.safetensors", "lora_strength": 1})
        self.assertEqual(response.status, 400)
        self.assertIn("LoRA", (await response.json())["error"])
        self.assertFalse(self.manager.jobs)
        self.assertFalse(self.manager.tasks)

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

    async def test_upscale_copies_source_survives_parent_delete_and_completes_without_qwen(self):
        self.completed_image()
        self.manager.jobs[IMAGE_ID]["model_variant"] = "turbo"
        await self.manager.gpu_lock.acquire()
        capabilities={"ready":True,"models":[{"id":"general","installed":True}]}
        result=io.BytesIO(); Image.new('RGBA',(64,64),(50,100,200,100)).save(result,format='PNG')
        async def run(workflow,callback,event):
            self.assertEqual(workflow['2']['class_type'],'UpscaleModelLoader')
            self.assertEqual(workflow['4']['inputs']['width'],64)
            return 'upscale',{'outputs':{'8':{'images':[{'type':'output','filename':'test.png'}]}}}
        with patch('qwen_image.upscale_capabilities',new=AsyncMock(return_value=capabilities)), \
             patch('qwen_image.image_capabilities',new=AsyncMock(side_effect=AssertionError('must not require Qwen'))), \
             patch.object(self.manager.comfy,'ensure_running',new=AsyncMock()), \
             patch.object(self.manager.comfy,'upload_asset',new=AsyncMock(return_value='host/source.png')), \
             patch.object(self.manager.comfy,'run_prompt',side_effect=run), \
             patch.object(self.manager.comfy,'fetch_output',new=AsyncMock(return_value=(result.getvalue(),'image/png'))):
            response=await self.client.post(f'/api/images/jobs/{IMAGE_ID}/upscale',json={'scale':2,'model':'general'})
            self.assertEqual(response.status,202)
            job=await response.json()
            self.assertEqual(job['mode'],'upscale')
            self.assertEqual(job['source_model_variant'],'turbo')
            self.assertEqual(job['status'],'queued')
            reference=self.app['assets'].path_for(job['image_asset_ids'][0])
            with Image.open(reference) as image:self.assertEqual(image.getpixel((0,0))[3],100)
            self.assertEqual((await self.client.delete(f'/api/images/jobs/{IMAGE_ID}')).status,200)
            self.assertTrue(reference.exists())
            self.manager.gpu_lock.release()
            await self.manager.tasks[job['id']]
        finished=self.manager.jobs[job['id']]
        self.assertEqual(finished['status'],'completed')
        self.assertEqual((finished['width'],finished['height']),(64,64))

    async def test_upscale_model_provenance_survives_a_chain_and_legacy_source_defaults(self):
        self.completed_image()
        source = self.manager.jobs[IMAGE_ID]
        source.update(mode="upscale", source_model_variant="turbo")
        status = {"ready": True, "models": [{"id": "general", "installed": True}]}
        with patch("qwen_image.upscale_capabilities", new=AsyncMock(return_value=status)), \
             patch.object(self.manager, "_create_job", side_effect=lambda compiled: compiled):
            compiled = await self.manager.create_upscale(IMAGE_ID, {"scale": 2})
            self.assertEqual(compiled["source_model_variant"], "turbo")
            self.assertNotIn("model_variant", compiled)
            source.pop("source_model_variant")
            compiled = await self.manager.create_upscale(IMAGE_ID, {"scale": 2})
            self.assertEqual(compiled["source_model_variant"], "standard")

    async def test_upscale_validation_and_missing_engine_model_create_no_job(self):
        self.completed_image()
        for payload in ({'scale':8},{'model':'missing'},[]):
            self.assertEqual((await self.client.post(f'/api/images/jobs/{IMAGE_ID}/upscale',json=payload)).status,400)
        with patch('qwen_image.upscale_capabilities',new=AsyncMock(return_value={'ready':False,'error':'missing models'})):
            self.assertEqual((await self.client.post(f'/api/images/jobs/{IMAGE_ID}/upscale',json={'scale':2})).status,400)
        self.assertEqual(list(self.manager.jobs),[IMAGE_ID])

    async def test_upscale_dimensions_must_match_completed_output(self):
        job_id='b'*32
        self.manager.jobs[job_id]={'id':job_id,'mode':'upscale','width':64,'height':64}
        result=io.BytesIO(); Image.new('RGB',(32,32)).save(result,format='PNG')
        with patch.object(self.manager.comfy,'fetch_output',new=AsyncMock(return_value=(result.getvalue(),'image/png'))):
            with self.assertRaises(RequestError):
                await self.manager._complete(job_id,{'outputs':{'8':{'images':[{'type':'output','filename':'wrong.png'}]}}})
