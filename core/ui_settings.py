"""
Приблуда на python — настройки интерфейса (геометрия окон, звук).
"""
import json
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.json"

def load_ui_settings() -> Dict[str, Any]:
    if not UI_SETTINGS_FILE.exists():
        return {}
    with open(UI_SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_ui_settings(settings: Dict[str, Any]) -> None:
    with open(UI_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)