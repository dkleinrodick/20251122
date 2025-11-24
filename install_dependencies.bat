@echo off
echo WildFares Dependency Installation Script
echo ========================================
echo.

REM Check if Python is installed
py --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Display Python version
echo Checking Python version...
py --version
echo.

REM Check Python version is 3.10 or higher
py -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.10 or higher is required
    echo Please upgrade Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python version check passed!
echo.

REM Ask user if they want to create a virtual environment
set /p CREATE_VENV="Do you want to create a virtual environment? (Recommended) [Y/n]: "
if /i "%CREATE_VENV%"=="n" goto SKIP_VENV

echo.
echo Creating virtual environment...
py -m venv venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment
    echo Make sure the venv module is available
    pause
    exit /b 1
)

echo Virtual environment created successfully!
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

echo Virtual environment activated!
echo.

:SKIP_VENV

REM Upgrade pip
echo Upgrading pip to latest version...
py -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: Failed to upgrade pip, continuing anyway...
)
echo.

REM Install requirements
echo Installing dependencies from requirements.txt...
echo.
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install dependencies
    echo Please check the error messages above
    pause
    exit /b 1
)

echo.
echo ========================================
echo Installation completed successfully!
echo ========================================
echo.

if exist venv (
    echo Virtual environment created at: %CD%\venv
    echo.
    echo To activate the virtual environment in the future, run:
    echo   venv\Scripts\activate.bat
    echo.
    echo To deactivate the virtual environment, run:
    echo   deactivate
    echo.
)

echo You can now run the application with:
echo   run_locally.bat
echo.
pause
