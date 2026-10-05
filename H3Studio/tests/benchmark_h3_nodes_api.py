"""Controlled full-generation A/B runner for isolated ComfyUI test servers.

Uses the existing VAE benchmark Runner for WebSocket timing, cache validation,
history handling and persistence. The manifest supplies fixed graphs/seeds:
    {"cases": [{"name": "fl", "graph": {...}, "uncached": ["sampler_id"]}]}
Each case may supply ``label`` and ``clear_cache`` (defaults true for the first
case, false thereafter). Production is
read-only. If production becomes busy, only this runner's prompt on the TEST
server is interrupted, and no further case is submitted. No model is loaded by
this controller process. Run --self-test for a tiny mocked CPU-only check.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import aiohttp
import psutil
import pynvml

from benchmark_h3_vae_api import Runner as VAERunner


class ProductionBecameBusy(RuntimeError):
    """The production guard stopped further benchmark work."""


def validate_manifest(manifest):
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest must contain a nonempty cases list")
    labels = set()
    for case in cases:
        label = case.get("label") or case.get("name")
        if not isinstance(label, str) or not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label):
            raise ValueError("Case label/name must use letters, digits, underscore or hyphen")
        if label in labels:
            raise ValueError(f"Duplicate case label: {label}")
        labels.add(label)
        graph, uncached = case.get("graph"), case.get("uncached")
        if not isinstance(graph, dict) or not graph or not isinstance(uncached, list):
            raise ValueError(f"{label}: graph must be a node dict and uncached a list")
        if any(not isinstance(key, str) or key not in graph for key in uncached):
            raise ValueError(f"{label}: uncached must contain existing string node IDs")
        samplers = [key for key, value in graph.items() if value.get("class_type") == "SamplerCustomAdvanced"]
        if not samplers or any(key not in uncached for key in samplers):
            raise ValueError(f"{label}: every SamplerCustomAdvanced must be expected uncached")
        if "clear_cache" in case and not isinstance(case["clear_cache"], bool):
            raise ValueError(f"{label}: clear_cache must be boolean")
    return cases


def ensure_distinct_servers(test_url, production_url):
    parsed = [urlsplit(url) for url in (test_url, production_url)]
    if any(p.scheme not in ("http", "https") or not p.hostname or p.query or p.fragment for p in parsed):
        raise ValueError("Both server URLs must be ordinary HTTP(S) server URLs")
    # These are local comparison servers; reject the same effective port even
    # when one endpoint uses localhost and the other a loopback IP alias.
    ports = [p.port or (443 if p.scheme == "https" else 80) for p in parsed]
    if ports[0] == ports[1]:
        raise ValueError("Test and production servers must use different ports")


class H3NodesRunner(VAERunner):
    def __init__(self, args):
        args.url = args.url.rstrip("/")
        args.production_url = args.production_url.rstrip("/")
        ensure_distinct_servers(args.url, args.production_url)
        self.manifest_path = args.input_manifest.resolve()
        manifest_bytes = self.manifest_path.read_bytes()
        self.manifest = json.loads(manifest_bytes)
        self.cases = validate_manifest(self.manifest)
        super().__init__(args)
        self.production_url = args.production_url
        self.studio_url = getattr(args, "studio_url", None)
        if self.studio_url:
            self.studio_url = self.studio_url.rstrip("/")
        self.production_abort = asyncio.Event()
        self.submission_finished = asyncio.Event()
        self.submission_finished.set()
        self.last_submitted_prompt_id = None
        self.production_counts = {"running": 0, "pending": 0, "studio_local_active": 0, "studio_local_waiting": 0}
        self.state.update({
            "benchmark_kind": "H3 custom-node full generation comparison",
            "input_manifest": str(self.manifest_path),
            "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "test_url": args.url, "production_url": self.production_url, "studio_url": self.studio_url,
            "comfy_pid": args.comfy_pid,
            "seed_policy": "Manifest graphs are submitted unchanged; seeds are never rewritten",
            "production_guard": {"poll_seconds": 1.0, "status": "waiting_for_initial_idle"},
            "cpu_percent_note": "Process CPU percent may exceed 100 across multiple cores; system CPU percent is 0-100",
        })

    def check_abort(self):
        if self.production_abort.is_set():
            raise ProductionBecameBusy(self.state["production_guard"].get("reason", "Production guard stopped benchmark"))

    async def production_queue_counts(self):
        async with self.session.get(self.production_url + "/queue", timeout=aiohttp.ClientTimeout(total=5)) as response:
            response.raise_for_status()
            queue = await response.json()
        if not isinstance(queue.get("queue_running"), list) or not isinstance(queue.get("queue_pending"), list):
            raise ValueError("Production /queue response is missing queue lists")
        counts = {"running": len(queue["queue_running"]), "pending": len(queue["queue_pending"]),
                  "studio_local_active": 0, "studio_local_waiting": 0}
        if self.studio_url:
            async with self.session.get(self.studio_url + "/api/queue", timeout=aiohttp.ClientTimeout(total=5)) as response:
                response.raise_for_status()
                studio = await response.json()
            if not isinstance(studio.get("local_active"), list) or not isinstance(studio.get("local_waiting_count"), int):
                raise ValueError("Studio /api/queue response is missing local queue fields")
            counts["studio_local_active"] = len(studio["local_active"])
            counts["studio_local_waiting"] = studio["local_waiting_count"]
        return counts

    async def wait_production_idle(self):
        started, last_notice = time.monotonic(), 0.0
        while True:
            self.production_counts = await self.production_queue_counts()
            if not any(self.production_counts.values()):
                self.state["production_initial_wait_seconds"] = round(time.monotonic() - started, 3)
                return
            if time.monotonic() - last_notice > 55:
                print("WAITING for production idle: " + json.dumps(self.production_counts), flush=True)
                last_notice = time.monotonic()
            await asyncio.sleep(5)

    async def production_watchdog(self):
        while not self.stop:
            try:
                self.production_counts = await self.production_queue_counts()
                reason = "Production received new running/pending work" if any(self.production_counts.values()) else None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                reason = f"Production queue monitoring failed: {type(error).__name__}"
            if reason:
                self.state["production_guard"].update({"status": "stopping", "reason": reason,
                    "detected_at": time.time(), "queue_counts": self.production_counts.copy()})
                self.production_abort.set()
                self.save()
                print("STOPPING BENCHMARK: " + reason, flush=True)
                return
            await asyncio.sleep(1)

    async def wait_idle(self):
        while True:
            self.check_abort()
            queue = await self.get("/queue")
            if not queue.get("queue_running") and not queue.get("queue_pending"):
                self.check_abort()
                return
            await asyncio.sleep(1)

    async def post(self, path, payload):
        if path in ("/prompt", "/free"):
            self.check_abort()
        if path != "/prompt":
            return await super().post(path, payload)
        # The abort handler must wait for a concurrent submission to return its
        # ID before cancelling it; cancelling HTTP submission can orphan a job.
        self.submission_finished.clear()
        try:
            result = await super().post(path, payload)
            self.last_submitted_prompt_id = result["prompt_id"]
            return result
        finally:
            self.submission_finished.set()

    async def interrupt_own_test_work(self):
        await self.submission_finished.wait()
        prompt_id = self.last_submitted_prompt_id
        if not prompt_id:
            return
        queue = await self.get("/queue")
        pending_ids = [item[1] for item in queue.get("queue_pending", []) if item[1] == prompt_id]
        if pending_ids:
            await self.post("/queue", {"delete": pending_ids})
        # Current core supports targeted interruption. Never issue the global
        # form, and never POST to the production URL.
        await self.post("/interrupt", {"prompt_id": prompt_id})
        self.state["production_guard"].update({"test_interrupt_prompt_id": prompt_id,
            "test_interrupt_requested_at": time.time(), "deleted_own_pending_ids": pending_ids})
        self.save()
        last_notice = 0.0
        while True:
            queue = await self.get("/queue")
            owned = [item for key in ("queue_running", "queue_pending") for item in queue.get(key, []) if item[1] == prompt_id]
            if not owned:
                self.state["production_guard"]["test_prompt_stopped_at"] = time.time()
                self.save()
                return
            if time.monotonic() - last_notice > 55:
                print("Waiting for test prompt to acknowledge targeted interrupt", flush=True)
                last_notice = time.monotonic()
            await asyncio.sleep(0.5)

    async def run_graph(self, label, graph, uncached):
        self.check_abort()
        self.last_submitted_prompt_id = None
        execution = asyncio.create_task(super().run_graph(label, graph, uncached))
        abortion = asyncio.create_task(self.production_abort.wait())
        try:
            done, _ = await asyncio.wait((execution, abortion), return_when=asyncio.FIRST_COMPLETED)
            if abortion in done or self.production_abort.is_set():
                await self.interrupt_own_test_work()
                if not execution.done():
                    execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                if self.active is not None and "result_event" not in self.active:
                    self.active["result_event"] = "execution_interrupted"
                    self.active["timing_valid"] = False
                    self.active["stopped_by_production_guard"] = True
                self.state["production_guard"]["status"] = "stopped"
                self.save()
                self.check_abort()
            run = await execution
            # The inherited fallback is useful for completion, but does not
            # provide a precise end event for the last node's stage timing.
            run["precise_ws_timing"] = not run.get("completion_poll_fallback", False)
            self.save()
            if not run["precise_ws_timing"]:
                raise RuntimeError(f"{label}: completion used polling; do not treat stage timing as a valid benchmark")
            return run
        finally:
            abortion.cancel()
            await asyncio.gather(abortion, return_exceptions=True)
            if not execution.done():
                # Covers controller/API exceptions without orphaning its job.
                await self.interrupt_own_test_work()
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)

    async def monitor(self):
        self.process.cpu_percent(None)
        psutil.cpu_percent(None)
        while not self.stop:
            if self.active is not None and self.current_node is not None:
                gpu = pynvml.nvmlDeviceGetMemoryInfo(self.gpu)
                process_memory = self.process.memory_info()
                system_memory = psutil.virtual_memory()
                values = {"gpu_used_MiB": gpu.used / 1024 ** 2,
                    "comfy_rss_MiB": process_memory.rss / 1024 ** 2,
                    "comfy_vms_MiB": process_memory.vms / 1024 ** 2,
                    "system_available_MiB": system_memory.available / 1024 ** 2,
                    "comfy_cpu_percent": self.process.cpu_percent(None),
                    "system_cpu_percent": psutil.cpu_percent(None),
                    "production_busy": int(any(self.production_counts.values()))}
                if hasattr(process_memory, "private"):
                    values["comfy_private_MiB"] = process_memory.private / 1024 ** 2
                try:
                    values["gpu_power_W"] = pynvml.nvmlDeviceGetPowerUsage(self.gpu) / 1000
                except pynvml.NVMLError:
                    pass
                metrics = self.active["nodes"].setdefault(self.current_node, {}).setdefault("samples", {})
                for key, value in values.items():
                    point = metrics.setdefault(key, {"min": value, "max": value, "sum": 0, "count": 0})
                    point["min"], point["max"] = min(point["min"], value), max(point["max"], value)
                    point["sum"] += value
                    point["count"] += 1
            await asyncio.sleep(0.5)

    async def benchmark(self):
        await self.wait_production_idle()
        self.state["system_stats"] = await self.get("/system_stats")
        self.state["production_guard"].update({"status": "armed", "armed_at": time.time()})
        self.save()
        watchdog = asyncio.create_task(self.production_watchdog())
        try:
            for index, case in enumerate(self.cases):
                self.check_abort()
                clear_cache = case.get("clear_cache", index == 0)
                if clear_cache:
                    await self.clear_idle_cache()
                self.check_abort()
                label = case.get("label") or case["name"]
                run = await self.run_graph(label, case["graph"], case["uncached"])
                run["case_name"] = case.get("name", label)
                run["cache_cleared_before_case"] = clear_cache
                run["engine_execution_seconds"] = round(run["execution_end"] - run["execution_start"], 6)
                run["memory_node_ui"] = {key: run.get("outputs", {}).get(key, {}) for key, value in case["graph"].items() if value["class_type"] == "H3MemoryOptimization"}
                self.save()
            self.check_abort()
            self.state["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self.state["production_guard"]["status"] = "completed_without_production_interference"
            self.save()
            print("ALL H3 NODE BENCHMARK CASES COMPLETE", flush=True)
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)


async def self_test():
    graph = {"1": {"class_type": "SamplerCustomAdvanced", "inputs": {}}}
    assert validate_manifest({"cases": [{"name": "fl", "graph": graph, "uncached": ["1"]}]})
    try:
        validate_manifest({"cases": [{"name": "fl", "graph": graph, "uncached": []}]})
    except ValueError:
        pass
    else:
        raise AssertionError("Cached sampler configuration was accepted")
    try:
        ensure_distinct_servers("http://localhost:8188", "http://127.0.0.1:8188")
    except ValueError:
        pass
    else:
        raise AssertionError("Production/test URL alias was accepted")
    runner = object.__new__(H3NodesRunner)
    runner.production_abort = asyncio.Event()
    runner.stop = False
    runner.production_counts = {"running": 0, "pending": 0}
    runner.state = {"production_guard": {}}
    runner.save = lambda: None
    async def busy():
        return {"running": 0, "pending": 1}
    runner.production_queue_counts = busy
    await runner.production_watchdog()
    assert runner.production_abort.is_set()
    runner.last_submitted_prompt_id = "our-test-id"
    runner.submission_finished = asyncio.Event()
    runner.submission_finished.set()
    queues = [{"queue_running": [], "queue_pending": [[0, "our-test-id"], [1, "someone-else"]]},
              {"queue_running": [], "queue_pending": [[1, "someone-else"]]}]
    calls = []
    async def get(path):
        assert path == "/queue"
        return queues.pop(0)
    async def post(path, payload):
        calls.append((path, payload))
    runner.get, runner.post = get, post
    await runner.interrupt_own_test_work()
    assert calls == [("/queue", {"delete": ["our-test-id"]}),
                     ("/interrupt", {"prompt_id": "our-test-id"})]
    print("SELF-TEST PASSED: manifest validation, separate-port guard, production busy detection, targeted test-only interruption")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--comfy-pid", type=int)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--production-url")
    parser.add_argument("--studio-url", help="Optional Studio URL; also guards local voice/preparation/waiting jobs")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        asyncio.run(self_test())
        return
    if any(getattr(args, name) is None for name in ("url", "comfy_pid", "directory", "input_manifest", "production_url")):
        parser.error("--url, --comfy-pid, --directory, --input-manifest and --production-url are required")
    asyncio.run(H3NodesRunner(args).main())


if __name__ == "__main__":
    main()
