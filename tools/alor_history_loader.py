"""
Загрузчик истории тиковых сделок (alltrades) с API Алор.

Использует refresh-токен для получения access-токена, который затем
применяется в запросах к marketdata API. Access-токен обновляется
при истечении срока действия.

Переменные окружения (.env):
    ALOR_REFRESH_TOKEN — refresh-токен для авторизации в Алор
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
TOKEN_URL = "https://api.alor.ru/v2/auth/token"
API_URL = "https://api.alor.ru"

EXCHANGE = "MOEX"
BOARD = "TQBR"


class AlorTokenManager:
    """Управляет refresh- и access-токенами Алор."""

    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0.0
        self.session = self._make_session()

    @staticmethod
    def _make_session() -> requests.Session:
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

    def get_access_token(self) -> str:
        """Возвращает valid access-токен, обновляя при необходимости."""
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token

        self._refresh_access_token()
        return self.access_token

    def _refresh_access_token(self) -> None:
        """Обменивает refresh-токен на access-токен."""
        response = self.session.get(
            TOKEN_URL,
            params={"refreshToken": self.refresh_token},
        )
        response.raise_for_status()
        data = response.json()

        self.access_token = data.get("access_token") or data.get("AccessToken")
        expires_in = data.get("expires_in", 3600)
        # Закладываем буфер в 60 секунд для безопасного обновления
        self.token_expires_at = time.time() + expires_in - 60

        if not self.access_token:
            raise ValueError("Не удалось получить access_token из ответа Алор")


def get_alltrades_history_page(
    token_manager: AlorTokenManager,
    symbol: str,
    date_from: int,
    date_to: int,
    offset: int = 0,
    limit: int = 50000,
) -> dict:
    """Запрашивает страницу истории всех сделок по инструменту."""
    url = f"{API_URL}/md/v2/Securities/{EXCHANGE}/{symbol}/alltrades/history"
    access_token = token_manager.get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "instrumentGroup": BOARD,
        "from": date_from,
        "to": date_to,
        "limit": limit,
        "offset": offset,
    }
    response = token_manager.session.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def get_all_trades_for_day(
    token_manager: AlorTokenManager,
    symbol: str,
    date_from: int,
    date_to: int,
    page_size: int = 50000,
) -> list[dict]:
    """Загружает все сделки за указанный день с пагинацией."""
    all_trades: list[dict] = []
    offset = 0

    while True:
        payload = get_alltrades_history_page(
            token_manager, symbol, date_from, date_to,
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


def day_range_unix(day: datetime) -> tuple[int, int]:
    """Возвращает Unix-timestamp начала и конца дня в UTC."""
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def previous_trading_day(reference: datetime) -> datetime:
    """Возвращает предыдущий рабочий день (пн-пт)."""
    day = reference - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def main() -> None:
    """Точка входа для загрузки истории сделок."""
    if not REFRESH_TOKEN:
        print("Ошибка: не найден ALOR_REFRESH_TOKEN в .env")
        print(f"Ищу файл .env здесь: {ENV_PATH}")
        return

    token_manager = AlorTokenManager(REFRESH_TOKEN)

    try:
        # Проверяем получение токена
        token_manager.get_access_token()
        print("Подключение успешно!")
    except requests.exceptions.RequestException as e:
        print(f"Не удалось получить access-токен: {e}")
        return

    target_day = previous_trading_day(datetime.now(timezone.utc))
    date_from, date_to = day_range_unix(target_day)
    print(f"Последний торговый день: {target_day.date()}")

    test_symbol = "SBER"
    print(f"Запрашиваю ленту сделок по {test_symbol} за этот день...")

    try:
        trades = get_all_trades_for_day(
            token_manager, test_symbol, date_from, date_to
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


if __name__ == "__main__":
    main()
