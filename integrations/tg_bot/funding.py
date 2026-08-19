"""
Приблуда на python — фандинг по срочному рынку (FORTS), MOEX ISS API.

Держим локальный кэш последних значений SWAPRATE (ставка фандинга) по
списку контрактов, обновляем в фоне раз в FUNDING_REFRESH_SEC секунд.
Кнопка "Фандинг" в Telegram-меню отдаёт кэш мгновенно, не дожидаясь
живого запроса к MOEX на каждое нажатие.
"""

import asyncio
from datetime import datetime

import aiohttp

# ВАЖНО: без ".json" в пути MOEX ISS по умолчанию отдаёт XML, а не JSON,
# даже если в query-параметрах ничего про формат не сказано.
FUNDING_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
    "?securities=USDRUBF,EURRUBF,CNYRUBF,SBERF,GAZPF,GLDRUBF,SLVRUBF,IMOEXF,RGBIF"
    "&iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,SWAPRATE"
)

FUNDING_REFRESH_SEC = 60

# Человекочитаемые названия — чтобы не показывать голые тикеры фьючерсов.
NAMES = {
    "USDRUBF": "USD/RUB",
    "EURRUBF": "EUR/RUB",
    "CNYRUBF": "CNY/RUB",
    "SBERF": "SBER",
    "GAZPF": "GAZP",
    "GLDRUBF": "Золото",
    "SLVRUBF": "Серебро",
    "IMOEXF": "IMOEX",
    "RGBIF": "RGBI",
}


class FundingCache:
    def __init__(self):
        self.rows: list[dict] = []
        self.updated_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(FUNDING_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                # content_type=None — не привередничать насчёт заголовка
                # Content-Type в ответе, ISS иногда отдаёт его не совсем
                # точно, а тело всё равно валидный JSON.
                payload = await resp.json(content_type=None)

        market_data = payload.get("marketdata", {})
        columns = market_data.get("columns", [])
        data = market_data.get("data", [])

        try:
            secid_idx = columns.index("SECID")
            swap_idx = columns.index("SWAPRATE")
        except ValueError:
            print(f"Фандинг: неожиданный формат ответа MOEX ISS, пропускаю обновление: {payload}")
            return

        rows = []
        for row in data:
            secid = row[secid_idx]
            swap_rate = row[swap_idx]
            if secid is None:
                continue
            rows.append({"secid": secid, "swap_rate": swap_rate})

        async with self._lock:
            self.rows = rows
            self.updated_at = datetime.now()

    def format_message(self) -> str:
        if not self.rows:
            return "Данные по фандингу ещё не загружены, попробуйте через минуту."

        lines = ["<b>💰 ФАНДИНГ (FORTS)</b>", ""]
        for row in self.rows:
            name = NAMES.get(row["secid"], row["secid"])
            rate = row["swap_rate"]
            rate_str = f"{rate:.4f}%" if isinstance(rate, (int, float)) else "н/д"
            lines.append(f"<b>{name}</b>: {rate_str}")

        if self.updated_at:
            lines.append("")
            lines.append(f"🕐 Обновлено: {self.updated_at.strftime('%H:%M:%S')}")

        return "\n".join(lines)


async def funding_refresh_loop(cache: FundingCache) -> None:
    while True:
        try:
            await cache.refresh()
        except Exception as e:
            print(f"Ошибка обновления фандинга: {type(e).__name__}: {e!r}")
        await asyncio.sleep(FUNDING_REFRESH_SEC)