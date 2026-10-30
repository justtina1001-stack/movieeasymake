import asyncio
import copy
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import wave

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from sound_effects import (SoundEffectJobManager, SoundEffectError, MODEL_ID,
                           compile_sound_effect_request, register_sound_effect_routes)
from queue_presentation import build_queue_view
from video_editor import EditorStore, EditorError


def wav_file(path, duration=2):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(struct.pack('<h', 1000) * (48000 * duration))


class RequestTests(unittest.TestCase):
    def test_bilingual_request_preserves_text_and_fixed_seed_without_private_extra_fields(self):
        payload = {'prompt': '一聲金屬撞擊，no music.', 'duration': 3.5, 'seed': '314159',
                   'job_name': '撞擊', 'model_dir': 'untrusted', 'remote_access_token': 'secret'}
        original = copy.deepcopy(payload)
        result = compile_sound_effect_request(payload)
        self.assertEqual(result['prompt'], payload['prompt'])
        self.assertEqual(result['seed'], 314159)
        self.assertEqual(result['steps'], 100)
        self.assertNotIn('model_dir', result)
        self.assertNotIn('remote_access_token', result)
        self.assertEqual(payload, original)

    def test_invalid_inputs_fail_before_work_is_created(self):
        for change in ({'prompt': ''}, {'prompt': []}, {'prompt': 'a'*2001}, {'duration': float('nan')},
                       {'duration': 0}, {'duration': 31}, {'duration': True}, {'steps': 4},
                       {'steps': 100.0}, {'seed': -1}, {'seed': 2**53}, {'seed': 3.4},
                       {'job_name': []}):
            with self.subTest(change=change), self.assertRaises(SoundEffectError):
                compile_sound_effect_request({'prompt': 'Water splashes.', **change})
        with self.assertRaises(SoundEffectError):
            compile_sound_effect_request([])

    def test_duration_matches_worker_tenth_second_precision(self):
        self.assertEqual(compile_sound_effect_request({'prompt':'Wind','duration':3.56})['duration'], 3.6)


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lock = asyncio.Lock()
        self.manager = SoundEffectJobManager(self.root, self.lock)
        self.manager.public_status = lambda: {'installed': True, 'available': True}
        self.manager.python_path = Path(sys.executable)
        self.manager.worker_path = self.root / 'worker.py'
        self.manager.worker_path.write_text('''import argparse, json, wave, struct, time, os, subprocess, sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('action'); p.add_argument('--request'); p.add_argument('--output'); p.add_argument('--metrics'); a=p.parse_args()
r=json.load(open(a.request,encoding='utf-8')); print(json.dumps({'stage':'產生音效'}),flush=True)
if r['prompt']=='tree':
 Path(a.output+'.parentpid').write_text(str(os.getpid()))
 subprocess.Popen([sys.executable,'-c',"import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",a.output+'.childpid'])
 time.sleep(60)
if r['prompt']=='slow': time.sleep(60)
if r['prompt']=='oom': raise RuntimeError('CUDA out of memory')
with wave.open(a.output,'wb') as w:
 w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(struct.pack('<h',1000)*int(48000*r['seconds']))
json.dump({'generation_seconds':0.12,'peak_allocated_gib':1.5,'sample_rate':48000},open(a.metrics,'w'))
''', encoding='utf-8')

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def test_worker_result_metrics_and_seed_persist_and_editor_output_is_confined(self):
        job = self.manager.create({'prompt': 'Metal impact', 'duration': 2, 'seed': 42})
        await self.manager.tasks[job['id']]
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['performance']['peak_allocated_gib'], 1.5)
        self.assertGreater(self.manager.local_output_path(job).stat().st_size, 128)
        saved = json.loads((self.manager.job_dir / f"{job['id']}.request.json").read_text(encoding='utf-8'))
        self.assertEqual(saved['seed'], 42)
        self.assertFalse((self.manager.job_dir / f"{job['id']}.worker.json").exists())
        bad = {**job, 'local_output': '../outside.wav'}
        self.assertIsNone(self.manager.local_output_path(bad))
        bad['local_output'] = job['local_output']; bad['status'] = 'failed'
        self.assertIsNone(self.manager.local_output_path(bad))

    async def test_memory_error_preserves_diagnostics_without_claiming_shorter_output_fixes_latent_memory(self):
        job = self.manager.create({'prompt':'oom','seed':42})
        await self.manager.tasks[job['id']]
        self.assertEqual(job['status'],'failed')
        self.assertEqual(job['error_code'],'gpu_out_of_memory')
        self.assertIn('其他',job['error'])
        self.assertIn('CUDA out of memory',job['error_detail'])
        self.assertNotIn('縮短',job['error'])

    async def test_shared_gpu_lock_prevents_worker_launch_and_cancel_does_not_touch_other_work(self):
        await self.lock.acquire()
        job = self.manager.create({'prompt': 'Water', 'seed': 9})
        await asyncio.sleep(0)
        self.assertEqual(job['status'], 'queued')
        self.assertFalse(self.manager.processes)
        await self.manager.cancel(job['id'])
        self.assertEqual(job['status'], 'cancelled')
        self.assertTrue(self.lock.locked())
        self.lock.release()
        self.assertFalse(list(self.manager.output_dir.iterdir()))

    async def test_external_engine_queue_must_be_idle_before_freeing_models_or_loading_worker(self):
        cancel = asyncio.Event()
        fake = SimpleNamespace(queue_status=AsyncMock(return_value={'available': False}))
        self.manager.comfy = fake
        job = self.manager.create({'prompt': 'Wind', 'seed': 8})
        await self.manager.tasks[job['id']]
        self.assertEqual(job['status'], 'failed')
        self.assertIn('佇列', job['error'])
        self.assertFalse(self.manager.processes)
        self.assertFalse(list(self.manager.output_dir.iterdir()))

    async def test_host_gateway_drain_is_called_while_sound_effect_owns_shared_gpu_lock(self):
        async def drain(cancel):
            self.assertTrue(self.lock.locked())
            self.assertFalse(cancel.is_set())
        self.manager.before_local_generate = AsyncMock(side_effect=drain)
        job = self.manager.create({'prompt':'Water','seed':32})
        await self.manager.tasks[job['id']]
        self.manager.before_local_generate.assert_awaited_once()
        self.assertEqual(job['status'],'completed')

    async def test_free_acknowledgement_waits_for_actual_engine_memory_release(self):
        async def free(request):
            self.assertEqual(await request.json(), {'unload_models':True,'free_memory':True})
            return web.json_response({})
        app = web.Application(); app.router.add_post('/free',free)
        client = TestClient(TestServer(app)); await client.start_server()
        try:
            idle = {'available':True,'running_count':0,'pending_count':0}
            self.manager.comfy = SimpleNamespace(mode='local', base_url=str(client.make_url('')).rstrip('/'),
                auth_headers=lambda:{}, queue_status=AsyncMock(return_value=idle),
                system_stats=AsyncMock(side_effect=[{'devices':[{'type':'cuda','torch_vram_total':4*1024**3}]},
                                                    {'devices':[{'type':'cuda','torch_vram_total':0}]}]))
            job = self.manager.create({'prompt':'Water','seed':45})
            await self.manager.tasks[job['id']]
            self.assertEqual(job['status'],'completed',job)
            self.assertEqual(self.manager.comfy.system_stats.await_count,2)
        finally:
            await client.close()

    async def test_new_engine_work_after_free_ack_prevents_sound_effect_worker_start(self):
        async def free(request):
            return web.json_response({})
        app=web.Application(); app.router.add_post('/free',free)
        client=TestClient(TestServer(app)); await client.start_server()
        try:
            idle={'available':True,'running_count':0,'pending_count':0}
            self.manager.comfy=SimpleNamespace(mode='local',base_url=str(client.make_url('')).rstrip('/'),
                auth_headers=lambda:{},queue_status=AsyncMock(side_effect=[idle,{**idle,'running_count':1}]),
                system_stats=AsyncMock())
            job=self.manager.create({'prompt':'Water','seed':46})
            await self.manager.tasks[job['id']]
            self.assertEqual(job['status'],'failed')
            self.assertIn('收到新的工作',job['error'])
            self.manager.comfy.system_stats.assert_not_called()
            self.assertFalse(list(self.manager.output_dir.iterdir()))
        finally:
            await client.close()

    async def test_remote_mode_does_not_silently_start_a_local_worker(self):
        manager = SoundEffectJobManager(self.root/'remote', self.lock, comfy=SimpleNamespace(mode='remote'))
        self.assertFalse(manager.public_status()['available'])
        with self.assertRaises(SoundEffectError):
            manager.create({'prompt':'Water','seed':42})
        self.assertFalse(manager.tasks)

    async def test_network_retry_is_idempotent_even_after_manager_restart(self):
        payload = {'request_id': 'request_12345678', 'prompt': 'Water', 'seed': 44}
        first = self.manager.create(payload)
        self.assertIs(self.manager.create(dict(payload)), first)
        with self.assertRaisesRegex(SoundEffectError, '不同設定'):
            self.manager.create({**payload, 'prompt': 'Wind'})
        await self.manager.tasks[first['id']]
        restarted = SoundEffectJobManager(self.root, self.lock)
        restarted.public_status = self.manager.public_status
        self.assertEqual(restarted.create(payload)['id'], first['id'])
        self.assertFalse(restarted.tasks)

    async def test_running_worker_cancel_reaps_its_process_before_releasing_gpu(self):
        job = self.manager.create({'prompt': 'slow', 'seed': 15})
        for _ in range(100):
            if job['id'] in self.manager.processes:
                break
            await asyncio.sleep(.01)
        process = self.manager.processes[job['id']]
        await asyncio.wait_for(self.manager.cancel(job['id']), timeout=5)
        self.assertEqual(job['status'], 'cancelled')
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.lock.locked())

    @unittest.skipUnless(os.name == 'nt', 'Windows venv redirector regression')
    async def test_windows_cancel_stops_real_parent_and_child_before_releasing_shared_gpu(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        def alive(pid):
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel.WaitForSingleObject(handle, 0) == 258
            finally:
                kernel.CloseHandle(handle)
        job = self.manager.create({'prompt':'tree','seed':61})
        output = self.manager.output_dir / f"{job['id']}.wav"
        parent_file, child_file = Path(str(output)+'.parentpid'), Path(str(output)+'.childpid')
        pids = []
        try:
            async with asyncio.timeout(5):
                while not parent_file.is_file() or not child_file.is_file():
                    await asyncio.sleep(.01)
            pids = [int(parent_file.read_text()), int(child_file.read_text())]
            self.assertTrue(all(alive(pid) for pid in pids))
            self.assertTrue(self.lock.locked())
            cancellation = asyncio.create_task(self.manager.cancel(job['id']))
            async with asyncio.timeout(5):
                while not cancellation.done():
                    if not self.lock.locked():
                        self.assertFalse(any(alive(pid) for pid in pids), 'GPU unlocked while own worker child remains alive')
                    await asyncio.sleep(.005)
                await cancellation
            self.assertFalse(any(alive(pid) for pid in pids))
            self.assertEqual(job['status'],'cancelled')
            self.assertFalse(self.lock.locked())
        finally:
            # Clean up only the fake CPU processes whose IDs this fixture wrote.
            for pid in pids:
                if alive(pid):
                    killer = await asyncio.create_subprocess_exec('taskkill','/PID',str(pid),'/T','/F',
                        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()

    async def test_restarted_active_job_is_interrupted_without_rewriting_original_record(self):
        identifier = 'a'*32
        path = self.manager.job_dir / f'{identifier}.json'
        raw = json.dumps({'id':identifier,'type':MODEL_ID,'status':'running','prompt':'Wind'})
        path.write_text(raw, encoding='utf-8')
        restarted = SoundEffectJobManager(self.root, self.lock)
        self.assertEqual(restarted.jobs[identifier]['status'], 'interrupted')
        self.assertEqual(path.read_text(encoding='utf-8'), raw)


class RoutesAndEditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = SoundEffectJobManager(self.root, asyncio.Lock())
        self.identifier = 'b'*32
        self.path = self.manager.output_dir / f'{self.identifier}.wav'
        wav_file(self.path)
        self.manager.jobs[self.identifier] = {'id':self.identifier, 'type':MODEL_ID, 'name':'法術音效',
            'status':'completed','local_output':self.path.name, 'created_at':'2026-10-30', 'favorite':False}
        self.app = web.Application()
        self.app['sfx_jobs'] = self.manager
        register_sound_effect_routes(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_preview_download_favorites_and_bad_request_contract(self):
        result = await self.client.get(f'/api/sfx/jobs/{self.identifier}/audio?download=1')
        self.assertEqual(result.status, 200)
        self.assertIn('audio/wav', result.headers['Content-Type'])
        self.assertIn("filename*=UTF-8''", result.headers['Content-Disposition'])
        self.assertTrue((await result.read()).startswith(b'RIFF'))
        response = await self.client.post(f'/api/sfx/jobs/{self.identifier}/favorite',json={'favorite':True})
        self.assertTrue((await response.json())['favorite'])
        response = await self.client.post('/api/sfx/jobs',json=[])
        self.assertEqual(response.status, 400)
        response = await self.client.get('/api/sfx/jobs?page=wrong')
        self.assertEqual(response.status, 400)
        self.manager.jobs[self.identifier]['status'] = 'failed'
        self.assertEqual((await self.client.get(f'/api/sfx/jobs/{self.identifier}/audio')).status,404)

    async def test_editor_imports_an_independent_audio_copy_and_saved_audio_track(self):
        store = EditorStore(self.root, object(), SimpleNamespace(jobs={}), sfx_jobs=self.manager)
        media = await store.import_media({'sfx_job_id':self.identifier})
        self.assertEqual(media['kind'],'audio')
        self.assertEqual(media['name'],'法術音效')
        self.assertEqual((await store.import_media({'sfx_job_id':self.identifier}))['id'],media['id'])
        self.assertEqual(store.media_path(media['id']).read_bytes(),self.path.read_bytes())
        project = store.create_project({'name':'音效剪輯'})
        project = store.update_project(project['id'],{'canvas_duration':2,'audio_clips':[
            {'id':'audio_test_1','media_id':media['id'],'in':0,'out':2,'start':0,'track':0,'volume':1}]})
        self.assertEqual(project['audio_clips'][0]['media_id'],media['id'])
        self.path.unlink()
        self.assertTrue(store.media_path(media['id']).is_file())
        with self.assertRaises(EditorError):
            await store.import_media({'sfx_job_id':self.identifier,'path':'arbitrary'})


class QueueTests(unittest.TestCase):
    def test_sound_effect_work_is_visible_as_local_processing(self):
        snapshot = {'available':True,'running_count':0,'pending_count':0,'running':[],'pending':[]}
        result = build_queue_view(snapshot,{}, {}, {},sfx_jobs={'sfx':{'id':'sfx','status':'running'}})
        self.assertEqual(result['jobs']['sfx']['kind'],'sfx')
        self.assertEqual(result['jobs']['sfx']['phase'],'local_processing')
        self.assertEqual(len(result['local_active']),1)


if __name__ == '__main__':
    unittest.main()
