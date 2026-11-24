"""
ai_solution_v1/config.py
========================
Пороги и настройки ДЕТЕКТОРА интервальных роботов.
Все литералы, которые в старом проекте были разбросаны по
detectors/interval_robot.py и core/config.py, собраны здесь, в
типизированном виде (frozen dataclass), с чётким комментарием:
что параметр делает и какие значения были "боевыми".

Правила:
- Никаких импортов из dpNEW. Модуль автономный.
- Никакой торговли, сети и GUI. Только числа.
- Параметры времени — ВСЕГДА в секундах, если имя не кончается на _ms.
- Параметры-допуски — ВСЕГДА доля (0.05 = 5%), если имя не кончается
  на _ms.
"""
from __future__ import annotations

from dataclasses import dataclass, replace, asdict
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "DetectorConfig",
    "SettingsError",
    "DEFAULT_CONFIG",
    "build_config",
    "overlay",
    "validate_qty_range",
]


# ---------------------------------------------------------------------------
# Ошибки конфигурации
# ---------------------------------------------------------------------------
class SettingsError(ValueError):
    """Некорректная конфигурация детектора (падаем громко, молча — нет)."""


# ---------------------------------------------------------------------------
# Основной конфиг детектора
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """
    Настройки одного экземпляра детектора.

    Экземпляр детектора обслуживает ОДИН тикер (symbol) и все его сделки.
    Конфиг неизменяемый: если нужно подогнать параметры под тикер —
    делается это через overlay(); исходный объект не мутируется.

    Блоки параметров:
      1) Отбор сделок (min_qty, noisy qty, min gap);
      2) База и допуск интервала (tolerance, short/long thresholds);
      3) Объёмы (qty variants, ratio, stable qty);
      4) Набор серии (кратные, двойные удары, misses);
      5) Порог подтверждения/репорта и качество (regularity, jitter);
      6) Ёмкости памяти (жёсткие границы, чтобы детектор не рос).
    """

    # ------------------------------------------------------------------
    # 1. Отбор сделок
    # ------------------------------------------------------------------
    # Сделки с qty < min_qty детектором игнорируются полностью.
    # 1 = принимать всё. В старом проекте выставлялся вручную по тикеру.
    min_qty: int = 1

    # Фильтр "шумных" объёмов: объём, чья доля во ВСЕЙ ленте тикера
    # >= noisy_qty_share, считается маркет-мейкерским шумом и не может
    # БЫТЬ БАЗОЙ длинной серии (короткие серии им строить можно).
    # В старом проекте по умолчанию OFF — оставляем OFF, чтобы поведение
    # было эквивалентным; при включении правило применяется так же.
    noisy_qty_filter: bool = False
    noisy_qty_share: float = 0.03
    # Минимальное число сделок тикера, прежде чем статистика объёмов
    # вообще начинает считаться достоверной.
    noisy_qty_min_samples: int = 100

    # Минимальный зазор между двумя "ударами" одной серии. Всё, что
    # ближе — считается двойным ударом (одна и та же заявка частично
    # исполнена), не продлевает серию. Старого значения 1.0 сек
    # НЕ ХВАТАЛО для суб-секундных сеток: зазор 0.3 с ломал серию.
    # Поэтому здесь минимальный зазор = min_gap_sec, но он никогда
    # не больше макс. интервала серии.
    min_gap_sec: float = 0.1

    # ЖЁСТКИЙ ФИЛЬТР ОТЧЁТА: серия с медианным интервалом меньше этого
    # значения не считается роботом вообще. Ловит два класса мусора:
    #   - интервал 0 / 1 мс (лента дробит один удар на десятки записей
    #      одного мс — так было с CNY-12.26: "интервал 0.0с");
    #   - High-Frequency шум, не являющийся сеткой.
    # ВАЖНО (2026-11-20): эталон Aniscan ищет СЕТОЧНЫХ роботов с периодом
    # 6-131 с. Всё короче 1 с — не робот этого класса, а пачка сделок
    # CScalp (при пороге 0.5 было 225 тыс. таких серий и Precision 0%).
    # 0.0 = фильтр выключен (только для отладки сырых данных).
    min_report_interval_sec: float = 1.0

    # ------------------------------------------------------------------
    # 2. База и допуск интервала
    # ------------------------------------------------------------------
    # Абсолютные границы интервалов серии (сек). Серии с периодом вне
    # диапазона не строятся вообще.
    # min_interval_sec = 1.0 (2026-11-20): значение 0.1 (эксперимент)
    # пропускало суб-секундные пачки — 225 тыс. серий с интервалом 0 мс
    # и Precision 0%. Сеточные роботы Aniscan живут в диапазоне 6-131 с.
    min_interval_sec: float = 1.0
    max_interval_sec: float = 600.0

    # Базовый относительный допуск интервала для периодов >=
    # short_interval_threshold_sec (0.05 = 5%).
    interval_tolerance: float = 0.05

    # Для коротких периодов (1..short_interval_threshold_sec) допуск
    # широкий — короткие сетки "дрожат" сильнее, а требовать 5% на
    # 2-3 секундах означает терять большинство роботов.
    short_interval_threshold_sec: float = 20.0
    short_interval_tolerance: float = 0.12

    # Выше этого периода qty обязан быть стабильным (см. stable qty).
    long_interval_threshold_sec: float = 120.0

    # Кратность: если в серию пришёл интервал, равный k*base, серия НЕ
    # рвётся — робот мог "пропустить" k-1 удар (частичный пропуск или
    # пропуск в ленте). Максимальный k.
    interval_mult_max: int = 4

    # Сколько в секундах держать базу интервала, если она "перещёлкнулась".
    # relock: если медиана интервалов отклоняется от базы сильнее, чем
    # relock_tol_ms, И >= relock_min_agree всех интервалов совпадают с
    # медианой в пределах relock_tol_ms — переякориваемся на медиану.
    relock_tol_ms: float = 150.0
    relock_min_agree: float = 0.8
    # Сколько последних интервалов серии участвуют в переякоривании
    # базы. Если в окне стабильно 500 мс — база станет 500 мс, даже
    # если серия началась с 5000 мс (см. _maybe_relock ветка A).
    relock_window: int = 6

    # Насколько "выравнивающий" допуск используется при проверке
    # регулярности и jitter (абсолютный пол, потому что на мс-уровне
    # относительный допуск слишком жёсткий).
    regularity_abs_floor_ms: float = 2000.0
    regularity_rel: float = 0.2

    # ------------------------------------------------------------------
    # 3. Объёмы
    # ------------------------------------------------------------------
    # Сколько разных объёмов может быть у одной серии робота.
    # Обычно робот работает фиксированным qty или парой (например 64/65).
    max_qty_variants: int = 4
    # Разрешение "прилипания" нового объёма к серии:
    # max(variants)/min(variants) <= qty_ratio и новый объём >=
    # min(варианты)*qty_attach_min_frac. В старом коде это проверялось
    # ТОЛЬКО с условием len < max_variants; здесь та же семантика плюс
    # защита: новый объём не может быть маркет-мейкерским шумом.
    qty_ratio: float = 1.10
    qty_attach_min_frac: float = 1.0

    # Если период >= short_interval_threshold_sec, "длинная" серия
    # требует стабильного объёма (иначе маркет-мейкер с разными
    # размерами заявок выглядит как робот).
    stable_qty_required: bool = False
    stable_qty_ratio: float = 0.8

    # ------------------------------------------------------------------
    # 4. Набор серии / закрытия
    # ------------------------------------------------------------------
    # Сколько "пропусков" подряд сверх ожидаемого интервала держим
    # серию открытой (до закрытия и записи сигнала).
    close_after_misses: int = 6

    # Сколько всего сигналов серии (ударов) максимум. После — серия
    # закрывается и сигнализируется, начинается новая.
    max_series_hits: int = 100_000

    # Сколько максимум активных кандидатов держим на одну сторону
    # (защита от взрыва памяти на шумных тикерах). Выброс идёт по
    # наименее подтверждённым.
    max_active_candidates: int = 500

    # ------------------------------------------------------------------
    # 5. Порог подтверждения и качество
    # ------------------------------------------------------------------
    # После какого числа ударов серия считается "подтверждённой":
    # фиксируется сигнал (confirmation event) и, если проходит проверки
    # качества, пишется в историю (report).
    report_min_repeats: int = 8

    # Минимальная доля интервалов, лежащих на сетке (регулярность).
    # < report_min_regular -> серия подтверждена, но НЕ репортится.
    report_min_regular: float = 0.6

    # Самый сильный фильтр качества: относительный разброс интервалов
    # к базе. Джиттер считается относительно СЕТКИ (квантизация по
    # базе), иначе любая серия с пропусками получит огромный джиттер.
    jitter_ratio_max: float = 0.3

    # С какого числа ударов серия показывается в live-снимке (GUI не
    # трогаем, но snapshot нужен для агрегации).
    min_display_repeats: int = 3

    # ------------------------------------------------------------------
    # 6. Идентификация тикера (для логов)
    # ------------------------------------------------------------------
    symbol: str = ""
    preset_name: str = ""

    # ------------------------------------------------------------------
    # Валидация
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        def chk(cond: bool, msg: str) -> None:
            if not cond:
                raise SettingsError(msg)

        chk(self.min_qty >= 1, "min_qty должен быть >= 1")
        chk(self.min_gap_sec >= 0.0, "min_gap_sec должен быть >= 0")
        chk(self.min_report_interval_sec >= 0.0,
            "min_report_interval_sec должен быть >= 0")
        chk(0.0 < self.min_interval_sec <= self.max_interval_sec,
            "нужно 0 < min_interval_sec <= max_interval_sec")
        chk(0.0 < self.interval_tolerance < 1.0,
            "interval_tolerance должен быть в (0, 1)")
        chk(0.0 < self.short_interval_tolerance < 1.0,
            "short_interval_tolerance должен быть в (0, 1)")
        chk(self.short_interval_threshold_sec > 0.0,
            "short_interval_threshold_sec должен быть > 0")
        chk(self.long_interval_threshold_sec > 0.0,
            "long_interval_threshold_sec должен быть > 0")
        chk(self.interval_mult_max >= 1, "interval_mult_max должен быть >= 1")
        chk(self.relock_tol_ms >= 0.0, "relock_tol_ms должен быть >= 0")
        chk(self.relock_min_agree > 0.0 and self.relock_min_agree <= 1.0,
            "relock_min_agree должен быть в (0, 1]")
        chk(self.relock_window >= 2, "relock_window должен быть >= 2")
        chk(self.regularity_abs_floor_ms >= 0.0,
            "regularity_abs_floor_ms должен быть >= 0")
        chk(self.regularity_rel >= 0.0, "regularity_rel должен быть >= 0")
        chk(self.max_qty_variants >= 1, "max_qty_variants должен быть >= 1")
        chk(self.qty_ratio >= 1.0, "qty_ratio должен быть >= 1.0")
        chk(0.0 < self.qty_attach_min_frac <= 1.0,
            "qty_attach_min_frac должен быть в (0, 1]")
        chk(0.0 < self.stable_qty_ratio <= 1.0,
            "stable_qty_ratio должен быть в (0, 1]")
        chk(self.close_after_misses >= 1, "close_after_misses должен быть >= 1")
        chk(self.max_series_hits >= 2, "max_series_hits должен быть >= 2")
        chk(self.max_active_candidates >= 1,
            "max_active_candidates должен быть >= 1")
        chk(self.report_min_repeats >= 2,
            "report_min_repeats должен быть >= 2")
        chk(0.0 <= self.report_min_regular <= 1.0,
            "report_min_regular должен быть в [0, 1]")
        chk(self.jitter_ratio_max >= 0.0, "jitter_ratio_max должен быть >= 0")
        chk(self.min_display_repeats >= 1,
            "min_display_repeats должен быть >= 1")

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------
    def with_overrides(self, **kwargs: Any) -> "DetectorConfig":
        """Вернуть копию конфига с переопределёнными полями (с валидацией)."""
        unknown = [k for k in kwargs if k not in self.field_names()]
        if unknown:
            raise SettingsError(f"неизвестные параметры: {', '.join(sorted(unknown))}")
        return replace(self, **kwargs)

    @classmethod
    def field_names(cls) -> Tuple[str, ...]:
        return tuple(f for f in cls.__dataclass_fields__)  # type: ignore[attr-defined]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]

    def derived(self) -> "DerivedConfig":
        """Предвычисленные значения — чтобы hot-path не умножал на 1000."""
        return DerivedConfig(self)


