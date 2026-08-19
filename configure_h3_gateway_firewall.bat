@echo off
setlocal
set "H3_GATEWAY_PORT=%~1"
if not defined H3_GATEWAY_PORT set "H3_GATEWAY_PORT=8190"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%H3_GATEWAY_PORT%' -Verb RunAs"
  exit /b
)

set "H3_RULE_NAME=MiniMax H3 Shared Gateway %H3_GATEWAY_PORT%"
netsh advfirewall firewall show rule name="%H3_RULE_NAME%" >nul 2>&1
if "%errorlevel%"=="0" (
  echo Firewall rule already exists: %H3_RULE_NAME%
) else (
  netsh advfirewall firewall add rule name="%H3_RULE_NAME%" dir=in action=allow protocol=TCP localport=%H3_GATEWAY_PORT% remoteip=localsubnet profile=private
)

echo.
echo H3 Gateway port %H3_GATEWAY_PORT% is allowed on Private networks for the local subnet only.
echo Do not expose ComfyUI port 8188.
pause
