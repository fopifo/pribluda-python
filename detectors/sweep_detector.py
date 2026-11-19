"""
Приблуда на python — детектор "удара по рынку" (Sweep / Volume Spike).
Ищет резкий всплеск объёма торгов за короткий промежуток времени,
что является ленточным проявлением паттерна "постановка крупной заявки ↔ удар".
Работает параллельно с interval_robot, не ломая его логику.
"""
import logging
import time
from collections import deque
from pathlib import Path
from zoneinfo import ZoneInfo
from .base import Detector, Signal

MSK = ZoneInfo("Europe/Moscow")

_log = logging.getLogger("detector")

class SweepDetector(Detector):
    name = "робот-всплеск"
    
    def __init__(self, symbol, settings):
        super().__init__(symbol, settings)
        # Пороговые значения из settings или дефолтные
        self.window_sec = settings.get("sweep_window_sec", 2.0)  # окно в секундах
        self.min_qty = settings.get("sweep_min_qty", 100)        # мин. объём в лотах за окно
        self.max_interval_sec = settings.get("sweep_max_interval", 5.0) # макс. разрыв между сделками в всплеске
        
        self._trades = deque()  # Храним (timestamp_sec, qty, price)
        self._last_signal_ts = 0
        self.cooldown_sec = settings.get("sweep_cooldown_sec", 10.0)

        _log.info(f"[{symbol}] SWEEP INIT: window={self.window_sec}s, min_qty={self.min_qty}")

    def on_trade(self, trade):
        ts_sec = trade["timestamp"] / 1000.0
        qty = float(trade.get("qty", 0))
        price = float(trade.get("price", 0))
        side = trade.get("side", "").lower()
        
        if qty <= 0:
            return []

        # Добавляем сделку в окно
        self._trades.append((ts_sec, qty, price, side))
        
        # Удаляем старые сделки за пределами окна
        cutoff = ts_sec - self.window_sec
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()
            
        # Считаем суммарный объём в окне
        total_qty = sum(t[1] for t in self._trades)
        
        signals = []
        # Если объём превысил порог и прошёл кулдаун с последнего сигнала
        if total_qty >= self.min_qty and (ts_sec - self._last_signal_ts) > self.cooldown_sec:
            # Проверяем, что сделки в окне не слишком разрежены (это именно всплеск, а не просто набор мелких сделок за 2 часа)
            if self._trades:
                first_ts = self._trades[0][0]
                last_ts = self._trades[-1][0]
                if (last_ts - first_ts) <= self.max_interval_sec:
                    self._last_signal_ts = ts_sec
                    
                    # Формируем сигнал
                    signals.append(Signal(
                        detector_name=self.name,
                        symbol=self.symbol,
                        side=self._trades[-1][3], # сторона последней сделки
                        qty_variants=[int(total_qty)], # общий объём всплеска
                        repeats=len(self._trades), # количество сделок в всплеске
                        interval_avg=(last_ts - first_ts) / max(len(self._trades) - 1, 1),
                        start_ts=first_ts,
                        end_ts=last_ts,
                        jitter_ms=None,
                        stability_ratio=None
                    ))
                    # Очищаем окно после сигнала, чтобы не спамить на том же объёме
                    self._trades.clear()
                    
        return signals

    def drain_confirms(self):
        return []

    def check_overdue(self, now_ts):
        return [], []

    def get_active_snapshot(self, now_ts):
        return []

    def flush(self):
        self._trades.clear()
        return []