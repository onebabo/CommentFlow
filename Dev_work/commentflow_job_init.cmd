@echo off
REM Start a real PowerShell window (not CMD-hosted), then close this CMD.
start "Windows PowerShell" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%~dp0commentflow_job_init.ps1"
exit /b 0
