"""
Приблуда на python — загрузка настроек арбитражных связок из
arb_pairs.json. Тот же паттерн, что и ticker_settings.py — настройки
живут в отдельном файле, не в коде, чтобы менять пороги без правки
кода (и чтобы позже завести под это вкладку в GUI, аналогично
"Настройкам тикеров").
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "arb_pairs.json"


def load_pairs() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    with open(SETTINGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_pairs(pairs: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(pairs, f, ensure_ascii=False, indent=2)


def get_pair_symbols(pairs: dict) -> set[str]:
    """Все тикеры, участвующие хоть в одной связке — нужно подписаться
    на них в WebSocket, даже если их нет в основном списке тикеров для
    поиска роботов."""
    symbols = set()
    for cfg in pairs.values():
        symbols.add(cfg["symbol_a"])
        symbols.add(cfg["symbol_b"])
    return symbols