@echo off
setlocal
cd /d "%~dp0"
if not exist "H3Studio\.venv\Scripts\python.exe" (
  echo [ERROR] Run setup_h3_studio.bat first.
  pause
  exit /b 1
)
echo Local screenplay writer: Hermes 4 14B, Q5_K_M, approximately 10.51 GB.
echo The default model is h3-script-hermes4:14b-q5_k_m, installed from verified GGUF weights.
echo The managed runtime and weights are stored inside H3Studio\data\scriptwriting.
echo An existing Ollama server keeps its own model storage location.
echo Existing models are preserved. Use --model qwen3.8:27b to install the previous model.
"H3Studio\.venv\Scripts\python.exe" "H3Studio\setup_scriptwriting.py" %*
if errorlevel 1 (
  echo [ERROR] Installation failed. Run again to resume unfinished downloads.
  pause
  exit /b 1
)
echo Ready. Restart H3 Studio, then open the screenplay workspace.
pause
