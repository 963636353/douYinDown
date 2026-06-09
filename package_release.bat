@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1" -PauseOnExit -OpenReleaseFolder
exit /b %ERRORLEVEL%
