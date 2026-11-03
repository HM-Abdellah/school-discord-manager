@echo off
setlocal

rem Only a copy launched from the Windows temporary folder may self-delete.
for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"
for %%I in ("%TEMP%\.") do set "TEMP_DIR=%%~fI"
if /i "%~1"=="--run-from-temp" if /i "%SCRIPT_DIR%"=="%TEMP_DIR%" goto :run_updater

rem Run a temporary copy so this updater can safely replace itself.
for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "HELPER=%TEMP%\school_discord_manager_update_%RANDOM%_%RANDOM%.bat"
copy /y "%~f0" "%HELPER%" >nul
if errorlevel 1 (
    echo ERROR: Could not prepare the temporary updater.
    pause
    exit /b 1
)
start "SCHOOL DISCORD MANAGER - UPDATE" cmd /k ""%HELPER%" --run-from-temp "%PROJECT_DIR%""
exit /b 0

:run_updater
timeout /t 1 /nobreak >nul
set "PROJECT_DIR=%~2"
if not defined PROJECT_DIR (
    echo ERROR: Project directory argument is missing.
    goto :failed
)
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not open the project directory.
    goto :failed
)

title SCHOOL DISCORD MANAGER - UPDATE
set "BRANCH=fix/launcher-existing-venv-dependencies"
set "EXPECTED_HTTPS=https://github.com/HM-Abdellah/school-discord-manager.git"
set "EXPECTED_HTTPS_NO_GIT=https://github.com/HM-Abdellah/school-discord-manager"
set "EXPECTED_SSH=git@github.com:HM-Abdellah/school-discord-manager.git"
set "EXPECTED_SSH_NO_GIT=git@github.com:HM-Abdellah/school-discord-manager"

echo ============================================================
echo   SCHOOL DISCORD MANAGER - SAFE ONE-CLICK UPDATE
echo ============================================================
echo.
echo This updater downloads approved code from the school update branch.
echo It does not delete school records or reset the Discord server.
echo.
echo Project folder: %PROJECT_DIR%
echo Update branch:  %BRANCH%
echo.
echo IMPORTANT:
echo 1. Stop the bot first using Ctrl+C in its bot console.
echo 2. Do not update while a command or school operation is running.
echo 3. Keep this computer connected to the Internet.
echo.
choice /C YN /N /M "Continue with the update? [Y/N] "
if errorlevel 2 goto :cancelled

echo.
echo [1/7] Checking Git and project folder...
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or is not available in PATH.
    goto :failed
)
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a valid Git working copy.
    goto :failed
)
if not exist "bot.py" (
    echo ERROR: bot.py is missing from this folder.
    goto :failed
)

echo.
echo [2/7] Verifying the project source...
set "ORIGIN_URL="
for /f "delims=" %%U in ('git remote get-url origin 2^>nul') do set "ORIGIN_URL=%%U"
if /i "%ORIGIN_URL%"=="%EXPECTED_HTTPS%" goto :origin_verified
if /i "%ORIGIN_URL%"=="%EXPECTED_HTTPS_NO_GIT%" goto :origin_verified
if /i "%ORIGIN_URL%"=="%EXPECTED_SSH%" goto :origin_verified
if /i "%ORIGIN_URL%"=="%EXPECTED_SSH_NO_GIT%" goto :origin_verified
echo ERROR: The Git origin does not match the official School Discord Manager repository.
echo Found origin: %ORIGIN_URL%
goto :failed

:origin_verified
echo Official repository verified.

echo.
echo [3/7] Checking that this project's bot is stopped...
set "PROJECT_DIR=%PROJECT_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $python=[IO.Path]::GetFullPath((Join-Path $env:PROJECT_DIR '.venv\Scripts\python.exe')); $running=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq $python) -and $_.CommandLine -match 'bot\.py' }); if ($running.Count -gt 0) { Write-Host 'ERROR: This project bot is still running. Stop it normally before updating.'; exit 1 }; exit 0 } catch { Write-Host 'ERROR: Could not verify whether the bot is running. Update cancelled for safety.'; exit 2 }"
if errorlevel 1 goto :failed

