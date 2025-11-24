@echo off
title WildFares - Local Test

echo ===================================================
echo WildFares - Local Testing Mode
echo ===================================================
echo.
echo This script starts the server locally for verification.
echo Note: You must have Python installed.
echo.

:: Check for Python
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

echo [INFO] Attempting to install dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Dependency installation failed. 
    echo If this is due to 'asyncpg', the backend might not run locally on Windows without build tools.
    echo proceeding anyway...
)

echo.
echo [INFO] Starting Server on Port 8000...
start http://localhost:8000

py -m uvicorn app.main:app --reload --port 8000 --log-level info

pause