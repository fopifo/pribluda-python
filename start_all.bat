@echo off
chcp 65001 >nul
echo ==========================================
echo   Запуск: Приблуда + MAX Бот + PbSdk мост + T-Widgets сервер
echo ==========================================
echo.

echo [1/4] Запуск T-Widgets сервера (приёмник данных)...
start "TW Server" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python tools/tw_server.py"

timeout /t 2 /nobreak >nul

echo [2/4] Запуск Приблуды...
start "Приблуда (Main)" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python main.py"

timeout /t 2 /nobreak >nul

echo [3/4] Запуск MAX Бота...
start "MAX Бот" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder && python bot.py"

timeout /t 2 /nobreak >nul

echo [4/4] Запуск PbSdk моста (лента+стакан)...
start "PbSdk Bridge" cmd /k "cd /d C:\Users\Public\MI_CODES\Qwen_coder\PbSdkConsole_Project && dotnet run --project PbSdkConsole"

echo.
echo Все четыре процесса запущены в отдельных окнах.
echo Закройте это окно, когда закончите работу.
pause