echo.
echo [4/7] Checking the approved update branch...
git fetch --no-tags origin "refs/heads/%BRANCH%:refs/remotes/origin/%BRANCH%"
if errorlevel 1 (
    echo ERROR: Could not download the approved update branch.
    echo Check the Internet connection and GitHub access, then try again.
    goto :failed
)
git show-ref --verify --quiet "refs/remotes/origin/%BRANCH%"
if errorlevel 1 (
    echo ERROR: The approved update branch was not found on GitHub.
    goto :failed
)

set "ORIGINAL_BRANCH="
set "INITIAL_HEAD="
for /f "delims=" %%B in ('git branch --show-current') do set "ORIGINAL_BRANCH=%%B"
for /f "delims=" %%H in ('git rev-parse HEAD') do set "INITIAL_HEAD=%%H"
if not defined INITIAL_HEAD (
    echo ERROR: Could not identify the currently installed version.
    goto :failed
)

git show-ref --verify --quiet "refs/heads/%BRANCH%"
if errorlevel 1 goto :new_local_branch
for /f "delims=" %%H in ('git rev-parse "refs/heads/%BRANCH%"') do set "PRE_UPDATE_HEAD=%%H"
goto :check_update_history

:new_local_branch
set "PRE_UPDATE_HEAD=%INITIAL_HEAD%"

:check_update_history
for /f "delims=" %%H in ('git rev-parse "refs/remotes/origin/%BRANCH%"') do set "TARGET_HEAD=%%H"
if not defined TARGET_HEAD (
    echo ERROR: Could not identify the GitHub update version.
    goto :failed
)
git merge-base --is-ancestor "%PRE_UPDATE_HEAD%" "%TARGET_HEAD%"
if errorlevel 1 (
    echo ERROR: The local update branch has commits that are not in the approved GitHub branch.
    echo Nothing will be overwritten. Ask the technical maintainer to inspect this installation.
    goto :failed
)

echo.
echo [5/7] Checking for local code changes...
set "STATUS_FILE=%TEMP%\school_discord_manager_status_%RANDOM%_%RANDOM%.txt"
git status --porcelain=v1 --untracked-files=all > "%STATUS_FILE%"
if errorlevel 1 (
    echo ERROR: Could not inspect the project's local changes.
    goto :failed
)
set "STATUS_COUNT=0"
for /f %%N in ('find /c /v "" ^< "%STATUS_FILE%"') do set "STATUS_COUNT=%%N"
if "%STATUS_COUNT%"=="0" goto :local_changes_clean
if not "%STATUS_COUNT%"=="1" goto :local_changes_detected

findstr /l /x /c:" M update_school_bot.bat" "%STATUS_FILE%" >nul
if errorlevel 1 goto :local_changes_detected

set "LOCAL_UPDATER_BLOB="
set "TARGET_UPDATER_BLOB="
for /f "delims=" %%H in ('git hash-object --path=update_school_bot.bat "update_school_bot.bat"') do set "LOCAL_UPDATER_BLOB=%%H"
for /f "delims=" %%H in ('git rev-parse "refs/remotes/origin/%BRANCH%:update_school_bot.bat"') do set "TARGET_UPDATER_BLOB=%%H"
if not defined LOCAL_UPDATER_BLOB goto :local_changes_detected
if not defined TARGET_UPDATER_BLOB goto :local_changes_detected

set "OFFICIAL_UPDATER_MATCH="
for /f "delims=" %%C in ('git log --format^=%%H "refs/remotes/origin/%BRANCH%" -- update_school_bot.bat') do call :check_official_updater_blob "%%C"
if not defined OFFICIAL_UPDATER_MATCH goto :local_changes_detected

echo The local updater matches an official version in the approved branch history.
set "TRUSTED_UPDATER_CHANGE=1"
goto :local_changes_done

