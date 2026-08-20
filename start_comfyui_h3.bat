@echo off
setlocal
cd /d "%~dp0ComfyUI"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] ComfyUI virtual environment was not found.
  pause
  exit /b 1
)
echo Starting ComfyUI for MiniMax H3 on http://127.0.0.1:8188
".venv\Scripts\python.exe" main.py --lowvram --reserve-vram 1.5 --preview-method taesd --auto-launch
if errorlevel 1 pause
endlocal
