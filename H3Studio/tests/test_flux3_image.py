import asyncio
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as studio
from domain import RequestError
from flux3_image import EDIT_SYSTEM_PROMPT, compatible_nodes
from flux3_image_models import MODEL_FILES as FLUX_FILES, STEPS
from qwen_image import ImageJobManager, build_image_workflow, compile_image_request, image_capabilities
from qwen_image_models import MODEL_FILES as QWEN_FILES, TURBO_MODEL_FILE
from tests.test_qwen_image import variant_schemas


IMAGE_ID = "a" * 32


def node(required=None, output=(), optional=None, **extra):
    return {"input": {"required": required or {}, "optional": optional or {}}, "output": list(output), **extra}


def fields(**values):
    return {name: [kind] for name, kind in values.items()}


def flux_schemas(qwen=True, flux=True):
    schemas = variant_schemas() if qwen else {}
    schemas.update({
        "UNETLoader": node({"unet_name": [([QWEN_FILES[0][1], TURBO_MODEL_FILE[1]] if qwen else []) + ([FLUX_FILES[0][1]] if flux else [])]}, ["MODEL"]),
        "CLIPLoader": node({"clip_name": [([QWEN_FILES[1][1]] if qwen else []) + ([FLUX_FILES[1][1], FLUX_FILES[2][1]] if flux else [])],
                             "type": [["qwen_image", "flux3"]]}, ["CLIP"]),
        "VAELoader": node({"vae_name": [([QWEN_FILES[2][1]] if qwen else []) + ([FLUX_FILES[3][1]] if flux else [])]}, ["VAE"]),
        "LoadImage": node({"image": [["reference.png"]]}, ["IMAGE", "MASK"]),
        "TextGenerate": node({**fields(clip="CLIP", prompt="STRING", max_length="INT"),
            "sampling_mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "on", "inputs": {
                "required": fields(temperature="FLOAT", top_k="INT", top_p="FLOAT", min_p="FLOAT", repetition_penalty="FLOAT", seed="INT"),
                "optional": fields(presence_penalty="FLOAT")}}]}]}, ["STRING", "STRING"],
            {**fields(image="IMAGE", thinking="BOOLEAN", use_default_template="BOOLEAN", system_prompt="STRING"), "mtp": ["COMBO", {"options": ["auto", "off"], "multiselect": False}]}),
        "PreviewAny": node(fields(source="*"), ["STRING"], output_node=True),
        "CLIPTextEncode": node(fields(clip="CLIP", text="STRING"), ["CONDITIONING"]),
        "ConditioningZeroOut": node(fields(conditioning="CONDITIONING"), ["CONDITIONING"]),
        "ReferenceMultiLatent": node({**fields(positive="CONDITIONING"),
            "resolution": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "resize", "inputs": {"required": fields(megapixels="FLOAT")}}]}]},
            ["CONDITIONING", "CONDITIONING", "LATENT"],
            {**fields(negative="CONDITIONING", vae="VAE"), "images": ["COMFY_AUTOGROW_V3", {"template": {
                "prefix": "image", "min": 1, "max": 10, "input": {"optional": fields(image="IMAGE")}}}]}),
        "KSampler": node({**fields(model="MODEL", positive="CONDITIONING", negative="CONDITIONING", latent_image="LATENT",
                                     seed="INT", steps="INT", cfg="FLOAT", denoise="FLOAT"),
                           "sampler_name": [["euler"]], "scheduler": [["simple"]]}, ["LATENT"]),
        "VAEDecode": node(fields(samples="LATENT", vae="VAE"), ["IMAGE"]),
        "SaveImage": node(fields(images="IMAGE", filename_prefix="STRING"), output_node=True),
    })
    return schemas


def payload(**changes):
    return {"prompt": "把白杯改成藍色，保留窗邊光線。", "mode": "edit", "model_variant": "flux3",
            "image_asset_ids": [IMAGE_ID], "seed_auto": False, "seed": 42, **changes}


