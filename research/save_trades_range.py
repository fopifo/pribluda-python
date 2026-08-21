"""
Приблуда на python — докачка лент сделок с Алора за ДИАПАЗОН дат.
В отличие от save_trades.py (только последний торговый день), качает
все торговые дни (пн–пт) из заданного диапазона по всем активным
тикерам из ticker_settings.json.
ИДЕМПОТЕНТНО: если data/{SYMBOL}_{ДАТА}.json уже лежит — файл
пропускается (не скачивается и не перезаписывается). Значит, можно
спокойно запускать поверх имеющейся недели — дотащит только новое
и недостающие файлы. Сортировка по timestamp и формат файла — один
в один как в save_trades.py, чтобы run_all_dates.py и entry_backtest.py
читали без изменений.
Все даты — с часовым поясом Europe/Moscow.
v2: мигрирован на core.ticker_settings.
Запуск (из корня проекта):
python research/save_trades_range.py [start_date end_date]
Даты в формате ГГГГ-ММ-ДД. По умолчанию: с 2026-08-10 по последний
торговый день (пятница перед сегодня).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# v2: мигрировано с config.get_tracked_symbols на core.ticker_settings
from core.ticker_settings import load_settings

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
DEFAULT_START = "2026-08-10"
REQUEST_PAUSE_SEC = 0.2


def get_tracked_symbols() -> list[str]:
    """Активные тикеры из ticker_settings.json (замена устаревшего config.TRACKED_SYMBOLS)."""
    settings = load_settings()
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


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


def parse_day(raw: str) -> datetime:
    """Дата ГГГГ-ММ-ДД -> datetime С поясом Europe/Moscow."""
    return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)


def trading_days(start: datetime, end: datetime) -> list[datetime]:
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def previous_trading_day(reference: datetime) -> datetime:
    day = reference - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def day_range_unix(day: datetime) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=MOSCOW_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


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
        url = f"{API_URL}/md/v2/Securities/{EXCHANGE}/{symbol}/alltrades/history"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "instrumentGroup": BOARD,
            "from": date_from,
            "to": date_to,
            "limit": page_size,
            "offset": offset,
        }
        response = session.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("list", [])
        total = payload.get("total", 0)
        if not page:
            break
        all_trades.extend(page)
        offset += len(page)
        if offset >= total:
            break
        time.sleep(REQUEST_PAUSE_SEC)
    return all_trades


def save_trades_to_file(symbol: str, day: datetime, trades: list[dict]) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    trades_sorted = sorted(trades, key=lambda t: t["timestamp"])
    filepath = DATA_DIR / f"{symbol}_{day.date()}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(trades_sorted, f, ensure_ascii=False, indent=2)
    return filepath


def main() -> None:
    if not REFRESH_TOKEN or REFRESH_TOKEN == PLACEHOLDER:
        print("Не найден настоящий ALOR_REFRESH_TOKEN.")
        print(f"Ищу файл .env здесь: {ENV_PATH}")
        return
    symbols = get_tracked_symbols()
    if not symbols:
        print("В ticker_settings.json нет ни одного активного тикера.")
        return

    if len(sys.argv) == 3:
        start_day = parse_day(sys.argv[1])
        end_day = parse_day(sys.argv[2])
    else:
        start_day = parse_day(DEFAULT_START)
        end_day = previous_trading_day(datetime.now(MOSCOW_TZ))
    days = trading_days(start_day, end_day)
    print(f"Диапазон: {start_day.date()} — {end_day.date()}, "
          f"торговых дней: {len(days)}, тикеров: {len(symbols)}")

    session = make_session()
    try:
        access_token = get_access_token(session, REFRESH_TOKEN)
        print("Подключение успешно!")
    except requests.exceptions.RequestException as e:
        print(f"Не удалось получить access-токен: {e}")
        return

    downloaded = skipped = empty = failed = 0
    for day in days:
        date_from, date_to = day_range_unix(day)
        for symbol in symbols:
            filepath = DATA_DIR / f"{symbol}_{day.date()}.json"
            if filepath.exists():
                skipped += 1
                continue
            try:
                trades = get_all_trades_for_day(
                    session, access_token, symbol, date_from, date_to
                )
            except requests.exceptions.RequestException as e:
                print(f"  {symbol} {day.date()}: ошибка сети/API: {e}")
                failed += 1
                continue
            if not trades:
                empty += 1
                continue
            save_trades_to_file(symbol, day, trades)
            downloaded += 1
            size_mb = filepath.stat().st_size / (1024 * 1024)
            print(f"  {symbol} {day.date()}: {len(trades)} сделок ({size_mb:.1f} МБ)")
            time.sleep(REQUEST_PAUSE_SEC)
    print(f"\nГотово: скачано {downloaded}, уже было {skipped}, "
          f"пустых {empty}, ошибок {failed}.")


if __name__ == "__main__":
    main()