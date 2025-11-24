@echo off
title WildFares Local Development v3

echo ===================================================
echo  WildFares - Universal Python Launcher
echo ===================================================
echo.

:: === FIND PYTHON EXECUTABLE ===
set "PYTHON_EXE="

:: Check for standard 'py' launcher first
py --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Found 'py.exe' launcher. Using it.
    set "PYTHON_EXE=py"
    goto found_python
)

:: Check for 'python' in PATH
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Found 'python.exe' in PATH. Using it.
    set "PYTHON_EXE=python"
    goto found_python
)

:: Check common AppData location (Windows Store version)
set "PYTHON_WIN_APP=%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
if exist "%PYTHON_WIN_APP%" (
    echo [INFO] Found Python in Windows App store location.
    set "PYTHON_EXE=%PYTHON_WIN_APP%"
    goto found_python
)

echo [ERROR] Could not automatically find a Python executable.
echo Please ensure Python 3.8+ is installed and that you have run it at least once.
pause
exit /b

:found_python
echo [SUCCESS] Using Python executable at: %PYTHON_EXE%
echo.

:: === VIRTUAL ENVIRONMENT & DEPENDENCIES ===
if not exist venv\ (
    echo [INFO] Creating virtual environment...
    %PYTHON_EXE% -m venv venv
)
call venv\Scripts\activate
echo [INFO] Installing dependencies...
pip install -r requirements.txt --quiet
echo [SUCCESS] Environment is ready.
echo.

:: === START THE SERVER ===
echo [INFO] Starting the Uvicorn server at http://localhost:8000
start http://localhost:8000
%PYTHON_EXE% -m uvicorn app.main:app --reload --port 8000

echo.
echo Server has been stopped.
pause