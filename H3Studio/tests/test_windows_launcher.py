import os
from io import BytesIO
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "nt", "Windows batch launcher test")
class WindowsLauncherTests(unittest.TestCase):
    def run_discovery_with_exit_codes(self, py312, py311, path_python):
        # Keep the real batch decisions; emulate only external process results.
        # PyManager's missing-runtime code 0xA0000006 is negative to cmd.exe.
        source = (Path(__file__).resolve().parents[2] / "setup_h3_studio.bat").read_text(encoding="utf-8")
        discovery = source.split("\n:find_python\n", 1)[1].split("\n:no_python\n", 1)[0]
        replacements = [
            (r"(?m)^\s*where (?:py|python) >nul 2>&1$", 0),
            (r"(?m)^\s*py -3\.12 -c .+$", py312),
            (r"(?m)^\s*py -3\.11 -c .+$", py311),
            (r"(?m)^\s*python -c .+$", path_python),
        ]
        for pattern, code in replacements:
            discovery, count = re.subn(pattern, f"\n  cmd /d /c exit /b {code}", discovery)
            self.assertGreater(count, 0, pattern)
        with tempfile.TemporaryDirectory(prefix="h3 runtime discovery ") as folder:
            probe = Path(folder) / "probe.bat"
            probe.write_text(
                '@echo off\nsetlocal EnableExtensions\ncall :find_python\n'
                'if defined SYSTEM_PYTHON (echo %SYSTEM_PYTHON%) else (echo NONE)\n'
                'exit /b 0\n\n:find_python\n' + discovery,
                encoding="utf-8", newline="\r\n",
            )
            result = subprocess.run(
                [str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"), "/d", "/c", "probe.bat"],
                cwd=folder, capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.strip()

    def test_missing_manager_runtime_falls_back_to_python_311(self):
        self.assertEqual(self.run_discovery_with_exit_codes(-1610612730, 0, 1), "py -3.11")

    def test_missing_manager_runtimes_fall_back_to_path_python(self):
        self.assertEqual(self.run_discovery_with_exit_codes(-1610612730, -1610612730, 0), "python")

    def test_no_runtime_is_selected_when_all_probes_fail(self):
        self.assertEqual(self.run_discovery_with_exit_codes(-1610612730, -1610612730, -1610612730), "NONE")

    def test_successful_python_312_remains_preferred(self):
        self.assertEqual(self.run_discovery_with_exit_codes(0, 0, 0), "py -3.12")

    def test_positive_failure_also_falls_back(self):
        self.assertEqual(self.run_discovery_with_exit_codes(1, 0, 0), "py -3.11")

    @unittest.skipUnless((3, 11) <= sys.version_info[:2] <= (3, 13), "Requires a supported Python version")
    def test_finds_path_python_without_py_launcher(self):
        # Execute the real discovery subroutine without reaching installation.
        setup_path = Path(__file__).resolve().parents[2] / "setup_h3_studio.bat"
        source = setup_path.read_text(encoding="utf-8")
        discovery = source.split("\n:find_python\n", 1)[1].split("\n:no_python\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="h3 launcher test ") as folder:
            root = Path(folder)
            runtime = root / "python runtime"
            venv.EnvBuilder(with_pip=False).create(runtime)
            system32 = Path(os.environ["SystemRoot"]) / "System32"
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(runtime / "Scripts"), str(system32)))
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            probe = root / "probe.bat"
            probe.write_text(
                "@echo off\n"
                "setlocal EnableExtensions\n"
                "where py >nul 2>&1\n"
                "if not errorlevel 1 exit /b 10\n"
                "call :find_python\n"
                "if not defined SYSTEM_PYTHON exit /b 11\n"
                'if not "%SYSTEM_PYTHON%"=="python" exit /b 12\n'
                '%SYSTEM_PYTHON% -c "import sys; print(sys.executable)"\n'
                "exit /b %errorlevel%\n"
                "\n:find_python\n" + discovery,
                encoding="utf-8",
                newline="\r\n",
            )
            result = subprocess.run(
                [str(system32 / "cmd.exe"), "/d", "/c", "probe.bat"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Path(result.stdout.strip()), runtime / "Scripts" / "python.exe")


class BatchSourceFormatTests(unittest.TestCase):
    def test_all_shipped_root_batches_are_complete_crlf_without_bom_or_control_eof(self):
        scripts = sorted(REPOSITORY_ROOT.glob("*.bat"))
        self.assertGreaterEqual(len(scripts), 11)
        for script in scripts:
            with self.subTest(script=script.name):
                raw = script.read_bytes()
                self.assertTrue(raw.endswith(b"\r\n"), "The last batch line must end with CRLF")
                self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), "LF-only lines can break CALL label lookup")
                self.assertEqual(raw.count(b"\r"), raw.count(b"\r\n"))
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\x1a", raw)
                self.assertNotIn(b"\x00", raw)


