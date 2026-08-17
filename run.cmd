@echo off
REM Launch the widget with no console window, then exit immediately.
REM
REM pythonw.exe is the same interpreter as python.exe with no console attached,
REM so nothing has to stay open. Double-click this file, or run it from a
REM terminal you can then close.
setlocal
set "HERE=%~dp0"

REM Prefer the project's virtual environment when one exists.
set "PYW=%HERE%.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto launch

REM Otherwise fall back to pythonw.exe on PATH.
set "PYW="
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    if not defined PYW set "PYW=%%P"
)
if not defined PYW (
    echo Could not find pythonw.exe.
    echo.
    echo It ships with Python and normally sits next to python.exe. If Python
    echo is installed but not on PATH, run the widget with the full path, e.g.
    echo    C:\Python314\pythonw.exe -m claude_usage_widget
    pause
    exit /b 1
)

:launch
start "Claude Usage Widget" /D "%HERE%" "%PYW%" -m claude_usage_widget
exit /b 0
