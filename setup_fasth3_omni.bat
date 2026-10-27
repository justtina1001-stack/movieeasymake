@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Install the official FastH3 Omni NVFP4 checkpoint for Blackwell GPUs.
echo Requires ComfyUI 0.42.0 with full PDD support and the existing H3 components.
echo This installer does not update the engine or replace the original H3 model.
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_fasth3_omni.py" %*
if errorlevel 1 (
  echo [ERROR] Installation failed. Existing models and partial downloads were preserved.
  pause
  exit /b 1
)
pause
