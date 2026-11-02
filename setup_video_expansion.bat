@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Install pinned Pika H3 Flex, updated Turbo adapters, Character Swap V1.5,
echo Motion Repair V2, and the experimental FL2VA Ref Patch.
echo Existing versions are preserved. ComfyUI core and services are not restarted.
"H3Studio\.venv\Scripts\python.exe" -X utf8 "H3Studio\setup_video_expansion.py" %*
if errorlevel 1 (
  echo [ERROR] Installation failed. Verified files and partial downloads were retained.
  pause
  exit /b 1
)
pause
