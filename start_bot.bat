@echo off
setlocal
cd /d "%~dp0"
title School Discord Manager

echo ============================================================
echo   SCHOOL DISCORD MANAGER
echo ============================================================
echo.

if not exist "bot.py" (
    echo ERROR: bot.py was not found.
    echo Keep this file in the main School Discord Manager folder.
    pause
    exit /b 1
)

if not exist "requirements-runtime.txt" (
    echo ERROR: requirements-runtime.txt was not found.
    echo Download the complete project folder again.
    pause
    exit /b 1
)

if not exist ".env" goto :create_env

call :validate_env
if errorlevel 1 exit /b 1

if not exist ".venv\Scripts\python.exe" goto :setup_python

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :python_version_error

goto :run_bot

:create_env
if not exist ".env.example" (
    echo ERROR: .env.example was not found.
    pause
    exit /b 1
)
copy /Y ".env.example" ".env" >nul
if errorlevel 1 (
    echo ERROR: Could not create .env.
    pause
    exit /b 1
)
echo First-time setup: a .env file was created.
echo Add the school bot token in that file, save it, then run this file again.
start "" notepad.exe "%CD%\.env"
pause
exit /b 1

:validate_env
findstr /R /C:"^[ ]*DISCORD_TOKEN[ ]*=" ".env" >nul
if errorlevel 1 goto :invalid_env
findstr /R /C:"^[ ]*DISCORD_TOKEN[ ]*=[ ]*$" ".env" >nul
if not errorlevel 1 goto :invalid_env
findstr /C:"YOUR_DISCORD_BOT_TOKEN_HERE" /C:"YOUR_REAL_DISCORD_BOT_TOKEN" ".env" >nul
if not errorlevel 1 goto :invalid_env
exit /b 0

:invalid_env
echo ERROR: DISCORD_TOKEN is missing or still contains the example placeholder.
echo Edit .env and set DISCORD_TOKEN to the school bot's token.
echo Never send or publish the token.
start "" notepad.exe "%CD%\.env"
pause
exit /b 1

:setup_python
echo First run: preparing the Python environment.
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv ".venv"
) else (
    where python >nul 2>&1
    if errorlevel 1 goto :python_missing
    python -m venv ".venv"
)
if errorlevel 1 goto :venv_failed

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :python_version_error

echo Installing the bot's runtime dependencies. This happens on first setup.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements-runtime.txt"
if errorlevel 1 goto :dependencies_failed

goto :run_bot

:python_missing
echo ERROR: Python 3.12 or newer was not found.
echo Install Python 3.12 or newer, then run this file again.
echo During installation, enable the Python launcher or add Python to PATH.
pause
exit /b 1

:python_version_error
echo ERROR: The selected Python environment is older than Python 3.12
echo or could not be started.
echo Install Python 3.12 or newer. If .venv already exists, remove that
echo virtual environment and run this file again.
pause
exit /b 1

:venv_failed
echo ERROR: Could not create the Python virtual environment.
echo Check the Python installation and try again.
pause
exit /b 1

:dependencies_failed
echo ERROR: Dependency installation failed.
echo Check the Internet connection, then run this file again.
pause
exit /b 1

:run_bot
echo.
echo Starting School Discord Manager...
echo Keep this window open while the bot should be online.
echo Press Ctrl+C to stop the bot safely.
echo Do not open this launcher a second time while it is already running.
echo.
".venv\Scripts\python.exe" bot.py
set "BOT_EXIT_CODE=%ERRORLEVEL%"
echo.
echo The bot process has stopped (exit code %BOT_EXIT_CODE%).
echo Check the messages above if there was an error.
pause
exit /b %BOT_EXIT_CODE%
