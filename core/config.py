"""
Приблуда на python — конфигурация детекторов.
ИСПРАВЛЕНО: ужесточены дефолты — interval_tolerance=0.05, max_qty_ratio=1.10.
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
        "min_repeats": overrides.get("min_repeats", 4),
        "min_interval": overrides.get("min_interval", 2.0),
        "max_interval": overrides.get("max_interval", 600.0),
        "interval_tolerance": overrides.get("interval_tolerance", 0.05),
        "max_qty_variants": overrides.get("max_qty_variants", 2),
        "max_qty_ratio": overrides.get("max_qty_ratio", 1.10),
        "ignore_qty": overrides.get("ignore_qty", False),
        "time_window_sec": overrides.get("time_window_sec", 0.0),
        "close_after_misses": overrides.get("close_after_misses", 6),
        "max_series": overrides.get("max_series", 100000),
        "preset_name": overrides.get("preset_name", ""),
        
        # Н-013: допуск для коротких интервалов
        "short_interval_tolerance": overrides.get("short_interval_tolerance", 0.12),
        "short_interval_threshold": overrides.get("short_interval_threshold", 60.0),
        
        # Н-004: фильтр стабильного qty для длинных интервалов
        "long_interval_threshold": overrides.get("long_interval_threshold", 120.0),
        "stable_qty_required": overrides.get("stable_qty_required", False),
        "stable_qty_ratio": overrides.get("stable_qty_ratio", 0.8),
        
        # Минимальное число повторов для отображения в UI
        "min_display_repeats": overrides.get("min_display_repeats", 2),
    }
    configs.append(base_config)
    
    return configs