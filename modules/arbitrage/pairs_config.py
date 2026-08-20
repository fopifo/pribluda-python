"""
Приблуда на python — загрузка/сохранение арбитражных связок (arb_pairs.json).
Схема связки: symbol_a, symbol_b, mode (absolute_rub|ratio_pct),
threshold, half_life_sec; symbol_c — опционально (трёхногие, v2).
Архитектура: modules/arbitrage/. Не торгует, только конфиг.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PAIRS_FILE = BASE_DIR / "arb_pairs.json"
MODES = ("absolute_rub", "ratio_pct")


def load_pairs():
    """Возвращает словарь имя->связка. Битый/отсутствующий файл -> {}."""
    try:
        raw = json.loads(PAIRS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("symbol_a") or not cfg.get("symbol_b"):
            continue
        pair = dict(cfg)
        if pair.get("mode") not in MODES:
            pair["mode"] = "absolute_rub"
        try:
            pair["threshold"] = float(pair.get("threshold", 0.5))
        except (TypeError, ValueError):
            pair["threshold"] = 0.5
        try:
            pair["half_life_sec"] = float(pair.get("half_life_sec", 600))
        except (TypeError, ValueError):
            pair["half_life_sec"] = 600.0
        out[str(name)] = pair
    return out


def save_pairs(pairs):
    """Пишет файл целиком. Исключения бросает — вызывающий ловит."""
    PAIRS_FILE.write_text(
        json.dumps(pairs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )