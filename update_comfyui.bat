@echo off
setlocal
cd /d "%~dp0ComfyUI"
git pull --ff-only
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo ComfyUI update completed.
pause
exit /b 0
:failed
echo ComfyUI update failed. Review the messages above.
pause
exit /b 1
