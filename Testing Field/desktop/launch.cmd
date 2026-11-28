@echo off
rem Runs desktop\launch.ps1 without changing the system script policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
exit /b %errorlevel%
