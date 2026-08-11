"""
Приблуда на python — движок: подаёт сделки по одной, по порядку времени,
в набор детекторов для одного тикера и собирает найденные сигналы.

На этом этапе TradeBuffer работает в "историческом" режиме — читает
список сделок целиком (например, из JSON-файла) и прогоняет их через
детекторы, как будто это живой поток. Позже, когда подключим WebSocket,
интерфейс детекторов (on_trade) не изменится — просто сделки будут
приходить по одной в реальном времени, а не из списка.
"""

from detectors.base import Detector, Signal


class TradeBuffer:
    """Прогоняет отсортированный по времени список сделок через набор
    детекторов одного тикера."""

    def __init__(self, symbol: str, detectors: list[Detector]):
        self.symbol = symbol
        self.detectors = detectors

    def process(self, trades: list[dict]) -> list[Signal]:
        """Подаёт сделки по одной в каждый детектор, собирает сигналы."""
        signals: list[Signal] = []

        for trade in trades:
            for detector in self.detectors:
                signals.extend(detector.on_trade(trade))

        # В конце ленты — забираем незакрытые серии из каждого детектора.
        for detector in self.detectors:
            signals.extend(detector.flush())

        return signals