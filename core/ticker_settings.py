"""
Приблуда на python — загрузка настроек тикеров из JSON.
Фильтрует только активные тикеры (active: true).
"""
import json
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "ticker_settings.json"


def load_settings() -> Dict[str, Dict[str, Any]]:
    """Загружает настройки тикеров из JSON файла. Возвращает только активные."""
    if not SETTINGS_FILE.exists():
        return {}
    
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Фильтруем только активные тикеры
    active_settings = {}
    for symbol, settings in data.items():
        if settings.get("active", True):
            active_settings[symbol] = settings
            
    return active_settings


def load_all_settings() -> Dict[str, Dict[str, Any]]:
    """Загружает ВСЕ настройки тикеров (включая неактивные)."""
    if not SETTINGS_FILE.exists():
        return {}
    
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(data: Dict[str, Dict[str, Any]]) -> None:
    """Сохраняет настройки тикеров в JSON файл."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)