import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import web

from comfy_client import ComfyClient
from domain import TURBO_LORA_CANDIDATES
from settings import ConnectionSettings


class ComfyClientRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.history_calls = 0
        app = web.Application()
        app.router.add_post("/prompt", self.prompt)
        app.router.add_get("/ws", self.websocket)
        app.router.add_get("/history/{prompt_id}", self.history)
        app.router.add_post("/interrupt", self.interrupt)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.temporary = tempfile.TemporaryDirectory()
        settings = ConnectionSettings(
            mode="remote",
            base_url=f"http://127.0.0.1:{port}",
            comfy_dir=self.temporary.name,
            auto_start_local=False,
        )
        self.client = ComfyClient(settings, Path(self.temporary.name))

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temporary.cleanup()

    async def prompt(self, _request):
        return web.json_response({"prompt_id": "prompt-1"})

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()
        return ws

    async def history(self, _request):
        self.history_calls += 1
        if self.history_calls < 3:
            return web.json_response({})
        return web.json_response({
            "prompt-1": {
                "outputs": {"15": {"images": [{"filename": "done.mp4", "subfolder": "H3Studio", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        })

    async def interrupt(self, _request):
        return web.json_response({})

    async def test_closed_websocket_reconnects_and_uses_history_result(self):
        events = []

        async def progress(event):
            events.append(event)

        prompt_id, history = await asyncio.wait_for(
            self.client.run_prompt({}, progress, asyncio.Event()),
            timeout=8,
        )
        self.assertEqual(prompt_id, "prompt-1")
        self.assertEqual(self.client.history_state(history), "success")
        self.assertGreaterEqual(self.history_calls, 3)
        self.assertTrue(any("自動重連" in str(event.get("current_node")) for event in events))

    def test_history_error_extracts_comfy_exception(self):
        history = {
            "status": {
                "status_str": "error",
                "completed": True,
                "messages": [["execution_error", {"exception_message": "GPU out of memory"}]],
            }
        }
        self.assertEqual(self.client.history_state(history), "error")
        self.assertEqual(self.client.history_error(history), "GPU out of memory")

    def test_decodes_comfy_preview_binary_messages(self):
        jpeg = b"\xff\xd8preview"
        decoded = self.client.decode_preview_message((1).to_bytes(4, "big") + (1).to_bytes(4, "big") + jpeg)
        self.assertEqual(decoded, (jpeg, "image/jpeg"))

        metadata = json.dumps({"image_type": "image/png"}).encode("utf-8")
        png = b"\x89PNGpreview"
        decoded = self.client.decode_preview_message(
            (4).to_bytes(4, "big") + len(metadata).to_bytes(4, "big") + metadata + png
        )
        self.assertEqual(decoded, (png, "image/png"))


class ComfyClientInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schemas = {
            "UNETLoader": {"input": {"required": {"unet_name": [[
                "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            ]]}}},
            "CLIPLoader": {"input": {"required": {"clip_name": [["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"]]}}},
            "VAELoader": {"input": {"required": {"vae_name": [["minimax_h3_video_vae_fp16.safetensors", "minimax_h3_audio_vae_fp32.safetensors"]]}}},
            "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[candidates[0] for candidates in TURBO_LORA_CANDIDATES.values()]]}}},
            "H3MemoryOptimization": {"input": {"required": {"model": ["MODEL"]}}},
            "MiniMaxH3SigmaShift": {"input": {"required": {"model": ["MODEL"]}}},
            "BlockSparseAttention": {"input": {"required": {"selection": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                {"key": "sla", "inputs": {"required": {"keep_percent": ["FLOAT", {"default": 10.0}]}}},
            ]}]}}},
        }
        app = web.Application()
        app.router.add_get("/object_info/{node}", self.object_info)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.temporary = tempfile.TemporaryDirectory()
        settings = ConnectionSettings(mode="remote", base_url=f"http://127.0.0.1:{port}",
                                      comfy_dir=self.temporary.name, auto_start_local=False)
        self.client = ComfyClient(settings, Path(self.temporary.name))

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temporary.cleanup()

    async def object_info(self, request):
        node = request.match_info["node"]
        # Comfy returns HTTP 200 with an empty object for unknown nodes.
        return web.json_response({node: self.schemas[node]} if node in self.schemas else {})

    async def test_missing_sparse_node_does_not_hide_independent_memory(self):
        inventory = await self.client.model_inventory()
        self.assertTrue(inventory["h3_memory_optimization"])
        self.assertFalse(inventory["h3_optimizations"])
        self.assertTrue(inventory["h3_sla_attention"])
        self.assertTrue(inventory["h3_sigma_shift"])
        for key in ("fl2v_768_sla", "fl2v_768_audio_v12", "ref2v_768_quality_v10"):
            self.assertTrue(inventory[f"turbo_{key}"])
            self.assertEqual(await self.client.resolve_turbo_lora(key), TURBO_LORA_CANDIDATES[key][0])

    async def test_sla_requires_correct_dynamic_schema_not_just_http_200(self):
        await self.client.model_inventory()
        self.schemas["BlockSparseAttention"] = {"input": {"required": {"selection": [["sol-attn"]]}}}
        self.assertFalse((await self.client.model_inventory(refresh=True))["h3_sla_attention"])
        del self.schemas["H3MemoryOptimization"]
        self.assertFalse((await self.client.model_inventory(refresh=True))["h3_memory_optimization"])


if __name__ == "__main__":
    unittest.main()
