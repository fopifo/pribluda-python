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
    # миллисекундах. None, если интервалов меньше двух (серия из 2
    # сделок даёт только 1 интервал — дрожать ему не с чем сравнивать).
    # Низкое значение — интервал держится очень ровно (похоже на чистый
    # программный автомат). Высокое — тайминг "гуляет" (может быть
    # имитация человеком/полу-ручным скриптом, а не чистый робот).
    jitter_ms: float | None = None

    def __str__(self) -> str:
        start = datetime.fromtimestamp(self.start_ts, tz=timezone.utc)
        end = datetime.fromtimestamp(self.end_ts, tz=timezone.utc)
        duration = self.end_ts - self.start_ts
        qty_str = "-".join(str(q) for q in self.qty_variants)
        jitter_str = f"джиттер={self.jitter_ms:.1f}мс" if self.jitter_ms is not None else "джиттер=н/д"
        return (
            f"[{self.detector_name}] {self.symbol} {self.side} "
            f"qty={qty_str} повторов={self.repeats} "
            f"интервал~{self.interval_avg:.1f}с {jitter_str} "
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