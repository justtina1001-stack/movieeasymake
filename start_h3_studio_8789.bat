@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0start_h3_studio.bat" --port 8789 --open-editor %*
exit /b %errorlevel%
