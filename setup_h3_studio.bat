@echo off
setlocal
cd /d "%~dp0"

if exist "H3Studio\.venv\Scripts\python.exe" goto install

where py >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv "H3Studio\.venv" >nul 2>&1
  if exist "H3Studio\.venv\Scripts\python.exe" goto install
  py -3.11 -m venv "H3Studio\.venv" >nul 2>&1
  if exist "H3Studio\.venv\Scripts\python.exe" goto install
)

where python >nul 2>&1
if errorlevel 1 goto no_python
python -m venv "H3Studio\.venv"
if not exist "H3Studio\.venv\Scripts\python.exe" goto no_python

:install
echo Installing MiniMax H3 Studio dependencies...
"H3Studio\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
"H3Studio\.venv\Scripts\python.exe" -m pip install -r "H3Studio\requirements.txt"
if errorlevel 1 goto failed
if not exist "H3Studio\config.json" copy /y "H3Studio\config.example.json" "H3Studio\config.json" >nul
echo.
echo Setup complete. Double-click start_h3_studio.bat.
pause
exit /b 0

:no_python
echo [ERROR] Python 3.11 or 3.12 was not found. Install Python first, then run this file again.
pause
exit /b 1

:failed
echo [ERROR] Dependency installation failed. Check the network and error message above.
pause
exit /b 1
