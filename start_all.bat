@echo off
chcp 65001 >nul
set "PROJECT_DIR=C:\Users\Public\MI_CODES\dpNEW"
title start_all
rem guard: povtorniy zapusk nichego ne delaet
tasklist /FI "WINDOWTITLE eq PbSdk Bridge*" 2>NUL | find /I "cmd.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] already running.
    timeout /t 5 /nobreak >nul
    exit /b
)
echo [1/4] TW Server
start "TW Server" /min cmd /k "cd /d %PROJECT_DIR% && python tools/tw_server.py"
timeout /t 2 /nobreak >nul
echo [2/4] PbSdk Bridge - dotnet compiles up to a minute
start "PbSdk Bridge" /min cmd /k "cd /d %PROJECT_DIR%\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
timeout /t 5 /nobreak >nul
echo [3/4] Snapshot Positions
start "Snapshot Positions" /min cmd /k "cd /d %PROJECT_DIR% && python tools/snapshot_positions.py --loop"
timeout /t 2 /nobreak >nul
echo [4/4] Aggregator + Overlay
start "" /min pyw tools\entities_aggregator.py
start "" /min pyw tools\overlay.py
echo.
echo COLLECTION ONLY. No trading. Done.
timeout /t 5 >nul
