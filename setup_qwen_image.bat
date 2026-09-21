@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Qwen-Image-2.1: research/evaluation only. Commercial use requires a separate license.
echo https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE
set "QWEN_RESEARCH_CHOICE="
set /p "QWEN_RESEARCH_CHOICE=Type RESEARCH to install for non-commercial evaluation: "
if /i not "%QWEN_RESEARCH_CHOICE%"=="RESEARCH" exit /b 1
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_qwen_image.py" --research-evaluation
if errorlevel 1 (
  echo [ERROR] Installation failed. Check the message above; downloads can be resumed.
  pause
  exit /b 1
)
pause
