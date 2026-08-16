"""
Приблуда на python — базовые классы для детекторов алгоритмов (роботов).

Каждый конкретный детектор наследуется от Detector и реализует
on_trade(). Движок подаёт детектору сделки по одной, по порядку
времени, а детектор сам решает, когда пора сообщить о найденной серии —
возвращая список объектов Signal.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

# Если у серии больше этого числа разных объёмов — в строковом
# представлении показываем сводку, а не полный список.
MAX_QTY_VARIANTS_TO_LIST = 6


@dataclass
class Signal:
    """Найденная серия сделок, похожая на алгоритм."""

    detector_name: str
    symbol: str
    side: str
    qty_variants: list
    repeats: int
    interval_avg: float
    start_ts: float
    end_ts: float
    # Стандартное отклонение интервалов между сделками серии, в
    # миллисекундах. None, если интервалов меньше двух.
    jitter_ms: float | None = None
    # Доля интервалов серии, попавших в пределы time_window_sec от
    # медианы — справочная метрика "ровности" серии, НЕ влияет на
    # решение детектора продлевать/обрывать серию (см. docstring
    # detectors/interval_robot.py). None, если пресет не задаёт
    # time_window_sec или интервалов меньше двух.
    stability_ratio: float | None = None

    def _qty_str(self) -> str:
        if len(self.qty_variants) <= MAX_QTY_VARIANTS_TO_LIST:
            return "-".join(str(q) for q in self.qty_variants)
        return (
            f"{len(self.qty_variants)} разных ({min(self.qty_variants)}"
            f"–{max(self.qty_variants)})"
        )

    def __str__(self) -> str:
        start = datetime.fromtimestamp(self.start_ts, tz=timezone.utc)
        end = datetime.fromtimestamp(self.end_ts, tz=timezone.utc)
        duration = self.end_ts - self.start_ts
        jitter_str = f"джиттер={self.jitter_ms:.1f}мс" if self.jitter_ms is not None else "джиттер=н/д"
        stability_str = (
            f" стабильность={self.stability_ratio:.0%}" if self.stability_ratio is not None else ""
        )
        return (
            f"[{self.detector_name}] {self.symbol} {self.side} "
            f"qty={self._qty_str()} повторов={self.repeats} "
            f"интервал~{self.interval_avg:.1f}с {jitter_str}{stability_str} "
            f"с {start:%H:%M:%S} по {end:%H:%M:%S} "
            f"(длилось {duration:.1f} сек)"
        )


class Detector(ABC):
    """Базовый класс детектора. Один экземпляр — один тикер."""

    name = "base"

    def __init__(self, symbol: str, settings: dict):
        self.symbol = symbol
        self.settings = settings

    @abstractmethod
    def on_trade(self, trade: dict):
        raise NotImplementedError

    def flush(self):
        return []