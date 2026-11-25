@echo off
rem 计划任务用：后台起服务，不弹控制台、不 pause，日志写 .cache\service.log
chcp 65001 >nul 2>&1
setlocal
set "APPDIR=%~dp0.."
set "TEMP=%APPDIR%\.cache\tmp"
set "TMP=%TEMP%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%TEMP%" mkdir "%TEMP%" >nul 2>&1
cd /d "%APPDIR%"
"%APPDIR%\.venv\Scripts\python.exe" "%APPDIR%\run.py" >> "%APPDIR%\.cache\service.log" 2>&1
endlocal
