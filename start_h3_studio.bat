@echo off
setlocal
cd /d "%~dp0H3Studio"
if exist ".venv\Scripts\python.exe" set "STUDIO_PYTHON=.venv\Scripts\python.exe"
if not defined STUDIO_PYTHON if exist "..\ComfyUI\.venv\Scripts\python.exe" set "STUDIO_PYTHON=..\ComfyUI\.venv\Scripts\python.exe"
if not defined STUDIO_PYTHON (
  echo [ERROR] MiniMax H3 Studio environment was not found.
  echo Run setup_h3_studio.bat first.
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
"%STUDIO_PYTHON%" app.py
if errorlevel 1 pause
endlocal
exit /b 0

:check_running
powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8787/api/status' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
exit /b %errorlevel%
