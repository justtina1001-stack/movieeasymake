@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Install the official Qwen-Image-2.1 Turbo INT8 model only.
echo The existing text encoder, VAE and ComfyUI core will be reused.
echo Qwen Research License: https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE
if /i "%~1"=="--research-evaluation" goto install
set "QWEN_TURBO_RESEARCH_CHOICE="
set /p "QWEN_TURBO_RESEARCH_CHOICE=Type RESEARCH for non-commercial evaluation: "
if /i not "%QWEN_TURBO_RESEARCH_CHOICE%"=="RESEARCH" exit /b 1
:install
set "HF_XET_HIGH_PERFORMANCE=1"
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_qwen_image_turbo.py" --research-evaluation
if errorlevel 1 (
  echo [ERROR] Installation failed. Existing models were preserved; staging can be resumed.
  pause
  exit /b 1
)
pause
