import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as studio
from anima_rgba import capabilities, compatible_nodes, prepare_prompt
from anima_rgba_models import (MODEL_FILES, NEGATIVE_PROMPT, QUALITY_PREFIX, STEPS, CFG,
                               SAMPLER, SCHEDULER, LICENSE_URL)
from domain import RequestError
from qwen_image import compile_image_request, build_image_workflow, image_capabilities, ImageJobManager


def payload(**changes):
    return {"prompt": "1girl, solo, witch hat, holding staff, full body", "model_variant": "anima_rgba",
            "mode": "generate", "image_asset_ids": [], "seed_auto": False, "seed": 202, **changes}


def schemas():
    def node(fields, outputs, **extra):
        return dict(input={"required": fields}, output=outputs, **extra)
    def fields(**values):
        return {key: [value] for key, value in values.items()}
    return {
        "UNETLoader": node({"unet_name": [[MODEL_FILES[0][1]]], "weight_dtype": [["default"]]}, ["MODEL"]),
        "CLIPLoader": node({"clip_name": [[MODEL_FILES[1][1]]], "type": [["stable_diffusion"]], "device": [["default", "cpu"]]}, ["CLIP"]),
        "VAELoader": node({"vae_name": [[MODEL_FILES[2][1]]]}, ["VAE"]),
        "LoraLoaderModelOnly": node({**fields(model="MODEL", strength_model="FLOAT"),
                                     "lora_name": [[MODEL_FILES[3][1].replace("/", "\\")]]}, ["MODEL"]),
        "CLIPTextEncode": node(fields(clip="CLIP", text="STRING"), ["CONDITIONING"]),
        "EmptyLatentImage": node(fields(width="INT", height="INT", batch_size="INT"), ["LATENT"]),
        "KSampler": node({**fields(model="MODEL", seed="INT", steps="INT", cfg="FLOAT", positive="CONDITIONING",
                                    negative="CONDITIONING", latent_image="LATENT", denoise="FLOAT"),
                           "sampler_name": [[SAMPLER]], "scheduler": [[SCHEDULER]]}, ["LATENT"]),
        "VAEDecode": node(fields(samples="LATENT", vae="VAE"), ["IMAGE"]),
        "SaveImage": node(fields(images="IMAGE", filename_prefix="STRING"), [], output_node=True),
    }


class AnimaCompileTests(unittest.TestCase):
    def test_author_recipe_retains_raw_prompt_and_actual_positive_negative(self):
        original = "  1girl, solo, 藍色法袍  \n"
        compiled = compile_image_request(payload(prompt=original, steps=4, transparent=False,
                                                negative_prompt="  blurry, cropped  "))
        self.assertEqual(compiled["original_prompt"], original)
        self.assertEqual(compiled["prompt"], QUALITY_PREFIX + "transparent background, " + original.strip())
        self.assertEqual(compiled["original_negative_prompt"], "  blurry, cropped  ")
        self.assertEqual(compiled["negative_prompt"], "blurry, cropped")
        self.assertTrue(compiled["transparent"])
        self.assertTrue(compiled["native_alpha"])
        self.assertEqual((compiled["steps"], compiled["cfg"], compiled["sampler_name"], compiled["scheduler"]),
                         (STEPS, CFG, SAMPLER, SCHEDULER))
        self.assertEqual((compiled["seed"], compiled["model_adapter"], compiled["model_adapter_strength"]),
                         (202, MODEL_FILES[3][1], 1.0))
        self.assertEqual(compiled["name"], "Anima 透明動漫圖")

    def test_explicit_trigger_weight_is_not_duplicated_or_reweighted(self):
        prompt = QUALITY_PREFIX + "1girl, (transparent background:1.3), solo"
        self.assertEqual(prepare_prompt(prompt), prompt)
        prompt = "1girl, isolated on a transparent background"
        self.assertEqual(prepare_prompt(prompt), QUALITY_PREFIX + prompt)

    def test_default_negative_and_explicit_empty_negative_are_different(self):
        self.assertEqual(compile_image_request(payload())["negative_prompt"], NEGATIVE_PROMPT)
        self.assertEqual(compile_image_request(payload(negative_prompt=""))["negative_prompt"], "")

    def test_unsupported_features_and_bad_dimensions_are_rejected(self):
        changes = ({"mode": "edit"}, {"image_asset_ids": ["a" * 32]},
                   {"lora_name": "qwen_image_2_1/style.safetensors", "lora_strength": 0},
                   {"batch_size": 2}, {"batch_size": True}, {"width": 256}, {"height": 1600},
                   {"width": 1056}, {"height": 1000}, {"width": True},
                   {"negative_prompt": []}, {"negative_prompt": "x" * 8001})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(RequestError):
                compile_image_request(payload(**change))
        for width, height in ((512, 512), (768, 1024), (1536, 1536)):
            compiled = compile_image_request(payload(width=width, height=height))
            self.assertEqual((compiled["width"], compiled["height"]), (width, height))

    def test_graph_uses_native_author_sampler_lora_and_rgba_decode_without_matting(self):
        compiled = compile_image_request(payload())
        graph = build_image_workflow(compiled, {}, "native-alpha")
        self.assertEqual(graph["2"]["inputs"], dict(clip_name=MODEL_FILES[1][1], type="stable_diffusion", device="default"))
        self.assertEqual(graph["3"]["inputs"]["vae_name"], MODEL_FILES[2][1])
        self.assertEqual(graph["9"]["inputs"]["lora_name"].replace("\\", "/"), MODEL_FILES[3][1])
        self.assertEqual(graph["9"]["inputs"]["strength_model"], 1.0)
        self.assertEqual(graph["6"]["inputs"], dict(model=["9", 0], positive=["4", 0], negative=["10", 0],
            latent_image=["5", 0], seed=202, steps=30, cfg=4.5, sampler_name="er_sde", scheduler="simple", denoise=1.0))
        self.assertEqual(graph["4"]["inputs"]["text"], compiled["prompt"])
        self.assertEqual(graph["10"]["inputs"]["text"], NEGATIVE_PROMPT)
        self.assertEqual(graph["8"]["inputs"]["images"], ["7", 0])
        self.assertEqual(graph["8"]["inputs"]["filename_prefix"], "H3Studio/AnimaRGBA/native-alpha")
        self.assertFalse({"TextEncodeQwenImage21", "LoadImage", "TextGenerate", "JoinImageWithAlpha"} &
                         {node["class_type"] for node in graph.values()})

    def test_saved_recipe_cannot_swap_components_or_strip_native_alpha(self):
        compiled = compile_image_request(payload())
        for change in ({"mode": "edit"}, {"transparent": False}, {"steps": 8}, {"cfg": 1},
                       {"sampler_name": "euler"}, {"model_adapter": "h3.safetensors"},
                       {"model_adapter_strength": 1.5}, {"width": 2048},
                       {"native_model_names": {"lora_name": "another/adapter.safetensors"}}):
            with self.subTest(change=change), self.assertRaises(RequestError):
                build_image_workflow({**compiled, **change}, {}, "tampered")
        with self.assertRaises(RequestError):
            build_image_workflow(compiled, {"unused": "ref.png"}, "no-reference")


class AnimaCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def engine(self, values):
        seen = []
        async def info(request):
            seen.append(request.headers.get("Authorization"))
            name = request.match_info["name"]
            if name not in values:
                raise web.HTTPNotFound()
            return web.json_response({name: values[name]})
        app = web.Application()
        app.router.add_get("/object_info/{name}", info)
        async def stats(request):
            seen.append(request.headers.get("Authorization"))
            return web.json_response({"system": {"comfyui_version": values.get("_version", "0.42.0")}})
        app.router.add_get("/system_stats", stats)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return SimpleNamespace(base_url=str(server.make_url("/")).rstrip("/"), mode="remote",
                               auth_headers=lambda: {"Authorization": "Bearer test"}), seen

    async def test_ready_independently_of_qwen_flux_and_uses_live_windows_lora_name(self):
        comfy, seen = await self.engine(schemas())
        status = await image_capabilities(comfy)
        self.assertTrue(status["ready"])
        self.assertFalse(any(profile["ready"] for profile in status["variants"][:3]))
        profile = status["variants"][-1]
        self.assertEqual(profile["id"], "anima_rgba")
        self.assertEqual(status["anima_rgba"], profile)
        self.assertTrue(profile["ready"])
        self.assertEqual(profile["mode_ready"], {"generate": True, "edit": False})
        self.assertEqual(profile["license_url"], LICENSE_URL)
        self.assertEqual(profile["generation_dimension_step"], 64)
        self.assertTrue(profile["native_alpha"])
        self.assertTrue(profile["core_supported"])
        self.assertEqual(profile["adapter_license"], "Apache-2.0")
        self.assertIn("Non-Commercial", profile["base_license"])
        self.assertEqual(len(profile["models"]), 4)
        self.assertTrue(seen and all(value == "Bearer test" for value in seen))
        graph = build_image_workflow({**compile_image_request(payload()), "native_model_names": profile["loader_names"]}, {}, "win")
        self.assertEqual(graph["9"]["inputs"]["lora_name"], MODEL_FILES[3][1].replace("/", "\\"))

    async def test_missing_native_contracts_disable_only_anima(self):
        values = schemas()
        comfy, _ = await self.engine(values)
        for node, change in (("KSampler", lambda n: n["input"]["required"].update(sampler_name=[["euler"]])),
                             ("CLIPLoader", lambda n: n["input"]["required"].update(type=[["qwen_image"]])),
                             ("LoraLoaderModelOnly", lambda n: n.update(output=["CLIP"])),
                             ("VAEDecode", lambda n: n.update(output=["LATENT"])),
                             ("SaveImage", lambda n: n.update(output_node=False))):
            original = copy.deepcopy(values[node])
            change(values[node])
            with self.subTest(node=node):
                result = await capabilities(comfy)
                self.assertFalse(result["ready"])
                self.assertFalse(result["nodes_ready"])
                self.assertIn("節點", result["error"])
            values[node] = original

    async def test_native_v3_combos_and_partial_local_file_handling(self):
        values = schemas()
        for name, field in (("UNETLoader", "unet_name"), ("UNETLoader", "weight_dtype"),
                            ("CLIPLoader", "clip_name"), ("CLIPLoader", "type"), ("CLIPLoader", "device"),
                            ("VAELoader", "vae_name"), ("LoraLoaderModelOnly", "lora_name"),
                            ("KSampler", "sampler_name"), ("KSampler", "scheduler")):
            choices = values[name]["input"]["required"][field][0]
            values[name]["input"]["required"][field] = ["COMBO", {"options": choices, "multiselect": False}]
        comfy, _ = await self.engine(values)
        self.assertTrue((await capabilities(comfy))["ready"])
        comfy.mode, comfy.comfy_dir = "local", Path("model-host")
        sizes = {Path(model[1]).name: model[2] for model in MODEL_FILES}
        sizes[MODEL_FILES[2][1]] -= 1
        old_stat, old_is_file = Path.stat, Path.is_file
        def stat(path, *args, **kwargs):
            return SimpleNamespace(st_size=sizes[path.name]) if path.name in sizes else old_stat(path, *args, **kwargs)
        def is_file(path):
            return True if path.name in sizes else old_is_file(path)
        with patch.object(Path, "stat", stat), patch.object(Path, "is_file", is_file):
            result = await capabilities(comfy)
        self.assertFalse(result["ready"])
        self.assertTrue(result["nodes_ready"])
        self.assertFalse(result["models"][2]["installed"])
        self.assertIn("setup_anima_rgba.bat", result["error"])

    async def test_generic_loader_names_do_not_enable_older_or_unverifiable_core(self):
        values = schemas()
        comfy, _ = await self.engine(values)
        for version in ("0.37.0", "unknown", None):
            values["_version"] = version
            with self.subTest(version=version):
                result = await capabilities(comfy)
                self.assertFalse(result["ready"])
                self.assertTrue(result["nodes_ready"])
                self.assertFalse(result["core_supported"])
                self.assertIn("0.42.0", result["error"])


