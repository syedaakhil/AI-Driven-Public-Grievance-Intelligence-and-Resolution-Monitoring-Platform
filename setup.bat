@echo off
rem City Corporation grievance portal - first-time setup on Windows.
rem Double-click this file, or run it from a terminal in the project folder.
setlocal
cd /d "%~dp0"

echo ==================================================
echo   Welcome! Setting up the City Corporation portal
echo ==================================================
echo.

echo [1/5] Creating the virtual environment .venv ...
python -m venv .venv
if errorlevel 1 goto :error

echo [2/5] Upgrading pip ...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/5] Installing packages from requirements.txt ...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/5] Checking the .env settings file ...
if not exist .env (
    copy .env.example .env >nul
    echo Created .env from .env.example - edit it to add your settings.
) else (
    echo .env already exists - left unchanged.
)

echo [5/5] Creating the database tables ...
.venv\Scripts\python.exe manage.py migrate
if errorlevel 1 goto :error

echo.
echo Setup complete! Run: .venv\Scripts\python.exe manage.py runserver
echo Then open http://127.0.0.1:8000/login/ in your browser.
echo.
pause
exit /b 0

:error
echo.
echo Setup stopped: the step above failed. Fix the problem and run setup.bat again.
echo  - "python" not found: install Python 3.12+ from python.org and tick "Add to PATH".
echo  - migrate failed with MySQL: check DATABASE_URL in .env and that citycorp_db exists.
echo.
pause
exit /b 1
