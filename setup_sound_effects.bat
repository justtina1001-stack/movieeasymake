@echo off
setlocal
cd /d "%~dp0"
"H3Studio\.venv\Scripts\python.exe" -X utf8 "H3Studio\setup_sound_effects.py" %*
if errorlevel 1 (
  echo MOSS sound effect setup failed. See the message above.
  exit /b 1
)
echo MOSS sound effect setup is ready.