class AnimaAPITests(unittest.IsolatedAsyncioTestCase):
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

    async def test_rgba_bytes_alpha_and_recipe_survive_completion_download_reference_and_reload(self):
        image = Image.new("RGBA", (512, 512), (40, 80, 200, 0))
        image.putpixel((1, 1), (255, 255, 255, 128))
        image.putpixel((2, 2), (255, 255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        content = output.getvalue()
        history = {"outputs": {"8": {"images": [{"filename": "rgba.png", "type": "output"}]}}}
        profile = {"id": "anima_rgba", "ready": True, "modes": ["generate"],
                   "mode_ready": {"generate": True, "edit": False},
                   "loader_names": {"lora_name": MODEL_FILES[3][1].replace("/", "\\")}}
        async def run(graph, callback, event):
            self.assertEqual(graph["9"]["inputs"]["lora_name"], profile["loader_names"]["lora_name"])
            self.assertEqual(graph["6"]["inputs"]["steps"], 30)
            await callback({"prompt_id": "native-rgba", "progress": 100})
            return "native-rgba", history
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value={"ready": True, "variants": [profile]})), \
             patch.object(self.manager.comfy, "ensure_running", new=AsyncMock()), \
             patch.object(self.manager.comfy, "upload_asset", new=AsyncMock()) as upload, \
             patch.object(self.manager.comfy, "run_prompt", side_effect=run), \
             patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(content, "image/png"))):
            response = await self.client.post("/api/images/jobs", json=payload(width=512, height=512, transparent=False))
            self.assertEqual(response.status, 202)
            job = await response.json()
            await self.manager.tasks[job["id"]]
        upload.assert_not_called()
        final = self.manager.jobs[job["id"]]
        self.assertEqual(final["status"], "completed", final.get("error"))
        self.assertTrue(final["rgba"])
        self.assertEqual(final["alpha_extrema"], [0, 255])
        self.assertEqual(final["alpha_transparent_pixels"], 512 * 512 - 1)
        self.assertAlmostEqual(final["alpha_meaningful_fraction"], (512 * 512 - 2) / (512 * 512))
        self.assertIsNone(final["alpha_warning"])
        for suffix in ("", "?download=1"):
            response = await self.client.get(f"/api/images/jobs/{job['id']}/image{suffix}")
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), content)
        response = await self.client.post(f"/api/images/jobs/{job['id']}/reference")
        reference = await response.json()
        with Image.open(self.app["assets"].path_for(reference["id"])) as saved:
            self.assertEqual(saved.mode, "RGBA")
            self.assertEqual(saved.getpixel((1, 1)), (255, 255, 255, 128))
            self.assertEqual(saved.getpixel((0, 0))[3], 0)
        restored = ImageJobManager(self.manager.comfy, self.root, self.manager.gpu_lock, self.app["assets"])
        self.assertEqual(restored.jobs[job["id"]]["negative_prompt"], NEGATIVE_PROMPT)
        self.assertEqual(restored.jobs[job["id"]]["model_variant"], "anima_rgba")
        self.assertEqual(restored.jobs[job["id"]]["alpha_extrema"], [0, 255])

    async def test_rgb_or_wrong_canvas_is_not_published_as_native_rgba(self):
        job_id = "d" * 32
        self.manager.jobs[job_id] = {**compile_image_request(payload(width=512, height=512)), "id": job_id}
        history = {"outputs": {"8": {"images": [{"filename": "bad.png", "type": "output"}]}}}
        for image in (Image.new("RGB", (512, 512), "white"), Image.new("RGBA", (64, 64), (1, 1, 1, 0))):
            output = io.BytesIO()
            image.save(output, format="PNG")
            with self.subTest(mode=image.mode, size=image.size), \
                 patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(output.getvalue(), "image/png"))), \
                 self.assertRaises(RequestError):
                await self.manager._complete(job_id, history)
            self.assertFalse((self.manager.output_dir / (job_id + ".png")).exists())

    async def test_fully_opaque_rgba_is_reported_without_inventing_a_cutout(self):
        job_id = "e" * 32
        self.manager.jobs[job_id] = {**compile_image_request(payload(width=512, height=512)), "id": job_id}
        output = io.BytesIO()
        Image.new("RGBA", (512, 512), (255, 255, 255, 255)).save(output, format="PNG")
        history = {"outputs": {"8": {"images": [{"filename": "opaque.png", "type": "output"}]}}}
        with patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(output.getvalue(), "image/png"))):
            await self.manager._complete(job_id, history)
        self.assertEqual(self.manager.jobs[job_id]["alpha_extrema"], [255, 255])
        self.assertEqual(self.manager.jobs[job_id]["alpha_transparent_fraction"], 0)
        self.assertEqual(self.manager.jobs[job_id]["alpha_meaningful_fraction"], 0)
        self.assertIn("全部不透明", self.manager.jobs[job_id]["alpha_warning"])

    async def test_nearly_opaque_rgba_warns_even_when_some_pixels_have_alpha_below_255(self):
        job_id = "f" * 32
        self.manager.jobs[job_id] = {**compile_image_request(payload(width=512, height=512)), "id": job_id}
        image = Image.new("RGBA", (512, 512), (12, 3, 25, 255))
        image.putpixel((0, 0), (20, 4, 35, 231))
        output = io.BytesIO()
        image.save(output, format="PNG")
        history = {"outputs": {"8": {"images": [{"filename": "nominal-alpha.png", "type": "output"}]}}}
        with patch.object(self.manager.comfy, "fetch_output", new=AsyncMock(return_value=(output.getvalue(), "image/png"))):
            await self.manager._complete(job_id, history)
        job = self.manager.jobs[job_id]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["alpha_extrema"], [231, 255])
        self.assertEqual(job["alpha_transparent_fraction"], 1 / (512 * 512))
        self.assertEqual(job["alpha_meaningful_fraction"], 0)
        self.assertIn("近乎不透明", job["alpha_warning"])
        self.assertIn("不到 5%", job["alpha_warning"])
        self.assertNotIn("全部不透明", job["alpha_warning"])
        self.assertEqual(self.manager.output_path(job_id).read_bytes(), output.getvalue())

    async def test_invalid_anima_api_requests_create_no_job(self):
        for change in ({"mode": "edit"}, {"width": 1056}, {"lora_name": "qwen_image_2_1/style.safetensors"}):
            with self.subTest(change=change):
                response = await self.client.post("/api/images/jobs", json=payload(**change))
                self.assertEqual(response.status, 400)
        self.assertFalse(self.manager.jobs)

    async def test_status_endpoint_exposes_anima_and_native_alpha_contract(self):
        profile = {"id": "anima_rgba", "ready": True, "native_alpha": True, "modes": ["generate"]}
        with patch("qwen_image.image_capabilities", new=AsyncMock(return_value={"ready": True, "variants": [profile]})):
            response = await self.client.get("/api/images/status")
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["variants"][0], profile)


if __name__ == "__main__":
    unittest.main()
