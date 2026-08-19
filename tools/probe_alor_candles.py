"""
Приблуда на python — разовый зонд свечного API Алора.
Ищем: (1) какой формат параметра tf принимает /md/v2/Securities/.../history
(число секунд, "M1"/"M5", и т.п.), (2) как выглядит свеча в ответе.
Не гадаем — сначала смотрим реальный ответ, потом пишем график спреда.
Запуск (из корня проекта):
python tools/probe_alor_candles.py
"""
import os
import sys
import time
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

now = int(time.time())
hour_ago = now - 3600

# Пробуем разные форматы tf, чтобы понять, что принимает Алор.
TF_VARIANTS = [60, "60", "M1", "m1", 300, "M5", "H1"]

for tf in TF_VARIANTS:
    url = f"{API_URL}/md/v2/Securities/MOEX/{SYMBOL}/history"
    params = {
        "tf": tf,
        "from": hour_ago,
        "to": now,
        "instrumentGroup": "TQBR",
    }
    print("=" * 70)
    print(f"Пробую tf={tf!r} ...")
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=10)
        print(f"  HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  тело: {r.text[:300]}")
            continue
        payload = r.json()
        if isinstance(payload, list) and payload:
            print(f"  Отдано свечей: {len(payload)}")
            print(f"  Первая свеча: {payload[0]}")
            print(f"  Последняя свеча: {payload[-1]}")
        else:
            print(f"  Пусто или не список: {str(payload)[:300]}")
    except Exception as e:
        print(f"  ОШИБКА: {type(e).__name__}: {e}")