class Flux3CompileTests(unittest.TestCase):
    def test_fixed_tested_profile_preserves_original_request_and_seed(self):
        compiled = compile_image_request(payload(steps=25, reference_resolution=2048))
        self.assertEqual(compiled["model_variant"], "flux3")
        self.assertEqual(compiled["steps"], STEPS)
        self.assertEqual(compiled["reference_resolution"], 1024)
        self.assertEqual(compiled["reference_megapixels"], 1.0)
        self.assertEqual(compiled["output_size_policy"], "reference_aspect_1mp")
        self.assertEqual(compiled["prompt"], compiled["original_prompt"])
        self.assertEqual(compiled["seed"], 42)
        self.assertEqual(compiled["lora_strength"], 0)

    def test_unverified_features_are_rejected_without_discarding_them(self):
        for change, message in (({"mode": "generate", "image_asset_ids": []}, "參考圖編輯"),
                                ({"image_asset_ids": []}, "單張"),
                                ({"image_asset_ids": [IMAGE_ID, "b" * 32]}, "單張"),
                                ({"transparent": True}, "透明"),
                                ({"lora_name": "qwen_image_2_1/style.safetensors", "lora_strength": 0}, "LoRA")):
            with self.subTest(change=change), self.assertRaisesRegex(RequestError, message):
                compile_image_request(payload(**change))

    def test_workflow_uses_reference_latent_and_real_prompt_upsampler(self):
        compiled = compile_image_request(payload())
        graph = build_image_workflow(compiled, {IMAGE_ID: "H3Gateway/user/ref.png"}, "job")
        self.assertEqual(graph["1"]["inputs"]["unet_name"], FLUX_FILES[0][1])
        self.assertEqual(graph["2"]["inputs"]["clip_name"], FLUX_FILES[2][1])
        self.assertEqual(graph["46"]["inputs"]["clip_name"], FLUX_FILES[1][1])
        self.assertEqual(graph["47"]["inputs"]["image"], ["11", 0])
        self.assertEqual(graph["47"]["inputs"]["system_prompt"], EDIT_SYSTEM_PROMPT)
        self.assertGreater(len(EDIT_SYSTEM_PROMPT), 2000)
        self.assertEqual(graph["4"]["inputs"]["text"], ["47", 0])
        self.assertEqual(graph["48"]["inputs"]["source"], ["47", 0])
        self.assertEqual(graph["17"]["inputs"]["images.image0"], ["11", 0])
        self.assertEqual(graph["17"]["inputs"]["resolution.megapixels"], 1.0)
        self.assertEqual(graph["6"]["inputs"]["latent_image"], ["17", 2])
        self.assertEqual(graph["6"]["inputs"]["negative"], ["17", 1])
        self.assertEqual(graph["6"]["inputs"]["steps"], 16)
        self.assertEqual(graph["6"]["inputs"]["seed"], graph["47"]["inputs"]["sampling_mode.seed"])
        self.assertEqual(graph["8"]["class_type"], "SaveImage")
        self.assertNotIn("LoraLoaderModelOnly", {node["class_type"] for node in graph.values()})

    def test_saved_unsupported_recipe_cannot_bypass_workflow_validation(self):
        compiled = compile_image_request(payload())
        for change in ({"transparent": True}, {"mode": "generate"}, {"image_asset_ids": [IMAGE_ID, "b" * 32]}, {"lora_name": "custom.safetensors"}):
            with self.subTest(change=change), self.assertRaises(RequestError):
                build_image_workflow({**compiled, **change}, {IMAGE_ID: "ref.png"}, "job")
        with self.assertRaisesRegex(RequestError, "尚未上傳"):
            build_image_workflow(compiled, {}, "job")


