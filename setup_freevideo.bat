@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Install the pinned FreeVideo / VDN-H3 runtime in a separate environment.
echo The original ComfyUI environment and video models are preserved.
"H3Studio\.venv\Scripts\python.exe" -X utf8 "H3Studio\setup_freevideo.py" %*
if errorlevel 1 (
  echo [ERROR] Installation did not finish. Retained downloads can be resumed.
  pause
  exit /b 1
)
pause
