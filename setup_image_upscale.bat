@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo Please run setup_h3_studio.bat first.
  pause
  exit /b 1
)
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_image_upscale.py"
if errorlevel 1 (
  echo Image upscale setup failed. Check the error above.
  pause
  exit /b 1
)
pause
exit /b 0
