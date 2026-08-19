"""
Приблуда на python — разовый зонд: какие поля отдаёт Алор в описании
инструмента (GET /md/v2/Securities/MOEX/{symbol}). Ищем, под каким
именем там лежат планки (min/max цена) по АКЦИЯМ TQBR — ISS их не
отдаёт, поэтому идём через Алор. Не часть рабочего цикла.
Запуск (из корня проекта):
python tools/probe_alor_security.py
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")
REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")

OAUTH_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"
SYMBOL = "SBER"

if not REFRESH_TOKEN:
    print("Не найден ALOR_REFRESH_TOKEN в .env")
    raise SystemExit(1)

resp = requests.post(OAUTH_URL, params={"token": REFRESH_TOKEN}, timeout=10)
resp.raise_for_status()
token = resp.json()["AccessToken"]

url = f"{API_URL}/md/v2/Securities/MOEX/{SYMBOL}"
r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                 params={"instrumentGroup": "TQBR"}, timeout=10)
r.raise_for_status()
payload = r.json()
print(f"Поля описания инструмента {SYMBOL}:")
for key, value in payload.items():
    print(f"  {key}: {value}")