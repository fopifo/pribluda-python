@echo off
chcp 65001 >nul
echo ==========================================
echo   Запуск: TW Server + Chrome + Мост + Снапшоты + Приблуда + MAX Бот + Отчёт
echo   Рабочая папка: C:\Users\Public\MI_CODES\dpNEW
echo ==========================================
echo.

:: Устанавливаем корневую папку проекта
set "PROJECT_DIR=C:\Users\Public\MI_CODES\dpNEW"

echo [1/7] TW Server (приёмник роботов T-Widgets)...
start "TW Server" cmd /k "cd /d %PROJECT_DIR% && python tools/tw_server.py"
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

echo [3/7] PbSdk мост (лента+стакан+позиции)...
:: ВНИМАНИЕ: Если проект PbSdkConsole тоже перенесен в dpNEW, измени путь ниже на %PROJECT_DIR%\PbSdkConsole_Project
start "PbSdk Bridge" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder\PbSdkConsole_Project && dotnet run --project PbSdkConsole"
timeout /t 8 /nobreak >nul

echo [4/7] Снапшоты позиций (каждые 5 мин + финал 18:50)...
start "Snapshot Positions" cmd /k "cd /d %PROJECT_DIR% && python tools/snapshot_positions.py --loop"
timeout /t 2 /nobreak >nul

echo [5/7] Приблуда (Main) -- источник: мост (bridge)...
start "Приблуда (Main)" cmd /k "cd /d %PROJECT_DIR% && python main.py --source bridge"
timeout /t 2 /nobreak >nul

echo [6/7] MAX Бот...
start "MAX Бот" cmd /k "cd /d %PROJECT_DIR% && python bot.py"
timeout /t 3 /nobreak >nul

echo [7/7] Утренний отчёт (анализ за вчера/сегодня)...
start "Утренний отчёт" cmd /k "cd /d %PROJECT_DIR% && python tools/morning_report.py"

echo.
echo ==========================================
echo  Все 7 процессов запущены в отдельных окнах.
echo  Данные будут записываться в: %PROJECT_DIR%\data\
echo  Окна НЕ закрывать (особенно TW Server, PbSdk Bridge, Snapshot Positions).
echo ==========================================
pause