:local_changes_clean
if exist "%STATUS_FILE%" del /q "%STATUS_FILE%" >nul 2>&1
set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain=v1 --untracked-files=all') do set "HAS_CHANGES=1"
if defined HAS_CHANGES goto :local_changes_detected
goto :local_changes_done

:local_changes_detected
if defined STATUS_FILE if exist "%STATUS_FILE%" del /q "%STATUS_FILE%" >nul 2>&1
echo ERROR: Local code changes or untracked files were found.
git status --short
echo No project files will be overwritten. Only an updater identical to the approved GitHub copy can be reconciled automatically.
goto :failed

:local_changes_done
if defined STATUS_FILE if exist "%STATUS_FILE%" del /q "%STATUS_FILE%" >nul 2>&1
if /i "%PRE_UPDATE_HEAD%"=="%TARGET_HEAD%" if /i "%ORIGINAL_BRANCH%"=="%BRANCH%" goto :switch_branch
echo A new version is available.
echo Installed: %PRE_UPDATE_HEAD%
echo Available: %TARGET_HEAD%

echo.
echo [6/7] Creating a local recovery backup...
if not defined LOCALAPPDATA (
    echo ERROR: Windows LOCALAPPDATA is unavailable; backup location cannot be secured.
    goto :failed
)
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "BACKUP_STAMP=%%T"
if not defined BACKUP_STAMP (
    echo ERROR: Could not create a backup timestamp.
    goto :failed
)
set "BACKUP_DIR=%LOCALAPPDATA%\SchoolDiscordManager\UpdateBackups\%BACKUP_STAMP%_%RANDOM%"
mkdir "%BACKUP_DIR%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Could not create the recovery backup folder.
    goto :failed
)
echo %PRE_UPDATE_HEAD%> "%BACKUP_DIR%\previous_commit.txt"
echo %TARGET_HEAD%> "%BACKUP_DIR%\target_commit.txt"

if not exist ".env" goto :env_backup_done
copy /y ".env" "%BACKUP_DIR%\.env" >nul
if errorlevel 1 (
    echo ERROR: Could not back up .env. The update was cancelled.
    goto :backup_failed
)

:env_backup_done
if not exist "data\" goto :data_backup_done
robocopy "data" "%BACKUP_DIR%\data" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NFL /NDL /NJH /NJS /NP >nul
set "ROBOCOPY_EXIT=%ERRORLEVEL%"
if %ROBOCOPY_EXIT% GEQ 8 (
    echo ERROR: Could not copy the school's data folder completely.
    goto :backup_failed
)
if exist "data\school.db" if not exist "%BACKUP_DIR%\data\school.db" goto :backup_failed
if exist "data\school.db" call :verify_database_backup
if exist "data\school.db" if errorlevel 1 goto :backup_failed

:data_backup_done
echo Backup created:
echo %BACKUP_DIR%

if not defined TRUSTED_UPDATER_CHANGE goto :switch_branch
echo.
echo Reconciling the verified updater copy after the recovery backup...
git restore --source=HEAD --worktree -- "update_school_bot.bat"
if errorlevel 1 (
    echo ERROR: Could not safely prepare the verified updater file.
    goto :failed
)
set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain=v1 --untracked-files=all') do set "HAS_CHANGES=1"
if defined HAS_CHANGES (
    echo ERROR: Unexpected local changes remain after updater reconciliation.
    git status --short
    goto :failed
)

:switch_branch
echo.
echo [7/7] Applying the approved update...
if /i "%ORIGINAL_BRANCH%"=="%BRANCH%" goto :branch_ready
git show-ref --verify --quiet "refs/heads/%BRANCH%"
if not errorlevel 1 goto :switch_existing_branch
git switch -c "%BRANCH%" "%PRE_UPDATE_HEAD%"
if errorlevel 1 goto :failed
git branch --set-upstream-to="origin/%BRANCH%" "%BRANCH%" >nul 2>&1
if errorlevel 1 goto :failed
goto :branch_ready

:switch_existing_branch
git switch "%BRANCH%"
if errorlevel 1 goto :failed

