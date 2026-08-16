"""
Приблуда на python — конфиг с настройками детекции по каждому тикеру.

Два семейства пресетов:

  - "fast_strict" — робота считаем роботом, только если:
      - объём — ЛИБО одно и то же значение каждый раз, ЛИБО
        чередование ДВУХ конкретных значений (max_qty_variants=2,
        каждое совпадает ТОЧНО — не диапазон/допуск, а именно
        конкретные повторяющиеся числа, например 45 и 46);
        max_qty_ratio=1.5 защищает от попадания в ту же серию второго
        значения, которое слишком далеко от первого (это было бы уже
        не "чередование одного робота", а случайное совпадение);
      - интервал между ударами одинаковый (± interval_tolerance от
        предыдущего фактического интервала в этой же серии) — без
        запасных/смягчённых путей принятия;
      - интервал НЕ короче 3 секунд;
      - минимум 3 срабатывания подряд;
      - серия закрывается только после 2 пропущенных max_interval
        подряд (CLOSE_AFTER_MISSES в detectors/interval_robot.py), не
        после первого — чтобы не терять серию из-за случайного разового
        пропуска.

    ПОПРОБОВАЛИ И ОТКАЗАЛИСЬ (ветка experiments): допуск объёма в виде
    диапазона (±N лотов вокруг любого значения) и запасной "медианный"
    критерий принятия интервала, если строгий не прошёл. Оба решили не
    переносить — для скальпинга (владелец заходит в сделку "по рынку"
    вслед за роботом секунда в секунду) любое смягчение критерия
    повышает риск ложного сигнала, а не помогает.

  - "twap_strict" — для паттерна "крупная заявка, нарезанная по
    ВРЕМЕНИ на равные интервалы, с разным объёмом каждый раз" (найдено
    на реальных логах: PLZL, BELU). Объём вообще не участвует в
    матчинге (ignore_qty=True) — единственная защита от случайных
    совпадений это точность интервала, поэтому interval_tolerance
    строже (0.03) и min_repeats выше (4).

Медленные "прокиды" (slow_loose/slow_strict) остаются удалены.

Список тикеров и ручные пороги пользователя живут в
ticker_settings.json (см. ticker_settings.py) — редактируются через GUI.
Если у тикера задан ручной min_interval/min_repeats — он переопределяет
соответствующее поле пресета именно для этого тикера (см.
get_detector_configs ниже).

TICKER_SETTINGS ниже — тонкая настройка на уровне кода (какие пресеты
включены, свой min_qty_percentile), не путать с ticker_settings.json.
"""

from ticker_settings import get_active_symbols, load_settings

DETECTOR_PRESETS = {
    "fast_strict": {
        "min_interval": 3.0,
        "max_interval": 30.0,
        "min_repeats": 3,
        "max_qty_variants": 2,
        "max_qty_ratio": 1.5,
        "interval_tolerance": 0.1,
        "time_window_sec": 0.5,  # только для справочной stability_ratio
    },
    "twap_strict": {
        "min_interval": 3.0,
        "max_interval": 60.0,
        "min_repeats": 4,
        "ignore_qty": True,
        "interval_tolerance": 0.03,
        "time_window_sec": 0.5,
    },
}

TICKER_SETTINGS = {
    # Индивидуальные переопределения — если после наблюдения окажется,
    # что какой-то бумаге нужен свой min_qty_percentile или набор
    # пресетов.
}

DEFAULT_SETTINGS = {
    "min_qty_percentile": 5,
    "presets": ["fast_strict", "twap_strict"],
}


def get_tracked_symbols() -> list[str]:
    return get_active_symbols(load_settings())


def get_min_qty_percentile(symbol: str) -> float:
    ticker_cfg = TICKER_SETTINGS.get(symbol, DEFAULT_SETTINGS)
    return ticker_cfg.get("min_qty_percentile", DEFAULT_SETTINGS["min_qty_percentile"])


def get_detector_configs(symbol: str, min_qty: int, override: dict | None = None) -> list[dict]:
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