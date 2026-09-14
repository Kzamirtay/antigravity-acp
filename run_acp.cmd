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
    )
)

if exist "%SCRIPT_DIR%acp_bridge.py" (
    %PYTHON_CMD% -u "%SCRIPT_DIR%acp_bridge.py" %*
) else if exist "%SCRIPT_DIR%agy_acp_server.exe" (
    "%SCRIPT_DIR%agy_acp_server.exe" %*
) else (
    echo Error: Neither acp_bridge.py nor agy_acp_server.exe found in %SCRIPT_DIR%
    exit /b 1
)
