@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_face_repair.py"
if errorlevel 1 (
  echo [ERROR] Face repair setup failed. See the message above.
  pause
  exit /b 1
)
pause
