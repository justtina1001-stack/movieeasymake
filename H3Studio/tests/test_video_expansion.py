"""Released model recipes remain explicit from saved settings to executable graph."""
import copy
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from comfy_client import ComfyClient
from domain import CompiledRequest, RequestError, build_workflow, compile_request, required_asset_ids, validate_runtime_inventory
from generation_review import GenerationReviewStore, compiled_from_review
from settings import ConnectionSettings
from shortfilm import ShortFilmStore, compile_shot_payload
from tests.test_fasth3_video import native_schemas
from tests.test_fasth3_workspaces import project
from video_models import (PIKA_LORA_FILENAMES, PIKA_SIGMAS, PIKA_SIGMAS_TEXT, REF_PATCH_FILENAME,
                          VIDEO_MODEL_FAST, VIDEO_MODEL_H3, VIDEO_MODEL_PIKA, VIDEO_MODEL_REF_PATCH,
                          VIDEO_MODES, REFERENCE_VIDEO_MODES, video_model_profiles)

A, B, C, V, AUDIO = (character * 32 for character in 'abcde')


def payload(model=VIDEO_MODEL_PIKA, mode='t2v', **values):
    return dict(video_model=model, mode=mode, prompt='The actor raises one hand and lowers it.',
                duration=5, megapixels=.4, aspect_ratio='16:9', seed=42, quality_mode='native', **values)


def reference_payload(model=VIDEO_MODEL_PIKA, **values):
    return payload(model, 'r2v', references=[dict(alias='Actor', type='character', image_asset_ids=[A])], **values)


def workflow(compiled):
    return build_workflow(compiled, {asset:'uploads/'+asset for asset in required_asset_ids(compiled)}, 'profile-test')


def node(graph, kind):
    return next(value for value in graph.values() if value['class_type'] == kind)


