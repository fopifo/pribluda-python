import json
from pathlib import Path
p = Path('core/config.py')
p.write_text('''\x22\x22\x22
риблуда на python - конфигурация детекторов.
ефолты: interval_tolerance=0.05, max_qty_ratio=1.10, min_display_repeats=3.
-013: short_interval_threshold=20.0 (адаптивный допуск 12\x25 только для 1-19s).
v8: jitter_ratio_max=0.3 - фильтр джиттера (0 = выключен).
v9: grid_lock=True, grid_tolerance_ms=700 - защёлка на сетку после подтверждения.
\x22\x22\x22
from typing import List, Dict, Any

def get_detector_configs(symbol: str, min_qty: int, overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    configs = []
    base_config = {
        \x22min_qty\x22: min_qty,
        \x22min_repeats\x22: overrides.get(\x22min_repeats\x22, 4),
        \x22min_interval\x22: overrides.get(\x22min_interval\x22, 2.0),
        \x22max_interval\x22: overrides.get(\x22max_interval\x22, 600.0),
        \x22interval_tolerance\x22: overrides.get(\x22interval_tolerance\x22, 0.05),
        \x22max_qty_variants\x22: overrides.get(\x22max_qty_variants\x22, 2),
        \x22max_qty_ratio\x22: overrides.get(\x22max_qty_ratio\x22, 1.10),
        \x22ignore_qty\x22: overrides.get(\x22ignore_qty\x22, False),
        \x22time_window_sec\x22: overrides.get(\x22time_window_sec\x22, 0.0),
        \x22close_after_misses\x22: overrides.get(\x22close_after_misses\x22, 6),
        \x22max_series\x22: overrides.get(\x22max_series\x22, 100000),
        \x22preset_name\x22: overrides.get(\x22preset_name\x22, \x22\x22),
        \x22short_interval_tolerance\x22: overrides.get(\x22short_interval_tolerance\x22, 0.12),
        \x22short_interval_threshold\x22: overrides.get(\x22short_interval_threshold\x22, 20.0),
        \x22long_interval_threshold\x22: overrides.get(\x22long_interval_threshold\x22, 120.0),
        \x22stable_qty_required\x22: overrides.get(\x22stable_qty_required\x22, False),
        \x22stable_qty_ratio\x22: overrides.get(\x22stable_qty_ratio\x22, 0.8),
        \x22min_display_repeats\x22: overrides.get(\x22min_display_repeats\x22, 3),
        \x22jitter_ratio_max\x22: overrides.get(\x22jitter_ratio_max\x22, 0.3),
        \x22grid_lock\x22: overrides.get(\x22grid_lock\x22, True),
        \x22grid_tolerance_ms\x22: overrides.get(\x22grid_tolerance_ms\x22, 700),
    }
    configs.append(base_config)
    return configs
''', encoding='utf-8')
print(f'записано: {p.read_text(encoding="utf-8").count(chr(10))} строк')
