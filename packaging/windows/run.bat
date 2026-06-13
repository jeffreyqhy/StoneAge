@echo off
setlocal
cd /d "%~dp0"

set "STONEAGE_ADB_DIR=%~dp0support\platform-tools"
set "PATH=%STONEAGE_ADB_DIR%;%PATH%"

start "" "%~dp0StoneAge Script Studio.exe"
