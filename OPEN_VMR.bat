@echo off
setlocal
cd /d "%~dp0"
where node.exe >nul 2>nul
if errorlevel 1 (
  echo VMR requires Node.js, but node.exe was not found.
  echo Install Node.js or open VMR.html and grant file permission manually.
  pause
  exit /b 1
)
title Vehicle Maintenance Record
node.exe "%~dp0vmr-launcher.js"
if errorlevel 1 pause
