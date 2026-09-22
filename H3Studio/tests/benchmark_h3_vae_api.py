"""Opt-in VAE benchmark using the existing ComfyUI queue, never a second GPU process.

Run with ComfyUI's Python. Requires prepared inputs.json and downloaded VAEs.
Outputs lossless 8-bit PNGs for the first decode of each model/resolution, then
video previews for three repeats. Unique byte-identical latent input filenames
force real decoding instead of graph-cache hits. WebSocket node wall times
include model transfers and CPU output transfer, but exclude PNG/video saving.
GPU memory is sampled whole-device NVML usage, not model-only VRAM.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

import aiohttp
import psutil
import pynvml


VAES = {
    'fp16': 'minimax_h3_video_vae_fp16.safetensors',
    'int8': 'minimax_h3_video_vae_int8_convrot.safetensors',
}


def node(class_type, **inputs):
    return {'class_type': class_type, 'inputs': inputs}


class Runner:
    def __init__(self, args):
        self.args = args
        self.root = args.root.resolve()
        self.directory = args.directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / 'benchmark.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'method': 'Existing ComfyUI API; node wall-clock timing; NVML whole-device samples',
            'memory_sampling_interval_seconds': 0.5,
            'quality_output_format': '8-bit RGB PNG; float differences below quantization are not recoverable',
            'runs': [],
        }
        if self.state['runs']:
            raise RuntimeError('This directory already contains benchmark runs. Use a fresh directory (with inputs.json) so first-load and warm trials cannot be mixed after restart.')
        self.state.setdefault('run_id', uuid.uuid4().hex[:10])
        self.client_id = 'vae-benchmark-' + uuid.uuid4().hex
        self.active = None
        self.current_node = None
        self.node_started = None
        self.done = asyncio.Event()
        self.stop = False
        self.pending_events = []
        self.process = psutil.Process(args.comfy_pid)
        pynvml.nvmlInit()
        self.gpu = pynvml.nvmlDeviceGetHandleByIndex(0)

    def save(self):
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        tmp.replace(self.state_path)

    async def get(self, path):
        async with self.session.get(self.args.url + path) as response:
            response.raise_for_status()
            return await response.json()

    async def post(self, path, payload):
        async with self.session.post(self.args.url + path, json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f'{path}: {response.status}: {data}')
            return data

    async def wait_idle(self):
        last_notice = 0
        while True:
            queue = await self.get('/queue')
            running, pending = len(queue.get('queue_running', [])), len(queue.get('queue_pending', []))
            if not running and not pending:
                return
            if time.monotonic() - last_notice > 55:
                print(f'WAITING: existing engine work; running={running}, pending={pending}', flush=True)
                last_notice = time.monotonic()
            await asyncio.sleep(5)

    def close_node(self, now):
        if self.active is not None and self.current_node is not None and self.node_started is not None:
            self.active['nodes'].setdefault(self.current_node, {})['seconds'] = round(now - self.node_started, 6)

    async def clear_idle_cache(self):
        await self.wait_idle()
        await self.post('/free', {'unload_models': True, 'free_memory': True})
        await asyncio.sleep(4)

    async def events(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(msg.data)
            kind, data = event.get('type'), event.get('data', {})
            if self.active is not None and 'prompt_id' not in self.active and data.get('prompt_id'):
                self.pending_events.append((event, time.perf_counter()))
                continue
            if self.active is None or data.get('prompt_id') != self.active.get('prompt_id'):
                continue
            now = time.perf_counter()
            self.handle_event(kind, data, now)

    def handle_event(self, kind, data, now):
        if kind == 'execution_start':
            self.active['execution_start'] = time.time()
        elif kind == 'execution_cached':
            self.active['cached_nodes'] = data.get('nodes', [])
        elif kind == 'executing':
            self.close_node(now)
            self.current_node, self.node_started = data.get('node'), now
            if self.current_node:
                print(f"NODE {self.active['label']}: {self.active['classes'].get(self.current_node, self.current_node)}", flush=True)
        elif kind in ('execution_success', 'execution_error', 'execution_interrupted'):
            self.close_node(now)
            self.current_node = None
            self.active['result_event'] = kind
            if kind != 'execution_success':
                self.active['error'] = {k: data.get(k) for k in ('node_id', 'node_type', 'exception_type', 'exception_message')}
            self.active['execution_end'] = time.time()
            self.done.set()

    async def monitor(self):
        while not self.stop:
            if self.active is not None and self.current_node is not None:
                memory = pynvml.nvmlDeviceGetMemoryInfo(self.gpu)
                values = {'gpu_used_MiB': memory.used / 1024 ** 2,
                          'comfy_rss_MiB': self.process.memory_info().rss / 1024 ** 2,
                          'system_available_MiB': psutil.virtual_memory().available / 1024 ** 2}
                try:
                    values['gpu_power_W'] = pynvml.nvmlDeviceGetPowerUsage(self.gpu) / 1000
                except pynvml.NVMLError:
                    pass
                metrics = self.active['nodes'].setdefault(self.current_node, {}).setdefault('samples', {})
                for key, value in values.items():
                    point = metrics.setdefault(key, {'min': value, 'max': value, 'sum': 0, 'count': 0})
                    point['min'], point['max'] = min(point['min'], value), max(point['max'], value)
                    point['sum'] += value
                    point['count'] += 1
            await asyncio.sleep(0.5)

    async def run_graph(self, label, graph, uncached):
        await self.wait_idle()
        run = {'label': label, 'classes': {k: v['class_type'] for k, v in graph.items()},
               'nodes': {}, 'submitted_at': time.time(), 'expected_uncached': uncached}
        # Buffer early websocket events until the POST response provides the id.
        self.active = run
        self.pending_events.clear()
        self.done.clear()
        self.current_node = self.node_started = None
        graph_file = self.directory / f'{label}.workflow.json'
        graph_file.write_text(json.dumps(graph, indent=2) + '\n', encoding='utf-8')
        result = await self.post('/prompt', {'prompt': graph, 'client_id': self.client_id})
        run['prompt_id'] = result['prompt_id']
        for event, stamp in self.pending_events:
            data = event.get('data', {})
            if data.get('prompt_id') == run['prompt_id']:
                self.handle_event(event.get('type'), data, stamp)
        self.pending_events.clear()
        self.state['runs'].append(run)
        self.save()
        print(f'SUBMITTED {label}: {run["prompt_id"]}', flush=True)
        while not self.done.is_set():
            try:
                await asyncio.wait_for(self.done.wait(), timeout=10)
            except asyncio.TimeoutError:
                history = await self.get('/history/' + run['prompt_id'])
                if run['prompt_id'] in history:
                    status = history[run['prompt_id']].get('status', {})
                    fallback_event = 'execution_success' if status.get('status_str') == 'success' else 'execution_error'
                    run['result_event'] = run.get('result_event', fallback_event)
                    if fallback_event != 'execution_success':
                        run['error'] = next((entry[1] for entry in status.get('messages', []) if entry[0] in ('execution_error', 'execution_interrupted')), {'status': status.get('status_str')})
                    run['completion_poll_fallback'] = True
                    self.close_node(time.perf_counter())
                    self.done.set()
                self.save()
        # execution_success is sent before the queue publishes its history.
        deadline = time.monotonic() + 30
        while True:
            histories = await self.get('/history/' + run['prompt_id'])
            if run['prompt_id'] in histories:
                history = histories[run['prompt_id']]
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Completed benchmark did not publish history')
            await asyncio.sleep(0.2)
        run['outputs'] = history.get('outputs', {})
        run['history_status'] = history.get('status', {})
        run['wall_seconds'] = round(time.time() - run['submitted_at'], 6)
        run['timing_valid'] = all(k in run['nodes'] and k not in run.get('cached_nodes', []) and 'seconds' in run['nodes'][k] for k in uncached)
        self.save()
        self.active = None
        if run['result_event'] != 'execution_success':
            raise RuntimeError(f'{label} failed: {run.get("error")}')
        if not run['timing_valid']:
            raise RuntimeError(f'{label} did not execute expected nodes or timing events were missed')
        print('COMPLETE ' + label + ': ' + json.dumps({k: round(v.get('seconds', 0), 3) for k, v in run['nodes'].items()}), flush=True)
        return run

    def output_path(self, locator):
        if locator['type'] != 'output':
            raise RuntimeError('Unexpected output type')
        root = (self.root / 'ComfyUI/output').resolve()
        path = (root / locator.get('subfolder', '') / locator['filename']).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError('Output escaped ComfyUI output directory')
        return path

    async def benchmark(self):
        inputs = json.loads((self.directory / 'inputs.json').read_text())
        await self.wait_idle()
        # Clear previous inactive models/cached tensors only while queue is idle.
        # These flags are handled by the engine worker; no running job is interrupted.
        await self.clear_idle_cache()
        self.state['baseline_gpu_MiB'] = pynvml.nvmlDeviceGetMemoryInfo(self.gpu).used / 1024 ** 2
        self.state['system_stats'] = await self.get('/system_stats')
        self.save()
        for clip in inputs['clips']:
            case = f"{clip['width']}x{clip['height']}"
            encodes = {}
            for variant in ('fp16', 'int8'):
                await self.clear_idle_cache()
                graph = {
                    '1': node('LoadVideo', file=clip['filename']),
                    '2': node('GetVideoComponents', video=['1', 0]),
                    '3': node('VAELoader', vae_name=VAES[variant]),
                    '4': node('VAEEncode', pixels=['2', 0], vae=['3', 0]),
                    '5': node('SaveLatent', samples=['4', 0], filename_prefix=f"vae_benchmark_20260921/{self.state['run_id']}/{case}/encode_{variant}"),
                }
                encodes[variant] = await self.run_graph(f'{case}_encode_{variant}', graph, ['4'])
            latent = self.output_path(encodes['fp16']['outputs']['5']['latents'][0])
            latent_hash = hashlib.sha256(latent.read_bytes()).hexdigest()
            # Use the exact same saved FP16-encoded latent for all decoder arms.
            # Each path is different so LoadLatent and downstream VAEDecode rerun.
            for variant in ('fp16', 'int8'):
                # Give each VAE the same exclusive model/cache starting state.
                # Trial 0 includes first GPU load; 1..3 are warm, real decodes.
                await self.clear_idle_cache()
                for trial in range(4):
                    label = f'{case}_decode_{variant}_{trial}'
                    filename = f"vae_benchmark_20260921_{self.state['run_id']}_{label}.latent"
                    target = self.root / 'ComfyUI/input' / filename
                    if not target.exists():
                        shutil.copyfile(latent, target)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != latent_hash:
                        raise RuntimeError('Decoder input differs from shared latent')
                    prefix = f"vae_benchmark_20260921/{self.state['run_id']}/{case}/{variant}/trial_{trial}/frame"
                    graph = {
                        '1': node('LoadLatent', latent=filename),
                        '2': node('VAELoader', vae_name=VAES[variant]),
                        '3': node('VAEDecode', samples=['1', 0], vae=['2', 0]),
                    }
                    if trial == 0:
                        graph['4'] = node('SaveImage', images=['3', 0], filename_prefix=prefix)
                    else:
                        graph['4'] = node('CreateVideo', images=['3', 0], fps=24.0)
                        graph['5'] = node('SaveVideo', video=['4', 0], filename_prefix=prefix, format='mp4', **{'format.codec': 'h264'})
                    run = await self.run_graph(label, graph, ['1', '3'])
                    run['latent_sha256'] = latent_hash
                    self.save()
        self.state['completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        self.save()
        print('ALL BENCHMARK RUNS COMPLETE', flush=True)

    async def main(self):
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as self.session:
            async with self.session.ws_connect(self.args.url.replace('http', 'ws', 1) + '/ws?clientId=' + self.client_id, heartbeat=30) as ws:
                event_task = asyncio.create_task(self.events(ws))
                monitor_task = asyncio.create_task(self.monitor())
                try:
                    await self.benchmark()
                finally:
                    self.stop = True
                    event_task.cancel()
                    monitor_task.cancel()
                    await asyncio.gather(event_task, monitor_task, return_exceptions=True)
                    self.save()
                    pynvml.nvmlShutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8188')
    parser.add_argument('--comfy-pid', type=int, required=True)
    args = parser.parse_args()
    asyncio.run(Runner(args).main())


if __name__ == '__main__':
    main()
