@echo off
chcp 65001 >nul
set "PROJECT_DIR=C:\Users\Public\MI_CODES\dpNEW"

echo [1/6] TW Server
start "TW Server" cmd /k "cd /d %PROJECT_DIR% && python tools/tw_server.py"
timeout /t 2 /nobreak >nul

echo [2/6] Chrome + TW widget
tasklist /FI "IMAGENAME eq chrome.exe" 2>NUL | find /I "chrome.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "" chrome
    timeout /t 5 /nobreak >nul
)

echo [3/6] PbSdk Bridge
start "PbSdk Bridge" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
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