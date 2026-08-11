"""
Приблуда на python — хранит K ("рублей за пункт") для калькулятора
объёма позиции в главном окне GUI.

Отдельно от ticker_settings.json, потому что это не настройка тикера, а
личный параметр трейдера — сколько сейчас стоит один "пункт" в рублях.
Значение меняется по мере роста заработка, не привязано к конкретной
бумаге.

Формула калькулятора (см. gui/window.py):
    лоты = округлить(K / шаг_цены_бумаги)

Пример: K=10, шаг цены 0.05 (как у VTBR) -> лоты = round(10/0.05) = 200.
"""

import json
import os
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "calc_settings.json"
DEFAULT_RUBLES_PER_POINT = 10.0


def load_rubles_per_point() -> float:
    if not SETTINGS_PATH.exists():
        return DEFAULT_RUBLES_PER_POINT
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rubles_per_point", DEFAULT_RUBLES_PER_POINT)


def save_rubles_per_point(value: float) -> None:
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"rubles_per_point": value}, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SETTINGS_PATH)