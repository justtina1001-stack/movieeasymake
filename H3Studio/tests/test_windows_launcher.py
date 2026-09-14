import os
import re
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
