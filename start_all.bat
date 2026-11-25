@echo off
chcp 65001 >nul
title start_all
set "PROJECT_DIR=C:\Users\Public\MI_CODES\dpNEW"

rem === RE-ENTRY GUARD: povtorniy zapusk nichego ne delaet ===
tasklist /FI "IMAGENAME eq pyw.exe" 2>NUL | find /I "pyw.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] aggregator/overlay already running - nothing to start.
    timeout /t 5 /nobreak >nul
    exit /b
)
tasklist /FI "WINDOWTITLE eq Arbitrage Main*" 2>NUL | find /I "cmd.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] trading stack already running - nothing to start.
    timeout /t 5 /nobreak >nul
    exit /b
)

echo [1/6] TW Server
start "TW Server" cmd /k "cd /d %PROJECT_DIR% && python tools/tw_server.py"
timeout /t 2 /nobreak >nul

echo [2/6] Chrome + TW widget
tasklist /FI "IMAGENAME eq chrome.exe" 2>NUL | find /I "chrome.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "" chrome
    timeout /t 5 /nobreak >nul
)

echo [3/6] PbSdk Bridge - dotnet compiles, up to a minute
start "PbSdk Bridge" cmd /k "cd /d %PROJECT_DIR%\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
timeout /t 8 /nobreak >nul

echo [4/6] Snapshot Positions
start "Snapshot Positions" cmd /k "cd /d %PROJECT_DIR% && python tools/snapshot_positions.py --loop"
timeout /t 2 /nobreak >nul

echo [5/6] Arbitrage Main (bridge)
start "Arbitrage Main" cmd /k "cd /d %PROJECT_DIR% && python main.py --source bridge"
timeout /t 2 /nobreak >nul

echo [6/6] MAX bot
start "MAX bot" cmd /k "cd /d %PROJECT_DIR% && python bot.py"

echo.
echo All 6 started. Data: %PROJECT_DIR%\data\
start "" /min pyw tools\entities_aggregator.py
timeout /t 2 /nobreak >nul
start "" /min pyw tools\overlay.py
echo.
echo Done. This window can be closed.
