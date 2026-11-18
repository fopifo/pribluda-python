@echo off
chcp 65001 >nul
echo ==========================================
echo   Запуск: TW Server + Мост + Снапшоты + Приблуда + MAX Бот
echo ==========================================
echo.

echo [1/5] TW Server (приёмник роботов T-Widgets)...
start "TW Server" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/tw_server.py"
timeout /t 2 /nobreak >nul

echo [2/5] PbSdk мост (лента+стакан+позиции)...
start "PbSdk Bridge" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
timeout /t 8 /nobreak >nul

echo [3/5] Снапшоты позиций (каждые 5 мин + финал 18:50)...
start "Snapshot Positions" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/snapshot_positions.py --loop"
timeout /t 2 /nobreak >nul

echo [4/5] Приблуда (Main)...
start "Приблуда (Main)" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python main.py"
timeout /t 2 /nobreak >nul

echo [5/5] MAX Бот...
start "MAX Бот" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python bot.py"

echo.
echo Все 5 процессов запущены в отдельных окнах.
echo Окна НЕ закрывать (особенно TW Server, PbSdk Bridge, Snapshot Positions).
echo Закройте это окно-лаунчер, когда закончите работу.
pause