# ---------------------------------------------------------------------------
# Производный конфиг: всё в миллисекундах без плавающей арифметики в hot-path
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DerivedConfig:
    """
    Предвычисленные величины. Детектор работает ВНУТРИ в миллисекундах
    (int), поэтому здесь всё уже приведено к мс. Это убирает
    float-арифметику на каждой сделке и ошибки вида ts/1000.
    """

    min_interval_ms: int
    max_interval_ms: int
    close_after_misses: int
    miss_close_after_ms: int
    short_interval_threshold_ms: int
    long_interval_threshold_ms: int
    min_gap_ms: int
    min_report_interval_ms: int
    relock_tol_ms: float
    relock_window: int
    regularity_abs_floor_ms: float
    short_interval_tolerance: float
    interval_tolerance: float
    regularity_rel: float
    relock_min_agree: float
    max_qty_variants: int
    qty_ratio: float
    qty_attach_min_frac: float
    stable_qty_required: bool
    stable_qty_ratio: float
    report_min_repeats: int
    report_min_regular: float
    jitter_ratio_max: float
    min_display_repeats: int
    min_qty: int
    max_series_hits: int
    max_active_candidates: int
    interval_mult_max: int
    noisy_qty_filter: bool
    noisy_qty_share: float
    noisy_qty_min_samples: int

    def __init__(self, cfg: DetectorConfig) -> None:
        ms = 1000.0
        object.__setattr__(self, "min_interval_ms", int(round(cfg.min_interval_sec * ms)))
        object.__setattr__(self, "max_interval_ms", int(round(cfg.max_interval_sec * ms)))
        object.__setattr__(self, "close_after_misses", cfg.close_after_misses)
        # Серия закрывается, если с последнего удара прошло больше
        # max_interval_ms * close_after_misses.
        object.__setattr__(
            self, "miss_close_after_ms", int(round(cfg.max_interval_sec * ms)) * cfg.close_after_misses
        )
        object.__setattr__(
            self, "short_interval_threshold_ms", int(round(cfg.short_interval_threshold_sec * ms))
        )
        object.__setattr__(
            self, "long_interval_threshold_ms", int(round(cfg.long_interval_threshold_sec * ms))
        )
        object.__setattr__(self, "min_gap_ms", int(round(cfg.min_gap_sec * ms)))
        # Эффективный порог отчёта: не меньше min_gap_ms, даже если
        # min_report_interval_sec задан нулём (нулевой интервал —
        # мусор по определению).
        object.__setattr__(
            self,
            "min_report_interval_ms",
            max(int(round(cfg.min_report_interval_sec * ms)), int(round(cfg.min_gap_sec * ms))),
        )
        object.__setattr__(self, "relock_tol_ms", cfg.relock_tol_ms)
        object.__setattr__(self, "relock_window", cfg.relock_window)
        object.__setattr__(self, "regularity_abs_floor_ms", cfg.regularity_abs_floor_ms)
        object.__setattr__(self, "short_interval_tolerance", cfg.short_interval_tolerance)
        object.__setattr__(self, "interval_tolerance", cfg.interval_tolerance)
        object.__setattr__(self, "regularity_rel", cfg.regularity_rel)
        object.__setattr__(self, "relock_min_agree", cfg.relock_min_agree)
        object.__setattr__(self, "max_qty_variants", cfg.max_qty_variants)
        object.__setattr__(self, "qty_ratio", cfg.qty_ratio)
        object.__setattr__(self, "qty_attach_min_frac", cfg.qty_attach_min_frac)
        object.__setattr__(self, "stable_qty_required", cfg.stable_qty_required)
        object.__setattr__(self, "stable_qty_ratio", cfg.stable_qty_ratio)
        object.__setattr__(self, "report_min_repeats", cfg.report_min_repeats)
        object.__setattr__(self, "report_min_regular", cfg.report_min_regular)
        object.__setattr__(self, "jitter_ratio_max", cfg.jitter_ratio_max)
        object.__setattr__(self, "min_display_repeats", cfg.min_display_repeats)
        object.__setattr__(self, "min_qty", cfg.min_qty)
        object.__setattr__(self, "max_series_hits", cfg.max_series_hits)
        object.__setattr__(self, "max_active_candidates", cfg.max_active_candidates)
        object.__setattr__(self, "interval_mult_max", cfg.interval_mult_max)
        object.__setattr__(self, "noisy_qty_filter", cfg.noisy_qty_filter)
        object.__setattr__(self, "noisy_qty_share", cfg.noisy_qty_share)
        object.__setattr__(self, "noisy_qty_min_samples", cfg.noisy_qty_min_samples)


# ---------------------------------------------------------------------------
# Дефолт и фабрики
# ---------------------------------------------------------------------------
# "Боевые" значения из dpNEW (core/config.py v13 + detectors/interval_robot.py).
# Единственное сознательное отличие — БАЗОВЫЙ min_interval_sec:
#   в старом коде дефолт был 10.0 с (отсечение маркет-мейкинга), но
#   фактическое значение задавалось в core/config.py как 2.0 с.
#   Здесь 1.0 с — нижняя граница, ниже которой мс-разрешение ленты
#   слишком грубое; а конкретное значение 2.0/10.0 ставится per-ticker
#   через overlay(min_interval_sec=...).
DEFAULT_CONFIG = DetectorConfig(
    min_qty=1,
    noisy_qty_filter=False,
    noisy_qty_share=0.03,
    noisy_qty_min_samples=100,
    min_gap_sec=0.1,
    min_report_interval_sec=1.0,
    min_interval_sec=1.0,
    max_interval_sec=600.0,
    interval_tolerance=0.05,
    short_interval_threshold_sec=20.0,
    short_interval_tolerance=0.12,
    long_interval_threshold_sec=120.0,
    interval_mult_max=4,
    relock_tol_ms=150.0,
    relock_min_agree=0.8,
    relock_window=6,
    regularity_abs_floor_ms=2000.0,
    regularity_rel=0.2,
    max_qty_variants=4,
    qty_ratio=1.10,
    qty_attach_min_frac=1.0,
    stable_qty_required=False,
    stable_qty_ratio=0.8,
    close_after_misses=6,
    max_series_hits=100_000,
    max_active_candidates=500,
    report_min_repeats=8,
    report_min_regular=0.6,
    jitter_ratio_max=0.3,
    min_display_repeats=3,
    symbol="",
    preset_name="",
)


