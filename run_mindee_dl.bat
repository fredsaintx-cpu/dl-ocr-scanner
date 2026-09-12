@echo off
title Mindee DL OCR - Driver License Automation
cd /d "%~dp0"

:: Find Python
set PY=
for %%P in (python3.exe python.exe) do (
    if not defined PY (
        where %%P >nul 2>&1 && set PY=%%P
    )
)

:: Try common install paths if not in PATH
if not defined PY (
    for %%D in (
        "C:\Python314\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    ) do (
        if not defined PY (
            if exist %%D set PY=%%D
        )
    )
)

if not defined PY (
    echo ERROR: Python not found. Install from https://python.org
    pause
    exit /b 1
)

echo Using Python: %PY%
echo.
%PY% "%~dp0run10.py" %*
pause
