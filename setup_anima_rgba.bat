@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo Please run setup_h3_studio.bat first to create the Studio environment.
  pause
  exit /b 1
)
"H3Studio\.venv\Scripts\python.exe" -X utf8 "H3Studio\setup_anima_rgba.py" %*
set "ANIMA_RGBA_EXIT=%errorlevel%"
pause
exit /b %ANIMA_RGBA_EXIT%
