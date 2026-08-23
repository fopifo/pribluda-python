"""
Приблуда на python — детектор пробоев и подходов к уровням (аналог LEVELS из LiveScreener).
Отслеживает заданные уровни сопротивления и поддержки по тикеру:
- BREAK_UP / BREAK_DOWN — факт пробоя уровня;
- APPROACH_UP / APPROACH_DOWN — ранний сигнал: цена подошла к уровню
  заранее на approach_points пунктов (до пробоя).
Отдельный модуль, не трогает рабочие limits/арбитраж/backend.
Уровни задаются в настройках per-ticker (ключ "levels") — позже подключим
чтение из levels_break.json и из планок QUIK (PRICEMAX/PRICEMIN).
"""
from dataclasses import dataclass
from typing import List, Dict, Any
from detectors.base import Signal


@dataclass
class LevelSignal(Signal):
    """Сигнал уровня: добавляет сам уровень и тип события."""
    level: float = 0.0
    event: str = ""  # "break_up","break_down","approach_up","approach_down"


class LevelBreakDetector:
    """
    Детектор пробоев/подходов к уровням. Один экземпляр — один тикер.
    Не наследуется от Detector (события ценовые, не интервальные), но
    возвращает Signal-совместимые объекты, чтобы позже встать в GUI.
    """
    name = "level-break"

    def __init__(self, symbol: str, settings: Dict[str, Any]):
        self.symbol = symbol
        self.settings = settings
        levels = settings.get("levels", {}) or {}
        self.resistance = sorted(float(x) for x in levels.get("resistance", []))
        self.support = sorted((float(x) for x in levels.get("support", [])), reverse=True)
        self.approach_points = float(settings.get("approach_points", 0.0))
        self.cooldown_sec = float(settings.get("cooldown_sec", 60.0))
        self.last_price = None
        self.last_event = {}  # (event, level) -> ts

    def _ok_cooldown(self, key, ts: float) -> bool:
        prev = self.last_event.get(key)
        if prev is None or (ts - prev) >= self.cooldown_sec:
            self.last_event[key] = ts
            return True
        return False

    def _mk(self, trade: Dict[str, Any], event: str, level: float) -> LevelSignal:
        ts = trade["timestamp"] / 1000.0
        side = "buy" if event.endswith("up") else "sell"
        return LevelSignal(
            detector_name=self.name,
            symbol=self.symbol,
            side=side,
            qty_variants=[trade.get("qty", 0)],
            repeats=1,
            interval_avg=0.0,
            start_ts=ts,
            end_ts=ts,
            level=level,
            event=event,
        )

    def on_trade(self, trade: Dict[str, Any]) -> List[Signal]:
        price = trade.get("price")
        if price is None:
            return []
        ts = trade["timestamp"] / 1000.0
        signals = []
        last = self.last_price

        if last is not None:
            for r in self.resistance:
                if last < r <= price:
                    if self._ok_cooldown(("break_up", r), ts):
                        signals.append(self._mk(trade, "break_up", r))
                elif self.approach_points > 0 and (r - self.approach_points) <= price < r:
                    if self._ok_cooldown(("approach_up", r), ts):
                        signals.append(self._mk(trade, "approach_up", r))
            for s in self.support:
                if last > s >= price:
                    if self._ok_cooldown(("break_down", s), ts):
                        signals.append(self._mk(trade, "break_down", s))
                elif self.approach_points > 0 and s < price <= (s + self.approach_points):
                    if self._ok_cooldown(("approach_down", s), ts):
                        signals.append(self._mk(trade, "approach_down", s))

        self.last_price = price
        return signals

    def flush(self) -> List[Signal]:
        return []