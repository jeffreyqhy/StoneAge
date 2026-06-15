@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\bootstrap_support.ps1"
if errorlevel 1 (
    echo.
    echo Setup failed. Please check the message above.
    pause
    exit /b 1
)

call "%~dp0run.bat"
