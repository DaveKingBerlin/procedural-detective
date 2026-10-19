@echo off
REM build-bridge-windows.bat - thin wrapper around build-bridge-windows.ps1
REM (Phase 49 Sec.8/Sec.35). Requires no Administrator privileges.
REM
REM Usage:
REM   build-bridge-windows.bat
REM   build-bridge-windows.bat -AllowDirty
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-bridge-windows.ps1" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
