@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "AUTO_MODE=0"
if /i "%~1"=="--auto" set "AUTO_MODE=1"
set "STUDIO_PYTHON=H3Studio\.venv\Scripts\python.exe"

if exist "%STUDIO_PYTHON%" (
  "%STUDIO_PYTHON%" -c "import sys; import aiohttp, av, numpy, PIL, huggingface_hub; print(sys.executable)" >nul 2>&1
  if not errorlevel 1 goto install
  echo Detected a Python environment copied from another computer. Rebuilding it locally...
)

call :find_python
if not defined SYSTEM_PYTHON goto no_python

echo Creating a local MiniMax H3 Studio environment...
if exist "H3Studio\.venv" (
  %SYSTEM_PYTHON% -m venv --clear "H3Studio\.venv"
) else (
  %SYSTEM_PYTHON% -m venv "H3Studio\.venv"
)
if errorlevel 1 goto failed
if not exist "%STUDIO_PYTHON%" goto failed

:install
echo Installing MiniMax H3 Studio dependencies...
"%STUDIO_PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto failed
"%STUDIO_PYTHON%" -m pip install -r "H3Studio\requirements.txt"
if errorlevel 1 goto failed
"%STUDIO_PYTHON%" -c "import aiohttp, av, numpy, PIL, huggingface_hub" >nul 2>&1
if errorlevel 1 goto failed
if not exist "H3Studio\config.json" copy /y "H3Studio\config.example.json" "H3Studio\config.json" >nul
echo.
echo Setup complete. Double-click start_h3_studio.bat.
if "%AUTO_MODE%"=="0" pause
exit /b 0

:find_python
set "SYSTEM_PYTHON="
where py >nul 2>&1
if not errorlevel 1 (
  py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>&1
  if not errorlevel 1 set "SYSTEM_PYTHON=py -3.12"
  if not defined SYSTEM_PYTHON (
    py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
    if not errorlevel 1 set "SYSTEM_PYTHON=py -3.11"
  )
)
if not defined SYSTEM_PYTHON (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; assert (3, 11) ^<= sys.version_info[:2] ^<= (3, 13)" >nul 2>&1
    if not errorlevel 1 set "SYSTEM_PYTHON=python"
  )
)
exit /b 0

:no_python
echo [ERROR] Python 3.11 or 3.12 was not found on this computer.
echo Install 64-bit Python 3.12 and enable "Add Python to PATH", then run start_h3_studio.bat again.
pause
exit /b 1

:failed
echo [ERROR] Environment repair or dependency installation failed.
echo Check the network connection and the error message above.
pause
exit /b 1
