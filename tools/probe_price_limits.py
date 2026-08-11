"""
Приблуда на python — разовая проверка: под каким именем MOEX ISS отдаёт
границы цены (планки) по акции. Не часть рабочего цикла — запускается
один раз вручную, чтобы понять реальный формат ответа перед тем, как
писать постоянный код для раздела "Планки".
"""

import requests

SYMBOL = "SBER"
URL = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{SYMBOL}.json?iss.meta=off"

response = requests.get(URL, timeout=10)
response.raise_for_status()
payload = response.json()

for block_name, block in payload.items():
    columns = block.get("columns", [])
    print(f"\n=== Блок: {block_name} ===")
    print("Колонки:", columns)
    if block.get("data"):
        print("Первая строка:", block["data"][0])