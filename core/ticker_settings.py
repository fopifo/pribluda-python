"""
Приблуда на python — загрузка настроек тикеров из JSON.
"""
import json
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "ticker_settings.json"

def load_settings() -> Dict[str, Dict[str, Any]]:
    """Загружает настройки тикеров из JSON файла."""
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