class Flux3CapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def engine(self, schemas):
        seen = []
        async def info(request):
            seen.append(request.headers.get("Authorization"))
            name = request.match_info["name"]
            if name not in schemas:
                raise web.HTTPNotFound()
            return web.json_response({name: schemas[name]})
        app = web.Application()
        app.router.add_get("/object_info/{name}", info)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return SimpleNamespace(base_url=str(server.make_url("/")).rstrip("/"), mode="remote", auth_headers=lambda: {"Authorization": "Bearer test"}), seen

    async def test_flux_ready_independently_of_missing_qwen_and_forwards_auth(self):
        comfy, seen = await self.engine(flux_schemas(qwen=False))
        result = await image_capabilities(comfy)
        self.assertTrue(result["ready"])
        self.assertFalse(result["variants"][0]["ready"])
        flux = result["variants"][2]
        self.assertEqual(flux["id"], "flux3")
        self.assertTrue(flux["ready"])
        self.assertEqual(flux["reference_limit"], 1)
        self.assertEqual(flux["modes"], ["edit"])
        self.assertFalse(flux["transparent_supported"])
        self.assertEqual(len(flux["models"]), 4)
        self.assertTrue(seen and all(value == "Bearer test" for value in seen))

    async def test_qwen_remains_ready_when_flux_weights_or_nodes_are_missing(self):
        for schemas in (flux_schemas(flux=False), variant_schemas()):
            with self.subTest(flux_weights="flux3" in str(schemas)):
                comfy, _ = await self.engine(schemas)
                result = await image_capabilities(comfy)
                self.assertTrue(result["ready"])
                self.assertTrue(result["variants"][0]["ready"])
                self.assertTrue(result["variants"][1]["ready"])
                self.assertFalse(result["variants"][2]["ready"])
                self.assertIsNone(result["error"])

    async def test_dynamic_schema_and_output_contracts_are_required(self):
        schemas = flux_schemas()
        comfy, _ = await self.engine(schemas)
        self.assertTrue(compatible_nodes(schemas))
        mutations = (
            ("TextGenerate", lambda item: item["input"]["optional"].pop("system_prompt")),
            ("TextGenerate", lambda item: item.update(output=["CONDITIONING"])),
            ("TextGenerate", lambda item: item["input"]["required"]["sampling_mode"][1]["options"][0]["inputs"]["required"].update(temperature=["STRING"])),
            ("ReferenceMultiLatent", lambda item: item["input"]["required"]["resolution"][1]["options"][0]["inputs"]["required"].pop("megapixels")),
            ("ReferenceMultiLatent", lambda item: item["input"]["optional"]["images"][1]["template"].update(prefix="wrong")),
            ("ReferenceMultiLatent", lambda item: item.update(output=["CONDITIONING", "CONDITIONING", "IMAGE"])),
            ("PreviewAny", lambda item: item.update(output_node=False)),
            ("KSampler", lambda item: item["input"]["required"].update(scheduler=[["normal"]])),
        )
        for name, mutate in mutations:
            with self.subTest(node=name):
                original = copy.deepcopy(schemas[name])
                mutate(schemas[name])
                result = await image_capabilities(comfy)
                self.assertTrue(result["variants"][0]["ready"])
                self.assertFalse(result["variants"][2]["ready"])
                self.assertIn("節點", result["variants"][2]["error"])
                schemas[name] = original

    async def test_partial_local_model_is_not_ready(self):
        comfy, _ = await self.engine(flux_schemas())
        comfy.mode, comfy.comfy_dir = "local", Path("fake-gpu-host")
        sizes = {model[1]: model[2] for model in (*QWEN_FILES, TURBO_MODEL_FILE, *FLUX_FILES)}
        sizes[FLUX_FILES[1][1]] -= 1
        original_stat, original_is_file = Path.stat, Path.is_file
        def file_stat(path, *args, **kwargs):
            return SimpleNamespace(st_size=sizes[path.name]) if path.name in sizes else original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", file_stat), patch.object(Path, "is_file", lambda path: True if path.name in sizes else original_is_file(path)):
            result = await image_capabilities(comfy)
        self.assertTrue(result["variants"][0]["ready"])
        self.assertFalse(result["variants"][2]["ready"])
        self.assertIn("尚未齊全", result["variants"][2]["error"])


