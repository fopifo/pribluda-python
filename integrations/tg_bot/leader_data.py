"""
Приблуда на python — поводыри рынка (индекс Мосбиржи, RGBI, юань, доллар,
нефть). Кэш свечей на нескольких таймфреймах + полосы Боллинджера.
Источник — MOEX ISS candles API (бесплатно, без токена). Та же схема,
что в funding.py / news_moex.py: фоновый кэш, GUI читает готовое.
"""
import asyncio
import statistics
from datetime import datetime

import aiohttp

LEADERS = [
    ("IMOEXF", "futures", "forts", "RFUD", "Индекс Мосбиржи"),
    ("RGBIF", "futures", "forts", "RFUD", "RGBI облигации"),
    ("CNYRUBF", "futures", "forts", "RFUD", "Юань"),
    ("USDRUBF", "futures", "forts", "RFUD", "Доллар"),
    ("BR", "futures", "forts", "RFUD", "Нефть Brent"),
]

TIMEFRAMES = {"1м": 1, "10м": 10, "1ч": 60, "1д": 24}

BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
LEADER_REFRESH_SEC = 15
CANDLES_LIMIT = 120


def candles_url(engine, market, board, secid, interval, limit):
    return (
        f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
        f"/boards/{board}/securities/{secid}/candles.json"
        f"?interval={interval}&limit={limit}&iss.meta=off"
    )


def bollinger(closes, period=BOLLINGER_PERIOD, num_std=BOLLINGER_STD):
    n = len(closes)
    upper = [None] * n
    mid = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = statistics.fmean(window)
        sd = statistics.pstdev(window)
        mid[i] = m
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return upper, mid, lower


class LeaderCache:
    def __init__(self):
        self.candles = {}
        self.updated_at = None
        self._lock = asyncio.Lock()

    async def _refresh_one(self, session, secid, engine, market, board, interval):
        url = candles_url(engine, market, board, secid, interval, CANDLES_LIMIT)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        block = payload.get("candles", {})
        cols = block.get("columns", [])
        data = block.get("data", [])
        if not cols:
            return
        idx = {c: i for i, c in enumerate(cols)}
        candles = []
        for row in data:
            try:
                candles.append({
                    "open": row[idx["open"]],
                    "close": row[idx["close"]],
                    "high": row[idx["high"]],
                    "low": row[idx["low"]],
                    "begin": row[idx["begin"]],
                })
            except (KeyError, IndexError):
                continue
        async with self._lock:
            self.candles[(secid, interval)] = candles

    async def refresh_all(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            for secid, engine, market, board, _name in LEADERS:
                for interval in TIMEFRAMES.values():
                    tasks.append(self._refresh_one(session, secid, engine, market, board, interval))
            await asyncio.gather(*tasks, return_exceptions=True)
        self.updated_at = datetime.now()

    def get(self, secid, interval_label):
        return self.candles.get((secid, TIMEFRAMES[interval_label]), [])


async def leader_refresh_loop(cache):
    while True:
        try:
            await cache.refresh_all()
        except Exception as e:
            print(f"Ошибка обновления поводырей: {type(e).__name__}: {e!r}")
        await asyncio.sleep(LEADER_REFRESH_SEC)