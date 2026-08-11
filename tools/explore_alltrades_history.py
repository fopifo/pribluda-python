"""
Приблуда на python — разведочный запрос истории сделок (тиковой ленты)
по одному инструменту. Справочный/архивный инструмент, в рабочем цикле
проекта не используется (его роль выполняют save_trades.py +
run_detectors.py/live_screener.py) — оставлен для ручных проверок API.

Метод: /md/v2/Securities/{exchange}/{symbol}/alltrades/history
Отдаёт сделки ТОЛЬКО за прошлые торговые сессии (не за сегодня).
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
OAUTH_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"
PLACEHOLDER = "вставь_сюда_свой_refresh_token"

BOARD = "TQBR"
EXCHANGE = "MOEX"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

TEST_SYMBOL = "SBER"


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_access_token(session: requests.Session, refresh_token: str) -> str:
    response = session.post(OAUTH_URL, params={"token": refresh_token})
    response.raise_for_status()
    return response.json()["AccessToken"]


def previous_trading_day(reference: datetime) -> datetime:
    day = reference - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def day_range_unix(day: datetime) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=MOSCOW_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def get_alltrades_history_page(
    session: requests.Session,
    access_token: str,
    symbol: str,
    date_from: int,
    date_to: int,
    offset: int = 0,
    limit: int = 50000,
) -> dict:
    url = f"{API_URL}/md/v2/Securities/{EXCHANGE}/{symbol}/alltrades/history"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "instrumentGroup": BOARD,
        "from": date_from,
        "to": date_to,
        "limit": limit,
        "offset": offset,
    }
    response = session.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def get_all_trades_for_day(
    session: requests.Session,
    access_token: str,
    symbol: str,
    date_from: int,
    date_to: int,
    page_size: int = 50000,
) -> list[dict]:
    all_trades: list[dict] = []
    offset = 0

    while True:
        payload = get_alltrades_history_page(
            session, access_token, symbol, date_from, date_to,
            offset=offset, limit=page_size,
        )
        page = payload.get("list", [])
        total = payload.get("total", 0)

        if offset == 0:
            print(f"Всего сделок за день (по данным Алора): {total}")

        if not page:
            break

        all_trades.extend(page)
        offset += len(page)
        print(f"  загружено {offset} из {total}...")

        if offset >= total:
            break

    return all_trades


def main() -> None:
    if not REFRESH_TOKEN or REFRESH_TOKEN == PLACEHOLDER:
        print("Не найден настоящий ALOR_REFRESH_TOKEN.")
        print(f"Ищу файл .env здесь: {ENV_PATH}")
        return

    session = make_session()

    try:
        access_token = get_access_token(session, REFRESH_TOKEN)
        print("Подключение успешно!")
    except requests.exceptions.RequestException as e:
        print(f"Не удалось получить access-токен: {e}")
        return

    target_day = previous_trading_day(datetime.now(MOSCOW_TZ))
    date_from, date_to = day_range_unix(target_day)
    print(f"Последний торговый день (по МСК): {target_day.date()}")
    print(f"Запрашиваю ленту сделок по {TEST_SYMBOL} за этот день...")

    try:
        trades = get_all_trades_for_day(
            session, access_token, TEST_SYMBOL, date_from, date_to
        )
    except requests.exceptions.HTTPError as e:
        print(f"Алор вернул ошибку: {e}")
        print(f"Тело ответа: {e.response.text}")
        return
    except requests.exceptions.RequestException as e:
        print(f"Не удалось связаться с Алор: {e}")
        return

    print(f"\nИтого сделок собрано: {len(trades)}")
    if not trades:
        return

    print("\nПервая сделка:")
    print(json.dumps(trades[0], ensure_ascii=False, indent=2))
    print("\nПоследняя сделка:")
    print(json.dumps(trades[-1], ensure_ascii=False, indent=2))

    all_keys = set()
    for t in trades:
        all_keys.update(t.keys())
    print(f"\nВсе поля сделки: {sorted(all_keys)}")

    from collections import Counter
    qty_counts = Counter(t["qty"] for t in trades)
    top_qtys = qty_counts.most_common(10)
    print("\nСамые частые объёмы (qty) за день:")
    for qty, count in top_qtys:
        print(f"  {qty} лотов — встретился {count} раз(а)")


if __name__ == "__main__":
    main()