class VideoExpansionCompileTests(unittest.TestCase):
    def test_pika_uses_author_six_step_schedule_and_no_double_shift(self):
        compiled = compile_request(payload(steps=40, scheduler='karras'))
        self.assertEqual((compiled.steps, compiled.sampler_name, compiled.scheduler), (6,'lcm','manual'))
        self.assertEqual(compiled.manual_sigmas, list(PIKA_SIGMAS))
        self.assertIsNone(compiled.shift_video)
        self.assertIsNone(compiled.turbo_profile)
        graph = workflow(compiled)
        self.assertEqual(node(graph,'ManualSigmas')['inputs'], {'sigmas':PIKA_SIGMAS_TEXT})
        self.assertEqual(node(graph,'LoraLoaderModelOnly')['inputs']['lora_name'], PIKA_LORA_FILENAMES['fl2va'])
        self.assertEqual(node(graph,'LoraLoaderModelOnly')['inputs']['strength_model'], 1.0)
        kinds = [value['class_type'] for value in graph.values()]
        self.assertNotIn('MiniMaxH3SigmaShift', kinds)
        self.assertNotIn('BasicScheduler', kinds)
        self.assertNotIn('H3MemoryOptimization', kinds)

    def test_pika_preserves_all_mode_media_and_selects_matching_adapter(self):
        cases = {
            't2v':{}, 'fl2va':dict(first_image_asset_id=A,last_image_asset_id=B),
            'extend':dict(first_image_asset_id=A), 'symbol_loop':dict(first_image_asset_id=A),
            'r2v':dict(references=[dict(alias='Actor',type='character',image_asset_ids=[A],video_asset_id=V,video_use_audio=True,audio_asset_id=AUDIO)]),
            'replace':dict(references=[dict(alias='Actor',type='character',image_asset_ids=[A],video_asset_id=V)]),
            'popup_panel':dict(references=[dict(alias='背景圖',type='background',image_asset_ids=[A]),dict(alias='面板',type='object',image_asset_ids=[B])]),
            'mg_animation':dict(references=[dict(alias='背景圖',type='background',image_asset_ids=[A]),dict(alias='轉輪帶',type='object',image_asset_ids=[B]),dict(alias='角色',type='character',image_asset_ids=[C])]),
        }
        self.assertEqual(set(cases), set(VIDEO_MODES))
        for mode, extra in cases.items():
            with self.subTest(mode=mode):
                compiled = compile_request(payload(mode=mode, **extra))
                graph = workflow(compiled)
                family = 'ref2va' if mode in REFERENCE_VIDEO_MODES else 'fl2va'
                self.assertEqual(compiled.model_adapter, PIKA_LORA_FILENAMES[family])
                self.assertEqual(node(graph,'UNETLoader')['inputs']['unet_name'], f'minimax_h3_{family}_pruned_int8_convrot.safetensors')
                if mode == 'fl2va':
                    self.assertIn('first_frame',node(graph,'MiniMaxH3ImageToVideo')['inputs'])
                    self.assertIn('last_frame',node(graph,'MiniMaxH3ImageToVideo')['inputs'])
                if mode == 'r2v':
                    inputs = node(graph,'MiniMaxH3ReferenceToVideo')['inputs']
                    self.assertIn('ref_videos.ref_video_0',inputs)
                    self.assertIn('ref_video_audios.ref_video_audio_0',inputs)
                    self.assertIn('ref_audios.ref_audio_0',inputs)

    def test_ref_patch_uses_fl2va_base_with_reference_conditioning(self):
        compiled = compile_request(reference_payload(VIDEO_MODEL_REF_PATCH, steps=40, scheduler='beta'))
        graph = workflow(compiled)
        self.assertEqual((compiled.steps,compiled.sampler_name,compiled.scheduler),(20,'res_multistep','simple'))
        self.assertEqual(node(graph,'UNETLoader')['inputs']['unet_name'],'minimax_h3_fl2va_pruned_int8_convrot.safetensors')
        self.assertEqual(node(graph,'MiniMaxH3RefPatchLoader')['inputs'],{'model':['1',0],'patch_name':REF_PATCH_FILENAME,'ref_strength':1.0})
        self.assertIn('ref_images.ref_image_0',node(graph,'MiniMaxH3ReferenceToVideo')['inputs'])
        self.assertEqual(node(graph,'BasicGuider')['inputs']['model'],['2',0])
        for mode in set(VIDEO_MODES)-set(REFERENCE_VIDEO_MODES):
            with self.subTest(mode=mode), self.assertRaisesRegex(RequestError,'Ref Patch'):
                compile_request(payload(VIDEO_MODEL_REF_PATCH,mode))

    def test_special_models_do_not_silently_stack_other_recipes(self):
        for model in (VIDEO_MODEL_PIKA,VIDEO_MODEL_REF_PATCH):
            for changes in (dict(quality_mode='turbo'),dict(quality_mode='invalid'),dict(memory_optimization=True),
                            dict(custom_loras=[dict(name='x.safetensors',strength=.5)]),
                            dict(custom_loras=[dict(name='x.safetensors',strength=-.5)])):
                with self.subTest(model=model,changes=changes), self.assertRaises(RequestError):
                    compile_request({**reference_payload(model),**changes})
            compiled=compile_request(reference_payload(model,custom_loras=[dict(name='x.safetensors',strength=.5,enabled=False)]))
            self.assertFalse(any(value['inputs'].get('lora_name')=='x.safetensors' for value in workflow(compiled).values()))
        for filename in PIKA_LORA_FILENAMES.values():
            with self.assertRaisesRegex(RequestError,'Pika'):
                compile_request(payload(VIDEO_MODEL_H3,custom_loras=[dict(name=filename,strength=1)]))

    def test_builder_rejects_tampered_family_schedule_and_patch_recipe(self):
        compiled=compile_request(reference_payload())
        for changes in (dict(steps=8),dict(sampler_name='euler'),dict(scheduler='simple'),
                        dict(manual_sigmas=[1,.5,0]),dict(model_adapter=PIKA_LORA_FILENAMES['fl2va']),
                        dict(model_adapter_strength=.5),dict(shift_video=12),dict(memory_optimization=True),
                        dict(face_repair={'source_asset_id':V})):
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                workflow(replace(compiled,**changes))
        patched=compile_request(reference_payload(VIDEO_MODEL_REF_PATCH))
        for changes in (dict(steps=8),dict(mode='t2v'),dict(model_adapter=PIKA_LORA_FILENAMES['ref2va'])):
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                workflow(replace(patched,**changes))

    def test_new_turbo_profiles_are_explicit_and_old_profile_is_unchanged(self):
        cases = [
            ('turbo_fast_v13','t2v',4,'fl2v_768_fast_v13','fl2v','1.3',6,.75),
            ('turbo_quality_v11','t2v',8,'fl2v_768_quality_v11','fl2v','1.1',6,.75),
            ('turbo_ref_fast_v10','r2v',4,'ref2v_768_fast_v10','ref2v','1.0',12,1),
            ('turbo_ref_quality_v11','r2v',8,'ref2v_768_quality_v11','ref2v','1.1',12,1),
        ]
        for quality,mode,steps,profile,family,version,shift,strength in cases:
            with self.subTest(quality=quality):
                raw = reference_payload(VIDEO_MODEL_H3) if mode=='r2v' else payload(VIDEO_MODEL_H3)
                raw['quality_mode']=quality
                compiled=compile_request(raw)
                expected=f'minimax_h3_{family}_turbo_{steps}step_v{version}_768p_comfyui_bf16.safetensors'
                self.assertEqual((compiled.steps,compiled.turbo_profile,compiled.turbo_lora),(steps,profile,expected))
                self.assertEqual((compiled.shift_video,compiled.shift_audio,compiled.turbo_lora_strength),(shift,3,strength))
                self.assertEqual(node(workflow(compiled),'LoraLoaderModelOnly')['inputs']['lora_name'],expected)
                raw['mode']='t2v' if mode=='r2v' else 'r2v'
                with self.assertRaises(RequestError): compile_request(raw)
        old=compile_request({**reference_payload(VIDEO_MODEL_H3),'quality_mode':'turbo_ref_quality'})
        self.assertIn('_v1.0_',old.turbo_lora)

    def test_saved_reviews_preserve_exact_adapter_and_manual_schedule(self):
        raw=reference_payload()
        compiled=compile_request(raw)
        store=GenerationReviewStore()
        receipt=store.issue(raw,raw,compiled)
        _,record=store.claim({**raw,'review_receipt':receipt['review_receipt']})
        restored=compiled_from_review(asdict(record['compiled']))
        self.assertEqual(workflow(restored),workflow(compiled))
        old=asdict(compile_request(payload(VIDEO_MODEL_H3)))
        for key in ('model_adapter','model_adapter_strength','manual_sigmas'):old.pop(key)
        self.assertEqual(workflow(CompiledRequest(**old)),workflow(compile_request(payload(VIDEO_MODEL_H3))))

    def test_shortfilm_switches_recipe_without_destroying_h3_preferences(self):
        for model in (VIDEO_MODEL_PIKA,VIDEO_MODEL_REF_PATCH):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                original=project(); original['video_model']=model
                store=ShortFilmStore(Path(directory)); saved=store.create(original); before=copy.deepcopy(saved)
                scene,shot=saved['scenes'][0],saved['scenes'][0]['shots'][0]
                raw,_=compile_shot_payload(saved,scene['id'],shot['id'])
                self.assertEqual((raw['custom_loras'],raw['memory_optimization'],raw['quality_mode']),([],False,'native'))
                self.assertEqual(compile_request(raw).video_model,model)
                self.assertEqual(saved,before)
                saved['video_model']=VIDEO_MODEL_H3
                restored,_=compile_shot_payload(saved,scene['id'],shot['id'])
                self.assertEqual(restored['custom_loras'],before['custom_loras'])
                self.assertEqual(restored['quality_mode'],before['quality_mode'])

    def test_profile_readiness_is_family_specific_and_preserves_original_models(self):
        inventory=dict(text_encoder=True,audio_vae=True,video_vae=True,video_vae_fp16=True,
                       fl2va=True,ref2va=False,pika_fl2va=True,pika_ref2va=True,pika_nodes_ready=True,ref_patch_nodes_ready=True)
        profiles={item['id']:item for item in video_model_profiles(inventory)}
        self.assertTrue(profiles[VIDEO_MODEL_PIKA]['mode_ready']['t2v'])
        self.assertFalse(profiles[VIDEO_MODEL_PIKA]['mode_ready']['r2v'])
        self.assertTrue(profiles[VIDEO_MODEL_REF_PATCH]['mode_ready']['r2v'])
        self.assertFalse(profiles[VIDEO_MODEL_REF_PATCH]['mode_ready']['t2v'])
        validate_runtime_inventory(compile_request(payload()),inventory)
        validate_runtime_inventory(compile_request(reference_payload(VIDEO_MODEL_REF_PATCH)),inventory)
        with self.assertRaises(RequestError):validate_runtime_inventory(compile_request(reference_payload()),inventory)


class VideoExpansionInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schemas=native_schemas()
        self.schemas['LoraLoaderModelOnly']={'input':{'required':{'lora_name':[list(PIKA_LORA_FILENAMES.values())]}}}
        self.schemas['ManualSigmas']={'input':{'required':{'sigmas':['STRING']}},'output':['SIGMAS']}
        self.schemas['KSamplerSelect']={'input':{'required':{'sampler_name':['COMBO',{'options':['euler','lcm']}]}}}
        self.schemas['MiniMaxH3RefPatchLoader']={'input':{'required':{'model':['MODEL'],'patch_name':[[REF_PATCH_FILENAME]],'ref_strength':['FLOAT']}},'output':['MODEL']}
        app=web.Application()
        async def info(request):
            name=request.match_info['node'];return web.json_response({name:self.schemas[name]} if name in self.schemas else {})
        async def stats(request):return web.json_response({'system':{'comfyui_version':'0.42.0'},'devices':[]})
        app.router.add_get('/object_info/{node}',info);app.router.add_get('/system_stats',stats)
        self.runner=web.AppRunner(app);await self.runner.setup();self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.client=ComfyClient(ConnectionSettings(mode='remote',base_url=f'http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}',comfy_dir=str(self.root),auto_start_local=False),self.root)

    async def asyncTearDown(self):
        await self.runner.cleanup();self.temp.cleanup()

    async def test_native_lcm_and_patch_schema_are_checked_not_just_names(self):
        inventory=await self.client.model_inventory()
        self.assertTrue(inventory['pika_nodes_ready']);self.assertTrue(inventory['ref_patch_nodes_ready'])
        self.schemas['KSamplerSelect']['input']['required']['sampler_name'][1]['options']=['euler']
        self.assertFalse((await self.client.model_inventory(refresh=True))['pika_nodes_ready'])
        self.schemas['MiniMaxH3RefPatchLoader']['input']['required']['ref_strength']=['INT']
        self.assertFalse((await self.client.model_inventory(refresh=True))['ref_patch_nodes_ready'])
        self.assertTrue((await self.client.model_inventory(refresh=True))['ref2va'])

    async def test_local_adapter_and_patch_sizes_are_checked_independently(self):
        self.client.mode='local'
        for family,filename in PIKA_LORA_FILENAMES.items():
            path=self.root/'models/loras'/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'model')
        patch_path=self.root/'models/model_patches'/REF_PATCH_FILENAME;patch_path.parent.mkdir(parents=True);patch_path.write_bytes(b'patch')
        with patch('comfy_client.PIKA_LORA_SIZE',5),patch('comfy_client.REF_PATCH_SIZE',5):
            inventory=await self.client.model_inventory(refresh=True)
            self.assertTrue(inventory['pika_fl2va']);self.assertTrue(inventory['pika_ref2va']);self.assertTrue(inventory['ref_patch_nodes_ready'])
            (self.root/'models/loras'/PIKA_LORA_FILENAMES['ref2va']).write_bytes(b'bad')
            patch_path.write_bytes(b'bad')
            inventory=await self.client.model_inventory(refresh=True)
            self.assertTrue(inventory['pika_fl2va']);self.assertFalse(inventory['pika_ref2va']);self.assertFalse(inventory['ref_patch_nodes_ready'])


if __name__=='__main__':unittest.main()
