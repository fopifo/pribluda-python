"""
Приблуда на python — разовый зонд свечей MOEX ISS для АКЦИЙ TQBR.
Подтверждаем формат 1-минутных свечей перед тем, как строить график
спреда для арбитража (Алор свечи через REST не отдал — 404).
Формат для фьючерсов уже подтверждён в tg_bot/leader_data.py; здесь
проверяем, что тот же формат и у акций.
Запуск (из корня проекта):
python tools/probe_iss_candles.py
"""
import requests

SYMBOL = "MTLR"
URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR"
    f"/securities/{SYMBOL}/candles.json?interval=1&limit=5&iss.meta=off"
)

response = requests.get(URL, timeout=10)
print(f"HTTP {response.status_code}")
if response.status_code != 200:
    print(f"тело: {response.text[:300]}")
    raise SystemExit(1)

payload = response.json()
block = payload.get("candles", {})
print("Колонки:", block.get("columns"))
data = block.get("data", [])
print(f"Отдано свечей: {len(data)}")
for row in data[:5]:
    print(" ", row)