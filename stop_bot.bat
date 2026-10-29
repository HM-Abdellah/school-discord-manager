@echo off
setlocal
title SCHOOL DISCORD MANAGER - STOP BOT
set "SDM_ROOT=%~dp0"

echo ============================================================
echo   STOP SCHOOL DISCORD MANAGER
echo ============================================================
echo.
echo Looking for this project's bot only...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=(Resolve-Path -LiteralPath $env:SDM_ROOT).Path; $python=(Join-Path $root '.venv\Scripts\python.exe'); $targets=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $python -and $_.CommandLine -match 'bot\.py' }); if ($targets.Count -eq 0) { Write-Host 'No School Discord Manager bot process was found.' -ForegroundColor Yellow; exit 0 }; $ids=@($targets | ForEach-Object { $_.ProcessId }); foreach ($target in $targets) { Write-Host ('Requesting stop for bot PID ' + $target.ProcessId + '...'); & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $target.ProcessId /T 2>$null | Out-Null }; Start-Sleep -Seconds 2; $remaining=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $python -and $_.CommandLine -match 'bot\.py' }); if ($remaining.Count -gt 0) { foreach ($target in $remaining) { Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500; $stillRunning=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $python -and $_.CommandLine -match 'bot\.py' }); if ($stillRunning.Count -gt 0) { Write-Host 'WARNING: One or more bot processes could not be stopped.' -ForegroundColor Red; exit 1 } }; Write-Host 'School Discord Manager is stopped.' -ForegroundColor Green"

set "STOP_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%STOP_EXIT_CODE%"=="0" (
    echo WARNING: Check the message above. The bot may still be running.
) else (
    echo You can close this window now.
)
echo.
pause
exit /b %STOP_EXIT_CODE%