@unittest.skipUnless(os.name == "nt", "Windows batch launcher test")
class WindowsStartLauncherTests(unittest.TestCase):
    """Execute shipped launch decisions with only isolated runtime/app fixtures.

    No fixture can launch Studio, connect to ComfyUI, install dependencies or
    touch an existing data directory. Source .bat bytes are copied unchanged.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3 batch launch with spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.studio = self.root / "H3Studio"
        self.studio.mkdir()
        venv.EnvBuilder(with_pip=False).create(self.studio / ".venv")
        for name in ("start_h3_studio.bat", "start_h3_studio_8789.bat"):
            shutil.copyfile(REPOSITORY_ROOT / name, self.root / name)
        # The real -c dependency probe imports harmless local modules. Missing
        # one exercises automatic repair without calling pip or a real setup.
        for name in ("aiohttp", "av", "numpy", "PIL", "huggingface_hub"):
            (self.root / f"{name}.py").write_text("", encoding="utf-8")
        self.marker = self.studio / "launch-args.json"
        self.write_app()
        self.write_setup()
        self.env = os.environ.copy()
        self.env.pop("PYTHONHOME", None)
        self.env.pop("PYTHONPATH", None)

    def write_app(self, exit_code=0):
        (self.studio / "app.py").write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path('launch-args.json').write_text(json.dumps({'args': sys.argv[1:], "
            "'cwd': str(Path.cwd()), 'python': sys.executable}), encoding='utf-8')\n"
            "print('LAUNCHER_FIXTURE_APP')\n"
            f"sys.exit({exit_code})\n", encoding="utf-8",
        )

    def write_setup(self, commands="", exit_code=0):
        content = ('@echo off\n>>"%~dp0setup-calls.txt" echo %*\n' + commands + f"\nexit /b {exit_code}\n")
        (self.root / "setup_h3_studio.bat").write_text(content, encoding="utf-8", newline="\r\n")

    def launch(self, command="start_h3_studio.bat --launcher-fixture"):
        executable = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
        # list2cmdline on the whole /c command would backslash-escape its inner
        # quotes. cmd.exe uses its own parser; pass a native command string with
        # /s removing only the outside pair, so the fixture exercises real quotes.
        result = subprocess.run(
            subprocess.list2cmdline([executable]) + f' /d /s /c "{command}"',
            cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, check=False,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        return result.returncode, output

    def setup_calls(self):
        path = self.root / "setup-calls.txt"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def assert_launched(self, code, output, arguments):
        self.assertEqual(code, 0, output)
        self.assertIn("LAUNCHER_FIXTURE_APP", output)
        self.assertNotIn("check_studio_python", output)
        metadata = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(metadata["args"], arguments)
        self.assertEqual(Path(metadata["cwd"]), self.studio)
        self.assertEqual(Path(metadata["python"]), self.studio / ".venv" / "Scripts" / "python.exe")

    def test_unmodified_launcher_calls_its_label_and_preserves_quoted_arguments(self):
        code, output = self.launch('start_h3_studio.bat --fixture-token "two words"')
        self.assert_launched(code, output, ["--auto-port", "--fixture-token", "two words"])
        self.assertEqual(self.setup_calls(), [])

    def test_raw_lf_copy_reproduces_missing_label_and_crlf_copy_runs_identical_contents(self):
        raw = (REPOSITORY_ROOT / "start_h3_studio.bat").read_bytes()
        path = self.root / "start_h3_studio.bat"
        line_feed = raw.replace(b"\r\n", b"\n")
        path.write_bytes(line_feed)
        code, output = self.launch()
        self.assertNotEqual(code, 0, output)
        self.assertIn("check_studio_python", output)
        self.assertFalse(self.marker.exists())
        path.write_bytes(line_feed.replace(b"\n", b"\r\n"))
        code, output = self.launch()
        self.assert_launched(code, output, ["--auto-port", "--launcher-fixture"])

    def test_editor_port_wrapper_reaches_real_launcher_and_forwards_arguments(self):
        code, output = self.launch('start_h3_studio_8789.bat --fixture-token "two words"')
        self.assert_launched(code, output, ["--auto-port", "--port", "8789", "--open-editor", "--fixture-token", "two words"])
        self.assertEqual(self.setup_calls(), [])

    def test_missing_local_runtime_uses_auto_setup_stub_and_rechecks_before_launching(self):
        runtime = self.studio / ".venv" / "Scripts" / "python.exe"
        runtime.rename(runtime.with_name("python.ready.exe"))
        self.write_setup('copy /y "%~dp0H3Studio\\.venv\\Scripts\\python.ready.exe" "%~dp0H3Studio\\.venv\\Scripts\\python.exe" >nul')
        code, output = self.launch()
        self.assert_launched(code, output, ["--auto-port", "--launcher-fixture"])
        self.assertEqual(self.setup_calls(), ["--auto"])

    def test_failed_probe_can_be_repaired_without_reaching_real_setup_or_pip(self):
        (self.root / "PIL.py").unlink()
        self.write_setup('type nul >"%~dp0PIL.py"')
        code, output = self.launch()
        self.assert_launched(code, output, ["--auto-port", "--launcher-fixture"])
        self.assertEqual(self.setup_calls(), ["--auto"])

    def test_failed_setup_stops_before_app_and_keeps_failure_status(self):
        (self.root / "PIL.py").unlink()
        self.write_setup(exit_code=27)
        code, output = self.launch()
        self.assertEqual(code, 1, output)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.setup_calls(), ["--auto"])

    def test_setup_success_without_a_valid_runtime_stops_after_second_probe(self):
        (self.root / "PIL.py").unlink()
        code, output = self.launch()
        self.assertEqual(code, 1, output)
        self.assertIn("environment could not be repaired", output)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.setup_calls(), ["--auto"])

    def test_app_failure_returns_its_original_exit_code_after_message(self):
        self.write_app(exit_code=23)
        code, output = self.launch()
        self.assertEqual(code, 23, output)
        self.assertIn("LAUNCHER_FIXTURE_APP", output)
        self.assertIn("could not start", output)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.setup_calls(), [])

    @unittest.skipUnless(shutil.which("git"), "Requires Git for isolated archive roundtrip")
    def test_git_blob_and_zip_roundtrip_preserve_crlf_and_archived_launcher_executes(self):
        # Use a fresh repository, not the user's checkout/index/config. Force
        # autocrlf on to prove .gitattributes overrides checkout normalization.
        repository = self.root / "isolated git archive"
        repository.mkdir()
        shutil.copyfile(REPOSITORY_ROOT / ".gitattributes", repository / ".gitattributes")
        scripts = sorted(REPOSITORY_ROOT.glob("*.bat"))
        for script in scripts:
            shutil.copyfile(script, repository / script.name)

        def git(*arguments):
            result = subprocess.run([shutil.which("git"), "-c", "core.autocrlf=true", "-c", "user.name=Launcher Fixture",
                                     "-c", "user.email=launcher-fixture@example.invalid", *arguments], cwd=repository,
                                    capture_output=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            return result.stdout

        git("init", "--quiet")
        git("add", "--", ".gitattributes", *(script.name for script in scripts))
        git("commit", "--quiet", "-m", "Isolated launcher fixture")
        self.assertEqual(git("check-attr", "text", "--", "start_h3_studio.bat").strip(), b"start_h3_studio.bat: text: unset")
        archive_bytes = git("archive", "--format=zip", "HEAD")
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            for script in scripts:
                with self.subTest(script=script.name):
                    raw = script.read_bytes()
                    self.assertEqual(git("show", f"HEAD:{script.name}"), raw, "Git blob itself must retain CRLF")
                    self.assertEqual(archive.read(script.name), raw)
            # Execute only the archived launchers over the safe fixture setup;
            # every other batch is inspected as bytes and never executed.
            for name in ("start_h3_studio.bat", "start_h3_studio_8789.bat"):
                (self.root / name).write_bytes(archive.read(name))
        code, output = self.launch("start_h3_studio_8789.bat --archive-fixture")
        self.assert_launched(code, output, ["--auto-port", "--port", "8789", "--open-editor", "--archive-fixture"])
        self.assertEqual(self.setup_calls(), [])


if __name__ == "__main__":
    unittest.main()
