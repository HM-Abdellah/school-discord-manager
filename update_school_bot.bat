@echo off
setlocal

rem Run a temporary copy so Git can safely update this script itself.
if /i "%~1"=="--run-from-temp" goto :run_updater

for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "HELPER=%TEMP%\school_discord_manager_update_%RANDOM%_%RANDOM%.bat"
copy /y "%~f0" "%HELPER%" >nul
if errorlevel 1 (
    echo ERROR: Could not prepare a temporary updater.
    pause
    exit /b 1
)
start "SCHOOL DISCORD MANAGER - UPDATE" cmd /k ""%HELPER%" --run-from-temp "%PROJECT_DIR%""
exit /b 0

:run_updater
rem Let the original batch process close before replacing its file.
timeout /t 1 /nobreak >nul
set "PROJECT_DIR=%~2"
if not defined PROJECT_DIR (
    echo ERROR: Project directory argument is missing.
    pause
    exit /b 1
)
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not open the project directory.
    pause
    exit /b 1
)

title SCHOOL DISCORD MANAGER - UPDATE
set "BRANCH=fix/launcher-existing-venv-dependencies"

echo ==========================================
echo   SCHOOL DISCORD MANAGER - SAFE UPDATE
echo ==========================================
echo.
echo Project: %PROJECT_DIR%
echo Branch:  %BRANCH%
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
for /f "delims=" %%A in ('git status --porcelain') do (
    if /i not "%%A"=="?? update_school_bot.bat" (
        echo.
        echo ERROR: Local changes detected:
        echo %%A
        echo Back up or commit them before updating.
        goto :failed
    )
)

echo.
echo Preparing the updater for the Git update...
git ls-files --error-unmatch -- update_school_bot.bat >nul 2>&1
if not errorlevel 1 goto :updater_ready

set "PARKED_UPDATER=%TEMP%\school_discord_manager_parked_%RANDOM%_%RANDOM%.bat"
if exist "%PROJECT_DIR%\update_school_bot.bat" (
    move /y "%PROJECT_DIR%\update_school_bot.bat" "%PARKED_UPDATER%" >nul
    if errorlevel 1 (
        echo ERROR: Could not temporarily move the updater file.
        goto :failed
    )
    set "UPDATER_PARKED=1"
)

:updater_ready
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
if defined UPDATER_PARKED (
    if exist "%PARKED_UPDATER%" move /y "%PARKED_UPDATER%" "%PROJECT_DIR%\update_school_bot.bat" >nul
)
goto :finish

:done
if defined UPDATER_PARKED (
    if exist "%PARKED_UPDATER%" del /q "%PARKED_UPDATER%" >nul 2>&1
)

:finish
echo.
pause
