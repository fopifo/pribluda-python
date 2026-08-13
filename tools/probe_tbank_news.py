"""
Приблуда на python — разовая проверка: как в SDK tinkoff-investments
называется метод получения новостей. Не гадаем на неподтверждённом API
(та же дисциплина, что и с probe_price_limits.py для планок) — сначала
смотрим, что реально доступно, потом пишем рабочий код.

Требует: pip install tinkoff-investments --break-system-packages
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("TBANK_TOKEN_READONLY")

if not TOKEN:
    print("Не найден TBANK_TOKEN_READONLY в .env")
    raise SystemExit(1)

from tinkoff.invest import Client

with Client(TOKEN) as client:
    print("=== Подключение установлено ===\n")

    print("=== Сервисы, доступные у client (без служебных __...) ===")
    for name in sorted(dir(client)):
        if not name.startswith("_"):
            print(f"  client.{name}")

    # Пробуем несколько вероятных мест, где может жить работа с новостями.
    for candidate in ("news", "instruments"):
        service = getattr(client, candidate, None)
        if service is None:
            continue
        print(f"\n=== Методы client.{candidate} (без служебных __...) ===")
        for name in sorted(dir(service)):
            if not name.startswith("_"):
                print(f"  client.{candidate}.{name}")