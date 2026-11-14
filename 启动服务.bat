@echo off
chcp 65001 >nul 2>&1
title ComfyBridge 1.01 - Local Image Service
setlocal
set "APPDIR=%~dp0"
rem Keep every temp/cache write inside this application folder (no writes to C:)
set "TEMP=%APPDIR%.cache\tmp"
set "TMP=%APPDIR%.cache\tmp"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%TEMP%" mkdir "%TEMP%" >nul 2>&1
cd /d "%APPDIR%"
echo.
echo  ==========================================================
echo   ComfyBridge 1.01  -  Local image service
echo  ----------------------------------------------------------
echo   Open in browser : http://127.0.0.1:8190
echo   Close this window to stop the service.
echo   Startup errors and server logs are printed below.
echo  ==========================================================
echo.
rem Port precheck: plain ASCII only (Chinese hints are printed by port_check.py)
"%APPDIR%.venv\Scripts\python.exe" "%APPDIR%tools\port_check.py"
if errorlevel 1 (
  echo.
  echo   Service NOT started - see the explanation above.
  echo   Press any key to close this window...
  pause >nul
  exit /b 1
)
"%APPDIR%.venv\Scripts\python.exe" "%APPDIR%run.py"
set "EXITCODE=%ERRORLEVEL%"
echo.
echo  ----------------------------------------------------------
echo   Service stopped. Exit code: %EXITCODE%
echo   Press any key to close this window...
pause >nul
endlocal
