"""
Приблуда на python — планки (min/max возможная цена) по АКЦИЯМ TQBR.
Планки отдаёт АЛОР — MOEX ISS для акций TQBR их не отдаёт (подтверждено
зондом tools/probe_price_limits2.py). В Алоре это поля priceMax / priceMin
в описании инструмента: GET /md/v2/Securities/MOEX/{symbol}
(подтверждено зондом tools/probe_alor_security.py: SBER -> 295.83/249.17).
Текущая цена — из последней сделки (alltrades history, limit=1).
Это НЕ плотность и НЕ айсберг: планку ставит биржа, и пока её не расширят,
цена дальше не идёт — можно набраться и ждать расширения.
Обновляется фоном раз в PRICE_LIMITS_REFRESH_SEC. Access-токен Алора
получаем из refresh-токена (.env, ALOR_REFRESH_TOKEN) при каждом обновлении.
Примечание: сигнатура PriceLimitsCache(stock_symbols, fut_symbols=None) —
fut_symbols принят только для совместимости со старой сигнатурой в
live_screener.py; планки нужны лишь по акциям, фьючерсы игнорируются.
"""
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")

OAUTH_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"
EXCHANGE = "MOEX"
BOARD = "TQBR"
PRICE_LIMITS_REFRESH_SEC = 120   # планки меняются редко — раз в 2 минуты достаточно
NEAR_LIMIT_PCT = 0.005           # 0.5% до планки считаем "упёрся"


class PriceLimitsCache:
    def __init__(self, stock_symbols, fut_symbols=None):
        self.stock_symbols = list(stock_symbols)
        self.rows = []
        self.updated_at = None
        self._lock = asyncio.Lock()

    async def _get_access_token(self, session):
        async with session.post(OAUTH_URL, params={"token": REFRESH_TOKEN},
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        return payload["AccessToken"]

    async def _fetch_limits_one(self, session, access_token, symbol):
        """priceMax / priceMin из описания инструмента (планки)."""
        url = f"{API_URL}/md/v2/Securities/{EXCHANGE}/{symbol}"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(url, headers=headers,
                               params={"instrumentGroup": BOARD},
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        return payload.get("priceMax"), payload.get("priceMin")

    async def _fetch_last_price(self, session, access_token, symbol):
        """Текущая цена = цена последней сделки за последний час."""
        url = f"{API_URL}/md/v2/Securities/{EXCHANGE}/{symbol}/alltrades/history"
        headers = {"Authorization": f"Bearer {access_token}"}
        now = int(time.time())
        params = {
            "instrumentGroup": BOARD,
            "from": now - 3600,
            "to": now,
            "limit": 1,
            "offset": 0,
        }
        async with session.get(url, headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        trades = payload.get("list", [])
        if trades:
            return trades[-1].get("price")
        return None

    async def refresh(self):
        if not REFRESH_TOKEN:
            print("Планки: не найден ALOR_REFRESH_TOKEN в .env")
            return
        rows = []
        async with aiohttp.ClientSession() as session:
            try:
                access_token = await self._get_access_token(session)
            except Exception as e:
                print(f"Планки: не удалось получить токен: {type(e).__name__}: {e!r}")
                return
            for symbol in self.stock_symbols:
                try:
                    high, low = await self._fetch_limits_one(session, access_token, symbol)
                    last = await self._fetch_last_price(session, access_token, symbol)
                except Exception:
                    continue
                near_low = near_high = False
                if last is not None and low is not None and low > 0:
                    near_low = (last - low) / low <= NEAR_LIMIT_PCT
                if last is not None and high is not None and high > 0:
                    near_high = (high - last) / high <= NEAR_LIMIT_PCT
                rows.append({
                    "symbol": symbol,
                    "last": last,
                    "low_limit": low,
                    "high_limit": high,
                    "near_low": near_low,
                    "near_high": near_high,
                })
        rows.sort(key=lambda r: r["symbol"])
        async with self._lock:
            self.rows = rows
            self.updated_at = datetime.now()


async def price_limits_refresh_loop(cache):
    while True:
        try:
            await cache.refresh()
        except Exception as e:
            print(f"Ошибка обновления планок: {type(e).__name__}: {e!r}")
        await asyncio.sleep(PRICE_LIMITS_REFRESH_SEC)