:branch_ready
for /f "delims=" %%H in ('git rev-parse HEAD') do set "CHECKOUT_HEAD=%%H"
if not "%CHECKOUT_HEAD%"=="%PRE_UPDATE_HEAD%" (
    echo ERROR: The local branch changed unexpectedly during preparation.
    goto :failed
)
if /i "%PRE_UPDATE_HEAD%"=="%TARGET_HEAD%" goto :already_current

git merge --ff-only "refs/remotes/origin/%BRANCH%"
if errorlevel 1 (
    echo ERROR: Git refused a fast-forward update. No forced overwrite was attempted.
    goto :failed
)

for /f "delims=" %%H in ('git rev-parse HEAD') do set "UPDATED_HEAD=%%H"
if not "%UPDATED_HEAD%"=="%TARGET_HEAD%" (
    echo ERROR: The resulting version does not match the approved GitHub version.
    echo Do not start the bot. Contact the technical maintainer.
    goto :failed_after_merge
)

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain') do set "HAS_CHANGES=1"
if defined HAS_CHANGES (
    echo ERROR: Git reported local changes after updating.
    git status --short
    echo Do not start the bot until the technical maintainer reviews this folder.
    goto :failed_after_merge
)

echo.
echo UPDATE COMPLETED SUCCESSFULLY.
echo Previous version: %PRE_UPDATE_HEAD%
echo Installed version: %UPDATED_HEAD%
echo Recovery backup: %BACKUP_DIR%
goto :ask_start

:already_current
echo.
echo The project is already up to date.
echo Installed version: %PRE_UPDATE_HEAD%
if defined BACKUP_DIR echo Recovery backup: %BACKUP_DIR%
goto :ask_start

:backup_failed
echo.
echo ERROR: The recovery backup could not be verified.
echo The application update was NOT started.
if defined BACKUP_DIR echo Partial backup folder: %BACKUP_DIR%
goto :failed

:failed_after_merge
echo.
echo The Git update may already have been applied.
echo Do not run the bot and do not repeat the update blindly.
if defined BACKUP_DIR echo Recovery backup: %BACKUP_DIR%
goto :failed

:history_mismatch
echo.
echo ERROR: Local and approved update histories do not match.
goto :failed

:ask_start
echo.
choice /C YN /N /M "Do you want to start the bot now? [Y/N] "
if errorlevel 2 goto :finish
if not exist "%PROJECT_DIR%\start_bot.bat" (
    echo ERROR: start_bot.bat was not found. Start the bot manually after review.
    goto :finish
)
start "" "%PROJECT_DIR%\start_bot.bat"
echo The bot launcher has been opened in a separate window.
goto :finish

:cancelled
echo.
echo Update cancelled. No project files were changed.
goto :finish

:failed
echo.
echo UPDATE STOPPED. No forced overwrite or cleanup was performed.
if defined ORIGINAL_BRANCH (
    if /i not "%ORIGINAL_BRANCH%"=="%BRANCH%" git switch "%ORIGINAL_BRANCH%" >nul 2>&1
)
echo Review the message above. Contact the technical maintainer if the cause is unclear.
goto :finish

:finish
echo.
pause
del /q "%~f0" >nul 2>&1
exit /b 0

:verify_database_backup
set "SOURCE_DB_SIZE="
set "BACKUP_DB_SIZE="
for %%F in ("data\school.db") do set "SOURCE_DB_SIZE=%%~zF"
for %%F in ("%BACKUP_DIR%\data\school.db") do set "BACKUP_DB_SIZE=%%~zF"
if not "%SOURCE_DB_SIZE%"=="%BACKUP_DB_SIZE%" exit /b 1
exit /b 0

:check_official_updater_blob
for /f "delims=" %%H in ('git rev-parse "%~1:update_school_bot.bat" 2^>nul') do (
    if /i "%%H"=="%LOCAL_UPDATER_BLOB%" set "OFFICIAL_UPDATER_MATCH=1"
)
exit /b 0
