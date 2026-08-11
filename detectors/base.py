"""
Приблуда на python — базовые классы для детекторов алгоритмов (роботов).

Каждый конкретный детектор (робот-интервал, кэшбек, разнолот, змейка и т.д.)
наследуется от Detector и реализует on_trade(). Движок подаёт детектору
сделки по одной, по порядку времени, а детектор сам решает, когда пора
сообщить о найденной серии — возвращая список объектов Signal.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Signal:
    """Найденная серия сделок, похожая на алгоритм."""

    detector_name: str
    symbol: str
    side: str               # "buy" или "sell"
    qty_variants: list[int]  # объём(ы) лота в серии — робот может чередовать
    repeats: int
    interval_avg: float     # средний интервал между сделками серии, сек
    start_ts: float          # unix-время (сек) первой сделки серии
    end_ts: float             # unix-время (сек) последней сделки серии

    def __str__(self) -> str:
        start = datetime.fromtimestamp(self.start_ts, tz=timezone.utc)
        end = datetime.fromtimestamp(self.end_ts, tz=timezone.utc)
        duration = self.end_ts - self.start_ts
        qty_str = "-".join(str(q) for q in self.qty_variants)
        return (
            f"[{self.detector_name}] {self.symbol} {self.side} "
            f"qty={qty_str} повторов={self.repeats} "
            f"интервал~{self.interval_avg:.1f}с "
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
    def on_trade(self, trade: dict) -> list[Signal]:
        """Обрабатывает одну сделку по порядку времени.

        Возвращает список Signal — обычно пустой или из одного элемента,
        но может быть больше одного, если этой же сделкой попутно закрылись
        по таймауту другие, устаревшие параллельные серии.
        """
        raise NotImplementedError

    def flush(self) -> list[Signal]:
        """Вызывается в конце потока сделок — на случай, если несколько
        серий ещё не закрылись явно (не было "разрыва"), но лента данных
        кончилась. Возвращает список сигналов (может быть пустым).
        """
        return []