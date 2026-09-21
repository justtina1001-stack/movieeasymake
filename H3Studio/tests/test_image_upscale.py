import unittest
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestServer
from domain import RequestError
from image_upscale import compile_upscale, build_upscale_workflow, upscale_capabilities, MODELS


class UpscaleTests(unittest.TestCase):
    def test_exact_ratio_and_original_alpha_path(self):
        job = compile_upscale({'scale':2,'model':'general'}, (513,287))
        self.assertEqual((job['width'],job['height']),(1026,574))
        job['image_asset_ids']=['source']
        graph=build_upscale_workflow(job,{'source':'host/reference.png'},'job')
        self.assertEqual(graph['1']['inputs']['image'],'host/reference.png')
        self.assertEqual(graph['2']['inputs']['model_name'],MODELS['general']['filename'])
        self.assertEqual(graph['4']['inputs']['crop'],'disabled')
        self.assertEqual(graph['7']['inputs']['alpha'],['1',1])
        self.assertEqual(graph['8']['inputs']['images'],['7',0])
        self.assertFalse(any(n['class_type']=='KSampler' for n in graph.values()))

    def test_rejects_unbounded_dimensions_models_and_factors(self):
        for payload,size in [([], (512,512)), ({'scale':True},(512,512)), ({'scale':2.0},(512,512)),
                             ({'scale':8},(512,512)), ({'model':'../../file'},(512,512)),
                             ({'model':[]},(512,512)), ({'scale':4},(1025,512)), ({},(4096,4096))]:
            with self.subTest(payload=payload,size=size),self.assertRaises(RequestError):
                compile_upscale(payload,size)
        self.assertEqual(compile_upscale({'scale':4},(1024,1024))['width'],4096)


class UpscaleCapabilitiesTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_legacy_comfy_combo_schema_and_missing_models(self):
        files=[MODELS['anime']['filename']]
        modern=True
        async def info(request):
            name=request.match_info['name']
            field=['COMBO',{'options':files}] if modern else [files]
            return web.json_response({name:{'input':{'required':{'model_name':field}}}})
        engine=web.Application(); engine.router.add_get('/object_info/{name}',info)
        server=TestServer(engine); await server.start_server(); self.addAsyncCleanup(server.close)
        comfy=SimpleNamespace(base_url=str(server.make_url('/')).rstrip('/'),auth_headers=lambda:{})
        for modern in (True,False):
            status=await upscale_capabilities(comfy)
            self.assertTrue(status['ready'])
            self.assertFalse(status['models'][0]['installed'])
            self.assertTrue(status['models'][1]['installed'])
        files.clear()
        self.assertFalse((await upscale_capabilities(comfy))['ready'])
