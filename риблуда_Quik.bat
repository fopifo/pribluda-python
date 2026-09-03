@echo off
rem v3 (2026-09-03): iss_quotes_sync + tw_server + main.py together
cd /d "%~dp0"
start /min "ISS_Quotes" python.exe tools/iss_quotes_sync.py
start /min "TW_Server" python.exe tools/tw_server.py
start /min "Pribluda_Quik" python.exe main.py --source quik
