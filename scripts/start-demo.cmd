@echo off
rem Double-click wrapper for scripts\start-demo.ps1 (bypasses execution policy).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
