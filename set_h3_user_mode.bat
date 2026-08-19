@echo off
setlocal
cd /d "%~dp0"
if exist "H3Studio\.venv\Scripts\python.exe" set "H3_ROLE_PYTHON=H3Studio\.venv\Scripts\python.exe"
if not defined H3_ROLE_PYTHON if exist "ComfyUI\.venv\Scripts\python.exe" set "H3_ROLE_PYTHON=ComfyUI\.venv\Scripts\python.exe"
if not defined H3_ROLE_PYTHON goto missing
"%H3_ROLE_PYTHON%" "H3Studio\set_role.py" client
pause
exit /b 0
:missing
echo [ERROR] H3 Studio Python environment was not found. Run setup_h3_studio.bat first.
pause
