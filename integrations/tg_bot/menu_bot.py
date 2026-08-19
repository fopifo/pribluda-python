"""
Приблуда на python — интерактивное меню Telegram-бота.

Может запускаться как отдельный процесс (python tg_bot/menu_bot.py) для
отладки, но в постоянной работе запускается через start_all.py вместе
со всем остальным проектом — НЕ держи одновременно два живых экземпляра
(и через start_all.py, и напрямую), иначе оба будут опрашивать Telegram
и отвечать на одни и те же нажатия по два раза.

Обход DNS/маршрутизации: как и в tg_bot/bot.py, прямое подключение по
домену api.telegram.org в этой сети иногда не проходит — при неудаче
пробуем резервные IP с заголовком Host (тот же список, что и в bot.py).

Отвечает ТОЛЬКО в чат из TELEGRAM_CHAT_ID (.env).
"""

import asyncio
import os
import re
import ssl
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv

from funding import FundingCache, funding_refresh_loop
from news_moex import NewsCache, news_refresh_loop

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
OUTPUT_DIR = BASE_DIR / "output"

load_dotenv(ENV_PATH)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

TELEGRAM_IPS = [
    "149.154.167.220",
    "149.154.167.221",
    "149.154.167.222",
]

BTN_ARB = "⚖️ Арбитраж"
BTN_FUNDING = "💰 Фандинг"
BTN_NEWS = "📰 Новости"

MAIN_MENU = {
    "keyboard": [[BTN_ARB, BTN_FUNDING], [BTN_NEWS]],
    "resize_keyboard": True,
}

ARB_LOG_PATTERN = re.compile(r"РАСХОЖДЕНИЕ")


def make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def telegram_api_call(
    session: aiohttp.ClientSession,
    method: str,
    params: dict | None = None,
    json_body: dict | None = None,
    http_method: str = "GET",
) -> dict | None:
    """Вызов метода Telegram Bot API с обходом через IP, если домен не
    отвечает. Возвращает распарсенный JSON или None, если не получилось
    ни через домен, ни через один из резервных IP — в этом случае
    печатает ПОСЛЕДНЮЮ реальную ошибку, а не просто "не получилось"."""
    domain_url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    timeout = aiohttp.ClientTimeout(total=35 if http_method == "GET" else 15)
    last_error: Exception | None = None

    try:
        if http_method == "GET":
            async with session.get(domain_url, params=params, timeout=timeout) as resp:
                return await resp.json(content_type=None)
        else:
            async with session.post(domain_url, json=json_body, timeout=timeout) as resp:
                return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        last_error = e

    headers = {"Host": "api.telegram.org"}
    for ip in TELEGRAM_IPS:
        ip_url = f"https://{ip}/bot{TOKEN}/{method}"
        try:
            if http_method == "GET":
                async with session.get(ip_url, params=params, headers=headers, timeout=timeout) as resp:
                    return await resp.json(content_type=None)
            else:
                async with session.post(ip_url, json=json_body, headers=headers, timeout=timeout) as resp:
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            continue

    print(f"Telegram API [{method}]: не удалось ни через домен, ни через IP. Последняя ошибка: {last_error!r}")
    return None


async def send_message(
    session: aiohttp.ClientSession,
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = await telegram_api_call(session, "sendMessage", json_body=payload, http_method="POST")
    if result is None or not result.get("ok", False):
        print(f"Ошибка отправки сообщения: {result}")


def find_today_log() -> Path | None:
    today = datetime.now(MOSCOW_TZ).date()
    path = OUTPUT_DIR / f"live_signals_{today}.txt"
    return path if path.exists() else None


def read_recent_arb_events(limit: int = 10) -> str:
    log_path = find_today_log()
    if log_path is None:
        return "Лог за сегодня ещё не создан."

    matches = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            if ARB_LOG_PATTERN.search(line):
                matches.append(line.strip())

    if not matches:
        return "Сегодня расхождений по арбитражным связкам ещё не было."

    recent = matches[-limit:]
    return "<b>⚖️ Последние расхождения:</b>\n\n" + "\n".join(recent)


async def handle_update(
    session: aiohttp.ClientSession,
    update: dict,
    funding_cache: FundingCache,
    news_cache: NewsCache,
) -> None:
    message = update.get("message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    if CHAT_ID and chat_id != str(CHAT_ID):
        return

    text = message.get("text", "")

    if text in ("/start", "/menu"):
        await send_message(session, chat_id, "Выбери раздел:", reply_markup=MAIN_MENU)
        return

    if text == BTN_ARB:
        await send_message(session, chat_id, read_recent_arb_events())
        return

    if text == BTN_FUNDING:
        await send_message(session, chat_id, funding_cache.format_message())
        return

    if text == BTN_NEWS:
        await send_message(session, chat_id, news_cache.format_message())
        return


async def polling_loop(funding_cache: FundingCache, news_cache: NewsCache) -> None:
    offset = 0
    ssl_context = make_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            result = await telegram_api_call(
                session, "getUpdates", params={"timeout": 30, "offset": offset}
            )

            if result is None:
                await asyncio.sleep(5)
                continue

            if not result.get("ok", False):
                print(f"Telegram вернул ошибку: {result}")
                await asyncio.sleep(5)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                await handle_update(session, update, funding_cache, news_cache)


async def main() -> None:
    if not TOKEN or not CHAT_ID:
        print("Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env")
        return

    funding_cache = FundingCache()
    news_cache = NewsCache()

    funding_task = asyncio.create_task(funding_refresh_loop(funding_cache))
    news_task = asyncio.create_task(news_refresh_loop(news_cache))
    polling_task = asyncio.create_task(polling_loop(funding_cache, news_cache))

    print("Меню-бот запущен, жду команды /start в Telegram...")
    await asyncio.gather(funding_task, news_task, polling_task)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass