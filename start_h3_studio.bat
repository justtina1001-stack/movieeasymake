@echo off
setlocal EnableExtensions
title MiniMax H3 Studio
cd /d "%~dp0"
set "STUDIO_PYTHON=H3Studio\.venv\Scripts\python.exe"

call :check_studio_python
if errorlevel 1 (
  echo MiniMax H3 Studio is being opened on a new computer or its environment needs repair.
  echo Running automatic local setup. Models and generated files will not be changed.
  call "%~dp0setup_h3_studio.bat" --auto
  if errorlevel 1 exit /b 1
)

call :check_studio_python
if errorlevel 1 (
  echo [ERROR] MiniMax H3 Studio environment could not be repaired.
  echo Install 64-bit Python 3.12, then run setup_h3_studio.bat.
  pause
  exit /b 1
)
echo Checking the running MiniMax H3 Studio version...
cd /d "%~dp0H3Studio"
".venv\Scripts\python.exe" -u app.py --auto-port %*
set "STUDIO_EXIT=%errorlevel%"
if not "%STUDIO_EXIT%"=="0" (
  echo.
  echo [ERROR] MiniMax H3 Studio could not start or reuse the running version. Review the error above.
  pause
)
exit /b %STUDIO_EXIT%

:check_studio_python
if not exist "%~dp0%STUDIO_PYTHON%" exit /b 1
"%~dp0%STUDIO_PYTHON%" -c "import aiohttp, av, numpy, PIL, huggingface_hub" >nul 2>&1
exit /b %errorlevel%
