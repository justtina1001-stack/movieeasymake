@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Install the official FLUX 3 Image Klein INT8 editing bundle.
echo Requires ComfyUI 0.41.3 or later. This installer does not update the engine.
echo Model access: https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy
echo Model license: https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_flux3_image.py" %*
if errorlevel 1 (
  echo [ERROR] Installation failed. Existing models and partial downloads were preserved.
  pause
  exit /b 1
)
pause
