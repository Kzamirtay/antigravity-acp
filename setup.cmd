@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PYTHON_CMD=python

where python >nul 2>nul
if %errorlevel% neq 0 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
        set PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    ) else (
        echo Error: python is not found in PATH or standard locations. Please install Python 3.10+.
        exit /b 1
    )
)

%PYTHON_CMD% "%SCRIPT_DIR%agy_acp.py" %*
exit /b %errorlevel%