def build_config(symbol: str, overrides: Optional[Mapping[str, Any]] = None) -> DetectorConfig:
    """
    Собрать конфиг тикера: DEFAULT_CONFIG + symbol + переопределения.

    overrides берутся, например, из ticker_settings.json старого проекта:
        {"min_qty": 10, "min_interval_sec": 2.0, ...}

    Неизвестный ключ в overrides — SettingsError (а не молчаливое
    игнорирование, как в старом коде, где опечатка в имени ключа
    тихо выключала фильтр).
    """
    ov: Dict[str, Any] = dict(overrides or {})
    ov["symbol"] = symbol
    return DEFAULT_CONFIG.with_overrides(**ov)


def overlay(base: DetectorConfig, **kwargs: Any) -> DetectorConfig:
    """Изменённая копия готового конфига (частный случай with_overrides)."""
    return base.with_overrides(**kwargs)


# ---------------------------------------------------------------------------
# Хелперы для фильтрации объёмов
# ---------------------------------------------------------------------------
def validate_qty_range(cfg: DetectorConfig, qty: int) -> bool:
    """
    Примитивная валидация объёма сделки без доверия к источнику:
    int > 0. Ленты CScalp иногда дают строки "0"/"" — такие
    отбрасываются ДО детектора, но defensively проверяем и здесь.
    """
    try:
        q = int(qty)
    except (TypeError, ValueError):
        return False
    return q > 0 and q >= cfg.min_qty


def iter_detector_fields() -> Iterable[str]:
    """Все имена полей конфига (для опции --help / валидации CLI)."""
    return DetectorConfig.field_names()
