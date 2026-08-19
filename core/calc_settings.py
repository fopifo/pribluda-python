"""
Приблуда на python — настройки калькулятора/расчётов.
"""
import json
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
CALC_SETTINGS_FILE = BASE_DIR / "calc_settings.json"

def load_calc_settings() -> Dict[str, Any]:
    if not CALC_SETTINGS_FILE.exists():
        return {}
    with open(CALC_SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_calc_settings(settings: Dict[str, Any]) -> None:
    with open(CALC_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)