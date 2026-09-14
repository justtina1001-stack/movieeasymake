import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "Windows batch launcher test")
class WindowsLauncherTests(unittest.TestCase):
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
