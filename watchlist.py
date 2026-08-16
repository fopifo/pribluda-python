"""
Приблуда на python — работа с watchlist (списком приоритетных тикеров).

Watchlist хранится в watchlist.json в корне проекта. Управляется только
через GUI (окно "⚙ Настройки тикеров"), руками файл править не нужно.
"""

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "watchlist.json"


def load_watchlist() -> set[str]:
    """Возвращает множество тикеров из watchlist.json. Если файла нет —
    пустое множество."""
    if not SETTINGS_PATH.exists():
        return set()
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {str(item).upper() for item in data}
    if isinstance(data, dict):
        return {str(key).upper() for key, value in data.items() if value}
    return set()


def save_watchlist(watchlist: set[str]) -> None:
    """Сохраняет множество тикеров в watchlist.json (атомарно, через
    временный файл)."""
    watchlist = {symbol.upper() for symbol in watchlist}
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted(watchlist), f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SETTINGS_PATH)


def is_in_watchlist(symbol: str) -> bool:
    """Проверяет, находится ли тикер в watchlist."""
    return symbol.upper() in load_watchlist()