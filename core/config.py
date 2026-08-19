"""
Приблуда на python — конфигурация детекторов.
ИСПРАВЛЕНО: строгая группировка QTY (2 варианта, ratio 1.25) — конец дичи 1-209.
"""
from typing import List, Dict, Any

def get_detector_configs(symbol: str, min_qty: int, overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Возвращает список конфигураций детекторов по тикеру.
    Сейчас только один детектор — интервальный робот.
    """
    configs = []
    
    # Базовая конфигурация интервального робота
    base_config = {
        "min_qty": min_qty,
        "min_repeats": overrides.get("min_repeats", 3),
        "min_interval": overrides.get("min_interval", 2.0),
        "max_interval": overrides.get("max_interval", 600.0),
        "interval_tolerance": overrides.get("interval_tolerance", 0.1),
        "max_qty_variants": overrides.get("max_qty_variants", 2),   # ИЗМЕНЕНО: было 3
        "max_qty_ratio": overrides.get("max_qty_ratio", 1.25),      # ИЗМЕНЕНО: было None
        "ignore_qty": overrides.get("ignore_qty", False),
        "time_window_sec": overrides.get("time_window_sec", 0.0),
        "close_after_misses": overrides.get("close_after_misses", 6),
        "max_series": overrides.get("max_series", 100000),
        "preset_name": overrides.get("preset_name", ""),
    }
    configs.append(base_config)
    
    return configs