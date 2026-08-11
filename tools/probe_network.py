"""
Приблуда на python — разовая проверка сетевой доступности внешних
сервисов (MOEX ISS, Telegram), которые падали в live-режиме с неясной
ошибкой. Простой синхронный requests — без наших асинхронных обёрток,
чтобы увидеть настоящую причину сбоя без маскировки.
"""

import requests

TARGETS = {
    "MOEX ISS (sitenews)": "https://iss.moex.com/iss/sitenews.json?limit=1",
    "Telegram API (домен)": "https://api.telegram.org",
    "Telegram API (по IP)": "https://149.154.167.220",
}

for name, url in TARGETS.items():
    print(f"\n=== {name} ===")
    print(f"URL: {url}")
    try:
        response = requests.get(url, timeout=10)
        print(f"OK: статус {response.status_code}, получено {len(response.content)} байт")
    except requests.exceptions.SSLError as e:
        print(f"ОШИБКА SSL: {e}")
    except requests.exceptions.ConnectTimeout as e:
        print(f"ОШИБКА: таймаут подключения (сеть не пускает / фильтрует): {e}")
    except requests.exceptions.ConnectionError as e:
        print(f"ОШИБКА соединения: {e}")
    except Exception as e:
        print(f"ОШИБКА ({type(e).__name__}): {e}")