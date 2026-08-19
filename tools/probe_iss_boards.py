"""
Приблуда на python — разовый зонд площадок MOEX ISS для разных типов
инструментов трёхногого арбитража: валюта, фьючерсы на валюту, металлы.
Нужно понять, какая площадка (engine/market/board) отдаёт свечи для
каждого типа, чтобы board_for_symbol в ticker_chart_data.py работал
честно для всех ног арбитража, а не только для акций.
Запуск (из корня проекта):
python tools/probe_iss_boards.py
"""
import requests

# (описание, engine, market, board, symbol). board=None — пробуем без board.
CANDIDATES = [
    ("фьючерс на валюту USDRUBF", "futures", "forts", "RFUD", "USDRUBF"),
    ("фьючерс на валюту CNYRUBF", "futures", "forts", "RFUD", "CNYRUBF"),
    ("фьючерс на золото  GLDRUBF", "futures", "forts", "RFUD", "GLDRUBF"),
    ("фьючерс на серебро SVRUBF", "futures", "forts", "RFUD", "SVRUBF"),
    ("спот валюда USDRUB (index)", "currency", "index", None, "USDRUB"),
    ("спот валюта USDRUB (selt)", "currency", "selt", "CETS", "USDRUB"),
    ("спот валюта CNYRUB (index)", "currency", "index", None, "CNYRUB"),
]

for desc, engine, market, board, symbol in CANDIDATES:
    if board:
        url = (
            f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
            f"/boards/{board}/securities/{symbol}/candles.json"
            "?interval=1&limit=3&iss.meta=off"
        )
    else:
        url = (
            f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
            f"/securities/{symbol}/candles.json?interval=1&limit=3&iss.meta=off"
        )
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            print(f"{desc} [{engine}/{market}/{board or '-'}]: HTTP {r.status_code}")
            continue
        candles = r.json().get("candles", {}).get("data", [])
        status = f"свечей: {len(candles)}"
        if candles:
            status += f"  первая: {candles[0]}"
        print(f"{desc} [{engine}/{market}/{board or '-'}]: {status}")
    except Exception as e:
        print(f"{desc} [{engine}/{market}/{board or '-'}]: ОШИБКА {type(e).__name__}: {e}")