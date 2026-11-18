@echo off
chcp 65001 >nul
echo ==========================================
echo   Запуск: TW Server + Chrome + Мост + Снапшоты + Приблуда + MAX Бот + Отчёт
echo ==========================================
echo.

echo [1/7] TW Server (приёмник роботов T-Widgets)...
start "TW Server" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/tw_server.py"
timeout /t 2 /nobreak >nul

echo [2/7] Браузер для TW-эталона (вкладка терминала + расширение)...
tasklist /FI "IMAGENAME eq chrome.exe" 2>NUL | find /I "chrome.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo       Chrome закрыт - открываю браузер, вкладки восстановятся сами...
    start "" chrome
    timeout /t 5 /nobreak >nul
) else (
    echo       Chrome уже запущен - вкладка терминала должна быть открыта.
)
echo       Если вкладки не восстановились: Chrome -> три точки -> История -> Недавние вкладки.
echo       Проверь, что расширение "Pribluda TW Intercept" включено на tbank-online.com/terminal/.

echo [3/7] PbSdk мост (лента+стакан+позиции)...
start "PbSdk Bridge" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
timeout /t 8 /nobreak >nul

echo [4/7] Снапшоты позиций (каждые 5 мин + финал 18:50)...
start "Snapshot Positions" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/snapshot_positions.py --loop"
timeout /t 2 /nobreak >nul

echo [5/7] Приблуда (Main)...
start "Приблуда (Main)" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python main.py"
timeout /t 2 /nobreak >nul

echo [6/7] MAX Бот...
start "MAX Бот" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python bot.py"
timeout /t 3 /nobreak >nul

echo [7/7] Утренний отчёт (анализ за вчера/сегодня)...
start "Утренний отчёт" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/morning_report.py"

echo.
echo Все 7 процессов запущены в отдельных окнах.
echo Окна НЕ закрывать (особенно TW Server, PbSdk Bridge, Snapshot Positions).
echo Окно "Утренний отчёт" можно закрыть крестиком после прочтения.
echo Браузер с вкладкой терминала НЕ закрывать - иначе встанет запись TW-эталона.
echo Закройте это окно-лаунчер, когда закончите работу.
pause