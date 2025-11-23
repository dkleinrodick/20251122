@echo off
title WildFares

echo ===================================================
echo WildFares - Launch
echo ===================================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: Check for and activate virtual environment
if exist venv\Scripts\activate.bat (
    echo [INFO] Virtual environment detected, activating...
    call venv\Scripts\activate.bat
    echo.
)

echo [1/2] Cleaning up port 8000...
:: Find and kill any process listening on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    echo Terminating existing process PID %%a...
    taskkill /f /pid %%a >nul 2>&1
)

echo.
echo [2/2] Starting Server...
echo Opening browser...
start http://localhost:8000

echo Press Ctrl+C to stop the server.

:loop
python -m uvicorn app.main:app --reload --port 8000 --log-level warning
echo Server stopped. Restarting...
timeout /t 2 >nul
goto loop

pause