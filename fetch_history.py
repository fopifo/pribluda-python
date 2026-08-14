"""
Приблуда на python — скачивание ленты сделок за указанный диапазон дат.

Пример:
    python fetch_history.py 2026-08-10 2026-08-14

Если даты не указаны, качает последние 5 торговых дней.
Файлы сохраняются в data/{SYMBOL}_{ДАТА}.json.
Уже скачанные файлы пропускаются.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import get_tracked_symbols

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
OAUTH_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"
PLACEHOLDER = "вставь_сюда_свой_refresh_token"

BOARD = "TQBR"
EXCHANGE = "MOEX"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


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


def trading_days_between(start: datetime, end: datetime) -> list[datetime]:
    """Возвращает список торговых дней (будние дни) в диапазоне [start, end]."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


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

        if not page:
            break

        all_trades.extend(page)
        offset += len(page)

        if offset >= total:
            break

    return all_trades


def save_trades_to_file(symbol: str, day: datetime, trades: list[dict]) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    trades_sorted = sorted(trades, key=lambda t: t["timestamp"])

    filename = f"{symbol}_{day.date()}.json"
    filepath = DATA_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(trades_sorted, f, ensure_ascii=False, indent=2)

    return filepath


def main() -> None:
    if len(sys.argv) >= 3:
        try:
            start = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)
            end = datetime.strptime(sys.argv[2], "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)
        except ValueError:
            print("Формат дат: YYYY-MM-DD")
            return
    else:
        # По умолчанию последние 5 торговых дней
        end = datetime.now(MOSCOW_TZ)
        start = end - timedelta(days=10)  # запас, потом обрежем
        # Получим последние 5 торговых дней
        days = []
        cur = end
        while len(days) < 5:
            if cur.weekday() < 5:
                days.append(cur)
            cur -= timedelta(days=1)
        start = min(days)
        end = max(days)

    trading_days = trading_days_between(start, end)
    if not trading_days:
        print("В указанном диапазоне нет торговых дней.")
        return

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

    symbols = get_tracked_symbols()
    print(f"Активных тикеров: {len(symbols)}")
    print(f"Будут скачаны данные за торговые дни: {', '.join(d.date().isoformat() for d in trading_days)}\n")

    for day in trading_days:
        print(f"=== День {day.date()} ===")
        date_from, date_to = day_range_unix(day)

        for symbol in symbols:
            filepath = DATA_DIR / f"{symbol}_{day.date()}.json"
            if filepath.exists():
                print(f"  {symbol}: файл уже есть, пропускаю.")
                continue

            print(f"Запрашиваю ленту сделок по {symbol}...")
            try:
                trades = get_all_trades_for_day(
                    session, access_token, symbol, date_from, date_to
                )
            except requests.exceptions.HTTPError as e:
                print(f"  Алор вернул ошибку: {e}")
                continue
            except requests.exceptions.RequestException as e:
                print(f"  Не удалось связаться с Алор: {e}")
                continue

            if not trades:
                print(f"  Сделок не найдено для {symbol} за {day.date()} — пропускаю.\n")
                continue

            saved_path = save_trades_to_file(symbol, day, trades)
            size_mb = saved_path.stat().st_size / (1024 * 1024)
            print(f"  Сохранено {len(trades)} сделок в {saved_path} ({size_mb:.1f} МБ)\n")

    print("Готово. Все данные скачаны.")


if __name__ == "__main__":
    main()