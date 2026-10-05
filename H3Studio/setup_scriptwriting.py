"""Pinned, portable local Ollama installation; model pulls never run inference."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
import zipfile

import aiohttp

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR / "data" / "scriptwriting"
VERSION = "0.35.0"
ARCHIVE_SIZE = 1461196158
ARCHIVE_SHA256 = "d6f7d3dd4f5d013553a78c1e78b2521fcf41d43dd2863e4596cdc046fe6036db"
ARCHIVE_URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip"
RUNTIME_DIR = ROOT / "runtime" / f"ollama-v{VERSION}"
DEFAULT_MODEL = "h3-script-hermes4:14b-q5_k_m"
HERMES_SOURCE = "hf.co/bartowski/NousResearch_Hermes-4-14B-GGUF:Q5_K_M"
HERMES_SHA256 = "aebc57a6319d0486a5adb0c6891d51f383ea5c29281d764a87085b474a4af06b"
HERMES_SIZE = 10514570176
# Ollama collates every system message into .System and also keeps those turns
# in .Messages. Render the combined system once, then the remaining ChatML
# turns. Hermes non-reasoning mode must not inherit Qwen's open <think> prefix.
HERMES_CHAT_TEMPLATE = '''{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ range .Messages }}{{ if or (ne .Role "system") (not $.System) }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}{{ end }}<|im_start|>assistant
'''
# The pinned GGUF also embeds Hermes' official Jinja template. Ollama 0.35 may
# select it instead of the saved Go template; both use plain non-thinking
# ChatML. Accept only this exact reviewed template, never arbitrary Jinja.
HERMES_JINJA_SHA256 = "fa3d3eab6e9e7ffeef38e38a737d2a665e0dfd2f73a4e375faaf37febe516b3e"
HERMES_PARAMETERS = {"temperature": .6, "top_p": .95, "top_k": 20,
                     "repeat_penalty": 1, "stop": ["<|im_start|>", "<|im_end|>"]}
MODELS = {DEFAULT_MODEL, "qwen3.8:27b", "qwen3.5:9b", "gemma4:12b"}
MODEL_DISK_GIB = {DEFAULT_MODEL: 14, "qwen3.8:27b": 22, "qwen3.5:9b": 10, "gemma4:12b": 10}
BASE_URL = "http://127.0.0.1:11434"


def executable() -> Path:
    return RUNTIME_DIR / "ollama.exe"


def probe() -> dict | None:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE_URL + "/api/version", timeout=1) as response:
            data = json.load(response)
            return data if isinstance(data.get("version"), str) else None
    except (OSError, ValueError):
        return None


def start_managed_server() -> dict:
    """Do not replace an existing service; start our loopback service if installed."""
    if current := probe():
        return {"running": True, "managed": False, **current}
    if not executable().is_file():
        return {"running": False, "error": "請先執行 setup_scriptwriting.bat 安裝本地劇本模型。"}
    ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": str(ROOT / "models"),
        "OLLAMA_NO_CLOUD": "true",
        "OLLAMA_KEEP_ALIVE": "0",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_CONTEXT_LENGTH": "8192",
    })
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (ROOT / "ollama-server.log").open("ab") as log:
        process = subprocess.Popen(
            [str(executable()), "serve"], cwd=RUNTIME_DIR, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags,
        )
    for _ in range(80):
        if current := probe():
            return {"running": True, "managed": True, "pid": process.pid, **current}
        if process.poll() is not None:
            raise RuntimeError(f"Ollama 啟動失敗，請查看 {ROOT / 'ollama-server.log'}")
        time.sleep(.25)
    raise RuntimeError("Ollama 未在預期時間啟動，請查看 ollama-server.log。")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def download_parallel(session: aiohttp.ClientSession, archive: Path) -> None:
    """Resume a sequential prefix and eight verified HTTP ranges without rewriting it."""
    base_size = archive.stat().st_size if archive.exists() else 0
    if base_size == ARCHIVE_SIZE:
        return
    if base_size > ARCHIVE_SIZE:
        raise RuntimeError("下載暫存檔大小異常，請將 .zip.part 移走後重試。")
    plan_file = archive.with_suffix(".ranges.json")
    if plan_file.exists():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        if plan.get("base_size") != base_size or plan.get("sha256") != ARCHIVE_SHA256:
            raise RuntimeError("下載續傳資訊與暫存檔不符，請保留檔案並檢查。")
    else:
        plan = {"base_size": base_size, "sha256": ARCHIVE_SHA256}
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
    span = (ARCHIVE_SIZE - base_size + 7) // 8
    ranges = [(start, min(start + span, ARCHIVE_SIZE) - 1)
              for start in range(base_size, ARCHIVE_SIZE, span)]
    parts = [archive.with_suffix(f".range{index}.part") for index in range(len(ranges))]
    sizes = [part.stat().st_size if part.exists() else 0 for part in parts]
    printed = (base_size + sum(sizes)) // (100 * 1024**2)

    async def fetch(index: int) -> None:
        nonlocal printed
        first, last = ranges[index]
        part, expected = parts[index], last - first + 1
        for attempt in range(3):
            offset = part.stat().st_size if part.exists() else 0
            if offset == expected:
                return
            if offset > expected:
                raise RuntimeError("分塊下載暫存大小異常。")
            try:
                headers = {"Range": f"bytes={first + offset}-{last}"}
                async with session.get(ARCHIVE_URL, headers=headers) as response:
                    response.raise_for_status()
                    required = f"bytes {first + offset}-{last}/{ARCHIVE_SIZE}"
                    if response.status != 206 or response.headers.get("Content-Range") != required:
                        raise RuntimeError("官方下載来源未提供正確的 HTTP 分塊續傳回應。")
                    with part.open("ab") as target:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            offset += len(chunk)
                            if offset > expected:
                                raise RuntimeError("分塊下載超過預期大小。")
                            target.write(chunk)
                            sizes[index] = offset
                            total = base_size + sum(sizes)
                            step = total // (100 * 1024**2)
                            if step > printed:
                                print(f"Ollama runtime: {total / ARCHIVE_SIZE:.0%}", flush=True)
                                printed = step
                if offset != expected:
                    raise aiohttp.ClientPayloadError("分塊下載未完成。")
                return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2)

    async with asyncio.TaskGroup() as group:
        for index in range(len(ranges)):
            group.create_task(fetch(index))

    def assemble() -> None:
        combined = archive.with_suffix(".assembled")
        with combined.open("wb") as target:
            for part in ([archive] if base_size else []) + parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
        if combined.stat().st_size != ARCHIVE_SIZE or digest_file(combined) != ARCHIVE_SHA256:
            raise RuntimeError("分塊下載組合後 SHA-256 驗證失敗；暫存檔保留供檢查。")
        combined.replace(archive)
        for part in parts:
            part.unlink()
        plan_file.unlink()
    await asyncio.to_thread(assemble)


async def install_runtime() -> None:
    if executable().is_file():
        return
    if os.name != "nt":
        raise RuntimeError("此可攜安裝器適用 Windows x64；其他系統請自行安裝 Ollama。")
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = ROOT / f"ollama-windows-amd64-v{VERSION}.zip.part"
    free = shutil.disk_usage(ROOT).free
    if free < 5 * 1024**3:
        raise RuntimeError("Ollama 執行環境至少需要 5 GiB 可用空間，另需保留模型空間。")
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if ARCHIVE_SIZE > 128 * 1024**2:
            await download_parallel(session, archive)
        for attempt in range(0 if ARCHIVE_SIZE > 128 * 1024**2 else 3):
            start = archive.stat().st_size if archive.exists() else 0
            if start == ARCHIVE_SIZE:
                break
            if start > ARCHIVE_SIZE:
                raise RuntimeError("下載暫存檔大小異常，請將 .zip.part 移走後重試。")
            try:
                headers = {"Range": f"bytes={start}-"} if start else {}
                async with session.get(ARCHIVE_URL, headers=headers) as response:
                    response.raise_for_status()
                    append = bool(start and response.status == 206)
                    if append and not response.headers.get("Content-Range", "").startswith(f"bytes {start}-"):
                        raise RuntimeError("下載來源回傳錯誤的續傳位置。")
                    downloaded = start if append else 0
                    printed = downloaded // (100 * 1024**2)
                    with archive.open("ab" if append else "wb") as target:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            downloaded += len(chunk)
                            if downloaded > ARCHIVE_SIZE:
                                raise RuntimeError("下載檔案超出官方固定大小。")
                            target.write(chunk)
                            step = downloaded // (100 * 1024**2)
                            if step != printed:
                                print(f"Ollama runtime: {downloaded / ARCHIVE_SIZE:.0%}", flush=True)
                                printed = step
                if downloaded != ARCHIVE_SIZE:
                    raise aiohttp.ClientPayloadError("下載未完成。")
                break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
    if await asyncio.to_thread(digest_file, archive) != ARCHIVE_SHA256:
        raise RuntimeError("Ollama 官方執行檔 SHA-256 驗證失敗，保留下載暫存供檢查。")
    staging = ROOT / "runtime" / ("staging-" + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            resolved = (staging / item.filename).resolve()
            if not resolved.is_relative_to(staging.resolve()):
                raise RuntimeError("Ollama 安裝包包含錯誤的路徑。")
        await asyncio.to_thread(bundle.extractall, staging)
    if not (staging / "ollama.exe").is_file():
        raise RuntimeError("官方安裝包缺少 ollama.exe。")
    staging.rename(RUNTIME_DIR)
    # The verified archive is expendable. Never recursively remove runtime/models.
    archive.unlink()
    print(f"Verified Ollama {VERSION}: {executable()}", flush=True)


async def local_model(session: aiohttp.ClientSession, model: str) -> dict:
    async with session.get(BASE_URL + "/api/tags", allow_redirects=False) as response:
        response.raise_for_status()
        data = await response.json()
    installed = next((entry for entry in data.get("models", []) if entry.get("name") == model), None)
    if not installed or installed.get("remote_host") or installed.get("remote_model"):
        raise RuntimeError("下載後未找到本地模型權重。")
    return installed


async def verify_hermes(session: aiohttp.ClientSession, installed: dict) -> dict:
    if installed.get("details", {}).get("quantization_level") != "Q5_K_M":
        raise RuntimeError("Hermes 模型不是已核對的 Q5_K_M 量化版本。")
    async with session.post(BASE_URL + "/api/show", json={"model": installed["name"]}, allow_redirects=False) as response:
        response.raise_for_status()
        data = await response.json()
    if data.get("remote_host") or data.get("remote_model"):
        raise RuntimeError("Hermes 必須使用本地模型權重。")
    # Ollama validates the downloaded blob's contents against its SHA-256. Check
    # the generated Modelfile's actual blob, not the manifest or display name.
    match = re.search(r"(?mi)^FROM[ \t]+([^\r\n]+)[ \t]*$", str(data.get("modelfile", "")))
    blob = match.group(1).strip() if match else ""
    if len(blob) >= 2 and blob[0] == blob[-1] and blob[0] in {'"', "'"}:
        blob = blob[1:-1]
    basename = blob.replace("\\", "/").rsplit("/", 1)[-1]
    if basename != "sha256-" + HERMES_SHA256:
        raise RuntimeError("Hermes GGUF 的 SHA-256 與已核對版本不符；未標記安裝完成。")
    return data


def verify_hermes_template(shown: dict) -> None:
    # /api/show.modelfile reports the saved Go template; .template reports the
    # selected renderer, which can prefer the GGUF's richer embedded template.
    match = re.search(r'(?ms)^TEMPLATE[ \t]+"""(.*?)"""[ \t]*(?:\r?\n|$)', str(shown.get("modelfile", "")))
    if not match or match.group(1).strip() != HERMES_CHAT_TEMPLATE.strip():
        raise RuntimeError("Hermes 本地別名的 ChatML 模板驗證失敗。")
    selected = str(shown.get("template", "")).strip()
    if (selected != HERMES_CHAT_TEMPLATE.strip()
            and hashlib.sha256(selected.encode("utf-8")).hexdigest() != HERMES_JINJA_SHA256):
        raise RuntimeError("Hermes 實際使用的 ChatML 模板驗證失敗。")


async def pull_model(model: str) -> None:
    if model not in MODELS:
        raise RuntimeError("此安裝器只接受已核對的本地模型。")
    # Estimate disk requirement before downloading; Ollama verifies blob digests.
    needed = MODEL_DISK_GIB[model] * 1024**3
    if shutil.disk_usage(ROOT).free < needed:
        raise RuntimeError(f"安裝 {model} 至少需保留 {needed / 1024**3:.0f} GiB 空間。")
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=600)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        source_model = HERMES_SOURCE if model == DEFAULT_MODEL else model
        async with session.post(BASE_URL + "/api/pull", json={"model": source_model, "stream": True}, allow_redirects=False) as response:
            response.raise_for_status()
            previous = None
            success = False
            async for line in response.content:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
                status = str(data.get("status", ""))
                total, completed = data.get("total", 0), data.get("completed", 0)
                progress = int(completed / total * 100) if total else None
                mark = (status, progress // 5 if progress is not None else None)
                if mark != previous:
                    print(f"{model}: {status}" + (f" {progress}%" if progress is not None else ""), flush=True)
                    previous = mark
                success = status == "success"
            if not success:
                raise RuntimeError("模型下載串流提前結束，可重新執行安裝器續傳。")
        installed = await local_model(session, source_model)
        if model == DEFAULT_MODEL:
            await verify_hermes(session, installed)
            # Keep the original source intact. The local alias shares its GGUF
            # weights, but explicitly selects Hermes' plain ChatML mode.
            create = {"model": model, "from": source_model, "template": HERMES_CHAT_TEMPLATE,
                      "parameters": HERMES_PARAMETERS, "stream": False}
            async with session.post(BASE_URL + "/api/create", json=create, allow_redirects=False) as response:
                response.raise_for_status()
                created = await response.json()
            if created.get("error") or created.get("status") != "success":
                raise RuntimeError(str(created.get("error") or "Hermes 本地別名建立失敗。"))
            installed = await local_model(session, model)
            shown = await verify_hermes(session, installed)
            verify_hermes_template(shown)
        (ROOT / "installed-model.json").write_text(json.dumps(installed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Installed local model {model}; no inference or GPU allocation performed.", flush=True)


async def run(args) -> None:
    if not args.start_only:
        await install_runtime()
    result = await asyncio.to_thread(start_managed_server)
    if not result.get("running"):
        raise RuntimeError(result.get("error"))
    print(f"Local Ollama {result['version']}: {BASE_URL}", flush=True)
    if not args.start_only:
        await pull_model(args.model)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install portable Ollama and a local screenplay model.")
    parser.add_argument("--model", choices=sorted(MODELS), default=DEFAULT_MODEL)
    parser.add_argument("--start-only", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
        return 0
    except (OSError, RuntimeError, ValueError, aiohttp.ClientError) as error:
        print(f"[ERROR] {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
