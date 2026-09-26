@echo off
rem Launch the CollaboratorMCP desktop app (UI + MCP bridge).
rem Runs from source so code changes apply without a rebuild; falls back to
rem the built dist\CollaboratorMCP.exe if Python is not available.

cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if not exist "%PYW%" (
    for /f "delims=" %%P in ('where pythonw 2^>nul') do (
        set "PYW=%%P"
        goto :have_python
    )
    goto :use_exe
)

:have_python
rem pythonw has no console window; "start" returns immediately.
start "" "%PYW%" "%~dp0main.py"
exit /b 0

:use_exe
if exist "%~dp0dist\CollaboratorMCP.exe" (
    start "" "%~dp0dist\CollaboratorMCP.exe"
    exit /b 0
)

echo Could not find Python or dist\CollaboratorMCP.exe.
echo Install Python 3.10+ or run "python build.py" to build the executable.
pause
exit /b 1
