"""
Приблуда на python — новости с сайта MOEX (ISS API, /iss/sitenews).

Та же схема, что и funding.py: фоновый кэш, обновляется раз в
NEWS_REFRESH_SEC секунд, кнопка "Новости" отдаёт кэш мгновенно.

Формат ответа подтверждён реальным запросом (см. tools/probe_network.py):
колонки — id, tag, title, published_at, modified_at. Отдельного поля со
ссылкой на новость MOEX не отдаёт — ссылка строится по шаблону
https://www.moex.com/n{id}, который подтверждён на реальном примере
новости биржи.
"""

import asyncio
from datetime import datetime

import aiohttp

NEWS_URL = "https://iss.moex.com/iss/sitenews.json?limit=10&iss.meta=off"
NEWS_REFRESH_SEC = 300  # новости не такие срочные, как сделки — раз в 5 минут достаточно


class NewsCache:
    def __init__(self):
        self.items: list[dict] = []
        self.updated_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._warned_format = False

    async def refresh(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(NEWS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)

        block = payload.get("sitenews", {})
        columns = block.get("columns", [])
        data = block.get("data", [])

        if not columns and not self._warned_format:
            print(f"Новости MOEX: неожиданный формат ответа: {payload}")
            self._warned_format = True
            return

        rows = [dict(zip(columns, row)) for row in data]

        async with self._lock:
            self.items = rows
            self.updated_at = datetime.now()

    def _extract_title(self, row: dict) -> str:
        title = row.get("title")
        return str(title) if title else "(без заголовка)"

    def _extract_time(self, row: dict) -> str:
        published = row.get("published_at")
        return str(published) if published else ""

    def _extract_url(self, row: dict) -> str | None:
        news_id = row.get("id")
        if news_id is None:
            return None
        return f"https://www.moex.com/n{news_id}"

    def format_message(self, limit: int = 8) -> str:
        if not self.items:
            return "Новости MOEX ещё не загружены, попробуйте через несколько минут."

        lines = ["<b>📰 НОВОСТИ MOEX</b>", ""]
        for row in self.items[:limit]:
            title = self._extract_title(row)
            time_str = self._extract_time(row)
            url = self._extract_url(row)
            line = f"• {title}"
            if time_str:
                line += f" <i>({time_str})</i>"
            lines.append(line)
            if url:
                lines.append(f"  {url}")

        if self.updated_at:
            lines.append("")
            lines.append(f"🕐 Обновлено: {self.updated_at.strftime('%H:%M:%S')}")

        return "\n".join(lines)


async def news_refresh_loop(cache: NewsCache) -> None:
    while True:
        try:
            await cache.refresh()
        except Exception as e:
            print(f"Ошибка обновления новостей MOEX: {type(e).__name__}: {e!r}")
        await asyncio.sleep(NEWS_REFRESH_SEC)