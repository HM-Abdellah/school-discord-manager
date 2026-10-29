@echo off
setlocal
cd /d "%~dp0"
title SCHOOL DISCORD MANAGER - UPDATE

set "BRANCH=fix/launcher-existing-venv-dependencies"

echo ==========================================
echo   SCHOOL DISCORD MANAGER - SAFE UPDATE
echo ==========================================
echo.
echo This script updates only the branch:
echo %BRANCH%
echo.
echo Make sure the bot is stopped before continuing.
pause

echo.
echo Checking that Git is available...
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or not in PATH.
    goto :failed
)

if not exist ".git" (
    echo ERROR: This folder is not a Git repository.
    goto :failed
)

echo.
echo Checking that the project bot is not running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = (Resolve-Path '.').Path; $running = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'bot\.py' -and $_.CommandLine -like ('*' + $root + '*') }; if ($running) { Write-Host 'ERROR: The project bot is still running. Stop it before updating.'; exit 1 }"
if errorlevel 1 goto :failed

echo.
echo Checking for local changes...
for /f "delims=" %%A in ('git status --porcelain -- . ":(exclude)update_school_bot.bat"') do (
    echo.
    echo ERROR: Local changes detected.
    echo Back up or commit them before updating.
    goto :failed
)

echo.
echo Fetching the update branch from GitHub...
git fetch origin "refs/heads/%BRANCH%:refs/remotes/origin/%BRANCH%"
if errorlevel 1 goto :failed

echo.
echo Switching to the update branch...
git show-ref --verify --quiet "refs/heads/%BRANCH%"
if not errorlevel 1 goto :existing_branch

git switch --track -c "%BRANCH%" "origin/%BRANCH%"
if errorlevel 1 goto :failed
goto :pull_updates

:existing_branch
git switch "%BRANCH%"
if errorlevel 1 goto :failed

:pull_updates
echo.
echo Applying updates with fast-forward only...
git pull --ff-only origin "%BRANCH%"
if errorlevel 1 goto :failed

echo.
echo Update completed. Current version:
git log -1 --oneline
echo.
git status --short --branch
echo.
echo Next: double-click start_bot.bat to start the bot.
goto :done

:failed
echo.
echo UPDATE STOPPED. Read the error above.
echo No local changes were intentionally discarded.

:done
echo.
pause
