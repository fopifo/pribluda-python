"""
Приблуда на python — 1-минутные свечи произвольного тикера (MOEX ISS).
Используется modules/arbitrage/spread_chart_data.py для ног спреда.
Честный перебор площадок: сначала акции (TQBR/TQOD/TQNL), затем фьючерсы (RFUD).
v2: рынок фьючерсов "forts" (было "fortis" — фьючерсы не находились).
"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

MSK = ZoneInfo("Europe/Moscow")
_STOCK_BOARDS = ["TQBR", "TQOD", "TQNL"]
_FUT_BOARDS = ["RFUD"]
_PAGE = 500


def _days_back_for_tf(tf_minutes: int) -> int:
    if tf_minutes <= 15:
        return 2
    if tf_minutes <= 60:
        return 5
    if tf_minutes <= 240:
        return 15
    return 30


def parse_begin(begin) -> datetime:
    s = str(begin)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return datetime.fromisoformat(s).replace(tzinfo=MSK)


def _fetch_board(engine: str, market: str, board: str, sec: str, days_back: int):
    url = (f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
           f"/boards/{board}/securities/{sec}/candles.json")
    frm = (datetime.now(MSK) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    out = []
    start = 0
    while True:
        try:
            r = requests.get(url, params={"interval": 1, "from": frm,
                                          "start": start, "iss.meta": "off"},
                             timeout=30)
        except requests.RequestException:
            break
        if r.status_code != 200:
            break
        try:
            block = r.json().get("candles", {})
        except ValueError:
            break
        cols = block.get("columns", [])
        rows = block.get("data", [])
        if not cols or not rows:
            break
        idx = {c: i for i, c in enumerate(cols)}
        for row in rows:
            out.append({"begin": row[idx["begin"]], "open": row[idx["open"]],
                        "high": row[idx["high"]], "low": row[idx["low"]],
                        "close": row[idx["close"]]})
        if len(rows) < _PAGE:
            break
        start += len(rows)
        time.sleep(0.05)
    return out


def fetch_candles_1min(ticker: str, days_back: int) -> list[dict]:
    for board in _STOCK_BOARDS:
        c = _fetch_board("stock", "shares", board, ticker, days_back)
        if c:
            return c
    for board in _FUT_BOARDS:
        c = _fetch_board("futures", "forts", board, ticker, days_back)  # v2: forts
        if c:
            return c
    return []