class Flux3ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.multiple(studio, DATA_DIR=self.root, ASSET_DIR=self.root / "assets",
            JOB_DIR=self.root / "jobs", OUTPUT_DIR=self.root / "outputs", CONFIG_PATH=self.root / "config.json"))
        self.app = studio.create_app()
        self.app.on_startup.clear()
        self.client = TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.manager = self.app["image_jobs"]
        self.asset = self.app["assets"].save_image(Image.new("RGB", (64, 96), "white"), "cup.png", "qwen-image-reference")

    async def test_queued_flux_job_persists_variant_and_cancels_without_touching_engine(self):
        await self.manager.gpu_lock.acquire()
        try:
            response = await self.client.post("/api/images/jobs", json=payload(image_asset_ids=[self.asset["id"]]))
            self.assertEqual(response.status, 202)
            job = await response.json()
            saved = json.loads((self.manager.job_dir / f"{job['id']}.json").read_text())
            self.assertEqual((saved["model_variant"], saved["mode"], saved["steps"]), ("flux3", "edit", 16))
            with patch.object(self.manager.comfy, "interrupt", new=AsyncMock()) as interrupt:
                response = await self.client.post(f"/api/images/jobs/{job['id']}/cancel")
                self.assertEqual(response.status, 200)
                interrupt.assert_not_called()
        finally:
            self.manager.gpu_lock.release()

    async def test_invalid_flux_features_return_400_without_creating_any_job(self):
        response = await self.client.post("/api/images/jobs", json=payload(image_asset_ids=[self.asset["id"]], transparent=True))
        self.assertEqual(response.status, 400)
        self.assertIn("透明", (await response.json())["error"])
        self.assertFalse(self.manager.jobs)

    async def test_flux_completion_persists_expansion_actual_dimensions_and_reusable_reference(self):
        content = io.BytesIO()
        Image.new("RGB", (768, 1344), "blue").save(content, format="PNG")
        engine_history = {"outputs": {"8": {"images": [{"filename": "flux-final.png", "type": "output"}]},
                                      "48": {"text": ["Change only the white mug to cobalt blue."]}}}
        async def run(graph, callback, event):
            self.assertEqual(graph["6"]["inputs"]["steps"], 16)
            self.assertEqual(graph["8"]["inputs"]["filename_prefix"].split("/")[:2], ["H3Studio", "Flux3ImageKlein"])
            await callback({"prompt_id": "owned-flux-prompt", "progress": 50, "preview_bytes": b"not json"})
            return "owned-flux-prompt", engine_history
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value={"ready": True, "variants": [{"id": "flux3", "ready": True}]})), \
             patch.object(self.manager.comfy, "ensure_running", new=AsyncMock()), \
             patch.object(self.manager.comfy, "upload_asset", new=AsyncMock(return_value="ref.png")), \
             patch.object(self.manager.comfy, "run_prompt", side_effect=run), \
             patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(content.getvalue(), "image/png"))):
            response = await self.client.post("/api/images/jobs", json=payload(image_asset_ids=[self.asset["id"]]))
            self.assertEqual(response.status, 202)
            job = await response.json()
            await self.manager.tasks[job["id"]]
        final = self.manager.jobs[job["id"]]
        self.assertEqual(final["status"], "completed")
        self.assertEqual((final["width"], final["height"]), (768, 1344))
        self.assertEqual(final["expanded_prompt"], "Change only the white mug to cobalt blue.")
        self.assertEqual(final["prompt"], final["original_prompt"])
        reloaded = ImageJobManager(self.manager.comfy, self.root, self.manager.gpu_lock, self.app["assets"])
        self.assertEqual(reloaded.jobs[job["id"]]["model_variant"], "flux3")
        self.assertEqual(reloaded.jobs[job["id"]]["expanded_prompt"], final["expanded_prompt"])
        response = await self.client.post(f"/api/images/jobs/{job['id']}/reference")
        reference = await response.json()
        with Image.open(self.app["assets"].path_for(reference["id"])) as image:
            self.assertEqual(image.size, (768, 1344))

    async def test_recover_flux_history_does_not_resubmit_and_saves_caption(self):
        content = io.BytesIO()
        Image.new("RGB", (64, 64), "blue").save(content, format="PNG")
        job_id = "c" * 32
        job = {**compile_image_request(payload(image_asset_ids=[self.asset["id"]])), "id": job_id,
               "status": "running", "prompt_id": "already-submitted"}
        self.manager.jobs[job_id] = job
        history = {"outputs": {"8": {"images": [{"filename": "existing.png", "type": "output"}]},
                               "48": {"text": ["The original expanded instruction."]}}}
        with patch.object(self.manager.comfy, "get_history", new=AsyncMock(return_value=history)), \
             patch.object(self.manager.comfy, "history_state", return_value="success"), \
             patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(content.getvalue(), "image/png"))), \
             patch.object(self.manager.comfy, "run_prompt", new=AsyncMock()) as run:
            await self.manager.recover(job_id)
        run.assert_not_called()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["expanded_prompt"], "The original expanded instruction.")


if __name__ == "__main__":
    unittest.main()
