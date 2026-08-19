"""
Приблуда на python — разовый зонд: ищем, в каких полях MOEX ISS отдаёт
планки (min/max возможная цена) по акциям и фьючерсам. Не гадаем —
сначала смотрим реальные колонки, потом пишем окно "Планки".
Запуск (из корня проекта):
python tools/probe_price_limits2.py
"""
import requests

TARGETS = [
    ("stock", "shares", "TQBR", "SBER"),
    ("stock", "shares", "TQBR", "VTBR"),
    ("futures", "forts", "RFUD", "SBERF"),
]

KEYWORDS = ("PRICE", "LIMIT", "MAX", "MIN", "STEP", "BOUND")

for engine, market, board, sec in TARGETS:
    url = (
        f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
        f"/boards/{board}/securities/{sec}.json?iss.meta=off"
    )
    print("=" * 70)
    print(f"{engine}/{market}/{board} {sec}")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"  ОШИБКА: {type(e).__name__}: {e}")
        continue
    for block_name, block in payload.items():
        cols = block.get("columns", [])
        if not cols:
            continue
        hits = [c for c in cols if c and any(k in c.upper() for k in KEYWORDS)]
        if hits:
            print(f"  блок '{block_name}': поля, похожие на планки: {hits}")
            if block.get("data"):
                row = block["data"][0]
                pairs = {c: row[i] for i, c in enumerate(cols) if c in hits}
                print(f"     значения: {pairs}")