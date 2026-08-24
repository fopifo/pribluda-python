@echo off
rem v2 (2026-08-24): start iss_quotes_sync + main.py together
cd /d "%~dp0"
start /min "ISS_Quotes" python.exe tools/iss_quotes_sync.py
start /min "Pribluda_Quik" python.exe main.py --source quik