@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "STUDIO_PYTHON=H3Studio\.venv\Scripts\python.exe"

call :check_studio_python
if errorlevel 1 (
  echo MiniMax H3 Studio is being opened on a new computer or its environment needs repair.
  echo Running automatic local setup. Models and generated files will not be changed.
  call "%~dp0setup_h3_studio.bat" --auto
  if errorlevel 1 exit /b 1
)

call :check_studio_python
if errorlevel 1 (
  echo [ERROR] MiniMax H3 Studio environment could not be repaired.
  echo Install 64-bit Python 3.12, then run setup_h3_studio.bat.
  pause
  exit /b 1
)
call :check_running
if not errorlevel 1 (
  echo MiniMax H3 Studio is already running. Opening http://127.0.0.1:8787
  start "" "http://127.0.0.1:8787"
  exit /b 0
)
echo MiniMax H3 Studio: http://127.0.0.1:8787
cd /d "%~dp0H3Studio"
".venv\Scripts\python.exe" app.py
if errorlevel 1 (
  echo.
  echo [ERROR] MiniMax H3 Studio stopped unexpectedly. Review the error above.
  pause
)
endlocal
exit /b 0

:check_studio_python
if not exist "%~dp0%STUDIO_PYTHON%" exit /b 1
"%~dp0%STUDIO_PYTHON%" -c "import aiohttp, av, numpy, PIL, huggingface_hub" >nul 2>&1
exit /b %errorlevel%

:check_running
powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8787/api/status' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
exit /b %errorlevel%
