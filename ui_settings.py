"""
Приблуда на python — настройки интерфейса, которые должны переживать
перезапуск программы: включён ли звук на новую серию, и последняя
позиция/размер главного окна и мини-окна.

Отдельно от calc_settings.json — там торговый параметр (К руб/пункт),
здесь — поведение самого GUI. Оба окна при закрытии сохраняют сюда
СВОЮ геометрию, читая перед записью текущее содержимое файла, чтобы не
затереть то, что уже сохранило другое окно (сначала load, потом меняем
только свой ключ, потом save).
"""

import json
import os
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "ui_settings.json"

DEFAULTS = {
    "sound_enabled": True,
    "main_window_geometry": None,
    "mini_window_geometry": None,
}


def load_ui_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save_ui_settings(settings: dict) -> None:
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SETTINGS_PATH)