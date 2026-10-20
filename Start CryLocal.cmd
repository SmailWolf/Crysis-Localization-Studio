@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.python\pythonw.exe" (
    start "CryLocal" "%~dp0.python\pythonw.exe" -X utf8 "%~dp0windows_start.py" gui %*
    exit /b 0
)
echo CryLocal: setting up the portable Windows environment...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\setup.ps1"
if errorlevel 1 (
    echo Setup failed. Please check your internet connection and try again.
    pause
    exit /b 1
)
start "CryLocal" "%~dp0.python\pythonw.exe" -X utf8 "%~dp0windows_start.py" gui %*
