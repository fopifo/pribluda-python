"""
Приблуда на python — конфиг с настройками детекции по каждому тикеру.

ТОЛЬКО СТРОГИЙ "КЛАССИЧЕСКИЙ" АЛГОРИТМ. Все остальные варианты убраны:
медленные "прокиды" (slow_loose/slow_strict) и мягкие пресеты с
чередованием объёма (fast_loose) удалены из DETECTOR_PRESETS целиком —
они давали слишком много ложных срабатываний при сравнении с
конкурентами, решение принято окончательно, возвращать их не планируется.

Робота считаем роботом, только если ВСЕ условия выполнены разом:
  - объём лота одинаковый КАЖДЫЙ раз (max_qty_variants=1 — без
    чередования вроде "45-46");
  - интервал между ударами одинаковый (в пределах interval_tolerance);
  - интервал НЕ короче 3 секунд — быстрее это уже не отличить от
    случайного шума на ленте, не робот;
  - минимум 3 срабатывания подряд — 2 удара слишком легко совпадают
    случайно, ниже этого порога серия вообще не считается сигналом.

Список тикеров и ручные пороги пользователя (мин. лотов, мин. сек
интервала, мин. повторов, активен/отключён) по-прежнему живут в
ticker_settings.json (см. ticker_settings.py) — редактируются через GUI
("⚙ Настройки тикеров"). Если у тикера задан ручной
min_interval/min_repeats — он переопределяет соответствующее поле
пресета именно для этого тикера (см. get_detector_configs ниже).
get_tracked_symbols() — точка входа для получения списка активных
тикеров.

TICKER_SETTINGS ниже — ЭТО ДРУГОЕ: тонкая настройка на уровне кода
(какие пресеты включены, свой min_qty_percentile), которую меняет
разработчик, а не трейдер через GUI. Не путать с ticker_settings.json.
"""

from ticker_settings import get_active_symbols, load_settings

DETECTOR_PRESETS = {
    "fast_strict": {
        "min_interval": 3.0,
        "max_interval": 30.0,
        "min_repeats": 3,
        "max_qty_variants": 1,
        "interval_tolerance": 0.1,
    },
}

# Настройки по конкретным тикерам — уровень разработчика (не GUI).
#   min_qty_percentile — низкий ПОЛ (не потолок!) дневного распределения
#                        объёма в лотах
#   presets             — какие пресеты из DETECTOR_PRESETS включены
TICKER_SETTINGS = {
    # Индивидуальные переопределения добавим сюда, если после наблюдения
    # окажется, что какой-то бумаге нужен свой min_qty_percentile.
}

# Настройки по умолчанию — для тикеров без отдельной записи выше.
# Только fast_strict — других пресетов в DETECTOR_PRESETS больше нет.
DEFAULT_SETTINGS = {
    "min_qty_percentile": 5,
    "presets": ["fast_strict"],
}


def get_tracked_symbols() -> list[str]:
    """Список активных тикеров — читается из ticker_settings.json
    (тикеры с active=False сюда не попадают)."""
    return get_active_symbols(load_settings())


def get_min_qty_percentile(symbol: str) -> float:
    """Возвращает, какой процентиль дневного объёма использовать как
    нижний порог (пол) для этого тикера. Используется, только если у
    тикера НЕТ ручного min_qty в ticker_settings.json."""
    ticker_cfg = TICKER_SETTINGS.get(symbol, DEFAULT_SETTINGS)
    return ticker_cfg.get("min_qty_percentile", DEFAULT_SETTINGS["min_qty_percentile"])


def get_detector_configs(symbol: str, min_qty: int, override: dict | None = None) -> list[dict]:
    """Возвращает список готовых настроек — по одному словарю на каждый
    включённый для тикера пресет (сейчас всегда один — fast_strict).
    min_qty вычисляется заранее (снаружи, в run_detectors.py /
    live_screener.py) — здесь просто подставляется в пресет.

    override — ручные настройки этого тикера из ticker_settings.json
    (поля min_interval / min_repeats). Если заданы (не None) —
    переопределяют соответствующее поле пресета для ЭТОГО тикера;
    max_interval пресета override не трогает."""
    ticker_cfg = TICKER_SETTINGS.get(symbol, DEFAULT_SETTINGS)
    presets = ticker_cfg.get("presets", DEFAULT_SETTINGS["presets"])

    override = override or {}
    manual_min_interval = override.get("min_interval")
    manual_min_repeats = override.get("min_repeats")

    configs = []
    for preset_name in presets:
        settings = dict(DETECTOR_PRESETS[preset_name])
        settings["min_qty"] = min_qty
        settings["preset_name"] = preset_name
        if manual_min_interval is not None:
            settings["min_interval"] = manual_min_interval
        if manual_min_repeats is not None:
            settings["min_repeats"] = manual_min_repeats
        configs.append(settings)
    return configs