import asyncio
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import setup_scriptwriting as setup


class Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def iter_chunked(self, _):
        yield self.body

    def __aiter__(self):
        async def lines():
            for line in self.body.splitlines(keepends=True):
                yield line
        return lines()

    async def json(self):
        return self.body if isinstance(self.body, dict) else json.loads(self.body)


class Session:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, *args, **kwargs):
        return self.response


class RangeSession:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def get(self, _, headers):
        first, last = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        self.requests.append((first, last))
        response = Response(self.body[first:last + 1])
        response.status = 206
        response.headers = {"Content-Range": f"bytes {first}-{last}/{len(self.body)}"}
        return response


class ModelSession(Session):
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        route = url.removeprefix(setup.BASE_URL)
        self.requests.append((method, route, kwargs))
        expected_method, expected_route, body = self.responses.pop(0)
        if (method, route) != (expected_method, expected_route):
            raise AssertionError(f"Unexpected request {(method, route)}")
        return Response(body)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def model_entry(name, *, quant="Q5_K_M", digest="manifest-sha256"):
    return {"name": name, "digest": digest, "details": {"quantization_level": quant}}


def hermes_responses(*, source=None, blob=None, destination=None):
    source = source or model_entry(setup.HERMES_SOURCE)
    destination = destination or model_entry(setup.DEFAULT_MODEL, digest="custom-template-manifest")
    blob = blob or "sha256-" + setup.HERMES_SHA256
    return [
        ("POST", "/api/pull", b'{"status":"success"}\n'),
        ("GET", "/api/tags", {"models": [source]}),
        ("POST", "/api/show", {"modelfile": 'FROM "C:\\model storage\\blobs\\' + blob + '"\nPARAMETER temperature 0.8'}),
        ("POST", "/api/create", {"status": "success"}),
        ("GET", "/api/tags", {"models": [source, destination]}),
        ("POST", "/api/show", {"modelfile": "FROM /models/blobs/" + blob + '\nTEMPLATE """' + setup.HERMES_CHAT_TEMPLATE + '"""\n',
                                "template": setup.HERMES_CHAT_TEMPLATE}),
    ]


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_download_preserves_prefix_and_resumes_partial_ranges(self):
        body = bytes(range(128))
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "download.zip.part"
            archive.write_bytes(body[:16])
            archive.with_suffix(".range0.part").write_bytes(body[16:21])
            session = RangeSession(body)
            with patch.multiple(setup, ARCHIVE_SIZE=len(body), ARCHIVE_SHA256=hashlib.sha256(body).hexdigest()):
                await setup.download_parallel(session, archive)
            self.assertEqual(archive.read_bytes(), body)
            self.assertIn((21, 29), session.requests)
            self.assertEqual(len(session.requests), 8)
            self.assertEqual(list(Path(directory).iterdir()), [archive])

    async def test_parallel_integrity_failure_does_not_replace_original_prefix(self):
        body = bytes(range(128))
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "download.zip.part"
            archive.write_bytes(body[:16])
            with patch.multiple(setup, ARCHIVE_SIZE=len(body), ARCHIVE_SHA256="0" * 64):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    await setup.download_parallel(RangeSession(body), archive)
            self.assertEqual(archive.read_bytes(), body[:16])
            self.assertEqual(len(list(Path(directory).glob("*.range*.part"))), 8)

    async def test_verified_portable_archive_is_promoted_only_after_validation(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("ollama.exe", b"test executable")
            bundle.writestr("lib/ollama/test.dll", b"test library")
        body = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "version"
            with patch.multiple(setup, ROOT=root, RUNTIME_DIR=runtime,
                                ARCHIVE_SIZE=len(body), ARCHIVE_SHA256=hashlib.sha256(body).hexdigest()), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=Session(Response(body))):
                await setup.install_runtime()
                self.assertEqual((runtime / "ollama.exe").read_bytes(), b"test executable")
                self.assertFalse(list(root.glob("*.part")))

    async def test_tampered_download_is_preserved_and_never_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "version"
            with patch.multiple(setup, ROOT=root, RUNTIME_DIR=runtime, ARCHIVE_SIZE=3,
                                ARCHIVE_SHA256="0" * 64), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=Session(Response(b"bad"))):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    await setup.install_runtime()
            self.assertFalse(runtime.exists())
            self.assertEqual(next(root.glob("*.part")).read_bytes(), b"bad")

    async def test_archive_cannot_escape_runtime_directory(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("../escape.txt", b"outside")
            bundle.writestr("ollama.exe", b"test")
        body = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(setup, ROOT=root, RUNTIME_DIR=root / "runtime" / "version",
                                ARCHIVE_SIZE=len(body), ARCHIVE_SHA256=hashlib.sha256(body).hexdigest()), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=Session(Response(body))):
                with self.assertRaisesRegex(RuntimeError, "路徑"):
                    await setup.install_runtime()
            self.assertFalse((root / "runtime" / "escape.txt").exists())

    async def test_model_installer_refuses_cloud_or_arbitrary_model_before_network(self):
        with patch.object(setup.aiohttp, "ClientSession") as session:
            with self.assertRaisesRegex(RuntimeError, "本地模型"):
                await setup.pull_model("qwen3.8:cloud")
            session.assert_not_called()

    async def test_hermes_uses_verified_source_then_creates_chatml_alias_without_inference(self):
        session = ModelSession(hermes_responses())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=14 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session) as factory:
                await setup.pull_model(setup.DEFAULT_MODEL)
            installed = json.loads((root / "installed-model.json").read_text(encoding="utf-8"))
            self.assertEqual(installed["name"], setup.DEFAULT_MODEL)
        self.assertEqual(session.requests[0][2]["json"], {"model": setup.HERMES_SOURCE, "stream": True})
        created = session.requests[3][2]["json"]
        self.assertEqual(created, {"model": setup.DEFAULT_MODEL, "from": setup.HERMES_SOURCE,
                                  "template": setup.HERMES_CHAT_TEMPLATE, "parameters": setup.HERMES_PARAMETERS,
                                  "stream": False})
        self.assertNotIn("<think>", created["template"])
        self.assertTrue(created["template"].endswith("<|im_start|>assistant\n"))
        self.assertEqual(session.responses, [])
        self.assertFalse(factory.call_args.kwargs["trust_env"])
        self.assertTrue(all(not request[2]["allow_redirects"] for request in session.requests))
        self.assertFalse(any(route in {"/api/chat", "/api/generate", "/api/delete", "/api/copy"} for _, route, _ in session.requests))

    async def test_hermes_wrong_quantization_never_changes_alias_or_installed_record(self):
        responses = hermes_responses(source=model_entry(setup.HERMES_SOURCE, quant="Q4_K_M"))
        session = ModelSession(responses)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "installed-model.json"
            record.write_text("previous model", encoding="utf-8")
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "Q5_K_M"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
            self.assertEqual(record.read_text(encoding="utf-8"), "previous model")
        self.assertFalse(any(route == "/api/create" for _, route, _ in session.requests))

    async def test_hermes_wrong_or_missing_blob_digest_prevents_alias_creation(self):
        for modelfile in ['FROM "C:\\blobs\\sha256-' + "0" * 64 + '"', "FROM unknown-model"]:
            with self.subTest(modelfile=modelfile), tempfile.TemporaryDirectory() as directory:
                responses = hermes_responses()
                responses[2] = ("POST", "/api/show", {"modelfile": modelfile})
                session = ModelSession(responses)
                with patch.object(setup, "ROOT", Path(directory)), \
                     patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                     patch.object(setup.aiohttp, "ClientSession", return_value=session):
                    with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                        await setup.pull_model(setup.DEFAULT_MODEL)
                self.assertFalse(any(route == "/api/create" for _, route, _ in session.requests))
                self.assertFalse((Path(directory) / "installed-model.json").exists())

    async def test_hermes_show_accepts_generated_windows_and_posix_blob_paths(self):
        blob = "sha256-" + setup.HERMES_SHA256
        for path in ["E:\\MINIMAX H3\\blobs\\" + blob, "/models/blobs/" + blob, "'C:\\model storage\\" + blob + "'"]:
            with self.subTest(path=path):
                session = ModelSession([("POST", "/api/show", {"modelfile": "# Generated file\nFROM " + path + "\nPARAMETER temperature 0.8"})])
                await setup.verify_hermes(session, model_entry(setup.HERMES_SOURCE))
                self.assertEqual(session.responses, [])

    async def test_hermes_remote_source_is_rejected_before_alias_creation(self):
        source = {**model_entry(setup.HERMES_SOURCE), "remote_host": "https://example.invalid"}
        session = ModelSession(hermes_responses(source=source))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup, "ROOT", Path(directory)), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "本地模型"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
        self.assertFalse(any(route == "/api/create" for _, route, _ in session.requests))

    async def test_hermes_download_failure_never_creates_alias(self):
        session = ModelSession([("POST", "/api/pull", b'{"error":"download failed"}\n')])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup, "ROOT", Path(directory)), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "download failed"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
        self.assertEqual(len(session.requests), 1)

    async def test_hermes_alias_template_must_match_plain_chatml(self):
        responses = hermes_responses()
        responses[5][2]["template"] = "<|im_start|>assistant\n<think>"
        session = ModelSession(responses)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "installed-model.json"
            record.write_text("previous model", encoding="utf-8")
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "ChatML 模板"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
            self.assertEqual(record.read_text(encoding="utf-8"), "previous model")

    async def test_hermes_alias_must_still_use_pinned_blob_after_custom_template(self):
        responses = hermes_responses()
        responses[5][2]["modelfile"] = "FROM /models/blobs/sha256-" + "0" * 64
        session = ModelSession(responses)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "installed-model.json"
            record.write_text("previous model", encoding="utf-8")
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
            self.assertEqual(record.read_text(encoding="utf-8"), "previous model")

    async def test_hermes_failed_alias_create_never_marks_model_installed(self):
        responses = hermes_responses()
        responses[3] = ("POST", "/api/create", {"error": "invalid template"})
        session = ModelSession(responses)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup, "ROOT", Path(directory)), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "invalid template"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
            self.assertFalse((Path(directory) / "installed-model.json").exists())
        self.assertEqual(len(session.requests), 4)

    async def test_hermes_selected_embedded_template_requires_exact_reviewed_hash(self):
        # Small deterministic fixture exercises the exact-hash branch without
        # duplicating the full upstream Jinja. Real model validation pins the
        # immutable GGUF and the independently reviewed official template hash.
        approved = "{% for message in messages %}{{ message.content }}{% endfor %}"
        responses = hermes_responses()
        responses[5][2]["template"] = "\n" + approved + "\n"
        session = ModelSession(responses)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup, "HERMES_JINJA_SHA256", hashlib.sha256(approved.encode()).hexdigest()), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=40 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                await setup.pull_model(setup.DEFAULT_MODEL)
            self.assertEqual(json.loads((root / "installed-model.json").read_text())["name"], setup.DEFAULT_MODEL)

    async def test_hermes_arbitrary_or_malformed_selected_jinja_is_rejected(self):
        for selected in ["{% if thinking %}<think>{% endif %}", "{% malformed", ""]:
            with self.subTest(selected=selected):
                shown = hermes_responses()[5][2]
                shown["template"] = selected
                with self.assertRaisesRegex(RuntimeError, "實際使用.*ChatML 模板"):
                    setup.verify_hermes_template(shown)

    async def test_hermes_saved_go_template_must_match_even_when_selected_template_is_allowed(self):
        for stored in ["", '\nTEMPLATE """<|im_start|>assistant\n<think>"""\n']:
            with self.subTest(stored=stored):
                shown = {"modelfile": "FROM /models/blobs/sha256-model" + stored,
                         "template": setup.HERMES_CHAT_TEMPLATE}
                with self.assertRaisesRegex(RuntimeError, "本地別名.*ChatML 模板"):
                    setup.verify_hermes_template(shown)

    async def test_legacy_qwen_pull_keeps_native_model_name_and_disk_requirement(self):
        model = "qwen3.8:27b"
        session = ModelSession([
            ("POST", "/api/pull", b'{"status":"success"}\n'),
            ("GET", "/api/tags", {"models": [model_entry(model, quant="Q4_K_M")]}),
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=22 * 1024**3)), \
                 patch.object(setup.aiohttp, "ClientSession", return_value=session):
                await setup.pull_model(model)
            self.assertEqual(json.loads((root / "installed-model.json").read_text())["name"], model)
        self.assertEqual(session.requests[0][2]["json"], {"model": model, "stream": True})
        self.assertEqual(len(session.requests), 2)
        self.assertIn("qwen3.5:9b", setup.MODELS)
        self.assertIn("gemma4:12b", setup.MODELS)
        self.assertEqual(setup.MODEL_DISK_GIB[model], 22)

    async def test_hermes_requires_fourteen_gib_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup, "ROOT", Path(directory)), \
                 patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=14 * 1024**3 - 1)), \
                 patch.object(setup.aiohttp, "ClientSession") as session:
                with self.assertRaisesRegex(RuntimeError, "14 GiB"):
                    await setup.pull_model(setup.DEFAULT_MODEL)
                session.assert_not_called()


class ServerTests(unittest.TestCase):
    def test_existing_service_is_reused_without_starting_or_loading_model(self):
        with patch.object(setup, "probe", return_value={"version": "test"}), \
             patch.object(setup.subprocess, "Popen") as spawn:
            self.assertTrue(setup.start_managed_server()["running"])
            spawn.assert_not_called()

    def test_managed_service_disables_cloud_and_keeps_weights_local(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / "ollama.exe"
            exe.touch()
            process = SimpleNamespace(pid=123, poll=lambda: None)
            with patch.object(setup, "ROOT", root), \
                 patch.object(setup, "executable", return_value=exe), \
                 patch.object(setup, "probe", side_effect=[None, {"version": "test"}]), \
                 patch.object(setup.subprocess, "Popen", return_value=process) as spawn:
                result = setup.start_managed_server()
            self.assertTrue(result["managed"])
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11434")
            self.assertEqual(env["OLLAMA_NO_CLOUD"], "true")
            self.assertEqual(env["OLLAMA_MODELS"], str(root / "models"))
            self.assertEqual(spawn.call_args.args[0], [str(exe), "serve"])


if __name__ == "__main__":
    unittest.main()
